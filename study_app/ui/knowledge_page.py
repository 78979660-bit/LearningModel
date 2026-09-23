"""知识图谱页面。

页面只承载章节拓扑图；旧的“知识点明细”仅保留为后台读模型，
不再作为独立前台界面。
"""
from __future__ import annotations

from dataclasses import dataclass

from study_app.core.computation_context import DashboardComputationContext
from study_app.core.dashboard import DashboardState
from study_app.core.knowledge_explanations import KnowledgeExplanation, explain_knowledge_topic
from study_app.core.knowledge_states import ClassifiedTopic, classify_topics
from study_app.core.topic_insights import build_topic_insights
from study_app.data.database import list_prerequisite_edges, list_topic_identities


@dataclass(frozen=True)
class KnowledgePageEntry:
    """后台知识点读模型，供预警、助理和解释链路复用。"""

    classified_topic: ClassifiedTopic
    explanation: KnowledgeExplanation


def load_knowledge_page_entries(state: DashboardState) -> tuple[KnowledgePageEntry, ...]:
    """构建后台知识点投影，不创建独立前台页面。"""
    identities = list_topic_identities()
    prerequisite_edges = list_prerequisite_edges()
    model = state.model_data
    records = list(state.raw_records)
    context = DashboardComputationContext(
        model, records, state.today, model.get("warning_policy", {})
    )
    insights = build_topic_insights(
        model,
        records,
        state.today,
        context,
        identities,
        prerequisite_edges,
    )
    classified = classify_topics(insights, include_archived=True)
    by_key = {item.insight.topic_key: item for item in classified}
    return tuple(
        KnowledgePageEntry(item, explain_knowledge_topic(item, by_key, None))
        for item in classified
    )


def knowledge_page(state: DashboardState, data_loader=None):
    """创建仅包含章节拓扑的知识图谱页。

    ``data_loader`` 仅为旧调用方兼容保留；图谱页不再加载知识点列表。
    """
    from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

    import study_app.ui.chapter_graph_view as chapter_graph_view

    current_state = [state]

    class KnowledgeGraphPage(QWidget):
        def closeEvent(self, event):
            close_graph_tasks = getattr(graph_panel, "_close_graph_tasks", None)
            if callable(close_graph_tasks):
                close_graph_tasks()
            super().closeEvent(event)

    page = KnowledgeGraphPage()
    page.setObjectName("KnowledgePage")
    layout = QVBoxLayout(page)
    # 与其他主页保持一致的内容安全区，避免标题和画布
    # 紧贴侧边栏与窗口边界。
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(16)

    hero = QFrame()
    hero.setObjectName("KnowledgeGraphHero")
    hero_layout = QVBoxLayout(hero)
    hero_layout.setContentsMargins(0, 0, 0, 0)
    hero_layout.setSpacing(4)
    heading = QLabel("知识图谱")
    heading.setObjectName("HeroTitle")
    hero_layout.addWidget(heading)
    layout.addWidget(hero)

    graph_panel = chapter_graph_view.chapter_graph_panel(
        parent_callable=lambda: current_state[0],
    )
    layout.addWidget(graph_panel, 1)

    def set_dashboard_state(new_state: DashboardState) -> None:
        current_state[0] = new_state
        refresh_graph = getattr(graph_panel, "_refresh_for_state", None)
        if callable(refresh_graph):
            refresh_graph(new_state)

    page._set_dashboard_state = set_dashboard_state
    page._graph_panel = graph_panel
    return page
