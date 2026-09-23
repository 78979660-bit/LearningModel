from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from study_app.core.knowledge_states import (
    ACTIONABLE_ALERT_STATES,
    ClassifiedTopic,
)


ALERT_TYPES = tuple(sorted(ACTIONABLE_ALERT_STATES))
ALERT_STATUSES = ("active", "snoozed", "handled", "resolved")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KnowledgeAlertSnapshot:
    fingerprint: str
    topic_key: str
    alert_type: str
    rule_version: str
    evidence_version: str
    condition_cycle: str
    as_of_date: date
    recommended_date: date | None
    priority: float
    snapshot: dict[str, object]


@dataclass(frozen=True)
class KnowledgeAlert:
    id: int
    fingerprint: str
    topic_key: str
    alert_type: str
    status: str
    rule_version: str
    evidence_version: str
    condition_cycle: str
    as_of_date: date
    recommended_date: date | None
    priority: float
    snapshot: dict[str, object]
    snoozed_until: date | None
    handled_at: str | None
    resolved_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KnowledgeAlertEvent:
    id: int
    alert_id: int
    event_type: str
    from_status: str | None
    to_status: str
    effective_date: date
    actor: str
    detail: dict[str, object]
    created_at: str


@dataclass(frozen=True)
class AlertReconcileResult:
    created_ids: tuple[int, ...]
    reactivated_ids: tuple[int, ...]
    resolved_ids: tuple[int, ...]
    unchanged_ids: tuple[int, ...]


def _condition_cycle(item: ClassifiedTopic) -> str:
    insight = item.insight
    if item.primary_state == "recent_failure":
        error = insight.recent_error
        if error is None:
            raise ValueError("recent_failure 必须包含失败观察身份")
        identity = {
            "record_id": error.record_id,
            "occurred_on": error.occurred_on.isoformat(),
            "result_source": error.result_source,
        }
        return f"failed-observation:{_digest(identity)}"
    if item.primary_state == "insufficient_evidence":
        return f"evidence:{insight.evidence_version}"
    if item.primary_state == "overdue_review":
        if item.effective_recommended_date is None:
            raise ValueError("overdue_review 必须包含建议复习日期")
        return (
            f"due:{item.effective_recommended_date.isoformat()}"
            f"|evidence:{insight.evidence_version}"
        )
    raise ValueError(f"状态不产生默认预警：{item.primary_state}")


def _snapshot_payload(item: ClassifiedTopic) -> dict[str, object]:
    insight = item.insight
    recent_error = None
    if insight.recent_error is not None:
        recent_error = {
            "record_id": insight.recent_error.record_id,
            "occurred_on": insight.recent_error.occurred_on.isoformat(),
            "correctness": insight.recent_error.correctness,
            "result_source": insight.recent_error.result_source,
        }
    return {
        "topic": {
            "subject_name": insight.subject_name,
            "module_name": insight.module_name,
            "topic_name": insight.topic_name,
        },
        "classification": {
            "primary_state": item.primary_state,
            "reason_codes": list(item.reason_codes),
            "secondary_flags": list(item.secondary_flags),
            "recommended_action": item.recommended_action,
        },
        "evidence": {
            "observation_count": insight.observation_count,
            "exercise_count": insight.exercise_count,
            "confidence": insight.evidence_confidence,
            "version": insight.evidence_version,
            "recent_error": recent_error,
        },
        "mastery": {
            "point": insight.mastery_point,
            "interval": (
                None
                if insight.mastery_interval is None
                else list(insight.mastery_interval)
            ),
            "source": insight.mastery_source,
        },
        "memory": {
            "recall_probability": insight.recall_probability,
            "target_recall": insight.target_recall,
            "recommended_date": (
                None
                if item.effective_recommended_date is None
                else item.effective_recommended_date.isoformat()
            ),
        },
    }


def build_knowledge_alert_snapshots(
    classified_topics: Iterable[ClassifiedTopic],
    as_of_date: date,
) -> tuple[KnowledgeAlertSnapshot, ...]:
    """Build deterministic alert candidates without persistence or side effects."""
    if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
        raise ValueError("as_of_date 必须是 date")
    values = tuple(classified_topics)
    if any(not isinstance(item, ClassifiedTopic) for item in values):
        raise ValueError("classified_topics 只能包含 ClassifiedTopic")

    candidates: list[KnowledgeAlertSnapshot] = []
    seen_topics: set[str] = set()
    for item in values:
        insight = item.insight
        if insight.topic_key in seen_topics:
            raise ValueError(f"classified_topics 包含重复 topic_key：{insight.topic_key}")
        seen_topics.add(insight.topic_key)
        if insight.as_of_date != as_of_date:
            raise ValueError("分类结果的 as_of_date 与协调日期不一致")
        if not item.alert_eligible:
            continue
        if insight.is_archived or item.primary_state not in ALERT_TYPES:
            raise ValueError("alert_eligible 与归档/主状态约束不一致")
        if not math.isfinite(item.alert_priority) or item.alert_priority < 0:
            raise ValueError("预警优先级必须是非负有限数值")

        cycle = _condition_cycle(item)
        identity = {
            "topic_key": insight.topic_key,
            "alert_type": item.primary_state,
            "rule_version": item.rule_version,
            "evidence_version": insight.evidence_version,
            "condition_cycle": cycle,
        }
        candidates.append(
            KnowledgeAlertSnapshot(
                fingerprint=_digest(identity),
                topic_key=insight.topic_key,
                alert_type=item.primary_state,
                rule_version=item.rule_version,
                evidence_version=insight.evidence_version,
                condition_cycle=cycle,
                as_of_date=as_of_date,
                recommended_date=item.effective_recommended_date,
                priority=item.alert_priority,
                snapshot=_snapshot_payload(item),
            )
        )
    return tuple(sorted(candidates, key=lambda value: (value.topic_key, value.alert_type)))


def reconcile_knowledge_alerts(
    classified_topics: Iterable[ClassifiedTopic],
    as_of_date: date,
    connection: sqlite3.Connection,
) -> AlertReconcileResult:
    """Reconcile one classified snapshot; user handle/snooze actions live elsewhere."""
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    snapshots = build_knowledge_alert_snapshots(classified_topics, as_of_date)
    from study_app.data.database import reconcile_knowledge_alert_snapshots

    return reconcile_knowledge_alert_snapshots(snapshots, as_of_date, connection)


def handle_alert(
    alert_id: int,
    effective_date: date,
    connection: sqlite3.Connection,
    *,
    actor: str = "local_user",
) -> KnowledgeAlert:
    """Mark one active alert handled without changing any learning evidence."""
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError("alert_id 必须是正整数")
    if not isinstance(effective_date, date) or isinstance(effective_date, datetime):
        raise ValueError("effective_date 必须是 date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    if (
        not isinstance(actor, str)
        or not 1 <= len(actor) <= 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            for character in actor
        )
    ):
        raise ValueError("actor 必须是 1～64 位本地技术标识")

    from study_app.data.database import handle_knowledge_alert

    return handle_knowledge_alert(
        alert_id,
        effective_date,
        actor,
        connection,
    )


def snooze_alert(
    alert_id: int,
    snoozed_until: date,
    as_of_date: date,
    connection: sqlite3.Connection,
    *,
    actor: str = "local_user",
) -> KnowledgeAlert:
    """Snooze an active or already-snoozed alert without changing its evidence."""
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError("alert_id 必须是正整数")
    for label, value in (
        ("snoozed_until", snoozed_until),
        ("as_of_date", as_of_date),
    ):
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValueError(f"{label} 必须是 date")
    if snoozed_until < as_of_date:
        raise ValueError("延后日期不得早于 as_of_date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    if (
        not isinstance(actor, str)
        or not 1 <= len(actor) <= 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            for character in actor
        )
    ):
        raise ValueError("actor 必须是 1～64 位本地技术标识")

    from study_app.data.database import snooze_knowledge_alert

    return snooze_knowledge_alert(
        alert_id,
        snoozed_until,
        as_of_date,
        actor,
        connection,
    )
