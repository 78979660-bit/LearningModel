"""Path-injected home daily-summary use case.

The old summary algorithms and validation stay in ``study_app.ai.daily_summary``.
This service only supplies an isolated snapshot, enforces explicit cloud
permission, and persists the existing signature-checked cache.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from study_app.ai.providers import LLMSettings, is_llm_feature_enabled, load_llm_settings
from study_app.core.async_tasks import capture_catalog_revision, revalidate_catalog_revision
from study_app.core.dashboard import DashboardState, load_dashboard_state
from study_app.data.database import get_setting, set_setting
from study_app.paths import MODEL_PATH, RECORDS_PATH
from study_app.ui.daily_summary_support import (
    daily_summary_cache_signature,
    matching_daily_summary_cache,
)

LOGGER = logging.getLogger(__name__)
CACHE_KEY_PREFIX = "daily_summary_cache:"


class DailySummaryPermissionError(ValueError):
    """A safe, user-facing reason that blocks an explicit enhanced summary."""


class DailySummaryStaleError(RuntimeError):
    """The records, date, catalog, or provider settings changed during work."""


class DailySummaryService:
    def __init__(
        self,
        db_path,
        model_path=MODEL_PATH,
        records_path=RECORDS_PATH,
        today=None,
        initialize: Callable[[], None] | None = None,
        *,
        settings_service=None,
        llm_settings_loader=None,
        llm_generator=None,
    ) -> None:
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        self.today = today
        self._initialize = initialize
        self._settings_service = settings_service
        self._llm_settings_loader = llm_settings_loader or load_llm_settings
        self._llm_generator = llm_generator

    def ensure_initialized(self) -> None:
        if self._initialize is not None:
            self._initialize()
            self._initialize = None

    def current_date(self) -> str:
        value = self.today
        if value is None:
            return date.today().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)[:10]

    def _load_state(self) -> tuple[DashboardState, object]:
        self.ensure_initialized()
        revision = capture_catalog_revision(self.db_path)
        state = load_dashboard_state(
            model_path=self.model_path,
            records_path=self.records_path,
            db_path=self.db_path,
            today=self.today,
        )
        revalidate_catalog_revision(revision)
        return state, revision

    def _load_settings(self) -> LLMSettings:
        return self._llm_settings_loader(db_path=self.db_path)

    @staticmethod
    def _settings_stamp(settings: LLMSettings) -> tuple[Any, ...]:
        # Never retain or serialize the credential itself. The non-secret
        # credential scope and presence bit are enough to detect a change.
        return (
            settings.enabled,
            settings.provider,
            settings.model,
            settings.credential_scope,
            bool(settings.api_key),
            settings.single_call_token_limit,
            settings.daily_budget_cny,
            settings.allow_upload_images,
            settings.allow_upload_pdfs,
            tuple(settings.enabled_features),
        )

    @classmethod
    def _settings_fingerprint(cls, settings: LLMSettings) -> str:
        stamp = json.dumps(
            cls._settings_stamp(settings),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(stamp.encode("utf-8")).hexdigest()

    @staticmethod
    def _input_fingerprint(state: DashboardState, records: list[dict]) -> str:
        # Keep the legacy cache signature, but also detect edits to existing
        # records while an enhanced summary is running.
        from study_app.ai.daily_summary import build_daily_summary_payload

        payload = build_daily_summary_payload(state, records)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _permission(settings: LLMSettings) -> tuple[bool, str]:
        if not settings.enabled or settings.provider == "local":
            return False, "主页 LLM 增强模式未启用；本地总结仍可使用。"
        if "daily_summary" not in settings.enabled_features:
            return False, "设置中尚未授权每日学习总结使用云端能力。"
        if not settings.api_key:
            return False, "当前服务商尚未配置 API Key。"
        if not settings.base_url or not settings.model:
            return False, "当前云端服务配置不完整，请先检查设置。"
        return True, "已允许显式生成云端每日总结。"

    def _permission_snapshot(self) -> tuple[dict[str, Any], LLMSettings | None]:
        try:
            settings = self._load_settings()
        except Exception as error:
            LOGGER.warning("QML daily summary settings read failed (%s)", type(error).__name__)
            return {
                "llmModeEnabled": False,
                "canGenerateEnhanced": False,
                "enhancedStatus": "云端设置暂不可用；本地总结仍可查看。",
            }, None
        enabled, message = self._permission(settings)
        enabled = enabled and is_llm_feature_enabled(
            "daily_summary", settings=settings, db_path=self.db_path
        )
        return {
            "llmModeEnabled": bool(settings.enabled),
            "canGenerateEnhanced": bool(enabled),
            "enhancedStatus": message,
        }, settings

    @staticmethod
    def _summary_dto(summary: object, api_key: str = "") -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        if not isinstance(summary, dict):
            summary = {}
        for key in ("overview", "risks", "actions", "source"):
            value = summary.get(key, [])
            if isinstance(value, str):
                values = [value]
            elif isinstance(value, (list, tuple)):
                values = value
            else:
                values = []
            safe_lines = []
            for line in values[:8]:
                if not isinstance(line, str):
                    continue
                text = line.strip()[:600]
                if api_key:
                    text = text.replace(api_key, "[凭据已隐藏]")
                if text:
                    safe_lines.append(text)
            result[key] = safe_lines
        return result

    def load(self) -> dict[str, Any]:
        """Read a real local summary and a matching, validated cached result."""
        from study_app.ai.daily_summary import local_daily_summary

        state, _revision = self._load_state()
        records = list(state.raw_records)
        signature = daily_summary_cache_signature(state, records)
        input_fingerprint = self._input_fingerprint(state, records)
        local = self._summary_dto(local_daily_summary(state, records))
        summary, mode = local, "local"
        cache_hit = False
        permission, settings = self._permission_snapshot()
        settings_fingerprint = (
            self._settings_fingerprint(settings) if settings is not None else None
        )
        cache_key = f"{CACHE_KEY_PREFIX}{state.today.isoformat()}"
        try:
            cached = get_setting(cache_key, None, db_path=self.db_path)
            cache_settings_match = (
                not isinstance(cached, dict)
                or cached.get("mode") != "llm"
                or (
                    settings_fingerprint is not None
                    and cached.get("settingsFingerprint") == settings_fingerprint
                )
            )
            fingerprint_matches = (
                not isinstance(cached, dict)
                or not cached.get("inputFingerprint")
                or cached.get("inputFingerprint") == input_fingerprint
            )
            matched = (
                matching_daily_summary_cache(cached, signature, state.subjects)
                if cache_settings_match and fingerprint_matches
                else None
            )
            if matched is not None:
                summary, cached_mode = matched
                summary = self._summary_dto(summary)
                mode = "enhanced" if cached_mode == "llm" else "local"
                cache_hit = True
            elif isinstance(cached, dict) and cached.get("mode") == "llm":
                # Enhanced text belongs to the settings identity/policy that
                # produced it. Remove it when that identity can no longer be
                # confirmed, including legacy cache entries without a stamp.
                try:
                    set_setting(cache_key, None, db_path=self.db_path)
                except Exception as error:
                    LOGGER.warning(
                        "QML daily summary stale enhanced cache cleanup failed (%s)",
                        type(error).__name__,
                    )
        except Exception as error:
            # Cache is an optional optimization; never let it hide real data.
            LOGGER.warning("QML daily summary cache read failed (%s)", type(error).__name__)
        summary = self._summary_dto(summary, settings.api_key if settings else "")
        local = self._summary_dto(local, settings.api_key if settings else "")
        return {
            "date": state.today.isoformat(),
            "summary": summary,
            "localSummary": local,
            "mode": mode,
            "dataSource": "SQLite" if state.data_source == "SQLite" else "兼容记录",
            "signature": signature,
            "cacheHit": cache_hit,
            "stale": False,
            **permission,
        }

    def generate_enhanced(self) -> dict[str, Any]:
        """Generate only after a user action, auditing against this DB path."""
        from study_app.ai.daily_summary import (
            generate_daily_summary_with_llm,
            local_daily_summary,
            validate_cached_daily_summary,
        )

        state, revision = self._load_state()
        records = list(state.raw_records)
        signature = daily_summary_cache_signature(state, records)
        input_fingerprint = self._input_fingerprint(state, records)
        settings = self._load_settings()
        permitted, message = self._permission(settings)
        if not permitted or not is_llm_feature_enabled(
            "daily_summary", settings=settings, db_path=self.db_path
        ):
            raise DailySummaryPermissionError(message)

        generator = self._llm_generator or generate_daily_summary_with_llm
        summary = generator(state, records, settings=settings, db_path=self.db_path)
        validate_cached_daily_summary(summary, state.subjects)
        summary = self._summary_dto(summary, settings.api_key)

        current_settings = self._load_settings()
        current_state, current_revision = self._load_state()
        if (
            state.today.isoformat() != self.current_date()
            or current_state.today != state.today
            or daily_summary_cache_signature(current_state, list(current_state.raw_records)) != signature
            or self._input_fingerprint(current_state, list(current_state.raw_records)) != input_fingerprint
            or self._settings_stamp(current_settings) != self._settings_stamp(settings)
        ):
            raise DailySummaryStaleError("每日总结输入已变化，旧结果不能缓存。")
        revalidate_catalog_revision(revision)
        revalidate_catalog_revision(current_revision)

        allowed, enhanced_status = self._permission(current_settings)
        allowed = allowed and is_llm_feature_enabled(
            "daily_summary", settings=current_settings, db_path=self.db_path
        )
        # Construct the success receipt before the cache write. Nothing after
        # the write may turn a paid, committed result into a failed operation.
        committed_receipt = {
            "date": state.today.isoformat(),
            "summary": summary,
            "localSummary": self._summary_dto(
                local_daily_summary(state, records), current_settings.api_key
            ),
            "mode": "enhanced",
            "dataSource": "SQLite" if state.data_source == "SQLite" else "兼容记录",
            "signature": signature,
            "cacheHit": False,
            "stale": True,
            "refreshWarning": True,
            "llmModeEnabled": bool(current_settings.enabled),
            # A successful explicit reload is required to re-enable another
            # paid generation after a post-commit refresh problem.
            "canGenerateEnhanced": False,
            "enhancedStatus": enhanced_status if allowed else "云端设置已变化，请刷新状态。",
        }

        set_setting(
            f"{CACHE_KEY_PREFIX}{state.today.isoformat()}",
            {
                "mode": "llm",
                "summary": summary,
                "signature": signature,
                "inputFingerprint": input_fingerprint,
                "settingsFingerprint": self._settings_fingerprint(settings),
            },
            db_path=self.db_path,
        )
        # Re-read through the ordinary validator/cache path. That read is
        # post-commit, so errors and drift must return the committed receipt
        # instead of telling the caller to repeat a potentially billable call.
        try:
            refreshed = self.load()
        except Exception as error:
            LOGGER.warning(
                "QML daily summary post-commit refresh failed (%s)",
                type(error).__name__,
            )
            return committed_receipt
        if (
            not isinstance(refreshed, dict)
            or refreshed.get("date") != state.today.isoformat()
            or refreshed.get("signature") != signature
            or refreshed.get("mode") != "enhanced"
            or refreshed.get("cacheHit") is not True
        ):
            return committed_receipt
        return refreshed

    def set_llm_mode(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须是布尔值")
        settings_service = self._settings_service
        if settings_service is None:
            from study_app.quick.settings_service import SettingsService

            settings_service = SettingsService(
                self.db_path, self.model_path, self.records_path
            )
            self._settings_service = settings_service
        # This narrow transaction updates only the mode bit under BEGIN
        # IMMEDIATE and preserves the latest credential identity and policies.
        result = settings_service.set_llm_enabled(enabled)
        return {"saved": bool(result.get("saved")), "enabled": bool(result.get("enabled"))}
