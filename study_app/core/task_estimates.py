from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source


USER_ESTIMATE = "user"
CONFIRMED_TEMPLATE_ESTIMATE = "confirmed_template"
ESTIMATE_SOURCES = frozenset((USER_ESTIMATE, CONFIRMED_TEMPLATE_ESTIMATE))


@dataclass(frozen=True)
class TaskEstimate:
    task_id: str
    estimated_minutes: int
    source: str


@dataclass(frozen=True)
class EstimateExclusion:
    task_id: str
    title: str
    reason: str


@dataclass(frozen=True)
class EstimatedCandidates:
    candidates: tuple[PlanCandidate, ...]
    excluded: tuple[EstimateExclusion, ...]


def validate_estimated_minutes(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 1440:
        raise ValueError("任务预计分钟必须是 1～1440 的整数")
    return value


def validate_estimate_source(value: object) -> str:
    if not isinstance(value, str) or value not in ESTIMATE_SOURCES:
        raise ValueError("估时来源必须是用户输入或已确认模板")
    return value


def validate_candidate_identity(candidate: PlanCandidate) -> None:
    if not isinstance(candidate, PlanCandidate) or candidate.task_id != task_id_for_source(
        candidate.subject_id, candidate.source_kind, candidate.source_id
    ):
        raise ValueError("估时必须绑定有效的结构化任务来源")


def make_task_estimate(candidate: PlanCandidate, minutes: object, source: object) -> TaskEstimate:
    validate_candidate_identity(candidate)
    return TaskEstimate(candidate.task_id, validate_estimated_minutes(minutes), validate_estimate_source(source))


def make_assistant_task_estimate(candidate: PlanCandidate) -> TaskEstimate:
    """Return a stable local assistant estimate without consulting user history.

    The estimate uses the already-confirmed local template source so it can be
    scheduled immediately, while a later user edit still takes precedence.
    No external model call or database write is performed here.
    """
    validate_candidate_identity(candidate)
    evidence = candidate.priority_evidence if isinstance(candidate.priority_evidence, dict) else {}
    raw_priority = evidence.get("priority", 0.0)
    if isinstance(raw_priority, dict):
        raw_priority = raw_priority.get("value", 0.0)
    try:
        priority = max(0.0, min(1.0, float(raw_priority)))
    except (TypeError, ValueError):
        priority = 0.0

    minutes = 30 if priority >= 0.75 else 25 if priority >= 0.4 else 20
    descriptor = " ".join(
        str(value)
        for value in (candidate.title, evidence.get("kind"), evidence.get("detail"))
        if value
    )
    if any(token in descriptor for token in ("模拟卷", "诊断卷", "整套", "综合测验")):
        minutes = max(minutes, 60)
    elif any(token in descriptor for token in ("证明", "推导", "综合")):
        minutes = max(minutes, 30)
    elif any(token in descriptor for token in ("复习", "回顾", "记忆")):
        minutes = max(minutes, 25)
    return make_task_estimate(candidate, minutes, CONFIRMED_TEMPLATE_ESTIMATE)


def resolve_candidate_estimates(
    collection: CandidateCollection,
    user_estimates: Mapping[str, TaskEstimate],
    confirmed_template_estimates: Mapping[str, TaskEstimate],
) -> EstimatedCandidates:
    """Only explicit current estimates count; historical actual durations are never consulted."""
    selected: list[PlanCandidate] = []
    excluded: list[EstimateExclusion] = []
    for candidate in collection.candidates:
        validate_candidate_identity(candidate)
        user = user_estimates.get(candidate.task_id)
        template = confirmed_template_estimates.get(candidate.task_id)
        for estimate, expected_source in ((user, USER_ESTIMATE), (template, CONFIRMED_TEMPLATE_ESTIMATE)):
            if estimate is not None:
                if estimate.task_id != candidate.task_id or validate_estimate_source(estimate.source) != expected_source:
                    raise ValueError("估时记录的任务 ID 或来源与候选不一致")
                validate_estimated_minutes(estimate.estimated_minutes)
        estimate = user or template
        if estimate is None:
            excluded.append(EstimateExclusion(candidate.task_id, candidate.title, "missing_estimate"))
            continue
        selected.append(replace(candidate, estimated_minutes=estimate.estimated_minutes, estimate_source=estimate.source))
    return EstimatedCandidates(tuple(selected), tuple(excluded))
