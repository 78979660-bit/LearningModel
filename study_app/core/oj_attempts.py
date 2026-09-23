from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass

from study_app.core.oj_identity import validate_problem_key


RESULTS = (
    "accepted",
    "partial",
    "wrong",
    "runtime_error",
    "time_limit",
    "memory_limit",
    "compile_error",
    "abandoned",
)
INDEPENDENCE_LEVELS = ("independent", "guided", "copied")
HINT_LEVELS = ("none", "concept", "pseudocode", "solution")
ERROR_TYPES = (
    "none",
    "misunderstood",
    "algorithm",
    "implementation",
    "edge_case",
    "complexity",
    "syntax",
    "runtime",
    "unknown",
)


@dataclass(frozen=True)
class OJAttemptInput:
    problem_key: str
    attempted_at: str
    result: str
    duration_seconds: int
    independence: str
    hint_level: str
    error_type: str
    notes: str = ""
    source_attempt_key: str | None = None


@dataclass(frozen=True)
class OJAttempt(OJAttemptInput):
    attempt_id: int = 0


def _enum(value: object, allowed: tuple[str, ...], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} 必须是 {list(allowed)} 之一：{value!r}")
    return value


def normalize_attempted_at(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("attempted_at 必须是带时区的 ISO-8601 字符串")
    raw = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"attempted_at 不是有效 ISO-8601 时间：{value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("attempted_at 必须显式包含时区偏移")
    utc = parsed.astimezone(dt.timezone.utc)
    return utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_duration_seconds(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"duration_seconds 必须是正整数：{value!r}")
    if not math.isfinite(float(value)):
        raise ValueError("duration_seconds 必须是有限数")
    return value


def normalize_notes(value: object) -> str:
    if not isinstance(value, str) or len(value.strip()) > 2000:
        raise ValueError("notes 必须是不超过 2000 位的字符串")
    return value.strip()


def normalize_source_attempt_key(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("source_attempt_key 必须是字符串或 null")
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("source_attempt_key 必须是 1-200 位非空字符串")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("source_attempt_key 不得包含控制字符")
    return normalized


def make_oj_attempt_input(
    *,
    problem_key: object,
    attempted_at: object,
    result: object,
    duration_seconds: object,
    independence: object,
    hint_level: object,
    error_type: object,
    notes: object = "",
    source_attempt_key: object = None,
) -> OJAttemptInput:
    valid_result = _enum(result, RESULTS, "result")
    valid_error = _enum(error_type, ERROR_TYPES, "error_type")
    if valid_result == "accepted" and valid_error != "none":
        raise ValueError("accepted 提交的 error_type 必须为 none")
    if valid_result != "accepted" and valid_error == "none":
        raise ValueError("非 accepted 提交的 error_type 不得为 none")
    return OJAttemptInput(
        problem_key=validate_problem_key(problem_key),
        attempted_at=normalize_attempted_at(attempted_at),
        result=valid_result,
        duration_seconds=validate_duration_seconds(duration_seconds),
        independence=_enum(independence, INDEPENDENCE_LEVELS, "independence"),
        hint_level=_enum(hint_level, HINT_LEVELS, "hint_level"),
        error_type=valid_error,
        notes=normalize_notes(notes),
        source_attempt_key=normalize_source_attempt_key(source_attempt_key),
    )


def attempt_payload_sha256(value: OJAttemptInput) -> str:
    payload = json.dumps(
        asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
