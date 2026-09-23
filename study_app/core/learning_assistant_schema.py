"""Learning assistant action schema (learning-assistant-action-v1).

Pure data module: no Qt, no database, no network. The model (or the local
deterministic intent parser) may only ever produce proposals in this shape;
everything else is rejected. See GLM53F_assistant_evidence/design/
action_schema_and_permissions.md.
"""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "learning-assistant-action-v1"

ACTION_TYPES: tuple[str, ...] = (
    "answer_query",
    "submit_homework",
    "update_learning_progress",
    "revise_learning_record",
    "undo_last_action",
    "preview_mastery_change",
    "recompute_alerts",
    "explain_alert",
    "handle_alert",
    "snooze_alert",
)

READONLY_ACTIONS = frozenset({"answer_query", "preview_mastery_change", "explain_alert"})
CONFIRM_ACTIONS = frozenset(
    {
        "submit_homework",
        "update_learning_progress",
        "revise_learning_record",
        "handle_alert",
        "snooze_alert",
    }
)
EXPLICIT_CONFIRM_ACTIONS = frozenset({"undo_last_action"})
AUTO_ELIGIBLE_ACTIONS = frozenset({"recompute_alerts"})
# Everything that must never run without an explicit confirmation step.
NEVER_AUTO_ACTIONS = frozenset(ACTION_TYPES) - READONLY_ACTIONS - AUTO_ELIGIBLE_ACTIONS

# A proposal may describe only the entity/operation pair owned by its action
# type.  This is deliberately narrower than the generic JSON shape so a
# tampered journal entry cannot smuggle a second backend operation through a
# legitimate action label.
ACTION_CHANGE_RULES: dict[str, frozenset[tuple[str, str]]] = {
    "answer_query": frozenset({("none", "read")}),
    "submit_homework": frozenset({("learning_record", "insert")}),
    "update_learning_progress": frozenset({("learning_record", "insert")}),
    "revise_learning_record": frozenset({("learning_record", "revise")}),
    "undo_last_action": frozenset({("learning_record", "revoke")}),
    "preview_mastery_change": frozenset({("none", "preview")}),
    "recompute_alerts": frozenset({("knowledge_alert", "recompute")}),
    "explain_alert": frozenset({("none", "read")}),
    "handle_alert": frozenset({("knowledge_alert", "handle")}),
    "snooze_alert": frozenset({("knowledge_alert", "snooze")}),
}

PROPOSAL_MODES = ("preview", "confirmed")

ACTION_LABELS = {
    "answer_query": "只读问答",
    "submit_homework": "登记作业",
    "update_learning_progress": "更新学习进度",
    "revise_learning_record": "修订学习记录",
    "undo_last_action": "撤销上一步",
    "preview_mastery_change": "掌握度影响预览",
    "recompute_alerts": "重算知识预警",
    "explain_alert": "解释预警",
    "handle_alert": "处理预警",
    "snooze_alert": "延后预警",
}

PERMISSION_LABELS = {
    "readonly": "只读，可直接执行",
    "confirm": "预览后确认",
    "explicit": "需要明确确认",
    "auto_configurable": "可配置自动执行",
}


def permission_class(action_type: str) -> str:
    if action_type in READONLY_ACTIONS:
        return "readonly"
    if action_type in EXPLICIT_CONFIRM_ACTIONS:
        return "explicit"
    if action_type in AUTO_ELIGIBLE_ACTIONS:
        return "auto_configurable"
    return "confirm"


class ProposalSchemaError(ValueError):
    """Raised when a raw proposal does not satisfy the frozen schema."""


_ACTION_ID_RE = re.compile(r"^[0-9a-zA-Z_.:\-]{8,64}$")

TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "action_id",
        "action_type",
        "mode",
        "subject_key",
        "user_facts",
        "inferences",
        "attachments",
        "proposed_changes",
        "confidence",
        "requires_confirmation",
        "external_data_disclosure",
        "warnings",
    }
)
REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "action_id",
        "action_type",
        "mode",
        "requires_confirmation",
    }
)


def new_action_id() -> str:
    return f"la-{uuid.uuid4().hex}"


def validate_action_id(value: object) -> str:
    if not isinstance(value, str) or not _ACTION_ID_RE.match(value):
        raise ProposalSchemaError(
            "action_id 必须是 8-64 位 [0-9a-zA-Z_.:-] 的稳定幂等标识"
        )
    return value


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProposalSchemaError(f"{label} 必须是对象")
    return value


def _finite_unit_interval(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProposalSchemaError(f"{label} 必须是数字")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ProposalSchemaError(f"{label} 必须在 0.0-1.0 之间且为有限数")
    return number


@dataclass(frozen=True)
class ActionProposal:
    """One immutable, reviewable proposal; never a fact by itself."""

    schema_version: str
    action_id: str
    action_type: str
    mode: str
    subject_key: str | None = None
    user_facts: dict[str, Any] = None  # type: ignore[assignment]
    inferences: tuple[dict[str, Any], ...] = ()
    attachments: tuple[dict[str, Any], ...] = ()
    proposed_changes: tuple[dict[str, Any], ...] = ()
    confidence: float = 0.0
    requires_confirmation: bool = True
    external_data_disclosure: dict[str, Any] = None  # type: ignore[assignment]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.user_facts is None:
            object.__setattr__(self, "user_facts", {})
        if self.external_data_disclosure is None:
            object.__setattr__(self, "external_data_disclosure", {})

    @property
    def is_readonly(self) -> bool:
        return self.action_type in READONLY_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "mode": self.mode,
            "subject_key": self.subject_key,
            "user_facts": dict(self.user_facts),
            "inferences": [dict(item) for item in self.inferences],
            "attachments": [dict(item) for item in self.attachments],
            "proposed_changes": [dict(item) for item in self.proposed_changes],
            "confidence": self.confidence,
            "requires_confirmation": self.requires_confirmation,
            "external_data_disclosure": dict(self.external_data_disclosure),
            "warnings": list(self.warnings),
        }


def _validate_inference_entry(entry: object, index: int) -> dict[str, Any]:
    item = _require_mapping(entry, f"inferences[{index}]")
    if "field" not in item or not isinstance(item["field"], str) or not item["field"]:
        raise ProposalSchemaError(f"inferences[{index}].field 必须是非空字符串")
    if "value" not in item:
        raise ProposalSchemaError(f"inferences[{index}].value 必须存在")
    basis = item.get("basis")
    if basis is not None and not isinstance(basis, str):
        raise ProposalSchemaError(f"inferences[{index}].basis 必须是字符串")
    extra = set(item) - {"field", "value", "basis"}
    if extra:
        raise ProposalSchemaError(f"inferences[{index}] 存在未定义字段：{sorted(extra)}")
    return dict(item)


def _validate_attachment_entry(entry: object, index: int) -> dict[str, Any]:
    item = _require_mapping(entry, f"attachments[{index}]")
    path = item.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ProposalSchemaError(f"attachments[{index}].path 必须是非空字符串")
    for key in ("file_name", "sha256", "kind", "preflight_status"):
        value = item.get(key)
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ProposalSchemaError(f"attachments[{index}].{key} 类型非法")
    return dict(item)


def _validate_change_entry(entry: object, index: int) -> dict[str, Any]:
    item = _require_mapping(entry, f"proposed_changes[{index}]")
    for key in ("entity", "op", "summary"):
        value = item.get(key)
        if not isinstance(value, str) or not value:
            raise ProposalSchemaError(f"proposed_changes[{index}].{key} 必须是非空字符串")
    fields = item.get("fields", {})
    _require_mapping(fields, f"proposed_changes[{index}].fields")
    extra = set(item) - {"entity", "op", "summary", "fields", "target_id", "problems"}
    if extra:
        raise ProposalSchemaError(
            f"proposed_changes[{index}] 存在未定义字段：{sorted(extra)}"
        )
    problems = item.get("problems")
    if problems is not None:
        if not isinstance(problems, list) or any(
            not isinstance(problem, dict) for problem in problems
        ):
            raise ProposalSchemaError(
                f"proposed_changes[{index}].problems 必须是对象数组"
            )
    return dict(item)


def validate_proposal_dict(raw: object) -> dict[str, Any]:
    """Strictly validate one raw proposal mapping and return a normalized dict."""
    data = _require_mapping(raw, "proposal")
    unknown = sorted(set(data) - TOP_LEVEL_FIELDS)
    if unknown:
        raise ProposalSchemaError(f"提案存在未定义的顶层字段：{unknown}")
    missing = sorted(REQUIRED_FIELDS - set(data))
    if missing:
        raise ProposalSchemaError(f"提案缺少必填字段：{missing}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ProposalSchemaError(
            f"schema_version 不兼容：期望 {SCHEMA_VERSION}，收到 {data['schema_version']!r}"
        )
    validate_action_id(data["action_id"])
    if data["action_type"] not in ACTION_TYPES:
        raise ProposalSchemaError(
            f"未知动作类型：{data['action_type']!r}（白名单外动作一律拒绝）"
        )
    if data["mode"] not in PROPOSAL_MODES:
        raise ProposalSchemaError(f"mode 必须是 {PROPOSAL_MODES}")
    subject_key = data.get("subject_key")
    if subject_key is not None and (
        not isinstance(subject_key, str) or not subject_key.startswith("subject:v1:")
    ):
        raise ProposalSchemaError("subject_key 必须是 subject:v1: 形式的稳定身份或 null")
    _require_mapping(data.get("user_facts", {}), "user_facts")
    raw_inferences = data.get("inferences", [])
    if not isinstance(raw_inferences, list):
        raise ProposalSchemaError("inferences 必须是数组")
    inferences = [
        _validate_inference_entry(entry, index)
        for index, entry in enumerate(raw_inferences)
    ]
    raw_attachments = data.get("attachments", [])
    if not isinstance(raw_attachments, list):
        raise ProposalSchemaError("attachments 必须是数组")
    attachments = [
        _validate_attachment_entry(entry, index)
        for index, entry in enumerate(raw_attachments)
    ]
    raw_changes = data.get("proposed_changes", [])
    if not isinstance(raw_changes, list):
        raise ProposalSchemaError("proposed_changes 必须是数组")
    changes = [
        _validate_change_entry(entry, index)
        for index, entry in enumerate(raw_changes)
    ]
    allowed_changes = ACTION_CHANGE_RULES[data["action_type"]]
    for index, change in enumerate(changes):
        operation = (change["entity"], change["op"])
        if operation not in allowed_changes:
            raise ProposalSchemaError(
                f"proposed_changes[{index}] 的操作 {operation!r} 不属于动作 "
                f"{data['action_type']!r} 的白名单"
            )
    confidence = _finite_unit_interval(data.get("confidence", 0.0), "confidence")
    if not isinstance(data["requires_confirmation"], bool):
        raise ProposalSchemaError("requires_confirmation 必须是布尔值")
    disclosure = _require_mapping(
        data.get("external_data_disclosure", {}), "external_data_disclosure"
    )
    raw_warnings = data.get("warnings", [])
    if not isinstance(raw_warnings, list) or any(
        not isinstance(item, str) for item in raw_warnings
    ):
        raise ProposalSchemaError("warnings 必须是字符串数组")
    if data["action_type"] in READONLY_ACTIONS and data["requires_confirmation"]:
        raise ProposalSchemaError("只读动作不得要求确认")
    if data["action_type"] not in READONLY_ACTIONS and not data["requires_confirmation"]:
        raise ProposalSchemaError("所有非只读动作必须要求确认；自动策略不能改写提案权限")
    return {
        "schema_version": data["schema_version"],
        "action_id": data["action_id"],
        "action_type": data["action_type"],
        "mode": data["mode"],
        "subject_key": subject_key,
        "user_facts": dict(data.get("user_facts") or {}),
        "inferences": inferences,
        "attachments": attachments,
        "proposed_changes": changes,
        "confidence": confidence,
        "requires_confirmation": data["requires_confirmation"],
        "external_data_disclosure": dict(disclosure),
        "warnings": list(raw_warnings),
    }


def proposal_from_dict(raw: object) -> ActionProposal:
    return ActionProposal(**validate_proposal_dict(raw))


def default_disclosure(
    *,
    will_contact_external: bool = False,
    provider: str = "",
    fields_sent: tuple[str, ...] | list[str] = (),
    pdf_or_image_sent: bool = False,
    derived_text_only: bool = False,
    advisor_required: bool = False,
) -> dict[str, Any]:
    return {
        "will_contact_external": bool(will_contact_external),
        "provider": str(provider),
        "fields_sent": list(fields_sent),
        "pdf_or_image_sent": bool(pdf_or_image_sent),
        "derived_text_only": bool(derived_text_only),
        "advisor_required": bool(advisor_required),
    }
