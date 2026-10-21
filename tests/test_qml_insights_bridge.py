from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from study_app.core.async_tasks import AsyncTaskService
from study_app.quick.insights_bridge import InsightsBridge
from study_app.quick.self_test import wait_until


class Assistant:
    def __init__(self):
        self.saved = 0
        self.fail_reload = False
        self.paths = []

    def load(self):
        if self.fail_reload and self.saved:
            raise RuntimeError("secret api-key /private/data.sqlite")
        return {"journal": [], "policyActions": []}

    def ask(self, text, paths, external=False):
        return {"answer": {"local": [text]}, "external_used": external}

    def preview(self, text, paths):
        self.paths = paths
        return {"token": "reviewed-proposal", "summary": text}

    def confirm(self, token, execution_mode="user"):
        assert token == "reviewed-proposal"
        self.saved += 1
        return {"saved": True, "status": "completed", "executionMode": execution_mode}

    def undo_preview(self):
        return {"token": "undo-reviewed", "review": "record to undo"}

    def undo(self, token):
        assert token == "undo-reviewed"
        return {"saved": True, "status": "undone"}


class Knowledge:
    def __init__(self):
        self.subjects = []
        self.handled = []

    def load(self, subject=""):
        self.subjects.append(subject)
        return {"subjects": [], "graph": {"nodes": []}, "alerts": []}

    def handle(self, alert_id):
        self.handled.append(alert_id)
        return {"saved": True}


@pytest.fixture
def insights():
    app = QApplication.instance() or QApplication(["insights-tests"])
    tasks = AsyncTaskService(max_workers=1)
    assistant, knowledge = Assistant(), Knowledge()
    bridge = InsightsBridge(assistant, knowledge, tasks)
    yield app, bridge, assistant, knowledge, tasks
    bridge.close()
    tasks.close()
    tasks.wait_for_shutdown()


def finish(bridge):
    wait_until(lambda: not bridge.busy, timeout_ms=3000)


def test_static_metaobject_and_worker_loaded_data(insights):
    _, bridge, _, _, _ = insights
    static = type(bridge).staticMetaObject
    assert static.indexOfSlot("_poll()") >= 0
    assert static.indexOfSlot("leave_page()") >= 0
    assert bridge.metaObject().methodCount() == static.methodCount()
    bridge.run("assistant.load", {})
    finish(bridge)
    assert "journal" in bridge.assistantData
    assert bridge.metaObject().methodCount() == static.methodCount()


def test_exact_review_token_and_explicit_confirmation_required(insights):
    _, bridge, assistant, _, _ = insights
    bridge.run("assistant.confirm", {"token": "forged", "confirmed": True})
    assert bridge.error and not assistant.saved
    bridge.run("assistant.preview", {"text": "intent", "attachments": ["file:///C:/test/file.txt"]})
    finish(bridge)
    assert assistant.paths == ["C:/test/file.txt"]
    token = bridge.proposalPreview["token"]
    bridge.run("assistant.confirm", {"token": token})
    finish(bridge)
    assert bridge.error and not assistant.saved
    assert not bridge.proposalPreview
    bridge.run("assistant.preview", {"text": "intent"})
    finish(bridge)
    bridge.run("assistant.confirm", {"token": bridge.proposalPreview["token"], "confirmed": True})
    finish(bridge)
    assert assistant.saved == 1 and not bridge.error
    assert not bridge.proposalPreview
    bridge.run("assistant.confirm", {"token": token, "confirmed": True})
    assert assistant.saved == 1


def test_commit_refresh_failure_reports_saved_without_secrets(insights):
    _, bridge, assistant, _, _ = insights
    assistant.fail_reload = True
    bridge.run("assistant.preview", {"text": "intent"})
    finish(bridge)
    bridge.run("assistant.confirm", {"token": bridge.proposalPreview["token"], "confirmed": True})
    finish(bridge)
    assert assistant.saved == 1 and bridge.receipt["saved"]
    assert not bridge.error
    assert "刷新失败" in bridge.message and "勿重复提交" in bridge.message
    assert "secret" not in bridge.message


def test_slow_preview_gui_heartbeat_leaving_discards_and_queues_next_route(insights):
    _, bridge, assistant, knowledge, _ = insights
    started, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
    gui_thread = threading.get_ident()
    caller_threads = []

    def slow(text, paths):
        caller_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return {"token": "old-preview"}

    assistant.preview = slow
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(heartbeat.set)
    try:
        bridge.run("assistant.preview", {"text": "intent"})
        assert started.wait(2)
        timer.start(10)
        wait_until(heartbeat.is_set, timeout_ms=1000)
        bridge.leave_page()
        bridge.run("knowledge.load", {"subjectKey": "real-subject"})
        release.set()
        finish(bridge)
        assert not bridge.proposalPreview
        assert knowledge.subjects == ["real-subject"]
        assert caller_threads == [caller_threads[0]] and caller_threads[0] != gui_thread
    finally:
        release.set()


def test_cancel_waits_until_worker_exits_and_rejects_new_write(insights):
    _, bridge, assistant, _, _ = insights
    started, release = threading.Event(), threading.Event()

    def slow(text, paths, external=False):
        started.set()
        assert release.wait(3)
        return {"answer": {"local": ["late result"]}}

    assistant.ask = slow
    try:
        bridge.run("assistant.ask", {"text": "query"})
        assert started.wait(2)
        bridge.cancel()
        QApplication.instance().processEvents()
        assert bridge.busy
        bridge.run("assistant.confirm", {"token": "forged", "confirmed": True})
        assert not assistant.saved
        release.set()
        finish(bridge)
        assert not bridge.answer
    finally:
        release.set()


def test_knowledge_scope_refresh_and_strict_ids(insights):
    _, bridge, _, knowledge, _ = insights
    bridge.run("knowledge.load", {"subjectKey": "selected"})
    finish(bridge)
    bridge.run("knowledge.load", {})
    finish(bridge)
    assert knowledge.subjects == ["selected", "selected"]
    bridge.run("alert.handle", {"alertId": 1.5, "confirmed": True})
    finish(bridge)
    assert not knowledge.handled and bridge.error
    bridge.run("alert.handle", {"alertId": 1.0, "confirmed": True})
    finish(bridge)
    assert knowledge.handled == [1] and not bridge.error


def test_edit_invalidates_pending_preview_and_undo_token(insights):
    _, bridge, _, _, _ = insights
    bridge.run("assistant.undoPreview", {})
    finish(bridge)
    assert bridge.undoPreview["token"]
    bridge.leave_page()
    bridge.run("assistant.undo", {"token": "undo-reviewed", "confirmed": True})
    assert bridge.error and not bridge.receipt


def test_external_flag_requires_boolean_and_errors_do_not_leak(insights):
    _, bridge, _, _, _ = insights
    bridge.run("assistant.ask", {"text": "query", "external": "true"})
    finish(bridge)
    assert bridge.error and not bridge.answer
    bridge.run("arbitrary.sql", {})
    assert bridge.error and not bridge.busy
    bridge.close()
    bridge.run("assistant.ask", {"text": "query"})
    assert not bridge.busy


def test_chat_retains_ordered_turns_across_page_reads_and_does_not_replay_requests(insights):
    _, bridge, assistant, _, _ = insights
    for text in ("先复习什么", "离散数学呢"):
        bridge.run("assistant.ask", {"text": text})
        assert bridge.conversation[-1]["pending"]
        bridge.run("assistant.ask", {"text": "重复按键"})
        finish(bridge)
    messages = bridge.conversation
    assert [item["role"] for item in messages] == ["user", "assistant", "user", "assistant"]
    assert [item["text"] for item in messages if item["role"] == "user"] == ["先复习什么", "离散数学呢"]
    assert all(not item.get("pending") for item in messages)
    bridge.leave_page()
    bridge.run("assistant.load", {})
    finish(bridge)
    assert bridge.conversation == messages
    # Consumers cannot mutate the bridge's stored transcript.
    messages[0]["text"] = "changed"
    assert bridge.conversation[0]["text"] == "先复习什么"


def test_chat_failure_finishes_pending_bubble_without_leaking_exception(insights):
    _, bridge, assistant, _, _ = insights
    def fail(*args, **kwargs):
        raise RuntimeError("secret-api-key /private/data.sqlite")
    assistant.ask = fail
    bridge.run("assistant.ask", {"text": "需要加强哪里"})
    finish(bridge)
    failed = bridge.conversation[-1]
    assert failed["failed"] and not failed["pending"]
    assert failed["question"] == "需要加强哪里"
    assert "secret" not in str(failed) and "private" not in str(failed)
    assistant.ask = lambda text, paths, external=False: {"answer": {"answer": ["先复习熟悉的内容"]}}
    bridge.run("assistant.ask", {"text": failed["question"]})
    finish(bridge)
    assert not bridge.conversation[-1]["failed"]
