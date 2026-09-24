from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

from study_app.ai.study_plan_service import (
    current_study_plan_signature,
    generate_study_plan_with_optional_llm,
    llm_plan_unavailable_hint,
    should_refresh_plan_for_model,
    should_upgrade_plan_to_llm,
)
from study_app.core.dashboard import DashboardState, load_dashboard_state
from study_app.core.async_tasks import (
    capture_subject_revision,
    revalidate_subject_revision,
)
from study_app.core.budgeted_day_plan import BudgetPlanInput, build_budgeted_day_plan
from study_app.core.day_budget_input import validate_day_budget_input, validate_subject_exam_date
from study_app.core.plan_candidates import CandidateCollection, build_plan_candidates
from study_app.core.practice_prompts import (
    build_mock_exam_generation_prompt,
    build_oj_practice_list,
    build_practice_generation_prompt,
)
from study_app.core.practice_spec import is_oj_plan_homework
from study_app.core.study_plan_feedback import plan_day_feedback_details, planned_homework_score
from study_app.core.study_plan_items import (
    build_study_plan_items,
    humanize_plan_text,
    infer_subject_topic_from_plan_line,
    is_plan_homework_item,
    split_short_plan_item,
)
from study_app.core.task_estimates import (
    CONFIRMED_TEMPLATE_ESTIMATE,
    USER_ESTIMATE,
    make_assistant_task_estimate,
    make_task_estimate,
)
from study_app.data.database import (
    DEFAULT_DB_PATH,
    DatabaseNotInitializedError,
    add_learning_record,
    archive_active_study_plan,
    create_budgeted_day_plan,
    create_study_plan,
    delete_settings_by_prefix,
    get_active_study_plan,
    get_budgeted_day_plan,
    get_setting,
    get_study_day_budget,
    get_subject_exam_date,
    get_task_estimate,
    save_study_day_budget,
    save_subject_exam_date,
    save_task_estimate,
    set_setting,
    update_study_plan_item_state,
)
from study_app.ui.activity_view import (
    activity_subject_names, final_review_subject_names, plan_subject_scope_options,
)


_PLAN_GENERATION_WORKERS: set[object] = set()
_CHATGPT_BRIDGE_WORKERS: set[object] = set()
_CHATGPT_BRIDGE_LOCK = threading.Lock()
_CHATGPT_BRIDGE_SHUTDOWN_STARTED = False
LOGGER = logging.getLogger(__name__)


def _shutdown_chatgpt_bridge_workers() -> None:
    global _CHATGPT_BRIDGE_SHUTDOWN_STARTED
    with _CHATGPT_BRIDGE_LOCK:
        if _CHATGPT_BRIDGE_SHUTDOWN_STARTED:
            return
        workers = list(_CHATGPT_BRIDGE_WORKERS)
        _CHATGPT_BRIDGE_SHUTDOWN_STARTED = True

    deadline = time.monotonic() + 4.0
    cleanup_temp_artifacts = None
    try:
        from study_app.integrations.chatgpt_desktop_bridge import (
            cancel_chatgpt_pdf_generation,
            cleanup_chatgpt_temp_artifacts as cleanup_temp_artifacts,
            request_chatgpt_pdf_shutdown,
        )

        request_chatgpt_pdf_shutdown()
        result = cancel_chatgpt_pdf_generation(deadline=deadline)
        if not result.success:
            LOGGER.error("ChatGPT PDF shutdown cancel failed (%s)", result.code)
    except Exception as error:
        LOGGER.error(
            "ChatGPT PDF shutdown cancel raised (%s)",
            type(error).__name__,
        )
    finally:
        for worker in workers:
            remaining = min(2.0, max(0.0, deadline - time.monotonic()))
            remaining_ms = int(remaining * 1000)
            if remaining_ms <= 0 or not worker.wait(remaining_ms):
                LOGGER.error("ChatGPT PDF worker cleanup timed out")
        if cleanup_temp_artifacts is not None:
            try:
                cleanup_temp_artifacts()
            except Exception as error:
                LOGGER.error(
                    "ChatGPT temporary artifact cleanup failed (%s)",
                    type(error).__name__,
                )


def generate_local_practice_for_plan_line(line: str, subject_name: str):
    """Generate a local paper without invoking ChatGPT, a browser, or UI automation."""
    normalized_subject = str(subject_name or "").strip()
    if not normalized_subject:
        raise ValueError("生成本地练习卷前，请先选择一个具体学科。")
    if is_oj_plan_homework(line):
        raise ValueError("OJ 作业使用官方题目与测试集，不自动转换为本地练习卷。")
    from study_app.core.local_practice_service import (
        generate_local_practice_paper,
        spec_from_homework,
    )

    spec = spec_from_homework(line, subject=normalized_subject)
    return generate_local_practice_paper(spec)


def local_practice_success_message(result) -> str:
    return (
        "本地练习卷已生成，不依赖 ChatGPT 或网络。\n\n"
        f"题目卷：{result.question_pdf}\n"
        f"答案卷：{result.answer_pdf}\n"
        f"生成记录：{result.manifest_json}"
    )


def study_plan_page(state: DashboardState):
    from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QProgressBar, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget

    class WorkerSignalRelay(QObject):
        def __init__(
            self,
            *,
            on_completed=None,
            on_failed=None,
            on_finished=None,
            parent=None,
        ):
            super().__init__(parent)
            self._on_completed = on_completed
            self._on_failed = on_failed
            self._on_finished = on_finished
            self._finished_called = False

        @Slot(object, str)
        def completed(self, result, source):
            if self._on_completed is not None:
                self._on_completed(result, source)

        @Slot(str)
        def failed(self, error):
            if self._on_failed is not None:
                self._on_failed(error)

        @Slot()
        def finished(self):
            if self._finished_called:
                return
            self._finished_called = True
            if self._on_finished is not None:
                self._on_finished()
            self.deleteLater()

    class PlanGenerationThread(QObject):
        completed = Signal(object, str)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, dashboard_state, subject_name, revision_token=None, parent=None):
            super().__init__(None)
            self.dashboard_state = dashboard_state
            self.subject_name = subject_name
            self.revision_token = revision_token
            self._finished = threading.Event()
            self._interruption_requested = threading.Event()
            self._thread = None

        def start(self):
            if self.isRunning():
                return
            self._finished.clear()
            self._interruption_requested.clear()
            _PLAN_GENERATION_WORKERS.add(self)
            try:
                self._thread = threading.Thread(
                    target=self.run,
                    name="study-plan-generation",
                    daemon=True,
                )
                self._thread.start()
            except Exception:
                _PLAN_GENERATION_WORKERS.discard(self)
                self._thread = None
                self._finished.set()
                raise

        def isRunning(self):
            return self._thread is not None and self._thread.is_alive()

        def isFinished(self):
            return self._finished.is_set()

        def wait(self, timeout=None):
            seconds = None if timeout is None else max(0, timeout) / 1000
            return self._finished.wait(seconds)

        def requestInterruption(self):
            self._interruption_requested.set()

        def isInterruptionRequested(self):
            return self._interruption_requested.is_set()

        def run(self):
            try:
                if self.revision_token is None:
                    plan, source = generate_study_plan_with_optional_llm(
                        self.dashboard_state,
                        self.subject_name,
                    )
                else:
                    plan, source = generate_study_plan_with_optional_llm(
                        self.dashboard_state,
                        self.subject_name,
                        revision_token=self.revision_token,
                    )
                self.completed.emit(plan, source)
            except Exception as error:
                self.failed.emit(str(error))
            finally:
                _PLAN_GENERATION_WORKERS.discard(self)
                self._finished.set()
                self.finished.emit()

    class ChatGPTBridgeThread(QObject):
        completed = Signal(object, str)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, prompt, parent=None):
            super().__init__(None)
            self.prompt = prompt
            self.cancelled = False
            self._finished = threading.Event()
            self._thread = None

        def start(self):
            if self.isRunning():
                return False
            with _CHATGPT_BRIDGE_LOCK:
                if _CHATGPT_BRIDGE_SHUTDOWN_STARTED or _CHATGPT_BRIDGE_WORKERS:
                    return False
                _CHATGPT_BRIDGE_WORKERS.add(self)
            self._finished.clear()
            try:
                self._thread = threading.Thread(
                    target=self.run,
                    name="chatgpt-pdf-generation",
                    daemon=True,
                )
                self._thread.start()
            except Exception:
                with _CHATGPT_BRIDGE_LOCK:
                    _CHATGPT_BRIDGE_WORKERS.discard(self)
                self._thread = None
                self._finished.set()
                raise
            return True

        def isRunning(self):
            return self._thread is not None and self._thread.is_alive()

        def isFinished(self):
            return self._finished.is_set()

        def wait(self, timeout=None):
            seconds = None if timeout is None else max(0, timeout) / 1000
            return self._finished.wait(seconds)

        def run(self):
            from study_app.integrations.chatgpt_desktop_bridge import generate_pdf_with_chatgpt

            try:
                self.completed.emit(generate_pdf_with_chatgpt(self.prompt), self.prompt)
            except Exception as error:
                self.failed.emit(str(error))
            finally:
                with _CHATGPT_BRIDGE_LOCK:
                    _CHATGPT_BRIDGE_WORKERS.discard(self)
                self._finished.set()
                self.finished.emit()

        def cancel(self):
            from study_app.integrations.chatgpt_desktop_bridge import cancel_chatgpt_pdf_generation

            return cancel_chatgpt_pdf_generation()

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    content.setMinimumWidth(0)
    content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    content._chatgpt_bridge_page_alive = True

    def mark_chatgpt_page_destroyed():
        setattr(content, "_chatgpt_bridge_page_alive", False)

    content.destroyed.connect(mark_chatgpt_page_destroyed)
    app = QApplication.instance()
    if app is not None and not getattr(app, "_chatgpt_bridge_shutdown_connected", False):
        app.aboutToQuit.connect(_shutdown_chatgpt_bridge_workers)
        app._chatgpt_bridge_shutdown_connected = True
    layout = QVBoxLayout(content)
    layout.setContentsMargins(30, 28, 30, 30)
    layout.setSpacing(18)

    hero = QWidget()
    hero.setObjectName("RecordHero")
    hero_layout = QVBoxLayout(hero)
    hero_layout.setContentsMargins(18, 16, 18, 16)
    hero_layout.setSpacing(6)
    title = QLabel("学习计划")
    title.setObjectName("HeroTitle")
    hero_layout.addWidget(title)
    layout.addWidget(hero)

    actions = QGridLayout()
    actions.setHorizontalSpacing(10)
    actions.setVerticalSpacing(10)
    for column in range(4):
        actions.setColumnStretch(column, 1)
    scope_label = QLabel("计划范围")
    scope_label.setObjectName("FormLabel")
    subject_scope = QComboBox()
    for label, value in plan_subject_scope_options(state):
        subject_scope.addItem(label, value)
    generate_button = QPushButton("生成今日计划")
    generate_button.setObjectName("PrimaryButton")
    final_review_button = QPushButton("期末复习模式")
    final_review_button.setObjectName("GhostButton")
    clear_button = QPushButton("清空计划")
    clear_button.setObjectName("GhostButton")
    copy_context_button = QPushButton("复制题库上下文")
    copy_context_button.setObjectName("GhostButton")
    mock_exam_mode = QComboBox()
    mock_exam_mode.addItem("诊断卷", "diagnostic")
    mock_exam_mode.addItem("标准期末卷", "standard")
    mock_exam_mode.addItem("冲刺专题卷", "sprint")
    mock_exam_button = QPushButton("生成模拟卷 PDF")
    mock_exam_button.setObjectName("GhostButton")
    stop_pdf_button = QPushButton("停止 PDF 生成")
    stop_pdf_button.setObjectName("DangerButton")
    stop_pdf_button.setVisible(False)
    stop_pdf_button.setDisabled(True)
    subject_scope.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Fixed,
    )
    for control in (
        generate_button,
        final_review_button,
        clear_button,
        copy_context_button,
        mock_exam_mode,
        mock_exam_button,
        stop_pdf_button,
    ):
        control.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
    scope_row = QHBoxLayout()
    scope_label.setBuddy(subject_scope)
    scope_row.addWidget(scope_label)
    scope_row.addWidget(subject_scope, 1)
    layout.addLayout(scope_row)
    actions.addWidget(generate_button, 0, 0, 1, 4)
    actions.addWidget(final_review_button, 1, 0, 1, 2)
    actions.addWidget(clear_button, 1, 2)
    actions.addWidget(copy_context_button, 1, 3)
    actions.addWidget(mock_exam_mode, 2, 0, 1, 2)
    actions.addWidget(mock_exam_button, 2, 2)
    actions.addWidget(stop_pdf_button, 2, 3)
    tools_body = QWidget()
    tools_layout = QVBoxLayout(tools_body)
    tools_layout.setContentsMargins(0, 0, 0, 0)
    tools_layout.addLayout(actions)

    budget_toggle = QPushButton("考试日期与单项估时 · 展开")
    budget_toggle.setObjectName("BudgetOptionsToggle")
    budget_toggle.setCheckable(True)
    budget_toggle.setChecked(False)
    budget_toggle.setToolTip("查看考试日期与每项任务估时；修改后需重新预览并确认。")


    budget_panel = QWidget()
    budget_panel.setObjectName("Card")
    budget_layout = QVBoxLayout(budget_panel)
    budget_layout.setContentsMargins(18, 16, 18, 16)
    budget_layout.setSpacing(10)
    budget_title = QLabel("今天能学多久")
    budget_title.setObjectName("CardTitle")
    budget_layout.addWidget(budget_title)

    budget_row = QHBoxLayout()
    minutes_label = QLabel("今日可用时间（分钟）")
    budget_row.addWidget(minutes_label)
    budget_minutes_input = QLineEdit()
    budget_minutes_input.setObjectName("BudgetMinutesInput")
    budget_minutes_input.setPlaceholderText("0～1440")
    minutes_label.setBuddy(budget_minutes_input)
    budget_minutes_input.setAccessibleName("今日可用时间（分钟）")
    budget_row.addWidget(budget_minutes_input)
    budget_row.addStretch()
    budget_layout.addLayout(budget_row)

    load_label = QLabel("可用时间未设置；请输入 0–1440 分钟。")
    load_label.setObjectName("PlanLoadPreview")
    load_label.setWordWrap(True)
    budget_layout.addWidget(load_label)
    budget_layout.addWidget(budget_toggle)
    options_panel = QWidget()
    options_layout = QVBoxLayout(options_panel)
    options_layout.setContentsMargins(0, 0, 0, 0)
    options_panel.hide()
    budget_layout.addWidget(options_panel)
    exam_inputs: dict[str, QLineEdit] = {}
    for subject_name in activity_subject_names(state):
        row = QHBoxLayout()
        row.addWidget(QLabel(f"{subject_name}考试日期"))
        field = QLineEdit()
        field.setObjectName("SubjectExamDateInput")
        field.setProperty("subjectId", subject_name)
        field.setPlaceholderText("可空；YYYY-MM-DD")
        row.addWidget(field)
        row.addStretch()
        options_layout.addLayout(row)
        exam_inputs[subject_name] = field

    candidate_collection = build_plan_candidates(state)
    estimate_inputs: dict[str, QSpinBox] = {}
    candidate_by_id = {candidate.task_id: candidate for candidate in candidate_collection.candidates}
    for candidate in candidate_collection.candidates:
        row = QHBoxLayout()
        task_label = QLabel(f"{candidate.subject_id} · {candidate.title}")
        task_label.setWordWrap(True)
        row.addWidget(task_label, 1)
        field = QSpinBox()
        field.setObjectName("TaskEstimateInput")
        field.setProperty("taskId", candidate.task_id)
        field.setRange(1, 1440)
        field.setSuffix(" 分钟 · 助理估时")
        row.addWidget(field)
        row.addStretch()
        options_layout.addLayout(row)
        estimate_inputs[candidate.task_id] = field

    budget_apply_button = QPushButton("预览今日安排")
    budget_apply_button.setObjectName("PrimaryButton")
    class BudgetStatusLabel(QLabel):
        def setText(self, text):
            super().setText(text)
            self.setVisible(bool(text))

    budget_status = BudgetStatusLabel()
    budget_status.setObjectName("StatusLabel")
    budget_status.setWordWrap(True)
    budget_actions = QHBoxLayout()
    budget_actions.addWidget(budget_apply_button)
    confirm_budget_button = QPushButton("确认安排")
    confirm_budget_button.setObjectName("ConfirmBudgetPlan")
    confirm_budget_button.setEnabled(False)
    confirm_budget_button.setToolTip("先预览安排，再确认保存")
    budget_actions.addWidget(confirm_budget_button)
    budget_actions.addStretch()
    budget_layout.addLayout(budget_actions)
    preview_holder = {"signature": None}
    task_rows = {}
    budget_layout.addWidget(budget_status)
    budget_result_host = QWidget()
    budget_result_layout = QVBoxLayout(budget_result_host)
    budget_result_layout.setContentsMargins(0, 0, 0, 0)
    budget_result_layout.setSpacing(8)
    budget_layout.addWidget(budget_result_host)
    budget_panel.setVisible(True)
    content._budget_options_panel = options_panel
    content._budget_options_toggle = budget_toggle
    layout.addWidget(budget_panel)

    def toggle_budget_panel(expanded: bool):
        options_panel.setVisible(expanded)
        budget_toggle.setText(
            "考试日期与单项估时 · 收起" if expanded else "考试日期与单项估时 · 展开"
        )

    budget_toggle.toggled.connect(toggle_budget_panel)

    def clear_budget_results():
        task_rows.clear()
        while budget_result_layout.count():
            item = budget_result_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    budget_schema_ready = True
    loaded_estimates = {}
    try:
        saved_budget = get_study_day_budget(state.today.isoformat())
        if saved_budget is not None:
            budget_minutes_input.setText(str(saved_budget.available_minutes))
        for subject_name, field in exam_inputs.items():
            saved_date = get_subject_exam_date(subject_name)
            field.setText(saved_date or "")
        for task_id, candidate in candidate_by_id.items():
            estimate = get_task_estimate(candidate)
            if estimate is None:
                estimate = make_assistant_task_estimate(candidate)
            loaded_estimates[task_id] = estimate
            field = estimate_inputs[task_id]
            field.setValue(estimate.estimated_minutes)
            if estimate.source == USER_ESTIMATE:
                field.setSuffix(" 分钟 · 已保存")
            elif estimate.source == CONFIRMED_TEMPLATE_ESTIMATE:
                field.setSuffix(" 分钟 · 助理估时")
        get_budgeted_day_plan(state.today.isoformat())
    except DatabaseNotInitializedError:
        budget_schema_ready = False
        budget_status.setText("时间预算暂不可用。请在设置中检查数据结构，已有学习记录会保留。")
        budget_apply_button.setDisabled(True)

    def render_budgeted_plan(saved_plan=None, *, preview=False):
        clear_budget_results()
        if not budget_schema_ready:
            return
        if saved_plan is None:
            saved_plan = get_budgeted_day_plan(
                state.today.isoformat(), subject_scope.currentData() or None
            )
        if not saved_plan:
            note = QLabel("暂无今日安排")
            note.setObjectName("Muted")
            budget_result_layout.addWidget(note)
            return
        summary = saved_plan["summary"]
        summary_text = (
            f"已安排 {summary['planned_minutes']} 分钟；剩余 {summary['remaining_minutes']} 分钟"
        )
        summary_text = ("未保存预览 · " if preview else f"已保存预算 {saved_plan.get('budget_minutes', '未知')} 分钟 · ") + summary_text
        if summary.get("over_budget_completed_minutes"):
            summary_text += f"；已完成超出新预算 {summary['over_budget_completed_minutes']} 分钟"
        if summary.get("completed_occupancy_unknown"):
            summary_text += "；存在完成占用未知项，补估时前不新增任务"
        summary_label = QLabel(summary_text)
        summary_label.setObjectName("BudgetSummaryLabel")
        summary_label.setWordWrap(True)
        budget_result_layout.addWidget(summary_label)
        if not saved_plan["items"]:
            empty = QLabel("当前学科暂无任务")
            empty.setWordWrap(True)
            budget_result_layout.addWidget(empty)
        active_rows = [r for r in saved_plan["items"] if not r["excluded_reason"]]
        if active_rows and all(r["checked"] for r in active_rows):
            budget_result_layout.addWidget(QLabel("今日任务已完成"))
        for row in saved_plan["items"]:
            completed = bool(row["checked"])
            excluded = bool(row["excluded_reason"])
            status = "已完成" if completed else "未安排" if excluded else "待执行"
            minutes = f" · {row['estimated_minutes']} 分钟" if row["estimated_minutes"] else ""
            reason = row["selection_reason"].get("reason") or row["excluded_reason"] or ""
            line = f"{status} · {row['item_text']}{minutes} · {reason}"
            if excluded:
                label = QLabel(line)
                label.setObjectName("BudgetExcludedItem")
                label.setWordWrap(True)
                budget_result_layout.addWidget(label)
                continue
            from study_app.ui.design_components import disclosure
            task = QWidget()
            task_layout = QVBoxLayout(task)
            task_layout.setContentsMargins(0, 8, 0, 8)
            task_heading = QLabel(f"{status} · {row['item_text']}{minutes}")
            task_heading.setWordWrap(True)
            task_heading.setObjectName("ListTitle")
            task_layout.addWidget(task_heading)
            detail = QLabel(reason)
            detail.setWordWrap(True)
            detail.setObjectName("Muted")
            evidence = (row.get('selection_reason') or {}).get('ranking_evidence') or {}
            evidence_labels = {"priority": "模型排序分值", "forgetting_risk": "遗忘风险权重", "mastery_gap": "掌握缺口权重", "coverage_value": "覆盖权重", "exam_date": "考试日期", "estimate_source": "估时来源"}
            def evidence_value(key, value):
                if key == "estimate_source":
                    return {"confirmed_template": "规则估时", "user": "手动估时", "user_estimate": "手动估时"}.get(value, value)
                return f"{value:.3f}" if isinstance(value, float) else str(value)
            extra = [f"{label}：{evidence_value(key, evidence[key])}" for key,label in evidence_labels.items() if evidence.get(key) is not None]
            detail.setText(reason + ("\n" + "；".join(extra) if extra else ""))
            details = disclosure("查看依据", detail)
            task_layout.addWidget(details)
            checkbox = QCheckBox("已完成" if completed else "标记完成")
            checkbox.setAccessibleName(f"完成任务：{row['item_text']}")
            checkbox.setObjectName("BudgetPlanItem")
            checkbox.setProperty("itemId", row["id"])
            checkbox.setChecked(completed)
            checkbox.setEnabled(not preview)
            if preview:
                checkbox.setToolTip("确认安排后可记录完成状态")

            def update_budget_check(value, item_id=row["id"]):
                update_study_plan_item_state(item_id, checked=bool(value))
                invalidate_preview()
                render_budgeted_plan()
                budget_status.setText("完成状态已保存")

            checkbox.stateChanged.connect(update_budget_check)
            task_layout.addWidget(checkbox)
            budget_result_layout.addWidget(task)
            task_rows[row['id']] = (task, details)

    def invalidate_preview(*_args):
        preview_holder['signature'] = None
        confirm_budget_button.setEnabled(False)
        if not budget_schema_ready:
            return
        raw = budget_minutes_input.text().strip()
        total = sum(field.value() for field in estimate_inputs.values())
        try:
            available = int(raw)
            if not 0 <= available <= 1440:
                raise ValueError()
            delta = total - available
            load_label.setText(f"候选 {total} 分钟 · 预算 {available} 分钟 · " +
                              (f"超出 {delta} 分钟" if delta > 0 else f"余量 {abs(delta)} 分钟"))
        except ValueError:
            load_label.setText(f"候选 {total} 分钟 · 预算请输入 0–1440 分钟")
        budget_status.setText("输入已变更，请重新预览")

    def generate_budgeted_plan(*, preview_only=False, require_preview=False):
        nonlocal candidate_collection, loaded_estimates
        scroll_position = scroll.verticalScrollBar().value()
        try:
            raw_budget = budget_minutes_input.text().strip()
            if not raw_budget:
                raise ValueError("请填写今日可用时间（0–1440 分钟）")
            if not raw_budget.isdecimal() or len(raw_budget) > 4 or not 0 <= int(raw_budget) <= 1440:
                raise ValueError("今日可用时间须为 0–1440 的整数分钟")
            budget = validate_day_budget_input(
                state.today.isoformat(), int(raw_budget) if raw_budget else None
            )
            exam_dates = {
                subject_name: validate_subject_exam_date(field.text().strip())
                for subject_name, field in exam_inputs.items()
            }
            estimates = dict(loaded_estimates)
            new_estimates = {}
            for task_id, field in estimate_inputs.items():
                if field.value() > 0:
                    existing = loaded_estimates.get(task_id)
                    estimate = (
                        existing
                        if existing is not None and existing.estimated_minutes == field.value()
                        else make_task_estimate(candidate_by_id[task_id], field.value(), USER_ESTIMATE)
                    )
                    estimates[task_id] = estimate
                    if estimate is not existing:
                        new_estimates[task_id] = estimate
            prior = get_budgeted_day_plan(state.today.isoformat())
            prior_done = {r['task_id']: r for r in (prior or {}).get('items', []) if r['checked']}
            missing = set(prior_done) - set(candidate_by_id)
            if missing:
                raise ValueError("已有完成任务不在当前推荐中。请保留当前计划，刷新数据核对后再调整。")
            enriched = tuple(
                replace(candidate, estimated_minutes=estimates[candidate.task_id].estimated_minutes,
                        estimate_source=estimates[candidate.task_id].source,
                        completion_state="checked" if candidate.task_id in prior_done else candidate.completion_state)
                if candidate.task_id in estimates else candidate
                for candidate in candidate_collection.candidates
            )
            result = build_budgeted_day_plan(
                BudgetPlanInput(
                    budget.plan_date, budget.available_minutes, activity_subject_names(state),
                    subject_scope.currentData() or None, exam_dates, budget.plan_date,
                ),
                CandidateCollection(enriched, candidate_collection.unmapped),
            )
            if preview_only:
                preview_holder['signature'] = result.input_signature
                rows = []
                for section, decisions in (("selected", result.selected), ("completed", result.completed), ("excluded", result.excluded)):
                    for decision in decisions:
                        rows.append({"id": -(len(rows)+1), "item_text": decision.title,
                                     "estimated_minutes": decision.estimated_minutes,
                                     "checked": section == "completed", "excluded_reason": decision.rule_code if section == "excluded" else None,
                                     "selection_reason": {"reason": decision.reason, "ranking_evidence": dict(decision.ranking_evidence)}})
                render_budgeted_plan({"summary": {"planned_minutes": result.planned_minutes, "remaining_minutes": result.remaining_minutes,
                                      "over_budget_completed_minutes": result.over_budget_completed_minutes,
                                      "completed_occupancy_unknown": result.completed_occupancy_unknown}, "items": rows}, preview=True)
                budget_status.setText(f"预览：安排 {len(result.selected)} 项，保留已完成 {len(result.completed)} 项，未安排 {len(result.excluded)} 项。确认后保存。")
                confirm_budget_button.setEnabled(True)
                QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(scroll_position))
                return
            if require_preview and preview_holder['signature'] != result.input_signature:
                invalidate_preview()
                budget_status.setText("任务状态已变化，请重新预览后确认。")
                return
            save_study_day_budget(budget.plan_date, budget.available_minutes)
            for subject_name, exam_date in exam_dates.items():
                save_subject_exam_date(state, subject_name, exam_date)
            for task_id, estimate in new_estimates.items():
                save_task_estimate(state, candidate_by_id[task_id], estimate.estimated_minutes,
                                   estimate.source)
            create_budgeted_day_plan(budget.plan_date, budget.available_minutes, result)
            loaded_estimates = estimates
            preview_holder["signature"] = None
            budget_apply_button.setFocus()
            confirm_budget_button.setEnabled(False)
            render_budgeted_plan()
            budget_status.setText("今日安排已保存")
            QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(scroll_position))
        except Exception as error:
            budget_status.setText(f"时间预算计划未保存：{error}")
            confirm_budget_button.setEnabled(False)
            preview_holder["signature"] = None

    def mark_manual_estimate(task_id: str, value: int):
        existing = loaded_estimates.get(task_id)
        field = estimate_inputs[task_id]
        if existing is None or existing.estimated_minutes != value:
            field.setSuffix(" 分钟 · 手动")
        elif existing.source == USER_ESTIMATE:
            field.setSuffix(" 分钟 · 已保存")
        else:
            field.setSuffix(" 分钟 · 助理估时")

    for task_id, field in estimate_inputs.items():
        field.valueChanged.connect(
            lambda value, current_task_id=task_id: mark_manual_estimate(current_task_id, value)
        )

    budget_apply_button.clicked.connect(lambda _checked=False: generate_budgeted_plan(preview_only=True))
    confirm_budget_button.clicked.connect(lambda _checked=False: generate_budgeted_plan(require_preview=True))
    for field in [budget_minutes_input, *exam_inputs.values()]:
        field.textChanged.connect(invalidate_preview)
    for field in estimate_inputs.values():
        field.valueChanged.connect(invalidate_preview)
    invalidate_preview()
    if budget_schema_ready:
        budget_status.setText("")

    phase_status = QLabel()
    phase_status.setObjectName("StatusLabel")
    phase_status.setWordWrap(True)
    layout.addWidget(phase_status)

    plan_host = QWidget()
    plan_layout = QVBoxLayout(plan_host)
    plan_layout.setContentsMargins(0, 0, 0, 0)
    plan_layout.setSpacing(14)
    progress_label = QLabel("计划进度：尚未生成")
    progress_label.setObjectName("StatusLabel")
    progress_label.setWordWrap(True)
    progress_bar = QProgressBar()
    progress_bar.setRange(0, 100)
    progress_bar.setValue(0)
    layout.addWidget(progress_label)
    layout.addWidget(progress_bar)
    placeholder = QLabel("暂无计划")
    placeholder.setObjectName("Muted")
    placeholder.setWordWrap(True)
    plan_layout.addWidget(placeholder)
    layout.addWidget(plan_host)
    layout.addStretch()

    def clear_layout():
        while plan_layout.count():
            item = plan_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_generation_busy(busy: bool):
        generate_button.setDisabled(busy)
        final_review_button.setDisabled(busy)
        clear_button.setDisabled(busy)
        mock_exam_button.setDisabled(busy)
        mock_exam_mode.setDisabled(busy)
        subject_scope.setDisabled(busy)
        if busy:
            generate_button.setText("正在生成...")
            progress_bar.setRange(0, 0)
            progress_label.setText("正在生成计划…")
        else:
            generate_button.setText("生成今日计划")
            progress_bar.setRange(0, 100)

    def refresh_phase_status():
        from study_app.core.study_phase import exam_scope_label, is_final_review

        active = [name for name in final_review_subject_names(state) if is_final_review(name)]
        if active:
            active_text = "、".join(
                f"{name}（考试范围：{exam_scope_label(name)}）"
                if exam_scope_label(name)
                else name
                for name in active
            )
            phase_status.setText(
                "期末复习模式已启用：" + active_text
                + "。这些学科将提高掌握度缺口权重与综合题比例；无题目证据时按初始掌握度参与排序。"
            )
        else:
            phase_status.setText("当前没有学科启用期末复习模式。")

    def manage_final_review_modes():
        nonlocal state
        from study_app.core.study_phase import exam_scope_label, is_final_review, set_subject_phase

        dialog = QDialog(content)
        dialog.setWindowTitle("管理期末复习模式")
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(24, 22, 24, 22)
        dialog_layout.setSpacing(14)

        dialog_title = QLabel("选择进入期末复习阶段的学科")
        dialog_title.setObjectName("SectionTitle")
        dialog_hint = QLabel(
            "启用后，该学科仍使用统一的加权优先级排序，但掌握度缺口权重会提高，"
            "并增加跨章节综合练习比例；无题目证据的知识点按初始掌握度参与排序。"
        )
        dialog_hint.setObjectName("Muted")
        dialog_hint.setWordWrap(True)
        dialog_layout.addWidget(dialog_title)
        dialog_layout.addWidget(dialog_hint)

        phase_checks = {}
        initial_states = {}
        for subject_name in final_review_subject_names(state):
            checked = is_final_review(subject_name)
            initial_states[subject_name] = checked
            scope_label = exam_scope_label(subject_name)
            checkbox = QCheckBox(
                f"{subject_name}（考试范围：{scope_label}）"
                if scope_label
                else subject_name
            )
            checkbox.setChecked(checked)
            phase_checks[subject_name] = checkbox
            dialog_layout.addWidget(checkbox)

        button_row = QHBoxLayout()
        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("GhostButton")
        save_button = QPushButton("保存设置")
        save_button.setObjectName("PrimaryButton")
        button_row.addStretch()
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)
        dialog_layout.addLayout(button_row)

        cancel_button.clicked.connect(dialog.reject)

        def save_modes():
            changed = []
            for subject_name, checkbox in phase_checks.items():
                enabled = checkbox.isChecked()
                if enabled == initial_states[subject_name]:
                    continue
                set_subject_phase(subject_name, "final_review" if enabled else "regular")
                archive_active_study_plan(subject_name)
                changed.append(subject_name)
            if changed:
                archive_active_study_plan(None)
                delete_settings_by_prefix("daily_summary_cache:")
            dialog.accept()

        save_button.clicked.connect(save_modes)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        state = load_dashboard_state()
        refresh_phase_status()
        render_saved_plan(get_active_study_plan(subject_scope.currentData() or None))
        changed_text = [
            f"{name}：{'已启用' if phase_checks[name].isChecked() else '已退出'}"
            for name in phase_checks
            if phase_checks[name].isChecked() != initial_states[name]
        ]
        if changed_text:
            QMessageBox.information(
                content,
                "期末复习模式已更新",
                "\n".join(changed_text) + "\n\n相关旧计划已归档，请重新生成今日计划。",
            )

    def finish_plan_generation(
        plan,
        plan_source: str,
        subject_name: str | None,
        generation_date,
        input_signature: str,
        revision_token,
    ):
        set_generation_busy(False)
        if plan_source != "local":
            progress_label.setText(plan_source)
        revalidate_subject_revision(revision_token)
        create_study_plan(
            subject_name,
            generation_date.isoformat(),
            generation_date.isoformat(),
            input_signature,
            plan,
            build_study_plan_items(plan),
        )
        delete_settings_by_prefix("daily_summary_cache:")
        render_saved_plan(get_active_study_plan(subject_name), allow_auto_refresh=False, reload_state=False)
        if (
            plan_source.startswith("LLM 增强计划生成失败")
            or plan_source.startswith("已显示本地计划；LLM 增强未通过校验")
        ):
            QMessageBox.warning(
                content,
                "LLM 计划生成失败",
                "LLM 增强计划未能生成可保存结果；系统已保留本地计划。\n\n"
                f"详细原因：{plan_source}",
            )

    def fail_plan_generation(error: str):
        set_generation_busy(False)
        progress_label.setText("计划生成失败，请检查网络或 LLM 设置后重试。")
        QMessageBox.warning(content, "计划生成失败", error)

    def active_plan_generation_token():
        return getattr(content, "_plan_generation_token", None)

    def clear_plan_generation_state(token=None):
        if token is not None and active_plan_generation_token() is not token:
            return
        setattr(content, "_plan_worker", None)
        setattr(content, "_plan_generation_token", None)
        watchdog = getattr(content, "_plan_worker_watchdog", None)
        if watchdog is not None:
            watchdog.stop()
            watchdog.deleteLater()
            setattr(content, "_plan_worker_watchdog", None)

    def render_plan(force_regenerate: bool = False):
        nonlocal state
        if getattr(content, "_plan_worker", None) is not None:
            worker = content._plan_worker
            if worker.isRunning():
                return
        delete_settings_by_prefix("daily_summary_cache:")
        state = load_dashboard_state()
        clear_layout()
        subject_name = subject_scope.currentData() or None
        plan_llm_hint = llm_plan_unavailable_hint()
        active_plan = get_active_study_plan(subject_name)
        if active_plan:
            should_regenerate = (
                force_regenerate
                or should_refresh_plan_for_model(active_plan, state, subject_name)
                or should_upgrade_plan_to_llm(active_plan)
            )
            if not should_regenerate:
                render_saved_plan(active_plan)
                if plan_llm_hint:
                    QMessageBox.information(content, "未使用 LLM 生成计划", plan_llm_hint)
                return
        set_generation_busy(True)
        generation_date = state.today
        generation_signature = current_study_plan_signature(state, subject_name)
        try:
            lifecycle_revision_token = (
                capture_subject_revision(DEFAULT_DB_PATH, subject_name)
                if subject_name
                else None
            )
        except Exception as error:
            fail_plan_generation(str(error))
            return
        generation_token = object()
        content._plan_generation_token = generation_token
        worker = PlanGenerationThread(
            state, subject_name, lifecycle_revision_token, content
        )
        content._plan_worker = worker

        def abandon_if_page_destroyed():
            if active_plan_generation_token() is generation_token:
                setattr(content, "_plan_worker", None)
                setattr(content, "_plan_generation_token", None)
                setattr(content, "_plan_worker_watchdog", None)

        content.destroyed.connect(abandon_if_page_destroyed)

        def finish_if_current(plan, source):
            if active_plan_generation_token() is not generation_token:
                return
            clear_plan_generation_state(generation_token)
            finish_plan_generation(
                plan,
                source,
                subject_name,
                generation_date,
                generation_signature,
                lifecycle_revision_token,
            )

        def fail_if_current(error):
            if active_plan_generation_token() is not generation_token:
                return
            clear_plan_generation_state(generation_token)
            fail_plan_generation(error)

        def timeout_if_current():
            if active_plan_generation_token() is not generation_token:
                return
            worker.requestInterruption()
            clear_plan_generation_state(generation_token)
            set_generation_busy(False)
            progress_label.setText("LLM 计划生成超过 90 秒，已停止等待。请稍后重试，或临时切换为本地模式。")
            QMessageBox.warning(
                content,
                "LLM 计划生成超时",
                "LLM 计划生成超过 90 秒仍未返回，系统已停止等待，避免界面长时间卡住。\n\n"
                "如果后台请求稍后返回，应用会忽略这次过期结果；你可以重新点击生成计划。",
            )

        watchdog = QTimer(content)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(timeout_if_current)
        content._plan_worker_watchdog = watchdog

        def release_worker():
            clear_plan_generation_state(generation_token)

        relay = WorkerSignalRelay(
            on_completed=finish_if_current,
            on_failed=fail_if_current,
            on_finished=release_worker,
            parent=content,
        )
        worker.completed.connect(relay.completed)
        worker.failed.connect(relay.failed)
        worker.finished.connect(relay.finished)
        watchdog.start(90000)
        try:
            worker.start()
        except Exception as error:
            clear_plan_generation_state(generation_token)
            relay.deleteLater()
            fail_plan_generation(str(error))
            return
        if plan_llm_hint:
            QMessageBox.information(content, "未使用 LLM 生成计划", plan_llm_hint)

    def render_saved_plan(saved_plan, *, allow_auto_refresh: bool = True, reload_state: bool = True):
        from PySide6.QtCore import QTimer

        nonlocal state
        subject_name = subject_scope.currentData() or None
        if reload_state:
            state = load_dashboard_state()
        if allow_auto_refresh and saved_plan and should_refresh_plan_for_model(saved_plan, state, subject_name):
            render_plan()
            return
        scroll_position = scroll.verticalScrollBar().value()
        clear_layout()
        progress_bar.setVisible(bool(saved_plan))
        clear_button.setEnabled(bool(saved_plan))
        clear_button.setToolTip("归档当前详细计划" if saved_plan else "尚无可清空的详细计划")
        if not saved_plan:
            progress_label.setText("计划进度：尚未生成")
            progress_bar.setValue(0)
            note = QLabel("暂无计划")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            plan_layout.addWidget(note)
            return
        plan = saved_plan["plan"]
        item_by_hash = {
            (item["section_key"], item["item_type"], item["item_text"]): item
            for item in saved_plan["items"]
        }
        created_at = saved_plan.get("created_at") or ""
        checkboxes = []

        def update_progress():
            total = len(checkboxes)
            done = sum(1 for item in checkboxes if item.isChecked())
            percent = round(done / total * 100) if total else 0
            suffix = f"  | 当前计划生成于 {created_at}" if created_at else ""
            progress_label.setText(f"计划进度：{done}/{total} 项已完成{suffix}")
            progress_bar.setValue(percent)

        for title, key, interactive in [
            ("当前判断", "judgement", False),
            ("今日目标", "goals", False),
            ("今日计划", "short", True),
            ("错题诊断表", "diagnostic", False),
            ("推荐记录格式", "record_template", False),
            ("预期模型变化", "expected", False),
            ("生成依据", "evidence", False),
        ]:
            card, card_checks = plan_section_card(
                title,
                plan[key],
                interactive=interactive,
                section_key=key,
                subject_scope=subject_name,
                on_result=record_plan_result,
                on_check=record_plan_check,
                on_copy_practice_prompt=copy_practice_prompt if key == "short" else None,
                on_send_chatgpt=send_practice_prompt_to_chatgpt if key == "short" else None,
                on_generate_local_pdf=generate_local_practice_pdf if key == "short" else None,
                item_by_hash=item_by_hash,
                result_predicate=is_plan_homework_item,
                show_checkbox=True,
                subdivide_multiline=key == "short",
            )
            for checkbox in card_checks:
                checkbox.stateChanged.connect(update_progress)
            checkboxes.extend(card_checks)
            plan_layout.addWidget(card)
            if key == "short":
                summary = plan_day_feedback_card(saved_plan, state, subject_name)
                if summary is not None:
                    plan_layout.addWidget(summary)
        update_progress()
        restore_scroll_timer = QTimer(scroll)
        restore_scroll_timer.setSingleShot(True)
        restore_scroll_timer.timeout.connect(
            lambda: scroll.verticalScrollBar().setValue(scroll_position)
        )
        restore_scroll_timer.start(0)

    def clear_plan():
        archive_active_study_plan(subject_scope.currentData() or None)
        delete_settings_by_prefix("daily_summary_cache:")
        clear_layout()
        progress_label.setText("计划进度：尚未生成")
        progress_bar.setValue(0)
        note = QLabel("计划已清空")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        plan_layout.addWidget(note)

    def record_plan_check(item_id: int, checked: bool):
        update_study_plan_item_state(item_id, checked=checked)
        if checked:
            from PySide6.QtCore import QTimer

            maybe_show_completed_day_feedback()
            QTimer.singleShot(
                0,
                lambda: render_saved_plan(get_active_study_plan(subject_scope.currentData() or None)),
            )

    def record_plan_result(line: str, is_correct: bool, item_id: int | None = None):
        nonlocal state
        from datetime import date
        import re
        from PySide6.QtWidgets import QMessageBox

        subject, topic = infer_subject_topic_from_plan_line(line, subject_scope.currentData() or None)
        subject_before = next((item for item in state.subjects if item.name == subject), None)
        feedback_key = f"study_plan_item_feedback:{item_id}" if item_id is not None else ""
        feedback_snapshot = get_setting(feedback_key, {}) if feedback_key else {}
        if subject_before and not feedback_snapshot.get("before_captured"):
            feedback_snapshot.update(
                {
                    "before_captured": True,
                    "window_score_before": subject_before.window_score,
                    "covered_mastery_before": subject_before.covered_mastery_score,
                    "total_mastery_before": subject_before.mastery_score,
                }
            )
            set_setting(feedback_key, feedback_snapshot)
        difficulty_match = re.search(r"参考难度\s*(\d+(?:\.\d+)?)\s*/\s*100", line)
        difficulty_score = float(difficulty_match.group(1)) if difficulty_match else None
        score = planned_homework_score(difficulty_score, is_correct)
        result_text = "完成正确" if is_correct else "完成有误"
        record = {
            "date": date.today().isoformat(),
            "subject": subject,
            "module": None,
            "topic": topic,
            "activity": "review_exercise",
            "source": "outside_class",
            "score": score,
            "note": f"学习计划项{result_text}：{line}",
            "problems": [
                {
                    "title": f"学习计划作业：{topic}",
                    "statement": line,
                    "answer_result": "全对" if is_correct else "错误",
                    "status": "correct" if is_correct else "wrong",
                    "correctness": 100 if is_correct else 0,
                    "partial_credit": 1.0 if is_correct else 0.0,
                    "error_cause": "" if is_correct else "学习计划作业完成有误，需要复盘错因",
                    "related_topics": [topic],
                    "difficulty_score": difficulty_score,
                    "difficulty_source": "planned_reference" if difficulty_score is not None else "planned_homework",
                }
            ],
        }
        try:
            add_learning_record(
                record,
                study_plan_item_id=item_id,
                study_plan_result="correct" if is_correct else "wrong",
            )
        except Exception as error:
            QMessageBox.critical(content, "记录失败", str(error))
            return False
        try:
            delete_settings_by_prefix("daily_summary_cache:")
            state = load_dashboard_state()
            subject_after = next((item for item in state.subjects if item.name == subject), None)
            if feedback_key and subject_after:
                feedback_snapshot.update(
                    {
                        "result_score": score,
                        "window_score_after": subject_after.window_score,
                        "covered_mastery_after": subject_after.covered_mastery_score,
                        "total_mastery_after": subject_after.mastery_score,
                    }
                )
                set_setting(feedback_key, feedback_snapshot)
            maybe_show_completed_day_feedback()
            render_saved_plan(get_active_study_plan(subject_scope.currentData() or None))
        except Exception:
            QMessageBox.warning(
                content,
                "记录已保存",
                "学习记录已写入，但页面刷新失败。",
            )
            return True
        QMessageBox.information(content, "已记录", f"已按“{result_text}”写入学习记录，难度折算分 {score:.1f}。")
        return True

    def copy_practice_prompt(line: str):
        from PySide6.QtWidgets import QApplication, QMessageBox

        prompt = build_practice_generation_prompt(line, state, subject_scope.currentData() or None)
        QApplication.clipboard().setText(prompt)
        if is_oj_plan_homework(line):
            QMessageBox.information(content, "已复制", "已复制 LeetCode 原题清单，可直接按官方链接练习并提交。")
        else:
            QMessageBox.information(content, "已复制", "已复制出题提示词，可直接粘贴给 Codex、GPT 或其他 LLM。")

    def set_pdf_generation_busy(busy: bool):
        stop_pdf_button.setVisible(busy)
        stop_pdf_button.setDisabled(not busy)

    def watch_global_pdf_worker(worker):
        def release_busy():
            if getattr(content, "_chatgpt_bridge_page_alive", False):
                set_pdf_generation_busy(False)

        relay = WorkerSignalRelay(on_finished=release_busy, parent=content)
        worker.finished.connect(relay.finished)
        if worker.isFinished():
            relay.finished()

    def stop_pdf_generation():
        from PySide6.QtWidgets import QMessageBox

        with _CHATGPT_BRIDGE_LOCK:
            workers = list(_CHATGPT_BRIDGE_WORKERS)
        for worker in workers:
            if worker.isRunning():
                try:
                    result = worker.cancel()
                except Exception:
                    QMessageBox.warning(
                        content,
                        "停止 PDF 生成失败",
                        "未能停止当前 PDF 生成任务，请稍后重试。",
                    )
                    return
                if not result.success:
                    QMessageBox.warning(
                        content,
                        "停止 PDF 生成失败",
                        "未能停止当前 PDF 生成任务，请稍后重试。",
                    )
                    return
        for worker in workers:
            worker.cancelled = True
        set_pdf_generation_busy(False)
        progress_label.setText("已手动停止 ChatGPT PDF 生成，并清理临时提示词。")
        QMessageBox.information(content, "已停止", "已停止当前 ChatGPT PDF 生成流程。")

    def start_chatgpt_pdf_prompt(prompt: str):
        from PySide6.QtWidgets import QApplication, QMessageBox

        with _CHATGPT_BRIDGE_LOCK:
            active_threads = list(_CHATGPT_BRIDGE_WORKERS)
        if active_threads:
            set_pdf_generation_busy(True)
            watch_global_pdf_worker(active_threads[0])
            QMessageBox.information(
                content,
                "正在生成 PDF",
                "已有一个 ChatGPT PDF 生成任务正在运行。你可以点击“停止 PDF 生成”后再启动新的任务。",
            )
            return
        task_token = object()
        content._chatgpt_pdf_active_token = task_token
        set_pdf_generation_busy(True)
        progress_label.setText("正在让 ChatGPT 生成 PDF；生成期间可以继续使用学习应用...")
        thread = ChatGPTBridgeThread(prompt, content)
        thread.task_token = task_token
        bridge_threads = getattr(content, "_chatgpt_bridge_threads", [])
        bridge_threads.append(thread)
        content._chatgpt_bridge_threads = bridge_threads

        def detach_bridge(worker, token):
            workers = getattr(content, "_chatgpt_bridge_threads", [])
            if worker in workers:
                workers.remove(worker)
            if not getattr(content, "_chatgpt_bridge_page_alive", False):
                return False
            is_current = getattr(content, "_chatgpt_pdf_active_token", None) is token
            if is_current and not any(item.isRunning() for item in workers):
                set_pdf_generation_busy(False)
            return is_current

        def finish_bridge(result, original_prompt, worker=thread, token=task_token):
            if not detach_bridge(worker, token):
                return
            if worker.cancelled:
                progress_label.setText("ChatGPT PDF 生成已手动停止。")
                return
            if result.success:
                if result.code != "pdf_opened":
                    progress_label.setText("PDF 已下载，但未能自动打开。")
                    QMessageBox.warning(
                        content,
                        "PDF 已下载但未打开",
                        f"PDF 已安全下载，但系统未能自动打开文件。\n\n文件位置：\n{result.pdf_path}",
                    )
                    return
                progress_label.setText("ChatGPT 已生成 PDF，并已自动打开。")
                QMessageBox.information(
                    content,
                    "PDF 已生成",
                    f"ChatGPT 已完成生成，PDF 已归档并自动打开。\n\n临时文件位置：\n{result.pdf_path}",
                )
                return
            QApplication.clipboard().setText(original_prompt)
            progress_label.setText("ChatGPT 自动生成 PDF 失败，提示词已复制。")
            QMessageBox.warning(
                content,
                "ChatGPT 自动生成失败",
                "ChatGPT 未能完成 PDF 生成。为避免丢失，出题提示词已复制到剪贴板。",
            )

        def fail_bridge(_error, worker=thread, token=task_token):
            if not detach_bridge(worker, token):
                return
            if worker.cancelled:
                progress_label.setText("ChatGPT PDF 生成已手动停止。")
                return
            progress_label.setText("ChatGPT 自动生成 PDF 失败，请检查桌面端状态后重试。")
            QMessageBox.warning(
                content,
                "ChatGPT 自动生成失败",
                "ChatGPT 桌面桥接发生错误，请检查桌面端状态后重试。",
            )

        relay = WorkerSignalRelay(
            on_completed=finish_bridge,
            on_failed=fail_bridge,
            parent=content,
        )
        thread.completed.connect(relay.completed)
        thread.failed.connect(relay.failed)
        thread.finished.connect(relay.finished)
        try:
            started = thread.start()
        except Exception:
            bridge_threads.remove(thread)
            relay.deleteLater()
            set_pdf_generation_busy(False)
            QMessageBox.warning(
                content,
                "启动 PDF 生成失败",
                "未能启动 PDF 生成任务，请稍后重试。",
            )
            return
        if not started:
            bridge_threads.remove(thread)
            relay.deleteLater()
            set_pdf_generation_busy(True)
            with _CHATGPT_BRIDGE_LOCK:
                active_threads = list(_CHATGPT_BRIDGE_WORKERS)
            if active_threads:
                watch_global_pdf_worker(active_threads[0])
            QMessageBox.information(
                content,
                "正在生成 PDF",
                "已有一个 ChatGPT PDF 生成任务正在运行。你可以点击“停止 PDF 生成”后再启动新的任务。",
            )

    def send_practice_prompt_to_chatgpt(line: str):
        if is_oj_plan_homework(line):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                content,
                "无需生成 PDF",
                "算法设计与 OJ 训练使用 LeetCode 官方原题和官方测试集；系统已在计划中直接匹配题目，不再生成出题提示词。",
            )
            return
        prompt = build_practice_generation_prompt(line, state, subject_scope.currentData() or None)
        start_chatgpt_pdf_prompt(prompt)

    def generate_local_practice_pdf(line: str):
        from PySide6.QtWidgets import QMessageBox

        subject_name = str(subject_scope.currentData() or "").strip()
        try:
            result = generate_local_practice_for_plan_line(line, subject_name)
        except Exception as error:
            message = str(error).strip() or "本地练习卷生成失败，请检查题库答案和运行依赖。"
            QMessageBox.warning(content, "本地练习卷生成失败", message)
            return
        QMessageBox.information(content, "本地练习卷已生成", local_practice_success_message(result))

    stop_pdf_button.clicked.connect(stop_pdf_generation)

    def copy_plan_practice_context():
        from PySide6.QtWidgets import QApplication, QMessageBox

        saved = get_active_study_plan(subject_scope.currentData() or None)
        if not saved:
            QMessageBox.information(content, "暂无计划", "请先生成今日计划，再复制题库上下文。")
            return
        homework_items = [
            item["item_text"]
            for item in saved.get("items", [])
            if item.get("section_key") == "short" and item.get("item_type") == "result"
        ]
        if not homework_items:
            QMessageBox.information(content, "暂无作业", "当前计划中还没有可导出的每日作业。")
            return
        exports = []
        for item in homework_items:
            if is_oj_plan_homework(item):
                exports.append(build_oj_practice_list(item, state, subject_scope.currentData() or None))
            else:
                exports.append(build_practice_generation_prompt(item, state, subject_scope.currentData() or None))
        QApplication.clipboard().setText("\n\n---\n\n".join(exports))
        QMessageBox.information(content, "已复制", f"已复制 {len(exports)} 条练习上下文。OJ 作业会直接复制 LeetCode 原题清单。")

    def send_mock_exam_pdf():
        from PySide6.QtWidgets import QMessageBox

        subject_name = subject_scope.currentData() or None
        if not subject_name:
            QMessageBox.information(content, "请选择学科", "生成模拟卷前，请先在计划范围中选择一个具体学科。")
            return
        try:
            prompt = build_mock_exam_generation_prompt(state, subject_name, str(mock_exam_mode.currentData() or "diagnostic"))
        except Exception as error:
            QMessageBox.warning(content, "模拟卷提示词生成失败", str(error))
            return
        start_chatgpt_pdf_prompt(prompt)

    def maybe_show_completed_day_feedback():
        from PySide6.QtWidgets import QMessageBox

        saved = get_active_study_plan(subject_scope.currentData() or None)
        if not saved:
            return
        shown_key = "study_plan_day_feedback_shown"
        shown = set(get_setting(shown_key, []) or [])
        new_shown = set(shown)
        for feedback in plan_day_feedback_details(saved, state, subject_scope.currentData() or None):
            marker = f"{saved['id']}:{feedback['day_index']}"
            if not feedback["complete"] or marker in shown:
                continue
            QMessageBox.information(content, "当天计划已完成", feedback["popup"])
            new_shown.add(marker)
        if new_shown != shown:
            set_setting(shown_key, sorted(new_shown))

    generate_handler = lambda _checked=False: render_plan(force_regenerate=True)
    clear_handler = lambda _checked=False: clear_plan()
    generate_button.clicked.connect(generate_handler)
    final_review_button.clicked.connect(lambda _checked=False: manage_final_review_modes())
    clear_button.clicked.connect(clear_handler)
    copy_context_button.clicked.connect(lambda _checked=False: copy_plan_practice_context())
    mock_exam_button.clicked.connect(lambda _checked=False: send_mock_exam_pdf())
    def refresh_scope_views(_index=None):
        invalidate_preview()
        render_saved_plan(get_active_study_plan(subject_scope.currentData() or None), reload_state=False)
        render_budgeted_plan()

    subject_scope.currentIndexChanged.connect(refresh_scope_views)
    refresh_phase_status()
    render_saved_plan(get_active_study_plan(subject_scope.currentData() or None), reload_state=False)
    render_budgeted_plan()
    content._plan_callbacks = (render_plan, render_saved_plan, clear_plan, clear_layout, generate_handler, clear_handler)
    content._budget_plan_callbacks = (generate_budgeted_plan, render_budgeted_plan)
    def update_dashboard_state(new_state: DashboardState):
        nonlocal state
        state = new_state

    scroll._set_dashboard_state = update_dashboard_state
    from study_app.ui.design_components import disclosure, readable_page
    for widget in (phase_status, progress_label, progress_bar, plan_host):
        layout.removeWidget(widget)
        tools_layout.addWidget(widget)
    tools_group = disclosure("详细学习方案与出卷工具", tools_body)
    layout.insertWidget(layout.count()-1, tools_group)
    readable_page(scroll, content)

    def open_task(item_id=None):
        render_budgeted_plan()
        if item_id in task_rows:
            task, details = task_rows[item_id]
            details.toggle.setChecked(True)
            QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(task, 0, 24))
        else:
            scroll.verticalScrollBar().setValue(0)
    scroll._open_task = open_task
    scroll.setWidget(content)
    return scroll

def plan_day_feedback_card(saved_plan: dict, state: DashboardState, subject_scope: str | None):
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    summaries = [item["line"] for item in plan_day_feedback_details(saved_plan, state, subject_scope)]
    if not summaries:
        return None
    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    heading = QLabel("每日完成反馈")
    heading.setObjectName("CardTitle")
    layout.addWidget(heading)
    for line in summaries:
        label = QLabel(humanize_plan_text(line))
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        layout.addWidget(label)
    return card

def plan_section_card(
    title: str,
    lines: list[str],
    interactive: bool = False,
    section_key: str = "",
    subject_scope: str | None = None,
    on_result=None,
    on_check=None,
    on_copy_practice_prompt=None,
    on_send_chatgpt=None,
    on_generate_local_pdf=None,
    item_by_hash: dict[str, dict] | None = None,
    result_predicate=None,
    show_checkbox: bool = True,
    subdivide_multiline: bool = False,
):
    from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    heading = QLabel(title)
    heading.setObjectName("CardTitle")
    layout.addWidget(heading)
    checkboxes = []
    handlers = []
    def add_interactive_row(text: str, row_kind: str = "plain"):
        plan_item = (item_by_hash or {}).get((section_key, row_kind, text))
        display_text = humanize_plan_text(text)
        row = QGridLayout()
        row.setSpacing(8)
        for column in range(3):
            row.setColumnStretch(column, 1)
        use_checkbox = show_checkbox or row_kind == "check"
        checkbox = QCheckBox() if use_checkbox else None
        label = QLabel(display_text)
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        if checkbox is not None:
            if plan_item and plan_item.get("checked"):
                checkbox.setChecked(True)
                checkbox.setEnabled(False)
            if plan_item and on_check:
                item_id = int(plan_item["id"])

                def check_handler(state, saved_item_id=item_id, box=checkbox):
                    on_check(saved_item_id, box.isChecked())
                    if box.isChecked():
                        box.setEnabled(False)

                checkbox.stateChanged.connect(check_handler)
                handlers.append(check_handler)
            row.addWidget(checkbox, 0, 0)
        row.addWidget(label, 0, 1 if checkbox is not None else 0, 1, 2 if checkbox is not None else 3)
        action_buttons = []
        is_homework_row = (
            row_kind == "result"
            or (row_kind == "plain" and (result_predicate is None or result_predicate(text)))
        )
        is_oj_row = is_oj_plan_homework(text)
        if is_homework_row and plan_item and on_result and not is_oj_row:
            item_id = int(plan_item["id"])
            result_buttons = []
            for button_text, is_correct, object_name in (
                ("完成正确", True, "PrimaryButton"),
                ("完成有误", False, "DangerButton"),
            ):
                result_button = QPushButton(button_text)
                result_button.setObjectName(object_name)
                result_button.setDisabled(bool(plan_item.get("result")))
                result_buttons.append(result_button)

                def result_handler(
                    _checked=False,
                    source_text=text,
                    correct=is_correct,
                    saved_item_id=item_id,
                    row_buttons=result_buttons,
                ):
                    for button in row_buttons:
                        button.setDisabled(True)
                    if on_result(source_text, correct, saved_item_id) is False:
                        for button in row_buttons:
                            button.setDisabled(False)

                result_button.clicked.connect(result_handler)
                handlers.append(result_handler)
                action_buttons.append(result_button)
        if is_homework_row and on_copy_practice_prompt:
            copy_button = QPushButton("复制 LeetCode 清单" if is_oj_row else "复制出题提示词")
            copy_button.setObjectName("GhostButton")

            def copy_handler(_checked=False, source_text=text):
                on_copy_practice_prompt(source_text)

            copy_button.clicked.connect(copy_handler)
            handlers.append(copy_handler)
            action_buttons.append(copy_button)
        if is_homework_row and on_generate_local_pdf and not is_oj_row:
            local_button = QPushButton("本地生成练习卷")
            local_button.setObjectName("PrimaryButton")

            def local_handler(_checked=False, source_text=text):
                on_generate_local_pdf(source_text)

            local_button.clicked.connect(local_handler)
            handlers.append(local_handler)
            action_buttons.append(local_button)
        if is_homework_row and on_send_chatgpt and not is_oj_row:
            send_button = QPushButton("让 ChatGPT 生成 PDF")
            send_button.setObjectName("GhostButton")

            def send_handler(_checked=False, source_text=text):
                on_send_chatgpt(source_text)

            send_button.clicked.connect(send_handler)
            handlers.append(send_handler)
            action_buttons.append(send_button)
        for index, button in enumerate(action_buttons):
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            row.addWidget(button, 1 + index // 3, index % 3)
        layout.addLayout(row)
        if checkbox is not None:
            checkboxes.append(checkbox)

    for line in lines:
        if interactive:
            if subdivide_multiline:
                for row_kind, text in split_short_plan_item(line):
                    if row_kind == "heading":
                        day_label = QLabel(text)
                        day_label.setObjectName("CardTitle")
                        day_label.setWordWrap(True)
                        layout.addWidget(day_label)
                    elif row_kind == "info":
                        info_label = QLabel(text)
                        info_label.setObjectName("ListTitle")
                        info_label.setWordWrap(True)
                        layout.addWidget(info_label)
                    else:
                        add_interactive_row(text, row_kind)
            else:
                add_interactive_row(line)
        else:
            label = QLabel(line)
            label.setObjectName("ListTitle")
            label.setWordWrap(True)
            layout.addWidget(label)
    card._plan_result_handlers = handlers
    return card, checkboxes
