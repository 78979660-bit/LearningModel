"""Path-injected settings, provider, privacy, and maintenance use cases for QML.

Credentials remain owned by the Python provider/security layer. The QML-facing
snapshot exposes only whether a credential is present; credential entry is a
separate native-dialog operation handled by the bridge.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from study_app.ai.providers import (
    DEFAULT_LLM_SETTINGS,
    LLM_FEATURES,
    PROVIDERS,
    LLMSettings,
    credential_identity,
    load_llm_settings,
    save_llm_settings,
)
from study_app.core.learning_assistant_policy import (
    ADVISOR_MODES,
    load_auto_exec_policy,
    load_privacy_settings,
    save_auto_exec_policy,
    save_privacy_settings,
)
from study_app.core.learning_assistant_schema import ACTION_LABELS, AUTO_ELIGIBLE_ACTIONS
from study_app.data import database
from study_app.data.backup import BACKUP_ROOT, BackupResult, create_backup, integrity_check
from study_app.data.db_runtime import DEFAULT_DB_PATH
from study_app.data.settings_audit_repository import list_llm_call_audits


_CONFIG_SECTIONS = frozenset({"llm", "privacy", "autoExec"})
_LLM_FIELDS = frozenset(
    {
        "enabled",
        "provider",
        "model",
        "customBaseUrl",
        "singleCallTokenLimit",
        "dailyBudgetCny",
        "allowUploadImages",
        "allowUploadPdfs",
        "enabledFeatures",
    }
)
_PRIVACY_FIELDS = frozenset({"advisorMode", "allowExternalIntent"})
_MAX_MODEL_LENGTH = 200
_MAX_BASE_URL_LENGTH = 2048
_MAX_CREDENTIAL_LENGTH = 4096
_TOKEN_LIMIT_MIN = 1000
_TOKEN_LIMIT_MAX = 1_000_000
_BUDGET_MAX_CNY = 1_000_000.0
_NUMBER_TEXT = re.compile(r"(?:0|[1-9]\d*)(?:\.\d{1,2})?\Z")


class SettingsService:
    """Read safe settings and persist reviewed configuration in an isolated DB.

    Constructing this object stores paths only. Database and filesystem access
    starts in ``load``, a save, a credential update, or a maintenance action.
    """

    def __init__(
        self,
        db_path: str | Path,
        model_path: str | Path,
        records_path: str | Path,
        backup_root: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        if backup_root is not None:
            self.backup_root = Path(backup_root)
        elif self.db_path == Path(DEFAULT_DB_PATH):
            self.backup_root = Path(BACKUP_ROOT)
        else:
            # Keep isolated/test profiles self-contained without resolving or
            # touching paths during construction.
            self.backup_root = self.db_path.parent / "backups"
        self._last_backup: dict[str, Any] | None = None
        self._last_integrity: dict[str, Any] | None = None

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} 必须是对象")
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} 字段名必须是文本")
        return value

    @staticmethod
    def _strict_bool(value: object, label: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{label} 必须是布尔值")
        return value

    @staticmethod
    def _text(value: object, label: str, *, maximum: int, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label} 必须是文本")
        result = value.strip()
        if len(result) > maximum:
            raise ValueError(f"{label} 超出长度限制")
        if not allow_empty and not result:
            raise ValueError(f"{label} 不能为空")
        if any(ord(character) < 32 for character in result):
            raise ValueError(f"{label} 不得包含控制字符")
        return result

    @classmethod
    def _safe_custom_url(cls, value: str) -> tuple[str, bool]:
        """Return a displayable endpoint, hiding any URL-embedded secret material."""
        text = value.strip()
        if not text:
            return "", False
        if len(text) > _MAX_BASE_URL_LENGTH:
            return "", True
        try:
            parts = urlsplit(text)
            host = parts.hostname
            _ = parts.port
        except ValueError:
            return "", True
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            return "", True
        path_segments = [segment for segment in unquote(parts.path).split("/") if segment]
        if any(
            len(segment) > 64
            or re.search(r"(?:^|[-_])(?:sk|rk|key|token|secret)[-_][A-Za-z0-9]{8,}$", segment, re.IGNORECASE)
            or (
                len(segment) >= 32
                and any(character.isalpha() for character in segment)
                and any(character.isdigit() for character in segment)
            )
            for segment in path_segments
        ):
            return "", True
        return text.rstrip("/"), False

    @classmethod
    def _validate_custom_url(cls, value: object) -> str:
        text = cls._text(value, "customBaseUrl", maximum=_MAX_BASE_URL_LENGTH, allow_empty=True)
        if not text:
            return ""
        safe, needs_review = cls._safe_custom_url(text)
        if needs_review or not safe:
            raise ValueError("Base URL 只能使用不包含账号、密钥参数或片段的 HTTP(S) 地址")
        return safe

    @classmethod
    def _number(cls, value: object, label: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label} 必须是有限数字")
        if isinstance(value, str):
            text = value.strip()
            if not _NUMBER_TEXT.fullmatch(text):
                raise ValueError(f"{label} 格式不正确")
            number = float(text)
        elif isinstance(value, (int, float)):
            number = float(value)
        else:
            raise ValueError(f"{label} 必须是有限数字")
        if not math.isfinite(number):
            raise ValueError(f"{label} 必须是有限数字")
        return number

    @classmethod
    def _validate_llm_payload(cls, raw: object, current: LLMSettings) -> LLMSettings:
        payload = cls._mapping(raw, "llm")
        unknown = set(payload) - _LLM_FIELDS
        if unknown:
            raise ValueError("LLM 设置包含不允许的字段")
        required = _LLM_FIELDS - {"customBaseUrl"}
        if not required <= set(payload):
            raise ValueError("LLM 设置字段不完整")

        enabled = cls._strict_bool(payload["enabled"], "enabled")
        provider = cls._text(payload["provider"], "provider", maximum=40)
        if provider not in PROVIDERS:
            raise ValueError("所选服务商不受支持")
        model = cls._text(
            payload["model"], "model", maximum=_MAX_MODEL_LENGTH, allow_empty=True
        )
        if not model:
            model = str(PROVIDERS[provider]["default_model"] or "")

        base_url = cls._validate_custom_url(payload.get("customBaseUrl", ""))
        if provider not in {"custom", "anthropic"}:
            base_url = ""
        elif not base_url and provider == current.provider and current.custom_base_url:
            _, needs_review = cls._safe_custom_url(current.custom_base_url)
            if needs_review:
                raise ValueError("当前 Base URL 含有不应放入地址栏的敏感参数；请先输入不含密钥的地址")

        raw_limit = payload["singleCallTokenLimit"]
        limit_number = cls._number(raw_limit, "singleCallTokenLimit")
        if not limit_number.is_integer() or not _TOKEN_LIMIT_MIN <= limit_number <= _TOKEN_LIMIT_MAX:
            raise ValueError(
                f"单次 token 上限必须是 {_TOKEN_LIMIT_MIN}～{_TOKEN_LIMIT_MAX} 的整数"
            )
        budget = cls._number(payload["dailyBudgetCny"], "dailyBudgetCny")
        if budget < 0 or budget > _BUDGET_MAX_CNY:
            raise ValueError(f"每日预算必须在 0～{_BUDGET_MAX_CNY:g} 元之间")

        images = cls._strict_bool(payload["allowUploadImages"], "allowUploadImages")
        pdfs = cls._strict_bool(payload["allowUploadPdfs"], "allowUploadPdfs")
        raw_features = payload["enabledFeatures"]
        if not isinstance(raw_features, (list, tuple)) or any(
            not isinstance(item, str) for item in raw_features
        ):
            raise ValueError("enabledFeatures 必须是功能编号列表")
        if len(set(raw_features)) != len(raw_features) or not set(raw_features) <= set(LLM_FEATURES):
            raise ValueError("enabledFeatures 包含重复或未知功能")
        features = tuple(key for key in LLM_FEATURES if key in set(raw_features))

        return LLMSettings(
            enabled=enabled,
            provider=provider,
            model=model,
            custom_base_url=base_url,
            api_key=(
                current.api_key
                if credential_identity(provider, base_url) == current.credential_identity
                else ""
            ),
            single_call_token_limit=int(limit_number),
            daily_budget_cny=budget,
            allow_upload_images=images,
            allow_upload_pdfs=pdfs,
            enabled_features=features,
        )

    @classmethod
    def _validate_privacy_payload(cls, raw: object, current: Mapping[str, Any]) -> dict[str, Any]:
        payload = cls._mapping(raw, "privacy")
        if set(payload) != _PRIVACY_FIELDS:
            raise ValueError("隐私设置字段不完整或包含未知字段")
        mode = cls._text(payload["advisorMode"], "advisorMode", maximum=30)
        if mode not in ADVISOR_MODES:
            raise ValueError("顾问模式不受支持")
        return {
            "advisor_mode": mode,
            "allow_external_intent": cls._strict_bool(
                payload["allowExternalIntent"], "allowExternalIntent"
            ),
        }

    @classmethod
    def _validate_auto_exec_payload(
        cls, raw: object, current: Mapping[str, bool]
    ) -> dict[str, bool]:
        payload = cls._mapping(raw, "autoExec")
        if not set(payload) <= AUTO_ELIGIBLE_ACTIONS:
            raise ValueError("自动执行设置包含不允许的动作")
        result = dict(current)
        for action, value in payload.items():
            result[action] = cls._strict_bool(value, f"autoExec.{action}")
        return result

    def _audit_dto(self) -> list[dict[str, Any]]:
        try:
            rows = list_llm_call_audits(limit=8, db_path=self.db_path)
        except (sqlite3.Error, OSError, database.DatabaseNotInitializedError):
            return []
        result = []
        for row in rows:
            provider = str(row.get("provider") or "")
            provider_label = str(PROVIDERS.get(provider, {}).get("label") or "未知服务商")
            feature = str(row.get("feature") or "")
            result.append(
                {
                    "id": int(row.get("id") or 0),
                    "createdAt": str(row.get("created_at") or ""),
                    "feature": feature,
                    "featureLabel": LLM_FEATURES.get(feature, "其他功能"),
                    "provider": provider,
                    "providerLabel": provider_label,
                    "model": str(row.get("model") or ""),
                    "status": str(row.get("status") or "unknown"),
                    "estimatedTokens": max(0, int(row.get("estimated_tokens") or 0)),
                }
            )
        return result

    def _llm_dto(self) -> dict[str, Any]:
        settings = load_llm_settings(db_path=self.db_path)
        safe_url, endpoint_needs_review = self._safe_custom_url(settings.custom_base_url)
        raw = database.get_setting(
            "llm_provider", DEFAULT_LLM_SETTINGS, db_path=self.db_path
        )
        protected_saved = bool(
            isinstance(raw, Mapping)
            and (raw.get("api_key_protected") or raw.get("api_key"))
        )
        provider_label = settings.provider_label
        if not settings.enabled or settings.provider == "local":
            status = "当前为仅本地解析，不会上传文本、图片或 PDF。"
        else:
            credential_status = "已配置凭据" if settings.api_key else "尚未配置凭据"
            status = (
                f"已启用 {provider_label}，模型 {settings.model}，{credential_status}；"
                f"单次上限 {settings.single_call_token_limit} tokens，每日预算 "
                f"{settings.daily_budget_cny:.2f} 元。"
            )
        return {
            "enabled": settings.enabled,
            "provider": settings.provider,
            "providerLabel": provider_label,
            "model": settings.model,
            "customBaseUrl": safe_url,
            "baseUrlNeedsReview": endpoint_needs_review,
            "singleCallTokenLimit": settings.single_call_token_limit,
            "dailyBudgetCny": settings.daily_budget_cny,
            "allowUploadImages": settings.allow_upload_images,
            "allowUploadPdfs": settings.allow_upload_pdfs,
            "enabledFeatures": list(settings.enabled_features),
            "hasCredential": bool(settings.api_key),
            "credentialScope": settings.credential_scope,
            "credentialNeedsReview": protected_saved and not bool(settings.api_key),
            "status": status,
            "providers": [
                {
                    "key": key,
                    "label": meta["label"],
                    "defaultModel": meta["default_model"],
                    "customEndpoint": key in {"custom", "anthropic"},
                }
                for key, meta in PROVIDERS.items()
            ],
            "features": [{"key": key, "label": label} for key, label in LLM_FEATURES.items()],
        }

    def load(self) -> dict[str, Any]:
        """Read current safe settings, maintenance state, and bounded audit metadata."""
        llm = self._llm_dto()
        privacy_raw = load_privacy_settings(self.db_path)
        auto_exec = load_auto_exec_policy(self.db_path)
        auto_options = [
            {
                "key": action,
                "label": ACTION_LABELS.get(action, action),
                "enabled": bool(auto_exec.get(action, False)),
            }
            for action in sorted(AUTO_ELIGIBLE_ACTIONS)
        ]
        return {
            "llm": llm,
            "privacy": {
                "advisorMode": privacy_raw["advisor_mode"],
                "allowExternalIntent": privacy_raw["allow_external_intent"],
                "advisorModes": [
                    {"key": "unset", "label": "未配置"},
                    {"key": "mock", "label": "模拟顾问（仅供测试）"},
                    {"key": "external", "label": "外部顾问"},
                ],
            },
            "autoExec": auto_exec,
            "autoExecOptions": auto_options,
            "audits": self._audit_dto(),
            "maintenance": {
                "backupRoot": str(self.backup_root),
                "lastBackup": self._last_backup,
                "lastIntegrity": self._last_integrity,
            },
        }

    def save(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and atomically save provider, privacy, and auto-exec settings."""
        clean_payload = self._mapping(payload, "settings")
        if not set(clean_payload) <= _CONFIG_SECTIONS or not clean_payload:
            raise ValueError("设置包含未知字段或没有可保存项目")

        with database.connect(self.db_path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                # Read settings being preserved or merged only after locking
                # the write transaction, so a concurrent credential update
                # cannot be overwritten by an earlier snapshot.
                current_llm = load_llm_settings(
                    db_path=self.db_path, connection=connection
                )
                current_privacy = load_privacy_settings(
                    self.db_path, connection=connection
                )
                current_auto_exec = load_auto_exec_policy(
                    self.db_path, connection=connection
                )
                llm = (
                    self._validate_llm_payload(clean_payload["llm"], current_llm)
                    if "llm" in clean_payload
                    else None
                )
                privacy = (
                    self._validate_privacy_payload(clean_payload["privacy"], current_privacy)
                    if "privacy" in clean_payload
                    else None
                )
                auto_exec = (
                    self._validate_auto_exec_payload(clean_payload["autoExec"], current_auto_exec)
                    if "autoExec" in clean_payload
                    else None
                )
                if llm is not None:
                    save_llm_settings(llm, self.db_path, connection=connection)
                if privacy is not None:
                    save_privacy_settings(privacy, self.db_path, connection=connection)
                if auto_exec is not None:
                    save_auto_exec_policy(auto_exec, self.db_path, connection=connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        # Do not perform post-commit reads here; the bridge may refresh later,
        # but a committed settings save must never be reported as a failed save.
        return {
            "saved": True,
            "sections": list(clean_payload.keys()),
            "credentialReset": bool(llm is not None and current_llm.api_key and not llm.api_key),
        }

    def set_llm_enabled(self, enabled: bool) -> dict[str, Any]:
        """Change only cloud mode, preserving the latest identity and policies."""
        value = self._strict_bool(enabled, "enabled")
        with database.connect(self.db_path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = load_llm_settings(db_path=self.db_path, connection=connection)
                save_llm_settings(replace(current, enabled=value), self.db_path, connection=connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"saved": True, "enabled": value}

    def set_credential(
        self, provider: str, key: str, *, clear: bool = False,
        expected_scope: str | None = None,
    ) -> dict[str, Any]:
        """Save a credential received only from the bridge's native secure dialog."""
        provider_key = self._text(provider, "provider", maximum=40)
        if provider_key not in PROVIDERS or provider_key == "local":
            raise ValueError("此服务商不接受云端凭据")
        if not isinstance(clear, bool):
            raise ValueError("clear 必须是布尔值")
        if clear:
            if key not in ("", None):
                raise ValueError("清除凭据操作不得附带密钥文本")
            safe_key = ""
        else:
            safe_key = self._text(key, "credential", maximum=_MAX_CREDENTIAL_LENGTH)
        with database.connect(self.db_path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = load_llm_settings(
                    db_path=self.db_path, connection=connection
                )
                if current.provider != provider_key:
                    raise ValueError("当前已保存的服务商已改变，请刷新后重新设置凭据")
                if expected_scope is not None and expected_scope != current.credential_scope:
                    raise ValueError("当前已保存的接口身份已改变，请刷新后重新设置凭据")
                updated = replace(current, api_key=safe_key)
                save_llm_settings(updated, self.db_path, connection=connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "saved": True,
            "provider": provider_key,
            "hasCredential": bool(safe_key),
            "cleared": clear,
        }

    @staticmethod
    def _backup_dto(result: BackupResult) -> dict[str, Any]:
        return {
            "saved": True,
            "folder": str(result.folder),
            "archive": str(result.archive),
            "databaseCopy": result.database_copy.name,
            "recordsExport": "learning_records_export.json",
            "manifest": result.manifest.name,
        }

    def backup(self) -> dict[str, Any]:
        """Create a real SQLite backup and records export in the configured root."""
        result = create_backup(self.db_path, self.backup_root)
        self._last_backup = self._backup_dto(result)
        return dict(self._last_backup)

    def check_integrity(self) -> dict[str, Any]:
        """Run the repository's SQLite, foreign-key, and text-integrity checks."""
        if not self.db_path.is_file():
            raise FileNotFoundError("数据库尚未初始化，检查未运行")
        detail = integrity_check(self.db_path)
        healthy = (
            "SQLite: ok" in detail
            and "外键检查通过" in detail
            and "文本编码检查通过" in detail
        )
        self._last_integrity = {"healthy": healthy, "summary": detail}
        return dict(self._last_integrity)
