from __future__ import annotations

import sqlite3
import uuid

import pytest

from study_app.core.async_tasks import (
    StaleSubjectTaskError,
    capture_subject_revision,
    revalidate_subject_revision,
)
from study_app.core.subject_changeset import (
    approve_changeset,
    current_input_version_vector,
    prepare_changeset,
    store_manifest_version,
)
from study_app.core.subject_executor import execute_archive_changeset, execute_switch_changeset
from study_app.core.subject_projection import process_projection_outbox
from study_app.core.subject_validator import VALIDATOR_VERSION
from study_app.data import database
from study_app.data.subject_repository import SubjectCatalogRepository, install_subject_lifecycle_schema
from study_app.ui.subject_lifecycle_page import load_subject_lifecycle_view


def setup_catalog(tmp_path):
    db = tmp_path / "i10.sqlite"
    database.initialize_database(db)
    install_subject_lifecycle_schema(db)
    repo = SubjectCatalogRepository(db)
    old = repo.create_subject(
        "旧学科", capabilities={"study_plan": True, "generic_practice": True}
    )
    database.create_study_plan(
        "旧学科",
        "2026-09-01",
        "2026-09-30",
        "existing",
        {"signature": "existing"},
        [
            {
                "section_key": "core",
                "section_title": "核心",
                "day_index": 1,
                "item_type": "task",
                "item_text": "旧计划",
                "item_hash": "existing-core",
            }
        ],
        db,
    )
    manifest = "manifest:v1:i10"
    store_manifest_version(
        db,
        manifest_version=manifest,
        payload={"schema_version": "subject-manifest-v1", "subject": {"name": "新学科"}},
        status="adopted",
        generator_version="fixture-v1",
        input_vector={"fixture": 1},
    )
    return db, repo, old, manifest


def prepare_archive(db, subject_key, operation_id="archive-i10"):
    vector = current_input_version_vector(db, target_subject_keys=(subject_key,))
    prepare_changeset(
        db,
        operation_id=operation_id,
        payload={
            "schema_version": "subject-changeset-v1",
            "actions": [
                {
                    "type": "archive_subject",
                    "subject_key": subject_key,
                    "reason": "I10 迟到任务演练",
                }
            ],
            "expected_diff": {"archived": 1},
        },
        input_version_vector=vector,
        manifest_version=None,
        validator_version=VALIDATOR_VERSION,
        target_subject_keys=(subject_key,),
    )
    approve_changeset(db, operation_id, approver="reviewer")


def prepare_switch(db, old_key, manifest, operation_id="switch-i10"):
    new_key = f"subject:v1:{uuid.uuid4().hex}"
    vector = current_input_version_vector(
        db,
        target_subject_keys=(old_key, new_key),
        manifest_version=manifest,
    )
    prepare_changeset(
        db,
        operation_id=operation_id,
        payload={
            "schema_version": "subject-changeset-v1",
            "actions": [
                {
                    "type": "activate_subject",
                    "subject_key": new_key,
                    "canonical_name": "新学科",
                    "display_name": "新学科",
                    "aliases": [],
                    "capabilities": {"study_plan": True},
                    "manifest_version": manifest,
                    "core_plan": {
                        "start_date": "2026-09-19",
                        "end_date": "2026-10-19",
                        "item_text": "新学科核心计划",
                    },
                },
                {
                    "type": "archive_subject",
                    "subject_key": old_key,
                    "reason": "I10 完整隔离切换",
                },
            ],
            "expected_diff": {
                "active_subjects_added": 1,
                "active_subjects_archived": 1,
                "projection_outbox_added": 1,
            },
        },
        input_version_vector=vector,
        manifest_version=manifest,
        validator_version=VALIDATOR_VERSION,
        target_subject_keys=(old_key, new_key),
    )
    approve_changeset(db, operation_id, approver="reviewer")
    return new_key


def test_lifecycle_view_exposes_status_diff_permissions_and_recovery(tmp_path):
    db, _repo, old, manifest = setup_catalog(tmp_path)
    prepare_switch(db, old.subject_key, manifest)
    approved = load_subject_lifecycle_view(db, actor_role="owner")
    operation = next(item for item in approved["operations"] if item["operation_id"] == "switch-i10")
    assert operation["can_execute"] is True
    assert operation["can_approve"] is False
    assert operation["expected_diff"]["projection_outbox_added"] == 1
    assert approved["manifests"][0]["status"] == "adopted"
    execute_switch_changeset(db, "switch-i10", actor="owner")
    pending = load_subject_lifecycle_view(db, actor_role="owner")
    operation = next(item for item in pending["operations"] if item["operation_id"] == "switch-i10")
    assert operation["can_recover"] is True
    process_projection_outbox(db, tmp_path / "projection.json")
    completed = load_subject_lifecycle_view(db, actor_role="viewer")
    operation = next(item for item in completed["operations"] if item["operation_id"] == "switch-i10")
    assert operation["status"] == "completed"
    assert operation["can_execute"] is False
    assert completed["events"]


def test_repeated_execute_after_page_disposal_has_one_effect_and_two_attempts(tmp_path):
    db, _repo, old, manifest = setup_catalog(tmp_path)
    new_key = prepare_switch(db, old.subject_key, manifest)
    execute_switch_changeset(
        db, "switch-i10", actor="owner", attempt_id="ui-click-one"
    )
    # The page/controller may be gone; audit and idempotency live in SQLite.
    replay = execute_switch_changeset(
        db, "switch-i10", actor="owner", attempt_id="ui-click-two"
    )
    assert replay["idempotent"] is True
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_catalog WHERE subject_key=?", (new_key,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_operation_attempts WHERE operation_id='switch-i10'"
        ).fetchone()[0] == 2


def test_complete_isolation_switch_matches_expected_diff_and_rejects_late_plan(tmp_path):
    db, repo, old, manifest = setup_catalog(tmp_path)
    token = capture_subject_revision(db, "旧学科")
    old_plan_id = database.get_active_study_plan("旧学科", db)["id"]
    new_key = prepare_switch(db, old.subject_key, manifest)
    execute_switch_changeset(db, "switch-i10", actor="owner")
    with pytest.raises(StaleSubjectTaskError):
        revalidate_subject_revision(token)
    with pytest.raises(ValueError, match="禁止普通新增"):
        database.create_study_plan(
            "旧学科",
            "2026-09-19",
            "2026-09-19",
            "late",
            {"signature": "late"},
            [],
            db,
        )
    assert repo.get_subject(old.subject_key).lifecycle_status == "archived"
    assert repo.get_subject(new_key).lifecycle_status == "active"
    assert database.get_active_study_plan("新学科", db) is not None
    with sqlite3.connect(db) as connection:
        actual = {
            "active_subjects_added": connection.execute(
                "SELECT COUNT(*) FROM subject_catalog WHERE subject_key=? AND lifecycle_status='active'",
                (new_key,),
            ).fetchone()[0],
            "active_subjects_archived": connection.execute(
                "SELECT COUNT(*) FROM subject_catalog WHERE subject_key=? AND lifecycle_status='archived'",
                (old.subject_key,),
            ).fetchone()[0],
            "projection_outbox_added": connection.execute(
                "SELECT COUNT(*) FROM subject_projection_outbox WHERE operation_id='switch-i10'"
            ).fetchone()[0],
        }
        expected = connection.execute(
            "SELECT payload_json FROM subject_changesets WHERE operation_id='switch-i10'"
        ).fetchone()[0]
        assert actual == __import__("json").loads(expected)["expected_diff"]
        assert connection.execute(
            "SELECT status FROM study_plans WHERE id=?", (old_plan_id,)
        ).fetchone()[0] == "archived"
        assert connection.execute(
            "SELECT COUNT(*) FROM study_plans WHERE input_signature='late'"
        ).fetchone()[0] == 0


def test_local_practice_late_result_is_cleaned_before_publish(tmp_path, monkeypatch):
    from study_app.core import local_practice_service as service
    from study_app.core.local_practice_spec import LocalPracticePaperSpec

    db, _repo, old, _manifest = setup_catalog(tmp_path)
    prepare_archive(db, old.subject_key)
    fake_paper = type(
        "FakePaper",
        (),
        {
            "paper_id": "a" * 64,
            "manifest_core": lambda self: {"paper_id": self.paper_id},
        },
    )()
    monkeypatch.setattr(service, "list_local_practice_candidates", lambda **_kwargs: [])
    monkeypatch.setattr(service, "build_candidate_pool", lambda rows: rows)
    monkeypatch.setattr(service, "compose_local_practice_paper", lambda spec, pool: fake_paper)

    def renderer(_paper, question_path, answer_path, **_kwargs):
        question_path.write_bytes(b"question")
        answer_path.write_bytes(b"answer")
        execute_archive_changeset(db, "archive-i10", actor="owner")
        return {"question_pages": 1, "answer_pages": 1}

    spec = LocalPracticePaperSpec.create(
        subject="旧学科",
        template_id="daily-brief-v1",
        topic="",
        target_difficulty=50,
        question_count=1,
        paper_date="2026-09-18",
        title="I10",
    )
    output = tmp_path / "practice-output"
    with pytest.raises(StaleSubjectTaskError):
        service.generate_local_practice_paper(
            spec, db_path=db, output_directory=output, renderer=renderer
        )
    assert list(output.iterdir()) == []


def test_weekly_collector_rejects_results_returning_after_archive(tmp_path, monkeypatch):
    from study_app.data import collection_backlog
    from study_app.data import ds_collection_gaps
    from study_app.data import weekly_practice_collector as collector

    db, _repo, old, _manifest = setup_catalog(tmp_path)
    prepare_archive(db, old.subject_key)
    monkeypatch.setattr(ds_collection_gaps, "audit_and_register_ds_collection_gaps", lambda **_kwargs: [])
    monkeypatch.setattr(collection_backlog, "list_collection_backlog", lambda **_kwargs: [])
    source = collector.TRUSTED_SOURCES[0]
    monkeypatch.setattr(collector, "TRUSTED_SOURCES", (source,))

    def late_discovery(_source):
        execute_archive_changeset(db, "archive-i10", actor="owner")
        return [
            {
                "source_name": "fixture",
                "institution": "fixture",
                "subject_hint": "旧学科",
                "topic_hint": "",
                "title": "late",
                "url": "https://example.invalid/late.pdf",
                "document_type": "assignment",
                "quality_score": 90,
                "estimated_difficulty": 70,
                "raw": {},
            }
        ]

    monkeypatch.setattr(collector, "discover_source", late_discovery)
    with pytest.raises(StaleSubjectTaskError):
        collector.collect_weekly_sources(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM practice_collection_candidates WHERE title='late'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM practice_collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == "failed"
