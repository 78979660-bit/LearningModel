from __future__ import annotations

import json
from typing import Any

from study_app.ai.audit import (
    audited_chat_completion_json,
    mark_audit_adopted,
    mark_audit_validated,
    mark_audit_validation_failed,
    summarize_payload,
)
from study_app.ai.validation import (
    LLMValidationError,
    validate_normalized_summary_cache,
    validate_summary_output,
)
from study_app.core.active_subjects import (
    active_subject_names,
    active_subjects,
    archived_subject_names,
    subject_reference_violations,
)


def build_daily_summary_payload(state, records: list[dict[str, Any]]) -> dict[str, Any]:
    today = state.today.isoformat()
    current_subjects = active_subjects(state.subjects)
    active_names = {subject.name for subject in current_subjects}
    active_records = [record for record in records if record.get("subject") in active_names]
    today_records = [record for record in active_records if str(record.get("date") or record.get("record_date")) == today]
    window_records = [
        record for record in active_records
        if state.start.isoformat() <= str(record.get("date") or record.get("record_date")) <= today
    ]
    current_todos = [
        item for item in state.todos
        if any(item.title.startswith(f"{name} /") for name in active_names)
    ][:8]
    return {
        "date": today,
        "today_records_count": len(today_records),
        "memory_risks_count": sum(item.get("subject") in active_names for item in state.memory_risks),
        "bkt_alerts_count": sum(item.get("subject") in active_names for item in state.bkt_alerts),
        "window": {
            "start": state.start.isoformat(),
            "end": today,
            "benchmark": state.benchmark,
        },
        "allowed_subjects": list(active_subject_names(state.subjects)),
        "today_records": shrink_records(today_records[-12:]),
        "window_records_count": len(window_records),
        "subjects": [
            {
                "name": subject.name,
                "window_score": subject.window_score,
                "covered_mastery_score": getattr(subject, "covered_mastery_score", None),
                "covered_topic_count": getattr(subject, "covered_topic_count", None),
                "total_topic_count": getattr(subject, "total_topic_count", None),
                "mastery_score": subject.mastery_score,
                "warnings": list(subject.warnings),
            }
            for subject in current_subjects
        ],
        "todos": [
            {
                "kind": item.kind,
                "title": item.title,
                "detail": item.detail,
                "level": item.level,
                "priority": item.priority,
            }
            for item in current_todos
        ],
        "memory_risks": [shrink_risk(item) for item in state.memory_risks if item.get("subject") in active_names][:8],
        "bkt_alerts": [shrink_risk(item) for item in state.bkt_alerts if item.get("subject") in active_names][:8],
        "low_subjects": [name for name in state.low_subjects if name in active_names],
        "stale_subjects": [subject.name for subject in state.stale_subjects if subject.name in active_names],
    }


def local_daily_summary(state, records: list[dict[str, Any]]) -> dict[str, list[str]]:
    payload = build_daily_summary_payload(state, records)
    today_records = payload["today_records"]
    overview = []
    if today_records:
        subjects = sorted({record.get("subject") or "未命名学科" for record in today_records})
        overview.append(f"今天记录 {payload['today_records_count']} 条，涉及 {'、'.join(subjects)}。")
    else:
        overview.append("今天还没有学习记录，当前总结主要依据三天窗口和预警状态。")
    overview.append(
        f"三天窗口共有 {payload['window_records_count']} 条记录；"
        f"低于标杆学科 {len(payload['low_subjects'])} 个，当前全部活动学科遗忘风险 {payload['memory_risks_count']} 项，需要巩固 {payload['bkt_alerts_count']} 项（BKT）；下方仅列重点示例。"
    )

    risks = []
    for item in payload["memory_risks"][:3]:
        recall = item.get("recall")
        if isinstance(recall, (int, float)):
            risks.append(f"{item.get('subject')} / {item.get('topic')}：回忆 {recall:.0%}，需要优先复习。")
    for item in payload["bkt_alerts"][:2]:
        mastery = item.get("mastery_probability")
        if isinstance(mastery, (int, float)):
            risks.append(f"{item.get('subject')} / {item.get('topic')}：BKT 掌握概率 {mastery:.0%}，建议补题。")
    if not risks:
        risks.append("当前没有强烈的遗忘或 BKT 预警，重点是保持记录连续性。")

    actions = []
    for item in payload["todos"][:3]:
        actions.append(f"[{item['kind']}] {item['title']}：{item['detail']}")
    if not actions:
        actions.append("补充一条今日学习记录，给模型新的判断依据。")

    return {
        "overview": overview,
        "risks": risks,
        "actions": actions,
        "source": ["生成方式：本地规则总结。"],
    }


def generate_daily_summary_with_llm(state, records: list[dict[str, Any]]) -> dict[str, list[str]]:
    payload = build_daily_summary_payload(state, records)
    prompt = (
        "你是学习模型桌面应用的每日总结器。请只输出 JSON，不要解释。\n"
        "根据今天记录、三天窗口分数、遗忘风险和 BKT 预警，生成高信号学习总结。\n"
        "输出 schema：\n"
        "{\n"
        '  "overview": ["今日概况，1-3条"],\n'
        '  "risks": ["最需要警惕的科目/知识点，1-4条"],\n'
        '  "actions": ["下一步建议，2-5条"],\n'
        '  "source": ["生成依据"]\n'
        "}\n"
        "要求：不要空泛鼓励；建议必须指向具体学科或知识点；不要编造输入中没有的学习记录。\n"
        "所有可显示文本只能提及 allowed_subjects 中的活动学科，不得提及其他课程名称或别名。\n\n"
        "输入：\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )
    content = audited_chat_completion_json(
        "daily_summary",
        prompt,
        summarize_payload(payload),
    )
    try:
        raw = json.loads(content)
        validate_summary_output(raw)
        summary = normalize_summary(raw)
        validate_summary_allowed_subjects(
            summary,
            payload,
            archived_subject_names(state.subjects),
        )
    except Exception as error:
        mark_audit_validation_failed(content, error)
        raise
    mark_audit_validated(content)
    mark_audit_adopted(content)
    return summary


def _display_text_values(value: Any):
    if not isinstance(value, dict):
        return
    for key in ("overview", "risks", "actions", "source"):
        for item in value.get(key, []):
            if isinstance(item, str):
                yield item


def validate_summary_allowed_subjects(
    raw: dict[str, Any],
    payload: dict[str, Any],
    archived_subjects: tuple[str, ...],
) -> None:
    allowed = payload.get("allowed_subjects")
    if not isinstance(allowed, list) or not allowed:
        raise LLMValidationError("每日总结输入缺少活动学科白名单。")
    violations = subject_reference_violations(
        "\n".join(_display_text_values(raw)),
        allowed_subjects=allowed,
        archived_subjects=archived_subjects,
    )
    if violations:
        raise LLMValidationError(
            "每日总结包含活动学科白名单外内容：" + "、".join(violations)
        )


def normalize_summary(raw: dict[str, Any]) -> dict[str, list[str]]:
    result = {}
    for key in ("overview", "risks", "actions", "source"):
        value = raw.get(key)
        if isinstance(value, list):
            result[key] = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        elif isinstance(value, str) and value.strip():
            result[key] = [value.strip()]
        else:
            result[key] = []
    if not result["overview"]:
        result["overview"] = ["LLM 未返回概况，已保留空白总结。"]
    if not result["actions"]:
        result["actions"] = ["查看主页待办事项，并补充今日学习记录。"]
    result["source"].append("生成方式：LLM 增强总结，经本地结构校验。")
    return result


def validate_cached_daily_summary(summary: object, subjects) -> None:
    validate_normalized_summary_cache(summary)
    validate_summary_allowed_subjects(
        summary,
        {"allowed_subjects": list(active_subject_names(subjects))},
        archived_subject_names(subjects),
    )


def shrink_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for record in records:
        result.append(
            {
                "date": record.get("date") or record.get("record_date"),
                "subject": record.get("subject"),
                "module": record.get("module"),
                "topic": record.get("topic"),
                "activity": record.get("activity"),
                "source": record.get("source"),
                "score": record.get("score"),
                "note": record.get("note"),
                "problems": shrink_problems(record.get("problems", [])),
            }
        )
    return result


def shrink_problems(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": problem.get("title"),
            "correctness": problem.get("correctness"),
            "difficulty_score": problem.get("difficulty_score"),
            "error_cause": problem.get("error_cause"),
            "error_category": problem.get("error_category"),
            "related_topics": problem.get("related_topics"),
        }
        for problem in problems[:8]
    ]


def shrink_risk(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "subject",
        "module",
        "topic",
        "level",
        "priority",
        "recall",
        "target_recall",
        "days_since",
        "mastery_probability",
        "target_mastery",
    ]
    return {key: item.get(key) for key in keys if key in item}
