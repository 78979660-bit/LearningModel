from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from pathlib import Path

from study_app.core.knowledge_alerts import KnowledgeAlert, KnowledgeAlertEvent
from study_app.core.knowledge_explanations import (
    SOURCE_LAYERS,
    UNKNOWN_TEXT,
    explain_knowledge_topic,
)
from study_app.core.knowledge_states import classify_topic
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import RecentTopicError, TopicInsight
from study_app.data import database


AS_OF = date(2026, 9, 17)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        last_observation_date=AS_OF - timedelta(days=2),
        last_observation_correctness=1.0,
        last_review_date=AS_OF - timedelta(days=3),
        review_count=2,
        half_life_days=8.0,
        recall_probability=0.90,
        target_recall=0.78,
        recommended_date=AS_OF + timedelta(days=2),
        recommendation_source="half_life_target_recall",
        direct_prerequisite_keys=(),
        unresolved_prerequisite_keys=(),
        as_of_date=AS_OF,
        model_version="model-v1",
        records_version="records-v1",
    )
    return replace(base, **changes)


def recent_error(topic_id: int, *, cause=None, title="错误题目") -> RecentTopicError:
    return RecentTopicError(
        record_id=topic_id * 100,
        occurred_on=AS_OF - timedelta(days=1),
        title=title,
        correctness=0.0,
        result_source="problem.correctness",
        error_cause=cause,
    )


def traced():
    return {
        "coverage": "traced",
        "legacy_provenance_gap": False,
        "contributions": [
            {
                "record_id": 7,
                "record_status": "verified",
                "old_mastery": 0.4,
                "new_mastery": 0.6,
                "evidence_items": [
                    {"title": "sensitive full problem statement", "secret": "token"}
                ],
            }
        ],
    }


def alert_for(item, *, alert_id=1, topic_key=None):
    insight_value = item.insight
    return KnowledgeAlert(
        id=alert_id,
        fingerprint="a" * 64,
        topic_key=topic_key or insight_value.topic_key,
        alert_type=item.primary_state,
        status="active",
        rule_version=item.rule_version,
        evidence_version=insight_value.evidence_version,
        condition_cycle="condition",
        as_of_date=insight_value.as_of_date,
        recommended_date=item.effective_recommended_date,
        priority=item.alert_priority,
        snapshot={"raw": "must not be copied into explanation"},
        snoozed_until=None,
        handled_at=None,
        resolved_at=None,
        created_at="2026-09-17 08:00:00",
        updated_at="2026-09-17 08:00:00",
    )


class KnowledgeExplanationTests(unittest.TestCase):
    def test_a12_four_layers_show_versions_formula_inputs_and_cutoff(self):
        item = classify_topic(insight(1), {})
        explanation = explain_knowledge_topic(item, {}, traced())
        self.assertEqual(
            tuple(layer.name for layer in explanation.layers), SOURCE_LAYERS
        )
        facts = {
            fact.code: fact
            for layer in explanation.layers
            for fact in layer.facts
        }
        self.assertEqual(facts["primary_state"].value, "stable")
        self.assertEqual(facts["model_version"].value, "model-v1")
        self.assertEqual(facts["records_version"].value, "records-v1")
        self.assertEqual(facts["as_of_date"].value, AS_OF.isoformat())
        self.assertEqual(facts["half_life_days"].value, "8")
        self.assertEqual(facts["target_recall"].value, "0.78")
        self.assertIn("不是统计置信区间", facts["mastery_interval_rule"].value)
        self.assertTrue(all(fact.source_reference for layer in explanation.layers for fact in layer.facts))

    def test_missing_error_cause_stays_unknown_and_is_not_fabricated(self):
        error = recent_error(2, cause=None)
        item = classify_topic(
            insight(
                2,
                recent_error=error,
                last_observation_date=error.occurred_on,
                last_observation_correctness=0.0,
            ),
            {},
        )
        explanation = explain_knowledge_topic(item, {}, traced())
        evidence = {fact.code: fact for fact in explanation.layer("evidence").facts}
        self.assertEqual(evidence["recent_error_cause"].value, UNKNOWN_TEXT)
        self.assertEqual(evidence["recent_error_cause"].status, "unknown")
        self.assertIn("recent_error_cause_unrecorded", explanation.diagnostics)

    def test_recent_problem_text_is_single_line_limited_and_trace_payload_is_omitted(self):
        long_title = "个人信息\n" + "题面" * 200
        error = recent_error(3, cause="  边界条件\n遗漏  ", title=long_title)
        item = classify_topic(
            insight(
                3,
                recent_error=error,
                last_observation_date=error.occurred_on,
                last_observation_correctness=0.0,
            ),
            {},
        )
        explanation = explain_knowledge_topic(item, {}, traced())
        evidence = {fact.code: fact for fact in explanation.layer("evidence").facts}
        self.assertNotIn("\n", evidence["recent_error_title"].value)
        self.assertLessEqual(len(evidence["recent_error_title"].value), 160)
        self.assertEqual(evidence["recent_error_cause"].value, "边界条件 遗漏")
        rendered = repr(explanation)
        self.assertNotIn("sensitive full problem statement", rendered)
        self.assertNotIn("token", rendered)

    def test_legacy_trace_gap_is_explicit_and_no_chain_is_invented(self):
        item = classify_topic(insight(4), {})
        trace = {
            "coverage": "partial_legacy",
            "legacy_provenance_gap": True,
            "contributions": [],
            "unverified_prior_mastery": 0.5,
        }
        explanation = explain_knowledge_topic(item, {}, trace)
        evidence = {fact.code: fact for fact in explanation.layer("evidence").facts}
        self.assertEqual(evidence["trace_coverage"].value, "partial_legacy")
        self.assertEqual(evidence["trace_contributions"].value, UNKNOWN_TEXT)
        self.assertIn("legacy_mastery_provenance_gap", explanation.diagnostics)
        self.assertNotIn("0.5", evidence["trace_contributions"].value)

    def test_missing_and_malformed_trace_become_diagnostics_not_guesses(self):
        item = classify_topic(insight(5), {})
        missing = explain_knowledge_topic(item, {}, None)
        self.assertIn("mastery_trace_missing_or_invalid", missing.diagnostics)
        malformed = explain_knowledge_topic(
            item,
            {},
            {
                "coverage": "magical",
                "legacy_provenance_gap": ["bad"],
                "contributions": ["bad", {"record_id": 2, "record_status": "invented"}],
            },
        )
        self.assertIn("mastery_trace_coverage_invalid", malformed.diagnostics)
        self.assertIn("legacy_gap_flag_invalid", malformed.diagnostics)
        self.assertIn("mastery_trace_contribution_invalid:0", malformed.diagnostics)
        self.assertIn("mastery_trace_record_status_invalid:1", malformed.diagnostics)

    def test_explicit_prerequisite_states_and_unknowns_are_distinguished(self):
        stable = classify_topic(insight(6), {})
        archived = classify_topic(
            insight(7, subject_status="archived", is_archived=True), {}
        )
        missing_key = topic_key_for_id(99)
        item = classify_topic(
            insight(
                8,
                direct_prerequisite_keys=(
                    stable.insight.topic_key,
                    archived.insight.topic_key,
                    missing_key,
                ),
                unresolved_prerequisite_keys=(missing_key,),
            ),
            {
                stable.insight.topic_key: stable,
                archived.insight.topic_key: archived,
            },
        )
        explanation = explain_knowledge_topic(
            item,
            {
                stable.insight.topic_key: stable,
                archived.insight.topic_key: archived,
            },
            traced(),
        )
        prerequisite = next(
            fact
            for fact in explanation.layer("evidence").facts
            if fact.code == "prerequisite_states"
        )
        self.assertIn("stable", prerequisite.value)
        self.assertIn("封存历史", prerequisite.value)
        self.assertIn(UNKNOWN_TEXT, prerequisite.value)
        self.assertIn(f"prerequisite_unresolved:{missing_key}", explanation.diagnostics)

    def test_workflow_layer_uses_instance_and_event_metadata_not_raw_payloads(self):
        error = recent_error(9)
        item = classify_topic(
            insight(
                9,
                recent_error=error,
                last_observation_date=error.occurred_on,
                last_observation_correctness=0.0,
            ),
            {},
        )
        alert = alert_for(item)
        event = KnowledgeAlertEvent(
            id=1,
            alert_id=alert.id,
            event_type="created",
            from_status=None,
            to_status="active",
            effective_date=AS_OF,
            actor="system",
            detail={"raw": "private workflow detail"},
            created_at="2026-09-17 08:00:00",
        )
        explanation = explain_knowledge_topic(
            item, {}, traced(), alert=alert, alert_events=(event,)
        )
        workflow = {fact.code: fact for fact in explanation.layer("workflow").facts}
        self.assertEqual(workflow["alert_status"].value, "active")
        self.assertIn("created", workflow["event_history"].value)
        rendered = repr(explanation)
        self.assertNotIn("must not be copied", rendered)
        self.assertNotIn("private workflow detail", rendered)

    def test_mismatched_workflow_sources_are_diagnostic_and_not_displayed(self):
        item = classify_topic(insight(10), {})
        alert = alert_for(item, topic_key=topic_key_for_id(999))
        event = KnowledgeAlertEvent(
            id=1,
            alert_id=42,
            event_type="created",
            from_status=None,
            to_status="active",
            effective_date=AS_OF,
            actor="system",
            detail={},
            created_at="2026-09-17 08:00:00",
        )
        explanation = explain_knowledge_topic(
            item, {}, traced(), alert=alert, alert_events=(event,)
        )
        self.assertIn("alert_topic_mismatch", explanation.diagnostics)
        workflow = {fact.code: fact for fact in explanation.layer("workflow").facts}
        self.assertEqual(workflow["alert_instance"].value, UNKNOWN_TEXT)

    def test_explanation_is_byte_for_byte_read_only_for_sqlite_model_and_records(self):
        item = classify_topic(insight(11), {})
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "read_only.sqlite"
            model_path = root / "model.json"
            records_path = root / "records.json"
            database.initialize_database(db_path)
            model_path.write_text('{"model":"unchanged"}', encoding="utf-8")
            records_path.write_text('{"records":[]}', encoding="utf-8")
            before = tuple(sha256(path) for path in (db_path, model_path, records_path))
            explanation = explain_knowledge_topic(item, {}, traced())
            after = tuple(sha256(path) for path in (db_path, model_path, records_path))
        self.assertEqual(before, after)
        self.assertEqual(explanation.as_of_date, AS_OF)

    def test_inputs_and_result_are_not_mutated(self):
        item = classify_topic(insight(12), {})
        trace = traced()
        before = copy.deepcopy(trace)
        explanation = explain_knowledge_topic(item, {}, trace)
        self.assertEqual(trace, before)
        with self.assertRaises(FrozenInstanceError):
            explanation.topic_key = "changed"
        with self.assertRaises(FrozenInstanceError):
            explanation.layers[0].facts[0].value = "changed"

    def test_invalid_top_level_inputs_are_rejected_without_io(self):
        item = classify_topic(insight(13), {})
        with self.assertRaisesRegex(ValueError, "ClassifiedTopic"):
            explain_knowledge_topic({}, {}, traced())
        with self.assertRaisesRegex(ValueError, "映射"):
            explain_knowledge_topic(item, (), traced())
        with self.assertRaisesRegex(ValueError, "tuple"):
            explain_knowledge_topic(item, {}, traced(), alert_events=[])


if __name__ == "__main__":
    unittest.main()
