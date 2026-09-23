from __future__ import annotations

import pytest

from study_app.core.local_practice_candidates import build_candidate_pool
from study_app.core.local_practice_composer import (
    InsufficientPracticeCandidatesError,
    compose_local_practice_paper,
    requested_distribution,
)
from study_app.core.local_practice_spec import LocalPracticePaperSpec


def spec(count=6, target=60):
    return LocalPracticePaperSpec.create(
        subject="高等数学",
        template_id="TEST",
        target_difficulty=target,
        question_count=count,
        paper_date="2026-09-17",
    )


def candidate(index: int, difficulty: float):
    return {
        "template_id": "TEST",
        "title": f"题目 {index}",
        "statement": f"题面 {index}",
        "answer_outline": f"答案 {index}",
        "difficulty_score": difficulty,
        "subject_hint": "高等数学",
        "topic_hint": "级数",
    }


def test_distribution_quotas_cover_small_and_six_question_papers() -> None:
    assert requested_distribution(1) == {"foundation": 0, "core": 1, "challenge": 0}
    assert requested_distribution(4) == {"foundation": 1, "core": 2, "challenge": 1}
    assert requested_distribution(6) == {"foundation": 1, "core": 4, "challenge": 1}


def test_six_question_distribution_and_order_are_deterministic() -> None:
    rows = [
        candidate(1, 40),
        candidate(2, 56),
        candidate(3, 58),
        candidate(4, 60),
        candidate(5, 64),
        candidate(6, 82),
        candidate(7, 90),
    ]
    pool = build_candidate_pool(rows)
    first = compose_local_practice_paper(spec(), pool)
    second = compose_local_practice_paper(spec(), build_candidate_pool(reversed(rows)))
    assert first.paper_id == second.paper_id
    assert [item.problem_key for item in first.questions] == [item.problem_key for item in second.questions]
    assert first.actual_distribution == {"foundation": 1, "core": 4, "challenge": 1}
    assert first.warnings == ()


def test_missing_band_is_explicitly_and_deterministically_backfilled() -> None:
    pool = build_candidate_pool([candidate(index, difficulty) for index, difficulty in enumerate([40, 52, 55, 58, 60, 62, 65], 1)])
    paper = compose_local_practice_paper(spec(), pool)
    assert len(paper.questions) == 6
    assert paper.actual_distribution == {"foundation": 1, "core": 5, "challenge": 0}
    assert len(paper.warnings) == 1
    assert "challenge" in paper.warnings[0]


def test_insufficient_candidates_fail_before_output() -> None:
    pool = build_candidate_pool([candidate(1, 60), candidate(2, 65)])
    with pytest.raises(InsufficientPracticeCandidatesError, match="合格题仅 2 道，请求 4 道"):
        compose_local_practice_paper(spec(count=4), pool)


def test_content_change_changes_snapshot_and_paper_id() -> None:
    rows = [candidate(index, difficulty) for index, difficulty in enumerate([40, 55, 60, 62, 65, 80], 1)]
    first = compose_local_practice_paper(spec(), build_candidate_pool(rows))
    changed_rows = list(rows)
    changed_rows[0] = {**changed_rows[0], "answer_outline": "更正后的答案"}
    changed = compose_local_practice_paper(spec(), build_candidate_pool(changed_rows))
    assert first.bank_snapshot_sha256 != changed.bank_snapshot_sha256
    assert first.paper_id != changed.paper_id


def test_manifest_core_preserves_selected_order_without_full_answer_leak() -> None:
    rows = [candidate(index, difficulty) for index, difficulty in enumerate([40, 55, 60, 62, 65, 80], 1)]
    paper = compose_local_practice_paper(spec(), build_candidate_pool(rows))
    core = paper.manifest_core()
    assert [item["problem_key"] for item in core["questions"]] == [item.problem_key for item in paper.questions]
    assert all("answer" not in item and "statement" not in item for item in core["questions"])
