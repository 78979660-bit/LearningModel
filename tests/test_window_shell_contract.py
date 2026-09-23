from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class WindowShellContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def state():
        from study_app.core.dashboard import DashboardState

        return DashboardState(date(2026, 7, 15), date(2026, 7, 17), 60, (), (), (), ())

    @staticmethod
    def llm_settings(**changes):
        from study_app.ai.providers import LLMSettings

        values = {
            "enabled": False,
            "provider": "openai",
            "model": "model-x",
            "custom_base_url": "",
            "api_key": "runtime-key",
            "single_call_token_limit": 8000,
            "daily_budget_cny": 3.0,
            "allow_upload_images": False,
            "allow_upload_pdfs": False,
            "enabled_features": ("daily_summary",),
        }
        values.update(changes)
        return LLMSettings(**values)

    def test_module_is_lazy_and_main_window_is_identity_facade(self) -> None:
        code = (
            "import sys; from study_app.ui import window_shell; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'study_app.ui.main_window' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        from study_app.ui import main_window, window_shell

        self.assertIs(main_window.MainWindow, window_shell.MainWindow)
        self.assertIs(main_window.FloatingIcon, window_shell.FloatingIcon)

    def build_window(self):
        from PySide6.QtWidgets import QSystemTrayIcon, QWidget
        from study_app.ui import window_shell

        activity = SimpleNamespace(
            counts={"todos": 0, "memory_risks": 0, "bkt_alerts": 0, "low_subjects": 0},
            todos=[],
            memory_risks=[],
            bkt_alerts=[],
        )
        widget_names = ("metric_card", "subject_card", "daily_summary_card", "todo_card", "risk_card")
        page_names = ("study_plan_page", "query_page", "knowledge_page", "settings_page")
        stack = ExitStack()
        stack.enter_context(patch.object(window_shell, "load_llm_settings", return_value=self.llm_settings()))
        stack.enter_context(patch.object(window_shell, "filter_homepage_activity", return_value=activity))
        for name in widget_names + page_names:
            stack.enter_context(patch.object(window_shell, name, side_effect=lambda *args, **kwargs: QWidget()))
        stack.enter_context(patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False))
        window = window_shell.MainWindow(self.state())
        return window, stack

    def test_window_builds_five_user_facing_pages_without_admin_surfaces(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.ui import window_shell

        window, stack = self.build_window()
        with stack:
            self.assertEqual(window.windowTitle(), "学习模型桌面应用")
            self.assertEqual(window.stack.count(), 5)
            nav = [button.text() for button in window.findChildren(QPushButton) if button.objectName() == "NavButton"]
            self.assertEqual(nav, ["主页", "学习计划", "学习助理", "知识图谱", "设置"])
            window.switch_page(window.PAGE_ASSISTANT)
            self.assertEqual(window.stack.currentIndex(), window.PAGE_ASSISTANT)
            refreshed = self.state()
            with patch.object(window_shell, "load_dashboard_state", return_value=refreshed):
                window.refresh_dashboard()
            self.assertIs(window.state, refreshed)
            self.assertEqual(window.current_page_index, window.PAGE_ASSISTANT)
            self.assertEqual(window.stack.currentIndex(), window.PAGE_ASSISTANT)
        window.deleteLater()

    def test_window_respects_minimum_size_during_quick_resize(self) -> None:
        window, stack = self.build_window()
        with stack:
            self.assertEqual((window.width(), window.height()), (1120, 760))
            self.assertEqual((window.minimumWidth(), window.minimumHeight()), (920, 620))
            window.show()
            window.resize(930, 630)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                self.app.processEvents()
                if (window.width(), window.height()) == (930, 630):
                    break
            self.assertEqual((window.width(), window.height()), (930, 630))
            self.assertEqual(window.stack.count(), 5)
            self.assertTrue(window._sidebar_collapsed)
            self.assertEqual(window.sidebar.width(), window.SIDEBAR_COLLAPSED_WIDTH)
        window.deleteLater()

    def test_removed_admin_surfaces_have_no_window_entry_points(self) -> None:
        from PySide6.QtWidgets import QPushButton

        window, stack = self.build_window()
        with stack:
            nav = {
                button.text()
                for button in window.findChildren(QPushButton)
                if button.objectName() == "NavButton"
            }
            self.assertTrue(
                {"新增记录", "学习记录", "预警", "学科生命周期", "OJ 闭环"}.isdisjoint(nav)
            )
            self.assertFalse(hasattr(window, "save_record_from_page"))
            self.assertFalse(hasattr(window, "sync_and_switch_page"))
        window.deleteLater()

    def test_compact_navigation_maps_one_to_one_to_the_five_page_stack(self) -> None:
        from PySide6.QtWidgets import QPushButton

        window, stack = self.build_window()
        with stack:
            page_constants = (
                window.PAGE_HOME,
                window.PAGE_PLAN,
                window.PAGE_ASSISTANT,
                window.PAGE_KNOWLEDGE,
                window.PAGE_SETTINGS,
            )
            self.assertEqual(page_constants, tuple(range(window.stack.count())))
            nav = [
                button
                for button in window.findChildren(QPushButton)
                if button.objectName() == "NavButton"
            ]
            for expected_index, button in enumerate(nav):
                button.click()
                self.assertEqual(window.stack.currentIndex(), expected_index)
        window.deleteLater()

    def test_sidebar_can_collapse_without_rebuilding_or_switching_page(self) -> None:
        from PySide6.QtWidgets import QPushButton

        window, stack = self.build_window()
        with stack:
            window.switch_page(2)
            page = window.stack.currentWidget()
            toggle = window.findChild(QPushButton, "SidebarToggle")
            nav = window.findChildren(QPushButton, "NavButton")

            self.assertIsNotNone(toggle)
            self.assertFalse(window._sidebar_collapsed)
            self.assertEqual(window.sidebar.width(), window.SIDEBAR_EXPANDED_WIDTH)

            toggle.click()
            self.assertTrue(window._sidebar_collapsed)
            self.assertEqual(window.sidebar.width(), window.SIDEBAR_COLLAPSED_WIDTH)
            self.assertEqual(toggle.text(), "")
            self.assertFalse(toggle.icon().isNull())
            self.assertTrue(all(not button.isHidden() for button in nav))
            self.assertEqual([button.text() for button in nav], [""] * 5)
            self.assertTrue(all(not button.icon().isNull() for button in nav))
            self.assertEqual([button.toolTip() for button in nav], ["主页", "学习计划", "学习助理", "知识图谱", "设置"])
            self.assertIs(window.stack.currentWidget(), page)
            self.assertEqual(window.stack.currentIndex(), 2)

            toggle.click()
            self.assertFalse(window._sidebar_collapsed)
            self.assertEqual(window.sidebar.width(), window.SIDEBAR_EXPANDED_WIDTH)
            self.assertEqual(toggle.text(), "")
            self.assertFalse(toggle.icon().isNull())
            self.assertEqual([button.text() for button in nav], ["主页", "学习计划", "学习助理", "知识图谱", "设置"])
            self.assertTrue(all(not button.isHidden() for button in nav))
            self.assertIs(window.stack.currentWidget(), page)
        window.deleteLater()

    def test_subject_catalog_refresh_rebuilds_selectors_without_duplicate_navigation(self):
        from study_app.ui import window_shell
        window, stack = self.build_window()
        with stack, patch.object(window_shell, 'load_dashboard_state', return_value=self.state()):
            window.switch_page(2)
            old_page = window.stack.currentWidget()
            window.set_sidebar_collapsed(True, remember=True)
            window.refresh_subject_catalog()
            self.assertIsNot(window.stack.currentWidget(), old_page)
            self.assertEqual(window.stack.currentIndex(), 2)
            self.assertEqual(len(window._sidebar_nav_buttons), 5)
            self.assertTrue(window._sidebar_collapsed)
        window.deleteLater()

    def test_visible_sidebar_slides_and_can_reverse_without_rebuilding_page(self) -> None:
        from PySide6.QtTest import QTest

        window, stack = self.build_window()
        with stack:
            window.show()
            self.app.processEvents()
            window.switch_page(2)
            page = window.stack.currentWidget()
            window.toggle_sidebar()
            QTest.qWait(80)
            intermediate = window.sidebar.width()
            self.assertLess(intermediate, window.SIDEBAR_EXPANDED_WIDTH)
            self.assertGreater(intermediate, window.SIDEBAR_COLLAPSED_WIDTH)
            emitted_widths = []
            window._sidebar_animation.valueChanged.connect(emitted_widths.append)
            window.toggle_sidebar()
            self.assertEqual(window.sidebar.width(), intermediate)
            self.assertTrue(all(width == intermediate for width in emitted_widths))
            QTest.qWait(window.SIDEBAR_ANIMATION_MS + 100)
            self.assertEqual(window.sidebar.width(), window.SIDEBAR_EXPANDED_WIDTH)
            window.toggle_sidebar()
            window.resize(930, 630)
            QTest.qWait(window.SIDEBAR_ANIMATION_MS + 100)
            self.assertEqual(window.sidebar.width(), window.SIDEBAR_COLLAPSED_WIDTH)
            self.assertIs(window.stack.currentWidget(), page)
            self.assertEqual(window.stack.currentIndex(), 2)
            self.assertTrue(window._sidebar_header.isHidden())
            window.close()
        window.deleteLater()

    def test_manual_sidebar_choice_wins_over_later_resize(self) -> None:
        window, stack = self.build_window()
        with stack:
            window.set_sidebar_collapsed(False, remember=True)
            window.resize(930, 630)
            self.app.processEvents()
            self.assertFalse(window._sidebar_collapsed)
            self.assertEqual(window.sidebar.width(), window.SIDEBAR_EXPANDED_WIDTH)
        window.deleteLater()

    def test_llm_toggle_preserves_all_settings_and_changes_only_enabled(self) -> None:
        from PySide6.QtWidgets import QCheckBox
        from study_app.ui import window_shell

        original = self.llm_settings()
        window, stack = self.build_window()
        with stack, patch.object(window_shell, "load_llm_settings", return_value=original), patch.object(
            window_shell, "save_llm_settings"
        ) as save:
            toggle = next(box for box in window.findChildren(QCheckBox) if box.text() == "本地模式")
            toggle.setChecked(True)
        saved = save.call_args.args[0]
        self.assertTrue(saved.enabled)
        self.assertEqual(saved.provider, original.provider)
        self.assertEqual(saved.model, original.model)
        self.assertEqual(saved.api_key, original.api_key)
        window.deleteLater()

    def test_floating_icon_keeps_owner_and_fixed_size(self) -> None:
        from study_app.ui import window_shell

        owner = Mock()
        icon = window_shell.FloatingIcon(owner)
        self.assertIs(icon.main_window, owner)
        self.assertEqual((icon.width(), icon.height()), (64, 64))
        icon.deleteLater()


if __name__ == "__main__":
    unittest.main()
