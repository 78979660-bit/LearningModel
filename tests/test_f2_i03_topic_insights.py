from __future__ import annotations

import copy
import unittest
from collections import Counter
from dataclasses import FrozenInstanceError, asdict
from datetime import date

from study_app.core.computation_context import DashboardComputationContext
from study_app.core.prerequisite_graph import make_prerequisite_edge
from study_app.core.topic_identity import make_topic_identity, topic_key_for_id
from study_app.core.topic_insights import (
    build_topic_insights,
    mastery_interval,
    recommended_review_date,
)


def fixture_model():
    return {
        "model_name": "fixture-v1",
        "warning_policy": {
            "bkt_model": {
                "enabled": True,
                "initial_prior": 0.35,
                "personal_evidence_confidence": {
                    "enabled": False,
                    "population_prior_strength": 6,
                },
            },
            "spaced_repetition_model": {
                "target_recall": 0.5,
                "base_half_life_days": 1.0,
                "min_half_life_days": 1.0,
                "max_half_life_days": 1.0,
                "half_life_weights": {},
            },
        },
        "subjects": [
            {
                "name": "S",
                "status": "active",
                "modules": [
                    {
                        "name": "M",
                        "status": "learning",
                        "topics": [
                            {"name": "A", "status": "not_started", "mastery": 0.35},
                            {"name": "B", "status": "learning", "mastery": 0.45},
                        ],
                    }
                ],
            },
            {
                "name": "Archived",
                "status": "archived",
                "modules": [
                    {
                        "name": "Old",
                        "status": "completed",
                        "topics": [{"name": "Z", "status": "completed", "mastery": 0.8}],
                    }
                ],
            },
        ],
    }


def registry():
    paths = ((1, "S", "M", "A"), (2, "S", "M", "B"), (3, "Archived", "Old", "Z"))
    return tuple(
        make_topic_identity(
            topic_key=topic_key_for_id(topic_id),
            topic_id=topic_id,
            subject_name=subject,
            module_name=module,
            topic_name=topic,
            identity_version="topic-identity-v1",
        )
        for topic_id, subject, module, topic in paths
    )


def problem_record(record_id, day, topic, correctnesses, *, error_cause=None):
    record = {
        "id": record_id,
        "date": day,
        "subject": "S",
        "module": "M",
        "topic": topic,
        "activity": "exercise",
        "source": "outside_class",
        "score": 100 * sum(correctnesses) / len(correctnesses),
        "problems": [
            {
                "title": f"{topic}-{index}",
                "related_topics": [topic],
                "correctness": value,
                "difficulty_score": 50,
            }
            for index, value in enumerate(correctnesses, start=1)
        ],
    }
    if error_cause is not None:
        record["error_causes"] = [{"topic": topic, "description": error_cause}]
    return record


def build(records, *, as_of=date(2028, 2, 28), model=None, edges=()):
    model = model or fixture_model()
    context = DashboardComputationContext(
        model, records, as_of, model["warning_policy"]
    )
    return build_topic_insights(model, records, as_of, context, registry(), edges)


class TopicInsightTests(unittest.TestCase):
    def test_no_observation_is_baseline_prior_without_interval_or_fake_date(self):
        unscored_review = {
            "id": 99,
            "date": "2028-02-27",
            "subject": "S",
            "module": "M",
            "topic": "A",
            "activity": "review",
            "source": "outside_class",
        }
        insights = build([unscored_review])
        item = insights[topic_key_for_id(1)]
        self.assertEqual(item.observation_count, 0)
        self.assertEqual(item.exercise_count, 0)
        self.assertEqual(item.mastery_source, "baseline_prior")
        self.assertIsNone(item.mastery_interval)
        self.assertIsNone(item.recommended_date)

    def test_five_problem_observations_are_one_independent_exercise(self):
        item = build([problem_record(10, "2028-02-27", "B", [1, 0, 1, 0.5, 1])])[
            topic_key_for_id(2)
        ]
        self.assertEqual(item.observation_count, 5)
        self.assertEqual(item.exercise_count, 1)
        self.assertIsNotNone(item.mastery_interval)
        self.assertEqual(item.last_review_date, date(2028, 2, 27))
        self.assertEqual(item.recommended_date, date(2028, 2, 28))

    def test_mastery_interval_uses_frozen_formula_without_rounding(self):
        low = mastery_interval(0.60, 0.20, 1)
        high = mastery_interval(0.60, 0.80, 1)
        self.assertAlmostEqual(low[0], 0.27)
        self.assertAlmostEqual(low[1], 0.93)
        self.assertAlmostEqual(high[0], 0.48)
        self.assertAlmostEqual(high[1], 0.72)
        self.assertLess(high[1] - high[0], low[1] - low[0])

    def test_recent_problem_error_survives_later_correction_without_fabrication(self):
        records = [
            problem_record(1, "2028-02-26", "B", [0], error_cause="边界条件漏判"),
            problem_record(2, "2028-02-27", "B", [1]),
        ]
        item = build(records)[topic_key_for_id(2)]
        self.assertEqual(item.recent_error.record_id, 1)
        self.assertEqual(item.recent_error.correctness, 0.0)
        self.assertEqual(item.recent_error.error_cause, "边界条件漏判")
        self.assertEqual(item.last_observation_correctness, 1.0)

    def test_missing_error_cause_stays_none(self):
        item = build([problem_record(1, "2028-02-27", "B", [0.5])])[
            topic_key_for_id(2)
        ]
        self.assertIsNone(item.recent_error.error_cause)

    def test_future_evidence_changes_no_cutoff_result(self):
        current = [problem_record(1, "2028-02-27", "B", [0])]
        before = build(current)[topic_key_for_id(2)]
        after = build(current + [problem_record(2, "2028-02-29", "B", [1])])[
            topic_key_for_id(2)
        ]
        before_fields = asdict(before)
        after_fields = asdict(after)
        self.assertNotEqual(before_fields.pop("records_version"), after_fields.pop("records_version"))
        self.assertEqual(before_fields, after_fields)

    def test_recommendation_uses_real_calendar_leap_day(self):
        self.assertEqual(
            recommended_review_date(date(2028, 2, 28), 1.0, 0.5),
            date(2028, 2, 29),
        )

    def test_one_context_computes_observations_bkt_and_memory_once_per_topic(self):
        from study_app.core import computation_context

        model = fixture_model()
        records = [problem_record(1, "2028-02-27", "B", [0, 1])]
        counts = Counter()
        original_observations = computation_context.iter_topic_observations
        original_bkt = computation_context.topic_bkt_state
        original_memory = computation_context.topic_memory_state

        def observations(*args, **kwargs):
            counts[("observations", args[2]["name"])] += 1
            return original_observations(*args, **kwargs)

        def bkt(*args, **kwargs):
            counts[("bkt", args[2]["name"])] += 1
            return original_bkt(*args, **kwargs)

        def memory(*args, **kwargs):
            counts[("memory", args[2]["name"])] += 1
            return original_memory(*args, **kwargs)

        computation_context.iter_topic_observations = observations
        computation_context.topic_bkt_state = bkt
        computation_context.topic_memory_state = memory
        self.addCleanup(setattr, computation_context, "iter_topic_observations", original_observations)
        self.addCleanup(setattr, computation_context, "topic_bkt_state", original_bkt)
        self.addCleanup(setattr, computation_context, "topic_memory_state", original_memory)

        context = DashboardComputationContext(
            model, records, date(2028, 2, 28), model["warning_policy"]
        )
        first = build_topic_insights(
            model, records, date(2028, 2, 28), context, registry(), ()
        )
        second = build_topic_insights(
            model, records, date(2028, 2, 28), context, registry(), ()
        )
        self.assertEqual(first, second)
        self.assertTrue(counts)
        self.assertTrue(all(value == 1 for value in counts.values()))

    def test_prerequisite_and_archived_metadata_are_read_only_inputs(self):
        identities = registry()
        edge = make_prerequisite_edge(
            identities[1].topic_key,
            identities[2].topic_key,
            "user_confirmed",
            {},
        )
        insights = build([], edges=(edge,))
        self.assertEqual(
            insights[identities[1].topic_key].direct_prerequisite_keys,
            (identities[2].topic_key,),
        )
        self.assertTrue(insights[identities[2].topic_key].is_archived)
        with self.assertRaises(TypeError):
            insights[identities[0].topic_key] = insights[identities[0].topic_key]
        with self.assertRaises(FrozenInstanceError):
            insights[identities[0].topic_key].topic_name = "changed"

    def test_mismatched_context_and_registry_are_rejected(self):
        model = fixture_model()
        context = DashboardComputationContext(
            model, [], date(2028, 2, 28), model["warning_policy"]
        )
        with self.assertRaisesRegex(ValueError, "上下文"):
            build_topic_insights(
                model, [], date(2028, 2, 29), context, registry(), ()
            )
        bad = list(registry())
        bad[0] = make_topic_identity(
            topic_key=bad[0].topic_key,
            topic_id=bad[0].topic_id,
            subject_name="S",
            module_name="M",
            topic_name="missing",
            identity_version="topic-identity-v1",
        )
        with self.assertRaisesRegex(ValueError, "未出现在模型"):
            build_topic_insights(model, [], date(2028, 2, 28), context, bad, ())

    def test_inputs_are_not_mutated(self):
        model = fixture_model()
        records = [problem_record(1, "2028-02-27", "B", [0.5])]
        model_before = copy.deepcopy(model)
        records_before = copy.deepcopy(records)
        build(records, model=model)
        self.assertEqual(model, model_before)
        self.assertEqual(records, records_before)


if __name__ == "__main__":
    unittest.main()
