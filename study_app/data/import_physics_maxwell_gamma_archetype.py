from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, ensure_seeded_database
from study_app.data.practice_repository import upsert_practice_source


SUBJECT = "大学物理学"
TEMPLATE_ID = "PHYS-STATISTICAL"
SOURCE_TITLE = "大学物理 Maxwell-Gamma 必考题型种子 2026-06-26"
SOURCE_NOTE = (
    "根据用户确认的大学物理期末要求整理：模拟卷必须出现 Maxwell 速率分布与 "
    "Gamma 函数积分结合题。该种子用于约束题型、难度和考查点，不作为用户作答证据。"
)


def import_physics_maxwell_gamma_archetype(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, object]:
    path = ensure_seeded_database(db_path)
    with connect(path) as connection:
        source_id = upsert_practice_source(
            connection,
            "exam_scope_reference",
            SOURCE_TITLE,
            "docs/maxwell_speed_distribution_gamma_notes.md",
            SOURCE_NOTE,
        )
        connection.execute(
            """
            INSERT INTO practice_templates(
                template_id, subject_hint, topic_hint, title, description,
                generation_rules_json, source_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(template_id) DO UPDATE SET
                subject_hint=excluded.subject_hint,
                topic_hint=excluded.topic_hint,
                title=excluded.title,
                description=excluded.description,
                generation_rules_json=excluded.generation_rules_json,
                source_json=excluded.source_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                TEMPLATE_ID,
                SUBJECT,
                "理想气体微观模型",
                "Maxwell 速率分布、统计平均与气体微观模型",
                "Maxwell 速率分布、Gamma 函数积分、速率矩、分子束通量加权和能量均分。",
                dumps(
                    {
                        "style": "physics_statistical_exam_archetype",
                        "must_include": "Maxwell speed distribution with Gamma-function integral",
                    }
                ),
                dumps({"kind": "built_in_physics_maxwell_gamma_20260626"}),
            ),
        )
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
                statement=excluded.statement,
                answer_outline=excluded.answer_outline,
                common_errors_json=excluded.common_errors_json,
                difficulty_score=excluded.difficulty_score,
                difficulty_source=excluded.difficulty_source,
                subject_hint=excluded.subject_hint,
                topic_hint=excluded.topic_hint,
                tags_json=excluded.tags_json,
                source_id=excluded.source_id,
                source_note=excluded.source_note,
                raw_json=excluded.raw_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                TEMPLATE_ID,
                "Maxwell 速率分布与 Gamma 函数积分",
                (
                    "围绕 Maxwell 速率分布 f(v)=C v^2 exp(-a v^2) 设计一道递进题："
                    "先用 Gamma 函数积分证明归一化，再推导一般速率矩 <v^r>，并进一步求"
                    "平均速率、方均根速率或逸出分子束通量加权分布。题面必须显式出现 "
                    "∫_0^∞ v^n exp(-a v^2) dv 或 Gamma((n+1)/2)。"
                ),
                (
                    "关键步骤：令 a=m/(2kT)，使用 ∫_0^∞ x^n e^{-a x^2}dx="
                    "1/2*a^{-(n+1)/2}*Gamma((n+1)/2)；由归一化确定 C；"
                    "由 <v^r>=∫v^r f(v)dv 得到通式；必要时对分子束分布多乘 v 权重。"
                ),
                dumps(
                    [
                        "把速率分布与速度分量分布混淆",
                        "忘记积分区间是 0 到无穷",
                        "Gamma(5/2)、Gamma(7/2) 递推错误",
                        "分子束题忘记通量加权多乘一个 v",
                    ]
                ),
                88,
                "user_confirmed_exam_requirement",
                SUBJECT,
                "Maxwell 速率分布与 Gamma 函数积分",
                dumps(["Maxwell 速率分布", "Gamma 函数", "速率矩", "第13章", "must_include_mock_exam"]),
                source_id,
                SOURCE_NOTE,
                dumps(
                    {
                        "must_include_in_mock_exam": True,
                        "chapter": 13,
                        "source": "user_request_2026_06_26",
                    }
                ),
            ),
        )
    return {"subject": SUBJECT, "template_id": TEMPLATE_ID, "imported_at": datetime.now().isoformat(timespec="seconds")}


if __name__ == "__main__":
    print(json.dumps(import_physics_maxwell_gamma_archetype(), ensure_ascii=False, indent=2))
