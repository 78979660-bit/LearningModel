from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from study_app.core.practice_bank import known_template_ids, template_for_topic
from study_app.core.practice_seed_bank import PRACTICE_SEEDS
from study_app.data.database import (
    DEFAULT_DB_PATH,
    connect,
    connect_readonly,
    dumps,
    ensure_seeded_database,
    initialize_database,
    require_initialized_database,
)
from study_app.core.ds_course_taxonomy import TEMPLATE_INFO
from study_app.core.discrete_math_taxonomy import DISCRETE_TEMPLATE_INFO


TEMPLATE_SUBJECT_HINTS = {
    "DS-AVL-ROT": ("数据结构与算法基础", "AVL 树"),
    "DS-BST-OPS": ("数据结构与算法基础", "二叉搜索树"),
    "DS-HASH-ASL": ("数据结构与算法基础", "哈希表"),
    "ALG-KMP-PREFIX": ("数据结构与算法基础", "KMP 算法"),
    "ALG-COMPLEXITY": ("数据结构与算法基础", "复杂度分析"),
    "CS-CODING-PRACTICE": ("计算机科学", "编程实践与实现训练"),
    "CS-ALGORITHM-DESIGN": ("计算机科学", "算法设计与OJ训练"),
    "CS-OJ-PRACTICE": ("计算机科学", "算法设计与OJ训练"),
    "CPP-OOP-PRACTICE": ("高级程序设计", "面向对象综合实践"),
    "CALC-GAUSS-FLUX": ("高等数学", "曲线/曲面积分"),
    "CALC-TRIPLE-INTEGRAL": ("高等数学", "三重积分"),
    "CALC-SERIES": ("高等数学", "级数"),
    "CHEM-KINETICS": ("化学原理", "化学动力学"),
    "CHEM-EQUILIBRIUM": ("化学原理", "化学平衡与相平衡"),
    "PHYS-MODELING": ("大学物理学", "物理建模"),
    "PHYS-RELATIVITY": ("大学物理学", "狭义相对论"),
    "PHYS-REL-EVENTS": ("大学物理学", "相对论时空观"),
    "PHYS-REL-LIFETIME": ("大学物理学", "时间膨胀与长度收缩"),
    "PHYS-REL-VELOCITY": ("大学物理学", "相对论速度变换"),
    "PHYS-REL-DOPPLER": ("大学物理学", "相对论 Doppler 效应"),
    "PHYS-REL-DYNAMICS": ("大学物理学", "相对论动力学"),
    "PHYS-REL-DECAY": ("大学物理学", "相对论粒子衰变"),
    "PHYS-THERMO-ENTROPY": ("大学物理学", "熵与熵增原理"),
    "PHYS-TEMP-EOS": ("\u5927\u5b66\u7269\u7406\u5b66", "\u6e29\u5ea6\u4e0e\u6c14\u4f53\u72b6\u6001\u65b9\u7a0b"),
    "PHYS-FIRST-LAW": ("\u5927\u5b66\u7269\u7406\u5b66", "\u70ed\u529b\u5b66\u7b2c\u4e00\u5b9a\u5f8b"),
    "PHYS-SECOND-LAW-ENGINE": ("\u5927\u5b66\u7269\u7406\u5b66", "\u7b2c\u4e8c\u5b9a\u5f8b\u4e0e\u70ed\u673a"),
    "PHYS-THERMO-PROCESS": ("大学物理学", "气体热力学过程"),
    "PHYS-THERMO-POTENTIAL": ("大学物理学", "热力学势与 Maxwell 关系"),
    "PHYS-PHASE-TRANSITION": ("大学物理学", "相变与 Clapeyron 方程"),
    "PHYS-STATISTICAL": ("大学物理学", "气体动理论与统计分布"),
    "PHYS-QUANTUM-PHENOMENA": ("大学物理学", "量子现象"),
    "PHYS-SCHRODINGER": ("大学物理学", "Schrodinger 方程"),
    "PHYS-QUANTUM-STATISTICS": ("大学物理学", "量子统计"),
    "PHYS-ATOMIC": ("大学物理学", "原子物理"),
    "PHYS-NUCLEAR": ("大学物理学", "核物理"),
    "PHYS-PARTICLE": ("大学物理学", "粒子物理"),
    "GEN-MIXED-PRACTICE": ("", "通用混合练习"),
}

TEMPLATE_SUBJECT_HINTS.update(
    {template_id: (subject, topic) for template_id, (subject, topic, _description) in TEMPLATE_INFO.items()}
)
TEMPLATE_SUBJECT_HINTS.update(
    {
        template_id: ("离散数学", module)
        for template_id, (module, _description) in DISCRETE_TEMPLATE_INFO.items()
    }
)


from study_app.core.composite_templates import COMPOSITE_TEMPLATES

TEMPLATE_SUBJECT_HINTS.update(
    {
        item.template_id: (item.subject, " + ".join(item.components))
        for item in COMPOSITE_TEMPLATES
    }
)


def seed_practice_bank(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, int]:
    path = initialize_database(db_path)
    with connect(path) as connection:
        template_count = 0
        problem_count = 0
        for template_id in sorted(known_template_ids()):
            subject_hint, topic_hint = TEMPLATE_SUBJECT_HINTS.get(template_id, ("", ""))
            _, description = template_for_topic(topic_hint or template_id, subject_hint or None)
            if template_id == "GEN-MIXED-PRACTICE":
                description = "概念判定、基础计算/推导、混合应用与错因归类"
            connection.execute(
                """
                INSERT INTO practice_templates(
                    template_id, subject_hint, topic_hint, title, description,
                    generation_rules_json, source_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_id) DO UPDATE SET
                    subject_hint = excluded.subject_hint,
                    topic_hint = excluded.topic_hint,
                    title = excluded.title,
                    description = excluded.description,
                    generation_rules_json = excluded.generation_rules_json,
                    source_json = excluded.source_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    template_id,
                    subject_hint,
                    topic_hint,
                    template_id,
                    description,
                    dumps(default_generation_rules()),
                    dumps({"kind": "built_in_template"}),
                ),
            )
            template_count += 1

        for seed in PRACTICE_SEEDS:
            source_id = upsert_practice_source(
                connection,
                "seed",
                seed.source_note,
                "",
                "来自历史记录或公开资料抽象的题型种子。",
            )
            subject_hint, topic_hint = TEMPLATE_SUBJECT_HINTS.get(seed.template_id, ("", ""))
            connection.execute(
                """
                INSERT INTO practice_problems(
                    template_id, title, statement, answer_outline,
                    common_errors_json, difficulty_score, difficulty_source,
                    subject_hint, topic_hint, tags_json, source_id, source_note,
                    raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_id, title) DO UPDATE SET
                    statement = excluded.statement,
                    common_errors_json = excluded.common_errors_json,
                    difficulty_score = excluded.difficulty_score,
                    difficulty_source = excluded.difficulty_source,
                    subject_hint = excluded.subject_hint,
                    topic_hint = excluded.topic_hint,
                    tags_json = excluded.tags_json,
                    source_id = excluded.source_id,
                    source_note = excluded.source_note,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    seed.template_id,
                    seed.title,
                    seed.seed_statement,
                    "这是题型种子，不提供固定答案；外部 LLM 应生成同型变式题并给出答案步骤。",
                    dumps(common_errors_for_seed(seed.tested_points)),
                    seed.target_difficulty,
                    "seed_estimate",
                    subject_hint,
                    topic_hint,
                    dumps(list(seed.tested_points)),
                    source_id,
                    seed.source_note,
                    dumps(
                        {
                            "template_id": seed.template_id,
                            "title": seed.title,
                            "tested_points": list(seed.tested_points),
                            "source_note": seed.source_note,
                        }
                    ),
                ),
            )
            problem_count += 1
    return {"templates": template_count, "problems": problem_count}


def default_generation_rules() -> list[str]:
    return [
        "题目难度应贴近参考难度，允许上下浮动 5 分。",
        "每题给出题面、考查点、预估难度、标准答案或关键步骤。",
        "不要直接重复教材原题，可以生成同型变式题。",
        "题目应能判断做对或有误，并能归纳错因。",
        "若最近错因与模板相关，优先围绕该错因生成题目。",
    ]


def common_errors_for_seed(points: tuple[str, ...]) -> list[str]:
    joined = " ".join(points)
    if any(key in joined for key in ["区域", "曲面", "通量", "法向", "奇点"]):
        return ["区域或方向判断错误", "公式适用条件误判", "边界或特殊点遗漏"]
    if any(key in joined for key in ["AVL", "旋转", "哈希", "KMP", "复杂度"]):
        return ["过程追踪遗漏", "边界情况误判", "复杂度或计数错误"]
    if any(key in joined for key in ["反应", "Arrhenius", "半衰期"]):
        return ["反应级数判断错误", "公式适用条件误判", "单位或代入计算错误"]
    if any(key in joined for key in ["振动", "能量", "物理"]):
        return ["模型选择错误", "近似条件误判", "单位或符号错误"]
    return ["概念适用条件误判", "计算或推导步骤遗漏"]


def upsert_practice_source(
    connection,
    source_type: str,
    title: str,
    url: str = "",
    note: str = "",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO practice_sources(source_type, title, url, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_type, title, url) DO UPDATE SET
            note = excluded.note
        RETURNING id
        """,
        (source_type, title, url or "", note),
    )
    return int(cursor.fetchone()["id"])


def list_practice_templates(db_path: Path | str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT template_id, subject_hint, topic_hint, title, description, generation_rules_json
            FROM practice_templates
            ORDER BY template_id
            """
        ).fetchall()
    return [decode_template_row(row) for row in rows]


def find_practice_problems(
    template_id: str | None = None,
    subject: str | None = None,
    topic: str | None = None,
    difficulty: float | None = None,
    limit: int = 8,
    prefer_real_sources: bool = True,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    clauses = [
        "practice_problems.title NOT LIKE '%???%'",
        "practice_problems.statement NOT LIKE '%???%'",
        "COALESCE(practice_problems.answer_outline, '') NOT LIKE '%???%'",
        "COALESCE(practice_problems.source_note, '') NOT LIKE '%???%'",
        "COALESCE(practice_sources.title, '') NOT LIKE '%???%'",
        "COALESCE(practice_sources.note, '') NOT LIKE '%???%'",
    ]
    params: list[Any] = []
    if template_id:
        clauses.append("practice_problems.template_id = ?")
        params.append(template_id)
    if subject:
        clauses.append("practice_problems.subject_hint LIKE ?")
        params.append(f"%{subject}%")
    if topic:
        clauses.append(
            "(practice_problems.topic_hint LIKE ? OR practice_problems.title LIKE ? "
            "OR practice_problems.tags_json LIKE ? OR practice_problems.statement LIKE ?)"
        )
        needle = f"%{topic}%"
        params.extend([needle, needle, needle, needle])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(int(limit))
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT practice_problems.*, practice_sources.source_type, practice_sources.title AS source_title,
                   practice_sources.url AS source_url
            FROM practice_problems
            LEFT JOIN practice_sources ON practice_sources.id = practice_problems.source_id
            {where}
            ORDER BY
                CASE
                    WHEN ? IS NULL THEN 0
                    WHEN ABS(practice_problems.difficulty_score - ?) <= 12 THEN 0
                    WHEN ABS(practice_problems.difficulty_score - ?) <= 20 THEN 1
                    ELSE 2
                END,
                {source_priority_order(prefer_real_sources)},
                CASE WHEN ? IS NULL THEN 0 ELSE ABS(practice_problems.difficulty_score - ?) END,
                practice_problems.template_id,
                practice_problems.id
            LIMIT ?
            """,
            build_problem_query_params(params, difficulty),
        ).fetchall()
    return [decode_problem_row(row) for row in rows]


def build_problem_query_params(params: list[Any], difficulty: float | None) -> list[Any]:
    limit = params.pop()
    return [*params, difficulty, difficulty, difficulty, difficulty, difficulty, limit]


def source_priority_order(prefer_real_sources: bool) -> str:
    if not prefer_real_sources:
        return "0"
    return """
                CASE
                    WHEN practice_sources.source_type IN (
                        'learning_record', 'uploaded_homework', 'uploaded_classroom',
                        'classroom_exercise', 'homework_upload', 'assignment_upload',
                        'manual_upload', 'trusted_university'
                    ) THEN 0
                    WHEN practice_sources.source_type = 'seed'
                         AND (
                            practice_problems.source_note LIKE '%上传%'
                            OR practice_problems.source_note LIKE '%课堂%'
                            OR practice_problems.source_note LIKE '%练习%'
                            OR practice_problems.source_note LIKE '%错因%'
                            OR practice_problems.source_note LIKE '%LeetCode%'
                         ) THEN 1
                    WHEN practice_sources.source_type = 'seed' THEN 3
                    WHEN practice_sources.source_type IN ('web_gpt_pdf', 'ai_generated', 'llm_generated') THEN 9
                    ELSE 4
                END
    """


def practice_context(
    template_id: str | None = None,
    subject: str | None = None,
    topic: str | None = None,
    difficulty: float | None = None,
    limit: int = 4,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    inferred_template_id = template_id
    if not inferred_template_id and topic:
        inferred_template_id = template_for_topic(topic)[0]
    templates = list_practice_templates(db_path)
    template = next((item for item in templates if item["template_id"] == inferred_template_id), None)
    problems = find_practice_problems(
        template_id=inferred_template_id,
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        limit=limit,
        db_path=db_path,
    )
    if not problems and inferred_template_id:
        problems = find_practice_problems(
            template_id=inferred_template_id,
            subject=subject,
            difficulty=difficulty,
            limit=limit,
            db_path=db_path,
        )
    return {
        "template": template,
        "target_difficulty": difficulty,
        "seed_problems": problems,
        "generation_rules": template["generation_rules"] if template else default_generation_rules(),
    }


def list_local_practice_candidates(
    *,
    subject: str,
    template_id: str = "",
    topic: str = "",
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Return local paper candidates without initializing or mutating the database."""
    clauses = [
        "practice_problems.title NOT LIKE '%???%'",
        "practice_problems.statement NOT LIKE '%???%'",
        "COALESCE(practice_problems.answer_outline, '') NOT LIKE '%???%'",
    ]
    params: list[Any] = []
    normalized_subject = str(subject or "").strip()
    if normalized_subject:
        clauses.append("practice_problems.subject_hint = ?")
        params.append(normalized_subject)
    normalized_template = str(template_id or "").strip()
    if normalized_template:
        clauses.append("practice_problems.template_id = ?")
        params.append(normalized_template)
    normalized_topic = str(topic or "").strip()
    if normalized_topic:
        clauses.append(
            "(practice_problems.topic_hint LIKE ? OR practice_problems.tags_json LIKE ? "
            "OR practice_problems.title LIKE ? OR practice_problems.statement LIKE ?)"
        )
        needle = f"%{normalized_topic}%"
        params.extend([needle, needle, needle, needle])
    where = " AND ".join(clauses)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT practice_problems.*, practice_sources.source_type,
                   practice_sources.title AS source_title,
                   practice_sources.url AS source_url
            FROM practice_problems
            LEFT JOIN practice_sources ON practice_sources.id = practice_problems.source_id
            WHERE {where}
            ORDER BY practice_problems.template_id, practice_problems.title,
                     practice_problems.statement, practice_problems.id
            """,
            params,
        ).fetchall()
    return [decode_problem_row(row) for row in rows]


def import_practice_problem(
    problem: dict[str, Any],
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    connection=None,
) -> int:
    if connection is not None:
        return _import_practice_problem(connection, problem)
    path = require_initialized_database(db_path)
    with connect(path) as owned_connection:
        return _import_practice_problem(owned_connection, problem)


def _import_practice_problem(connection, problem: dict[str, Any]) -> int:
    from study_app.data.text_integrity import validate_text_integrity

    validate_text_integrity(problem, context="题库题目")
    template_id = str(problem.get("template_id") or "").strip()
    if not template_id:
        topic_text = " ".join(
            str(problem.get(key) or "")
            for key in ("topic", "topic_hint", "title", "statement")
        )
        template_id = template_for_topic(topic_text)[0]
    if template_id not in known_template_ids():
        template_id = template_for_topic(str(problem.get("statement") or problem.get("title") or ""))[0]
    title = str(problem.get("title") or "").strip()
    statement = str(problem.get("statement") or "").strip()
    if not title:
        raise ValueError("problem.title is required")
    if not statement:
        raise ValueError("problem.statement is required")
    difficulty = float(problem.get("difficulty_score") or problem.get("target_difficulty") or 60)
    difficulty = max(0.0, min(100.0, difficulty))
    tags = problem.get("tags") or problem.get("tested_points") or []
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.replace("、", ",").split(",") if item.strip()]
    source = problem.get("source") or {}
    if isinstance(source, str):
        source = {"title": source}
    source_id = upsert_practice_source(
        connection,
        str(source.get("type") or problem.get("source_type") or "manual"),
        str(source.get("title") or problem.get("source_note") or "手动导入"),
        str(source.get("url") or ""),
        str(source.get("note") or ""),
    )
    cursor = connection.execute(
        """
        INSERT INTO practice_problems(
            template_id, title, statement, answer_outline,
            common_errors_json, difficulty_score, difficulty_source,
            subject_hint, topic_hint, tags_json, source_id, source_note,
            raw_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(template_id, title) DO UPDATE SET
            statement = excluded.statement,
            answer_outline = excluded.answer_outline,
            common_errors_json = excluded.common_errors_json,
            difficulty_score = excluded.difficulty_score,
            difficulty_source = excluded.difficulty_source,
            subject_hint = excluded.subject_hint,
            topic_hint = excluded.topic_hint,
            tags_json = excluded.tags_json,
            source_id = excluded.source_id,
            source_note = excluded.source_note,
            raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (
            template_id,
            title,
            statement,
            str(problem.get("answer_outline") or ""),
            dumps(problem.get("common_errors") or []),
            difficulty,
            str(problem.get("difficulty_source") or "manual"),
            str(problem.get("subject") or problem.get("subject_hint") or ""),
            str(problem.get("topic") or problem.get("topic_hint") or ""),
            dumps(tags),
            source_id,
            str(problem.get("source_note") or source.get("title") or ""),
            dumps(problem),
        ),
    )
    return int(cursor.fetchone()["id"])


def ensure_practice_bank_seeded(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    path = ensure_seeded_database(db_path)
    return ensure_practice_templates_seeded(path)


def ensure_practice_templates_seeded(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    path = initialize_database(db_path)
    with connect(path) as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM practice_templates").fetchone()["count"]
    if count == 0:
        seed_practice_bank(path)
    return path


def decode_template_row(row) -> dict[str, Any]:
    return {
        "template_id": row["template_id"],
        "subject_hint": row["subject_hint"],
        "topic_hint": row["topic_hint"],
        "title": row["title"],
        "description": row["description"],
        "generation_rules": loads_json(row["generation_rules_json"], []),
    }


def decode_problem_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "template_id": row["template_id"],
        "title": row["title"],
        "statement": row["statement"],
        "answer_outline": row["answer_outline"],
        "common_errors": loads_json(row["common_errors_json"], []),
        "difficulty_score": row["difficulty_score"],
        "difficulty_source": row["difficulty_source"],
        "subject_hint": row["subject_hint"],
        "topic_hint": row["topic_hint"],
        "tags": loads_json(row["tags_json"], []),
        "raw": loads_json(row["raw_json"], {}),
        "source": {
            "type": row["source_type"],
            "title": row["source_title"],
            "url": row["source_url"],
            "note": row["source_note"],
        },
    }


def loads_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
