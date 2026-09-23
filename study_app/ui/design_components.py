"""Shared presentation helpers; importing this module does not load Qt."""


def disclosure(title, body, *, expanded=False):
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
    class Disclosure(QWidget):
        def closeEvent(self, event):
            body.close()
            super().closeEvent(event)
    host = Disclosure()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    toggle = QPushButton()
    toggle.setObjectName('DisclosureButton')
    toggle.setCheckable(True)
    toggle.setChecked(expanded)
    def apply(checked):
        body.setVisible(checked)
        toggle.setText(f'{title} · {"收起" if checked else "展开"}')
        toggle.setAccessibleName(toggle.text())
    toggle.toggled.connect(apply)
    apply(expanded)
    layout.addWidget(toggle)
    layout.addWidget(body)
    host.toggle = toggle
    host.body = body
    return host


def readable_page(scroll, content):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSizePolicy
    content.setMaximumWidth(1180)
    content.setMinimumWidth(0)
    content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)


def install_chinese_dialogs(app):
    """Translate standard Qt buttons even when the Qt language pack is absent."""
    from PySide6.QtCore import QTranslator
    class DialogTranslator(QTranslator):
        def translate(self, context, sourceText, disambiguation=None, n=-1):
            if context not in {"QPlatformTheme", "QDialogButtonBox", "QMessageBox"}:
                return ""
            return {"OK": "确定", "&OK": "确定", "Cancel": "取消", "&Cancel": "取消",
                    "Yes": "是", "&Yes": "是", "No": "否", "&No": "否",
                    "Close": "关闭", "&Close": "关闭", "Save": "保存", "&Save": "保存",
                    "Retry": "重试", "&Retry": "重试", "Open": "打开", "&Open": "打开"}.get(sourceText, "")
    if not hasattr(app, '_dialog_translator'):
        app._dialog_translator = DialogTranslator(app)
        app.installTranslator(app._dialog_translator)


def today_plan_card(state, open_plan):
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
    from study_app.data.database import get_budgeted_day_plan, get_active_study_plan

    card = QWidget()
    card.setObjectName('Card')
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    title = QLabel('今日安排')
    title.setObjectName('CardTitle')
    summary = QLabel()
    summary.setObjectName('TodayPlanSummary')
    summary.setWordWrap(True)
    next_task = QLabel()
    next_task.setObjectName('TodayNextTask')
    next_task.setWordWrap(True)
    action = QPushButton()
    action.setObjectName('PrimaryButton')
    selected = [None]
    action.clicked.connect(lambda: open_plan(selected[0]))
    for widget in (title, summary, next_task, action):
        layout.addWidget(widget)

    def refresh(new_state=None):
        nonlocal state
        if new_state is not None:
            state = new_state
        selected[0] = None
        try:
            plan = get_budgeted_day_plan(state.today.isoformat())
            if plan:
                rows = [r for r in plan['items'] if not r.get('excluded_reason') or r.get('checked')]
                pending = [r for r in rows if not r.get('checked')]
                total = plan['summary']['planned_minutes']
                remaining = plan['summary']['remaining_minutes']
                summary.setText(f"{plan['plan_date']} · 已完成 {len(rows)-len(pending)}/{len(rows)} 项 · "
                                f"预计共 {total} 分钟 · 预算 {plan['budget_minutes']} 分钟 · 余量 {remaining} 分钟")
                if plan['summary'].get('over_budget_completed_minutes'):
                    summary.setText(summary.text() + f" · 已完成用时超预算 {plan['summary']['over_budget_completed_minutes']} 分钟")
                if plan['summary'].get('completed_occupancy_unknown'):
                    summary.setText(summary.text() + ' · 部分完成任务估时未知，请补充估时')
                if pending:
                    selected[0] = pending[0]['id']
                    next_task.setText('下一项：' + pending[0]['item_text'])
                    action.setText('打开下一项任务')
                elif rows:
                    next_task.setText('今日任务已完成')
                    action.setText('查看今日计划')
                else:
                    next_task.setText('当前预算内暂无任务')
                    action.setText('调整时间与任务')
            else:
                legacy = get_active_study_plan(None)
                if legacy:
                    summary.setText('已有计划 · 时长未知 · 今日预算未确认')
                    next_task.setText('')
                    action.setText('查看与调整计划')
                else:
                    summary.setText('暂无今日计划')
                    next_task.setText('')
                    action.setText('制定今日计划')
        except Exception:
            summary.setText('今日计划暂时无法读取')
            next_task.setText('请重试')
            action.setText('打开学习计划')
        next_task.setVisible(bool(next_task.text()))
    card.refresh = refresh
    refresh()
    return card


def current_plan_answer(state, *, db_path=None):
    """Read the same canonical plan used by the overview, without creating one."""
    from study_app.data.database import get_budgeted_day_plan, DatabaseNotInitializedError
    if not getattr(state, 'today', None):
        return None
    try:
        options = {} if db_path is None else {'db_path': db_path}
        plan = get_budgeted_day_plan(state.today.isoformat(), **options)
    except DatabaseNotInitializedError:
        return None
    except Exception:
        return {'answer': ['暂时无法读取已确认计划，请到学习计划页重试。'],
                'evidence': ['本次未能读取当日计划。'], 'source': ['本地计划查询；没有修改数据。']}
    if not plan:
        return None
    pending = [row for row in plan['items'] if not row.get('checked') and not row.get('excluded_reason')]
    answer = [f"今日已确认预算 {plan['budget_minutes']} 分钟，安排共 {plan['summary']['planned_minutes']} 分钟。"]
    if pending:
        answer.append('下一项：' + pending[0]['item_text'])
        answer.extend(f"待执行：{row['item_text']} · {row['estimated_minutes']} 分钟" for row in pending[1:3])
    else:
        answer.append('当前已安排任务均已完成，或预算内没有可执行任务。可到学习计划页调整时间并重新预览。')
    answer.append('如需改变可用时间或任务顺序，请在学习计划页预览并确认；这次回答不会更改安排。')
    return {'answer': answer, 'evidence': [f"依据：{plan['plan_date']} 已确认的时间预算计划。"],
            'source': ['生成方式：本地计划查询（与今日概览共用状态）。']}
