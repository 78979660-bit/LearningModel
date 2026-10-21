from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from study_app.ai.providers import load_llm_settings
from study_app.core.active_subjects import require_activity_subject
from study_app.core.dashboard import DashboardState
from study_app.core.local_study_plan import (
    apply_dynamic_difficulty_to_plan,
    generate_study_plan,
    study_plan_llm_payload,
)
from study_app.data.database import study_plan_signature
from study_app.data.db_runtime import DEFAULT_DB_PATH


def _injected_path_kwargs(
    db_path: str | Path,
    model_path: str | Path | None,
    records_path: str | Path | None = None,
) -> dict[str, object]:
    if Path(db_path) == Path(DEFAULT_DB_PATH) and model_path is None and records_path is None:
        return {}
    values: dict[str, object] = {"db_path": db_path}
    if model_path is not None:
        values["model_path"] = model_path
    if records_path is not None:
        values["records_path"] = records_path
    return values


@dataclass(frozen=True)
class BudgetSuggestionGuardResult:
    plan: object
    accepted: bool
    diagnostic_codes: tuple[str, ...]
    message: str
    explanation: str = ""


def guard_llm_budget_suggestion(
    local_plan: object,
    suggestion: object,
    active_subject_ids: tuple[str, ...],
) -> BudgetSuggestionGuardResult:
    """Keep the local plan authoritative and never call an LLM or external service."""
    from study_app.ai.study_plan_generator import (
        BudgetSuggestionValidationError,
        validate_llm_budget_suggestion,
    )

    try:
        validated = validate_llm_budget_suggestion(local_plan, suggestion, active_subject_ids)
    except BudgetSuggestionValidationError as error:
        return BudgetSuggestionGuardResult(
            plan=local_plan,
            accepted=False,
            diagnostic_codes=error.codes,
            message="LLM 建议已被本地预算硬约束拒绝；已保留本地计划。",
        )
    return BudgetSuggestionGuardResult(
        plan=local_plan,
        accepted=True,
        diagnostic_codes=(),
        message="LLM 建议未改变本地任务集合；仅采用说明文本。",
        explanation=validated["explanation"],
    )

def should_upgrade_plan_to_llm(saved_plan: dict) -> bool:
    plan_text = _plan_text(saved_plan)
    if "CS-OJ-PRACTICE" in plan_text and "LeetCode" in plan_text:
        return False
    try:
        from study_app.ai.providers import is_llm_feature_enabled
    except Exception:
        return False
    if not is_llm_feature_enabled("plan_generation"):
        return False
    evidence = saved_plan.get("plan", {}).get("evidence", [])
    evidence_text = "\n".join(str(item) for item in evidence)
    return "LLM \u589e\u5f3a\u8ba1\u5212" not in evidence_text and "LLM \u589e\u5f3a" not in evidence_text


def current_study_plan_signature(
    state: DashboardState,
    subject_name: str | None,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    model_path: str | Path | None = None,
    records_path: str | Path | None = None,
) -> str:
    return study_plan_signature(
        {
            "model_version": 16,
            "plan_payload": study_plan_llm_payload(
                state,
                subject_name,
                **_injected_path_kwargs(db_path, model_path),
            ),
        }
    )


def _plan_text(saved_plan: dict) -> str:
    plan = saved_plan.get("plan", {}) or {}
    return "\n".join(
        str(line)
        for lines in plan.values()
        if isinstance(lines, list)
        for line in lines
    )


def should_refresh_plan_for_model(
    saved_plan: dict,
    state: DashboardState | None = None,
    subject_name: str | None = None,
) -> bool:
    if state is not None:
        if str(saved_plan.get("end_date") or "") < state.today.isoformat():
            return True
        if saved_plan.get("input_signature") != current_study_plan_signature(state, subject_name):
            return True

    text = _plan_text(saved_plan)
    if state is not None and subject_name and "暂无真实做题证据" in text:
        if any(phrase in text for phrase in ("上述", "这些知识点", "当前理论主题", "当前主题")):
            if any(
                item.get("subject") == subject_name
                and int(item.get("observation_count") or 0) > 0
                for item in state.weighted_priorities
            ):
                return True
    if subject_name == "计算机科学":
        required_cs_patterns = [
            "OJ 原题训练",
            "LeetCode",
            "CS-OJ-PRACTICE",
        ]
        stale_cs_patterns = [
            "CS-CODING-PRACTICE",
            "编程实践与实现训练",
            "计算机知识的代码实现",
            "GEN-MIXED-PRACTICE",
            "回忆自测：",
            "查漏补缺：",
            "教材或笔记",
            "基础构造题",
        ]
        if not all(pattern in text for pattern in required_cs_patterns):
            return True
        if any(pattern in text for pattern in stale_cs_patterns):
            return True

    stale_patterns = [
        "\u8fdc\u4f4e\u4e8e\u7a97\u53e3\u5206\u6570",
        "\u4f4e\u4e8e\u7a97\u53e3\u5206\u6570",
        "\u8bf4\u660e\u8fd1\u671f\u8868\u73b0\u4e0d\u7a33\u5b9a",
        "window_score",
        "covered_mastery_score",
        "mastery_score",
        "recall=",
    ]
    return any(pattern in text for pattern in stale_patterns)


def llm_plan_unavailable_hint(db_path: str | Path = DEFAULT_DB_PATH) -> str:
    try:
        settings = (
            load_llm_settings()
            if Path(db_path) == Path(DEFAULT_DB_PATH)
            else load_llm_settings(db_path=db_path)
        )
    except Exception:
        return ""
    if not settings.enabled or settings.provider == "local":
        return ""
    if "plan_generation" not in settings.enabled_features:
        return "当前已开启 LLM 增强模式，但设置中尚未勾选“今日计划生成”。因此本次仍显示/生成本地计划。"
    if not settings.api_key:
        return "当前已开启“今日计划生成”，但尚未填写 API Key。"
    return ""


def generate_study_plan_with_optional_llm(
    state: DashboardState,
    subject_name: str | None = None,
    *,
    revision_token=None,
    db_path: str | Path = DEFAULT_DB_PATH,
    model_path: str | Path | None = None,
    records_path: str | Path | None = None,
) -> tuple[dict[str, list[str]], str]:
    def checked(result: tuple[dict[str, list[str]], str]):
        from study_app.core.async_tasks import revalidate_subject_revision

        revalidate_subject_revision(revision_token)
        return result

    if subject_name:
        require_activity_subject(state, subject_name, "生成每日学习计划")
    path_kwargs = _injected_path_kwargs(db_path, model_path, records_path)
    local_path_kwargs = {
        key: value for key, value in path_kwargs.items()
        if key in {"db_path", "model_path"}
    }
    if subject_name == "计算机科学":
        return checked((generate_study_plan(state, subject_name, **local_path_kwargs), "本地 OJ 计划已生成。"))
    from study_app.ai.providers import is_llm_feature_enabled

    enabled = (
        is_llm_feature_enabled("plan_generation")
        if Path(db_path) == Path(DEFAULT_DB_PATH)
        else is_llm_feature_enabled("plan_generation", db_path=db_path)
    )
    if not enabled:
        return checked((generate_study_plan(state, subject_name, **local_path_kwargs), "local"))
    from study_app.ai.study_plan_generator import generate_plan_with_llm
    from study_app.core.study_phase import ScopeValidationError

    payload = study_plan_llm_payload(
        state, subject_name, **_injected_path_kwargs(db_path, model_path)
    )
    try:
        plan_kwargs = {}
        if Path(db_path) != Path(DEFAULT_DB_PATH) or model_path is not None:
            plan_kwargs = {"db_path": db_path, "model_path": model_path}
        plan = generate_plan_with_llm(payload, **plan_kwargs)
    except ScopeValidationError:
        raise
    except Exception as error:
        plan = generate_study_plan(state, subject_name, **local_path_kwargs)
        plan.setdefault("evidence", [])
        error_code = type(error).__name__
        plan["evidence"].append(
            f"LLM 增强计划未通过本地校验（{error_code}），已显示本地计划。"
        )
        return checked((plan, f"已显示本地计划；LLM 增强未通过本地校验（{error_code}）。"))
    priority_topics = tuple(
        str(item.get("topic") or "").strip()
        for item in payload.get("weighted_topic_priorities", ())
        if item.get("topic")
    )
    difficulty_kwargs = {"priority_topics": priority_topics} if priority_topics else {}
    dynamic_kwargs = _injected_path_kwargs(db_path, model_path, records_path)
    if dynamic_kwargs:
        dynamic_kwargs.update(
            model=getattr(state, "model_data", None),
            records=getattr(state, "raw_records", None),
        )
    plan = apply_dynamic_difficulty_to_plan(
        plan,
        subject_name,
        as_of_date=state.today,
        **dynamic_kwargs,
        **difficulty_kwargs,
    )
    plan.setdefault("evidence", [])
    plan["evidence"].append("生成方式：LLM 增强计划，经本地结构校验后保存。")
    plan["evidence"].append("BKT证据口径：题目级匹配已启用同义词、记录级兜底和重复证据去重。")
    return checked((plan, "LLM 增强计划已生成，正在保存。"))
