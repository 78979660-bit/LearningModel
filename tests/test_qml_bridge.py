from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["qml-bridge-tests"])


def _snapshot(record_count: int = 0) -> dict:
    return {
        "date": "2026-10-04",
        "rangeLabel": "隔离测试周期",
        "source": "SQLite",
        "sourceReason": "",
        "catalogDiagnostic": "",
        "activeCount": 1,
        "archivedCount": 0,
        "recordCount": record_count,
        "metrics": [],
        "subjects": [],
        "todos": [],
        "memoryRisks": [],
        "bktAlerts": [],
        "todayPlan": {"status": "empty", "title": "暂无今日计划", "detail": "", "items": []},
        "recentRecords": [],
    }


def _wait_for(qapp, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return bool(predicate())


def _bridge_type():
    from study_app.quick.bridge import DashboardBridge

    return DashboardBridge


class _ImmediateService:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.lock = threading.Lock()

    def load(self):
        with self.lock:
            self.calls += 1
            call_number = self.calls
        outcome = self.outcomes.pop(0) if self.outcomes else _snapshot(call_number)
        if isinstance(outcome, BaseException):
            raise outcome
        return copy.deepcopy(outcome)


def test_routes_sidebar_and_invalid_route_are_explicit(qapp):
    bridge = _bridge_type()(_ImmediateService())
    try:
        route_rows = bridge.routes
        route_by_key = {row["key"]: row for row in route_rows}
        assert bridge.currentPage == "home"
        assert route_by_key["home"]["migrated"] is True
        assert route_by_key["plan"]["migrated"] is True
        assert route_by_key["assistant"]["migrated"] is True
        assert route_by_key["knowledge"]["migrated"] is True
        assert all(
            route["migrated"] is True
            for key, route in route_by_key.items()
            if key not in {"home", "plan", "assistant", "knowledge"}
        )

        bridge.navigate("plan")
        assert bridge.currentPage == "plan"
        bridge.navigate("not-a-real-page")
        assert bridge.currentPage == "plan"

        initial_collapsed = bridge.sidebarCollapsed
        bridge.toggleSidebar()
        assert bridge.sidebarCollapsed is not initial_collapsed
        bridge.toggleSidebar()
        assert bridge.sidebarCollapsed is initial_collapsed
    finally:
        bridge.close()


def test_timer_connection_preserves_static_metaobject_during_refresh(qapp):
    bridge = _bridge_type()(_ImmediateService(_snapshot(1)))
    static = type(bridge).staticMetaObject
    try:
        assert bridge.metaObject().methodCount() == static.methodCount()
        assert bridge.metaObject().propertyCount() == static.propertyCount()
        assert static.indexOfSlot("_poll()") >= 0
        bridge.refresh()
        assert _wait_for(qapp, lambda: bridge.hasData and not bridge.busy)
        assert bridge.snapshot["recordCount"] == 1
        assert bridge.metaObject().methodCount() == static.methodCount()
        assert bridge.metaObject().propertyCount() == static.propertyCount()
    finally:
        bridge.close()
        bridge.wait_for_shutdown()


def test_qml_property_cache_survives_cross_bridge_lifetimes():
    # An isolated child keeps the global QML cache and allocator independent of
    # the test host. The safe sentinel detects the original busy -> map getter
    # misrouting without letting a converter write a map into a bool stack slot.
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-B", "tools/qml_diagnostics/metaobject_cache_probe.py", "--cycles", "400"],
        cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["cycles"] == 400
    assert report["wrong_getters"] == []
    assert report["cross_class_meta_address_reuse"] == 0
    assert result.stderr == ""


def test_slow_load_runs_off_gui_thread_keeps_events_responsive_and_coalesces_refreshes(
    qapp,
):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    caller_threads = []

    class BlockingService:
        def load(self):
            calls.append(len(calls) + 1)
            call_number = calls[-1]
            caller_threads.append(threading.get_ident())
            if call_number == 1:
                entered.set()
                assert release.wait(3), "test did not release the slow dashboard read"
            return _snapshot(call_number)

    bridge = _bridge_type()(BlockingService())
    gui_thread = threading.get_ident()
    try:
        bridge.refresh()
        assert bridge.busy is True
        assert entered.wait(2), "dashboard load did not start in the worker"

        for _ in range(5):
            bridge.refresh()

        heartbeat = threading.Event()
        from PySide6.QtCore import QTimer

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(heartbeat.set)
        timer.start(10)
        assert _wait_for(qapp, heartbeat.is_set, timeout=0.5)
        assert calls == [1]

        release.set()
        assert _wait_for(
            qapp,
            lambda: len(calls) == 2 and not bridge.busy and bridge.hasData,
        )
        assert bridge.snapshot["recordCount"] == 2
        assert caller_threads
        assert all(thread_id != gui_thread for thread_id in caller_threads)
    finally:
        release.set()
        bridge.close()


def test_failed_refresh_keeps_last_snapshot_and_hides_exception_details(qapp):
    secret_detail = "secret-key=do-not-leak C:\\private\\records.sqlite"
    service = _ImmediateService(
        _snapshot(1), RuntimeError(secret_detail)
    )
    bridge = _bridge_type()(service)
    try:
        bridge.refresh()
        assert _wait_for(qapp, lambda: bridge.hasData and not bridge.busy)
        previous = copy.deepcopy(bridge.snapshot)

        bridge.refresh()
        assert _wait_for(qapp, lambda: bool(bridge.error) and not bridge.busy)

        assert bridge.hasData is True
        assert bridge.snapshot == previous
        assert secret_detail not in bridge.error
        assert "secret-key" not in bridge.error
        assert "records.sqlite" not in bridge.error
    finally:
        bridge.close()


def test_close_prevents_later_refresh_from_submitting_work(qapp):
    service = _ImmediateService(_snapshot(1))
    bridge = _bridge_type()(service)
    bridge.refresh()
    assert _wait_for(qapp, lambda: bridge.hasData and not bridge.busy)
    submitted_before_close = service.calls

    bridge.close()
    bridge.refresh()
    qapp.processEvents()

    assert service.calls == submitted_before_close
    assert bridge.busy is False


def test_submit_start_failure_restores_idle_state_and_allows_retry(qapp, monkeypatch):
    service = _ImmediateService(_snapshot(1))
    bridge = _bridge_type()(service)
    real_submit = bridge._tasks.submit

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("secret path C:\\private\\queue.sqlite")

    try:
        monkeypatch.setattr(bridge._tasks, "submit", fail_submit)
        bridge.refresh()

        assert bridge.busy is False
        assert bridge.error == "读取任务未能启动，请重试。"
        assert "private" not in bridge.error
        assert service.calls == 0

        monkeypatch.setattr(bridge._tasks, "submit", real_submit)
        bridge.refresh()
        assert _wait_for(qapp, lambda: bridge.hasData and not bridge.busy)
        assert bridge.error == ""
        assert bridge.snapshot["recordCount"] == 1
        assert service.calls == 1
    finally:
        bridge.close()
