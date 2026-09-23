from __future__ import annotations


def recent_record_display_lines(records: list[dict]) -> tuple[str, ...]:
    lines = []
    for item in records:
        attachment_count = len(item.get("attachments", []))
        problem_count = item.get("problem_count", 0)
        score = "" if item.get("score") is None else f" | {item['score']:.0f} 分"
        lines.append(
            f"{item['record_date']}  {item['subject_name']} / "
            f"{item.get('topic_name') or item.get('module_name') or '未命名'}"
            f"{score} | 题目 {problem_count} | 附件 {attachment_count}"
        )
    return tuple(lines)
