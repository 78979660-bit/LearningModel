from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_app.core.oj_identity import (
    normalize_external_problem_key,
    normalize_source_key,
    problem_key_for,
    validate_problem_key,
)
from study_app.data import database
from data_test_support import initialize_legacy_base_database


class OJProblemIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "oj.sqlite"
        database.initialize_database(self.db_path)
        database.install_oj_schema(self.db_path)

    def test_normalization_and_key_are_strict_and_deterministic(self) -> None:
        self.assertEqual(normalize_source_key(" LeetCode "), "leetcode")
        self.assertEqual(normalize_external_problem_key(" 001 "), "001")
        key = problem_key_for(" LeetCode ", " 001 ")
        self.assertEqual(key, problem_key_for("leetcode", "001"))
        self.assertEqual(validate_problem_key(key), key)
        self.assertEqual(len(key), 71)
        self.assertEqual(normalize_source_key("UPPER"), "upper")
        for value in (None, True, "", "bad source", "-bad"):
            with self.subTest(source=value), self.assertRaises(ValueError):
                normalize_source_key(value)
        for value in (None, True, "", "x\n2", "x" * 161):
            with self.subTest(external=value), self.assertRaises(ValueError):
                normalize_external_problem_key(value)
        for value in (None, True, "ojp:v1:xyz", key.upper()):
            with self.subTest(problem_key=value), self.assertRaises(ValueError):
                validate_problem_key(value)

    def test_same_title_different_identity_does_not_merge(self) -> None:
        first = database.register_oj_problem(
            "leetcode", "1", "Two Sum", db_path=self.db_path
        )
        second = database.register_oj_problem(
            "local", "1", "Two Sum", db_path=self.db_path
        )
        self.assertNotEqual(first.problem_key, second.problem_key)
        self.assertEqual(len(database.list_oj_problems(self.db_path)), 2)

    def test_registration_is_idempotent_and_rename_preserves_key(self) -> None:
        first = database.register_oj_problem(
            "leetcode", "1", "Two Sum", "https://example.test/1", self.db_path
        )
        same = database.register_oj_problem(
            " LEETCODE ", " 1 ", "Two Sum", "https://example.test/1", self.db_path
        )
        renamed = database.register_oj_problem(
            "leetcode", "1", "Two Sum / 两数之和", "", self.db_path
        )
        self.assertEqual(first.problem_key, same.problem_key)
        self.assertEqual(first.problem_key, renamed.problem_key)
        self.assertEqual(renamed.title, "Two Sum / 两数之和")
        self.assertEqual(renamed.source_url, "")
        self.assertEqual(len(database.list_oj_problems(self.db_path)), 1)

    def test_readers_are_byte_for_byte_read_only(self) -> None:
        problem = database.register_oj_problem(
            "leetcode", "1", "Two Sum", db_path=self.db_path
        )
        before = self.db_path.read_bytes()
        self.assertEqual(database.get_oj_problem(problem.problem_key, self.db_path), problem)
        self.assertEqual(database.list_oj_problems(self.db_path), (problem,))
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_missing_schema_is_diagnostic_and_not_implicitly_installed(self) -> None:
        old_db = Path(self.temp_dir.name) / "old.sqlite"
        initialize_legacy_base_database(old_db)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.list_oj_problems(old_db)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.register_oj_problem("leetcode", "1", "Two Sum", db_path=old_db)
        with database.connect_readonly(old_db) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oj_problems'"
                ).fetchone()
            )

    def test_invalid_input_does_not_create_database(self) -> None:
        missing = Path(self.temp_dir.name) / "missing" / "oj.sqlite"
        with self.assertRaises(ValueError):
            database.register_oj_problem("bad source", "1", "Title", db_path=missing)
        with self.assertRaises(ValueError):
            database.get_oj_problem("bad-key", missing)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())


if __name__ == "__main__":
    unittest.main()
