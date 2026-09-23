from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source
from study_app.core.task_estimates import (
    CONFIRMED_TEMPLATE_ESTIMATE, USER_ESTIMATE, TaskEstimate,
    make_assistant_task_estimate, make_task_estimate, resolve_candidate_estimates,
    validate_estimated_minutes,
)
from study_app.data import database


def candidate(source_id: str = "model_topic_path:v1:abc") -> PlanCandidate:
    subject, kind = "计算机科学", "model_topic_practice"
    return PlanCandidate(task_id_for_source(subject, kind, source_id), subject, kind,
                         source_id, "图论练习")


def state(archived: bool = False) -> SimpleNamespace:
    return SimpleNamespace(subjects=(SimpleNamespace(name="计算机科学", archived=archived),))


class EstimateResolutionTests(unittest.TestCase):
    def test_assistant_estimate_is_local_stable_and_template_backed(self) -> None:
        base = candidate()
        first = make_assistant_task_estimate(base)
        second = make_assistant_task_estimate(base)
        self.assertEqual(first, second)
        self.assertEqual(first.source, CONFIRMED_TEMPLATE_ESTIMATE)
        self.assertTrue(1 <= first.estimated_minutes <= 1440)

        urgent = replace(base, priority_evidence={"priority": 0.9})
        proof = replace(base, title="等价性证明", priority_evidence={"priority": 0.1})
        mock_exam = replace(base, title="诊断卷", priority_evidence={"priority": 0.1})
        self.assertEqual(make_assistant_task_estimate(urgent).estimated_minutes, 30)
        self.assertEqual(make_assistant_task_estimate(proof).estimated_minutes, 30)
        self.assertEqual(make_assistant_task_estimate(mock_exam).estimated_minutes, 60)

    def test_boundaries_and_invalid_inputs(self) -> None:
        self.assertEqual(validate_estimated_minutes(1), 1)
        self.assertEqual(validate_estimated_minutes(1440), 1440)
        for value in (None, 0, 1441, -1, True, False, 1.0, float("nan"), "30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_estimated_minutes(value)

    def test_user_overrides_confirmed_template_and_missing_excludes(self) -> None:
        first, second = candidate(), candidate("model_topic_path:v1:def")
        collection = CandidateCollection((first, second), ())
        template = make_task_estimate(first, 45, CONFIRMED_TEMPLATE_ESTIMATE)
        user = make_task_estimate(first, 30, USER_ESTIMATE)
        result = resolve_candidate_estimates(collection, {first.task_id: user},
                                             {first.task_id: template})
        self.assertEqual((result.candidates[0].estimated_minutes,
                          result.candidates[0].estimate_source), (30, USER_ESTIMATE))
        self.assertEqual([(x.task_id, x.reason) for x in result.excluded],
                         [(second.task_id, "missing_estimate")])
        template_only = resolve_candidate_estimates(collection, {}, {first.task_id: template})
        self.assertEqual(template_only.candidates[0].estimate_source, CONFIRMED_TEMPLATE_ESTIMATE)
        no_estimate = resolve_candidate_estimates(collection, {}, {})
        self.assertEqual(len(no_estimate.candidates), 0)
        self.assertEqual(len(no_estimate.excluded), 2)

    def test_mismatched_identity_and_unconfirmed_source_rejected(self) -> None:
        first = candidate()
        with self.assertRaises(ValueError):
            make_task_estimate(replace(first, task_id="title:图论练习"), 30, USER_ESTIMATE)
        with self.assertRaises(ValueError):
            make_task_estimate(first, 30, "historical_actual")
        with self.assertRaises(ValueError):
            resolve_candidate_estimates(CandidateCollection((first,), ()), {}, {
                first.task_id: TaskEstimate(first.task_id, 45, "historical_actual")})


class EstimatePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "estimates.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute("INSERT INTO subjects(name, status) VALUES ('计算机科学', 'learning')")
            connection.execute(database.STUDY_TASK_ESTIMATES_TABLE_SQL)

    def rows(self) -> list[tuple]:
        with database.connect_readonly(self.db_path) as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT task_id, estimated_minutes, source FROM study_task_estimates ORDER BY task_id")]

    def test_round_trip_override_and_archived_historical_read(self) -> None:
        item = candidate()
        database.save_task_estimate(state(), item, 50, CONFIRMED_TEMPLATE_ESTIMATE, self.db_path)
        self.assertEqual(database.get_task_estimate(item, self.db_path).source,
                         CONFIRMED_TEMPLATE_ESTIMATE)
        database.save_task_estimate(state(), item, 25, USER_ESTIMATE, self.db_path)
        self.assertEqual(self.rows(), [(item.task_id, 25, USER_ESTIMATE)])
        self.assertEqual(database.get_task_estimate(item, self.db_path).estimated_minutes, 25)
        with database.connect(self.db_path) as connection:
            connection.execute("UPDATE subjects SET status = 'archived' WHERE name = '计算机科学'")
        self.assertEqual(database.get_task_estimate(item, self.db_path).estimated_minutes, 25)
        with self.assertRaises(ValueError):
            database.save_task_estimate(state(), item, 26, USER_ESTIMATE, self.db_path)
        self.assertEqual(self.rows(), [(item.task_id, 25, USER_ESTIMATE)])

    def test_invalid_inputs_and_archived_state_leave_row_unchanged(self) -> None:
        item = candidate()
        database.save_task_estimate(state(), item, 35, USER_ESTIMATE, self.db_path)
        before = self.rows()
        for minutes, source in ((0, USER_ESTIMATE), (True, USER_ESTIMATE),
                                (30, "historical_actual")):
            with self.subTest(minutes=minutes, source=source), self.assertRaises(ValueError):
                database.save_task_estimate(state(), item, minutes, source, self.db_path)
        with self.assertRaises(ValueError):
            database.save_task_estimate(state(True), item, 40, USER_ESTIMATE, self.db_path)
        self.assertEqual(self.rows(), before)

    def test_missing_table_is_not_reinstalled_and_sql_check_is_hard(self) -> None:
        item = candidate()
        other_path = Path(self.temp_dir.name) / "uninstalled.sqlite"
        database.initialize_database(other_path)
        with database.connect(other_path) as connection:
            connection.execute("DROP TABLE study_task_estimates")
            connection.execute("INSERT INTO subjects(name, status) VALUES ('计算机科学', 'learning')")
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.save_task_estimate(state(), item, 30, USER_ESTIMATE, other_path)
        with database.connect_readonly(other_path) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'study_task_estimates'").fetchone())
        with database.connect(self.db_path) as connection:
            subject_id = connection.execute("SELECT id FROM subjects WHERE name='计算机科学'").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO study_task_estimates(task_id, subject_id, source_kind, "
                                   "source_id, estimated_minutes, source) VALUES (?, ?, ?, ?, ?, ?)",
                                   (item.task_id, subject_id, item.source_kind, item.source_id, 0, USER_ESTIMATE))
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
