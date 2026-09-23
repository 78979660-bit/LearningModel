from __future__ import annotations

import hashlib
from pathlib import Path

from study_app.core.local_practice_candidates import build_candidate_pool
from study_app.data.database import connect, initialize_database
from study_app.data.practice_repository import list_local_practice_candidates


def row(**overrides):
    value = {
        "template_id": "TEST",
        "title": "题目一",
        "statement": "计算 1 + 1。",
        "answer_outline": "答案为 2。",
        "difficulty_score": 50,
        "subject_hint": "高等数学",
        "topic_hint": "基础计算",
        "source": {"type": "manual", "title": "fixture"},
    }
    value.update(overrides)
    return value


def test_candidate_gate_excludes_missing_placeholder_invalid_and_duplicate() -> None:
    pool = build_candidate_pool(
        [
            row(),
            row(),
            row(title="空答案", answer_outline=""),
            row(title="种子", answer_outline="这是题型种子，不提供固定答案；外部 LLM 应生成。"),
            row(title="坏难度", difficulty_score=float("nan")),
            row(title="无题面", statement=""),
        ]
    )
    assert len(pool.candidates) == 1
    assert pool.stats == {
        "total_seen": 6,
        "eligible": 1,
        "excluded_missing_answer": 1,
        "excluded_placeholder_answer": 1,
        "excluded_invalid_core": 2,
        "excluded_duplicate": 1,
    }
    assert len(pool.snapshot_sha256) == 64


def test_content_not_database_id_defines_identity_and_snapshot() -> None:
    first = build_candidate_pool([row(id=1)])
    second = build_candidate_pool([row(id=999)])
    changed = build_candidate_pool([row(id=1, answer_outline="答案改为 3。")])
    assert first.candidates[0].problem_key == second.candidates[0].problem_key
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.snapshot_sha256 != changed.snapshot_sha256


def test_repository_query_is_filtered_and_byte_read_only(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "practice.sqlite")
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO practice_templates(template_id, title, description) VALUES ('TEST', 'T', 'D')"
        )
        source_id = connection.execute(
            "INSERT INTO practice_sources(source_type, title) VALUES ('manual', 'fixture') RETURNING id"
        ).fetchone()["id"]
        for title, subject, topic in (
            ("目标题", "高等数学", "级数"),
            ("别科题", "大学物理学", "级数"),
            ("别主题", "高等数学", "重积分"),
        ):
            connection.execute(
                """
                INSERT INTO practice_problems(
                    template_id, title, statement, answer_outline, difficulty_score,
                    subject_hint, topic_hint, source_id
                ) VALUES ('TEST', ?, ?, ?, 60, ?, ?, ?)
                """,
                (title, f"{title}题面", f"{title}答案", subject, topic, source_id),
            )
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    rows = list_local_practice_candidates(
        subject="高等数学", template_id="TEST", topic="级数", db_path=db_path
    )
    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert [item["title"] for item in rows] == ["目标题"]
    assert before == after
