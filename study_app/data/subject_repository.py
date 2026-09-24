from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Callable, Iterator

from study_app.core.subject_capabilities import (
    CAPABILITY_KEYS,
    CapabilityAvailability,
    validate_capability_key,
)
from study_app.core.subject_identity import (
    ModuleIdentity,
    SubjectIdentity,
    allocate_module_key,
    allocate_subject_key,
    make_module_identity,
    make_subject_identity,
    normalize_alias,
    normalize_name,
    validate_module_key,
    validate_subject_key,
)


class SubjectLifecycleNotInstalledError(RuntimeError):
    pass


class SubjectIdentityConflictError(ValueError):
    pass


def _connect(path: Path | str, *, readonly: bool) -> sqlite3.Connection:
    db_path = Path(path)
    if readonly:
        if not db_path.is_file():
            raise SubjectLifecycleNotInstalledError(f"数据库不存在：{db_path}")
        connection = sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro", uri=True
        )
        connection.execute("PRAGMA query_only = ON")
    else:
        connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _table_names(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )


def install_subject_lifecycle_schema(db_path: Path | str) -> None:
    """Explicit installer. Runtime reads and repository construction never call it."""
    schema_path = Path(__file__).with_name("f5_schema.sql")
    script = schema_path.read_text(encoding="utf-8")
    with closing(_connect(db_path, readonly=False)) as connection:
        with connection:
            connection.executescript(script)


class SubjectCatalogRepository:
    REQUIRED_TABLES = frozenset(
        {
            "subject_catalog_state",
            "subject_catalog",
            "subject_aliases",
            "subject_capabilities",
            "subject_module_identities",
            "subject_structure_versions",
            "subject_structure_modules",
            "subject_structure_topics",
            "subject_identity_relations",
            "subject_documents",
            "document_preflight_inspections",
            "document_parses",
            "document_pages",
            "document_page_contents",
            "subject_evidence",
            "subject_manifest_versions",
            "subject_change_operations",
            "subject_changesets",
            "subject_approvals",
            "subject_operation_preflight_events",
            "subject_operation_attempts",
            "subject_operation_events",
            "subject_lifecycle_events",
            "subject_projection_outbox",
            "historical_correction_requests",
            "historical_correction_events",
        }
    )

    def __init__(
        self,
        db_path: Path | str,
        *,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self.db_path = Path(db_path)
        self._uuid_factory = uuid_factory

    def _open(self, *, readonly: bool = True) -> sqlite3.Connection:
        connection = _connect(self.db_path, readonly=readonly)
        missing = self.REQUIRED_TABLES - _table_names(connection)
        if missing:
            connection.close()
            raise SubjectLifecycleNotInstalledError(
                "F5 学科生命周期结构尚未安装：" + ", ".join(sorted(missing))
            )
        return connection

    @contextmanager
    def transaction(
        self, *, readonly: bool = True, snapshot: bool = False
    ) -> Iterator[sqlite3.Connection]:
        """Expose a checked catalog transaction for multi-table operations.

        Callers that coordinate several tables can keep one atomic unit of work.
        Use snapshot=True for a consistent multi-query read view.
        The connection is committed or rolled back and then closed on exit.
        """
        connection = self._open(readonly=readonly)
        try:
            if readonly and snapshot:
                # sqlite3 does not start a transaction for SELECT statements.
                # Pin all reads in a multi-query view to one database snapshot.
                connection.execute("BEGIN")
            with connection:
                yield connection
        finally:
            connection.close()

    def begin_immediate(self) -> sqlite3.Connection:
        """Reserve a catalog write transaction for a caller with explicit commits.

        The caller must commit or roll back and close the returned connection.
        This is used when projection publication must be coordinated with SQL.
        """
        connection = self._open(readonly=False)
        try:
            connection.execute("BEGIN IMMEDIATE")
        except Exception:
            connection.close()
            raise
        return connection

    def schema_diagnostics(self) -> dict[str, object]:
        with closing(_connect(self.db_path, readonly=True)) as connection:
            names = _table_names(connection)
        required_missing = tuple(sorted(self.REQUIRED_TABLES - names))
        f2_ready = "knowledge_topic_registry" in names
        f3_tables = {"oj_problems", "oj_problem_topics", "oj_attempts"}
        f3_ready = f3_tables.issubset(names)
        return {
            "f5_ready": not required_missing,
            "f5_missing": required_missing,
            "f1_ready": {"study_task_estimates", "study_plans"}.issubset(names),
            "f2_ready": f2_ready,
            "f3_ready": f3_ready,
            "diagnostics": tuple(
                message
                for ready, message in (
                    (f2_ready, "F2 knowledge_topic_registry 尚未安装"),
                    (f3_ready, "F3 OJ schema 尚未安装"),
                )
                if not ready
            ),
        }

    def create_subject(
        self,
        canonical_name: object,
        *,
        display_name: object | None = None,
        capabilities: Mapping[str, bool] | None = None,
    ) -> SubjectIdentity:
        canonical = normalize_name(canonical_name, label="canonical_name")
        display = normalize_name(
            canonical if display_name is None else display_name,
            label="display_name",
        )
        normalized = normalize_alias(canonical)
        subject_key = allocate_subject_key(self._uuid_factory)
        declared = dict(capabilities or {})
        for key, value in declared.items():
            validate_capability_key(key)
            if type(value) is not bool:
                raise ValueError(f"能力声明必须是布尔值：{key}={value!r}")
        try:
            with self.transaction(readonly=False) as connection:
                connection.execute(
                    """
                    INSERT INTO subject_catalog(
                        subject_key, canonical_name, canonical_name_normalized,
                        display_name, lifecycle_status
                    ) VALUES (?, ?, ?, ?, 'active')
                    """,
                    (subject_key, canonical, normalized, display),
                )
                connection.execute(
                    "INSERT INTO subject_aliases(alias_normalized, alias, subject_key) VALUES (?, ?, ?)",
                    (normalized, canonical, subject_key),
                )
                for key in sorted(CAPABILITY_KEYS):
                    connection.execute(
                        """
                        INSERT INTO subject_capabilities(
                            subject_key, capability_key, declared_supported
                        ) VALUES (?, ?, ?)
                        """,
                        (subject_key, key, int(declared.get(key, False))),
                    )
                connection.execute(
                    "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
                )
        except sqlite3.IntegrityError as error:
            raise SubjectIdentityConflictError(
                f"学科名称或别名冲突：{canonical}"
            ) from error
        return self.get_subject(subject_key)

    def get_subject(self, subject_key: object) -> SubjectIdentity:
        valid_key = validate_subject_key(subject_key)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM subject_catalog WHERE subject_key = ?", (valid_key,)
            ).fetchone()
        if row is None:
            raise LookupError(f"未知 subject_key：{valid_key}")
        return make_subject_identity(row)

    def resolve_subject(self, name_or_alias: object) -> SubjectIdentity:
        normalized = normalize_alias(name_or_alias)
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT catalog.*
                FROM subject_aliases aliases
                JOIN subject_catalog catalog ON catalog.subject_key = aliases.subject_key
                WHERE aliases.alias_normalized = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            raise LookupError(f"未知学科名称或别名：{name_or_alias!r}")
        return make_subject_identity(row)

    def add_alias(self, subject_key: object, alias: object) -> None:
        valid_key = validate_subject_key(subject_key)
        text = normalize_name(alias, label="alias")
        normalized = normalize_alias(text)
        try:
            with self.transaction(readonly=False) as connection:
                if connection.execute(
                    "SELECT 1 FROM subject_catalog WHERE subject_key = ?", (valid_key,)
                ).fetchone() is None:
                    raise LookupError(f"未知 subject_key：{valid_key}")
                connection.execute(
                    "INSERT INTO subject_aliases(alias_normalized, alias, subject_key) VALUES (?, ?, ?)",
                    (normalized, text, valid_key),
                )
                connection.execute(
                    "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
                )
        except sqlite3.IntegrityError as error:
            raise SubjectIdentityConflictError(f"别名已被占用：{text}") from error

    def rename_subject(
        self,
        subject_key: object,
        canonical_name: object,
        *,
        display_name: object | None = None,
    ) -> SubjectIdentity:
        valid_key = validate_subject_key(subject_key)
        canonical = normalize_name(canonical_name, label="canonical_name")
        normalized = normalize_alias(canonical)
        display = normalize_name(
            canonical if display_name is None else display_name,
            label="display_name",
        )
        try:
            with self.transaction(readonly=False) as connection:
                current = connection.execute(
                    "SELECT * FROM subject_catalog WHERE subject_key = ?", (valid_key,)
                ).fetchone()
                if current is None:
                    raise LookupError(f"未知 subject_key：{valid_key}")
                connection.execute(
                    "INSERT OR IGNORE INTO subject_aliases(alias_normalized, alias, subject_key) VALUES (?, ?, ?)",
                    (
                        current["canonical_name_normalized"],
                        current["canonical_name"],
                        valid_key,
                    ),
                )
                occupied = connection.execute(
                    "SELECT subject_key FROM subject_aliases WHERE alias_normalized = ?",
                    (normalized,),
                ).fetchone()
                if occupied is not None and occupied["subject_key"] != valid_key:
                    raise SubjectIdentityConflictError(f"名称已被其他学科占用：{canonical}")
                connection.execute(
                    "INSERT OR IGNORE INTO subject_aliases(alias_normalized, alias, subject_key) VALUES (?, ?, ?)",
                    (normalized, canonical, valid_key),
                )
                connection.execute(
                    """
                    UPDATE subject_catalog
                    SET canonical_name = ?, canonical_name_normalized = ?, display_name = ?,
                        object_version = object_version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE subject_key = ?
                    """,
                    (canonical, normalized, display, valid_key),
                )
                connection.execute(
                    "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
                )
        except sqlite3.IntegrityError as error:
            raise SubjectIdentityConflictError(f"名称或别名冲突：{canonical}") from error
        return self.get_subject(valid_key)

    def create_module(
        self,
        subject_key: object,
        canonical_name: object,
        *,
        display_name: object | None = None,
    ) -> ModuleIdentity:
        valid_subject = validate_subject_key(subject_key)
        canonical = normalize_name(canonical_name, label="canonical_name")
        display = normalize_name(
            canonical if display_name is None else display_name,
            label="display_name",
        )
        module_key = allocate_module_key(self._uuid_factory)
        try:
            with self.transaction(readonly=False) as connection:
                connection.execute(
                    """
                    INSERT INTO subject_module_identities(
                        module_key, subject_key, canonical_name,
                        canonical_name_normalized, display_name
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        module_key,
                        valid_subject,
                        canonical,
                        normalize_alias(canonical),
                        display,
                    ),
                )
                connection.execute(
                    "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
                )
        except sqlite3.IntegrityError as error:
            raise SubjectIdentityConflictError(
                f"模块名称冲突或学科不存在：{canonical}"
            ) from error
        return self.get_module(module_key)

    def get_module(self, module_key: object) -> ModuleIdentity:
        valid_key = validate_module_key(module_key)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM subject_module_identities WHERE module_key = ?",
                (valid_key,),
            ).fetchone()
        if row is None:
            raise LookupError(f"未知 module_key：{valid_key}")
        return make_module_identity(row)

    def record_relation(
        self,
        relation_type: object,
        source_subject_key: object,
        target_subject_key: object,
        decision_reference: object,
    ) -> None:
        if relation_type not in {"split_from", "merged_from"}:
            raise ValueError(f"不支持的身份关系：{relation_type!r}")
        source = validate_subject_key(source_subject_key)
        target = validate_subject_key(target_subject_key)
        decision = normalize_name(decision_reference, label="decision_reference")
        with self.transaction(readonly=False) as connection:
            connection.execute(
                """
                INSERT INTO subject_identity_relations(
                    relation_type, source_subject_key, target_subject_key, decision_reference
                ) VALUES (?, ?, ?, ?)
                """,
                (relation_type, source, target, decision),
            )
            connection.execute(
                "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
            )

    def require_topic_key(self, topic_key: object) -> str:
        from study_app.core.topic_identity import validate_topic_key

        valid_key = validate_topic_key(topic_key)
        with self.transaction() as connection:
            names = _table_names(connection)
            if "knowledge_topic_registry" not in names:
                raise SubjectLifecycleNotInstalledError(
                    "F2 knowledge_topic_registry 尚未安装；不能按名称猜测 topic_key"
                )
            row = connection.execute(
                "SELECT 1 FROM knowledge_topic_registry WHERE topic_key = ?",
                (valid_key,),
            ).fetchone()
        if row is None:
            raise LookupError(f"未知 F2 topic_key：{valid_key}")
        return valid_key

    def capability(
        self, subject_key: object, capability_key: object
    ) -> CapabilityAvailability:
        valid_subject = validate_subject_key(subject_key)
        key = validate_capability_key(capability_key)
        with self.transaction() as connection:
            subject = connection.execute(
                "SELECT lifecycle_status FROM subject_catalog WHERE subject_key = ?",
                (valid_subject,),
            ).fetchone()
            if subject is None:
                raise LookupError(f"未知 subject_key：{valid_subject}")
            declaration = connection.execute(
                """
                SELECT declared_supported FROM subject_capabilities
                WHERE subject_key = ? AND capability_key = ?
                """,
                (valid_subject, key),
            ).fetchone()
            names = _table_names(connection)
        diagnostics: list[str] = []
        environment_ready = True
        if key == "oj":
            required = {
                "knowledge_topic_registry",
                "oj_problems",
                "oj_problem_topics",
                "oj_attempts",
            }
            missing = sorted(required - names)
            environment_ready = not missing
            if missing:
                diagnostics.append("OJ 依赖尚未安装：" + ", ".join(missing))
        lifecycle_allowed = subject["lifecycle_status"] == "active"
        if not lifecycle_allowed:
            diagnostics.append("学科已归档")
        return CapabilityAvailability(
            capability_key=key,
            declared_supported=bool(declaration["declared_supported"]),
            environment_ready=environment_ready,
            lifecycle_allowed=lifecycle_allowed,
            diagnostics=tuple(diagnostics),
        )

    def catalog_revision(self) -> int:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT catalog_revision FROM subject_catalog_state WHERE singleton = 1"
            ).fetchone()
        return int(row["catalog_revision"])
