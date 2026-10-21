"""Phase-five real data, startup diagnostics and non-blocking recovery contracts."""
from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from study_app.core.async_tasks import AsyncTaskService
from study_app.data import database
from study_app.quick.lifecycle import LifecycleController
from study_app.quick.maintenance_service import StartupMaintenanceService
from study_app.quick.self_test import run_probe, wait_until
from test_qml_window import quick_window, find_item, click, capture, texts


def reveal_home(window, item):
    ancestor = item
    while ancestor is not None:
        if ancestor.objectName() == "homeDetails" and not ancestor.isVisible():
            toggle = find_item(window, "homeMoreDetailsToggle")
            reveal_home(window, toggle)
            click(window, toggle)
            QTest.qWait(60)
            break
        ancestor = ancestor.parentItem()
    parent = item.parentItem()
    while parent is not None and parent.property("contentY") is None:
        parent = parent.parentItem()
    assert parent is not None
    parent.setProperty("contentY", max(0, item.mapToItem(parent.property("contentItem"), QPointF()).y() - 20))
    QTest.qWait(60)


def test_real_home_summary_refreshes_after_edit_and_reentering_route(quick_window):
    app, window, bridge, engine, db_path, model_path = quick_window
    daily = bridge.dailySummary
    reveal_home(window, find_item(window, "homeSummaryPane"))
    wait_until(lambda: not daily.busy and bool(daily.summaryData))
    before = daily.summaryData["signature"]
    assert find_item(window, "homeSummaryPane").property("implicitHeight") > 100
    pane = find_item(window, "homeSummaryPane")
    reveal_home(window, pane)
    capture(window, "phase5-home-summary")
    bridge.navigate("plan")
    app.processEvents()
    database.add_learning_record({"date": "2026-10-04", "subject": "测试学科",
                                  "module": "测试模块", "topic": "测试知识点",
                                  "activity": "review", "duration_minutes": 15},
                                 db_path=db_path, model_path=model_path)
    bridge.refresh()
    wait_until(lambda: not bridge.busy)
    bridge.navigate("home")
    app.processEvents()
    reveal_home(window, find_item(window, "homeSummaryPane"))
    wait_until(lambda: not daily.busy and daily.summaryData.get("signature") != before)
    assert not daily.error
    assert daily.summaryData["date"] == "2026-10-04"
    assert engine._qml_warnings == []


def test_paid_summary_postcommit_failure_shows_receipt_and_disables_repeat_in_window(
    quick_window, monkeypatch
):
    from test_qml_daily_summary_service import _save_cloud_settings, _set_test_credential
    from study_app.quick.settings_service import SettingsService

    app, window, bridge, engine, db_path, model_path = quick_window
    daily = bridge.dailySummary
    reveal_home(window, find_item(window, "homeSummaryPane"))
    wait_until(lambda: not daily.busy and bool(daily.summaryData))
    settings = SettingsService(db_path, model_path, bridge._service.records_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    daily.run("refresh", {})
    wait_until(lambda: not daily.busy)
    generated = []
    def generate(*_args, **_kwargs):
        generated.append(True)
        return {"overview": ["测试学科的真实数据摘要。"], "risks": ["继续巩固测试知识点。"],
                "actions": ["复习测试知识点。"], "source": ["隔离测试生成，无外部请求。"]}
    monkeypatch.setattr(daily._service, "_llm_generator", generate)
    real_state = daily._service._load_state
    reads = []
    def state():
        reads.append(True)
        if len(reads) == 3:
            raise OSError("isolated post-commit read failure")
        return real_state()
    monkeypatch.setattr(daily._service, "_load_state", state)
    button = find_item(window, "dailySummaryGenerateEnhanced")
    reveal_home(window, button)
    assert button.property("enabled")
    click(window, button)
    wait_until(lambda: not daily.busy)
    assert generated == [True]
    assert not daily.error
    assert daily.summaryData["refreshWarning"]
    assert any("已生成并缓存" in line and "不要重复生成" in line for line in texts(window))
    assert not button.property("enabled")
    assert database.get_setting("daily_summary_cache:2026-10-04", None, db_path=db_path)
    refresh = find_item(window, "dailySummaryRefresh")
    reveal_home(window, refresh)
    click(window, refresh)
    wait_until(lambda: not daily.busy)
    assert button.property("enabled")
    assert generated == [True]
    assert engine._qml_warnings == []


def test_cancelled_paid_summary_reconciles_real_cache_before_another_request(
    quick_window, monkeypatch
):
    from test_qml_daily_summary_service import _save_cloud_settings, _set_test_credential
    from study_app.quick.settings_service import SettingsService

    app, window, bridge, engine, db_path, model_path = quick_window
    daily = bridge.dailySummary
    reveal_home(window, find_item(window, "homeSummaryPane"))
    wait_until(lambda: not daily.busy and bool(daily.summaryData))
    settings = SettingsService(db_path, model_path, bridge._service.records_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    daily.run("refresh", {})
    wait_until(lambda: not daily.busy)
    started, release = threading.Event(), threading.Event()
    calls = []

    def generate(*_args, **_kwargs):
        calls.append(True)
        started.set()
        if not release.wait(5):
            raise TimeoutError("isolated test did not release its generator")
        return {"overview": ["测试学科的取消后缓存摘要。"], "risks": ["继续巩固测试知识点。"],
                "actions": ["复习测试知识点。"], "source": ["隔离测试生成，无外部请求。"]}

    monkeypatch.setattr(daily._service, "_llm_generator", generate)
    try:
        pane = find_item(window, "homeSummaryPane")
        reveal_home(window, pane)
        click(window, find_item(window, "dailySummaryGenerateEnhanced"))
        wait_until(started.is_set)
        click(window, find_item(window, "dailySummaryStop"))
        assert daily.busy, "cancel must wait until the file-using worker ends"
        release.set()
        wait_until(lambda: not daily.busy)

        cached = database.get_setting("daily_summary_cache:2026-10-04", None, db_path=db_path)
        assert cached and cached["mode"] == "llm"
        assert len(calls) == 1
        assert daily.summaryData["generationOutcomeUnknown"] is True
        assert not find_item(window, "dailySummaryGenerateEnhanced").isEnabled()
        assert "刷新" in daily.message and not daily.error
        daily.run("generateEnhanced", {})
        assert len(calls) == 1

        click(window, find_item(window, "dailySummaryRefresh"))
        wait_until(lambda: not daily.busy)
        assert daily.summaryData["cacheHit"] and daily.summaryData["mode"] == "enhanced"
        assert "找到并载入" in daily.message
        assert "取消后缓存摘要" in str(daily.summaryData["summary"])
        assert len(calls) == 1, "refresh must never call the paid generator"
        assert engine._qml_warnings == []
    finally:
        release.set()


def test_real_home_float_buttons_keyboard_and_explicit_quit(quick_window):
    app, window, bridge, engine, *_ = quick_window
    floating = window.findChild(QQuickWindow, "floatWindow")
    controller = LifecycleController(enable_tray=False)
    bridge.attach_lifecycle(controller)
    quit_calls = []
    controller.start(window, floating, lambda: quit_calls.append(True))
    try:
        for width, height in ((800, 600), (960, 640), (1440, 900)):
            window.resize(width, height)
            app.processEvents()
            click(window, find_item(window, "windowActionsButton"))
            QTest.qWait(100)
            for name in ("homeHideButton", "homeQuitButton"):
                button = find_item(window, name)
                position = button.mapToScene(QPointF())
                assert position.x() >= 0 and position.x() + button.width() <= window.width() + 1
            click(window, find_item(window, "homeHideButton"))
            wait_until(lambda: floating.isVisible() and not window.isVisible())
            capture(floating, "phase5-floating-window")
            floating.requestActivate()
            QTest.qWait(100)
            # Showing the float focuses its accessible restore button.
            if width == 800:
                assert floating.width() == 56 and floating.height() == 56
                QTest.mouseClick(floating, Qt.MouseButton.LeftButton, pos=QPoint(28, 28))
                QTest.qWait(100)
                assert floating.isVisible() and not window.isVisible()
                QTest.mouseDClick(floating, Qt.MouseButton.LeftButton, pos=QPoint(28, 28))
            else:
                QTest.keyClick(floating, Qt.Key.Key_Return)
            wait_until(lambda: window.isVisible() and not floating.isVisible())
        click(window, find_item(window, "windowActionsButton"))
        QTest.qWait(100)
        click(window, find_item(window, "homeQuitButton"))
        wait_until(lambda: quit_calls == [True])
        assert controller.quitting
        assert engine._qml_warnings == []
    finally:
        controller.stop()


def test_expanded_startup_probe_decodes_assets_imports_and_reads_real_summary(quick_window):
    app, _, bridge, _, *_ = quick_window
    report = run_probe(app, bridge._service)
    assert report["daily_summary"]["status"] == "ok"
    assert report["daily_summary"]["external_requested"] is False
    assert len(report["runtime"]["imports"]) == 6
    assert "QtQuick.Shapes" in report["runtime"]["imports"]
    assert len(report["runtime"]["images"]) == 11
    assert any(item["resource"] == "assets/sakura-corner.png" for item in report["runtime"]["images"])
    assert set(report["runtime"]["translations"]) == {"qtbase_zh_CN", "qtdeclarative_zh_CN"}
    assert report["lifecycle"]["hide_restore_cycles"] == 3
    assert report["qml_warnings"] == []


@pytest.mark.parametrize("enabled,due,expected", [(False, True, "disabled"),
                                                  (True, False, "not-due"),
                                                  (True, True, "complete")])
def test_opt_in_weekly_maintenance_uses_injected_profile_on_worker(
    tmp_path, enabled, due, expected
):
    from test_qml_settings_service import _service

    settings = _service(tmp_path)
    database.set_setting("weekly_practice_collection_enabled", enabled, db_path=settings.db_path)
    worker_threads, collections = [], []
    gui_thread = threading.get_ident()

    def collect(path):
        worker_threads.append(threading.get_ident())
        collections.append(path)

    service = StartupMaintenanceService(settings.db_path, due=lambda path: due, collect=collect)
    tasks = AsyncTaskService(max_workers=1)
    try:
        result = tasks.submit("maintenance", service.run).wait(timeout=5)
        assert result.result == {"status": expected, "collected": expected == "complete"}
        if enabled and due:
            assert collections == [settings.db_path]
            assert worker_threads[0] != gui_thread
        else:
            assert collections == []
    finally:
        tasks.close()
        tasks.wait_for_shutdown()


