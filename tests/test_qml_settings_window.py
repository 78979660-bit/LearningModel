"""Actual full-window settings and lifecycle operations against isolated data."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zipfile

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest

from study_app.ai.providers import load_llm_settings
from study_app.data.database import add_learning_record, connect_readonly
from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, click, capture, texts


pytestmark = pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)


def reveal(window, item):
    parent = item.parentItem()
    while parent is not None:
        if parent.objectName() in {"subjectsPane", "settingsPreferencesScroll"}:
            flick = parent.property("contentItem")
            content = flick.property("contentItem")
            QTest.qWait(30)
            def scroll_into_view():
                point = item.mapToItem(content, QPointF(0, 0))
                limit = max(0, float(flick.property("contentHeight")) - flick.height())
                flick.setProperty("contentY", max(0, min(point.y() - 20, limit)))
                visible = item.mapToItem(flick, QPointF(0, 0))
                return visible.y() >= 0 and visible.y() + item.height() <= flick.height() + 1
            wait_until(scroll_into_view, timeout_ms=3000)
            return
        parent = parent.parentItem()


def activate(window, name):
    item = find_item(window, name)
    reveal(window, item)
    wait_until(lambda: item.isVisible() and bool(item.property("enabled")), timeout_ms=2000)
    assert item.isVisible() and item.property("enabled"), name
    activated = QSignalSpy(item.clicked)
    click(window, item)
    assert activated.count() == 1, name


def edit(window, name, text):
    item = find_item(window, name)
    reveal(window, item)
    item.forceActiveFocus()
    item.setProperty("text", text)
    if hasattr(item, "textEdited"):
        item.textEdited.emit()


def ready(work):
    wait_until(lambda: not work.busy)
    assert not work.error, getattr(work, "_test_failure", work.error)


def open_settings(window, bridge):
    activate(window, "nav-settings")
    work = bridge.settings
    ready(work)
    pane = find_item(window, "preferencesPane")
    wait_until(lambda: not pane.property("busy") and
               find_item(window, "settingsProviderChoice").property("currentValue") == work.settingsData["llm"]["provider"])
    assert work.settingsData["llm"]["providers"] and work.subjectsData["subjects"]
    original = work._execute
    def execute(action, payload):
        try:
            return original(action, payload)
        except Exception as error:
            # This window uses only synthetic data. Production error reporting
            # remains redacted; include the cause to diagnose this fixture.
            work._test_failure = (action, type(error).__name__, str(error))
            raise
    work._execute = execute
    return work


def test_settings_save_and_refresh_persist_real_configuration(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_settings(window, bridge)
    edit(window, "settingsTokenLimitField", "14000")
    edit(window, "settingsDailyBudgetField", "5.75")
    activate(window, "settingsSaveButton")
    ready(work)
    saved = load_llm_settings(db_path=db_path)
    assert saved.single_call_token_limit == 14000 and saved.daily_budget_cny == 5.75
    assert work.receipt["saved"]
    assert find_item(window, "settingsTokenLimitField").property("text") == "14000"
    QTest.keyClick(window, Qt.Key.Key_F5)
    ready(work)
    assert work.settingsData["llm"]["dailyBudgetCny"] == 5.75
    for _ in range(2):
        activate(window, "settingsPreferencesTab")
        assert find_item(window, "settingsPreferencesTab").property("primary")
    capture(window, "phase4-settings-saved")
    assert not engine._qml_warnings


def test_python_credential_entry_and_qml_clear_confirmation(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_settings(window, bridge)
    choice = find_item(window, "settingsProviderChoice")
    providers = work.settingsData["llm"]["providers"]
    choice.setProperty("currentIndex", next(i for i, row in enumerate(providers) if row["key"] == "openai"))
    edit(window, "settingsModelField", "isolated-provider-model")
    activate(window, "settingsSaveButton")
    ready(work)
    work._credential_prompt = lambda *_: "phase4-private-isolated-key"
    activate(window, "settingsCredentialButton")
    ready(work)
    assert load_llm_settings(db_path=db_path).api_key == "phase4-private-isolated-key"
    assert work.settingsData["llm"]["hasCredential"]
    assert "phase4-private-isolated-key" not in " ".join(texts(window))
    assert "phase4-private-isolated-key" not in repr((work.settingsData, work.receipt))
    activate(window, "settingsClearCredentialButton")
    activate(window, "settingsClearCredentialCancel")
    assert load_llm_settings(db_path=db_path).api_key
    activate(window, "settingsClearCredentialButton")
    activate(window, "settingsClearCredentialConfirm")
    ready(work)
    assert not load_llm_settings(db_path=db_path).api_key
    assert not work.settingsData["llm"]["hasCredential"]
    assert not engine._qml_warnings


@pytest.mark.parametrize("original_provider", ["deepseek", "custom"])
def test_changed_identity_requires_saved_endpoint_and_new_credential(quick_window, original_provider):
    from test_qml_settings_service import _valid_payload

    _, window, bridge, engine, db_path, _ = quick_window
    service = bridge.settings._settings
    service.save(_valid_payload(
        provider=original_provider,
        custom_url="https://endpoint-a.example.test/v1" if original_provider == "custom" else "",
    ))
    service.set_credential(original_provider, "isolated-old-window-key")
    work = open_settings(window, bridge)
    assert "已配置" in find_item(window, "settingsCredentialStatus").property("text")
    providers = work.settingsData["llm"]["providers"]
    find_item(window, "settingsProviderChoice").setProperty(
        "currentIndex", next(i for i, row in enumerate(providers) if row["key"] == "custom"),
    )
    edit(window, "settingsCustomBaseUrlField", "https://endpoint-b.example.test/v1")
    assert not find_item(window, "settingsCredentialButton").property("enabled")
    assert not find_item(window, "settingsClearCredentialButton").property("enabled")
    assert "先保存" in find_item(window, "settingsCredentialStatus").property("text")
    activate(window, "settingsSaveButton")
    ready(work)
    wait_until(lambda: "尚未配置" in find_item(window, "settingsCredentialStatus").property("text"))
    assert not load_llm_settings(db_path=db_path).api_key
    assert work.receipt["credentialReset"] and "旧凭据已清除" in work.message
    assert not work.settingsData["llm"]["hasCredential"]
    work._credential_prompt = lambda *_: "isolated-new-window-key"
    activate(window, "settingsCredentialButton")
    ready(work)
    assert load_llm_settings(db_path=db_path).api_key == "isolated-new-window-key"
    assert "已配置" in find_item(window, "settingsCredentialStatus").property("text")
    assert "isolated-new-window-key" not in " ".join(texts(window))
    assert not engine._qml_warnings


def test_backup_and_integrity_use_only_injected_data(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_settings(window, bridge)
    assert work._settings.backup_root.is_relative_to(db_path.parent)
    activate(window, "settingsDataTab")
    activate(window, "settingsBackupButton")
    ready(work)
    result = work.receipt
    archive = Path(result["archive"])
    assert archive.is_file() and archive.is_relative_to(db_path.parent)
    with zipfile.ZipFile(archive) as package:
        assert {"learning_app.sqlite", "learning_records_export.json", "manifest.json"} <= set(package.namelist())
    copy = Path(result["folder"]) / result["databaseCopy"]
    with sqlite3.connect(copy) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    activate(window, "settingsIntegrityButton")
    ready(work)
    assert work.receipt["healthy"] and "SQLite: ok" in work.receipt["summary"]
    capture(window, "phase4-settings-maintenance")
    assert not engine._qml_warnings


def confirm_subject(window, work, name):
    assert not find_item(window, "subjectConfirmButton").property("enabled")
    edit(window, "subjectConfirmationNameInput", "错误确认")
    activate(window, "subjectImpactAcknowledgement")
    assert not find_item(window, "subjectConfirmButton").property("enabled")
    edit(window, "subjectConfirmationNameInput", name)
    activate(window, "subjectConfirmButton")
    ready(work)
    assert work.subjectResult["saved"] and work.subjectResult["backupVerified"]
    assert not work.subjectPreview
    return work.subjectResult["subjectKey"]


def test_introduce_remove_restore_keep_identity_and_real_records(quick_window):
    _, window, bridge, engine, db_path, model_path = quick_window
    work = open_settings(window, bridge)
    activate(window, "settingsSubjectsTab")
    edit(window, "subjectNameInput", "阶段四验收学科")
    edit(window, "subjectOutlineInput", "第一章 / 基础知识\n第二章 / 进阶知识")
    activate(window, "subjectPreviewIntroduceButton")
    ready(work)
    assert work.subjectPreview["moduleCount"] == 2 and work.subjectPreview["topicCount"] == 2
    with connect_readonly(db_path) as connection:
        assert not connection.execute("SELECT 1 FROM subjects WHERE name=?", ("阶段四验收学科",)).fetchone()
    capture(window, "phase4-subject-review")
    key = confirm_subject(window, work, "阶段四验收学科")
    add_learning_record({"date": "2026-10-04", "subject": "阶段四验收学科", "module": "第一章",
                         "topic": "基础知识", "activity": "review", "source": "outside_class", "duration_minutes": 20},
                        db_path=db_path, model_path=model_path)
    work.run("load", {})
    ready(work)
    activate(window, "subjectArchivePreview_" + key)
    ready(work)
    assert work.subjectPreview["impact"]["records"] == 1
    assert confirm_subject(window, work, "阶段四验收学科") == key
    with connect_readonly(db_path) as connection:
        assert connection.execute("SELECT lifecycle_status FROM subject_catalog WHERE subject_key=?", (key,)).fetchone()[0] == "archived"
        assert connection.execute("SELECT COUNT(*) FROM learning_records WHERE subject_name=?", ("阶段四验收学科",)).fetchone()[0] == 1
    activate(window, "subjectRestorePreview_" + key)
    ready(work)
    assert confirm_subject(window, work, "阶段四验收学科") == key
    with connect_readonly(db_path) as connection:
        assert connection.execute("SELECT lifecycle_status FROM subject_catalog WHERE subject_key=?", (key,)).fetchone()[0] == "active"
        assert connection.execute("SELECT COUNT(*) FROM learning_records WHERE subject_name=?", ("阶段四验收学科",)).fetchone()[0] == 1
    activate(window, "nav-home")
    wait_until(lambda: not bridge.busy)
    assert bridge.snapshot["activeCount"] == 2 and bridge.snapshot["recordCount"] == 1
    activate(window, "nav-knowledge")
    ready(bridge.insights)
    assert any(row["label"] == "阶段四验收学科" for row in bridge.insights.knowledgeData["subjects"])
    capture(window, "phase4-subject-real-refresh")
    assert not engine._qml_warnings


def test_file_draft_preview_invalidation_and_small_window_keyboard(quick_window):
    _, window, bridge, engine, db_path, model_path = quick_window
    work = open_settings(window, bridge)
    activate(window, "settingsSubjectsTab")
    source = model_path.parent / "checked-outline.json"
    source.write_text(json.dumps({"name": "文件目录学科", "modules": [{"name": "明确章节", "topics": ["明确知识点"]}]}, ensure_ascii=False), encoding="utf-8-sig")
    work.run("subject.importOutline", {"url": source.as_uri()})
    ready(work)
    assert find_item(window, "subjectNameInput").property("text") == "文件目录学科"
    assert find_item(window, "subjectOutlineInput").property("text") == "明确章节 / 明确知识点"
    activate(window, "subjectPreviewIntroduceButton")
    ready(work)
    edit(window, "subjectConfirmationNameInput", "文件目录学科")
    activate(window, "subjectImpactAcknowledgement")
    assert find_item(window, "subjectConfirmButton").property("enabled")
    edit(window, "subjectNameInput", "修改后的学科")
    assert not work.subjectPreview
    assert "失效" in work.message
    activate(window, "subjectPreviewIntroduceButton")
    ready(work)
    activate(window, "settingsPreferencesTab")
    assert not work.subjectPreview
    for width, height in ((800, 600), (1600, 1000)):
        window.resize(width, height)
        QTest.qWait(150)
        for section, control, pane_name in (("preferences", "settingsIntegrityButton", "preferencesPane"),
                                            ("subjects", "subjectPreviewIntroduceButton", "subjectsPane")):
            activate(window, "settingsPreferencesTab" if section == "preferences" else "settingsSubjectsTab")
            if section == "preferences":
                activate(window, "settingsDataTab")
            pane = find_item(window, pane_name)
            # Native rendering polishes a newly visible layout on the next
            # frame. Keep the geometry assertion, wait for the actual layout.
            wait_until(lambda: pane.isVisible() and pane.width() <= window.width() and pane.height() > 60,
                       timeout_ms=2000)
            assert pane.isVisible() and pane.width() <= window.width() and pane.height() > 60
            target = find_item(window, control)
            reveal(window, target)
            point = target.mapToScene(QPointF(0, 0))
            assert point.x() >= 0 and point.x() + target.width() <= window.width() + 1
            assert point.y() >= 0 and point.y() + target.height() <= window.height() + 1
            target.forceActiveFocus()
            assert target.hasActiveFocus()
            capture(window, "phase4-" + section + f"-{width}x{height}")
    QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    assert bridge.currentPage == "home"
    assert not engine._qml_warnings
