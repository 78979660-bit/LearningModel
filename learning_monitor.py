import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from study_app.paths import DATABASE_PATH, MODEL_PATH, RECORDS_PATH

from learning_difficulty import infer_problem_difficulty
from learning_bkt import bkt_topic_states
from learning_memory import is_outside_class_review, topic_memory_state
from learning_problem_result import interpret_problem_result


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        # data_contract_v1 §4.3.6: 无法解析的历史日期跳过统计，不拖垮报告。
        return None


def load_json(path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def load_records(database_path=DATABASE_PATH, records_path=RECORDS_PATH):
    """Prefer current SQLite evidence; retain JSON as a compatibility fallback."""
    fallback_reason = ""
    try:
        from study_app.data.database import load_raw_records

        if Path(database_path).is_file():
            return load_raw_records(database_path), "SQLite", ""
        fallback_reason = "SQLite 数据库不存在"
    except Exception as error:
        detail = str(error).replace("\n", " ").strip()
        fallback_reason = f"SQLite 读取失败：{type(error).__name__}"
        if detail:
            fallback_reason += f"：{detail[:200]}"
    return (
        load_json(records_path).get("records", []),
        "JSON fallback",
        fallback_reason,
    )


def weighted_mastery(subject):
    modules = subject.get("modules", [])
    total_weight = sum(module.get("weight", 0) for module in modules)
    if not total_weight:
        return 0
    return sum(module.get("mastery", 0) * module.get("weight", 0) for module in modules) / total_weight


def assessment_map(model):
    items = model.get("initial_percent_assessment", {}).get("subjects", [])
    return {item["name"]: item for item in items}


def records_for_subject(records, subject_name):
    return [record for record in records if record.get("subject") == subject_name]


def records_through(records, as_of_date):
    """Return only valid dated evidence available at the historical cutoff."""
    cutoff = parse_date(as_of_date)
    if cutoff is None:
        raise ValueError("as_of_date must be a valid calendar date")
    matched = []
    for record in records:
        record_date = parse_date(record.get("date"))
        if record_date is not None and record_date <= cutoff:
            matched.append(record)
    return matched


def records_in_window(records, subject_name, start, end):
    matched = []
    for record in records_for_subject(records, subject_name):
        record_date = parse_date(record.get("date"))
        if record_date and start <= record_date <= end:
            matched.append(record)
    return matched


def latest_record_date(records, subject_name):
    dates = []
    for record in records_for_subject(records, subject_name):
        record_date = parse_date(record.get("date"))
        if record_date:
            dates.append(record_date)
    return max(dates) if dates else None


def latest_outside_class_review_date(records, subject_name):
    dates = []
    for record in records_for_subject(records, subject_name):
        if not is_outside_class_review(record):
            continue
        record_date = parse_date(record.get("date"))
        if record_date:
            dates.append(record_date)
    return max(dates) if dates else None


def is_problem_correct(problem):
    status = problem.get("status")
    if isinstance(problem.get("correct"), bool):
        return problem["correct"]
    if status in {"completed", "solved", "correct", "accepted", "AC", "全对", "做出"}:
        return True
    if status in {"not_solved", "wrong", "incorrect", "failed", "WA", "未做出", "没做出来"}:
        return False
    return None


def problem_correctness_fraction(problem):
    return interpret_problem_result(problem)["value"]


def difficulty_problem_score(problem, policy, record=None):
    inferred = infer_problem_difficulty(record or {}, problem)
    difficulty_score = float(inferred.get("difficulty_score", 55.0))
    correctness = interpret_problem_result(problem, record)["value"]
    if correctness is None:
        return None

    scoring = policy.get("difficulty_scoring", {})
    numeric = scoring.get("numeric_scoring", {})
    if numeric.get("enabled", False):
        ratio = max(0.0, min(1.0, difficulty_score / 100))
        correct_floor = float(numeric.get("correct_floor", 58))
        correct_ceiling = float(numeric.get("correct_ceiling", 100))
        correct_alpha = float(numeric.get("correct_alpha", 1.15))
        wrong_floor = float(numeric.get("wrong_floor", 8))
        wrong_ceiling = float(numeric.get("wrong_ceiling", 58))
        wrong_alpha = float(numeric.get("wrong_alpha", 1.25))
        correct_score = correct_floor + (correct_ceiling - correct_floor) * (ratio**correct_alpha)
        wrong_score = wrong_floor + (wrong_ceiling - wrong_floor) * (ratio**wrong_alpha)
        return wrong_score + (correct_score - wrong_score) * correctness

    scores = scoring.get("problem_scores", {})
    default_scores = {
        "easy": {"correct": 75, "wrong": 20},
        "medium": {"correct": 85, "wrong": 35},
        "hard": {"correct": 100, "wrong": 50},
    }
    scores = {**default_scores, **scores}
    difficulty = inferred["difficulty"]
    if difficulty not in scores:
        difficulty = "medium"

    correct_score = float(scores[difficulty]["correct"])
    wrong_score = float(scores[difficulty]["wrong"])
    return wrong_score + (correct_score - wrong_score) * correctness


def difficulty_weighted_score(record, policy):
    if not policy.get("difficulty_scoring", {}).get("enabled", False):
        return None

    problems = record.get("problems") or []
    if not problems:
        return None

    scores = []
    weights = []
    weight_map = policy.get("difficulty_scoring", {}).get(
        "problem_weights",
        {"easy": 0.8, "medium": 1.0, "hard": 1.2},
    )
    numeric = policy.get("difficulty_scoring", {}).get("numeric_scoring", {})
    for problem in problems:
        score = difficulty_problem_score(problem, policy, record)
        if score is None:
            continue
        inferred = infer_problem_difficulty(record, problem)
        if numeric.get("enabled", False):
            difficulty_score = float(inferred.get("difficulty_score", 55.0))
            ratio = max(0.0, min(1.0, difficulty_score / 100))
            weight_base = float(numeric.get("weight_base", 0.65))
            weight_span = float(numeric.get("weight_span", 0.85))
            weight = weight_base + weight_span * ratio
        else:
            difficulty = inferred["difficulty"]
            weight = float(weight_map.get(difficulty, 1.0))
        scores.append(score)
        weights.append(weight)

    if not scores:
        return None
    return sum(score * weight for score, weight in zip(scores, weights)) / sum(weights)


def record_score(record, policy=None):
    policy = policy or {}
    difficulty_score = difficulty_weighted_score(record, policy)
    if difficulty_score is not None:
        return difficulty_score

    if isinstance(record.get("score"), (int, float)):
        return float(record["score"])

    result = record.get("result", {})
    correct = result.get("correct")
    total = result.get("total")
    if isinstance(correct, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return 100 * correct / total

    if isinstance(record.get("self_rating"), (int, float)):
        return float(record["self_rating"])

    return None


def window_score(records, policy=None):
    scores = []
    weights = []
    for record in records:
        score = record_score(record, policy)
        if score is None:
            continue
        duration = record.get("duration_minutes")
        weight = max(float(duration), 20.0) if isinstance(duration, (int, float)) else 60.0
        scores.append(score)
        weights.append(weight)
    if not scores:
        return None
    return sum(score * weight for score, weight in zip(scores, weights)) / sum(weights)


def warning_level(score, benchmark, severe_margin):
    if score < benchmark - severe_margin:
        return "high"
    if score < benchmark:
        return "medium"
    return "none"


def iter_topics(model):
    for subject in model.get("subjects", []):
        if str(subject.get("lifecycle", {}).get("status") or "") == "archived":
            continue
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                yield subject["name"], module["name"], topic


def forgetting_risks(model, records, today):
    policy = model.get("warning_policy", {})

    risks = []
    for subject_name, module_name, topic in iter_topics(model):
        item = topic_memory_state(subject_name, module_name, topic, records, today, policy)
        if item["level"] != "none":
            risks.append(item)

    risks.sort(key=lambda item: item["priority"], reverse=True)
    return risks


def bkt_alerts(model, records, as_of_date):
    policy = model.get("warning_policy", {})
    max_items = policy.get("bkt_model", {}).get("max_report_items", 8)
    archived = {
        subject.get("name") for subject in model.get("subjects", [])
        if str(subject.get("lifecycle", {}).get("status") or "") == "archived"
    }
    alerts = [
        item for item in bkt_topic_states(model, records, as_of_date=as_of_date)
        if item["level"] != "none" and item.get("subject") not in archived
    ]
    return alerts[:max_items]


def generate_report(model, records, today):
    today = parse_date(today)
    if today is None:
        raise ValueError("today must be a valid calendar date")
    records = records_through(records, today)
    policy = model.get("warning_policy", {})
    window_days = policy.get("period_days", 3)
    benchmark = policy.get("period_benchmark_score", policy.get("benchmark_score", 60))
    severe_margin = policy.get("severe_score_margin", 10)
    stale_days = policy.get("stale_review_days", 6)
    compare_only_active = policy.get("compare_only_subjects_with_window_records", True)
    score_map = assessment_map(model)

    start = today - timedelta(days=window_days - 1)

    lines = []
    lines.append(f"# 三天学习概况 - {start.isoformat()} 至 {today.isoformat()}")
    lines.append("")
    lines.append(f"周期标杆分数：{benchmark}")
    lines.append("")
    lines.append("| 学科 | 初始评估 | 三天窗口分数 | 模型掌握度 | 最近记录 | 预警 |")
    lines.append("|---|---:|---:|---:|---|---|")

    warnings = []

    for subject in model.get("subjects", []):
        name = subject["name"]
        if str(subject.get("lifecycle", {}).get("status") or "") == "archived":
            lines.append(f"| {name} | - | 已封存 | - | - | 只读参考，不再检查 |")
            continue
        initial_score = score_map.get(name, {}).get("initial_score", round(weighted_mastery(subject) * 100))
        mastery_score = round(weighted_mastery(subject) * 100)
        latest = latest_record_date(records, name)
        latest_review = latest_outside_class_review_date(records, name)
        latest_text = latest.isoformat() if latest else "无"
        subject_records = records_in_window(records, name, start, today)
        cycle_score = window_score(subject_records, policy)
        if cycle_score is not None:
            cycle_text = f"{cycle_score:.1f}"
        elif subject_records:
            cycle_text = "有记录/未评分"
        else:
            cycle_text = "未记录"

        subject_warnings = []
        level = "none"

        if cycle_score is not None:
            level = warning_level(cycle_score, benchmark, severe_margin)
            if level != "none":
                subject_warnings.append(f"三天分数低于标杆({cycle_score:.1f} < {benchmark})")
        elif not compare_only_active:
            subject_warnings.append("本周期无记录")

        if latest_review is not None and (today - latest_review).days >= stale_days:
            subject_warnings.append(f"{(today - latest_review).days}天无课外复习")

        warning_text = "；".join(subject_warnings) if subject_warnings else "正常"
        lines.append(
            f"| {name} | {initial_score} | {cycle_text} | {mastery_score} | {latest_text} | {warning_text} |"
        )

        if subject_warnings:
            sort_score = cycle_score if cycle_score is not None else initial_score
            warnings.append((level, sort_score, name, subject_warnings))

    lines.append("")
    lines.append("## 本周期预警")
    if not warnings:
        lines.append("")
        lines.append("暂无学科级预警。未记录的科目不会自动判定为低于标杆。")
    else:
        severity_order = {"high": 0, "medium": 1, "none": 2}
        warnings.sort(key=lambda item: (severity_order[item[0]], item[1]))
        for _, score, name, subject_warnings in warnings:
            lines.append(f"- {name}：{score:.1f}分，" + "；".join(subject_warnings))

    curve_risks = forgetting_risks(model, records, today)
    max_items = policy.get("spaced_repetition_model", {}).get(
        "max_report_items",
        policy.get("forgetting_curve", {}).get("max_report_items", 8),
    )
    lines.append("")
    lines.append("## 遗忘曲线预警")
    if not curve_risks:
        lines.append("")
        lines.append("暂无知识点低于目标回忆概率。")
    else:
        for item in curve_risks[:max_items]:
            recall_text = f"{item['recall']:.0%}"
            risk_text = f"{item['risk']:.0%}"
            half_life_text = f"{item['half_life']:.1f}"
            if item["last_review"]:
                detail = (
                    f"上次课外复习 {item['last_review'].isoformat()}，"
                    f"间隔 {item['days_since']} 天，半衰期约 {half_life_text} 天"
                )
            else:
                detail = f"暂无课外复习记录，使用初始先验，半衰期估计约 {half_life_text} 天"
            lines.append(
                f"- {item['subject']} / {item['topic']}：回忆概率 {recall_text}，"
                f"遗忘风险 {risk_text}，目标 {item['target_recall']:.0%}，{detail}"
            )

    lines.append("")
    lines.append("## 建议")
    if warnings:
        for _, _, name, _ in warnings[:3]:
            lines.append(f"- 下个三天周期优先安排 `{name}` 的一次复习或练习。")
    elif curve_risks:
        for item in curve_risks[:3]:
            lines.append(f"- 优先复习 `{item['topic']}`，降低遗忘风险。")
    else:
        lines.append("- 下个三天周期选择 1-2 门主攻科目即可，不需要四科每天都覆盖。")

    bkt_items = bkt_alerts(model, records, today)
    lines.append("")
    lines.append("## BKT 掌握概率预警")
    if not bkt_items:
        lines.append("")
        lines.append("暂无基于做题证据的 BKT 掌握概率预警。")
    else:
        for item in bkt_items:
            last = item.get("last_observation") or {}
            if last:
                detail = f"最近证据：{last.get('title', '')}，正确度 {last.get('correctness', 0):.0%}"
            else:
                detail = "暂无直接做题证据，使用先验估计"
            lines.append(
                f"- {item['subject']} / {item['topic']}：P(掌握) {item['mastery_probability']:.0%}，"
                f"目标 {item['target_mastery']:.0%}，证据 {item['observation_count']} 条；{detail}"
            )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate a three-day learning warning report.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Report end date, YYYY-MM-DD.")
    parser.add_argument("--output", default="", help="Optional output markdown path.")
    args = parser.parse_args()

    model = load_json(MODEL_PATH)
    records, data_source, fallback_reason = load_records()
    today = parse_date(args.date)
    report = generate_report(model, records, today)
    report = f"{report}\n\n数据源：{data_source}"
    if fallback_reason:
        report += f"（{fallback_reason}）"

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
