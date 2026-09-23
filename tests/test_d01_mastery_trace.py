from __future__ import annotations

import hashlib
import json

from study_app.core.mastery_trace import list_topic_mastery_trace
from study_app.data import database


MODEL = {
    "model_name": "trace_test_model_v1",
    "subjects": [{
        "name": "测试学科", "mastery": 0.2,
        "modules": [{
            "name": "基础", "weight": 1.0, "mastery": 0.2,
            "topics": [{"name": "哈希表", "mastery": 0.2,
                        "difficulty": 0.6, "status": "learning"}],
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


def _record(correctness, *, date_string):
    return {
        "date": date_string,
        "subject": "测试学科", "module": "基础", "topic": "哈希表",
        "activity": "exercise", "source": "outside_class",
        "score": 80, "note": "哈希表练习",
        "problems": [{
            "title": "两数之和", "related_topics": ["哈希表"],
            "correctness": correctness,
            "partial_credit": 1.0 if correctness == 0 else 0.0,
            "difficulty_score": 0,
        }],
    }


def test_new_contributions_keep_complete_chain_and_verified_problem_evidence(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    before_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    first_payload = _record(0, date_string="2026-09-14")
    first_payload["problems"].append({
        "title": "无关题目", "related_topics": ["二叉搜索树"],
        "correctness": 100, "difficulty_score": 80,
    })
    first_id = database.add_learning_record(
        first_payload, db_path=db_path,
        model_path=model_path,
    )
    second_id = database.add_learning_record(
        _record(100, date_string="2026-09-15"), db_path=db_path,
        model_path=model_path,
    )
    diagnosis = list_topic_mastery_trace(
        "测试学科", "基础", "哈希表",
        model_path=model_path, db_path=db_path,
    )
    events = diagnosis["contributions"]
    assert [item["record_id"] for item in events] == [first_id, second_id]
    assert [item["record_status"] for item in events] == ["verified", "verified"]
    assert events[0]["normalization_version"] == database.NORMALIZATION_VERSION
    assert events[0]["model_identity"] == MODEL["model_name"]
    assert events[0]["model_version"] == "mastery-progress-sync-v1"
    assert events[0]["model_snapshot_sha256_before"] == before_hash
    assert events[0]["evidence_items"][0]["result_source"] == "explicit_numeric"
    assert events[0]["evidence_items"][0]["result_value"] == 0.0
    assert events[0]["evidence_items"][0]["mapping_source"] == "problem_related_topics"
    assert events[0]["evidence_items"][0]["difficulty_raw"] == 0
    assert events[0]["evidence_items"][0]["difficulty_applied"] == 20.0
    assert events[0]["evidence_items"][0]["difficulty_source"] == "explicit"
    assert len(events[0]["evidence_items"]) == 1
    assert diagnosis["coverage"] == "traced"
    assert diagnosis["contribution_chain_scope"] == "since_trace_v1"
    assert diagnosis["prior_baseline_status"] == "unverified"
    assert diagnosis["legacy_provenance_gap"] is False
    assert events[0]["new_mastery"] == events[1]["old_mastery"]


def test_missing_record_is_flagged_without_fabricated_replacement(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    record_id = database.add_learning_record(
        _record(0, date_string="2026-09-14"), db_path=db_path,
        model_path=model_path,
    )
    with database.connect(db_path) as connection:
        connection.execute("DELETE FROM learning_records WHERE id = ?", (record_id,))
    diagnosis = list_topic_mastery_trace(
        "测试学科", "基础", "哈希表",
        model_path=model_path, db_path=db_path,
    )
    assert diagnosis["contributions"][0]["record_status"] == "missing"
    assert diagnosis["contributions"][0]["record_id"] == record_id


def test_legacy_last_update_reports_gap_not_invented_chain(tmp_path):
    model_path, db_path = _fixture(tmp_path)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    topic = model["subjects"][0]["modules"][0]["topics"][0]
    topic["source_json"] = {
        "last_mastery_update": {"record_id": 999, "new_mastery": 0.3}
    }
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    diagnosis = list_topic_mastery_trace(
        "测试学科", "基础", "哈希表",
        model_path=model_path, db_path=db_path,
    )
    assert diagnosis["contributions"] == []
    assert diagnosis["legacy_provenance_gap"] is True
    assert diagnosis["coverage"] == "partial_legacy"


def test_unmarked_direct_sync_does_not_invent_normalization_or_difficulty_source(tmp_path):
    from study_app.data import model_progress_sync

    model_path, db_path = _fixture(tmp_path)
    record = _record(0, date_string="2026-09-14")
    record["id"] = 77
    model_progress_sync.sync_learning_record_to_model(
        record, model_path=model_path, db_path=db_path
    )
    model = json.loads(model_path.read_text(encoding="utf-8"))
    event = model["subjects"][0]["modules"][0]["topics"][0][
        "source_json"
    ]["mastery_contribution_trace_v1"][0]
    assert event["normalization_version"] is None
    assert event["evidence_items"][0]["difficulty_source"] is None
    assert event["record_id"] == 77


def test_repeated_sync_does_not_append_duplicate_contribution(tmp_path):
    from study_app.data import model_progress_sync

    model_path, db_path = _fixture(tmp_path)
    record_id = database.add_learning_record(
        _record(0, date_string="2026-09-14"), db_path=db_path,
        model_path=model_path,
    )
    record = next(
        item for item in database.load_raw_records(db_path)
        if item["id"] == record_id
    )
    model_progress_sync.sync_learning_record_to_model(
        record, model_path=model_path, db_path=db_path
    )
    diagnosis = list_topic_mastery_trace(
        "测试学科", "基础", "哈希表",
        model_path=model_path, db_path=db_path,
    )
    assert len(diagnosis["contributions"]) == 1
