from __future__ import annotations

from unittest.mock import patch

from data_test_support import IsolatedDatabaseTestCase
from study_app.data import database


class DataSchemaMigrationTests(IsolatedDatabaseTestCase):
    def test_connect_context_closes_database_handle(self) -> None:
        connection = database.connect(self.db_path)

        with connection:
            connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")

        with self.assertRaises(database.sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_initialize_database_creates_schema_indexes_and_foreign_keys(self) -> None:
        database.initialize_database(self.db_path)

        with database.connect(self.db_path) as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            indexes = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            versions = [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

        self.assertTrue(
            {
                "subjects",
                "learning_records",
                "problem_attempts",
                "record_attachments",
                "study_plans",
                "study_plan_items",
                "study_plan_item_states",
                "llm_call_audits",
                "practice_collection_backlog",
            }.issubset(tables)
        )
        self.assertTrue(
            {
                "idx_learning_records_subject_date",
                "idx_study_plan_items_plan",
                "idx_llm_call_audits_created",
                "idx_practice_collection_backlog_priority",
            }.issubset(indexes)
        )
        self.assertEqual(versions, [1, database.APPLICATION_SCHEMA_VERSION])
        self.assertEqual(foreign_keys, 1)

    def test_initialize_database_is_idempotent(self) -> None:
        database.initialize_database(self.db_path)
        database.initialize_database(self.db_path)

        with database.connect(self.db_path) as connection:
            versions = connection.execute(
                "SELECT version, COUNT(*) AS count FROM schema_migrations GROUP BY version"
            ).fetchall()
        self.assertEqual(
            [(row["version"], row["count"]) for row in versions],
            [(1, 1), (database.APPLICATION_SCHEMA_VERSION, 1)],
        )

    def test_initialize_database_migrates_old_problem_attempts_and_preserves_rows(self) -> None:
        with database.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE problem_attempts (
                    id INTEGER PRIMARY KEY,
                    record_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT,
                    correctness REAL,
                    difficulty_label TEXT,
                    difficulty_score REAL,
                    related_topics_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL
                );
                INSERT INTO problem_attempts(
                    id, record_id, title, related_topics_json, raw_json
                ) VALUES (1, 9, '旧题目', '[]', '{}');
                """
            )

        database.initialize_database(self.db_path)

        with database.connect(self.db_path) as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(problem_attempts)")
            }
            row = connection.execute(
                "SELECT id, record_id, title, statement, error_cause FROM problem_attempts"
            ).fetchone()
        self.assertTrue({"statement", "error_cause"}.issubset(columns))
        self.assertEqual(tuple(row), (1, 9, "旧题目", None, None))

    def test_ensure_seeded_database_skips_production_json_for_nonempty_database(self) -> None:
        self.initialize_seed_guarded_database()

        with patch.object(database, "import_current_json_files") as importer:
            result = database.ensure_seeded_database(self.db_path)

        self.assertEqual(result, self.db_path)
        importer.assert_not_called()
        self.assertEqual(database.get_counts(self.db_path)["subjects"], 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
