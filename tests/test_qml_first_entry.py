"""First presentation with slow data, failures, and navigation during loading."""
import threading

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest

from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, visual_items


def raw_item(window, name):
    return next((item for item in visual_items(window.contentItem())
                 if item.objectName() == name), None)


def test_cold_home_waits_for_snapshot_before_presenting_overview(quick_window, monkeypatch):
    from study_app.quick.bridge import DashboardBridge
    from study_app.quick.dashboard_service import DashboardService
    from study_app.quick.runtime import create_engine
    _, original_window, original_bridge, _, db_path, model_path = quick_window
    service = DashboardService(db_path, model_path, original_bridge._service.records_path,
                               today="2026-10-04")
    original_load = service.load
    release = threading.Event()

    def delayed():
        assert release.wait(5)
        return original_load()

    monkeypatch.setattr(service, "load", delayed)
    bridge = DashboardBridge(service)
    engine = create_engine(bridge)
    window = engine.rootObjects()[0]
    window.requestActivate()
    try:
        bridge.refresh()
        QTest.qWait(100)
        loader = raw_item(window, "pageLoader").property("currentLoader")
        assert loader.opacity() == 0 and not loader.isEnabled()
        release.set()
        overview = find_item(window, "homeOverview")
        assert overview.isVisible() and overview.width() > 280
        assert not raw_item(window, "homeEmptyState").isVisible()
        assert bridge.hasData and loader.property("presentationReady")
        assert not engine._qml_warnings
    finally:
        release.set()
        bridge.close()
        bridge.wait_for_shutdown()
        window.close()
        engine.deleteLater()
        original_window.requestActivate()


@pytest.mark.parametrize("route", ["plan", "knowledge", "assistant", "settings"])
def test_slow_first_read_presents_complete_page_once(quick_window, monkeypatch, route):
    _, window, bridge, engine, _, _ = quick_window
    work = {"plan": bridge.workspace, "knowledge": bridge.insights,
            "assistant": bridge.insights, "settings": bridge.settings}[route]
    original = work._execute
    release = threading.Event()
    calls = []

    def delayed(*args):
        calls.append(args[0])
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(work, "_execute", delayed)
    try:
        bridge.navigate(route)
        page = raw_item(window, route + "Page")
        loader = raw_item(window, "pageLoader").property("currentLoader")
        wait_until(lambda: bool(calls))
        QTest.qWait(120)
        assert page is not None
        assert loader.opacity() == 0 and not loader.property("presentationReady")
        assert not loader.isEnabled()
        release.set()
        wait_until(lambda: loader.property("presentationReady"))
        find_item(window, route + "Page")
        assert len(calls) == 1
        assert not work.error
        assert page.width() > 0 and page.height() > 0
        if route == "plan":
            scope = find_item(window, "planScopeCard")
            tasks = find_item(window, "budgetTaskListCard")
            geometry = [(i.mapToScene(QPointF()), i.width(), i.height()) for i in (scope, tasks)]
            assert scope.width() > 280 and tasks.height() > 200
            QTest.qWait(100)
            assert geometry == [(i.mapToScene(QPointF()), i.width(), i.height()) for i in (scope, tasks)]
        bridge.navigate("home")
        QTest.qWait(30)
        bridge.navigate(route)
        assert raw_item(window, route + "Page") is page
        assert loader.property("presentationReady") and loader.opacity() == 1
        QTest.qWait(100)
        assert len(calls) == 1
        assert not engine._qml_warnings
    finally:
        release.set()


def test_first_read_failure_shows_error_instead_of_staying_hidden(quick_window, monkeypatch):
    _, window, bridge, engine, _, _ = quick_window

    def fail(*args):
        raise RuntimeError("synthetic first-read failure")

    monkeypatch.setattr(bridge.workspace, "_execute", fail)
    bridge.navigate("plan")
    status = find_item(window, "workspaceStatus")
    assert bridge.workspace.error
    assert status.property("text") == bridge.workspace.error
    assert raw_item(window, "pageLoader").property("pagePresented")
    assert not engine._qml_warnings


def test_leaving_during_first_read_does_not_present_hidden_page(quick_window, monkeypatch):
    _, window, bridge, engine, _, _ = quick_window
    original = bridge.workspace._execute
    release = threading.Event()

    def delayed(*args):
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(bridge.workspace, "_execute", delayed)
    try:
        bridge.navigate("plan")
        loader = raw_item(window, "pageLoader").property("currentLoader")
        bridge.navigate("home")
        release.set()
        wait_until(lambda: not bridge.workspace.busy)
        QTest.qWait(100)
        assert not loader.isVisible() and not loader.property("presentationReady")
        bridge.navigate("plan")
        assert find_item(window, "planScopeCard").width() > 280
        assert loader.property("presentationReady")
        assert not engine._qml_warnings
    finally:
        release.set()
