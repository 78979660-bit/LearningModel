from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def test_summary_counts_all_items_while_payload_examples_remain_bounded():
    from study_app.ai.daily_summary import build_daily_summary_payload, local_daily_summary
    subject = SimpleNamespace(name='数学', archived=False, window_score=None,
                              covered_mastery_score=34, covered_topic_count=41,
                              total_topic_count=84, mastery_score=10, warnings=())
    state = SimpleNamespace(today=date(2026, 9, 22), start=date(2026, 9, 20), benchmark=55,
                            subjects=(subject,), todos=(), low_subjects=(), stale_subjects=(),
                            memory_risks=tuple({'subject':'数学', 'topic':str(i), 'recall':0.5} for i in range(22)),
                            bkt_alerts=())
    records = [{'subject':'数学','date':'2026-09-22'} for _ in range(15)]
    payload = build_daily_summary_payload(state, records)
    assert len(payload['memory_risks']) == 8
    assert payload['memory_risks_count'] == 22
    summary = local_daily_summary(state, records)
    assert '15 条' in summary['overview'][0]
    assert '遗忘风险 22 项' in summary['overview'][1]


def test_today_card_distinguishes_uncreated_completed_failure_and_direct_task():
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    from study_app.ui.design_components import today_plan_card
    app = QApplication.instance() or QApplication([])
    state = SimpleNamespace(today=date(2026, 9, 22))
    opened = []
    plan = {'plan_date':'2026-09-22', 'budget_minutes':40,
            'summary':{'planned_minutes':25,'remaining_minutes':15},
            'items':[{'id':71,'item_text':'数学 / 长标题任务','checked':False,'excluded_reason':None}]}
    with patch('study_app.data.database.get_budgeted_day_plan', return_value=plan) as read, \
            patch('study_app.data.database.get_active_study_plan', return_value=None):
        card = today_plan_card(state, opened.append)
        button = card.findChild(QPushButton)
        button.click()
        assert opened == [71]
        assert '0/1' in card.findChild(QLabel,'TodayPlanSummary').text()
        plan['items'][0]['checked'] = True
        card.refresh()
        assert '已完成' in card.findChild(QLabel,'TodayNextTask').text()
        read.return_value = None
        card.refresh()
        assert '暂无今日计划' in card.findChild(QLabel,'TodayPlanSummary').text()
        read.side_effect = RuntimeError('database unavailable')
        card.refresh()
        assert '无法读取' in card.findChild(QLabel,'TodayPlanSummary').text()
        card.deleteLater()
    app.processEvents()


def test_standard_dialog_buttons_are_chinese():
    from PySide6.QtWidgets import QApplication, QMessageBox
    from study_app.ui.design_components import install_chinese_dialogs
    app = QApplication.instance() or QApplication([])
    install_chinese_dialogs(app)
    box = QMessageBox()
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    assert box.button(QMessageBox.StandardButton.Ok).text() == '确定'
    assert box.button(QMessageBox.StandardButton.Cancel).text() == '取消'
    box.deleteLater()


def test_plan_answer_does_not_recommend_completed_tasks():
    from study_app.ui.design_components import current_plan_answer
    plan = {'plan_date':'2026-09-22','budget_minutes':40,
            'summary':{'planned_minutes':35},
            'items':[{'item_text':'已做完的任务','checked':True,'excluded_reason':None,'estimated_minutes':25},
                     {'item_text':'下一项任务','checked':False,'excluded_reason':None,'estimated_minutes':10},
                     {'item_text':'时间不够的任务','checked':False,'excluded_reason':'budget','estimated_minutes':30}]}
    with patch('study_app.data.database.get_budgeted_day_plan', return_value=plan):
        answer = current_plan_answer(SimpleNamespace(today=date(2026,9,22)))
    text = '\n'.join(answer['answer'])
    assert '下一项任务' in text
    assert '已做完的任务' not in text
    assert '时间不够的任务' not in text
