from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from study_app.core.mastery_trace import list_topic_mastery_trace
from study_app.data import database
from study_app.data.mastery_replay import (
    ReplayProvenanceError, import_learning_record_once,
    list_record_revision_history, replay_mastery,
    revise_learning_record, revoke_learning_record,
)


MODEL = {
    "model_name": "replay_test_model_v1",
    "subjects": [{
        "name": "测试学科", "mastery": 0.3,
        "modules": [{
            "name": "基础", "weight": 1.0, "mastery": 0.3,
            "topics": [
                {"name": "哈希表", "mastery": 0.2, "difficulty": 0.6,
                 "status": "learning"},
                {"name": "二叉树", "mastery": 0.4, "difficulty": 0.6,
                 "status": "learning"},
            ],
        }],
    }],
}


def _fixture(tmp_path):
    db_path = tmp_path / "records.sqlite"
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(MODEL, ensure_ascii=False), encoding="utf-8")
    database.initialize_database(db_path)
    with database.connect(db_path) as connection:
        database.import_model_json(connection, MODEL)
    return model_path, db_path


def _record(topic, correctness, day):
    return {
        "date": day, "subject": "测试学科", "module": "基础",
        "topic": topic, "activity": "exercise", "source": "outside_class",
        "score": 80, "note": f"{topic}练习",
        "problems": [{
            "title": f"{topic}题", "related_topics": [topic],
            "correctness": correctness, "difficulty_score": 60,
        }],
    }


def _trace(model_path, db_path, topic="哈希表"):
    return list_topic_mastery_trace(
        "测试学科", "基础", topic,
        model_path=model_path, db_path=db_path,
    )["contributions"]


def test_revision_replays_later_record_and_preserves_versions(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    second = import_learning_record_once(
        "platform:item:2", _record("哈希表", 100, "2026-09-15"),
        db_path=db_path, model_path=model_path,
    )
    before = _trace(model_path, db_path)
    revise_learning_record(
        first, _record("哈希表", 100, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    after = _trace(model_path, db_path)
    assert [event["record_id"] for event in after] == [first, second]
    assert after[0]["result_correctness"] != before[0]["result_correctness"]
    assert after[1]["old_mastery"] == after[0]["new_mastery"]
    with database.connect_readonly(db_path) as connection:
        rows = connection.execute(
            "SELECT action FROM learning_record_revisions WHERE record_id=? "
            "ORDER BY version", (first,),
        ).fetchall()
    assert [row[0] for row in rows] == ["created", "revised"]
    history = list_record_revision_history(first, db_path=db_path)
    assert history[1]["prior_contributions"][0]["record_id"] == first
    assert history[1]["payload_sha256"]


def test_revoke_only_target_and_two_replays_are_identical(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    second = import_learning_record_once(
        "platform:item:2", _record("哈希表", 100, "2026-09-15"),
        db_path=db_path, model_path=model_path,
    )
    revoke_learning_record(first, db_path=db_path, model_path=model_path)
    assert [event["record_id"] for event in _trace(model_path, db_path)] == [second]
    assert [item["id"] for item in database.load_raw_records(db_path)] == [second]
    with database.connect_readonly(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM learning_records"
        ).fetchone()[0] == 2
    replay_mastery(db_path=db_path, model_path=model_path)
    first_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    replay_mastery(db_path=db_path, model_path=model_path)
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == first_hash


def test_last_contribution_revocation_restores_frozen_mastery_and_aggregates(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    revoke_learning_record(first, db_path=db_path, model_path=model_path)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    subject = model["subjects"][0]
    module = subject["modules"][0]
    topic = module["topics"][0]
    assert topic["mastery"] == 0.2
    assert topic["status"] == "learning"
    assert module["mastery"] == 0.3
    assert subject["mastery"] == 0.3
    assert _trace(model_path, db_path) == []
    with database.connect_readonly(db_path) as connection:
        sql_topic = connection.execute(
            "SELECT mastery, status FROM topics WHERE name='哈希表'"
        ).fetchone()
        sql_module = connection.execute(
            "SELECT mastery, status FROM modules WHERE name='基础'"
        ).fetchone()
        sql_subject = connection.execute(
            "SELECT mastery FROM subjects WHERE name='测试学科'"
        ).fetchone()
    assert sql_topic[0] == topic["mastery"]
    assert sql_topic[1] == topic["status"]
    assert abs(sql_module[0] - module["mastery"]) < 1e-9
    assert sql_module[1] == module.get("status")
    assert abs(sql_subject[0] - subject["mastery"]) < 1e-9


def test_duplicate_source_import_and_mapping_revision(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    assert import_learning_record_once(
        "platform:item:1", _record("哈希表", 100, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    ) == first
    assert len(database.load_raw_records(db_path)) == 1
    revise_learning_record(
        first, _record("二叉树", 100, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    assert _trace(model_path, db_path, "哈希表") == []
    assert [event["record_id"] for event in _trace(
        model_path, db_path, "二叉树"
    )] == [first]


def test_concurrent_duplicate_import_has_one_record_and_contribution(tmp_path):
    model_path, db_path = _fixture(tmp_path)

    def import_same(_):
        return import_learning_record_once(
            "platform:item:shared", _record("哈希表", 0, "2026-09-14"),
            db_path=db_path, model_path=model_path,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(import_same, range(2)))
    assert ids[0] == ids[1]
    assert len(database.load_raw_records(db_path)) == 1
    assert len(_trace(model_path, db_path)) == 1


def test_two_preexisting_records_can_be_remapped_into_same_new_topic(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    second = import_learning_record_once(
        "platform:item:2", _record("哈希表", 100, "2026-09-15"),
        db_path=db_path, model_path=model_path,
    )
    revise_learning_record(
        first, _record("二叉树", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    revise_learning_record(
        second, _record("二叉树", 100, "2026-09-15"),
        db_path=db_path, model_path=model_path,
    )
    events = _trace(model_path, db_path, "二叉树")
    assert [event["record_id"] for event in events] == [first, second]
    assert events[0]["new_mastery"] == events[1]["old_mastery"]


def test_multiple_revisions_keep_monotonic_versions_and_final_evidence(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    record_id = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    revise_learning_record(
        record_id, _record("哈希表", 100, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    revise_learning_record(
        record_id, _record("二叉树", 100, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    history = list_record_revision_history(record_id, db_path=db_path)
    assert [item["version"] for item in history] == [1, 2, 3]
    assert [item["action"] for item in history] == ["created", "revised", "revised"]
    assert history[2]["prior_contributions"][0]["record_id"] == record_id
    assert _trace(model_path, db_path, "哈希表") == []
    assert [item["record_id"] for item in _trace(
        model_path, db_path, "二叉树"
    )] == [record_id]


def test_invalid_revision_keeps_record_and_model_unchanged(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    model_before = model_path.read_bytes()
    with database.connect_readonly(db_path) as connection:
        raw_before = connection.execute(
            "SELECT raw_json FROM learning_records WHERE id=?", (first,)
        ).fetchone()[0]
    invalid = _record("哈希表", 100, "2026-02-30")
    try:
        revise_learning_record(first, invalid, db_path=db_path, model_path=model_path)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid calendar date was accepted")
    assert model_path.read_bytes() == model_before
    with database.connect_readonly(db_path) as connection:
        assert connection.execute(
            "SELECT raw_json FROM learning_records WHERE id=?", (first,)
        ).fetchone()[0] == raw_before


def test_untraced_legacy_record_is_refused(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    model = json.loads(model_path.read_text(encoding="utf-8"))
    topic = model["subjects"][0]["modules"][0]["topics"][0]
    topic["source_json"].pop("mastery_contribution_trace_v1")
    topic["source_json"].pop("mastery_replay_baseline_v1")
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    try:
        revoke_learning_record(first, db_path=db_path, model_path=model_path)
    except ReplayProvenanceError:
        pass
    else:
        raise AssertionError("untraced legacy evidence was replayed")
    assert len(database.load_raw_records(db_path)) == 1


def test_undocumented_raw_record_change_blocks_replay(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    with database.connect(db_path) as connection:
        row = connection.execute(
            "SELECT raw_json FROM learning_records WHERE id=?", (first,)
        ).fetchone()
        raw = json.loads(row[0])
        raw["note"] = "undocumented edit"
        connection.execute(
            "UPDATE learning_records SET raw_json=? WHERE id=?",
            (json.dumps(raw, ensure_ascii=False), first),
        )
    try:
        replay_mastery(db_path=db_path, model_path=model_path)
    except ReplayProvenanceError:
        pass
    else:
        raise AssertionError("unverified raw evidence was replayed")


def test_replay_prepare_failure_rolls_back_revision(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    model_before = model_path.read_bytes()
    with database.connect_readonly(db_path) as connection:
        row_before = connection.execute(
            "SELECT raw_json FROM learning_records WHERE id=?", (first,)
        ).fetchone()[0]
        versions_before = connection.execute(
            "SELECT COUNT(*) FROM learning_record_revisions WHERE record_id=?",
            (first,),
        ).fetchone()[0]
    with patch(
        "study_app.data.mastery_replay._commit_replay",
        side_effect=RuntimeError("simulated replay preparation failure"),
    ):
        try:
            revise_learning_record(
                first, _record("哈希表", 100, "2026-09-14"),
                db_path=db_path, model_path=model_path,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("replay preparation failure was ignored")
    assert model_path.read_bytes() == model_before
    with database.connect_readonly(db_path) as connection:
        assert connection.execute(
            "SELECT raw_json FROM learning_records WHERE id=?", (first,)
        ).fetchone()[0] == row_before
        assert connection.execute(
            "SELECT COUNT(*) FROM learning_record_revisions WHERE record_id=?",
            (first,),
        ).fetchone()[0] == versions_before


def test_model_replace_failure_is_recovered_from_pending_journal(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    first = import_learning_record_once(
        "platform:item:1", _record("哈希表", 0, "2026-09-14"),
        db_path=db_path, model_path=model_path,
    )
    with patch(
        "study_app.data.mastery_replay._finish_replay",
        side_effect=RuntimeError("simulated model replacement failure"),
    ):
        try:
            revoke_learning_record(first, db_path=db_path, model_path=model_path)
        except RuntimeError:
            pass
        else:
            raise AssertionError("model replacement failure was ignored")
    with database.connect_readonly(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'pending_model_sync:%'"
        ).fetchone()[0] == 1
    replay_mastery(db_path=db_path, model_path=model_path)
    assert _trace(model_path, db_path) == []
    with database.connect_readonly(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'pending_model_sync:%'"
        ).fetchone()[0] == 0
