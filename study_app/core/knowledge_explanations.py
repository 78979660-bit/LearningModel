from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from study_app.core.knowledge_alerts import KnowledgeAlert, KnowledgeAlertEvent
from study_app.core.knowledge_states import ClassifiedTopic, F2_RULES_V1


EXPLANATION_VERSION = "knowledge-explanation-v1"
SOURCE_LAYERS = ("rules", "evidence", "model", "workflow")
UNKNOWN_TEXT = "未知／未记录"
_FACT_STATUSES = frozenset({"known", "unknown", "diagnostic"})
_TRACE_COVERAGE = frozenset(
    {"traced", "partial_legacy", "no_recorded_contribution"}
)
_TRACE_RECORD_STATUSES = frozenset(
    {"verified", "missing", "changed_or_unverifiable"}
)


@dataclass(frozen=True)
class ExplanationFact:
    code: str
    label: str
    value: str
    source_reference: str
    status: str = "known"


@dataclass(frozen=True)
class ExplanationLayer:
    name: str
    facts: tuple[ExplanationFact, ...]


@dataclass(frozen=True)
class KnowledgeExplanation:
    topic_key: str
    topic_path: tuple[str, str, str]
    as_of_date: date
    layers: tuple[ExplanationLayer, ...]
    diagnostics: tuple[str, ...]
    explanation_version: str = EXPLANATION_VERSION

    def layer(self, name: str) -> ExplanationLayer:
        for layer in self.layers:
            if layer.name == name:
                return layer
        raise KeyError(name)


def _fact(
    code: str,
    label: str,
    value: object,
    source_reference: str,
    status: str = "known",
) -> ExplanationFact:
    if status not in _FACT_STATUSES:
        raise ValueError(f"未知解释事实状态：{status}")
    text = UNKNOWN_TEXT if value is None else str(value)
    return ExplanationFact(
        code=code,
        label=label,
        value=text,
        source_reference=source_reference,
        status=status,
    )


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _number_text(value: float) -> str:
    if not math.isfinite(value):
        return UNKNOWN_TEXT
    return format(value, ".12g")


def _minimal_text(value: object, *, limit: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _rule_layer(item: ClassifiedTopic) -> ExplanationLayer:
    insight = item.insight
    interval = (
        UNKNOWN_TEXT
        if insight.mastery_interval is None
        else "[" + ", ".join(_number_text(value) for value in insight.mastery_interval) + "]"
    )
    facts = (
        _fact("primary_state", "主状态", item.primary_state, item.rule_version),
        _fact(
            "reason_codes",
            "判定理由码",
            "、".join(item.reason_codes) or UNKNOWN_TEXT,
            item.rule_version,
        ),
        _fact(
            "secondary_flags",
            "次级标签",
            "、".join(item.secondary_flags) or "无",
            item.rule_version,
        ),
        _fact(
            "recommended_action",
            "建议动作",
            item.recommended_action,
            item.rule_version,
        ),
        _fact(
            "recent_failure_rule",
            "近期失败规则",
            (
                f"{F2_RULES_V1.recent_failure_days} 日；失败≤"
                f"{_number_text(F2_RULES_V1.failure_threshold)}；纠正≥"
                f"{_number_text(F2_RULES_V1.correction_threshold)}"
            ),
            F2_RULES_V1.rule_version,
        ),
        _fact(
            "evidence_threshold",
            "证据置信阈值",
            _number_text(F2_RULES_V1.evidence_confidence_threshold),
            F2_RULES_V1.rule_version,
        ),
        _fact(
            "mastery_interval_rule",
            "掌握区间规则",
            f"{interval}（解释性区间，不是统计置信区间）",
            insight.mastery_interval_rule_version,
            "unknown" if insight.mastery_interval is None else "known",
        ),
    )
    return ExplanationLayer("rules", facts)


def _trace_facts(
    mastery_trace: Mapping[str, object] | None,
    diagnostics: list[str],
) -> tuple[ExplanationFact, ...]:
    if not isinstance(mastery_trace, Mapping):
        diagnostics.append("mastery_trace_missing_or_invalid")
        return (
            _fact(
                "trace_coverage",
                "掌握贡献追溯覆盖",
                None,
                "mastery-evidence-trace-v1",
                "unknown",
            ),
            _fact(
                "trace_contributions",
                "可验证贡献",
                None,
                "mastery-evidence-trace-v1",
                "unknown",
            ),
        )

    coverage = mastery_trace.get("coverage")
    if coverage not in _TRACE_COVERAGE:
        diagnostics.append("mastery_trace_coverage_invalid")
        coverage_text = UNKNOWN_TEXT
        coverage_status = "diagnostic"
    else:
        coverage_text = str(coverage)
        coverage_status = "known"
    legacy_gap = mastery_trace.get("legacy_provenance_gap")
    if legacy_gap is True:
        diagnostics.append("legacy_mastery_provenance_gap")
    elif legacy_gap is not False:
        diagnostics.append("legacy_gap_flag_invalid")

    raw_contributions = mastery_trace.get("contributions")
    summaries: list[str] = []
    if not isinstance(raw_contributions, list):
        diagnostics.append("mastery_trace_contributions_invalid")
    else:
        for index, contribution in enumerate(raw_contributions):
            if not isinstance(contribution, Mapping):
                diagnostics.append(f"mastery_trace_contribution_invalid:{index}")
                continue
            record_id = contribution.get("record_id")
            record_status = contribution.get("record_status")
            if record_status not in _TRACE_RECORD_STATUSES:
                diagnostics.append(f"mastery_trace_record_status_invalid:{index}")
                record_status = UNKNOWN_TEXT
            old_mastery = contribution.get("old_mastery")
            new_mastery = contribution.get("new_mastery")
            transition = ""
            if isinstance(old_mastery, (int, float)) and isinstance(
                new_mastery, (int, float)
            ):
                transition = (
                    f", {_number_text(float(old_mastery))}→"
                    f"{_number_text(float(new_mastery))}"
                )
            summaries.append(f"record={record_id!s}, {record_status}{transition}")
    contribution_text = "；".join(summaries) if summaries else UNKNOWN_TEXT
    return (
        _fact(
            "trace_coverage",
            "掌握贡献追溯覆盖",
            coverage_text,
            "mastery-evidence-trace-v1",
            coverage_status,
        ),
        _fact(
            "trace_contributions",
            "可验证贡献",
            contribution_text,
            "mastery-evidence-trace-v1",
            "known" if summaries else "unknown",
        ),
    )


def _evidence_layer(
    item: ClassifiedTopic,
    prerequisite_states: Mapping[str, ClassifiedTopic],
    mastery_trace: Mapping[str, object] | None,
    diagnostics: list[str],
) -> ExplanationLayer:
    insight = item.insight
    error = insight.recent_error
    if error is None:
        error_title = "无可证明的最近错误"
        error_date = None
        error_correctness = None
        error_source = "normalized observations"
        error_cause = None
    else:
        error_title = _minimal_text(error.title)
        error_date = error.occurred_on.isoformat()
        error_correctness = _number_text(error.correctness)
        error_source = error.result_source
        error_cause = _minimal_text(error.error_cause)
        if error_title is None:
            diagnostics.append("recent_error_title_missing")
        if error.error_cause is None or error_cause is None:
            diagnostics.append("recent_error_cause_unrecorded")

    prerequisite_values: list[str] = []
    for key in insight.direct_prerequisite_keys:
        if key in insight.unresolved_prerequisite_keys:
            prerequisite_values.append(f"{key}: {UNKNOWN_TEXT}")
            diagnostics.append(f"prerequisite_unresolved:{key}")
            continue
        prerequisite = prerequisite_states.get(key)
        if not isinstance(prerequisite, ClassifiedTopic):
            prerequisite_values.append(f"{key}: {UNKNOWN_TEXT}")
            diagnostics.append(f"prerequisite_state_missing:{key}")
            continue
        suffix = "（封存历史）" if prerequisite.insight.is_archived else ""
        prerequisite_values.append(f"{key}: {prerequisite.primary_state}{suffix}")

    facts = [
        _fact(
            "observation_count",
            "有效作答数",
            insight.observation_count,
            insight.evidence_version,
        ),
        _fact(
            "exercise_count",
            "独立练习数",
            insight.exercise_count,
            insight.evidence_version,
        ),
        _fact(
            "evidence_confidence",
            "证据置信度",
            _number_text(insight.evidence_confidence),
            insight.evidence_version,
        ),
        _fact(
            "last_observation_date",
            "最后观察日",
            _date_text(insight.last_observation_date),
            insight.evidence_version,
            "unknown" if insight.last_observation_date is None else "known",
        ),
        _fact(
            "recent_error_title",
            "最近错误题目摘要",
            error_title,
            error_source,
            "unknown" if error_title is None else "known",
        ),
        _fact(
            "recent_error_date",
            "最近错误日期",
            error_date,
            error_source,
            "unknown" if error_date is None else "known",
        ),
        _fact(
            "recent_error_correctness",
            "最近错误正确度",
            error_correctness,
            error_source,
            "unknown" if error_correctness is None else "known",
        ),
        _fact(
            "recent_error_cause",
            "错误原因",
            error_cause,
            error_source,
            "unknown" if error_cause is None else "known",
        ),
        _fact(
            "prerequisite_states",
            "直接先修状态",
            "；".join(prerequisite_values) if prerequisite_values else "未配置先修关系",
            "explicit prerequisite graph",
            "unknown" if any(UNKNOWN_TEXT in value for value in prerequisite_values) else "known",
        ),
        _fact(
            "records_version",
            "记录版本",
            insight.records_version,
            insight.records_version,
        ),
    ]
    facts.extend(_trace_facts(mastery_trace, diagnostics))
    return ExplanationLayer("evidence", tuple(facts))


def _model_layer(item: ClassifiedTopic) -> ExplanationLayer:
    insight = item.insight
    return ExplanationLayer(
        "model",
        (
            _fact(
                "mastery_point",
                "掌握点值",
                _number_text(insight.mastery_point),
                insight.mastery_source,
            ),
            _fact(
                "model_version",
                "模型版本",
                insight.model_version,
                insight.model_version,
            ),
            _fact(
                "last_review_date",
                "最后复习日",
                _date_text(insight.last_review_date),
                insight.recommendation_source,
                "unknown" if insight.last_review_date is None else "known",
            ),
            _fact(
                "half_life_days",
                "半衰期（天）",
                _number_text(insight.half_life_days),
                insight.recommendation_source,
            ),
            _fact(
                "recall_probability",
                "当前回忆概率",
                _number_text(insight.recall_probability),
                insight.recommendation_source,
            ),
            _fact(
                "target_recall",
                "目标回忆率",
                _number_text(insight.target_recall),
                insight.recommendation_source,
            ),
            _fact(
                "recommended_date",
                "建议日期",
                _date_text(item.effective_recommended_date),
                insight.recommendation_source,
                "unknown" if item.effective_recommended_date is None else "known",
            ),
            _fact(
                "as_of_date",
                "计算截止日",
                insight.as_of_date.isoformat(),
                insight.rule_version,
            ),
        ),
    )


def _workflow_layer(
    item: ClassifiedTopic,
    alert: KnowledgeAlert | None,
    alert_events: tuple[KnowledgeAlertEvent, ...],
    diagnostics: list[str],
) -> ExplanationLayer:
    if alert is None:
        if alert_events:
            diagnostics.append("workflow_events_without_alert")
        return ExplanationLayer(
            "workflow",
            (
                _fact(
                    "alert_instance",
                    "预警实例",
                    None,
                    "knowledge_alerts",
                    "unknown",
                ),
                _fact(
                    "event_history",
                    "工作流事件",
                    None,
                    "knowledge_alert_events",
                    "unknown",
                ),
            ),
        )
    if not isinstance(alert, KnowledgeAlert):
        diagnostics.append("alert_source_invalid")
        return _workflow_layer(item, None, (), diagnostics)
    if alert.topic_key != item.insight.topic_key:
        diagnostics.append("alert_topic_mismatch")
        return _workflow_layer(item, None, (), diagnostics)

    summaries: list[str] = []
    for index, event in enumerate(alert_events):
        if not isinstance(event, KnowledgeAlertEvent):
            diagnostics.append(f"workflow_event_invalid:{index}")
            continue
        if event.alert_id != alert.id:
            diagnostics.append(f"workflow_event_alert_mismatch:{index}")
            continue
        summaries.append(
            f"{event.effective_date.isoformat()} {event.event_type} "
            f"{event.from_status or 'none'}→{event.to_status} actor={event.actor}"
        )
    return ExplanationLayer(
        "workflow",
        (
            _fact("alert_type", "预警类型", alert.alert_type, alert.fingerprint),
            _fact("alert_status", "预警状态", alert.status, alert.fingerprint),
            _fact(
                "alert_rule_version",
                "预警规则版本",
                alert.rule_version,
                alert.fingerprint,
            ),
            _fact(
                "alert_evidence_version",
                "预警证据版本",
                alert.evidence_version,
                alert.fingerprint,
            ),
            _fact(
                "snoozed_until",
                "延后到期日",
                _date_text(alert.snoozed_until),
                alert.fingerprint,
                "unknown" if alert.snoozed_until is None else "known",
            ),
            _fact(
                "handled_at",
                "处理日期",
                alert.handled_at,
                alert.fingerprint,
                "unknown" if alert.handled_at is None else "known",
            ),
            _fact(
                "event_history",
                "工作流事件",
                "；".join(summaries) if summaries else None,
                "knowledge_alert_events",
                "known" if summaries else "unknown",
            ),
        ),
    )


def explain_knowledge_topic(
    classified_topic: ClassifiedTopic,
    prerequisite_states: Mapping[str, ClassifiedTopic],
    mastery_trace: Mapping[str, object] | None,
    *,
    alert: KnowledgeAlert | None = None,
    alert_events: tuple[KnowledgeAlertEvent, ...] = (),
) -> KnowledgeExplanation:
    """Explain frozen inputs without database, filesystem, LLM, or mutation side effects."""
    if not isinstance(classified_topic, ClassifiedTopic):
        raise ValueError("classified_topic 必须是 ClassifiedTopic")
    if not isinstance(prerequisite_states, Mapping):
        raise ValueError("prerequisite_states 必须是映射")
    if not isinstance(alert_events, tuple):
        raise ValueError("alert_events 必须是 tuple")

    diagnostics: list[str] = []
    layers = (
        _rule_layer(classified_topic),
        _evidence_layer(
            classified_topic,
            prerequisite_states,
            mastery_trace,
            diagnostics,
        ),
        _model_layer(classified_topic),
        _workflow_layer(
            classified_topic,
            alert,
            alert_events,
            diagnostics,
        ),
    )
    if tuple(layer.name for layer in layers) != SOURCE_LAYERS:
        raise RuntimeError("解释来源层顺序损坏")
    insight = classified_topic.insight
    return KnowledgeExplanation(
        topic_key=insight.topic_key,
        topic_path=(insight.subject_name, insight.module_name, insight.topic_name),
        as_of_date=insight.as_of_date,
        layers=layers,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )
