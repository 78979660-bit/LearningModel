from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from study_app.core.dashboard import DashboardState
from study_app.core.knowledge_alerts import (
    KnowledgeAlert,
    KnowledgeAlertEvent,
    handle_alert,
    snooze_alert,
)
from study_app.core.knowledge_explanations import (
    KnowledgeExplanation,
    explain_knowledge_topic,
)
from study_app.core.knowledge_states import ClassifiedTopic
from study_app.data.database import (
    DEFAULT_DB_PATH,
    DatabaseNotInitializedError,
    connect,
    list_knowledge_alert_events,
    list_knowledge_alerts,
)
from study_app.ui.knowledge_page import load_knowledge_page_entries


STATUS_LABELS = {
    "active": "活动预警",
    "snoozed": "已延后",
    "handled": "已处理",
    "resolved": "已解除",
}

ALERT_TYPE_LABELS = {
    "recent_failure": "近期失败",
    "insufficient_evidence": "证据不足",
    "overdue_review": "间隔过长",
}


@dataclass(frozen=True)
class AlertPageEntry:
    alert: KnowledgeAlert
    events: tuple[KnowledgeAlertEvent, ...]
    classified_topic: ClassifiedTopic | None
    explanation: KnowledgeExplanation | None


def load_alert_page_entries(state: DashboardState) -> tuple[AlertPageEntry, ...]:
    """Read alert instances and explanations without reconciling or writing state."""
    alerts = list_knowledge_alerts()
    if not alerts:
        return ()
    events = list_knowledge_alert_events()
    events_by_alert: dict[int, list[KnowledgeAlertEvent]] = {}
    for event in events:
        events_by_alert.setdefault(event.alert_id, []).append(event)

    knowledge_entries = load_knowledge_page_entries(state)
    classified_by_key = {
        entry.classified_topic.insight.topic_key: entry.classified_topic
        for entry in knowledge_entries
    }
    result = []
    for alert in alerts:
        classified = classified_by_key.get(alert.topic_key)
        alert_events = tuple(events_by_alert.get(alert.id, ()))
        explanation_compatible = (
            classified is not None
            and alert.rule_version == classified.rule_version
            and alert.evidence_version == classified.insight.evidence_version
            and alert.alert_type == classified.primary_state
            and alert.as_of_date == classified.insight.as_of_date
        )
        explanation = (
            None
            if not explanation_compatible
            else explain_knowledge_topic(
                classified,
                classified_by_key,
                None,
                alert=alert,
                alert_events=alert_events,
            )
        )
        result.append(
            AlertPageEntry(alert, alert_events, classified, explanation)
        )
    return tuple(result)


def _handle_alert_action(alert_id: int, effective_date: date) -> KnowledgeAlert:
    with connect(DEFAULT_DB_PATH) as connection:
        return handle_alert(
            alert_id,
            effective_date,
            connection,
            actor="desktop_local_user",
        )


def _snooze_alert_action(
    alert_id: int,
    snoozed_until: date,
    as_of_date: date,
) -> KnowledgeAlert:
    with connect(DEFAULT_DB_PATH) as connection:
        return snooze_alert(
            alert_id,
            snoozed_until,
            as_of_date,
            connection,
            actor="desktop_local_user",
        )


def _snapshot_section(alert: KnowledgeAlert, name: str) -> dict[str, object]:
    value = alert.snapshot.get(name, {})
    return value if isinstance(value, dict) else {}


def _topic_path(alert: KnowledgeAlert) -> str:
    topic = _snapshot_section(alert, "topic")
    values = (
        str(topic.get("subject_name") or "未知学科"),
        str(topic.get("module_name") or "未知模块"),
        str(topic.get("topic_name") or alert.topic_key),
    )
    return " / ".join(values)


def _recommended_text(alert: KnowledgeAlert) -> str:
    return (
        alert.recommended_date.isoformat()
        if alert.recommended_date is not None
        else "未知／未记录"
    )


def _explanation_text(entry: AlertPageEntry) -> str:
    if entry.explanation is None:
        return "无法关联当前知识点分类；保留预警快照与事件历史，不猜测解释。"
    parts = []
    for layer in entry.explanation.layers:
        facts = "；".join(f"{fact.label}={fact.value}" for fact in layer.facts)
        parts.append(f"[{layer.name}] {facts}")
    if entry.explanation.diagnostics:
        parts.append("[diagnostics] " + "；".join(entry.explanation.diagnostics))
    return "\n".join(parts)


def alert_page(
    state: DashboardState,
    data_loader=None,
    handle_action=None,
    snooze_action=None,
):
    from PySide6.QtCore import QDate, Qt, QTimer
    from PySide6.QtWidgets import (
        QComboBox,
        QDateEdit,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )

    loader = data_loader or load_alert_page_entries
    handle_writer = handle_action or _handle_alert_action
    snooze_writer = snooze_action or _snooze_alert_action
    current_state = [state]
    entries: list[AlertPageEntry] = []

    scroll = QScrollArea()
    scroll.setObjectName("AlertPage")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(24, 22, 24, 24)
    layout.setSpacing(14)

    heading = QLabel("预警")
    heading.setObjectName("HeroTitle")
    layout.addWidget(heading)

    cutoff_label = QLabel()
    cutoff_label.setObjectName("AlertCutoff")
    layout.addWidget(cutoff_label)

    filter_row = QHBoxLayout()
    view_filter = QComboBox()
    view_filter.setObjectName("AlertStatusFilter")
    for code in ("active", "snoozed", "handled", "resolved"):
        view_filter.addItem(STATUS_LABELS[code], code)
    filter_row.addWidget(view_filter)
    filter_row.addStretch()
    layout.addLayout(filter_row)

    diagnostic = QLabel()
    diagnostic.setObjectName("AlertDiagnostic")
    diagnostic.setWordWrap(True)
    diagnostic.hide()
    layout.addWidget(diagnostic)

    action_status = QLabel()
    action_status.setObjectName("AlertActionStatus")
    action_status.setWordWrap(True)
    action_status.hide()
    layout.addWidget(action_status)

    result_count = QLabel()
    result_count.setObjectName("AlertResultCount")
    layout.addWidget(result_count)

    alert_list = QListWidget()
    alert_list.setObjectName("AlertList")
    alert_list.setMinimumHeight(250)
    alert_list.setAlternatingRowColors(True)
    layout.addWidget(alert_list)

    detail = QWidget()
    detail.setObjectName("AlertDetail")
    detail_layout = QGridLayout(detail)
    detail_layout.setContentsMargins(18, 16, 18, 16)
    detail_layout.setHorizontalSpacing(16)
    detail_layout.setVerticalSpacing(10)
    detail_fields = {}
    field_specs = (
        ("path", "知识点", "AlertTopicPath"),
        ("status", "状态", "AlertWorkflowStatus"),
        ("trigger", "触发依据", "AlertTriggerReason"),
        ("evidence", "关键数值", "AlertEvidence"),
        ("recommended", "建议日期", "AlertRecommendedDate"),
        ("versions", "证据版本", "AlertVersions"),
        ("history", "事件历史", "AlertEventHistory"),
    )
    for row, (key, label_text, object_name) in enumerate(field_specs):
        label = QLabel(label_text)
        label.setObjectName("Muted")
        value = QLabel("—")
        value.setObjectName(object_name)
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
        detail_layout.addWidget(value, row, 1)
        detail_fields[key] = value
    detail_layout.setColumnStretch(1, 1)
    layout.addWidget(detail)

    action_row = QHBoxLayout()
    handle_button = QPushButton("处理")
    handle_button.setObjectName("AlertHandleButton")
    snooze_date = QDateEdit()
    snooze_date.setObjectName("AlertSnoozeDate")
    snooze_date.setCalendarPopup(True)
    snooze_button = QPushButton("延后")
    snooze_button.setObjectName("AlertSnoozeButton")
    explain_button = QPushButton("解释")
    explain_button.setObjectName("AlertExplainButton")
    action_row.addWidget(handle_button)
    action_row.addWidget(snooze_date)
    action_row.addWidget(snooze_button)
    action_row.addWidget(explain_button)
    action_row.addStretch()
    layout.addLayout(action_row)

    explanation_label = QLabel()
    explanation_label.setObjectName("AlertExplanation")
    explanation_label.setWordWrap(True)
    explanation_label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
    )
    explanation_label.hide()
    layout.addWidget(explanation_label)
    layout.addStretch()
    scroll.setWidget(content)

    def selected_entry() -> AlertPageEntry | None:
        selected = alert_list.currentItem()
        if selected is None:
            return None
        alert_id = selected.data(Qt.ItemDataRole.UserRole)
        return next((entry for entry in entries if entry.alert.id == alert_id), None)

    def visible_entries() -> list[AlertPageEntry]:
        status = view_filter.currentData()
        return sorted(
            (entry for entry in entries if entry.alert.status == status),
            key=lambda entry: (
                -entry.alert.priority,
                entry.alert.recommended_date or date.max,
                entry.alert.id,
            ),
        )

    def clear_detail() -> None:
        for value in detail_fields.values():
            value.setText("—")
        handle_button.setDisabled(True)
        snooze_button.setDisabled(True)
        explain_button.setDisabled(True)
        explanation_label.hide()
        explanation_label.setText("")

    def render_detail() -> None:
        entry = selected_entry()
        if entry is None:
            clear_detail()
            return
        alert = entry.alert
        classification = _snapshot_section(alert, "classification")
        evidence = _snapshot_section(alert, "evidence")
        mastery = _snapshot_section(alert, "mastery")
        memory = _snapshot_section(alert, "memory")
        reason_codes = classification.get("reason_codes")
        if not isinstance(reason_codes, list):
            reason_codes = []
        detail_fields["path"].setText(_topic_path(alert))
        detail_fields["status"].setText(
            f"{STATUS_LABELS[alert.status]}｜"
            f"{ALERT_TYPE_LABELS.get(alert.alert_type, alert.alert_type)}"
        )
        detail_fields["trigger"].setText(
            "、".join(str(value) for value in reason_codes)
            or str(classification.get("recommended_action") or "未知／未记录")
        )
        interval = mastery.get("interval")
        interval_text = "不可估计"
        if isinstance(interval, list) and len(interval) == 2:
            interval_text = f"[{interval[0]}, {interval[1]}]"
        detail_fields["evidence"].setText(
            f"有效作答 {evidence.get('observation_count', '未知')}；"
            f"独立练习 {evidence.get('exercise_count', '未知')}；"
            f"置信度 {evidence.get('confidence', '未知')}；"
            f"掌握区间 {interval_text}；"
            f"回忆 {memory.get('recall_probability', '未知')} / "
            f"目标 {memory.get('target_recall', '未知')}"
        )
        detail_fields["recommended"].setText(_recommended_text(alert))
        detail_fields["versions"].setText(
            f"rule={alert.rule_version}；evidence={alert.evidence_version}；"
            f"snapshot={alert.as_of_date.isoformat()}"
        )
        detail_fields["history"].setText(
            "；".join(
                f"{event.effective_date.isoformat()} {event.event_type} "
                f"{event.from_status or 'none'}→{event.to_status} actor={event.actor}"
                for event in entry.events
            )
            or "无事件历史"
        )
        handle_button.setEnabled(alert.status == "active")
        snooze_button.setEnabled(alert.status in {"active", "snoozed"})
        explain_button.setEnabled(True)
        explanation_label.hide()
        explanation_label.setText("")

    def render_list(preferred_id: int | None = None) -> None:
        if preferred_id is None and alert_list.currentItem() is not None:
            preferred_id = alert_list.currentItem().data(Qt.ItemDataRole.UserRole)
        alert_list.clear()
        values = visible_entries()
        for entry in values:
            alert = entry.alert
            item = QListWidgetItem(
                f"{_topic_path(alert)}\n"
                f"{ALERT_TYPE_LABELS.get(alert.alert_type, alert.alert_type)}｜"
                f"优先级 {alert.priority:.3f}｜建议 {_recommended_text(alert)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, alert.id)
            alert_list.addItem(item)
        result_count.setText(f"{STATUS_LABELS[view_filter.currentData()]}：{len(values)} 条")
        target_row = -1
        for index in range(alert_list.count()):
            if alert_list.item(index).data(Qt.ItemDataRole.UserRole) == preferred_id:
                target_row = index
                break
        if target_row < 0 and alert_list.count():
            target_row = 0
        if target_row >= 0:
            alert_list.setCurrentRow(target_row)
        else:
            clear_detail()

    def load_entries() -> bool:
        entries.clear()
        diagnostic.hide()
        diagnostic.setText("")
        try:
            loaded = tuple(loader(current_state[0]))
            if any(not isinstance(item, AlertPageEntry) for item in loaded):
                raise ValueError("预警页数据必须是 AlertPageEntry")
            entries.extend(loaded)
            return True
        except DatabaseNotInitializedError as error:
            diagnostic.setText(
                "F2 结构尚未安装；当前仅显示只读诊断，不会自动修改现用数据库。"
                f"\n{error}"
            )
            diagnostic.show()
        except Exception as error:
            diagnostic.setText(f"预警只读加载失败：{error}")
            diagnostic.show()
        return False

    def reload_preserving(preferred_id: int | None = None) -> bool:
        list_scroll = alert_list.verticalScrollBar().value()
        page_scroll = scroll.verticalScrollBar().value()
        loaded = load_entries()
        render_list(preferred_id)

        def restore_scroll() -> None:
            alert_list.verticalScrollBar().setValue(
                min(list_scroll, alert_list.verticalScrollBar().maximum())
            )
            scroll.verticalScrollBar().setValue(
                min(page_scroll, scroll.verticalScrollBar().maximum())
            )

        restore_scroll()
        QTimer.singleShot(0, restore_scroll)
        return loaded

    def show_explanation() -> None:
        entry = selected_entry()
        if entry is None:
            return
        explanation_label.setText(_explanation_text(entry))
        explanation_label.show()

    def handle_selected() -> None:
        entry = selected_entry()
        if entry is None:
            return
        alert_id = entry.alert.id
        action_status.hide()
        try:
            handle_writer(alert_id, current_state[0].today)
        except Exception as error:
            action_status.setText(f"处理失败：{error}")
            action_status.show()
            return
        loaded = reload_preserving(alert_id)
        action_status.setText(
            "预警已处理。" if loaded else "预警已处理，但页面刷新失败。"
        )
        action_status.show()

    def snooze_selected() -> None:
        entry = selected_entry()
        if entry is None:
            return
        alert_id = entry.alert.id
        until = snooze_date.date().toPython()
        action_status.hide()
        try:
            snooze_writer(alert_id, until, current_state[0].today)
        except Exception as error:
            action_status.setText(f"延后失败：{error}")
            action_status.show()
            return
        loaded = reload_preserving(alert_id)
        action_status.setText(
            f"预警已延后至 {until.isoformat()}。"
            if loaded
            else "预警已延后，但页面刷新失败。"
        )
        action_status.show()

    view_filter.currentIndexChanged.connect(lambda _index: render_list())
    alert_list.currentItemChanged.connect(
        lambda _current, _previous: render_detail()
    )
    handle_button.clicked.connect(handle_selected)
    snooze_button.clicked.connect(snooze_selected)
    explain_button.clicked.connect(show_explanation)

    def set_dashboard_state(new_state: DashboardState) -> None:
        selected = selected_entry()
        selected_id = selected.alert.id if selected is not None else None
        current_view = view_filter.currentData()
        chosen_snooze = snooze_date.date().toPython()
        current_state[0] = new_state
        cutoff_label.setText(f"计算截止日：{new_state.today.isoformat()}")
        minimum = QDate(new_state.today.year, new_state.today.month, new_state.today.day)
        snooze_date.setMinimumDate(minimum)
        chosen = max(chosen_snooze, new_state.today)
        snooze_date.setDate(QDate(chosen.year, chosen.month, chosen.day))
        view_filter.blockSignals(True)
        index = view_filter.findData(current_view)
        view_filter.setCurrentIndex(index if index >= 0 else 0)
        view_filter.blockSignals(False)
        reload_preserving(selected_id)

    scroll._set_dashboard_state = set_dashboard_state
    scroll._refresh_alerts = lambda: reload_preserving(
        selected_entry().alert.id if selected_entry() is not None else None
    )
    cutoff_label.setText(f"计算截止日：{state.today.isoformat()}")
    initial_date = state.today + timedelta(days=1)
    snooze_date.setMinimumDate(QDate(state.today.year, state.today.month, state.today.day))
    snooze_date.setDate(
        QDate(initial_date.year, initial_date.month, initial_date.day)
    )
    load_entries()
    render_list()
    return scroll
