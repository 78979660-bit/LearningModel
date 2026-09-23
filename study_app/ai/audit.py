from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from study_app.ai.llm_client import assert_can_call_llm, chat_completion_json
from study_app.ai.providers import estimate_tokens, load_llm_settings
from study_app.data.database import record_llm_call_audit, update_llm_call_audit


LOGGER = logging.getLogger(__name__)

_AUDIT_SUMMARY_FIELDS = frozenset(
    {
        "allowed_subjects",
        "answer_result",
        "attachment_text",
        "attachments",
        "bkt_alerts",
        "date",
        "error_cause",
        "low_subjects",
        "memory_risks",
        "mock_exam_readiness",
        "module",
        "note",
        "question",
        "repair_reason",
        "stale_subjects",
        "study_phase",
        "subject",
        "subject_scope",
        "subjects",
        "today_records",
        "todos",
        "topic",
        "weighted_topic_priorities",
        "window",
        "window_records_count",
    }
)
_AUDIT_SUMMARY_TYPES = frozenset(
    {"bool", "float", "int", "list", "NoneType", "object", "text", "validation_error"}
)
_AUDIT_VERBATIM_FIELDS = frozenset(
    {"action_id", "input_sha256", "output_sha256", "external_data_categories", "cost_estimate_cny"}
)
_SAFE_VERBATIM_RE = re.compile(r"^[0-9a-zA-Z_.:\-]{1,128}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Full lifecycle state machine for assistant LLM calls (contract §10.3).
AUDIT_STATUS_VALUES = (
    "requested",
    "transport_failed",
    "parse_failed",
    "business_invalid",
    "parsed_unvalidated",
    "validated",
    "validation_failed",
    "validated_not_adopted",
    "awaiting_confirmation",
    "rejected",
    "adopted",
    "local_commit_failed",
    "completed",
    "undone",
)


def _sanitize_upload_summary(summary: object) -> dict[str, dict[str, Any]]:
    if not isinstance(summary, dict):
        return {}
    sanitized: dict[str, dict[str, Any]] = {}
    for key in sorted(_AUDIT_VERBATIM_FIELDS):
        if key not in summary:
            continue
        value = summary[key]
        if isinstance(value, dict) and isinstance(value.get("value"), (str, int, float, list)) and len(value) == 2:
            value = value["value"]
        if key == "cost_estimate_cny":
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and 0 <= float(value) <= 10000
            ):
                sanitized[key] = {"type": "number", "value": round(float(value), 6)}
        elif key == "external_data_categories":
            if (
                isinstance(value, list)
                and len(value) <= 8
                and all(
                    isinstance(item, str) and _SAFE_VERBATIM_RE.match(item)
                    for item in value
                )
            ):
                sanitized[key] = {"type": "categories", "value": [str(item) for item in value]}
        else:
            text = value if isinstance(value, str) else ""
            if key in {"input_sha256", "output_sha256"}:
                if _HEX64_RE.match(text):
                    sanitized[key] = {"type": "sha256", "value": text}
            elif isinstance(value, str) and _SAFE_VERBATIM_RE.match(value):
                sanitized[key] = {"type": "id", "value": value}
    for key in _AUDIT_SUMMARY_FIELDS:
        value = summary.get(key)
        if not isinstance(value, dict) or value.get("type") not in _AUDIT_SUMMARY_TYPES:
            continue
        item = {"type": value["type"]}
        metric = "chars" if value["type"] == "text" else "items"
        count = value.get(metric)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            item[metric] = min(count, 1_000_000)
        sanitized[key] = item
    return sanitized


class AuditPersistenceError(RuntimeError):
    """Raised when the required audit trail cannot be persisted."""


class AuditedJSON(str):
    audit_id: int

    def __new__(cls, value: str, audit_id: int):
        instance = super().__new__(cls, value)
        instance.audit_id = audit_id
        return instance


def _record_audit_required(**fields: Any) -> int:
    try:
        fields["upload_summary"] = _sanitize_upload_summary(fields.get("upload_summary"))
        return int(record_llm_call_audit(**fields))
    except Exception as error:
        LOGGER.error("Failed to persist required LLM call audit (%s)", type(error).__name__)
        raise AuditPersistenceError("LLM 审计写入失败，业务结果已阻止。") from error


def _update_audit_required(audit_id: int, status: str, error: Exception | None = None) -> None:
    try:
        update_llm_call_audit(
            audit_id,
            status,
            type(error).__name__ if error is not None else None,
        )
    except Exception as audit_error:
        LOGGER.error("Failed to update required LLM call audit (%s)", type(audit_error).__name__)
        raise AuditPersistenceError("LLM 审计更新失败，业务结果已阻止。") from audit_error


def audit_id_of(value: object) -> int | None:
    audit_id = getattr(value, "audit_id", None)
    return int(audit_id) if isinstance(audit_id, int) else None


def mark_audit_validation_failed(value: object, error: Exception) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "validation_failed", error)


def mark_audit_validated(value: object) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "validated_not_adopted")


def mark_audit_adopted(value: object) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "adopted")


def audited_chat_completion_json(
    feature: str,
    prompt: str,
    upload_summary: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
    parent_audit_id: int | None = None,
) -> AuditedJSON:
    """Call an LLM and persist a compact audit record.

    The audit deliberately stores only metadata and upload summaries, not the
    prompt body or model response.
    """
    settings = load_llm_settings()
    token_estimate = estimate_tokens(prompt)
    summary = upload_summary or {}
    audit_id = _record_audit_required(
        feature=feature,
        provider=settings.provider,
        model=settings.model,
        estimated_tokens=token_estimate,
        upload_summary=summary,
        status="requested",
        parent_audit_id=parent_audit_id,
    )
    try:
        settings = assert_can_call_llm(prompt, settings)
        content = chat_completion_json(prompt, settings, timeout_seconds=timeout_seconds)
    except Exception as error:
        status = "parse_failed" if isinstance(error, json.JSONDecodeError) else "transport_failed"
        _update_audit_required(audit_id, status, error)
        raise

    _update_audit_required(audit_id, "parsed_unvalidated")
    return AuditedJSON(content, audit_id)


def summarize_payload(
    payload: dict[str, Any],
    verbatim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a privacy-conscious summary of payload shape (plus safe verbatim fields)."""
    summary: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in _AUDIT_SUMMARY_FIELDS:
            continue
        if isinstance(value, str):
            summary[key] = {"type": "text", "chars": len(value)}
        elif isinstance(value, list):
            summary[key] = {"type": "list", "items": len(value)}
        elif isinstance(value, dict):
            summary[key] = {"type": "object", "items": len(value)}
        else:
            summary[key] = {"type": type(value).__name__}
    for key in sorted(_AUDIT_VERBATIM_FIELDS):
        if verbatim and key in verbatim:
            wrapped = _wrap_verbatim_field(key, verbatim[key])
            if wrapped is not None:
                summary[key] = wrapped
    return summary


def _wrap_verbatim_field(key: str, value: Any) -> dict[str, Any] | None:
    if key == "cost_estimate_cny":
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0 <= float(value) <= 10000
        ):
            return {"type": "number", "value": round(float(value), 6)}
        return None
    if key == "external_data_categories":
        if (
            isinstance(value, list)
            and len(value) <= 8
            and all(isinstance(item, str) and _SAFE_VERBATIM_RE.match(item) for item in value)
        ):
            return {"type": "categories", "value": [str(item) for item in value]}
        return None
    text = value if isinstance(value, str) else ""
    if key in {"input_sha256", "output_sha256"}:
        return {"type": "sha256", "value": text} if _HEX64_RE.match(text) else None
    return {"type": "id", "value": text} if isinstance(value, str) and _SAFE_VERBATIM_RE.match(value) else None


def assistant_call_summary(
    payload: dict[str, Any] | None = None,
    *,
    action_id: str = "",
    input_sha256: str = "",
    output_sha256: str = "",
    external_data_categories: list[str] | None = None,
    cost_estimate_cny: float = 0.0,
) -> dict[str, Any]:
    """Build an upload summary with the safe verbatim audit fields attached."""
    return summarize_payload(
        payload or {},
        {
            "action_id": action_id,
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
            "external_data_categories": list(external_data_categories or []),
            "cost_estimate_cny": cost_estimate_cny,
        },
    )


def mark_audit_transport_failed(value: object, error: Exception | None = None) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "transport_failed", error)


def mark_audit_business_invalid(value: object, error: Exception | None = None) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "business_invalid", error)


def mark_audit_awaiting_confirmation(value: object) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "awaiting_confirmation")


def mark_audit_rejected(value: object, reason: str | None = None) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        error = ValueError(reason) if reason else None
        _update_audit_required(audit_id, "rejected", error)


def mark_audit_local_commit_failed(value: object, error: Exception | None = None) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "local_commit_failed", error)


def mark_audit_completed(value: object) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "completed")


def mark_audit_undone(value: object) -> None:
    audit_id = audit_id_of(value)
    if audit_id is not None:
        _update_audit_required(audit_id, "undone")
