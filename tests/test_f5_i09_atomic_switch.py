from __future__ import annotations

import json
import multiprocessing
import sqlite3
import uuid

import pytest

from study_app.core.subject_changeset import (
    approve_changeset,
    current_input_version_vector,
    prepare_changeset,
    store_manifest_version,
)
from study_app.core.subject_executor import execute_switch_changeset
from study_app.core.subject_projection import load_projection, process_projection_outbox
from study_app.core.subject_validator import VALIDATOR_VERSION
from study_app.data import database
from study_app.data.subject_repository import SubjectCatalogRepository, install_subject_lifecycle_schema


def subject_key() -> str:
    return f"subject:v1:{uuid.uuid4().hex}"


def module_key() -> str:
    return f"module:v1:{uuid.uuid4().hex}"


def setup_switch(tmp_path):
    db = tmp_path / "switch.sqlite"
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
        "old-plan",
        {"signature": "old-plan"},
        [
            {
                "section_key": "core",
                "section_title": "核心",
                "day_index": 1,
                "item_type": "task",
                "item_text": "旧计划",
                "item_hash": "old-core",
            }
        ],
        db,
    )
    manifest_version = "manifest:v1:new-subject"
    store_manifest_version(
        db,
        manifest_version=manifest_version,
        payload={"schema_version": "subject-manifest-v1", "subject": {"name": "新学科"}},
        status="adopted",
        generator_version="fixture-v1",
        input_vector={"fixture": 1},
    )
    return db, repo, old, manifest_version


def prepare_switch(
    db,
    old_key,
    manifest_version,
    *,
    operation_id="switch-op",
    new_key=None,
    new_name="新学科",
    archive_old=True,
):
    new_key = new_key or subject_key()
    targets = (old_key, new_key) if archive_old else (new_key,)
    vector = current_input_version_vector(
        db, target_subject_keys=targets, manifest_version=manifest_version
    )
    actions = [
        {
            "type": "activate_subject",
            "subject_key": new_key,
            "canonical_name": new_name,
            "display_name": new_name,
            "aliases": [f"{new_name}别名"],
            "capabilities": {"study_plan": True, "generic_practice": True},
            "modules": [
                {
                    "module_key": module_key(),
                    "canonical_name": "新模块",
                    "display_name": "新模块",
                }
            ],
            "manifest_version": manifest_version,
            "reason": "采纳教材结构",
            "core_plan": {
                "start_date": "2026-09-19",
                "end_date": "2026-10-19",
                "item_text": f"开始学习{new_name}",
            },
        }
    ]
    if archive_old:
        actions.append(
            {
                "type": "archive_subject",
                "subject_key": old_key,
                "reason": "切换到新学科",
            }
        )
    prepare_changeset(
        db,
        operation_id=operation_id,
        payload={
            "schema_version": "subject-changeset-v1",
            "actions": actions,
            "expected_diff": {"activated": 1, "archived": int(archive_old)},
        },
        input_version_vector=vector,
        manifest_version=manifest_version,
        validator_version=VALIDATOR_VERSION,
        target_subject_keys=targets,
    )
    approve_changeset(db, operation_id, approver="reviewer")
    return new_key


def prepare_compensation(db, original_operation_id, old_key, new_key):
    operation_id = f"compensate-{original_operation_id}"
    vector = current_input_version_vector(
        db, target_subject_keys=(old_key, new_key)
    )
    prepare_changeset(
        db,
        operation_id=operation_id,
        payload={
            "schema_version": "subject-changeset-v1",
            "actions": [
                {
                    "type": "compensate_switch",
                    "original_operation_id": original_operation_id,
                    "reason": "验收补偿",
                }
            ],
            "expected_diff": {"reactivated": 1, "archived": 1},
        },
        input_version_vector=vector,
        manifest_version=None,
        validator_version=VALIDATOR_VERSION,
        target_subject_keys=(old_key, new_key),
    )
    approve_changeset(db, operation_id, approver="reviewer")
    return operation_id


def test_atomic_switch_creates_core_plan_and_revision_aware_projection(tmp_path):
    db, repo, old, manifest = setup_switch(tmp_path)
    new_key = prepare_switch(db, old.subject_key, manifest)
    result = execute_switch_changeset(
        db, "switch-op", actor="executor", attempt_id="attempt-switch"
    )
    assert result["status"] == "db_committed_projection_pending"
    assert repo.get_subject(old.subject_key).lifecycle_status == "archived"
    assert repo.get_subject(new_key).lifecycle_status == "active"
    plan = database.get_active_study_plan("新学科", db)
    assert plan is not None
    assert plan["items"][0]["item_text"] == "开始学习新学科"
    assert repo.capability(new_key, "study_plan").available is True
    with sqlite3.connect(db) as connection:
        module = connection.execute(
            """
            SELECT canonical_name,display_name
            FROM subject_module_identities
            WHERE subject_key=?
            """,
            (new_key,),
        ).fetchone()
    assert module == ("新模块", "新模块")
    assert len(result["new_module_keys"]) == 1
    unavailable = repo.capability(new_key, "oj")
    assert unavailable.available is False
    assert unavailable.declared_supported is False
    projection = tmp_path / "projection.json"
    published = process_projection_outbox(db, projection)
    assert published["status"] == "published"
    assert load_projection(projection)["catalog_revision"] == repo.catalog_revision()
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT status FROM subject_change_operations WHERE operation_id='switch-op'"
        ).fetchone()[0] == "completed"


def test_failure_before_commit_keeps_complete_old_state(tmp_path):
    db, repo, old, manifest = setup_switch(tmp_path)
    new_key = prepare_switch(db, old.subject_key, manifest)
    with pytest.raises(RuntimeError, match="injected failure"):
        execute_switch_changeset(
            db, "switch-op", actor="executor", fail_before_commit=True
        )
    assert repo.get_subject(old.subject_key).lifecycle_status == "active"
    with pytest.raises(LookupError):
        repo.get_subject(new_key)
    assert database.get_active_study_plan("旧学科", db) is not None
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subject_projection_outbox").fetchone()[0] == 0


def test_response_loss_retries_without_duplicate_business_effect(tmp_path):
    db, _repo, old, manifest = setup_switch(tmp_path)
    new_key = prepare_switch(db, old.subject_key, manifest)
    with pytest.raises(ConnectionError, match="response loss"):
        execute_switch_changeset(
            db,
            "switch-op",
            actor="executor",
            attempt_id="lost-response",
            simulate_response_loss=True,
        )
    retry = execute_switch_changeset(
        db, "switch-op", actor="executor", attempt_id="retry-response"
    )
    assert retry["idempotent"] is True
    assert retry["new_subject_key"] == new_key
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_catalog WHERE subject_key=?", (new_key,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM study_plans WHERE input_signature='f5:switch-op'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_operation_attempts WHERE operation_id='switch-op'"
        ).fetchone()[0] == 2


def test_projection_failure_rebuilds_projection_without_replaying_business(tmp_path):
    db, _repo, old, manifest = setup_switch(tmp_path)
    new_key = prepare_switch(db, old.subject_key, manifest)
    execute_switch_changeset(db, "switch-op", actor="executor")
    projection = tmp_path / "projection.json"
    with pytest.raises(RuntimeError, match="projection failure"):
        process_projection_outbox(db, projection, fail_before_publish=True)
    with sqlite3.connect(db) as connection:
        before = (
            connection.execute("SELECT COUNT(*) FROM subject_catalog").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM subject_lifecycle_events").fetchone()[0],
        )
        assert connection.execute(
            "SELECT status FROM subject_change_operations WHERE operation_id='switch-op'"
        ).fetchone()[0] == "db_committed_projection_pending"
    recovered = process_projection_outbox(db, projection)
    assert recovered["status"] == "published"
    with sqlite3.connect(db) as connection:
        after = (
            connection.execute("SELECT COUNT(*) FROM subject_catalog").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM subject_lifecycle_events").fetchone()[0],
        )
    assert before == after
    assert any(item["subject_key"] == new_key for item in load_projection(projection)["subjects"])


def test_projection_out_of_order_cannot_overwrite_newer_revision(tmp_path):
    db, repo, old, manifest = setup_switch(tmp_path)
    prepare_switch(db, old.subject_key, manifest, operation_id="switch-one")
    execute_switch_changeset(db, "switch-one", actor="executor")
    second_key = prepare_switch(
        db,
        old.subject_key,
        manifest,
        operation_id="switch-two",
        new_name="第二新学科",
        archive_old=False,
    )
    execute_switch_changeset(db, "switch-two", actor="executor")
    projection = tmp_path / "projection.json"
    newest = process_projection_outbox(
        db, projection, task_id="projection:switch-two"
    )
    old_task = process_projection_outbox(
        db, projection, task_id="projection:switch-one"
    )
    assert newest["status"] == "published"
    assert old_task["status"] == "superseded"
    payload = load_projection(projection)
    assert payload["catalog_revision"] == repo.catalog_revision()
    assert any(item["subject_key"] == second_key for item in payload["subjects"])


def test_compensation_preserves_other_subject_later_record(tmp_path):
    db, repo, old, manifest = setup_switch(tmp_path)
    other = repo.create_subject("其他学科", capabilities={"generic_practice": True})
    new_key = prepare_switch(db, old.subject_key, manifest)
    execute_switch_changeset(db, "switch-op", actor="executor")
    record_id = database.add_learning_record(
        {
            "date": "2026-09-18",
            "subject": "其他学科",
            "activity": "review",
            "source": "self_study",
            "note": "must survive compensation",
        },
        db_path=db,
        model_path=tmp_path / "missing-model.json",
    )
    compensation = prepare_compensation(
        db, "switch-op", old.subject_key, new_key
    )
    execute_switch_changeset(db, compensation, actor="executor")
    assert repo.get_subject(old.subject_key).lifecycle_status == "active"
    assert repo.get_subject(new_key).lifecycle_status == "archived"
    assert repo.get_subject(other.subject_key).lifecycle_status == "active"
    with sqlite3.connect(db) as connection:
        raw = connection.execute(
            "SELECT raw_json FROM learning_records WHERE id=?", (record_id,)
        ).fetchone()[0]
        assert "must survive compensation" in raw
        assert connection.execute(
            "SELECT status FROM subject_change_operations WHERE operation_id='switch-op'"
        ).fetchone()[0] == "compensated"


def _switch_worker(db, operation_id, queue):
    try:
        execute_switch_changeset(
            db, operation_id, actor="worker", attempt_id=f"attempt-{operation_id}"
        )
    except Exception as error:
        queue.put((operation_id, "error", type(error).__name__))
    else:
        queue.put((operation_id, "ok", ""))


def test_two_processes_switching_same_old_subject_have_one_commit(tmp_path):
    db, repo, old, manifest = setup_switch(tmp_path)
    prepare_switch(
        db, old.subject_key, manifest, operation_id="race-one", new_name="竞争一"
    )
    prepare_switch(
        db, old.subject_key, manifest, operation_id="race-two", new_name="竞争二"
    )
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_switch_worker, args=(str(db), operation, queue))
        for operation in ("race-one", "race-two")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    results = [queue.get(timeout=2) for _ in processes]
    assert sum(item[1] == "ok" for item in results) == 1
    assert repo.get_subject(old.subject_key).lifecycle_status == "archived"
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_catalog WHERE lifecycle_status='active'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_projection_outbox"
        ).fetchone()[0] == 1
