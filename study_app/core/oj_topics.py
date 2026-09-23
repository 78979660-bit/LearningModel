from __future__ import annotations

from dataclasses import dataclass

from study_app.core.oj_identity import validate_problem_key
from study_app.core.topic_identity import validate_topic_key


MAPPING_SOURCES = frozenset({"manual", "offline_import"})


@dataclass(frozen=True)
class OJTopicMapping:
    problem_key: str
    topic_key: str
    mapping_source: str
    note: str = ""


def validate_mapping_source(value: object) -> str:
    if not isinstance(value, str) or value not in MAPPING_SOURCES:
        raise ValueError(
            f"mapping_source 必须是 {sorted(MAPPING_SOURCES)} 之一：{value!r}"
        )
    return value


def validate_mapping_note(value: object) -> str:
    if not isinstance(value, str) or len(value.strip()) > 500:
        raise ValueError("mapping note 必须是不超过 500 位的字符串")
    return value.strip()


def normalize_topic_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("topic_keys 必须是字符串列表")
    normalized = tuple(validate_topic_key(item) for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValueError("topic_keys 不得重复")
    return tuple(sorted(normalized))


def make_oj_topic_mapping(
    *,
    problem_key: object,
    topic_key: object,
    mapping_source: object,
    note: object = "",
) -> OJTopicMapping:
    return OJTopicMapping(
        problem_key=validate_problem_key(problem_key),
        topic_key=validate_topic_key(topic_key),
        mapping_source=validate_mapping_source(mapping_source),
        note=validate_mapping_note(note),
    )
