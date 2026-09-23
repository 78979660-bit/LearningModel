from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from study_app.data.database import get_setting, set_setting
from study_app.security import protect_secret, unprotect_secret


SETTINGS_KEY = "llm_provider"
LOGGER = logging.getLogger(__name__)


PROVIDERS = {
    "local": {
        "label": "仅本地解析",
        "base_url": "",
        "default_model": "",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
    },
    "custom": {
        "label": "OpenAI 兼容接口",
        "base_url": "",
        "default_model": "",
    },
    "anthropic": {
        "label": "Anthropic 兼容接口",
        "base_url": "",
        "default_model": "",
    },
}


LLM_FEATURES = {
    "record_parser": "新增记录智能解析",
    "attachment_parser": "附件题面理解",
    "error_classifier": "错因归类",
    "knowledge_mapping": "知识点自动映射",
    "difficulty_calibration": "题目难度校准",
    "plan_generation": "今日计划生成",
    "daily_summary": "每日学习总结",
    "natural_query": "自然语言查询",
}


DEFAULT_LLM_SETTINGS = {
    "enabled": False,
    "provider": "local",
    "model": "",
    "custom_base_url": "",
    "api_key": "",
    "api_key_protected": "",
    "single_call_token_limit": 8000,
    "daily_budget_cny": 3.0,
    "allow_upload_images": False,
    "allow_upload_pdfs": False,
    "enabled_features": list(LLM_FEATURES.keys()),
    "feature_defaults_version": 3,
}


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool
    provider: str
    model: str
    custom_base_url: str
    api_key: str
    single_call_token_limit: int
    daily_budget_cny: float
    allow_upload_images: bool
    allow_upload_pdfs: bool
    enabled_features: tuple[str, ...]

    @property
    def provider_label(self) -> str:
        return PROVIDERS.get(self.provider, PROVIDERS["local"])["label"]

    @property
    def base_url(self) -> str:
        if self.provider in {"custom", "anthropic"}:
            return self.custom_base_url.strip().rstrip("/")
        return PROVIDERS.get(self.provider, PROVIDERS["local"])["base_url"]

    @property
    def api_protocol(self) -> str:
        if self.provider == "anthropic":
            return "anthropic"
        return "openai"

    @property
    def is_cloud_enabled(self) -> bool:
        return self.enabled and self.provider != "local"

    @property
    def masked_key(self) -> str:
        if not self.api_key:
            return ""
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"


def load_llm_settings() -> LLMSettings:
    raw = dict(get_setting(SETTINGS_KEY, DEFAULT_LLM_SETTINGS) or {})
    legacy_key = str(raw.get("api_key") or "")
    protected_key = str(raw.get("api_key_protected") or "")
    credential_changed = False
    if legacy_key:
        protected_key = protect_secret(legacy_key)
        raw["api_key"] = ""
        raw["api_key_protected"] = protected_key
        credential_changed = True
    if should_upgrade_feature_defaults(raw):
        legacy_features = raw.get("enabled_features")
        enabled_features = (
            ["record_parser"]
            if legacy_features is None
            else [item for item in legacy_features if item in LLM_FEATURES]
        )
        raw = {
            **(raw or {}),
            "enabled_features": enabled_features,
            "feature_defaults_version": 3,
        }
        credential_changed = True
    if credential_changed:
        set_setting(SETTINGS_KEY, raw)
    api_key = legacy_key
    if not api_key and protected_key:
        try:
            api_key = unprotect_secret(protected_key)
        except Exception as error:
            LOGGER.warning("Unable to decrypt saved API credential (%s)", type(error).__name__)
            api_key = ""
    merged = {**DEFAULT_LLM_SETTINGS, **(raw or {})}
    provider = merged.get("provider") if merged.get("provider") in PROVIDERS else "local"
    model = merged.get("model") or PROVIDERS[provider]["default_model"]
    return LLMSettings(
        enabled=bool(merged.get("enabled")),
        provider=provider,
        model=str(model or ""),
        custom_base_url=str(merged.get("custom_base_url") or ""),
        api_key=api_key,
        single_call_token_limit=max(1000, int(float(merged.get("single_call_token_limit") or 8000))),
        daily_budget_cny=max(0.0, float(merged.get("daily_budget_cny") or 0)),
        allow_upload_images=bool(merged.get("allow_upload_images")),
        allow_upload_pdfs=bool(merged.get("allow_upload_pdfs")),
        enabled_features=tuple(
            item for item in (merged.get("enabled_features") or []) if item in LLM_FEATURES
        ),
    )


def should_upgrade_feature_defaults(raw: dict[str, Any] | None) -> bool:
    if not raw:
        return False
    if int(raw.get("feature_defaults_version") or 1) >= 3:
        return False
    legacy_features = raw.get("enabled_features")
    return legacy_features in (None, [], ["record_parser"], ("record_parser",))


def save_llm_settings(settings: LLMSettings | dict[str, Any]) -> None:
    if isinstance(settings, LLMSettings):
        payload = settings.__dict__
    else:
        payload = {**DEFAULT_LLM_SETTINGS, **settings}
    provider = payload.get("provider") if payload.get("provider") in PROVIDERS else "local"
    if not payload.get("model"):
        payload["model"] = PROVIDERS[provider]["default_model"]
    if provider not in {"custom", "anthropic"}:
        payload["custom_base_url"] = ""
    payload["provider"] = provider
    payload["enabled_features"] = [
        item for item in (payload.get("enabled_features") or []) if item in LLM_FEATURES
    ]
    payload["feature_defaults_version"] = 3
    api_key = str(payload.pop("api_key", "") or "")
    payload["api_key"] = ""
    payload["api_key_protected"] = protect_secret(api_key) if api_key else ""
    set_setting(SETTINGS_KEY, payload)


def provider_status(settings: LLMSettings | None = None) -> str:
    settings = settings or load_llm_settings()
    if not settings.enabled or settings.provider == "local":
        return "当前为仅本地解析，不会上传文本、图片或 PDF。"
    if settings.provider in {"custom", "anthropic"} and not settings.base_url:
        return f"{settings.provider_label} 已选择，但尚未填写 Base URL。"
    if not settings.api_key:
        return f"{settings.provider_label} 已选择，但尚未填写 API Key。"
    upload = []
    if settings.allow_upload_images:
        upload.append("图片")
    if settings.allow_upload_pdfs:
        upload.append("PDF")
    upload_text = "，允许上传：" + "、".join(upload) if upload else "，不上传图片/PDF"
    features = "、".join(LLM_FEATURES[item] for item in settings.enabled_features) or "未选择增强功能"
    endpoint = f"，接口 {settings.base_url}" if settings.provider in {"custom", "anthropic"} else ""
    return (
        f"已启用 {settings.provider_label}{endpoint}，模型 {settings.model}，"
        f"单次上限 {settings.single_call_token_limit} tokens，每日预算 {settings.daily_budget_cny:.2f} 元"
        f"{upload_text}；增强功能：{features}。"
    )


def is_llm_feature_enabled(feature: str, settings: LLMSettings | None = None) -> bool:
    settings = settings or load_llm_settings()
    return settings.is_cloud_enabled and feature in settings.enabled_features


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    other = max(0, len(text) - cjk)
    return int(cjk * 1.2 + other / 4) + 8
