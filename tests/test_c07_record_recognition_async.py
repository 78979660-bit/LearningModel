from __future__ import annotations

import os
import threading
import time
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox


def _editor(monkeypatch, callback=None):
    from study_app.data import database
    from study_app.ui import record_editor

    monkeypatch.setattr(database, "list_module_names", lambda *_args: ["测试模块"])
    editor = record_editor.AddRecordDialog(None, callback, ("测试学科",))
    editor.infer_problem_fields = lambda _problem: None
    editor.estimate_record_score_from_problems = lambda: 75.0
    return editor


def _pump_until(application, predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("record recognition UI did not reach expected state")


def test_slow_recognition_is_off_ui_thread_deduplicated_and_save_waits(monkeypatch):
    from study_app.ai import providers, record_parser

    application = QApplication.instance() or QApplication([])
    started = threading.Event()
    release = threading.Event()
    calls = []
    saved = Mock()

    def slow(_payload):
        calls.append(threading.get_ident())
        started.set()
        release.wait(1)
        return [{"title": "LLM题", "status": "correct", "correctness": 100}]

    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _feature: True)
    monkeypatch.setattr(record_parser, "parse_record_payload_with_llm", slow)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    editor = _editor(monkeypatch, saved)
    try:
        editor.topic_input.setText("主题")
        editor.note_input.setPlainText("人工备注")
        editor.problem_result_input.setPlainText("第1题全对")
        editor.score_input.setText("92")
        editor.recognize_problems()
        assert started.wait(1)
        editor.recognize_problems()
        editor.save_record()
        assert len(calls) == 1 and calls[0] != threading.get_ident()
        saved.assert_not_called()
        marker = []
        QTimer.singleShot(0, lambda: marker.append("responsive"))
        application.processEvents()
        assert marker == ["responsive"]
        assert editor.note_input.toPlainText() == "人工备注"
        release.set()
        _pump_until(application, lambda: editor._recognition_handle is None)
        assert editor.problems[0]["title"] == "LLM题"
        assert editor.score_input.text() == "92"
        assert editor.note_input.toPlainText() == "人工备注"
    finally:
        release.set()
        editor.close()
        application.processEvents()


def test_timeout_uses_local_rules_without_erasing_manual_fields(monkeypatch):
    from study_app.ai import record_parser
    from study_app.ui import record_editor

    application = QApplication.instance() or QApplication([])
    release = threading.Event()
    monkeypatch.setattr(record_editor, "RECORD_RECOGNITION_TASK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(
        record_parser,
        "parse_record_payload_with_llm",
        lambda _payload: (release.wait(1), [{"title": "迟到题"}])[1],
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    editor = _editor(monkeypatch)
    try:
        editor.topic_input.setText("人工主题")
        editor.note_input.setPlainText("人工备注")
        editor.problem_result_input.setPlainText("第1题做错了")
        editor.score_input.setText("81")
        assert editor.recognize_problems_with_llm()
        _pump_until(application, lambda: editor._recognition_handle is None)
        assert editor.problems
        assert editor.problems[0]["title"] != "迟到题"
        assert editor.topic_input.text() == "人工主题"
        assert editor.note_input.toPlainText() == "人工备注"
        assert editor.score_input.text() == "81"
        release.set()
        time.sleep(0.04)
        application.processEvents()
        assert editor.problems[0]["title"] != "迟到题"
    finally:
        release.set()
        editor.close()
        application.processEvents()


def test_close_and_changed_input_discard_inflight_result(monkeypatch):
    from study_app.ai import record_parser

    application = QApplication.instance() or QApplication([])
    started = threading.Event()
    release = threading.Event()

    def slow(_payload):
        started.set()
        release.wait(1)
        return [{"title": "迟到 LLM 题"}]

    monkeypatch.setattr(record_parser, "parse_record_payload_with_llm", slow)
    editor = _editor(monkeypatch)
    try:
        editor.note_input.setPlainText("初始备注")
        editor.problems = [{"title": "人工题", "status": "correct"}]
        assert editor.recognize_problems_with_llm()
        assert started.wait(1)
        editor.note_input.setPlainText("新的人工备注")
        release.set()
        _pump_until(application, lambda: editor._recognition_handle is None)
        assert editor.problems == [{"title": "人工题", "status": "correct"}]
        assert editor.note_input.toPlainText() == "新的人工备注"
    finally:
        release.set()
        editor.close()

    started.clear()
    release.clear()
    second = _editor(monkeypatch)
    try:
        second.problems = [{"title": "人工题", "status": "correct"}]
        assert second.recognize_problems_with_llm()
        assert started.wait(1)
        second.close()
        release.set()
        time.sleep(0.04)
        application.processEvents()
        assert second.problems == [{"title": "人工题", "status": "correct"}]
        assert second.problem_list.count() == 0
    finally:
        release.set()
        second.close()
        application.processEvents()
