"""Preflight guards shared by record, plan, and OJ repositories."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from study_app.data.db_runtime import DatabaseNotInitializedError, connect_readonly


F5_STRICT_MODE_SETTING = "f5_catalog:strict_mode"


def f5_catalog_is_installed(connection: sqlite3.Connection) -> bool:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='subject_catalog'"
    ).fetchone() is not None
    if not table_exists:
        return False
    if connection.execute("SELECT 1 FROM subject_catalog LIMIT 1").fetchone():
        return True
    settings_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'"
    ).fetchone()
    if settings_table is None:
        return False
    marker = connection.execute(
        "SELECT value_json FROM app_settings WHERE key=?",
        (F5_STRICT_MODE_SETTING,),
    ).fetchone()
    if marker is None:
        return False
    try:
        return json.loads(str(marker[0])) is True
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def require_f5_subject_write_allowed(
    connection: sqlite3.Connection,
    subject_name: object,
) -> tuple[str, int] | None:
    """Gate ordinary writes inside their transaction when F5 is installed."""
    if not f5_catalog_is_installed(connection):
        return None
    from study_app.core.subject_identity import normalize_alias

    normalized = normalize_alias(subject_name)
    row = connection.execute(
        """
        SELECT catalog.subject_key, catalog.lifecycle_status, catalog.object_version
        FROM subject_aliases aliases
        JOIN subject_catalog catalog ON catalog.subject_key=aliases.subject_key
        WHERE aliases.alias_normalized=?
        """,
        (normalized,),
    ).fetchone()
    if row is None:
        raise ValueError(f"学科尚未登记到 F5 目录：{subject_name!r}")
    if row["lifecycle_status"] != "active":
        raise ValueError(f"{subject_name} 已归档，禁止普通新增学习记录")
    return str(row["subject_key"]), int(row["object_version"])


def require_knowledge_topic_registry_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'knowledge_topic_registry'
            """
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "知识点身份表尚未安装；真实数据库需先完成独立 CR-F2-01 结构变更"
        )
