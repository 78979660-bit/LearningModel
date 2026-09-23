from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_app.core.oj_attempts import (
    make_oj_attempt_input,
    normalize_attempted_at,
    validate_duration_seconds,
)
from study_app.core.oj_identity import problem_key_for
from study_app.data import database


class OJAttemptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "attempts.sqlite"
        database.initialize_database(self.db_path)
        database.install_oj_schema(self.db_path)
        self.problem = database.register_oj_problem(
            "leetcode", "1", "Two Sum", db_path=self.db_path
        )

    def attempt(self, **overrides):
        values = {
            "problem_key": self.problem.problem_key,
            "attempted_at": "2026-09-17T20:00:00+08:00",
            "result": "wrong",
            "duration_seconds": 600,
            "independence": "guided",
            "hint_level": "concept",
            "error_type": "algorithm",
            "notes": "状态转移遗漏",
            "source_attempt_key": None,
            "db_path": self.db_path,
        }
        values.update(overrides)
        return database.append_oj_attempt(**values)

    def test_time_and_duration_are_strict(self) -> None:
        self.assertEqual(
            normalize_attempted_at("2026-09-17T20:00:00+08:00"),
            "2026-09-17T12:00:00Z",
        )
        for value in (None, "", "2026-09-17T20:00:00", "not-time"):
            with self.subTest(time=value), self.assertRaises(ValueError):
                normalize_attempted_at(value)
        for value in (None, True, False, 0, -1, 1.0, "300", float("nan")):
            with self.subTest(duration=value), self.assertRaises(ValueError):
                validate_duration_seconds(value)

    def test_all_frozen_fields_round_trip_and_attempts_append(self) -> None:
        first = self.attempt(source_attempt_key="manual:1")
        second = self.attempt(
            attempted_at="2026-09-18T09:00:00+08:00",
            result="accepted",
            duration_seconds=420,
            independence="independent",
            hint_level="none",
            error_type="none",
            notes="独立通过",
            source_attempt_key="manual:2",
        )
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        attempts = database.list_oj_attempts(self.problem.problem_key, self.db_path)
        self.assertEqual(attempts, (first, second))
        self.assertEqual(attempts[0].attempted_at, "2026-09-17T12:00:00Z")
        self.assertEqual(attempts[1].notes, "独立通过")

    def test_result_error_combinations_are_enforced_before_write(self) -> None:
        for result, error in (("accepted", "algorithm"), ("wrong", "none")):
            with self.subTest(result=result, error=error), self.assertRaises(ValueError):
                self.attempt(result=result, error_type=error)
        self.assertEqual(database.list_oj_attempts(self.problem.problem_key, self.db_path), ())

    def test_all_enums_reject_unknown_values(self) -> None:
        base = dict(
            problem_key=self.problem.problem_key,
            attempted_at="2026-09-17T12:00:00Z",
            result="wrong",
            duration_seconds=1,
            independence="guided",
            hint_level="concept",
            error_type="unknown",
        )
        for field in ("result", "independence", "hint_level", "error_type"):
            values = dict(base)
            values[field] = "invalid"
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_oj_attempt_input(**values)

    def test_idempotency_replays_identical_and_rejects_conflict(self) -> None:
        first = self.attempt(source_attempt_key="import:row-1")
        replay = self.attempt(source_attempt_key="import:row-1")
        self.assertEqual(replay, first)
        with self.assertRaises(ValueError):
            self.attempt(source_attempt_key="import:row-1", duration_seconds=601)
        self.assertEqual(database.list_oj_attempts(self.problem.problem_key, self.db_path), (first,))

    def test_same_payload_without_key_is_a_new_fact(self) -> None:
        first = self.attempt()
        second = self.attempt()
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        self.assertEqual(len(database.list_oj_attempts(self.problem.problem_key, self.db_path)), 2)

    def test_missing_problem_and_invalid_payload_leave_zero_rows(self) -> None:
        with self.assertRaises(LookupError):
            self.attempt(problem_key=problem_key_for("local", "missing"))
        with self.assertRaises(ValueError):
            self.attempt(duration_seconds="600")
        with database.connect_readonly(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM oj_attempts").fetchone()[0], 0)

    def test_reader_is_byte_for_byte_read_only(self) -> None:
        self.attempt()
        before = self.db_path.read_bytes()
        self.assertEqual(len(database.list_oj_attempts(self.problem.problem_key, self.db_path)), 1)
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
