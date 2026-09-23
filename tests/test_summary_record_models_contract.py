from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace


class SummaryRecordModelsContractTests(unittest.TestCase):
    def test_daily_summary_signature_golden_and_facade_identity(self) -> None:
        from study_app.ui import daily_summary_support, main_window

        active = SimpleNamespace(
            name="计算机科学",
            archived=False,
            window_score=61,
            covered_mastery_score=72,
            covered_topic_count=3,
            total_topic_count=8,
            mastery_score=68,
        )
        archived = SimpleNamespace(
            name="高等数学",
            archived=True,
            window_score=99,
            covered_mastery_score=99,
            covered_topic_count=9,
            total_topic_count=9,
            mastery_score=99,
        )
        state = SimpleNamespace(
            today=date(2026, 7, 17),
            subjects=(archived, active),
            memory_risks=(
                {
                    "subject": "计算机科学",
                    "topic": "图",
                    "recall": 0.45678,
                    "last_review": "2026-07-16",
                },
                {
                    "subject": "高等数学",
                    "topic": "级数",
                    "recall": 0.1,
                    "last_review": "2026-07-15",
                },
            ),
            bkt_alerts=(
                {
                    "subject": "计算机科学",
                    "topic": "图",
                    "mastery_probability": 0.61234,
                },
                {
                    "subject": "高等数学",
                    "topic": "级数",
                    "mastery_probability": 0.2,
                },
            ),
        )
        records = [
            {"id": 7, "date": "2026-07-17", "subject": "计算机科学"},
            {"id": 8, "date": "2026-07-16", "subject": "计算机科学"},
            {"id": 9, "date": "2026-07-17", "subject": "高等数学"},
        ]

        self.assertEqual(
            daily_summary_support.daily_summary_cache_signature(state, records),
            "817b2c83512ed6ef39ae647442c67e71cedbdaad118eb29e68aa080e049e6060",
        )
        self.assertIs(
            main_window.daily_summary_cache_signature,
            daily_summary_support.daily_summary_cache_signature,
        )
        self.assertIs(
            main_window.matching_daily_summary_cache,
            daily_summary_support.matching_daily_summary_cache,
        )

    def test_recent_record_lines_golden_and_facade_identity(self) -> None:
        from study_app.ui import main_window, record_view_model

        records = [
            {
                "record_date": "2026-07-17",
                "subject_name": "计算机科学",
                "topic_name": "图",
                "score": 82.5,
                "problem_count": 3,
                "attachments": ["a", "b"],
            },
            {
                "record_date": "2026-07-16",
                "subject_name": "高等数学",
                "topic_name": None,
                "module_name": None,
                "score": None,
            },
        ]

        self.assertEqual(
            record_view_model.recent_record_display_lines(records),
            (
                "2026-07-17  计算机科学 / 图 | 82 分 | 题目 3 | 附件 2",
                "2026-07-16  高等数学 / 未命名 | 题目 0 | 附件 0",
            ),
        )
        self.assertIs(
            main_window.recent_record_display_lines,
            record_view_model.recent_record_display_lines,
        )


if __name__ == "__main__":
    unittest.main()
