from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from study_app.core.oj_attempts import OJAttempt


RESULT_RANK = {
    "accepted": 8,
    "partial": 7,
    "wrong": 6,
    "time_limit": 5,
    "memory_limit": 4,
    "runtime_error": 3,
    "compile_error": 2,
    "abandoned": 1,
}


@dataclass(frozen=True)
class NumberedOJAttempt:
    attempt_number: int
    is_retry: bool
    attempt: OJAttempt


@dataclass(frozen=True)
class OJRetrySummary:
    problem_key: str | None
    attempt_count: int
    first_result: str | None
    latest_result: str | None
    best_result: str | None
    ever_independent_accepted: bool
    attempts_to_first_accepted: int | None
    recent_duration_improvement_seconds: int | None


@dataclass(frozen=True)
class OJProblemHistory:
    attempts: tuple[NumberedOJAttempt, ...]
    summary: OJRetrySummary


def build_oj_history(attempts: Iterable[OJAttempt]) -> OJProblemHistory:
    values = tuple(attempts)
    if any(not isinstance(item, OJAttempt) for item in values):
        raise ValueError("attempts 必须全部为 OJAttempt")
    problem_keys = {item.problem_key for item in values}
    if len(problem_keys) > 1:
        raise ValueError("单题历史不得混入不同 problem_key")
    attempt_ids = [item.attempt_id for item in values]
    if any(item <= 0 for item in attempt_ids) or len(set(attempt_ids)) != len(attempt_ids):
        raise ValueError("attempt_id 必须为唯一正整数")
    ordered = tuple(sorted(values, key=lambda item: (item.attempted_at, item.attempt_id)))
    numbered = tuple(
        NumberedOJAttempt(index, index > 1, attempt)
        for index, attempt in enumerate(ordered, start=1)
    )
    if not ordered:
        return OJProblemHistory(
            attempts=(),
            summary=OJRetrySummary(None, 0, None, None, None, False, None, None),
        )
    first_accepted = next(
        (index for index, item in enumerate(ordered, start=1) if item.result == "accepted"),
        None,
    )
    best = max(ordered, key=lambda item: RESULT_RANK[item.result]).result
    duration_improvement = None
    if len(ordered) >= 2:
        duration_improvement = ordered[-2].duration_seconds - ordered[-1].duration_seconds
    summary = OJRetrySummary(
        problem_key=ordered[0].problem_key,
        attempt_count=len(ordered),
        first_result=ordered[0].result,
        latest_result=ordered[-1].result,
        best_result=best,
        ever_independent_accepted=any(
            item.result == "accepted" and item.independence == "independent"
            for item in ordered
        ),
        attempts_to_first_accepted=first_accepted,
        recent_duration_improvement_seconds=duration_improvement,
    )
    return OJProblemHistory(numbered, summary)
