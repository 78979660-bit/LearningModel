from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from study_app.ai import llm_client, providers, record_parser
from study_app.ai.audit import AuditedJSON
from study_app.ai.validation import LLMValidationError


FEATURE_FIELDS = {
    "error_classifier": {"error_category"},
    "knowledge_mapping": {"related_topics"},
    "difficulty_calibration": {"difficulty_score", "difficulty"},
}


def _settings(*features: str) -> providers.LLMSettings:
    return providers.LLMSettings(
        enabled=True,
        provider="deepseek",
        model="deepseek-chat",
        custom_base_url="",
        api_key="synthetic-test-key",
        single_call_token_limit=8000,
        daily_budget_cny=3.0,
        allow_upload_images=False,
        allow_upload_pdfs=False,
        enabled_features=("record_parser", *features),
    )


def test_save_llm_settings_keeps_frozen_input_and_status_intact() -> None:
    settings = _settings("error_classifier")
    original = vars(settings).copy()
    writes = []

    with (
        patch.object(providers, "protect_secret", side_effect=lambda key: f"encrypted:{key}"),
        patch.object(providers, "set_setting", side_effect=lambda _key, value: writes.append(value.copy())),
    ):
        providers.save_llm_settings(settings)
        providers.save_llm_settings(settings)

    assert vars(settings) == original
    assert settings.enabled_features == ("record_parser", "error_classifier")
    assert all(write["api_key"] == "" for write in writes)
    assert all(write["api_key_protected"] == "encrypted:synthetic-test-key" for write in writes)
    assert "尚未填写 API Key" not in providers.provider_status(settings)


def test_save_llm_settings_keeps_dict_input_intact() -> None:
    input_settings = {**vars(_settings()), "enabled_features": ["record_parser"]}
    original = {**input_settings, "enabled_features": list(input_settings["enabled_features"])}
    with (
        patch.object(providers, "protect_secret", return_value="encrypted"),
        patch.object(providers, "set_setting"),
    ):
        providers.save_llm_settings(input_settings)
    assert input_settings == original


@pytest.mark.parametrize("mask", range(8))
def test_prompt_only_requests_selected_optional_fields(mask: int) -> None:
    selected = {
        feature for index, feature in enumerate(FEATURE_FIELDS) if mask & (1 << index)
    }
    prompt = llm_client.build_record_parse_prompt({"note": "复习"}, selected)
    schema = json.loads(prompt.split("输出 JSON schema：\n", 1)[1].split("\n约束：", 1)[0])
    fields = set(schema["problems"][0])
    assert {"title", "statement", "correctness", "error_cause", "statement_source"} <= fields
    for feature, optional_fields in FEATURE_FIELDS.items():
        assert bool(fields & optional_fields) == (feature in selected)


@pytest.mark.parametrize("mask", range(8))
def test_record_result_uses_only_selected_optional_features(monkeypatch, mask: int) -> None:
    selected = {
        feature for index, feature in enumerate(FEATURE_FIELDS) if mask & (1 << index)
    }
    settings = _settings(*sorted(selected))
    model_problem = {
        "title": "第1题",
        "correctness": 0.5,
        "error_cause": "计算错误",
        "error_category": "calculation_error",
        "related_topics": ["二重积分"] if "knowledge_mapping" in selected else [123],
        "difficulty_score": 55 if "difficulty_calibration" in selected else float("nan"),
        "difficulty": "medium",
    }
    monkeypatch.setattr(record_parser, "load_llm_settings", lambda: settings)
    monkeypatch.setattr(
        record_parser,
        "parse_record_with_llm",
        lambda _payload, *, settings: {"problems": [model_problem]},
    )
    monkeypatch.setattr(record_parser, "mark_audit_validated", lambda _result: None)
    monkeypatch.setattr(record_parser, "mark_audit_adopted", lambda _result: None)

    [problem] = record_parser.parse_record_payload_with_llm({"note": "复习"})

    assert problem["correctness"] == 50
    assert problem["status"] == "partial"
    for feature, optional_fields in FEATURE_FIELDS.items():
        assert bool(set(problem) & optional_fields) == (feature in selected)
    assert (problem.get("difficulty_source") == "llm") == ("difficulty_calibration" in selected)


@pytest.mark.parametrize(
    ("feature", "bad_field", "bad_value"),
    [
        ("knowledge_mapping", "related_topics", [123]),
        ("difficulty_calibration", "difficulty_score", float("nan")),
    ],
)
def test_enabled_optional_feature_still_validates_its_output(
    monkeypatch, feature: str, bad_field: str, bad_value: object
) -> None:
    settings = _settings(feature)
    failures = []
    monkeypatch.setattr(record_parser, "load_llm_settings", lambda: settings)
    monkeypatch.setattr(
        record_parser,
        "parse_record_with_llm",
        lambda _payload, *, settings: {"problems": [{"title": "第1题", bad_field: bad_value}]},
    )
    monkeypatch.setattr(record_parser, "mark_audit_validation_failed", lambda _result, error: failures.append(error))
    with pytest.raises(LLMValidationError):
        record_parser.parse_record_payload_with_llm({"note": "复习"})
    assert len(failures) == 1


def test_live_record_flow_uses_same_settings_for_prompt_and_result(monkeypatch) -> None:
    settings = _settings("knowledge_mapping")
    prompts = []
    audited_settings = []
    monkeypatch.setattr(record_parser, "load_llm_settings", lambda: settings)

    def audited_record_call(_feature, prompt, _summary, *, settings):
        prompts.append(prompt)
        audited_settings.append(settings)
        return AuditedJSON(
            json.dumps({"problems": [{
                "title": "第1题",
                "error_category": "calculation_error",
                "related_topics": ["积分"],
                "difficulty_score": 88,
            }]}),
            1,
        )

    monkeypatch.setattr(
        "study_app.ai.audit.audited_chat_completion_json",
        audited_record_call,
    )
    monkeypatch.setattr(record_parser, "mark_audit_validated", lambda _result: None)
    monkeypatch.setattr(record_parser, "mark_audit_adopted", lambda _result: None)

    [problem] = record_parser.parse_record_payload_with_llm({"note": "复习"})

    assert "related_topics" in prompts[0]
    assert audited_settings == [settings]
    assert "error_category" not in prompts[0]
    assert "difficulty_score" not in prompts[0]
    assert problem["related_topics"] == ["积分"]
    assert "error_category" not in problem
    assert "difficulty_score" not in problem


def test_audited_record_call_uses_supplied_settings_snapshot(monkeypatch) -> None:
    from study_app.ai import audit

    settings = _settings("knowledge_mapping")
    recorded = []
    used_settings = []

    def unexpected_reload():
        raise AssertionError("the record call must keep its settings snapshot")

    monkeypatch.setattr(audit, "load_llm_settings", unexpected_reload)
    monkeypatch.setattr(audit, "_record_audit_required", lambda **fields: recorded.append(fields) or 7)
    monkeypatch.setattr(audit, "_update_audit_required", lambda *_args: None)
    monkeypatch.setattr(audit, "assert_can_call_llm", lambda _prompt, value: value)
    monkeypatch.setattr(
        audit,
        "chat_completion_json",
        lambda _prompt, value, timeout_seconds=None: used_settings.append(value) or "{}",
    )

    result = audit.audited_chat_completion_json(
        "record_parser", "prompt", settings=settings
    )

    assert result.audit_id == 7
    assert recorded[0]["provider"] == settings.provider
    assert used_settings == [settings]
