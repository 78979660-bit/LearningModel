"""Learning-assistant alert-action tests (engine level).

Every test runs against a temporary database + temporary model JSON; the real
``app_data`` store is never touched. Covers: idempotent recompute, intent-driven
handle/snooze with journal + evidence-preservation contracts, rejection of
actions on non-active alerts, undo of recompute, and explain_alert on
migrated / fresh environments.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from study_app.core.learning_assistant_engine import (
    LearningAssistantEngine,
    LearningAssistantError,
)
from study_app.core.learning_assistant_intent import build_alert_action
from study_app.core.learning_assistant_policy import save_privacy_settings
from study_app.data import database
from data_test_support import initialize_legacy_base_database

SUBJECT = "数学"
MODULE = "基础模块"
TOPIC = "基础主题"


def _model() -> dict:
    return {
        "warning_policy": {
            "bkt_model": {
                "enabled": True,
                "base_guess": 0.22,
                "base_slip": 0.12,
                "base_learn": 0.08,
            }
        },
        "initial_percent_assessment": {"subjects": []},
        "subjects": [
            {
                "name": SUBJECT,
                "weight": 1,
                "status": "active",
                "modules": [
                    {
                        "name": MODULE,
                        "weight": 1,
                        "status": "active",
                        "topics": [
                            {
                                "name": TOPIC,
                                "status": "learning",
                                "mastery": 0.5,
                                "importance": 1,
                                "difficulty": 0.4,
                                "forgetting_risk": 0.2,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _install_f2_tables(db_path: Path) -> None:
    """Install the four F2 tables exactly like tests/test_f2_i05 does."""
    with database.connect(db_path) as connection:
        for constant in (
            database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL,
            database.KNOWLEDGE_ALERTS_TABLE_SQL,
            database.KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
            database.KNOWLEDGE_ALERT_INDEXES_SQL,
        ):
            for part in constant.split(";"):
                part = part.strip()
                if part:
                    connection.execute(part)


@pytest.fixture()
def env(tmp_path: Path):
    db_path = tmp_path / "assistant.sqlite"
    model_path = tmp_path / "model.json"
    model_path.write_text(
        json.dumps(_model(), ensure_ascii=False), encoding="utf-8"
    )
    database.initialize_database(db_path)
    with database.connect(db_path) as connection:
        database.import_model_json(connection, _model())
    _install_f2_tables(db_path)
    database.register_topic_identities(db_path)
    save_privacy_settings(
        {"advisor_mode": "mock", "allow_external_intent": False}, db_path
    )
    return db_path, model_path


@pytest.fixture()
def engine(env):
    db_path, model_path = env
    return LearningAssistantEngine(db_path=db_path, model_path=model_path)


def _confirm(engine: LearningAssistantEngine, bundle):
    return engine.confirm_and_execute(
        bundle.proposal.action_id,
        confirmation_token=bundle.confirmation_token,
        execution_mode="user",
    )


def _seed_recent_failure(db_path: Path, model_path: Path) -> None:
    database.add_learning_record(
        {
            "date": date.today().isoformat(),
            "subject": SUBJECT,
            "activity": "exercise",
            "duration_minutes": 30,
            "problems": [
                {
                    "title": "练习1",
                    "status": "wrong",
                    "correctness": 0.0,
                    "error_cause": "计算失误",
                    "related_topics": [TOPIC],
                }
            ],
        },
        db_path=db_path,
        model_path=model_path,
    )


def _make_alert(engine: LearningAssistantEngine) -> int:
    """Seed one failed exercise and recompute; return the created alert id."""
    _seed_recent_failure(engine.db_path, engine.model_path)
    receipt = engine.recompute_alerts_now()
    assert receipt.status == "completed"
    assert receipt.alert_result.get("created", 0) >= 1
    alerts = database.list_knowledge_alerts(db_path=engine.db_path)
    assert alerts, "expected at least one alert after recompute"
    return alerts[0].id


def _topic_state(db_path: Path) -> tuple[object, object]:
    with database.connect_readonly(db_path) as connection:
        row = connection.execute(
            "SELECT status, mastery FROM topics WHERE name = ?", (TOPIC,)
        ).fetchone()
    assert row is not None
    return row["status"], row["mastery"]


def _evidence_counts(db_path: Path) -> tuple[int, int]:
    with database.connect_readonly(db_path) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("learning_records", "problem_attempts")
        )


def test_recompute_alerts_now_is_idempotent(engine):
    _seed_recent_failure(engine.db_path, engine.model_path)

    first = engine.recompute_alerts_now()
    assert first.status == "completed"
    assert first.alert_result["created"] >= 1
    assert first.undo_token == {"kind": "recompute"}
    created_total = (
        first.alert_result["created"]
        + first.alert_result["reactivated"]
        + first.alert_result["resolved"]
    )
    assert created_total >= 1

    second = engine.recompute_alerts_now()
    assert second.status == "completed"
    assert second.alert_result["created"] == 0
    assert second.alert_result["reactivated"] == 0
    assert second.alert_result["resolved"] == 0
    assert second.alert_result["unchanged"] == created_total
    # every existing alert stayed exactly as it was
    assert {alert.status for alert in database.list_knowledge_alerts(db_path=engine.db_path)} == {
        "active"
    }


def test_handle_alert_via_intent_marks_handled_and_journals_la_actor(engine):
    db_path = engine.db_path
    alert_id = _make_alert(engine)
    topic_before = _topic_state(db_path)
    evidence_before = _evidence_counts(db_path)
    snapshot_before = database.list_knowledge_alerts(db_path=db_path)[0].snapshot

    bundle = engine.create_proposal(f"处理预警{alert_id}")
    proposal = bundle.proposal
    assert proposal.action_type == "handle_alert"
    assert proposal.user_facts["alert_id"] == alert_id
    assert proposal.requires_confirmation is True

    receipt = _confirm(engine, bundle)
    assert receipt.status == "completed"
    assert receipt.alert_id == alert_id
    assert "不代表" in receipt.message  # 处理 ≠ 掌握

    alert = database.list_knowledge_alerts(db_path=db_path)[0]
    assert alert.id == alert_id
    assert alert.status == "handled"
    assert alert.snapshot == snapshot_before  # 原始证据未修改

    events = database.list_knowledge_alert_events(alert_id, db_path)
    assert [event.event_type for event in events] == ["created", "handled"]
    assert events[-1].actor.startswith("la:")
    assert (events[-1].from_status, events[-1].to_status) == ("active", "handled")

    # handling must not touch topic mastery/status nor any learning evidence
    assert _topic_state(db_path) == topic_before
    assert _evidence_counts(db_path) == evidence_before

    # confirming the SAME action id again replays the stored receipt: no new event
    replay = _confirm(engine, bundle)
    assert replay.status == "completed"
    assert replay.replayed is True
    assert len(database.list_knowledge_alert_events(alert_id, db_path)) == 2


def test_action_on_non_active_alert_is_rejected_without_new_event(engine):
    db_path = engine.db_path
    alert_id = _make_alert(engine)

    # snooze first: the alert leaves the active state
    tomorrow = date.today() + timedelta(days=1)
    snooze = engine.create_proposal(f"延后预警{alert_id}到{tomorrow.isoformat()}")
    assert snooze.proposal.action_type == "snooze_alert"
    snooze_receipt = _confirm(engine, snooze)
    assert snooze_receipt.status == "completed"

    events_after_snooze = database.list_knowledge_alert_events(alert_id, db_path)

    # a handle attempt on the same (no longer active) alert is rejected
    retry = engine.create_proposal(f"处理预警{alert_id}")
    assert retry.proposal.user_facts["alert_id"] == alert_id
    with pytest.raises(LearningAssistantError) as error:
        _confirm(engine, retry)
    assert "只有 active" in str(error.value)

    # rejection appends no lifecycle event
    assert (
        database.list_knowledge_alert_events(alert_id, db_path)
        == events_after_snooze
    )


def test_snooze_via_intent_honors_snoozed_until_and_keeps_evidence(engine):
    db_path = engine.db_path
    alert_id = _make_alert(engine)
    snapshot_before = database.list_knowledge_alerts(db_path=db_path)[0].snapshot
    evidence_before = _evidence_counts(db_path)
    topic_before = _topic_state(db_path)

    tomorrow = date.today() + timedelta(days=1)
    bundle = engine.create_proposal(f"延后预警{alert_id}到{tomorrow.isoformat()}")
    proposal = bundle.proposal
    assert proposal.action_type == "snooze_alert"
    assert proposal.user_facts["alert_id"] == alert_id
    assert proposal.user_facts["snoozed_until"] == tomorrow.isoformat()

    receipt = _confirm(engine, bundle)
    assert receipt.status == "completed"
    assert receipt.alert_id == alert_id

    alert = database.list_knowledge_alerts(db_path=db_path)[0]
    assert alert.status == "snoozed"
    assert alert.snoozed_until == tomorrow
    assert alert.snapshot == snapshot_before  # evidence untouched

    events = database.list_knowledge_alert_events(alert_id, db_path)
    assert [event.event_type for event in events] == ["created", "snoozed"]
    assert events[-1].actor.startswith("la:")
    assert _evidence_counts(db_path) == evidence_before
    assert _topic_state(db_path) == topic_before

    # a past snooze date is rejected at proposal-build time...
    yesterday = date.today() - timedelta(days=1)
    with pytest.raises(ValueError):
        build_alert_action(
            "snooze_alert", alert_id, date_value=yesterday, today=date.today()
        )
    # ...and on the execute path (rejected, no state change, no event)
    past = engine.create_proposal(f"延后预警{alert_id}到{yesterday.isoformat()}")
    with pytest.raises(LearningAssistantError):
        _confirm(engine, past)
    alert = database.list_knowledge_alerts(db_path=db_path)[0]
    assert alert.status == "snoozed"
    assert alert.snoozed_until == tomorrow
    assert [event.event_type for event in database.list_knowledge_alert_events(alert_id, db_path)] == [
        "created",
        "snoozed",
    ]


def test_recompute_undo_completes(engine):
    receipt = engine.recompute_alerts_now()
    assert receipt.status == "completed"

    undoable = engine.last_undoable()
    assert undoable is not None
    assert undoable["undo_token"] == {"kind": "recompute"}
    assert undoable["action_id"] == receipt.action_id

    undo = engine.undo_last()
    assert undo.status == "completed"
    assert undo.action_type == "undo_last_action"
    assert undo.undo_token is None
    assert isinstance(undo.alert_result, dict)
    assert "重算" in undo.message
    # the recompute step itself is now marked undone in the journal
    entry = engine.load_action(receipt.action_id)
    assert entry is not None and entry["state"] == "undone"


def test_explain_alert_reports_rules_and_disclaimer(engine):
    db_path = engine.db_path
    alert_id = _make_alert(engine)

    result = engine.explain_alert(alert_id)
    assert result["available"] is True
    assert result["alert_id"] == alert_id
    text = "\n".join(result["lines"])
    assert "规则版本" in text
    assert "建议动作" in text
    assert "不代表" in text

    # a fresh environment without the F2 tables degrades gracefully
    fresh_db = db_path.with_name("bare.sqlite")
    initialize_legacy_base_database(fresh_db)
    bare_engine = LearningAssistantEngine(db_path=fresh_db)
    missing = bare_engine.explain_alert(1)
    assert missing["available"] is False
    assert "未安装" in str(missing.get("reason", ""))
