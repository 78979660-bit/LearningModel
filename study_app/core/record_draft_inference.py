"""Qt-independent record inference shared by the legacy editor and QML service.

This module keeps the record editor's existing local heuristics in one place.
It deliberately does not read files or contact providers; callers supply the
already extracted attachment text and the context that the user entered.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Iterable


def extract_problem_titles(text: str) -> list[str]:
    titles: list[str] = []
    for start, end in re.findall(r"(?:第)?(\d+)\s*(?:到|-|~|至)\s*(\d+)\s*题", text):
        a, b = int(start), int(end)
        if 0 < a <= b and b - a <= 80:
            titles.extend([f"第{number}题" for number in range(a, b + 1)])
    for end in re.findall(r"前\s*(\d+)\s*题", text):
        count = min(int(end), 80)
        titles.extend([f"第{number}题" for number in range(1, count + 1)])
    for number in re.findall(r"(?:第|例)\s*(\d+(?:\.\d+)*)\s*题?", text):
        title = f"第{number}题" if "." not in number else f"例{number}"
        if title not in titles:
            titles.append(title)
    if not titles:
        match = re.search(r"(?:做了|完成了|练了)\s*(\d+)\s*题", text)
        if match:
            count = min(int(match.group(1)), 50)
            titles.extend([f"第{number}题" for number in range(1, count + 1)])
    if not titles:
        for number in re.findall(r"(?:^|\n)\s*((?:\d+\.)?\d+)\s+.{6,}", text):
            title = f"第{number}题"
            if title not in titles:
                titles.append(title)
            if len(titles) >= 30:
                break
    return list(dict.fromkeys(titles))


def parse_problem_number(value: Any) -> int | None:
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[1:], 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(value)


def extract_wrong_titles(text: str) -> set[str]:
    wrong: set[str] = set()
    for group in re.findall(r"错(?:了|误)?\s*(?:第)?([\d一二三四五六七八九十两、，,\s和]+)\s*题", text):
        for token in re.findall(r"\d+|[一二三四五六七八九十两]+", group):
            number = parse_problem_number(token)
            if number is not None:
                wrong.add(f"第{number}题")
    for token in re.findall(r"(?:错(?:因|在)?|错误在|没掌握|不会|算错)\s*(?:第)?(\d+|[一二三四五六七八九十两]+)\s*题", text):
        number = parse_problem_number(token)
        if number is not None:
            wrong.add(f"第{number}题")
    return wrong


def extract_error_causes_by_title(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = r"(?:第)?(\d+|[一二三四五六七八九十两]+)\s*题?(?:错因(?:是|为)?|因为|因|错在)([^。；;\n]+)"
    for raw_number, reason in re.findall(pattern, text):
        number = parse_problem_number(raw_number)
        if number is not None:
            result[f"第{number}题"] = reason.strip(" ：:，,")
    return result


def classify_error_category(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return "none"
    rules = [
        ("boundary_omission", ["边界", "端点", "零点", "特殊情况", "特殊点", "奇点", "范围", "定义域"]),
        ("condition_misjudgment", ["条件", "适用", "方向", "取向", "补面", "符号", "判断错误"]),
        ("calculation_error", ["计算", "算错", "代入", "化简", "积分算错", "面积算错", "通量计算"]),
        ("method_gap", ["不会", "没有掌握", "不熟", "忘记方法", "势函数", "公式遗忘"]),
        ("modeling_error", ["建模", "列式", "区域设置", "变量", "换元", "坐标"]),
        ("concept_forgetting", ["概念", "定义", "遗忘", "忘了", "记错"]),
        ("time_management", ["时间", "来不及", "粗心", "审题太快"]),
    ]
    for category, keywords in rules:
        if any(keyword in value for keyword in keywords):
            return category
    return "condition_misjudgment"


def attachment_based_title(attachments: Iterable[str | Path]) -> str:
    first = next(iter(attachments), None)
    return Path(first).stem if first else ""


def extract_statement_snippet(title: str, text: str) -> str:
    if not text:
        return ""
    numbers = re.findall(r"\d+(?:\.\d+)*", title)
    if not numbers:
        return ""
    number = numbers[0]
    patterns = [
        rf"(?:第\s*)?{re.escape(number)}\s*题?[^\n]*(?:\n[^\n]*){{0,3}}",
        rf"(?m)^\s*{re.escape(number)}\s+[^\n]*(?:\n[^\n]*){{0,3}}",
        rf"例\s*{re.escape(number)}[^\n]*(?:\n[^\n]*){{0,3}}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()[:1000]
    return ""


def inferred_statement_for(
    title: str,
    context: str,
    *,
    extracted_text: str = "",
    topic: str = "",
    module: str = "",
    attachments: Iterable[str | Path] = (),
) -> str:
    attachment_list = list(attachments)
    snippet = extract_statement_snippet(title, extracted_text)
    pieces = [
        f"系统从做题描述中识别：{title}",
        f"主题：{topic.strip()}" if topic.strip() else "",
        f"模块：{module.strip()}" if module.strip() else "",
        "附件：" + "；".join(Path(path).name for path in attachment_list) if attachment_list else "",
        f"附件题面摘录：{snippet}" if snippet else "",
        context[:500],
    ]
    return "\n".join(piece for piece in pieces if piece)


def answer_result_for(title: str, answer_result: str, wrong_titles: set[str], total_count: int) -> str:
    if title in wrong_titles:
        return "错误"
    if "全对" in answer_result or "其余都对" in answer_result or "其余全对" in answer_result:
        return "全对"
    if total_count == 1:
        return answer_result
    return "全对"


def infer_problem_fields(problem: dict[str, Any], record_context: dict[str, Any]) -> dict[str, Any]:
    """Apply the editor's existing correctness and difficulty enrichment rules."""
    from study_app.data.database import _infer_correctness_from_text
    from learning_difficulty import infer_problem_difficulty

    result = dict(problem)
    correctness = _infer_correctness_from_text(result)
    if correctness is not None:
        result["correctness"] = correctness * 100
        result["partial_credit"] = correctness
        if correctness >= 0.995:
            result["status"] = "correct"
        elif correctness <= 0.005:
            result["status"] = "wrong"
        else:
            result["status"] = "partial"

    if not (result.get("difficulty_source") == "llm" and result.get("difficulty_score") is not None):
        inferred = infer_problem_difficulty(record_context, result)
        result["difficulty"] = inferred.get("difficulty")
        result["difficulty_score"] = inferred.get("difficulty_score")
        result["difficulty_source"] = inferred.get("source")
        result["difficulty_confidence"] = inferred.get("confidence")
        if inferred.get("reasons"):
            result["difficulty_reasons"] = inferred["reasons"]
    return result


def infer_problems_from_inputs(
    *,
    note: str,
    answer_result: str,
    error_cause: str,
    topic: str,
    module: str,
    attachments: Iterable[str | Path],
    extracted_text: str,
    problem_blocks: Iterable[Any],
    record_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the original record-editor local title/error/statement inference."""
    attachment_list = list(attachments)
    block_list = list(problem_blocks)
    # Keep the legacy title scan over paths (the editor historically treated
    # numbered filenames as evidence), but never copy a full path into the
    # public problem statement or any DTO text.
    raw_manual_context = "\n".join(
        [note.strip(), answer_result.strip(), error_cause.strip(), *map(str, attachment_list)]
    )
    safe_manual_context = "\n".join(
        [
            note.strip(),
            answer_result.strip(),
            error_cause.strip(),
            *(Path(path).name for path in attachment_list),
        ]
    )
    context = "\n".join([safe_manual_context, extracted_text])
    titles = extract_problem_titles(raw_manual_context)
    if not titles:
        titles = [str(block.title) for block in block_list[:30]]
    if not titles:
        titles = extract_problem_titles(extracted_text)
    if not titles and (answer_result.strip() or error_cause.strip()):
        titles = [topic.strip() or attachment_based_title(attachment_list) or "识别题目"]
    wrong_titles = extract_wrong_titles("\n".join([raw_manual_context, extracted_text]))
    error_by_title = extract_error_causes_by_title(error_cause)
    statement_by_title = {str(block.title): str(block.statement) for block in block_list}
    record = dict(record_context or {})
    record.setdefault("subject", "")
    record.setdefault("module", module.strip())
    record.setdefault("topic", topic.strip())
    record.setdefault("note", note.strip())
    result: list[dict[str, Any]] = []
    for title in titles:
        problem_error = error_by_title.get(title, error_cause.strip() if title in wrong_titles else "")
        statement = statement_by_title.get(title) or inferred_statement_for(
            title,
            context,
            extracted_text=extracted_text,
            topic=topic,
            module=module,
            attachments=attachment_list,
        )
        problem = {
            "title": title,
            "statement": statement,
            "statement_source": "attachment_excerpt" if title in statement_by_title or "附件题面摘录" in statement else "generated_summary",
            "answer_result": answer_result_for(title, answer_result.strip(), wrong_titles, len(titles)),
            "error_cause": problem_error,
            "error_category": classify_error_category(problem_error),
            "related_topics": [topic.strip()] if topic.strip() else [],
            "parser_source": "local_rule",
            "attachment_sources": [Path(path).name for path in attachment_list],
        }
        result.append(infer_problem_fields(problem, record))
    return result


def estimate_record_score_from_problems(
    problems: list[dict[str, Any]],
    record_context: dict[str, Any],
    model_path: str | Path | None = None,
    *,
    use_default_model: bool = True,
) -> float | None:
    """Preserve the editor's canonical record_score plus historical fallback."""
    if not problems:
        return None
    try:
        from learning_monitor import load_json, record_score
        if model_path is None:
            if not use_default_model:
                raise FileNotFoundError("No model path was supplied")
            from study_app.paths import MODEL_PATH
            model_path = MODEL_PATH
        model = load_json(Path(model_path))
        policy = model.get("warning_policy", {})
    except Exception:
        policy = {}
        try:
            from learning_monitor import record_score
        except Exception:
            return None
    record = {**record_context, "problems": list(problems)}
    def fallback_problem_weighted_score() -> float | None:
        from learning_problem_result import interpret_problem_result
        scores: list[float] = []
        weights: list[float] = []
        for problem in problems:
            interpretation = interpret_problem_result(problem)
            correctness = interpretation["value"]
            if correctness is None:
                continue
            difficulty = problem.get("difficulty_score")
            difficulty = float(difficulty) if isinstance(difficulty, (int, float)) else 55.0
            ratio = max(0.0, min(1.0, difficulty / 100))
            correct_score = 58 + (100 - 58) * (ratio ** 1.15)
            wrong_score = 8 + (58 - 8) * (ratio ** 1.25)
            score = wrong_score + (correct_score - wrong_score) * correctness
            weight = 0.65 + 0.85 * ratio
            scores.append(score)
            weights.append(weight)
        if not scores:
            return None
        return sum(score * weight for score, weight in zip(scores, weights)) / sum(weights)

    score = record_score(record, policy)
    if score is None:
        score = fallback_problem_weighted_score()
    if score is None:
        return None
    return max(0.0, min(100.0, float(score)))
