from __future__ import annotations

import json
import multiprocessing
import sqlite3

import pytest

from study_app.core.historical_corrections import apply_historical_correction, request_historical_correction
from study_app.core.subject_changeset import approve_changeset, current_input_version_vector, prepare_changeset
from study_app.core.subject_executor import execute_archive_changeset
from study_app.core.subject_validator import VALIDATOR_VERSION
from study_app.data import database, model_progress_sync
from study_app.data.subject_repository import SubjectCatalogRepository, install_subject_lifecycle_schema


def setup_subject(tmp_path, *, with_record=False):
    db = tmp_path / "archive.sqlite"
    database.initialize_database(db)
    install_subject_lifecycle_schema(db)
    repo = SubjectCatalogRepository(db)
    subject = repo.create_subject("旧学科", capabilities={"study_plan": True, "generic_practice": True})
    with sqlite3.connect(db) as connection:
        legacy_subject = connection.execute("INSERT INTO subjects(name,status) VALUES ('旧学科','active')").lastrowid
        module = connection.execute("INSERT INTO modules(subject_id,name,status) VALUES (?, '模块','learning')", (legacy_subject,)).lastrowid
        connection.execute("INSERT INTO topics(module_id,name,status,mastery,source_json) VALUES (?, '主题','learning',0.2,'{}')", (module,))
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"subjects":[{"name":"旧学科","mastery":0.2,"modules":[{"name":"模块","status":"learning","topics":[{"name":"主题","status":"learning","mastery":0.2}]}]}]}, ensure_ascii=False), encoding="utf-8")
    record_id = None
    if with_record:
        record_id = database.add_learning_record(
            {
                "date":"2026-09-01","subject":"旧学科","module":"模块","topic":"主题",
                "activity":"review","source":"self_study","score":80,"note":"before archive",
                "problems":[{"title":"历史题目","status":"correct","difficulty_score":50}],
                "attachments":[{"file_path":str(tmp_path / "historical.pdf"),"file_name":"historical.pdf"}],
            },
            db_path=db,
            model_path=model,
        )
    return db, repo, subject, model, record_id


def prepare_archive(db, subject_key, operation="archive-op"):
    vector = current_input_version_vector(db, target_subject_keys=(subject_key,))
    prepared = prepare_changeset(
        db, operation_id=operation,
        payload={"schema_version":"subject-changeset-v1","actions":[{"type":"archive_subject","subject_key":subject_key,"reason":"课程结束"}],"expected_diff":{"archived":1}},
        input_version_vector=vector, manifest_version=None,
        validator_version=VALIDATOR_VERSION, target_subject_keys=(subject_key,),
    )
    approve_changeset(db, operation, approver="reviewer")
    return prepared


def test_archive_stops_ordinary_writes_plans_capabilities_and_mastery(tmp_path):
    db, repo, subject, model, record_id = setup_subject(tmp_path, with_record=True)
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO study_plans(subject_scope,start_date,end_date,input_signature,status,plan_json) VALUES ('旧学科','2026-09-01','2026-09-02','sig','active','{}')")
    prepare_archive(db, subject.subject_key)
    before_model = model.read_bytes()
    result = execute_archive_changeset(db, "archive-op", actor="executor", attempt_id="attempt-1")
    assert result["status"] == "completed"
    assert repo.get_subject(subject.subject_key).lifecycle_status == "archived"
    assert repo.capability(subject.subject_key, "study_plan").available is False
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT status FROM study_plans").fetchone()[0] == "archived"
        original = connection.execute("SELECT raw_json FROM learning_records WHERE id=?", (record_id,)).fetchone()[0]
        assert "before archive" in original
        assert connection.execute("SELECT COUNT(*) FROM problem_attempts WHERE record_id=?", (record_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM record_attachments WHERE record_id=?", (record_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM subject_lifecycle_events").fetchone()[0] == 1
    with pytest.raises(ValueError, match="禁止普通新增"):
        database.add_learning_record({"date":"2026-08-01","subject":"旧学科","activity":"review","source":"self_study","note":"backdated"}, db_path=db, model_path=model)
    changed = model_progress_sync.sync_learning_record_to_model({"id":99,"date":"2026-09-02","subject":"旧学科","module":"模块","topic":"主题","activity":"review","source":"self_study","score":100}, model_path=model, db_path=db)
    assert changed == []
    assert model.read_bytes() == before_model


def test_repeated_archive_is_idempotent_but_each_attempt_is_audited(tmp_path):
    db, repo, subject, _model, _ = setup_subject(tmp_path)
    prepare_archive(db, subject.subject_key)
    first = execute_archive_changeset(db, "archive-op", actor="executor", attempt_id="attempt-1")
    second = execute_archive_changeset(db, "archive-op", actor="executor", attempt_id="attempt-2")
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subject_lifecycle_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM subject_operation_attempts").fetchone()[0] == 2


def test_historical_correction_appends_revision_without_reactivating(tmp_path):
    db, repo, subject, _model, record_id = setup_subject(tmp_path, with_record=True)
    prepare_archive(db, subject.subject_key)
    execute_archive_changeset(db, "archive-op", actor="executor")
    with sqlite3.connect(db) as connection:
        original = connection.execute("SELECT raw_json FROM learning_records WHERE id=?", (record_id,)).fetchone()[0]
    correction = request_historical_correction(
        db, subject_key=subject.subject_key, record_id=record_id,
        event_date="2026-09-01", discovery_date="2026-09-18",
        reason="成绩录入错误", evidence={"source":"paper"}, actor="owner",
    )
    revision = apply_historical_correction(db, correction, replacement_record={"date":"2026-09-01","subject":"旧学科","score":90}, approver="reviewer")
    assert revision == 1
    assert apply_historical_correction(db, correction, replacement_record={}, approver="reviewer") == 1
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT raw_json FROM learning_records WHERE id=?", (record_id,)).fetchone()[0] == original
        assert connection.execute("SELECT COUNT(*) FROM learning_record_revisions WHERE record_id=?", (record_id,)).fetchone()[0] == 1
    assert repo.get_subject(subject.subject_key).lifecycle_status == "archived"


def _archive_worker(db, queue):
    try:
        execute_archive_changeset(db, "archive-race", actor="archive", attempt_id="archive-attempt")
    except Exception as error:
        queue.put(("archive", "error", type(error).__name__))
    else:
        queue.put(("archive", "ok", ""))


def _record_worker(db, model, queue):
    try:
        database.add_learning_record({"date":"2026-09-18","subject":"旧学科","activity":"review","source":"self_study","note":"race"}, db_path=db, model_path=model)
    except Exception as error:
        queue.put(("record", "error", type(error).__name__))
    else:
        queue.put(("record", "ok", ""))


def test_archive_and_record_write_race_has_exactly_one_success(tmp_path):
    db, _repo, subject, model, _ = setup_subject(tmp_path)
    prepare_archive(db, subject.subject_key, operation="archive-race")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_archive_worker, args=(str(db), queue)),
        context.Process(target=_record_worker, args=(str(db), str(model), queue)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    results = [queue.get(timeout=2) for _ in processes]
    assert sum(item[1] == "ok" for item in results) == 1
    with sqlite3.connect(db) as connection:
        status = connection.execute("SELECT lifecycle_status FROM subject_catalog").fetchone()[0]
        record_count = connection.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0]
    assert (status, record_count) in {("archived", 0), ("active", 1)}


def test_legacy_hardcoded_archive_path_is_disabled_when_f5_is_installed(tmp_path, monkeypatch):
    db, _repo, _subject, model, _ = setup_subject(tmp_path)
    from study_app.data import archive_subject

    monkeypatch.setattr(archive_subject, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(archive_subject, "MODEL_PATH", model)
    with pytest.raises(RuntimeError, match="SubjectChangeSet"):
        archive_subject.archive_subject("旧学科", "课程结束", "不得使用硬编码别名")
    assert not (tmp_path / "app_data" / "backups").exists()
