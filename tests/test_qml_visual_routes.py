"""New overview actions still reach the real, reviewed business workspaces."""
from datetime import date

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from study_app.quick.plan_service import PlanService
from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, click, capture, visual_items
from test_qml_phase5 import reveal_home


def test_bundled_serif_is_resolved_for_window_and_note(quick_window):
    from PySide6.QtGui import QFontInfo, QRawFont
    _, window, _, _, _, _ = quick_window
    for item in (window, find_item(window, "homeQuickNote")):
        font = item.property("font")
        assert QFontInfo(font).family() == "Noto Serif SC"
        raw = QRawFont.fromFont(font)
        assert all(raw.supportsCharacter(ord(c)) for c in "和纸学习室计划0123ABC")


def test_home_quick_note_prefills_review_form_without_saving(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    note = find_item(window, "homeQuickNote")
    note.setProperty("text", "刚复习了集合，明天再看交集。")
    button = find_item(window, "homeRecordButton")
    reveal_home(window, button)
    click(window, button)
    wait_until(lambda: bridge.currentPage == "plan" and not bridge.workspace.busy)
    wait_until(lambda: find_item(window, "recordNote").property("text") == "刚复习了集合，明天再看交集。")
    assert not bridge.workspace.recordPreview
    assert not find_item(window, "saveReviewedRecord").property("enabled")
    assert not engine._qml_warnings


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_home_budget_refresh_and_record_route_use_real_services(quick_window):
    _, window, bridge, engine, db_path, model_path = quick_window
    assert bridge.snapshot["todayPlan"]["status"] == "empty"
    assert "90" not in str(find_item(window, "homeBudgetValue").property("text"))
    service = PlanService(db_path, model_path, bridge._service.records_path, today=date(2026, 10, 4))
    preview = service.preview_budget({"budget_minutes": 90})
    service.confirm_budget(preview["token"])
    QTest.keyClick(window, Qt.Key.Key_F5)
    wait_until(lambda: not bridge.busy and bridge.snapshot["todayPlan"]["status"] == "confirmed")
    assert str(find_item(window, "homeBudgetValue").property("text")) == "90"

    record = find_item(window, "homeRecordButton")
    reveal_home(window, record)
    record.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Return)
    wait_until(lambda: bridge.currentPage == "plan" and find_item(window, "planPage").property("section") == 1)
    wait_until(lambda: not bridge.workspace.busy)
    assert find_item(window, "recordNote").isVisible()
    assert find_item(window, "saveReviewedRecord").property("enabled") is False

    bridge.navigate("home")
    QTest.qWait(50)
    plan = find_item(window, "homePlanButton")
    reveal_home(window, plan)
    click(window, plan)
    wait_until(lambda: bridge.currentPage == "plan")
    assert find_item(window, "planPage").property("section") == 0
    bridge.navigate("home")
    QTest.qWait(50)
    assistant = find_item(window, "homeAssistantButton")
    reveal_home(window, assistant)
    click(window, assistant)
    wait_until(lambda: bridge.currentPage == "assistant")
    wait_until(lambda: not bridge.insights.busy)
    assert find_item(window, "assistantQuestion").isVisible()
    assert not engine._qml_warnings


@pytest.mark.parametrize("size", [(800, 600), (1280, 820), (1600, 1000)])
def test_every_route_keeps_paper_panels_inside_window_at_breakpoints(quick_window, size):
    _, window, bridge, engine, _, _ = quick_window
    window.resize(*size)
    QTest.qWait(120)
    for route in bridge.routes:
        bridge.navigate(route["key"])
        wait_until(lambda: not bridge.busy and not bridge.workspace.busy and not bridge.insights.busy and not bridge.settings.busy)
        QTest.qWait(100)
        loader = find_item(window, "pageLoader")
        for panel in visual_items(loader):
            if panel.isVisible() and panel.property("paperColor") is not None:
                left = panel.mapToScene(QPointF()).x()
                right = panel.mapToScene(QPointF(panel.width(), 0)).x()
                assert left >= loader.mapToScene(QPointF()).x() - 1, (route["key"], size, left)
                assert right <= window.width() + 1, (route["key"], size, right)
        capture(window, f"visual-{route['key']}-{size[0]}x{size[1]}")
    assert not engine._qml_warnings


def test_graph_resize_then_immediate_route_change_cancels_pending_layout(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    for cycle in range(6):
        bridge.navigate("knowledge")
        wait_until(lambda: not bridge.insights.busy)
        assert find_item(window, "graphPane").isVisible()
        window.resize(1600 if cycle % 2 else 800, 820)
        # Leave before the next render/layout frame can fit the graph.
        bridge.navigate("assistant")
        QTest.qWait(50)
        wait_until(lambda: not bridge.insights.busy)
    assert not engine._qml_warnings


@pytest.mark.parametrize("size", [(800, 600), (1280, 820), (1600, 1000)])
def test_plan_columns_and_settings_categories_at_breakpoints(quick_window, size):
    _, window, bridge, engine, _, _ = quick_window
    window.resize(*size)
    bridge.navigate("plan")
    wait_until(lambda: not bridge.workspace.busy)
    QTest.qWait(100)
    scope = find_item(window, "planScopeCard")
    budget = find_item(window, "budgetPlanCard")
    tasks = find_item(window, "budgetTaskListCard")
    assert scope.mapToScene(QPointF()).y() < budget.mapToScene(QPointF()).y()
    # Windows can clamp requested sizes to the monitor's logical work area
    # at high DPI. Check the actual available content width, not the request.
    columns = find_item(window, "planningMainColumns")
    wide = columns.width() >= 780
    assert columns.property("columns") == (2 if wide else 1)
    if wide:
        assert scope.width() > budget.width() * 1.9
        assert tasks.width() > budget.width()
        assert abs(budget.mapToScene(QPointF()).y() - tasks.mapToScene(QPointF()).y()) < 1
    else:
        assert tasks.mapToScene(QPointF()).y() < budget.mapToScene(QPointF()).y()
    capture(window, f"top-aligned-plan-{size[0]}x{size[1]}")

    bridge.navigate("settings")
    wait_until(lambda: not bridge.settings.busy)
    QTest.qWait(100)
    service = find_item(window, "settingsLlmCard")
    privacy = find_item(window, "settingsPrivacyCard")
    assert service.isVisible() and not privacy.isVisible()
    click(window, find_item(window, "settingsPrivacyTab"))
    QTest.qWait(100)
    assert privacy.isVisible() and not service.isVisible()
    assert privacy.mapToScene(QPointF()).x() >= 0
    assert privacy.mapToScene(QPointF(privacy.width(), 0)).x() <= window.width()
    capture(window, f"top-aligned-settings-{size[0]}x{size[1]}")
    assert not engine._qml_warnings

@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_plan_assistant_instances_and_drafts_survive_route_switch(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    bridge.navigate('plan')
    wait_until(lambda: not bridge.workspace.busy)
    plan = find_item(window, 'planPage')
    budget = find_item(window, 'budgetMinutes')
    budget.setProperty('text', '73')
    bridge.navigate('assistant')
    wait_until(lambda: not bridge.insights.busy)
    assistant = find_item(window, 'assistantPage')
    question = find_item(window, 'assistantQuestion')
    question.setProperty('text', '尚未发送的学习问题')
    bridge.navigate('plan')
    wait_until(lambda: not bridge.workspace.busy)
    assert find_item(window, 'planPage') is plan
    assert find_item(window, 'budgetMinutes') is budget
    assert budget.property('text') == '73'
    assert not assistant.isVisible()
    assert not find_item(window, 'budgetReviewed').property('checked')
    y = find_item(window, 'budgetTaskListCard').mapToScene(QPointF()).y()
    bridge.workspace.loadForPage()
    QTest.qWait(20)
    assert find_item(window, 'budgetTaskListCard').mapToScene(QPointF()).y() == y
    wait_until(lambda: not bridge.workspace.busy)
    bridge.navigate('assistant')
    wait_until(lambda: not bridge.insights.busy)
    assert find_item(window, 'assistantPage') is assistant
    assert question.property('text') == '尚未发送的学习问题'
    assert not bridge.insights.proposalPreview
    assert not engine._qml_warnings


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_large_task_list_keeps_geometry_and_only_creates_visible_rows(quick_window):
    from copy import deepcopy
    _, window, bridge, engine, _, _ = quick_window
    bridge.navigate('plan')
    wait_until(lambda: not bridge.workspace.busy)
    QTest.qWait(80)
    card = find_item(window, 'budgetTaskListCard')
    height = card.height()
    data = deepcopy(bridge.workspace.planData)
    data['tasks'] = [dict(title=f'性能测试任务 {i}', taskId=str(i), estimatedMinutes=20) for i in range(300)]
    bridge.workspace._data['plan'] = data
    bridge.workspace.planDataChanged.emit()
    QTest.qWait(120)
    task_list = find_item(window, 'budgetTaskList')
    assert task_list.property('count') == 300
    assert abs(card.height() - height) < 1
    rows = [item for item in visual_items(window.contentItem()) if item.objectName().startswith('budgetTaskRow_')]
    assert 1 <= len(rows) < 20
    task_list.setProperty('contentY', 4000)
    QTest.qWait(100)
    rows = [item for item in visual_items(window.contentItem()) if item.objectName().startswith('budgetTaskRow_')]
    assert any(int(item.objectName().split('_')[-1]) > 20 for item in rows)
    assert len(rows) < 20
    assert not engine._qml_warnings


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_revisiting_unchanged_pages_preserves_forms_and_does_not_toggle_busy(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    routes = [('plan', bridge.workspace), ('knowledge', bridge.insights), ('assistant', bridge.insights), ('settings', bridge.settings)]
    pages = {}
    for route, work in routes:
        bridge.navigate(route)
        wait_until(lambda: not work.busy)
        QTest.qWait(80)
        pages[route] = find_item(window, route + 'Page')
    busy = []
    for work in (bridge.workspace, bridge.insights, bridge.settings):
        work.stateChanged.connect(lambda work=work: busy.append(work.busy))
    for route, work in routes:
        bridge.navigate(route)
        QTest.qWait(160)
        assert pages[route] is find_item(window, route + 'Page')
        assert not work.busy
    assert not any(busy)
    assert not engine._qml_warnings


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_deferred_paper_surfaces_are_light_after_first_show_and_reentry(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    bridge.navigate('plan')
    wait_until(lambda: not bridge.workspace.busy)
    card = find_item(window, 'budgetTaskListCard')
    for cycle in range(2):
        if cycle:
            bridge.navigate('assistant')
            wait_until(lambda: not bridge.insights.busy)
            bridge.navigate('plan')
        QTest.qWait(120)
        picture = window.grabWindow()
        for name in ('budgetTaskListCard', 'budgetPlanCard'):
            paper = find_item(window, name)
            point = paper.mapToScene(QPointF(paper.width() / 2, 8))
            ratio = window.devicePixelRatio()
            shades = [picture.pixelColor(round((point.x() + dx) * ratio), round(point.y() * ratio)).lightness()
                      for dx in range(-10, 11)]
            assert sum(shades) / len(shades) > 180, (name, shades)
    assert not engine._qml_warnings


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
@pytest.mark.parametrize("size", [(800, 600), (1280, 850)])
def test_graph_geometry_stays_fixed_when_first_read_finishes(quick_window, monkeypatch, size):
    from threading import Event
    _, window, bridge, engine, _, _ = quick_window
    release = Event()
    original = bridge.insights._knowledge.load

    def delayed(*args, **kwargs):
        if not release.wait(10):
            raise TimeoutError("test did not release graph read")
        return original(*args, **kwargs)

    monkeypatch.setattr(bridge.insights._knowledge, "load", delayed)
    window.resize(*size)
    try:
        bridge.navigate("knowledge")
        # Inspect the hidden layout while the deliberately blocked first read
        # is pending; find_item waits for interactive first presentation.
        viewport = next(i for i in visual_items(window.contentItem())
                        if i.objectName() == "knowledgeGraphViewport")
        details = next(i for i in visual_items(window.contentItem())
                       if i.objectName() == "knowledgeTopicDetail")
        page_loader = next(i for i in visual_items(window.contentItem())
                           if i.objectName() == "pageLoader")
        QTest.qWait(120)
        assert bridge.insights.busy
        assert not page_loader.property("pagePresented")
        assert details.isVisible()
        before = (viewport.mapToScene(QPointF()), viewport.width(), viewport.height())
        release.set()
        wait_until(lambda: not bridge.insights.busy)
        assert find_item(window, "knowledgeGraphViewport") is viewport
        QTest.qWait(180)
        after = (viewport.mapToScene(QPointF()), viewport.width(), viewport.height())
        assert abs(before[0].x() - after[0].x()) < 1
        assert abs(before[0].y() - after[0].y()) < 1
        assert abs(before[1] - after[1]) < 1
        assert abs(before[2] - after[2]) < 1
        assert not engine._qml_warnings
    finally:
        release.set()


@pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)
def test_plan_hidden_controls_are_created_on_demand_and_retained(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    bridge.navigate("plan")
    wait_until(lambda: not bridge.workspace.busy)
    pane = find_item(window, "planningPane")
    names = {item.objectName() for item in visual_items(window.contentItem())}
    assert "planEstimateDetails" not in names
    assert "detailedPlanContent" not in names
    pane.setProperty("estimatesExpanded", True)
    estimates = find_item(window, "planEstimateDetails")
    assert find_item(window, "estimate-0").isVisible()
    pane.setProperty("estimatesExpanded", False)
    pane.setProperty("detailExpanded", True)
    detail = find_item(window, "detailedPlanContent")
    assert find_item(window, "previewDetailedPlan").isVisible()
    pane.setProperty("detailExpanded", False)
    pane.setProperty("estimatesExpanded", True)
    assert find_item(window, "planEstimateDetails") is estimates
    pane.setProperty("detailExpanded", True)
    assert find_item(window, "detailedPlanContent") is detail
    assert not engine._qml_warnings
