from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from study_app.core.active_subjects import (
    dashboard_activity_subject_names as activity_subject_names,
    dashboard_activity_subjects as activity_subjects,
    dashboard_plan_subject_scope_label as plan_subject_scope_label,
)


if TYPE_CHECKING:
    from study_app.core.dashboard import DashboardState


ALL_ACTIVE_SUBJECTS_LABEL = "全部活动学科"


@dataclass(frozen=True)
class HomepageActivity:
    todos: tuple
    memory_risks: tuple[dict, ...]
    bkt_alerts: tuple[dict, ...]
    low_subjects: tuple[str, ...]
    stale_subjects: tuple

    @property
    def counts(self) -> dict[str, int]:
        return {
            "todos": len(self.todos),
            "memory_risks": len(self.memory_risks),
            "bkt_alerts": len(self.bkt_alerts),
            "low_subjects": len(self.low_subjects),
        }


def filter_homepage_activity(state: DashboardState) -> HomepageActivity:
    active_names = set(activity_subject_names(state))
    archived_names = {
        str(subject.name)
        for subject in state.subjects
        if getattr(subject, "archived", False)
    }

    def keep_todo(item) -> bool:
        prefix, separator, _ = str(item.title).partition(" /")
        return not separator or prefix.strip() not in archived_names

    return HomepageActivity(
        todos=tuple(item for item in state.todos if keep_todo(item)),
        memory_risks=tuple(item for item in state.memory_risks if item.get("subject") in active_names),
        bkt_alerts=tuple(item for item in state.bkt_alerts if item.get("subject") in active_names),
        low_subjects=tuple(name for name in state.low_subjects if name in active_names),
        stale_subjects=tuple(subject for subject in state.stale_subjects if subject.name in active_names),
    )


def record_subject_names(state: DashboardState) -> tuple[str, ...]:
    return activity_subject_names(state)


def final_review_subject_names(state: DashboardState) -> tuple[str, ...]:
    return activity_subject_names(state)


def plan_subject_scope_options(state: DashboardState) -> tuple[tuple[str, str], ...]:
    subjects = tuple((name, name) for name in activity_subject_names(state))
    return ((ALL_ACTIVE_SUBJECTS_LABEL, ""), *subjects)
