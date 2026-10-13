"""Production startup prepares real page instances and DTOs before interaction."""
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtTest import QTest

from study_app.quick.bridge import DashboardBridge
from study_app.quick.dashboard_service import DashboardService
from study_app.quick.runtime import create_engine
from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, visual_items


@pytest.fixture
def prepared_window(quick_window):
    app, original, old_bridge, _, db, model = quick_window
    service = DashboardService(db, model, old_bridge._service.records_path, today="2026-10-04")
    bridge = DashboardBridge(service, preload_pages=True)
    calls = []
    for work in (bridge.workspace, bridge.insights, bridge.settings):
        execute = work._execute

        def tracked(action, *args, original_execute=execute):
            calls.append(action)
            return original_execute(action, *args)

        work._execute = tracked
    engine = create_engine(bridge)
    window = engine.rootObjects()[0]
    window.requestActivate()
    bridge.refresh()
    try:
        yield app, window, bridge, engine, calls
    finally:
        bridge.close()
        bridge.wait_for_shutdown()
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        original.requestActivate()


def raw(window, name):
    return next((i for i in visual_items(window.contentItem()) if i.objectName() == name), None)


def prepared(window):
    return raw(window, "startupPreloader").property("ready")


def test_startup_prepares_every_page_and_plan_tab_without_reloads(prepared_window):
    _, window, bridge, engine, calls = prepared_window
    wait_until(lambda: prepared(window), timeout_ms=30000)
    assert bridge.currentPage == "home"
    assert raw(window, "appContent").isEnabled()
    assert not raw(window, "startupCover").isVisible()
    pages = {name: raw(window, name + "Page") for name in ("home", "plan", "assistant", "knowledge", "settings")}
    assert all(pages.values())
    assert bridge.workspace.planData["catalog"]["subjects"]
    assert bridge.insights.assistantData and bridge.insights.knowledgeData
    assert bridge.settings.settingsData and bridge.settings.subjectsData
    plan_loaders = {name: raw(window, name + "TabLoader") for name in ("planning", "records", "oj", "practice")}
    assert all(loader.property("presentationReady") and loader.property("item") for loader in plan_loaders.values())
    assert calls.count("load") == 2  # workspace and settings
    assert calls.count("assistant.load") == calls.count("knowledge.load") == 1
    initial_calls = list(calls)
    bridge.navigate("plan")
    plan = find_item(window, "planPage")
    assert plan.property("section") == 0
    budget = find_item(window, "budgetMinutes")
    budget.setProperty("text", "73")
    for _ in range(2):
        for route in ("settings", "assistant", "knowledge", "plan", "home"):
            bridge.navigate(route)
            assert find_item(window, route + "Page") is pages[route]
            assert raw(window, "pageLoader").property("pagePresented")
        bridge.navigate("plan")
        for section in (1, 2, 3, 0):
            plan.setProperty("section", section)
            assert plan.property("panesPresented")
        QTest.qWait(100)
    assert budget.property("text") == "73"
    assert calls == initial_calls
    assert not engine._qml_warnings


def test_preload_cover_stays_until_slow_workspace_finishes(prepared_window, monkeypatch):
    _, window, bridge, engine, _ = prepared_window
    release = threading.Event()
    started = threading.Event()
    original = bridge.workspace._execute

    def delayed(*args):
        started.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(bridge.workspace, "_execute", delayed)
    try:
        wait_until(started.is_set)
        QTest.qWait(100)
        assert raw(window, "startupCover").isVisible()
        content = raw(window, "appContent")
        assert not content.isEnabled() and content.opacity() == 0
        clock = window.findChild(QObject, "ambientWindClock")
        assert clock.property("paused")
        assert not prepared(window)
        release.set()
        wait_until(lambda: prepared(window), timeout_ms=30000)
        assert content.isEnabled() and content.opacity() == 1
        assert not engine._qml_warnings
    finally:
        release.set()


def test_failed_preload_preserves_error_and_allows_entering_app(prepared_window, monkeypatch):
    _, window, bridge, engine, _ = prepared_window

    def fail(*args):
        raise RuntimeError("synthetic preload read failure")

    monkeypatch.setattr(bridge.workspace, "_execute", fail)
    wait_until(lambda: prepared(window), timeout_ms=30000)
    assert bridge.workspace.error
    assert bridge.settings.settingsData
    bridge.navigate("plan")
    wait_until(lambda: not bridge.workspace.busy)
    assert find_item(window, "workspaceStatus").property("text") == bridge.workspace.error
    assert not engine._qml_warnings

def test_retained_plan_resyncs_subject_choices_on_entry_without_reloading(prepared_window):
    _, window, bridge, engine, calls = prepared_window
    wait_until(lambda: prepared(window), timeout_ms=30000)
    bridge.navigate("plan")
    pane = find_item(window, "planningPane")
    choice = find_item(window, "planScope")
    names = [row["name"] for row in bridge.workspace.planData["catalog"]["subjects"]]
    assert choice.property("count") == len(names) + 1
    budget = find_item(window, "budgetMinutes")
    budget.setProperty("text", "73")
    bridge.navigate("home")
    # Simulate a retained pane left with a stale empty view while its backend
    # snapshot is complete. Re-entry must reconcile even without a DTO change.
    pane.setProperty("planData", {"catalog": {"subjects": []}})
    QTest.qWait(30)
    assert choice.property("count") == 1
    budget.setProperty("text", "73")
    previous = list(calls)
    bridge.navigate("plan")
    wait_until(lambda: choice.property("count") == len(names) + 1)
    QTest.qWait(150)
    assert budget.property("text") == "73"
    assert calls == previous
    assert not engine._qml_warnings


def test_unchanged_manual_read_resyncs_choices_without_resetting_draft(prepared_window):
    from PySide6.QtTest import QSignalSpy
    _, window, bridge, engine, _ = prepared_window
    wait_until(lambda: prepared(window), timeout_ms=30000)
    bridge.navigate("plan")
    pane = find_item(window, "planningPane")
    choice = find_item(window, "planScope")
    budget = find_item(window, "budgetMinutes")
    budget.setProperty("text", "73")
    expected = len(bridge.workspace.planData["catalog"]["subjects"]) + 1
    pane.setProperty("planData", {"catalog": {"subjects": []}})
    QTest.qWait(30)
    assert choice.property("count") == 1
    budget.setProperty("text", "73")
    changed = QSignalSpy(bridge.workspace.planDataChanged)
    bridge.workspace.run("load", {})
    wait_until(lambda: not bridge.workspace.busy)
    wait_until(lambda: choice.property("count") == expected)
    assert changed.count() == 0
    assert budget.property("text") == "73"
    assert not engine._qml_warnings
