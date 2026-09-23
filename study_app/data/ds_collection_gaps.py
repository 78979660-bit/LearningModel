from __future__ import annotations

from pathlib import Path
from typing import Any

from study_app.core.ds_course_taxonomy import DS_TOPICS, classify_ds_text
from study_app.data.collection_backlog import register_collection_gap
from study_app.data.database import DEFAULT_DB_PATH, connect, connect_readonly, initialize_database


SUBJECT = "数据结构与算法基础"
TRUSTED_TYPES = (
    "learning_record", "uploaded_homework", "uploaded_classroom", "classroom_exercise",
    "homework_upload", "assignment_upload", "manual_upload", "trusted_university",
)


def audit_and_register_ds_collection_gaps(
    minimum_count: int = 3,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    path = initialize_database(db_path)
    gaps = []
    with connect(path) as connection:
        for item in DS_TOPICS:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count, ROUND(MAX(p.difficulty_score), 1) AS maximum
                FROM practice_problems p
                LEFT JOIN practice_sources s ON s.id = p.source_id
                WHERE p.template_id = ?
                  AND p.topic_hint LIKE ?
                  AND (
                    s.source_type IN ({})
                    OR p.difficulty_source = 'trusted_university_estimate'
                  )
                """.format(",".join("?" for _ in TRUSTED_TYPES)),
                (item.template_id, f"%{item.topic}%", *TRUSTED_TYPES),
            ).fetchone()
            count = int(row["count"] or 0)
            if count >= minimum_count:
                continue
            target = 78 if item.chapter in {"第7章：搜索结构", "第8章：图", "第9章：排序"} else 70
            register_collection_gap(
                SUBJECT,
                item.template_id,
                f"{item.chapter} / {item.topic}",
                target,
                note=f"九章重分类后高质量参考题仅 {count} 道，目标至少 {minimum_count} 道。",
                db_path=path,
            )
            gaps.append({
                "chapter": item.chapter,
                "topic": item.topic,
                "template_id": item.template_id,
                "trusted_count": count,
                "maximum_difficulty": row["maximum"],
                "target_difficulty": target,
            })
    return gaps


def ds_collection_coverage(db_path: Path | str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    coverage = []
    with connect_readonly(db_path) as connection:
        for item in DS_TOPICS:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count, ROUND(MAX(p.difficulty_score), 1) AS maximum,
                       ROUND(AVG(p.difficulty_score), 1) AS average
                FROM practice_problems p
                LEFT JOIN practice_sources s ON s.id = p.source_id
                WHERE p.template_id = ? AND p.topic_hint LIKE ?
                  AND (
                    s.source_type IN ({})
                    OR p.difficulty_source = 'trusted_university_estimate'
                  )
                """.format(",".join("?" for _ in TRUSTED_TYPES)),
                (item.template_id, f"%{item.topic}%", *TRUSTED_TYPES),
            ).fetchone()
            coverage.append({
                "chapter": item.chapter,
                "topic": item.topic,
                "template_id": item.template_id,
                "trusted_count": int(row["count"] or 0),
                "average_difficulty": row["average"],
                "maximum_difficulty": row["maximum"],
            })
    return coverage


def reclassify_trusted_ds_bank(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, int]:
    path = initialize_database(db_path)
    updated = 0
    unclassified = 0
    duplicates_removed = 0
    with connect(path) as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.template_id, p.title, p.statement, p.tags_json, p.topic_hint
            FROM practice_problems p
            LEFT JOIN practice_sources s ON s.id = p.source_id
            WHERE p.subject_hint = ?
              AND (
                s.source_type IN ({})
                OR p.difficulty_source = 'trusted_university_estimate'
              )
            """.format(",".join("?" for _ in TRUSTED_TYPES)),
            (SUBJECT, *TRUSTED_TYPES),
        ).fetchall()
        for row in rows:
            classified = classify_ds_text(
                " ".join(str(row[key] or "") for key in ("title", "statement", "tags_json"))
            )
            if not classified:
                unclassified += 1
                continue
            duplicate = connection.execute(
                "SELECT id FROM practice_problems WHERE template_id = ? AND title = ? AND id <> ?",
                (classified.template_id, row["title"], row["id"]),
            ).fetchone()
            if duplicate:
                connection.execute("DELETE FROM practice_problems WHERE id = ?", (row["id"],))
                duplicates_removed += 1
                continue
            connection.execute(
                """
                UPDATE practice_problems
                SET template_id = ?, topic_hint = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (classified.template_id, f"{classified.chapter} / {classified.topic}", row["id"]),
            )
            updated += 1
    return {"updated": updated, "unclassified": unclassified, "duplicates_removed": duplicates_removed}
