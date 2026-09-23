from __future__ import annotations

import os
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QWidget,
)

from study_app.core.dashboard import DashboardState
from study_app.core.knowledge_explanations import explain_knowledge_topic
from study_app.core.knowledge_states import classify_topic
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import RecentTopicError, TopicInsight
from study_app.data.database import DatabaseNotInitializedError
from study_app.ui.knowledge_page import KnowledgePageEntry, knowledge_page


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


def topic_insight(topic_id: int, **changes) -> TopicInsight:
    base = TopicInsight(
        topic_key=topic_key_for_id(topic_id),
        topic_id=topic_id,
        subject_name="学科一",
        module_name="模块一",
        topic_name=f"知识点{topic_id}",
        subject_status="active",
        course_status="learning",
        is_archived=False,
        observation_count=4,
        exercise_count=2,
        evidence_confidence=0.8,
        evidence_version="evidence-v1",
        mastery_point=0.75,
        mastery_source="bkt_observed",
        mastery_interval=(0.63, 0.87),
        mastery_interval_rule_version="mastery-interval-v1",
        recent_error=None,
        last_observation_date=AS_OF - timedelta(days=2),
        last_observation_correctness=1.0,
        last_review_date=AS_OF - timedelta(days=3),
        review_count=2,
        half_life_days=8.0,
        recall_probability=0.9,
        target_recall=0.78,
        recommended_date=AS_OF + timedelta(days=2),
        recommendation_source="half_life_target_recall",
        direct_prerequisite_keys=(),
        unresolved_prerequisite_keys=(),
        as_of_date=AS_OF,
        model_version="model-v1",
        records_version="records-v1",
    )
    return replace(base, **changes)


def trace():
    return {
        "coverage": "traced",
        "legacy_provenance_gap": False,
        "contributions": [],
    }


def page_entries() -> tuple[KnowledgePageEntry, ...]:
    prerequisite = classify_topic(
        topic_insight(1, topic_name="基础概念"),
        {},
    )
    failure_insight = topic_insight(
        2,
        topic_name="进阶应用",
        observation_count=3,
        exercise_count=1,
        evidence_confidence=0.6,
        mastery_point=0.45,
        mastery_interval=(0.26, 0.64),
        recent_error=RecentTopicError(
            record_id=20,
            occurred_on=AS_OF - timedelta(days=1),
            title="边界条件判断",
            correctness=0.0,
            result_source="problem.correctness",
            error_cause="遗漏空输入",
        ),
        last_observation_date=AS_OF - timedelta(days=1),
        last_observation_correctness=0.0,
        direct_prerequisite_keys=(prerequisite.insight.topic_key,),
    )
    failure = classify_topic(
        failure_insight,
        {prerequisite.insight.topic_key: prerequisite},
    )
    insufficient = classify_topic(
        topic_insight(
            3,
            subject_name="学科二",
            module_name="模块二",
            topic_name="诊断主题",
            evidence_confidence=0.2,
        ),
        {},
    )
    unlearned = classify_topic(
        topic_insight(
            4,
            subject_name="学科二",
            module_name="模块二",
            topic_name="尚未学习",
            course_status="not_started",
            observation_count=0,
            exercise_count=0,
            evidence_confidence=0.0,
            mastery_point=0.3,
            mastery_source="baseline_prior",
            mastery_interval=None,
            last_observation_date=None,
            last_observation_correctness=None,
            last_review_date=None,
            review_count=0,
            recommended_date=None,
            recommendation_source="unavailable",
        ),
        {},
    )
    archived = classify_topic(
        topic_insight(
            5,
            subject_name="旧学科",
            module_name="旧模块",
            topic_name="历史主题",
            subject_status="archived",
            is_archived=True,
        ),
        {},
    )
    classified = (failure, insufficient, unlearned, prerequisite, archived)
    by_key = {item.insight.topic_key: item for item in classified}
    return tuple(
        KnowledgePageEntry(
            item,
            explain_knowledge_topic(item, by_key, trace()),
        )
        for item in classified
    )


def set_combo_data(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    if index < 0:
        raise AssertionError(f"combo value not found: {value!r}")
    combo.setCurrentIndex(index)


class KnowledgePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def graph_stub(self):
        panel = QWidget()
        panel.setObjectName("ChapterGraphPanel")
        panel._refresh_for_state = Mock()
        panel._close_graph_tasks = Mock()
        return panel

    def test_page_keeps_only_the_knowledge_graph_surface(self) -> None:
        panel = self.graph_stub()
        loader = Mock()
        with patch(
            "study_app.ui.chapter_graph_view.chapter_graph_panel",
            return_value=panel,
        ):
            page = knowledge_page(dashboard_state(), data_loader=loader)
        labels = [item.text() for item in page.findChildren(QLabel)]
        self.assertIn("知识图谱", labels)
        self.assertIs(page._graph_panel, panel)
        self.assertIsNone(page.findChild(QListWidget, "KnowledgeList"))
        self.assertIsNone(page.findChild(QPushButton, "GraphViewButton"))
        self.assertIsNone(page.findChild(QPushButton, "DetailViewButton"))
        margins = page.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (28, 24, 28, 24),
        )
        loader.assert_not_called()
        page.close()

    def test_refresh_is_forwarded_to_the_graph_in_place(self) -> None:
        panel = self.graph_stub()
        with patch(
            "study_app.ui.chapter_graph_view.chapter_graph_panel",
            return_value=panel,
        ):
            page = knowledge_page(dashboard_state())
        refreshed = dashboard_state(AS_OF + timedelta(days=1))
        page._set_dashboard_state(refreshed)
        panel._refresh_for_state.assert_called_once_with(refreshed)
        page.close()

    def test_page_close_closes_graph_tasks(self) -> None:
        panel = self.graph_stub()
        with patch(
            "study_app.ui.chapter_graph_view.chapter_graph_panel",
            return_value=panel,
        ):
            page = knowledge_page(dashboard_state())
        page.show()
        self.app.processEvents()
        page.close()
        self.app.processEvents()
        panel._close_graph_tasks.assert_called_once_with()

    def test_window_refresh_routes_new_state_to_knowledge_page_in_place(self) -> None:
        from study_app.ui import window_shell

        knowledge = QScrollArea()
        knowledge._set_dashboard_state = Mock()
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
        for name in ("study_plan_page", "query_page", "settings_page"):
            stack.enter_context(
                patch.object(
                    window_shell,
                    name,
                    side_effect=lambda *_args, **_kwargs: QWidget(),
                )
            )
        stack.enter_context(patch.object(window_shell, "knowledge_page", return_value=knowledge))
        stack.enter_context(patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False))
        with stack:
            window = window_shell.MainWindow(dashboard_state())
            refreshed = dashboard_state(AS_OF + timedelta(days=1))
            window._refresh_dashboard_state(refreshed)
            self.assertIs(window.stack.widget(window.PAGE_KNOWLEDGE), knowledge)
            knowledge._set_dashboard_state.assert_called_once_with(refreshed)
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
