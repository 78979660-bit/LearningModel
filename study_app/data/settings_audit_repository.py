"""Application settings and LLM call audit persistence."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from study_app.data.db_runtime import (
    DEFAULT_DB_PATH,
    connect,
    connect_readonly,
    dumps,
    require_initialized_database,
)


def upsert_setting(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        """
        INSERT INTO app_settings(key, value_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, dumps(value)),
    )


def get_setting(key: str, default: Any = None, db_path: Path | str = DEFAULT_DB_PATH) -> Any:
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return default


def set_setting(
    key: str,
    value: Any,
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    if connection is not None:
        upsert_setting(connection, key, value)
        return
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        upsert_setting(connection, key, value)


def delete_settings_by_prefix(prefix: str, db_path: Path | str = DEFAULT_DB_PATH) -> int:
    path = require_initialized_database(db_path)
    escaped_prefix = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with connect(path) as connection:
        cursor = connection.execute(
            "DELETE FROM app_settings WHERE key LIKE ? ESCAPE '\\'",
            (f"{escaped_prefix}%",),
        )
        return int(cursor.rowcount or 0)


def record_llm_call_audit(
    feature: str,
    provider: str,
    model: str,
    estimated_tokens: int,
    upload_summary: dict[str, Any],
    status: str,
    error_message: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    parent_audit_id: int | None = None,
) -> int:
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO llm_call_audits(
                feature, provider, model, estimated_tokens,
                upload_summary_json, status, error_message, parent_audit_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feature,
                provider,
                model,
                int(estimated_tokens or 0),
                dumps(upload_summary or {}),
                status,
                error_message,
                parent_audit_id,
            ),
        )
        return int(cursor.lastrowid)


def update_llm_call_audit(
    audit_id: int,
    status: str,
    error_message: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        cursor = connection.execute(
            "UPDATE llm_call_audits SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, int(audit_id)),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"LLM audit row {audit_id} does not exist")


def list_llm_call_audits(
    limit: int = 20,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, created_at, feature, provider, model, estimated_tokens,
                   upload_summary_json, status, error_message, parent_audit_id
            FROM llm_call_audits
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["upload_summary"] = json.loads(item.pop("upload_summary_json") or "{}")
        except json.JSONDecodeError:
            item["upload_summary"] = {}
        result.append(item)
    return result
