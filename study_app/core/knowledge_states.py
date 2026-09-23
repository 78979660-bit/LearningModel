from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Iterable, Mapping

from study_app.core.topic_insights import (
    MASTERY_INTERVAL_RULE_VERSION,
    TopicInsight,
)


PRIMARY_STATES = (
    "unlearned",
    "recent_failure",
    "insufficient_evidence",
    "overdue_review",
    "stable",
)
ACTIONABLE_ALERT_STATES = frozenset(
    {"recent_failure", "insufficient_evidence", "overdue_review"}
)
_UNLEARNED_COURSE_STATUSES = frozenset(
    {"not_started", "unlearned", "planned", "未开始", "未学"}
)
_DISPLAY_STATE_RANK = {
    "recent_failure": 0,
    "insufficient_evidence": 1,
    "overdue_review": 2,
    "unlearned": 3,
    "stable": 4,
}


@dataclass(frozen=True)
class KnowledgeStatePolicy:
    rule_version: str
    recent_failure_days: int
    evidence_confidence_threshold: float
    failure_threshold: float
    correction_threshold: float
    mastery_interval_rule_version: str


F2_RULES_V1 = KnowledgeStatePolicy(
    rule_version="knowledge-state-v1",
    recent_failure_days=7,
    evidence_confidence_threshold=0.50,
    failure_threshold=0.50,
    correction_threshold=0.80,
    mastery_interval_rule_version=MASTERY_INTERVAL_RULE_VERSION,
)


@dataclass(frozen=True)
class ClassifiedTopic:
    insight: TopicInsight
    primary_state: str
    reason_codes: tuple[str, ...]
    secondary_flags: tuple[str, ...]
    recommended_action: str
    effective_recommended_date: date | None
    overdue_days: int | None
    alert_priority: float
    alert_eligible: bool
    is_actionable: bool
    sort_key: tuple[int, int, float, str]
    rule_version: str


def _validate_policy(policy: KnowledgeStatePolicy) -> None:
    if not isinstance(policy, KnowledgeStatePolicy):
        raise ValueError("policy 必须是版本化 KnowledgeStatePolicy")
    if policy.recent_failure_days <= 0:
        raise ValueError("近期失败窗口必须为正整数天")
    for label, value in (
        ("证据阈值", policy.evidence_confidence_threshold),
        ("失败阈值", policy.failure_threshold),
        ("纠正阈值", policy.correction_threshold),
    ):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{label}必须是 0～1 的有限数值")
    if policy.correction_threshold < policy.failure_threshold:
        raise ValueError("纠正阈值不得低于失败阈值")
    if policy.mastery_interval_rule_version != MASTERY_INTERVAL_RULE_VERSION:
        raise ValueError("状态策略与掌握区间规则版本不一致")


def _validate_insight(insight: TopicInsight) -> None:
    if not isinstance(insight, TopicInsight):
        raise ValueError("insight 必须是 TopicInsight")
    if insight.observation_count < 0 or insight.exercise_count < 0:
        raise ValueError("观察数与独立练习数不得为负")
    if not math.isfinite(insight.evidence_confidence) or not (
        0 <= insight.evidence_confidence <= 1
    ):
        raise ValueError("证据置信度必须是 0～1 的有限数值")
    for label, value in (
        ("掌握点值", insight.mastery_point),
        ("回忆概率", insight.recall_probability),
        ("目标回忆率", insight.target_recall),
    ):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{label}必须是 0～1 的有限数值")
    if insight.last_observation_date and insight.last_observation_date > insight.as_of_date:
        raise ValueError("最后观察日期晚于 as_of_date")
    if insight.last_review_date and insight.last_review_date > insight.as_of_date:
        raise ValueError("最后复习日期晚于 as_of_date")
    if insight.observation_count == 0 and (
        insight.last_observation_date is not None
        or insight.last_observation_correctness is not None
    ):
        raise ValueError("零观察洞察不得包含最后观察")
    if insight.observation_count > 0 and (
        insight.last_observation_date is None
        or insight.last_observation_correctness is None
    ):
        raise ValueError("有观察洞察必须包含最后观察")
    if insight.last_observation_correctness is not None and (
        not math.isfinite(insight.last_observation_correctness)
        or not 0 <= insight.last_observation_correctness <= 1
    ):
        raise ValueError("最后观察正确度必须是 0～1 的有限数值")
    if insight.recent_error is not None:
        if insight.recent_error.occurred_on > insight.as_of_date:
            raise ValueError("最近错误日期晚于 as_of_date")
        if not math.isfinite(insight.recent_error.correctness) or not (
            0 <= insight.recent_error.correctness <= 1
        ):
            raise ValueError("最近错误正确度必须是 0～1 的有限数值")


def _recent_failure(insight: TopicInsight, policy: KnowledgeStatePolicy) -> bool:
    if (
        insight.recent_error is None
        or insight.last_observation_date is None
        or insight.last_observation_correctness is None
    ):
        return False
    error = insight.recent_error
    age_days = (insight.as_of_date - error.occurred_on).days
    corrected_after_error = (
        insight.last_observation_date >= error.occurred_on
        and insight.last_observation_correctness >= policy.correction_threshold
    )
    return (
        0 <= age_days < policy.recent_failure_days
        and error.correctness <= policy.failure_threshold
        and not corrected_after_error
    )


def _overdue_review(insight: TopicInsight) -> tuple[bool, tuple[str, ...], int | None]:
    if insight.review_count <= 0 or insight.last_review_date is None:
        return False, (), None
    reasons: list[str] = []
    if insight.recall_probability < insight.target_recall:
        reasons.append("recall_below_target")
    overdue_days = None
    if insight.recommended_date is not None and insight.recommended_date < insight.as_of_date:
        reasons.append("recommended_date_passed")
        overdue_days = (insight.as_of_date - insight.recommended_date).days
    return bool(reasons), tuple(reasons), overdue_days


def _prerequisite_satisfied(item: ClassifiedTopic) -> bool:
    insight = item.insight
    return (
        not insight.is_archived
        and item.primary_state == "stable"
        and insight.observation_count > 0
        and insight.mastery_interval is not None
    )


def classify_topic(
    insight: TopicInsight,
    prerequisite_states: Mapping[str, ClassifiedTopic],
    policy: KnowledgeStatePolicy = F2_RULES_V1,
) -> ClassifiedTopic:
    """Classify one immutable insight without mutating evidence or workflow state."""
    _validate_policy(policy)
    _validate_insight(insight)
    if insight.mastery_interval_rule_version != policy.mastery_interval_rule_version:
        raise ValueError("洞察与状态策略的掌握区间规则版本不一致")
    if not isinstance(prerequisite_states, Mapping):
        raise ValueError("prerequisite_states 必须是映射")

    unlearned = (
        insight.observation_count == 0
        and insight.course_status in _UNLEARNED_COURSE_STATUSES
    )
    recent_failure = _recent_failure(insight, policy)
    insufficient = (
        insight.observation_count == 0
        or insight.evidence_confidence < policy.evidence_confidence_threshold
    )
    overdue, overdue_reasons, overdue_days = _overdue_review(insight)

    if unlearned:
        primary_state = "unlearned"
        reasons = ("course_not_started_no_observations",)
        action = "learn_then_assess"
        recommended = None
    elif recent_failure:
        primary_state = "recent_failure"
        reasons = ("recent_uncorrected_failure",)
        action = "retry_failed_problem"
        recommended = insight.as_of_date
    elif insufficient:
        primary_state = "insufficient_evidence"
        reasons = (
            ("no_valid_observations",)
            if insight.observation_count == 0
            else ("evidence_confidence_below_threshold",)
        )
        action = "complete_independent_diagnostic"
        recommended = insight.as_of_date
    elif overdue:
        primary_state = "overdue_review"
        reasons = overdue_reasons
        action = "review_now"
        recommended = insight.recommended_date or insight.as_of_date
    else:
        primary_state = "stable"
        reasons = ("no_action_condition",)
        action = "continue_schedule"
        recommended = insight.recommended_date

    flags: list[str] = []
    if insufficient and primary_state != "insufficient_evidence":
        flags.append("insufficient_evidence")
    if (
        insight.observation_count > 0
        and insight.course_status in _UNLEARNED_COURSE_STATUSES
    ):
        flags.append("course_status_mismatch")
    if overdue and primary_state != "overdue_review":
        flags.append("overdue_review")
    if insight.recent_error is not None and not recent_failure:
        flags.append("historical_error")
    if insight.unresolved_prerequisite_keys:
        flags.append("prerequisite_data_diagnostic")

    pending_prerequisites = False
    archived_prerequisite = False
    missing_prerequisite = False
    for prerequisite_key in insight.direct_prerequisite_keys:
        if prerequisite_key in insight.unresolved_prerequisite_keys:
            missing_prerequisite = True
            continue
        prerequisite = prerequisite_states.get(prerequisite_key)
        if prerequisite is None:
            missing_prerequisite = True
            continue
        if not isinstance(prerequisite, ClassifiedTopic):
            raise ValueError("prerequisite_states 的值必须是 ClassifiedTopic")
        if prerequisite.insight.is_archived:
            archived_prerequisite = True
        elif not _prerequisite_satisfied(prerequisite):
            pending_prerequisites = True
    if pending_prerequisites:
        flags.append("prerequisite_pending")
    if archived_prerequisite:
        flags.append("archived_prerequisite_history")
    if missing_prerequisite and "prerequisite_data_diagnostic" not in flags:
        flags.append("prerequisite_data_diagnostic")
    if insight.is_archived:
        flags.append("archived_subject")

    if primary_state == "recent_failure":
        priority = 1.0 - float(insight.last_observation_correctness or 0.0)
    elif primary_state == "insufficient_evidence":
        priority = 1.0 - insight.evidence_confidence
    elif primary_state == "overdue_review":
        priority = max(0.0, insight.target_recall - insight.recall_probability)
        priority += min(1.0, float(overdue_days or 0) / 30.0)
    else:
        priority = 0.0
    if pending_prerequisites:
        priority += 0.05
    priority = round(priority, 12)

    alert_eligible = (
        not insight.is_archived and primary_state in ACTIONABLE_ALERT_STATES
    )
    is_actionable = not insight.is_archived and (
        primary_state in ACTIONABLE_ALERT_STATES or pending_prerequisites
    )
    date_rank = recommended.toordinal() if recommended else date.max.toordinal()
    sort_key = (
        _DISPLAY_STATE_RANK[primary_state],
        date_rank,
        -priority,
        insight.topic_key,
    )
    return ClassifiedTopic(
        insight=insight,
        primary_state=primary_state,
        reason_codes=reasons,
        secondary_flags=tuple(flags),
        recommended_action=action,
        effective_recommended_date=recommended,
        overdue_days=overdue_days,
        alert_priority=priority,
        alert_eligible=alert_eligible,
        is_actionable=is_actionable,
        sort_key=sort_key,
        rule_version=policy.rule_version,
    )


def classify_topics(
    insights: Mapping[str, TopicInsight] | Iterable[TopicInsight],
    *,
    include_archived: bool = False,
    policy: KnowledgeStatePolicy = F2_RULES_V1,
) -> tuple[ClassifiedTopic, ...]:
    values = tuple(insights.values()) if isinstance(insights, Mapping) else tuple(insights)
    if any(not isinstance(item, TopicInsight) for item in values):
        raise ValueError("insights 只能包含 TopicInsight")
    by_key = {item.topic_key: item for item in values}
    if len(by_key) != len(values):
        raise ValueError("insights 包含重复 topic_key")

    base = MappingProxyType(
        {key: classify_topic(item, {}, policy) for key, item in by_key.items()}
    )
    classified = tuple(
        classify_topic(item, base, policy)
        for item in by_key.values()
        if include_archived or not item.is_archived
    )
    return tuple(sorted(classified, key=lambda item: item.sort_key))


def filter_classified_topics(
    classified_topics: Iterable[ClassifiedTopic],
    *,
    primary_states: Iterable[str] | None = None,
    include_archived: bool = False,
) -> tuple[ClassifiedTopic, ...]:
    values = tuple(classified_topics)
    if any(not isinstance(item, ClassifiedTopic) for item in values):
        raise ValueError("classified_topics 只能包含 ClassifiedTopic")
    allowed = None if primary_states is None else frozenset(primary_states)
    if allowed is not None and not allowed <= set(PRIMARY_STATES):
        raise ValueError("primary_states 包含未知状态")
    return tuple(
        item
        for item in values
        if (include_archived or not item.insight.is_archived)
        and (allowed is None or item.primary_state in allowed)
    )
