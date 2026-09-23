from __future__ import annotations

import datetime
import json
import hashlib
import logging
import math
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from study_app.paths import (
    BACKUPS_DIR,
    DATABASE_PATH,
    MODEL_PATH,
    RECORDS_PATH,
    RESOURCE_ROOT,
    ensure_user_layout,
)

ROOT = RESOURCE_ROOT
DEFAULT_DB_PATH = DATABASE_PATH
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
F5_SCHEMA_PATH = Path(__file__).with_name("f5_schema.sql")
LOGGER = logging.getLogger(__name__)
APPLICATION_SCHEMA_VERSION = 5
F5_STRICT_MODE_SETTING = "f5_catalog:strict_mode"
F5_LEGACY_BOOTSTRAP_SETTING = "f5_catalog:legacy_bootstrap_v1"


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


class DatabaseNotInitializedError(RuntimeError):
    """Raised when a read-only repository is opened before explicit setup."""


class DatabaseVersionTooNewError(RuntimeError):
    """Raised before writing when a newer application already owns the DB."""


def _database_schema_version(path: Path) -> int:
    if not path.is_file():
        return 0
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if table is None:
            return 0
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        connection.close()


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(
        f"{source.resolve().as_uri()}?mode=ro", uri=True
    )
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    verification = sqlite3.connect(destination)
    try:
        result = verification.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"数据库备份完整性校验失败：{result}")
    finally:
        verification.close()


def _migration_backup_root(path: Path) -> Path:
    try:
        if path.resolve() == Path(DEFAULT_DB_PATH).resolve():
            return BACKUPS_DIR
    except OSError:
        pass
    return path.parent / "backups"


def _install_topic_identity_schema(connection: sqlite3.Connection) -> None:
    from study_app.core.topic_identity import (
        TOPIC_IDENTITY_VERSION,
        topic_key_for_id,
    )

    connection.execute(KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL)
    connection.execute(KNOWLEDGE_PREREQUISITES_TABLE_SQL)
    connection.execute(KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL)
    connection.execute(KNOWLEDGE_ALERTS_TABLE_SQL)
    connection.execute(KNOWLEDGE_ALERT_EVENTS_TABLE_SQL)
    # sqlite3.executescript() commits any pending transaction before running.
    # Keep these statements explicit so model import + F5 bootstrap can roll
    # back as one unit when identity preflight detects an ambiguity.
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_alerts_status_due
        ON knowledge_alerts(status, snoozed_until, recommended_date)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_alert_events_alert
        ON knowledge_alert_events(alert_id, id)
        """
    )
    existing = {
        int(row[0])
        for row in connection.execute(
            "SELECT topic_id FROM knowledge_topic_registry"
        ).fetchall()
    }
    for row in connection.execute("SELECT id FROM topics ORDER BY id").fetchall():
        topic_id = int(row[0])
        if topic_id in existing:
            continue
        generation = 0
        while True:
            topic_key = topic_key_for_id(topic_id, generation)
            collision = connection.execute(
                "SELECT 1 FROM knowledge_topic_registry WHERE topic_key=?",
                (topic_key,),
            ).fetchone()
            if collision is None:
                break
            generation += 1
        connection.execute(
            """
            INSERT INTO knowledge_topic_registry(topic_key, topic_id, identity_version)
            VALUES (?, ?, ?)
            """,
            (topic_key, topic_id, TOPIC_IDENTITY_VERSION),
        )


def _bootstrap_f5_catalog_from_legacy_tables(
    connection: sqlite3.Connection,
) -> dict[str, int] | None:
    """Atomically give an empty F5 catalog identities for existing legacy rows.

    A non-empty catalog is already authoritative, even if it represents only a
    subset of the legacy tables.  Never guess how such a catalog should be
    merged.  This routine is therefore intentionally all-or-nothing and only
    bootstraps the unambiguous, empty-catalog case.
    """
    from study_app.core.subject_capabilities import CAPABILITY_KEYS
    from study_app.core.subject_identity import normalize_alias, normalize_name

    catalog_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='subject_catalog'"
    ).fetchone()
    if catalog_table is None:
        return None
    if connection.execute("SELECT 1 FROM subject_catalog LIMIT 1").fetchone():
        return None

    legacy_subject_rows = connection.execute(
        "SELECT id, name, source_json FROM subjects ORDER BY id"
    ).fetchall()
    if not legacy_subject_rows:
        return None

    state_row = connection.execute(
        "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
    ).fetchone()
    if state_row is None or int(state_row[0]) != 0:
        raise RuntimeError(
            "F5 目录为空但修订状态非初始值；拒绝自动合并旧学科"
        )

    module_rows = connection.execute(
        "SELECT id, subject_id, name, source_json FROM modules ORDER BY subject_id, id"
    ).fetchall()
    topic_rows = connection.execute(
        """
        SELECT topics.id, topics.module_id, topics.name, registry.topic_key
        FROM topics
        LEFT JOIN knowledge_topic_registry registry ON registry.topic_id=topics.id
        ORDER BY topics.module_id, topics.id
        """
    ).fetchall()

    modules_by_subject: dict[int, list[tuple[int, str, str]]] = {}
    for module_id, subject_id, name, source_json in module_rows:
        modules_by_subject.setdefault(int(subject_id), []).append(
            (int(module_id), str(name), str(source_json or "{}"))
        )
    topics_by_module: dict[int, list[tuple[int, str, str]]] = {}
    for topic_id, module_id, name, topic_key in topic_rows:
        if not topic_key:
            raise RuntimeError(f"旧 topic {topic_id} 缺少 F2 topic_key，无法回填 F5 结构")
        topics_by_module.setdefault(int(module_id), []).append(
            (int(topic_id), str(name), str(topic_key))
        )

    def source_object(raw: object) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    subjects: list[dict[str, Any]] = []
    subject_names: dict[str, int] = {}
    alias_owners: dict[str, int] = {}
    ignored_capability_declarations = 0
    for subject_id_value, raw_name, raw_source in legacy_subject_rows:
        subject_id = int(subject_id_value)
        canonical_name = normalize_name(raw_name, label="legacy subject name")
        canonical_normalized = normalize_alias(canonical_name)
        previous_subject = subject_names.get(canonical_normalized)
        if previous_subject is not None and previous_subject != subject_id:
            raise RuntimeError(
                f"旧学科名在 F5 规范化后冲突：{canonical_name!r}"
            )
        subject_names[canonical_normalized] = subject_id

        source = source_object(raw_source)
        display_value = source.get("display_name")
        display_name = normalize_name(
            display_value if isinstance(display_value, str) and display_value.strip() else canonical_name,
            label="legacy subject display_name",
        )
        lifecycle = source.get("lifecycle")
        lifecycle_status = (
            "archived"
            if isinstance(lifecycle, dict) and lifecycle.get("status") == "archived"
            else "active"
        )

        aliases: list[tuple[str, str]] = [(canonical_normalized, canonical_name)]
        explicit_aliases = source.get("aliases")
        if explicit_aliases is not None:
            if not isinstance(explicit_aliases, list):
                raise RuntimeError(f"旧学科 aliases 必须是列表：{canonical_name!r}")
            for raw_alias in explicit_aliases:
                alias = normalize_name(raw_alias, label="legacy subject alias")
                aliases.append((normalize_alias(alias), alias))
        unique_aliases: list[tuple[str, str]] = []
        for alias_normalized, alias in aliases:
            owner = alias_owners.get(alias_normalized)
            if owner is not None and owner != subject_id:
                raise RuntimeError(f"旧学科别名冲突：{alias!r}")
            alias_owners[alias_normalized] = subject_id
            if alias_normalized not in {item[0] for item in unique_aliases}:
                unique_aliases.append((alias_normalized, alias))

        source_capabilities = source.get("capabilities")
        if not isinstance(source_capabilities, dict):
            source_capabilities = {}
        capabilities: dict[str, bool] = {}
        for capability_key in sorted(CAPABILITY_KEYS):
            declared = source_capabilities.get(capability_key)
            if type(declared) is bool:
                capabilities[capability_key] = declared
            else:
                capabilities[capability_key] = capability_key in {
                    "study_plan",
                    "generic_practice",
                }
                if declared is not None:
                    ignored_capability_declarations += 1
        ignored_capability_declarations += len(
            set(source_capabilities) - set(CAPABILITY_KEYS)
        )

        modules: list[dict[str, Any]] = []
        normalized_modules: set[str] = set()
        for module_id, raw_module_name, raw_module_source in modules_by_subject.get(
            subject_id, []
        ):
            module_name = normalize_name(raw_module_name, label="legacy module name")
            module_normalized = normalize_alias(module_name)
            if module_normalized in normalized_modules:
                raise RuntimeError(
                    f"旧模块名在 F5 规范化后冲突："
                    f"{canonical_name!r} / {module_name!r}"
                )
            normalized_modules.add(module_normalized)
            module_source = source_object(raw_module_source)
            module_display_value = module_source.get("display_name")
            module_display_name = normalize_name(
                module_display_value
                if isinstance(module_display_value, str) and module_display_value.strip()
                else module_name,
                label="legacy module display_name",
            )
            topics: list[tuple[int, str, str]] = []
            normalized_topics: set[str] = set()
            for topic_id, raw_topic_name, topic_key in topics_by_module.get(
                module_id, []
            ):
                topic_name = normalize_name(raw_topic_name, label="legacy topic name")
                topic_normalized = normalize_alias(topic_name)
                if topic_normalized in normalized_topics:
                    raise RuntimeError(
                        f"旧知识点名在 F5 规范化后冲突："
                        f"{canonical_name!r} / {module_name!r} / {topic_name!r}"
                    )
                normalized_topics.add(topic_normalized)
                topics.append((topic_id, topic_name, topic_key))
            modules.append(
                {
                    "module_id": module_id,
                    "canonical_name": module_name,
                    "canonical_name_normalized": module_normalized,
                    "display_name": module_display_name,
                    "topics": topics,
                }
            )
        subjects.append(
            {
                "subject_id": subject_id,
                "canonical_name": canonical_name,
                "canonical_name_normalized": canonical_normalized,
                "display_name": display_name,
                "lifecycle_status": lifecycle_status,
                "aliases": unique_aliases,
                "capabilities": capabilities,
                "modules": modules,
            }
        )

    alias_count = 0
    module_count = 0
    topic_count = 0
    for subject in subjects:
        subject_key = "subject:v1:" + uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO subject_catalog(
                subject_key, canonical_name, canonical_name_normalized,
                display_name, lifecycle_status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                subject_key,
                subject["canonical_name"],
                subject["canonical_name_normalized"],
                subject["display_name"],
                subject["lifecycle_status"],
            ),
        )
        for alias_normalized, alias in subject["aliases"]:
            connection.execute(
                """
                INSERT INTO subject_aliases(alias_normalized, alias, subject_key)
                VALUES (?, ?, ?)
                """,
                (alias_normalized, alias, subject_key),
            )
            alias_count += 1
        for capability_key, declared_supported in subject["capabilities"].items():
            connection.execute(
                """
                INSERT INTO subject_capabilities(
                    subject_key, capability_key, declared_supported
                ) VALUES (?, ?, ?)
                """,
                (subject_key, capability_key, int(declared_supported)),
            )

        structure_version = "structure:v1:" + uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO subject_structure_versions(
                structure_version, subject_key, manifest_version,
                source_reference, is_current
            ) VALUES (?, ?, NULL, 'legacy-bootstrap:application-schema-v5', 1)
            """,
            (structure_version, subject_key),
        )
        for module_order, module in enumerate(subject["modules"]):
            module_key = "module:v1:" + uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO subject_module_identities(
                    module_key, subject_key, canonical_name,
                    canonical_name_normalized, display_name
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    module_key,
                    subject_key,
                    module["canonical_name"],
                    module["canonical_name_normalized"],
                    module["display_name"],
                ),
            )
            connection.execute(
                """
                INSERT INTO subject_structure_modules(
                    structure_version, module_key, module_order
                ) VALUES (?, ?, ?)
                """,
                (structure_version, module_key, module_order),
            )
            module_count += 1
            for topic_order, (_topic_id, _topic_name, topic_key) in enumerate(
                module["topics"]
            ):
                connection.execute(
                    """
                    INSERT INTO subject_structure_topics(
                        structure_version, module_key, topic_key, topic_order,
                        importance_bp, difficulty_bp
                    ) VALUES (?, ?, ?, ?, NULL, NULL)
                    """,
                    (structure_version, module_key, topic_key, topic_order),
                )
                topic_count += 1

    connection.execute(
        "UPDATE subject_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1"
    )
    receipt = {
        "subjects": len(subjects),
        "aliases": alias_count,
        "capabilities": len(subjects) * len(CAPABILITY_KEYS),
        "modules": module_count,
        "topics": topic_count,
        "ignored_capability_declarations": ignored_capability_declarations,
    }
    connection.execute(
        """
        INSERT INTO app_settings(key, value_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP
        """,
        (F5_LEGACY_BOOTSTRAP_SETTING, dumps(receipt)),
    )
    return receipt


def _repair_known_foreign_key_violations(connection: sqlite3.Connection) -> int:
    """Quarantine known historical orphans; reject every unknown FK violation."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_quarantine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            quarantined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_table, source_row_id, reason)
        )
        """
    )
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    repaired = 0
    for violation in violations:
        table_name = str(violation[0])
        row_id = violation[1]
        parent_table = str(violation[2])
        if table_name != "problem_attempts" or parent_table != "learning_records":
            raise RuntimeError(
                "数据库存在未识别的外键违规，已停止升级："
                f"{table_name} -> {parent_table}"
            )
        row = connection.execute(
            "SELECT * FROM problem_attempts WHERE rowid=?", (row_id,)
        ).fetchone()
        if row is None:
            continue
        payload = {key: row[key] for key in row.keys()}
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT OR IGNORE INTO migration_quarantine(
                source_table, source_row_id, reason, payload_json, payload_sha256
            ) VALUES ('problem_attempts', ?, 'missing_learning_record', ?, ?)
            """,
            (str(row_id), payload_json, payload_sha256),
        )
        connection.execute("DELETE FROM problem_attempts WHERE rowid=?", (row_id,))
        repaired += 1
    if repaired:
        receipt = json.dumps(
            {
                "kind": "foreign_key_quarantine",
                "table": "problem_attempts",
                "count": repaired,
                "source_backup_preserved": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO app_settings(key, value_json, updated_at)
            VALUES ('schema_migration:last_repair', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (receipt,),
        )
        LOGGER.warning(
            "Quarantined %s orphan problem_attempts rows during migration", repaired
        )
    remaining = connection.execute("PRAGMA foreign_key_check").fetchall()
    if remaining:
        raise RuntimeError("数据库升级后仍存在外键违规，已停止切换")
    return repaired


def _apply_application_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate_problem_attempts(connection)
    _migrate_llm_call_audits(connection)
    _migrate_practice_collection(connection)
    _install_topic_identity_schema(connection)
    connection.execute(OJ_PROBLEMS_TABLE_SQL)
    connection.execute(OJ_PROBLEM_TOPICS_TABLE_SQL)
    connection.execute(OJ_ATTEMPTS_TABLE_SQL)
    connection.execute(OJ_ATTEMPTS_INDEX_SQL)
    connection.executescript(F5_SCHEMA_PATH.read_text(encoding="utf-8"))
    _bootstrap_f5_catalog_from_legacy_tables(connection)
    _repair_known_foreign_key_violations(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (APPLICATION_SCHEMA_VERSION,),
    )


def _validate_migrated_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"数据库完整性校验失败：{integrity}")
        schema_sources = (
            SCHEMA_PATH.read_text(encoding="utf-8"),
            F5_SCHEMA_PATH.read_text(encoding="utf-8"),
            KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL,
            KNOWLEDGE_PREREQUISITES_TABLE_SQL,
            KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL,
            KNOWLEDGE_ALERTS_TABLE_SQL,
            KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
            KNOWLEDGE_ALERT_INDEXES_SQL,
            OJ_PROBLEMS_TABLE_SQL,
            OJ_PROBLEM_TOPICS_TABLE_SQL,
            OJ_ATTEMPTS_TABLE_SQL,
            OJ_ATTEMPTS_INDEX_SQL,
        )
        expected: dict[str, set[str]] = {
            "table": set(),
            "index": set(),
            "trigger": set(),
        }
        patterns = {
            "table": r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
            "index": r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
            "trigger": r"CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
        }
        for source in schema_sources:
            for object_type, pattern in patterns.items():
                expected[object_type].update(re.findall(pattern, source, re.IGNORECASE))
        present: dict[str, set[str]] = {}
        for object_type in expected:
            present[object_type] = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type=?", (object_type,)
                ).fetchall()
            }
        missing_objects = [
            f"{object_type}:{name}"
            for object_type, names in expected.items()
            for name in sorted(names - present[object_type])
        ]
        if missing_objects:
            raise RuntimeError(
                "数据库升级缺少结构：" + ", ".join(missing_objects)
            )
        required_columns = {
            "app_settings": {"key", "value_json", "updated_at"},
            "learning_records": {
                "id",
                "record_date",
                "subject_name",
                "raw_json",
            },
            "problem_attempts": {"id", "record_id", "title", "raw_json"},
            "knowledge_topic_registry": {
                "topic_key",
                "topic_id",
                "identity_version",
            },
            "subject_catalog": {
                "subject_key",
                "canonical_name",
                "lifecycle_status",
                "object_version",
            },
        }
        for table_name, columns in required_columns.items():
            actual_columns = {
                row[1]
                for row in connection.execute(
                    f'PRAGMA table_info("{table_name}")'
                ).fetchall()
            }
            missing_columns = columns - actual_columns
            if missing_columns:
                raise RuntimeError(
                    f"数据库表 {table_name} 缺少字段："
                    + ", ".join(sorted(missing_columns))
                )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("数据库升级后存在外键违规")
    finally:
        connection.close()


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


def initialize_database(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Install or upgrade every application schema through an atomic candidate DB."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    current_version = _database_schema_version(path)
    if current_version > APPLICATION_SCHEMA_VERSION:
        raise DatabaseVersionTooNewError(
            "数据库由更高版本的学习模型创建；为避免降级破坏，当前版本已停止写入。"
        )

    if path.is_file() and current_version == APPLICATION_SCHEMA_VERSION:
        try:
            _validate_migrated_database(path)
        except RuntimeError:
            pass
        else:
            return path

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if path.is_file():
        backup_root = _migration_backup_root(path)
        backup_path = backup_root / (
            f"{path.stem}-before-schema-{current_version}-to-"
            f"{APPLICATION_SCHEMA_VERSION}-{stamp}.sqlite"
        )
        _sqlite_backup(path, backup_path)

    candidate = path.with_name(f".{path.name}.migration-{uuid.uuid4().hex}.tmp")
    try:
        if path.is_file():
            _sqlite_backup(path, candidate)
        with connect(candidate) as connection:
            _apply_application_schema(connection)
        _validate_migrated_database(candidate)
        os.replace(candidate, path)
    except Exception:
        candidate.unlink(missing_ok=True)
        LOGGER.exception("Database initialization or migration failed; live DB was preserved")
        raise
    return path


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
NORMALIZATION_VERSION = "data_contract_v1@v1.0.2"


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


def _validate_problem_difficulty_fields(problem: dict) -> None:
    """校验题目显式难度字段（data_contract_v1 §4.2.5-6）：字符串数值/布尔/越界/非有限一律拒绝。"""
    for key in ("difficulty_score", "difficulty_value", "difficulty_percent"):
        value = problem.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 必须是数值（字符串数值与布尔值不接受）：{value!r}")
        number = float(value)
        if not math.isfinite(number) or number < 0 or number > 100:
            raise ValueError(f"{key} 必须是 0-100 范围内的有限数值：{value!r}")
    for key in ("correctness", "partial_credit"):
        value = problem.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 必须是数值（布尔与字符串数值不接受）：{value!r}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{key} 必须是有限数值：{value!r}")
        if key == "partial_credit":
            if number < 0 or number > 1:
                raise ValueError(f"partial_credit 必须在 0-1 范围内（不接受百分数形式）：{value!r}")
        else:
            if number < 0 or number > 100:
                raise ValueError(f"{key} 必须在 0-100 范围内（>100 拒绝，不截断）：{value!r}")
    related = problem.get("related_topics")
    if related is not None:
        if not isinstance(related, list):
            raise ValueError("related_topics 必须是字符串数组")
        for item in related:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"related_topics 元素必须是非空字符串：{item!r}")
    label_value = problem.get("difficulty")
    if label_value is not None:
        if isinstance(label_value, bool) or not isinstance(label_value, str):
            raise ValueError(f"difficulty 必须是字符串标签或数值字段 difficulty_score：{label_value!r}")
        stripped_label = label_value.strip()
        if not stripped_label:
            pass
        else:
            try:
                float(stripped_label)
            except ValueError:
                pass
            else:
                raise ValueError(f"difficulty 字符串数值不接受，请改用 difficulty_score：{label_value!r}")


def _migrate_problem_attempts(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(problem_attempts)").fetchall()
    }
    additions = {
        "statement": "TEXT",
        "error_cause": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE problem_attempts ADD COLUMN {name} {definition}")


def _migrate_llm_call_audits(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_call_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            feature TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            estimated_tokens INTEGER NOT NULL DEFAULT 0,
            upload_summary_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            error_message TEXT
        )
        """
    )
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(llm_call_audits)").fetchall()
    }
    if "parent_audit_id" not in columns:
        connection.execute(
            "ALTER TABLE llm_call_audits ADD COLUMN parent_audit_id INTEGER REFERENCES llm_call_audits(id)"
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_call_audits_created
        ON llm_call_audits(created_at DESC)
        """
    )


def _migrate_practice_collection(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS practice_collection_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            discovered_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS practice_collection_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            institution TEXT NOT NULL,
            subject_hint TEXT,
            topic_hint TEXT,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            document_type TEXT NOT NULL DEFAULT 'assignment',
            quality_score REAL NOT NULL DEFAULT 0,
            estimated_difficulty REAL,
            status TEXT NOT NULL DEFAULT 'discovered',
            discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            raw_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_practice_collection_candidates_quality
        ON practice_collection_candidates(status, quality_score DESC, estimated_difficulty DESC);
        CREATE TABLE IF NOT EXISTS practice_collection_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL DEFAULT '',
            template_id TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            target_difficulty REAL,
            request_count INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            first_requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            UNIQUE(subject, template_id, topic)
        );
        CREATE INDEX IF NOT EXISTS idx_practice_collection_backlog_priority
        ON practice_collection_backlog(status, request_count DESC, last_requested_at DESC);
        """
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


def mark_existing_text_integrity_issues(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    from study_app.data.text_integrity import scan_sqlite_text_integrity

    path = initialize_database(db_path)
    ignored_prefixes = ("app_settings[", "alerts[", "llm_call_audits[")
    issues = [
        issue
        for issue in scan_sqlite_text_integrity(path)
        if not issue.path.startswith(ignored_prefixes)
    ]
    payload = {
        "status": "issues_found" if issues else "clean",
        "issue_count": len(issues),
        "issues": [issue.as_dict() for issue in issues[:120]],
        "note": (
            "历史文本中检测到疑似编码损坏；原始字段未被修改。"
            if issues
            else "未检测到疑似编码损坏。"
        ),
    }
    with connect(path) as connection:
        upsert_setting(connection, "text_integrity_historical_issues", payload)
        if issues:
            existing = connection.execute(
                """
                SELECT id FROM alerts
                WHERE alert_type = 'text_integrity'
                  AND dismissed_at IS NULL
                LIMIT 1
                """
            ).fetchone()
            if not existing:
                connection.execute(
                    """
                    INSERT INTO alerts(
                        alert_type, severity, title, detail, source_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "text_integrity",
                        "warning",
                        "检测到历史编码损坏数据",
                        f"发现 {len(issues)} 处疑似历史编码损坏；原始数据未被修改，已在设置中保存清单。",
                        dumps(payload),
                    ),
                )
    return payload


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


def clear_imported_model(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM subjects")
    connection.execute("DELETE FROM learning_records")


def import_model_json(connection: sqlite3.Connection, model: dict[str, Any]) -> None:
    score_items = model.get("initial_percent_assessment", {}).get("subjects", [])
    initial_scores = {item.get("name"): item.get("initial_score") for item in score_items}

    upsert_setting(connection, "warning_policy", model.get("warning_policy", {}))
    upsert_setting(connection, "scale", model.get("scale", {}))

    for subject in model.get("subjects", []):
        cursor = connection.execute(
            """
            INSERT INTO subjects(name, weight, initial_score, mastery, status, current_anchor, source_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                weight = excluded.weight,
                initial_score = excluded.initial_score,
                mastery = excluded.mastery,
                status = excluded.status,
                current_anchor = excluded.current_anchor,
                source_json = excluded.source_json,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                subject.get("name"),
                subject.get("weight", 0),
                initial_scores.get(subject.get("name")),
                _weighted_mastery(subject),
                subject.get("status"),
                subject.get("current_anchor"),
                dumps(subject),
            ),
        )
        subject_id = cursor.fetchone()["id"]

        for module in subject.get("modules", []):
            cursor = connection.execute(
                """
                INSERT INTO modules(subject_id, name, weight, mastery, confidence, status, source_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject_id, name) DO UPDATE SET
                    weight = excluded.weight,
                    mastery = excluded.mastery,
                    confidence = excluded.confidence,
                    status = excluded.status,
                    source_json = excluded.source_json
                RETURNING id
                """,
                (
                    subject_id,
                    module.get("name"),
                    module.get("weight", 0),
                    module.get("mastery", 0),
                    module.get("confidence"),
                    module.get("status"),
                    dumps(module),
                ),
            )
            module_id = cursor.fetchone()["id"]

            for topic in module.get("topics", []):
                connection.execute(
                    """
                    INSERT INTO topics(
                        module_id, name, status, mastery, importance, difficulty, forgetting_risk, source_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(module_id, name) DO UPDATE SET
                        status = excluded.status,
                        mastery = excluded.mastery,
                        importance = excluded.importance,
                        difficulty = excluded.difficulty,
                        forgetting_risk = excluded.forgetting_risk,
                        source_json = excluded.source_json
                    """,
                    (
                        module_id,
                        topic.get("name"),
                        topic.get("status"),
                        topic.get("mastery", 0),
                        topic.get("importance", 0),
                        topic.get("difficulty", 0),
                        topic.get("forgetting_risk", 0),
                        dumps(topic),
                    ),
                )
    # A freshly initialized database installed F2/F5 before any model topics
    # existed.  Fill topic identities now, then atomically establish the F5
    # catalog while it is still unambiguously empty.  Existing catalogs remain
    # authoritative and are never merged by this compatibility import.
    _install_topic_identity_schema(connection)
    _bootstrap_f5_catalog_from_legacy_tables(connection)


def import_records_json(
    connection: sqlite3.Connection,
    records: list[dict[str, Any]],
    *,
    strict: bool = True,
    _allow_unregistered_historical: bool = False,
) -> None:
    normalized_records = []
    for record in records:
        if strict:
            validate_calendar_date(record.get("date"), label="record.date")
            from study_app.data.text_integrity import validate_text_integrity

            validate_text_integrity(record, context="学习记录")
            score_value = _optional_float(record.get("score"), label="score")
            if score_value is not None and not 0 <= score_value <= 100:
                raise ValueError(f"score 必须在 0-100 范围内：{record.get('score')!r}")
            duration_value = _optional_float(
                record.get("duration_minutes"), label="duration_minutes"
            )
            if duration_value is not None and duration_value <= 0:
                raise ValueError(f"duration_minutes 必须大于 0：{record.get('duration_minutes')!r}")
        enriched_problems = []
        for problem in record.get("problems") or []:
            if strict:
                _validate_problem_difficulty_fields(problem)
            enriched_problems.append(
                _with_inferred_problem_data(record, problem)
            )
        stored = {**record}
        if record.get("problems") is not None:
            stored["problems"] = enriched_problems
        if not strict:
            stored["data_warning"] = "legacy_lenient_import"
        normalized_records.append(stored)
    f5_versions: dict[str, int] = {}
    for record in normalized_records:
        try:
            gate = _require_f5_subject_write_allowed(
                connection, record.get("subject")
            )
        except ValueError:
            if not _allow_unregistered_historical:
                raise
            # A trusted first-run legacy seed may contain historical records
            # for a subject no longer present in the model.  Preserve that
            # evidence, but do not create or guess a formal F5 identity.
            gate = None
        if gate is not None:
            f5_versions[gate[0]] = gate[1]
    connection.execute("DELETE FROM learning_records")
    for record in normalized_records:
        cursor = connection.execute(
            """
            INSERT INTO learning_records(
                record_date, subject_name, module_name, topic_name, activity, source,
                score, duration_minutes, note, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("date"),
                record.get("subject"),
                record.get("module"),
                record.get("topic"),
                record.get("activity"),
                record.get("source"),
                record.get("score"),
                record.get("duration_minutes"),
                record.get("note"),
                dumps(record),
            ),
        )
        record_id = cursor.lastrowid

        for problem in record.get("problems") or []:
            connection.execute(
                """
                INSERT INTO problem_attempts(
                    record_id, title, statement, status, correctness, difficulty_label,
                    difficulty_score, error_cause, related_topics_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    _problem_title(problem, record.get("topic", "未命名题目")),
                    _problem_statement(problem),
                    problem.get("status"),
                    _problem_correctness(problem),
                    problem.get("difficulty"),
                    problem.get("difficulty_score"),
                    _problem_error_cause(problem),
                    dumps(problem.get("related_topics", [])),
                    dumps(problem),
                ),
            )
    for subject_key, version in f5_versions.items():
        changed = connection.execute(
            """
            UPDATE subject_catalog
            SET object_version=object_version+1, updated_at=CURRENT_TIMESTAMP
            WHERE subject_key=? AND lifecycle_status='active' AND object_version=?
            """,
            (subject_key, version),
        ).rowcount
        if changed != 1:
            raise ValueError("学科版本在记录导入期间发生变化")


def import_current_json_files(
    db_path: Path | str = DEFAULT_DB_PATH,
    model_path: Path | str = MODEL_PATH,
    records_path: Path | str = RECORDS_PATH,
    skip_if_populated: bool = False,
    strict: bool = True,
) -> Path:
    path = initialize_database(db_path)
    counts = get_counts(path)
    if skip_if_populated and counts["subjects"] > 0:
        return path
    model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    records_data = json.loads(Path(records_path).read_text(encoding="utf-8"))
    with connect(path) as connection:
        catalog_was_empty = connection.execute(
            "SELECT 1 FROM subject_catalog LIMIT 1"
        ).fetchone() is None
        clear_imported_model(connection)
        import_model_json(connection, model)
        import_records_json(
            connection,
            records_data.get("records", []),
            strict=strict,
            _allow_unregistered_historical=catalog_was_empty and not strict,
        )
        upsert_setting(connection, F5_STRICT_MODE_SETTING, True)
    return path


def get_counts(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, int]:
    tables = ["subjects", "modules", "topics", "learning_records", "problem_attempts"]
    with connect_readonly(db_path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in tables
        }


def ensure_seeded_database(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    model_path: Path | str | None = None,
) -> Path:
    try:
        is_default_database = Path(db_path).resolve() == Path(DEFAULT_DB_PATH).resolve()
    except OSError:
        is_default_database = False
    if is_default_database:
        ensure_user_layout()
    path = initialize_database(db_path)
    counts = get_counts(path)
    with connect(path) as connection:
        seeded = connection.execute(
            "SELECT value_json FROM app_settings WHERE key='initial_seed_completed'"
        ).fetchone()
    if counts["subjects"] == 0 and seeded is None:
        if model_path is not None:
            model = json.loads(Path(model_path).read_text(encoding="utf-8"))
            with connect(path) as connection:
                import_model_json(connection, model)
        elif is_default_database and MODEL_PATH.exists() and RECORDS_PATH.exists():
            import_current_json_files(
                path,
                model_path=MODEL_PATH,
                records_path=RECORDS_PATH,
                strict=False,
                skip_if_populated=True,
            )
        elif (
            not is_default_database
            and (ROOT / "learning_model_v1.json").exists()
            and (ROOT / "learning_records.json").exists()
        ):
            # 旧版 JSON 兼容路径（data_contract_v1 §4.4.4「可诊断兼容路径」
            # 与 A-05 必须测试项「旧数据兼容策略可被测试」）：种子导入保持宽松，
            # 不做 strict 校验；差异由读取侧 consistency_diag/data_warning 标记。
            # 直接调用 import_current_json_files 的外部入口默认 strict=True。
            import_current_json_files(
                path,
                model_path=ROOT / "learning_model_v1.json",
                records_path=ROOT / "learning_records.json",
                strict=False,
                skip_if_populated=True,
            )
        with connect(path) as connection:
            connection.execute(
                """
                INSERT INTO app_settings(key, value_json, updated_at)
                VALUES ('initial_seed_completed', 'true', CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json='true', updated_at=CURRENT_TIMESTAMP
                """
            )
    # Startup has now completed the only trusted seed path.  An empty clean
    # model remains a strict F5 database: ordinary writes must first introduce
    # a formal subject through subject management.
    with connect(path) as connection:
        # A current-schema database may still contain legacy rows inserted by
        # an older caller after schema installation.  Complete their F2/F5
        # identities before enabling the strict write gate; otherwise valid
        # legacy subjects would become unreadable as soon as startup marks the
        # database strict.
        _install_topic_identity_schema(connection)
        _bootstrap_f5_catalog_from_legacy_tables(connection)
        upsert_setting(connection, F5_STRICT_MODE_SETTING, True)
    return path


def add_learning_record(
    record: dict[str, Any],
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    study_plan_item_id: int | None = None,
    study_plan_result: str | None = None,
    model_path: Path | str | None = None,
) -> int:
    from study_app.data.text_integrity import validate_text_integrity

    validate_text_integrity(record, context="学习记录")
    validate_calendar_date(record.get("date"))
    # A-05（data_contract_v1 §4.4）：在第一条数据库写入之前完成一次规范化。
    normalized_problems = None
    if record.get("problems") is not None:
        normalized_problems = []
        for problem in record["problems"]:
            _validate_problem_difficulty_fields(problem)
            normalized_problems.append(
                _with_inferred_problem_data(record, problem)
            )
    normalized = {
        "date": record.get("date"),
        "subject": record.get("subject"),
        "module": record.get("module") or None,
        "topic": record.get("topic") or None,
        "activity": record.get("activity") or "review",
        "source": record.get("source") or "outside_class",
        "score": _optional_float(record.get("score")),
        "duration_minutes": _optional_float(record.get("duration_minutes")),
        "note": record.get("note") or "",
    }
    if not normalized["date"]:
        raise ValueError("record.date is required")
    if not normalized["subject"]:
        raise ValueError("record.subject is required")
    if normalized["score"] is not None and not 0 <= normalized["score"] <= 100:
        raise ValueError("record.score 必须在 0-100 范围内")
    if normalized["duration_minutes"] is not None and normalized["duration_minutes"] <= 0:
        raise ValueError("record.duration_minutes 必须大于 0")
    if (study_plan_item_id is None) != (study_plan_result is None):
        raise ValueError("study plan item id and result must be provided together")
    if study_plan_result is not None and study_plan_result not in {"correct", "wrong"}:
        raise ValueError("study plan result must be 'correct' or 'wrong'")
    path = require_initialized_database(db_path)

    raw_record = {**record, **normalized}
    raw_record["_normalization_version"] = NORMALIZATION_VERSION
    if normalized_problems is not None:
        raw_record["problems"] = normalized_problems
    practice_candidates: list[dict[str, Any]] = []
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        f5_gate = _require_f5_subject_write_allowed(
            connection, normalized["subject"]
        )
        cursor = connection.execute(
            """
            INSERT INTO learning_records(
                record_date, subject_name, module_name, topic_name, activity, source,
                score, duration_minutes, note, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["date"],
                normalized["subject"],
                normalized["module"],
                normalized["topic"],
                normalized["activity"],
                normalized["source"],
                normalized["score"],
                normalized["duration_minutes"],
                normalized["note"],
                dumps(raw_record),
            ),
        )
        record_id = int(cursor.lastrowid)
        raw_record["id"] = record_id
        for problem in raw_record.get("problems", []) or []:
            practice_candidates.append(problem)
            connection.execute(
                """
                INSERT INTO problem_attempts(
                    record_id, title, statement, status, correctness, difficulty_label,
                    difficulty_score, error_cause, related_topics_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    _problem_title(problem, raw_record.get("topic", "未命名题目")),
                    _problem_statement(problem),
                    problem.get("status"),
                    _problem_correctness(problem),
                    problem.get("difficulty"),
                    problem.get("difficulty_score"),
                    _problem_error_cause(problem),
                    dumps(problem.get("related_topics", [])),
                    dumps(problem),
                ),
            )
        for attachment in raw_record.get("attachments", []) or []:
            path_obj = Path(attachment.get("file_path", ""))
            connection.execute(
                """
                INSERT INTO record_attachments(
                    record_id, file_path, file_name, file_ext, mime_hint, file_size, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    str(path_obj),
                    attachment.get("file_name") or path_obj.name,
                    attachment.get("file_ext") or path_obj.suffix.lower(),
                    attachment.get("mime_hint"),
                    attachment.get("file_size"),
                    attachment.get("note"),
                ),
            )
        if study_plan_item_id is not None:
            pending_item = connection.execute(
                """
                SELECT items.id
                FROM study_plan_items AS items
                LEFT JOIN study_plan_item_states AS states ON states.item_id = items.id
                WHERE items.id = ? AND states.result IS NULL
                """,
                (study_plan_item_id,),
            ).fetchone()
            if pending_item is None:
                raise ValueError("study plan item not found or already completed")
            connection.execute(
                """
                INSERT INTO study_plan_item_states(item_id, checked, result)
                VALUES (?, 1, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    checked = 1,
                    result = excluded.result,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (study_plan_item_id, study_plan_result),
            )
        if f5_gate is not None:
            changed = connection.execute(
                """
                UPDATE subject_catalog
                SET object_version=object_version+1, updated_at=CURRENT_TIMESTAMP
                WHERE subject_key=? AND lifecycle_status='active' AND object_version=?
                """,
                f5_gate,
            ).rowcount
            if changed != 1:
                raise ValueError("学科版本在学习记录写入期间发生变化")
    _auto_import_practice_problems(raw_record, practice_candidates, db_path=path)
    try:
        from study_app.data.model_progress_sync import sync_learning_record_to_model

        if model_path is None:
            changed_topics = sync_learning_record_to_model(raw_record, db_path=path)
        else:
            changed_topics = sync_learning_record_to_model(
                raw_record,
                model_path=model_path,
                db_path=path,
            )
        if changed_topics:
            set_setting(
                "model_progress_sync_last_result",
                {
                    "record_id": record_id,
                    "subject": normalized["subject"],
                    "changed_topics": changed_topics,
                    "status": "success",
                },
                db_path=path,
            )
    except Exception as error:
        # Preserve the learning record, but persist the sync failure for diagnosis.
        try:
            set_setting(
                "model_progress_sync_last_result",
                {
                    "record_id": record_id,
                    "subject": normalized["subject"],
                    "status": "failed",
                    "error": str(error),
                },
                db_path=path,
            )
        except Exception:
            LOGGER.exception(
                "Learning record %s was committed, but model-sync diagnostics could not be saved",
                record_id,
            )
    return record_id


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


def _require_f5_subject_write_allowed(
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


def get_f5_subject_lifecycle_status(
    subject_name: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str | None:
    try:
        with connect_readonly(db_path) as connection:
            if not f5_catalog_is_installed(connection):
                return None
            from study_app.core.subject_identity import normalize_alias

            row = connection.execute(
                """
                SELECT catalog.lifecycle_status
                FROM subject_aliases aliases
                JOIN subject_catalog catalog ON catalog.subject_key=aliases.subject_key
                WHERE aliases.alias_normalized=?
                """,
                (normalize_alias(subject_name),),
            ).fetchone()
    except DatabaseNotInitializedError:
        return None
    if row is None:
        raise LookupError(f"学科尚未登记到 F5 目录：{subject_name!r}")
    return str(row["lifecycle_status"])


def list_subject_names(db_path: Path | str = DEFAULT_DB_PATH) -> list[str]:
    with connect_readonly(db_path) as connection:
        rows = connection.execute("SELECT name FROM subjects ORDER BY id").fetchall()
    return [row["name"] for row in rows]


def list_module_names(subject_name: str, db_path: Path | str = DEFAULT_DB_PATH) -> list[str]:
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT modules.name
            FROM modules
            JOIN subjects ON subjects.id = modules.subject_id
            WHERE subjects.name = ?
            ORDER BY modules.id
            """,
            (subject_name,),
        ).fetchall()
    return [row["name"] for row in rows]


def load_raw_records(db_path: Path | str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with connect_readonly(db_path) as connection:
        return load_raw_records_from_connection(connection)


def load_raw_records_from_connection(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, record_date, subject_name, module_name, topic_name, activity,
               source, score, duration_minutes, note, raw_json
        FROM learning_records
        ORDER BY record_date, id
        """
    ).fetchall()
    attempts_by_record: dict[int, list[sqlite3.Row]] = {}
    for attempt_row in connection.execute(
        """
        SELECT record_id, title, statement, status, correctness, difficulty_label,
               difficulty_score, error_cause, related_topics_json
        FROM problem_attempts
        ORDER BY id
        """
    ):
        attempts_by_record.setdefault(attempt_row["record_id"], []).append(attempt_row)
    attachments_by_record: dict[int, list[dict[str, Any]]] = {}
    for attachment_row in connection.execute(
        """
        SELECT record_id, file_path, file_name, file_ext, mime_hint, file_size, note
        FROM record_attachments
        ORDER BY id
        """
    ):
        attachments_by_record.setdefault(attachment_row["record_id"], []).append(
            {
                key: attachment_row[key]
                for key in (
                    "file_path", "file_name", "file_ext", "mime_hint", "file_size", "note"
                )
            }
        )
    records = []
    for row in rows:
        try:
            record = json.loads(row["raw_json"])
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            record = None
        attempts_for_record = attempts_by_record.get(row["id"], [])
        if not isinstance(record, dict):
            record = _rebuild_record_from_normalized_rows(row, attempts_for_record)
        if record.get("_revoked") is True:
            continue
        record["id"] = row["id"]
        try:
            validate_calendar_date(row["record_date"], label="record_date")
        except ValueError:
            record["data_warning"] = "invalid_date"
        problems = record.get("problems") or []
        count_mismatch = len(problems) != len(attempts_for_record)
        mismatches = []
        for problem_index, (problem_item, attempt_item) in enumerate(
            zip(problems, attempts_for_record), start=1
        ):
            diff_fields = []
            raw_value = problem_item.get("partial_credit")
            if raw_value is None and isinstance(
                problem_item.get("correctness"), (int, float)
            ) and not isinstance(problem_item.get("correctness"), bool):
                cv = float(problem_item["correctness"])
                raw_value = cv / 100 if cv > 1 else cv
            attempt_value = attempt_item["correctness"]
            if (
                raw_value is not None
                and attempt_value is not None
                and abs(float(raw_value) - float(attempt_value)) > 1e-9
            ):
                diff_fields.append("correctness")
            if (problem_item.get("status") or None) != (attempt_item["status"] or None):
                diff_fields.append("status")
            if (problem_item.get("difficulty_score") or None) != (
                attempt_item["difficulty_score"] or None
            ):
                diff_fields.append("difficulty_score")
            if str(problem_item.get("title") or "") != str(
                attempt_item["title"] or ""
            ):
                diff_fields.append("title")
            if (problem_item.get("error_cause") or None) != (
                attempt_item["error_cause"] or None
            ):
                diff_fields.append("error_cause")
            try:
                attempt_related = json.loads(
                    attempt_item["related_topics_json"] or "[]"
                )
            except (json.JSONDecodeError, TypeError):
                attempt_related = None
            if (problem_item.get("related_topics") or []) != (attempt_related or []):
                diff_fields.append("related_topics")
            if diff_fields:
                mismatches.append(
                    {
                        "index": problem_index,
                        "title": attempt_item["title"],
                        "fields": diff_fields,
                        "raw_correctness": raw_value,
                        "attempt_correctness": attempt_value,
                    }
                )
        if count_mismatch or mismatches:
            record["consistency_diag"] = {
                "problem_count_raw": len(problems),
                "problem_count_attempts": len(attempts_for_record),
                "count_mismatch": count_mismatch,
                "mismatch_count": len(mismatches),
                "fields": sorted(
                    {field for item in mismatches for field in item["fields"]}
                ),
                "first": mismatches[0] if mismatches else None,
            }
        attachments = attachments_by_record.get(row["id"], [])
        if attachments:
            record["attachments"] = attachments
        records.append(record)
    return records


def _rebuild_record_from_normalized_rows(
    row: sqlite3.Row,
    problem_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    record = {
        "date": row["record_date"],
        "subject": row["subject_name"],
        "module": row["module_name"],
        "topic": row["topic_name"],
        "activity": row["activity"],
        "source": row["source"],
        "score": row["score"],
        "duration_minutes": row["duration_minutes"],
        "note": row["note"],
        "data_warning": "invalid_raw_json",
    }
    problems = []
    for problem_row in problem_rows:
        try:
            related_topics = json.loads(problem_row["related_topics_json"] or "[]")
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            related_topics = []
        if not isinstance(related_topics, list):
            related_topics = []
        correctness = problem_row["correctness"]
        problems.append(
            {
                "title": problem_row["title"],
                "statement": problem_row["statement"],
                "status": problem_row["status"],
                "correctness": correctness * 100 if correctness is not None else None,
                "partial_credit": correctness,
                "difficulty": problem_row["difficulty_label"],
                "difficulty_score": problem_row["difficulty_score"],
                "error_cause": problem_row["error_cause"],
                "related_topics": related_topics,
            }
        )
    if problems:
        record["problems"] = problems
    return record


def list_recent_records(db_path: Path | str = DEFAULT_DB_PATH, limit: int = 20) -> list[dict[str, Any]]:
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, record_date, subject_name, module_name, topic_name, activity, source, score, note
            FROM learning_records
            ORDER BY record_date DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        record_ids = [row["id"] for row in rows]
        attachments_by_record: dict[int, list[dict[str, Any]]] = {}
        problem_counts: dict[int, int] = {}
        if record_ids:
            placeholders = ",".join("?" for _ in record_ids)
            for attachment in connection.execute(
                f"""
                SELECT record_id, file_path, file_name, file_ext, file_size
                FROM record_attachments
                WHERE record_id IN ({placeholders})
                ORDER BY id
                """,
                record_ids,
            ):
                item = dict(attachment)
                record_id = int(item.pop("record_id"))
                attachments_by_record.setdefault(record_id, []).append(item)
            for count_row in connection.execute(
                f"""
                SELECT record_id, COUNT(*) AS count
                FROM problem_attempts
                WHERE record_id IN ({placeholders})
                GROUP BY record_id
                """,
                record_ids,
            ):
                problem_counts[int(count_row["record_id"])] = int(count_row["count"])
        result = []
        for row in rows:
            item = dict(row)
            item["attachments"] = attachments_by_record.get(row["id"], [])
            item["problem_count"] = problem_counts.get(row["id"], 0)
            result.append(item)
    return result


def study_plan_signature(state: dict[str, Any]) -> str:
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


STUDY_DAY_BUDGET_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS study_day_budgets (
    plan_date TEXT PRIMARY KEY,
    available_minutes INTEGER NOT NULL
        CHECK(typeof(available_minutes) = 'integer' AND available_minutes BETWEEN 0 AND 1440),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _require_study_day_budget_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'study_day_budgets'"
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "今日预算数据表尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def save_study_day_budget(
    plan_date: object,
    available_minutes: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Validate both fields before opening a write connection."""
    from study_app.core.day_budget_input import validate_day_budget_input

    budget = validate_day_budget_input(plan_date, available_minutes)
    path = require_initialized_database(db_path)
    _require_study_day_budget_table(path)
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO study_day_budgets(plan_date, available_minutes)
            VALUES (?, ?)
            ON CONFLICT(plan_date) DO UPDATE SET
                available_minutes = excluded.available_minutes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (budget.plan_date, budget.available_minutes),
        )
    return budget


def get_study_day_budget(
    plan_date: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Read an existing day's budget without initializing or changing schema."""
    from study_app.core.day_budget_input import StudyDayBudget, validate_day_budget_date

    valid_date = validate_day_budget_date(plan_date)
    _require_study_day_budget_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            "SELECT plan_date, available_minutes FROM study_day_budgets WHERE plan_date = ?",
            (valid_date,),
        ).fetchone()
    if row is None:
        return None
    return StudyDayBudget(
        plan_date=row["plan_date"],
        available_minutes=row["available_minutes"],
    )


STUDY_SUBJECT_EXAM_DATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS study_subject_exam_dates (
    subject_id INTEGER PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
    exam_date TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _require_subject_exam_dates_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'study_subject_exam_dates'"
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "学科考试日期数据表尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def save_subject_exam_date(
    state: object,
    subject_name: str,
    exam_date: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str | None:
    """Only a current activity subject can set or clear its exam date."""
    from study_app.core.active_subjects import require_activity_subject
    from study_app.core.day_budget_input import validate_subject_exam_date

    valid_date = validate_subject_exam_date(exam_date)
    require_activity_subject(state, subject_name, "修改考试日期")
    path = require_initialized_database(db_path)
    _require_subject_exam_dates_table(path)
    with connect(path) as connection:
        subject = connection.execute(
            "SELECT id, status FROM subjects WHERE name = ?",
            (subject_name,),
        ).fetchone()
        if subject is None:
            raise LookupError(f"未找到学科：{subject_name}")
        if subject["status"] == "archived":
            raise ValueError(f"{subject_name} 已封存，不再修改考试日期。")
        connection.execute(
            """
            INSERT INTO study_subject_exam_dates(subject_id, exam_date)
            VALUES (?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET
                exam_date = excluded.exam_date,
                updated_at = CURRENT_TIMESTAMP
            """,
            (subject["id"], valid_date),
        )
    return valid_date


def get_subject_exam_date(
    subject_name: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str | None:
    """Historical reads remain available after a subject is archived."""
    from study_app.core.day_budget_input import validate_subject_exam_date

    _require_subject_exam_dates_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            """
            SELECT subjects.id, study_subject_exam_dates.exam_date
            FROM subjects
            LEFT JOIN study_subject_exam_dates
                ON study_subject_exam_dates.subject_id = subjects.id
            WHERE subjects.name = ?
            """,
            (subject_name,),
        ).fetchone()
    if row is None:
        raise LookupError(f"未找到学科：{subject_name}")
    return validate_subject_exam_date(row["exam_date"])


STUDY_TASK_ESTIMATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS study_task_estimates (
    task_id TEXT PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    estimated_minutes INTEGER NOT NULL
        CHECK(typeof(estimated_minutes) = 'integer' AND estimated_minutes BETWEEN 1 AND 1440),
    source TEXT NOT NULL CHECK(source IN ('user', 'confirmed_template')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _require_task_estimates_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'study_task_estimates'"
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "任务估时数据表尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def save_task_estimate(
    state: object,
    candidate: object,
    estimated_minutes: object,
    source: object = "user",
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Validate before writes, and require an explicitly installed estimate table."""
    from study_app.core.active_subjects import require_activity_subject
    from study_app.core.task_estimates import make_task_estimate

    estimate = make_task_estimate(candidate, estimated_minutes, source)
    require_activity_subject(state, candidate.subject_id, "修改任务估时")
    path = require_initialized_database(db_path)
    _require_task_estimates_table(path)
    with connect(path) as connection:
        subject = connection.execute(
            "SELECT id, status FROM subjects WHERE name = ?", (candidate.subject_id,)
        ).fetchone()
        if subject is None:
            raise LookupError(f"未找到学科：{candidate.subject_id}")
        if subject["status"] == "archived":
            raise ValueError(f"{candidate.subject_id} 已封存，不再修改任务估时")
        connection.execute(
            """
            INSERT INTO study_task_estimates
                (task_id, subject_id, source_kind, source_id, estimated_minutes, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                estimated_minutes = excluded.estimated_minutes,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (estimate.task_id, subject["id"], candidate.source_kind,
             candidate.source_id, estimate.estimated_minutes, estimate.source),
        )
    return estimate


def get_task_estimate(
    candidate: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Historical estimates can be read, including after subject archival."""
    from study_app.core.task_estimates import TaskEstimate, validate_candidate_identity

    validate_candidate_identity(candidate)
    _require_task_estimates_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            """
            SELECT e.task_id, e.estimated_minutes, e.source
            FROM study_task_estimates e JOIN subjects s ON s.id = e.subject_id
            WHERE e.task_id = ? AND s.name = ? AND e.source_kind = ? AND e.source_id = ?
            """,
            (candidate.task_id, candidate.subject_id, candidate.source_kind, candidate.source_id),
        ).fetchone()
    if row is None:
        return None
    return TaskEstimate(row["task_id"], row["estimated_minutes"], row["source"])


def _scope_key(subject_scope: str | None) -> str:
    return subject_scope or ""


BUDGET_PLAN_FORMAT_VERSION = "budget-v1"
BUDGET_PLAN_SCOPE = "__budget_day__"
F1_I06_MIGRATION_STATEMENTS = (
    "ALTER TABLE study_plans ADD COLUMN plan_date TEXT",
    "ALTER TABLE study_plans ADD COLUMN budget_minutes INTEGER",
    "ALTER TABLE study_plans ADD COLUMN plan_format_version TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN task_id TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN subject_id TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN estimated_minutes INTEGER",
    "ALTER TABLE study_plan_items ADD COLUMN estimate_source TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN selection_reason_json TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN excluded_reason TEXT",
    "CREATE UNIQUE INDEX uq_budget_plan_task ON study_plan_items(plan_id, task_id) WHERE task_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_active_budget_plan_date ON study_plans(plan_date) "
    "WHERE plan_format_version = 'budget-v1' AND status = 'active'",
)


def _require_budget_plan_schema(db_path: Path | str) -> None:
    required_plan = {"plan_date", "budget_minutes", "plan_format_version"}
    required_item = {
        "task_id", "subject_id", "estimated_minutes", "estimate_source",
        "selection_reason_json", "excluded_reason",
    }
    with connect_readonly(db_path) as connection:
        plan_columns = {row["name"] for row in connection.execute("PRAGMA table_info(study_plans)")}
        item_columns = {row["name"] for row in connection.execute("PRAGMA table_info(study_plan_items)")}
    if not required_plan <= plan_columns or not required_item <= item_columns:
        raise DatabaseNotInitializedError(
            "规范预算计划结构尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def create_budgeted_day_plan(
    plan_date: object,
    available_minutes: object,
    plan: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    """Atomically replace all active plans with one canonical day plan."""
    from study_app.core.day_budget_input import validate_day_budget_input
    from study_app.core.study_plan_items import build_budgeted_plan_items
    from study_app.core.task_estimates import validate_estimate_source, validate_estimated_minutes
    from study_app.data.text_integrity import validate_text_integrity

    budget = validate_day_budget_input(plan_date, available_minutes)
    rows = build_budgeted_plan_items(plan)
    selected_minutes = 0
    completed_minutes = 0
    for row in rows:
        minutes = row["estimated_minutes"]
        if row["section_key"] == "selected" and minutes is None:
            raise ValueError("待执行任务必须有可信预计时间")
        if minutes is not None:
            valid_minutes = validate_estimated_minutes(minutes)
            validate_estimate_source(row["estimate_source"])
            if row["section_key"] == "selected":
                selected_minutes += valid_minutes
            elif row["section_key"] == "completed":
                completed_minutes += valid_minutes
    expected_planned = selected_minutes + completed_minutes
    if plan.planned_minutes != expected_planned:
        raise ValueError("预算计划合计分钟与条目不一致")
    expected_overage = max(0, completed_minutes - budget.available_minutes)
    if plan.over_budget_completed_minutes != expected_overage:
        raise ValueError("已完成超额分钟与条目不一致")
    if selected_minutes and getattr(plan, "completed_occupancy_unknown", False):
        raise ValueError("完成占用未知时不能新增待执行任务")
    if plan.planned_minutes > budget.available_minutes and expected_overage == 0:
        raise ValueError("预算计划总分钟超过当日预算")
    if plan.remaining_minutes != max(0, budget.available_minutes - plan.planned_minutes):
        raise ValueError("预算计划剩余分钟与输入不一致")
    if len({row["task_id"] for row in rows if row["task_id"] is not None}) != sum(
        row["task_id"] is not None for row in rows
    ):
        raise ValueError("规范计划中存在重复任务 ID")
    validate_text_integrity(rows, context="规范预算计划条目")
    path = require_initialized_database(db_path)
    _require_budget_plan_schema(path)
    with connect(path) as connection:
        old_states = {
            row["task_id"]: (row["checked"], row["result"])
            for row in connection.execute(
                """
                SELECT items.task_id,
                       CASE WHEN COALESCE(states.checked, 0) = 1 OR states.result IS NOT NULL
                            THEN 1 ELSE 0 END AS checked,
                       states.result
                FROM study_plan_items items
                JOIN study_plans plans ON plans.id = items.plan_id
                LEFT JOIN study_plan_item_states states ON states.item_id = items.id
                WHERE items.task_id IS NOT NULL AND plans.status = 'active'
                """
            )
        }
        legacy_completed = connection.execute(
            """
            SELECT COUNT(*)
            FROM study_plan_items items
            JOIN study_plans plans ON plans.id = items.plan_id
            JOIN study_plan_item_states states ON states.item_id = items.id
            WHERE plans.status = 'active' AND items.task_id IS NULL
              AND (states.checked = 1 OR states.result IS NOT NULL)
            """
        ).fetchone()[0]
        if legacy_completed and any(row["section_key"] == "selected" for row in rows):
            raise ValueError("旧计划存在完成占用未知项；补估时前不能新增待执行任务")
        connection.execute(
            "UPDATE study_plans SET status = 'archived', archived_at = CURRENT_TIMESTAMP "
            "WHERE status = 'active'"
        )
        summary = {
            "format": BUDGET_PLAN_FORMAT_VERSION,
            "planned_minutes": plan.planned_minutes,
            "remaining_minutes": plan.remaining_minutes,
            "over_budget_completed_minutes": plan.over_budget_completed_minutes,
            "completed_occupancy_unknown": bool(plan.completed_occupancy_unknown or legacy_completed),
            "legacy_completed_unknown_count": legacy_completed,
        }
        cursor = connection.execute(
            """
            INSERT INTO study_plans(
                subject_scope, start_date, end_date, input_signature, status, plan_json,
                plan_date, budget_minutes, plan_format_version
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (BUDGET_PLAN_SCOPE, budget.plan_date, budget.plan_date, plan.input_signature,
             dumps(summary), budget.plan_date, budget.available_minutes, BUDGET_PLAN_FORMAT_VERSION),
        )
        plan_id = int(cursor.lastrowid)
        for order, row in enumerate(rows):
            item_cursor = connection.execute(
                """
                INSERT INTO study_plan_items(
                    plan_id, section_key, section_title, day_index, item_type, item_text,
                    item_order, item_hash, task_id, subject_id, estimated_minutes,
                    estimate_source, selection_reason_json, excluded_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (plan_id, row["section_key"], row["section_title"], row["day_index"],
                 row["item_type"], row["item_text"], order, row["item_hash"], row["task_id"],
                 row["subject_id"], row["estimated_minutes"], row["estimate_source"],
                 dumps(row["selection_reason"]), row["excluded_reason"]),
            )
            prior = old_states.get(row["task_id"])
            if prior is not None or row["initial_checked"]:
                checked, result = prior if prior is not None else (1, None)
                connection.execute(
                    "INSERT INTO study_plan_item_states(item_id, checked, result) VALUES (?, ?, ?)",
                    (int(item_cursor.lastrowid), checked, result),
                )
    return plan_id


def get_budgeted_day_plan(
    plan_date: object,
    subject_scope: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    from study_app.core.day_budget_input import validate_day_budget_date

    valid_date = validate_day_budget_date(plan_date)
    _require_budget_plan_schema(db_path)
    with connect_readonly(db_path) as connection:
        plan_row = connection.execute(
            """
            SELECT id, plan_date, budget_minutes, input_signature, plan_json, created_at
            FROM study_plans
            WHERE plan_date = ? AND plan_format_version = ? AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """,
            (valid_date, BUDGET_PLAN_FORMAT_VERSION),
        ).fetchone()
        if plan_row is None:
            return None
        item_rows = connection.execute(
            """
            SELECT items.id, items.section_key, items.item_text, items.item_order,
                   items.task_id, items.subject_id, items.estimated_minutes,
                   items.estimate_source, items.selection_reason_json, items.excluded_reason,
                   CASE WHEN COALESCE(states.checked, 0) = 1 OR states.result IS NOT NULL
                        THEN 1 ELSE 0 END AS checked,
                   states.result
            FROM study_plan_items items
            LEFT JOIN study_plan_item_states states ON states.item_id = items.id
            WHERE items.plan_id = ? AND (? IS NULL OR items.subject_id = ?)
            ORDER BY items.item_order
            """,
            (plan_row["id"], subject_scope, subject_scope),
        ).fetchall()
    result = dict(plan_row)
    result["summary"] = json.loads(result.pop("plan_json"))
    result["items"] = []
    for row in item_rows:
        item = dict(row)
        item["selection_reason"] = json.loads(item.pop("selection_reason_json"))
        result["items"].append(item)
    return result


def create_study_plan(
    subject_scope: str | None,
    start_date: str,
    end_date: str,
    input_signature: str,
    plan: dict[str, Any],
    items: list[dict[str, Any]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    from study_app.data.text_integrity import validate_text_integrity

    validate_text_integrity(plan, context="学习计划")
    validate_text_integrity(items, context="学习计划条目")
    path = require_initialized_database(db_path)
    scope = _scope_key(subject_scope)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        gated_subjects = {
            str(value).strip()
            for value in (
                subject_scope,
                *(item.get("subject_id") for item in items),
            )
            if value is not None and str(value).strip()
        }
        for gated_subject in sorted(gated_subjects):
            _require_f5_subject_write_allowed(connection, gated_subject)
        connection.execute(
            """
            UPDATE study_plans
            SET status = 'archived', archived_at = CURRENT_TIMESTAMP
            WHERE COALESCE(subject_scope, '') = ? AND status = 'active'
            """,
            (scope,),
        )
        cursor = connection.execute(
            """
            INSERT INTO study_plans(subject_scope, start_date, end_date, input_signature, status, plan_json)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (scope, start_date, end_date, input_signature, dumps(plan)),
        )
        plan_id = int(cursor.lastrowid)
        for order, item in enumerate(items):
            connection.execute(
                """
                INSERT INTO study_plan_items(
                    plan_id, section_key, section_title, day_index, item_type,
                    item_text, item_order, item_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    item["section_key"],
                    item["section_title"],
                    item.get("day_index"),
                    item["item_type"],
                    item["item_text"],
                    order,
                    item["item_hash"],
                ),
            )
    return plan_id


def get_active_study_plan(subject_scope: str | None, db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    scope = _scope_key(subject_scope)
    with connect_readonly(db_path) as connection:
        plan_row = connection.execute(
            """
            SELECT id, subject_scope, start_date, end_date, input_signature, plan_json, created_at
            FROM study_plans
            WHERE COALESCE(subject_scope, '') = ? AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (scope,),
        ).fetchone()
        if not plan_row:
            return None
        item_rows = connection.execute(
            """
            SELECT
                study_plan_items.id, section_key, section_title, day_index, item_type,
                item_text, item_order, item_hash,
                CASE
                    WHEN COALESCE(study_plan_item_states.checked, 0) = 1
                         OR study_plan_item_states.result IS NOT NULL
                    THEN 1 ELSE 0
                END AS checked,
                study_plan_item_states.result AS result
            FROM study_plan_items
            LEFT JOIN study_plan_item_states
                ON study_plan_item_states.item_id = study_plan_items.id
            WHERE plan_id = ?
            ORDER BY item_order
            """,
            (plan_row["id"],),
        ).fetchall()
    plan = dict(plan_row)
    plan["plan"] = json.loads(plan.pop("plan_json"))
    plan["items"] = [dict(row) for row in item_rows]
    return plan


def recent_study_plan_texts(
    subject_scope: str | None,
    limit: int = 5,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[str]:
    scope = _scope_key(subject_scope)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT plan_json
            FROM study_plans
            WHERE COALESCE(subject_scope, '') = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (scope, max(1, int(limit))),
        ).fetchall()
    texts = []
    for row in rows:
        plan = json.loads(row["plan_json"])
        text_parts: list[str] = []

        def collect_text(value: object) -> None:
            if isinstance(value, dict):
                for nested in value.values():
                    collect_text(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_text(nested)
            elif value is not None:
                text_parts.append(str(value))

        collect_text(plan)
        texts.append("\n".join(text_parts))
    return texts


def archive_active_study_plan(subject_scope: str | None, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    path = require_initialized_database(db_path)
    scope = _scope_key(subject_scope)
    with connect(path) as connection:
        connection.execute(
            """
            UPDATE study_plans
            SET status = 'archived', archived_at = CURRENT_TIMESTAMP
            WHERE COALESCE(subject_scope, '') = ? AND status = 'active'
            """,
            (scope,),
        )


def update_study_plan_item_state(
    item_id: int,
    checked: bool | None = None,
    result: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        row = connection.execute(
            "SELECT item_id FROM study_plan_item_states WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row:
            assignments = []
            values: list[Any] = []
            if checked is not None:
                assignments.append("checked = ?")
                values.append(1 if checked else 0)
            if result is not None:
                assignments.append("result = ?")
                values.append(result)
            if not assignments:
                return
            values.append(item_id)
            connection.execute(
                f"""
                UPDATE study_plan_item_states
                SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ?
                """,
                values,
            )
        else:
            connection.execute(
                """
                INSERT INTO study_plan_item_states(item_id, checked, result)
                VALUES (?, ?, ?)
                """,
                (item_id, 1 if checked else 0, result),
            )


def _weighted_mastery(subject: dict[str, Any]) -> float:
    modules = subject.get("modules", [])
    total_weight = sum(module.get("weight", 0) for module in modules)
    if not total_weight:
        return 0.0
    return sum(module.get("mastery", 0) * module.get("weight", 0) for module in modules) / total_weight


def _problem_correctness(problem: dict[str, Any]) -> float | None:
    if isinstance(problem.get("correctness"), (int, float)):
        value = float(problem["correctness"])
        if value > 1:
            value = value / 100
        return max(0.0, min(1.0, value))
    if isinstance(problem.get("partial_credit"), (int, float)):
        return max(0.0, min(1.0, float(problem["partial_credit"])))
    status = problem.get("status")
    if status in {"completed", "solved", "correct", "accepted", "AC", "all_correct"}:
        return 1.0
    if status in {"not_solved", "wrong", "incorrect", "failed", "WA"}:
        return 0.0
    if status in {"partial", "partial_wrong", "partially_correct"}:
        return 0.5
    return None


def _with_inferred_problem_data(record: dict[str, Any], problem: dict[str, Any]) -> dict[str, Any]:
    item = dict(problem)
    if not item.get("difficulty") or item.get("difficulty_score") is None:
        try:
            from learning_difficulty import infer_problem_difficulty

            inferred = infer_problem_difficulty(record, item)
        except Exception:
            inferred = {}
        if inferred:
            item.setdefault("difficulty", inferred.get("difficulty"))
            item.setdefault("difficulty_score", inferred.get("difficulty_score"))
            item.setdefault("difficulty_source", inferred.get("source"))
            item.setdefault("difficulty_confidence", inferred.get("confidence"))
            if inferred.get("matched_keywords"):
                item.setdefault("difficulty_matched_keywords", inferred["matched_keywords"])
            if inferred.get("reasons"):
                item.setdefault("difficulty_reasons", inferred["reasons"])
    if item.get("correctness") is None and item.get("partial_credit") is None:
        inferred_correctness = _infer_correctness_from_text(item)
        if inferred_correctness is not None:
            item["correctness"] = inferred_correctness * 100
            item["partial_credit"] = inferred_correctness
            if not item.get("status"):
                if inferred_correctness >= 0.995:
                    item["status"] = "correct"
                elif inferred_correctness <= 0.005:
                    item["status"] = "wrong"
                else:
                    item["status"] = "partial"
    return item


def _infer_correctness_from_text(problem: dict[str, Any]) -> float | None:
    import re

    explicit_result = " ".join(
        str(value)
        for value in [problem.get("answer_result"), problem.get("result"), problem.get("status")]
        if value
    )
    text = " ".join(
        str(value)
        for value in [
            problem.get("answer_result"),
            problem.get("result"),
            problem.get("note"),
            problem.get("error_cause"),
            problem.get("status"),
        ]
        if value
    )
    if not text:
        return None
    if any(keyword in text for keyword in ["全对", "做对", "正确", "AC", "accepted"]):
        return 1.0
    if explicit_result and any(keyword in explicit_result for keyword in ["错误", "错题", "错了", "wrong", "WA"]):
        if not any(keyword in explicit_result for keyword in ["部分", "一部分", "小错"]):
            return 0.0
    match = re.search(r"(\d+)\s*题?\s*错\s*(\d+)\s*题?", text)
    if match:
        total = int(match.group(1))
        wrong = int(match.group(2))
        if total > 0:
            return max(0.0, min(1.0, (total - wrong) / total))
    match = re.search(r"错\s*(\d+)\s*题?.*?共?\s*(\d+)\s*题?", text)
    if match:
        wrong = int(match.group(1))
        total = int(match.group(2))
        if total > 0:
            return max(0.0, min(1.0, (total - wrong) / total))
    if any(keyword in text for keyword in ["部分", "一部分", "小错", "算错", "漏", "不完整"]):
        return 0.5
    if any(keyword in text for keyword in ["错", "没做出来", "不会", "未掌握", "错误", "WA"]):
        return 0.0
    return None


def _problem_title(problem: dict[str, Any], fallback: str) -> str:
    title = problem.get("title") or problem.get("name") or fallback
    return str(title or "未命名题目")


def _problem_statement(problem: dict[str, Any]) -> str | None:
    value = problem.get("statement") or problem.get("problem_statement") or problem.get("prompt")
    return str(value).strip() if value else None


def _auto_import_practice_problems(
    record: dict[str, Any],
    problems: list[dict[str, Any]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    if not problems:
        return
    try:
        from study_app.core.practice_bank import template_for_topic
        from study_app.data.practice_repository import import_practice_problem
    except Exception:
        return
    for problem_index, problem in enumerate(problems, start=1):
        difficulty_source = str(problem.get("difficulty_source") or "").strip().lower()
        if (
            difficulty_source in {"ai_generated", "llm_generated", "planned_reference", "planned_homework"}
            or difficulty_source.startswith("ai_generated")
            or difficulty_source.startswith("llm_generated")
        ):
            continue
        title = _problem_title(problem, record.get("topic", "未命名题目"))
        statement = _problem_statement(problem)
        if not _should_import_to_practice_bank(title, statement):
            continue
        related_topics = problem.get("related_topics") or []
        if isinstance(related_topics, str):
            related_topics = [related_topics]
        classify_text = " ".join(
            [
                str(record.get("subject") or ""),
                str(record.get("module") or ""),
                str(record.get("topic") or ""),
                title,
                statement or "",
                " ".join(str(item) for item in related_topics),
                str(_problem_error_cause(problem) or ""),
            ]
        )
        template_id, _brief = template_for_topic(classify_text)
        difficulty_score = problem.get("difficulty_score")
        try:
            difficulty_score = float(difficulty_score) if difficulty_score is not None else 60.0
        except (TypeError, ValueError):
            difficulty_score = 60.0
        error_cause = _problem_error_cause(problem)
        payload = {
            "template_id": template_id,
            "title": title,
            "statement": statement,
            "answer_outline": str(problem.get("answer_outline") or problem.get("solution") or ""),
            "common_errors": [error_cause] if error_cause else [],
            "difficulty_score": difficulty_score,
            "difficulty_source": problem.get("difficulty_source") or "auto_from_problem_attempt",
            "subject": record.get("subject"),
            "topic": record.get("topic") or "、".join(str(item) for item in related_topics[:3]),
            "tags": related_topics,
            "source": {
                "type": "learning_record",
                "title": f"{record.get('date', '')} {record.get('subject', '')} 学习记录",
                "note": "由新增学习记录中的题面自动归类导入。",
            },
            "source_note": f"自动导入自学习记录：{record.get('date', '')} {str(record.get('note') or '')[:80]}",
        }
        try:
            import_practice_problem(payload, db_path=db_path)
        except Exception as error:
            LOGGER.exception(
                "Practice auto-import failed for problem index %s",
                problem_index,
            )
            continue


def _should_import_to_practice_bank(title: str, statement: str | None) -> bool:
    if not statement or len(statement.strip()) < 8:
        return False
    combined = f"{title} {statement}"
    blocked_markers = [
        "学习计划作业",
        "当天作业",
        "生成/选做",
        "题库模板",
        "完成标准",
        "复盘记录",
    ]
    return not any(marker in combined for marker in blocked_markers)


def _problem_error_cause(problem: dict[str, Any]) -> str | None:
    value = (
        problem.get("error_cause")
        or problem.get("error_reason")
        or problem.get("wrong_reason")
        or problem.get("mistake_reason")
    )
    if isinstance(value, list):
        value = "；".join(str(item) for item in value if item)
    return str(value).strip() if value else None


def _optional_float(value: Any, *, label: str = "record 数值字段") -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数值（字符串数值与布尔值不接受）：{value!r}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} 必须是非负有限数值：{value!r}")
    return number


KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_topic_registry (
    topic_key TEXT PRIMARY KEY,
    topic_id INTEGER NOT NULL UNIQUE REFERENCES topics(id) ON DELETE RESTRICT,
    identity_version TEXT NOT NULL DEFAULT 'topic-identity-v1'
        CHECK(identity_version = 'topic-identity-v1'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(
        length(topic_key) = 73
        AND substr(topic_key, 1, 9) = 'topic:v1:'
        AND substr(topic_key, 10) NOT GLOB '*[^0-9a-f]*'
    )
)
"""


_TOPIC_IDENTITY_SELECT = """
SELECT
    registry.topic_key,
    registry.topic_id,
    registry.identity_version,
    subjects.name AS subject_name,
    modules.name AS module_name,
    topics.name AS topic_name
FROM knowledge_topic_registry AS registry
LEFT JOIN topics ON topics.id = registry.topic_id
LEFT JOIN modules ON modules.id = topics.module_id
LEFT JOIN subjects ON subjects.id = modules.subject_id
"""


def _require_knowledge_topic_registry_table(db_path: Path | str) -> None:
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


def _topic_identity_from_row(row: sqlite3.Row):
    from study_app.core.topic_identity import make_topic_identity

    return make_topic_identity(
        topic_key=row["topic_key"],
        topic_id=row["topic_id"],
        subject_name=row["subject_name"],
        module_name=row["module_name"],
        topic_name=row["topic_name"],
        identity_version=row["identity_version"],
    )


def list_topic_identities(
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Read registered topic identities without installing schema or seeding rows."""
    _require_knowledge_topic_registry_table(db_path)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            _TOPIC_IDENTITY_SELECT + " ORDER BY registry.topic_key"
        ).fetchall()
    return tuple(_topic_identity_from_row(row) for row in rows)


def get_topic_identity(
    topic_key: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    from study_app.core.topic_identity import validate_topic_key

    valid_key = validate_topic_key(topic_key)
    _require_knowledge_topic_registry_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            _TOPIC_IDENTITY_SELECT + " WHERE registry.topic_key = ?",
            (valid_key,),
        ).fetchone()
    return None if row is None else _topic_identity_from_row(row)


def resolve_topic_identity(
    subject_name: object,
    module_name: object,
    topic_name: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    names = (subject_name, module_name, topic_name)
    if any(not isinstance(item, str) or not item.strip() for item in names):
        raise ValueError("解析知识点身份需要非空的学科、模块和知识点名称")
    _require_knowledge_topic_registry_table(db_path)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            _TOPIC_IDENTITY_SELECT
            + """
              WHERE subjects.name = ? AND modules.name = ? AND topics.name = ?
              ORDER BY registry.topic_key
              """,
            tuple(item.strip() for item in names),
        ).fetchall()
    if len(rows) > 1:
        raise ValueError("知识点路径映射到多个稳定身份")
    return None if not rows else _topic_identity_from_row(rows[0])


def register_topic_identities(
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Explicitly seed missing registry rows; never called from a read path."""
    from study_app.core.topic_identity import (
        TOPIC_IDENTITY_VERSION,
        topic_key_for_id,
    )

    path = require_initialized_database(db_path)
    _require_knowledge_topic_registry_table(path)
    with connect(path) as connection:
        topic_rows = connection.execute(
            """
            SELECT topics.id AS topic_id
            FROM topics
            JOIN modules ON modules.id = topics.module_id
            JOIN subjects ON subjects.id = modules.subject_id
            ORDER BY topics.id
            """
        ).fetchall()
        registry_rows = connection.execute(
            _TOPIC_IDENTITY_SELECT + " ORDER BY registry.topic_key"
        ).fetchall()
        identities = tuple(_topic_identity_from_row(row) for row in registry_rows)
        existing_by_topic = {item.topic_id: item.topic_key for item in identities}
        existing_by_key = {item.topic_key: item.topic_id for item in identities}

        pending: list[tuple[str, int, str]] = []
        for row in topic_rows:
            topic_id = int(row["topic_id"])
            if topic_id in existing_by_topic:
                continue
            generation = 0
            topic_key = topic_key_for_id(topic_id, generation)
            while topic_key in existing_by_key:
                generation += 1
                topic_key = topic_key_for_id(topic_id, generation)
            pending.append((topic_key, topic_id, TOPIC_IDENTITY_VERSION))
            existing_by_key[topic_key] = topic_id

        connection.executemany(
            """
            INSERT INTO knowledge_topic_registry(topic_key, topic_id, identity_version)
            VALUES (?, ?, ?)
            """,
            pending,
        )
        rows = connection.execute(
            _TOPIC_IDENTITY_SELECT + " ORDER BY registry.topic_key"
        ).fetchall()
        return tuple(_topic_identity_from_row(row) for row in rows)


def rebind_topic_identity(
    topic_key: object,
    new_topic_id: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Explicitly retain a stable key when a controlled rename creates a new row."""
    from study_app.core.topic_identity import validate_topic_key, validate_topic_row_id

    valid_key = validate_topic_key(topic_key)
    valid_topic_id = validate_topic_row_id(new_topic_id)
    path = require_initialized_database(db_path)
    _require_knowledge_topic_registry_table(path)
    with connect(path) as connection:
        current = connection.execute(
            "SELECT topic_id FROM knowledge_topic_registry WHERE topic_key = ?",
            (valid_key,),
        ).fetchone()
        if current is None:
            raise LookupError(f"未找到知识点身份：{valid_key}")
        target = connection.execute(
            "SELECT id FROM topics WHERE id = ?", (valid_topic_id,)
        ).fetchone()
        if target is None:
            raise LookupError(f"未找到目标 topic_id：{valid_topic_id}")
        occupied = connection.execute(
            "SELECT topic_key FROM knowledge_topic_registry WHERE topic_id = ?",
            (valid_topic_id,),
        ).fetchone()
        if occupied is not None and occupied["topic_key"] != valid_key:
            raise ValueError(
                f"目标 topic_id={valid_topic_id} 已绑定其它知识点身份"
            )
        if int(current["topic_id"]) != valid_topic_id:
            connection.execute(
                """
                UPDATE knowledge_topic_registry
                SET topic_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE topic_key = ?
                """,
                (valid_topic_id, valid_key),
            )
        row = connection.execute(
            _TOPIC_IDENTITY_SELECT + " WHERE registry.topic_key = ?",
            (valid_key,),
        ).fetchone()
        return _topic_identity_from_row(row)


KNOWLEDGE_PREREQUISITES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_prerequisites (
    topic_key TEXT NOT NULL
        REFERENCES knowledge_topic_registry(topic_key) ON DELETE RESTRICT,
    prerequisite_topic_key TEXT NOT NULL
        REFERENCES knowledge_topic_registry(topic_key) ON DELETE RESTRICT,
    source TEXT NOT NULL
        CHECK(source IN ('user_confirmed', 'controlled_local_config')),
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(topic_key, prerequisite_topic_key),
    CHECK(topic_key <> prerequisite_topic_key)
)
"""


KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_prerequisites_reverse
ON knowledge_prerequisites(prerequisite_topic_key, topic_key)
"""


def _require_knowledge_prerequisites_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'knowledge_prerequisites'
            """
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "知识点先修关系表尚未安装；真实数据库需先完成独立 CR-F2-01 结构变更"
        )


def _prerequisite_edge_from_row(row: sqlite3.Row):
    from study_app.core.prerequisite_graph import make_prerequisite_edge

    try:
        source_data = json.loads(row["source_json"] or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("先修关系来源元数据损坏") from error
    return make_prerequisite_edge(
        row["topic_key"],
        row["prerequisite_topic_key"],
        row["source"],
        source_data,
    )


def _list_prerequisite_edges_from_connection(
    connection: sqlite3.Connection,
):
    rows = connection.execute(
        """
        SELECT topic_key, prerequisite_topic_key, source, source_json
        FROM knowledge_prerequisites
        ORDER BY topic_key, prerequisite_topic_key
        """
    ).fetchall()
    return tuple(_prerequisite_edge_from_row(row) for row in rows)


def list_prerequisite_edges(
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Return every explicit edge, including archived-subject history."""
    _require_knowledge_topic_registry_table(db_path)
    _require_knowledge_prerequisites_table(db_path)
    with connect_readonly(db_path) as connection:
        return _list_prerequisite_edges_from_connection(connection)


def get_direct_prerequisites(
    topic_key: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    from study_app.core.topic_identity import validate_topic_key

    valid_key = validate_topic_key(topic_key)
    return tuple(
        edge
        for edge in list_prerequisite_edges(db_path)
        if edge.topic_key == valid_key
    )


def get_direct_dependents(
    prerequisite_topic_key: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    from study_app.core.topic_identity import validate_topic_key

    valid_key = validate_topic_key(prerequisite_topic_key)
    return tuple(
        edge
        for edge in list_prerequisite_edges(db_path)
        if edge.prerequisite_topic_key == valid_key
    )


def get_upstream_prerequisite_paths(
    topic_key: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple[tuple[str, ...], ...]:
    from study_app.core.prerequisite_graph import upstream_prerequisite_paths

    return upstream_prerequisite_paths(
        topic_key,
        list_prerequisite_edges(db_path),
    )


def replace_prerequisite_edges(
    topic_key: object,
    prerequisite_keys: object,
    source: object,
    source_data: object = None,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Atomically replace one topic's direct prerequisites after full-graph checks."""
    from study_app.core.prerequisite_graph import (
        validate_acyclic_prerequisite_graph,
        validate_prerequisite_replacement,
    )

    topic, replacements = validate_prerequisite_replacement(
        topic_key,
        prerequisite_keys,
        source,
        source_data,
    )
    path = require_initialized_database(db_path)
    _require_knowledge_topic_registry_table(path)
    _require_knowledge_prerequisites_table(path)
    with connect(path) as connection:
        registry_keys = {
            row["topic_key"]
            for row in connection.execute(
                "SELECT topic_key FROM knowledge_topic_registry"
            ).fetchall()
        }
        required_keys = {topic} | {
            edge.prerequisite_topic_key for edge in replacements
        }
        missing = sorted(required_keys - registry_keys)
        if missing:
            raise LookupError("未找到知识点身份：" + "、".join(missing))

        existing = _list_prerequisite_edges_from_connection(connection)
        proposed = tuple(edge for edge in existing if edge.topic_key != topic) + replacements
        validate_acyclic_prerequisite_graph(proposed)

        current = tuple(edge for edge in existing if edge.topic_key == topic)
        if current == replacements:
            return current

        connection.execute(
            "DELETE FROM knowledge_prerequisites WHERE topic_key = ?",
            (topic,),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_prerequisites(
                topic_key, prerequisite_topic_key, source, source_json
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    edge.topic_key,
                    edge.prerequisite_topic_key,
                    edge.source,
                    json.dumps(
                        edge.source_data,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                for edge in replacements
            ],
        )
        return replacements


KNOWLEDGE_ALERTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(fingerprint) = 64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
    topic_key TEXT NOT NULL
        REFERENCES knowledge_topic_registry(topic_key) ON DELETE RESTRICT,
    alert_type TEXT NOT NULL
        CHECK(alert_type IN ('recent_failure', 'insufficient_evidence', 'overdue_review')),
    status TEXT NOT NULL
        CHECK(status IN ('active', 'snoozed', 'handled', 'resolved')),
    rule_version TEXT NOT NULL,
    evidence_version TEXT NOT NULL,
    condition_cycle TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    recommended_date TEXT,
    priority REAL NOT NULL CHECK(priority >= 0),
    snapshot_json TEXT NOT NULL,
    snoozed_until TEXT,
    handled_at TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


KNOWLEDGE_ALERT_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL
        REFERENCES knowledge_alerts(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL
        CHECK(to_status IN ('active', 'snoozed', 'handled', 'resolved')),
    effective_date TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


KNOWLEDGE_ALERT_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_alerts_status_due
ON knowledge_alerts(status, snoozed_until, recommended_date);
CREATE INDEX IF NOT EXISTS idx_knowledge_alert_events_alert
ON knowledge_alert_events(alert_id, id);
"""


def _require_knowledge_alert_tables_from_connection(
    connection: sqlite3.Connection,
) -> None:
    names = {
        row[0]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name IN (
                'knowledge_topic_registry',
                'knowledge_alerts',
                'knowledge_alert_events'
            )
            """
        ).fetchall()
    }
    missing = {
        "knowledge_topic_registry",
        "knowledge_alerts",
        "knowledge_alert_events",
    } - names
    if missing:
        raise DatabaseNotInitializedError(
            "知识预警表尚未安装；真实数据库需先完成独立 CR-F2-01 结构变更："
            + "、".join(sorted(missing))
        )


def _require_knowledge_alert_tables(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        _require_knowledge_alert_tables_from_connection(connection)


def _parse_iso_date(value: object, *, label: str, optional: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} 不是有效日期")
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} 不是有效日期：{value!r}") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{label} 必须使用 YYYY-MM-DD：{value!r}")
    return parsed


def _parse_json_object(value: object, *, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} JSON 损坏") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return parsed


def _knowledge_alert_from_row(row):
    from study_app.core.knowledge_alerts import KnowledgeAlert

    return KnowledgeAlert(
        id=int(row["id"]),
        fingerprint=row["fingerprint"],
        topic_key=row["topic_key"],
        alert_type=row["alert_type"],
        status=row["status"],
        rule_version=row["rule_version"],
        evidence_version=row["evidence_version"],
        condition_cycle=row["condition_cycle"],
        as_of_date=_parse_iso_date(row["as_of_date"], label="as_of_date"),
        recommended_date=_parse_iso_date(
            row["recommended_date"], label="recommended_date", optional=True
        ),
        priority=float(row["priority"]),
        snapshot=_parse_json_object(row["snapshot_json"], label="snapshot_json"),
        snoozed_until=_parse_iso_date(
            row["snoozed_until"], label="snoozed_until", optional=True
        ),
        handled_at=row["handled_at"],
        resolved_at=row["resolved_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _knowledge_alert_event_from_row(row):
    from study_app.core.knowledge_alerts import KnowledgeAlertEvent

    return KnowledgeAlertEvent(
        id=int(row["id"]),
        alert_id=int(row["alert_id"]),
        event_type=row["event_type"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        effective_date=_parse_iso_date(
            row["effective_date"], label="effective_date"
        ),
        actor=row["actor"],
        detail=_parse_json_object(row["detail_json"], label="detail_json"),
        created_at=row["created_at"],
    )


def list_knowledge_alerts(
    status: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Read F2 alert instances without installing schema or changing state."""
    allowed = {"active", "snoozed", "handled", "resolved"}
    if status is not None and status not in allowed:
        raise ValueError(f"未知预警状态：{status!r}")
    _require_knowledge_alert_tables(db_path)
    query = "SELECT * FROM knowledge_alerts"
    parameters: tuple[object, ...] = ()
    if status is not None:
        query += " WHERE status = ?"
        parameters = (status,)
    query += " ORDER BY id"
    with connect_readonly(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return tuple(_knowledge_alert_from_row(row) for row in rows)


def list_knowledge_alert_events(
    alert_id: int | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Read immutable lifecycle events without creating missing tables."""
    if alert_id is not None and (
        isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0
    ):
        raise ValueError("alert_id 必须是正整数")
    _require_knowledge_alert_tables(db_path)
    query = "SELECT * FROM knowledge_alert_events"
    parameters: tuple[object, ...] = ()
    if alert_id is not None:
        query += " WHERE alert_id = ?"
        parameters = (alert_id,)
    query += " ORDER BY id"
    with connect_readonly(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return tuple(_knowledge_alert_event_from_row(row) for row in rows)


def _validate_alert_snapshot(snapshot: object, as_of_date: datetime.date) -> None:
    from study_app.core.knowledge_alerts import ALERT_TYPES, KnowledgeAlertSnapshot
    from study_app.core.topic_identity import validate_topic_key

    if not isinstance(snapshot, KnowledgeAlertSnapshot):
        raise ValueError("snapshots 只能包含 KnowledgeAlertSnapshot")
    validate_topic_key(snapshot.topic_key)
    if snapshot.alert_type not in ALERT_TYPES:
        raise ValueError(f"未知预警类型：{snapshot.alert_type!r}")
    if (
        len(snapshot.fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in snapshot.fingerprint)
    ):
        raise ValueError("预警 fingerprint 必须是 64 位小写十六进制")
    for label, value in (
        ("rule_version", snapshot.rule_version),
        ("evidence_version", snapshot.evidence_version),
        ("condition_cycle", snapshot.condition_cycle),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} 必须是非空字符串")
    if snapshot.as_of_date != as_of_date:
        raise ValueError("预警快照日期与协调日期不一致")
    if snapshot.recommended_date is not None and (
        not isinstance(snapshot.recommended_date, datetime.date)
        or isinstance(snapshot.recommended_date, datetime.datetime)
    ):
        raise ValueError("recommended_date 必须是 date 或 None")
    if (
        isinstance(snapshot.priority, bool)
        or not isinstance(snapshot.priority, (int, float))
        or not math.isfinite(float(snapshot.priority))
        or snapshot.priority < 0
    ):
        raise ValueError("priority 必须是非负有限数值")
    if not isinstance(snapshot.snapshot, dict):
        raise ValueError("snapshot 必须是结构化对象")
    identity = {
        "topic_key": snapshot.topic_key,
        "alert_type": snapshot.alert_type,
        "rule_version": snapshot.rule_version,
        "evidence_version": snapshot.evidence_version,
        "condition_cycle": snapshot.condition_cycle,
    }
    expected_fingerprint = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if snapshot.fingerprint != expected_fingerprint:
        raise ValueError("预警 fingerprint 与身份字段不一致")
    try:
        json.dumps(
            snapshot.snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("snapshot 必须是可序列化且不含非有限数值的 JSON 对象") from error


def _insert_alert_event(
    connection: sqlite3.Connection,
    *,
    alert_id: int,
    event_type: str,
    from_status: str | None,
    to_status: str,
    effective_date: datetime.date,
    detail: dict[str, object],
    actor: str = "system",
) -> None:
    connection.execute(
        """
        INSERT INTO knowledge_alert_events(
            alert_id, event_type, from_status, to_status,
            effective_date, actor, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            event_type,
            from_status,
            to_status,
            effective_date.isoformat(),
            actor,
            json.dumps(
                detail,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )


def reconcile_knowledge_alert_snapshots(
    snapshots: object,
    as_of_date: datetime.date,
    connection: sqlite3.Connection,
):
    """Atomically reconcile computed candidates against dedicated F2 alert tables."""
    from study_app.core.knowledge_alerts import AlertReconcileResult

    if not isinstance(as_of_date, datetime.date) or isinstance(
        as_of_date, datetime.datetime
    ):
        raise ValueError("as_of_date 必须是 date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    values = tuple(snapshots)
    for snapshot in values:
        _validate_alert_snapshot(snapshot, as_of_date)
    fingerprints = [snapshot.fingerprint for snapshot in values]
    slots = [(snapshot.topic_key, snapshot.alert_type) for snapshot in values]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("预警快照包含重复 fingerprint")
    if len(set(slots)) != len(slots):
        raise ValueError("同一知识点与预警类型只能有一个当前快照")

    _require_knowledge_alert_tables_from_connection(connection)
    registry_keys = {
        row[0]
        for row in connection.execute(
            "SELECT topic_key FROM knowledge_topic_registry"
        ).fetchall()
    }
    missing = sorted({snapshot.topic_key for snapshot in values} - registry_keys)
    if missing:
        raise LookupError("未找到知识点身份：" + "、".join(missing))

    cursor = connection.execute("SELECT * FROM knowledge_alerts ORDER BY id")
    columns = tuple(description[0] for description in cursor.description)
    existing = tuple(dict(zip(columns, row)) for row in cursor.fetchall())
    allowed_statuses = {"active", "snoozed", "handled", "resolved"}
    for row in existing:
        if row["status"] not in allowed_statuses:
            raise ValueError(f"知识预警状态损坏：{row['status']!r}")
        if row["status"] == "snoozed":
            _parse_iso_date(row["snoozed_until"], label="snoozed_until")

    current_by_slot = {
        (snapshot.topic_key, snapshot.alert_type): snapshot for snapshot in values
    }
    existing_by_fingerprint = {row["fingerprint"]: row for row in existing}
    resolve_plan: list[tuple[dict[str, object], str]] = []
    reactivate_plan: list[dict[str, object]] = []
    unchanged_ids: set[int] = set()

    for row in existing:
        row_id = int(row["id"])
        if row["status"] not in {"active", "snoozed"}:
            continue
        current = current_by_slot.get((row["topic_key"], row["alert_type"]))
        if current is None:
            resolve_plan.append((row, "condition_cleared"))
        elif current.fingerprint != row["fingerprint"]:
            resolve_plan.append((row, "condition_superseded"))
        elif row["status"] == "snoozed":
            snoozed_until = _parse_iso_date(
                row["snoozed_until"], label="snoozed_until"
            )
            if snoozed_until <= as_of_date:
                reactivate_plan.append(row)
            else:
                unchanged_ids.add(row_id)
        else:
            unchanged_ids.add(row_id)

    create_plan = []
    for snapshot in values:
        row = existing_by_fingerprint.get(snapshot.fingerprint)
        if row is None:
            create_plan.append(snapshot)
        elif int(row["id"]) not in {
            int(item[0]["id"]) for item in resolve_plan
        } and int(row["id"]) not in {int(item["id"]) for item in reactivate_plan}:
            unchanged_ids.add(int(row["id"]))

    created_ids: list[int] = []
    resolved_ids: list[int] = []
    reactivated_ids: list[int] = []
    connection.execute("SAVEPOINT reconcile_knowledge_alerts")
    try:
        for row, reason in resolve_plan:
            alert_id = int(row["id"])
            previous_snooze = row["snoozed_until"]
            connection.execute(
                """
                UPDATE knowledge_alerts
                SET status = 'resolved', resolved_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (as_of_date.isoformat(), alert_id),
            )
            _insert_alert_event(
                connection,
                alert_id=alert_id,
                event_type=reason,
                from_status=str(row["status"]),
                to_status="resolved",
                effective_date=as_of_date,
                detail=(
                    {}
                    if previous_snooze is None
                    else {"snoozed_until": previous_snooze}
                ),
            )
            resolved_ids.append(alert_id)

        for row in reactivate_plan:
            alert_id = int(row["id"])
            previous_snooze = str(row["snoozed_until"])
            connection.execute(
                """
                UPDATE knowledge_alerts
                SET status = 'active', snoozed_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (alert_id,),
            )
            _insert_alert_event(
                connection,
                alert_id=alert_id,
                event_type="snooze_expired_reactivated",
                from_status="snoozed",
                to_status="active",
                effective_date=as_of_date,
                detail={"snoozed_until": previous_snooze},
            )
            reactivated_ids.append(alert_id)

        for snapshot in create_plan:
            cursor = connection.execute(
                """
                INSERT INTO knowledge_alerts(
                    fingerprint, topic_key, alert_type, status,
                    rule_version, evidence_version, condition_cycle,
                    as_of_date, recommended_date, priority, snapshot_json
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.fingerprint,
                    snapshot.topic_key,
                    snapshot.alert_type,
                    snapshot.rule_version,
                    snapshot.evidence_version,
                    snapshot.condition_cycle,
                    snapshot.as_of_date.isoformat(),
                    (
                        None
                        if snapshot.recommended_date is None
                        else snapshot.recommended_date.isoformat()
                    ),
                    float(snapshot.priority),
                    json.dumps(
                        snapshot.snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ),
            )
            alert_id = int(cursor.lastrowid)
            _insert_alert_event(
                connection,
                alert_id=alert_id,
                event_type="created",
                from_status=None,
                to_status="active",
                effective_date=as_of_date,
                detail={"fingerprint": snapshot.fingerprint},
            )
            created_ids.append(alert_id)
        connection.execute("RELEASE SAVEPOINT reconcile_knowledge_alerts")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT reconcile_knowledge_alerts")
        connection.execute("RELEASE SAVEPOINT reconcile_knowledge_alerts")
        raise

    return AlertReconcileResult(
        created_ids=tuple(created_ids),
        reactivated_ids=tuple(reactivated_ids),
        resolved_ids=tuple(resolved_ids),
        unchanged_ids=tuple(sorted(unchanged_ids)),
    )


def handle_knowledge_alert(
    alert_id: int,
    effective_date: datetime.date,
    actor: str,
    connection: sqlite3.Connection,
):
    """Persist the sole I06 transition and its event in one savepoint."""
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError("alert_id 必须是正整数")
    if not isinstance(effective_date, datetime.date) or isinstance(
        effective_date, datetime.datetime
    ):
        raise ValueError("effective_date 必须是 date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    if (
        not isinstance(actor, str)
        or not 1 <= len(actor) <= 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            for character in actor
        )
    ):
        raise ValueError("actor 必须是 1～64 位本地技术标识")

    _require_knowledge_alert_tables_from_connection(connection)
    row = connection.execute(
        "SELECT * FROM knowledge_alerts WHERE id = ?",
        (alert_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"未找到知识预警实例：{alert_id}")
    current = _knowledge_alert_from_row(row)
    if effective_date < current.as_of_date:
        raise ValueError("处理生效日不得早于预警快照日期")
    if current.status == "handled":
        return current
    if current.status != "active":
        raise ValueError(
            f"只有 active 预警可以处理；当前状态为 {current.status}"
        )

    connection.execute("SAVEPOINT handle_knowledge_alert")
    try:
        cursor = connection.execute(
            """
            UPDATE knowledge_alerts
            SET status = 'handled', handled_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active'
            """,
            (effective_date.isoformat(), alert_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("预警状态已变化，处理操作未执行")
        _insert_alert_event(
            connection,
            alert_id=alert_id,
            event_type="handled",
            from_status="active",
            to_status="handled",
            effective_date=effective_date,
            detail={},
            actor=actor,
        )
        updated_row = connection.execute(
            "SELECT * FROM knowledge_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
        if updated_row is None:
            raise RuntimeError("处理后的预警实例不可读")
        updated = _knowledge_alert_from_row(updated_row)
        connection.execute("RELEASE SAVEPOINT handle_knowledge_alert")
        return updated
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT handle_knowledge_alert")
        connection.execute("RELEASE SAVEPOINT handle_knowledge_alert")
        raise


def snooze_knowledge_alert(
    alert_id: int,
    snoozed_until: datetime.date,
    as_of_date: datetime.date,
    actor: str,
    connection: sqlite3.Connection,
):
    """Persist active/snoozed -> snoozed and append its complete date history."""
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError("alert_id 必须是正整数")
    for label, value in (
        ("snoozed_until", snoozed_until),
        ("as_of_date", as_of_date),
    ):
        if not isinstance(value, datetime.date) or isinstance(
            value, datetime.datetime
        ):
            raise ValueError(f"{label} 必须是 date")
    if snoozed_until < as_of_date:
        raise ValueError("延后日期不得早于 as_of_date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    if (
        not isinstance(actor, str)
        or not 1 <= len(actor) <= 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            for character in actor
        )
    ):
        raise ValueError("actor 必须是 1～64 位本地技术标识")

    _require_knowledge_alert_tables_from_connection(connection)
    row = connection.execute(
        "SELECT * FROM knowledge_alerts WHERE id = ?",
        (alert_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"未找到知识预警实例：{alert_id}")
    current = _knowledge_alert_from_row(row)
    if as_of_date < current.as_of_date:
        raise ValueError("操作日期不得早于预警快照日期")
    if current.status not in {"active", "snoozed"}:
        raise ValueError(
            "只有 active 或 snoozed 预警可以延后；"
            f"当前状态为 {current.status}"
        )
    if current.status == "snoozed":
        if current.snoozed_until is None:
            raise ValueError("snoozed 预警缺少延后到期日")
        if current.snoozed_until <= as_of_date:
            raise ValueError("延后已到期；必须先按当前条件执行协调重算")
    latest_event_row = connection.execute(
        """
        SELECT effective_date
        FROM knowledge_alert_events
        WHERE alert_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (alert_id,),
    ).fetchone()
    if latest_event_row is None:
        raise ValueError("预警实例缺少创建事件，不能延后")
    latest_event_date = _parse_iso_date(
        latest_event_row["effective_date"], label="latest_event.effective_date"
    )
    if as_of_date < latest_event_date:
        raise ValueError("操作日期不得早于上一条生命周期事件")

    previous_status = current.status
    previous_until = current.snoozed_until
    event_type = "snoozed" if previous_status == "active" else "resnoozed"
    connection.execute("SAVEPOINT snooze_knowledge_alert")
    try:
        cursor = connection.execute(
            """
            UPDATE knowledge_alerts
            SET status = 'snoozed', snoozed_until = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (snoozed_until.isoformat(), alert_id, previous_status),
        )
        if cursor.rowcount != 1:
            raise ValueError("预警状态已变化，延后操作未执行")
        _insert_alert_event(
            connection,
            alert_id=alert_id,
            event_type=event_type,
            from_status=previous_status,
            to_status="snoozed",
            effective_date=as_of_date,
            detail={
                "previous_snoozed_until": (
                    None if previous_until is None else previous_until.isoformat()
                ),
                "snoozed_until": snoozed_until.isoformat(),
            },
            actor=actor,
        )
        updated_row = connection.execute(
            "SELECT * FROM knowledge_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
        if updated_row is None:
            raise RuntimeError("延后后的预警实例不可读")
        updated = _knowledge_alert_from_row(updated_row)
        connection.execute("RELEASE SAVEPOINT snooze_knowledge_alert")
        return updated
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT snooze_knowledge_alert")
        connection.execute("RELEASE SAVEPOINT snooze_knowledge_alert")
        raise


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
    _require_knowledge_topic_registry_table(db_path)
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
