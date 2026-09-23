from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch


class StudyPlanHomeworkContractTests(unittest.TestCase):
    def test_facade_reexports_canonical_homework_helpers_by_identity(self) -> None:
        from study_app.core import study_plan_homework
        from study_app.ui import main_window

        names = (
            "_lowest_cs_oj_focus",
            "_daily_plan",
            "_is_cs_oj_focus",
            "_oj_topic_hint",
            "_oj_seed_lines",
            "_oj_seed_line_scores",
            "_oj_daily_plan_text",
            "_homework_for_topic",
            "_completion_standard_for_topic",
            "_matches_plan_subject",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(main_window, name),
                    getattr(study_plan_homework, name),
                )

    def test_assignment_lookup_ignores_facade_patch_and_uses_canonical_patch(self) -> None:
        from study_app.core import study_plan_homework

        canonical_assignment = SimpleNamespace(
            difficulty_score=67,
            to_homework_text=lambda: "canonical assignment",
        )
        facade_assignment = SimpleNamespace(
            difficulty_score=99,
            to_homework_text=lambda: "facade assignment",
        )
        with (
            patch(
                "study_app.ui.main_window.generate_practice_assignment",
                return_value=facade_assignment,
                create=True,
            ) as facade_generate,
            patch(
                "study_app.core.study_plan_homework.generate_practice_assignment",
                return_value=canonical_assignment,
            ) as canonical_generate,
        ):
            homework = study_plan_homework._homework_for_topic("计算机科学 / 图")
            standard = study_plan_homework._completion_standard_for_topic("算法")

        self.assertEqual(homework, "canonical assignment")
        self.assertEqual(
            standard,
            "参考难度 67/100；能不看资料写出核心定义/流程，并独立完成至少 1 道基础手算题。",
        )
        facade_generate.assert_not_called()
        self.assertEqual(canonical_generate.call_count, 2)

    def test_oj_routes_and_daily_plan_text_goldens(self) -> None:
        from study_app.core import study_plan_homework

        assignment = SimpleNamespace(difficulty_score=64)
        seeds = [
            ("题目甲（官方 Medium，校准难度 70/100）：摘要甲；https://a", 70),
            ("题目乙（官方 Medium，校准难度 74/100）：摘要乙；https://b", 74),
        ]
        with (
            patch(
                "study_app.core.study_plan_homework.generate_practice_assignment",
                return_value=assignment,
            ),
            patch(
                "study_app.core.study_plan_homework._oj_seed_line_scores",
                return_value=seeds,
            ),
        ):
            text = study_plan_homework._oj_daily_plan_text(
                "计算机科学 / 算法设计与OJ训练 / 动态规划"
            )

        self.assertTrue(
            study_plan_homework._is_cs_oj_focus(
                "算法设计与OJ训练 / 动态规划", "计算机科学"
            )
        )
        self.assertEqual(
            study_plan_homework._oj_topic_hint("图论最短路"),
            "图搜索、并查集与最短路",
        )
        self.assertEqual(
            study_plan_homework._oj_topic_hint("动态规划入门"),
            "动态规划基础",
        )
        self.assertIn("训练主题：动态规划基础。", text)
        self.assertIn("参考难度 72/100（中高，按所选 LeetCode 原题校准分折算）", text)
        self.assertIn("至少提交 2 道官方原题", text)

    def test_subject_match_contract(self) -> None:
        from study_app.core.study_plan_homework import _matches_plan_subject

        self.assertTrue(_matches_plan_subject("计算机科学 / 图", None))
        self.assertTrue(_matches_plan_subject("计算机科学 / 图", "计算机科学"))
        self.assertFalse(_matches_plan_subject("高等数学 / 级数", "计算机科学"))

    def test_oj_seed_selection_avoids_recent_plan_titles(self) -> None:
        from study_app.core import study_plan_homework

        assignment = SimpleNamespace(difficulty_score=64)
        problems = [
            {
                "title": "旧题",
                "difficulty_score": 64,
                "raw": {"difficulty_label": "Medium", "url": "https://old"},
            },
            {
                "title": "新题甲",
                "difficulty_score": 65,
                "raw": {"difficulty_label": "Medium", "url": "https://new-a"},
            },
            {
                "title": "新题乙",
                "difficulty_score": 66,
                "raw": {"difficulty_label": "Medium", "url": "https://new-b"},
            },
        ]
        with (
            patch(
                "study_app.core.study_plan_homework.generate_practice_assignment",
                return_value=assignment,
            ),
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=problems,
            ),
            patch(
                "study_app.data.database.recent_study_plan_texts",
                return_value=["昨日计划包含旧题"],
            ),
        ):
            lines = study_plan_homework._oj_seed_line_scores(
                "计算机科学 / 算法设计与OJ训练 / 堆", limit=2
            )

        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0][0].startswith("新题甲"))
        self.assertTrue(lines[1][0].startswith("新题乙"))
        self.assertNotIn("旧题", "\n".join(line for line, _score in lines))


if __name__ == "__main__":
    unittest.main()
