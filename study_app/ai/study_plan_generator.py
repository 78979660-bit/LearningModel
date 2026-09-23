from __future__ import annotations
import re

import json
from typing import Any

from study_app.ai.audit import (
    audit_id_of,
    audited_chat_completion_json,
    mark_audit_adopted,
    mark_audit_validated,
    mark_audit_validation_failed,
    summarize_payload,
)
from study_app.ai.validation import (
    LLMValidationError,
    require_object,
    validate_plan_contract,
    validate_plan_output,
)
from study_app.core.active_subjects import subject_reference_violations
from study_app.core.study_phase import ScopeValidationError


PLAN_KEYS = (
    "judgement",
    "goals",
    "short",
    "diagnostic",
    "record_template",
    "expected",
    "evidence",
)

PLAN_LLM_TIMEOUT_SECONDS = 35


class BudgetSuggestionValidationError(LLMValidationError):
    def __init__(self, codes: list[str]):
        self.codes = tuple(dict.fromkeys(codes))
        super().__init__("LLM 预算建议未通过本地硬约束：" + "、".join(self.codes))


def build_budget_suggestion_prompt(local_plan: object) -> str:
    """Build an explanation-only prompt; the local selected set remains authoritative."""
    tasks = [
        {
            "task_id": item.task_id,
            "subject_id": item.subject_id,
            "estimated_minutes": item.estimated_minutes,
            "estimate_source": item.estimate_source,
        }
        for item in local_plan.selected
    ]
    return (
        "只可解释以下本地预算计划。tasks 必须逐项原样返回，不得新增、删除、改学科、改预计分钟或改来源。"
        "输出 JSON：{\"tasks\":[...],\"explanation\":\"...\"}。本地计划："
        + json.dumps(tasks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def validate_llm_budget_suggestion(
    local_plan: object,
    suggestion: object,
    active_subject_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Reject every task mutation; an LLM may only add a textual explanation."""
    codes: list[str] = []
    if not isinstance(suggestion, dict):
        raise BudgetSuggestionValidationError(["invalid_suggestion_shape"])
    raw_tasks = suggestion.get("tasks")
    if not isinstance(raw_tasks, list):
        raise BudgetSuggestionValidationError(["invalid_task_list"])
    active = set(active_subject_ids)
    local = {item.task_id: item for item in local_plan.selected}
    suggested: dict[str, dict[str, Any]] = {}
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            codes.append("invalid_task_shape")
            continue
        task_id = raw.get("task_id")
        subject_id = raw.get("subject_id")
        minutes = raw.get("estimated_minutes")
        source = raw.get("estimate_source")
        if not isinstance(task_id, str) or not task_id:
            codes.append("invalid_task_id")
            continue
        if task_id in suggested:
            codes.append("duplicate_task_id")
            continue
        suggested[task_id] = raw
        if subject_id not in active:
            codes.append("inactive_subject")
        expected = local.get(task_id)
        if expected is None:
            codes.append("unknown_task")
            continue
        if subject_id != expected.subject_id:
            codes.append("subject_changed")
        if type(minutes) is not int or minutes != expected.estimated_minutes:
            codes.append("estimate_changed")
        if source != expected.estimate_source:
            codes.append("estimate_source_changed")
    if set(suggested) != set(local):
        if set(local) - set(suggested):
            codes.append("missing_local_task")
        if set(suggested) - set(local):
            codes.append("added_task")
    completed_minutes = sum(
        item.estimated_minutes or 0 for item in local_plan.completed
    )
    suggested_minutes = sum(
        raw.get("estimated_minutes", 0)
        for task_id, raw in suggested.items()
        if task_id in local and type(raw.get("estimated_minutes")) is int
    )
    available_minutes = (
        local_plan.planned_minutes + local_plan.remaining_minutes
        - local_plan.over_budget_completed_minutes
    )
    if completed_minutes + suggested_minutes > available_minutes:
        codes.append("budget_exceeded")
    explanation = suggestion.get("explanation", "")
    if not isinstance(explanation, str):
        codes.append("invalid_explanation")
    if codes:
        raise BudgetSuggestionValidationError(codes)
    return {
        "tasks": [suggested[task_id] for task_id in sorted(suggested)],
        "explanation": explanation.strip(),
    }

def compact_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    keep_keys = (
        "subject_scope",
        "allowed_subjects",
        "study_phase",
        "mock_exam_readiness",
        "weighted_topic_priorities",
        "memory_risks",
        "bkt_alerts",
        "todos",
        "window",
    )
    compact = {key: payload.get(key) for key in keep_keys if key in payload}
    for key in ("weighted_topic_priorities", "memory_risks", "bkt_alerts", "todos"):
        if isinstance(compact.get(key), list):
            compact[key] = compact[key][:4]
    return _compact_value(compact)


def _compact_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:180]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"raw_json", "source_json", "records", "problem_attempts", "subjects"}:
                continue
            result[str(key)] = _compact_value(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_compact_value(item, depth + 1) for item in value[:6]]
    if isinstance(value, str):
        return value[:500]
    return value


def generate_plan_with_llm(payload: dict[str, Any]) -> dict[str, list[str]]:
    prompt = build_plan_prompt(payload)
    content = audited_chat_completion_json(
        "plan_generation",
        prompt,
        summarize_payload(payload),
        timeout_seconds=PLAN_LLM_TIMEOUT_SECONDS,
    )
    try:
        plan = parse_and_validate_plan(content)
        validate_plan_allowed_subjects(plan, payload)
        validate_plan_exam_scope(plan, payload)
        mark_audit_validated(content)
        mark_audit_adopted(content)
        return plan
    except ScopeValidationError:
        raise
    except Exception as first_error:
        mark_audit_validation_failed(content, first_error)
        repair_prompt = build_plan_repair_prompt(payload, content, first_error)
        repaired_content = audited_chat_completion_json(
            "plan_generation_repair",
            repair_prompt,
            {
                **summarize_payload(payload),
                "repair_reason": {"type": "validation_error"},
            },
            timeout_seconds=PLAN_LLM_TIMEOUT_SECONDS,
            parent_audit_id=audit_id_of(content),
        )
        try:
            plan = parse_and_validate_plan(repaired_content)
            validate_plan_allowed_subjects(plan, payload)
            validate_plan_exam_scope(plan, payload)
        except ScopeValidationError as scope_error:
            mark_audit_validation_failed(repaired_content, scope_error)
            raise
        except Exception as repair_error:
            mark_audit_validation_failed(repaired_content, repair_error)
            raise LLMValidationError(
                f"LLM 首次输出不符合计划契约：{first_error}；"
                f"已要求 LLM 修复一次，但修复后仍不合格：{repair_error}"
            ) from repair_error
        plan.setdefault("evidence", [])
        plan["evidence"].append("生成方式：LLM 首次输出未通过本地校验，已自动修复一次并通过校验。")
        mark_audit_validated(repaired_content)
        mark_audit_adopted(repaired_content)
        return plan


def validate_plan_allowed_subjects(plan: dict[str, list[str]], payload: dict[str, Any]) -> None:
    raw_allowed = payload.get("allowed_subjects")
    if not isinstance(raw_allowed, (list, tuple)):
        raise LLMValidationError("计划输入缺少 allowed_subjects 活动学科白名单。")
    allowed = tuple(str(subject) for subject in raw_allowed if str(subject or "").strip())
    if not allowed:
        raise LLMValidationError("计划输入的 allowed_subjects 活动学科白名单为空。")

    text = "\n".join(line for key in PLAN_KEYS for line in plan.get(key, []))
    violations = subject_reference_violations(
        text,
        allowed_subjects=allowed,
    )
    if violations:
        raise LLMValidationError(
            "计划包含活动学科白名单外内容：" + "、".join(violations)
        )


def validate_plan_exam_scope(plan: dict[str, list[str]], payload: dict[str, Any]) -> None:
    from study_app.core.study_phase import out_of_exam_scope_references

    subject = str(payload.get("subject_scope") or "")
    checked_keys = ("judgement", "goals", "short", "diagnostic", "record_template", "expected")
    text = "\n".join(line for key in checked_keys for line in plan.get(key, []))
    violations = out_of_exam_scope_references(subject, text)
    if violations:
        raise LLMValidationError("计划包含考试范围外内容：" + "、".join(violations[:8]))


def parse_and_validate_plan(content: str) -> dict[str, list[str]]:
    raw = json.loads(content)
    validate_plan_output(raw, PLAN_KEYS)
    plan = normalize_plan(raw)
    from study_app.data.text_integrity import validate_text_integrity

    validate_text_integrity(plan, context="LLM 学习计划")
    validate_plan_contract(plan)
    validate_plan_semantics(plan)
    return plan


def validate_plan_semantics(plan: dict[str, list[str]]) -> None:
    text = "\n".join(line for key in PLAN_KEYS for line in plan.get(key, []))
    invalid_patterns = [
        "远低于窗口分数",
        "低于窗口分数，说明近期表现不稳定",
        "高于掌握度，说明",
        "已学范围掌握度仅",
    ]
    if any(pattern in text for pattern in invalid_patterns):
        raise LLMValidationError(
            "计划错误地比较了不同口径指标：已学范围掌握度不能与窗口分数直接作差并推出表现不稳定。"
        )


def validate_plan_shape(raw: Any, keys: tuple[str, ...]) -> None:
    data = require_object(raw, "单日计划输出")
    missing = [key for key in keys if key not in data]
    if missing:
        raise LLMValidationError("单日计划缺少字段：" + "、".join(missing))
    for key in keys:
        value = data[key]
        if not isinstance(value, list):
            raise LLMValidationError(f"{key} 必须是数组。")
        items = [str(item).strip() for item in value if str(item).strip()]
        max_items = 1 if key == "short" else 8
        if key != "short" and not items:
            raise LLMValidationError(f"{key} 至少需要 1 条。")
        if len(items) > max_items:
            raise LLMValidationError(f"{key} 最多允许 {max_items} 条。")


def _exam_scope_constraints(payload: dict[str, Any]) -> str:
    phase = payload.get("study_phase")
    if not isinstance(phase, dict):
        return ""
    exam_scope = phase.get("exam_scope")
    if not isinstance(exam_scope, dict) or not exam_scope:
        return ""

    label = str(exam_scope.get("label") or "").strip()
    included_topics = [str(item).strip() for item in exam_scope.get("included_topics", []) if str(item).strip()]
    included_keywords = [str(item).strip() for item in exam_scope.get("included_keywords", []) if str(item).strip()]
    excluded_topics = [str(item).strip() for item in exam_scope.get("excluded_topics", []) if str(item).strip()]
    excluded_keywords = [str(item).strip() for item in exam_scope.get("excluded_keywords", []) if str(item).strip()]
    modules = [str(item).strip() for item in exam_scope.get("modules", []) if str(item).strip()]
    included_submodules = [str(item).strip() for item in exam_scope.get("included_submodules", []) if str(item).strip()]
    excluded_submodules = [str(item).strip() for item in exam_scope.get("excluded_submodules", []) if str(item).strip()]

    forbidden = list(dict.fromkeys(excluded_submodules + excluded_topics + excluded_keywords))
    parts = ["考试范围强约束："]
    if label:
        parts.append(f"- 当前只允许安排该考试范围内内容：{label}")
    if modules:
        parts.append(f"- 可用模块：{'、'.join(modules[:10])}")
    if included_submodules:
        parts.append(f"- 只从这些分模块选题：{'、'.join(included_submodules[:16])}")
    if included_topics:
        parts.append(f"- 优先允许知识点：{'、'.join(included_topics[:18])}")
    elif included_keywords:
        parts.append(f"- 允许关键词：{'、'.join(included_keywords[:18])}")
    if forbidden:
        parts.append(f"- 禁止安排这些范围外内容：{'、'.join(forbidden[:18])}")
    parts.extend(
        [
            "- 如果某知识点不在考试范围内，即使遗忘风险高或掌握度低，也不要写入计划。",
            "- 可以在 evidence 中说明范围约束，但不要在 judgement/goals/short/diagnostic/record_template/expected 中安排范围外内容。",
            "- 高等数学期末复习只安排多元函数积分学与级数范围内内容；不要安排傅里叶级数、场论、Dirichlet/Abel 一致收敛判别法；普通数项级数的 Dirichlet/Abel 判别法可以安排。",
        ]
    )
    return "\n".join(parts)


def build_plan_prompt(payload: dict[str, Any]) -> str:
    exam_scope_block = _exam_scope_constraints(payload)
    compact_payload = compact_plan_payload(payload)
    return (
        "IMPORTANT: Return one compact valid JSON object only. No markdown. No prose. No trailing commas. No raw analysis.\n"
        "Each key must contain a string array. judgement/goals/diagnostic/record_template/expected/evidence: exactly 1 short item each. short: exactly 1 item under 900 Chinese characters.\n"
        "你是学习计划软件的单日计划生成器。只输出 JSON object，不要输出解释、Markdown 或代码块。\n"
        "请基于输入数据生成今天的学习计划，计划要具体、可执行、适合直接显示在桌面应用中。\n\n"
        "输出 schema：\n"
        "{\n"
        '  "judgement": ["当前判断"],\n'
        '  "goals": ["今日目标"],\n'
        '  "short": ["回忆自测：...\\n查漏补缺：...\\n专项练习：...\\n当天作业：...\\n完成标准：...\\n复盘记录：...\\n调整依据：..."],\n'
        '  "diagnostic": ["错题诊断要求"],\n'
        '  "record_template": ["推荐记录格式"],\n'
        '  "expected": ["预期模型变化"],\n'
        '  "evidence": ["生成依据"]\n'
        "}\n\n"
        "必须满足：\n"
        "1. 只生成今天，short 必须正好 1 条。\n"
        "2. short 中必须包含：回忆自测、查漏补缺、专项练习、当天作业、完成标准、复盘记录、调整依据。\n"
        "3. 当天作业必须包含“参考难度 x/100”“题库模板 TEMPLATE_ID”“题量”；不要写完整题面。\n"
        "4. 必须优先依据 weighted_topic_priorities 排序安排今日重点；不要忽略无题目级证据但已进入排序的知识点。\n"
        "5. 展示给用户的文字使用自然中文，不要直接输出 window_score、covered_mastery_score、recall、mastery_score 等内部字段名。\n"
        "6. 如果 study_phase.phase = final_review，则按期末复习模式写计划：更重视掌握度缺口、综合题、模拟卷准备。\n"
        "7. 数据结构作业通常不要低于 64/100；AVL、哈希、KMP、复杂度等必须安排可判定的手算、构造或推导任务。\n"
        "8. 如果某知识点刚学且缺少真实做题证据，应先安排诊断或首轮专项，不要直接安排过高难度综合题。\n"
        "9. 禁止把“已学范围掌握度”和“窗口分数”直接作差后推出“表现不稳定”之类结论。\n"
        "10. 所有字段值都必须是字符串数组。\n"
        "11. 计划只能提及 allowed_subjects 中列出的活动学科；不得提及其他学科或其别名。\n\n"
        "12. Output must be compact: judgement/goals/diagnostic/record_template/expected/evidence each use 1-3 short Chinese strings; short must be one string under 1200 Chinese characters.\n"
        "13. Do not include long evidence dumps, raw data, markdown, code fences, or explanations outside JSON.\n"
        "14. Keep JSON compact: each non-short field has 1-3 short strings; short has exactly one string under 1200 Chinese characters.\n"
        "15. Never output markdown, code fences, raw evidence dumps, or text outside JSON.\n"
        + (exam_scope_block + "\n\n" if exam_scope_block else "")
        + "输入数据：\n"
        + json.dumps(compact_payload, ensure_ascii=False, default=str)
    )


def build_plan_repair_prompt(payload: dict[str, Any], bad_content: str, error: Exception) -> str:
    exam_scope_block = _exam_scope_constraints(payload)
    compact_payload = compact_plan_payload(payload)
    return (
        "你刚才生成的今日学习计划 JSON 没有通过本地校验。请修复后重新输出完整 JSON object。\n"
        "这不是新任务，不要改变学习目标，只修复结构、缺失内容和违规内容。\n\n"
        f"失败原因：\n{error}\n\n"
        "修复要求：\n"
        "1. 只输出 JSON object。\n"
        "2. 必须包含 judgement, goals, short, diagnostic, record_template, expected, evidence。\n"
        "3. short 必须正好 1 条。\n"
        "4. short 必须包含：当天作业、完成标准、复盘记录。\n"
        "5. 当天作业必须包含：参考难度 x/100、题库模板 TEMPLATE_ID、题量。\n"
        "6. 所有字段值都必须是字符串数组。\n"
        "7. 如果失败原因提到考试范围外内容，必须彻底删除这些范围外内容，不要换个说法继续保留。\n\n"
        "8. 计划只能提及 allowed_subjects 中的活动学科；不得提及其他学科或其别名。\n\n"
        + (exam_scope_block + "\n\n" if exam_scope_block else "")
        + "上一次不合格输出：\n"
        + "Compact JSON rule: each non-short field has 1-3 short strings; short has exactly one string under 1200 Chinese characters. No markdown or text outside JSON.\n\n"
        + f"{bad_content[:2500]}\n\n"
        + "原始输入数据：\n"
        + json.dumps(compact_payload, ensure_ascii=False, default=str)
    )


def normalize_plan(raw: dict[str, Any]) -> dict[str, list[str]]:
    plan: dict[str, list[str]] = {}
    for key in PLAN_KEYS:
        value = raw.get(key)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
        elif value:
            items = [str(value).strip()]
        else:
            items = []
        plan[key] = items

    plan["short"] = normalize_short_plan(plan["short"])
    for key in PLAN_KEYS:
        if not plan[key]:
            plan[key] = [fallback_line_for_key(key)]
    return plan


def normalize_short_plan(lines: list[str]) -> list[str]:
    normalized = [line for line in lines if line.strip()]
    while len(normalized) < 1:
        normalized.append(
            "今日计划\n"
            "回忆自测：围绕当前最高优先级知识点做 8 分钟无资料回忆。\n"
            "查漏补缺：只补回忆中卡住的定义、公式或方法。\n"
            "专项练习：围绕当前重点做 1 轮针对练习。\n"
            "当天作业：参考难度 60/100；题库模板 TEMPLATE_ID=GEN-MIXED-PRACTICE；题量 2 题。\n"
            "完成标准：至少 1 题独立做对，并记录错误原因。\n"
            "复盘记录：记录正确率、错因和是否需要进入下一轮计划。\n"
            "调整依据：若作业有误，下轮继续保留该知识点。"
        )
    return [ensure_short_day_contract(normalized[0])]


def ensure_short_day_contract(line: str) -> str:
    text = line.strip()
    # Strip legacy "第 N 天：" prefix if present
    text = re.sub(r"^第\s*\d+\s*天[：:]\s*", "", text)
    if "当天作业：" not in text:
        text += "\n当天作业：参考难度 60/100；题库模板 TEMPLATE_ID=GEN-MIXED-PRACTICE；题量 2 题。"
    homework_line = text.split("当天作业：", 1)[1].splitlines()[0]
    if "参考难度" not in homework_line:
        text = text.replace("当天作业：", "当天作业：参考难度 60/100；", 1)
        homework_line = text.split("当天作业：", 1)[1].splitlines()[0]
    if "题库模板" not in homework_line and "TEMPLATE_ID" not in homework_line:
        text = text.replace(homework_line, homework_line + "；题库模板 TEMPLATE_ID=GEN-MIXED-PRACTICE", 1)
        homework_line = text.split("当天作业：", 1)[1].splitlines()[0]
    if "题量" not in homework_line:
        text = text.replace(homework_line, homework_line + "；题量 2 题", 1)
    if "完成标准：" not in text:
        text += "\n完成标准：至少 1 题独立完成，并记录错误原因。"
    if "复盘记录：" not in text:
        text += "\n复盘记录：记录正确率、错因和是否需要进入下一轮计划。"
    if "调整依据：" not in text:
        text += "\n调整依据：根据当天作业结果决定是否保留该知识点。"
    return text


def fallback_line_for_key(key: str) -> str:
    return {
        "judgement": "当前判断：依据最近记录、遗忘风险和知识追踪结果生成。",
        "goals": "今日目标：完成回忆、自测、专项练习和复盘闭环。",
        "diagnostic": "错题诊断要求：记录题目、正确率、错因类型和是否独立完成。",
        "record_template": "记录：完成【知识点】练习【数量】，正确率【x%】，主要错因【...】。",
        "expected": "预期模型变化：完成计划后，相关遗忘风险和知识追踪预警应下降。",
        "evidence": "生成依据：来自当前学习记录、遗忘风险、知识追踪预警和窗口表现。",
    }.get(key, "暂无内容。")
