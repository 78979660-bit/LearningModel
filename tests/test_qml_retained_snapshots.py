"""All retained pages must recover a stale view without reloading or losing drafts."""
import gc

from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QSignalSpy, QTest

from study_app.quick.self_test import wait_until
from test_qml_startup_preload import prepared_window, prepared, raw
from test_qml_window import quick_window


def evaluate(item, text):
    expression = QQmlExpression(QQmlEngine.contextForObject(item), item, text)
    result = expression.evaluate()[0]
    assert not expression.hasError(), expression.error().toString()
    return result


def test_all_retained_snapshots_reconcile_on_entry_and_unchanged_reads(prepared_window):
    _, window, bridge, engine, calls = prepared_window
    wait_until(lambda: prepared(window), timeout_ms=30000)
    descriptors = [
        ("home", "homePage", "homeSnapshot", bridge, "snapshot", None),
        ("plan", "planningPane", "planSnapshot", bridge.workspace, "planData", 0),
        ("plan", "recordsPane", "recordsSnapshot", bridge.workspace, "recordsData", 1),
        ("plan", "ojPane", "ojSnapshot", bridge.workspace, "ojData", 2),
        ("plan", "practicePane", "practiceOptionsSnapshot", bridge.workspace, "practiceOptions", 3),
        ("assistant", "assistantPage", "assistantSnapshot", bridge.insights, "assistantData", None),
        ("knowledge", "knowledgePage", "knowledgeSnapshot", bridge.insights, "knowledgeData", None),
        ("settings", "subjectsPane", "subjectsSnapshot", bridge.settings, "subjectsData", "subjects"),
    ]
    # Retained form input must survive visits and all unchanged reads.
    raw(window, "recordNote").setProperty("text", "unsaved record note")
    raw(window, "practiceTitle").setProperty("text", "unsaved practice title")
    raw(window, "budgetMinutes").setProperty("text", "73")
    previous = list(calls)
    for route, page, snapshot, work, key, section in descriptors:
        bridge.navigate("home" if route != "home" else "settings")
        observer = raw(window, snapshot)
        assert observer is not None, snapshot
        assert observer.property("ready")
        observer.setProperty("value", {})
        QTest.qWait(20)
        assert not observer.property("ready")
        engine.collectGarbage()
        gc.collect()
        bridge.navigate(route)
        if route == "plan": raw(window, "planPage").setProperty("section", section)
        if route == "settings": raw(window, "settingsPage").setProperty("activeTab", section)
        wait_until(lambda: observer.property("ready"))
        assert evaluate(observer, "JSON.stringify(value)") == evaluate(
            raw(window, page), "JSON.stringify(" + ("bridge" if route == "home" else
                 "workspace" if route == "plan" else "work") + "." + key + ")")
    QTest.qWait(100)
    assert calls == previous
    changed = QSignalSpy(bridge.workspace.dataChanged)
    raw(window, "recordsSnapshot").setProperty("value", {})
    raw(window, "practiceOptionsSnapshot").setProperty("value", {})
    bridge.workspace.run("load", {})
    wait_until(lambda: not bridge.workspace.busy)
    wait_until(lambda: raw(window, "recordsSnapshot").property("ready") and
                      raw(window, "practiceOptionsSnapshot").property("ready"))
    assert changed.count() == 0
    assert raw(window, "recordNote").property("text") == "unsaved record note"
    assert raw(window, "practiceTitle").property("text") == "unsaved practice title"
    assert raw(window, "budgetMinutes").property("text") == "73"
    assert raw(window, "recordSubject").property("count") == len(bridge.workspace.recordsData["catalog"]["subjects"])
    assert raw(window, "practiceSubject").property("count") == len(bridge.workspace.planData["catalog"]["subjects"])
    assert raw(window, "ojProblemChoice").property("count") == len(bridge.workspace.ojData["problems"])
    assert not engine._qml_warnings


def test_preloader_waits_for_controls_after_backend_data_is_ready(prepared_window):
    _, window, bridge, engine, _ = prepared_window
    preloader = raw(window, "startupPreloader")
    wait_until(lambda: raw(window, "settingsPage") is not None, timeout_ms=30000)
    wait_until(lambda: bool(bridge.settings.settingsData))
    pane = raw(window, "preferencesPane")
    assert bridge.settings.settingsData
    # Recreate the race: complete backend DTO, a not-yet-synchronized form.
    pane.setProperty("snapshotReady", False)
    # Find the pane's synchronization timer through its QObject children.
    from PySide6.QtCore import QObject
    for child in pane.findChildren(QObject):
        if child.metaObject().className().startswith("QQmlTimer"):
            child.setProperty("running", False)
    QTest.qWait(600)
    assert not prepared(window)
    assert preloader.property("stepIndex") == 8
    assert raw(window, "startupCover").isVisible()
    evaluate(pane, "syncFromSnapshot()")
    wait_until(lambda: prepared(window), timeout_ms=30000)
    assert pane.property("snapshotReady")
    assert not engine._qml_warnings


def test_loader_error_is_visible_and_does_not_leave_endless_spinner(prepared_window):
    _, window, bridge, engine, _ = prepared_window
    wait_until(lambda: prepared(window), timeout_ms=30000)
    loader = raw(window, "pageLoader").property("currentLoader")
    evaluate(loader, 'setSource("file:///__missing_preload_test__/page.qml")')
    wait_until(lambda: bool(evaluate(loader, "status === 3")))
    assert raw(window, "pageLoadError").isVisible()
    assert "未能加载" in raw(window, "pageLoadError").property("text")
    assert raw(window, "appContent").isEnabled()
    assert engine._qml_warnings  # Expected missing-component diagnostic only.
