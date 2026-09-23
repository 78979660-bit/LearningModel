from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date

from study_app.core.dashboard import DashboardState, SubjectSummary, TodoItem
from study_app.core.plan_candidates import (
    SOURCE_KIND,
    build_plan_candidates,
    identify_model_topic,
    task_id_for_source,
)


class CandidateIdentityTests(unittest.TestCase):
    @staticmethod
    def model() -> dict:
        return {
            "subjects": [
                {"name": "计算机科学", "modules": [
                    {"name": "算法", "topics": [{"name": "图"}]},
                    {"name": "数据结构", "topics": [{"name": "图"}]},
                ]},
                {"name": "高等数学", "modules": [
                    {"name": "级数", "topics": [{"name": "收敛"}]},
                ]},
            ]
        }

    @staticmethod
    def subject(name: str, *, archived: bool = False) -> SubjectSummary:
        return SubjectSummary(
            name=name, initial_score=60, window_score=60, has_window_records=True,
            mastery_score=60, covered_mastery_score=60, covered_topic_count=1,
            total_topic_count=1, latest_record=None, latest_review=None,
            warnings=(), archived=archived,
        )

    @classmethod
    def state(cls, todos: tuple[TodoItem, ...]) -> DashboardState:
        return DashboardState(
            start=date(2026, 9, 15), today=date(2026, 9, 16), benchmark=55,
            subjects=(cls.subject("计算机科学"), cls.subject("高等数学", archived=True)),
            todos=todos, memory_risks=(), bkt_alerts=(), model_data=cls.model(),
        )

    @staticmethod
    def todo(source, title: str = "计算机科学 / 图", priority: float = 0.8) -> TodoItem:
        return TodoItem(
            kind="做题", title=title, detail="掌握缺口", level="high", priority=priority,
            subject_id=source.subject_id, source_kind=source.source_kind,
            source_id=source.source_id, task_id=source.task_id,
        )

    def test_same_model_source_has_same_id_across_views_and_display_edits(self) -> None:
        source = identify_model_topic(self.model(), "计算机科学", "算法", "图")
        self.assertIsNotNone(source)
        first = self.todo(source)
        edited = replace(first, title="图论专项", detail="新说明", kind="诊断", priority=0.9)
        unknown = TodoItem("做题", "旧文本项", "无模型对象", "low", 0.1)

        all_candidates = build_plan_candidates(self.state((unknown, first, edited)))
        scoped_candidates = build_plan_candidates(self.state((edited, first, unknown)), "计算机科学")
        self.assertEqual([item.task_id for item in all_candidates.candidates], [source.task_id])
        self.assertEqual([item.task_id for item in scoped_candidates.candidates], [source.task_id])
        self.assertEqual(scoped_candidates.candidates[0].title, "图论专项")
        self.assertEqual(all_candidates.unmapped[0].reason, "missing_stable_source")
        self.assertIsNone(scoped_candidates.candidates[0].estimated_minutes)

    def test_same_topic_name_in_other_module_has_distinct_source_id(self) -> None:
        first = identify_model_topic(self.model(), "计算机科学", "算法", "图")
        second = identify_model_topic(self.model(), "计算机科学", "数据结构", "图")
        self.assertNotEqual(first.source_id, second.source_id)
        self.assertNotEqual(first.task_id, second.task_id)
        self.assertEqual(first.source_method, "model_topic_path")

    def test_explicit_model_id_survives_topic_display_rename(self) -> None:
        first_model = {"subjects": [{"name": "计算机科学", "modules": [
            {"name": "算法", "topics": [{"id": "topic-42", "name": "图"}]},
        ]}]}
        second_model = {"subjects": [{"name": "计算机科学", "modules": [
            {"name": "算法", "topics": [{"id": "topic-42", "name": "图算法"}]},
        ]}]}
        first = identify_model_topic(first_model, "计算机科学", "算法", "图")
        second = identify_model_topic(second_model, "计算机科学", "算法", "图算法")
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(first.source_method, "explicit_model_topic_id")

    def test_unknown_model_object_cannot_be_identified(self) -> None:
        self.assertIsNone(identify_model_topic(self.model(), "计算机科学", "算法", "不存在"))
        self.assertIsNone(identify_model_topic(self.model(), "不存在", "算法", "图"))

    def test_duplicate_explicit_source_id_is_unmapped(self) -> None:
        model = {"subjects": [{"name": "计算机科学", "modules": [
            {"name": "算法", "topics": [{"id": "duplicate", "name": "图"}]},
            {"name": "数据结构", "topics": [{"id": "duplicate", "name": "栈"}]},
        ]}]}
        self.assertIsNone(identify_model_topic(model, "计算机科学", "算法", "图"))

    def test_archived_source_and_invalid_identity_do_not_become_candidates(self) -> None:
        archived_source = identify_model_topic(self.model(), "高等数学", "级数", "收敛")
        valid_source = identify_model_topic(self.model(), "计算机科学", "算法", "图")
        tampered = replace(self.todo(valid_source), task_id="task:v1:wrong")
        collection = build_plan_candidates(self.state((self.todo(archived_source), tampered)))
        self.assertEqual(collection.candidates, ())
        self.assertEqual(
            [item.reason for item in collection.unmapped],
            ["subject_archived_or_unknown", "invalid_source_identity"],
        )
        with self.assertRaises(ValueError):
            build_plan_candidates(self.state((self.todo(archived_source),)), "高等数学")

    def test_task_identity_requires_structured_source_not_title(self) -> None:
        source = identify_model_topic(self.model(), "计算机科学", "算法", "图")
        self.assertEqual(source.task_id, task_id_for_source("计算机科学", SOURCE_KIND, source.source_id))
        for subject_id, source_kind, source_id in (("", SOURCE_KIND, source.source_id),
                                                    ("计算机科学", "", source.source_id),
                                                    ("计算机科学", SOURCE_KIND, "")):
            with self.subTest(subject_id=subject_id, source_kind=source_kind), self.assertRaises(ValueError):
                task_id_for_source(subject_id, source_kind, source_id)


if __name__ == "__main__":
    unittest.main()
