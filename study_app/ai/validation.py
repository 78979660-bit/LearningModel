from __future__ import annotations

import math
from typing import Any


class LLMValidationError(ValueError):
    pass


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LLMValidationError(f"{label} 必须是 JSON object。")
    return value


def require_string_list(
    value: Any,
    label: str,
    min_items: int = 1,
    max_items: int = 12,
    max_chars: int = 1200,
) -> list[str]:
    if not isinstance(value, list):
        raise LLMValidationError(f"{label} 必须是数组。")
    items = []
    for item_index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise LLMValidationError(f"{label} 第 {item_index} 项必须是字符串。")
        items.append(item.strip())
    if len(items) < min_items:
        raise LLMValidationError(f"{label} 至少需要 {min_items} 条。")
    if len(items) > max_items:
        raise LLMValidationError(f"{label} 最多允许 {max_items} 条。")
    too_long = [item for item in items if len(item) > max_chars]
    if too_long:
        raise LLMValidationError(f"{label} 中存在超过 {max_chars} 字的条目。")
    return items


def require_number_range(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMValidationError(f"{label} 必须是数字。")
    number = float(value)
    if not math.isfinite(number):
        raise LLMValidationError(f"{label} 必须是有限数字。")
    if number < minimum or number > maximum:
        raise LLMValidationError(f"{label} 必须在 {minimum:g}-{maximum:g} 范围内。")
    return number


def validate_plan_output(raw: Any, keys: tuple[str, ...]) -> None:
    data = require_object(raw, "单日计划输出")
    missing = [key for key in keys if key not in data]
    if missing:
        raise LLMValidationError("单日计划缺少字段：" + "、".join(missing))
    for key in keys:
        max_items = 1 if key == "short" else 8
        require_string_list(data[key], key, min_items=1, max_items=max_items, max_chars=2600)


def validate_plan_contract(plan: dict[str, list[str]]) -> None:
    short = require_string_list(plan.get("short"), "short", min_items=1, max_items=1, max_chars=3000)
    for index, line in enumerate(short, start=1):
        required = ["当天作业：", "完成标准：", "复盘记录："]
        missing = [marker for marker in required if marker not in line]
        if missing:
            raise LLMValidationError(f"第 {index} 天计划缺少：" + "、".join(missing))
        homework = line.split("当天作业：", 1)[1].splitlines()[0]
        if "参考难度" not in homework:
            raise LLMValidationError(f"第 {index} 天当天作业缺少参考难度。")
        if "题库模板" not in homework and "TEMPLATE_ID" not in homework:
            raise LLMValidationError(f"第 {index} 天当天作业缺少题库模板。")
        if "题量" not in homework and "题" not in homework:
            raise LLMValidationError(f"第 {index} 天当天作业缺少题量。")
        if not any(marker in homework for marker in ["题", "练习", "自测", "计算", "证明", "手算", "作业", "判断"]):
            raise LLMValidationError(f"第 {index} 天当天作业不像可判定任务：{homework}")


def _validate_summary_contract(
    raw: Any,
    *,
    object_label: str,
    missing_label: str,
    source_max_items: int,
) -> None:
    data = require_object(raw, object_label)
    required = {
        "overview": (1, 3),
        "risks": (1, 5),
        "actions": (1, 6),
        "source": (1, source_max_items),
    }
    missing = [key for key in required if key not in data]
    if missing:
        raise LLMValidationError(f"{missing_label}缺少字段：" + "、".join(missing))
    for key, (minimum, maximum) in required.items():
        value = data[key]
        if isinstance(value, list):
            invalid = [index for index, item in enumerate(value) if not isinstance(item, str)]
            if invalid:
                raise LLMValidationError(f"{key} 的所有条目必须是字符串。")
        require_string_list(value, key, min_items=minimum, max_items=maximum, max_chars=700)


def validate_summary_output(raw: Any) -> None:
    _validate_summary_contract(
        raw,
        object_label="每日总结输出",
        missing_label="每日总结",
        source_max_items=5,
    )


def validate_normalized_summary_cache(raw: Any) -> None:
    _validate_summary_contract(
        raw,
        object_label="规范化每日总结缓存",
        missing_label="每日总结缓存",
        source_max_items=6,
    )


def validate_query_output(raw: Any) -> None:
    data = require_object(raw, "自然语言查询输出")
    required = {
        "answer": (1, 8),
        "evidence": (1, 8),
        "source": (1, 5),
    }
    missing = [key for key in required if key not in data]
    if missing:
        raise LLMValidationError("自然语言查询缺少字段：" + "、".join(missing))
    for key, (minimum, maximum) in required.items():
        require_string_list(data[key], key, min_items=minimum, max_items=maximum, max_chars=900)


def validate_record_parser_output(raw: Any) -> None:
    data = require_object(raw, "新增记录解析输出")
    problems = data.get("problems")
    if not isinstance(problems, list):
        raise LLMValidationError("新增记录解析缺少 problems 数组。")
    if not problems:
        raise LLMValidationError("新增记录解析没有返回可用题目。")
    if len(problems) > 80:
        raise LLMValidationError("新增记录解析题目数量超过 80，疑似误识别。")
    for index, problem in enumerate(problems, start=1):
        item = require_object(problem, f"第 {index} 个题目")
        title = str(item.get("title") or "").strip()
        if not title:
            raise LLMValidationError(f"第 {index} 个题目缺少 title。")
        if len(title) > 120:
            raise LLMValidationError(f"第 {index} 个题目 title 过长。")
        statement = str(item.get("statement") or "")
        if len(statement) > 3000:
            raise LLMValidationError(f"第 {index} 个题目题面超过 3000 字。")
        if item.get("correctness") is not None:
            require_number_range(item.get("correctness"), f"第 {index} 个题目 correctness", 0, 1)
        if item.get("difficulty_score") is not None:
            require_number_range(item.get("difficulty_score"), f"第 {index} 个题目 difficulty_score", 0, 100)
        topics = item.get("related_topics", [])
        if topics is not None:
            if not isinstance(topics, list):
                raise LLMValidationError(f"第 {index} 个题目 related_topics 必须是数组。")
            for topic_index, topic_value in enumerate(topics, start=1):
                if not isinstance(topic_value, str) or not topic_value.strip():
                    raise LLMValidationError(
                        f"第 {index} 个题目 related_topics 第 {topic_index} 项必须是字符串。"
                    )
