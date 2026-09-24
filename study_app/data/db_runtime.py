"""Shared SQLite access, serialization, and input validation primitives.

This module has no dependency on application repositories. Domain repositories
can import it without routing back through the database compatibility facade.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from study_app.paths import DATABASE_PATH


DEFAULT_DB_PATH = DATABASE_PATH


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


class DatabaseNotInitializedError(RuntimeError):
    """Raised when a read-only repository is opened before explicit setup."""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def connect_readonly(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open an existing database without creating directories, files or schema."""
    path = Path(db_path)
    if not path.is_file():
        raise DatabaseNotInitializedError(
            f"数据库尚未初始化：{path}；请先运行显式初始化流程"
        )
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        required = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'learning_records'"
        ).fetchone()
        if required is None:
            connection.close()
            raise DatabaseNotInitializedError(
                f"数据库结构尚未初始化：{path}；请先运行显式初始化流程"
            )
        return connection
    except sqlite3.Error as error:
        raise DatabaseNotInitializedError(
            f"无法只读打开数据库 {path}；请先运行显式初始化流程"
        ) from error


def require_initialized_database(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Validate a write target without performing schema migration or seeding."""
    path = Path(db_path)
    with connect_readonly(path):
        pass
    return path


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def validate_calendar_date(value: Any, *, label: str = "record.date") -> str:
    """Validate a real calendar date string (YYYY-MM-DD); raise ValueError otherwise.

    data_contract_v1 §4.3: 校验必须发生在任何数据库写入之前。
    """
    if value is None or value == "":
        raise ValueError(f"{label} is required")
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{label} 必须是 YYYY-MM-DD 格式的字符串：{value!r}")
    if not _DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{label} 格式必须为 YYYY-MM-DD：{value!r}")
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError(f"{label} 不是真实日历日期：{value!r}（{error}）") from error
    return value
