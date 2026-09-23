from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from learning_bkt import bkt_topic_states
from learning_difficulty import infer_problem_difficulty
from learning_problem_analysis import analyze_problem_statement
from study_app.core.ds_course_taxonomy import DS_TOPICS, DSTopic, chapter_from_text, classify_ds_text
from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, import_model_json, load_raw_records
from study_app.paths import LOGS_DIR, MODEL_PATH


SUBJECT = "数据结构与算法基础"
AUDIT_PATH = LOGS_DIR / "ds_history_recalibration_audit.json"

DEFAULT_DIFFICULTY = {
    "数据结构基本概念与抽象数据类型": 48,
    "算法复杂度分析": 68,
    "顺序表": 55,
    "单链表、循环链表与双向链表": 60,
    "栈及其应用": 58,
    "队列及其应用": 60,
    "递归与非递归转换": 68,
    "多维数组、特殊矩阵与稀疏矩阵": 66,
    "串与 KMP 算法": 72,
    "广义表": 62,
    "树、森林与二叉树": 64,
    "堆与优先队列": 68,
    "哈夫曼树与编码": 66,
    "等价类与并查集": 65,
    "字典与哈希表": 70,
    "静态搜索结构": 62,
    "二叉搜索树": 68,
    "AVL 树旋转与插入删除": 76,
    "图的存储与遍历": 68,
    "最小生成树与最短路径": 77,
    "拓扑排序与关键路径": 75,
    "插入排序与希尔排序": 67,
    "交换排序与快速排序": 72,
    "选择排序与堆排序": 72,
    "归并、基数与外部排序": 75,
}

DEFAULT_MASTERY_PRIOR = {
    "数据结构基本概念与抽象数据类型": 0.12,
    "算法复杂度分析": 0.25,
    "顺序表": 0.55,
    "单链表、循环链表与双向链表": 0.50,
    "栈及其应用": 0.52,
    "队列及其应用": 0.52,
    "递归与非递归转换": 0.42,
    "多维数组、特殊矩阵与稀疏矩阵": 0.55,
    "串与 KMP 算法": 0.22,
    "广义表": 0.12,
    "树、森林与二叉树": 0.45,
    "堆与优先队列": 0.08,
    "哈夫曼树与编码": 0.12,
    "等价类与并查集": 0.12,
    "字典与哈希表": 0.08,
    "静态搜索结构": 0.12,
    "二叉搜索树": 0.38,
    "AVL 树旋转与插入删除": 0.28,
    "图的存储与遍历": 0.12,
    "最小生成树与最短路径": 0.12,
    "拓扑排序与关键路径": 0.12,
    "插入排序与希尔排序": 0.12,
    "交换排序与快速排序": 0.12,
    "选择排序与堆排序": 0.08,
    "归并、基数与外部排序": 0.12,
}


def flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(flatten(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten(item) for item in value)
    return str(value)


def is_ai_generated(record: dict[str, Any], problem: dict[str, Any]) -> bool:
    text = flatten(
        [
            record.get("source"),
            record.get("note"),
            problem.get("difficulty_source"),
            problem.get("source"),
            problem.get("note"),
        ]
    ).lower()
    return any(marker in text for marker in ("ai生成", "ai 生成", "gpt生成", "gpt 生成", "ai_generated", "llm_generated", "web_gpt"))


def is_plan_completion(record: dict[str, Any]) -> bool:
    return str(record.get("note") or "").startswith("学习计划项完成")


def classification_text(record: dict[str, Any], problem: dict[str, Any] | None = None) -> str:
    problem = problem or {}
    return flatten(
        [
            problem.get("title"),
            problem.get("statement"),
            problem.get("related_topics"),
            problem.get("error_cause"),
            problem.get("note"),
            record.get("topic"),
            record.get("module"),
            record.get("related_topics"),
            record.get("note"),
        ]
    )


def problem_classification_text(problem: dict[str, Any]) -> str:
    return flatten(
        [
            problem.get("title"),
            problem.get("statement"),
            problem.get("error_cause"),
            problem.get("note"),
        ]
    )


def recalculated_difficulty(record: dict[str, Any], problem: dict[str, Any], classified: DSTopic) -> dict[str, Any]:
    clean = dict(problem)
    for key in ("difficulty", "difficulty_score", "difficulty_value", "difficulty_percent", "level"):
        clean.pop(key, None)
    if str(problem.get("statement") or "").strip():
        result = analyze_problem_statement(record, clean)
        if result.get("has_statement"):
            score = float(result.get("difficulty_score", DEFAULT_DIFFICULTY[classified.topic]))
            source = "statement_analysis_reclassified"
            confidence = float(result.get("confidence", 0.65))
        else:
            result = infer_problem_difficulty(record, clean)
            score = float(result.get("difficulty_score", DEFAULT_DIFFICULTY[classified.topic]))
            source = str(result.get("source") or "topic_baseline")
            confidence = float(result.get("confidence", 0.45))
    else:
        score = float(DEFAULT_DIFFICULTY[classified.topic])
        source = "course_topic_baseline"
        confidence = 0.45
    if is_ai_generated(record, problem):
        source = "ai_generated_recalibrated"
    return {
        "score": max(20.0, min(95.0, score)),
        "label": "hard" if score >= 75 else "medium" if score >= 40 else "easy",
        "source": source,
        "confidence": confidence,
    }


def reclassify_records(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "subject": SUBJECT,
        "records_updated": 0,
        "attempts_updated": 0,
        "unclassified_attempts": [],
        "topic_attempt_counts": Counter(),
        "chapter_record_counts": Counter(),
    }
    with connect(db_path) as connection:
        records = connection.execute(
            "SELECT * FROM learning_records WHERE subject_name = ? ORDER BY record_date, id",
            (SUBJECT,),
        ).fetchall()
        for record_row in records:
            record = json.loads(record_row["raw_json"])
            if is_plan_completion(record):
                record["activity"] = "plan_completion"
                record["source"] = "planned_homework"
            attempts = connection.execute(
                "SELECT * FROM problem_attempts WHERE record_id = ? ORDER BY id",
                (record_row["id"],),
            ).fetchall()
            classified_attempts: list[DSTopic] = []
            raw_problems = list(record.get("problems") or [])
            for index, attempt_row in enumerate(attempts):
                raw_problem = json.loads(attempt_row["raw_json"])
                classified = classify_ds_text(problem_classification_text(raw_problem))
                if not classified:
                    classified = classify_ds_text(classification_text(record, raw_problem))
                if not classified:
                    audit["unclassified_attempts"].append(
                        {"id": attempt_row["id"], "title": attempt_row["title"], "record_id": record_row["id"]}
                    )
                    continue
                difficulty = recalculated_difficulty(record, raw_problem, classified)
                related_topics = [classified.topic]
                raw_problem.update(
                    {
                        "related_topics": related_topics,
                        "course_chapter": classified.chapter,
                        "course_template_id": classified.template_id,
                        "difficulty": difficulty["label"],
                        "difficulty_score": round(difficulty["score"], 2),
                        "difficulty_source": difficulty["source"],
                        "difficulty_confidence": difficulty["confidence"],
                    }
                )
                if is_plan_completion(record):
                    raw_problem["difficulty_source"] = "planned_homework"
                connection.execute(
                    """
                    UPDATE problem_attempts
                    SET difficulty_label = ?, difficulty_score = ?, related_topics_json = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (
                        difficulty["label"],
                        round(difficulty["score"], 2),
                        dumps(related_topics),
                        dumps(raw_problem),
                        attempt_row["id"],
                    ),
                )
                if index < len(raw_problems):
                    raw_problems[index] = raw_problem
                classified_attempts.append(classified)
                audit["attempts_updated"] += 1
                audit["topic_attempt_counts"][f"{classified.chapter} / {classified.topic}"] += 1

            dominant = Counter(item for item in classified_attempts).most_common(1)
            explicit_chapter = chapter_from_text(flatten([record.get("chapter"), record.get("module"), record.get("topic"), record.get("note")]))
            record_classified = dominant[0][0] if dominant else classify_ds_text(classification_text(record))
            if record_classified or explicit_chapter:
                record["module"] = explicit_chapter or record_classified.chapter
                record["topic"] = (
                    f"{explicit_chapter}课程覆盖"
                    if explicit_chapter and not classified_attempts
                    else record_classified.topic if record_classified else f"{explicit_chapter}课程覆盖"
                )
                record["related_topics"] = list(dict.fromkeys(item.topic for item in classified_attempts)) or (
                    [record_classified.topic] if record_classified else []
                )
                record["course_classification_source"] = "nine_chapter_ppt_taxonomy"
                if raw_problems:
                    record["problems"] = raw_problems
                connection.execute(
                    """
                    UPDATE learning_records
                    SET module_name = ?, topic_name = ?, activity = ?, source = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (
                        record["module"], record["topic"], record.get("activity"), record.get("source"),
                        dumps(record), record_row["id"],
                    ),
                )
                audit["records_updated"] += 1
                audit["chapter_record_counts"][record["module"]] += 1

    audit["topic_attempt_counts"] = dict(audit["topic_attempt_counts"])
    audit["chapter_record_counts"] = dict(audit["chapter_record_counts"])
    return audit


def objective_difficulty_evidence(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, list[float]]:
    evidence: dict[str, list[float]] = defaultdict(list)
    with connect(db_path) as connection:
        attempts = connection.execute(
            """
            SELECT p.difficulty_score, p.related_topics_json, p.raw_json, r.raw_json AS record_json
            FROM problem_attempts p
            JOIN learning_records r ON r.id = p.record_id
            WHERE r.subject_name = ?
            """,
            (SUBJECT,),
        ).fetchall()
        for row in attempts:
            problem = json.loads(row["raw_json"])
            record = json.loads(row["record_json"])
            if is_ai_generated(record, problem):
                continue
            source = str(problem.get("difficulty_source") or "")
            if source in {
                "course_topic_baseline", "keyword_rule", "default_medium",
                "planned_homework", "planned_reference",
            }:
                continue
            score = row["difficulty_score"]
            if not isinstance(score, (int, float)):
                continue
            for topic in json.loads(row["related_topics_json"] or "[]"):
                evidence[str(topic)].append(float(score))

        bank_rows = connection.execute(
            """
            SELECT p.difficulty_score, p.topic_hint, s.source_type
            FROM practice_problems p
            LEFT JOIN practice_sources s ON s.id = p.source_id
            WHERE p.subject_hint LIKE ?
              AND COALESCE(s.source_type, '') NOT IN ('web_gpt_pdf', 'ai_generated', 'llm_generated')
            """,
            (f"%{SUBJECT}%",),
        ).fetchall()
        for row in bank_rows:
            classified = classify_ds_text(str(row["topic_hint"] or ""))
            if classified and isinstance(row["difficulty_score"], (int, float)):
                evidence[classified.topic].append(float(row["difficulty_score"]))
    return evidence


def update_model_mastery_and_difficulty(audit: dict[str, Any], db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    records = load_raw_records(db_path)
    subject = next(item for item in model["subjects"] if item["name"] == SUBJECT)
    for module in subject["modules"]:
        for topic in module["topics"]:
            topic["mastery_prior"] = DEFAULT_MASTERY_PRIOR.get(topic["name"], 0.12)
    states = {
        (item["module"], item["topic"]): item
        for item in bkt_topic_states(model, records)
        if item["subject"] == SUBJECT
    }
    difficulty_evidence = objective_difficulty_evidence(db_path)
    result_topics = []
    for module in subject["modules"]:
        for topic in module["topics"]:
            key = (module["name"], topic["name"])
            state = states.get(key)
            observations = int(state.get("observation_count", 0)) if state else 0
            if observations:
                topic["mastery"] = round(float(state["mastery_probability"]), 4)
            values = difficulty_evidence.get(topic["name"], [])
            baseline = float(DEFAULT_DIFFICULTY.get(topic["name"], 60))
            if values:
                evidence_median = median(values)
                blend = min(0.85, 0.35 + 0.1 * len(values))
                standard = (1 - blend) * baseline + blend * evidence_median
                source = f"课程题型基准与{len(values)}条非AI真实/可信题目证据加权"
            else:
                standard = baseline
                source = "九章课件题型基准；暂无非AI客观难度证据"
            topic["difficulty"] = round(standard / 100, 4)
            topic["difficulty_standard"] = {
                "score": round(standard, 1),
                "baseline": baseline,
                "objective_evidence_count": len(values),
                "objective_evidence_median": round(median(values), 1) if values else None,
                "source": source,
            }
            result_topics.append(
                {
                    "chapter": module["name"],
                    "topic": topic["name"],
                    "mastery": round(float(topic["mastery"]) * 100, 1),
                    "observations": observations,
                    "difficulty_standard": round(standard, 1),
                    "objective_evidence_count": len(values),
                }
            )
        module["mastery"] = round(sum(float(item["mastery"]) for item in module["topics"]) / len(module["topics"]), 4)
        module["difficulty_standard"] = round(
            sum(float(item["difficulty"]) for item in module["topics"]) / len(module["topics"]) * 100,
            1,
        )
    subject["mastery"] = round(sum(float(item["mastery"]) for item in subject["modules"]) / len(subject["modules"]), 4)
    subject["recalibration_note"] = "历史数据结构题目已按九章课件重新分类；AI生成题参与掌握度，不参与客观难度标尺。"
    MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with connect(db_path) as connection:
        subject_id = connection.execute("SELECT id FROM subjects WHERE name = ?", (SUBJECT,)).fetchone()["id"]
        connection.execute("DELETE FROM modules WHERE subject_id = ?", (subject_id,))
        import_model_json(connection, model)

    audit["recalculated_topics"] = result_topics
    audit["chapter_summary"] = [
        {
            "chapter": module["name"],
            "mastery": round(float(module["mastery"]) * 100, 1),
            "difficulty_standard": module["difficulty_standard"],
        }
        for module in subject["modules"]
    ]
    return audit


def recalibrate_ds_history(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    audit = reclassify_records(db_path)
    audit = update_model_mastery_and_difficulty(audit, db_path)
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


if __name__ == "__main__":
    result = recalibrate_ds_history()
    print(json.dumps({
        "records_updated": result["records_updated"],
        "attempts_updated": result["attempts_updated"],
        "unclassified_attempts": len(result["unclassified_attempts"]),
        "chapter_summary": result["chapter_summary"],
    }, ensure_ascii=False))
