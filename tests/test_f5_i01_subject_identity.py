from __future__ import annotations

import hashlib
import sqlite3
import uuid

import pytest

from study_app.core.subject_identity import (
    SUBJECT_KEY_PREFIX,
    validate_subject_key,
)
from study_app.core.topic_identity import topic_key_for_id
from study_app.data.subject_repository import (
    SubjectCatalogRepository,
    SubjectIdentityConflictError,
    SubjectLifecycleNotInstalledError,
    install_subject_lifecycle_schema,
)


def fixed_uuids():
    values = iter(
        [
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
            uuid.UUID("00000000-0000-0000-0000-000000000002"),
            uuid.UUID("00000000-0000-0000-0000-000000000003"),
            uuid.UUID("00000000-0000-0000-0000-000000000004"),
        ]
    )
    return lambda: next(values)


@pytest.fixture
def repository(tmp_path):
    db_path = tmp_path / "isolated.sqlite"
    install_subject_lifecycle_schema(db_path)
    return SubjectCatalogRepository(db_path, uuid_factory=fixed_uuids())


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_readonly_open_never_installs_schema(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE legacy(id INTEGER PRIMARY KEY)")
    before = file_hash(db_path)
    repo = SubjectCatalogRepository(db_path)
    with pytest.raises(SubjectLifecycleNotInstalledError):
        repo.catalog_revision()
    assert file_hash(db_path) == before
    with sqlite3.connect(db_path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"legacy"}


def test_formal_keys_are_allocated_locally_and_names_do_not_become_identity(repository):
    subject = repository.create_subject("大学物理", capabilities={"study_plan": True})
    assert subject.subject_key == SUBJECT_KEY_PREFIX + "0" * 31 + "1"
    assert validate_subject_key(subject.subject_key) == subject.subject_key
    assert "大学物理" not in subject.subject_key
    module = repository.create_module(subject.subject_key, "力学")
    assert module.subject_key == subject.subject_key
    assert module.module_key.endswith("00000000000000000000000000000002")


def test_rename_keeps_key_and_old_name_as_alias(repository):
    before = repository.create_subject("微积分Ⅱ")
    after = repository.rename_subject(before.subject_key, "高等数学（二）")
    assert after.subject_key == before.subject_key
    assert after.object_version == before.object_version + 1
    assert repository.resolve_subject("微积分Ⅱ").subject_key == before.subject_key
    assert repository.resolve_subject(" 高等数学（二） ").subject_key == before.subject_key


def test_alias_conflict_is_rejected_without_partial_change(repository):
    first = repository.create_subject("物理")
    second = repository.create_subject("化学")
    revision = repository.catalog_revision()
    repository.add_alias(first.subject_key, "大物")
    with pytest.raises(SubjectIdentityConflictError):
        repository.add_alias(second.subject_key, " 大物 ")
    assert repository.resolve_subject("大物").subject_key == first.subject_key
    assert repository.catalog_revision() == revision + 1


def test_split_and_merge_require_explicit_relation(repository):
    source = repository.create_subject("综合课程")
    left = repository.create_subject("课程甲")
    right = repository.create_subject("课程乙")
    repository.record_relation("split_from", source.subject_key, left.subject_key, "DEC-1")
    repository.record_relation("merged_from", right.subject_key, left.subject_key, "DEC-2")
    with sqlite3.connect(repository.db_path) as connection:
        rows = connection.execute(
            "SELECT relation_type, decision_reference FROM subject_identity_relations ORDER BY relation_id"
        ).fetchall()
    assert rows == [("split_from", "DEC-1"), ("merged_from", "DEC-2")]


def test_capabilities_are_independent_and_unknown_subject_cannot_inherit(repository):
    physics = repository.create_subject("物理", capabilities={"study_plan": True, "oj": True})
    chemistry = repository.create_subject("化学", capabilities={"study_plan": False})
    assert repository.capability(physics.subject_key, "study_plan").available is True
    assert repository.capability(chemistry.subject_key, "study_plan").available is False
    oj = repository.capability(physics.subject_key, "oj")
    assert oj.declared_supported is True
    assert oj.environment_ready is False
    assert oj.available is False
    assert "knowledge_topic_registry" in oj.diagnostics[0]
    with pytest.raises(LookupError):
        repository.capability(SUBJECT_KEY_PREFIX + "f" * 32, "study_plan")


def test_missing_f2_f3_are_diagnostic_and_do_not_install_tables(repository):
    before = file_hash(repository.db_path)
    status = repository.schema_diagnostics()
    after = file_hash(repository.db_path)
    assert status["f2_ready"] is False
    assert status["f3_ready"] is False
    assert before == after
    with sqlite3.connect(repository.db_path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "knowledge_topic_registry" not in names
    assert not any(name.startswith("oj_") for name in names)


def test_f2_topic_key_is_reused_and_f1_f3_identifiers_are_untouched(repository):
    topic_key = topic_key_for_id(1)
    with sqlite3.connect(repository.db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE knowledge_topic_registry(
                topic_key TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL,
                identity_version TEXT NOT NULL
            );
            CREATE TABLE compatibility_refs(
                task_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                topic_key TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO knowledge_topic_registry VALUES (?, 1, 'topic-identity-v1')",
            (topic_key,),
        )
        connection.execute(
            "INSERT INTO compatibility_refs VALUES ('task-1', 'source-1', ?)",
            (topic_key,),
        )
    assert repository.require_topic_key(topic_key) == topic_key
    subject = repository.create_subject("计算机科学")
    repository.rename_subject(subject.subject_key, "计算机学科")
    with sqlite3.connect(repository.db_path) as connection:
        row = connection.execute("SELECT * FROM compatibility_refs").fetchone()
    assert row == ("task-1", "source-1", topic_key)
