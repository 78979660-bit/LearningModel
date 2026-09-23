from __future__ import annotations

import unittest
import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from study_app.core.dashboard import DashboardState, SubjectSummary, TodoItem


class ActiveSubjectFilteringTests(unittest.TestCase):
    @staticmethod
    def mixed_state() -> DashboardState:
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
        active = SubjectSummary(
            name="计算机科学",
            initial_score=60,
            window_score=50,
            has_window_records=True,
            mastery_score=75,
            covered_mastery_score=70,
            covered_topic_count=5,
            total_topic_count=10,
            latest_record=None,
            latest_review=None,
            warnings=(),
            archived=False,
        )
        return DashboardState(
            start=date(2026, 7, 15),
            today=date(2026, 7, 17),
            benchmark=55,
            subjects=(archived, active),
            todos=(
                TodoItem("做题", "高等数学 / 级数", "封存待办", "high", 1.0),
                TodoItem("做题", "计算机科学 / 图", "活动待办", "high", 0.9),
            ),
            memory_risks=(
                {"subject": "高等数学", "module": "级数", "topic": "级数", "recall": 0.2, "target_recall": 0.8},
                {"subject": "计算机科学", "module": "算法", "topic": "图", "recall": 0.3, "target_recall": 0.8},
            ),
            bkt_alerts=(
                {"subject": "高等数学", "module": "级数", "topic": "级数", "mastery_probability": 0.2, "target_mastery": 0.8},
                {"subject": "计算机科学", "module": "算法", "topic": "图", "mastery_probability": 0.3, "target_mastery": 0.8},
            ),
        )

    def test_active_subjects_exclude_archived_summaries(self) -> None:
        from study_app.core.active_subjects import active_subjects

        subjects = (
            SimpleNamespace(name="高等数学", archived=True),
            SimpleNamespace(name="计算机科学", archived=False),
        )

        self.assertEqual(
            [subject.name for subject in active_subjects(subjects)],
            ["计算机科学"],
        )

    def test_activity_ui_subject_names_only_include_active_subjects(self) -> None:
        from study_app.ui.main_window import activity_subject_names

        state = SimpleNamespace(
            subjects=(
                SimpleNamespace(name="高等数学", archived=True),
                SimpleNamespace(name="大学物理学", archived=True),
                SimpleNamespace(name="化学原理", archived=True),
                SimpleNamespace(name="数据结构与算法基础", archived=True),
                SimpleNamespace(name="计算机科学", archived=False),
            )
        )

        self.assertEqual(activity_subject_names(state), ("计算机科学",))

    def test_plan_scope_options_label_and_values_only_include_active_subjects(self) -> None:
        from study_app.ui.main_window import plan_subject_scope_options

        self.assertEqual(
            plan_subject_scope_options(self.mixed_state()),
            (("全部活动学科", ""), ("计算机科学", "计算机科学")),
        )

    def test_final_review_candidates_only_include_active_subjects(self) -> None:
        from study_app.ui.main_window import final_review_subject_names

        self.assertEqual(final_review_subject_names(self.mixed_state()), ("计算机科学",))

    def test_record_subject_candidates_only_include_active_subjects(self) -> None:
        from study_app.ui.main_window import record_subject_names

        self.assertEqual(record_subject_names(self.mixed_state()), ("计算机科学",))

    def test_homepage_activity_items_and_counts_only_use_active_subjects(self) -> None:
        from study_app.ui.main_window import filter_homepage_activity

        state = self.mixed_state()
        subjects = (
            replace(state.subjects[0], warnings=("低于标杆", "8 天未课外复习")),
            replace(state.subjects[1], warnings=("7 天未课外复习",)),
        )
        generic_todo = TodoItem("记录", "整理今日学习材料", "通用待办", "none", 0.1)
        state = replace(state, subjects=subjects, todos=(*state.todos, generic_todo))

        filtered = filter_homepage_activity(state)

        self.assertEqual(
            [item.title for item in filtered.todos],
            ["计算机科学 / 图", "整理今日学习材料"],
        )
        self.assertEqual([item["subject"] for item in filtered.memory_risks], ["计算机科学"])
        self.assertEqual([item["subject"] for item in filtered.bkt_alerts], ["计算机科学"])
        self.assertEqual(filtered.low_subjects, ("计算机科学",))
        self.assertEqual([item.name for item in filtered.stale_subjects], ["计算机科学"])
        self.assertEqual(
            filtered.counts,
            {"todos": 2, "memory_risks": 1, "bkt_alerts": 1, "low_subjects": 1},
        )
        homepage_text = json.dumps(
            {
                "todos": [item.title for item in filtered.todos],
                "memory": filtered.memory_risks,
                "bkt": filtered.bkt_alerts,
                "low": filtered.low_subjects,
                "stale": [item.name for item in filtered.stale_subjects],
            },
            ensure_ascii=False,
        )
        self.assertNotIn("高等数学", homepage_text)
        self.assertIn("计算机科学", homepage_text)

    def test_history_record_rows_keep_archived_and_active_subjects(self) -> None:
        from study_app.ui.main_window import recent_record_display_lines

        records = [
            {
                "record_date": "2026-07-15",
                "subject_name": "高等数学",
                "topic_name": "级数",
                "score": 70,
                "problem_count": 2,
                "attachments": [],
            },
            {
                "record_date": "2026-07-17",
                "subject_name": "计算机科学",
                "topic_name": "图",
                "score": 80,
                "problem_count": 3,
                "attachments": [],
            },
        ]

        lines = recent_record_display_lines(records)

        self.assertEqual(len(lines), 2)
        self.assertIn("高等数学", lines[0])
        self.assertIn("计算机科学", lines[1])

    def test_empty_scope_payload_and_plan_use_all_active_subjects_label(self) -> None:
        from study_app.ui.main_window import generate_study_plan, study_plan_llm_payload

        fake_assignment = SimpleNamespace(
            difficulty_score=60,
            to_homework_text=lambda: "参考难度 60/100；题库模板 TEST-ACTIVE；题量 1 题。",
        )
        with patch("study_app.core.study_plan_homework.generate_practice_assignment", return_value=fake_assignment):
            plan = generate_study_plan(self.mixed_state())
        payload = study_plan_llm_payload(self.mixed_state())

        serialized = json.dumps({"payload": payload, "plan": plan}, ensure_ascii=False, default=str)
        self.assertIn("全部活动学科", serialized)
        self.assertNotIn("全部学科", serialized)

    def test_all_subject_plan_payload_excludes_archived_subject_inputs(self) -> None:
        from study_app.ui.main_window import study_plan_llm_payload

        payload = study_plan_llm_payload(self.mixed_state())

        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        self.assertNotIn("高等数学", serialized)
        self.assertIn("计算机科学", serialized)

    def test_plan_payload_exposes_allowed_active_subject_names(self) -> None:
        from study_app.ui.main_window import study_plan_llm_payload

        payload = study_plan_llm_payload(self.mixed_state())

        self.assertEqual(payload["allowed_subjects"], ["计算机科学"])

    def test_all_subject_local_plan_excludes_archived_subject_inputs(self) -> None:
        from study_app.ui.main_window import generate_study_plan

        fake_assignment = SimpleNamespace(
            difficulty_score=60,
            to_homework_text=lambda: "参考难度 60/100；题库模板 TEST-ACTIVE；题量 1 题。",
        )
        with patch("study_app.core.study_plan_homework.generate_practice_assignment", return_value=fake_assignment):
            plan = generate_study_plan(self.mixed_state())

        serialized = json.dumps(plan, ensure_ascii=False, default=str)
        self.assertNotIn("高等数学", serialized)
        self.assertIn("计算机科学", serialized)

    def test_archived_plan_request_is_rejected_before_database_phase_lookup(self) -> None:
        from study_app.ui.main_window import generate_study_plan_with_optional_llm

        with patch("study_app.ai.providers.is_llm_feature_enabled") as provider_enabled:
            with self.assertRaisesRegex(ValueError, "已封存"):
                generate_study_plan_with_optional_llm(self.mixed_state(), "高等数学")
        provider_enabled.assert_not_called()

    def test_current_summary_payload_excludes_archived_subject_history(self) -> None:
        from study_app.ai.daily_summary import build_daily_summary_payload

        records = [
            {"date": "2026-07-17", "subject": "高等数学", "topic": "级数"},
            {"date": "2026-07-17", "subject": "计算机科学", "topic": "图"},
        ]

        payload = build_daily_summary_payload(self.mixed_state(), records)

        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        self.assertNotIn("高等数学", serialized)
        self.assertIn("计算机科学", serialized)

    def test_current_plan_signature_ignores_archived_mastery_changes(self) -> None:
        from study_app.ui.main_window import current_study_plan_signature

        state = self.mixed_state()
        changed_archived = replace(state.subjects[0], mastery_score=99)
        changed_state = replace(state, subjects=(changed_archived, state.subjects[1]))

        self.assertEqual(
            current_study_plan_signature(state, None),
            current_study_plan_signature(changed_state, None),
        )

    def test_current_summary_signature_ignores_archived_history_changes(self) -> None:
        from study_app.ui.main_window import daily_summary_cache_signature

        state = self.mixed_state()
        active_record = {"id": 1, "date": "2026-07-17", "subject": "计算机科学", "topic": "图"}
        archived_record = {"id": 2, "date": "2026-07-17", "subject": "高等数学", "topic": "级数"}
        changed_archived = replace(state.subjects[0], mastery_score=99)
        changed_state = replace(state, subjects=(changed_archived, state.subjects[1]))

        self.assertEqual(
            daily_summary_cache_signature(state, [active_record]),
            daily_summary_cache_signature(changed_state, [active_record, archived_record]),
        )

    def test_archived_mock_exam_is_rejected_before_phase_lookup(self) -> None:
        from study_app.ui.main_window import build_mock_exam_generation_prompt

        with patch(
            "study_app.core.study_phase.get_subject_phase",
            side_effect=AssertionError("phase lookup must not run"),
        ):
            with self.assertRaisesRegex(ValueError, "已封存"):
                build_mock_exam_generation_prompt(self.mixed_state(), "高等数学")

    def test_archived_practice_prompt_is_rejected_without_database_lookup(self) -> None:
        from study_app.ui.main_window import build_practice_generation_prompt

        line = "高等数学 / 级数\n当天作业：参考难度 60/100；题库模板 CALC-SERIES；题量 2 题。"
        with patch("study_app.core.practice_prompts.recent_practice_context") as recent_context:
            with self.assertRaisesRegex(ValueError, "已封存"):
                build_practice_generation_prompt(line, self.mixed_state(), "高等数学")
        recent_context.assert_not_called()

    def test_archived_oj_list_is_rejected_before_problem_lookup(self) -> None:
        from study_app.ui.main_window import build_oj_practice_list

        line = "高等数学 / 级数\n当天作业：参考难度 60/100；题库模板 CS-OJ-PRACTICE；题量 2 题。"
        with patch("study_app.data.practice_repository.find_practice_problems") as find_problems:
            with self.assertRaisesRegex(ValueError, "已封存"):
                build_oj_practice_list(line, self.mixed_state(), "高等数学")
        find_problems.assert_not_called()


if __name__ == "__main__":
    unittest.main()
