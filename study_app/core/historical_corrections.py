from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Mapping

from study_app.data.database import validate_calendar_date
from study_app.data.subject_repository import SubjectCatalogRepository


def request_historical_correction(
    db_path: Path | str,
    *,
    subject_key: str,
    record_id: int,
    event_date: str,
    discovery_date: str,
    reason: str,
    evidence: Mapping[str, object],
    actor: str,
    correction_id: str | None = None,
) -> str:
    event = validate_calendar_date(event_date, label="event_date")
    discovery = validate_calendar_date(discovery_date, label="discovery_date")
    if discovery < event:
        raise ValueError("discovery_date 不得早于 event_date")
    if not reason.strip() or not actor.strip():
        raise ValueError("reason 和 actor 必须非空")
    identifier = correction_id or f"correction:{uuid.uuid4().hex}"
    repository = SubjectCatalogRepository(db_path)
    with repository._open(readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        subject = connection.execute(
            "SELECT lifecycle_status FROM subject_catalog WHERE subject_key=?",
            (subject_key,),
        ).fetchone()
        if subject is None or subject["lifecycle_status"] != "archived":
            raise ValueError("历史更正只适用于已归档学科")
        record = connection.execute(
            "SELECT subject_name FROM learning_records WHERE id=?", (record_id,)
        ).fetchone()
        if record is None:
            raise LookupError(f"学习记录不存在：{record_id}")
        resolved = connection.execute(
            """
            SELECT aliases.subject_key FROM subject_aliases aliases
            WHERE aliases.alias_normalized=?
            """,
            (__import__("study_app.core.subject_identity", fromlist=["normalize_alias"]).normalize_alias(record["subject_name"]),),
        ).fetchone()
        if resolved is None or resolved["subject_key"] != subject_key:
            raise ValueError("学习记录不属于目标学科")
        connection.execute(
            """
            INSERT INTO historical_correction_requests(
                correction_id,subject_key,record_id,event_date,discovery_date,
                reason,evidence_json,actor,status
            ) VALUES (?,?,?,?,?,?,?,?, 'requested')
            """,
            (identifier, subject_key, record_id, event, discovery, reason.strip(), json.dumps(evidence, ensure_ascii=False, sort_keys=True), actor.strip()),
        )
        connection.execute(
            "INSERT INTO historical_correction_events(correction_id,event_type,actor) VALUES (?,'requested',?)",
            (identifier, actor.strip()),
        )
    return identifier


def apply_historical_correction(
    db_path: Path | str,
    correction_id: str,
    *,
    replacement_record: Mapping[str, object],
    approver: str,
) -> int:
    if not approver.strip():
        raise ValueError("approver 必须非空")
    repository = SubjectCatalogRepository(db_path)
    with repository._open(readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        request = connection.execute(
            "SELECT * FROM historical_correction_requests WHERE correction_id=?",
            (correction_id,),
        ).fetchone()
        if request is None:
            raise LookupError(f"更正申请不存在：{correction_id}")
        if request["status"] == "applied":
            return int(request["applied_revision"])
        subject = connection.execute(
            "SELECT lifecycle_status FROM subject_catalog WHERE subject_key=?",
            (request["subject_key"],),
        ).fetchone()
        if subject is None or subject["lifecycle_status"] != "archived":
            raise ValueError("目标学科不再处于 archived，停止历史更正")
        next_version = int(
            connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM learning_record_revisions WHERE record_id=?",
                (request["record_id"],),
            ).fetchone()[0]
        )
        raw_json = json.dumps(dict(replacement_record), ensure_ascii=False, sort_keys=True)
        import hashlib

        payload_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO learning_record_revisions(
                record_id,version,action,raw_json,payload_sha256,actor,
                prior_contributions_json,source_key
            ) VALUES (?,?, 'historical_correction', ?,?,?, '[]',?)
            """,
            (request["record_id"], next_version, raw_json, payload_hash, approver.strip(), f"f5-correction:{correction_id}"),
        )
        connection.execute(
            """
            UPDATE historical_correction_requests
            SET status='applied', applied_revision=?, updated_at=CURRENT_TIMESTAMP
            WHERE correction_id=?
            """,
            (next_version, correction_id),
        )
        connection.execute(
            "INSERT INTO historical_correction_events(correction_id,event_type,actor,detail_json) VALUES (?,'applied',?,?)",
            (correction_id, approver.strip(), json.dumps({"revision": next_version}, sort_keys=True)),
        )
    return next_version
