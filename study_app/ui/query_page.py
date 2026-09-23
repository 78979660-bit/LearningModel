"""学习助理页面（原“问模型”）。

Module top imports stay pure (no PySide6, no database): everything else is
imported lazily inside the ``query_page`` factory so importing this module
never pulls Qt or the engine. The read-only Q&A semantics (local answer
first, optional LLM enrichment with a 30s timeout, duplicate-question
dedupe, fallback on failure) are preserved exactly; the assistant surface
(intent -> proposal -> confirm/execute/undo) is added on top and runs every
engine/database call inside AsyncTaskService workers polled by a 20ms QTimer.
"""
from __future__ import annotations

from study_app.core.async_tasks import AsyncTaskService, TaskStatus
from study_app.core.dashboard import DashboardState


QUERY_TASK_TIMEOUT_SECONDS = 30.0

ASSISTANT_QUERY_SLOT = "query-page"
ASSISTANT_PREVIEW_SLOT = "query-page-assistant"
ASSISTANT_EXECUTE_SLOT = "query-page-execution"
ASSISTANT_UNDO_SLOT = "query-page-undo"

ASSISTANT_ATTACHMENT_FILTER = (
    "学习材料 (*.pdf *.png *.jpg *.jpeg *.webp *.txt *.md *.docx *.pptx *.xlsx)"
)
ADVISOR_MODE_TEXTS = {
    "external": "外部顾问",
    "mock": "模拟顾问",
    "unset": "作业提交未配置",
}
CHIP_READONLY = "只读问答"
CHIP_SUGGESTED = "建议操作"
CHIP_PENDING = "待确认"
CHIP_RUNNING = "执行中"
CHIP_DONE = "已完成"
CHIP_FAILED = "失败"


def query_page(state: DashboardState, alerts_jump=None, *, db_path=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QCheckBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
    import study_app.ai.natural_query as natural_query
    import study_app.ai.providers as providers
    from study_app.core.learning_assistant_schema import (
        ACTION_LABELS,
        AUTO_ELIGIBLE_ACTIONS,
    )

    service = AsyncTaskService(max_workers=2)
    state_holder = {"state": state}
    lifecycle = {"open": True, "handle": None, "question": "", "local_answer": None}
    attachment_holder = {"paths": []}
    proposal_holder = {"bundle": None, "proposal": None, "auto": False, "text": "", "paths": []}
    pending: list[tuple[object, object]] = []
    timer = None

    def make_engine():
        from study_app.core.learning_assistant_engine import LearningAssistantEngine

        if db_path is None:
            return LearningAssistantEngine()
        return LearningAssistantEngine(db_path=db_path)

    def engine_available() -> bool:
        try:
            import study_app.core.learning_assistant_engine  # noqa: F401
        except Exception:
            return False
        return True

    def close_tasks():
        if not lifecycle["open"]:
            return
        lifecycle["open"] = False
        if timer is not None:
            timer.stop()
        service.invalidate(ASSISTANT_QUERY_SLOT)
        service.invalidate(ASSISTANT_PREVIEW_SLOT)
        service.close()

    class QueryScrollArea(QScrollArea):
        def closeEvent(self, event):
            close_tasks()
            super().closeEvent(event)

    scroll = QueryScrollArea()
    scroll.destroyed.connect(close_tasks)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(30, 28, 30, 30)
    layout.setSpacing(14)

    # ---- hero -----------------------------------------------------------------
    hero = QWidget()
    hero.setObjectName("RecordHero")
    hero_layout = QVBoxLayout(hero)
    hero_layout.setContentsMargins(18, 16, 18, 16)
    hero_layout.setSpacing(6)
    title = QLabel("学习助理")
    title.setObjectName("HeroTitle")
    hero_layout.addWidget(title)
    layout.addWidget(hero)

    # ---- mode chip + status strip ---------------------------------------------
    mode_chip = QLabel(CHIP_READONLY)
    mode_chip.setObjectName("AssistantModeChip")
    mode_chip.setStyleSheet(
        "padding: 2px 12px; border-radius: 11px; background-color: #e0e7ff; "
        "color: #1e3a8a; font-weight: 600;"
    )
    chip_row = QHBoxLayout()
    chip_row.addWidget(mode_chip)
    chip_row.addStretch()
    layout.addLayout(chip_row)

    status_strip = QLabel("")
    status_strip.setObjectName("AssistantStatusStrip")
    status_strip.setWordWrap(True)

    def refresh_status_strip():
        try:
            llm_settings = providers.load_llm_settings()
            provider_text = providers.provider_status(llm_settings)
            mode_text = f"LLM 增强 · {llm_settings.model}" if llm_settings.enabled else "本地模式"
        except Exception as error:
            provider_text = f"LLM 状态读取失败：{error}"
            mode_text = "LLM 状态读取失败"
        try:
            from study_app.core.learning_assistant_policy import load_privacy_settings

            settings = (
                load_privacy_settings()
                if db_path is None
                else load_privacy_settings(db_path)
            )
            advisor_mode = settings.get("advisor_mode")
        except Exception:
            advisor_mode = "unset"
        advisor_text = ADVISOR_MODE_TEXTS.get(advisor_mode, ADVISOR_MODE_TEXTS["unset"])
        status_strip.setText(f"{mode_text} · {advisor_text}")
        status_strip.setToolTip(provider_text + '\n附件不上传')

    layout.addWidget(status_strip)

    def open_subject_management():
        from study_app.ui.subject_management_dialog import subject_management_dialog
        dialog = subject_management_dialog(scroll.window(), db_path=db_path)
        dialog.exec()
        if dialog.changed:
            refresh = getattr(scroll.window(), 'refresh_subject_catalog', None)
            if callable(refresh):
                QTimer.singleShot(0, refresh)
        dialog.deleteLater()

    subject_button = QPushButton('学科管理 · 引入 / 移除 / 恢复')
    subject_button.setObjectName('SubjectManagementEntry')
    subject_button.setToolTip('本地预览、输入名称确认和自动备份；不会自动执行')
    subject_button.clicked.connect(open_subject_management)
    chip_row.addWidget(subject_button)

    # ---- question input --------------------------------------------------------
    question_input = QTextEdit()
    question_input.setObjectName("QueryQuestionInput")
    question_input.setPlaceholderText(
        "输入问题或学习记录"
    )
    question_input.setFixedHeight(112)
    layout.addWidget(question_input)

    # ---- attachment row ---------------------------------------------------------
    attachment_list = QLabel("未选择附件")
    attachment_list.setObjectName("AssistantAttachmentList")
    attachment_list.setWordWrap(True)

    def refresh_attachment_list():
        paths = attachment_holder["paths"]
        attachment_list.setText("\n".join(paths) if paths else "未选择附件")

    def choose_attachments():
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            content, "选择学习材料", "", ASSISTANT_ATTACHMENT_FILTER
        )
        for path in paths:
            if path and path not in attachment_holder["paths"]:
                attachment_holder["paths"].append(path)
        refresh_attachment_list()

    def clear_attachments():
        attachment_holder["paths"].clear()
        refresh_attachment_list()

    attach_button = QPushButton("附加文件…")
    attach_button.setObjectName("AssistantAttachButton")
    clear_button = QPushButton("清空附件")
    clear_button.setObjectName("AssistantAttachmentClearButton")
    attachment_row = QHBoxLayout()
    attachment_row.addWidget(attach_button)
    attachment_row.addWidget(clear_button)
    attachment_row.addStretch()
    attachment_column = QVBoxLayout()
    attachment_column.addLayout(attachment_row)
    attachment_column.addWidget(attachment_list)
    layout.addLayout(attachment_column)

    # ---- send button -------------------------------------------------------------
    ask_button = QPushButton("发送")
    ask_button.setObjectName("PrimaryButton")
    send_row = QHBoxLayout()
    send_row.addStretch()
    send_row.addWidget(ask_button)
    layout.addLayout(send_row)
    ask_button.setEnabled(False)
    ask_button.setToolTip("输入问题或请求后发送")
    question_input.textChanged.connect(lambda: ask_button.setEnabled(bool(question_input.toPlainText().strip())))

    # ---- result card ---------------------------------------------------------------
    result_card = QWidget()
    result_card.setObjectName("Card")
    result_layout = QVBoxLayout(result_card)
    result_layout.setContentsMargins(20, 18, 20, 18)
    result_layout.setSpacing(10)
    result_title = QLabel("回答")
    result_title.setObjectName("CardTitle")
    result_layout.addWidget(result_title)
    result_body = QLabel("暂无回答")
    result_body.setObjectName("ListTitle")
    result_body.setWordWrap(True)
    result_layout.addWidget(result_body)
    layout.addWidget(result_card)

    # ---- proposal card ---------------------------------------------------------------
    proposal_card = QWidget()
    proposal_card.setObjectName("AssistantProposalCard")
    proposal_layout = QVBoxLayout(proposal_card)
    proposal_layout.setContentsMargins(20, 18, 20, 18)
    proposal_layout.setSpacing(10)
    proposal_heading = QLabel("待确认提案")
    proposal_heading.setObjectName("CardTitle")
    proposal_layout.addWidget(proposal_heading)
    proposal_summary = QLabel("")
    proposal_summary.setObjectName("AssistantProposalSummary")
    proposal_summary.setWordWrap(True)
    proposal_layout.addWidget(proposal_summary)
    proposal_buttons = QHBoxLayout()
    confirm_button = QPushButton("确认执行")
    confirm_button.setObjectName("AssistantConfirmButton")
    cancel_button = QPushButton("取消")
    cancel_button.setObjectName("AssistantCancelButton")
    repreview_button = QPushButton("重新预览")
    repreview_button.setObjectName("AssistantRepreviewButton")
    retry_button = QPushButton("重试")
    retry_button.setObjectName("AssistantRetryButton")
    for button in (confirm_button, cancel_button, repreview_button, retry_button):
        proposal_buttons.addWidget(button)
    proposal_buttons.addStretch()
    proposal_layout.addLayout(proposal_buttons)
    proposal_card.setVisible(False)
    layout.addWidget(proposal_card)

    # ---- undo + receipt card ------------------------------------------------------
    receipt_card = QWidget()
    receipt_card.setObjectName("Card")
    receipt_layout = QVBoxLayout(receipt_card)
    receipt_layout.setContentsMargins(20, 18, 20, 18)
    receipt_layout.setSpacing(10)
    receipt_heading = QLabel("执行与撤销")
    receipt_heading.setObjectName("CardTitle")
    receipt_layout.addWidget(receipt_heading)
    undo_button = QPushButton("撤销上一步")
    undo_button.setObjectName("AssistantUndoButton")
    receipt_layout.addWidget(undo_button)
    receipt_label = QLabel("尚无执行回执。")
    receipt_label.setObjectName("AssistantReceiptLabel")
    receipt_label.setWordWrap(True)
    receipt_layout.addWidget(receipt_label)
    from study_app.ui.design_components import disclosure
    receipt_group = disclosure("历史操作与撤销", receipt_card)
    layout.addWidget(receipt_group)

    # ---- alerts summary card --------------------------------------------------------
    alerts_card = QWidget()
    alerts_card.setObjectName("Card")
    alerts_layout = QVBoxLayout(alerts_card)
    alerts_layout.setContentsMargins(20, 18, 20, 18)
    alerts_layout.setSpacing(10)
    alerts_summary = QLabel("知识预警：读取中…")
    alerts_summary.setObjectName("AssistantAlertSummary")
    alerts_summary.setWordWrap(True)
    alerts_button = QPushButton("查看预警")
    alerts_button.setObjectName("AssistantAlertsJumpButton")
    alerts_button.setVisible(callable(alerts_jump))
    alerts_row = QHBoxLayout()
    alerts_row.addWidget(alerts_summary, 1)
    alerts_row.addWidget(alerts_button)
    alerts_layout.addLayout(alerts_row)
    layout.addWidget(alerts_card)

    def refresh_alert_summary():
        try:
            from study_app.data.database import (
                DatabaseNotInitializedError,
                list_knowledge_alerts,
            )
        except Exception:
            alerts_summary.setText("知识预警结构未安装")
            return
        try:
            alerts = (
                list_knowledge_alerts(status="active")
                if db_path is None
                else list_knowledge_alerts(status="active", db_path=db_path)
            )
        except DatabaseNotInitializedError:
            alerts_summary.setText("知识预警结构未安装")
        except Exception as error:
            alerts_summary.setText(f"知识预警暂不可用：{error}")
        else:
            alerts_summary.setText(f"知识预警：{len(alerts)} 条活动")

    # ---- auto-exec policy row ---------------------------------------------------------
    auto_exec_card = QWidget()
    auto_exec_card.setObjectName("Card")
    auto_exec_layout = QVBoxLayout(auto_exec_card)
    auto_exec_layout.setContentsMargins(20, 18, 20, 18)
    auto_exec_layout.setSpacing(8)
    auto_exec_heading = QLabel("自动执行策略")
    auto_exec_heading.setObjectName("CardTitle")
    auto_exec_layout.addWidget(auto_exec_heading)

    def load_auto_policy() -> dict:
        try:
            from study_app.core.learning_assistant_policy import load_auto_exec_policy

            policy = (
                load_auto_exec_policy()
                if db_path is None
                else load_auto_exec_policy(db_path)
            )
            return dict(policy)
        except Exception:
            return {}

    def make_auto_toggle(action: str):
        def toggle(checked: bool):
            try:
                from study_app.core.learning_assistant_policy import (
                    load_auto_exec_policy,
                    save_auto_exec_policy,
                )

                current = (
                    load_auto_exec_policy()
                    if db_path is None
                    else load_auto_exec_policy(db_path)
                )
                current[action] = bool(checked)
                if db_path is None:
                    save_auto_exec_policy(current)
                else:
                    save_auto_exec_policy(current, db_path)
            except Exception as error:
                QMessageBox.warning(content, "保存失败", f"自动执行策略保存失败：{error}")
                return
            QMessageBox.information(content, "已保存", "学习助理自动执行策略已保存。")

        return toggle

    initial_auto_policy = load_auto_policy()
    for action in sorted(AUTO_ELIGIBLE_ACTIONS):
        box = QCheckBox(f"自动执行：{ACTION_LABELS.get(action, action)}")
        box.setObjectName(f"AssistantAutoExec_{action}")
        box.setChecked(bool(initial_auto_policy.get(action, False)))
        box.toggled.connect(make_auto_toggle(action))
        auto_exec_layout.addWidget(box)
    from study_app.ui.design_components import disclosure
    layout.addWidget(disclosure("自动执行策略（即时保存）", auto_exec_card))
    layout.addStretch()

    # ---- rendering helpers ----------------------------------------------------------
    def render_answer(answer: dict[str, list[str]]):
        parts = []
        if answer.get("answer"):
            parts.append("回答：")
            parts.extend(f"- {line}" for line in answer["answer"])
        if answer.get("evidence"):
            parts.append("\n数据依据：")
            parts.extend(f"- {line}" for line in answer["evidence"])
        if answer.get("source"):
            parts.append("\n来源：")
            parts.extend(f"- {line}" for line in answer["source"])
        result_body.setText("\n".join(parts))

    def set_chip(text: str):
        mode_chip.setText(text)

    def disclosure_lines(proposal) -> list[str]:
        data = proposal.external_data_disclosure or {}
        lines: list[str] = []
        if data.get("will_contact_external"):
            provider = data.get("provider") or "顾问"
            fields = "、".join(str(item) for item in (data.get("fields_sent") or []))
            suffix = f"：{fields}" if fields else ""
            lines.append(f"外发：最小派生文本({provider}){suffix}")
            if data.get("pdf_or_image_sent"):
                lines.append("⚠ 外发内容包含文件本体，请核对披露。")
        else:
            lines.append("外发：无")
        return lines

    def _mastery_line(item: dict) -> str | None:
        label = item.get("topic") or item.get("subject") or item.get("module")
        before = item.get("mastery_before", item.get("before"))
        after = item.get("mastery_after", item.get("after"))
        try:
            if before is not None and after is not None:
                return f"- 主题{label} 掌握度 {float(before):.2f}→{float(after):.2f}"
        except (TypeError, ValueError):
            return None
        return None

    def build_proposal_summary(bundle) -> str:
        proposal = bundle.proposal
        lines = [
            f"动作：{ACTION_LABELS.get(proposal.action_type, proposal.action_type)}",
            f"编号：{proposal.action_id}",
        ]
        if proposal.subject_key:
            lines.append(f"对象：{proposal.subject_key}")
        for change in proposal.proposed_changes or ():
            lines.append(
                f"- 变更 {change.get('op', '')} {change.get('entity', '')}：{change.get('summary', '')}"
            )
        mastery = bundle.mastery_preview or {}
        for item in mastery.get("matched_topics") or []:
            line = _mastery_line(item) if isinstance(item, dict) else None
            if line:
                lines.append(line)
        for item in mastery.get("subject_mastery") or []:
            line = _mastery_line(item) if isinstance(item, dict) else None
            if line:
                lines.append(line)
        if mastery.get("no_evidence"):
            lines.append("- 掌握度：未匹配到主题证据")
        for note in mastery.get("notes") or []:
            lines.append(f"- 掌握度备注：{note}")
        for key, value in sorted((bundle.plan_impact or {}).items()):
            lines.append(f"- 计划影响：{key}={value}")
        for warning in bundle.warnings or ():
            lines.append(f"⚠ {warning}")
        lines.extend(disclosure_lines(proposal))
        lines.append("确认执行前不会写入任何数据。")
        return "\n".join(lines)

    def render_proposal_card(bundle):
        proposal = bundle.proposal
        proposal_summary.setText(build_proposal_summary(bundle))
        confirm_button.setEnabled(bool(proposal.requires_confirmation))
        retry_button.setEnabled(False)
        proposal_card.setVisible(True)

    def render_receipt(receipt):
        status_text = str(getattr(receipt, "status", "")).lower()
        if status_text in {"succeeded", "completed", "done", "ok"}:
            prefix = "已完成"
        elif "fail" in status_text or status_text in {"failed", "error"}:
            prefix = "失败"
        else:
            prefix = str(getattr(receipt, "status", ""))
        lines = [f"{prefix}：{getattr(receipt, 'message', '')}"]
        for change in getattr(receipt, "mastery_changes", ()) or ():
            if isinstance(change, dict):
                line = _mastery_line(change)
                lines.append(line or f"- 掌握度变化：{change}")
            else:
                lines.append(f"- {change}")
        if getattr(receipt, "undo_token", None):
            lines.append("撤销：上一步可撤销（点击“撤销上一步”）。")
        else:
            lines.append("撤销：当前步骤不可撤销。")
        for warning in getattr(receipt, "warnings", ()) or ():
            lines.append(f"⚠ {warning}")
        receipt_group.toggle.setChecked(True)
        receipt_label.setText("\n".join(lines))

    # ---- async plumbing ---------------------------------------------------------------
    def register(handle, dispatch):
        for existing, _dispatch in pending:
            if existing is handle:
                return
        pending.append((handle, dispatch))
        if timer is not None:
            timer.start()

    def poll_result():
        if not lifecycle["open"]:
            return
        for entry in list(pending):
            handle, dispatch = entry
            snapshot = handle.snapshot()
            if not snapshot.finished:
                continue
            pending.remove(entry)
            try:
                dispatch(snapshot)
            except Exception:
                result_title.setText("回答")
        if not pending and timer is not None:
            timer.stop()

    def dispatch_llm(snapshot):
        if not lifecycle["open"]:
            return
        if snapshot.status in {TaskStatus.STALE, TaskStatus.CANCELLED}:
            return
        result_title.setText("回答")
        if snapshot.status == TaskStatus.SUCCEEDED:
            render_answer(snapshot.result)
        elif snapshot.status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
            local = lifecycle["local_answer"]
            if local is None:
                return
            answer = {key: list(value) for key, value in local.items()}
            reason = (
                "LLM 查询超时，已保留本地回答。"
                if snapshot.status == TaskStatus.TIMED_OUT
                else f"LLM 查询失败，已回落本地回答：{snapshot.error}"
            )
            answer.setdefault("source", []).append(reason)
            render_answer(answer)

    def intent_signature(text: str, paths: list[str]):
        return (text, tuple(paths))

    def start_intent(text: str, paths: list[str]):
        proposal_holder["text"] = text
        proposal_holder["paths"] = list(paths)
        if not engine_available():
            return

        def worker():
            engine = make_engine()
            bundle = engine.create_proposal(text, list(paths))
            auto = False
            proposal = bundle.proposal
            if not proposal.is_readonly:
                try:
                    from study_app.core.learning_assistant_policy import can_auto_execute

                    auto = bool(can_auto_execute(proposal.action_type))
                except Exception:
                    auto = False
            return bundle, auto

        handle = service.submit(
            ("assistant_intent",) + intent_signature(text, paths),
            worker,
            slot=ASSISTANT_PREVIEW_SLOT,
        )
        register(handle, dispatch_intent)

    def dispatch_intent(snapshot):
        if not lifecycle["open"]:
            return
        if snapshot.status in {TaskStatus.STALE, TaskStatus.CANCELLED}:
            return
        if snapshot.status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
            set_chip(CHIP_FAILED)
            retry_button.setEnabled(False)
            detail = snapshot.error if snapshot.error is not None else "助理任务超时"
            receipt_group.toggle.setChecked(True)
            receipt_label.setText(f"学习助理暂不可用：{detail}")
            return
        bundle, auto = snapshot.result
        proposal = bundle.proposal
        if proposal.is_readonly and proposal.action_type == "answer_query":
            set_chip(CHIP_READONLY)
            proposal_card.setVisible(False)
            proposal_holder["bundle"] = None
            proposal_holder["proposal"] = None
            proposal_holder["auto"] = False
            return
        proposal_holder["bundle"] = bundle
        proposal_holder["proposal"] = proposal
        proposal_holder["auto"] = bool(auto)
        render_proposal_card(bundle)
        if auto and not proposal.is_readonly:
            start_execution(
                proposal.action_id,
                bundle.confirmation_token,
                execution_mode="auto",
            )
        else:
            set_chip(CHIP_PENDING if proposal.requires_confirmation else CHIP_SUGGESTED)

    def start_execution(
        action_id: str,
        confirmation_token: str | None,
        *,
        execution_mode: str,
    ):
        confirm_button.setEnabled(False)
        set_chip(CHIP_RUNNING)

        def worker():
            engine = make_engine()
            return engine.confirm_and_execute(
                action_id,
                confirmation_token=confirmation_token,
                execution_mode=execution_mode,
            )

        handle = service.submit(
            ("assistant_execute", action_id),
            worker,
            slot=ASSISTANT_EXECUTE_SLOT,
        )
        register(handle, dispatch_execution)

    def dispatch_execution(snapshot):
        if not lifecycle["open"]:
            return
        if snapshot.status in {TaskStatus.CANCELLED, TaskStatus.STALE}:
            return
        if snapshot.status == TaskStatus.SUCCEEDED:
            set_chip(CHIP_DONE)
            render_receipt(snapshot.result)
            proposal_card.setVisible(False)
            proposal_holder["bundle"] = None
            proposal_holder["proposal"] = None
            proposal_holder["auto"] = False
            retry_button.setEnabled(False)
            refresh_alert_summary()
        else:
            set_chip(CHIP_FAILED)
            if snapshot.status == TaskStatus.TIMED_OUT:
                receipt_group.toggle.setChecked(True)
                receipt_label.setText("失败：执行超时。输入与附件已保留，可点击“重试”。")
            else:
                receipt_group.toggle.setChecked(True)
                receipt_label.setText(f"失败：{snapshot.error}。输入与附件已保留，可点击“重试”。")
            retry_button.setEnabled(True)
            confirm_button.setEnabled(True)

    def dispatch_retry(snapshot):
        if not lifecycle["open"]:
            return
        if snapshot.status in {TaskStatus.CANCELLED, TaskStatus.STALE}:
            return
        if snapshot.status == TaskStatus.SUCCEEDED:
            bundle = snapshot.result
            proposal_holder["bundle"] = bundle
            proposal_holder["proposal"] = bundle.proposal
            render_proposal_card(bundle)
            set_chip(CHIP_PENDING if bundle.proposal.requires_confirmation else CHIP_SUGGESTED)
        else:
            set_chip(CHIP_FAILED)
            receipt_group.toggle.setChecked(True)
            receipt_label.setText(f"重新生成提案失败：{snapshot.error}")
            retry_button.setEnabled(True)

    def confirm_execution():
        proposal = proposal_holder["proposal"]
        bundle = proposal_holder["bundle"]
        if proposal is None or bundle is None or not lifecycle["open"]:
            return
        start_execution(
            proposal.action_id,
            bundle.confirmation_token,
            execution_mode="user",
        )

    def cancel_proposal():
        if not lifecycle["open"]:
            return
        service.cancel(
            ("assistant_intent",)
            + intent_signature(proposal_holder["text"], proposal_holder["paths"])
        )
        proposal = proposal_holder["proposal"]
        if proposal is not None:
            service.cancel(("assistant_execute", proposal.action_id))
            service.cancel(("assistant_retry", proposal.action_id))
            if engine_available():
                action_id = proposal.action_id

                def worker():
                    engine = make_engine()
                    return engine.reject_proposal(action_id)

                try:
                    handle = service.submit(
                        ("assistant_reject", action_id),
                        worker,
                        slot=ASSISTANT_PREVIEW_SLOT,
                    )
                except RuntimeError:
                    handle = None
                if handle is not None:
                    register(handle, lambda _snapshot: None)
        actionable_visible = proposal is not None and not proposal.is_readonly
        set_chip(CHIP_SUGGESTED if actionable_visible else CHIP_READONLY)

    def request_repreview():
        if not lifecycle["open"]:
            return
        text = proposal_holder["text"]
        if not text:
            run_query()
            return
        start_intent(text, list(proposal_holder["paths"]))

    def request_retry():
        proposal = proposal_holder["proposal"]
        if proposal is None or not lifecycle["open"] or not engine_available():
            return
        action_id = proposal.action_id
        set_chip(CHIP_RUNNING)

        def worker():
            engine = make_engine()
            return engine.retry_proposal(action_id)

        handle = service.submit(
            ("assistant_retry", action_id),
            worker,
            slot=ASSISTANT_PREVIEW_SLOT,
        )
        register(handle, dispatch_retry)

    def request_undo():
        if not lifecycle["open"]:
            return

        def check_worker():
            engine = make_engine()
            return engine.last_undoable()

        try:
            handle = service.submit(
                ("assistant_undo_check",),
                check_worker,
                slot=ASSISTANT_UNDO_SLOT,
            )
        except RuntimeError:
            return
        register(handle, dispatch_undo_check)

    def dispatch_undo_check(snapshot):
        if not lifecycle["open"]:
            return
        if snapshot.status in {TaskStatus.CANCELLED, TaskStatus.STALE}:
            return
        if snapshot.status != TaskStatus.SUCCEEDED:
            undo_button.setEnabled(False)
            detail = snapshot.error if snapshot.error is not None else "任务超时"
            receipt_group.toggle.setChecked(True)
            receipt_label.setText(f"暂不能撤销：{detail}")
            return
        if snapshot.result is None:
            undo_button.setEnabled(False)
            receipt_group.toggle.setChecked(True)
            receipt_label.setText("当前没有可撤销的步骤。")
            return
        answer = QMessageBox.question(
            content,
            "撤销确认",
            "撤销将重放掌握度与预警且保留审计。确定要撤销上一步吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        set_chip(CHIP_RUNNING)

        def undo_worker():
            engine = make_engine()
            return engine.undo_last()

        handle = service.submit(
            ("assistant_undo",),
            undo_worker,
            slot=ASSISTANT_UNDO_SLOT,
        )
        register(handle, dispatch_undo)

    def dispatch_undo(snapshot):
        if not lifecycle["open"]:
            return
        if snapshot.status in {TaskStatus.CANCELLED, TaskStatus.STALE}:
            return
        if snapshot.status == TaskStatus.SUCCEEDED:
            set_chip(CHIP_DONE)
            render_receipt(snapshot.result)
            refresh_alert_summary()
        else:
            set_chip(CHIP_FAILED)
            detail = snapshot.error if snapshot.error is not None else "撤销超时"
            receipt_group.toggle.setChecked(True)
            receipt_label.setText(f"撤销失败：{detail}")

    def jump_to_alerts():
        if callable(alerts_jump):
            alerts_jump()

    # ---- dashboard refresh ------------------------------------------------------------
    def update_dashboard_state(new_state: DashboardState):
        state_holder["state"] = new_state
        service.invalidate(ASSISTANT_QUERY_SLOT)
        service.invalidate(ASSISTANT_PREVIEW_SLOT)
        lifecycle["handle"] = None
        result_title.setText("回答")
        refresh_status_strip()
        refresh_alert_summary()

    refresh_status_strip()
    refresh_alert_summary()

    timer = QTimer(content)
    timer.setInterval(20)
    timer.timeout.connect(poll_result)

    # ---- send flow (read-only Q&A semantics preserved) ---------------------------------
    def run_query():
        if not lifecycle["open"]:
            return
        question = question_input.toPlainText().strip()
        if not question:
            result_body.setText("请输入内容")
            question_input.setFocus()
            return
        if any(word in question for word in ('学科', '课程')) and any(word in question for word in ('引入', '添加', '新增', '导入', '删除', '移除', '恢复')):
            # A management request opens a local form only. Neither LLM output nor
            # the assistant auto-execution policy can approve a lifecycle change.
            service.invalidate(ASSISTANT_QUERY_SLOT)
            service.invalidate(ASSISTANT_PREVIEW_SLOT)
            proposal_card.setVisible(False)
            set_chip(CHIP_READONLY)
            result_body.setText('请在学科管理窗口明确选择目标并预览影响。此请求本身不会更改学科，也不会自动使用附件执行。')
            open_subject_management()
            return
        attachments = list(attachment_holder["paths"])
        existing = lifecycle["handle"]
        if (
            existing is not None
            and not existing.snapshot().finished
            and lifecycle["question"] == question
        ):
            return
        if existing is not None:
            service.invalidate(ASSISTANT_QUERY_SLOT)
            lifecycle["handle"] = None
        service.invalidate(ASSISTANT_PREVIEW_SLOT)
        current_state = state_holder["state"]
        records = list(getattr(current_state, "raw_records", ()) or [])
        local_answer = natural_query.answer_query_locally(question, current_state, records)
        # The confirmed day plan is authoritative for today's next action.
        # Snapshot recommendations must not send the user back to completed work.
        plan_answer = None
        if any(word in question for word in ("今天", "今日", "先学", "下一项", "分钟", "计划")):
            from study_app.ui.design_components import current_plan_answer
            plan_answer = current_plan_answer(current_state, db_path=db_path)
            if plan_answer is not None:
                local_answer = plan_answer
        lifecycle["question"] = question
        lifecycle["local_answer"] = local_answer
        render_answer(local_answer)
        set_chip(CHIP_READONLY)
        proposal_card.setVisible(False)
        if plan_answer is None and providers.is_llm_feature_enabled("natural_query"):
            result_title.setText("回答（LLM 查询中…）")
            handle = service.submit(
                ("natural_query", question),
                lambda: natural_query.answer_query_with_llm(question, current_state, records),
                slot=ASSISTANT_QUERY_SLOT,
                timeout_seconds=QUERY_TASK_TIMEOUT_SECONDS,
            )
            lifecycle["handle"] = handle
            register(handle, dispatch_llm)
        start_intent(question, attachments)

    attach_button.clicked.connect(choose_attachments)
    clear_button.clicked.connect(clear_attachments)
    ask_button.clicked.connect(run_query)
    confirm_button.clicked.connect(confirm_execution)
    cancel_button.clicked.connect(cancel_proposal)
    repreview_button.clicked.connect(request_repreview)
    retry_button.clicked.connect(request_retry)
    undo_button.clicked.connect(request_undo)
    if callable(alerts_jump):
        alerts_button.clicked.connect(jump_to_alerts)

    content._query_handler = run_query
    content._query_task_service = service
    content._query_lifecycle = lifecycle
    scroll._set_dashboard_state = update_dashboard_state
    from study_app.ui.design_components import readable_page
    readable_page(scroll, content)
    scroll.setWidget(content)
    return scroll
