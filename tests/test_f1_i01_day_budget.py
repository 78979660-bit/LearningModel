from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from study_app.core.day_budget_input import (
    StudyDayBudget,
    validate_day_budget_input,
)
from study_app.data import database


class DayBudgetInputTests(unittest.TestCase):
    def test_valid_boundaries_and_real_dates(self) -> None:
        self.assertEqual(
            validate_day_budget_input("2028-02-29", 0),
            StudyDayBudget("2028-02-29", 0),
        )
        self.assertEqual(
            validate_day_budget_input("2026-09-16", 1440).available_minutes,
            1440,
        )

    def test_invalid_fields_are_rejected_before_database_access(self) -> None:
        bad_dates = (None, "", "2026-02-29", "2026-13-01", "2026-9-16", True, 20260916)
        bad_minutes = (None, True, False, -1, 1441, 30.0, float("nan"), float("inf"), "30")
        for value in bad_dates:
            with self.subTest(plan_date=value), self.assertRaises(ValueError):
                validate_day_budget_input(value, 30)
        for value in bad_minutes:
            with self.subTest(available_minutes=value), self.assertRaises(ValueError):
                validate_day_budget_input("2026-09-16", value)


class DayBudgetPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "budget.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.STUDY_DAY_BUDGET_TABLE_SQL)

    def test_save_read_and_replace_same_day(self) -> None:
        self.assertIsNone(database.get_study_day_budget("2026-09-16", self.db_path))
        self.assertEqual(
            database.save_study_day_budget("2026-09-16", 0, self.db_path),
            StudyDayBudget("2026-09-16", 0),
        )
        database.save_study_day_budget("2026-09-17", 1440, self.db_path)
        database.save_study_day_budget("2026-09-16", 45, self.db_path)
        self.assertEqual(
            database.get_study_day_budget("2026-09-16", self.db_path),
            StudyDayBudget("2026-09-16", 45),
        )
        self.assertEqual(
            database.get_study_day_budget("2026-09-17", self.db_path),
            StudyDayBudget("2026-09-17", 1440),
        )
        with database.connect_readonly(self.db_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM study_day_budgets").fetchone()[0]
        self.assertEqual(count, 2)

    def test_invalid_input_leaves_existing_budget_and_other_tables_unchanged(self) -> None:
        database.save_study_day_budget("2026-09-16", 40, self.db_path)
        with database.connect_readonly(self.db_path) as connection:
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("study_day_budgets", "study_plans", "learning_records")
            }
        for plan_date, minutes in (("2026-02-29", 50), ("2026-09-16", True), ("2026-09-17", 30.5)):
            with self.subTest(plan_date=plan_date, minutes=minutes), self.assertRaises(ValueError):
                database.save_study_day_budget(plan_date, minutes, self.db_path)
        with database.connect_readonly(self.db_path) as connection:
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            }
        self.assertEqual(after, before)
        self.assertEqual(
            database.get_study_day_budget("2026-09-16", self.db_path),
            StudyDayBudget("2026-09-16", 40),
        )

    def test_invalid_input_does_not_create_missing_database(self) -> None:
        missing_path = Path(self.temp_dir.name) / "missing.sqlite"
        with self.assertRaises(ValueError):
            database.save_study_day_budget("2026-09-16", "20", missing_path)
        self.assertFalse(missing_path.exists())

    def test_missing_table_is_not_implicitly_reinstalled_by_budget_api(self) -> None:
        unmigrated_path = Path(self.temp_dir.name) / "unmigrated.sqlite"
        database.initialize_database(unmigrated_path)
        with database.connect(unmigrated_path) as connection:
            connection.execute("DROP TABLE study_day_budgets")
        with database.connect_readonly(unmigrated_path) as connection:
            installed = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'study_day_budgets'"
            ).fetchone()
        self.assertIsNone(installed)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.save_study_day_budget("2026-09-16", 30, unmigrated_path)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.get_study_day_budget("2026-09-16", unmigrated_path)

    def test_database_constraint_rejects_out_of_range_direct_writes(self) -> None:
        with database.connect(self.db_path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO study_day_budgets(plan_date, available_minutes) VALUES (?, ?)",
                    ("2026-09-16", 1441),
                )
        self.assertIsNone(database.get_study_day_budget("2026-09-16", self.db_path))


if __name__ == "__main__":
    unittest.main()
