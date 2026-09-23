from __future__ import annotations

import json
import sqlite3
import uuid
from unittest.mock import patch

import pytest

from study_app.core.active_subjects import subject_reference_violations
from study_app.core.dashboard import load_dashboard_state
from study_app.core.subject_catalog import (
    adopt_structure,
    load_catalog_snapshot,
    overlay_model_with_catalog,
)
from study_app.core.subject_projection import (
    load_projection,
    projection_payload,
    publish_projection,
    rebuild_projection,
)
from study_app.core.topic_identity import TOPIC_IDENTITY_VERSION, topic_key_for_id
from study_app.data.database import initialize_database
from study_app.data.subject_repository import (
    SubjectCatalogRepository,
    install_subject_lifecycle_schema,
)


@pytest.fixture
def catalog(tmp_path):
    db_path = tmp_path / "catalog.sqlite"
    initialize_database(db_path)
    install_subject_lifecycle_schema(db_path)
    values = iter(
        [
            uuid.UUID("00000000-0000-0000-0000-000000000011"),
            uuid.UUID("00000000-0000-0000-0000-000000000012"),
            uuid.UUID("00000000-0000-0000-0000-000000000014"),
        ]
    )
    repo = SubjectCatalogRepository(db_path, uuid_factory=lambda: next(values))
    subject = repo.create_subject(
        "新物理", display_name="物理学", capabilities={"study_plan": True}
    )
    repo.add_alias(subject.subject_key, "旧物理")
    module = repo.create_module(subject.subject_key, "力学")
    with sqlite3.connect(db_path) as connection:
        legacy_subject_id = connection.execute(
            "INSERT INTO subjects(name, status) VALUES ('旧物理', 'active')"
        ).lastrowid
        legacy_module_id = connection.execute(
            "INSERT INTO modules(subject_id, name) VALUES (?, '力学')",
            (legacy_subject_id,),
        ).lastrowid
        topic_id = connection.execute(
            """
            INSERT INTO topics(module_id, name, status, mastery, importance, difficulty, source_json)
            VALUES (?, '动量', 'learning', 0.6, 0.8, 0.7, '{}')
            """,
            (legacy_module_id,),
        ).lastrowid
        topic_key = topic_key_for_id(topic_id)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_topic_registry(
                topic_key TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL UNIQUE REFERENCES topics(id) ON DELETE RESTRICT,
                identity_version TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_topic_registry(
                topic_key, topic_id, identity_version
            ) VALUES (?, ?, ?)
            """,
            (topic_key, topic_id, TOPIC_IDENTITY_VERSION),
        )
    adopt_structure(
        db_path,
        subject.subject_key,
        {module.module_key: (topic_key,)},
        source_reference="legacy-import:test",
        uuid_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000013"),
    )
    return db_path, repo, subject, module, topic_key


def legacy_model():
    return {
        "model_name": "test",
        "warning_policy": {"period_days": 3, "period_benchmark_score": 55},
        "subjects": [
            {
                "name": "旧物理",
                "display_name": "陈旧显示名",
                "mastery": 0.77,
                "initial_score": 66,
                "lifecycle": {"status": "archived"},
                "capabilities": {"study_plan": False},
                "modules": [
                    {
                        "name": "力学",
                        "mastery": 0.65,
                        "topics": [{"name": "动量", "mastery": 0.61}],
                    }
                ],
            },
            {"name": "仅 JSON 学科", "mastery": 0.9, "modules": []},
        ],
    }


def test_sqlite_directory_overrides_stale_json_but_preserves_unmigrated_parameters(catalog):
    db_path, _repo, subject, module, topic_key = catalog
    snapshot = load_catalog_snapshot(db_path)
    overlaid = overlay_model_with_catalog(legacy_model(), snapshot)
    assert overlaid["subject_catalog_revision"] == snapshot.catalog_revision
    assert len(overlaid["subjects"]) == 1
    item = overlaid["subjects"][0]
    assert item["subject_key"] == subject.subject_key
    assert item["name"] == "新物理"
    assert item["display_name"] == "物理学"
    assert item["lifecycle"] == {"status": "active"}
    assert item["capabilities"]["study_plan"] is True
    assert item["mastery"] == 0.77
    assert item["modules"][0]["module_key"] == module.module_key
    assert item["modules"][0]["topics"][0]["topic_key"] == topic_key
    assert item["modules"][0]["topics"][0]["mastery"] == 0.61


def test_projection_can_be_deleted_and_rebuilt_equivalently(catalog, tmp_path):
    db_path, *_ = catalog
    projection = tmp_path / "subject_catalog_projection.json"
    first = rebuild_projection(db_path, projection)
    first_bytes = projection.read_bytes()
    projection.unlink()
    second = rebuild_projection(db_path, projection)
    assert second == first
    assert projection.read_bytes() == first_bytes
    assert load_projection(projection, expected_revision=first["catalog_revision"]) == first


def test_old_projection_cannot_overwrite_newer_revision(catalog, tmp_path):
    db_path, repo, *_ = catalog
    projection = tmp_path / "projection.json"
    old = load_catalog_snapshot(db_path)
    repo.create_subject("化学")
    new = load_catalog_snapshot(db_path)
    publish_projection(new, projection)
    with pytest.raises(RuntimeError, match="旧投影"):
        publish_projection(old, projection)
    assert load_projection(projection)["catalog_revision"] == new.catalog_revision


def test_stale_json_cannot_change_projection_authority(catalog):
    db_path, *_ = catalog
    snapshot = load_catalog_snapshot(db_path)
    before = projection_payload(snapshot)
    stale = legacy_model()
    stale["subjects"][0]["name"] = "伪造名称"
    stale["subjects"][0]["lifecycle"] = {"status": "archived"}
    overlay_model_with_catalog(stale, snapshot)
    assert projection_payload(load_catalog_snapshot(db_path)) == before


def test_dashboard_exposes_one_catalog_revision(catalog, tmp_path):
    db_path, _repo, subject, *_ = catalog
    model_path = tmp_path / "model.json"
    records_path = tmp_path / "records.json"
    model_path.write_text(json.dumps(legacy_model(), ensure_ascii=False), encoding="utf-8")
    records_path.write_text('{"records": []}', encoding="utf-8")
    with (
        patch("study_app.core.study_phase.is_in_exam_scope", return_value=True),
        patch("study_app.core.study_phase.weighted_topic_priority_states", return_value=[]),
    ):
        state = load_dashboard_state(
            model_path=model_path,
            records_path=records_path,
            today="2026-09-18",
            db_path=db_path,
        )
    assert state.catalog_revision is not None
    assert state.catalog_diagnostic == ""
    assert len(state.subjects) == 1
    assert state.subjects[0].subject_key == subject.subject_key
    assert state.subjects[0].catalog_revision == state.catalog_revision


def test_explicit_catalog_alias_map_disables_hardcoded_cross_subject_aliases():
    violations = subject_reference_violations(
        "今天复习高数",
        allowed_subjects=(),
        alias_map={"物理": ("物理", "大物")},
    )
    assert violations == ()
