from __future__ import annotations

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from study_app.ai.validation import LLMValidationError


class DailySummaryActiveSubjectValidationTests(unittest.TestCase):
    @staticmethod
    def state() -> SimpleNamespace:
        def subject(name: str, archived: bool) -> SimpleNamespace:
            return SimpleNamespace(
                name=name,
                archived=archived,
                window_score=None if archived else 60,
                covered_mastery_score=70,
                covered_topic_count=5,
                total_topic_count=10,
                mastery_score=70,
                warnings=(),
            )

        return SimpleNamespace(
            start=date(2026, 7, 15),
            today=date(2026, 7, 17),
            benchmark=55,
            subjects=(
                subject("高等数学", True),
                subject("大学物理学", True),
                subject("化学原理", True),
                subject("数据结构与算法基础", True),
                subject("计算机科学", False),
            ),
            todos=(),
            memory_risks=(),
            bkt_alerts=(),
            low_subjects=(),
            stale_subjects=(),
        )

    @staticmethod
    def valid_raw() -> dict[str, object]:
        return {
            "overview": ["今天完成计算机科学图算法复习。"],
            "risks": ["树与图仍需巩固。"],
            "actions": ["提高数据结构与算法能力。"],
            "source": ["依据计算机科学活动记录。"],
        }

    def generate(self, raw: dict[str, object]) -> dict[str, list[str]]:
        from study_app.ai.daily_summary import generate_daily_summary_with_llm

        with patch(
            "study_app.ai.daily_summary.audited_chat_completion_json",
            return_value=json.dumps(raw, ensure_ascii=False),
        ):
            return generate_daily_summary_with_llm(self.state(), [])

    def test_daily_summary_payload_exposes_allowed_active_subjects(self) -> None:
        from study_app.ai.daily_summary import build_daily_summary_payload

        payload = build_daily_summary_payload(self.state(), [])

        self.assertEqual(payload["allowed_subjects"], ["计算机科学"])

    def test_llm_summary_rejects_archived_course_aliases(self) -> None:
        aliases = (
            "高等数学",
            "高数",
            "微积分",
            "微积分2",
            "微积分 2",
            "微积分Ⅱ",
            "大学物理学",
            "大学物理",
            "大物",
            "化学原理",
            "数据结构与算法基础",
            "数据结构课程",
        )
        for alias in aliases:
            with self.subTest(alias=alias):
                raw = self.valid_raw()
                raw["overview"] = [f"今天重点复习{alias}。"]
                with self.assertRaises(LLMValidationError):
                    self.generate(raw)

    def test_llm_summary_checks_every_display_text_field(self) -> None:
        fields = ("overview", "risks", "actions", "source")
        for field in fields:
            with self.subTest(field=field):
                raw = self.valid_raw()
                raw[field] = ["建议恢复高等数学复习。"]
                with self.assertRaises(LLMValidationError):
                    self.generate(raw)

    def test_llm_summary_rejects_non_string_display_items_before_normalizing(self) -> None:
        malicious_items = (
            {"高等数学": "复习"},
            {"course": "高等数学"},
            {"nested": [{"course": "微积分"}]},
            ["大学物理"],
        )
        for field in ("overview", "risks", "actions", "source"):
            for item in malicious_items:
                with self.subTest(field=field, item=item):
                    raw = self.valid_raw()
                    raw[field] = [item]
                    with self.assertRaises(LLMValidationError):
                        self.generate(raw)

    def test_legal_computer_science_summary_passes(self) -> None:
        result = self.generate(self.valid_raw())

        self.assertIn("计算机科学", result["overview"][0])
        self.assertIn("提高数据结构与算法能力", result["actions"][0])


if __name__ == "__main__":
    unittest.main()
