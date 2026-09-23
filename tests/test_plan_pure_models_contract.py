from __future__ import annotations

import unittest


class PlanPureModelsContractTests(unittest.TestCase):
    def test_homework_parsing_goldens_and_practice_facade_identity(self) -> None:
        from study_app.core import practice_spec
        from study_app.ui import main_window

        homework = "当天作业：共 6 题（2 道级数、1 题 Green、3 道综合）；题量 5 题"

        self.assertEqual(practice_spec.extract_homework_exercise_count(homework, 2), 6)
        self.assertEqual(
            practice_spec.normalize_homework_count(homework, 7),
            "当天作业：共 6 题（2 道级数、1 题 Green、3 道综合）；题量 7 题",
        )
        self.assertEqual(
            [practice_spec.desired_practice_seed_count(value) for value in (0, 1, 6, 20)],
            [3, 3, 3, 8],
        )
        self.assertEqual(
            practice_spec._extract_homework_component_counts(homework),
            [("级数", 2), ("Green", 1), ("综合", 3)],
        )
        self.assertEqual(
            practice_spec._extract_homework_components(homework),
            ["级数", "Green", "综合"],
        )
        self.assertEqual(practice_spec.safe_int("7.9"), 7)
        self.assertEqual(practice_spec.safe_int(None, 4), 4)
        self.assertTrue(practice_spec.is_oj_plan_homework("当天作业：LeetCode 两题"))
        self.assertEqual(practice_spec.planned_homework_difficulty("参考难度 120/100"), 100.0)

        names = (
            "safe_int",
            "extract_homework_exercise_count",
            "normalize_homework_count",
            "desired_practice_seed_count",
            "_extract_homework_component_counts",
            "_extract_homework_components",
            "is_oj_plan_homework",
            "extract_template_brief",
            "infer_practice_template",
            "planned_homework_difficulty",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(main_window, name), getattr(practice_spec, name))

    def test_study_plan_item_hash_golden_and_facade_identity(self) -> None:
        from study_app.core import study_plan_items
        from study_app.ui import main_window

        self.assertEqual(
            study_plan_items.study_plan_item_hash(
                "short", 1, "result", "当天作业：完成 2 题", 3
            ),
            "13675ef3cf05db2f1fa8895e8de40692f718df73",
        )
        names = (
            "infer_subject_topic_from_plan_line",
            "is_plan_homework_item",
            "split_short_plan_item",
            "study_plan_item_hash",
            "build_study_plan_items",
            "humanize_plan_text",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(main_window, name), getattr(study_plan_items, name))

    def test_complete_study_plan_item_structure_golden(self) -> None:
        from study_app.core.study_plan_items import build_study_plan_items

        plan = {
            "judgement": ["当前稳定"],
            "short": ["任务：复习图\n当天作业：完成 2 题"],
            "evidence": ["依据 A"],
            "unknown": ["忽略"],
        }

        self.assertEqual(
            build_study_plan_items(plan),
            [
                {
                    "section_key": "judgement",
                    "section_title": "当前判断",
                    "day_index": None,
                    "item_type": "info",
                    "item_text": "当前稳定",
                    "item_order": 0,
                    "item_hash": "e08b1870b507681e2717f884ab5d23a8cf94d674",
                },
                {
                    "section_key": "short",
                    "section_title": "今日计划",
                    "day_index": 1,
                    "item_type": "check",
                    "item_text": "任务：复习图",
                    "item_order": 1,
                    "item_hash": "d4682780721309e297e660c6ded8ba371029047d",
                },
                {
                    "section_key": "short",
                    "section_title": "今日计划",
                    "day_index": 1,
                    "item_type": "result",
                    "item_text": "当天作业：完成 2 题",
                    "item_order": 2,
                    "item_hash": "c73e176a0c19c7249b650015a32b0904ea97d77a",
                },
                {
                    "section_key": "evidence",
                    "section_title": "生成依据",
                    "day_index": None,
                    "item_type": "info",
                    "item_text": "依据 A",
                    "item_order": 3,
                    "item_hash": "89e86b76ac1f31cf3436003c1eb7da4d6ab5b044",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
