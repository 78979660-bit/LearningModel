from __future__ import annotations

import copy
import unittest

from study_app.core.prior_time_backtest import freeze_prior_catalog, time_backtest


def model():
    return {
        "model_name": "fixture_model_v1",
        "warning_policy": {"bkt_model": {"enabled": True, "time_effect": {"enabled": False},
                                         "personal_evidence_confidence": {"enabled": False}}},
        "subjects": [{"name": "S", "modules": [{"name": "M", "topics": [
            {"name": "T", "status": "learning", "mastery": 0.3, "difficulty": 0.5},
            {"name": "U", "status": "learning", "mastery": 0.8,
             "mastery_prior": 0.1, "difficulty": 0.5},
        ]}]}],
    }


def record(record_id, day, score, topic="T"):
    return {"id": record_id, "date": day, "subject": "S", "module": "M", "topic": topic,
            "activity": "review", "source": "self_study", "score": score}


class FrozenPriorBacktestTests(unittest.TestCase):
    def test_prior_sources_are_frozen_and_distinct_from_display(self):
        snapshot = model()
        catalog = freeze_prior_catalog(snapshot, snapshot_id="commit:x", snapshot_date="2026-07-17")
        t = next(item for key, item in catalog["topics"].items() if '"T"' in key)
        u = next(item for key, item in catalog["topics"].items() if '"U"' in key)
        self.assertEqual(t["source"], "snapshot_mastery_older_provenance_unverified")
        self.assertEqual(u["source"], "snapshot_explicit_mastery_prior")
        self.assertNotEqual(u["effective_prior"], u["display_mastery_at_snapshot"])
        snapshot["subjects"][0]["modules"][0]["topics"][0]["mastery"] = 0.99
        self.assertEqual(t["source_value"], 0.3)

    def test_future_and_same_day_outcomes_cannot_enter_training(self):
        snapshot = model()
        records = [record(1, "2026-07-18", 0), record(2, "2026-07-20", 100),
                   record(3, "2026-07-20", 0)]
        result = time_backtest(snapshot, records, snapshot_id="commit:x",
                               snapshot_date="2026-07-17", test_from="2026-07-20", seed=17)
        self.assertEqual(result["metrics"]["count"], 2)
        self.assertEqual([item["training_record_ids"] for item in result["events"]], [[1], [1]])
        self.assertEqual(result["events"][0]["forecast"], result["events"][1]["forecast"])
        altered = copy.deepcopy(records)
        altered.append(record(4, "2026-07-21", 100))
        later = time_backtest(snapshot, altered, snapshot_id="commit:x",
                              snapshot_date="2026-07-17", test_from="2026-07-20", seed=17)
        self.assertEqual(result["events"], later["events"][:2])

    def test_snapshot_date_excludes_older_evidence_and_fixed_seed_reproduces_metrics(self):
        snapshot = model()
        records = [record(0, "2026-07-16", 100), record(1, "2026-07-18", 0),
                   record(2, "2026-07-20", 100)]
        args = dict(snapshot_id="commit:x", snapshot_date="2026-07-17",
                    test_from="2026-07-20", seed=4)
        first = time_backtest(snapshot, records, **args)
        second = time_backtest(snapshot, records, **args)
        self.assertEqual(first, second)
        self.assertEqual(first["events"][0]["training_record_ids"], [1])
        self.assertGreaterEqual(first["metrics"]["brier"], 0)
        self.assertLessEqual(first["metrics"]["brier"], 1)

    def test_fractional_outcomes_are_excluded_from_binary_calibration(self):
        result = time_backtest(model(), [record(1, "2026-07-20", 50)],
                               snapshot_id="commit:x", snapshot_date="2026-07-17",
                               test_from="2026-07-20")
        self.assertEqual(result["metrics"]["count"], 0)
        self.assertEqual(result["skipped_fractional_outcomes"], 1)

    def test_invalid_temporal_split_is_rejected(self):
        with self.assertRaises(ValueError):
            time_backtest(model(), [], snapshot_id="commit:x", snapshot_date="2026-07-17",
                          test_from="2026-07-17")


if __name__ == "__main__":
    unittest.main()
