from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from study_app.core.day_budget_input import validate_subject_exam_date
from study_app.data import database


class ExamDateInputTests(unittest.TestCase):
    def test_optional_real_date_and_past_date(self) -> None:
        self.assertIsNone(validate_subject_exam_date(None))
        self.assertIsNone(validate_subject_exam_date(""))
        self.assertEqual(validate_subject_exam_date("2028-02-29"), "2028-02-29")
        self.assertEqual(validate_subject_exam_date("2026-09-15"), "2026-09-15")

    def test_invalid_date_types_and_non_calendar_dates(self) -> None:
        for value in (True, 20260916, "2026-9-16", "2026-02-29", "2026-13-01", "tomorrow"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_subject_exam_date(value)


class ExamDatePersistenceTests(unittest.TestCase):
    @staticmethod
    def state(*, archived_cs: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            subjects=(
                SimpleNamespace(name="计算机科学", archived=archived_cs),
                SimpleNamespace(name="高等数学", archived=True),
            )
        )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "exam_dates.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute("INSERT INTO subjects(name, status) VALUES ('计算机科学', 'learning')")
            connection.execute("INSERT INTO subjects(name, status) VALUES ('高等数学', 'archived')")
            connection.execute(database.STUDY_SUBJECT_EXAM_DATES_TABLE_SQL)

    def test_active_subject_round_trip_and_clear(self) -> None:
        self.assertIsNone(database.get_subject_exam_date("计算机科学", self.db_path))
        self.assertEqual(
            database.save_subject_exam_date(self.state(), "计算机科学", "2026-09-20", self.db_path),
            "2026-09-20",
        )
        self.assertEqual(database.get_subject_exam_date("计算机科学", self.db_path), "2026-09-20")
        self.assertIsNone(database.save_subject_exam_date(self.state(), "计算机科学", "", self.db_path))
        self.assertIsNone(database.get_subject_exam_date("计算机科学", self.db_path))
        with database.connect_readonly(self.db_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM study_subject_exam_dates").fetchone()[0]
        self.assertEqual(count, 1)

    def test_archived_subject_date_is_readable_but_not_writable(self) -> None:
        database.save_subject_exam_date(self.state(), "计算机科学", "2026-09-20", self.db_path)
        archived_state = self.state(archived_cs=True)
        self.assertEqual(database.get_subject_exam_date("计算机科学", self.db_path), "2026-09-20")
        for value in ("2026-09-21", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                database.save_subject_exam_date(archived_state, "计算机科学", value, self.db_path)
        with self.assertRaises(ValueError):
            database.save_subject_exam_date(self.state(), "高等数学", "2026-09-20", self.db_path)
        self.assertEqual(database.get_subject_exam_date("计算机科学", self.db_path), "2026-09-20")

    def test_database_archived_status_rejects_stale_active_state(self) -> None:
        with database.connect(self.db_path) as connection:
            connection.execute("UPDATE subjects SET status = 'archived' WHERE name = '计算机科学'")
        with self.assertRaises(ValueError):
            database.save_subject_exam_date(self.state(), "计算机科学", "2026-09-20", self.db_path)
        self.assertIsNone(database.get_subject_exam_date("计算机科学", self.db_path))

    def test_invalid_date_and_unknown_subject_leave_rows_unchanged(self) -> None:
        database.save_subject_exam_date(self.state(), "计算机科学", "2026-09-20", self.db_path)
        with database.connect_readonly(self.db_path) as connection:
            before = connection.execute("SELECT subject_id, exam_date FROM study_subject_exam_dates").fetchall()
        with self.assertRaises(ValueError):
            database.save_subject_exam_date(self.state(), "计算机科学", "2026-02-29", self.db_path)
        with self.assertRaises(ValueError):
            database.save_subject_exam_date(self.state(), "高数", "2026-09-20", self.db_path)
        with database.connect_readonly(self.db_path) as connection:
            after = connection.execute("SELECT subject_id, exam_date FROM study_subject_exam_dates").fetchall()
        self.assertEqual([tuple(row) for row in after], [tuple(row) for row in before])
        with self.assertRaises(LookupError):
            database.get_subject_exam_date("不存在的学科", self.db_path)

    def test_missing_table_is_not_implicitly_reinstalled_by_exam_date_api(self) -> None:
        missing_table_path = Path(self.temp_dir.name) / "unmigrated.sqlite"
        database.initialize_database(missing_table_path)
        with database.connect(missing_table_path) as connection:
            connection.execute("DROP TABLE study_subject_exam_dates")
            connection.execute("INSERT INTO subjects(name) VALUES ('计算机科学')")
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.save_subject_exam_date(self.state(), "计算机科学", "2026-09-20", missing_table_path)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.get_subject_exam_date("计算机科学", missing_table_path)
        with database.connect_readonly(missing_table_path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='study_subject_exam_dates'"
            ).fetchone()
        self.assertIsNone(exists)

    def test_foreign_key_rejects_orphan_exam_date(self) -> None:
        with database.connect(self.db_path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO study_subject_exam_dates(subject_id, exam_date) VALUES (?, ?)",
                    (999999, "2026-09-20"),
                )

    def test_corrupt_stored_date_is_diagnosed_on_read(self) -> None:
        with database.connect(self.db_path) as connection:
            subject_id = connection.execute(
                "SELECT id FROM subjects WHERE name = '计算机科学'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO study_subject_exam_dates(subject_id, exam_date) VALUES (?, ?)",
                (subject_id, "2026-02-29"),
            )
        with self.assertRaises(ValueError):
            database.get_subject_exam_date("计算机科学", self.db_path)


if __name__ == "__main__":
    unittest.main()
