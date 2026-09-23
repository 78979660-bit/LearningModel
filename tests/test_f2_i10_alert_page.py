from __future__ import annotations

import os
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QWidget,
)

from study_app.core.dashboard import DashboardState
from study_app.core.knowledge_alerts import KnowledgeAlert, KnowledgeAlertEvent
from study_app.core.knowledge_explanations import explain_knowledge_topic
from study_app.core.knowledge_states import classify_topic
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import RecentTopicError, TopicInsight
from study_app.data.database import DatabaseNotInitializedError
from study_app.ui.alert_page import AlertPageEntry, alert_page
from study_app.ui.knowledge_page import KnowledgePageEntry


AS_OF = date(2026, 9, 17)


def dashboard_state(today: date = AS_OF) -> DashboardState:
    return DashboardState(
        start=today - timedelta(days=2),
        today=today,
        benchmark=60,
        subjects=(),
        todos=(),
        memory_risks=(),
        bkt_alerts=(),
        raw_records=(),
        model_data={},
    )


def classified_topic(topic_id: int):
    insight = TopicInsight(
        topic_key=topic_key_for_id(topic_id),
        topic_id=topic_id,
        subject_name=f"学科{topic_id}",
        module_name="模块",
        topic_name=f"知识点{topic_id}",
        subject_status="active",
        course_status="learning",
        is_archived=False,
        observation_count=3,
        exercise_count=2,
        evidence_confidence=0.6,
        evidence_version=f"evidence-{topic_id}",
        mastery_point=0.4,
        mastery_source="bkt_observed",
        mastery_interval=(0.2, 0.6),
        mastery_interval_rule_version="mastery-interval-v1",
        recent_error=RecentTopicError(
            record_id=topic_id * 10,
            occurred_on=AS_OF - timedelta(days=1),
            title="边界题",
            correctness=0.0,
            result_source="problem.correctness",
            error_cause="边界遗漏",
        ),
        last_observation_date=AS_OF - timedelta(days=1),
        last_observation_correctness=0.0,
        last_review_date=AS_OF - timedelta(days=4),
        review_count=2,
        half_life_days=5.0,
        recall_probability=0.5,
        target_recall=0.78,
        recommended_date=AS_OF,
        recommendation_source="half_life_target_recall",
        direct_prerequisite_keys=(),
        unresolved_prerequisite_keys=(),
        as_of_date=AS_OF,
        model_version="model-v1",
        records_version="records-v1",
    )
    return classify_topic(insight, {})


def entry(
    alert_id: int,
    status: str,
    *,
    priority: float = 0.8,
    recommended: date | None = AS_OF,
) -> AlertPageEntry:
    classified = classified_topic(alert_id)
    insight = classified.insight
    alert = KnowledgeAlert(
        id=alert_id,
        fingerprint=f"{alert_id:064x}",
        topic_key=insight.topic_key,
        alert_type="recent_failure",
        status=status,
        rule_version=classified.rule_version,
        evidence_version=insight.evidence_version,
        condition_cycle=f"cycle-{alert_id}",
        as_of_date=AS_OF,
        recommended_date=recommended,
        priority=priority,
        snapshot={
            "topic": {
                "subject_name": insight.subject_name,
                "module_name": insight.module_name,
                "topic_name": insight.topic_name,
            },
            "classification": {
                "primary_state": classified.primary_state,
                "reason_codes": list(classified.reason_codes),
                "recommended_action": classified.recommended_action,
            },
            "evidence": {
                "observation_count": insight.observation_count,
                "exercise_count": insight.exercise_count,
                "confidence": insight.evidence_confidence,
            },
            "mastery": {
                "point": insight.mastery_point,
                "interval": list(insight.mastery_interval or ()),
            },
            "memory": {
                "recall_probability": insight.recall_probability,
                "target_recall": insight.target_recall,
            },
        },
        snoozed_until=(AS_OF + timedelta(days=3)) if status == "snoozed" else None,
        handled_at=AS_OF.isoformat() if status == "handled" else None,
        resolved_at="2026-09-17 09:00:00" if status == "resolved" else None,
        created_at="2026-09-17 08:00:00",
        updated_at="2026-09-17 09:00:00",
    )
    event = KnowledgeAlertEvent(
        id=alert_id,
        alert_id=alert_id,
        event_type="created" if status == "active" else status,
        from_status=None if status == "active" else "active",
        to_status=status,
        effective_date=AS_OF,
        actor="desktop_local_user",
        detail={},
        created_at="2026-09-17 08:00:00",
    )
    explanation = explain_knowledge_topic(
        classified,
        {classified.insight.topic_key: classified},
        {
            "coverage": "traced",
            "legacy_provenance_gap": False,
            "contributions": [],
        },
        alert=alert,
        alert_events=(event,),
    )
    return AlertPageEntry(alert, (event,), classified, explanation)


def entries() -> tuple[AlertPageEntry, ...]:
    return (
        entry(1, "active", priority=0.4, recommended=AS_OF - timedelta(days=1)),
        entry(2, "active", priority=0.9, recommended=AS_OF),
        entry(3, "snoozed", priority=0.7),
        entry(4, "handled", priority=0.6),
        entry(5, "resolved", priority=0.5),
    )


def set_view(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index < 0:
        raise AssertionError(value)
    combo.setCurrentIndex(index)


def replace_status(value: AlertPageEntry, status: str) -> AlertPageEntry:
    alert = replace(
        value.alert,
        status=status,
        snoozed_until=(AS_OF + timedelta(days=3)) if status == "snoozed" else None,
        handled_at=AS_OF.isoformat() if status == "handled" else None,
    )
    event = KnowledgeAlertEvent(
        id=100 + value.alert.id,
        alert_id=value.alert.id,
        event_type=status,
        from_status=value.alert.status,
        to_status=status,
        effective_date=AS_OF,
        actor="desktop_local_user",
        detail={},
        created_at="2026-09-17 10:00:00",
    )
    history = value.events + (event,)
    explanation = explain_knowledge_topic(
        value.classified_topic,
        {value.classified_topic.insight.topic_key: value.classified_topic},
        {
            "coverage": "traced",
            "legacy_provenance_gap": False,
            "contributions": [],
        },
        alert=alert,
        alert_events=history,
    )
    return AlertPageEntry(alert, history, value.classified_topic, explanation)


class AlertPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def build_page(self, values=None, **kwargs):
        source = entries() if values is None else tuple(values)
        return alert_page(
            dashboard_state(),
            data_loader=lambda _state: source,
            **kwargs,
        )

    def test_defaults_to_active_with_four_views_and_stable_sort(self) -> None:
        page = self.build_page()
        view = page.findChild(QComboBox, "AlertStatusFilter")
        alert_list = page.findChild(QListWidget, "AlertList")
        self.assertEqual(view.currentData(), "active")
        self.assertEqual(
            [view.itemData(index) for index in range(view.count())],
            ["active", "snoozed", "handled", "resolved"],
        )
        self.assertEqual(alert_list.count(), 2)
        self.assertEqual(alert_list.item(0).data(256), 2)
        for status, expected_id in (
            ("snoozed", 3),
            ("handled", 4),
            ("resolved", 5),
        ):
            set_view(view, status)
            self.assertEqual(alert_list.count(), 1)
            self.assertEqual(alert_list.item(0).data(256), expected_id)
        page.close()

    def test_detail_button_states_and_four_layer_explanation(self) -> None:
        page = self.build_page()
        view = page.findChild(QComboBox, "AlertStatusFilter")
        handle = page.findChild(QPushButton, "AlertHandleButton")
        snooze = page.findChild(QPushButton, "AlertSnoozeButton")
        explain = page.findChild(QPushButton, "AlertExplainButton")
        explanation = page.findChild(QLabel, "AlertExplanation")
        self.assertTrue(handle.isEnabled())
        self.assertTrue(snooze.isEnabled())
        explain.click()
        self.assertTrue(explanation.isVisibleTo(page))
        self.assertIn("[rules]", explanation.text())
        self.assertIn("[evidence]", explanation.text())
        self.assertIn("[model]", explanation.text())
        self.assertIn("[workflow]", explanation.text())
        self.assertIn("created", page.findChild(QLabel, "AlertEventHistory").text())

        set_view(view, "snoozed")
        self.assertFalse(handle.isEnabled())
        self.assertTrue(snooze.isEnabled())
        set_view(view, "handled")
        self.assertFalse(handle.isEnabled())
        self.assertFalse(snooze.isEnabled())
        self.assertTrue(explain.isEnabled())
        page.close()

    def test_handle_success_calls_i06_then_refreshes_without_reconcile(self) -> None:
        backing = list(entries())
        loader = Mock(side_effect=lambda _state: tuple(backing))

        def handle_writer(alert_id, effective_date):
            index = next(i for i, value in enumerate(backing) if value.alert.id == alert_id)
            backing[index] = replace_status(backing[index], "handled")
            return backing[index].alert

        writer = Mock(side_effect=handle_writer)
        page = alert_page(
            dashboard_state(),
            data_loader=loader,
            handle_action=writer,
            snooze_action=Mock(),
        )
        alert_list = page.findChild(QListWidget, "AlertList")
        selected_id = alert_list.currentItem().data(256)
        page.findChild(QPushButton, "AlertHandleButton").click()
        writer.assert_called_once_with(selected_id, AS_OF)
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(alert_list.count(), 1)
        self.assertIn("预警已处理", page.findChild(QLabel, "AlertActionStatus").text())
        set_view(page.findChild(QComboBox, "AlertStatusFilter"), "handled")
        self.assertTrue(any(alert_list.item(i).data(256) == selected_id for i in range(alert_list.count())))
        page.close()

    def test_snooze_failure_preserves_view_selection_date_and_does_not_reload(self) -> None:
        loader = Mock(return_value=entries())
        failure = Mock(side_effect=RuntimeError("temporary write failure"))
        page = alert_page(
            dashboard_state(),
            data_loader=loader,
            handle_action=Mock(),
            snooze_action=failure,
        )
        view = page.findChild(QComboBox, "AlertStatusFilter")
        alert_list = page.findChild(QListWidget, "AlertList")
        date_input = page.findChild(QDateEdit, "AlertSnoozeDate")
        date_input.setDate(QDate(2026, 9, 21))
        alert_list.setCurrentRow(1)
        selected_id = alert_list.currentItem().data(256)
        page.findChild(QPushButton, "AlertSnoozeButton").click()
        failure.assert_called_once_with(selected_id, date(2026, 9, 21), AS_OF)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(view.currentData(), "active")
        self.assertEqual(alert_list.currentItem().data(256), selected_id)
        self.assertEqual(date_input.date().toPython(), date(2026, 9, 21))
        self.assertIn("延后失败", page.findChild(QLabel, "AlertActionStatus").text())
        page.close()

    def test_snooze_success_calls_i07_then_moves_alert_to_snoozed_view(self) -> None:
        backing = list(entries())
        loader = Mock(side_effect=lambda _state: tuple(backing))

        def snooze_writer(alert_id, snoozed_until, as_of_date):
            index = next(i for i, value in enumerate(backing) if value.alert.id == alert_id)
            updated = replace_status(backing[index], "snoozed")
            updated = replace(
                updated,
                alert=replace(updated.alert, snoozed_until=snoozed_until),
            )
            backing[index] = updated
            return updated.alert

        writer = Mock(side_effect=snooze_writer)
        page = alert_page(
            dashboard_state(),
            data_loader=loader,
            handle_action=Mock(),
            snooze_action=writer,
        )
        alert_list = page.findChild(QListWidget, "AlertList")
        selected_id = alert_list.currentItem().data(256)
        date_input = page.findChild(QDateEdit, "AlertSnoozeDate")
        date_input.setDate(QDate(2026, 9, 23))
        page.findChild(QPushButton, "AlertSnoozeButton").click()
        writer.assert_called_once_with(selected_id, date(2026, 9, 23), AS_OF)
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(alert_list.count(), 1)
        self.assertIn("2026-09-23", page.findChild(QLabel, "AlertActionStatus").text())
        set_view(page.findChild(QComboBox, "AlertStatusFilter"), "snoozed")
        self.assertTrue(any(alert_list.item(i).data(256) == selected_id for i in range(alert_list.count())))
        page.close()

    def test_refresh_is_readonly_and_preserves_view_selection_and_snooze_date(self) -> None:
        loader = Mock(return_value=entries())
        handle = Mock()
        snooze = Mock()
        page = alert_page(
            dashboard_state(),
            data_loader=loader,
            handle_action=handle,
            snooze_action=snooze,
        )
        view = page.findChild(QComboBox, "AlertStatusFilter")
        alert_list = page.findChild(QListWidget, "AlertList")
        date_input = page.findChild(QDateEdit, "AlertSnoozeDate")
        set_view(view, "snoozed")
        selected_id = alert_list.currentItem().data(256)
        date_input.setDate(QDate(2026, 9, 22))
        page._set_dashboard_state(dashboard_state(AS_OF + timedelta(days=1)))
        self.app.processEvents()
        self.assertEqual(loader.call_count, 2)
        handle.assert_not_called()
        snooze.assert_not_called()
        self.assertEqual(view.currentData(), "snoozed")
        self.assertEqual(alert_list.currentItem().data(256), selected_id)
        self.assertEqual(date_input.date().toPython(), date(2026, 9, 22))
        self.assertIn("2026-09-18", page.findChild(QLabel, "AlertCutoff").text())
        page.close()

    def test_missing_f2_tables_show_readonly_diagnostic(self) -> None:
        def missing(_state):
            raise DatabaseNotInitializedError("alerts missing")

        page = alert_page(dashboard_state(), data_loader=missing)
        diagnostic = page.findChild(QLabel, "AlertDiagnostic")
        self.assertTrue(diagnostic.isVisibleTo(page))
        self.assertIn("F2 结构尚未安装", diagnostic.text())
        self.assertIn("不会自动修改现用数据库", diagnostic.text())
        self.assertEqual(page.findChild(QListWidget, "AlertList").count(), 0)
        self.assertFalse(page.findChild(QPushButton, "AlertHandleButton").isEnabled())
        self.assertFalse(page.findChild(QPushButton, "AlertSnoozeButton").isEnabled())
        page.close()

    def test_loader_does_not_mix_current_classification_into_stale_history(self) -> None:
        import importlib

        alert_module = importlib.import_module("study_app.ui.alert_page")
        current = entry(20, "active")
        knowledge = KnowledgePageEntry(
            current.classified_topic,
            current.explanation,
        )
        with (
            patch.object(alert_module, "list_knowledge_alerts", return_value=(current.alert,)),
            patch.object(alert_module, "list_knowledge_alert_events", return_value=current.events),
            patch.object(alert_module, "load_knowledge_page_entries", return_value=(knowledge,)),
        ):
            matching = alert_module.load_alert_page_entries(dashboard_state())
        self.assertIsNotNone(matching[0].explanation)

        stale = replace(current.alert, evidence_version="older-evidence")
        with (
            patch.object(alert_module, "list_knowledge_alerts", return_value=(stale,)),
            patch.object(alert_module, "list_knowledge_alert_events", return_value=current.events),
            patch.object(alert_module, "load_knowledge_page_entries", return_value=(knowledge,)),
        ):
            historical = alert_module.load_alert_page_entries(dashboard_state())
        self.assertIsNone(historical[0].explanation)

    def test_window_does_not_mount_a_dedicated_alert_page(self) -> None:
        from study_app.ui import window_shell

        activity = SimpleNamespace(
            counts={"todos": 0, "memory_risks": 0, "bkt_alerts": 0, "low_subjects": 0},
            todos=[],
            memory_risks=[],
            bkt_alerts=[],
        )
        stack = ExitStack()
        stack.enter_context(
            patch.object(window_shell, "load_llm_settings", return_value=SimpleNamespace(enabled=False))
        )
        stack.enter_context(patch.object(window_shell, "filter_homepage_activity", return_value=activity))
        for name in ("metric_card", "subject_card", "daily_summary_card", "todo_card", "risk_card"):
            stack.enter_context(
                patch.object(
                    window_shell,
                    name,
                    side_effect=lambda *_args, **_kwargs: QWidget(),
                )
            )
        for name in ("study_plan_page", "query_page", "knowledge_page", "settings_page"):
            stack.enter_context(
                patch.object(
                    window_shell,
                    name,
                    side_effect=lambda *_args, **_kwargs: QWidget(),
                )
            )
        stack.enter_context(patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False))
        with stack:
            window = window_shell.MainWindow(dashboard_state())
            nav = [
                button.text()
                for button in window.findChildren(QPushButton)
                if button.objectName() == "NavButton"
            ]
            self.assertNotIn("预警", nav)
            self.assertEqual(window.stack.count(), 5)
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
