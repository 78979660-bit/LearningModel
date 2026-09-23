from __future__ import annotations

from unittest.mock import patch

from data_test_support import IsolatedDatabaseTestCase
from study_app.data import database


class DataLearningRecordTests(IsolatedDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.initialize_seed_guarded_database()

    @staticmethod
    def record() -> dict:
        return {
            "date": "2026-07-18",
            "subject": "测试占位学科",
            "module": "模块甲",
            "topic": "主题乙",
            "activity": "practice",
            "source": "outside_class",
            "score": 88.5,
            "duration_minutes": 42,
            "note": "隔离测试",
            "problems": [
                {
                    "title": "测试题一",
                    "statement": "这是足够长的测试题面。",
                    "status": "wrong",
                    "correctness": 0,
                    "difficulty": "hard",
                    "difficulty_score": 78,
                    "error_cause": "计算失误",
                    "related_topics": ["主题乙"],
                }
            ],
            "attachments": [
                {
                    "file_path": "C:/tmp/worksheet.pdf",
                    "file_name": "worksheet.pdf",
                    "mime_hint": "application/pdf",
                    "file_size": 1234,
                    "note": "题目附件",
                }
            ],
        }

    def plan_item_id(self) -> int:
        database.create_study_plan(
            None,
            "2026-07-18",
            "2026-07-18",
            "atomic-result",
            {},
            [
                {
                    "section_key": "short",
                    "section_title": "今日作业",
                    "item_type": "result",
                    "item_text": "当天作业：测试原子写入",
                    "item_order": 0,
                    "item_hash": "atomic-result-item",
                }
            ],
            self.db_path,
        )
        return database.get_active_study_plan(None, self.db_path)["items"][0]["id"]

    def test_add_record_round_trips_children_and_recent_summary(self) -> None:
        with (
            patch.object(database, "_auto_import_practice_problems") as auto_import,
            patch(
                "study_app.data.model_progress_sync.sync_learning_record_to_model",
                return_value=[],
            ) as sync,
        ):
            record_id = database.add_learning_record(self.record(), self.db_path)

        raw = database.load_raw_records(self.db_path)
        recent = database.list_recent_records(self.db_path, limit=1)
        with database.connect(self.db_path) as connection:
            problem = connection.execute(
                "SELECT * FROM problem_attempts WHERE record_id = ?", (record_id,)
            ).fetchone()

        self.assertEqual(record_id, 1)
        self.assertEqual(raw[0]["score"], 88.5)
        self.assertEqual(raw[0]["duration_minutes"], 42.0)
        self.assertEqual(raw[0]["problems"][0]["title"], "测试题一")
        self.assertEqual(raw[0]["problems"][0]["difficulty_score"], 78)
        self.assertEqual(raw[0]["attachments"][0]["file_name"], "worksheet.pdf")
        self.assertEqual(recent[0]["record_date"], "2026-07-18")
        self.assertEqual(recent[0]["subject_name"], "测试占位学科")
        self.assertEqual(recent[0]["score"], 88.5)
        self.assertEqual(recent[0]["problem_count"], 1)
        self.assertEqual(recent[0]["attachments"][0]["file_size"], 1234)
        self.assertEqual(problem["title"], "测试题一")
        self.assertEqual(problem["statement"], "这是足够长的测试题面。")
        self.assertEqual(problem["correctness"], 0.0)
        self.assertEqual(problem["error_cause"], "计算失误")
        auto_import.assert_called_once()
        self.assertEqual(auto_import.call_args.kwargs["db_path"], self.db_path)
        sync.assert_called_once()
        self.assertEqual(sync.call_args.kwargs["db_path"], self.db_path)

    def test_subject_and_module_lists_remain_callable_and_scope_modules(self) -> None:
        with database.connect(self.db_path) as connection:
            other_subject_id = connection.execute(
                "INSERT INTO subjects(name, source_json) VALUES (?, ?)",
                ("另一学科", "{}"),
            ).lastrowid
            first_subject_id = connection.execute(
                "SELECT id FROM subjects WHERE name = ?", ("测试占位学科",)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO modules(subject_id, name) VALUES (?, ?)",
                (first_subject_id, "范围内模块"),
            )
            connection.execute(
                "INSERT INTO modules(subject_id, name) VALUES (?, ?)",
                (other_subject_id, "范围外模块"),
            )

        self.assertEqual(database.list_subject_names(self.db_path), ["测试占位学科", "另一学科"])
        self.assertEqual(database.list_module_names("测试占位学科", self.db_path), ["范围内模块"])

    def test_malformed_raw_json_is_rebuilt_from_normalized_rows(self) -> None:
        with (
            patch.object(database, "_auto_import_practice_problems"),
            patch(
                "study_app.data.model_progress_sync.sync_learning_record_to_model",
                return_value=[],
            ),
        ):
            record_id = database.add_learning_record(self.record(), self.db_path)
        for invalid_raw_json in ("{broken", "[]", b"\xff"):
            with self.subTest(raw_json=invalid_raw_json):
                with database.connect(self.db_path) as connection:
                    connection.execute(
                        "UPDATE learning_records SET raw_json = ? WHERE id = ?",
                        (invalid_raw_json, record_id),
                    )
                    connection.execute(
                        "UPDATE problem_attempts SET related_topics_json = ? WHERE record_id = ?",
                        ("{broken", record_id),
                    )

                records = database.load_raw_records(self.db_path)

                self.assertEqual(len(records), 1)
                rebuilt = records[0]
                self.assertEqual(rebuilt["id"], record_id)
                self.assertEqual(rebuilt["date"], "2026-07-18")
                self.assertEqual(rebuilt["subject"], "测试占位学科")
                self.assertEqual(rebuilt["score"], 88.5)
                self.assertEqual(rebuilt["data_warning"], "invalid_raw_json")
                self.assertEqual(rebuilt["problems"][0]["title"], "测试题一")
                self.assertEqual(rebuilt["problems"][0]["correctness"], 0.0)
                self.assertEqual(rebuilt["problems"][0]["related_topics"], [])
                self.assertEqual(rebuilt["attachments"][0]["file_name"], "worksheet.pdf")

    def test_missing_required_fields_make_no_record_writes(self) -> None:
        for missing in ("date", "subject"):
            record = self.record()
            record[missing] = ""
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(ValueError, f"record.{missing} is required"):
                    database.add_learning_record(record, self.db_path)

        self.assertEqual(database.get_counts(self.db_path)["learning_records"], 0)

    def test_child_insert_failure_rolls_back_parent_record(self) -> None:
        record = self.record()
        record["attachments"][0]["file_size"] = 2**100

        with self.assertRaises(OverflowError):
            database.add_learning_record(record, self.db_path)

        counts = database.get_counts(self.db_path)
        self.assertEqual(counts["learning_records"], 0)
        self.assertEqual(counts["problem_attempts"], 0)

    def test_record_and_plan_result_commit_atomically(self) -> None:
        item_id = self.plan_item_id()
        with (
            patch.object(database, "_auto_import_practice_problems"),
            patch(
                "study_app.data.model_progress_sync.sync_learning_record_to_model",
                return_value=[],
            ),
        ):
            record_id = database.add_learning_record(
                self.record(),
                self.db_path,
                study_plan_item_id=item_id,
                study_plan_result="correct",
            )

        saved = database.get_active_study_plan(None, self.db_path)
        self.assertEqual(record_id, 1)
        self.assertEqual(database.get_counts(self.db_path)["learning_records"], 1)
        self.assertTrue(saved["items"][0]["checked"])
        self.assertEqual(saved["items"][0]["result"], "correct")
        with self.assertRaisesRegex(ValueError, "already completed"):
            database.add_learning_record(
                self.record(),
                self.db_path,
                study_plan_item_id=item_id,
                study_plan_result="wrong",
            )
        self.assertEqual(database.get_counts(self.db_path)["learning_records"], 1)

    def test_missing_plan_item_rolls_back_learning_record(self) -> None:
        with self.assertRaisesRegex(ValueError, "study plan item not found"):
            database.add_learning_record(
                self.record(),
                self.db_path,
                study_plan_item_id=999999,
                study_plan_result="wrong",
            )

        self.assertEqual(database.get_counts(self.db_path)["learning_records"], 0)

    def test_model_sync_failure_preserves_record_and_persists_diagnostic(self) -> None:
        with (
            patch.object(database, "_auto_import_practice_problems"),
            patch(
                "study_app.data.model_progress_sync.sync_learning_record_to_model",
                side_effect=RuntimeError("sync unavailable"),
            ),
        ):
            record_id = database.add_learning_record(self.record(), self.db_path)

        audit = database.get_setting(
            "model_progress_sync_last_result", db_path=self.db_path
        )
        self.assertEqual(record_id, 1)
        self.assertEqual(database.get_counts(self.db_path)["learning_records"], 1)
        self.assertEqual(audit["status"], "failed")
        self.assertEqual(audit["record_id"], 1)
        self.assertEqual(audit["error"], "sync unavailable")

    def test_sync_and_diagnostic_failure_does_not_report_committed_record_as_failed(self) -> None:
        with (
            patch.object(database, "_auto_import_practice_problems"),
            patch(
                "study_app.data.model_progress_sync.sync_learning_record_to_model",
                side_effect=RuntimeError("sync unavailable"),
            ),
            patch.object(database, "set_setting", side_effect=OSError("diagnostic disk full")),
            self.assertLogs("study_app.data.database", level="ERROR") as captured,
        ):
            record_id = database.add_learning_record(self.record(), self.db_path)

        self.assertEqual(record_id, 1)
        self.assertEqual(database.get_counts(self.db_path)["learning_records"], 1)
        self.assertIn("diagnostic disk full", "\n".join(captured.output))

    def test_auto_import_failure_is_observable_without_losing_record_flow(self) -> None:
        problem = self.record()["problems"][0]
        with (
            patch(
                "study_app.data.practice_repository.import_practice_problem",
                side_effect=OSError("practice database locked"),
            ),
            self.assertLogs("study_app.data.database", level="ERROR") as captured,
        ):
            database._auto_import_practice_problems(
                self.record(), [problem], db_path=self.db_path
            )

        diagnostic = "\n".join(captured.output)
        self.assertIn("problem index 1", diagnostic)
        self.assertNotIn("测试题一", diagnostic)
        self.assertIn("practice database locked", diagnostic)


if __name__ == "__main__":
    import unittest

    unittest.main()
