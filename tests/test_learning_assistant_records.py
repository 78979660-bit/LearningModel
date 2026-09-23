"""learning_assistant_records 数据权威规则测试。

覆盖（合同 §1 / A7 / A8 / A12 / 难度0 契约）：
- “全对”声明 → status=all_correct + correctness=1.0；independence 默认 unknown；
  无错因 → error_cause=None。
- 题目级显式结果反驳“全对” → 题目级结果胜出并产生告警。
- 显式 difficulty_score=0 原样保留，不被覆盖（禁真值链）。
- draft_only：只有题面 → 不写结果、record["draft_only"]=True、告警含“题面不等于完成”。
- 日期语义：用户日期保留 + date_basis=user_stated_completed；非法日历日 ValueError；
  缺省回落 confirmed_at + confirmation_fallback。
- 进度 kind → activity 映射表；mastered_claim 只写“自评掌握：”note，不改模型状态。
- build_revision：白名单字段、未知字段拒绝、附件关系拒绝。
- preview_mastery_impact：匹配主题给出前后掌握度与模块/学科重算；
  draft_only → no_evidence；预览绝不写模型文件或数据库。
"""
from __future__ import annotations

import json
import unittest

from data_test_support import IsolatedDatabaseTestCase, file_sha256
from study_app.core.learning_assistant_records import (
    INDEPENDENCE_VALUES,
    PROGRESS_KINDS,
    HomeworkFacts,
    ProgressFacts,
    build_record_from_homework,
    build_record_from_progress,
    build_revision,
    normalize_homework_problems,
    preview_mastery_impact,
)
from study_app.data import database

CONFIRMED_AT = "2026-09-19"
ACTION_ID = "la-" + "1" * 32

MODEL = {
    "model_name": "assistant_records_test_model_v1",
    "subjects": [{
        "name": "测试学科", "mastery": 0.3,
        "modules": [{
            "name": "基础", "weight": 1.0, "mastery": 0.3,
            "topics": [
                {"name": "哈希表", "mastery": 0.2, "difficulty": 0.6,
                 "status": "learning"},
                {"name": "二叉树", "mastery": 0.4, "difficulty": 0.6,
                 "status": "learning"},
            ],
        }],
    }],
}


def _homework_facts(**overrides) -> HomeworkFacts:
    base = dict(
        subject="测试学科",
        completed_date=None,
        problems=({"title": "第1题", "statement": "证明哈希表相关结论"},),
        claim_all_correct=True,
        duration_minutes=None,
        note="",
        draft_only=False,
    )
    base.update(overrides)
    return HomeworkFacts(**base)


def _progress_facts(**overrides) -> ProgressFacts:
    base = dict(
        subject="测试学科",
        target="哈希表",
        kind="reviewed",
        completed_date=None,
        note="",
    )
    base.update(overrides)
    return ProgressFacts(**base)


class HomeworkRecordRuleTests(IsolatedDatabaseTestCase):
    def test_all_correct_claim_sets_status_correctness_and_defaults(self):
        problems = normalize_homework_problems(
            [{"title": "第1题"}, {"title": "第2题", "error_cause": None}],
            claim_all_correct=True,
        )
        for problem in problems:
            self.assertEqual(problem["status"], "all_correct")
            self.assertEqual(problem["correctness"], 1.0)
            self.assertEqual(problem["independence"], "unknown")
            self.assertIsNone(problem["error_cause"])

        record, warnings = build_record_from_homework(
            _homework_facts(), action_id=ACTION_ID, confirmed_at=CONFIRMED_AT
        )
        self.assertEqual(warnings, [])
        self.assertEqual(record["activity"], "exercise")
        self.assertEqual(record["source"], "outside_class")
        self.assertEqual(record["date_basis"], "confirmation_fallback")
        self.assertEqual(record["confirmed_at"], CONFIRMED_AT)
        self.assertEqual(record["source_evidence"], {"action_id": ACTION_ID})
        for problem in record["problems"]:
            self.assertEqual(problem["status"], "all_correct")
            self.assertEqual(problem["correctness"], 1.0)
            self.assertEqual(problem["independence"], "unknown")

    def test_problem_level_result_beats_all_correct_claim_with_warning(self):
        problems = (
            {"title": "第1题", "correct": False},
            {"title": "第2题"},
        )
        record, warnings = build_record_from_homework(
            _homework_facts(problems=problems),
            action_id=ACTION_ID,
            confirmed_at=CONFIRMED_AT,
        )
        first, second = record["problems"]
        self.assertNotEqual(first.get("status"), "all_correct")
        self.assertIsNone(first.get("correctness"))
        self.assertEqual(second["status"], "all_correct")
        self.assertEqual(second["correctness"], 1.0)
        conflict_warnings = [w for w in warnings if "题目级显式结果" in w]
        self.assertTrue(conflict_warnings, warnings)
        self.assertIn("第1题", conflict_warnings[0])

    def test_explicit_zero_difficulty_is_preserved_not_overwritten(self):
        problems = (
            {"title": "第1题", "difficulty_score": 0, "difficulty": "easy"},
        )
        normalized = normalize_homework_problems(problems, claim_all_correct=True)
        self.assertEqual(normalized[0]["difficulty_score"], 0)
        self.assertFalse(isinstance(normalized[0]["difficulty_score"], bool))
        self.assertEqual(normalized[0]["difficulty"], "easy")

        record, _ = build_record_from_homework(
            _homework_facts(problems=problems),
            action_id=ACTION_ID,
            confirmed_at=CONFIRMED_AT,
        )
        self.assertEqual(record["problems"][0]["difficulty_score"], 0)
        self.assertEqual(record["problems"][0]["difficulty"], "easy")

    def test_draft_only_record_carries_no_results_and_warns(self):
        facts = _homework_facts(
            problems=({"title": "第1题", "statement": "仅题面文字"},),
            draft_only=True,
        )
        record, warnings = build_record_from_homework(
            facts, action_id=ACTION_ID, confirmed_at=CONFIRMED_AT
        )
        self.assertIs(record["draft_only"], True)
        for problem in record["problems"]:
            self.assertNotIn("status", problem)
            self.assertNotIn("correctness", problem)
        self.assertTrue(any("题面不等于完成" in warning for warning in warnings), warnings)
        self.assertTrue(
            any("不产生掌握度贡献" in warning for warning in warnings), warnings
        )

    def test_user_date_is_kept_with_user_stated_basis(self):
        record, _ = build_record_from_homework(
            _homework_facts(completed_date="2026-09-01"),
            action_id=ACTION_ID,
            confirmed_at=CONFIRMED_AT,
        )
        self.assertEqual(record["date"], "2026-09-01")
        self.assertEqual(record["date_basis"], "user_stated_completed")

    def test_fake_calendar_date_is_rejected(self):
        with self.assertRaises(ValueError):
            build_record_from_homework(
                _homework_facts(completed_date="2026-02-30"),
                action_id=ACTION_ID,
                confirmed_at=CONFIRMED_AT,
            )

    def test_missing_date_falls_back_to_confirmation_date(self):
        record, _ = build_record_from_homework(
            _homework_facts(completed_date=None),
            action_id=ACTION_ID,
            confirmed_at=CONFIRMED_AT,
        )
        self.assertEqual(record["date"], CONFIRMED_AT)
        self.assertEqual(record["date_basis"], "confirmation_fallback")

    def test_explicit_independence_user_fact_is_kept(self):
        self.assertEqual(
            set(INDEPENDENCE_VALUES), {"independent", "assisted", "unknown"}
        )
        record, _ = build_record_from_homework(
            _homework_facts(independence="independent"),
            action_id=ACTION_ID,
            confirmed_at=CONFIRMED_AT,
        )
        for problem in record["problems"]:
            self.assertEqual(problem["independence"], "independent")
        with self.assertRaises(ValueError):
            normalize_homework_problems(
                [{"title": "第1题"}],
                claim_all_correct=True,
                independence="单独做完",
            )

    def test_duration_is_validated(self):
        with self.assertRaises(ValueError):
            build_record_from_homework(
                _homework_facts(duration_minutes=0),
                action_id=ACTION_ID,
                confirmed_at=CONFIRMED_AT,
            )
        with self.assertRaises(ValueError):
            build_record_from_homework(
                _homework_facts(duration_minutes="40"),
                action_id=ACTION_ID,
                confirmed_at=CONFIRMED_AT,
            )


class ProgressRecordRuleTests(IsolatedDatabaseTestCase):
    def test_kind_to_activity_mapping_table(self):
        expected = {
            "covered": "class",
            "class_completed": "class",
            "reviewed": "review",
            "quiz_passed": "quiz",
            "self_tested": "self_test",
            "mastered_claim": "self_test",
        }
        self.assertEqual(set(PROGRESS_KINDS), set(expected))
        for kind, activity in expected.items():
            record, warnings = build_record_from_progress(
                _progress_facts(kind=kind),
                action_id=ACTION_ID,
                confirmed_at=CONFIRMED_AT,
            )
            self.assertEqual(record["activity"], activity, kind)
            self.assertEqual(record["progress_kind"], kind)
            self.assertEqual(record["topic"], "哈希表")
            self.assertEqual(record["date"], CONFIRMED_AT)
            self.assertEqual(record["date_basis"], "confirmation_fallback")
            self.assertNotIn("status", record)

    def test_mastered_claim_only_writes_self_assessed_note(self):
        record, warnings = build_record_from_progress(
            _progress_facts(kind="mastered_claim", note="二叉树都能默写"),
            action_id=ACTION_ID,
            confirmed_at=CONFIRMED_AT,
        )
        self.assertEqual(record["note"], "自评掌握：二叉树都能默写")
        self.assertTrue(
            any("自评掌握" in warning and "不直接改变模型状态" in warning
                for warning in warnings),
            warnings,
        )
        self.assertNotIn("status", record)
        self.assertNotIn("mastery", record)

    def test_unknown_progress_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            build_record_from_progress(
                _progress_facts(kind="放松了"),
                action_id=ACTION_ID,
                confirmed_at=CONFIRMED_AT,
            )

    def test_progress_date_validation(self):
        with self.assertRaises(ValueError):
            build_record_from_progress(
                _progress_facts(completed_date="2026-02-30"),
                action_id=ACTION_ID,
                confirmed_at=CONFIRMED_AT,
            )
        record, _ = build_record_from_progress(
            _progress_facts(completed_date="2026-09-02"),
            action_id=ACTION_ID,
            confirmed_at=CONFIRMED_AT,
        )
        self.assertEqual(record["date"], "2026-09-02")
        self.assertEqual(record["date_basis"], "user_stated_completed")


class RevisionRuleTests(IsolatedDatabaseTestCase):
    def _original(self) -> dict:
        return {
            "id": 7,
            "date": "2026-09-01",
            "subject": "测试学科",
            "module": "基础",
            "topic": "哈希表",
            "activity": "exercise",
            "source": "outside_class",
            "score": 80,
            "note": "原笔记",
            "problems": [{"title": "第1题", "statement": "题面"}],
            "attachments": [{"file_name": "a.txt", "sha256": "ab" * 32}],
            "confirmed_at": CONFIRMED_AT,
        }

    def test_allowed_fields_are_applied_and_attachments_preserved(self):
        original = self._original()
        replacement, warnings = build_revision(
            original,
            {"fields": {"note": "新笔记", "score": 95, "date": "2026-09-02"}},
            record_id=7,
        )
        self.assertEqual(replacement["id"], 7)
        self.assertEqual(replacement["note"], "新笔记")
        self.assertEqual(replacement["score"], 95)
        self.assertEqual(replacement["date"], "2026-09-02")
        self.assertEqual(replacement["attachments"], original["attachments"])
        self.assertEqual(replacement["problems"], original["problems"])
        self.assertEqual(original["note"], "原笔记")
        self.assertTrue(
            any("全量字段替换" in warning for warning in warnings), warnings
        )

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            build_revision(
                self._original(),
                {"fields": {"nickname": "不允许"}},
                record_id=7,
            )
        self.assertIn("修订仅允许", str(ctx.exception))

    def test_attachment_change_is_rejected_before_anything_else(self):
        with self.assertRaises(ValueError) as ctx:
            build_revision(
                self._original(),
                {"fields": {"note": "顺手改附件"}, "attachments": []},
                record_id=7,
            )
        self.assertIn("附件", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            build_revision(
                self._original(),
                {"fields": {"attachments": []}},
                record_id=7,
            )
        self.assertIn("附件", str(ctx.exception))

    def test_unknown_top_level_key_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            build_revision(
                self._original(),
                {"fields": {}, "problems": None, "extra": 1},
                record_id=7,
            )
        self.assertIn("未支持键", str(ctx.exception))

    def test_invalid_date_and_score_ranges_are_rejected(self):
        with self.assertRaises(ValueError):
            build_revision(
                self._original(),
                {"fields": {"date": "2026-02-30"}},
                record_id=7,
            )
        with self.assertRaises(ValueError):
            build_revision(
                self._original(),
                {"fields": {"score": 120}},
                record_id=7,
            )
        with self.assertRaises(ValueError):
            build_revision(
                self._original(),
                {"fields": {"duration_minutes": 0}},
                record_id=7,
            )

    def test_full_problem_replacement_is_normalized(self):
        replacement, warnings = build_revision(
            self._original(),
            {
                "fields": {},
                "problems": [
                    {"title": "第1题", "statement": "新题面"},
                    {"title": "第2题", "statement": "新题面二"},
                ],
            },
            record_id=7,
        )
        self.assertEqual(len(replacement["problems"]), 2)
        self.assertTrue(
            any("完整替换" in warning for warning in warnings), warnings
        )


class MasteryPreviewTests(IsolatedDatabaseTestCase):
    def _fixture(self):
        model_path = self.temp_root / "model.json"
        model_path.write_text(json.dumps(MODEL, ensure_ascii=False), encoding="utf-8")
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            database.import_model_json(connection, MODEL)
        return model_path

    def _evidence_record(self) -> dict:
        return {
            "date": "2026-09-14",
            "subject": "测试学科",
            "module": "基础",
            "topic": "哈希表",
            "activity": "exercise",
            "source": "outside_class",
            "score": 80,
            "note": "哈希表练习",
            "problems": [{
                "title": "哈希表题",
                "related_topics": ["哈希表"],
                "correctness": 1.0,
                "difficulty_score": 60,
            }],
        }

    def test_matching_topic_shows_movement_and_recompute_never_writes(self):
        model_path = self._fixture()
        model_hash_before = file_sha256(model_path)
        db_hash_before = file_sha256(self.db_path)
        counts_before = database.get_counts(self.db_path)

        preview = preview_mastery_impact(
            self._evidence_record(), db_path=self.db_path, model_path=model_path
        )

        self.assertFalse(preview["no_evidence"])
        self.assertEqual(len(preview["matched_topics"]), 1)
        matched = preview["matched_topics"][0]
        self.assertEqual(matched["subject"], "测试学科")
        self.assertEqual(matched["module"], "基础")
        self.assertEqual(matched["topic"], "哈希表")
        self.assertEqual(matched["mastery_before"], 0.2)
        self.assertGreater(matched["mastery_after"], 0.2)
        self.assertEqual(matched["status_before"], "learning")
        self.assertEqual(matched["status_after"], "learned_needs_review")
        self.assertEqual(len(preview["subject_mastery"]), 1)
        subject_summary = preview["subject_mastery"][0]
        self.assertEqual(subject_summary["subject"], "测试学科")
        self.assertEqual(subject_summary["before"], 0.3)
        self.assertGreater(subject_summary["after"], 0.3)
        self.assertTrue(
            any("只读推演" in note for note in preview["notes"]), preview["notes"]
        )

        # 预览绝不写模型文件、绝不写数据库
        self.assertEqual(file_sha256(model_path), model_hash_before)
        self.assertEqual(file_sha256(self.db_path), db_hash_before)
        self.assertEqual(database.get_counts(self.db_path), counts_before)

    def test_draft_only_record_produces_no_evidence_and_no_writes(self):
        model_path = self._fixture()
        model_hash_before = file_sha256(model_path)
        record = dict(self._evidence_record(), draft_only=True)
        record["problems"] = [{"title": "第1题", "statement": "仅题面文字"}]

        preview = preview_mastery_impact(
            record, db_path=self.db_path, model_path=model_path
        )

        self.assertTrue(preview["no_evidence"])
        self.assertEqual(preview["matched_topics"], [])
        self.assertTrue(
            any("仅题面草稿" in note for note in preview["notes"]), preview["notes"]
        )
        self.assertEqual(file_sha256(model_path), model_hash_before)

    def test_record_without_subject_or_evidence_produces_no_evidence(self):
        model_path = self._fixture()
        no_subject = dict(self._evidence_record(), subject="")
        preview = preview_mastery_impact(
            no_subject, db_path=self.db_path, model_path=model_path
        )
        self.assertTrue(preview["no_evidence"])
        self.assertTrue(
            any("缺少学科" in note for note in preview["notes"]), preview["notes"]
        )


if __name__ == "__main__":
    unittest.main()
