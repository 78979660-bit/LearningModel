from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QLabel, QPushButton, QScrollArea, QSystemTrayIcon,
    QTextEdit, QVBoxLayout, QWidget,
)

from study_app.core.dashboard import DashboardState


def _state(today: date):
    return DashboardState(
        start=today, today=today, benchmark=60,
        subjects=(), todos=(), memory_risks=(), bkt_alerts=(), raw_records=(),
    )


def _scroll_page() -> QScrollArea:
    page = QScrollArea()
    page.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    text = QTextEdit()
    text.setObjectName("UnsavedInput")
    layout.addWidget(text)
    page.setWidget(content)
    return page


def test_refresh_preserves_page_identity_scroll_unsaved_input_and_nav_signal(monkeypatch):
    from study_app.ui import window_shell

    application = QApplication.instance() or QApplication([])
    activity = SimpleNamespace(
        counts={"todos": 0, "memory_risks": 0, "bkt_alerts": 0, "low_subjects": 0},
        todos=[], memory_risks=[], bkt_alerts=[],
    )
    closed_cards = []

    class DynamicCard(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumHeight(220)
            self.closed = False

        def closeEvent(self, event):
            self.closed = True
            closed_cards.append(self)
            super().closeEvent(event)

    def metric(_title, value, _detail):
        card = QWidget()
        layout = QVBoxLayout(card)
        label = QLabel(value)
        label.setObjectName("MetricValue")
        layout.addWidget(label)
        return card

    monkeypatch.setattr(window_shell, "load_llm_settings", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr(window_shell, "filter_homepage_activity", lambda _state: activity)
    monkeypatch.setattr(window_shell, "metric_card", metric)
    for name in ("subject_card", "daily_summary_card", "todo_card", "risk_card"):
        monkeypatch.setattr(window_shell, name, lambda *_args: DynamicCard())
    for name in (
        "study_plan_page", "query_page", "knowledge_page", "settings_page",
    ):
        monkeypatch.setattr(window_shell, name, lambda *_args, **_kwargs: _scroll_page())
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)

    window = window_shell.MainWindow(_state(date(2026, 9, 15)))
    window.show()
    application.processEvents()
    central = window.centralWidget()
    stack = window.stack
    pages = [stack.widget(index) for index in range(stack.count())]
    plan_input = pages[1].findChild(QTextEdit, "UnsavedInput")
    query_input = pages[2].findChild(QTextEdit, "UnsavedInput")
    plan_input.setPlainText("尚未提交的计划内容")
    query_input.setPlainText("尚未提交的问题")
    for toggle in pages[0].findChildren(QPushButton, "DisclosureButton"):
        toggle.setChecked(True)
    application.processEvents()
    scroll_bar = pages[0].verticalScrollBar()
    assert scroll_bar.maximum() > 0
    scroll_bar.setValue(min(120, scroll_bar.maximum()))
    old_scroll = scroll_bar.value()
    original_dynamic = list(window._home_dynamic_cards)

    monkeypatch.setattr(
        window_shell, "load_dashboard_state", lambda: _state(date(2026, 9, 16))
    )
    try:
        for _ in range(3):
            window.refresh_dashboard()
            application.processEvents()
        assert window.centralWidget() is central
        assert window.stack is stack
        assert all(stack.widget(index) is page for index, page in enumerate(pages))
        assert plan_input.toPlainText() == "尚未提交的计划内容"
        assert query_input.toPlainText() == "尚未提交的问题"
        assert scroll_bar.value() == old_scroll, (scroll_bar.maximum(), old_scroll)
        assert all(getattr(card, "body", card).closed for card in original_dynamic)

        calls = []
        original_switch = window.switch_page

        def counted(index):
            calls.append(index)
            original_switch(index)

        window.switch_page = counted
        navigation = next(
            button for button in window.findChildren(QPushButton)
            if button.objectName() == "NavButton" and button.text() == "学习助理"
        )
        navigation.click()
        assert calls == [2]
        assert stack.currentIndex() == 2
    finally:
        window.deleteLater()
        application.processEvents()


def test_records_page_updates_card_in_place_and_keeps_scroll_instance(monkeypatch):
    from study_app.ui import records_page

    application = QApplication.instance() or QApplication([])
    rows = [
        {"id": 1, "record_date": "2026-09-15", "subject_name": "测试学科",
         "topic_name": "旧主题", "module_name": "", "activity": "review",
         "source": "outside_class", "score": None, "note": "", "attachments": [],
         "problem_count": 0},
    ]
    monkeypatch.setattr(records_page, "list_recent_records", lambda **_kwargs: list(rows))
    page = records_page.records_page(lambda: None)
    assert "旧主题" in "\n".join(label.text() for label in page.findChildren(QLabel))
    identity = page
    rows[0] = {**rows[0], "topic_name": "新主题"}
    page._refresh_records()
    application.processEvents()
    assert page is identity
    assert "新主题" in "\n".join(label.text() for label in page.findChildren(QLabel))
    page.close()


def test_query_page_uses_refreshed_state_without_erasing_question(monkeypatch, tmp_path):
    from study_app.ai import natural_query, providers
    from study_app.ui import query_page

    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _feature: False)
    monkeypatch.setattr(
        natural_query,
        "answer_query_locally",
        lambda _question, state, _records: {
            "answer": [state.today.isoformat()], "evidence": [], "source": ["本地"]
        },
    )
    from study_app.data.database import initialize_database

    db_path = initialize_database(tmp_path / "query_page_lifecycle.sqlite")
    page = query_page.query_page(_state(date(2026, 9, 15)), db_path=db_path)
    question = page.findChild(QTextEdit, "QueryQuestionInput")
    answer = page.findChild(QLabel, "ListTitle")
    question.setPlainText("保留这个问题")
    page._set_dashboard_state(_state(date(2026, 9, 16)))
    assert question.toPlainText() == "保留这个问题"
    page.widget()._query_handler()
    application.processEvents()
    assert "2026-09-16" in answer.text()
    page.close()
