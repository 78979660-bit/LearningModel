from __future__ import annotations

from study_app.ai.providers import (
    LLMSettings,
    LLM_FEATURES,
    PROVIDERS,
    load_llm_settings,
    provider_status,
    save_llm_settings,
)
from study_app.data.backup import BACKUP_ROOT, create_backup, integrity_check
from study_app.data.database import list_llm_call_audits


def settings_page():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget
    from study_app.core.async_tasks import AsyncTaskService, TaskStatus

    service = AsyncTaskService(max_workers=1)
    job = {"handle": None, "kind": None, "open": True}

    def close_tasks():
        if not job["open"]:
            return
        job["open"] = False
        timer.stop()
        service.close()

    class SettingsScrollArea(QScrollArea):
        def closeEvent(self, event):
            close_tasks()
            super().closeEvent(event)

    scroll = SettingsScrollArea()
    timer = QTimer(scroll)
    timer.setInterval(50)
    scroll.destroyed.connect(close_tasks)
    scroll._maintenance_job = job
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(30, 28, 30, 30)
    layout.setSpacing(18)

    heading = QLabel("设置")
    heading.setObjectName("HeroTitle")
    layout.addWidget(heading)

    backup_card = QWidget()
    backup_card.setObjectName("Card")
    card_layout = QVBoxLayout(backup_card)
    card_layout.setContentsMargins(20, 18, 20, 18)
    card_layout.setSpacing(12)
    title = QLabel("数据备份")
    title.setObjectName("CardTitle")
    status = QLabel(f"备份目录：{BACKUP_ROOT}")
    status.setObjectName("StatusLabel")
    status.setWordWrap(True)

    actions = QHBoxLayout()
    backup_button = QPushButton("立即备份")
    backup_button.setObjectName("PrimaryButton")
    check_button = QPushButton("检查数据库")
    check_button.setObjectName("GhostButton")
    open_button = QPushButton("打开备份目录")
    open_button.setObjectName("GhostButton")
    actions.addWidget(backup_button)
    actions.addWidget(check_button)
    actions.addWidget(open_button)
    actions.addStretch()

    detail_button = QPushButton("展开检查详情")
    detail_button.setObjectName("GhostButton")
    detail_button.setCheckable(True)
    detail_button.hide()
    detail_label = QLabel("")
    detail_label.setWordWrap(True)
    detail_label.setObjectName("Muted")
    detail_label.hide()
    detail_button.toggled.connect(detail_label.setVisible)
    detail_button.toggled.connect(lambda checked: detail_button.setText("收起检查详情" if checked else "展开检查详情"))

    def start_maintenance(kind, work):
        if job["handle"] is not None or not job["open"]:
            return
        backup_button.clearFocus()
        check_button.clearFocus()
        backup_button.setEnabled(False)
        check_button.setEnabled(False)
        detail_button.setChecked(False)
        detail_button.hide()
        status.setText("正在检查数据…" if kind == "check" else "正在备份…")
        job["kind"] = kind
        job["handle"] = service.submit(kind, work)
        timer.start()

    def poll_maintenance():
        if not job["open"] or job["handle"] is None:
            return
        snapshot = job["handle"].snapshot()
        if not snapshot.finished:
            return
        kind = job["kind"]
        job["handle"] = None
        timer.stop()
        backup_button.setEnabled(True)
        check_button.setEnabled(True)
        if snapshot.status != TaskStatus.SUCCEEDED:
            title = "检查失败" if kind == "check" else "备份失败"
            status.setText(title + "，请重试。")
            QMessageBox.critical(backup_card, title, str(snapshot.error or "任务已取消"))
            return
        result = snapshot.result
        if kind == "check":
            result = str(result)
            summary = result.split("：", 1)[0] if len(result) > 160 else result
            status.setText(f"数据库检查结果：{summary}")
            detail_label.setText(result)
            detail_button.setVisible(len(result) > 160)
        else:
            status.setText(f"已创建备份：{result.archive if result.archive.exists() else result.folder}")
            QMessageBox.information(backup_card, "备份完成", f"备份已保存到：\n{result.folder}")

    timer.timeout.connect(poll_maintenance)

    def run_backup():
        start_maintenance("backup", create_backup)

    def run_check():
        start_maintenance("check", integrity_check)

    def open_backup_folder():
        import os

        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        os.startfile(BACKUP_ROOT)

    backup_button.clicked.connect(run_backup)
    check_button.clicked.connect(run_check)
    open_button.clicked.connect(open_backup_folder)

    card_layout.addWidget(title)
    card_layout.addLayout(actions)
    card_layout.addWidget(status)
    card_layout.addWidget(detail_button)
    card_layout.addWidget(detail_label)
    layout.addWidget(backup_card)

    llm_card = QWidget()
    llm_card.setObjectName("Card")
    llm_layout = QVBoxLayout(llm_card)
    llm_layout.setContentsMargins(20, 18, 20, 18)
    llm_layout.setSpacing(12)
    llm_title = QLabel("LLM 增强解析")
    llm_title.setObjectName("CardTitle")

    settings = load_llm_settings()
    enabled_input = QCheckBox("启用云端 LLM 增强")
    enabled_input.setChecked(settings.enabled)
    provider_input = QComboBox()
    for key, meta in PROVIDERS.items():
        provider_input.addItem(meta["label"], key)
    provider_input.setCurrentIndex(max(0, provider_input.findData(settings.provider)))
    model_input = QLineEdit(settings.model)
    model_input.setPlaceholderText("例如 deepseek-chat")
    base_url_input = QLineEdit(settings.custom_base_url)
    base_url_input.setPlaceholderText("例如 https://api.lmuai.com")
    api_key_input = QLineEdit(settings.api_key)
    api_key_input.setPlaceholderText("API Key 仅保存在本机 SQLite")
    api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    token_limit_input = QLineEdit(str(settings.single_call_token_limit))
    budget_input = QLineEdit(str(settings.daily_budget_cny))
    upload_images_input = QCheckBox("允许上传图片")
    upload_images_input.setChecked(settings.allow_upload_images)
    upload_pdfs_input = QCheckBox("允许上传 PDF")
    upload_pdfs_input.setChecked(settings.allow_upload_pdfs)
    feature_inputs = {}
    for key, label in LLM_FEATURES.items():
        checkbox = QCheckBox(label)
        checkbox.setChecked(key in settings.enabled_features)
        feature_inputs[key] = checkbox

    record_subfeatures = ("error_classifier", "knowledge_mapping", "difficulty_calibration")

    def sync_record_subfeature_availability():
        parser_selected = (
            feature_inputs["record_parser"].isChecked()
            or feature_inputs["attachment_parser"].isChecked()
        )
        for key in record_subfeatures:
            feature_inputs[key].setEnabled(parser_selected)

    feature_inputs["record_parser"].toggled.connect(sync_record_subfeature_availability)
    feature_inputs["attachment_parser"].toggled.connect(sync_record_subfeature_availability)
    sync_record_subfeature_availability()
    llm_status = QLabel(provider_status(settings))
    llm_status.setObjectName("StatusLabel")
    llm_status.setWordWrap(True)

    llm_form = QVBoxLayout()
    llm_form.setSpacing(8)
    llm_form.addWidget(enabled_input)
    llm_form.addWidget(QLabel("后端"))
    llm_form.addWidget(provider_input)
    llm_form.addWidget(QLabel("API Base URL（自定义/Anthropic 兼容接口）"))
    llm_form.addWidget(base_url_input)
    llm_form.addWidget(QLabel("模型"))
    llm_form.addWidget(model_input)
    llm_form.addWidget(QLabel("API Key"))
    llm_form.addWidget(api_key_input)
    llm_form.addWidget(QLabel("单次 token 上限"))
    llm_form.addWidget(token_limit_input)
    llm_form.addWidget(QLabel("每日预算上限（元）"))
    llm_form.addWidget(budget_input)
    llm_form.addWidget(upload_images_input)
    llm_form.addWidget(upload_pdfs_input)
    llm_form.addWidget(QLabel("允许使用 LLM 增强的功能"))
    for key, checkbox in feature_inputs.items():
        if key == "error_classifier":
            feature_note = QLabel(
                "以下三项仅控制云端记录/附件解析。关闭后，本地规则仍可能补齐错因类别和难度。"
            )
            feature_note.setObjectName("Muted")
            feature_note.setWordWrap(True)
            llm_form.addWidget(feature_note)
        llm_form.addWidget(checkbox)

    llm_actions = QHBoxLayout()
    save_llm_button = QPushButton("保存 LLM 设置")
    save_llm_button.setObjectName("PrimaryButton")
    local_mode_button = QPushButton("切回仅本地")
    local_mode_button.setObjectName("GhostButton")
    llm_actions.addWidget(save_llm_button)
    llm_actions.addWidget(local_mode_button)
    llm_actions.addStretch()

    def sync_model_placeholder():
        provider = provider_input.currentData()
        if not model_input.text().strip():
            model_input.setPlaceholderText(PROVIDERS[provider]["default_model"] or "仅本地无需模型")

    def save_llm_config():
        try:
            token_limit = int(float(token_limit_input.text().strip() or "8000"))
            budget = float(budget_input.text().strip() or "0")
        except ValueError:
            QMessageBox.warning(llm_card, "格式错误", "token 上限和预算需要填写数字。")
            return
        provider = provider_input.currentData()
        payload = LLMSettings(
            enabled=enabled_input.isChecked(),
            provider=provider,
            model=model_input.text().strip() or PROVIDERS[provider]["default_model"],
            custom_base_url=base_url_input.text().strip(),
            api_key=api_key_input.text().strip(),
            single_call_token_limit=token_limit,
            daily_budget_cny=budget,
            allow_upload_images=upload_images_input.isChecked(),
            allow_upload_pdfs=upload_pdfs_input.isChecked(),
            enabled_features=tuple(
                key for key, checkbox in feature_inputs.items() if checkbox.isChecked()
            ),
        )
        save_llm_settings(payload)
        llm_status.setText(provider_status(payload))
        QMessageBox.information(llm_card, "已保存", "LLM 设置已保存到本机。")

    def switch_local_mode():
        provider_input.setCurrentIndex(provider_input.findData("local"))
        enabled_input.setChecked(False)
        model_input.clear()
        base_url_input.clear()
        api_key_input.clear()
        upload_images_input.setChecked(False)
        upload_pdfs_input.setChecked(False)
        for checkbox in feature_inputs.values():
            checkbox.setChecked(True)
        save_llm_config()

    provider_input.currentIndexChanged.connect(sync_model_placeholder)
    save_llm_button.clicked.connect(save_llm_config)
    local_mode_button.clicked.connect(switch_local_mode)

    llm_layout.addWidget(llm_title)
    llm_layout.addLayout(llm_form)
    llm_layout.addLayout(llm_actions)
    llm_layout.addWidget(llm_status)
    layout.addWidget(llm_card)

    assistant_card = QWidget()
    assistant_card.setObjectName("Card")
    assistant_layout = QVBoxLayout(assistant_card)
    assistant_layout.setContentsMargins(20, 18, 20, 18)
    assistant_layout.setSpacing(12)
    assistant_title = QLabel("学习助理策略")
    assistant_title.setObjectName("CardTitle")
    assistant_layout.addWidget(assistant_title)

    advisor_entries = (
        ("未配置（作业提交将阻塞）", "unset"),
        ("模拟顾问（仅供测试）", "mock"),
        ("外部顾问（DeepSeek，最小外发）", "external"),
    )
    advisor_combo = QComboBox()
    advisor_combo.setObjectName("AssistantAdvisorModeCombo")
    for label, mode in advisor_entries:
        advisor_combo.addItem(label, mode)
    try:
        from study_app.core.learning_assistant_policy import load_privacy_settings

        privacy = load_privacy_settings()
    except Exception:
        privacy = {"advisor_mode": "unset", "allow_external_intent": False}
    advisor_combo.setCurrentIndex(max(0, advisor_combo.findData(privacy.get("advisor_mode", "unset"))))
    advisor_status = QLabel("")
    advisor_status.setObjectName("AssistantAdvisorStatusLabel")
    advisor_status.setWordWrap(True)

    def refresh_advisor_status(saved: bool = False):
        mode = advisor_combo.currentData()
        texts = {mode: label for label, mode in advisor_entries}
        prefix = "已保存。" if saved else "当前"
        advisor_status.setText(f"{prefix}顾问模式：{texts.get(mode, mode)}")

    def on_advisor_mode_changed(_index: int):
        mode = advisor_combo.currentData()
        try:
            from study_app.core.learning_assistant_policy import (
                load_privacy_settings,
                save_privacy_settings,
            )

            current = load_privacy_settings()
            save_privacy_settings(
                {
                    "advisor_mode": mode,
                    "allow_external_intent": bool(current.get("allow_external_intent", False)),
                }
            )
        except Exception as error:
            advisor_status.setText(f"顾问模式保存失败：{error}")
            return
        refresh_advisor_status(saved=True)

    advisor_mode_label = QLabel("顾问模式")
    assistant_layout.addWidget(advisor_mode_label)
    assistant_layout.addWidget(advisor_combo)
    refresh_advisor_status()
    advisor_combo.currentIndexChanged.connect(on_advisor_mode_changed)
    assistant_layout.addWidget(advisor_status)

    try:
        from study_app.core.learning_assistant_policy import load_auto_exec_policy

        auto_policy = load_auto_exec_policy()
    except Exception:
        auto_policy = {}

    def make_auto_exec_toggle(action: str):
        def toggle(checked: bool):
            try:
                from study_app.core.learning_assistant_policy import (
                    load_auto_exec_policy,
                    save_auto_exec_policy,
                )

                current = load_auto_exec_policy()
                current[action] = bool(checked)
                save_auto_exec_policy(current)
            except Exception as error:
                QMessageBox.warning(assistant_card, "保存失败", f"自动执行策略保存失败：{error}")

        return toggle

    from study_app.core.learning_assistant_schema import (
        ACTION_LABELS as ASSISTANT_ACTION_LABELS,
        AUTO_ELIGIBLE_ACTIONS as ASSISTANT_AUTO_ELIGIBLE_ACTIONS,
    )

    for action in sorted(ASSISTANT_AUTO_ELIGIBLE_ACTIONS):
        checkbox = QCheckBox(f"自动执行：{ASSISTANT_ACTION_LABELS.get(action, action)}")
        checkbox.setObjectName(f"SettingsAutoExec_{action}")
        checkbox.setChecked(bool(auto_policy.get(action, False)))
        checkbox.toggled.connect(make_auto_exec_toggle(action))
        assistant_layout.addWidget(checkbox)

    assistant_title.setToolTip("附件不上传；外部顾问仅接收题目文本。")
    layout.addWidget(assistant_card)

    audit_card = QWidget()
    audit_card.setObjectName("Card")
    audit_layout = QVBoxLayout(audit_card)
    audit_layout.setContentsMargins(20, 18, 20, 18)
    audit_layout.setSpacing(10)
    audit_title = QLabel("LLM 调用审计")
    audit_title.setObjectName("CardTitle")
    audit_layout.addWidget(audit_title)
    audits = list_llm_call_audits(limit=8)
    if not audits:
        empty = QLabel("暂无 LLM 调用记录。")
        empty.setObjectName("Muted")
        audit_layout.addWidget(empty)
    for item in audits:
        summary = item.get("upload_summary") or {}
        summary_text = "、".join(
            f"{key}:{value.get('type', '?')}"
            for key, value in list(summary.items())[:6]
            if isinstance(value, dict)
        )
        line = (
            f"{item['created_at']} | {item['feature']} | {item['provider']} / {item['model']} | "
            f"{item['status']} | ~{item['estimated_tokens']} tokens"
        )
        label = QLabel(line)
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        audit_layout.addWidget(label)
        detail = QLabel(
            (f"上传字段：{summary_text or '无'}" if item["status"] == "success" else f"失败原因：{item.get('error_message') or '未知'}")
        )
        detail.setObjectName("Muted" if item["status"] == "success" else "WarningText")
        detail.setWordWrap(True)
        audit_layout.addWidget(detail)
    layout.addWidget(audit_card)

    from study_app.ui.design_components import disclosure, readable_page
    for section in (backup_card, llm_card, assistant_card, audit_card):
        layout.removeWidget(section)
    layout.addWidget(disclosure("学习偏好与助理策略（即时保存）", assistant_card))
    layout.addWidget(disclosure("智能服务（修改后保存）", llm_card))
    layout.addWidget(disclosure("数据管理与备份", backup_card))
    layout.addWidget(disclosure("查看调用记录", audit_card))
    readable_page(scroll, content)
    layout.addStretch()
    scroll.setWidget(content)
    return scroll
