from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from study_app.core.subject_catalog import CatalogSnapshot, load_catalog_snapshot
from study_app.data.subject_repository import SubjectCatalogRepository


PROJECTION_SCHEMA_VERSION = "subject-catalog-projection-v1"


def projection_payload(snapshot: CatalogSnapshot) -> dict:
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "catalog_revision": snapshot.catalog_revision,
        "subjects": [
            {
                "subject_key": subject.subject_key,
                "canonical_name": subject.canonical_name,
                "display_name": subject.display_name,
                "lifecycle_status": subject.lifecycle_status,
                "object_version": subject.object_version,
                "aliases": list(subject.aliases),
                "capabilities": dict(subject.capabilities),
                "structure_version": subject.structure_version,
                "modules": [
                    {
                        "module_key": module.module_key,
                        "canonical_name": module.canonical_name,
                        "display_name": module.display_name,
                        "module_order": module.module_order,
                        "topics": [
                            {
                                "topic_key": topic.topic_key,
                                "name": topic.name,
                                "topic_order": topic.topic_order,
                                "importance_bp": topic.importance_bp,
                                "difficulty_bp": topic.difficulty_bp,
                            }
                            for topic in module.topics
                        ],
                    }
                    for module in subject.modules
                ],
            }
            for subject in snapshot.subjects
        ],
    }


def _encoded(payload: dict) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def load_projection(path: Path | str, *, expected_revision: int | None = None) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        raise ValueError("不支持的学科目录投影版本")
    revision = payload.get("catalog_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("投影缺少有效 catalog_revision")
    if expected_revision is not None and revision != expected_revision:
        raise ValueError(
            f"投影修订不匹配：expected={expected_revision}, actual={revision}"
        )
    return payload


def publish_projection(snapshot: CatalogSnapshot, path: Path | str) -> dict:
    target = Path(path)
    payload = projection_payload(snapshot)
    encoded = _encoded(payload)
    if target.exists():
        current = load_projection(target)
        current_revision = int(current["catalog_revision"])
        if current_revision > snapshot.catalog_revision:
            raise RuntimeError("旧投影任务不得覆盖较新投影")
        if current_revision == snapshot.catalog_revision:
            if target.read_bytes() != encoded:
                raise RuntimeError("同一 catalog_revision 的投影内容冲突")
            return current
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def rebuild_projection(db_path: Path | str, path: Path | str) -> dict:
    return publish_projection(load_catalog_snapshot(db_path), path)


def process_projection_outbox(
    db_path: Path | str,
    path: Path | str,
    *,
    task_id: str | None = None,
    fail_before_publish: bool = False,
) -> dict:
    """Publish one revision-aware outbox item while holding the DB write reservation."""
    repository = SubjectCatalogRepository(db_path)
    connection = repository.begin_immediate()
    selected_task_id: str | None = None
    operation_id: str | None = None
    try:
        if task_id is None:
            row = connection.execute(
                """
                SELECT * FROM subject_projection_outbox
                WHERE status IN ('pending','failed')
                ORDER BY target_revision DESC,created_at DESC LIMIT 1
                """
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM subject_projection_outbox WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            connection.rollback()
            raise LookupError("没有可处理的投影 outbox 任务")
        selected_task_id = row["task_id"]
        operation_id = row["operation_id"]
        if row["status"] in {"published", "superseded"}:
            connection.rollback()
            return {
                "task_id": selected_task_id,
                "operation_id": operation_id,
                "status": row["status"],
                "idempotent": True,
            }
        target_revision = int(row["target_revision"])
        current_revision = int(
            connection.execute(
                "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
            ).fetchone()[0]
        )
        if target_revision < current_revision:
            connection.execute(
                """
                UPDATE subject_projection_outbox
                SET status='superseded',attempt_count=attempt_count+1,last_error=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE task_id=?
                """,
                (selected_task_id,),
            )
            connection.execute(
                "UPDATE subject_change_operations SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE operation_id=? AND status='db_committed_projection_pending'",
                (operation_id,),
            )
            connection.execute(
                "INSERT INTO subject_operation_events(operation_id,event_type,detail_json) VALUES (?,'projection_superseded',?)",
                (
                    operation_id,
                    json.dumps(
                        {"target_revision": target_revision, "current_revision": current_revision},
                        sort_keys=True,
                    ),
                ),
            )
            connection.commit()
            return {
                "task_id": selected_task_id,
                "operation_id": operation_id,
                "status": "superseded",
                "catalog_revision": current_revision,
                "idempotent": False,
            }
        if target_revision > current_revision:
            raise RuntimeError("投影任务目标修订超前于数据库")
        snapshot = load_catalog_snapshot(db_path)
        if snapshot.catalog_revision != target_revision:
            raise RuntimeError("投影快照修订与 outbox 不一致")
        if fail_before_publish:
            raise RuntimeError("injected projection failure")
        payload = publish_projection(snapshot, path)
        connection.execute(
            """
            UPDATE subject_projection_outbox
            SET status='published',attempt_count=attempt_count+1,last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE task_id=?
            """,
            (selected_task_id,),
        )
        connection.execute(
            "UPDATE subject_change_operations SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE operation_id=? AND status='db_committed_projection_pending'",
            (operation_id,),
        )
        connection.execute(
            "INSERT INTO subject_operation_events(operation_id,event_type,detail_json) VALUES (?,'projection_published',?)",
            (
                operation_id,
                json.dumps({"target_revision": target_revision}, sort_keys=True),
            ),
        )
        connection.commit()
        return {
            "task_id": selected_task_id,
            "operation_id": operation_id,
            "status": "published",
            "catalog_revision": target_revision,
            "projection": payload,
            "idempotent": False,
        }
    except Exception as error:
        connection.rollback()
        if selected_task_id is not None and operation_id is not None:
            with repository.transaction(readonly=False) as failure_connection:
                failure_connection.execute(
                    """
                    UPDATE subject_projection_outbox
                    SET status='failed',attempt_count=attempt_count+1,last_error=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE task_id=?
                    """,
                    (f"{type(error).__name__}: {error}", selected_task_id),
                )
                failure_connection.execute(
                    "INSERT INTO subject_operation_events(operation_id,event_type,detail_json) VALUES (?,'projection_failed',?)",
                    (
                        operation_id,
                        json.dumps({"error": f"{type(error).__name__}: {error}"}, sort_keys=True),
                    ),
                )
        raise
    finally:
        connection.close()
