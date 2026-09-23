from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch


class StudyPlanFeedbackContractTests(unittest.TestCase):
    def test_planned_homework_score_fallback_goldens(self) -> None:
        from study_app.core.study_plan_feedback import planned_homework_score

        with (
            patch("learning_monitor.load_json", return_value={"warning_policy": {}}),
            patch(
                "learning_monitor.difficulty_problem_score",
                return_value=None,
            ) as difficulty_problem_score,
        ):
            self.assertEqual(planned_homework_score(None, True), 79.1)
            self.assertEqual(planned_homework_score(None, False), 31.7)
            self.assertEqual(planned_homework_score(0, True), 58.0)
            self.assertEqual(planned_homework_score(0, False), 8.0)
            self.assertEqual(planned_homework_score(100, True), 100.0)
            self.assertEqual(planned_homework_score(100, False), 58.0)

        self.assertEqual(difficulty_problem_score.call_count, 6)

    def test_complete_and_incomplete_feedback_full_dicts(self) -> None:
        from study_app.core.study_plan_feedback import plan_day_feedback_details

        saved_plan = {
            "items": [
                {
                    "section_key": "short",
                    "day_index": 1,
                    "item_type": "check",
                    "item_text": "回忆自测",
                    "checked": True,
                },
                {
                    "section_key": "short",
                    "day_index": 1,
                    "item_type": "result",
                    "item_text": "计算机科学 / 图搜索",
                    "checked": True,
                    "result": None,
                },
                {
                    "section_key": "short",
                    "day_index": 2,
                    "item_type": "check",
                    "item_text": "复习定义",
                    "checked": True,
                },
                {
                    "section_key": "short",
                    "day_index": 2,
                    "item_type": "result",
                    "item_text": "计算机科学 / 动态规划",
                    "checked": False,
                    "result": None,
                },
            ]
        }

        self.assertEqual(
            plan_day_feedback_details(saved_plan, object(), "计算机科学"),
            [
                {
                    "day_index": 1,
                    "complete": True,
                    "line": (
                        "今日进度 2/2，已完成。计算机科学 / 图搜索；"
                        "完成状态已保存，具体正确率、错因、学习分数与掌握度将在上传做题记录后更新。"
                    ),
                    "popup": (
                        "今日计划已完成。\n\n"
                        "学科与知识点：计算机科学 / 图搜索\n"
                        "当天进度：2/2\n\n"
                        "完成勾选仅表示任务已执行，不代表题目全部正确。"
                        "请通过“新增记录”上传实际做题情况，系统解析后再更新分数、掌握度、"
                        "遗忘风险和知识追踪证据。"
                    ),
                },
                {
                    "day_index": 2,
                    "complete": False,
                    "line": (
                        "今日进度 1/2；作业完成项 0/1。"
                        "完成勾选只记录执行状态，具体答题情况将在上传记录后判定。"
                    ),
                    "popup": "",
                },
            ],
        )

    def test_feedback_lines_and_facade_identity(self) -> None:
        from study_app.core import study_plan_feedback
        from study_app.ui import main_window

        for name in (
            "planned_homework_score",
            "plan_day_feedback_details",
            "plan_day_feedback_lines",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(main_window, name),
                    getattr(study_plan_feedback, name),
                )

        saved_plan = {
            "items": [
                {
                    "section_key": "short",
                    "day_index": 1,
                    "item_type": "result",
                    "item_text": "计算机科学 / 图搜索",
                    "checked": False,
                    "result": None,
                }
            ]
        }
        details = study_plan_feedback.plan_day_feedback_details(
            saved_plan, object(), "计算机科学"
        )
        self.assertEqual(
            study_plan_feedback.plan_day_feedback_lines(
                saved_plan, object(), "计算机科学"
            ),
            [details[0]["line"]],
        )

    def test_canonical_module_has_no_ui_or_pyside_dependency(self) -> None:
        from study_app.core import study_plan_feedback

        source = inspect.getsource(study_plan_feedback)
        self.assertNotIn("study_app.ui", source)
        self.assertNotIn("PySide", source)


if __name__ == "__main__":
    unittest.main()
