from __future__ import annotations

from study_app.core.practice_bank import known_template_ids
from study_app.core.practice_spec import extract_template_brief, infer_practice_template


def infer_subject_topic_from_plan_line(line: str, selected_subject: str | None = None) -> tuple[str, str]:
    import re

    subject_candidates = [
        "微积分",
        "高等数学",
        "高级程序设计",
        "计算机科学",
        "数据结构与算法基础",
        "大学物理学",
        "化学原理",
    ]
    subject_aliases = {
        "Gauss": "微积分",
        "Green": "微积分",
        "Stokes": "微积分",
        "曲线积分": "微积分",
        "曲面积分": "微积分",
        "三重积分": "微积分",
        "级数": "微积分",
        "编程实践": "计算机科学",
        "实现训练": "计算机科学",
        "代码实现": "计算机科学",
        "上机": "计算机科学",
        "调试": "计算机科学",
        "LeetCode": "计算机科学",
        "leetcode": "计算机科学",
        "AVL": "数据结构与算法基础",
        "KMP": "数据结构与算法基础",
        "哈希": "数据结构与算法基础",
        "复杂度": "数据结构与算法基础",
        "物理": "大学物理学",
        "化学": "化学原理",
    }

    subject = selected_subject or ""
    if not subject:
        subject = next((item for item in subject_candidates if item in line), "")
    if not subject:
        subject = next((value for key, value in subject_aliases.items() if key in line), "微积分")
    if "题库模板" in line or line.startswith("当天作业"):
        homework = line.split("当天作业：", 1)[-1].strip()
        template_match = re.search(r"题库模板\s*([A-Z0-9-]+)", homework)
        inferred_id, inferred_brief = infer_practice_template(homework)
        if template_match and template_match.group(1) in known_template_ids():
            topic = extract_template_brief(homework, template_match.group(1))
        else:
            topic = inferred_brief
        if topic:
            return subject, topic[:80]

    topic = ""
    subject_pattern = re.escape(subject)
    match = re.search(subject_pattern + r"\s*/\s*([^，。；：:、\n]+)", line)
    if match:
        topic = match.group(1).strip()
    if not topic:
        match = re.search(r"(?:主攻|复习|专项|围绕|任务|作业)[：: ]*([^，。；\n]+)", line)
        if match:
            topic = match.group(1).strip()
    if not topic:
        topic = next((key for key in subject_aliases if key in line), "")
    if not topic:
        topic = "学习计划作业"
    return subject, topic[:80]


def is_plan_homework_item(line: str) -> bool:
    homework_markers = [
        "当天作业",
        "作业",
        "做题",
        "练习",
        "自测",
        "完成 1 道",
        "完成 2 道",
        "完成 3 道",
        "完成 4 道",
    ]
    return any(marker in line for marker in homework_markers)


def split_short_plan_item(line: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for index, part in enumerate(item.strip() for item in line.splitlines() if item.strip()):
        if False:  # heading row removed — no "第 N 天" prefix
            rows.append(("info", part))
        elif part.startswith(("任务", "回忆自测", "查漏补缺", "方法整理", "专项练习", "混合检查", "错因归类")):
            rows.append(("check", part))
        elif part.startswith(("记录要求", "复盘记录")):
            rows.append(("check", part))
        elif part.startswith("当天作业"):
            rows.append(("result", part))
        else:
            rows.append(("info", part))
    return rows or [("info", line)]


def study_plan_item_hash(
    section_key: str,
    day_index: int | None,
    item_type: str,
    item_text: str,
    item_order: int,
) -> str:
    import hashlib

    raw = f"{section_key}|{day_index or ''}|{item_type}|{item_order}|{item_text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_study_plan_items(plan: dict[str, list[str]]) -> list[dict[str, object]]:
    section_titles = {
        "judgement": "当前判断",
        "goals": "今日目标",
        "short": "今日计划",
        "diagnostic": "错题诊断表",
        "record_template": "推荐记录格式",
        "expected": "预期模型变化",
        "evidence": "生成依据",
    }
    items: list[dict[str, object]] = []
    order = 0
    for section_key, section_title in section_titles.items():
        day_index = None
        for line in plan.get(section_key, []):
            rows = split_short_plan_item(line) if section_key == "short" else [("info", line)]
            for item_type, item_text in rows:
                if section_key == "short":
                    day_index = 1
                item_hash = study_plan_item_hash(section_key, day_index, item_type, item_text, order)
                items.append(
                    {
                        "section_key": section_key,
                        "section_title": section_title,
                        "day_index": day_index,
                        "item_type": item_type,
                        "item_text": item_text,
                        "item_order": order,
                        "item_hash": item_hash,
                    }
                )
                order += 1
    return items


def build_budgeted_plan_items(plan: object) -> list[dict[str, object]]:
    """Convert a budget result to structured rows without text-derived task identity."""
    import hashlib

    rows: list[dict[str, object]] = []
    groups = (
        ("selected", "已安排", getattr(plan, "selected")),
        ("completed", "已完成", getattr(plan, "completed")),
        ("excluded", "未安排", getattr(plan, "excluded")),
    )
    for section_key, section_title, decisions in groups:
        for decision in decisions:
            task_id = decision.task_id
            identity = task_id or hashlib.sha256(
                f"{decision.subject_id or ''}|{decision.title}|{decision.rule_code}".encode("utf-8")
            ).hexdigest()
            rows.append({
                "section_key": section_key,
                "section_title": section_title,
                "day_index": 1,
                "item_type": "budget_task",
                "item_text": decision.title,
                "item_hash": f"budget:v1:{identity}",
                "task_id": task_id,
                "subject_id": decision.subject_id,
                "estimated_minutes": decision.estimated_minutes,
                "estimate_source": decision.estimate_source,
                "selection_reason": {
                    "rule_code": decision.rule_code,
                    "reason": decision.reason,
                    "ranking_evidence": dict(decision.ranking_evidence),
                },
                "excluded_reason": decision.rule_code if section_key == "excluded" else None,
                "initial_checked": section_key == "completed",
            })
    return rows


def humanize_plan_text(text: str) -> str:
    import re

    replacements = {
        "window_score": "三天窗口分数",
        "covered_mastery_score": "已学范围掌握度",
        "covered_topic_count": "已学知识点数",
        "total_topic_count": "总知识点数",
        "mastery_score": "整门课总掌握度",
        "target_recall": "目标回忆概率",
        "recall": "回忆概率",
        "target_mastery": "目标掌握概率",
        "mastery_probability": "掌握概率",
        "BKT": "知识追踪",
    }
    result = str(text)
    for raw, label in replacements.items():
        result = result.replace(raw, label)
    result = re.sub(r"题库模板\s+TEMPLATE_?0*1\b", "题库模板：通用混合练习", result)
    result = re.sub(r"题库模板\s+TEMP_?0*1\b", "题库模板：通用混合练习", result)
    result = re.sub(r"题库模板\s+([A-Z]{2,}(?:-[A-Z0-9]+)+)", r"题库模板：\1", result)

    def raise_low_goal(match: re.Match) -> str:
        value = int(match.group(2))
        if value >= 60:
            return match.group(0)
        return f"{match.group(1)}60%"

    result = re.sub(r"(提升至|达到|提高到)\s*(\d{1,2})%", raise_low_goal, result)
    return result
