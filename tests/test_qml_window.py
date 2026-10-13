"""Exercise the real Quick window, including controls rather than bridge-only state."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtWidgets import QApplication

from study_app.data.database import add_learning_record, ensure_seeded_database
from study_app.quick.bridge import DashboardBridge
from study_app.quick.dashboard_service import DashboardService
from study_app.quick.runtime import create_engine
from study_app.quick.self_test import wait_until


def visual_items(item):
    yield item
    for child in item.childItems():
        yield from visual_items(child)


def find_item(window, name):
    # Deferred QML panes become interactive after Loader.Ready, not navigate().
    def ready():
        items = list(visual_items(window.contentItem()))
        return (all(item.property("pagePresented") for item in items
                    if item.objectName() == "pageLoader")
                and all(item.property("panePresented") for item in items
                    if item.objectName() == "workspacePaneLoader" and item.isVisible())
                and any(item.objectName() == name for item in items))
    wait_until(ready)
    QTest.qWait(20)  # Let the completed pane receive its layout polish.
    return next(item for item in visual_items(window.contentItem()) if item.objectName() == name)


def texts(window):
    return [str(item.property("text")) for item in visual_items(window.contentItem())
            if item.property("text") is not None]


def click(window, item):
    position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(position.x()), round(position.y())))


def capture(window, name):
    destination = os.environ.get("QML_CAPTURE_DIR")
    if destination:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        QTest.qWait(50)
        image = window.grabWindow()
        assert not image.isNull()
        assert abs(image.width() - window.width() * window.devicePixelRatio()) < 2
        assert image.save(str(root / (name + ".png")))


@pytest.fixture
def quick_window(tmp_path, request):
    app = QApplication.instance() or QApplication(["qml-window-tests"])
    # The Windows offscreen platform has no system font database. Read the OS
    # font for image QA only; this font is never copied into project resources.
    if not QFontDatabase.families():
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    model = json.loads((Path(__file__).parent / "fixtures/minimal_model.json").read_text(encoding="utf-8"))
    if getattr(request, "param", None) == "phase2":
        # A covered topic gives the real recommendation service a budget task.
        model["subjects"][0]["modules"][0]["topics"][0]["status"] = "learning"
        model["subjects"][0]["modules"][0]["topics"][0]["mastery"] = 0.25
        model["warning_policy"]["bkt_model"] = {"enabled": True}
    model_path = tmp_path / "model.json"
    records_path = tmp_path / "records.json"
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    records_path.write_text('{"records": []}', encoding="utf-8")
    db_path = tmp_path / "isolated.sqlite"
    ensure_seeded_database(db_path, model_path=model_path)
    from study_app.data.practice_repository import ensure_practice_templates_seeded
    ensure_practice_templates_seeded(db_path)
    bridge = DashboardBridge(DashboardService(db_path, model_path, records_path, today="2026-10-04"))
    # A shared test host must survive closing each fixture's last window.
    previous_quit_policy = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    engine = None
    try:
        engine = create_engine(bridge)
        window = engine.rootObjects()[0]
        window.requestActivate()
        if app.platformName() == "windows":
            assert QTest.qWaitForWindowExposed(window, 5000)
        bridge.refresh()
        wait_until(lambda: bridge.hasData and not bridge.busy)
        QTest.qWait(100)
        yield app, window, bridge, engine, db_path, model_path
    finally:
        bridge.close()
        bridge.wait_for_shutdown()
        if engine is not None:
            for root in engine.rootObjects():
                root.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        app.setQuitOnLastWindowClosed(previous_quit_policy)


def test_first_collapse_reversal_routes_and_real_data_refresh(quick_window):
    app, window, bridge, engine, db_path, model_path = quick_window
    sidebar = find_item(window, "sidebar")
    expanded = sidebar.width()
    capture(window, "home-expanded")
    assert "测试学科" in texts(window)
    assert not bridge.sidebarCollapsed
    menu_position = find_item(window, "menuToggle").mapToScene(QPointF(22, 21))
    click(window, find_item(window, "menuToggle"))
    wait_until(lambda: bridge.sidebarCollapsed and sidebar.width() < expanded - 50)
    QTest.qWait(280)
    compact = sidebar.width()
    capture(window, "home-collapsed")
    assert compact >= 48
    compact_position = find_item(window, "menuToggle").mapToScene(QPointF(22, 21))
    assert abs(menu_position.x() - compact_position.x()) < 1
    for _ in range(10):
        click(window, find_item(window, "menuToggle"))
        QTest.qWait(12)
    wait_until(lambda: abs(sidebar.width() - compact) < 1)
    for route in bridge.routes:
        button = find_item(window, "nav-" + route["key"])
        name, undefined = QQmlExpression(QQmlEngine.contextForObject(button), button,
                                         "Accessible.name").evaluate()
        assert not undefined and name == route["label"]
        click(window, button)
        wait_until(lambda: bridge.currentPage == route["key"])
        page = find_item(window, {"home": "homePage", "plan": "planPage", "assistant": "assistantPage", "knowledge": "knowledgePage", "settings": "settingsPage"}[route["key"]])
        assert page.width() > 0
        if not route["migrated"]:
            assert any("迁移" in text for text in texts(window))
    capture(window, "settings-boundary")
    bridge.navigate("home")
    add_learning_record({"date": "2026-10-04", "subject": "测试学科", "module": "测试模块",
                         "topic": "测试知识点", "activity": "review", "source": "outside_class",
                         "duration_minutes": 20}, db_path=db_path, model_path=model_path)
    QTest.keyClick(window, Qt.Key.Key_F5)
    wait_until(lambda: bridge.snapshot.get("recordCount") == 1 and not bridge.busy)
    assert any("测试知识点" in text for text in texts(window))
    capture(window, "home-refreshed")
    assert not engine._qml_warnings


def test_keyboard_focus_shortcuts_and_window_resize(quick_window):
    app, window, bridge, engine, _, _ = quick_window
    menu = find_item(window, "menuToggle")
    menu.forceActiveFocus()
    assert menu.hasActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    wait_until(lambda: bridge.sidebarCollapsed)
    QTest.qWait(280)
    QTest.keyClick(window, Qt.Key.Key_Tab)
    focused = window.activeFocusItem()
    assert focused is not None and focused != menu
    QTest.keyClick(window, Qt.Key.Key_Tab)
    assert window.activeFocusItem().objectName() == "nav-plan"
    QTest.keyClick(window, Qt.Key.Key_Return)
    wait_until(lambda: bridge.currentPage == "plan", timeout_ms=1000)
    QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
    wait_until(lambda: bridge.currentPage == "plan")
    QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    wait_until(lambda: bridge.currentPage == "home")
    QTest.keyClick(window, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    wait_until(lambda: not bridge.sidebarCollapsed)
    QTest.qWait(280)
    for width, height in ((800, 600), (1024, 768), (1600, 1000)):
        window.resize(width, height)
        QTest.qWait(80)
        page = find_item(window, "homePage")
        loader = find_item(window, "pageLoader")
        assert abs(loader.width() + find_item(window, "sidebar").width() - window.width()) < 2
        assert page.width() > 0 and page.height() == loader.height()
        assert find_item(window, "menuToggle").isVisible()
        for card in visual_items(page):
            if card.isVisible() and card.property("paperColor") is not None:
                left = card.mapToScene(QPointF(0, 0)).x()
                right = card.mapToScene(QPointF(card.width(), 0)).x()
                assert left >= loader.mapToScene(QPointF(0, 0)).x()
                assert right <= window.width() + 1, (width, card.width(), left, right)
        for route in bridge.routes:
            icon = find_item(window, "nav-icon-" + route["key"])
            label = find_item(window, "nav-label-" + route["key"])
            assert icon.width() == 21
            assert icon.mapToScene(QPointF(icon.width(), 0)).x() + 4 <= label.mapToScene(QPointF(0, 0)).x()
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            assert abs(window.devicePixelRatio() - float(os.environ.get("QT_SCALE_FACTOR", "1"))) < 0.01
        else:
            assert window.devicePixelRatio() >= 1
        capture(window, f"home-{width}x{height}")
    assert not engine._qml_warnings


@pytest.mark.parametrize("collapsed", [False, True])
def test_current_route_stays_highlighted_on_repeated_activation(quick_window, collapsed):
    _, window, bridge, engine, _, _ = quick_window

    def background_color(button):
        color, undefined = QQmlExpression(QQmlEngine.contextForObject(button), button,
                                          "background.color").evaluate()
        assert not undefined
        return color

    selected_color = background_color(find_item(window, "nav-home"))
    if collapsed:
        click(window, find_item(window, "menuToggle"))
        QTest.qWait(280)
    for route in bridge.routes:
        button = find_item(window, "nav-" + route["key"])
        click(window, button)
        wait_until(lambda: bridge.currentPage == route["key"])
        for activation in ("mouse", "mouse", "mouse", Qt.Key.Key_Space,
                           Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if activation == "mouse":
                click(window, button)
            else:
                button.forceActiveFocus()
                QTest.keyClick(window, activation)
            assert bridge.currentPage == route["key"]
            # Check the actual visible highlight, not just unchanged bridge state.
            for candidate in bridge.routes:
                highlighted = background_color(find_item(window, "nav-" + candidate["key"])) == selected_color
                assert highlighted == (candidate["key"] == bridge.currentPage)
                candidate_button = find_item(window, "nav-" + candidate["key"])
                accessible_selected, undefined = QQmlExpression(
                    QQmlEngine.contextForObject(candidate_button), candidate_button,
                    "Accessible.selected").evaluate()
                assert not undefined
                assert accessible_selected == highlighted
    assert not engine._qml_warnings
