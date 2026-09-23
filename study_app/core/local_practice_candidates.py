from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable


_PLACEHOLDER_ANSWER_PATTERNS = (
    "题型种子",
    "不提供固定答案",
    "llm应生成",
    "llm 应生成",
    "需要生成答案",
    "待生成答案",
    "待补充答案",
)


@dataclass(frozen=True)
class LocalPracticeCandidate:
    problem_key: str
    template_id: str
    title: str
    statement: str
    answer: str
    difficulty: float
    subject: str
    topic: str
    source_type: str
    source_title: str

    def manifest_dict(self) -> dict[str, Any]:
        difficulty: int | float = self.difficulty
        if self.difficulty.is_integer():
            difficulty = int(self.difficulty)
        return {
            "answer": self.answer,
            "difficulty": difficulty,
            "problem_key": self.problem_key,
            "source_title": self.source_title,
            "source_type": self.source_type,
            "statement": self.statement,
            "subject": self.subject,
            "template_id": self.template_id,
            "title": self.title,
            "topic": self.topic,
        }


@dataclass(frozen=True)
class CandidatePool:
    candidates: tuple[LocalPracticeCandidate, ...]
    stats: dict[str, int]
    snapshot_sha256: str


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_placeholder_answer(answer: str) -> bool:
    normalized = re.sub(r"\s+", " ", answer).lower()
    return any(pattern in normalized for pattern in _PLACEHOLDER_ANSWER_PATTERNS)


def _candidate_from_row(row: dict[str, Any]) -> tuple[LocalPracticeCandidate | None, str]:
    title = _clean_text(row.get("title"))
    statement = _clean_text(row.get("statement"))
    answer = _clean_text(row.get("answer_outline") if "answer_outline" in row else row.get("answer"))
    difficulty_value = row.get("difficulty_score") if "difficulty_score" in row else row.get("difficulty")
    if not title or not statement:
        return None, "invalid_core"
    if not answer:
        return None, "missing_answer"
    if _is_placeholder_answer(answer):
        return None, "placeholder_answer"
    if isinstance(difficulty_value, bool) or not isinstance(difficulty_value, (int, float)):
        return None, "invalid_core"
    difficulty = float(difficulty_value)
    if not math.isfinite(difficulty) or difficulty < 0 or difficulty > 100:
        return None, "invalid_core"
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    canonical = {
        "answer": answer,
        "difficulty": difficulty,
        "statement": statement,
        "subject": _clean_text(row.get("subject_hint") or row.get("subject")),
        "template_id": _clean_text(row.get("template_id")),
        "title": title,
        "topic": _clean_text(row.get("topic_hint") or row.get("topic")),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    problem_key = hashlib.sha256(encoded).hexdigest()
    return (
        LocalPracticeCandidate(
            problem_key=problem_key,
            template_id=canonical["template_id"],
            title=title,
            statement=statement,
            answer=answer,
            difficulty=difficulty,
            subject=canonical["subject"],
            topic=canonical["topic"],
            source_type=_clean_text(source.get("type") or row.get("source_type")),
            source_title=_clean_text(source.get("title") or row.get("source_title")),
        ),
        "eligible",
    )


def build_candidate_pool(rows: Iterable[dict[str, Any]]) -> CandidatePool:
    stats = {
        "total_seen": 0,
        "eligible": 0,
        "excluded_missing_answer": 0,
        "excluded_placeholder_answer": 0,
        "excluded_invalid_core": 0,
        "excluded_duplicate": 0,
    }
    unique: dict[str, LocalPracticeCandidate] = {}
    for row in rows:
        stats["total_seen"] += 1
        candidate, reason = _candidate_from_row(row)
        if candidate is None:
            stats[f"excluded_{reason}"] += 1
            continue
        if candidate.problem_key in unique:
            stats["excluded_duplicate"] += 1
            continue
        unique[candidate.problem_key] = candidate
    candidates = tuple(unique[key] for key in sorted(unique))
    stats["eligible"] = len(candidates)
    snapshot_payload = "\n".join(candidate.problem_key for candidate in candidates).encode("ascii")
    return CandidatePool(
        candidates=candidates,
        stats=stats,
        snapshot_sha256=hashlib.sha256(snapshot_payload).hexdigest(),
    )
