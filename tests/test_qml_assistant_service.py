from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from study_app.core.learning_assistant_engine import LearningAssistantEngine, StaleProposalError
from study_app.core.learning_assistant_policy import save_privacy_settings
from study_app.data import database
from study_app.data.subject_repository import SubjectCatalogRepository
from study_app.quick.assistant_service import AssistantService


TODAY = date(2026, 10, 4)
SUBMIT_TEXT = "测试学科 测试知识点 作业2题全对，2026-10-04 做完了"
HOMEWORK_TEXT = (
    "第1题 测试知识点：计算基础题\n解：过程完整，结论正确\n\n"
    "第2题 测试知识点：计算进阶题\n解：过程完整，结论正确\n"
)


def _model() -> dict:
    return {
        "model_name": "assistant_service_fixture",
        "scale": "0-1",
        "warning_policy": {
            "period_days": 3,
            "period_benchmark_score": 55,
            "stale_review_days": 6,
            "bkt_model": {
                "enabled": True,
                "base_guess": 0.22,
                "base_slip": 0.12,
                "base_learn": 0.08,
            },
        },
        "subjects": [
            {
                "name": "测试学科",
                "weight": 1,
                "status": "learning",
                "modules": [
                    {
                        "name": "测试模块",
                        "weight": 1,
                        "mastery": 0.25,
                        "status": "learning",
                        "topics": [
                            {
                                "id": "assistant-service-topic",
                                "name": "测试知识点",
                                "status": "learning",
                                "mastery": 0.25,
                                "importance": 1,
                                "difficulty": 0.5,
                                "forgetting_risk": 0.8,
                            }
                        ],
                    }
                ],
            }
        ],
        "initial_percent_assessment": {
            "subjects": [{"name": "测试学科", "initial_score": 45}]
        },
    }


def _record_wrong_attempt(env) -> None:
    database.add_learning_record(
        {
            "date": TODAY.isoformat(),
            "subject": "测试学科",
            "activity": "exercise",
            "duration_minutes": 30,
            "problems": [
                {
                    "title": "练习题",
                    "status": "wrong",
                    "correctness": 0.0,
                    "error_cause": "计算失误",
                    "related_topics": ["测试知识点"],
                }
            ],
        },
        db_path=env.db_path,
        model_path=env.model_path,
    )


@pytest.fixture()
def env(tmp_path: Path):
    db_path = tmp_path / "assistant.sqlite"
    model_path = tmp_path / "model.json"
    records_path = tmp_path / "records.json"
    model = _model()
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    records_path.write_text("[]", encoding="utf-8")
    database.initialize_database(db_path)
    with database.connect(db_path) as connection:
        database.import_model_json(connection, model)
        for constant in (
            database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_TABLE_SQL,
            database.KNOWLEDGE_ALERTS_TABLE_SQL,
            database.KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL,
            database.KNOWLEDGE_ALERT_INDEXES_SQL,
        ):
            for statement in constant.split(";"):
                if statement.strip():
                    connection.execute(statement)
    database.register_topic_identities(db_path)
    assert SubjectCatalogRepository(db_path).resolve_subject("测试学科").canonical_name == "测试学科"
    # A deterministic local advisor stub is confined to this test database.
    save_privacy_settings(
        {"advisor_mode": "mock", "allow_external_intent": False}, db_path
    )
    service = AssistantService(db_path, model_path, records_path, today=TODAY)
    attachment = tmp_path / "homework.txt"
    attachment.write_text(HOMEWORK_TEXT, encoding="utf-8")
    return SimpleNamespace(
        service=service,
        db_path=db_path,
        model_path=model_path,
        records_path=records_path,
        attachment=attachment,
        tmp_path=tmp_path,
    )


def test_constructor_is_lazy_and_accepts_injected_paths(tmp_path: Path):
    db_path = tmp_path / "missing" / "study.sqlite"
    model_path = tmp_path / "missing" / "model.json"
    records_path = tmp_path / "missing" / "records.json"

    service = AssistantService(db_path, model_path, records_path, today="2026-10-04")

    assert service.today == TODAY
    assert not db_path.parent.exists()


def test_load_returns_allowlisted_policy_privacy_and_private_undo_projection(env):
    loaded = env.service.load()

    assert loaded["journal"] == []
    assert "recompute_alerts" in loaded["policy"]
    assert loaded["privacy"]["advisor_mode"] == "mock"
    assert loaded["privacy"]["llm"]["natural_query_enabled"] is False
    assert loaded["policyActions"] == [
        {
            "key": "recompute_alerts",
            "label": "重算知识预警",
            "enabled": False,
        }
    ]
    assert loaded["undo"] == {
        "available": False,
        "action_id": "",
        "action_type": "",
        "message": "",
    }
    encoded = json.dumps(loaded, ensure_ascii=False).lower()
    assert str(env.db_path).lower() not in encoded
    assert str(env.model_path).lower() not in encoded
    assert "api_key" not in encoded
    assert "api_key_protected" not in encoded


def test_ask_is_local_by_default_and_explicit_external_call_uses_injected_database(env):
    fake_state = SimpleNamespace(raw_records=())
    local = {"answer": ["本地回答"], "evidence": ["本地证据"], "source": ["local"]}
    with (
        patch("study_app.core.dashboard.load_dashboard_state", return_value=fake_state) as load_state,
        patch("study_app.ai.natural_query.answer_query_locally", return_value=local),
        patch("study_app.ai.providers.is_llm_feature_enabled") as feature_enabled,
        patch("study_app.ai.natural_query.answer_query_with_llm") as answer_llm,
    ):
        default_answer = env.service.ask("今天先学什么？", [str(env.attachment)])

    assert default_answer["answer"] == local
    assert default_answer["external_requested"] is False
    assert default_answer["external_used"] is False
    feature_enabled.assert_not_called()
    answer_llm.assert_not_called()
    assert load_state.call_args.kwargs == {
        "model_path": env.model_path,
        "records_path": env.records_path,
        "today": TODAY,
        "db_path": env.db_path,
    }

    with (
        patch("study_app.core.dashboard.load_dashboard_state", return_value=fake_state),
        patch("study_app.ai.natural_query.answer_query_locally", return_value=local),
        patch("study_app.ai.providers.is_llm_feature_enabled", return_value=True),
        patch(
            "study_app.ai.natural_query.answer_query_with_llm",
            return_value={"answer": ["外部回答"], "evidence": ["证据"], "source": ["LLM"]},
        ) as answer_llm,
    ):
        external_answer = env.service.ask("今天先学什么？", [str(env.attachment)], external=True)

    assert external_answer["external_used"] is True
    assert external_answer["answer"]["answer"] == ["外部回答"]
    assert answer_llm.call_args.kwargs["db_path"] == env.db_path
    assert str(env.attachment) not in repr(answer_llm.call_args.args)


def test_natural_query_audit_lifecycle_uses_explicit_db_path(env, monkeypatch):
    from study_app.ai import natural_query
    from study_app.ai.audit import AuditedJSON
    from study_app.core.dashboard import load_dashboard_state

    state = load_dashboard_state(
        model_path=env.model_path,
        records_path=env.records_path,
        today=TODAY,
        db_path=env.db_path,
    )
    audit_paths = []
    content = AuditedJSON(
        json.dumps(
            {"answer": ["ok"], "evidence": ["fixture"], "source": ["llm"]},
            ensure_ascii=False,
        ),
        17,
    )

    def fake_audit(*args, **kwargs):
        audit_paths.append(("request", kwargs.get("db_path")))
        return content

    monkeypatch.setattr(natural_query, "audited_chat_completion_json", fake_audit)
    monkeypatch.setattr(
        natural_query,
        "mark_audit_validation_failed",
        lambda _value, _error, **kwargs: audit_paths.append(("invalid", kwargs.get("db_path"))),
    )
    monkeypatch.setattr(
        natural_query,
        "mark_audit_validated",
        lambda _value, **kwargs: audit_paths.append(("validated", kwargs.get("db_path"))),
    )
    monkeypatch.setattr(
        natural_query,
        "mark_audit_adopted",
        lambda _value, **kwargs: audit_paths.append(("adopted", kwargs.get("db_path"))),
    )

    result = natural_query.answer_query_with_llm(
        "问题", state, list(state.raw_records), db_path=env.db_path
    )

    assert result["answer"] == ["ok"]
    assert audit_paths == [
        ("request", env.db_path),
        ("validated", env.db_path),
        ("adopted", env.db_path),
    ]


def test_external_error_keeps_local_answer_and_hides_provider_details(env):
    local = {"answer": ["本地回答"], "evidence": ["本地证据"], "source": ["local"]}
    with (
        patch(
            "study_app.core.dashboard.load_dashboard_state",
            return_value=SimpleNamespace(raw_records=()),
        ),
        patch("study_app.ai.natural_query.answer_query_locally", return_value=local),
        patch("study_app.ai.providers.is_llm_feature_enabled", return_value=True),
        patch(
            "study_app.ai.natural_query.answer_query_with_llm",
            side_effect=RuntimeError("api_key=secret-token https://private.example/path"),
        ),
    ):
        result = env.service.ask("问题", [], external=True)

    assert result["answer"] == local
    assert result["external_used"] is False
    assert "secret-token" not in result["external_error"]
    assert "private.example" not in result["external_error"]


def test_external_validation_failure_is_distinct_from_disabled_or_transport(env):
    from study_app.ai.validation import LLMValidationError
    with (
        patch("study_app.ai.providers.is_llm_feature_enabled", return_value=True),
        patch("study_app.ai.natural_query.answer_query_with_llm",
              side_effect=LLMValidationError("untrusted endpoint secret must not be shown")),
    ):
        result = env.service.ask("哪些地方需要加强？", [], external=True)
    assert result["external_requested"] and not result["external_used"]
    assert "已返回" in result["external_error"] and "格式" in result["external_error"]
    assert "secret" not in json.dumps(result)
    assert result["answer"]["answer"]


def test_preview_confirm_and_undo_use_private_one_time_handles(env):
    preview = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])

    assert preview["token"]
    assert preview["confirmationAllowed"] is True
    assert preview["autoEligible"] is False
    assert preview["autoEnabled"] is False
    assert preview["proposal"]["action_type"] == "submit_homework"
    assert preview["proposal"]["attachments"][0]["file_name"] == "homework.txt"
    encoded_preview = json.dumps(preview, ensure_ascii=False)
    assert str(env.attachment) not in encoded_preview
    assert "confirmation_secret" not in encoded_preview
    assert "confirmation_token" not in encoded_preview

    receipt = env.service.confirm(preview["token"])
    assert receipt["saved"] is True
    assert receipt["status"] == "completed"
    assert receipt["recordId"] is not None
    assert receipt["refreshWarning"] is False
    assert "undo_token" not in json.dumps(receipt, ensure_ascii=False)

    undo_preview = env.service.undo_preview()
    assert undo_preview["available"] is True
    assert undo_preview["token"]
    assert undo_preview["review"]["action_id"] == receipt["actionId"]
    assert "undo_token" not in json.dumps(undo_preview, ensure_ascii=False)
    undo_receipt = env.service.undo(undo_preview["token"])
    assert undo_receipt["saved"] is True
    assert undo_receipt["actionType"] == "undo_last_action"
    assert env.service.load()["undo"]["available"] is False

    replay = env.service.confirm(preview["token"])
    assert replay["saved"] is False
    assert replay["errorCode"] == "preview_expired"


def test_stale_proposal_fails_closed_without_committing(env):
    preview = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])
    with patch.object(
        LearningAssistantEngine,
        "_revalidate_version_token",
        side_effect=StaleProposalError("catalog changed"),
    ):
        result = env.service.confirm(preview["token"])

    assert result["saved"] is False
    assert result["errorCode"] == "StaleProposalError"
    assert "catalog changed" not in result["message"]
    assert database.get_counts(env.db_path)["learning_records"] == 0


def test_reject_post_commit_error_reports_saved_and_never_confirms(env):
    preview = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])
    original_transition = LearningAssistantEngine._transition

    def transition_then_fail(engine, entry, state, detail=""):
        original_transition(engine, entry, state, detail)
        if state == "rejected":
            raise RuntimeError("private rejected journal diagnostic")

    with patch.object(LearningAssistantEngine, "_transition", transition_then_fail):
        result = env.service.reject(preview["token"])

    assert result["saved"] is True
    assert result["rejected"] is True
    assert result["refreshWarning"] is True
    assert "private rejected journal diagnostic" not in json.dumps(result)
    assert database.get_counts(env.db_path)["learning_records"] == 0


def test_retry_builds_a_new_private_preview_handle(env):
    first = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])

    retried = env.service.retry(first["proposal"]["action_id"])

    assert retried["token"]
    assert retried["proposal"]["action_id"] != first["proposal"]["action_id"]
    assert retried["proposal"]["action_type"] == "submit_homework"
    assert retried["proposal"]["attachments"][0]["file_name"] == "homework.txt"
    assert retried["confirmationAllowed"] is True
    old_handle = env.service.confirm(first["token"])
    assert old_handle["saved"] is False
    assert old_handle["errorCode"] == "preview_expired"


def test_alert_post_commit_error_is_reconciled_from_persisted_alert_state(env):
    _record_wrong_attempt(env)
    engine = env.service._engine()
    recomputed = engine.recompute_alerts_now()
    assert recomputed.status == "completed"
    alerts = database.list_knowledge_alerts(db_path=env.db_path)
    assert alerts
    alert_id = alerts[0].id
    preview = env.service.preview(f"处理预警{alert_id}", [])
    assert preview["proposal"]["action_type"] == "handle_alert"
    original_action = LearningAssistantEngine._alert_state_action

    def commit_then_fail(action_engine, proposal, kind):
        original_action(action_engine, proposal, kind)
        raise RuntimeError("private alert journal diagnostic")

    with patch.object(LearningAssistantEngine, "_alert_state_action", commit_then_fail):
        receipt = env.service.confirm(preview["token"])

    assert receipt["saved"] is True
    assert receipt["refreshWarning"] is True
    assert receipt["alertId"] == alert_id
    assert "private alert journal diagnostic" not in json.dumps(receipt)
    current = next(item for item in database.list_knowledge_alerts(db_path=env.db_path) if item.id == alert_id)
    assert current.status == "handled"


def test_other_actor_alert_transition_does_not_prove_this_confirmation_saved(env):
    from study_app.core.knowledge_alerts import handle_alert

    _record_wrong_attempt(env)
    assert env.service._engine().recompute_alerts_now().status == "completed"
    alert_id = database.list_knowledge_alerts(db_path=env.db_path)[0].id
    preview = env.service.preview(f"处理预警{alert_id}", [])

    with database.connect(env.db_path) as connection:
        handle_alert(alert_id, TODAY, connection, actor="other_actor")

    with patch.object(
        LearningAssistantEngine,
        "_revalidate_version_token",
        side_effect=StaleProposalError("stale before dispatch"),
    ):
        receipt = env.service.confirm(preview["token"])

    assert receipt["saved"] is False
    assert receipt["errorCode"] == "StaleProposalError"
    events = database.list_knowledge_alert_events(alert_id, env.db_path)
    assert events[-1].actor == "other_actor"
    assert events[-1].to_status == "handled"


def test_unrelated_prior_revoke_does_not_prove_this_undo_saved(env):
    from study_app.data.mastery_replay import revoke_learning_record

    preview = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])
    receipt = env.service.confirm(preview["token"])
    assert receipt["saved"] is True
    undo_preview = env.service.undo_preview()
    assert undo_preview["available"] is True
    revoke_learning_record(
        receipt["recordId"],
        db_path=env.db_path,
        model_path=env.model_path,
        actor="other_actor",
    )

    with patch.object(
        LearningAssistantEngine,
        "undo_last",
        side_effect=RuntimeError("failed before undo dispatch"),
    ):
        result = env.service.undo(undo_preview["token"])

    assert result["saved"] is False
    assert result["errorCode"] == "RuntimeError"


def test_undo_commit_error_is_reconciled_by_exact_undo_revision_actor(env):
    preview = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])
    receipt = env.service.confirm(preview["token"])
    assert receipt["saved"] is True
    undo_preview = env.service.undo_preview()

    with patch.object(
        LearningAssistantEngine,
        "_post_write_recompute",
        side_effect=RuntimeError("post-undo refresh failed"),
    ):
        result = env.service.undo(undo_preview["token"])

    assert result["saved"] is True
    assert result["refreshWarning"] is True
    assert result["actionId"] != receipt["actionId"]
    with database.connect_readonly(env.db_path) as connection:
        row = connection.execute(
            "SELECT actor FROM learning_record_revisions "
            "WHERE record_id = ? AND action = 'revoked' ORDER BY id DESC LIMIT 1",
            (receipt["recordId"],),
        ).fetchone()
    assert row[0] == f"la:undo:{result['actionId']}"


def test_undo_review_stales_when_a_newer_undoable_action_completes(env):
    first = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])
    first_receipt = env.service.confirm(first["token"])
    assert first_receipt["saved"] is True
    old_review = env.service.undo_preview()
    assert old_review["review"]["action_id"] == first_receipt["actionId"]

    second = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])
    second_receipt = env.service.confirm(second["token"])
    assert second_receipt["saved"] is True
    assert second_receipt["actionId"] != first_receipt["actionId"]

    stale = env.service.undo(old_review["token"])
    assert stale["saved"] is False
    assert stale["errorCode"] == "undo_stale"
    with database.connect_readonly(env.db_path) as connection:
        raw_records = [row[0] for row in connection.execute("SELECT raw_json FROM learning_records")]
    active_records = sum(not json.loads(raw).get("_revoked") for raw in raw_records)
    assert active_records == 2


def test_post_commit_engine_error_reports_saved_and_refresh_warning(env):
    preview = env.service.preview(SUBMIT_TEXT, [str(env.attachment)])
    with patch.object(
        LearningAssistantEngine,
        "_post_write_recompute",
        side_effect=RuntimeError("api_key=hidden-after-commit"),
    ):
        result = env.service.confirm(preview["token"])

    assert result["saved"] is True
    assert result["refreshWarning"] is True
    assert result["recordId"] is not None
    assert "hidden-after-commit" not in json.dumps(result, ensure_ascii=False)
    with database.connect_readonly(env.db_path) as connection:
        row = connection.execute(
            "SELECT record_id FROM learning_record_revisions WHERE source_key = ?",
            (f"la:{result['actionId']}",),
        ).fetchone()
    assert row is not None


def test_auto_policy_accepts_only_engine_whitelist(env):
    disabled_preview = env.service.preview("重新计算预警", [])
    assert disabled_preview["autoEligible"] is True
    assert disabled_preview["autoEnabled"] is False

    enabled = env.service.policy("recompute_alerts", True)
    denied = env.service.policy("submit_homework", True)
    enabled_preview = env.service.preview("重新计算预警", [])

    assert enabled["saved"] is True
    assert enabled["enabled"] is True
    assert denied["saved"] is False
    assert enabled_preview["autoEligible"] is True
    assert enabled_preview["autoEnabled"] is True
    assert env.service.load()["policy"]["recompute_alerts"] is True

    env.service.policy("recompute_alerts", False)
    after_policy_change = env.service.confirm(
        enabled_preview["token"], execution_mode="auto"
    )
    assert after_policy_change["saved"] is False
    assert after_policy_change["errorCode"] == "LearningAssistantError"
