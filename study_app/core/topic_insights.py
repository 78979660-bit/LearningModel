from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Iterable, Mapping

from learning_bkt import group_observations_by_exercise, topic_prior
from learning_memory import topic_matches_text
from study_app.core.computation_context import DashboardComputationContext
from study_app.core.prerequisite_graph import PrerequisiteEdge
from study_app.core.topic_identity import TopicIdentity, validate_topic_key


TOPIC_INSIGHT_RULE_VERSION = "topic-insight-v1"
MASTERY_INTERVAL_RULE_VERSION = "mastery-interval-v1"
NORMALIZED_OBSERVATION_VERSION = "normalized-topic-observation-v1"


@dataclass(frozen=True)
class RecentTopicError:
    record_id: object
    occurred_on: date
    title: str
    correctness: float
    result_source: str
    error_cause: str | None


@dataclass(frozen=True)
class TopicInsight:
    topic_key: str
    topic_id: int
    subject_name: str
    module_name: str
    topic_name: str
    subject_status: str
    course_status: str
    is_archived: bool
    observation_count: int
    exercise_count: int
    evidence_confidence: float
    evidence_version: str
    mastery_point: float
    mastery_source: str
    mastery_interval: tuple[float, float] | None
    mastery_interval_rule_version: str
    recent_error: RecentTopicError | None
    last_observation_date: date | None
    last_observation_correctness: float | None
    last_review_date: date | None
    review_count: int
    half_life_days: float
    recall_probability: float
    target_recall: float
    recommended_date: date | None
    recommendation_source: str
    direct_prerequisite_keys: tuple[str, ...]
    unresolved_prerequisite_keys: tuple[str, ...]
    as_of_date: date
    model_version: str
    records_version: str
    rule_version: str = TOPIC_INSIGHT_RULE_VERSION


def mastery_interval(
    mastery_point: object,
    evidence_confidence: object,
    observation_count: object,
) -> tuple[float, float] | None:
    if isinstance(observation_count, bool) or not isinstance(observation_count, int):
        raise ValueError("observation_count 必须是非负整数")
    if observation_count < 0:
        raise ValueError("observation_count 必须是非负整数")
    if observation_count == 0:
        return None
    values = (mastery_point, evidence_confidence)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("掌握点值与证据置信度必须是数值")
    point = float(mastery_point)
    confidence = float(evidence_confidence)
    if not math.isfinite(point) or not 0 <= point <= 1:
        raise ValueError("掌握点值必须是 0～1 的有限数值")
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("证据置信度必须是 0～1 的有限数值")
    half_width = 0.05 + 0.35 * (1 - confidence)
    return max(0.0, point - half_width), min(1.0, point + half_width)


def recommended_review_date(
    last_review_date: date | None,
    half_life_days: object,
    target_recall: object,
) -> date | None:
    if last_review_date is None:
        return None
    if not isinstance(last_review_date, date):
        raise ValueError("last_review_date 必须是真实日期或 None")
    values = (half_life_days, target_recall)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("半衰期与目标回忆率必须是数值")
    half_life = float(half_life_days)
    target = float(target_recall)
    if not math.isfinite(half_life) or half_life <= 0:
        raise ValueError("半衰期必须是正有限数值")
    if not math.isfinite(target) or not 0 < target <= 1:
        raise ValueError("目标回忆率必须在 (0, 1] 范围内")
    interval_days = math.ceil(half_life * math.log2(1 / target))
    return last_review_date + timedelta(days=max(0, interval_days))


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _version(value: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        candidate = value.get(key)
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return "sha256:" + _canonical_digest(value)


def _registry_values(
    identity_registry: Mapping[str, TopicIdentity] | Iterable[TopicIdentity],
) -> tuple[TopicIdentity, ...]:
    values = (
        tuple(identity_registry.values())
        if isinstance(identity_registry, Mapping)
        else tuple(identity_registry)
    )
    if any(not isinstance(item, TopicIdentity) for item in values):
        raise ValueError("identity_registry 只能包含 TopicIdentity")
    keys = [item.topic_key for item in values]
    paths = [
        (item.subject_name, item.module_name, item.topic_name) for item in values
    ]
    if len(set(keys)) != len(keys) or len(set(paths)) != len(paths):
        raise ValueError("identity_registry 包含重复键或重复知识点路径")
    return tuple(sorted(values, key=lambda item: item.topic_key))


def _model_topics(model: Mapping[str, object]):
    topics: dict[tuple[str, str, str], tuple[dict, dict, dict]] = {}
    for subject in model.get("subjects", []):
        if not isinstance(subject, dict):
            continue
        for module in subject.get("modules", []):
            if not isinstance(module, dict):
                continue
            for topic in module.get("topics", []):
                if not isinstance(topic, dict):
                    continue
                path = (
                    str(subject.get("name") or ""),
                    str(module.get("name") or ""),
                    str(topic.get("name") or ""),
                )
                if not all(path):
                    raise ValueError("模型包含缺少学科、模块或知识点名称的条目")
                if path in topics:
                    raise ValueError("模型包含重复知识点路径：" + " / ".join(path))
                topics[path] = (subject, module, topic)
    return topics


def _error_cause(record: Mapping[str, object], topic_name: str) -> str | None:
    causes = record.get("error_causes")
    if isinstance(causes, str):
        return causes.strip() if topic_matches_text(topic_name, causes) else None
    if not isinstance(causes, list):
        return None
    for item in causes:
        if isinstance(item, str):
            if topic_matches_text(topic_name, item):
                return item.strip() or None
            continue
        if not isinstance(item, dict):
            continue
        topic_text = str(item.get("topic") or "")
        description = str(item.get("description") or "").strip()
        if topic_matches_text(topic_name, topic_text) or topic_matches_text(
            topic_name, description
        ):
            return description or None
    return None


def _recent_error(
    observations: tuple[dict, ...],
    records_by_id: Mapping[object, Mapping[str, object]],
    topic_name: str,
) -> RecentTopicError | None:
    eligible = [
        item
        for item in observations
        if item.get("result_source") != "record_fallback"
        and isinstance(item.get("correctness"), (int, float))
        and not isinstance(item.get("correctness"), bool)
        and float(item["correctness"]) < 1.0
        and isinstance(item.get("date"), date)
    ]
    if not eligible:
        return None
    observation = eligible[-1]
    record = records_by_id.get(observation.get("record_id"), {})
    return RecentTopicError(
        record_id=observation.get("record_id"),
        occurred_on=observation["date"],
        title=str(observation.get("title") or ""),
        correctness=float(observation["correctness"]),
        result_source=str(observation.get("result_source") or "unknown"),
        error_cause=_error_cause(record, topic_name),
    )


def build_topic_insights(
    model: dict,
    records: list[dict],
    as_of_date: date,
    context: DashboardComputationContext | None,
    identity_registry: Mapping[str, TopicIdentity] | Iterable[TopicIdentity],
    prerequisite_graph: Iterable[PrerequisiteEdge] = (),
):
    if not isinstance(as_of_date, date):
        raise ValueError("as_of_date 必须是真实日期")
    if not isinstance(model, dict) or not isinstance(records, list):
        raise ValueError("model 必须是对象且 records 必须是数组")
    if context is None:
        context = DashboardComputationContext(
            model, records, as_of_date, model.get("warning_policy", {})
        )
    if (
        context.as_of_date != as_of_date
        or context.model != model
        or context.records != records
    ):
        raise ValueError("计算上下文与 model、records 或 as_of_date 不一致")

    identities = _registry_values(identity_registry)
    registered_keys = {item.topic_key for item in identities}
    model_topics = _model_topics(model)
    edges = tuple(prerequisite_graph)
    if any(not isinstance(edge, PrerequisiteEdge) for edge in edges):
        raise ValueError("prerequisite_graph 只能包含 PrerequisiteEdge")
    direct: dict[str, set[str]] = {}
    for edge in edges:
        direct.setdefault(edge.topic_key, set()).add(edge.prerequisite_topic_key)

    records_by_id = {
        item.get("id"): item
        for item in records
        if isinstance(item, dict) and item.get("id") is not None
    }
    records_version = "sha256:" + _canonical_digest(records)
    model_version = _version(model, "model_version", "version", "model_name")
    result: dict[str, TopicInsight] = {}

    for identity in identities:
        validate_topic_key(identity.topic_key)
        path = (identity.subject_name, identity.module_name, identity.topic_name)
        if path not in model_topics:
            raise ValueError("registry 知识点路径未出现在模型中：" + " / ".join(path))
        subject, module, topic = model_topics[path]
        observations = context.observations(*path[:2], topic)
        bkt = context.bkt_state(*path[:2], topic)
        memory = context.memory_state(*path[:2], topic)

        observation_count = len(observations)
        exercise_count = len(group_observations_by_exercise(observations))
        confidence = float((bkt or {}).get("evidence_confidence", 0.0))
        if observation_count:
            if not bkt:
                raise ValueError("存在有效观察但 BKT 未启用，无法生成掌握洞察")
            mastery_point = float(bkt["mastery_probability"])
            mastery_source = "bkt_observed"
        else:
            mastery_point = float(topic_prior(topic, context.policy))
            mastery_source = "baseline_prior"

        last_observation = observations[-1] if observations else None
        last_review = memory.get("last_review")
        half_life = float(memory["half_life"])
        target_recall = float(memory["target_recall"])
        recommendation = (
            recommended_review_date(last_review, half_life, target_recall)
            if observation_count
            else None
        )
        prerequisite_keys = tuple(sorted(direct.get(identity.topic_key, ())))
        unresolved = tuple(
            item for item in prerequisite_keys if item not in registered_keys
        )
        subject_status = str(subject.get("status") or "active")
        course_status = str(
            topic.get("status") or module.get("status") or "unknown"
        )

        evidence_payload = [
            {
                "record_id": item.get("record_id"),
                "date": item.get("date"),
                "correctness": item.get("correctness"),
                "result_source": item.get("result_source"),
                "title": item.get("title"),
            }
            for item in observations
        ]
        evidence_version = (
            NORMALIZED_OBSERVATION_VERSION + ":sha256:" + _canonical_digest(evidence_payload)
        )
        result[identity.topic_key] = TopicInsight(
            topic_key=identity.topic_key,
            topic_id=identity.topic_id,
            subject_name=identity.subject_name,
            module_name=identity.module_name,
            topic_name=identity.topic_name,
            subject_status=subject_status,
            course_status=course_status,
            is_archived=subject_status == "archived",
            observation_count=observation_count,
            exercise_count=exercise_count,
            evidence_confidence=confidence,
            evidence_version=evidence_version,
            mastery_point=mastery_point,
            mastery_source=mastery_source,
            mastery_interval=mastery_interval(
                mastery_point, confidence, observation_count
            ),
            mastery_interval_rule_version=MASTERY_INTERVAL_RULE_VERSION,
            recent_error=_recent_error(observations, records_by_id, identity.topic_name),
            last_observation_date=(
                last_observation.get("date") if last_observation else None
            ),
            last_observation_correctness=(
                float(last_observation["correctness"])
                if last_observation is not None else None
            ),
            last_review_date=last_review,
            review_count=int(memory["review_count"]),
            half_life_days=half_life,
            recall_probability=float(memory["recall"]),
            target_recall=target_recall,
            recommended_date=recommendation,
            recommendation_source=(
                "half_life_target_recall" if recommendation is not None else "unavailable"
            ),
            direct_prerequisite_keys=prerequisite_keys,
            unresolved_prerequisite_keys=unresolved,
            as_of_date=as_of_date,
            model_version=model_version,
            records_version=records_version,
        )
    return MappingProxyType(result)
