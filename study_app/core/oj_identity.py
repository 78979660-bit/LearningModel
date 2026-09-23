from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


OJ_IDENTITY_VERSION = "oj-problem-v1"
PROBLEM_KEY_PREFIX = "ojp:v1:"
_PROBLEM_KEY_PATTERN = re.compile(r"ojp:v1:[0-9a-f]{64}")
_SOURCE_KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


@dataclass(frozen=True)
class OJProblem:
    problem_key: str
    source_key: str
    external_problem_key: str
    title: str
    source_url: str = ""
    identity_version: str = OJ_IDENTITY_VERSION


def normalize_source_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"source_key 必须是字符串：{value!r}")
    normalized = value.strip().lower()
    if not _SOURCE_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "source_key 必须为 1-64 位小写字母、数字、点、下划线或连字符，"
            f"且须以字母或数字开头：{value!r}"
        )
    return normalized


def normalize_external_problem_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"external_problem_key 必须是字符串：{value!r}")
    normalized = value.strip()
    if not normalized or len(normalized) > 160:
        raise ValueError("external_problem_key 必须是 1-160 位非空字符串")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("external_problem_key 不得包含控制字符")
    return normalized


def validate_problem_key(value: object) -> str:
    if not isinstance(value, str) or not _PROBLEM_KEY_PATTERN.fullmatch(value):
        raise ValueError(f"problem_key 格式无效：{value!r}")
    return value


def problem_key_for(source_key: object, external_problem_key: object) -> str:
    source = normalize_source_key(source_key)
    external = normalize_external_problem_key(external_problem_key)
    digest = hashlib.sha256(
        f"personal-learning-os/oj-problem/v1/{source}/{external}".encode("utf-8")
    ).hexdigest()
    return PROBLEM_KEY_PREFIX + digest


def make_oj_problem(
    *,
    problem_key: object,
    source_key: object,
    external_problem_key: object,
    title: object,
    source_url: object = "",
    identity_version: object = OJ_IDENTITY_VERSION,
) -> OJProblem:
    valid_key = validate_problem_key(problem_key)
    source = normalize_source_key(source_key)
    external = normalize_external_problem_key(external_problem_key)
    if valid_key != problem_key_for(source, external):
        raise ValueError("problem_key 与来源／外部题号不一致")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 300:
        raise ValueError("title 必须是 1-300 位非空字符串")
    if not isinstance(source_url, str) or len(source_url.strip()) > 1000:
        raise ValueError("source_url 必须是不超过 1000 位的字符串")
    if identity_version != OJ_IDENTITY_VERSION:
        raise ValueError(f"不支持的 OJ 题目身份版本：{identity_version!r}")
    return OJProblem(
        problem_key=valid_key,
        source_key=source,
        external_problem_key=external,
        title=title.strip(),
        source_url=source_url.strip(),
    )
