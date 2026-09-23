from __future__ import annotations

from study_app.core.dashboard import DashboardState
from study_app.core.study_plan_items import infer_subject_topic_from_plan_line
from study_app.paths import MODEL_PATH


def plan_day_feedback_lines(saved_plan: dict, state: DashboardState, subject_scope: str | None) -> list[str]:
    return [item["line"] for item in plan_day_feedback_details(saved_plan, state, subject_scope)]


def planned_homework_score(difficulty_score: float | None, is_correct: bool) -> float:
    difficulty = 55.0 if difficulty_score is None else max(0.0, min(100.0, float(difficulty_score)))
    try:
        from learning_monitor import difficulty_problem_score, load_json

        policy = load_json(MODEL_PATH).get("warning_policy", {})
        score = difficulty_problem_score(
            {
                "difficulty_score": difficulty,
                "partial_credit": 1.0 if is_correct else 0.0,
            },
            policy,
            {},
        )
        if score is not None:
            return round(float(score), 1)
    except Exception:
        pass

    ratio = difficulty / 100
    if is_correct:
        return round(58 + (100 - 58) * (ratio**1.15), 1)
    return round(8 + (58 - 8) * (ratio**1.25), 1)


def plan_day_feedback_details(saved_plan: dict, state: DashboardState, subject_scope: str | None) -> list[dict[str, object]]:
    """Summarize plan execution without inferring correctness from a completion check."""
    by_day: dict[int, list[dict]] = {}
    for item in saved_plan.get("items", []):
        if item.get("section_key") == "short" and item.get("day_index"):
            by_day.setdefault(int(item["day_index"]), []).append(item)

    details = []
    for day_index in sorted(by_day):
        actionable = [
            item
            for item in by_day[day_index]
            if item.get("item_type") in {"check", "result"}
        ]
        done_count = sum(
            1 for item in actionable if item.get("checked") or item.get("result")
        )
        total_count = len(actionable)
        complete = total_count > 0 and done_count == total_count
        homework_items = [item for item in actionable if item.get("item_type") == "result"]
        homework_done = sum(
            1 for item in homework_items if item.get("checked") or item.get("result")
        )
        first_homework = homework_items[0]["item_text"] if homework_items else ""
        subject_name, topic = infer_subject_topic_from_plan_line(first_homework, subject_scope)
        progress = f"{done_count}/{total_count}" if total_count else "0/0"
        if complete:
            line = (
                f"今日进度 {progress}，已完成。{subject_name} / {topic}；"
                "完成状态已保存，具体正确率、错因、学习分数与掌握度将在上传做题记录后更新。"
            )
            popup = (
                "今日计划已完成。\n\n"
                f"学科与知识点：{subject_name} / {topic}\n"
                f"当天进度：{progress}\n\n"
                "完成勾选仅表示任务已执行，不代表题目全部正确。"
                "请通过“新增记录”上传实际做题情况，系统解析后再更新分数、掌握度、"
                "遗忘风险和知识追踪证据。"
            )
        else:
            line = (
                f"今日进度 {progress}；"
                f"作业完成项 {homework_done}/{len(homework_items)}。"
                "完成勾选只记录执行状态，具体答题情况将在上传记录后判定。"
            )
            popup = ""
        details.append({"day_index": day_index, "complete": complete, "line": line, "popup": popup})
    return details
