"""Actual phase-three Quick controls against isolated repositories and engines."""
from __future__ import annotations

import json
import re
from copy import deepcopy

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest

from study_app.core.learning_assistant_policy import save_privacy_settings
from study_app.data.database import connect_readonly
from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, click, capture, texts

pytestmark = pytest.mark.parametrize("quick_window", ["phase2"], indirect=True)


def ready(work):
    wait_until(lambda: not work.busy)
    assert not work.error, (work.error, work.receipt)


def reveal(item):
    parent = item.parentItem()
    while parent is not None:
        if parent.objectName() in {"assistantScroll", "assistantWorkspaceScroll", "knowledgeAlertsScroll"}:
            flick = parent.property("contentItem")
            content = flick.property("contentItem")
            QTest.qWait(30)

            def positioned():
                point = item.mapToItem(content, QPointF(0, 0))
                flick.setProperty("contentY", max(0, min(point.y() - 24,
                                  float(flick.property("contentHeight")) - flick.height())))
                visible = item.mapToItem(flick, QPointF(0, 0))
                return visible.y() >= 0 and visible.y() + item.height() <= flick.height()

            wait_until(positioned, timeout_ms=2000)
            return
        parent = parent.parentItem()


def activate(window, name):
    item = find_item(window, name)
    reveal(item)
    assert item.isVisible() and item.property("enabled"), name
    spy = QSignalSpy(item.clicked)
    click(window, item)
    assert spy.count() == 1, name


def open_assistant(window, bridge, db_path):
    # Explicit local mock advisor is test configuration, never an app default.
    save_privacy_settings({"advisor_mode": "mock", "allow_external_intent": False}, db_path)
    activate(window, "nav-assistant")
    ready(bridge.insights)
    assert bridge.insights.assistantData["privacy"]["advisor_mode"] == "mock"
    return bridge.insights


def test_local_query_real_graph_keyboard_zoom_and_refresh(quick_window):
    _, window, bridge, engine, db_path, model_path = quick_window
    work = open_assistant(window, bridge, db_path)
    find_item(window, "assistantQuestion").setProperty("text", "当前学习计划是什么？")
    assert find_item(window, "assistantExternal").property("checked")
    activate(window, "assistantExternal")
    activate(window, "assistantAsk")
    ready(work)
    assert work.answer["answer"] and not work.answer["external_used"]
    assert not find_item(window, "assistantAnswer-evidence").isVisible()
    activate(window, "assistantAnswerDetails")
    for key in ("answer", "evidence", "source"):
        body = find_item(window, "assistantAnswer-" + key)
        assert body.isVisible() and body.height() > 0
        assert body.property("plainText" if key == "answer" else "text") == "\n".join(work.answer["answer"][key])
    assert not work.assistantData["journal"]
    capture(window, "phase3-assistant-local-query")

    activate(window, "nav-knowledge")
    ready(work)
    graph = work.knowledgeData["graph"]
    assert graph["nodes"] and graph["details"]
    assert graph["nodes"][0]["label"] == "测试模块"
    node_name = "knowledgeGraphNode_" + re.sub(r"[^A-Za-z0-9_]", "_", graph["nodes"][0]["key"])
    activate(window, node_name)
    assert any("测试知识点" in value for value in texts(window))
    pane = find_item(window, "graphPane")
    before = pane.property("zoomFactor")
    activate(window, "knowledgeGraphZoomInButton")
    assert pane.property("zoomFactor") > before
    activate(window, "knowledgeGraphZoomOutButton")
    node = find_item(window, node_name)
    node.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Down)
    assert pane.property("selectedKey") == graph["nodes"][0]["key"]
    capture(window, "phase3-knowledge-real-graph")

    from study_app.data.database import add_learning_record
    module_key = graph["nodes"][0]["key"]
    before_evidence = graph["details"][module_key]["topics"][0]["evidenceVersion"]
    add_learning_record({"date": "2026-10-04", "subject": "测试学科", "module": "测试模块", "topic": "测试知识点",
                         "activity": "exercise", "source": "outside_class", "duration_minutes": 20,
                         "problems": [{"title": "测试知识点 隔离错题", "status": "wrong", "correctness": 0.0}]},
                        db_path=db_path, model_path=model_path)
    QTest.keyClick(window, Qt.Key.Key_F5)
    ready(work)
    assert work.knowledgeData["graph"]["details"][module_key]["topics"][0]["evidenceVersion"] != before_evidence
    capture(window, "phase3-knowledge-refreshed")
    assert not engine._qml_warnings


@pytest.mark.parametrize("value", [["实际返回的第一行", "第二行依据"], "实际返回的单行"])
def test_external_answer_body_renders_python_sequences_and_strings(quick_window, value):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_assistant(window, bridge, db_path)
    work._data["answer"] = {"answer": {"answer": value, "evidence": ["隔离数据依据"],
                                     "source": ["外部响应结构"]},
                            "external_requested": True, "external_used": True, "external_error": ""}
    work.dataChanged.emit()
    QTest.qWait(50)
    expected = "\n".join(value) if isinstance(value, list) else value
    body = find_item(window, "assistantAnswer-answer")
    assert body.isVisible() and body.height() > 0 and body.property("plainText") == expected
    assert find_item(window, "assistantAnswer-evidence").property("text") == "隔离数据依据"
    assert find_item(window, "assistantAnswer-source").property("text") == "外部响应结构"
    assert not engine._qml_warnings


def test_graph_selection_binding_survives_search_subject_switch_and_removed_node(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    activate(window, "nav-knowledge")
    ready(bridge.insights)
    work = bridge.insights
    page = find_item(window, "knowledgePage")
    pane = find_item(window, "graphPane")
    payload = deepcopy(work.knowledgeData)
    graph = payload["graph"]
    first = deepcopy(graph["nodes"][0])
    first_key = first["key"]
    second = deepcopy(first)
    second.update(
        key="phase3_second_module",
        label="第二章节",
        order=1,
        state="partial",
        position={"x": 332, "y": 40},
    )
    graph["nodes"].append(second)
    graph["details"][second["key"]] = deepcopy(graph["details"][first_key])
    graph["details"][second["key"]]["key"] = second["key"]
    graph["details"][second["key"]]["label"] = second["label"]
    graph["width"] = 558
    payload["subjects"].append({"key": "phase3_second_subject", "label": "第二学科", "hasCurrentStructure": True})
    work._data["knowledge"] = payload
    work.knowledgeDataChanged.emit()
    QTest.qWait(60)

    first_button_name = "knowledgeGraphNode_" + re.sub(r"[^A-Za-z0-9_]", "_", first_key)
    activate(window, first_button_name)
    assert page.property("selectedModuleKey") == first_key
    assert pane.property("selectedKey") == first_key

    second_button_name = "knowledgeGraphNode_phase3_second_module"
    second_button = find_item(window, second_button_name)
    second_button.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Left)
    assert page.property("selectedModuleKey") == first_key
    assert pane.property("selectedKey") == first_key
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert page.property("selectedModuleKey") == "phase3_second_module"
    assert pane.property("selectedKey") == "phase3_second_module"

    search = find_item(window, "knowledgeGraphSearch")
    search.setProperty("text", "第二章节")
    QTest.qWait(40)
    assert find_item(window, second_button_name).isVisible()
    search.setProperty("text", "")

    # A new subject snapshot replaces the selected key. Both parent and pane
    # must follow the route-owned selection rather than retaining an old key.
    switched = deepcopy(payload)
    switched["selectedSubjectKey"] = "phase3_second_subject"
    switched_graph = switched["graph"]
    switched_graph["subjectKey"] = "phase3_second_subject"
    switched_graph["subjectLabel"] = "第二学科"
    third = deepcopy(second)
    third.update(key="phase3_second_subject_module", label="第二学科章节", position={"x": 42, "y": 40})
    switched_graph["nodes"] = [third]
    switched_graph["details"] = {third["key"]: deepcopy(graph["details"][second["key"]])}
    switched_graph["details"][third["key"]]["key"] = third["key"]
    switched_graph["details"][third["key"]]["label"] = third["label"]
    work._data["knowledge"] = switched
    work.knowledgeDataChanged.emit()
    wait_until(lambda: page.property("selectedSubjectKey") == "phase3_second_subject", timeout_ms=2000)
    assert page.property("selectedSubjectKey") == "phase3_second_subject"
    assert page.property("selectedModuleKey") == "phase3_second_subject_module"
    assert pane.property("selectedKey") == "phase3_second_subject_module"

    # If refresh removes the selected chapter, select the first remaining node
    # and propagate that choice through the pane binding.
    replacement = deepcopy(third)
    replacement.update(key="phase3_replacement_module", label="替换章节")
    switched_graph["nodes"] = [replacement]
    switched_graph["details"] = {replacement["key"]: deepcopy(switched_graph["details"][third["key"]])}
    switched_graph["details"][replacement["key"]]["key"] = replacement["key"]
    switched_graph["details"][replacement["key"]]["label"] = replacement["label"]
    work._data["knowledge"] = switched
    work.knowledgeDataChanged.emit()
    wait_until(lambda: page.property("selectedModuleKey") == "phase3_replacement_module", timeout_ms=2000)
    assert page.property("selectedModuleKey") == "phase3_replacement_module"
    assert pane.property("selectedKey") == "phase3_replacement_module"
    assert not engine._qml_warnings


@pytest.mark.parametrize("action", ["handle", "snooze"])
def test_alert_confirmation_saves_transition_and_audit(quick_window, action):
    from dataclasses import replace
    from datetime import date, timedelta
    from study_app.core.knowledge_alerts import reconcile_knowledge_alerts
    from study_app.core.knowledge_states import classify_topic
    from study_app.data.database import connect, list_knowledge_alerts, list_knowledge_alert_events
    from test_f2_i06_alert_handle import failure_insight

    _, window, bridge, engine, db_path, _ = quick_window
    today = date(2026, 10, 4)
    with connect_readonly(db_path) as connection:
        topic_id = connection.execute("SELECT id FROM topics LIMIT 1").fetchone()[0]
    insight = failure_insight(topic_id)
    error = replace(insight.recent_error, occurred_on=today - timedelta(days=1))
    insight = replace(insight, subject_name="测试学科", module_name="测试模块", topic_name="测试知识点",
                      recent_error=error, last_observation_date=error.occurred_on,
                      as_of_date=today, recommended_date=today)
    with connect(db_path) as connection:
        alert_id = reconcile_knowledge_alerts((classify_topic(insight, {}),), today, connection).created_ids[0]
    activate(window, "nav-knowledge")
    work = bridge.insights
    ready(work)
    activate(window, "knowledgeAlertsTabButton")
    control = "knowledgeAlertHandle_" if action == "handle" else "knowledgeAlertSnooze_"
    activate(window, control + str(alert_id))
    assert list_knowledge_alerts(db_path=db_path)[0].status == "active"
    activate(window, "knowledgeAlertCancelConfirmButton")
    assert list_knowledge_alerts(db_path=db_path)[0].status == "active"
    activate(window, control + str(alert_id))
    if action == "snooze":
        date_field = find_item(window, "knowledgeAlertSnoozeDateField")
        date_field.setProperty("text", "2026-02-30")
        assert not find_item(window, "knowledgeAlertActionConfirmButton").property("enabled")
        date_field.setProperty("text", "2026-10-06")
    activate(window, "knowledgeAlertActionConfirmButton")
    ready(work)
    alert = list_knowledge_alerts(db_path=db_path)[0]
    assert alert.status == ("handled" if action == "handle" else "snoozed")
    events = list_knowledge_alert_events(db_path=db_path)
    assert events[-1].to_status == alert.status and events[-1].actor == "desktop_local_user"
    assert any(row["id"] == alert_id and row["status"] == alert.status for row in work.knowledgeData["alerts"])
    capture(window, "phase3-alert-" + action)
    assert not engine._qml_warnings


def test_assistant_attachment_review_commit_and_undo_real_record(quick_window):
    _, window, bridge, engine, db_path, model_path = quick_window
    work = open_assistant(window, bridge, db_path)
    attachment = model_path.parent / "phase3-homework.txt"
    attachment.write_text("第1题 测试知识点：计算基础题\n解：过程完整，结论正确\n\n"
                          "第2题 测试知识点：计算进阶题\n解：过程完整，结论正确\n", encoding="utf-8")
    find_item(window, "assistantPage").setProperty("attachments", [attachment.as_uri()])
    find_item(window, "assistantQuestion").setProperty("text", "测试学科 测试知识点 作业2题全对，2026-10-04 做完了")
    activate(window, "assistantPreview")
    ready(work)
    assert work.proposalPreview["token"] and work.proposalPreview["confirmationAllowed"]
    assert not find_item(window, "assistantConfirm").property("enabled")
    with connect_readonly(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0] == 0
    capture(window, "phase3-assistant-review")
    activate(window, "assistantReviewed")
    activate(window, "assistantConfirm")
    ready(work)
    assert work.receipt["saved"], work.receipt
    record_id = work.receipt["recordId"]
    with connect_readonly(db_path) as connection:
        record = json.loads(connection.execute("SELECT raw_json FROM learning_records WHERE id=?", (record_id,)).fetchone()[0])
    assert len(record["problems"]) == 2 and not record.get("_revoked")
    assert not work.proposalPreview
    wait_until(lambda: not bridge.busy)
    assert bridge.snapshot["recordCount"] == 1

    activate(window, "assistantMoreActions")
    activate(window, "assistantUndoPreview")
    ready(work)
    assert work.undoPreview["available"]
    assert not find_item(window, "assistantUndo").property("enabled")
    activate(window, "assistantUndoReviewed")
    activate(window, "assistantUndo")
    ready(work)
    assert work.receipt["saved"], work.receipt
    with connect_readonly(db_path) as connection:
        record = json.loads(connection.execute("SELECT raw_json FROM learning_records WHERE id=?", (record_id,)).fetchone()[0])
    assert record["_revoked"]
    capture(window, "phase3-assistant-undone")
    assert not engine._qml_warnings


def test_edit_and_route_change_invalidate_review_then_resize(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_assistant(window, bridge, db_path)
    question = find_item(window, "assistantQuestion")
    question.setProperty("text", "测试学科 测试知识点 复习了，2026-10-04")
    activate(window, "assistantPreview")
    ready(work)
    assert work.proposalPreview["token"]
    question.setProperty("text", "测试学科 测试知识点 复习了，2026-10-03")
    assert not work.proposalPreview
    activate(window, "assistantPreview")
    ready(work)
    assert work.proposalPreview["token"]
    activate(window, "nav-home")
    assert not work.proposalPreview
    for page in ("assistant", "knowledge"):
        activate(window, "nav-" + page)
        ready(work)
        for width, height in ((800, 600), (1600, 1000)):
            window.resize(width, height)
            QTest.qWait(120)
            loader = find_item(window, "pageLoader")
            content = find_item(window, page + "Page")
            assert content.width() == loader.width() and content.height() == loader.height()
            if page == "knowledge":
                detail = find_item(window, "knowledgeTopicDetail")
                point = detail.mapToScene(QPointF(0, 0))
                assert detail.isVisible() and detail.height() > 0
                assert point.y() >= 0 and point.y() + detail.height() <= window.height() + 1
                assert find_item(window, "knowledgeGraphViewport").height() >= 60
            capture(window, "phase3-" + page + f"-{width}x{height}")
    assert not engine._qml_warnings


def test_graph_readable_overview_and_expanded_canvas(quick_window):
    _, window, bridge, engine, _, _ = quick_window
    window.resize(1600, 1000)
    activate(window, "nav-knowledge")
    ready(bridge.insights)
    page = find_item(window, "knowledgePage")
    pane = find_item(window, "graphPane")
    viewport = find_item(window, "knowledgeGraphViewport")
    payload = deepcopy(bridge.insights.knowledgeData)
    graph = payload["graph"]
    template = deepcopy(graph["nodes"][0])
    labels = ["集合", "关系", "函数", "命题逻辑", "谓词逻辑", "证明方法"]
    positions = [(42, 40), (42, 330), (332, 420), (332, 185), (622, 40), (622, 330)]
    graph["nodes"] = [dict(template, key=f"visual_{i}", label=label,
                           position={"x": xy[0], "y": xy[1]})
                      for i, (label, xy) in enumerate(zip(labels, positions))]
    graph["edges"] = [
        {"from": "visual_0", "to": "visual_3", "relationType": "strong_prerequisite"},
        {"from": "visual_1", "to": "visual_2", "relationType": "strong_prerequisite"},
        {"from": "visual_3", "to": "visual_4", "relationType": "strong_prerequisite"},
        {"from": "visual_3", "to": "visual_5", "relationType": "strong_prerequisite"}]
    graph.update(width=850, height=600)
    bridge.insights._data["knowledge"] = payload
    bridge.insights.knowledgeDataChanged.emit()
    wait_until(lambda: pane.property("selectedKey") == "visual_0")
    QTest.qWait(100)
    assert viewport.width() > page.width() * .65
    assert viewport.height() > page.height() * .65
    capture(window, "knowledge-six-chapters")
    initial_width = viewport.width()
    activate(window, "knowledgeDetailsButton")
    QTest.qWait(100)
    assert viewport.width() > initial_width + 200
    capture(window, "knowledge-expanded-canvas")
    activate(window, "knowledgeDetailsButton")
    for i, node in enumerate(graph["nodes"]):
        node["position"] = {"x": 42 + i * 500, "y": 40}
    graph.update(width=2900, height=232)
    bridge.insights._data["knowledge"] = deepcopy(payload)
    bridge.insights.knowledgeDataChanged.emit()
    QTest.qWait(100)
    assert pane.property("zoomFactor") >= .85
    activate(window, "knowledgeGraphFitButton")
    QTest.qWait(100)
    assert pane.property("zoomFactor") < .85
    activate(window, "knowledgeGraphReadableButton")
    QTest.qWait(100)
    assert pane.property("zoomFactor") >= .85
    search = find_item(window, "knowledgeGraphSearch")
    search.setProperty("text", "证明方法")
    QTest.qWait(100)
    node = find_item(window, "knowledgeGraphNode_visual_5")
    center = node.mapToItem(viewport, QPointF(node.width()/2, node.height()/2))
    assert abs(center.x() - viewport.width()/2) < 2
    assert abs(center.y() - viewport.height()/2) < 2
    assert not engine._qml_warnings


def test_chat_enter_shift_enter_fixed_composer_and_retained_turns(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_assistant(window, bridge, db_path)
    question = find_item(window, "assistantQuestion")
    question.setProperty("text", "测试学科有哪些需要加强的地方")
    question.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Return)
    ready(work)
    assert len(work.conversation) == 2
    assert question.property("text") == ""
    assert not find_item(window, "assistantAnswer-evidence").isVisible()
    question.setProperty("text", "第一行")
    question.setProperty("cursorPosition", len("第一行"))
    question.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert question.property("text") == "第一行\n"
    assert len(work.conversation) == 2
    question.setProperty("text", "测试学科今天先复习什么")
    QTest.keyClick(window, Qt.Key.Key_Enter)
    ready(work)
    assert len(work.conversation) == 4
    assert work.conversation[0]["text"] == "测试学科有哪些需要加强的地方"
    assert find_item(window, "assistantText-user-1").property("text") == work.conversation[0]["text"]
    activate(window, "nav-home")
    activate(window, "nav-assistant")
    ready(work)
    assert len(work.conversation) == 4
    for width, height in ((800, 600), (1600, 1000)):
        window.resize(width, height)
        QTest.qWait(100)
        composer = find_item(window, "assistantComposer")
        point = composer.mapToScene(QPointF(0, 0))
        assert point.y() >= 0 and point.y() + composer.height() <= window.height() + 1
        assert find_item(window, "assistantAsk").isVisible()
    assert not engine._qml_warnings


def test_chat_ime_preedit_return_does_not_submit(quick_window):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QInputMethodEvent
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_assistant(window, bridge, db_path)
    question = find_item(window, "assistantQuestion")
    question.setProperty("text", "测试学科需要")
    question.setProperty("cursorPosition", len("测试学科需要"))
    question.forceActiveFocus()
    QCoreApplication.sendEvent(question, QInputMethodEvent("加强", []))
    assert question.property("inputMethodComposing")
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert not work.busy and not work.conversation
    commit = QInputMethodEvent()
    commit.setCommitString("加强")
    QCoreApplication.sendEvent(question, commit)
    assert not question.property("inputMethodComposing")
    QTest.keyClick(window, Qt.Key.Key_Return)
    ready(work)
    assert work.conversation[0]["text"] == "测试学科需要加强"
    assert len(work.conversation) == 2
    assert not engine._qml_warnings


def test_assistant_prefers_external_by_default_and_keeps_local_override_on_navigation(quick_window, monkeypatch):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_assistant(window, bridge, db_path)
    from study_app.ai import natural_query, providers
    calls = []
    def external_answer(question, state, records, *, db_path=None):
        calls.append(question)
        return {"answer": ["先从你最不熟的内容开始。"], "evidence": ["已有学习记录"], "source": ["隔离外部模型替身"]}
    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda *args, **kwargs: True)
    monkeypatch.setattr(natural_query, "answer_query_with_llm", external_answer)
    assert find_item(window, "assistantExternal").property("checked")
    question = find_item(window, "assistantQuestion")
    question.setProperty("text", "测试学科先复习什么")
    question.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Return)
    ready(work)
    assert calls == ["测试学科先复习什么"]
    assert work.answer["external_requested"] and work.answer["external_used"]
    activate(window, "assistantExternal")
    activate(window, "nav-home")
    activate(window, "nav-assistant")
    ready(work)
    assert not find_item(window, "assistantExternal").property("checked")
    question.setProperty("text", "只看本地记录")
    activate(window, "assistantAsk")
    ready(work)
    assert not work.answer["external_requested"] and not work.answer["external_used"]
    assert len(calls) == 1
    assert not engine._qml_warnings


def test_assistant_shows_actual_model_readiness_and_settings_entry(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    work = open_assistant(window, bridge, db_path)
    status = find_item(window, "assistantModelStatus")
    assert "外部模型未开启" in status.property("text")
    llm = work._data["assistant"]["privacy"]["llm"]
    llm.update(enabled=True, natural_query_enabled=False, credential_configured=True, model="fixture-model")
    work.assistantDataChanged.emit()
    QTest.qWait(30)
    assert "外部问答未开启" in status.property("text")
    llm["natural_query_enabled"] = True
    work.assistantDataChanged.emit()
    QTest.qWait(30)
    assert status.property("text") == "外部模型：fixture-model"
    activate(window, "assistantExternal")
    assert status.property("text") == "本次使用本地回答"
    activate(window, "assistantModelSettings")
    assert bridge.currentPage == "settings"
    ready(bridge.settings)
    assert not engine._qml_warnings
