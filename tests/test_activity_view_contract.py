from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


class ActivityViewContractTests(unittest.TestCase):
    @staticmethod
    def state() -> SimpleNamespace:
        archived = SimpleNamespace(name="高等数学", archived=True)
        active = SimpleNamespace(name="计算机科学", archived=False)
        return SimpleNamespace(
            subjects=(archived, active),
            todos=(
                SimpleNamespace(title="高等数学 / 级数"),
                SimpleNamespace(title="计算机科学 / 图"),
                SimpleNamespace(title="整理学习材料"),
            ),
            memory_risks=(
                {"subject": "高等数学"},
                {"subject": "计算机科学"},
            ),
            bkt_alerts=(
                {"subject": "高等数学"},
                {"subject": "计算机科学"},
            ),
            low_subjects=("高等数学", "计算机科学"),
            stale_subjects=(archived, active),
        )

    def test_activity_module_golden_behavior(self) -> None:
        from study_app.ui.activity_view import (
            ALL_ACTIVE_SUBJECTS_LABEL,
            activity_subject_names,
            filter_homepage_activity,
            plan_subject_scope_label,
            plan_subject_scope_options,
        )

        state = self.state()
        filtered = filter_homepage_activity(state)

        self.assertEqual(ALL_ACTIVE_SUBJECTS_LABEL, "全部活动学科")
        self.assertEqual(activity_subject_names(state), ("计算机科学",))
        self.assertEqual(
            plan_subject_scope_options(state),
            (("全部活动学科", ""), ("计算机科学", "计算机科学")),
        )
        self.assertEqual(plan_subject_scope_label(None), "全部活动学科")
        self.assertEqual(plan_subject_scope_label("计算机科学"), "计算机科学")
        self.assertEqual(
            tuple(item.title for item in filtered.todos),
            ("计算机科学 / 图", "整理学习材料"),
        )
        self.assertEqual(filtered.memory_risks, ({"subject": "计算机科学"},))
        self.assertEqual(filtered.bkt_alerts, ({"subject": "计算机科学"},))
        self.assertEqual(filtered.low_subjects, ("计算机科学",))
        self.assertEqual(tuple(item.name for item in filtered.stale_subjects), ("计算机科学",))
        self.assertEqual(
            filtered.counts,
            {"todos": 2, "memory_risks": 1, "bkt_alerts": 1, "low_subjects": 1},
        )

    def test_main_window_is_an_identity_facade_for_activity_symbols(self) -> None:
        from study_app.core import active_subjects
        from study_app.ui import activity_view, main_window

        activity_names = (
            "HomepageActivity",
            "activity_subjects",
            "activity_subject_names",
            "filter_homepage_activity",
            "record_subject_names",
            "final_review_subject_names",
            "plan_subject_scope_options",
            "plan_subject_scope_label",
        )
        self.assertIs(
            main_window.ALL_ACTIVE_SUBJECTS_LABEL,
            activity_view.ALL_ACTIVE_SUBJECTS_LABEL,
        )
        for name in activity_names:
            with self.subTest(name=name):
                self.assertIs(getattr(main_window, name), getattr(activity_view, name))
        self.assertIs(
            main_window.require_activity_subject,
            active_subjects.require_activity_subject,
        )

    def test_activity_view_reexports_core_dashboard_adapters_by_identity(self) -> None:
        from study_app.core import active_subjects
        from study_app.ui import activity_view, main_window

        self.assertIs(
            activity_view.activity_subjects,
            active_subjects.dashboard_activity_subjects,
        )
        self.assertIs(
            activity_view.activity_subject_names,
            active_subjects.dashboard_activity_subject_names,
        )
        self.assertIs(
            activity_view.plan_subject_scope_label,
            active_subjects.dashboard_plan_subject_scope_label,
        )
        state = self.state()
        self.assertEqual(
            active_subjects.dashboard_activity_subjects(state),
            (state.subjects[1],),
        )
        self.assertEqual(
            active_subjects.dashboard_activity_subject_names(state),
            ("计算机科学",),
        )
        self.assertEqual(
            active_subjects.dashboard_plan_subject_scope_label(state, None),
            "全部活动学科",
        )
        self.assertEqual(
            active_subjects.dashboard_plan_subject_scope_label(state, "计算机科学"),
            "计算机科学",
        )
        self.assertEqual(
            main_window.plan_subject_scope_label(subject_name="计算机科学"),
            "计算机科学",
        )
        self.assertEqual(
            activity_view.plan_subject_scope_label(subject_name="计算机科学"),
            "计算机科学",
        )
        self.assertEqual(
            active_subjects.dashboard_plan_subject_scope_label(
                subject_name="计算机科学"
            ),
            "计算机科学",
        )
        self.assertEqual(main_window.plan_subject_scope_label(), "全部活动学科")
        self.assertEqual(activity_view.plan_subject_scope_label(), "全部活动学科")
        self.assertEqual(
            active_subjects.dashboard_plan_subject_scope_label(),
            "全部活动学科",
        )

    def test_core_and_ai_plan_modules_have_no_ui_dependency(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "study_app/core/local_study_plan.py",
            "study_app/ai/study_plan_service.py",
        ):
            with self.subTest(relative_path=relative_path):
                text = (root / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("study_app.ui", text)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import study_app.core.local_study_plan; "
                    "import study_app.ai.study_plan_service; "
                    "assert not any(name == 'study_app.ui' or name.startswith('study_app.ui.') "
                    "for name in sys.modules)"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_importing_activity_view_loads_neither_qt_nor_main_window(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import study_app.ui.activity_view; "
                    "assert 'study_app.ui.main_window' not in sys.modules; "
                    "assert not any(name == 'PySide6' or name.startswith('PySide6.') "
                    "for name in sys.modules)"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
