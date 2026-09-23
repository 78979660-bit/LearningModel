from __future__ import annotations

from study_app.core.practice_bank import template_for_topic


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def extract_homework_exercise_count(homework: str, default: int = 2) -> int:
    import re

    patterns = [
        r"题量\s*[:：=]?\s*(\d+)\s*(?:道|题)?",
        r"共\s*(\d+)\s*(?:道|题)",
        r"(\d+)\s*(?:道|题)\s*[（(]",
        r"(?:生成/选做|生成|选做|完成)\s*(\d+)\s*(?:道|题)?",
    ]
    matches: list[int] = []
    for pattern in patterns:
        matches.extend(safe_int(match.group(1), 0) for match in re.finditer(pattern, homework))
    matches = [value for value in matches if value > 0]
    return matches[-1] if matches else default


def normalize_homework_count(homework: str, exercise_count: int) -> str:
    import re

    text = re.sub(r"题量\s*[:：=]?\s*\d+\s*(?:道|题)?", f"题量 {exercise_count} 题", homework)
    if "题量" not in text:
        text = text.rstrip("。；; ") + f"；题量 {exercise_count} 题"
    return text


def desired_practice_seed_count(exercise_count: int) -> int:
    return max(3, min(8, (max(1, exercise_count) + 1) // 2))


def _extract_homework_component_counts(homework: str) -> list[tuple[str, int]]:
    import re

    text = homework or ""
    paren_match = re.search(r"[（(]([^()（）]+)[）)]", text)
    segment = paren_match.group(1) if paren_match else text
    parts = [part.strip(" ，,、;；（）()") for part in re.split(r"[、,，/+]+", segment)]
    result: list[tuple[str, int]] = []
    keywords = ["级数", "幂级数", "正项", "曲线积分", "曲面积分", "Green", "Gauss", "Stokes", "三重积分", "二重积分", "并查集", "AVL", "旋转", "归并排序", "排序", "综合"]
    for part in parts:
        match = re.match(r"(\d+)\s*[道题]\s*(.+)", part)
        if not match:
            continue
        count = safe_int(match.group(1), 0)
        component = match.group(2).strip()
        if count > 0 and component and any(keyword in component for keyword in keywords):
            result.append((component, count))
    return result[:8]


def _extract_homework_components(homework: str) -> list[str]:
    import re

    text = homework or ""
    match = re.search(r"(?:含|包含)([^。；;]+)", text)
    paren_match = re.search(r"[（(]([^()（）]+)[）)]", text)
    segment = match.group(1) if match else (paren_match.group(1) if paren_match else text)
    if not paren_match:
        segment = re.split(r"参考难度|题库模板|TEMPLATE_ID", segment, 1)[0]
    parts = [part.strip(" ，,、;；（）()") for part in re.split(r"[、,，/+]+", segment)]
    cleaned = []
    stop_words = {"题量", "共", "道", "题", "完成"}
    for part in parts:
        part = re.sub(r"\d+\s*[道题]", "", part).strip()
        if not part or part in stop_words:
            continue
        if any(keyword in part for keyword in ["级数", "幂级数", "正项", "曲线积分", "曲面积分", "Green", "Gauss", "Stokes", "三重积分", "二重积分", "并查集", "AVL", "旋转", "归并排序", "排序", "综合"]):
            cleaned.append(part)
    result = []
    for item in cleaned:
        if item not in result:
            result.append(item)
    return result[:6]


def is_oj_plan_homework(line: str) -> bool:
    text = str(line or "")
    return "CS-OJ-PRACTICE" in text or "LeetCode" in text or "OJ 原题" in text


def extract_template_brief(homework: str, template_id: str) -> str:
    marker = f"题库模板 {template_id}"
    if marker not in homework:
        return "概念判定、基础计算/推导、混合应用与错因归类"
    tail = homework.split(marker, 1)[1].lstrip("：: ")
    brief = tail.split("；", 1)[0].strip()
    return brief or "概念判定、基础计算/推导、混合应用与错因归类"


def infer_practice_template(text: str) -> tuple[str, str]:
    normalized = text or ""
    return template_for_topic(normalized)


def planned_homework_difficulty(text: str) -> float:
    import re

    match = re.search(r"参考难度\s*(\d+(?:\.\d+)?)\s*/\s*100", text or "")
    return max(0.0, min(100.0, float(match.group(1)))) if match else 55.0
