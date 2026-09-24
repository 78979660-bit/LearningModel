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

from study_app.data.alert_repository import (
    KNOWLEDGE_ALERTS_TABLE_SQL,
    KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
    KNOWLEDGE_ALERT_INDEXES_SQL,
    _insert_alert_event,
    _knowledge_alert_event_from_row,
    _knowledge_alert_from_row,
    _parse_iso_date,
    _parse_json_object,
    _require_knowledge_alert_tables,
    _require_knowledge_alert_tables_from_connection,
    _validate_alert_snapshot,
    handle_knowledge_alert,
    list_knowledge_alert_events,
    list_knowledge_alerts,
    reconcile_knowledge_alert_snapshots,
    snooze_knowledge_alert,
)
from study_app.data.catalog_guards import (
    F5_STRICT_MODE_SETTING,
    f5_catalog_is_installed,
    require_f5_subject_write_allowed as _require_f5_subject_write_allowed,
    require_knowledge_topic_registry_table as _require_knowledge_topic_registry_table,
)
from study_app.data.db_runtime import (
    DatabaseNotInitializedError,
    _ClosingConnection,
    _DATE_PATTERN,
    connect,
    connect_readonly,
    dumps,
    require_initialized_database,
    validate_calendar_date,
)
from study_app.data.settings_audit_repository import (
    delete_settings_by_prefix,
    get_setting,
    list_llm_call_audits,
    record_llm_call_audit,
    set_setting,
    update_llm_call_audit,
    upsert_setting,
)

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
NORMALIZATION_VERSION = "data_contract_v1@v1.0.2"
F5_LEGACY_BOOTSTRAP_SETTING = "f5_catalog:legacy_bootstrap_v1"


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


# Compatibility exports for existing study plan callers.
from study_app.data.plan_repository import (
    BUDGET_PLAN_FORMAT_VERSION,
    BUDGET_PLAN_SCOPE,
    F1_I06_MIGRATION_STATEMENTS,
    STUDY_DAY_BUDGET_TABLE_SQL,
    STUDY_SUBJECT_EXAM_DATES_TABLE_SQL,
    STUDY_TASK_ESTIMATES_TABLE_SQL,
    _require_budget_plan_schema,
    _require_study_day_budget_table,
    _require_subject_exam_dates_table,
    _require_task_estimates_table,
    _scope_key,
    archive_active_study_plan,
    create_budgeted_day_plan,
    create_study_plan,
    get_active_study_plan,
    get_budgeted_day_plan,
    get_study_day_budget,
    get_subject_exam_date,
    get_task_estimate,
    recent_study_plan_texts,
    save_study_day_budget,
    save_subject_exam_date,
    save_task_estimate,
    study_plan_signature,
    update_study_plan_item_state,
)

# Compatibility exports for callers of study_app.data.database.
from study_app.data.oj_repository import (
    OJ_ATTEMPTS_INDEX_SQL,
    OJ_ATTEMPTS_TABLE_SQL,
    OJ_PROBLEMS_TABLE_SQL,
    OJ_PROBLEM_TOPICS_TABLE_SQL,
    _oj_attempt_from_row,
    _oj_problem_from_row,
    _oj_topic_mapping_from_row,
    _require_oj_attempts_table,
    _require_oj_problem_topics_table,
    _require_oj_problems_table,
    append_oj_attempt,
    get_oj_problem,
    install_oj_schema,
    list_oj_attempts,
    list_oj_evidence_rows,
    list_oj_problem_topics,
    list_oj_problems,
    register_oj_problem,
    replace_oj_problem_topics,
)
