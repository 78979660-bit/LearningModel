from __future__ import annotations

import hashlib
import multiprocessing
import sqlite3

import pytest

from study_app.core.subject_changeset import (
    ChangeSetConflictError,
    approve_changeset,
    canonical_hash,
    canonical_json,
    current_input_version_vector,
    load_changeset,
    preflight_changeset,
    prepare_changeset,
)
from study_app.core.subject_validator import VALIDATOR_VERSION
from study_app.data.database import initialize_database
from study_app.data.subject_repository import install_subject_lifecycle_schema


def setup_db(tmp_path):
    db = tmp_path / "changeset.sqlite"
    initialize_database(db)
    install_subject_lifecycle_schema(db)
    return db


def payload(name="新学科"):
    return {
        "schema_version": "subject-changeset-v1",
        "actions": [{"type": "activate_subject", "candidate_name": name}],
        "expected_diff": {"subjects_added": 1},
    }


def prepare(db, operation="op-1", value=None):
    vector = current_input_version_vector(db)
    return prepare_changeset(
        db,
        operation_id=operation,
        payload=value or payload(),
        input_version_vector=vector,
        manifest_version=None,
        validator_version=VALIDATOR_VERSION,
    ), vector


def _prepare_worker(db_path, operation, value, vector, queue):
    try:
        item = prepare_changeset(
            db_path,
            operation_id=operation,
            payload=value,
            input_version_vector=vector,
            manifest_version=None,
            validator_version=VALIDATOR_VERSION,
        )
    except Exception as error:
        queue.put(("error", type(error).__name__))
    else:
        queue.put(("ok", item.changeset_hash))


def test_canonical_hash_is_order_independent_and_rejects_floats():
    assert canonical_hash({"b": 2, "a": "e\u0301"}) == canonical_hash({"a": "é", "b": 2})
    assert canonical_json({"z": -1, "a": True}) == '{"a":true,"z":-1}'
    with pytest.raises(ValueError, match="浮点数"):
        canonical_hash({"weight": 0.5})


def test_same_operation_same_payload_is_idempotent(tmp_path):
    db = setup_db(tmp_path)
    first, vector = prepare(db)
    second = prepare_changeset(
        db, operation_id="op-1", payload={"expected_diff": {"subjects_added": 1}, "actions": [{"candidate_name": "新学科", "type": "activate_subject"}], "schema_version": "subject-changeset-v1"},
        input_version_vector=vector, manifest_version=None, validator_version=VALIDATOR_VERSION,
    )
    assert second.changeset_hash == first.changeset_hash
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subject_changesets").fetchone()[0] == 1


def test_same_operation_different_payload_is_rejected_and_audited(tmp_path):
    db = setup_db(tmp_path)
    prepare(db)
    vector = current_input_version_vector(db)
    with pytest.raises(ChangeSetConflictError, match="不同 changeset_hash"):
        prepare_changeset(
            db, operation_id="op-1", payload=payload("另一个学科"),
            input_version_vector=vector, manifest_version=None,
            validator_version=VALIDATOR_VERSION,
        )
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subject_changesets").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM subject_operation_preflight_events WHERE event_type='operation_hash_conflict'").fetchone()[0] == 1


def test_approval_binds_hash_and_versions_then_drift_expires_preflight(tmp_path):
    db = setup_db(tmp_path)
    prepared, vector = prepare(db)
    approval = approve_changeset(db, prepared.operation_id, approver="reviewer", approval_id="approval-1")
    assert len(approval) == 64
    assert preflight_changeset(db, prepared.operation_id, current_vector=vector).valid is True
    drifted = {**vector, "catalog_revision": vector["catalog_revision"] + 1}
    report = preflight_changeset(db, prepared.operation_id, current_vector=drifted)
    assert report.valid is False
    assert report.stale is True
    assert report.issues == ("input_version_vector_drift",)


def test_signed_payload_is_immutable_while_operation_status_can_change(tmp_path):
    db = setup_db(tmp_path)
    prepared, _ = prepare(db)
    before = canonical_hash(load_changeset(db, prepared.operation_id).payload)
    approve_changeset(db, prepared.operation_id, approver="reviewer")
    after_item = load_changeset(db, prepared.operation_id)
    assert after_item.status == "approved"
    assert canonical_hash(after_item.payload) == before
    with sqlite3.connect(db) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE subject_changesets SET payload_json='{}' WHERE operation_id='op-1'")


def test_preflight_is_readonly_for_business_and_model_files(tmp_path):
    db = setup_db(tmp_path)
    model = tmp_path / "model.json"
    model.write_text('{"subjects":[]}', encoding="utf-8")
    prepared, vector = prepare(db)
    approve_changeset(db, prepared.operation_id, approver="reviewer")
    before_model = hashlib.sha256(model.read_bytes()).hexdigest()
    with sqlite3.connect(db) as connection:
        before_catalog = connection.execute("SELECT COUNT(*) FROM subject_catalog").fetchone()[0]
    report = preflight_changeset(db, prepared.operation_id, current_vector=vector)
    with sqlite3.connect(db) as connection:
        after_catalog = connection.execute("SELECT COUNT(*) FROM subject_catalog").fetchone()[0]
    assert report.valid is True
    assert report.impact == {"action_count": 1, "archive_count": 0, "activate_count": 1}
    assert before_catalog == after_catalog == 0
    assert hashlib.sha256(model.read_bytes()).hexdigest() == before_model


def test_two_processes_same_operation_different_payload_only_one_wins(tmp_path):
    db = setup_db(tmp_path)
    vector = current_input_version_vector(db)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_prepare_worker,
            args=(str(db), "op-race", payload(name), vector, queue),
        )
        for name in ("学科甲", "学科乙")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    results = [queue.get(timeout=2) for _ in processes]
    assert sorted(item[0] for item in results) == ["error", "ok"]
    assert next(item[1] for item in results if item[0] == "error") == "ChangeSetConflictError"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subject_changesets WHERE operation_id='op-race'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM subject_operation_preflight_events WHERE operation_id='op-race' AND event_type='operation_hash_conflict'").fetchone()[0] == 1
