from __future__ import annotations

from typing import Any

import math

from study_app.ai.llm_client import parse_record_with_llm
from study_app.ai.providers import LLM_FEATURES, load_llm_settings
from study_app.ai.audit import (
    mark_audit_adopted,
    mark_audit_validated,
    mark_audit_validation_failed,
)
from study_app.ai.validation import LLMValidationError, validate_record_parser_output


ERROR_CATEGORIES = {
    "concept_forgetting",
    "condition_misjudgment",
    "calculation_error",
    "modeling_error",
    "boundary_omission",
    "method_gap",
    "time_management",
    "none",
}

FEATURE_OUTPUT_FIELDS = {
    "error_classifier": ("error_category",),
    "knowledge_mapping": ("related_topics",),
    "difficulty_calibration": ("difficulty_score", "difficulty"),
}


def _finite_or_none(value: Any, label: str) -> float | None:
    """Return a finite float or None when missing; reject bool/NaN/inf (data_contract_v1 §4.2)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMValidationError(f"{label} 必须是数字。")
    number = float(value)
    if not math.isfinite(number):
        raise LLMValidationError(f"{label} 必须是有限数字。")
    return number


def normalize_llm_problem(
    problem: dict[str, Any], enabled_features: frozenset[str] | None = None
) -> dict[str, Any]:
    features = frozenset(LLM_FEATURES) if enabled_features is None else enabled_features
    correctness = _finite_or_none(problem.get("correctness"), "correctness")
    if correctness is not None:
        correctness = max(0.0, min(1.0, correctness))

    difficulty_score = (
        _finite_or_none(problem.get("difficulty_score"), "difficulty_score")
        if "difficulty_calibration" in features else None
    )
    if difficulty_score is not None:
        difficulty_score = max(0.0, min(100.0, difficulty_score))

    item = {
        "title": str(problem.get("title") or "LLM识别题目"),
        "statement": str(problem.get("statement") or ""),
        "statement_source": problem.get("statement_source") or "llm_text",
        "error_cause": str(problem.get("error_cause") or ""),
        "parser_source": "llm",
    }
    if "error_classifier" in features:
        error_category = problem.get("error_category") or "none"
        item["error_category"] = (
            error_category if error_category in ERROR_CATEGORIES else "none"
        )
    if "knowledge_mapping" in features:
        item["related_topics"] = (
            problem.get("related_topics")
            if isinstance(problem.get("related_topics"), list) else []
        )
    if correctness is not None:
        item["partial_credit"] = correctness
        item["correctness"] = correctness * 100
        if correctness >= 0.995:
            item["status"] = "correct"
            item["answer_result"] = "全对"
        elif correctness <= 0.005:
            item["status"] = "wrong"
            item["answer_result"] = "错误"
        else:
            item["status"] = "partial"
            item["answer_result"] = "部分正确"
    if difficulty_score is not None:
        item["difficulty_score"] = difficulty_score
        item["difficulty"] = problem.get("difficulty") or difficulty_label(difficulty_score)
        item["difficulty_source"] = "llm"
        item["difficulty_confidence"] = 0.75
    return item


def difficulty_label(score: float) -> str:
    if score >= 75:
        return "hard"
    if score >= 40:
        return "medium"
    return "easy"


def parse_record_payload_with_llm(payload: dict[str, Any]) -> list[dict[str, Any]]:
    settings = load_llm_settings()
    features = frozenset(settings.enabled_features)
    result = parse_record_with_llm(payload, settings=settings)
    try:
        selected = result
        if isinstance(result, dict) and isinstance(result.get("problems"), list):
            ignored_fields = {
                field
                for feature, fields in FEATURE_OUTPUT_FIELDS.items()
                if feature not in features
                for field in fields
            }
            selected = {
                **result,
                "problems": [
                    {key: value for key, value in problem.items() if key not in ignored_fields}
                    if isinstance(problem, dict) else problem
                    for problem in result["problems"]
                ],
            }
        validate_record_parser_output(selected)
        problems = [
            normalize_llm_problem(problem, features)
            for problem in selected.get("problems", [])
            if isinstance(problem, dict)
        ]
    except Exception as error:
        mark_audit_validation_failed(result, error)
        raise
    mark_audit_validated(result)
    mark_audit_adopted(result)
    return problems
