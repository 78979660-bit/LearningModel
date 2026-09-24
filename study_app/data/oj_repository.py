"""OJ problem, mapping, attempt, and evidence persistence."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from study_app.data.catalog_guards import require_knowledge_topic_registry_table
from study_app.data.db_runtime import (
    DatabaseNotInitializedError,
    connect,
    connect_readonly,
    dumps,
    require_initialized_database,
)

from study_app.paths import DATABASE_PATH

DEFAULT_DB_PATH = DATABASE_PATH


# F3-I01: OJ problem identity is installed explicitly on isolated/new databases.
# The production database remains behind the independent CR-F3-01 migration gate.
OJ_PROBLEMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS oj_problems (
    problem_key TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    external_problem_key TEXT NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    identity_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_key, external_problem_key)
)
"""

OJ_PROBLEM_TOPICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS oj_problem_topics (
    problem_key TEXT NOT NULL REFERENCES oj_problems(problem_key) ON DELETE CASCADE,
    topic_key TEXT NOT NULL REFERENCES knowledge_topic_registry(topic_key) ON DELETE RESTRICT,
    mapping_source TEXT NOT NULL CHECK(mapping_source IN ('manual', 'offline_import')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(problem_key, topic_key)
)
"""

OJ_ATTEMPTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS oj_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_key TEXT NOT NULL REFERENCES oj_problems(problem_key) ON DELETE RESTRICT,
    attempted_at TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN (
        'accepted', 'partial', 'wrong', 'runtime_error', 'time_limit',
        'memory_limit', 'compile_error', 'abandoned'
    )),
    duration_seconds INTEGER NOT NULL CHECK(duration_seconds > 0),
    independence TEXT NOT NULL CHECK(independence IN ('independent', 'guided', 'copied')),
    hint_level TEXT NOT NULL CHECK(hint_level IN ('none', 'concept', 'pseudocode', 'solution')),
    error_type TEXT NOT NULL CHECK(error_type IN (
        'none', 'misunderstood', 'algorithm', 'implementation', 'edge_case',
        'complexity', 'syntax', 'runtime', 'unknown'
    )),
    notes TEXT NOT NULL DEFAULT '',
    source_attempt_key TEXT UNIQUE,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

OJ_ATTEMPTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_oj_attempts_problem_time
ON oj_attempts(problem_key, attempted_at, attempt_id)
"""


def install_oj_schema(db_path: Path | str) -> None:
    """Explicitly install the currently frozen F3 schema on an initialized DB."""
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        connection.execute(OJ_PROBLEMS_TABLE_SQL)
        connection.execute(OJ_PROBLEM_TOPICS_TABLE_SQL)
        connection.execute(OJ_ATTEMPTS_TABLE_SQL)
        connection.execute(OJ_ATTEMPTS_INDEX_SQL)


def _require_oj_problems_table(db_path: Path | str) -> None:
    path = Path(db_path)
    with connect_readonly(path) as connection:
        row = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'oj_problems'
            """
        ).fetchone()
    if row is None:
        raise DatabaseNotInitializedError(
            "OJ 题目表尚未安装；现用数据库需先完成独立 CR-F3-01 结构变更"
        )


def _oj_problem_from_row(row: sqlite3.Row):
    from study_app.core.oj_identity import make_oj_problem

    return make_oj_problem(
        problem_key=row["problem_key"],
        source_key=row["source_key"],
        external_problem_key=row["external_problem_key"],
        title=row["title"],
        source_url=row["source_url"],
        identity_version=row["identity_version"],
    )


def register_oj_problem(
    source_key: object,
    external_problem_key: object,
    title: object,
    source_url: object = "",
    db_path: Path | str = DEFAULT_DB_PATH,
):
    from study_app.core.oj_identity import (
        OJ_IDENTITY_VERSION,
        make_oj_problem,
        problem_key_for,
    )

    problem = make_oj_problem(
        problem_key=problem_key_for(source_key, external_problem_key),
        source_key=source_key,
        external_problem_key=external_problem_key,
        title=title,
        source_url=source_url,
        identity_version=OJ_IDENTITY_VERSION,
    )
    _require_oj_problems_table(db_path)
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        existing = connection.execute(
            """
            SELECT * FROM oj_problems
            WHERE source_key = ? AND external_problem_key = ?
            """,
            (problem.source_key, problem.external_problem_key),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO oj_problems(
                    problem_key, source_key, external_problem_key, title,
                    source_url, identity_version
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    problem.problem_key,
                    problem.source_key,
                    problem.external_problem_key,
                    problem.title,
                    problem.source_url,
                    problem.identity_version,
                ),
            )
        else:
            persisted = _oj_problem_from_row(existing)
            if persisted.problem_key != problem.problem_key:
                raise ValueError("OJ 题目身份冲突，拒绝重绑")
            if persisted.title != problem.title or persisted.source_url != problem.source_url:
                connection.execute(
                    """
                    UPDATE oj_problems
                    SET title = ?, source_url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE problem_key = ?
                    """,
                    (problem.title, problem.source_url, problem.problem_key),
                )
        row = connection.execute(
            "SELECT * FROM oj_problems WHERE problem_key = ?",
            (problem.problem_key,),
        ).fetchone()
    if row is None:
        raise RuntimeError("OJ 题目注册后不可读")
    return _oj_problem_from_row(row)


def get_oj_problem(
    problem_key: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    from study_app.core.oj_identity import validate_problem_key

    valid_key = validate_problem_key(problem_key)
    _require_oj_problems_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM oj_problems WHERE problem_key = ?",
            (valid_key,),
        ).fetchone()
    if row is None:
        raise LookupError(f"未找到 OJ 题目：{valid_key}")
    return _oj_problem_from_row(row)


def list_oj_problems(db_path: Path | str = DEFAULT_DB_PATH) -> tuple:
    _require_oj_problems_table(db_path)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM oj_problems
            ORDER BY source_key, external_problem_key, problem_key
            """
        ).fetchall()
    return tuple(_oj_problem_from_row(row) for row in rows)


def _require_oj_problem_topics_table(db_path: Path | str) -> None:
    path = Path(db_path)
    with connect_readonly(path) as connection:
        row = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'oj_problem_topics'
            """
        ).fetchone()
    if row is None:
        raise DatabaseNotInitializedError(
            "OJ 题目知识点映射表尚未安装；"
            "现用数据库需先完成独立 CR-F3-01 结构变更"
        )


def _oj_topic_mapping_from_row(row: sqlite3.Row):
    from study_app.core.oj_topics import make_oj_topic_mapping

    return make_oj_topic_mapping(
        problem_key=row["problem_key"],
        topic_key=row["topic_key"],
        mapping_source=row["mapping_source"],
        note=row["note"],
    )


def replace_oj_problem_topics(
    problem_key: object,
    topic_keys: object,
    *,
    mapping_source: object = "manual",
    note: object = "",
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple:
    from study_app.core.oj_identity import validate_problem_key
    from study_app.core.oj_topics import (
        normalize_topic_keys,
        validate_mapping_note,
        validate_mapping_source,
    )

    valid_problem_key = validate_problem_key(problem_key)
    valid_topic_keys = normalize_topic_keys(topic_keys)
    valid_source = validate_mapping_source(mapping_source)
    valid_note = validate_mapping_note(note)
    _require_oj_problems_table(db_path)
    _require_oj_problem_topics_table(db_path)
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        if connection.execute(
            "SELECT 1 FROM oj_problems WHERE problem_key = ?",
            (valid_problem_key,),
        ).fetchone() is None:
            raise LookupError(f"未找到 OJ 题目：{valid_problem_key}")
        if valid_topic_keys:
            placeholders = ",".join("?" for _ in valid_topic_keys)
            rows = connection.execute(
                f"""
                SELECT topic_key FROM knowledge_topic_registry
                WHERE topic_key IN ({placeholders})
                """,
                valid_topic_keys,
            ).fetchall()
            found = {row["topic_key"] for row in rows}
            missing = sorted(set(valid_topic_keys) - found)
            if missing:
                raise LookupError(f"未找到知识点身份：{', '.join(missing)}")
        connection.execute(
            "DELETE FROM oj_problem_topics WHERE problem_key = ?",
            (valid_problem_key,),
        )
        connection.executemany(
            """
            INSERT INTO oj_problem_topics(
                problem_key, topic_key, mapping_source, note
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (valid_problem_key, topic_key, valid_source, valid_note)
                for topic_key in valid_topic_keys
            ],
        )
    return list_oj_problem_topics(valid_problem_key, db_path)


def list_oj_problem_topics(
    problem_key: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple:
    from study_app.core.oj_identity import validate_problem_key

    valid_key = validate_problem_key(problem_key)
    _require_oj_problem_topics_table(db_path)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT problem_key, topic_key, mapping_source, note
            FROM oj_problem_topics
            WHERE problem_key = ?
            ORDER BY topic_key
            """,
            (valid_key,),
        ).fetchall()
    return tuple(_oj_topic_mapping_from_row(row) for row in rows)


def _require_oj_attempts_table(db_path: Path | str) -> None:
    path = Path(db_path)
    with connect_readonly(path) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oj_attempts'"
        ).fetchone()
    if row is None:
        raise DatabaseNotInitializedError(
            "OJ 提交表尚未安装；现用数据库需先完成独立 CR-F3-01 结构变更"
        )


def _oj_attempt_from_row(row: sqlite3.Row):
    from study_app.core.oj_attempts import OJAttempt, make_oj_attempt_input

    value = make_oj_attempt_input(
        problem_key=row["problem_key"],
        attempted_at=row["attempted_at"],
        result=row["result"],
        duration_seconds=row["duration_seconds"],
        independence=row["independence"],
        hint_level=row["hint_level"],
        error_type=row["error_type"],
        notes=row["notes"],
        source_attempt_key=row["source_attempt_key"],
    )
    return OJAttempt(
        problem_key=value.problem_key,
        attempted_at=value.attempted_at,
        result=value.result,
        duration_seconds=value.duration_seconds,
        independence=value.independence,
        hint_level=value.hint_level,
        error_type=value.error_type,
        notes=value.notes,
        source_attempt_key=value.source_attempt_key,
        attempt_id=int(row["attempt_id"]),
    )


def append_oj_attempt(
    *,
    problem_key: object,
    attempted_at: object,
    result: object,
    duration_seconds: object,
    independence: object,
    hint_level: object,
    error_type: object,
    notes: object = "",
    source_attempt_key: object = None,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    from study_app.core.oj_attempts import (
        attempt_payload_sha256,
        make_oj_attempt_input,
    )

    value = make_oj_attempt_input(
        problem_key=problem_key,
        attempted_at=attempted_at,
        result=result,
        duration_seconds=duration_seconds,
        independence=independence,
        hint_level=hint_level,
        error_type=error_type,
        notes=notes,
        source_attempt_key=source_attempt_key,
    )
    payload_sha256 = attempt_payload_sha256(value)
    _require_oj_problems_table(db_path)
    _require_oj_attempts_table(db_path)
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        if connection.execute(
            "SELECT 1 FROM oj_problems WHERE problem_key = ?",
            (value.problem_key,),
        ).fetchone() is None:
            raise LookupError(f"未找到 OJ 题目：{value.problem_key}")
        existing = None
        if value.source_attempt_key is not None:
            existing = connection.execute(
                "SELECT * FROM oj_attempts WHERE source_attempt_key = ?",
                (value.source_attempt_key,),
            ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] != payload_sha256:
                raise ValueError("source_attempt_key 已存在且载荷不同")
            row = existing
        else:
            cursor = connection.execute(
                """
                INSERT INTO oj_attempts(
                    problem_key, attempted_at, result, duration_seconds,
                    independence, hint_level, error_type, notes,
                    source_attempt_key, payload_sha256
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value.problem_key,
                    value.attempted_at,
                    value.result,
                    value.duration_seconds,
                    value.independence,
                    value.hint_level,
                    value.error_type,
                    value.notes,
                    value.source_attempt_key,
                    payload_sha256,
                ),
            )
            row = connection.execute(
                "SELECT * FROM oj_attempts WHERE attempt_id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
    if row is None:
        raise RuntimeError("OJ 提交写入后不可读")
    return _oj_attempt_from_row(row)


def list_oj_attempts(
    problem_key: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple:
    from study_app.core.oj_identity import validate_problem_key

    valid_key = validate_problem_key(problem_key)
    _require_oj_attempts_table(db_path)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM oj_attempts
            WHERE problem_key = ?
            ORDER BY attempted_at, attempt_id
            """,
            (valid_key,),
        ).fetchall()
    return tuple(_oj_attempt_from_row(row) for row in rows)


def list_oj_evidence_rows(
    topic_key: object,
    *,
    as_of_time: object = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple[dict[str, Any], ...]:
    """Project only explicitly mapped OJ attempts; never infer from title/text."""
    from study_app.core.oj_attempts import normalize_attempted_at
    from study_app.core.topic_identity import validate_topic_key

    valid_topic_key = validate_topic_key(topic_key)
    cutoff = None if as_of_time is None else normalize_attempted_at(as_of_time)
    require_knowledge_topic_registry_table(db_path)
    _require_oj_problems_table(db_path)
    _require_oj_problem_topics_table(db_path)
    _require_oj_attempts_table(db_path)
    with connect_readonly(db_path) as connection:
        if connection.execute(
            "SELECT 1 FROM knowledge_topic_registry WHERE topic_key = ?",
            (valid_topic_key,),
        ).fetchone() is None:
            raise LookupError(f"未找到知识点身份：{valid_topic_key}")
        params: list[Any] = [valid_topic_key]
        cutoff_clause = ""
        if cutoff is not None:
            cutoff_clause = "AND attempts.attempted_at <= ?"
            params.append(cutoff)
        rows = connection.execute(
            f"""
            SELECT
                mappings.topic_key,
                problems.problem_key,
                problems.source_key,
                problems.external_problem_key,
                problems.title,
                mappings.mapping_source,
                attempts.attempt_id,
                attempts.attempted_at,
                attempts.result,
                attempts.duration_seconds,
                attempts.independence,
                attempts.hint_level,
                attempts.error_type,
                attempts.notes
            FROM oj_problem_topics AS mappings
            JOIN oj_problems AS problems
              ON problems.problem_key = mappings.problem_key
            JOIN oj_attempts AS attempts
              ON attempts.problem_key = mappings.problem_key
            WHERE mappings.topic_key = ?
              {cutoff_clause}
            ORDER BY attempts.attempted_at, attempts.attempt_id, problems.problem_key
            """,
            params,
        ).fetchall()
    return tuple(
        {
            key: row[key]
            for key in (
                "topic_key",
                "problem_key",
                "source_key",
                "external_problem_key",
                "title",
                "mapping_source",
                "attempt_id",
                "attempted_at",
                "result",
                "duration_seconds",
                "independence",
                "hint_level",
                "error_type",
                "notes",
            )
        }
        for row in rows
    )
