from __future__ import annotations

import unittest
from types import SimpleNamespace


class SubjectReferencePolicyTests(unittest.TestCase):
    @staticmethod
    def subjects() -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(name="高等数学", archived=True),
            SimpleNamespace(name="大学物理学", archived=True),
            SimpleNamespace(name="化学原理", archived=True),
            SimpleNamespace(name="数据结构与算法基础", archived=True),
            SimpleNamespace(name="计算机科学", archived=False),
        )

    def test_active_and_archived_canonical_names_come_from_dashboard_subjects(self) -> None:
        from study_app.core.active_subjects import active_subject_names, archived_subject_names

        self.assertEqual(active_subject_names(self.subjects()), ("计算机科学",))
        self.assertEqual(
            archived_subject_names(self.subjects()),
            ("高等数学", "大学物理学", "化学原理", "数据结构与算法基础"),
        )

    def test_archived_course_aliases_are_rejected_by_shared_policy(self) -> None:
        from study_app.core.active_subjects import subject_reference_violations

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
                self.assertTrue(
                    subject_reference_violations(
                        f"今天安排{alias}复习",
                        allowed_subjects=("计算机科学",),
                        archived_subjects=("高等数学", "大学物理学", "化学原理", "数据结构与算法基础"),
                    )
                )

    def test_general_computer_science_terms_remain_legal(self) -> None:
        from study_app.core.active_subjects import subject_reference_violations

        legal_texts = (
            "提高数据结构与算法能力",
            "复习数据结构、算法基础、算法与树",
            "完成树和图的算法练习",
        )
        for text in legal_texts:
            with self.subTest(text=text):
                self.assertEqual(
                    subject_reference_violations(
                        text,
                        allowed_subjects=("计算机科学",),
                        archived_subjects=("高等数学", "大学物理学", "化学原理", "数据结构与算法基础"),
                    ),
                    (),
                )


if __name__ == "__main__":
    unittest.main()
