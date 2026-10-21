"""Retained geometry, tab drafts, and review boundaries during navigation."""
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest

from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, visual_items


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_hidden_routes_skip_sidebar_layout_and_resize_on_return(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    pages = []
    for route, work in (("plan", bridge.workspace), ("assistant", bridge.insights),
                        ("knowledge", bridge.insights), ("settings", bridge.settings)):
        bridge.navigate(route)
        page = find_item(window, route + "Page")
        wait_until(lambda: not work.busy)
        pages.append(page)
    bridge.navigate("home")
    before = [(p.width(), p.height()) for p in pages]
    width_events = []
    for p in pages:
        p.widthChanged.connect(lambda: width_events.append(1))
    bridge.toggleSidebar()
    QTest.qWait(280)
    window.resize(1000, 700)
    QTest.qWait(120)
    assert [(p.width(), p.height()) for p in pages] == before
    assert not width_events
    bridge.navigate("plan")
    plan = find_item(window, "planPage")
    QTest.qWait(80)
    assert plan is pages[0]
    assert plan.width() == find_item(window, "pageLoader").width()
    assert plan.height() == find_item(window, "pageLoader").height()
    assert not engine._qml_warnings


def test_sidebar_collapse_keeps_navigation_row_positions(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    nav = find_item(window, "nav-plan")
    y = nav.mapToScene(QPointF()).y()
    for _ in range(4):
        bridge.toggleSidebar()
        QTest.qWait(280)
        assert abs(nav.mapToScene(QPointF()).y() - y) < 1
    assert not engine._qml_warnings


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_plan_tabs_preserve_drafts_but_invalidate_reviews(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    bridge.navigate("plan")
    plan = find_item(window, "planPage")
    budget = find_item(window, "budgetMinutes")
    wait_until(lambda: not bridge.workspace.busy)
    budget.setProperty("text", "73")
    bridge.workspace.run("budget.preview", {"budget_minutes": 73})
    wait_until(lambda: not bridge.workspace.busy)
    assert bridge.workspace.budgetPreview.get("token")
    find_item(window, "budgetReviewed").setProperty("checked", True)
    assert find_item(window, "confirmBudget").property("enabled")

    plan.setProperty("section", 1)
    note = find_item(window, "recordNote")
    note.setProperty("text", "保留尚未保存的记录草稿")
    assert not bridge.workspace.budgetPreview
    plan.setProperty("section", 0)
    assert find_item(window, "budgetMinutes") is budget
    assert budget.property("text") == "73"
    assert not find_item(window, "budgetReviewed").property("checked")
    assert not find_item(window, "confirmBudget").property("enabled")
    plan.setProperty("section", 1)
    assert find_item(window, "recordNote") is note
    assert note.property("text") == "保留尚未保存的记录草稿"
    assert not find_item(window, "saveReviewedRecord").property("enabled")
    assert not engine._qml_warnings


def test_rapid_tab_changes_finish_without_replacing_created_panes(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    bridge.navigate("plan")
    plan = find_item(window, "planPage")
    first = find_item(window, "planningPane")
    for section in (1, 2, 3, 0, 3, 1, 0):
        plan.setProperty("section", section)
        QTest.qWait(8)
    assert find_item(window, "planningPane") is first
    for section, name in ((1, "recordsPane"), (2, "ojPane"), (3, "practicePane"), (0, "planningPane")):
        plan.setProperty("section", section)
        assert find_item(window, name).isVisible()
    assert len([i for i in visual_items(window.contentItem()) if i.objectName() == "planningPane"]) == 1
    assert not engine._qml_warnings


def test_home_reapplies_same_note_to_retained_record_draft(quick_window):
    from test_qml_window import click
    from test_qml_phase5 import reveal_home
    _, window, bridge, engine, _, _ = quick_window
    quick_note = find_item(window, "homeQuickNote")
    quick_note.setProperty("text", "今天复习了集合")
    for lap in range(2):
        if lap:
            bridge.navigate("home")
        button = find_item(window, "homeRecordButton")
        reveal_home(window, button)
        click(window, button)
        assert bridge.currentPage == "plan"
        note = find_item(window, "recordNote")
        assert note.property("text") == "今天复习了集合", (
            lap, bridge.currentPage, window.property("pendingRecordNote"),
            window.property("openRecordsAfterNavigation"),
            find_item(window, "planPage").property("recordNotePrefill"))
        note.setProperty("text", "在记录页改写了草稿")
    assert not bridge.workspace.recordPreview
    assert not find_item(window, "saveReviewedRecord").property("enabled")
    assert not engine._qml_warnings
