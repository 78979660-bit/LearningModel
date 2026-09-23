from __future__ import annotations

import os
import unittest
from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QSystemTrayIcon, QWidget

from study_app.core.dashboard import DashboardState
from study_app.ui import window_shell


class OJNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def state() -> DashboardState:
        return DashboardState(
            date(2026, 9, 15), date(2026, 9, 17), 60, (), (), (), (), (), (), {}
        )

    def build_window(self):
        activity = SimpleNamespace(
            counts={"todos": 0, "memory_risks": 0, "bkt_alerts": 0, "low_subjects": 0},
            todos=[],
            memory_risks=[],
            bkt_alerts=[],
        )
        stack = ExitStack()
        stack.enter_context(
            patch.object(window_shell, "load_llm_settings", return_value=SimpleNamespace(enabled=False))
        )
        stack.enter_context(patch.object(window_shell, "filter_homepage_activity", return_value=activity))
        for name in ("metric_card", "subject_card", "daily_summary_card", "todo_card", "risk_card"):
            stack.enter_context(patch.object(window_shell, name, side_effect=lambda *_args: QWidget()))
        for name in ("study_plan_page", "query_page", "knowledge_page", "settings_page"):
            stack.enter_context(patch.object(window_shell, name, side_effect=lambda *_args: QWidget()))
        stack.enter_context(patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False))
        window = window_shell.MainWindow(self.state())
        return window, stack

    def test_oj_is_not_mounted_as_a_user_facing_page(self) -> None:
        window, stack = self.build_window()
        with stack:
            self.assertEqual(window.stack.count(), 5)
            nav = [
                button.text()
                for button in window.findChildren(QPushButton)
                if button.objectName() == "NavButton"
            ]
            self.assertEqual(nav, ["主页", "学习计划", "学习助理", "知识图谱", "设置"])
            self.assertNotIn("OJ 闭环", nav)
            self.assertFalse(hasattr(window, "PAGE_OJ"))
        window.deleteLater()

    def test_remaining_navigation_stays_contiguous_after_oj_removal(self) -> None:
        window, stack = self.build_window()
        with stack:
            for expected_index in range(window.stack.count()):
                window.switch_page(expected_index)
                self.assertEqual(window.stack.currentIndex(), expected_index)
                self.assertEqual(window.current_page_index, expected_index)
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
