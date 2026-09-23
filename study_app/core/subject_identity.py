from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Callable


SUBJECT_IDENTITY_VERSION = "subject-identity-v1"
MODULE_IDENTITY_VERSION = "module-identity-v1"
SUBJECT_KEY_PREFIX = "subject:v1:"
MODULE_KEY_PREFIX = "module:v1:"
_KEY_SUFFIX = re.compile(r"[0-9a-f]{32}")


def normalize_name(value: object, *, label: str = "name") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串：{value!r}")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
    if not normalized or len(normalized) > 200:
        raise ValueError(f"{label} 必须是 1-200 位非空字符串")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{label} 不得包含控制字符")
    return normalized


def normalize_alias(value: object) -> str:
    return normalize_name(value, label="alias").casefold()


def _allocate_key(prefix: str, uuid_factory: Callable[[], uuid.UUID]) -> str:
    value = uuid_factory()
    if not isinstance(value, uuid.UUID):
        raise TypeError("uuid_factory 必须返回 uuid.UUID")
    return prefix + value.hex


def allocate_subject_key(
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> str:
    """Allocate a formal subject identity locally; callers cannot provide a key."""
    return _allocate_key(SUBJECT_KEY_PREFIX, uuid_factory)


def allocate_module_key(
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> str:
    """Allocate a formal module identity locally; callers cannot provide a key."""
    return _allocate_key(MODULE_KEY_PREFIX, uuid_factory)


def _validate_key(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"{label} 格式无效：{value!r}")
    suffix = value[len(prefix) :]
    if not _KEY_SUFFIX.fullmatch(suffix):
        raise ValueError(f"{label} 格式无效：{value!r}")
    return value


def validate_subject_key(value: object) -> str:
    return _validate_key(value, SUBJECT_KEY_PREFIX, "subject_key")


def validate_module_key(value: object) -> str:
    return _validate_key(value, MODULE_KEY_PREFIX, "module_key")


@dataclass(frozen=True)
class SubjectIdentity:
    subject_key: str
    canonical_name: str
    display_name: str
    lifecycle_status: str
    object_version: int


@dataclass(frozen=True)
class ModuleIdentity:
    module_key: str
    subject_key: str
    canonical_name: str
    display_name: str
    object_version: int


def validate_lifecycle_status(value: object) -> str:
    if value not in {"active", "archived"}:
        raise ValueError(f"正式学科状态必须是 active 或 archived：{value!r}")
    return str(value)


def make_subject_identity(row: object) -> SubjectIdentity:
    return SubjectIdentity(
        subject_key=validate_subject_key(row["subject_key"]),
        canonical_name=normalize_name(row["canonical_name"], label="canonical_name"),
        display_name=normalize_name(row["display_name"], label="display_name"),
        lifecycle_status=validate_lifecycle_status(row["lifecycle_status"]),
        object_version=int(row["object_version"]),
    )


def make_module_identity(row: object) -> ModuleIdentity:
    return ModuleIdentity(
        module_key=validate_module_key(row["module_key"]),
        subject_key=validate_subject_key(row["subject_key"]),
        canonical_name=normalize_name(row["canonical_name"], label="canonical_name"),
        display_name=normalize_name(row["display_name"], label="display_name"),
        object_version=int(row["object_version"]),
    )
