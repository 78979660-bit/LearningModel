from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from study_app.ai.providers import LLMSettings, load_llm_settings, save_llm_settings
from study_app.core.learning_assistant_policy import load_auto_exec_policy, load_privacy_settings
from study_app.data import database
from study_app.quick.settings_service import SettingsService


@pytest.mark.parametrize(
    "original_provider,original_url,new_provider,new_url",
    [
        ("deepseek", "", "custom", "https://endpoint-b.example.test/v1"),
        ("custom", "https://endpoint-a.example.test/v1", "custom", "https://endpoint-b.example.test/v1"),
    ],
)
def test_identity_change_blocks_old_key_until_new_credential_is_configured(
    tmp_path, monkeypatch, original_provider, original_url, new_provider, new_url
):
    from study_app.ai import llm_client
    from study_app.ai.audit import audited_chat_completion_json

    service = _service(tmp_path)
    service.save(_valid_payload(provider=original_provider, custom_url=original_url))
    old_key, new_key = "isolated-old-credential", "isolated-new-credential"
    service.set_credential(original_provider, old_key)
    requests = []

    def intercept(url, body, headers, **kwargs):
        requests.append((url, headers))
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(llm_client, "_post_json", intercept)

    def request():
        return audited_chat_completion_json(
            "record_parser", "isolated prompt", {},
            settings=load_llm_settings(db_path=service.db_path), db_path=service.db_path,
        )

    request()
    assert requests[-1][1]["Authorization"] == f"Bearer {old_key}"
    service.save(_valid_payload(provider=new_provider, custom_url=new_url))
    requests.clear()
    with pytest.raises(llm_client.LLMNotConfiguredError, match="API Key"):
        request()
    assert requests == []
    assert not service.load()["llm"]["hasCredential"]
    assert "尚未配置凭据" in service.load()["llm"]["status"]
    raw = database.get_setting("llm_provider", {}, db_path=service.db_path)
    assert raw["api_key"] == raw["api_key_protected"] == ""

    service.set_credential(new_provider, new_key)
    rebound = database.get_setting("llm_provider", {}, db_path=service.db_path)
    assert rebound["api_key_identity"] == [new_provider, new_url]
    request()
    assert requests == [(new_url + "/chat/completions", {"Authorization": f"Bearer {new_key}"})]
    assert old_key not in repr(requests)


@pytest.mark.parametrize(
    "original_provider,new_provider,new_url",
    [
        ("custom", "custom", "https://endpoint-a.example.test/v2"),
        ("custom", "custom", "http://endpoint-a.example.test/v1"),
        ("custom", "custom", "https://endpoint-a.example.test:8443/v1"),
        ("custom", "anthropic", "https://endpoint-a.example.test/v1"),
        ("anthropic", "anthropic", "https://endpoint-b.example.test/v1"),
    ],
)
def test_credential_scope_includes_protocol_scheme_port_and_path(
    tmp_path, original_provider, new_provider, new_url
):
    service = _service(tmp_path)
    service.save(_valid_payload(provider=original_provider, custom_url="https://endpoint-a.example.test/v1"))
    service.set_credential(original_provider, "isolated-key-for-original-scope")
    old_scope = service.load()["llm"]["credentialScope"]
    result = service.save(_valid_payload(provider=new_provider, custom_url=new_url))
    assert result["credentialReset"]
    assert service.load()["llm"]["credentialScope"] != old_scope
    assert not load_llm_settings(db_path=service.db_path).api_key


def _paths(tmp_path: Path):
    model_path = tmp_path / "settings-model.json"
    records_path = tmp_path / "settings-records.json"
    db_path = tmp_path / "settings.sqlite"
    model_path.write_text(
        (Path(__file__).parent / "fixtures" / "minimal_model.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    records_path.write_text('{"records": []}', encoding="utf-8")
    database.ensure_seeded_database(db_path, model_path=model_path)
    return db_path, model_path, records_path


def _service(tmp_path: Path) -> SettingsService:
    db_path, model_path, records_path = _paths(tmp_path)
    return SettingsService(db_path, model_path, records_path)


def _valid_payload(
    *,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    custom_url: str = "",
    budget: str = "12.50",
):
    return {
        "llm": {
            "enabled": True,
            "provider": provider,
            "model": model,
            "customBaseUrl": custom_url,
            "singleCallTokenLimit": "12000",
            "dailyBudgetCny": budget,
            "allowUploadImages": False,
            "allowUploadPdfs": False,
            "enabledFeatures": ["record_parser", "daily_summary"],
        },
        "privacy": {"advisorMode": "mock", "allowExternalIntent": True},
        "autoExec": {"recompute_alerts": True},
    }


def _settings(api_key: str = "") -> LLMSettings:
    return LLMSettings(
        enabled=False,
        provider="local",
        model="",
        custom_base_url="",
        api_key=api_key,
        single_call_token_limit=8000,
        daily_budget_cny=3.0,
        allow_upload_images=False,
        allow_upload_pdfs=False,
        enabled_features=("record_parser",),
    )


def test_constructor_does_not_create_paths_and_isolated_backup_root_is_local(tmp_path):
    missing = tmp_path / "not-created"
    service = SettingsService(missing / "db.sqlite", missing / "model.json", missing / "records.json")

    assert service.backup_root == missing / "backups"
    assert not missing.exists()


def test_load_exposes_safe_provider_metadata_and_only_credential_presence(tmp_path):
    service = _service(tmp_path)
    secret = "sk-protected-test-secret"
    save_llm_settings(
        LLMSettings(
            enabled=True,
            provider="custom",
            model="custom-model",
            custom_base_url="https://user:password@example.test/v1?api_key=query-secret",
            api_key=secret,
            single_call_token_limit=8000,
            daily_budget_cny=3.0,
            allow_upload_images=False,
            allow_upload_pdfs=False,
            enabled_features=("record_parser",),
        ),
        service.db_path,
    )
    database.record_llm_call_audit(
        "record_parser",
        "custom",
        "custom-model",
        22,
        {"private_user_text": {"type": "string"}},
        "provider_failed",
        error_message=f"credential={secret}",
        db_path=service.db_path,
    )

    payload = service.load()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["llm"]["hasCredential"] is True
    assert payload["llm"]["customBaseUrl"] == ""
    assert payload["llm"]["baseUrlNeedsReview"] is True
    assert secret not in serialized
    assert "query-secret" not in serialized and "password" not in serialized
    assert "private_user_text" not in serialized
    assert "error_message" not in serialized and "credential=" not in serialized
    assert payload["audits"][0]["featureLabel"] == "新增记录智能解析"


def test_save_atomically_updates_settings_and_preserves_existing_credential(tmp_path):
    service = _service(tmp_path)
    secret = "sk-keep-this-secret"
    save_llm_settings(replace(_settings(secret), provider="deepseek"), service.db_path)

    result = service.save(_valid_payload())

    assert result["saved"] is True
    assert set(result["sections"]) == {"llm", "privacy", "autoExec"}
    saved_llm = load_llm_settings(db_path=service.db_path)
    assert saved_llm.provider == "deepseek" and saved_llm.model == "deepseek-chat"
    assert saved_llm.api_key == secret
    assert saved_llm.single_call_token_limit == 12000
    assert saved_llm.daily_budget_cny == 12.5
    assert saved_llm.enabled_features == ("record_parser", "daily_summary")
    assert load_privacy_settings(service.db_path) == {
        "advisor_mode": "mock",
        "allow_external_intent": True,
    }
    assert load_auto_exec_policy(service.db_path)["recompute_alerts"] is True
    assert secret not in json.dumps(result)


def test_save_reads_preserved_settings_inside_locked_transaction(tmp_path):
    service = _service(tmp_path)
    secret = "sk-transactionally-preserved"
    save_llm_settings(replace(_settings(secret), provider="deepseek"), service.db_path)
    original_loader = load_llm_settings
    observed = []

    def load_during_transaction(*, db_path, connection=None):
        assert connection is not None
        assert connection.in_transaction
        observed.append(connection)
        return original_loader(db_path=db_path, connection=connection)

    with patch(
        "study_app.quick.settings_service.load_llm_settings",
        side_effect=load_during_transaction,
    ):
        service.save({"llm": _valid_payload()["llm"]})

    assert len(observed) == 1
    assert load_llm_settings(db_path=service.db_path).api_key == secret


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["llm"].update(apiKey="forged-secret"),
        lambda payload: payload["llm"].update(provider="unknown"),
        lambda payload: payload["llm"].update(singleCallTokenLimit="999"),
        lambda payload: payload["llm"].update(dailyBudgetCny="-1"),
        lambda payload: payload["llm"].update(allowUploadImages="false"),
        lambda payload: payload["llm"].update(customBaseUrl="https://user:secret@example.test/v1"),
        lambda payload: payload["privacy"].update(advisorMode="anything"),
        lambda payload: payload["autoExec"].update(recompute_alerts="true"),
        lambda payload: payload.update(unrecognized={}),
    ],
)
def test_save_rejects_unknown_or_invalid_values_before_persisting(tmp_path, mutate):
    service = _service(tmp_path)
    payload = _valid_payload()
    mutate(payload)

    with pytest.raises(ValueError):
        service.save(payload)

    assert load_llm_settings(db_path=service.db_path).provider == "local"
    assert load_privacy_settings(service.db_path)["advisor_mode"] == "unset"
    assert load_auto_exec_policy(service.db_path)["recompute_alerts"] is False


def test_save_rolls_back_all_sections_when_a_later_section_fails(tmp_path):
    service = _service(tmp_path)
    service.save(_valid_payload(provider="deepseek"))
    service.set_credential("deepseek", "isolated-key-kept-on-rollback")
    before_llm = database.get_setting("llm_provider", {}, db_path=service.db_path)
    before_privacy = database.get_setting("learning_assistant_privacy", {}, db_path=service.db_path)
    before_auto = database.get_setting("learning_assistant_auto_exec", {}, db_path=service.db_path)

    with patch("study_app.quick.settings_service.save_auto_exec_policy", side_effect=RuntimeError("injected")):
        with pytest.raises(RuntimeError, match="injected"):
            service.save(_valid_payload(provider="openai", model="gpt-4.1-mini"))

    assert database.get_setting("llm_provider", {}, db_path=service.db_path) == before_llm
    assert database.get_setting("learning_assistant_privacy", {}, db_path=service.db_path) == before_privacy
    assert database.get_setting("learning_assistant_auto_exec", {}, db_path=service.db_path) == before_auto


def test_same_identity_keeps_key_on_model_budget_and_url_format_changes(tmp_path):
    service = _service(tmp_path)
    endpoint = "https://endpoint-a.example.test/v1"
    service.save(_valid_payload(provider="custom", custom_url=endpoint))
    service.set_credential("custom", "isolated-same-endpoint-key")
    scope = service.load()["llm"]["credentialScope"]
    result = service.save(_valid_payload(
        provider="custom", model="another-model", custom_url=f"  {endpoint}///  ", budget="25.00",
    ))
    assert not result["credentialReset"]
    assert load_llm_settings(db_path=service.db_path).api_key == "isolated-same-endpoint-key"
    assert service.load()["llm"]["credentialScope"] == scope


@pytest.mark.parametrize("clear", [False, True])
def test_stale_native_dialog_scope_cannot_write_or_clear_new_endpoint_key(tmp_path, clear):
    service = _service(tmp_path)
    service.save(_valid_payload(provider="custom", custom_url="https://endpoint-a.example.test/v1"))
    old_scope = service.load()["llm"]["credentialScope"]
    service.save(_valid_payload(provider="custom", custom_url="https://endpoint-b.example.test/v1"))
    service.set_credential("custom", "isolated-endpoint-b-key")
    before = database.get_setting("llm_provider", {}, db_path=service.db_path)
    with pytest.raises(ValueError, match="接口身份已改变"):
        service.set_credential(
            "custom", "" if clear else "isolated-stale-dialog-key",
            clear=clear, expected_scope=old_scope,
        )
    assert database.get_setting("llm_provider", {}, db_path=service.db_path) == before
    assert load_llm_settings(db_path=service.db_path).api_key == "isolated-endpoint-b-key"


def test_credential_update_is_separate_provider_checked_and_never_returned(tmp_path):
    service = _service(tmp_path)
    service.save(_valid_payload())
    secret = "sk-native-dialog-only-secret"

    with pytest.raises(ValueError, match="服务商已改变"):
        service.set_credential("openai", "sk-wrong-provider")
    result = service.set_credential("deepseek", secret)

    assert result == {
        "saved": True,
        "provider": "deepseek",
        "hasCredential": True,
        "cleared": False,
    }
    raw = database.get_setting("llm_provider", {}, db_path=service.db_path)
    assert raw["api_key"] == "" and raw["api_key_protected"] != secret
    assert service.load()["llm"]["hasCredential"] is True
    assert secret not in json.dumps(service.load())

    cleared = service.set_credential("deepseek", "", clear=True)
    assert cleared["cleared"] is True and cleared["hasCredential"] is False
    assert load_llm_settings(db_path=service.db_path).api_key == ""
    with pytest.raises(ValueError):
        service.set_credential("deepseek", "not-empty", clear=True)


def test_backup_creates_archive_and_integrity_uses_real_database_checks(tmp_path):
    service = _service(tmp_path)

    backup = service.backup()
    integrity = service.check_integrity()

    assert backup["saved"] is True
    assert Path(backup["archive"]).is_file()
    assert (Path(backup["folder"]) / backup["databaseCopy"]).is_file()
    assert (Path(backup["folder"]) / backup["manifest"]).is_file()
    assert service.backup_root == Path(service.db_path).parent / "backups"
    assert integrity["healthy"] is True
    assert "SQLite: ok" in integrity["summary"]
    assert "外键检查通过" in integrity["summary"]


def test_integrity_does_not_create_missing_database(tmp_path):
    missing = tmp_path / "missing" / "settings.sqlite"
    service = SettingsService(missing, tmp_path / "model.json", tmp_path / "records.json")

    with pytest.raises(FileNotFoundError):
        service.check_integrity()
    assert not missing.parent.exists()
