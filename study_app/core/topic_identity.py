from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


TOPIC_IDENTITY_VERSION = "topic-identity-v1"
TOPIC_KEY_PREFIX = "topic:v1:"
_TOPIC_KEY_PATTERN = re.compile(r"topic:v1:[0-9a-f]{64}")


@dataclass(frozen=True)
class TopicIdentity:
    topic_key: str
    topic_id: int
    subject_name: str
    module_name: str
    topic_name: str
    identity_version: str = TOPIC_IDENTITY_VERSION


def validate_topic_row_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"topic_id 必须是正整数：{value!r}")
    return value


def topic_key_for_id(topic_id: object, generation: object = 0) -> str:
    """Create the deterministic bootstrap key for one persisted SQLite topic row."""
    valid_id = validate_topic_row_id(topic_id)
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
    ):
        raise ValueError(f"generation 必须是非负整数：{generation!r}")
    digest = hashlib.sha256(
        f"personal-learning-os/topic/{valid_id}/generation/{generation}".encode("utf-8")
    ).hexdigest()
    return TOPIC_KEY_PREFIX + digest


def validate_topic_key(value: object) -> str:
    if not isinstance(value, str) or not _TOPIC_KEY_PATTERN.fullmatch(value):
        raise ValueError(f"topic_key 格式无效：{value!r}")
    return value


def make_topic_identity(
    *,
    topic_key: object,
    topic_id: object,
    subject_name: object,
    module_name: object,
    topic_name: object,
    identity_version: object,
) -> TopicIdentity:
    valid_key = validate_topic_key(topic_key)
    valid_id = validate_topic_row_id(topic_id)
    if identity_version != TOPIC_IDENTITY_VERSION:
        raise ValueError(f"不支持的知识点身份版本：{identity_version!r}")
    names = (subject_name, module_name, topic_name)
    if any(not isinstance(item, str) or not item.strip() for item in names):
        raise ValueError("知识点身份缺少学科、模块或知识点名称")
    return TopicIdentity(
        topic_key=valid_key,
        topic_id=valid_id,
        subject_name=subject_name.strip(),
        module_name=module_name.strip(),
        topic_name=topic_name.strip(),
    )
