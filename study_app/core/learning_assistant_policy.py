"""Execution-policy and privacy settings for the learning assistant.

Stored in the existing app_settings key-value table; no schema changes.
Defaults are the safe ones: nothing auto-executes, no advisor configured.
"""
from __future__ import annotations

from typing import Any

from study_app.core.learning_assistant_schema import (
    AUTO_ELIGIBLE_ACTIONS,
    READONLY_ACTIONS,
)
from study_app.data.database import get_setting, set_setting

AUTO_EXEC_SETTINGS_KEY = "learning_assistant_auto_exec"
PRIVACY_SETTINGS_KEY = "learning_assistant_privacy"

ADVISOR_MODES = ("external", "mock", "unset")

_ALLOWED_AUTO_ACTIONS = tuple(sorted(AUTO_ELIGIBLE_ACTIONS))


def load_auto_exec_policy(db_path: Any = None) -> dict[str, bool]:
    raw = get_setting(AUTO_EXEC_SETTINGS_KEY, {}, db_path) if db_path is not None else get_setting(AUTO_EXEC_SETTINGS_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    policy: dict[str, bool] = {action: False for action in _ALLOWED_AUTO_ACTIONS}
    for action in _ALLOWED_AUTO_ACTIONS:
        value = raw.get(action)
        if isinstance(value, bool):
            policy[action] = value
    return policy


def save_auto_exec_policy(policy: dict[str, bool], db_path: Any = None) -> dict[str, bool]:
    if not isinstance(policy, dict):
        raise ValueError("policy 必须是字典")
    unknown = sorted(set(policy) - set(AUTO_ELIGIBLE_ACTIONS))
    if unknown:
        raise ValueError(f"以下动作不允许自动执行：{unknown}")
    clean = {action: bool(policy.get(action, False)) for action in _ALLOWED_AUTO_ACTIONS}
    if db_path is not None:
        set_setting(AUTO_EXEC_SETTINGS_KEY, clean, db_path)
    else:
        set_setting(AUTO_EXEC_SETTINGS_KEY, clean)
    return clean


def can_auto_execute(
    action_type: str,
    policy: dict[str, bool] | None = None,
    db_path: Any = None,
) -> bool:
    if action_type in READONLY_ACTIONS:
        return True
    if action_type not in AUTO_ELIGIBLE_ACTIONS:
        return False
    current = policy if policy is not None else load_auto_exec_policy(db_path)
    return bool(current.get(action_type, False))


def load_privacy_settings(db_path: Any = None) -> dict[str, Any]:
    raw = get_setting(PRIVACY_SETTINGS_KEY, {}, db_path) if db_path is not None else get_setting(PRIVACY_SETTINGS_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    advisor_mode = raw.get("advisor_mode")
    if advisor_mode not in ADVISOR_MODES:
        advisor_mode = "unset"
    allow_intent = raw.get("allow_external_intent")
    return {
        "advisor_mode": advisor_mode,
        "allow_external_intent": bool(allow_intent),
    }


def save_privacy_settings(settings: dict[str, Any], db_path: Any = None) -> dict[str, Any]:
    if not isinstance(settings, dict):
        raise ValueError("settings 必须是字典")
    advisor_mode = settings.get("advisor_mode")
    if advisor_mode not in ADVISOR_MODES:
        raise ValueError(f"advisor_mode 必须是 {ADVISOR_MODES}")
    clean = {
        "advisor_mode": advisor_mode,
        "allow_external_intent": bool(settings.get("allow_external_intent", False)),
    }
    if db_path is not None:
        set_setting(PRIVACY_SETTINGS_KEY, clean, db_path)
    else:
        set_setting(PRIVACY_SETTINGS_KEY, clean)
    return clean
