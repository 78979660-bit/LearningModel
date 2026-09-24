from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from study_app.data.subject_repository import SubjectCatalogRepository


CANONICALIZER_VERSION = "canonical-json-v1"
CHANGESET_SCHEMA_VERSION = "subject-changeset-v1"
_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class ChangeSetConflictError(ValueError):
    pass


class ChangeSetStaleError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedChangeSet:
    operation_id: str
    changeset_hash: str
    status: str
    expected_catalog_revision: int
    payload: dict
    input_version_vector: dict
    manifest_version: str | None
    schema_version: str
    validator_version: str


@dataclass(frozen=True)
class PreflightReport:
    operation_id: str
    valid: bool
    stale: bool
    issues: tuple[str, ...]
    impact: dict[str, int]


def _normalize(value: object) -> object:
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, float):
        raise ValueError("canonical-json-v1 禁止浮点数；请使用整数 basis points")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list) or isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("canonical-json-v1 对象键必须是字符串")
            key = unicodedata.normalize("NFC", raw_key)
            if key in result:
                raise ValueError(f"Unicode 规范化后对象键冲突：{key}")
            result[key] = _normalize(item)
        return result
    raise ValueError(f"canonical-json-v1 不支持类型：{type(value).__name__}")


def canonical_json(value: object) -> str:
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _operation_id(value: object) -> str:
    if not isinstance(value, str) or not _OPERATION_ID.fullmatch(value):
        raise ValueError(f"operation_id 格式无效：{value!r}")
    return value


def current_input_version_vector(
    db_path: Path | str,
    *,
    target_subject_keys: tuple[str, ...] = (),
    manifest_version: str | None = None,
) -> dict:
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction() as connection:
        revision = int(
            connection.execute(
                "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
            ).fetchone()[0]
        )
        subjects: dict[str, int | None] = {}
        for key in sorted(set(target_subject_keys)):
            row = connection.execute(
                "SELECT object_version FROM subject_catalog WHERE subject_key=?", (key,)
            ).fetchone()
            subjects[key] = int(row[0]) if row else None
        manifest_hash = None
        manifest_object_version = None
        if manifest_version is not None:
            row = connection.execute(
                "SELECT payload_hash, object_version FROM subject_manifest_versions WHERE manifest_version=?",
                (manifest_version,),
            ).fetchone()
            if row is not None:
                manifest_hash = row["payload_hash"]
                manifest_object_version = int(row["object_version"])
    return {
        "catalog_revision": revision,
        "subjects": subjects,
        "manifest_version": manifest_version,
        "manifest_hash": manifest_hash,
        "manifest_object_version": manifest_object_version,
    }


def store_manifest_version(
    db_path: Path | str,
    *,
    manifest_version: str,
    payload: Mapping[str, object],
    status: str,
    generator_version: str,
    input_vector: Mapping[str, object],
) -> str:
    if status not in {"draft", "pending_review", "rejected", "adopted"}:
        raise ValueError("非法 Manifest 状态")
    payload_json = canonical_json(payload)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction(readonly=False) as connection:
        existing = connection.execute(
            "SELECT payload_hash FROM subject_manifest_versions WHERE manifest_version=?",
            (manifest_version,),
        ).fetchone()
        if existing is not None:
            if existing["payload_hash"] != payload_hash:
                raise ChangeSetConflictError("同一 manifest_version 不得更换载荷")
            return payload_hash
        connection.execute(
            """
            INSERT INTO subject_manifest_versions(
                manifest_version, schema_version, generator_version,
                input_vector_json, payload_json, payload_hash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest_version,
                str(payload.get("schema_version") or ""),
                generator_version,
                canonical_json(input_vector),
                payload_json,
                payload_hash,
                status,
            ),
        )
    return payload_hash


def _from_rows(operation: sqlite3.Row, changeset: sqlite3.Row) -> PreparedChangeSet:
    return PreparedChangeSet(
        operation_id=operation["operation_id"],
        changeset_hash=changeset["changeset_hash"],
        status=operation["status"],
        expected_catalog_revision=int(operation["expected_catalog_revision"]),
        payload=json.loads(changeset["payload_json"]),
        input_version_vector=json.loads(changeset["input_version_vector_json"]),
        manifest_version=changeset["manifest_version"],
        schema_version=changeset["schema_version"],
        validator_version=changeset["validator_version"],
    )


def prepare_changeset(
    db_path: Path | str,
    *,
    operation_id: str,
    payload: Mapping[str, object],
    input_version_vector: Mapping[str, object],
    manifest_version: str | None,
    schema_version: str = CHANGESET_SCHEMA_VERSION,
    validator_version: str,
    target_subject_keys: tuple[str, ...] = (),
) -> PreparedChangeSet:
    operation = _operation_id(operation_id)
    canonical_payload = canonical_json(payload)
    changeset_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    vector_json = canonical_json(input_version_vector)
    target_json = canonical_json(sorted(set(target_subject_keys)))
    expected_revision = int(input_version_vector.get("catalog_revision", -1))
    if expected_revision < 0:
        raise ValueError("input_version_vector 缺少 catalog_revision")
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction(readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_operation = connection.execute(
            "SELECT * FROM subject_change_operations WHERE operation_id=?", (operation,)
        ).fetchone()
        if existing_operation is not None:
            existing_changeset = connection.execute(
                "SELECT * FROM subject_changesets WHERE operation_id=?", (operation,)
            ).fetchone()
            if existing_changeset["changeset_hash"] != changeset_hash:
                connection.execute(
                    """
                    INSERT INTO subject_operation_preflight_events(
                        operation_id, event_type, detail_json
                    ) VALUES (?, 'operation_hash_conflict', ?)
                    """,
                    (
                        operation,
                        canonical_json(
                            {
                                "existing_hash": existing_changeset["changeset_hash"],
                                "received_hash": changeset_hash,
                            }
                        ),
                    ),
                )
                connection.commit()
                raise ChangeSetConflictError(
                    "同一 operation_id 携带不同 changeset_hash"
                )
            return _from_rows(existing_operation, existing_changeset)
        connection.execute(
            """
            INSERT INTO subject_change_operations(
                operation_id, changeset_hash, status,
                expected_catalog_revision, target_subject_keys_json
            ) VALUES (?, ?, 'prepared', ?, ?)
            """,
            (operation, changeset_hash, expected_revision, target_json),
        )
        connection.execute(
            """
            INSERT INTO subject_changesets(
                operation_id, changeset_hash, payload_json,
                input_version_vector_json, manifest_version,
                schema_version, validator_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation,
                changeset_hash,
                canonical_payload,
                vector_json,
                manifest_version,
                schema_version,
                validator_version,
            ),
        )
        connection.execute(
            "INSERT INTO subject_operation_preflight_events(operation_id,event_type) VALUES (?,'prepared')",
            (operation,),
        )
        op_row = connection.execute(
            "SELECT * FROM subject_change_operations WHERE operation_id=?", (operation,)
        ).fetchone()
        cs_row = connection.execute(
            "SELECT * FROM subject_changesets WHERE operation_id=?", (operation,)
        ).fetchone()
    return _from_rows(op_row, cs_row)


def _approval_binding(changeset: PreparedChangeSet) -> dict:
    return {
        "changeset_hash": changeset.changeset_hash,
        "input_version_vector": changeset.input_version_vector,
        "manifest_version": changeset.manifest_version,
        "schema_version": changeset.schema_version,
        "validator_version": changeset.validator_version,
    }


def approve_changeset(
    db_path: Path | str,
    operation_id: str,
    *,
    approver: str,
    approval_id: str | None = None,
) -> str:
    operation = _operation_id(operation_id)
    if not isinstance(approver, str) or not approver.strip():
        raise ValueError("approver 必须是非空字符串")
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction(readonly=False) as connection:
        op_row = connection.execute(
            "SELECT * FROM subject_change_operations WHERE operation_id=?", (operation,)
        ).fetchone()
        cs_row = connection.execute(
            "SELECT * FROM subject_changesets WHERE operation_id=?", (operation,)
        ).fetchone()
        if op_row is None or cs_row is None:
            raise LookupError(f"未知 operation_id：{operation}")
        prepared = _from_rows(op_row, cs_row)
        approval_hash = canonical_hash(_approval_binding(prepared))
        existing = connection.execute(
            "SELECT approval_hash FROM subject_approvals WHERE operation_id=?", (operation,)
        ).fetchone()
        if existing is not None:
            if existing["approval_hash"] != approval_hash:
                raise ChangeSetConflictError("既有签核与当前绑定不一致")
            return approval_hash
        identifier = approval_id or f"approval:{uuid.uuid4().hex}"
        connection.execute(
            """
            INSERT INTO subject_approvals(
                approval_id, operation_id, approval_hash, changeset_hash,
                input_version_vector_json, manifest_version, schema_version,
                validator_version, approver
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                operation,
                approval_hash,
                prepared.changeset_hash,
                canonical_json(prepared.input_version_vector),
                prepared.manifest_version,
                prepared.schema_version,
                prepared.validator_version,
                approver.strip(),
            ),
        )
        connection.execute(
            """
            UPDATE subject_change_operations
            SET status='approved', updated_at=CURRENT_TIMESTAMP
            WHERE operation_id=? AND status='prepared'
            """,
            (operation,),
        )
        connection.execute(
            "INSERT INTO subject_operation_preflight_events(operation_id,event_type) VALUES (?,'approved')",
            (operation,),
        )
    return approval_hash


def load_changeset(db_path: Path | str, operation_id: str) -> PreparedChangeSet:
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction() as connection:
        op_row = connection.execute(
            "SELECT * FROM subject_change_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        cs_row = connection.execute(
            "SELECT * FROM subject_changesets WHERE operation_id=?", (operation_id,)
        ).fetchone()
    if op_row is None or cs_row is None:
        raise LookupError(f"未知 operation_id：{operation_id}")
    return _from_rows(op_row, cs_row)


def preflight_changeset(
    db_path: Path | str,
    operation_id: str,
    *,
    current_vector: Mapping[str, object],
) -> PreflightReport:
    prepared = load_changeset(db_path, operation_id)
    issues: list[str] = []
    if canonical_hash(prepared.payload) != prepared.changeset_hash:
        issues.append("changeset_payload_hash_mismatch")
    stale = canonical_json(current_vector) != canonical_json(prepared.input_version_vector)
    if stale:
        issues.append("input_version_vector_drift")
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction() as connection:
        approval = connection.execute(
            "SELECT * FROM subject_approvals WHERE operation_id=?", (operation_id,)
        ).fetchone()
    if approval is None:
        issues.append("approval_missing")
    else:
        expected_approval = canonical_hash(_approval_binding(prepared))
        if approval["approval_hash"] != expected_approval:
            issues.append("approval_binding_mismatch")
    actions = prepared.payload.get("actions", [])
    impact = {
        "action_count": len(actions) if isinstance(actions, list) else 0,
        "archive_count": sum(
            isinstance(item, dict) and item.get("type") == "archive_subject"
            for item in actions if isinstance(actions, list)
        ),
        "activate_count": sum(
            isinstance(item, dict) and item.get("type") == "activate_subject"
            for item in actions if isinstance(actions, list)
        ),
    }
    return PreflightReport(operation_id, not issues, stale, tuple(issues), impact)
