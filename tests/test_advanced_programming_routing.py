from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from study_app.core.active_subjects import (
    SUBJECT_COURSE_ALIASES,
    subject_reference_violations,
)
from study_app.core.practice_bank import known_template_ids, template_for_topic
from study_app.core.study_plan_items import infer_subject_topic_from_plan_line
from study_app.data.practice_repository import TEMPLATE_SUBJECT_HINTS


def _assert_advanced_programming_aliases_are_specific_to_the_course() -> None:
    aliases = SUBJECT_COURSE_ALIASES["高级程序设计"]

    assert "C++程序设计" in aliases
    assert "C++面向对象程序设计" in aliases
    assert "C++" not in aliases
    assert subject_reference_violations(
        "阅读 C++ Primer 第五版",
        allowed_subjects=("离散数学",),
        archived_subjects=("高级程序设计",),
    ) == ()
    assert subject_reference_violations(
        "复习 C++程序设计课程",
        allowed_subjects=("离散数学",),
        archived_subjects=("高级程序设计",),
    ) == ("高级程序设计（C++程序设计）",)


class AdvancedProgrammingOpenRoutingTests(unittest.TestCase):
    def test_every_advanced_programming_topic_uses_open_practice_template(self) -> None:
        topics = (
            "类与对象",
            "单调栈",
            "MyStack",
            "二叉搜索树",
            "MiniTorch",
            "复杂度分析",
            "未来新增的跨模块综合实验",
        )
        for topic in topics:
            with self.subTest(topic=topic):
                template_id, description = template_for_topic(topic, "高级程序设计")
                self.assertEqual(template_id, "CPP-OOP-PRACTICE")
                self.assertIn("随本次实验或作业变化", description)


def _assert_embedded_subject_also_routes_before_algorithm_keywords() -> None:
    assert template_for_topic("高级程序设计 / 二叉搜索树")[0] == "CPP-OOP-PRACTICE"
    assert template_for_topic("高级程序设计 / DS-BST-OPS")[0] == "CPP-OOP-PRACTICE"


def _assert_open_practice_template_is_registered_with_subject_hint() -> None:
    assert "CPP-OOP-PRACTICE" in known_template_ids()
    assert TEMPLATE_SUBJECT_HINTS["CPP-OOP-PRACTICE"] == (
        "高级程序设计",
        "面向对象综合实践",
    )


def _assert_plan_item_prefers_explicit_or_selected_advanced_programming_subject() -> None:
    assert infer_subject_topic_from_plan_line(
        "任务：高级程序设计 / 类与对象；完成上机与代码实现"
    )[0] == "高级程序设计"
    assert infer_subject_topic_from_plan_line(
        "任务：完成上机、代码实现与调试",
        selected_subject="高级程序设计",
    )[0] == "高级程序设计"


def _assert_local_plan_recognizes_advanced_programming_without_oj_special_case() -> None:
    from study_app.core.local_study_plan import apply_dynamic_difficulty_to_plan

    assignment = SimpleNamespace(
        difficulty_score=60,
        to_homework_text=lambda: (
            "参考难度 60/100（中等）；题库模板 CPP-OOP-PRACTICE："
            "围绕当前理论主题开展开放式 C++ 实践；生成/选做 2 题。"
        ),
    )
    plan = {
        "short": [
            "高级程序设计；任务：围绕类与对象完成上机与代码实现。\n"
            "当天作业：参考难度 60/100；题库模板 GEN-MIXED-PRACTICE；生成/选做 2 题。"
        ]
    }

    with (
        patch(
            "study_app.core.local_study_plan.generate_practice_assignment",
            return_value=assignment,
        ) as generate_assignment,
        patch("study_app.core.study_phase.is_final_review", return_value=False),
        patch(
            "study_app.core.local_study_plan.first_diagnostic_difficulty_cap",
            return_value=None,
        ),
    ):
        updated = apply_dynamic_difficulty_to_plan(
            plan,
            as_of_date=date(2026, 9, 22),
        )

    assert generate_assignment.call_args.args[0].startswith("高级程序设计 / ")
    assert "CPP-OOP-PRACTICE" in updated["short"][0]


class AdvancedProgrammingIntegrationTests(unittest.TestCase):
    def test_aliases_are_specific_to_the_course(self) -> None:
        _assert_advanced_programming_aliases_are_specific_to_the_course()

    def test_embedded_subject_routes_before_algorithm_keywords(self) -> None:
        _assert_embedded_subject_also_routes_before_algorithm_keywords()

    def test_open_template_is_registered_with_subject_hint(self) -> None:
        _assert_open_practice_template_is_registered_with_subject_hint()

    def test_plan_item_prefers_explicit_or_selected_subject(self) -> None:
        _assert_plan_item_prefers_explicit_or_selected_advanced_programming_subject()

    def test_local_plan_recognizes_subject_without_oj_special_case(self) -> None:
        _assert_local_plan_recognizes_advanced_programming_without_oj_special_case()
