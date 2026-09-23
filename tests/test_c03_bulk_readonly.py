from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from study_app.data import database


def _fixture(db_path: Path, record_count: int) -> None:
    database.initialize_database(db_path)
    with database.connect(db_path) as connection:
        for index in range(record_count):
            record = {
                "date": "2026-09-15",
                "subject": "隔离学科",
                "topic": f"主题{index}",
                "problems": [{"title": f"题目{index}", "status": "wrong"}],
            }
            cursor = connection.execute(
                """
                INSERT INTO learning_records(
                    record_date, subject_name, topic_name, activity, source,
                    note, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["date"], record["subject"], record["topic"],
                    "exercise", "outside_class", "", json.dumps(record, ensure_ascii=False),
                ),
            )
            record_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO problem_attempts(
                    record_id, title, status, correctness, related_topics_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id, f"题目{index}", "wrong", None, "[]",
                    json.dumps(record["problems"][0], ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                INSERT INTO record_attachments(
                    record_id, file_path, file_name, file_ext, file_size
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, f"C:/tmp/{index}.pdf", f"{index}.pdf", ".pdf", index),
            )


def _trace_read(connection: sqlite3.Connection, callback) -> list[str]:
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    callback(connection)
    connection.set_trace_callback(None)
    return statements


@pytest.mark.parametrize("record_count", [2, 40])
def test_raw_records_prefetch_children_in_fixed_query_count(tmp_path, record_count):
    db_path = tmp_path / "bulk.sqlite"
    _fixture(db_path, record_count)
    with database.connect_readonly(db_path) as connection:
        statements = _trace_read(
            connection,
            lambda item: database.load_raw_records_from_connection(item),
        )
    records = database.load_raw_records(db_path)

    assert len(records) == record_count
    assert all(len(item["attachments"]) == 1 for item in records)
    assert all(item["problems"][0]["title"].startswith("题目") for item in records)
    assert sum("FROM record_attachments" in item for item in statements) == 1
    assert sum("FROM problem_attempts" in item for item in statements) == 1
    assert len(statements) == 3


@pytest.mark.parametrize("record_count", [2, 40])
def test_recent_records_prefetch_attachment_and_counts(tmp_path, record_count, monkeypatch):
    db_path = tmp_path / "recent.sqlite"
    _fixture(db_path, record_count)
    captured: list[str] = []
    original = database.connect_readonly

    def traced(path):
        connection = original(path)
        connection.set_trace_callback(captured.append)
        return connection

    monkeypatch.setattr(database, "connect_readonly", traced)
    rows = database.list_recent_records(db_path, limit=record_count)

    assert len(rows) == record_count
    assert all(item["problem_count"] == 1 for item in rows)
    assert all(len(item["attachments"]) == 1 for item in rows)
    assert sum("FROM record_attachments" in item for item in captured) == 1
    assert sum("FROM problem_attempts" in item for item in captured) == 1
    assert len(captured) == 3


def test_readonly_repository_never_initializes_or_writes(tmp_path, monkeypatch):
    db_path = tmp_path / "readonly.sqlite"
    _fixture(db_path, 2)
    before = db_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only Repository called initialization")

    monkeypatch.setattr(database, "ensure_seeded_database", forbidden)
    monkeypatch.setattr(database, "initialize_database", forbidden)
    captured: list[str] = []
    original = database.connect_readonly

    def traced(path):
        connection = original(path)
        connection.set_trace_callback(captured.append)
        return connection

    monkeypatch.setattr(database, "connect_readonly", traced)
    assert database.load_raw_records(db_path)
    assert database.list_recent_records(db_path, limit=2)
    assert database.get_setting("missing", db_path=db_path) is None
    assert database.get_counts(db_path)["learning_records"] == 2
    assert database.list_subject_names(db_path) == []
    assert database.list_module_names("隔离学科", db_path) == []
    assert database.get_active_study_plan(None, db_path) is None
    assert database.recent_study_plan_texts(None, db_path=db_path) == []
    assert database.list_llm_call_audits(db_path=db_path) == []

    assert before == db_path.read_bytes()
    assert not any(
        statement.lstrip().upper().startswith(
            ("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE", "REPLACE")
        )
        for statement in captured
    )
    with database.connect_readonly(db_path) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO app_settings(key, value_json) VALUES ('x', '{}')")


def test_empty_database_requires_explicit_initialization(tmp_path):
    missing = tmp_path / "missing" / "learning.sqlite"
    with pytest.raises(database.DatabaseNotInitializedError):
        database.load_raw_records(missing)
    assert not missing.exists()

    empty = tmp_path / "empty.sqlite"
    empty.write_bytes(b"")
    with pytest.raises(database.DatabaseNotInitializedError):
        database.load_raw_records(empty)
    assert empty.stat().st_size == 0

    database.initialize_database(empty)
    assert database.load_raw_records(empty) == []
    assert database.get_counts(empty)["subjects"] == 0


def test_practice_readers_require_explicit_seed_and_do_not_write(tmp_path, monkeypatch):
    from study_app.data import practice_repository

    db_path = tmp_path / "practice.sqlite"
    database.initialize_database(db_path)
    assert practice_repository.list_practice_templates(db_path) == []

    practice_repository.ensure_practice_bank_seeded(db_path)
    before = db_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("practice read initialized or seeded the database")

    monkeypatch.setattr(practice_repository, "ensure_practice_bank_seeded", forbidden)
    monkeypatch.setattr(practice_repository, "seed_practice_bank", forbidden)
    assert practice_repository.list_practice_templates(db_path)
    assert isinstance(practice_repository.find_practice_problems(db_path=db_path), list)
    assert isinstance(practice_repository.practice_context(db_path=db_path), dict)
    assert before == db_path.read_bytes()


def test_collection_and_snapshot_readers_do_not_initialize(tmp_path, monkeypatch):
    from study_app.core.sqlite_dashboard import database_snapshot
    from study_app.data import collection_backlog, ds_collection_gaps, weekly_practice_collector

    db_path = tmp_path / "collection.sqlite"
    database.initialize_database(db_path)
    before = db_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read path initialized database")

    monkeypatch.setattr(collection_backlog, "initialize_database", forbidden)
    monkeypatch.setattr(ds_collection_gaps, "initialize_database", forbidden)
    monkeypatch.setattr(weekly_practice_collector, "initialize_database", forbidden)

    assert database_snapshot(db_path)["counts"]["learning_records"] == 0
    assert collection_backlog.list_collection_backlog(db_path=db_path) == []
    assert isinstance(ds_collection_gaps.ds_collection_coverage(db_path=db_path), list)
    assert weekly_practice_collector.collection_is_due(db_path=db_path)
    assert isinstance(weekly_practice_collector.collection_status(db_path=db_path), dict)
    assert before == db_path.read_bytes()


def test_write_repository_requires_explicit_setup_before_first_write(tmp_path):
    from study_app.data.practice_repository import import_practice_problem

    db_path = tmp_path / "new" / "write.sqlite"
    record = {"date": "2026-09-15", "subject": "隔离学科", "activity": "review"}
    with pytest.raises(database.DatabaseNotInitializedError):
        database.set_setting("key", "value", db_path)
    with pytest.raises(database.DatabaseNotInitializedError):
        database.add_learning_record(record, db_path)
    with pytest.raises(database.DatabaseNotInitializedError):
        database.create_study_plan(None, "2026-09-15", "2026-09-15", "sig", {}, [], db_path)
    with pytest.raises(database.DatabaseNotInitializedError):
        import_practice_problem(
            {"title": "测试题", "statement": "这是一条足够长的测试题目。"},
            db_path=db_path,
        )
    assert not db_path.exists()
    assert not db_path.parent.exists()

    database.initialize_database(db_path)
    database.set_setting("key", "value", db_path)
    assert database.get_setting("key", db_path=db_path) == "value"
