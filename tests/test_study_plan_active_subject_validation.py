from __future__ import annotations

import unittest
from unittest.mock import patch

from study_app.ai.validation import LLMValidationError


class StudyPlanActiveSubjectValidationTests(unittest.TestCase):
    @staticmethod
    def plan_with_text(text: str) -> dict[str, list[str]]:
        return {
            "judgement": [text],
            "goals": ["完成活动学科训练"],
            "short": ["完成计算机科学练习"],
            "diagnostic": ["记录错因"],
            "record_template": ["记录正确率"],
            "expected": ["更新活动学科状态"],
            "evidence": ["依据活动学科证据"],
        }

    @staticmethod
    def payload() -> dict[str, object]:
        return {
            "subject_scope": "全部活动学科",
            "allowed_subjects": ["计算机科学"],
        }

    def test_compact_payload_keeps_only_explicit_allowed_subject_names(self) -> None:
        from study_app.ai.study_plan_generator import compact_plan_payload

        payload = {
            "subject_scope": "全部活动学科",
            "allowed_subjects": ["计算机科学"],
            "subjects": [
                {
                    "name": "计算机科学",
                    "source_json": {"sensitive": "must-not-leak"},
                    "mastery_score": 75,
                }
            ],
        }

        compact = compact_plan_payload(payload)

        self.assertEqual(compact["allowed_subjects"], ["计算机科学"])
        self.assertNotIn("subjects", compact)
        self.assertNotIn("must-not-leak", str(compact))

    def test_repair_prompt_uses_compact_allowed_subjects_without_full_subject_objects(self) -> None:
        from study_app.ai.study_plan_generator import build_plan_repair_prompt

        payload = {
            "subject_scope": "全部活动学科",
            "allowed_subjects": ["计算机科学"],
            "subjects": [{"name": "计算机科学", "source_json": "must-not-leak"}],
        }

        prompt = build_plan_repair_prompt(payload, "bad output", ValueError("bad"))

        self.assertIn('"allowed_subjects": ["计算机科学"]', prompt)
        self.assertNotIn("must-not-leak", prompt)

    def test_allowed_subject_validation_rejects_archived_subjects_and_calculus_aliases(self) -> None:
        from study_app.ai.study_plan_generator import validate_plan_allowed_subjects

        forbidden_references = (
            "高等数学",
            "高数",
            "微积分",
            "微积分2",
            "微积分 2",
            "大学物理学",
            "大学物理",
            "大物",
            "化学原理",
            "数据结构与算法基础",
            "数据结构课程",
        )

        for reference in forbidden_references:
            with self.subTest(reference=reference):
                with self.assertRaises(LLMValidationError):
                    validate_plan_allowed_subjects(
                        self.plan_with_text(f"今天复习{reference}"),
                        self.payload(),
                    )

    def test_allowed_subject_validation_accepts_computer_science_plan(self) -> None:
        from study_app.ai.study_plan_generator import validate_plan_allowed_subjects

        validate_plan_allowed_subjects(
            self.plan_with_text("今天复习计算机科学的图算法"),
            self.payload(),
        )
        validate_plan_allowed_subjects(
            self.plan_with_text("今天提高数据结构与算法能力"),
            self.payload(),
        )

    def test_first_illegal_llm_output_is_repaired_to_allowed_subject(self) -> None:
        from study_app.ai.study_plan_generator import generate_plan_with_llm

        illegal = self.plan_with_text("今天复习高等数学")
        legal = self.plan_with_text("今天复习计算机科学")
        with (
            patch("study_app.ai.study_plan_generator.audited_chat_completion_json", side_effect=["first", "repair"]) as llm_call,
            patch("study_app.ai.study_plan_generator.parse_and_validate_plan", side_effect=[illegal, legal]),
            patch("study_app.ai.study_plan_generator.validate_plan_exam_scope"),
        ):
            result = generate_plan_with_llm(self.payload())

        self.assertIn("计算机科学", result["judgement"][0])
        self.assertEqual(llm_call.call_count, 2)
        repair_summary = llm_call.call_args_list[1].args[2]
        self.assertEqual(repair_summary["repair_reason"], {"type": "validation_error"})
        self.assertNotIn("高等数学", str(repair_summary))

    def test_illegal_repair_output_is_rejected(self) -> None:
        from study_app.ai.study_plan_generator import generate_plan_with_llm

        illegal_first = self.plan_with_text("今天复习大学物理学")
        illegal_repair = self.plan_with_text("改为复习微积分2")
        with (
            patch("study_app.ai.study_plan_generator.audited_chat_completion_json", side_effect=["first", "repair"]),
            patch(
                "study_app.ai.study_plan_generator.parse_and_validate_plan",
                side_effect=[illegal_first, illegal_repair],
            ),
            patch("study_app.ai.study_plan_generator.validate_plan_exam_scope"),
        ):
            with self.assertRaises(LLMValidationError):
                generate_plan_with_llm(self.payload())

    def test_scope_backend_failure_does_not_trigger_llm_repair(self) -> None:
        from study_app.ai.study_plan_generator import generate_plan_with_llm
        from study_app.core.study_phase import ScopeValidationError

        plan = self.plan_with_text("今天复习计算机科学")
        with (
            patch(
                "study_app.ai.study_plan_generator.audited_chat_completion_json",
                return_value="first",
            ) as llm_call,
            patch(
                "study_app.ai.study_plan_generator.parse_and_validate_plan",
                return_value=plan,
            ),
            patch("study_app.ai.study_plan_generator.validate_plan_allowed_subjects"),
            patch(
                "study_app.ai.study_plan_generator.validate_plan_exam_scope",
                side_effect=ScopeValidationError("model unavailable"),
            ),
        ):
            with self.assertRaises(ScopeValidationError):
                generate_plan_with_llm(self.payload())

        llm_call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
