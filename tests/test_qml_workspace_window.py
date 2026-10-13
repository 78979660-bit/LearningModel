"""Drive actual QML controls through isolated persistent phase-two workflows."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtGui import QColor
from pypdf import PdfReader

from study_app.data.database import connect, get_active_study_plan, get_budgeted_day_plan, list_recent_records
from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, click, capture, visual_items

pytestmark = pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)


def reveal(window, item):
    parent = item.parentItem()
    while parent is not None:
        if parent.objectName() in {"planningPane", "recordsPane", "ojPane", "practicePane"}:
            flick = parent.property("contentItem")
            content = flick.property("contentItem")
            QTest.qWait(30)

            def scroll_into_view():
                # A refreshed Repeater may not have polished its content height
                # yet. Recompute from the live geometry before clicking.
                position = item.mapToItem(content, QPointF(0, 0))
                flick.setProperty("contentY", max(0, min(position.y() - 30,
                                    float(flick.property("contentHeight")) - flick.height())))
                visible_position = item.mapToItem(flick, QPointF(0, 0))
                window_position = item.mapToScene(QPointF(0, 0))
                return (visible_position.y() >= 0 and
                        visible_position.y() + item.height() <= flick.height() and
                        window_position.y() >= 0 and
                        window_position.y() + item.height() <= window.height())

            wait_until(scroll_into_view, timeout_ms=2000)
            return
        parent = parent.parentItem()


def activate(window, name):
    item = find_item(window, name)
    reveal(window, item)
    assert item.isVisible() and item.property("enabled")
    activation = QSignalSpy(item.clicked)
    before_position = item.mapToScene(QPointF(0, 0))
    click(window, item)
    assert activation.count() == 1, (name, before_position, item.mapToScene(QPointF(0, 0)),
                                      item.width(), item.height(), window.size())


def edit(window, name, text):
    item = find_item(window, name)
    reveal(window, item)
    item.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    # QTest.keyClicks cannot encode non-ASCII text on all Windows platforms.
    item.setProperty("text", text)
    item.textEdited.emit() if hasattr(item, "textEdited") else None


def ready(workspace):
    wait_until(lambda: not workspace.busy)
    assert not workspace.error, getattr(workspace, "_test_failure", workspace.error)


def open_workspace(window, bridge):
    activate(window, "nav-plan")
    workspace = bridge.workspace
    ready(workspace)
    assert workspace.planData["catalog"]["subjects"]
    original_execute = workspace._execute

    def execute(action, payload, scope):
        try:
            return original_execute(action, payload, scope)
        except Exception as error:
            # All data in this test window is synthetic. Include the service
            # failure in assertions without relaxing production redaction.
            workspace._test_failure = (action, payload, str(error))
            raise

    workspace._execute = execute
    return workspace


@pytest.mark.parametrize("empty_catalog", [False, True])
def test_scope_popup_highlight_remains_readable(quick_window, empty_catalog):
    _, window, bridge, engine, _, _ = quick_window
    workspace = open_workspace(window, bridge)
    if empty_catalog:
        workspace._data["plan"]["catalog"]["subjects"] = []
        workspace.planDataChanged.emit()
    scope = find_item(window, "planScope")
    assert scope.property("count") == (1 if empty_catalog else 2)
    click(window, scope)

    def value(expression):
        probe = QQmlExpression(QQmlEngine.contextForObject(scope), scope, expression)
        result, undefined = probe.evaluate()
        assert not probe.hasError() and not undefined
        return result

    wait_until(lambda: value("popup.visible"))
    QTest.qWait(100)
    assert value("popup.contentItem.itemAtIndex(0).text") == "全部活动学科"
    assert value("popup.contentItem.itemAtIndex(0).highlighted")
    foreground = QColor(value("popup.contentItem.itemAtIndex(0).contentItem.color"))
    background = QColor(value("popup.contentItem.itemAtIndex(0).background.color"))

    def luminance(color):
        channels = [color.redF(), color.greenF(), color.blueF()]
        channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(channels, (0.2126, 0.7152, 0.0722)))

    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    assert (light + 0.05) / (dark + 0.05) >= 4.5
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not engine._qml_warnings


def test_budget_preview_confirmation_and_real_plan_persistence(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    workspace = open_workspace(window, bridge)
    capture(window, "phase2-planning-top")
    edit(window, "budgetMinutes", "90")
    assert not find_item(window, "confirmBudget").property("enabled")
    activate(window, "previewBudget")
    ready(workspace)
    assert workspace.budgetPreview["token"] and workspace.budgetPreview["rows"]
    assert get_budgeted_day_plan("2026-10-04", db_path=db_path) is None
    assert not find_item(window, "confirmBudget").property("enabled")
    activate(window, "budgetReviewed")
    activate(window, "confirmBudget")
    ready(workspace)
    saved = get_budgeted_day_plan("2026-10-04", db_path=db_path)
    assert saved["budget_minutes"] == 90 and saved["items"]
    assert not workspace.budgetPreview
    wait_until(lambda: find_item(window, "budgetTaskStatus_0").property("text") == "已安排")
    wait_until(lambda: not bridge.busy)
    assert bridge.snapshot["todayPlan"]["status"] == "confirmed"
    capture(window, "phase2-confirmed-budget")

    activate(window, "detailedPlanToggle")
    activate(window, "previewDetailedPlan")
    ready(workspace)
    assert workspace.planPreview["items"]
    assert get_active_study_plan(None, db_path=db_path) is None
    activate(window, "planReviewed")
    activate(window, "confirmDetailedPlan")
    ready(workspace)
    saved_plan = get_active_study_plan(None, db_path=db_path)
    assert saved_plan and saved_plan["items"]
    check = next(i for i in workspace.planData["currentPlan"]["items"] if i["itemType"] == "check")
    activate(window, "plan-check-" + str(check["id"]))
    ready(workspace)
    persisted = get_active_study_plan(None, db_path=db_path)
    assert next(i for i in persisted["items"] if i["id"] == check["id"])["checked"]
    homework = next(i for i in workspace.planData["currentPlan"]["items"] if i["itemType"] == "result")
    before = len(list_recent_records(db_path))
    activate(window, "feedback-correct-" + str(homework["id"]))
    ready(workspace)
    assert len(list_recent_records(db_path)) == before + 1
    assert not find_item(window, "feedback-correct-" + str(homework["id"])).property("enabled")
    assert not engine._qml_warnings


def test_keyboard_scope_refresh_and_tabs_discard_reviewed_previews(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    workspace = open_workspace(window, bridge)
    scope = find_item(window, "planScope")
    scope.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Down)
    ready(workspace)
    assert scope.property("currentIndex") == 1
    assert workspace.planData["catalog"]["selectedScope"]["subject"] == "测试学科"
    QTest.keyClick(window, Qt.Key.Key_F5)
    ready(workspace)
    assert find_item(window, "planScope").property("currentIndex") == 1
    edit(window, "budgetMinutes", "60")
    field = find_item(window, "budgetMinutes")
    QTest.keyClick(window, Qt.Key.Key_Tab)
    assert not field.hasActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert field.hasActiveFocus()
    activate(window, "previewBudget")
    ready(workspace)
    activate(window, "budgetReviewed")
    assert find_item(window, "confirmBudget").property("enabled")
    edit(window, "budgetMinutes", "61")
    assert not workspace.budgetPreview and not find_item(window, "confirmBudget").property("enabled")
    assert get_budgeted_day_plan("2026-10-04", db_path=db_path) is None
    for _ in range(3):
        for tab in ("tab-records", "tab-oj", "tab-practice", "tab-plan"):
            activate(window, tab)
        assert find_item(window, "planScope").property("currentIndex") == 1
    activate(window, "nav-home")
    activate(window, "nav-plan")
    ready(workspace)
    assert find_item(window, "planScope").property("currentIndex") == 1
    assert not engine._qml_warnings


def test_record_review_save_and_oj_explicit_mapping_submission(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    workspace = open_workspace(window, bridge)
    activate(window, "tab-records")
    capture(window, "phase2-records-top")
    edit(window, "recordNote", "isolated GUI learning record")
    edit(window, "recordDate", "2026-10-04")
    edit(window, "recordDuration", "20")
    edit(window, "recordAnswerResult", "需要复核的一道题")
    activate(window, "previewRecord")
    ready(workspace)
    assert workspace.recordPreview["token"]
    assert list_recent_records(db_path) == []
    assert not find_item(window, "saveReviewedRecord").property("enabled")
    assert workspace.recordPreview["draft"]["problems"]
    edit(window, "review-title-0", "reviewed title")
    edit(window, "review-statement-0", "Solve x + 1 = 4.")
    edit(window, "review-correctness-0", "75")
    edit(window, "review-difficulty-0", "60")
    activate(window, "recordReviewed")
    activate(window, "saveReviewedRecord")
    ready(workspace)
    assert list_recent_records(db_path)[0]["note"] == "isolated GUI learning record"
    reviewed_problem = workspace.lastResult["record"]["problems"][0]
    assert reviewed_problem["title"] == "reviewed title"
    assert reviewed_problem["statement"] == "Solve x + 1 = 4."
    assert reviewed_problem["correctness"] == 75 and reviewed_problem["difficulty_score"] == 60
    wait_until(lambda: not bridge.busy)
    assert bridge.snapshot["recordCount"] == 1
    capture(window, "phase2-saved-record")

    activate(window, "tab-oj")
    capture(window, "phase2-oj-top")
    edit(window, "ojPlatform", "local")
    edit(window, "ojExternalId", "qml-001")
    edit(window, "ojProblemTitle", "QML isolated problem")
    activate(window, "saveOJProblem")
    ready(workspace)
    assert workspace.ojData["problems"][0]["title"] == "QML isolated problem"
    assert not find_item(window, "saveOJAttempt").property("enabled")
    activate(window, "oj-topic-0")
    activate(window, "ojMappingReviewed")
    activate(window, "saveOJMapping")
    ready(workspace)
    assert workspace.ojData["problems"][0]["mappings"]
    edit(window, "ojSeconds", "120")
    activate(window, "ojAttemptReviewed")
    activate(window, "saveOJAttempt")
    ready(workspace)
    attempt = workspace.ojData["attempts"][0]
    assert attempt["duration_seconds"] == 120 and attempt["result"] == "accepted"
    assert workspace.ojData["problems"][0]["attempt_count"] == 1
    capture(window, "phase2-oj-submission")
    assert not engine._qml_warnings


@pytest.mark.parametrize("score_text,expected", [("", "未填写"), ("0", "0"), ("75", "75")])
def test_record_preview_displays_optional_score_through_real_bridge(quick_window, score_text, expected):
    from test_qml_window import texts

    _, window, bridge, engine, db_path, _ = quick_window
    workspace = open_workspace(window, bridge)
    activate(window, "tab-records")
    edit(window, "recordNote", "optional score preview regression")
    edit(window, "recordScore", score_text)
    activate(window, "previewRecord")
    ready(workspace)
    assert workspace.recordPreview["token"]
    assert workspace.recordPreview["draft"]["score"] == (float(score_text) if score_text else None)
    wait_until(lambda: any("分数：" + expected in text for text in texts(window)))
    assert not any("undefined" in text for text in texts(window))
    assert list_recent_records(db_path) == []
    assert not engine._qml_warnings


def test_local_pdf_outputs_and_practice_prompt_cannot_dispatch_desktop_generation(quick_window, tmp_path):
    _, window, bridge, engine, db_path, _ = quick_window
    with connect(db_path) as connection:
        connection.execute("INSERT INTO practice_templates(template_id,title,description) VALUES ('QML-TEST','QML worksheet','isolated fixture')")
        source = connection.execute("INSERT INTO practice_sources(source_type,title) VALUES ('manual','isolated') RETURNING id").fetchone()["id"]
        for index in range(4):
            connection.execute("""INSERT INTO practice_problems(template_id,title,statement,answer_outline,difficulty_score,subject_hint,topic_hint,source_id)
                VALUES ('QML-TEST',?,?,'x = 3',55,'测试学科','测试知识点',?)""",
                (f"Question {index}", f"Solve x + {index} = {index + 3}.", source))
    workspace = open_workspace(window, bridge)
    workspace._practice.output_dir = tmp_path / "outputs"
    activate(window, "tab-practice")
    capture(window, "phase2-practice-top")
    templates = [item for item in workspace.practiceOptions["templates"] if item["localAllowed"] and (not item["subject"] or item["subject"] == "测试学科")]
    index = next(i for i, item in enumerate(templates) if item["id"] == "QML-TEST")
    find_item(window, "practiceTemplate").setProperty("currentIndex", index)
    activate(window, "generateLocalPDF")
    ready(workspace)
    assert workspace.practiceData["status"] == "generated", workspace.practiceData
    path = workspace._practice.artifact_path(workspace.practiceData["questionToken"])
    assert path.is_file() and len(PdfReader(path).pages) >= 1
    assert workspace.practiceData["questionCount"] == 2
    assert str(path) not in json.dumps(workspace.practiceData)
    object_names = {str(item.objectName()) for item in visual_items(window.contentItem())}
    assert "prepareChatGPT" not in object_names
    assert "executeChatGPT" not in object_names

    dispatched = []
    prepare_prompt = workspace._practice.prepare_practice_prompt
    workspace._practice.prepare_practice_prompt = lambda payload: dispatched.append(payload)
    workspace.run("practice.execute", {"confirmed": True})
    assert workspace.error == "未支持的操作。"
    assert dispatched == []
    workspace._practice.prepare_practice_prompt = prepare_prompt

    activate(window, "preparePracticePrompt")
    ready(workspace)
    assert workspace.practicePreview["prompt"] and workspace.practicePreview["token"]
    assert "模板编号：QML-TEST" in workspace.practicePreview["prompt"]
    assert "参考难度：55/100" in workspace.practicePreview["prompt"]
    assert "题量：2" in workspace.practicePreview["prompt"]
    assert workspace.practicePreview["copyable"] is True
    capture(window, "phase2-practice-preview")
    for size in ((800, 600), (1024, 768), (1600, 1000)):
        window.resize(*size)
        QTest.qWait(50)
        page = find_item(window, "planPage")
        for item in visual_items(page):
            if item.isVisible() and item.property("paperColor") is not None:
                assert item.width() <= page.width()
    assert not engine._qml_warnings
