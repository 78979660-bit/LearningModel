from __future__ import annotations

from pathlib import Path
from typing import Any

from study_app.data.database import DEFAULT_DB_PATH, connect, connect_readonly, initialize_database


def register_collection_gap(
    subject: str | None,
    template_id: str,
    topic: str | None,
    target_difficulty: float | None,
    note: str = "生成提示词时未找到合适历史样题",
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    path = initialize_database(db_path)
    with connect(path) as connection:
        row = connection.execute(
            """
            INSERT INTO practice_collection_backlog(
                subject, template_id, topic, target_difficulty, note
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subject, template_id, topic) DO UPDATE SET
                target_difficulty = COALESCE(excluded.target_difficulty, target_difficulty),
                request_count = request_count + 1,
                status = 'pending',
                last_requested_at = CURRENT_TIMESTAMP,
                resolved_at = NULL,
                note = excluded.note
            RETURNING id
            """,
            (subject or "", template_id, topic or "", target_difficulty, note),
        ).fetchone()
    return int(row["id"])


def list_collection_backlog(
    status: str | None = "pending",
    limit: int = 50,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    clause = "WHERE status = ?" if status else ""
    params: list[Any] = [status] if status else []
    params.append(limit)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM practice_collection_backlog
            {clause}
            ORDER BY request_count DESC, last_requested_at DESC, id
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_filled_collection_gaps(db_path: Path | str = DEFAULT_DB_PATH) -> int:
    path = initialize_database(db_path)
    resolved = 0
    with connect(path) as connection:
        gaps = connection.execute(
            "SELECT * FROM practice_collection_backlog WHERE status = 'pending'"
        ).fetchall()
        for gap in gaps:
            clauses = ["template_id = ?"]
            params: list[Any] = [gap["template_id"]]
            if gap["subject"]:
                clauses.append("subject_hint = ?")
                params.append(gap["subject"])
            if gap["target_difficulty"] is not None:
                clauses.append("ABS(difficulty_score - ?) <= 12")
                params.append(gap["target_difficulty"])
            count = connection.execute(
                f"SELECT COUNT(*) AS count FROM practice_problems WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()["count"]
            if count:
                connection.execute(
                    """
                    UPDATE practice_collection_backlog
                    SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (gap["id"],),
                )
                resolved += 1
    return resolved
