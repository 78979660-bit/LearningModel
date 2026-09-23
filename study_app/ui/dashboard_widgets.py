from __future__ import annotations

from study_app.core.dashboard import DashboardState
from study_app.core.async_tasks import AsyncTaskService, TaskStatus
from study_app.data.database import get_setting, set_setting
from study_app.ui.activity_view import activity_subjects, filter_homepage_activity
from study_app.ui.daily_summary_support import (
    daily_summary_cache_signature,
    matching_daily_summary_cache,
)


DAILY_SUMMARY_TASK_TIMEOUT_SECONDS = 30.0


def metric_card(title: str, value: str, detail: str):
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(8)
    title_label = QLabel(title)
    title_label.setObjectName("Muted")
    value_label = QLabel(value)
    value_label.setObjectName("MetricValue")
    detail_label = QLabel(detail)
    detail_label.setObjectName("Muted")
    detail_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    layout.addWidget(detail_label)
    return card

def subject_card(state: DashboardState):
    from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    title = QLabel("近期记录与掌握估计")
    title.setObjectName("CardTitle")
    layout.addWidget(title)

    note = QLabel(f"记录评分范围：{state.start} 至 {state.today}；分数 0–100。两种掌握估计的知识点范围不同，不能作为分子与分母比较。")
    note.setObjectName("Muted")
    note.setWordWrap(True)
    layout.addWidget(note)
    for item in activity_subjects(state):
        coverage = ""
        if item.total_topic_count:
            coverage = f"  |  已学 {item.covered_topic_count}/{item.total_topic_count}"
        covered = f"{item.covered_mastery_score}%" if item.covered_topic_count else "未知（无覆盖证据）"
        mastery_text = f"已覆盖知识点掌握估计 {covered}；全课程加权掌握估计 {item.mastery_score}%"
        if item.archived:
            text = f"{item.name}: 已封存（只读参考）  |  {mastery_text}{coverage}"
            value = item.covered_mastery_score
        elif item.window_score is not None:
            text = f"{item.name}: {item.window_score:.1f}  |  {mastery_text}{coverage}"
            value = int(max(0, min(100, item.window_score)))
        elif item.has_window_records:
            text = f"{item.name}: 有记录/未评分  |  {mastery_text}{coverage}"
            value = item.covered_mastery_score
        else:
            text = f"{item.name}: 未记录  |  {mastery_text}{coverage}"
            value = 0
        label = QLabel(text)
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        layout.addWidget(label)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(value)
        progress.setTextVisible(False)
        progress.setVisible(item.window_score is not None and not item.archived)
        layout.addWidget(progress)
        for warning in item.warnings:
            warn = QLabel(warning)
            warn.setObjectName("WarningText")
            warn.setWordWrap(True)
            layout.addWidget(warn)
    return card

def todo_card(state: DashboardState, todos: tuple | None = None):
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    title = QLabel("下一步待做事项")
    title.setObjectName("CardTitle")
    layout.addWidget(title)
    visible_todos = filter_homepage_activity(state).todos if todos is None else todos
    if not visible_todos:
        layout.addWidget(QLabel("当前没有强预警事项。"))
    for index, item in enumerate(visible_todos, start=1):
        label = QLabel(f"{index}. [{item.kind}] {item.title}")
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        detail = QLabel(item.detail)
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(detail)
    return card

def daily_summary_card(state: DashboardState):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
    from study_app.ai.daily_summary import generate_daily_summary_with_llm, local_daily_summary
    from study_app.ai.providers import is_llm_feature_enabled

    records = list(getattr(state, "raw_records", ()) or [])
    local_summary = local_daily_summary(state, records)
    summary = local_summary
    cache_key = f"daily_summary_cache:{state.today.isoformat()}"
    cache_signature = daily_summary_cache_signature(state, records)
    cached = get_setting(cache_key, None)
    cached_mode = ""
    cache_hit = matching_daily_summary_cache(cached, cache_signature, state.subjects)
    if cache_hit is not None:
        summary, cached_mode = cache_hit

    # Keep the card's dependencies stable across an asynchronous completion.
    persist_summary = set_setting
    show_information = QMessageBox.information
    show_warning = QMessageBox.warning

    service = AsyncTaskService(max_workers=1)
    lifecycle = {"open": True, "handle": None}
    timer = None

    def close_tasks():
        if not lifecycle["open"]:
            return
        lifecycle["open"] = False
        if timer is not None:
            timer.stop()
        service.invalidate("daily-summary-card")
        service.close()

    class DailySummaryCard(QWidget):
        def closeEvent(self, event):
            close_tasks()
            super().closeEvent(event)

    card = DailySummaryCard()
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    card.destroyed.connect(close_tasks)
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)

    title = QLabel("每日学习总结")
    title.setObjectName("CardTitle")
    layout.addWidget(title)

    body_labels = []

    def render_summary(data: dict[str, list[str]]):
        for label in body_labels:
            label.deleteLater()
        body_labels.clear()
        sections = [
            ("概况", data.get("overview", [])),
            ("风险", data.get("risks", [])),
            ("建议", data.get("actions", [])),
        ]
        for heading, lines in sections:
            heading_label = QLabel(heading)
            heading_label.setObjectName("Muted")
            layout.insertWidget(layout.count() - 1, heading_label)
            body_labels.append(heading_label)
            for line in lines:
                label = QLabel(f"- {line}")
                label.setObjectName("BodyText")
                label.setWordWrap(True)
                layout.insertWidget(layout.count() - 1, label)
                body_labels.append(label)
        source = "；".join(data.get("source", []))
        if source:
            source_label = QLabel(source)
            source_label.setObjectName("Muted")
            source_label.setWordWrap(True)
            layout.insertWidget(layout.count() - 1, source_label)
            body_labels.append(source_label)

    llm_button = QPushButton("重新生成总结")
    llm_button.setObjectName("GhostButton")
    idle_button_text = llm_button.text()

    def poll_result():
        if not lifecycle["open"]:
            return
        handle = lifecycle["handle"]
        if handle is None:
            return
        snapshot = handle.snapshot()
        if not snapshot.finished:
            return
        lifecycle["handle"] = None
        timer.stop()
        llm_button.setText(idle_button_text)
        if snapshot.status == TaskStatus.SUCCEEDED:
            try:
                persist_summary(
                    cache_key,
                    {"mode": "llm", "summary": snapshot.result, "signature": cache_signature},
                )
            except Exception as error:
                render_summary(local_summary)
                show_warning(card, "LLM 总结缓存失败", f"已保留本地总结。\n{error}")
                return
            llm_button.setText("重新生成总结")
            render_summary(snapshot.result)
            show_information(card, "已生成", "今日 LLM 总结已生成并缓存。")
        elif snapshot.status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
            render_summary(local_summary)
            message = (
                "LLM 总结超时，已保留本地总结。"
                if snapshot.status == TaskStatus.TIMED_OUT
                else f"已保留本地总结。\n{snapshot.error}"
            )
            show_warning(card, "LLM 总结失败", message)

    timer = QTimer(card)
    timer.setInterval(20)
    timer.timeout.connect(poll_result)

    def generate_llm_summary():
        if not lifecycle["open"]:
            return
        if not is_llm_feature_enabled("daily_summary"):
            show_information(card, "LLM 总结未启用", "请先在主页切到 LLM增强模式，并在设置中勾选“每日学习总结”。")
            return
        active = lifecycle["handle"]
        if active is not None and not active.snapshot().finished:
            return
        if active is not None:
            service.invalidate("daily-summary-card")
        llm_button.setText("LLM 总结生成中…")
        lifecycle["handle"] = service.submit(
            ("daily_summary", cache_key, cache_signature),
            lambda: generate_daily_summary_with_llm(state, records),
            slot="daily-summary-card",
            timeout_seconds=DAILY_SUMMARY_TASK_TIMEOUT_SECONDS,
        )
        timer.start()

    llm_button.clicked.connect(generate_llm_summary)
    layout.addWidget(llm_button)
    render_summary(summary)
    card._daily_summary_widgets = body_labels
    card._daily_summary_handler = generate_llm_summary
    card._daily_summary_task_service = service
    card._daily_summary_lifecycle = lifecycle
    return card

def simple_page(title: str, detail: str):
    from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(24, 22, 24, 24)
    layout.setSpacing(16)
    heading = QLabel(title)
    heading.setObjectName("HeroTitle")
    layout.addWidget(heading)
    card = QWidget()
    card.setObjectName("Card")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(16, 14, 16, 14)
    label = QLabel(detail)
    label.setObjectName("Muted")
    card_layout.addWidget(label)
    layout.addWidget(card)
    layout.addStretch()
    scroll.setWidget(content)
    return scroll

def risk_card(title: str, items: tuple[dict, ...], kind: str):
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    title_label = QLabel(title)
    title_label.setObjectName("CardTitle")
    layout.addWidget(title_label)
    if not items:
        layout.addWidget(QLabel("暂无预警。"))
    for item in items:
        if kind == "memory":
            text = (
                f"{item['subject']} / {item['topic']}: "
                f"回忆 {item['recall']:.0%}，目标 {item['target_recall']:.0%}"
            )
        else:
            text = (
                f"{item['subject']} / {item['topic']}: "
                f"P(掌握) {item['mastery_probability']:.0%}，目标 {item['target_mastery']:.0%}"
            )
        label = QLabel(text)
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        layout.addWidget(label)
    return card
