"""Retained settings forms reconcile saved data without losing user drafts."""
import gc

import pytest
from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtTest import QSignalSpy, QTest

from study_app.ai.providers import load_llm_settings
from study_app.quick.self_test import wait_until
from test_qml_startup_preload import prepared_window, prepared, raw
from test_qml_window import quick_window, find_item
from test_qml_settings_service import _valid_payload


@pytest.fixture
def saved_preferences(prepared_window):
    app, window, bridge, engine, calls = prepared_window
    wait_until(lambda: prepared(window), timeout_ms=30000)
    payload = _valid_payload(provider="custom", model="deepseek-flash",
                             custom_url="https://saved.example.test/v1", budget="3")
    payload["llm"].update(singleCallTokenLimit="200000", allowUploadImages=True, allowUploadPdfs=True)
    bridge.settings._settings.save(payload)
    bridge.settings._settings.set_credential("custom", "isolated-snapshot-test-credential")
    bridge.settings.run("load", {})
    wait_until(lambda: not bridge.settings.busy)
    bridge.navigate("settings")
    pane = find_item(window, "preferencesPane")
    wait_until(lambda: not pane.property("busy"))
    assert_saved_form(window)
    yield app, window, bridge, engine, calls, pane


def assert_saved_form(window):
    assert raw(window, "settingsProviderChoice").property("currentValue") == "custom"
    assert raw(window, "settingsModelField").property("text") == "deepseek-flash"
    assert raw(window, "settingsTokenLimitField").property("text") == "200000"
    assert raw(window, "settingsAllowImages").property("checked")
    assert raw(window, "settingsAllowPdfs").property("checked")
    assert "已配置" in raw(window, "settingsCredentialStatus").property("text")


def stale_form(window):
    # Programmatic stale state is not a user edit (no textEdited/activated).
    raw(window, "settingsProviderChoice").setProperty("currentIndex", 0)
    raw(window, "settingsModelField").setProperty("text", "")
    raw(window, "settingsTokenLimitField").setProperty("text", "8000")
    raw(window, "settingsAllowImages").setProperty("checked", False)
    raw(window, "settingsAllowPdfs").setProperty("checked", False)


def test_reentry_repairs_stale_controls_without_database_reload(saved_preferences):
    app, window, bridge, engine, calls, pane = saved_preferences
    bridge.navigate("home")
    stale_form(window)
    previous = list(calls)
    engine.collectGarbage()
    gc.collect()
    bridge.navigate("settings")
    wait_until(lambda: not pane.property("busy"))
    assert_saved_form(window)
    assert calls == previous
    assert not engine._qml_warnings


def test_unchanged_read_repairs_stale_snapshot_and_controls(saved_preferences):
    _, window, bridge, engine, _, pane = saved_preferences
    stale_form(window)
    pane.setProperty("settingsData", {})
    changed = QSignalSpy(bridge.settings.dataChanged)
    bridge.settings.run("load", {})
    wait_until(lambda: not bridge.settings.busy and not pane.property("busy"))
    assert changed.count() == 0
    assert_saved_form(window)
    assert not engine._qml_warnings


def test_unchanged_reads_and_navigation_preserve_user_drafts(saved_preferences):
    _, window, bridge, engine, _, pane = saved_preferences
    model = raw(window, "settingsModelField")
    model.setProperty("text", "unsaved-model-draft")
    model.textEdited.emit()
    assert pane.property("draftDirty")
    for _ in range(3):
        bridge.navigate("home")
        bridge.navigate("settings")
        bridge.settings.run("load", {})
        wait_until(lambda: not bridge.settings.busy and not pane.property("busy"))
        assert model.property("text") == "unsaved-model-draft"
    saved = load_llm_settings(db_path=bridge.settings._settings.db_path)
    assert saved.model == "deepseek-flash" and saved.api_key == "isolated-snapshot-test-credential"
    assert not engine._qml_warnings


def test_incomplete_snapshot_cannot_save_default_form(prepared_window):
    _, window, bridge, _, _ = prepared_window
    wait_until(lambda: prepared(window), timeout_ms=30000)
    bridge.navigate("settings")
    pane = find_item(window, "preferencesPane")
    wait_until(lambda: not pane.property("busy"))
    actions = []
    class PendingSettings(QObject):
        dataChanged = Signal()
        stateChanged = Signal()

        @Property("QVariantMap", notify=dataChanged)
        def settingsData(self):
            return {}

        @Property(bool, notify=stateChanged)
        def busy(self):
            return False

        @Slot(str, "QVariantMap")
        def run(self, action, payload):
            actions.append((action, payload))

    pending = PendingSettings()
    pane.setProperty("work", pending)
    QTest.qWait(30)
    assert not pane.property("snapshotReady")
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    expression = QQmlExpression(QQmlEngine.contextForObject(pane), pane, "saveSettings()")
    expression.evaluate()
    assert not expression.hasError()
    assert not actions
    assert not raw(window, "settingsSaveButton").property("enabled")
