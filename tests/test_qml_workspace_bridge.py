from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from study_app.core.async_tasks import AsyncTaskService
from study_app.quick.self_test import wait_until
from study_app.quick.workspace_bridge import WorkspaceBridge


class Plan:
    def __init__(self):
        self.saved = 0
        self.fail_reload = False

    def load(self, scope=None):
        if self.fail_reload and self.saved:
            raise RuntimeError("private reload path C:\\secret\\db.sqlite")
        return {"catalog": {"subjects": [{"name": "隔离学科"}]}, "scope": scope}

    def preview_budget(self, payload):
        return {"token": "reviewed-budget", "summary": payload}

    def confirm_budget(self, token):
        assert token == "reviewed-budget"
        self.saved += 1
        return {"saved": True}


class Other:
    def load(self):
        return {}

    def request_cancel(self):
        pass


@pytest.fixture
def workspace():
    app = QApplication.instance() or QApplication(["workspace-bridge-tests"])
    tasks = AsyncTaskService(max_workers=1)
    plan = Plan()
    bridge = WorkspaceBridge(plan, Other(), Other(), Other(), tasks)
    yield app, bridge, plan, tasks
    bridge.close()
    tasks.wait_for_shutdown()


def finish(bridge):
    wait_until(lambda: not bridge.busy, timeout_ms=3000)


def test_timer_connection_preserves_static_metaobject_during_load(workspace):
    _, bridge, _, _ = workspace
    static = type(bridge).staticMetaObject
    assert bridge.metaObject().methodCount() == static.methodCount()
    assert bridge.metaObject().propertyCount() == static.propertyCount()
    assert static.indexOfSlot("_poll()") >= 0
    bridge.run("load", {"scope": "隔离学科"})
    finish(bridge)
    assert bridge.planData["scope"] == "隔离学科"
    assert bridge.metaObject().methodCount() == static.methodCount()
    assert bridge.metaObject().propertyCount() == static.propertyCount()


def test_refresh_preserves_scope_until_user_explicitly_selects_all(workspace):
    _, bridge, _, _ = workspace
    bridge.run("load", {"scope": "隔离学科"})
    finish(bridge)
    assert bridge.planData["scope"] == "隔离学科"
    bridge.run("load", {})
    finish(bridge)
    assert bridge.planData["scope"] == "隔离学科"
    bridge.run("load", {"scope": None})
    finish(bridge)
    assert bridge.planData["scope"] is None


def test_worker_keeps_gui_responsive_and_discards_changed_preview(workspace):
    _, bridge, plan, _ = workspace
    entered, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
    gui_thread = threading.get_ident()
    caller_threads = []

    def slow_preview(payload):
        caller_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return {"token": "old-input", "summary": payload}

    plan.preview_budget = slow_preview
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(heartbeat.set)
    try:
        bridge.run("budget.preview", {"budget_minutes": 90})
        assert entered.wait(2)
        timer.start(10)
        wait_until(heartbeat.is_set, timeout_ms=500)
        bridge.invalidatePreview("budget")
        release.set()
        finish(bridge)
        assert bridge.budgetPreview == {}
        assert "旧预览已丢弃" in bridge.message
        assert caller_threads == [caller_threads[0]] and caller_threads[0] != gui_thread
    finally:
        release.set()


def test_post_commit_reload_failure_reports_saved_and_prevents_replay(workspace):
    _, bridge, plan, _ = workspace
    wrote = []
    bridge.dataWritten.connect(lambda: wrote.append(True))
    bridge.run("budget.preview", {"budget_minutes": 90})
    finish(bridge)
    plan.fail_reload = True
    bridge.run("budget.confirm", {"token": bridge.budgetPreview["token"]})
    finish(bridge)
    assert plan.saved == 1 and wrote == [True]
    assert "已保存" in bridge.message and "刷新失败" in bridge.message
    assert bridge.error == "" and bridge.budgetPreview == {}
    bridge.run("budget.confirm", {"token": "reviewed-budget"})
    assert not bridge.busy and "失效" in bridge.error
    assert plan.saved == 1


@pytest.mark.parametrize("action", ["budget.confirm", "plan.confirm", "plan.feedback"])
def test_service_post_commit_warning_survives_successful_bridge_reload(workspace, action):
    _, bridge, plan, _ = workspace
    wrote = []
    bridge.dataWritten.connect(lambda: wrote.append(True))

    def committed(*args, **kwargs):
        plan.saved += 1
        return {"saved": True, "refreshWarning": "已保存；后续处理失败。"}

    if action == "budget.confirm":
        plan.confirm_budget = committed
        bridge.run("budget.preview", {"budget_minutes": 90})
        finish(bridge)
        payload = {"token": bridge.budgetPreview["token"]}
    elif action == "plan.confirm":
        plan.preview_plan = lambda payload: {"token": "reviewed-plan"}
        plan.confirm_plan = committed
        bridge.run("plan.preview", {})
        finish(bridge)
        payload = {"token": bridge.planPreview["token"]}
    else:
        plan.feedback = committed
        payload = {"id": 1, "correct": True}
    bridge.run(action, payload)
    finish(bridge)
    assert plan.saved == 1 and wrote == [True]
    assert bridge.error == ""
    assert "已保存" in bridge.message and "勿重复提交" in bridge.message
    assert bridge.planData["catalog"]["subjects"]  # Reload actually succeeded.
    assert bridge.lastResult["saved"] is True
    assert not bridge.budgetPreview and not bridge.planPreview


def test_pre_commit_failure_is_not_reported_as_saved(workspace):
    _, bridge, plan, _ = workspace
    wrote = []
    bridge.dataWritten.connect(lambda: wrote.append(True))

    def failed(token):
        raise RuntimeError("private commit failure")

    plan.confirm_budget = failed
    bridge.run("budget.preview", {"budget_minutes": 90})
    finish(bridge)
    bridge.run("budget.confirm", {"token": bridge.budgetPreview["token"]})
    finish(bridge)
    assert plan.saved == 0 and wrote == []
    assert "操作失败" in bridge.error and "private" not in bridge.error
    assert "已保存" not in bridge.message and not bridge.lastResult


def test_cancel_keeps_busy_until_worker_releases_its_resources(workspace):
    app, bridge, plan, _ = workspace
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_preview(payload):
        entered.set()
        assert release.wait(3)
        finished.set()
        return {"token": "cancelled-preview"}

    plan.preview_budget = slow_preview
    try:
        bridge.run("budget.preview", {"budget_minutes": 90})
        assert entered.wait(2)
        bridge.cancel()
        for _ in range(10):
            app.processEvents()
            bridge._poll()
        assert bridge.busy and not finished.is_set()
        release.set()
        finish(bridge)
        assert finished.is_set() and not bridge.budgetPreview
        assert "已停止" in bridge.message
    finally:
        release.set()


def test_unknown_actions_and_failure_details_never_reach_qml(workspace):
    _, bridge, plan, _ = workspace
    bridge.run("execute-sql", {"sql": "DELETE FROM records"})
    assert not bridge.busy and bridge.error == "未支持的操作。"

    def failure(payload):
        raise RuntimeError("Authorization: Bearer private-key C:\\private\\db.sqlite")

    plan.preview_budget = failure
    bridge.run("budget.preview", {"budget_minutes": 90})
    finish(bridge)
    assert "操作失败" in bridge.error
    assert "private" not in bridge.error and "Bearer" not in bridge.error


def test_close_prevents_result_delivery_and_additional_dispatch(workspace):
    _, bridge, plan, tasks = workspace
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_preview(payload):
        entered.set()
        assert release.wait(3)
        finished.set()
        return {"token": "late-preview"}

    plan.preview_budget = slow_preview
    try:
        bridge.run("budget.preview", {"budget_minutes": 90})
        assert entered.wait(2)
        bridge.close()
        bridge.run("budget.preview", {"budget_minutes": 90})
        release.set()
        tasks.wait_for_shutdown()
        bridge._poll()
        assert finished.is_set() and bridge.budgetPreview == {} and not bridge.busy
    finally:
        release.set()
