from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class PracticeCandidateTransactionTests(unittest.TestCase):
    def test_candidate_processing_failure_rolls_back_partial_imports(self) -> None:
        from study_app.data import practice_candidate_processor as processor
        from study_app.data import database
        from study_app.data.database import connect, initialize_database
        from study_app.data.practice_repository import import_practice_problem

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "candidate.sqlite"
            initialize_database(db_path)
            with connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO practice_collection_candidates(
                        source_name, institution, subject_hint, topic_hint,
                        title, url, document_type, quality_score, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'assignment', 100, 'discovered')
                    """,
                    (
                        "source",
                        "institution",
                        "计算机科学",
                        "图",
                        "candidate",
                        "https://example.invalid/candidate",
                    ),
                )

            blocks = [
                SimpleNamespace(title="One", statement="Explain graph traversal?"),
                SimpleNamespace(title="Two", statement="Design a graph algorithm?"),
            ]
            import_count = 0

            def fail_second_import(problem, db_path, *, connection):
                nonlocal import_count
                import_count += 1
                if import_count == 2:
                    raise sqlite3.OperationalError("second import failed")
                return import_practice_problem(
                    problem,
                    db_path=db_path,
                    connection=connection,
                )

            with (
                patch.object(processor, "candidate_text", return_value=("text", "resolved")),
                patch.object(processor, "parse_problem_blocks_from_text", return_value=blocks),
                patch.object(processor, "statement_quality", return_value=100),
                patch.object(processor, "classify_topic", return_value="图"),
                patch.object(processor, "difficulty_for_block", return_value=70),
                patch(
                    "study_app.core.composite_templates.composite_template_for_text",
                    return_value=None,
                ),
                patch.object(
                    processor,
                    "import_practice_problem",
                    side_effect=fail_second_import,
                ),
                patch.object(
                    database,
                    "import_current_json_files",
                    side_effect=AssertionError("production import must not run"),
                ),
            ):
                result = processor.process_candidates(limit=1, db_path=db_path)

            with connect(db_path) as connection:
                imported_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM practice_problems WHERE title LIKE ?",
                    ("institution candidate - %",),
                ).fetchone()["count"]
                source_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM practice_sources WHERE title = ?",
                    ("candidate",),
                ).fetchone()["count"]
                status = connection.execute(
                    "SELECT status FROM practice_collection_candidates"
                ).fetchone()["status"]

        self.assertEqual(import_count, 2)
        self.assertEqual(imported_count, 0)
        self.assertEqual(source_count, 0)
        self.assertEqual(status, "parse_failed")
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
