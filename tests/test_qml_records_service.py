from __future__ import annotations

import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from study_app.data import database
from study_app.ai.attachment_parser import AttachmentProblemBlock
from study_app.core import record_draft_inference
from study_app.quick.records_service import (
    OJService,
    RecordsService,
    StaleRecordPreviewError,
)


class QmlRecordsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "isolated.sqlite"
        self.model_path = self.root / "model.json"
        self.records_path = self.root / "records.json"
        self.model = {
            "model_name": "isolated-qml-records-test",
            "scale": "0-1",
            "subjects": [
                {
                    "name": "数学",
                    "weight": 1,
                    "mastery": 0.25,
                    "status": "learning",
                    "modules": [
                        {
                            "name": "代数",
                            "weight": 1,
                            "mastery": 0.25,
                            "status": "learning",
                            "topics": [
                                {
                                    "id": "algebra-functions",
                                    "name": "函数",
                                    "status": "learning",
                                    "mastery": 0.25,
                                    "importance": 1,
                                    "difficulty": 0.5,
                                    "forgetting_risk": 0.5,
                                }
                            ],
                        }
                    ],
                }
            ],
            "initial_percent_assessment": {
                "subjects": [{"name": "数学", "initial_score": 65}]
            },
        }
        self.model_path.write_text(
            json.dumps(self.model, ensure_ascii=False), encoding="utf-8"
        )
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            database.import_model_json(connection, self.model)
        self.topic = database.list_topic_identities(self.db_path)[0]
        database.install_oj_schema(self.db_path)
        self.service = RecordsService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=date(2026, 10, 4),
        )
        self.oj = OJService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=date(2026, 10, 4),
        )

    def record_payload(self, **changes) -> dict:
        return {
            "subject": "数学",
            "module": "代数",
            "topic": "函数",
            "activity": "exercise",
            "source": "self_study",
            "score": 88,
            "duration_minutes": 25,
            "note": "复习二次函数",
            **changes,
        }

    def test_post_commit_practice_failure_reports_saved_without_replaying_record(self) -> None:
        preview = self.service.preview(self.record_payload(), recognition="manual")
        with patch.object(database, "_auto_import_practice_problems",
                          side_effect=OSError("private-token-and-path")):
            result = self.service.save(preview["token"], preview["draft"])
        self.assertTrue(result["saved"])
        self.assertTrue(result["refreshWarning"])
        self.assertNotIn("private-token", str(result))
        with database.connect_readonly(self.db_path) as connection:
            rows = connection.execute("SELECT id FROM learning_records").fetchall()
        self.assertEqual([row["id"] for row in rows], [result["recordId"]])
        with self.assertRaises(StaleRecordPreviewError):
            self.service.save(preview["token"], preview["draft"])

    def test_dto_failure_after_commit_reports_saved_with_record_id(self) -> None:
        preview = self.service.preview(self.record_payload(), recognition="manual")
        with patch.object(self.service, "_record_dto", side_effect=ValueError("private-path")):
            result = self.service.save(preview["token"], preview["draft"])
        self.assertTrue(result["saved"])
        self.assertTrue(result["refreshWarning"])
        with database.connect_readonly(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0], 1)

    def test_pre_commit_record_failure_is_still_a_failure(self) -> None:
        preview = self.service.preview(self.record_payload(), recognition="manual")
        with patch.object(database, "add_learning_record", side_effect=RuntimeError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                self.service.save(preview["token"], preview["draft"])
        with database.connect_readonly(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0], 0)

    def test_load_returns_real_catalog_and_path_redacted_records(self) -> None:
        attachment = self.root / "worksheet.txt"
        attachment.write_text("局部附件", encoding="utf-8")
        preview = self.service.preview(
            self.record_payload(attachments=[str(attachment)]), recognition="manual"
        )
        saved = self.service.save(preview["token"], preview["draft"])

        loaded = self.service.load()
        self.assertEqual(loaded["catalog"]["subjects"], [{"name": "数学"}])
        self.assertEqual(
            loaded["catalog"]["topics"],
            [
                {
                    "topic_key": self.topic.topic_key,
                    "subject_name": "数学",
                    "module_name": "代数",
                    "topic_name": "函数",
                }
            ],
        )
        self.assertEqual(loaded["records"][0]["id"], saved["record"]["id"])
        self.assertEqual(
            loaded["records"][0]["attachments"],
            [{"file_name": "worksheet.txt", "file_ext": ".txt", "file_size": attachment.stat().st_size}],
        )
        serialized = json.dumps(loaded, ensure_ascii=False)
        self.assertNotIn(str(attachment), serialized)
        self.assertNotIn("file_path", serialized)
        legacy = RecordsService._record_list_dto(
            {
                "id": 1,
                "attachments": [
                    {"file_name": str(self.root / "private-folder" / "legacy.pdf")}
                ],
            }
        )
        self.assertEqual(legacy["attachments"][0]["file_name"], "legacy.pdf")

    def test_local_preview_extracts_attachment_and_saves_only_after_review(self) -> None:
        attachment = self.root / "worksheet.txt"
        attachment.write_text("第1题： 求函数 f(x)=x^2 的定义域。", encoding="utf-8")
        preview = self.service.preview(
            self.record_payload(attachments=[str(attachment)]), recognition="local"
        )
        self.assertEqual(preview["recognition"]["mode"], "local")
        self.assertEqual(preview["draft"]["problems"][0]["title"], "第1题")
        self.assertEqual(
            preview["draft"]["problems"][0]["statement_source"], "attachment_excerpt"
        )
        self.assertEqual(database.load_raw_records(self.db_path), [])
        serialized = json.dumps(preview, ensure_ascii=False)
        self.assertNotIn(str(attachment), serialized)
        self.assertNotIn("file_path", serialized)

        reviewed = dict(preview["draft"])
        reviewed["problems"] = [
            {
                **preview["draft"]["problems"][0],
                "title": "第1题（已核对）",
                "error_cause": "定义域端点遗漏",
                "related_topics": ["函数"],
            }
        ]
        with patch(
            "study_app.data.model_progress_sync.sync_learning_record_to_model",
            return_value=[],
        ):
            result = self.service.save(preview["token"], reviewed)
        raw = database.load_raw_records(self.db_path)[0]
        self.assertEqual(result["record"]["id"], raw["id"])
        self.assertEqual(raw["problems"][0]["title"], "第1题（已核对）")
        self.assertEqual(raw["problems"][0]["related_topics"], ["函数"])
        self.assertEqual(raw["attachments"][0]["file_path"], str(attachment.resolve()))
        self.assertNotIn("file_path", json.dumps(result, ensure_ascii=False))

    def test_local_inference_keeps_range_error_difficulty_and_record_score_rules(self) -> None:
        problems = record_draft_inference.infer_problems_from_inputs(
            note="完成了第1到3题",
            answer_result="错第2题，其余都对",
            error_cause="第2题错因是定义域端点遗漏",
            topic="曲面积分",
            module="多元积分",
            attachments=[],
            extracted_text="",
            problem_blocks=[
                AttachmentProblemBlock("第1题", "计算基础曲面积分。", "attachment_excerpt"),
                AttachmentProblemBlock("第2题", "使用 Stokes 定理计算闭曲面通量并说明取向。", "attachment_excerpt"),
                AttachmentProblemBlock("第3题", "计算第二型曲面积分并检查边界。", "attachment_excerpt"),
            ],
            record_context={
                "subject": "高等数学",
                "module": "多元积分",
                "topic": "曲面积分",
                "note": "完成了第1到3题",
            },
        )
        self.assertEqual([item["title"] for item in problems], ["第1题", "第2题", "第3题"])
        self.assertEqual([item["answer_result"] for item in problems], ["全对", "错误", "全对"])
        self.assertEqual(problems[1]["error_cause"], "定义域端点遗漏")
        self.assertEqual(problems[1]["error_category"], "boundary_omission")
        self.assertTrue(all(isinstance(item.get("difficulty_score"), (int, float)) for item in problems))
        score = record_draft_inference.estimate_record_score_from_problems(
            problems,
            {"subject": "高等数学", "module": "多元积分", "topic": "曲面积分", "note": "完成了第1到3题"},
            self.model_path,
        )
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_search_file_token_and_preview_dto_never_expose_local_path(self) -> None:
        attachment = self.root / "private-folder" / "题目材料.pdf"
        attachment.parent.mkdir()
        attachment.write_bytes(b"isolated attachment")

        service = RecordsService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=date(2026, 10, 4),
            file_searcher=lambda _query, **_kwargs: [
                SimpleNamespace(path=attachment, score=12, reason=str(attachment.parent))
            ],
        )
        results = service.search_files(str(attachment.parent))
        self.assertEqual(results["files"][0]["file_name"], attachment.name)
        self.assertEqual(results["query"], attachment.parent.name)
        serialized_results = json.dumps(results, ensure_ascii=False)
        self.assertNotIn(str(attachment), serialized_results)
        self.assertNotIn(str(attachment.parent), serialized_results)
        preview = service.preview(
            self.record_payload(search_file_tokens=[results["files"][0]["file_token"]]),
            recognition="manual",
        )
        serialized_preview = json.dumps(preview, ensure_ascii=False)
        self.assertNotIn(str(attachment), serialized_preview)
        self.assertNotIn(str(attachment.parent), serialized_preview)
        self.assertEqual(preview["files"][0]["file_name"], attachment.name)
        self.assertNotIn("file_path", serialized_preview)
        attachment.write_bytes(b"changed attachment bytes")
        with self.assertRaisesRegex(StaleRecordPreviewError, "搜索文件已变化"):
            service.preview(
                self.record_payload(search_file_tokens=[results["files"][0]["file_token"]]),
                recognition="manual",
            )

    def test_local_recognition_uses_numbered_filename_without_returning_its_path(self) -> None:
        attachment = self.root / "private-folder" / "第9题材料.txt"
        attachment.parent.mkdir()
        attachment.write_text("无可提取题面", encoding="utf-8")
        service = RecordsService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=date(2026, 10, 4),
            attachment_extractor=lambda _paths: "",
        )
        preview = service.preview(
            self.record_payload(attachments=[str(attachment)]), recognition="local"
        )
        serialized = json.dumps(preview, ensure_ascii=False)
        self.assertEqual(preview["draft"]["problems"][0]["title"], "第9题")
        self.assertNotIn(str(attachment.parent), serialized)
        self.assertNotIn(str(attachment), serialized)
        self.assertIn(attachment.name, serialized)

    def test_manual_mode_does_not_extract_or_call_enhanced_recognition(self) -> None:
        attachment = self.root / "worksheet.txt"
        attachment.write_text("第1题： 题面文本足够长。", encoding="utf-8")
        calls = []

        def extractor(paths):
            calls.append(list(paths))
            return "第1题： 题面文本足够长。"

        service = RecordsService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=date(2026, 10, 4),
            attachment_extractor=extractor,
            settings_loader=lambda: self.fail("manual/local mode loaded LLM settings"),
            recognition_provider=lambda _payload: self.fail("manual mode called LLM"),
        )
        preview = service.preview(
            self.record_payload(
                problem_title="手动题目",
                free_context="请复核这道题",
                problem_count=1,
                attachments=[str(attachment)],
            ),
            recognition="manual",
        )
        self.assertEqual(calls, [])
        self.assertEqual(preview["draft"]["problems"][0]["title"], "手动题目")
        self.assertNotIn("difficulty_score", preview["draft"]["problems"][0])
        self.assertNotIn("correctness", preview["draft"]["problems"][0])
        self.assertEqual(preview["recognition"]["mode"], "manual")
        local = service.preview(self.record_payload(), recognition="local")
        self.assertEqual(local["recognition"]["mode"], "local")
        self.assertEqual(calls, [])

    def test_enhanced_mode_is_explicit_and_dto_drops_unknown_provider_fields(self) -> None:
        captured = []

        def recognize(payload):
            captured.append(payload)
            return [
                {
                    "title": "LLM 题目",
                    "statement": "题面内容",
                    "related_topics": ["函数"],
                    "api_key": "credential-must-never-leak",
                }
            ]

        settings = SimpleNamespace(
            is_cloud_enabled=True,
            enabled_features=("record_parser",),
        )
        service = RecordsService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=date(2026, 10, 4),
            recognition_provider=recognize,
            settings_loader=lambda: settings,
        )
        local = service.preview(self.record_payload(), recognition="local")
        self.assertEqual(captured, [])
        enhanced = service.preview(self.record_payload(), recognition="enhanced")
        self.assertEqual(enhanced["recognition"]["mode"], "enhanced")
        self.assertEqual(len(captured), 1)
        self.assertEqual(
            captured[0]["attachment_text"], ""
        )
        serialized = json.dumps(enhanced, ensure_ascii=False)
        self.assertNotIn("credential-must-never-leak", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("api_key", json.dumps(local, ensure_ascii=False))

    def test_save_rejects_changed_form_or_attachment_and_invalidates_preview(self) -> None:
        attachment = self.root / "worksheet.txt"
        attachment.write_text("初始内容", encoding="utf-8")
        changed_form = self.service.preview(self.record_payload())
        with self.assertRaises(StaleRecordPreviewError):
            self.service.save(
                changed_form["token"],
                {**changed_form["draft"], "note": "预览后的新备注"},
            )
        with self.assertRaises(StaleRecordPreviewError):
            self.service.save(changed_form["token"], changed_form["draft"])
        self.assertEqual(database.load_raw_records(self.db_path), [])
        changed_file = self.service.preview(
            self.record_payload(attachments=[str(attachment)]), recognition="local"
        )
        attachment.write_text("预览后文件内容发生了变化", encoding="utf-8")
        with self.assertRaises(StaleRecordPreviewError):
            self.service.save(changed_file["token"], changed_file["draft"])
        self.assertEqual(database.load_raw_records(self.db_path), [])

        changed_catalog = self.service.preview(self.record_payload())
        with database.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE subject_catalog_state "
                "SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
            )
        with self.assertRaises(StaleRecordPreviewError):
            self.service.save(changed_catalog["token"], changed_catalog["draft"])
        self.assertEqual(database.load_raw_records(self.db_path), [])

    def test_archived_subject_cannot_be_used_for_a_new_record(self) -> None:
        with database.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE subject_catalog SET lifecycle_status='archived' "
                "WHERE canonical_name='数学'"
            )
            connection.execute(
                "UPDATE subject_catalog_state "
                "SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
            )
        preview = self.service.preview(self.record_payload(), recognition="manual")
        with self.assertRaisesRegex(ValueError, "已归档，禁止普通新增学习记录"):
            self.service.save(preview["token"], preview["draft"])
        self.assertEqual(database.load_raw_records(self.db_path), [])

    def test_oj_requires_manual_topic_mapping_before_recording_attempt(self) -> None:
        created = self.oj.create_problem(
            {
                "platform": "leetcode",
                "external_id": "1",
                "title": "Two Sum",
                "url": "https://example.test/two-sum",
            }
        )
        problem_key = created["problem"]["problem_key"]
        with self.assertRaisesRegex(ValueError, "先为这道 OJ 题明确选择"):
            self.oj.submit_attempt(self.attempt_payload(problem_key))
        with self.assertRaisesRegex(ValueError, "至少一个知识点"):
            self.oj.save_mapping({"problem_key": problem_key, "topic_keys": []})

        mapped = self.oj.save_mapping(
            {
                "problem_key": problem_key,
                "topic_keys": [self.topic.topic_key],
                "note": "用户确认映射",
            }
        )
        self.assertEqual(mapped["mappings"][0]["topic_key"], self.topic.topic_key)
        self.assertEqual(mapped["mappings"][0]["mapping_source"], "manual")

        appended = self.oj.submit_attempt(self.attempt_payload(problem_key))
        self.assertEqual(appended["attempt"]["result"], "accepted")
        loaded = self.oj.load()
        self.assertEqual(loaded["problems"][0]["attempt_count"], 1)
        self.assertEqual(loaded["problems"][0]["mappings"][0]["topic_key"], self.topic.topic_key)
        self.assertEqual(loaded["attempts"][0]["attempt_id"], appended["attempt"]["attempt_id"])

    def test_oj_rejects_topic_not_in_loaded_catalog_before_mapping_write(self) -> None:
        created = self.oj.create_problem(
            {
                "source_key": "local",
                "external_problem_key": "q-1",
                "title": "本地题",
            }
        )
        unknown = self.topic.topic_key[:-1] + (
            "0" if self.topic.topic_key[-1] != "0" else "1"
        )
        with self.assertRaisesRegex(ValueError, "不在当前目录"):
            self.oj.save_mapping(
                {"problem_key": created["problem"]["problem_key"], "topic_keys": [unknown]}
            )
        self.assertEqual(self.oj.load()["mappings"], [])

    def test_oj_external_url_rejects_credentials_and_redacts_legacy_secret_query(self) -> None:
        with self.assertRaisesRegex(ValueError, "公开外部链接"):
            self.oj.create_problem(
                {
                    "platform": "leetcode",
                    "external_id": "credentialed",
                    "title": "Credentialed URL",
                    "url": "https://alice:secret@example.test/problem",
                }
            )
        with self.assertRaisesRegex(ValueError, "访问令牌"):
            self.oj.create_problem(
                {
                    "platform": "leetcode",
                    "external_id": "tokenized",
                    "title": "Tokenized URL",
                    "url": "https://example.test/problem?access_token=secret",
                }
            )
        self.assertEqual(self.oj.load()["problems"], [])

        legacy_problem = SimpleNamespace(
            problem_key="ojp:v1:legacy",
            source_key="leetcode",
            external_problem_key="legacy",
            title="Legacy URL",
            source_url="https://example.test/problem?id=3&token=secret",
        )
        self.assertEqual(
            OJService._problem_dto(legacy_problem)["source_url"],
            "https://example.test/problem?id=3",
        )

    @staticmethod
    def attempt_payload(problem_key: str) -> dict:
        return {
            "problem_key": problem_key,
            "attempted_at": "2026-10-04T20:00:00+08:00",
            "result": "accepted",
            "duration_seconds": 120,
            "independence": "independent",
            "hint_level": "none",
            "error_type": "none",
            "notes": "独立完成",
            "source_attempt_key": "manual-attempt-1",
        }


if __name__ == "__main__":
    unittest.main()
