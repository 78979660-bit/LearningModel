"""Explicit local-user gate for subject lifecycle changes."""
from __future__ import annotations


def subject_management_dialog(parent=None, *, db_path=None):
    import json
    from pathlib import Path
    from PySide6.QtCore import QThread, Signal, Qt
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                                  QComboBox, QLineEdit, QPlainTextEdit, QCheckBox, QFileDialog)
    from study_app.data.database import DEFAULT_DB_PATH
    from study_app.core.subject_management import LABELS, list_subjects, prepare_management, execute_management

    database = Path(db_path) if db_path is not None else DEFAULT_DB_PATH

    class Dialog(QDialog):
        changed = False
        busy = False

        def reject(self):
            if not self.busy:
                super().reject()

        def closeEvent(self, event):
            if self.busy:
                event.ignore()
            else:
                super().closeEvent(event)

    class Worker(QThread):
        outcome = Signal(object)

        def run(self):
            try:
                result = execute_management(database, self.operation_id,
                    confirmation_name=self.confirmation_name, acknowledged=True)
                self.outcome.emit({'result': result})
            except Exception as error:
                self.outcome.emit({'error': str(error)})

    dialog = Dialog(parent)
    dialog.setWindowTitle('学科管理 · 引入、移除与恢复')
    dialog.resize(680, 710)
    layout = QVBoxLayout(dialog)
    title = QLabel('学科管理')
    title.setObjectName('CardTitle')
    layout.addWidget(title)
    mode = QComboBox()
    mode.setObjectName('SubjectManagementMode')
    for key, label in LABELS.items():
        mode.addItem(label, key)
    layout.addWidget(mode)
    target = QComboBox()
    target.setObjectName('SubjectManagementTarget')
    layout.addWidget(target)
    name = QLineEdit()
    name.setObjectName('SubjectManagementName')
    name.setPlaceholderText('新学科的完整名称')
    name.setMaxLength(200)
    layout.addWidget(name)
    outline = QPlainTextEdit()
    outline.setObjectName('SubjectManagementOutline')
    outline.setPlaceholderText('每行：章节 / 知识点\n例如：概率基础 / 条件概率')
    outline.setMaximumHeight(140)
    layout.addWidget(outline)
    import_button = QPushButton('从本地目录文件填入…')
    import_button.setToolTip('UTF-8 TXT/Markdown，每行“章节 / 知识点”；也支持 name、modules 字段的 JSON。文件只填入草稿，不会执行。')
    layout.addWidget(import_button)
    preview_button = QPushButton('预览变更')
    preview_button.setObjectName('SubjectManagementPreview')
    layout.addWidget(preview_button)
    preview = QPlainTextEdit()
    preview.setObjectName('SubjectManagementImpact')
    preview.setReadOnly(True)
    preview.setPlaceholderText('变更预览')
    layout.addWidget(preview, 1)
    acknowledgement = QCheckBox('我已核对目标与影响范围')
    acknowledgement.setObjectName('SubjectManagementAcknowledgement')
    layout.addWidget(acknowledgement)
    confirmation = QLineEdit()
    confirmation.setObjectName('SubjectManagementConfirmation')
    confirmation.setPlaceholderText('输入学科全名以确认')
    layout.addWidget(confirmation)
    status = QLabel('尚未执行任何变更')
    status.setTextFormat(Qt.TextFormat.PlainText)
    status.setWordWrap(True)
    status.setObjectName('SubjectManagementStatus')
    layout.addWidget(status)
    buttons = QHBoxLayout()
    execute = QPushButton('确认执行')
    execute.setObjectName('SubjectManagementExecute')
    execute.setEnabled(False)
    close = QPushButton('关闭')
    close.clicked.connect(dialog.reject)
    buttons.addWidget(execute)
    buttons.addWidget(close)
    layout.addLayout(buttons)
    holder = {'prepared': None}

    def update_confirmation(*_):
        prepared = holder['prepared']
        execute.setEnabled(not dialog.busy and prepared is not None and acknowledgement.isChecked()
                           and confirmation.text() == prepared.payload['actions'][0]['name'])

    def invalidate(*_):
        holder['prepared'] = None
        acknowledgement.setChecked(False)
        confirmation.clear()
        preview.clear()
        execute.setEnabled(False)
        status.setText('草稿已变更，请重新预览')

    def load_targets(*_):
        invalidate()
        introducing = mode.currentData() == 'introduce'
        for widget in (name, outline, import_button):
            widget.setVisible(introducing)
        target.setVisible(not introducing)
        target.clear()
        try:
            expected = 'active' if mode.currentData() == 'remove' else 'archived'
            for row in list_subjects(database):
                if row['lifecycle_status'] == expected:
                    target.addItem(row['canonical_name'], row['subject_key'])
            preview_button.setEnabled(introducing or target.count() > 0)
            status.setText('' if introducing else '' if target.count() else '没有可操作的学科。')
        except Exception as error:
            preview_button.setEnabled(False)
            status.setText(f'目录暂不可用，已禁止写入：{error}')

    def import_outline():
        path, _ = QFileDialog.getOpenFileName(dialog, '选择已核对的学科目录', '', '目录文件 (*.txt *.md *.json)')
        if not path:
            return
        try:
            with Path(path).open('rb') as source:
                raw = source.read(300_001)
            if len(raw) > 300_000:
                raise ValueError('目录文件不能超过 300 KB')
            text = raw.decode('utf-8-sig')
            new_name = None
            if Path(path).suffix.lower() == '.json':
                data = json.loads(text)
                if not isinstance(data, dict) or set(data) != {'name', 'modules'} or not isinstance(data['name'], str) or not isinstance(data['modules'], list):
                    raise ValueError('JSON 仅接受 name 与 modules；modules 中每项为 name 与字符串数组 topics')
                rows = []
                for module in data['modules']:
                    if not isinstance(module, dict) or set(module) != {'name', 'topics'} or not isinstance(module['name'], str) or not isinstance(module['topics'], list) or not all(isinstance(t, str) for t in module['topics']):
                        raise ValueError('目录章节格式无效')
                    rows.extend(module['name'] + ' / ' + topic for topic in module['topics'])
                text, new_name = '\n'.join(rows), data['name']
            from study_app.core.subject_management import parse_outline
            parse_outline(text)
            if new_name is not None:
                name.setText(new_name)
            outline.setPlainText(text)
            status.setText('目录已载入，尚未保存')
        except Exception as error:
            status.setText(f'未读取目录：{error}')

    def prepare():
        invalidate()
        try:
            prepared = prepare_management(database, mode.currentData(), name=name.text(),
                                           outline=outline.toPlainText(), subject_key=target.currentData())
            holder['prepared'] = prepared
            action = prepared.payload['actions'][0]
            impact = action['impact']
            lines = [f"操作：{LABELS[action['kind']]}", f"学科：{action['name']}"]
            if action['kind'] == 'introduce':
                lines += ['将创建独立学科与以下目录；不生成学习成绩，不覆盖已有学科。',
                          '启用基础学习计划与通用练习；未配置的出卷、OJ 和外部采集能力保持关闭。']
                lines.extend(module['name'] + '：' + '、'.join(module['topics']) for module in action['modules'])
            elif action['kind'] == 'remove':
                lines += [f"保留 {impact['records']} 条学习记录及全部知识点。",
                          f"归档 {len(impact['plan_ids'])} 份活动计划（含关联的跨学科共享计划）；这些计划需重新安排。",
                          '计划编号：' + (', '.join(map(str, impact['plan_ids'])) or '无'),
                          '退出活动学科、推荐与后续任务；可通过“恢复学科”重新启用。']
            else:
                lines += [f"重新启用学科，保留的 {impact['records']} 条学习记录继续可用。", '旧计划保持归档，请重新制定计划，避免恢复过期任务。']
            lines += ['执行前必须成功创建并校验数据库备份；任一步失败不提交本次变更。',
                      '预览后目录、关联记录或计划变化时，本次确认会失效。']
            preview.setPlainText('\n\n'.join(lines))
            status.setText('勾选确认并输入学科全名')
        except Exception as error:
            status.setText(f'无法预览：{error}')

    def finish(outcome):
        dialog.busy = False
        for widget in (mode, target, name, outline, import_button, preview_button, acknowledgement, confirmation, close):
            widget.setEnabled(True)
        holder['prepared'] = None
        acknowledgement.setChecked(False)
        confirmation.clear()
        execute.setEnabled(False)
        if 'error' in outcome:
            status.setText('未完成：' + outcome['error'] + '。请重新预览后重试。')
        else:
            dialog.changed = True
            result = outcome['result']
            load_targets()
            preview.setPlainText(f"{LABELS[result['kind']]}：{result['name']}\n\n已保存。备份：{result['backup_path']}\n操作编号：{result['operation_id']}")
            status.setText(result.get('warning') or '已保存')

    def commit():
        if not execute.isEnabled() or holder['prepared'] is None:
            return
        dialog.busy = True
        execute.setEnabled(False)
        for widget in (mode, target, name, outline, import_button, preview_button, acknowledgement, confirmation, close):
            widget.setEnabled(False)
        status.setText('正在备份并保存…')
        worker = Worker(dialog)
        worker.operation_id = holder['prepared'].operation_id
        worker.confirmation_name = confirmation.text()
        worker.outcome.connect(lambda result: setattr(worker, 'result', result))
        worker.finished.connect(lambda: finish(worker.result))
        worker.finished.connect(worker.deleteLater)
        dialog.worker = worker
        worker.start()

    mode.currentIndexChanged.connect(load_targets)
    target.currentIndexChanged.connect(invalidate)
    name.textChanged.connect(invalidate)
    outline.textChanged.connect(invalidate)
    acknowledgement.toggled.connect(update_confirmation)
    confirmation.textChanged.connect(update_confirmation)
    preview_button.clicked.connect(prepare)
    import_button.clicked.connect(import_outline)
    execute.clicked.connect(commit)
    load_targets()
    return dialog
