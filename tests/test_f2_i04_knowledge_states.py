from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta

from study_app.core.knowledge_states import (
    F2_RULES_V1,
    classify_topic,
    classify_topics,
    filter_classified_topics,
)
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import RecentTopicError, TopicInsight


AS_OF = date(2026, 9, 16)


def insight(topic_id: int, **changes) -> TopicInsight:
    base = TopicInsight(
        topic_key=topic_key_for_id(topic_id),
        topic_id=topic_id,
        subject_name="S",
        module_name="M",
        topic_name=f"T{topic_id}",
        subject_status="active",
        course_status="learning",
        is_archived=False,
        observation_count=4,
        exercise_count=2,
        evidence_confidence=0.60,
        evidence_version="evidence-v1",
        mastery_point=0.75,
        mastery_source="bkt_observed",
        mastery_interval=(0.55, 0.95),
        mastery_interval_rule_version="mastery-interval-v1",
        recent_error=None,
        last_observation_date=AS_OF - timedelta(days=10),
        last_observation_correctness=1.0,
        last_review_date=AS_OF - timedelta(days=2),
        review_count=2,
        half_life_days=8.0,
        recall_probability=0.90,
        target_recall=0.78,
        recommended_date=AS_OF + timedelta(days=3),
        recommendation_source="half_life_target_recall",
        direct_prerequisite_keys=(),
        unresolved_prerequisite_keys=(),
        as_of_date=AS_OF,
        model_version="model-v1",
        records_version="records-v1",
    )
    return replace(base, **changes)


def recent_error(topic_id: int, days_ago: int, correctness: float):
    return RecentTopicError(
        record_id=topic_id * 100,
        occurred_on=AS_OF - timedelta(days=days_ago),
        title=f"T{topic_id} error",
        correctness=correctness,
        result_source="problem.correctness",
        error_cause=None,
    )


class KnowledgeStateTests(unittest.TestCase):
    def test_a1_unlearned_has_one_primary_state_and_no_default_alert(self):
        item = insight(
            1,
            course_status="not_started",
            observation_count=0,
            exercise_count=0,
            evidence_confidence=0.0,
            mastery_source="baseline_prior",
            mastery_interval=None,
            last_observation_date=None,
            last_observation_correctness=None,
            last_review_date=None,
            review_count=0,
            recommended_date=None,
            recommendation_source="unavailable",
        )
        state = classify_topic(item, {})
        self.assertEqual(state.primary_state, "unlearned")
        self.assertEqual(state.recommended_action, "learn_then_assess")
        self.assertIsNone(state.effective_recommended_date)
        self.assertFalse(state.alert_eligible)

    def test_a2_learning_without_observations_is_insufficient_evidence(self):
        item = insight(
            2,
            observation_count=0,
            exercise_count=0,
            evidence_confidence=0.0,
            mastery_source="baseline_prior",
            mastery_interval=None,
            last_observation_date=None,
            last_observation_correctness=None,
            last_review_date=None,
            review_count=0,
            recommended_date=None,
            recommendation_source="unavailable",
        )
        state = classify_topic(item, {})
        self.assertEqual(state.primary_state, "insufficient_evidence")
        self.assertEqual(state.effective_recommended_date, AS_OF)
        self.assertTrue(state.alert_eligible)

    def test_a4_recent_failure_beats_insufficient_evidence(self):
        error = recent_error(3, 2, 0.0)
        item = insight(
            3,
            evidence_confidence=0.20,
            recent_error=error,
            last_observation_date=error.occurred_on,
            last_observation_correctness=0.0,
        )
        state = classify_topic(item, {})
        self.assertEqual(state.primary_state, "recent_failure")
        self.assertIn("insufficient_evidence", state.secondary_flags)
        self.assertEqual(state.effective_recommended_date, AS_OF)

    def test_a5_later_correction_clears_recent_failure_but_keeps_history(self):
        error = recent_error(4, 3, 0.0)
        item = insight(
            4,
            evidence_confidence=0.30,
            recent_error=error,
            last_observation_date=AS_OF - timedelta(days=1),
            last_observation_correctness=0.90,
        )
        state = classify_topic(item, {})
        self.assertEqual(state.primary_state, "insufficient_evidence")
        self.assertIn("historical_error", state.secondary_flags)

    def test_subthreshold_followup_does_not_count_as_correction(self):
        error = recent_error(20, 3, 0.0)
        item = insight(
            20,
            recent_error=error,
            last_observation_date=AS_OF - timedelta(days=1),
            last_observation_correctness=0.70,
        )
        state = classify_topic(item, {})
        self.assertEqual(state.primary_state, "recent_failure")

    def test_recent_failure_window_is_seven_calendar_dates_including_cutoff(self):
        inside = recent_error(21, 6, 0.0)
        outside = recent_error(22, 7, 0.0)
        inside_state = classify_topic(
            insight(
                21,
                recent_error=inside,
                last_observation_date=inside.occurred_on,
                last_observation_correctness=0.0,
            ),
            {},
        )
        outside_state = classify_topic(
            insight(
                22,
                recent_error=outside,
                last_observation_date=outside.occurred_on,
                last_observation_correctness=0.0,
            ),
            {},
        )
        self.assertEqual(inside_state.primary_state, "recent_failure")
        self.assertEqual(outside_state.primary_state, "stable")

    def test_a6_overdue_uses_memory_probability_and_recommended_date(self):
        item = insight(
            5,
            recall_probability=0.60,
            target_recall=0.78,
            recommended_date=AS_OF - timedelta(days=3),
        )
        state = classify_topic(item, {})
        self.assertEqual(state.primary_state, "overdue_review")
        self.assertEqual(state.overdue_days, 3)
        self.assertEqual(
            state.reason_codes,
            ("recall_below_target", "recommended_date_passed"),
        )

    def test_recent_failure_beats_overdue_and_preserves_secondary_flag(self):
        error = recent_error(6, 1, 0.5)
        item = insight(
            6,
            recent_error=error,
            last_observation_date=error.occurred_on,
            last_observation_correctness=0.5,
            recall_probability=0.60,
            recommended_date=AS_OF - timedelta(days=4),
        )
        state = classify_topic(item, {})
        self.assertEqual(state.primary_state, "recent_failure")
        self.assertIn("overdue_review", state.secondary_flags)

    def test_existing_evidence_overrides_not_started_course_text(self):
        state = classify_topic(insight(7, course_status="not_started"), {})
        self.assertNotEqual(state.primary_state, "unlearned")
        self.assertIn("course_status_mismatch", state.secondary_flags)

    def test_stable_is_used_only_when_no_higher_condition_matches(self):
        state = classify_topic(insight(8), {})
        self.assertEqual(state.primary_state, "stable")
        self.assertEqual(state.recommended_action, "continue_schedule")
        self.assertFalse(state.alert_eligible)

    def test_prerequisite_pending_is_secondary_not_access_gate(self):
        prerequisite = classify_topic(
            insight(9, observation_count=1, exercise_count=1, evidence_confidence=0.2),
            {},
        )
        item = insight(10, direct_prerequisite_keys=(prerequisite.insight.topic_key,))
        state = classify_topic(item, {prerequisite.insight.topic_key: prerequisite})
        self.assertEqual(state.primary_state, "stable")
        self.assertIn("prerequisite_pending", state.secondary_flags)
        self.assertTrue(state.is_actionable)

    def test_archived_prerequisite_is_history_only_and_archived_topics_filter(self):
        archived = classify_topic(
            insight(11, subject_status="archived", is_archived=True),
            {},
        )
        item = insight(12, direct_prerequisite_keys=(archived.insight.topic_key,))
        state = classify_topic(item, {archived.insight.topic_key: archived})
        self.assertIn("archived_prerequisite_history", state.secondary_flags)
        self.assertNotIn("prerequisite_pending", state.secondary_flags)

        active_only = classify_topics((item, archived.insight))
        with_archived = classify_topics((item, archived.insight), include_archived=True)
        self.assertEqual(len(active_only), 1)
        self.assertEqual(len(with_archived), 2)
        self.assertFalse(next(x for x in with_archived if x.insight.is_archived).alert_eligible)

    def test_unresolved_prerequisite_is_diagnostic_not_guessed(self):
        unknown = topic_key_for_id(99)
        item = insight(
            13,
            direct_prerequisite_keys=(unknown,),
            unresolved_prerequisite_keys=(unknown,),
        )
        state = classify_topic(item, {})
        self.assertIn("prerequisite_data_diagnostic", state.secondary_flags)
        self.assertNotIn("prerequisite_pending", state.secondary_flags)

    def test_bulk_sort_is_deterministic_and_action_states_come_first(self):
        failure_error = recent_error(14, 0, 0.0)
        failure = insight(
            14,
            recent_error=failure_error,
            last_observation_date=AS_OF,
            last_observation_correctness=0.0,
        )
        insufficient = insight(15, evidence_confidence=0.2)
        stable = insight(16)
        first = classify_topics((stable, insufficient, failure))
        second = classify_topics((failure, stable, insufficient))
        self.assertEqual(first, second)
        self.assertEqual(
            [item.primary_state for item in first],
            ["recent_failure", "insufficient_evidence", "stable"],
        )

    def test_same_state_sort_uses_recommended_date_then_priority_then_key(self):
        later = insight(
            23,
            recall_probability=0.60,
            recommended_date=AS_OF - timedelta(days=1),
        )
        earlier = insight(
            24,
            recall_probability=0.70,
            recommended_date=AS_OF - timedelta(days=3),
        )
        values = classify_topics((later, earlier))
        self.assertEqual([item.insight.topic_id for item in values], [24, 23])

    def test_future_only_records_version_change_does_not_change_state_or_sort(self):
        before = classify_topic(insight(25, records_version="records-before"), {})
        after = classify_topic(insight(25, records_version="records-after"), {})
        self.assertEqual(before.primary_state, after.primary_state)
        self.assertEqual(before.secondary_flags, after.secondary_flags)
        self.assertEqual(before.effective_recommended_date, after.effective_recommended_date)
        self.assertEqual(before.sort_key, after.sort_key)

    def test_filtering_does_not_reclassify_or_mutate_results(self):
        values = classify_topics((insight(17), insight(18, evidence_confidence=0.2)))
        before = tuple(values)
        filtered = filter_classified_topics(
            values, primary_states=("insufficient_evidence",)
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].primary_state, "insufficient_evidence")
        self.assertEqual(values, before)
        with self.assertRaises(FrozenInstanceError):
            values[0].primary_state = "stable"

    def test_policy_is_versioned_and_thresholds_are_not_page_inputs(self):
        self.assertEqual(F2_RULES_V1.recent_failure_days, 7)
        self.assertEqual(F2_RULES_V1.evidence_confidence_threshold, 0.50)
        self.assertEqual(F2_RULES_V1.failure_threshold, 0.50)
        self.assertEqual(F2_RULES_V1.correction_threshold, 0.80)
        with self.assertRaises(FrozenInstanceError):
            F2_RULES_V1.failure_threshold = 0.60
        with self.assertRaisesRegex(ValueError, "KnowledgeStatePolicy"):
            classify_topic(insight(19), {}, policy={})


if __name__ == "__main__":
    unittest.main()
