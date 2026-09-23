from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import TYPE_CHECKING, TypeVar


if TYPE_CHECKING:
    from study_app.core.dashboard import DashboardState


SubjectT = TypeVar("SubjectT")

SUBJECT_COURSE_ALIASES = {
    "高等数学": ("高等数学", "微积分2", "微积分Ⅱ", "微积分", "高数"),
    "大学物理学": ("大学物理学", "大学物理", "大物"),
    "化学原理": ("化学原理",),
    "数据结构与算法基础": ("数据结构与算法基础", "数据结构课程"),
    "计算机科学": ("计算机科学",),
    "高级程序设计": (
        "高级程序设计",
        "高级程序设计课程",
        "C++高级程序设计",
        "C++程序设计",
        "C++面向对象程序设计",
    ),
}


def active_subjects(subjects: Iterable[SubjectT]) -> tuple[SubjectT, ...]:
    return tuple(subject for subject in subjects if not getattr(subject, "archived", False))


def active_subject_names(subjects: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(subject.name) for subject in active_subjects(subjects))


def dashboard_activity_subjects(state: DashboardState) -> tuple:
    return active_subjects(state.subjects)


def dashboard_activity_subject_names(state: DashboardState) -> tuple[str, ...]:
    return active_subject_names(state.subjects)


def dashboard_plan_subject_scope_label(
    state: DashboardState | str | None = None,
    subject_name: str | None = None,
) -> str:
    if subject_name is None and isinstance(state, str):
        subject_name = state
    return subject_name or "全部活动学科"


def archived_subject_names(subjects: Iterable[object]) -> tuple[str, ...]:
    return tuple(
        str(subject.name)
        for subject in subjects
        if getattr(subject, "archived", False)
    )


def require_activity_subject(state: DashboardState, subject_name: str, action: str) -> None:
    if subject_name not in set(active_subject_names(state.subjects)):
        raise ValueError(f"{subject_name} 已封存，不再{action}。")


def _normalize_subject_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _aliases_for(subject_name: str) -> tuple[str, ...]:
    return SUBJECT_COURSE_ALIASES.get(subject_name, (subject_name,))


def _alias_in_text(alias: str, normalized_text: str) -> bool:
    normalized_alias = _normalize_subject_text(alias)
    if alias == "高数":
        return re.search(r"高数(?!据)", normalized_text) is not None
    if alias == "大物":
        return re.search(r"大物(?!理)", normalized_text) is not None
    return normalized_alias in normalized_text


def subject_reference_violations(
    text: object,
    allowed_subjects: Iterable[str],
    archived_subjects: Iterable[str] | None = None,
    alias_map: Mapping[str, Iterable[str]] | None = None,
) -> tuple[str, ...]:
    normalized_text = _normalize_subject_text(text)
    normalized_allowed = {
        _normalize_subject_text(name)
        for name in allowed_subjects
        if str(name or "").strip()
    }
    authoritative_aliases = (
        {str(key): tuple(value) for key, value in alias_map.items()}
        if alias_map is not None
        else SUBJECT_COURSE_ALIASES
    )
    blocked = tuple(archived_subjects) if archived_subjects is not None else tuple(authoritative_aliases)
    violations = []
    for canonical in blocked:
        aliases = authoritative_aliases.get(str(canonical), (str(canonical),))
        if normalized_allowed.intersection(_normalize_subject_text(alias) for alias in aliases):
            continue
        matched = next((alias for alias in aliases if _alias_in_text(alias, normalized_text)), None)
        if matched:
            violations.append(
                str(canonical) if matched == canonical else f"{canonical}（{matched}）"
            )
    return tuple(violations)
