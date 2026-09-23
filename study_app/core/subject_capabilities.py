from __future__ import annotations

from dataclasses import dataclass


CAPABILITY_KEYS = frozenset(
    {
        "study_plan",
        "generic_practice",
        "local_practice_pdf",
        "mock_exam",
        "oj",
        "formula_rendering",
        "weekly_source_collection",
    }
)


def validate_capability_key(value: object) -> str:
    if not isinstance(value, str) or value not in CAPABILITY_KEYS:
        raise ValueError(f"未知学科能力：{value!r}")
    return value


@dataclass(frozen=True)
class CapabilityAvailability:
    capability_key: str
    declared_supported: bool
    environment_ready: bool
    lifecycle_allowed: bool
    diagnostics: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return (
            self.declared_supported
            and self.environment_ready
            and self.lifecycle_allowed
        )
