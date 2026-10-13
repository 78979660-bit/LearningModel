from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from study_app.core.knowledge_alerts import reconcile_knowledge_alerts, snooze_alert
from study_app.data import database
from study_app.quick.insights_bridge import InsightsBridge
from study_app.quick.knowledge_service import KnowledgeService, StaleKnowledgeAlertError


AS_OF = "2026-10-04"


def _service_paths(tmp_path: Path):
    model = json.loads(
        (Path(__file__).parent / "fixtures" / "minimal_model.json").read_text(
            encoding="utf-8"
        )
    )
    topic = model["subjects"][0]["modules"][0]["topics"][0]
    topic["status"] = "learning"
    topic["mastery"] = 0.25
    model["warning_policy"]["bkt_model"] = {"enabled": True}
    model_path = tmp_path / "knowledge-model.json"
    records_path = tmp_path / "knowledge-records.json"
    db_path = tmp_path / "knowledge.sqlite"
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    records_path.write_text('{"records": []}', encoding="utf-8")
    database.ensure_seeded_database(db_path, model_path=model_path)
    return db_path, model_path, records_path


def _create_active_alert(service: KnowledgeService) -> int:
    state = service._load_state()
    classified = service._classified_topics(state, service.db_path)
    candidates = [item for item in classified if item.alert_eligible]
    assert candidates
    with database.connect(service.db_path) as connection:
        result = reconcile_knowledge_alerts((candidates[0],), state.today, connection)
    assert len(result.created_ids) == 1
    return result.created_ids[0]


def test_constructor_stores_explicit_paths_without_touching_disk(tmp_path):
    missing = tmp_path / "does-not-exist"
    service = KnowledgeService(missing / "db.sqlite", missing / "model.json", missing / "records.json")

    assert service.db_path == missing / "db.sqlite"
    assert not missing.exists()


def test_load_returns_real_topology_topic_evidence_and_readonly_alerts(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    service = KnowledgeService(db_path, model_path, records_path, today=AS_OF)
    events_before = database.list_knowledge_alert_events(db_path=db_path)

    payload = service.load()

    assert payload["subjects"] == [
        {"key": payload["selectedSubjectKey"], "label": "测试学科", "hasCurrentStructure": True}
    ]
    graph = payload["graph"]
    assert graph["subjectLabel"] == "测试学科"
    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["label"] == "测试模块"
    # Chapter graph projection is authoritative: a legacy course status alone
    # does not synthesize coverage or a mastery estimate.
    assert node["state"] == "unlearned"
    assert node["coverageCount"] == 0 and node["masteryPoint"] is None
    assert node["position"] == {"x": 42, "y": 40}
    detail = graph["details"][node["key"]]["topics"][0]
    assert detail["label"] == "测试知识点"
    assert detail["observationCount"] == 0
    assert detail["evidenceConfidence"] == 0
    assert detail["explanation"]["layers"]
    assert payload["alerts"] == []
    assert database.list_knowledge_alert_events(db_path=db_path) == events_before


def test_handle_commits_existing_core_transition_and_audit(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    service = KnowledgeService(db_path, model_path, records_path, today=AS_OF)
    alert_id = _create_active_alert(service)

    service.load()
    handled = service.handle(alert_id)

    assert handled["saved"] is True and handled["status"] == "handled"
    assert database.list_knowledge_alerts(db_path=db_path)[0].status == "handled"
    event = database.list_knowledge_alert_events(alert_id, db_path)[-1]
    assert (event.event_type, event.from_status, event.to_status, event.actor) == (
        "handled", "active", "handled", "desktop_local_user"
    )

def test_snooze_commits_existing_core_transition_and_audit(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    service = KnowledgeService(db_path, model_path, records_path, today=AS_OF)
    alert_id = _create_active_alert(service)
    service.load()
    snoozed = service.snooze(alert_id, "2026-10-06")
    assert snoozed["saved"] is True and snoozed["status"] == "snoozed"
    assert snoozed["snoozedUntil"] == "2026-10-06"
    snooze_event = database.list_knowledge_alert_events(alert_id, db_path)[-1]
    assert (snooze_event.event_type, snooze_event.to_status, snooze_event.actor) == (
        "snoozed", "snoozed", "desktop_local_user"
    )


def test_invalid_or_stale_alert_action_is_rejected_without_new_event(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    service = KnowledgeService(db_path, model_path, records_path, today=AS_OF)
    alert_id = _create_active_alert(service)
    service.load()
    events_before = database.list_knowledge_alert_events(alert_id, db_path)

    with pytest.raises(ValueError):
        service.snooze(alert_id, "2026-02-30")
    with database.connect(db_path) as connection:
        snooze_alert(alert_id, date.fromisoformat("2026-10-06"), date.fromisoformat(AS_OF), connection)

    with pytest.raises(StaleKnowledgeAlertError):
        service.handle(alert_id)
    events_after = database.list_knowledge_alert_events(alert_id, db_path)
    assert len(events_after) == len(events_before) + 1
    assert events_after[:-1] == events_before and events_after[-1].to_status == "snoozed"
    assert database.list_knowledge_alerts(db_path=db_path)[0].status == "snoozed"


def test_committed_handle_stays_saved_when_post_commit_bridge_refresh_fails(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    service = KnowledgeService(db_path, model_path, records_path, today=AS_OF)
    alert_id = _create_active_alert(service)
    subject_key = service.load()["selectedSubjectKey"]
    bridge = InsightsBridge(SimpleNamespace(), service, None)
    try:
        with patch.object(service, "load", side_effect=RuntimeError("refresh failed")):
            result = bridge._execute(
                "alert.handle",
                {"alertId": alert_id, "confirmed": True},
                subject_key,
            )

        assert result["result"]["saved"] is True
        assert result["result"]["status"] == "handled"
        assert result["reloadFailed"] is True
        assert database.list_knowledge_alerts(db_path=db_path)[0].status == "handled"
        event = database.list_knowledge_alert_events(alert_id, db_path)[-1]
        assert event.to_status == "handled" and event.actor == "desktop_local_user"
    finally:
        bridge.close()

def test_classification_reuses_dashboard_calculations_from_same_read(tmp_path, monkeypatch):
    from study_app.core import computation_context
    db_path, model_path, records_path = _service_paths(tmp_path)
    service = KnowledgeService(db_path, model_path, records_path, today=AS_OF)
    contexts = []
    state = service._load_state(contexts.append)
    def unexpected(*args, **kwargs):
        raise AssertionError("classification repeated a computed topic state")
    monkeypatch.setattr(computation_context, "topic_bkt_state", unexpected)
    assert service._classified_topics(state, db_path, contexts[0])
