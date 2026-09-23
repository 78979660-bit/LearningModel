from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


@dataclass(frozen=True)
class StudyDayBudget:
    plan_date: str
    available_minutes: int


def validate_day_budget_date(value: object) -> str:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise ValueError("plan_date 必须是 YYYY-MM-DD 格式的真实日期")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("plan_date 必须是真实日历日期") from error
    if parsed.isoformat() != value:
        raise ValueError("plan_date 必须是 YYYY-MM-DD 格式的真实日期")
    return value


def validate_available_minutes(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 1440:
        raise ValueError("available_minutes 必须是 0～1440 的整数")
    return value


def validate_day_budget_input(plan_date: object, available_minutes: object) -> StudyDayBudget:
    return StudyDayBudget(
        plan_date=validate_day_budget_date(plan_date),
        available_minutes=validate_available_minutes(available_minutes),
    )


def validate_subject_exam_date(value: object) -> str | None:
    """An empty exam date is unknown; a past real date remains valid history."""
    if value is None or value == "":
        return None
    return validate_day_budget_date(value)
