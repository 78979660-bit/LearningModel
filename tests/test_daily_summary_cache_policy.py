from __future__ import annotations

import unittest
from types import SimpleNamespace


class DailySummaryCachePolicyTests(unittest.TestCase):
    @staticmethod
    def subjects() -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(name="高等数学", archived=True),
            SimpleNamespace(name="大学物理学", archived=True),
            SimpleNamespace(name="化学原理", archived=True),
            SimpleNamespace(name="数据结构与算法基础", archived=True),
            SimpleNamespace(name="计算机科学", archived=False),
        )

    @staticmethod
    def valid_summary() -> dict[str, list[str]]:
        return {
            "overview": ["计算机科学"],
            "risks": ["图算法"],
            "actions": ["继续练习"],
            "source": ["活动记录", "生成方式：LLM 增强总结，经本地结构校验。"],
        }

    @staticmethod
    def signed_cache(summary: dict[str, list[str]]) -> dict[str, object]:
        return {
            "signature": "active-signature",
            "mode": "llm",
            "summary": summary,
        }

    def test_legacy_unsigned_cache_with_archived_subject_is_a_miss(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        legacy = {
            "overview": ["今天继续高等数学与微积分。"],
            "actions": ["完成高数练习。"],
        }

        self.assertIsNone(matching_daily_summary_cache(legacy, "active-signature", self.subjects()))

    def test_mismatched_signature_is_a_miss(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        cached = {
            "signature": "old-signature",
            "mode": "llm",
            "summary": {"overview": ["计算机科学"]},
        }

        self.assertIsNone(matching_daily_summary_cache(cached, "active-signature", self.subjects()))

    def test_exact_signature_cache_with_non_string_display_item_is_a_miss(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        cached = {
            "signature": "active-signature",
            "mode": "llm",
            "summary": {
                "overview": [{"高等数学": "复习"}],
                "risks": ["图算法"],
                "actions": ["继续练习"],
                "source": ["活动记录"],
            },
        }

        self.assertIsNone(
            matching_daily_summary_cache(cached, "active-signature", self.subjects())
        )

    def test_exact_signature_cache_with_archived_subject_text_is_a_miss(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        cached = {
            "signature": "active-signature",
            "mode": "llm",
            "summary": {
                "overview": ["今天复习高等数学。"],
                "risks": ["图算法"],
                "actions": ["继续练习"],
                "source": ["活动记录"],
            },
        }

        self.assertIsNone(
            matching_daily_summary_cache(cached, "active-signature", self.subjects())
        )

    def test_exact_signature_cache_with_overlong_overview_item_is_a_miss(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        summary = self.valid_summary()
        summary["overview"] = ["计" * 701]

        self.assertIsNone(
            matching_daily_summary_cache(
                self.signed_cache(summary), "active-signature", self.subjects()
            )
        )

    def test_exact_signature_cache_with_too_many_items_is_a_miss(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        limits = {"overview": 3, "risks": 5, "actions": 6, "source": 6}
        for field, maximum in limits.items():
            with self.subTest(field=field):
                summary = self.valid_summary()
                summary[field] = [f"计算机科学 {index}" for index in range(maximum + 1)]

                self.assertIsNone(
                    matching_daily_summary_cache(
                        self.signed_cache(summary), "active-signature", self.subjects()
                    )
                )

    def test_exact_signature_normalized_cache_allows_six_source_items(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        summary = self.valid_summary()
        summary["source"] = [f"计算机科学依据 {index}" for index in range(6)]
        cached = self.signed_cache(summary)

        self.assertEqual(
            matching_daily_summary_cache(cached, "active-signature", self.subjects()),
            (summary, "llm"),
        )

    def test_exact_signature_returns_nested_summary_and_mode(self) -> None:
        from study_app.ui.main_window import matching_daily_summary_cache

        summary = self.valid_summary()
        cached = self.signed_cache(summary)

        self.assertEqual(
            matching_daily_summary_cache(cached, "active-signature", self.subjects()),
            (summary, "llm"),
        )


if __name__ == "__main__":
    unittest.main()
