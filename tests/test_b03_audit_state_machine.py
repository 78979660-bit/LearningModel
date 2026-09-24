from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _install_audit_store(monkeypatch):
    from study_app.ai import audit

    rows = {}
    transitions = []

    def insert(**fields):
        audit_id = len(rows) + 1
        rows[audit_id] = dict(fields)
        return audit_id

    def update(audit_id, status, error_message=None):
        rows[audit_id]["status"] = status
        rows[audit_id]["error_message"] = error_message
        transitions.append((audit_id, status, error_message))

    monkeypatch.setattr(audit, "record_llm_call_audit", insert)
    monkeypatch.setattr(audit, "update_llm_call_audit", update)
    monkeypatch.setattr(
        audit,
        "load_llm_settings",
        lambda: SimpleNamespace(provider="mock", model="mock-model"),
    )
    monkeypatch.setattr(audit, "estimate_tokens", lambda _prompt: 12)
    monkeypatch.setattr(audit, "assert_can_call_llm", lambda _prompt, settings: settings)
    return audit, rows, transitions


def test_request_and_parse_failures_are_distinct(monkeypatch):
    audit, rows, transitions = _install_audit_store(monkeypatch)

    monkeypatch.setattr(audit, "chat_completion_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network")))
    with pytest.raises(RuntimeError):
        audit.audited_chat_completion_json("query", "prompt")
    assert rows[1]["status"] == "transport_failed"

    def bad_json(*_args, **_kwargs):
        raise json.JSONDecodeError("bad", "{", 1)

    monkeypatch.setattr(audit, "chat_completion_json", bad_json)
    with pytest.raises(json.JSONDecodeError):
        audit.audited_chat_completion_json("query", "prompt")
    assert rows[2]["status"] == "parse_failed"
    assert transitions[-1][1] == "parse_failed"


def test_record_business_validation_failure_is_not_success(monkeypatch):
    audit, rows, transitions = _install_audit_store(monkeypatch)
    monkeypatch.setattr(audit, "chat_completion_json", lambda *_args, **_kwargs: '{"problems":[]}')
    monkeypatch.setattr(
        "study_app.ai.record_parser.load_llm_settings",
        lambda: SimpleNamespace(
            enabled_features=("record_parser",), provider="mock", model="mock-model"
        ),
    )

    from study_app.ai.record_parser import parse_record_payload_with_llm

    with pytest.raises(Exception):
        parse_record_payload_with_llm({"note": "fixture"})

    assert rows[1]["status"] == "validation_failed"
    assert "adopted" not in [status for _audit_id, status, _error in transitions]


def test_validated_and_adopted_states_are_separate(monkeypatch):
    audit, rows, transitions = _install_audit_store(monkeypatch)
    monkeypatch.setattr(audit, "chat_completion_json", lambda *_args, **_kwargs: '{"ok":true}')

    content = audit.audited_chat_completion_json("query", "prompt")
    audit.mark_audit_validated(content)
    assert rows[1]["status"] == "validated_not_adopted"
    audit.mark_audit_adopted(content)
    assert rows[1]["status"] == "adopted"


def test_repair_call_links_to_original_audit(monkeypatch):
    audit, rows, transitions = _install_audit_store(monkeypatch)
    responses = iter(("first", "repair"))
    monkeypatch.setattr(audit, "chat_completion_json", lambda *_args, **_kwargs: next(responses))

    from study_app.ai import study_plan_generator as generator
    from study_app.ai.validation import LLMValidationError

    plan = {key: ["ok"] for key in generator.PLAN_KEYS}
    outcomes = iter((LLMValidationError("invalid"), plan))

    def parse(_content):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(generator, "parse_and_validate_plan", parse)
    monkeypatch.setattr(generator, "validate_plan_allowed_subjects", lambda *_args: None)
    monkeypatch.setattr(generator, "validate_plan_exam_scope", lambda *_args: None)

    result = generator.generate_plan_with_llm({})

    assert result["judgement"] == ["ok"]
    assert rows[1]["status"] == "validation_failed"
    assert rows[2]["parent_audit_id"] == 1
    assert rows[2]["status"] == "adopted"
    assert (2, "validated_not_adopted", None) in transitions


def test_parent_link_and_state_update_persist_in_sqlite(tmp_path):
    from study_app.data import database

    db_path = tmp_path / "audit.sqlite"
    database.initialize_database(db_path)
    parent_id = database.record_llm_call_audit(
        "plan", "mock", "model", 10, {}, "validation_failed", db_path=db_path
    )
    repair_id = database.record_llm_call_audit(
        "plan_repair",
        "mock",
        "model",
        8,
        {},
        "requested",
        db_path=db_path,
        parent_audit_id=parent_id,
    )
    database.update_llm_call_audit(
        repair_id, "validated_not_adopted", db_path=db_path
    )

    rows = database.list_llm_call_audits(limit=2, db_path=db_path)
    assert rows[0]["id"] == repair_id
    assert rows[0]["parent_audit_id"] == parent_id
    assert rows[0]["status"] == "validated_not_adopted"
