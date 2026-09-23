from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from study_app.core.active_subjects import active_subject_names, require_activity_subject

if TYPE_CHECKING:
    from study_app.core.dashboard import DashboardState, TodoItem


SOURCE_KIND = "model_topic_practice"


@dataclass(frozen=True)
class TopicSource:
    subject_id: str
    source_kind: str
    source_id: str
    source_method: str
    task_id: str


@dataclass(frozen=True)
class PlanCandidate:
    task_id: str
    subject_id: str
    source_kind: str
    source_id: str
    title: str
    priority_evidence: dict[str, object] = field(default_factory=dict)
    estimated_minutes: int | None = None
    estimate_source: str | None = None
    completion_state: str = "pending"


@dataclass(frozen=True)
class UnmappedPlanItem:
    title: str
    reason: str
    subject_id: str | None = None


@dataclass(frozen=True)
class CandidateCollection:
    candidates: tuple[PlanCandidate, ...]
    unmapped: tuple[UnmappedPlanItem, ...]


def _digest(value: dict[str, str]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def task_id_for_source(subject_id: str, source_kind: str, source_id: str) -> str:
    if not all(isinstance(value, str) and value.strip() for value in (subject_id, source_kind, source_id)):
        raise ValueError("任务来源必须有学科、类型和对象标识")
    return "task:v1:" + _digest(
        {"subject_id": subject_id, "source_kind": source_kind, "source_id": source_id}
    )


def identify_model_topic(
    model_data: dict,
    subject_name: str,
    module_name: str,
    topic_name: str,
) -> TopicSource | None:
    """Use a model source object, never a rendered plan line, for task identity."""
    matches = [
        (subject, module, topic)
        for subject in model_data.get("subjects", [])
        if subject.get("name") == subject_name
        for module in subject.get("modules", [])
        if module.get("name") == module_name
        for topic in module.get("topics", [])
        if topic.get("name") == topic_name
    ]
    if len(matches) != 1:
        return None
    _subject, _module, topic = matches[0]
    explicit_id = topic.get("id")
    if isinstance(explicit_id, str) and explicit_id.strip():
        same_id_count = sum(
            candidate.get("id") == explicit_id
            for subject in model_data.get("subjects", [])
            if subject.get("name") == subject_name
            for module in subject.get("modules", [])
            for candidate in module.get("topics", [])
        )
        if same_id_count != 1:
            return None
        source_method = "explicit_model_topic_id"
        source_payload = {"subject": subject_name, "id": explicit_id.strip()}
    else:
        source_method = "model_topic_path"
        source_payload = {
            "subject": subject_name,
            "module": module_name,
            "topic": topic_name,
        }
    source_id = source_method + ":v1:" + _digest(source_payload)
    return TopicSource(
        subject_id=subject_name,
        source_kind=SOURCE_KIND,
        source_id=source_id,
        source_method=source_method,
        task_id=task_id_for_source(subject_name, SOURCE_KIND, source_id),
    )


def build_plan_candidates(
    state: DashboardState,
    subject_scope: str | None = None,
) -> CandidateCollection:
    """Return identified activity candidates and explicit unmapped old text items."""
    if subject_scope:
        require_activity_subject(state, subject_scope, "进入预算计划候选范围")
    active_names = set(active_subject_names(state.subjects))
    candidates_by_id: dict[str, PlanCandidate] = {}
    unmapped: list[UnmappedPlanItem] = []
    for item in state.todos:
        subject_id = item.subject_id
        if subject_id and subject_id not in active_names:
            unmapped.append(UnmappedPlanItem(item.title, "subject_archived_or_unknown", subject_id))
            continue
        if subject_scope and subject_id and subject_id != subject_scope:
            continue
        if not all((subject_id, item.source_kind, item.source_id, item.task_id)):
            unmapped.append(UnmappedPlanItem(item.title, "missing_stable_source", subject_id))
            continue
        expected_id = task_id_for_source(subject_id, item.source_kind, item.source_id)
        if item.task_id != expected_id:
            unmapped.append(UnmappedPlanItem(item.title, "invalid_source_identity", subject_id))
            continue
        candidate = PlanCandidate(
            task_id=item.task_id,
            subject_id=subject_id,
            source_kind=item.source_kind,
            source_id=item.source_id,
            title=item.title,
            priority_evidence={
                "kind": item.kind,
                "priority": item.priority,
                "detail": item.detail,
            },
        )
        prior = candidates_by_id.get(candidate.task_id)
        if prior is None or candidate.priority_evidence["priority"] > prior.priority_evidence["priority"]:
            candidates_by_id[candidate.task_id] = candidate
    return CandidateCollection(
        candidates=tuple(sorted(candidates_by_id.values(), key=lambda item: item.task_id)),
        unmapped=tuple(unmapped),
    )
