from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping

from study_app.core.day_budget_input import (
    validate_available_minutes, validate_day_budget_date, validate_subject_exam_date,
)
from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source
from study_app.core.task_estimates import validate_estimate_source, validate_estimated_minutes


COMPLETED_STATES = frozenset(("checked", "result_correct", "result_wrong"))
COMPLETION_STATES = COMPLETED_STATES | {"pending"}
SIGNAL_KEYS = ("priority", "forgetting_risk", "mastery_gap", "coverage_value")
PRIMARY_LABELS = {
    "exam_imminent": "考试临近", "todo_priority": "待办优先级较高",
    "forgetting_risk": "遗忘风险较高", "mastery_gap": "掌握缺口较大",
    "coverage_value": "未覆盖知识点需诊断", "stable_task_id": "按稳定任务标识排序",
}
EXCLUSION_LABELS = {
    "invalid_source_identity": "任务来源标识无效",
    "subject_archived_or_unknown": "学科已封存或未知",
    "invalid_completion_state": "完成状态无效",
    "future_source": "任务来源晚于计划日",
}


@dataclass(frozen=True)
class BudgetPlanInput:
    plan_date: str
    available_minutes: int
    active_subject_ids: tuple[str, ...]
    subject_scope: str | None = None
    subject_exam_dates: Mapping[str, str | None] = field(default_factory=dict)
    as_of_date: str | None = None


@dataclass(frozen=True)
class PlanDecision:
    task_id: str | None
    subject_id: str | None
    title: str
    estimated_minutes: int | None
    estimate_source: str | None
    rule_code: str
    reason: str
    ranking_evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetedDayPlan:
    selected: tuple[PlanDecision, ...]
    excluded: tuple[PlanDecision, ...]
    completed: tuple[PlanDecision, ...]
    planned_minutes: int
    remaining_minutes: int
    over_budget_completed_minutes: int
    input_signature: str
    completed_occupancy_unknown: bool = False


def _signal(value: object, plan_day: date) -> float:
    if isinstance(value, Mapping):
        evidence_date = value.get("evidence_date")
        if evidence_date is not None:
            if date.fromisoformat(validate_day_budget_date(evidence_date)) > plan_day:
                return 0.0
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) and number > 0 else 0.0


def _ranking(candidate: PlanCandidate, plan_day: date, exam_date: str | None) -> tuple[tuple, dict[str, object], str]:
    days = (date.fromisoformat(exam_date) - plan_day).days if exam_date else None
    upcoming = days is not None and days >= 0
    signals = {key: _signal(candidate.priority_evidence.get(key), plan_day) for key in SIGNAL_KEYS}
    exam_status = "upcoming" if upcoming else "past" if days is not None else "unset"
    evidence: dict[str, object] = {
        "exam_date": exam_date, "exam_status": exam_status,
        "exam_days_remaining": days if upcoming else None,
        "estimate_source": candidate.estimate_source,
        **signals,
    }
    rank = (0 if upcoming else 1, days if upcoming else 0,
            *(-signals[key] for key in SIGNAL_KEYS), candidate.task_id)
    if upcoming:
        primary = "exam_imminent"
    else:
        primary = next((name for key, name in (
            ("priority", "todo_priority"), ("forgetting_risk", "forgetting_risk"),
            ("mastery_gap", "mastery_gap"), ("coverage_value", "coverage_value"),
        ) if signals[key] > 0), "stable_task_id")
    return rank, evidence, primary


def _decision(candidate: PlanCandidate, code: str, reason: str,
              evidence: Mapping[str, object] | None = None) -> PlanDecision:
    return PlanDecision(candidate.task_id, candidate.subject_id, candidate.title,
                        candidate.estimated_minutes, candidate.estimate_source,
                        code, reason, evidence or {})


def build_budgeted_day_plan(inputs: BudgetPlanInput, collection: CandidateCollection) -> BudgetedDayPlan:
    """Select a deterministic, budget-fit pending list from one day's activity candidates."""
    plan_date = validate_day_budget_date(inputs.plan_date)
    budget = validate_available_minutes(inputs.available_minutes)
    if inputs.as_of_date is not None and validate_day_budget_date(inputs.as_of_date) != plan_date:
        raise ValueError("计划日期与候选证据截止日必须一致")
    if not isinstance(inputs.active_subject_ids, tuple) or any(
        not isinstance(name, str) or not name.strip() for name in inputs.active_subject_ids
    ) or len(set(inputs.active_subject_ids)) != len(inputs.active_subject_ids):
        raise ValueError("活动学科 ID 必须是无重复的非空字符串元组")
    active = set(inputs.active_subject_ids)
    if inputs.subject_scope is not None and inputs.subject_scope not in active:
        raise ValueError("单学科范围必须是活动学科")
    exam_dates = {subject: validate_subject_exam_date(value)
                  for subject, value in inputs.subject_exam_dates.items()}
    plan_day = date.fromisoformat(plan_date)
    completed: list[PlanDecision] = []
    excluded: list[PlanDecision] = []
    pending: list[tuple[tuple, PlanCandidate, dict[str, object], str]] = []
    completed_minutes = 0
    unknown_completed = False
    id_counts: dict[str, int] = {}
    for candidate in collection.candidates:
        id_counts[candidate.task_id] = id_counts.get(candidate.task_id, 0) + 1
    signature_candidates: list[dict[str, object]] = []

    for candidate in sorted(collection.candidates, key=lambda item: (item.task_id, item.subject_id)):
        if id_counts[candidate.task_id] > 1:
            excluded.append(_decision(candidate, "duplicate_task_id", "同日任务 ID 重复，未再次安排"))
            signature_candidates.append({"task_id": candidate.task_id, "rule": "duplicate_task_id"})
            continue
        code: str | None = None
        try:
            valid_identity = candidate.task_id == task_id_for_source(
                candidate.subject_id, candidate.source_kind, candidate.source_id
            )
        except ValueError:
            valid_identity = False
        if not valid_identity:
            code = "invalid_source_identity"
        elif candidate.subject_id not in active:
            code = "subject_archived_or_unknown"
        elif candidate.completion_state not in COMPLETION_STATES:
            code = "invalid_completion_state"
        elif candidate.priority_evidence.get("source_created_date") is not None and (
            date.fromisoformat(validate_day_budget_date(candidate.priority_evidence["source_created_date"])) > plan_day
        ):
            code = "future_source"
        if code is not None:
            excluded.append(_decision(candidate, code, EXCLUSION_LABELS[code]))
            signature_candidates.append({"task_id": candidate.task_id, "rule": code})
            continue

        minutes: int | None = None
        if candidate.estimated_minutes is not None:
            try:
                minutes = validate_estimated_minutes(candidate.estimated_minutes)
                validate_estimate_source(candidate.estimate_source)
            except ValueError:
                code = "invalid_estimate"
        elif candidate.estimate_source is not None:
            code = "invalid_estimate"
        rank, evidence, primary = _ranking(candidate, plan_day, exam_dates.get(candidate.subject_id))
        signature_candidates.append({
            "task_id": candidate.task_id, "subject_id": candidate.subject_id,
            "source_kind": candidate.source_kind, "source_id": candidate.source_id,
            "completion_state": candidate.completion_state,
            "estimated_minutes": minutes, "estimate_source": candidate.estimate_source,
            "rank": rank[:-1], "exam": evidence["exam_date"], "rule": code,
        })
        if candidate.completion_state in COMPLETED_STATES:
            if code is not None or minutes is None:
                unknown_completed = True
                completed.append(_decision(candidate, "completed_occupancy_unknown",
                                           "已完成，预计时间未知；暂停新增待执行安排", evidence))
            else:
                completed_minutes += minutes
                completed.append(_decision(candidate, "completed_consumes_budget",
                                           "已完成，预计时间计入今日预算", evidence))
            continue
        if inputs.subject_scope is not None and candidate.subject_id != inputs.subject_scope:
            excluded.append(_decision(candidate, "outside_subject_scope", "不在当前学科视图", evidence))
        elif code is not None:
            excluded.append(_decision(candidate, code, "预计时间或来源无效", evidence))
        elif minutes is None:
            excluded.append(_decision(candidate, "missing_estimate", "缺预计时间，须先补估时", evidence))
        else:
            pending.append((rank, candidate, evidence, primary))

    for item in collection.unmapped:
        excluded.append(PlanDecision(None, item.subject_id, item.title, None, None,
                                     item.reason, "来源待映射，不能排程"))
    signature_payload = {
        "version": "budgeted_day_plan:v1", "plan_date": plan_date,
        "available_minutes": budget, "active_subject_ids": sorted(active),
        "subject_scope": inputs.subject_scope,
        "exam_dates": sorted(exam_dates.items()),
        "candidates": sorted(signature_candidates, key=lambda item: (item["task_id"], item["rule"] or "")),
        "unmapped": sorted((item.title, item.reason, item.subject_id or "") for item in collection.unmapped),
    }
    signature = hashlib.sha256(json.dumps(signature_payload, ensure_ascii=False,
                                          sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    selected: list[PlanDecision] = []
    remaining = max(0, budget - completed_minutes)
    for _rank, candidate, evidence, primary in sorted(pending, key=lambda item: item[0]):
        if unknown_completed:
            excluded.append(_decision(candidate, "completed_occupancy_unknown",
                                      "已完成任务占用未知，补估时前暂停安排", evidence))
        elif candidate.estimated_minutes > remaining:
            excluded.append(_decision(candidate, "budget_insufficient",
                                      f"剩余 {remaining} 分钟，不足预计 {candidate.estimated_minutes} 分钟", evidence))
        else:
            remaining -= candidate.estimated_minutes
            exam_note = ("考试日期已过；" if evidence["exam_status"] == "past" else
                         "未设考试日期；" if evidence["exam_status"] == "unset" else "")
            selected.append(_decision(candidate, primary,
                                      f"{exam_note}{PRIMARY_LABELS[primary]}；预计 {candidate.estimated_minutes} 分钟，适合剩余预算", evidence))
    planned = completed_minutes + sum(item.estimated_minutes for item in selected)
    return BudgetedDayPlan(
        selected=tuple(selected), excluded=tuple(excluded), completed=tuple(completed),
        planned_minutes=planned, remaining_minutes=max(0, budget - planned),
        over_budget_completed_minutes=max(0, completed_minutes - budget),
        input_signature=signature, completed_occupancy_unknown=unknown_completed,
    )
