from __future__ import annotations

import json
from pathlib import Path

import pytest

from study_app.ai.llm_client import LLMNotConfiguredError
from study_app.ai.providers import load_llm_settings
from study_app.data.database import add_learning_record, connect, ensure_seeded_database, get_setting
from study_app.data.settings_audit_repository import list_llm_call_audits
from study_app.quick.daily_summary_service import (
    DailySummaryPermissionError,
    DailySummaryService,
    DailySummaryStaleError,
)
from study_app.quick.settings_service import SettingsService


def _fixture(tmp_path, *, llm_generator=None):
    model_path = tmp_path / "model.json"
    model_path.write_bytes(
        (Path(__file__).parent / "fixtures" / "minimal_model.json").read_bytes()
    )
    records_path = tmp_path / "records.json"
    records_path.write_text('{"records": []}', encoding="utf-8")
    db_path = tmp_path / "isolated.sqlite"
    ensure_seeded_database(db_path, model_path=model_path)
    settings = SettingsService(db_path, model_path, records_path)
    service = DailySummaryService(
        db_path,
        model_path,
        records_path,
        today="2026-10-05",
        settings_service=settings,
        llm_generator=llm_generator,
    )
    return service, settings, db_path, model_path


def _save_cloud_settings(settings, *, token_limit=8000, daily_budget=3.0):
    settings.save(
        {
            "llm": {
                "enabled": True,
                "provider": "deepseek",
                "model": "deepseek-chat",
                "customBaseUrl": "",
                "singleCallTokenLimit": token_limit,
                "dailyBudgetCny": daily_budget,
                "allowUploadImages": False,
                "allowUploadPdfs": False,
                "enabledFeatures": ["daily_summary"],
            }
        }
    )


def _set_test_credential(settings, db_path):
    current = load_llm_settings(db_path=db_path)
    settings.set_credential(
        "deepseek", "qml-test-credential-never-real", expected_scope=current.credential_scope
    )


def _add_record(db_path, model_path, *, note="今日完成了测试知识点练习。"):
    return add_learning_record(
        {
            "date": "2026-10-05",
            "subject": "测试学科",
            "module": "测试模块",
            "topic": "测试知识点",
            "activity": "practice",
            "source": "outside_class",
            "duration_minutes": 20,
            "score": 80,
            "note": note,
        },
        db_path=db_path,
        model_path=model_path,
    )


def _update_record_note(db_path, record_id, value):
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT raw_json FROM learning_records WHERE id = ?", (record_id,)
        ).fetchone()
        raw = json.loads(row["raw_json"])
        raw["note"] = value
        connection.execute(
            "UPDATE learning_records SET note = ?, raw_json = ? WHERE id = ?",
            (value, json.dumps(raw, ensure_ascii=False), record_id),
        )


def test_local_summary_uses_real_isolated_records_and_never_calls_llm(tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("local summary must not invoke an external provider")

    service, _settings, db_path, model_path = _fixture(tmp_path, llm_generator=forbidden)
    before = service.load()
    assert before["mode"] == "local"
    assert before["dataSource"] == "SQLite"
    assert "今天还没有学习记录" in before["summary"]["overview"][0]
    assert not before["canGenerateEnhanced"]

    _add_record(db_path, model_path)
    after = service.load()
    assert after["mode"] == "local"
    assert "今天记录 1 条" in after["summary"]["overview"][0]
    assert after["signature"] != before["signature"]
    assert "测试学科" in after["summary"]["overview"][0]


def test_enhanced_mode_blocks_missing_key_and_toggle_preserves_other_settings(tmp_path):
    service, settings, db_path, _model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    blocked = service.load()
    assert blocked["llmModeEnabled"]
    assert not blocked["canGenerateEnhanced"]
    assert "尚未配置 API Key" in blocked["enhancedStatus"]
    with pytest.raises(DailySummaryPermissionError, match="尚未配置 API Key"):
        service.generate_enhanced()
    assert list_llm_call_audits(limit=10, db_path=db_path) == []

    _set_test_credential(settings, db_path)
    before = load_llm_settings(db_path=db_path)
    service.set_llm_mode(False)
    service.set_llm_mode(True)
    after = load_llm_settings(db_path=db_path)
    assert after.enabled is True
    assert after.api_key == before.api_key == "qml-test-credential-never-real"
    assert after.credential_identity == before.credential_identity
    assert after.enabled_features == before.enabled_features == ("daily_summary",)
    assert after.daily_budget_cny == before.daily_budget_cny
    dto = service.load()
    assert "qml-test-credential-never-real" not in json.dumps(dto, ensure_ascii=False)
    raw_settings = get_setting("llm_provider", {}, db_path=db_path)
    assert raw_settings["api_key"] == ""
    assert "qml-test-credential-never-real" not in json.dumps(raw_settings)


def test_enhanced_summary_uses_isolated_audit_and_no_network(tmp_path, monkeypatch):
    from study_app.ai import audit

    service, settings, db_path, _model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    calls = []

    def intercepted(prompt, llm_settings, timeout_seconds=None):
        calls.append((prompt, llm_settings.api_key, timeout_seconds))
        return json.dumps(
            {
                "overview": ["今天完成了测试学科练习。"],
                "risks": ["继续巩固测试知识点。"],
                "actions": ["明天复习测试知识点。"],
                "source": ["根据今日学习记录。"],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(audit, "chat_completion_json", intercepted)
    result = service.generate_enhanced()
    assert calls and calls[0][1] == "qml-test-credential-never-real"
    assert result["mode"] == "enhanced"
    assert result["cacheHit"]
    assert "qml-test-credential-never-real" not in json.dumps(result, ensure_ascii=False)
    audits = list_llm_call_audits(limit=10, db_path=db_path)
    assert len(audits) == 1
    assert audits[0]["feature"] == "daily_summary"
    assert audits[0]["status"] == "adopted"


def test_postcommit_read_failure_returns_committed_stale_receipt(tmp_path):
    service, settings, db_path, _model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    service._llm_generator = lambda *_args, **_kwargs: {
        "overview": ["今天完成了测试学科练习。"],
        "risks": ["继续巩固测试知识点。"],
        "actions": ["明天复习测试知识点。"],
        "source": ["临时测试。"],
    }
    original_load_state = service._load_state
    calls = {"count": 0}

    def fail_only_postcommit_read():
        calls["count"] += 1
        if calls["count"] == 3:
            raise OSError("synthetic read failure after cache commit")
        return original_load_state()

    service._load_state = fail_only_postcommit_read
    result = service.generate_enhanced()
    cached = get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path)
    assert calls["count"] == 3
    assert cached and cached["mode"] == "llm"
    assert result["mode"] == "enhanced"
    assert result["summary"]["overview"] == ["今天完成了测试学科练习。"]
    assert result["stale"] is True
    assert result["refreshWarning"] is True
    assert result["canGenerateEnhanced"] is False


def test_postcommit_mismatched_read_returns_committed_stale_receipt(tmp_path):
    service, settings, db_path, _model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    service._llm_generator = lambda *_args, **_kwargs: {
        "overview": ["今天完成了测试学科练习。"],
        "risks": ["继续巩固测试知识点。"],
        "actions": ["明天复习测试知识点。"],
        "source": ["临时测试。"],
    }
    service.load = lambda: {
        "date": "2026-10-05",
        "summary": {"overview": ["本地回读"]},
        "mode": "local",
        "signature": "mismatched-signature",
        "cacheHit": False,
    }

    result = service.generate_enhanced()
    cached = get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path)
    assert cached and cached["mode"] == "llm"
    assert result["mode"] == "enhanced"
    assert result["stale"] is True
    assert result["refreshWarning"] is True
    assert result["canGenerateEnhanced"] is False


def test_token_limit_block_is_audited_in_isolated_database_without_request(tmp_path, monkeypatch):
    from study_app.ai import audit

    service, settings, db_path, model_path = _fixture(tmp_path)
    _save_cloud_settings(settings, token_limit=1000, daily_budget=0.0)
    _set_test_credential(settings, db_path)
    _add_record(db_path, model_path, note="x" * 6000)
    requests = []
    monkeypatch.setattr(audit, "chat_completion_json", lambda *a, **k: requests.append((a, k)))

    with pytest.raises(LLMNotConfiguredError):
        service.generate_enhanced()
    assert requests == []
    audits = list_llm_call_audits(limit=10, db_path=db_path)
    assert len(audits) == 1
    assert audits[0]["feature"] == "daily_summary"
    assert audits[0]["status"] == "transport_failed"
    assert audits[0]["estimated_tokens"] > 1000


def test_record_change_during_generation_discards_result_and_does_not_cache(tmp_path):
    service, settings, db_path, model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)

    def changes_data(_state, _records, *, settings, db_path):
        _add_record(db_path, model_path)
        return {
            "overview": ["今天完成了测试学科练习。"],
            "risks": ["继续巩固测试知识点。"],
            "actions": ["明天复习测试知识点。"],
            "source": ["本地测试生成。"],
        }

    service._llm_generator = changes_data
    with pytest.raises(DailySummaryStaleError):
        service.generate_enhanced()
    assert get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path) is None


def test_existing_cache_is_invalidated_when_record_text_changes(tmp_path):
    from study_app.data.database import get_setting

    service, settings, db_path, model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    record_id = _add_record(db_path, model_path)
    service._llm_generator = lambda *_args, **_kwargs: {
        "overview": ["今天完成了测试学科练习。"],
        "risks": ["继续巩固测试知识点。"],
        "actions": ["明天复习测试知识点。"],
        "source": ["基于今天的记录。"],
    }
    generated = service.generate_enhanced()
    assert generated["cacheHit"] and generated["mode"] == "enhanced"
    cached = get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path)
    assert cached["inputFingerprint"]

    _update_record_note(db_path, record_id, "已经修改的学习记录内容。")
    refreshed = service.load()
    assert refreshed["mode"] == "local"
    assert not refreshed["cacheHit"]


def test_edit_to_existing_record_invalidates_cache_even_when_legacy_signature_is_unchanged(tmp_path):
    from study_app.data.database import get_setting
    from study_app.ui.daily_summary_support import daily_summary_cache_signature

    service, settings, db_path, model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    record_id = _add_record(db_path, model_path)
    before_state, _ = service._load_state()
    before_signature = daily_summary_cache_signature(before_state, list(before_state.raw_records))

    def edit_existing_record(_state, _records, *, db_path, **_kwargs):
        _update_record_note(db_path, record_id, "修订后的记录内容。")
        return {
            "overview": ["今天完成了测试学科练习。"],
            "risks": ["继续巩固测试知识点。"],
            "actions": ["明天复习测试知识点。"],
            "source": ["本地测试生成。"],
        }

    service._llm_generator = edit_existing_record
    with pytest.raises(DailySummaryStaleError):
        service.generate_enhanced()
    after_state, _ = service._load_state()
    assert daily_summary_cache_signature(after_state, list(after_state.raw_records)) == before_signature
    assert service._input_fingerprint(after_state, list(after_state.raw_records)) != service._input_fingerprint(
        before_state, list(before_state.raw_records)
    )
    assert get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path) is None
    reloaded = service.load()
    assert reloaded["mode"] == "local"
    assert not reloaded["cacheHit"]


def test_enhanced_cache_is_not_reused_after_endpoint_or_feature_policy_changes(tmp_path):
    service, settings, db_path, _model_path = _fixture(tmp_path)
    _save_cloud_settings(settings)
    _set_test_credential(settings, db_path)
    service._llm_generator = lambda *_args, **_kwargs: {
        "overview": ["隔离测试生成的增强摘要。"],
        "risks": ["合成风险。"],
        "actions": ["合成行动。"],
        "source": ["合成测试记录。"],
    }

    generated = service.generate_enhanced()
    assert generated["mode"] == "enhanced"
    cached = get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path)
    assert cached and cached["mode"] == "llm"
    assert cached["settingsFingerprint"]
    assert "qml-test-credential-never-real" not in json.dumps(cached, ensure_ascii=False)

    settings.save(
        {
            "llm": {
                "enabled": True,
                "provider": "custom",
                "model": "summary-test-model",
                "customBaseUrl": "https://summary-test.example/v1",
                "singleCallTokenLimit": 8000,
                "dailyBudgetCny": 3.0,
                "allowUploadImages": False,
                "allowUploadPdfs": False,
                "enabledFeatures": ["daily_summary"],
            }
        }
    )

    refreshed = service.load()
    assert refreshed["mode"] == "local"
    assert not refreshed["cacheHit"]
    assert "隔离测试生成的增强摘要。" not in json.dumps(refreshed, ensure_ascii=False)
    # An identity-mismatched cached result must be removed, so switching back
    # cannot make another identity's enhanced text reappear later.
    assert get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path) is None

    current = load_llm_settings(db_path=db_path)
    settings.set_credential(
        "custom",
        "qml-test-credential-never-real",
        expected_scope=current.credential_scope,
    )
    service.generate_enhanced()
    cached = get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path)
    assert cached and cached["mode"] == "llm"
    settings.save(
        {
            "llm": {
                "enabled": True,
                "provider": "custom",
                "model": "summary-test-model",
                "customBaseUrl": "https://summary-test.example/v1",
                "singleCallTokenLimit": 8000,
                "dailyBudgetCny": 3.0,
                "allowUploadImages": False,
                "allowUploadPdfs": False,
                "enabledFeatures": [],
            }
        }
    )

    policy_refreshed = service.load()
    assert policy_refreshed["mode"] == "local"
    assert not policy_refreshed["cacheHit"]
    assert get_setting("daily_summary_cache:2026-10-05", None, db_path=db_path) is None
