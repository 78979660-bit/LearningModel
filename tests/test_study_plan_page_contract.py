from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import date
from types import ModuleType
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class StudyPlanPageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def state():
        from study_app.core.dashboard import DashboardState

        return DashboardState(date(2026, 7, 15), date(2026, 7, 17), 60, (), (), (), ())

    def generation_page(self, generator, *, fresh_states=None, signature=None, active_plans=None):
        from study_app.ui import study_plan_page

        state = self.state()
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        stack = ExitStack()
        stack.enter_context(patch.dict(sys.modules, {"study_app.core.study_phase": phase}))
        stack.enter_context(
            patch.object(
                study_plan_page,
                "plan_subject_scope_options",
                return_value=(("全部活动学科", ""),),
            )
        )
        if active_plans is None:
            stack.enter_context(patch.object(study_plan_page, "get_active_study_plan", return_value=None))
        else:
            stack.enter_context(patch.object(study_plan_page, "get_active_study_plan", side_effect=active_plans))
        if fresh_states is None:
            stack.enter_context(patch.object(study_plan_page, "load_dashboard_state", return_value=state))
        else:
            stack.enter_context(patch.object(study_plan_page, "load_dashboard_state", side_effect=fresh_states))
        stack.enter_context(patch.object(study_plan_page, "final_review_subject_names", return_value=()))
        stack.enter_context(patch.object(study_plan_page, "llm_plan_unavailable_hint", return_value=""))
        if signature is None:
            stack.enter_context(patch.object(study_plan_page, "current_study_plan_signature", return_value="sig"))
        else:
            stack.enter_context(patch.object(study_plan_page, "current_study_plan_signature", side_effect=signature))
        stack.enter_context(patch.object(study_plan_page, "archive_active_study_plan"))
        stack.enter_context(patch.object(study_plan_page, "delete_settings_by_prefix"))
        stack.enter_context(
            patch.object(study_plan_page, "generate_study_plan_with_optional_llm", side_effect=generator)
        )
        create_plan = stack.enter_context(patch.object(study_plan_page, "create_study_plan"))
        warning = stack.enter_context(patch("PySide6.QtWidgets.QMessageBox.warning"))
        page = study_plan_page.study_plan_page(state)
        return stack, page, create_plan, warning

    def pdf_page(self, generator):
        from study_app.ui import study_plan_page

        state = self.state()
        line = "当天作业：完成概率论作业，参考难度 72/100"
        saved = {
            "id": 9,
            "created_at": "2026-07-18 10:00:00",
            "plan": {
                "judgement": [],
                "goals": [],
                "short": [line],
                "diagnostic": [],
                "record_template": [],
                "expected": [],
                "evidence": [],
            },
            "items": [
                {
                    "id": 23,
                    "section_key": "short",
                    "item_type": "result",
                    "item_text": line,
                    "checked": False,
                    "result": None,
                }
            ],
        }
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        stack = ExitStack()
        stack.enter_context(patch.dict(sys.modules, {"study_app.core.study_phase": phase}))
        stack.enter_context(
            patch.object(
                study_plan_page,
                "plan_subject_scope_options",
                return_value=(("测试占位学科", "测试占位学科"),),
            )
        )
        stack.enter_context(patch.object(study_plan_page, "get_active_study_plan", return_value=saved))
        stack.enter_context(patch.object(study_plan_page, "load_dashboard_state", return_value=state))
        stack.enter_context(patch.object(study_plan_page, "final_review_subject_names", return_value=()))
        stack.enter_context(patch.object(study_plan_page, "should_refresh_plan_for_model", return_value=False))
        stack.enter_context(patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]))
        stack.enter_context(patch.object(study_plan_page, "build_practice_generation_prompt", return_value="test prompt"))
        stack.enter_context(
            patch(
                "study_app.integrations.chatgpt_desktop_bridge.generate_pdf_with_chatgpt",
                side_effect=generator,
            )
        )
        warning = stack.enter_context(patch("PySide6.QtWidgets.QMessageBox.warning"))
        information = stack.enter_context(patch("PySide6.QtWidgets.QMessageBox.information"))
        page = study_plan_page.study_plan_page(state)
        return stack, page, warning, information

    def wait_for_plan_worker(self, content, worker, timeout: float = 2.0) -> None:
        from study_app.ui.study_plan_page import _PLAN_GENERATION_WORKERS

        deadline = time.monotonic() + timeout
        while not worker.isFinished() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(worker.wait(100))
        self.app.processEvents()
        self.assertIsNone(getattr(content, "_plan_worker", None))
        self.assertIsNone(getattr(content, "_plan_generation_token", None))
        self.assertIsNone(getattr(content, "_plan_worker_watchdog", None))
        self.assertNotIn(worker, _PLAN_GENERATION_WORKERS)

    def assert_generation_controls_enabled(self, content) -> None:
        from PySide6.QtWidgets import QComboBox, QPushButton

        buttons = {
            button.text(): button for button in content.findChildren(QPushButton)
        }
        for text in ("生成今日计划", "期末复习模式", "生成模拟卷 PDF"):
            self.assertTrue(buttons[text].isEnabled(), text)
        self.assertTrue(all(combo.isEnabled() for combo in content.findChildren(QComboBox)))

    def dispose_page(self, page) -> None:
        from PySide6.QtCore import QCoreApplication, QEvent

        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_module_is_lazy_and_main_window_is_identity_facade(self) -> None:
        code = (
            "import sys; from study_app.ui import study_plan_page; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'study_app.ui.main_window' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        from study_app.ui import main_window, study_plan_page

        for name in ("study_plan_page", "plan_day_feedback_card", "plan_section_card"):
            self.assertIs(getattr(main_window, name), getattr(study_plan_page, name))

    def test_budget_draft_is_prepared_without_saving(self) -> None:
        from study_app.core.plan_candidates import CandidateCollection
        from study_app.ui import study_plan_page

        with (
            patch.object(study_plan_page, "get_budgeted_day_plan", return_value=None),
            patch.object(study_plan_page, "save_study_day_budget") as save_budget,
            patch.object(study_plan_page, "create_budgeted_day_plan") as save_plan,
        ):
            draft = study_plan_page._prepare_budget_plan(
                self.state(), None, "60", {}, {}, CandidateCollection((), ()), {}, {}
            )
        self.assertEqual(draft.budget.available_minutes, 60)
        self.assertEqual(draft.plan.remaining_minutes, 60)
        self.assertEqual(draft.new_estimates, {})
        save_budget.assert_not_called()
        save_plan.assert_not_called()

    def test_budget_draft_rejects_completed_task_missing_from_current_candidates(self) -> None:
        from study_app.core.plan_candidates import CandidateCollection
        from study_app.ui import study_plan_page

        prior = {"items": [{"task_id": "removed-task", "checked": True}]}
        with patch.object(study_plan_page, "get_budgeted_day_plan", return_value=prior):
            with self.assertRaisesRegex(ValueError, "已有完成任务不在当前推荐中"):
                study_plan_page._prepare_budget_plan(
                    self.state(), None, "60", {}, {}, CandidateCollection((), ()), {}, {}
                )

    def test_reusable_plan_keeps_existing_plan_until_refresh_is_needed(self) -> None:
        from study_app.ui import study_plan_page

        saved = {"id": 17}
        with (
            patch.object(study_plan_page, "get_active_study_plan", return_value=saved),
            patch.object(study_plan_page, "should_refresh_plan_for_model", return_value=False) as refresh,
            patch.object(study_plan_page, "should_upgrade_plan_to_llm", return_value=False) as upgrade,
        ):
            self.assertIs(study_plan_page._reusable_active_plan(self.state(), None, False), saved)
            self.assertIsNone(study_plan_page._reusable_active_plan(self.state(), None, True))
            refresh.assert_called_once()
            upgrade.assert_called_once()

    def test_completed_day_feedback_excludes_shown_and_incomplete_days(self) -> None:
        from study_app.ui import study_plan_page

        details = [
            {"day_index": 1, "complete": True, "popup": "已显示"},
            {"day_index": 2, "complete": True, "popup": "新完成"},
            {"day_index": 3, "complete": False, "popup": "未完成"},
        ]
        with (
            patch.object(study_plan_page, "get_setting", return_value=["9:1"]),
            patch.object(study_plan_page, "plan_day_feedback_details", return_value=details),
            patch.object(study_plan_page, "set_setting") as save_markers,
        ):
            feedback = study_plan_page._completed_day_feedback(
                {"id": 9}, self.state(), None
            )

        self.assertEqual(feedback.messages, ("新完成",))
        self.assertEqual(feedback.previous_markers, frozenset({"9:1"}))
        self.assertEqual(feedback.current_markers, frozenset({"9:1", "9:2"}))
        save_markers.assert_not_called()

        with patch.object(study_plan_page, "set_setting") as save_markers:
            study_plan_page._save_completed_day_feedback_markers(feedback)
        save_markers.assert_called_once_with("study_plan_day_feedback_shown", ["9:1", "9:2"])

    def test_archiving_current_plan_invalidates_summary_only_after_archive(self) -> None:
        from study_app.ui import study_plan_page

        with (
            patch.object(study_plan_page, "archive_active_study_plan") as archive,
            patch.object(study_plan_page, "delete_settings_by_prefix") as invalidate,
        ):
            study_plan_page._archive_current_plan("计算机科学")
        archive.assert_called_once_with("计算机科学")
        invalidate.assert_called_once_with("daily_summary_cache:")

        with (
            patch.object(
                study_plan_page,
                "archive_active_study_plan",
                side_effect=RuntimeError("archive failed"),
            ),
            patch.object(study_plan_page, "delete_settings_by_prefix") as invalidate,
        ):
            with self.assertRaisesRegex(RuntimeError, "archive failed"):
                study_plan_page._archive_current_plan("计算机科学")
        invalidate.assert_not_called()

    def test_dashboard_update_reaches_both_panels_without_affecting_another_page(self) -> None:
        from study_app.ui import study_plan_page

        with (
            patch.object(study_plan_page, "get_study_day_budget", return_value=None),
            patch.object(study_plan_page, "get_budgeted_day_plan", return_value=None),
        ):
            stack, page, _, _ = self.pdf_page(lambda *_args: None)
        other_page = None
        try:
            with (
                patch.object(study_plan_page, "get_study_day_budget", return_value=None),
                patch.object(study_plan_page, "get_budgeted_day_plan", return_value=None),
            ):
                other_page = study_plan_page.study_plan_page(self.state())
            updated = replace(self.state(), today=date(2026, 7, 16))
            page._set_dashboard_state(updated)
            with patch.object(study_plan_page, "get_budgeted_day_plan", return_value=None) as read_budget:
                page.widget()._budget_plan_callbacks[1]()
                self.assertEqual(read_budget.call_args.args[0], "2026-07-16")
                other_page.widget()._budget_plan_callbacks[1]()
                self.assertEqual(read_budget.call_args.args[0], self.state().today.isoformat())
            saved = study_plan_page.get_active_study_plan()
            with patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]) as feedback:
                page.widget()._plan_callbacks[1](saved, allow_auto_refresh=False, reload_state=False)
                self.assertIs(feedback.call_args.args[1], updated)
                other_page.widget()._plan_callbacks[1](saved, allow_auto_refresh=False, reload_state=False)
                self.assertEqual(feedback.call_args.args[1].today, self.state().today)
        finally:
            if other_page is not None:
                other_page.deleteLater()
            page.deleteLater()
            self.app.processEvents()
            stack.close()

    def test_review_dialog_cancel_does_not_save_or_refresh_page(self) -> None:
        from PySide6.QtWidgets import QDialog, QPushButton
        from study_app.ui import study_plan_page

        stack, page, _, _ = self.generation_page(lambda *_args: None)
        try:
            with (
                patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Rejected),
                patch.object(study_plan_page, "_apply_final_review_modes") as save,
                patch.object(study_plan_page, "load_dashboard_state") as reload_state,
            ):
                button = next(b for b in page.findChildren(QPushButton) if b.text() == "期末复习模式")
                button.click()
                save.assert_not_called()
                reload_state.assert_not_called()
        finally:
            page.deleteLater()
            self.app.processEvents()
            stack.close()

    def test_review_dialog_save_applies_selection_and_refreshes_page(self) -> None:
        from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton
        from study_app.ui import study_plan_page

        def accept_selection(dialog):
            dialog.findChild(QCheckBox).setChecked(True)
            next(b for b in dialog.findChildren(QPushButton) if b.text() == "保存设置").click()
            return dialog.result()

        stack, page, _, _ = self.generation_page(lambda *_args: None)
        try:
            with (
                patch.object(QDialog, "exec", accept_selection),
                patch.object(study_plan_page, "final_review_subject_names", return_value=("数学",)),
                patch.object(study_plan_page, "_apply_final_review_modes") as save,
                patch.object(study_plan_page, "load_dashboard_state", return_value=self.state()) as reload_state,
                patch("PySide6.QtWidgets.QMessageBox.information") as information,
            ):
                button = next(b for b in page.findChildren(QPushButton) if b.text() == "期末复习模式")
                button.click()
                save.assert_called_once_with({"数学": False}, {"数学": True})
                self.assertTrue(reload_state.called)
                self.assertIn("数学：已启用", information.call_args.args[2])
        finally:
            page.deleteLater()
            self.app.processEvents()
            stack.close()

    def test_study_plan_page_empty_state_is_read_only_and_keeps_callbacks(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton
        from study_app.ui import study_plan_page

        state = self.state()
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="未进入期末复习")
        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(study_plan_page, "plan_subject_scope_options", return_value=(("全部活动学科", ""),)),
            patch.object(study_plan_page, "get_active_study_plan", return_value=None) as get_plan,
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(study_plan_page, "create_study_plan") as create_plan,
            patch.object(study_plan_page, "archive_active_study_plan") as archive_plan,
            patch.object(study_plan_page, "update_study_plan_item_state") as update_item,
            patch.object(study_plan_page, "add_learning_record") as add_record,
        ):
            page = study_plan_page.study_plan_page(state)

        content = page.widget()
        self.assertEqual(len(content._plan_callbacks), 6)
        self.assertIn("计划进度：尚未生成", "\n".join(label.text() for label in content.findChildren(QLabel)))
        self.assertTrue(any(button.text() == "生成今日计划" for button in content.findChildren(QPushButton)))
        self.assertGreaterEqual(get_plan.call_count, 1)
        create_plan.assert_not_called()
        archive_plan.assert_not_called()
        update_item.assert_not_called()
        add_record.assert_not_called()

    def test_plan_generation_thread_success_restores_ui_and_persists_plan(self) -> None:
        from PySide6.QtWidgets import QPushButton

        plan = {
            "judgement": [],
            "goals": [],
            "short": [],
            "diagnostic": [],
            "record_template": [],
            "expected": [],
            "evidence": [],
        }
        stack, page, create_plan, warning = self.generation_page(lambda *_args: (plan, "local"))
        try:
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划"
            )
            button.click()
            worker = content._plan_worker
            self.wait_for_plan_worker(content, worker)

            self.assertEqual(button.text(), "生成今日计划")
            self.assert_generation_controls_enabled(content)
            create_plan.assert_called_once_with(
                None,
                "2026-07-17",
                "2026-07-17",
                "sig",
                plan,
                [],
            )
            warning.assert_not_called()
        finally:
            self.dispose_page(page)
            stack.close()

    def test_saved_signature_uses_the_state_snapshot_given_to_generator(self) -> None:
        from PySide6.QtWidgets import QPushButton

        generated_plan = {key: [] for key in (
            "judgement", "goals", "short", "diagnostic",
            "record_template", "expected", "evidence",
        )}
        start_state = self.state()
        changed_state = replace(start_state, today=date(2026, 7, 18))
        saved_plan = {
            "id": 10,
            "created_at": "2026-07-17 10:00:00",
            "end_date": "2026-07-17",
            "input_signature": "2026-07-17",
            "plan": generated_plan,
            "items": [],
        }
        generator = Mock(
            side_effect=[
                (generated_plan, "local"),
                RuntimeError("一次点击错误地启动了第二次生成"),
            ]
        )
        stack, page, create_plan, _warning = self.generation_page(
            generator,
            fresh_states=[start_state, start_state, changed_state, changed_state],
            signature=lambda snapshot, _subject: snapshot.today.isoformat(),
            active_plans=[None, None, saved_plan, saved_plan],
        )
        try:
            content = page.widget()
            button = next(item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划")
            button.click()
            worker = content._plan_worker
            self.wait_for_plan_worker(content, worker)

            create_plan.assert_called_once_with(
                None,
                "2026-07-17",
                "2026-07-17",
                "2026-07-17",
                generated_plan,
                [],
            )
            generator.assert_called_once_with(start_state, None)
        finally:
            self.dispose_page(page)
            stack.close()

    def test_generate_button_forces_new_version_when_active_plan_is_current(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.ui import study_plan_page

        state = self.state()
        plan = {key: [] for key in (
            "judgement", "goals", "short", "diagnostic",
            "record_template", "expected", "evidence",
        )}
        saved = {
            "id": 9,
            "created_at": "2026-07-17 10:00:00",
            "end_date": "2026-07-17",
            "input_signature": "sig",
            "plan": plan,
            "items": [],
        }
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        generator = Mock(return_value=(plan, "local"))
        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(study_plan_page, "plan_subject_scope_options", return_value=(("全部活动学科", ""),)),
            patch.object(study_plan_page, "get_active_study_plan", return_value=saved),
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(study_plan_page, "llm_plan_unavailable_hint", return_value=""),
            patch.object(study_plan_page, "should_refresh_plan_for_model", return_value=False),
            patch.object(study_plan_page, "should_upgrade_plan_to_llm", return_value=False),
            patch.object(study_plan_page, "current_study_plan_signature", return_value="sig"),
            patch.object(study_plan_page, "generate_study_plan_with_optional_llm", generator),
            patch.object(study_plan_page, "build_study_plan_items", return_value=[]),
            patch.object(study_plan_page, "create_study_plan"),
            patch.object(study_plan_page, "delete_settings_by_prefix"),
            patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]),
            patch.object(study_plan_page, "archive_active_study_plan") as archive_plan,
        ):
            page = study_plan_page.study_plan_page(state)
            try:
                content = page.widget()
                button = next(item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划")
                button.click()
                worker = getattr(content, "_plan_worker", None)
                self.assertIsNotNone(worker)
                self.wait_for_plan_worker(content, worker)
                archive_plan.assert_not_called()
                generator.assert_called_once_with(state, None)
            finally:
                self.dispose_page(page)

    def test_llm_validation_fallback_is_shown_as_warning(self) -> None:
        from PySide6.QtWidgets import QPushButton

        plan = {key: [] for key in (
            "judgement", "goals", "short", "diagnostic",
            "record_template", "expected", "evidence",
        )}
        source = "已显示本地计划；LLM 增强未通过校验：LLM 调用失败：HTTP 503"
        stack, page, _create_plan, warning = self.generation_page(
            lambda *_args: (plan, source)
        )
        try:
            content = page.widget()
            button = next(item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划")
            button.click()
            worker = content._plan_worker
            self.wait_for_plan_worker(content, worker)

            warning.assert_called_once()
            self.assertEqual(warning.call_args.args[1], "LLM 计划生成失败")
            message = warning.call_args.args[2]
            self.assertIn("LLM 调用失败：HTTP 503", message)
            self.assertNotIn("已自动要求 LLM 修复一次", message)
        finally:
            self.dispose_page(page)
            stack.close()

    def test_plan_generation_worker_can_restart_with_fresh_state(self) -> None:
        from study_app.ui.study_plan_page import _PLAN_GENERATION_WORKERS

        second_run_gate = threading.Event()
        calls = 0
        plan = {
            key: []
            for key in (
                "judgement",
                "goals",
                "short",
                "diagnostic",
                "record_template",
                "expected",
                "evidence",
            )
        }

        def generate_twice(*_args):
            nonlocal calls
            calls += 1
            if calls == 2:
                second_run_gate.wait(2)
            return plan, "local"

        stack, page, _create_plan, _warning = self.generation_page(generate_twice)
        try:
            content = page.widget()
            from PySide6.QtWidgets import QPushButton

            button = next(
                item
                for item in content.findChildren(QPushButton)
                if item.text() == "生成今日计划"
            )
            button.click()
            worker = content._plan_worker
            self.wait_for_plan_worker(content, worker)

            worker.requestInterruption()
            worker.start()
            self.assertTrue(worker.isRunning())
            self.assertFalse(worker.isFinished())
            self.assertFalse(worker.wait(0))
            self.assertFalse(worker.isInterruptionRequested())
            self.assertIn(worker, _PLAN_GENERATION_WORKERS)

            second_run_gate.set()
            self.assertTrue(worker.wait(2000))
            self.app.processEvents()
            self.assertNotIn(worker, _PLAN_GENERATION_WORKERS)
            self.assertEqual(calls, 2)
        finally:
            second_run_gate.set()
            self.dispose_page(page)
            stack.close()

    def test_plan_generation_thread_failure_restores_ui_and_reports_error(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton

        def fail(*_args):
            raise RuntimeError("provider unavailable")

        stack, page, create_plan, warning = self.generation_page(fail)
        try:
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划"
            )
            button.click()
            worker = content._plan_worker
            self.wait_for_plan_worker(content, worker)

            self.assertEqual(button.text(), "生成今日计划")
            self.assert_generation_controls_enabled(content)
            self.assertIn(
                "计划生成失败，请检查网络或 LLM 设置后重试。",
                [label.text() for label in content.findChildren(QLabel)],
            )
            create_plan.assert_not_called()
            warning.assert_called_once()
            self.assertEqual(warning.call_args.args[1:], ("计划生成失败", "provider unavailable"))
        finally:
            self.dispose_page(page)
            stack.close()

    def test_plan_generation_start_failure_cleans_registry_watchdog_and_ui(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.ui import study_plan_page

        stack, page, create_plan, warning = self.generation_page(lambda *_args: ({}, "local"))
        try:
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划"
            )
            with patch.object(
                study_plan_page.threading.Thread,
                "start",
                side_effect=RuntimeError("worker start failure"),
            ):
                button.click()
                self.app.processEvents()

            self.assertIsNone(getattr(content, "_plan_worker", None))
            self.assertIsNone(getattr(content, "_plan_generation_token", None))
            self.assertIsNone(getattr(content, "_plan_worker_watchdog", None))
            self.assertEqual(study_plan_page._PLAN_GENERATION_WORKERS, set())
            self.assert_generation_controls_enabled(content)
            create_plan.assert_not_called()
            warning.assert_called_once_with(content, "计划生成失败", "worker start failure")
        finally:
            self.dispose_page(page)
            stack.close()

    def test_plan_generation_timeout_ignores_late_success(self) -> None:
        from PySide6.QtWidgets import QPushButton

        gate = threading.Event()
        plan = {key: [] for key in (
            "judgement", "goals", "short", "diagnostic",
            "record_template", "expected", "evidence",
        )}

        def delayed_success(*_args):
            gate.wait(2)
            return plan, "local"

        stack, page, create_plan, warning = self.generation_page(delayed_success)
        try:
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划"
            )
            button.click()
            worker = content._plan_worker
            content._plan_worker_watchdog.timeout.emit()
            self.app.processEvents()

            self.assertIsNone(content._plan_worker)
            self.assertIsNone(content._plan_generation_token)
            self.assertIsNone(content._plan_worker_watchdog)
            self.assert_generation_controls_enabled(content)
            warning.assert_called_once()
            self.assertEqual(warning.call_args.args[1], "LLM 计划生成超时")

            gate.set()
            self.assertTrue(worker.wait(2000))
            self.app.processEvents()
            create_plan.assert_not_called()
            self.assertEqual(warning.call_count, 1)
        finally:
            gate.set()
            self.dispose_page(page)
            stack.close()

    def test_plan_generation_timeout_ignores_late_failure(self) -> None:
        from PySide6.QtWidgets import QPushButton

        gate = threading.Event()

        def delayed_failure(*_args):
            gate.wait(2)
            raise RuntimeError("late provider failure")

        stack, page, create_plan, warning = self.generation_page(delayed_failure)
        try:
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划"
            )
            button.click()
            worker = content._plan_worker
            content._plan_worker_watchdog.timeout.emit()
            self.app.processEvents()

            gate.set()
            self.assertTrue(worker.wait(2000))
            self.app.processEvents()
            create_plan.assert_not_called()
            warning.assert_called_once()
            self.assertEqual(warning.call_args.args[1], "LLM 计划生成超时")
            self.assert_generation_controls_enabled(content)
        finally:
            gate.set()
            self.dispose_page(page)
            stack.close()

    def test_plan_generation_persistence_callback_runs_on_gui_thread(self) -> None:
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QPushButton

        plan = {key: [] for key in (
            "judgement", "goals", "short", "diagnostic",
            "record_template", "expected", "evidence",
        )}
        stack, page, create_plan, _warning = self.generation_page(lambda *_args: (plan, "local"))
        callback_threads = []
        create_plan.side_effect = lambda *_args: callback_threads.append(QThread.currentThread())
        try:
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton) if item.text() == "生成今日计划"
            )
            button.click()
            worker = content._plan_worker
            self.wait_for_plan_worker(content, worker)

            self.assertEqual(callback_threads, [self.app.thread()])
        finally:
            self.dispose_page(page)
            stack.close()

    def test_running_plan_worker_survives_page_deletion(self) -> None:
        code = textwrap.dedent(
            """
            import os, time
            os.environ['QT_QPA_PLATFORM'] = 'offscreen'
            from PySide6.QtCore import QCoreApplication, QEvent
            from PySide6.QtWidgets import QPushButton
            from test_study_plan_page_contract import StudyPlanPageContractTests
            from study_app.ui.study_plan_page import _PLAN_GENERATION_WORKERS

            case = StudyPlanPageContractTests('runTest')
            case.setUpClass()
            plan = {key: [] for key in (
                'judgement', 'goals', 'short', 'diagnostic',
                'record_template', 'expected', 'evidence'
            )}
            def delayed(*_args):
                time.sleep(0.25)
                return plan, 'local'

            stack, page, _create, _warning = case.generation_page(delayed)
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton)
                if item.text() == '生成今日计划'
            )
            button.click()
            worker = content._plan_worker
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            case.app.processEvents()
            assert worker.wait(2000)
            case.app.processEvents()
            assert worker not in _PLAN_GENERATION_WORKERS
            stack.close()
            print('SURVIVED_RUNNING_PAGE_DELETE')
            """
        )
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(("tests", os.environ.get("PYTHONPATH", ""))),
        }
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SURVIVED_RUNNING_PAGE_DELETE", result.stdout)

    def test_bridge_result_messages_are_not_exposed(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult

        cases = (
            (
                ChatGPTBridgeResult(False, "bridge_failed", "SECRET C:/private/cmd"),
                "ChatGPT 未能完成 PDF 生成。为避免丢失，出题提示词已复制到剪贴板。",
            ),
            (
                ChatGPTBridgeResult(
                    True,
                    "pdf_saved",
                    "SECRET powershell failure",
                    "test.pdf",
                ),
                "PDF 已安全下载，但系统未能自动打开文件。\n\n文件位置：\ntest.pdf",
            ),
        )
        for result, expected_message in cases:
            with self.subTest(code=result.code):
                stack, page, warning, _information = self.pdf_page(
                    Mock(return_value=result)
                )
                try:
                    content = page.widget()
                    button = next(
                        item
                        for item in content.findChildren(QPushButton)
                        if item.text() == "让 ChatGPT 生成 PDF"
                    )
                    button.click()
                    worker = content._chatgpt_bridge_threads[0]
                    self.assertTrue(worker.wait(2000))
                    self.app.processEvents()

                    self.assertEqual(warning.call_args.args[2], expected_message)
                    self.assertNotIn("SECRET", repr(warning.call_args))
                    self.assertNotIn(
                        "SECRET",
                        " ".join(label.text() for label in content.findChildren(QLabel)),
                    )
                finally:
                    self.dispose_page(page)
                    stack.close()

    def test_pdf_worker_exception_restores_busy_state(self) -> None:
        from PySide6.QtWidgets import QPushButton

        def fail(_prompt):
            raise RuntimeError("bridge exploded")

        stack, page, warning, _information = self.pdf_page(fail)
        try:
            content = page.widget()
            buttons = {
                button.text(): button for button in content.findChildren(QPushButton)
            }
            buttons["让 ChatGPT 生成 PDF"].click()
            worker = content._chatgpt_bridge_threads[0]
            self.assertTrue(worker.wait(2000))
            self.app.processEvents()

            self.assertEqual(content._chatgpt_bridge_threads, [])
            self.assertFalse(buttons["停止 PDF 生成"].isVisible())
            self.assertTrue(buttons["停止 PDF 生成"].isEnabled() is False)
            warning.assert_called_once()
            self.assertEqual(
                warning.call_args.args[1:],
                (
                    "ChatGPT 自动生成失败",
                    "ChatGPT 桌面桥接发生错误，请检查桌面端状态后重试。",
                ),
            )
        finally:
            self.dispose_page(page)
            stack.close()

    def test_pdf_worker_success_restores_busy_state(self) -> None:
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QLabel, QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
        from study_app.ui.study_plan_page import _CHATGPT_BRIDGE_WORKERS

        generator = Mock(
            return_value=ChatGPTBridgeResult(
                True,
                "pdf_opened",
                "opened",
                "test.pdf",
            )
        )
        stack, page, warning, information = self.pdf_page(generator)
        callback_threads = []
        information.side_effect = lambda *_args: callback_threads.append(
            QThread.currentThread()
        )
        try:
            content = page.widget()
            buttons = {
                button.text(): button for button in content.findChildren(QPushButton)
            }
            buttons["让 ChatGPT 生成 PDF"].click()
            worker = content._chatgpt_bridge_threads[0]
            self.assertTrue(worker.wait(2000))
            self.app.processEvents()

            generator.assert_called_once_with("test prompt")
            self.assertNotIn(worker, _CHATGPT_BRIDGE_WORKERS)
            self.assertEqual(content._chatgpt_bridge_threads, [])
            self.assertFalse(buttons["停止 PDF 生成"].isVisible())
            self.assertFalse(buttons["停止 PDF 生成"].isEnabled())
            self.assertIn(
                "ChatGPT 已生成 PDF，并已自动打开。",
                [label.text() for label in content.findChildren(QLabel)],
            )
            warning.assert_not_called()
            self.assertEqual(information.call_args.args[1], "PDF 已生成")
            self.assertEqual(callback_threads, [self.app.thread()])
        finally:
            self.dispose_page(page)
            stack.close()

    def test_stopped_pdf_worker_ignores_late_success(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
        from study_app.ui.study_plan_page import _CHATGPT_BRIDGE_WORKERS

        gate = threading.Event()

        def delayed(_prompt):
            gate.wait(2)
            return ChatGPTBridgeResult(True, "pdf_opened", "opened", "test.pdf")

        cancel_result = ChatGPTBridgeResult(True, "cancelled", "cancelled")
        with patch(
            "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
            return_value=cancel_result,
        ) as cancel:
            stack, page, warning, information = self.pdf_page(delayed)
            try:
                content = page.widget()
                buttons = {
                    button.text(): button for button in content.findChildren(QPushButton)
                }
                buttons["让 ChatGPT 生成 PDF"].click()
                worker = content._chatgpt_bridge_threads[0]
                self.assertIn(worker, _CHATGPT_BRIDGE_WORKERS)
                buttons["停止 PDF 生成"].click()
                cancel.assert_called_once_with()
                self.assertFalse(buttons["停止 PDF 生成"].isVisible())

                gate.set()
                self.assertTrue(worker.wait(2000))
                self.app.processEvents()

                self.assertNotIn(worker, _CHATGPT_BRIDGE_WORKERS)
                self.assertEqual(content._chatgpt_bridge_threads, [])
                self.assertIn(
                    "ChatGPT PDF 生成已手动停止。",
                    [label.text() for label in content.findChildren(QLabel)],
                )
                warning.assert_not_called()
                self.assertNotIn(
                    "PDF 已生成",
                    [call_.args[1] for call_ in information.call_args_list],
                )
            finally:
                gate.set()
                self.dispose_page(page)
                stack.close()

    def test_pdf_worker_start_exception_releases_registry_and_busy_state(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.ui.study_plan_page import _CHATGPT_BRIDGE_WORKERS

        stack, page, warning, _information = self.pdf_page(Mock())
        try:
            content = page.widget()
            buttons = {
                button.text(): button for button in content.findChildren(QPushButton)
            }
            with patch("threading.Thread.start", side_effect=RuntimeError("start detail")):
                buttons["让 ChatGPT 生成 PDF"].click()

            self.assertEqual(_CHATGPT_BRIDGE_WORKERS, set())
            self.assertEqual(content._chatgpt_bridge_threads, [])
            self.assertTrue(buttons["停止 PDF 生成"].isHidden())
            self.assertEqual(
                warning.call_args.args[1:],
                ("启动 PDF 生成失败", "未能启动 PDF 生成任务，请稍后重试。"),
            )
            self.assertNotIn("start detail", repr(warning.call_args))
        finally:
            self.dispose_page(page)
            stack.close()

    def test_pdf_cancel_exception_keeps_result_valid(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult

        gate = threading.Event()

        def delayed(_prompt):
            gate.wait(2)
            return ChatGPTBridgeResult(True, "pdf_opened", "opened", "test.pdf")

        with patch(
            "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
            side_effect=RuntimeError("cancel detail"),
        ):
            stack, page, warning, information = self.pdf_page(delayed)
            try:
                content = page.widget()
                buttons = {
                    button.text(): button for button in content.findChildren(QPushButton)
                }
                buttons["让 ChatGPT 生成 PDF"].click()
                worker = content._chatgpt_bridge_threads[0]
                buttons["停止 PDF 生成"].click()

                self.assertFalse(worker.cancelled)
                self.assertFalse(buttons["停止 PDF 生成"].isHidden())
                self.assertEqual(
                    warning.call_args.args[1:],
                    ("停止 PDF 生成失败", "未能停止当前 PDF 生成任务，请稍后重试。"),
                )
                self.assertNotIn("cancel detail", repr(warning.call_args))

                gate.set()
                self.assertTrue(worker.wait(2000))
                self.app.processEvents()
                self.assertEqual(information.call_args.args[1], "PDF 已生成")
            finally:
                gate.set()
                self.dispose_page(page)
                stack.close()

    def test_pdf_cancel_failure_keeps_result_valid(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult

        gate = threading.Event()

        def delayed(_prompt):
            gate.wait(2)
            return ChatGPTBridgeResult(True, "pdf_opened", "opened", "test.pdf")

        cancel_result = ChatGPTBridgeResult(False, "cancel_failed", "cannot cancel")
        with patch(
            "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
            return_value=cancel_result,
        ):
            stack, page, warning, information = self.pdf_page(delayed)
            try:
                content = page.widget()
                buttons = {
                    button.text(): button for button in content.findChildren(QPushButton)
                }
                buttons["让 ChatGPT 生成 PDF"].click()
                worker = content._chatgpt_bridge_threads[0]
                buttons["停止 PDF 生成"].click()
                self.assertFalse(buttons["停止 PDF 生成"].isHidden())
                self.assertEqual(
                    warning.call_args.args[1:],
                    ("停止 PDF 生成失败", "未能停止当前 PDF 生成任务，请稍后重试。"),
                )

                gate.set()
                self.assertTrue(worker.wait(2000))
                self.app.processEvents()
                self.assertEqual(information.call_args.args[1], "PDF 已生成")
            finally:
                gate.set()
                self.dispose_page(page)
                stack.close()

    def test_cancelled_pdf_failure_is_not_reported_as_generation_error(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult

        gate = threading.Event()

        def delayed_failure(_prompt):
            gate.wait(2)
            raise RuntimeError("late bridge detail")

        cancel_result = ChatGPTBridgeResult(True, "cancelled", "cancelled")
        with patch(
            "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
            return_value=cancel_result,
        ):
            stack, page, warning, _information = self.pdf_page(delayed_failure)
            try:
                content = page.widget()
                buttons = {
                    button.text(): button for button in content.findChildren(QPushButton)
                }
                buttons["让 ChatGPT 生成 PDF"].click()
                worker = content._chatgpt_bridge_threads[0]
                buttons["停止 PDF 生成"].click()
                gate.set()
                self.assertTrue(worker.wait(2000))
                self.app.processEvents()

                warning.assert_not_called()
                self.assertIn(
                    "ChatGPT PDF 生成已手动停止。",
                    [label.text() for label in content.findChildren(QLabel)],
                )
            finally:
                gate.set()
                self.dispose_page(page)
                stack.close()

    def test_cancelled_old_pdf_result_is_ignored_after_restart(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult

        first_gate = threading.Event()
        second_gate = threading.Event()
        calls = 0

        def delayed(_prompt):
            nonlocal calls
            calls += 1
            (first_gate if calls == 1 else second_gate).wait(2)
            return ChatGPTBridgeResult(True, "pdf_opened", "opened", f"test-{calls}.pdf")

        cancel_result = ChatGPTBridgeResult(True, "cancelled", "cancelled")
        with patch(
            "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
            return_value=cancel_result,
        ):
            stack, page, warning, information = self.pdf_page(delayed)
            try:
                content = page.widget()
                buttons = {
                    button.text(): button for button in content.findChildren(QPushButton)
                }
                buttons["让 ChatGPT 生成 PDF"].click()
                first_worker = content._chatgpt_bridge_threads[0]
                buttons["停止 PDF 生成"].click()
                first_gate.set()
                self.assertTrue(first_worker.wait(2000))

                buttons["让 ChatGPT 生成 PDF"].click()
                second_worker = content._chatgpt_bridge_threads[-1]
                self.app.processEvents()
                self.assertNotIn(
                    "PDF 已生成",
                    [call_.args[1] for call_ in information.call_args_list],
                )

                second_gate.set()
                self.assertTrue(second_worker.wait(2000))
                self.app.processEvents()
                self.assertEqual(
                    [
                        call_.args[1]
                        for call_ in information.call_args_list
                        if call_.args[1] == "PDF 已生成"
                    ],
                    ["PDF 已生成"],
                )
                warning.assert_not_called()
                self.assertEqual(calls, 2)
            finally:
                first_gate.set()
                second_gate.set()
                self.dispose_page(page)
                stack.close()

    def test_pdf_generation_is_singleton_and_cancelable_across_pages(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
        from study_app.ui.study_plan_page import _CHATGPT_BRIDGE_WORKERS

        gate = threading.Event()

        def delayed(_prompt):
            gate.wait(2)
            return ChatGPTBridgeResult(True, "pdf_opened", "opened", "test.pdf")

        generator = Mock(side_effect=delayed)
        cancel_result = ChatGPTBridgeResult(True, "cancelled", "cancelled")
        with patch(
            "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
            return_value=cancel_result,
        ) as cancel:
            stack_one, page_one, _warning_one, _information_one = self.pdf_page(generator)
            stack_two, page_two, _warning_two, information_two = self.pdf_page(generator)
            try:
                first_button = next(
                    button
                    for button in page_one.widget().findChildren(QPushButton)
                    if button.text() == "让 ChatGPT 生成 PDF"
                )
                second_buttons = {
                    button.text(): button
                    for button in page_two.widget().findChildren(QPushButton)
                }
                first_button.click()
                worker = page_one.widget()._chatgpt_bridge_threads[0]
                second_buttons["让 ChatGPT 生成 PDF"].click()

                self.assertEqual(generator.call_count, 1)
                self.assertEqual(len(_CHATGPT_BRIDGE_WORKERS), 1)
                self.assertEqual(information_two.call_args.args[1], "正在生成 PDF")
                self.assertFalse(second_buttons["停止 PDF 生成"].isHidden())

                self.dispose_page(page_one)
                page_one = None
                second_buttons["停止 PDF 生成"].click()
                cancel.assert_called_once_with()

                gate.set()
                self.assertTrue(worker.wait(2000))
                self.app.processEvents()
                self.assertEqual(_CHATGPT_BRIDGE_WORKERS, set())
                self.assertTrue(second_buttons["停止 PDF 生成"].isHidden())
            finally:
                gate.set()
                self.dispose_page(page_two)
                if page_one is not None:
                    self.dispose_page(page_one)
                stack_two.close()
                stack_one.close()

    def test_app_shutdown_completes_cancel_once_before_returning(self) -> None:
        import time
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
        from study_app.ui.study_plan_page import _CHATGPT_BRIDGE_WORKERS

        stack_one, page_one, _warning_one, _information_one = self.pdf_page(Mock())
        stack_two, page_two, _warning_two, _information_two = self.pdf_page(Mock())
        cancel_called = threading.Event()

        def bounded_cancel(*, deadline):
            cancel_called.set()
            time.sleep(0.05)
            return ChatGPTBridgeResult(True, "cancelled", "ok")

        worker = Mock()
        worker.wait.return_value = True
        _CHATGPT_BRIDGE_WORKERS.add(worker)
        try:
            with patch(
                "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
                side_effect=bounded_cancel,
            ) as cancel:
                started = time.monotonic()
                self.app.aboutToQuit.emit()
                elapsed = time.monotonic() - started
                self.assertTrue(cancel_called.is_set())
                cancel.assert_called_once()
                self.assertGreater(cancel.call_args.kwargs["deadline"], started)
            self.assertGreaterEqual(elapsed, 0.04)
            self.assertLess(elapsed, 0.5)
            worker.wait.assert_called_once()
            self.assertLessEqual(worker.wait.call_args.args[0], 2000)
        finally:
            from study_app.integrations import chatgpt_desktop_bridge
            from study_app.ui import study_plan_page as study_plan_page_module

            chatgpt_desktop_bridge._PDF_SHUTDOWN_EVENT.clear()
            study_plan_page_module._CHATGPT_BRIDGE_SHUTDOWN_STARTED = False
            _CHATGPT_BRIDGE_WORKERS.discard(worker)
            self.dispose_page(page_two)
            self.dispose_page(page_one)
            stack_two.close()
            stack_one.close()

    def test_running_pdf_worker_survives_page_deletion(self) -> None:
        code = textwrap.dedent(
            """
            import os, time
            os.environ['QT_QPA_PLATFORM'] = 'offscreen'
            from PySide6.QtCore import QCoreApplication, QEvent
            from PySide6.QtWidgets import QPushButton
            from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
            from test_study_plan_page_contract import StudyPlanPageContractTests

            case = StudyPlanPageContractTests('runTest')
            case.setUpClass()
            def delayed(_prompt):
                time.sleep(0.25)
                return ChatGPTBridgeResult(True, 'pdf_opened', 'ok', 'test.pdf')

            stack, page, _warning, _information = case.pdf_page(delayed)
            content = page.widget()
            button = next(
                item for item in content.findChildren(QPushButton)
                if item.text() == '让 ChatGPT 生成 PDF'
            )
            button.click()
            worker = content._chatgpt_bridge_threads[0]
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            case.app.processEvents()
            assert worker.wait(2000)
            case.app.processEvents()
            stack.close()
            print('SURVIVED_RUNNING_PDF_PAGE_DELETE')
            """
        )
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(("tests", os.environ.get("PYTHONPATH", ""))),
        }
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SURVIVED_RUNNING_PDF_PAGE_DELETE", result.stdout)

    def test_plan_section_card_callbacks_preserve_item_ids_and_source_text(self) -> None:
        from PySide6.QtWidgets import QCheckBox, QPushButton
        from study_app.ui import study_plan_page

        on_check = Mock()
        on_copy = Mock()
        on_send = Mock()
        item_map = {("short", "plain", "完成图搜索作业"): {"id": 17, "checked": False}}
        card, checkboxes = study_plan_page.plan_section_card(
            "今日作业",
            ["完成图搜索作业"],
            interactive=True,
            section_key="short",
            on_check=on_check,
            on_copy_practice_prompt=on_copy,
            on_send_chatgpt=on_send,
            item_by_hash=item_map,
        )
        self.assertEqual(card.objectName(), "Card")
        self.assertEqual(len(checkboxes), 1)
        checkbox = card.findChild(QCheckBox)
        checkbox.setChecked(True)
        on_check.assert_called_once_with(17, True)
        buttons = {button.text(): button for button in card.findChildren(QPushButton)}
        buttons["复制出题提示词"].click()
        buttons["让 ChatGPT 生成 PDF"].click()
        on_copy.assert_called_once_with("完成图搜索作业")
        on_send.assert_called_once_with("完成图搜索作业")

    def test_homework_result_buttons_record_correctness_with_item_identity(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.ui import study_plan_page

        on_result = Mock()
        line = "当天作业：完成概率论作业，参考难度 72/100"

        def card_for(item, callback=on_result):
            return study_plan_page.plan_section_card(
                "今日作业",
                [line],
                interactive=True,
                section_key="short",
                on_result=callback,
                item_by_hash={("short", "plain", line): item} if item else {},
                result_predicate=study_plan_page.is_plan_homework_item,
            )[0]

        correct_card = card_for({"id": 23, "checked": False})
        correct_buttons = {
            button.text(): button for button in correct_card.findChildren(QPushButton)
        }
        correct_buttons["完成正确"].click()
        on_result.assert_called_once_with(line, True, 23)
        self.assertFalse(correct_buttons["完成正确"].isEnabled())
        self.assertFalse(correct_buttons["完成有误"].isEnabled())

        wrong_card = card_for({"id": 23, "checked": False})
        wrong_buttons = {
            button.text(): button for button in wrong_card.findChildren(QPushButton)
        }
        wrong_buttons["完成有误"].click()
        self.assertEqual(on_result.call_args_list, [call(line, True, 23), call(line, False, 23)])

        failed_result = Mock(return_value=False)
        retry_card = card_for({"id": 23, "checked": False}, failed_result)
        retry_buttons = {
            button.text(): button for button in retry_card.findChildren(QPushButton)
        }
        retry_buttons["完成正确"].click()
        self.assertTrue(retry_buttons["完成正确"].isEnabled())
        self.assertTrue(retry_buttons["完成有误"].isEnabled())

        for result in ("correct", "wrong"):
            with self.subTest(completed_result=result):
                completed_card = card_for(
                    {"id": 23, "checked": True, "result": result}
                )
                completed_buttons = {
                    button.text(): button
                    for button in completed_card.findChildren(QPushButton)
                }
                self.assertFalse(completed_buttons["完成正确"].isEnabled())
                self.assertFalse(completed_buttons["完成有误"].isEnabled())

        ordinary = "复习概率论定义"
        ordinary_card, _ = study_plan_page.plan_section_card(
            "今日任务",
            [ordinary],
            interactive=True,
            section_key="short",
            on_result=on_result,
            item_by_hash={("short", "plain", ordinary): {"id": 24}},
            result_predicate=study_plan_page.is_plan_homework_item,
        )
        missing_item_card = card_for(None)
        for negative_card in (ordinary_card, missing_item_card):
            labels = {
                button.text() for button in negative_card.findChildren(QPushButton)
            }
            self.assertNotIn("完成正确", labels)
            self.assertNotIn("完成有误", labels)

    def test_saved_plan_result_button_writes_record_and_item_state(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.core.study_plan_feedback import PlannedHomeworkScoreResult
        from study_app.ui import study_plan_page

        state = self.state()
        line = "当天作业：完成概率论作业，参考难度 72/100"
        saved = {
            "id": 9,
            "created_at": "2026-07-18 10:00:00",
            "plan": {
                "judgement": [],
                "goals": [],
                "short": [line],
                "diagnostic": [],
                "record_template": [],
                "expected": [],
                "evidence": [],
            },
            "items": [
                {
                    "id": 23,
                    "section_key": "short",
                    "item_type": "result",
                    "item_text": line,
                    "checked": False,
                    "result": None,
                }
            ],
        }
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(study_plan_page, "plan_subject_scope_options", return_value=(("测试占位学科", "测试占位学科"),)),
            patch.object(study_plan_page, "get_active_study_plan", return_value=saved),
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(study_plan_page, "should_refresh_plan_for_model", return_value=False),
            patch.object(study_plan_page, "infer_subject_topic_from_plan_line", return_value=("测试占位学科", "概率论")),
            patch.object(
                study_plan_page,
                "planned_homework_score_result",
                return_value=PlannedHomeworkScoreResult(86.0, "built_in", "模型文件不存在"),
            ),
            patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]),
            patch.object(study_plan_page, "add_learning_record") as add_record,
            patch.object(study_plan_page, "update_study_plan_item_state") as update_item,
            patch.object(study_plan_page, "delete_settings_by_prefix"),
            patch.object(study_plan_page, "get_setting", return_value={}),
            patch.object(study_plan_page, "set_setting") as set_setting,
            patch("PySide6.QtWidgets.QMessageBox.information") as information,
        ):
            page = study_plan_page.study_plan_page(state)
            button = next(
                item for item in page.widget().findChildren(QPushButton) if item.text() == "完成正确"
            )
            button.click()

        record = add_record.call_args.args[0]
        self.assertEqual(record["subject"], "测试占位学科")
        self.assertEqual(record["topic"], "概率论")
        self.assertEqual(record["score"], 86.0)
        self.assertEqual(record["problems"][0]["difficulty_score"], 72.0)
        self.assertEqual(record["problems"][0]["status"], "correct")
        self.assertEqual(set_setting.call_args.args[1]["score_source"], "built_in")
        self.assertEqual(
            set_setting.call_args.args[1]["score_fallback_reason"], "模型文件不存在"
        )
        self.assertEqual(set_setting.call_args.args[1]["result_score"], 86.0)
        self.assertIn("模型文件不存在", information.call_args.args[2])
        self.assertEqual(
            add_record.call_args.kwargs,
            {"study_plan_item_id": 23, "study_plan_result": "correct"},
        )
        update_item.assert_not_called()

    def test_saved_plan_commit_is_not_reported_failed_when_refresh_fails(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.core.study_plan_feedback import PlannedHomeworkScoreResult
        from study_app.ui import study_plan_page

        state = self.state()
        line = "当天作业：完成概率论作业，参考难度 72/100"
        saved = {
            "id": 9,
            "created_at": "2026-07-18 10:00:00",
            "plan": {
                "judgement": [],
                "goals": [],
                "short": [line],
                "diagnostic": [],
                "record_template": [],
                "expected": [],
                "evidence": [],
            },
            "items": [
                {
                    "id": 23,
                    "section_key": "short",
                    "item_type": "result",
                    "item_text": line,
                    "checked": False,
                    "result": None,
                }
            ],
        }
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(
                study_plan_page,
                "plan_subject_scope_options",
                return_value=(("测试占位学科", "测试占位学科"),),
            ),
            patch.object(study_plan_page, "get_active_study_plan", return_value=saved),
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(study_plan_page, "should_refresh_plan_for_model", return_value=False),
            patch.object(
                study_plan_page,
                "infer_subject_topic_from_plan_line",
                return_value=("测试占位学科", "概率论"),
            ),
            patch.object(
                study_plan_page,
                "planned_homework_score_result",
                return_value=PlannedHomeworkScoreResult(86.0, "model_policy"),
            ),
            patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]),
            patch.object(study_plan_page, "add_learning_record") as add_record,
            patch.object(study_plan_page, "get_setting", return_value={}),
            patch.object(study_plan_page, "set_setting"),
            patch.object(
                study_plan_page,
                "delete_settings_by_prefix",
                side_effect=RuntimeError("PRIVATE_REFRESH_DETAIL"),
            ),
            patch("PySide6.QtWidgets.QMessageBox.information") as information,
            patch("PySide6.QtWidgets.QMessageBox.warning") as warning,
            patch("PySide6.QtWidgets.QMessageBox.critical") as critical,
        ):
            page = study_plan_page.study_plan_page(state)
            button = next(
                item
                for item in page.widget().findChildren(QPushButton)
                if item.text() == "完成正确"
            )
            button.click()

        add_record.assert_called_once()
        self.assertFalse(button.isEnabled())
        information.assert_not_called()
        critical.assert_not_called()
        warning.assert_called_once_with(
            page.widget(),
            "记录已保存",
            "学习记录已写入，但页面刷新失败。",
        )
        self.assertNotIn("PRIVATE_REFRESH_DETAIL", str(warning.call_args))

    def test_plan_day_feedback_card_empty_and_rendered_paths(self) -> None:
        from PySide6.QtWidgets import QLabel
        from study_app.ui import study_plan_page

        with patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]):
            self.assertIsNone(study_plan_page.plan_day_feedback_card({}, self.state(), None))
        details = [{"line": "第 1 天完成"}]
        with (
            patch.object(study_plan_page, "plan_day_feedback_details", return_value=details),
            patch.object(study_plan_page, "humanize_plan_text", side_effect=lambda text: f"显示:{text}"),
        ):
            card = study_plan_page.plan_day_feedback_card({}, self.state(), None)
        self.assertEqual(card.objectName(), "Card")
        self.assertIn("显示:第 1 天完成", [label.text() for label in card.findChildren(QLabel)])


if __name__ == "__main__":
    unittest.main()
