from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from study_app.core.local_practice_candidates import CandidatePool, LocalPracticeCandidate
from study_app.core.local_practice_spec import LocalPracticePaperSpec


class InsufficientPracticeCandidatesError(ValueError):
    """Raised before output creation when the eligible local bank is too small."""


_BAND_ORDER = ("foundation", "core", "challenge")


def requested_distribution(question_count: int) -> dict[str, int]:
    edge = question_count // 4
    return {
        "foundation": edge,
        "core": question_count - (2 * edge),
        "challenge": edge,
    }


def difficulty_band(difficulty: float, target: float) -> str:
    if difficulty < target - 7.5:
        return "foundation"
    if difficulty > target + 7.5:
        return "challenge"
    return "core"


@dataclass(frozen=True)
class ComposedPracticePaper:
    spec: LocalPracticePaperSpec
    paper_id: str
    bank_snapshot_sha256: str
    questions: tuple[LocalPracticeCandidate, ...]
    requested_distribution: dict[str, int]
    actual_distribution: dict[str, int]
    warnings: tuple[str, ...]
    candidate_stats: dict[str, int]

    def manifest_core(self) -> dict[str, Any]:
        return {
            "actual_distribution": dict(self.actual_distribution),
            "bank_snapshot_sha256": self.bank_snapshot_sha256,
            "candidate_stats": dict(self.candidate_stats),
            "paper_id": self.paper_id,
            "questions": [
                {
                    "difficulty": int(item.difficulty) if item.difficulty.is_integer() else item.difficulty,
                    "problem_key": item.problem_key,
                    "template_id": item.template_id,
                    "title": item.title,
                }
                for item in self.questions
            ],
            "requested_distribution": dict(self.requested_distribution),
            "request": self.spec.canonical_dict(),
            "warnings": list(self.warnings),
        }


def _tie_break(seed: str, purpose: str, candidate: LocalPracticeCandidate) -> str:
    return hashlib.sha256(f"{seed}\n{purpose}\n{candidate.problem_key}".encode("utf-8")).hexdigest()


def compose_local_practice_paper(
    spec: LocalPracticePaperSpec,
    pool: CandidatePool,
) -> ComposedPracticePaper:
    if len(pool.candidates) < spec.question_count:
        missing = pool.stats.get("excluded_missing_answer", 0)
        placeholders = pool.stats.get("excluded_placeholder_answer", 0)
        raise InsufficientPracticeCandidatesError(
            f"本地题库合格题仅 {len(pool.candidates)} 道，请求 {spec.question_count} 道；"
            f"空答案排除 {missing} 道，占位答案排除 {placeholders} 道。"
        )

    target = spec.target_difficulty
    quotas = requested_distribution(spec.question_count)
    seed = f"{spec.canonical_json()}\n{pool.snapshot_sha256}"
    grouped: dict[str, list[LocalPracticeCandidate]] = {band: [] for band in _BAND_ORDER}
    for candidate in pool.candidates:
        grouped[difficulty_band(candidate.difficulty, target)].append(candidate)
    for band in _BAND_ORDER:
        grouped[band].sort(
            key=lambda item: (
                abs(item.difficulty - target),
                _tie_break(seed, f"band:{band}", item),
            )
        )

    selected: list[LocalPracticeCandidate] = []
    warnings: list[str] = []
    for band in _BAND_ORDER:
        selected.extend(grouped[band][: quotas[band]])
        if len(grouped[band]) < quotas[band]:
            warnings.append(
                f"{band} 档目标 {quotas[band]} 道，初选仅 {len(grouped[band])} 道；已从其他档确定性补位。"
            )

    selected_keys = {item.problem_key for item in selected}
    remaining = [item for item in pool.candidates if item.problem_key not in selected_keys]
    remaining.sort(
        key=lambda item: (
            abs(item.difficulty - target),
            _tie_break(seed, "fallback", item),
        )
    )
    selected.extend(remaining[: spec.question_count - len(selected)])
    selected.sort(key=lambda item: _tie_break(seed, "question-order", item))

    actual = {band: 0 for band in _BAND_ORDER}
    for candidate in selected:
        actual[difficulty_band(candidate.difficulty, target)] += 1
    identity = {
        "bank_snapshot_sha256": pool.snapshot_sha256,
        "question_keys_in_order": [item.problem_key for item in selected],
        "request": spec.canonical_dict(),
    }
    paper_id = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ComposedPracticePaper(
        spec=spec,
        paper_id=paper_id,
        bank_snapshot_sha256=pool.snapshot_sha256,
        questions=tuple(selected),
        requested_distribution=quotas,
        actual_distribution=actual,
        warnings=tuple(warnings),
        candidate_stats=dict(pool.stats),
    )
