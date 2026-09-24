"""Audit state-machine contract tests for the learning assistant (§10.3 / A28).

Every test runs against a temporary database; the real ``app_data`` store is
never touched. The full lifecycle test uses the deterministic ``mock``
advisor (zero network, zero audit rows) and checks the engine journal states;
the unit-level tests drive every audit marker through a temp ``db_path``.
"""
from __future__ import annotations

import json

import pytest

from study_app.ai import audit
from study_app.data.database import (
    connect,
    import_model_json,
    initialize_database,
    list_llm_call_audits,
)


def _model() -> dict:
    return {
        "warning_policy": {},
        "initial_percent_assessment": {"subjects": []},
        "subjects": [
            {
                "name": "数学",
                "weight": 1,
                "status": "active",
                "modules": [
                    {
                        "name": "第三章",
                        "weight": 1,
                        "status": "active",
                        "topics": [
                            {
                                "name": "函数",
                                "status": "learning",
                                "mastery": 0.5,
                                "importance": 1,
                                "difficulty": 0.4,
                                "forgetting_risk": 0.2,
                            }
                        ],
                    },
                    {
                        "name": "第四章",
                        "weight": 1,
                        "status": "active",
                        "topics": [
                            {
                                "name": "几何",
                                "status": "learning",
                                "mastery": 0.4,
                                "importance": 1,
                                "difficulty": 0.5,
                                "forgetting_risk": 0.3,
                            }
                        ],
                    },
                ],
            },
            {
                "name": "英语",
                "weight": 1,
                "status": "active",
                "modules": [
                    {
                        "name": "Unit 1",
                        "weight": 1,
                        "status": "active",
                        "topics": [
                            {
                                "name": "词汇",
                                "status": "learning",
                                "mastery": 0.6,
                                "importance": 1,
                                "difficulty": 0.3,
                                "forgetting_risk": 0.1,
                            }
                        ],
                    },
                ],
            },
        ],
    }


@pytest.fixture()
def seeded_db(tmp_path):
    db_path = tmp_path / "learning.sqlite"
    initialize_database(db_path)
    with connect(db_path) as connection:
        import_model_json(connection, _model())
    return db_path


@pytest.fixture()
def model_path(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(_model(), ensure_ascii=False), encoding="utf-8")
    return path


def _latest_audit_row(db_path):
    rows = list_llm_call_audits(limit=1, db_path=db_path)
    assert rows, "expected at least one llm_call_audits row"
    return rows[0]


def _patch_audit_persistence(db_path, monkeypatch):
    """Route audit writes to the temp db without touching production data."""
    from study_app.data.database import record_llm_call_audit, update_llm_call_audit

    def record(**fields):
        return record_llm_call_audit(**fields, db_path=db_path)

    def update(audit_id, status, error_message=None):
        update_llm_call_audit(audit_id, status, error_message, db_path=db_path)

    monkeypatch.setattr(audit, "record_llm_call_audit", record)
    monkeypatch.setattr(audit, "update_llm_call_audit", update)


def test_audit_status_vocabulary_covers_contract():
    expected = {
        "requested",
        "transport_failed",
        "parse_failed",
        "business_invalid",
        "validated",
        "awaiting_confirmation",
        "rejected",
        "adopted",
        "local_commit_failed",
        "completed",
        "undone",
    }
    assert expected.issubset(set(audit.AUDIT_STATUS_VALUES))


def test_engine_audit_marker_failure_is_visible(monkeypatch):
    from study_app.ai.audit import AuditPersistenceError
    from study_app.core.learning_assistant_engine import LearningAssistantEngine

    engine = object.__new__(LearningAssistantEngine)

    def fail_to_persist(_value):
        raise AuditPersistenceError("audit storage unavailable")

    monkeypatch.setattr(audit, "mark_audit_completed", fail_to_persist)
    with pytest.raises(AuditPersistenceError, match="audit storage unavailable"):
        engine._mark_audit(42, "mark_audit_completed")

    with pytest.raises(AttributeError):
        engine._mark_audit(42, "misspelled_audit_marker")


def test_mock_advisor_lifecycle_journals_contract_states(
    seeded_db, model_path, tmp_path
):
    from study_app.core.learning_assistant_engine import LearningAssistantEngine
    from study_app.core.learning_assistant_policy import save_privacy_settings

    save_privacy_settings({"advisor_mode": "mock"}, db_path=seeded_db)
    attachment = tmp_path / "homework.txt"
    attachment.write_text(
        "第1题 已知函数 f(x)=x^2+1，求它在 x=2 处的导数值。\n"
        "第2题 计算定积分 ∫[0,1] x dx 的值，并说明其几何意义。\n",
        encoding="utf-8",
    )
    engine = LearningAssistantEngine(db_path=seeded_db, model_path=model_path)

    bundle = engine.create_proposal("数学作业全对", [str(attachment)])
    assert bundle.proposal.action_type == "submit_homework"
    action_id = bundle.proposal.action_id

    receipt = engine.confirm_and_execute(
        action_id,
        confirmation_token=bundle.confirmation_token,
        execution_mode="user",
    )
    assert receipt.status == "completed"
    assert receipt.record_id is not None
    assert receipt.undo_token == {"kind": "record", "record_id": receipt.record_id}

    # Mock advisor: deterministic and local — no LLM call, no audit row.
    assert list_llm_call_audits(limit=100, db_path=seeded_db) == []

    entry = engine.load_action(action_id)
    assert entry is not None
    states = [item["state"] for item in entry["history"]]
    assert states == ["awaiting_confirmation", "completed"]
    assert entry["state"] == "completed"
    assert set(states).issubset(set(audit.AUDIT_STATUS_VALUES))
    undoable = engine.last_undoable()
    assert undoable is not None and undoable["action_id"] == action_id

    # A readonly proposal journals the "validated" state instead.
    readonly = engine.create_proposal("今天应该复习什么", [])
    assert readonly.proposal.action_type == "answer_query"
    readonly_entry = engine.load_action(readonly.proposal.action_id)
    assert readonly_entry["history"][0]["state"] == "validated"
    assert readonly_entry["state"] == "validated"


def test_external_failure_and_lifecycle_markers_write_contract_statuses(
    seeded_db, monkeypatch
):
    from study_app.ai.providers import LLMSettings

    _patch_audit_persistence(seeded_db, monkeypatch)
    settings = LLMSettings(
        enabled=True,
        provider="deepseek",
        model="deepseek-chat",
        custom_base_url="",
        api_key="sk-unit-test",
        single_call_token_limit=8000,
        daily_budget_cny=3.0,
        allow_upload_images=False,
        allow_upload_pdfs=False,
        enabled_features=("natural_query",),
    )
    monkeypatch.setattr(audit, "load_llm_settings", lambda: settings)

    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("simulated slow upstream")

    monkeypatch.setattr(audit, "chat_completion_json", raise_timeout)
    with pytest.raises(TimeoutError):
        audit.audited_chat_completion_json("unit_feature", "prompt")
    assert _latest_audit_row(seeded_db)["status"] == "transport_failed"

    def raise_decode(*_args, **_kwargs):
        raise json.JSONDecodeError("bad json", "doc", 0)

    monkeypatch.setattr(audit, "chat_completion_json", raise_decode)
    with pytest.raises(json.JSONDecodeError):
        audit.audited_chat_completion_json("unit_feature", "prompt")
    assert _latest_audit_row(seeded_db)["status"] == "parse_failed"

    monkeypatch.setattr(
        audit, "chat_completion_json", lambda *_args, **_kwargs: '{"ok": true}'
    )
    audited = audit.audited_chat_completion_json("unit_feature", "prompt")
    row = _latest_audit_row(seeded_db)
    assert row["status"] == "parsed_unvalidated"
    assert audit.audit_id_of(audited) == row["id"]

    markers = (
        (audit.mark_audit_validation_failed, ValueError("shape"), "validation_failed"),
        (audit.mark_audit_validated, None, "validated_not_adopted"),
        (audit.mark_audit_adopted, None, "adopted"),
        (audit.mark_audit_business_invalid, ValueError("bad"), "business_invalid"),
        (audit.mark_audit_awaiting_confirmation, None, "awaiting_confirmation"),
        (audit.mark_audit_rejected, None, "rejected"),
        (audit.mark_audit_local_commit_failed, OSError("disk"), "local_commit_failed"),
        (audit.mark_audit_completed, None, "completed"),
        (audit.mark_audit_undone, None, "undone"),
    )
    for marker, error, expected_status in markers:
        if error is None:
            marker(audited)
        else:
            marker(audited, error)
        assert _latest_audit_row(seeded_db)["status"] == expected_status


def test_assistant_call_summary_keeps_verbatim_and_drops_prompt_body():
    question_text = "内部题目原文不应外泄"
    payload = {
        "question": question_text,
        "attachments": [{"name": "a.pdf"}, {"name": "b.png"}],
        "unknown_field": "无关内容",
    }
    summary = audit.assistant_call_summary(
        payload,
        action_id="la-unit-1234",
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        external_data_categories=["homework_problem_text"],
        cost_estimate_cny=0.012345,
    )
    assert summary["action_id"] == {"type": "id", "value": "la-unit-1234"}
    assert summary["input_sha256"] == {"type": "sha256", "value": "a" * 64}
    assert summary["output_sha256"] == {"type": "sha256", "value": "b" * 64}
    assert summary["external_data_categories"] == {
        "type": "categories",
        "value": ["homework_problem_text"],
    }
    assert summary["cost_estimate_cny"] == {"type": "number", "value": 0.012345}
    # Prompt-body-shaped fields keep only the shape, never the content.
    assert summary["question"] == {"type": "text", "chars": len(question_text)}
    assert summary["attachments"] == {"type": "list", "items": 2}
    assert "unknown_field" not in summary
    encoded = json.dumps(summary, ensure_ascii=False)
    assert "内部题目原文不应外泄" not in encoded
    assert "无关内容" not in encoded
    assert "a.pdf" not in encoded

    dropped = audit.assistant_call_summary(
        {},
        action_id="包含 空格与中文",
        input_sha256="not-hex",
        output_sha256="",
        external_data_categories=["bad category!"],
        cost_estimate_cny=True,
    )
    for key in (
        "action_id",
        "input_sha256",
        "output_sha256",
        "external_data_categories",
        "cost_estimate_cny",
    ):
        assert key not in dropped
