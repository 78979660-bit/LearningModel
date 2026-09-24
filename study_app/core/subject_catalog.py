from __future__ import annotations

import copy
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from study_app.core.subject_identity import normalize_alias, validate_module_key, validate_subject_key
from study_app.data.subject_repository import (
    SubjectCatalogRepository,
    SubjectLifecycleNotInstalledError,
)


STRUCTURE_KEY_PREFIX = "structure:v1:"


@dataclass(frozen=True)
class CatalogTopic:
    topic_key: str
    name: str
    topic_order: int
    importance_bp: int | None
    difficulty_bp: int | None


@dataclass(frozen=True)
class CatalogModule:
    module_key: str
    canonical_name: str
    display_name: str
    module_order: int
    topics: tuple[CatalogTopic, ...]


@dataclass(frozen=True)
class CatalogSubject:
    subject_key: str
    canonical_name: str
    display_name: str
    lifecycle_status: str
    object_version: int
    aliases: tuple[str, ...]
    capabilities: tuple[tuple[str, bool], ...]
    structure_version: str | None
    modules: tuple[CatalogModule, ...]

    @property
    def capability_map(self) -> dict[str, bool]:
        return dict(self.capabilities)


@dataclass(frozen=True)
class CatalogSnapshot:
    catalog_revision: int
    subjects: tuple[CatalogSubject, ...]

    def by_key(self) -> dict[str, CatalogSubject]:
        return {subject.subject_key: subject for subject in self.subjects}

    def alias_map(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for subject in self.subjects:
            for alias in (subject.canonical_name, *subject.aliases):
                result[normalize_alias(alias)] = subject.subject_key
        return result

    def resolve(self, name_or_alias: object) -> CatalogSubject:
        key = self.alias_map().get(normalize_alias(name_or_alias))
        if key is None:
            raise LookupError(f"未知学科名称或别名：{name_or_alias!r}")
        return self.by_key()[key]


def load_catalog_snapshot(db_path: Path | str) -> CatalogSnapshot:
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction() as connection:
        connection.execute("BEGIN")
        revision_before = int(
            connection.execute(
                "SELECT catalog_revision FROM subject_catalog_state WHERE singleton = 1"
            ).fetchone()[0]
        )
        subject_rows = connection.execute(
            "SELECT * FROM subject_catalog ORDER BY subject_key"
        ).fetchall()
        alias_rows = connection.execute(
            "SELECT subject_key, alias FROM subject_aliases ORDER BY alias_normalized"
        ).fetchall()
        capability_rows = connection.execute(
            """
            SELECT subject_key, capability_key, declared_supported
            FROM subject_capabilities ORDER BY subject_key, capability_key
            """
        ).fetchall()
        structure_rows = connection.execute(
            """
            SELECT subject_key, structure_version
            FROM subject_structure_versions WHERE is_current = 1
            ORDER BY subject_key
            """
        ).fetchall()
        module_rows = connection.execute(
            """
            SELECT versions.subject_key, links.structure_version, links.module_order,
                   modules.module_key, modules.canonical_name, modules.display_name
            FROM subject_structure_versions versions
            JOIN subject_structure_modules links
              ON links.structure_version = versions.structure_version
            JOIN subject_module_identities modules ON modules.module_key = links.module_key
            WHERE versions.is_current = 1
            ORDER BY versions.subject_key, links.module_order
            """
        ).fetchall()
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "knowledge_topic_registry" in table_names:
            topic_rows = connection.execute(
                """
                SELECT versions.subject_key, links.structure_version, links.module_key,
                       links.topic_key, links.topic_order, links.importance_bp,
                       links.difficulty_bp, topics.name AS topic_name
                FROM subject_structure_versions versions
                JOIN subject_structure_topics links
                  ON links.structure_version = versions.structure_version
                JOIN knowledge_topic_registry registry ON registry.topic_key = links.topic_key
                JOIN topics ON topics.id = registry.topic_id
                WHERE versions.is_current = 1
                ORDER BY versions.subject_key, links.module_key, links.topic_order
                """
            ).fetchall()
        else:
            topic_rows = []
        revision_after = int(
            connection.execute(
                "SELECT catalog_revision FROM subject_catalog_state WHERE singleton = 1"
            ).fetchone()[0]
        )
    if revision_before != revision_after:
        raise RuntimeError("目录修订在读取期间发生变化")

    aliases: dict[str, list[str]] = {}
    for row in alias_rows:
        aliases.setdefault(row["subject_key"], []).append(row["alias"])
    capabilities: dict[str, list[tuple[str, bool]]] = {}
    for row in capability_rows:
        capabilities.setdefault(row["subject_key"], []).append(
            (row["capability_key"], bool(row["declared_supported"]))
        )
    structures = {row["subject_key"]: row["structure_version"] for row in structure_rows}
    topics_by_module: dict[tuple[str, str], list[CatalogTopic]] = {}
    for row in topic_rows:
        topics_by_module.setdefault(
            (row["structure_version"], row["module_key"]), []
        ).append(
            CatalogTopic(
                topic_key=row["topic_key"],
                name=row["topic_name"],
                topic_order=int(row["topic_order"]),
                importance_bp=row["importance_bp"],
                difficulty_bp=row["difficulty_bp"],
            )
        )
    modules_by_subject: dict[str, list[CatalogModule]] = {}
    for row in module_rows:
        modules_by_subject.setdefault(row["subject_key"], []).append(
            CatalogModule(
                module_key=row["module_key"],
                canonical_name=row["canonical_name"],
                display_name=row["display_name"],
                module_order=int(row["module_order"]),
                topics=tuple(
                    topics_by_module.get(
                        (row["structure_version"], row["module_key"]), []
                    )
                ),
            )
        )
    subjects = tuple(
        CatalogSubject(
            subject_key=row["subject_key"],
            canonical_name=row["canonical_name"],
            display_name=row["display_name"],
            lifecycle_status=row["lifecycle_status"],
            object_version=int(row["object_version"]),
            aliases=tuple(dict.fromkeys(aliases.get(row["subject_key"], []))),
            capabilities=tuple(capabilities.get(row["subject_key"], [])),
            structure_version=structures.get(row["subject_key"]),
            modules=tuple(modules_by_subject.get(row["subject_key"], [])),
        )
        for row in subject_rows
    )
    return CatalogSnapshot(revision_before, subjects)


def adopt_structure(
    db_path: Path | str,
    subject_key: object,
    modules: Mapping[str, Iterable[str]],
    *,
    source_reference: str,
    manifest_version: str | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> str:
    valid_subject = validate_subject_key(subject_key)
    if not isinstance(source_reference, str) or not source_reference.strip():
        raise ValueError("source_reference 必须是非空字符串")
    structure_version = STRUCTURE_KEY_PREFIX + uuid_factory().hex
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction(readonly=False) as connection:
        module_keys = [validate_module_key(key) for key in modules]
        if len(module_keys) != len(set(module_keys)):
            raise ValueError("结构中存在重复 module_key")
        for module_key in module_keys:
            row = connection.execute(
                "SELECT subject_key FROM subject_module_identities WHERE module_key = ?",
                (module_key,),
            ).fetchone()
            if row is None or row["subject_key"] != valid_subject:
                raise ValueError(f"module_key 不属于目标学科：{module_key}")
        normalized_topics: dict[str, tuple[str, ...]] = {}
        for module_key, topic_keys in modules.items():
            values = tuple(topic_keys)
            if len(values) != len(set(values)):
                raise ValueError(f"模块含重复 topic_key：{module_key}")
            for topic_key in values:
                repository.require_topic_key(topic_key)
            normalized_topics[module_key] = values
        connection.execute(
            "UPDATE subject_structure_versions SET is_current = 0 WHERE subject_key = ? AND is_current = 1",
            (valid_subject,),
        )
        connection.execute(
            """
            INSERT INTO subject_structure_versions(
                structure_version, subject_key, manifest_version,
                source_reference, is_current
            ) VALUES (?, ?, ?, ?, 1)
            """,
            (structure_version, valid_subject, manifest_version, source_reference.strip()),
        )
        for module_order, module_key in enumerate(module_keys):
            connection.execute(
                "INSERT INTO subject_structure_modules VALUES (?, ?, ?)",
                (structure_version, module_key, module_order),
            )
            for topic_order, topic_key in enumerate(normalized_topics[module_key]):
                connection.execute(
                    """
                    INSERT INTO subject_structure_topics(
                        structure_version, module_key, topic_key, topic_order
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (structure_version, module_key, topic_key, topic_order),
                )
        connection.execute(
            "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
        )
    return structure_version


def overlay_model_with_catalog(
    model: Mapping[str, object], snapshot: CatalogSnapshot
) -> dict:
    legacy = copy.deepcopy(dict(model))
    legacy_subjects = list(legacy.get("subjects", []) or [])
    aliases = snapshot.alias_map()
    legacy_by_key: dict[str, dict] = {}
    for item in legacy_subjects:
        if not isinstance(item, dict):
            continue
        key = aliases.get(normalize_alias(item.get("name", "")))
        if key is not None and key not in legacy_by_key:
            legacy_by_key[key] = item
    projected_subjects: list[dict] = []
    for catalog_subject in snapshot.subjects:
        subject = copy.deepcopy(legacy_by_key.get(catalog_subject.subject_key, {}))
        legacy_modules = {
            normalize_alias(module.get("name", "")): module
            for module in subject.get("modules", []) or []
            if isinstance(module, dict) and module.get("name")
        }
        modules: list[dict] = []
        for catalog_module in catalog_subject.modules:
            module = copy.deepcopy(
                legacy_modules.get(normalize_alias(catalog_module.canonical_name), {})
            )
            legacy_topics = {
                normalize_alias(topic.get("name", "")): topic
                for topic in module.get("topics", []) or []
                if isinstance(topic, dict) and topic.get("name")
            }
            topics: list[dict] = []
            for catalog_topic in catalog_module.topics:
                topic = copy.deepcopy(
                    legacy_topics.get(normalize_alias(catalog_topic.name), {})
                )
                topic["name"] = catalog_topic.name
                topic["topic_key"] = catalog_topic.topic_key
                topics.append(topic)
            module["name"] = catalog_module.canonical_name
            module["display_name"] = catalog_module.display_name
            module["module_key"] = catalog_module.module_key
            module["topics"] = topics
            modules.append(module)
        subject["subject_key"] = catalog_subject.subject_key
        subject["name"] = catalog_subject.canonical_name
        subject["display_name"] = catalog_subject.display_name
        subject["lifecycle"] = {"status": catalog_subject.lifecycle_status}
        subject["capabilities"] = catalog_subject.capability_map
        subject["structure_version"] = catalog_subject.structure_version
        subject["modules"] = modules
        projected_subjects.append(subject)
    legacy["subjects"] = projected_subjects
    legacy["subject_catalog_revision"] = snapshot.catalog_revision
    legacy["subject_catalog_source"] = "sqlite"
    return legacy


def canonicalize_record_subjects(
    records: Iterable[Mapping[str, object]], snapshot: CatalogSnapshot
) -> list[dict]:
    result: list[dict] = []
    for item in records:
        record = dict(item)
        try:
            subject = snapshot.resolve(record.get("subject", ""))
        except (LookupError, ValueError):
            pass
        else:
            record["subject"] = subject.canonical_name
            record["subject_key"] = subject.subject_key
        result.append(record)
    return result


def overlay_if_installed(
    model: Mapping[str, object], db_path: Path | str
) -> tuple[dict, CatalogSnapshot | None, str]:
    try:
        snapshot = load_catalog_snapshot(db_path)
    except (SubjectLifecycleNotInstalledError, sqlite3.Error) as error:
        return copy.deepcopy(dict(model)), None, f"F5 目录兼容模式：{error}"
    if not snapshot.subjects and list(model.get("subjects", []) or []):
        return (
            copy.deepcopy(dict(model)),
            None,
            "F5 目录兼容模式：目录尚未登记旧版模型中的学科",
        )
    return overlay_model_with_catalog(model, snapshot), snapshot, ""
