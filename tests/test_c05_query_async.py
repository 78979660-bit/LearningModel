from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTextEdit


def _state():
    return SimpleNamespace(
        raw_records=(), memory_risks=(), bkt_alerts=(), subjects=(),
        benchmark=55.0, todos=(),
    )


def _widgets(page):
    content = page.widget()
    return (
        content,
        content.findChild(QTextEdit, "QueryQuestionInput"),
        content.findChild(QPushButton, "PrimaryButton"),
        content.findChild(QLabel, "ListTitle"),
    )


def _pump_until(application, predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Qt UI did not reach expected state")


def _isolated_db(tmp_path):
    from study_app.data.database import initialize_database

    return initialize_database(tmp_path / "query_async.sqlite")


def test_slow_llm_does_not_block_ui_and_duplicate_click_starts_one_job(monkeypatch, tmp_path):
    from study_app.ai import natural_query, providers
    from study_app.ui import query_page as module

    application = QApplication.instance() or QApplication([])
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow(_question, _state, _records):
        calls.append(threading.get_ident())
        started.set()
        release.wait(1)
        return {"answer": ["LLM 结果"], "evidence": ["依据"], "source": ["LLM"]}

    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _feature: True)
    monkeypatch.setattr(natural_query, "answer_query_with_llm", slow)
    page = module.query_page(_state(), db_path=_isolated_db(tmp_path))
    content, question, button, body = _widgets(page)
    try:
        question.setPlainText("今天应该复习什么？")
        button.click()
        assert started.wait(1)
        button.click()
        application.processEvents()
        assert len(calls) == 1
        assert calls[0] != threading.get_ident()
        assert "本地查询" in body.text()
        question.setPlainText("页面仍可编辑")
        application.processEvents()
        assert question.toPlainText() == "页面仍可编辑"
        release.set()
        _pump_until(application, lambda: "LLM 结果" in body.text())
    finally:
        release.set()
        page.close()
        application.processEvents()


def test_timeout_keeps_local_answer_and_discards_late_llm(monkeypatch, tmp_path):
    from study_app.ai import natural_query, providers
    from study_app.ui import query_page as module

    application = QApplication.instance() or QApplication([])
    release = threading.Event()
    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _feature: True)
    monkeypatch.setattr(module, "QUERY_TASK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(
        natural_query,
        "answer_query_with_llm",
        lambda *_args: (release.wait(1), {
            "answer": ["迟到 LLM"], "evidence": [], "source": []
        })[1],
    )
    page = module.query_page(_state(), db_path=_isolated_db(tmp_path))
    _content, question, button, body = _widgets(page)
    try:
        question.setPlainText("今天复习什么？")
        button.click()
        _pump_until(application, lambda: "LLM 查询超时" in body.text())
        assert "本地查询" in body.text()
        release.set()
        time.sleep(0.04)
        application.processEvents()
        assert "迟到 LLM" not in body.text()
    finally:
        release.set()
        page.close()
        application.processEvents()


def test_page_close_invalidates_inflight_job_and_never_updates_controls(monkeypatch, tmp_path):
    from study_app.ai import natural_query, providers
    from study_app.ui import query_page as module

    application = QApplication.instance() or QApplication([])
    started = threading.Event()
    release = threading.Event()

    def slow(*_args):
        started.set()
        release.wait(1)
        return {"answer": ["关闭后结果"], "evidence": [], "source": []}

    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _feature: True)
    monkeypatch.setattr(natural_query, "answer_query_with_llm", slow)
    page = module.query_page(_state(), db_path=_isolated_db(tmp_path))
    content, question, button, body = _widgets(page)
    try:
        question.setPlainText("今日计划？")
        button.click()
        assert started.wait(1)
        local_text = body.text()
        page.close()
        assert not content._query_lifecycle["open"]
        release.set()
        time.sleep(0.04)
        application.processEvents()
        assert body.text() == local_text
        assert "关闭后结果" not in body.text()
    finally:
        release.set()
        page.close()
        application.processEvents()


def test_new_question_discards_older_llm_answer(monkeypatch, tmp_path):
    from study_app.ai import natural_query, providers
    from study_app.ui import query_page as module

    application = QApplication.instance() or QApplication([])
    old_started = threading.Event()
    old_release = threading.Event()

    def answer(question, *_args):
        if question == "旧问题":
            old_started.set()
            old_release.wait(1)
            value = "旧 LLM 答案"
        else:
            value = "新 LLM 答案"
        return {"answer": [value], "evidence": [], "source": []}

    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _feature: True)
    monkeypatch.setattr(natural_query, "answer_query_with_llm", answer)
    page = module.query_page(_state(), db_path=_isolated_db(tmp_path))
    _content, question, button, body = _widgets(page)
    try:
        question.setPlainText("旧问题")
        button.click()
        assert old_started.wait(1)
        question.setPlainText("新问题")
        button.click()
        _pump_until(application, lambda: "新 LLM 答案" in body.text())
        old_release.set()
        time.sleep(0.04)
        application.processEvents()
        assert "旧 LLM 答案" not in body.text()
    finally:
        old_release.set()
        page.close()
        application.processEvents()
