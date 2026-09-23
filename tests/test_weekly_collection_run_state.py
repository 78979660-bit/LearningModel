from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class WeeklyCollectionRunStateTests(unittest.TestCase):
    def test_unhandled_collection_failure_marks_run_failed(self) -> None:
        from study_app.data import weekly_practice_collector as collector
        from study_app.data.database import connect

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "weekly.sqlite"
            with (
                patch.object(collector, "TRUSTED_SOURCES", ()),
                patch.object(
                    collector,
                    "deduplicate",
                    side_effect=RuntimeError("PRIVATE_COLLECTION_DETAIL"),
                ),
                patch(
                    "study_app.data.ds_collection_gaps.audit_and_register_ds_collection_gaps",
                    return_value=[],
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "PRIVATE_COLLECTION_DETAIL"):
                    collector.collect_weekly_sources(db_path)

            with connect(db_path) as connection:
                run = connection.execute(
                    "SELECT * FROM practice_collection_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()

        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "failed")
        self.assertIsNotNone(run["finished_at"])
        self.assertNotIn("PRIVATE_COLLECTION_DETAIL", run["error_message"] or "")
        self.assertIn("RuntimeError", run["error_message"] or "")


if __name__ == "__main__":
    unittest.main()
