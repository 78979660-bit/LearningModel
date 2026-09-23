from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class LeafUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        from study_app.data.database import initialize_database

        cls.app = QApplication.instance() or QApplication([])
        cls._db_tempdir = tempfile.TemporaryDirectory(prefix="leaf_ui_contract_")
        cls.test_db_path = initialize_database(
            Path(cls._db_tempdir.name) / "leaf_ui.sqlite"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                cls._db_tempdir.cleanup()
                return
            except (OSError, PermissionError):
                if time.monotonic() >= deadline:
                    raise
                cls.app.processEvents()
                time.sleep(0.02)

    @staticmethod
    def state():
        from study_app.core.dashboard import DashboardState, SubjectSummary, TodoItem

        subject = SubjectSummary(
            name="计算机科学",
            initial_score=60,
            window_score=72.5,
            has_window_records=True,
            mastery_score=68,
            covered_mastery_score=70,
            covered_topic_count=3,
            total_topic_count=5,
            latest_record=date(2026, 7, 17),
            latest_review=date(2026, 7, 16),
            warnings=("测试预警",),
        )
        todo = TodoItem("复习", "计算机科学 / 图", "复习图搜索", "warning", 10)
        return DashboardState(
            start=date(2026, 7, 15),
            today=date(2026, 7, 17),
            benchmark=60,
            subjects=(subject,),
            todos=(todo,),
            memory_risks=(),
            bkt_alerts=(),
            raw_records=({"id": 1},),
        )

    @classmethod
    def wait_for_ui(cls, predicate, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cls.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("UI result did not arrive before timeout")

    def test_modules_are_lazy_and_facade_exports_are_identical(self) -> None:
        code = (
            "import sys; "
            "from study_app.ui import dashboard_widgets, records_page, query_page; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'study_app.ui.main_window' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        from study_app.ui import dashboard_widgets, main_window, query_page, records_page

        names = (
            "metric_card",
            "subject_card",
            "todo_card",
            "daily_summary_card",
            "simple_page",
            "risk_card",
        )
        for name in names:
            self.assertIs(getattr(main_window, name), getattr(dashboard_widgets, name))
        for name in ("recent_records_card", "records_page"):
            self.assertIs(getattr(main_window, name), getattr(records_page, name))
        self.assertIs(main_window.query_page, query_page.query_page)

    def test_basic_dashboard_widgets_preserve_text_and_object_names(self) -> None:
        from PySide6.QtWidgets import QLabel, QProgressBar
        from study_app.ui.dashboard_widgets import (
            metric_card,
            risk_card,
            simple_page,
            subject_card,
            todo_card,
        )

        state = self.state()
        metric = metric_card("标题", "88", "说明")
        self.assertEqual(metric.objectName(), "Card")
        self.assertEqual([label.text() for label in metric.findChildren(QLabel)], ["标题", "88", "说明"])

        subject = subject_card(state)
        self.assertIn("计算机科学: 72.5", "\n".join(x.text() for x in subject.findChildren(QLabel)))
        self.assertEqual(subject.findChild(QProgressBar).value(), 72)

        todo = todo_card(state)
        self.assertIn("[复习] 计算机科学 / 图", "\n".join(x.text() for x in todo.findChildren(QLabel)))
        simple = simple_page("知识点", "详情")
        self.assertEqual([label.text() for label in simple.findChildren(QLabel)], ["知识点", "详情"])
        self.assertEqual(risk_card("风险", (), "warning").objectName(), "Card")

    def test_daily_summary_cache_and_llm_handler_use_canonical_dependencies(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from study_app.ui import dashboard_widgets

        state = self.state()
        local = {"overview": ["本地概况"], "risks": [], "actions": [], "source": ["本地"]}
        enhanced = {"overview": ["LLM概况"], "risks": [], "actions": [], "source": ["LLM"]}
        with (
            patch("study_app.ai.daily_summary.local_daily_summary", return_value=local),
            patch("study_app.ai.daily_summary.generate_daily_summary_with_llm", return_value=enhanced),
            patch("study_app.ai.providers.is_llm_feature_enabled", return_value=True),
            patch.object(dashboard_widgets, "get_setting", return_value=None),
            patch.object(dashboard_widgets, "set_setting") as set_setting,
            patch.object(dashboard_widgets, "daily_summary_cache_signature", return_value="sig"),
            patch.object(QMessageBox, "information"),
        ):
            card = dashboard_widgets.daily_summary_card(state)
            self.assertTrue(callable(card._daily_summary_handler))
            card._daily_summary_handler()
            self.wait_for_ui(
                lambda: "LLM概况"
                in "\n".join(label.text() for label in card._daily_summary_widgets)
            )

        set_setting.assert_called_once_with(
            "daily_summary_cache:2026-07-17",
            {"mode": "llm", "summary": enhanced, "signature": "sig"},
        )
        self.assertIn("LLM概况", "\n".join(label.text() for label in card._daily_summary_widgets))
        card.close()

    def test_daily_summary_cache_disabled_and_failure_paths(self) -> None:
        from PySide6.QtWidgets import QMessageBox, QPushButton
        from study_app.ui import dashboard_widgets

        state = self.state()
        local = {"overview": ["本地概况"], "risks": [], "actions": [], "source": ["本地"]}
        cached = {"overview": ["缓存概况"], "risks": [], "actions": [], "source": ["缓存"]}
        with (
            patch("study_app.ai.daily_summary.local_daily_summary", return_value=local),
            patch.object(dashboard_widgets, "get_setting", return_value={"cache": True}),
            patch.object(dashboard_widgets, "matching_daily_summary_cache", return_value=(cached, "llm")),
        ):
            card = dashboard_widgets.daily_summary_card(state)
        self.assertIn("缓存概况", "\n".join(label.text() for label in card._daily_summary_widgets))
        self.assertEqual(card.findChild(QPushButton).text(), "重新生成总结")
        card.close()

        for enabled, error in ((False, None), (True, RuntimeError("offline"))):
            with self.subTest(enabled=enabled):
                with (
                    patch("study_app.ai.daily_summary.local_daily_summary", return_value=local),
                    patch(
                        "study_app.ai.daily_summary.generate_daily_summary_with_llm",
                        side_effect=error,
                    ) as generate,
                    patch("study_app.ai.providers.is_llm_feature_enabled", return_value=enabled),
                    patch.object(dashboard_widgets, "get_setting", return_value=None),
                    patch.object(dashboard_widgets, "set_setting") as set_setting,
                    patch.object(QMessageBox, "information") as information,
                    patch.object(QMessageBox, "warning") as warning,
                ):
                    card = dashboard_widgets.daily_summary_card(state)
                    card._daily_summary_handler()
                    if enabled:
                        self.wait_for_ui(
                            lambda: card._daily_summary_lifecycle["handle"] is None
                        )
                self.assertIn("本地概况", "\n".join(label.text() for label in card._daily_summary_widgets))
                set_setting.assert_not_called()
                if enabled:
                    generate.assert_called_once()
                    warning.assert_called_once()
                else:
                    generate.assert_not_called()
                    information.assert_called_once()
                card.close()

    def test_records_page_uses_canonical_lookup_and_callback(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton
        from study_app.ui import records_page

        rows = [{"record_date": "2026-07-17", "subject_name": "计算机科学", "topic_name": "图", "score": 80, "problem_count": 2, "attachments": [], "note": "备注"}]
        callback = Mock()
        with patch.object(records_page, "list_recent_records", return_value=rows) as lookup:
            page = records_page.records_page(callback)
        lookup.assert_called_once_with(limit=10)
        button = next(button for button in page.findChildren(QPushButton) if button.text() == "新增记录")
        button.click()
        callback.assert_called_once_with()
        self.assertIn("计算机科学 / 图", "\n".join(x.text() for x in page.findChildren(QLabel)))

    def test_query_page_local_and_llm_failure_fallback(self) -> None:
        from PySide6.QtWidgets import QLabel, QMessageBox, QTextEdit
        from study_app.ui import query_page

        local = {"answer": ["本地回答"], "evidence": ["证据"], "source": ["本地"]}
        with (
            patch("study_app.ai.natural_query.answer_query_locally", return_value=local),
            patch("study_app.ai.natural_query.answer_query_with_llm", side_effect=RuntimeError("offline")),
            patch("study_app.ai.providers.is_llm_feature_enabled", return_value=True),
            patch.object(QMessageBox, "warning"),
        ):
            page = query_page.query_page(self.state(), db_path=self.test_db_path)
            content = page.widget()
            content.findChild(QTextEdit).setPlainText("最近哪里薄弱？")
            content._query_handler()
            self.wait_for_ui(
                lambda: "LLM 查询失败，已回落本地回答：offline"
                in "\n".join(label.text() for label in content.findChildren(QLabel))
            )

        texts = "\n".join(label.text() for label in content.findChildren(QLabel))
        self.assertIn("本地回答", texts)
        self.assertIn("LLM 查询失败，已回落本地回答：offline", texts)
        page.close()

    def test_query_page_llm_success_and_disabled_local_paths(self) -> None:
        from PySide6.QtWidgets import QLabel, QTextEdit
        from study_app.ui import query_page

        local = {"answer": ["本地回答"], "evidence": ["本地证据"], "source": ["本地"]}
        llm = {"answer": ["LLM回答"], "evidence": ["LLM证据"], "source": ["LLM"]}
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                with (
                    patch("study_app.ai.natural_query.answer_query_locally", return_value=local),
                    patch("study_app.ai.natural_query.answer_query_with_llm", return_value=llm) as ask_llm,
                    patch("study_app.ai.providers.is_llm_feature_enabled", return_value=enabled),
                ):
                    page = query_page.query_page(
                        self.state(), db_path=self.test_db_path
                    )
                    content = page.widget()
                    content.findChild(QTextEdit).setPlainText("最近哪里薄弱？")
                    content._query_handler()
                    if enabled:
                        self.wait_for_ui(
                            lambda: "LLM回答"
                            in "\n".join(label.text() for label in content.findChildren(QLabel))
                        )
                texts = "\n".join(label.text() for label in content.findChildren(QLabel))
                if enabled:
                    ask_llm.assert_called_once()
                    self.assertIn("LLM回答", texts)
                    self.assertNotIn("本地回答", texts)
                else:
                    ask_llm.assert_not_called()
                    self.assertIn("本地回答", texts)
                page.close()


if __name__ == "__main__":
    unittest.main()
