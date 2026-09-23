from __future__ import annotations

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from study_app.core.dashboard import DashboardState, SubjectSummary, TodoItem


class LocalStudyPlanContractTests(unittest.TestCase):
    @staticmethod
    def state() -> DashboardState:
        return DashboardState(
            start=date(2026, 7, 15),
            today=date(2026, 7, 17),
            benchmark=55,
            subjects=(
                SubjectSummary(
                    name="计算机科学",
                    initial_score=60,
                    window_score=60,
                    has_window_records=True,
                    mastery_score=75,
                    covered_mastery_score=70,
                    covered_topic_count=5,
                    total_topic_count=10,
                    latest_record=None,
                    latest_review=None,
                    warnings=(),
                    archived=False,
                ),
            ),
            todos=(TodoItem("做题", "计算机科学 / 图", "活动待办", "high", 0.9),),
            memory_risks=(),
            bkt_alerts=(),
        )

    def test_complete_local_plan_structure_golden(self) -> None:
        from study_app.core.local_study_plan import generate_study_plan

        assignment = SimpleNamespace(
            difficulty_score=60,
            to_homework_text=lambda: "参考难度 60/100；题库模板 TEST-ACTIVE；题量 1 题。",
        )
        with patch(
            "study_app.core.study_plan_homework.generate_practice_assignment",
            return_value=assignment,
        ):
            plan = generate_study_plan(self.state())

        self.assertEqual(
            plan,
            {
                "judgement": [
                    "当前范围为 全部活动学科，没有强低分信号，计划重点放在巩固和防遗忘。",
                    "已学范围口径：计算机科学 已学掌握度 70/100（覆盖 5/10 个知识点），整门课总掌握度 75/100。",
                    "当前模型缺少足够的题目级证据，计划会更偏向建立记录闭环。",
                ],
                "goals": [
                    "今天围绕 计算机科学 / 图 完成一个“回忆复习 -> 针对练习 -> 错因复盘”的闭环。",
                    "每次学习后至少记录：做题数量、正确率、错因、是否独立完成。",
                    "优先处理能同时降低遗忘风险和 BKT 预警的知识点，而不是平均铺开所有内容。",
                    "明天根据新记录重新生成计划，对仍高风险的项目继续保留。",
                ],
                "short": [
                    "今日计划：回忆与重建\n"
                    "回忆自测：主攻 计算机科学 / 图，先不看资料，用 8-10 分钟写出核心定义、公式、算法流程或解题框架。\n"
                    "查漏补缺：对照资料补齐遗漏点 30-45 分钟，重点标出适用条件、边界条件和常见误判。\n"
                    "方法整理：把今天最容易错的 2 个判断点写成“如果……则……”形式。\n"
                    "当天作业：参考难度 60/100；题库模板 TEST-ACTIVE；题量 1 题。\n"
                    "完成标准：参考难度 60/100；能口述核心概念，并完成一次 10 分钟无资料回忆。\n"
                    "复盘记录：记录回忆是否卡住、补了哪些点、作业正确率和主要错因。\n"
                    "复盘记录：记录回忆是否卡住、作业完成情况；具体正确率与错因通过新增记录上传。\n"
                    "调整依据：明天根据上传记录重新生成计划；若表现稳定则推进，否则继续保留该题型。"
                ],
                "diagnostic": [
                    "每道错题记录：题目 / 是否独立完成 / 卡住位置 / 错因类型 / 下次复习触发条件。",
                    "错因类型建议：概念遗忘、题面建模失败、公式适用条件误判、计算错误、边界条件遗漏、时间管理问题。",
                    "如果同一错因连续出现 2 次，把它升级为下一轮计划的主攻点。",
                ],
                "record_template": [
                    "记录：复习【知识点】，完成【题目/数量】，正确率【x%】，错因是【...】，是否独立完成【是/否】。",
                    "记录：做了【题号范围】，错了【题号】，其余全对；主要问题是【...】。",
                    "记录：完成第 N 天计划，【达成/未达成】完成标准，下一步需要补【...】。",
                ],
                "expected": [
                    "若今天完成计划，相关知识点的遗忘风险应下降，复习优先级会后移。",
                    "若题目级正确率较高，BKT 掌握概率会上升；若仍低，会自动保留为明日重点。",
                    "三天窗口分数继续作为近期表现指标，但不再决定计划必须持续三天。",
                ],
                "evidence": [
                    "计划范围：全部活动学科。",
                    "三天窗口：2026-07-15 至 2026-07-17，周期标杆 55 分。",
                    "当前范围内待办 1 项，遗忘风险 0 项，BKT 预警 0 项。",
                    "掌握度口径：计划优先使用已学范围掌握度，整门课总掌握度仅作背景。",
                    "BKT证据口径：题目级匹配已启用同义词、记录级兜底和重复证据去重。",
                    "计算机科学：已学掌握度 70/100，覆盖 5/10 个知识点；整门课总掌握度 75/100。",
                ],
            },
        )
        self.assertEqual(
            list(plan),
            ["judgement", "goals", "short", "diagnostic", "record_template", "expected", "evidence"],
        )

    def test_llm_payload_golden(self) -> None:
        from study_app.core.local_study_plan import study_plan_llm_payload

        self.assertEqual(
            study_plan_llm_payload(self.state()),
            {
                "subject_scope": "全部活动学科",
                "allowed_subjects": ["计算机科学"],
                "study_phase": None,
                "mock_exam_readiness": None,
                "weighted_topic_priorities": [],
                "window": {
                    "start": "2026-07-15",
                    "today": "2026-07-17",
                    "benchmark": 55,
                },
                "subjects": [
                    {
                        "name": "计算机科学",
                        "initial_score": 60,
                        "window_score": 60,
                        "mastery_score": 75,
                        "covered_mastery_score": 70,
                        "covered_topic_count": 5,
                        "total_topic_count": 10,
                        "latest_record": None,
                        "latest_review": None,
                        "warnings": [],
                    }
                ],
                "todos": [
                    {
                        "kind": "做题",
                        "title": "计算机科学 / 图",
                        "detail": "活动待办",
                        "level": "high",
                        "priority": 0.9,
                    }
                ],
                "memory_risks": [],
                "bkt_alerts": [],
                "low_subjects": [],
                "stale_subjects": [],
            },
        )

    def test_archived_subject_is_filtered_from_plan_and_payload(self) -> None:
        from study_app.core.local_study_plan import generate_study_plan, study_plan_llm_payload

        archived = SubjectSummary(
            name="高等数学",
            initial_score=60,
            window_score=40,
            has_window_records=True,
            mastery_score=70,
            covered_mastery_score=65,
            covered_topic_count=10,
            total_topic_count=20,
            latest_record=None,
            latest_review=None,
            warnings=("低于标杆",),
            archived=True,
        )
        state = self.state()
        mixed = DashboardState(
            start=state.start,
            today=state.today,
            benchmark=state.benchmark,
            subjects=(archived, *state.subjects),
            todos=(TodoItem("做题", "高等数学 / 级数", "封存待办", "high", 1.0), *state.todos),
            memory_risks=(
                {"subject": "高等数学", "module": "级数", "topic": "级数"},
            ),
            bkt_alerts=(),
        )
        assignment = SimpleNamespace(
            difficulty_score=60,
            to_homework_text=lambda: "参考难度 60/100；题库模板 TEST-ACTIVE；题量 1 题。",
        )
        with patch(
            "study_app.core.study_plan_homework.generate_practice_assignment",
            return_value=assignment,
        ):
            serialized = json.dumps(
                {
                    "plan": generate_study_plan(mixed),
                    "payload": study_plan_llm_payload(mixed),
                },
                ensure_ascii=False,
                default=str,
            )

        self.assertNotIn("高等数学", serialized)
        self.assertIn("计算机科学", serialized)

    def test_facade_reexports_canonical_local_plan_functions_by_identity(self) -> None:
        from study_app.core import local_study_plan
        from study_app.ui import main_window

        names = (
            "_balanced_homework_topic",
            "apply_dynamic_difficulty_to_plan",
            "study_plan_llm_payload",
            "shrink_risk_item",
            "generate_study_plan",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(main_window, name), getattr(local_study_plan, name))


if __name__ == "__main__":
    unittest.main()
