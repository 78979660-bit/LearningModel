from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, ensure_seeded_database
from study_app.data.practice_repository import upsert_practice_source


def U(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


SUBJECT = U(r"\u5927\u5b66\u7269\u7406\u5b66")
SOURCE_TITLE = U(r"\u5927\u5b66\u7269\u7406\u76f8\u5bf9\u8bba\u7ec6\u5206\u9898\u578b\u79cd\u5b50 v1")
SOURCE_NOTE = U(
    r"\u6574\u7406\u81ea\u7528\u6237\u5df2\u4e0a\u4f20\u7684\u7b2c9\u7ae0\u7ec3\u4e60\u3001"
    r"\u5927\u5b66\u7269\u7406\u671f\u672b\u6837\u5377\u9898\u578b\u548c\u901a\u7528\u5927\u7269\u8003\u8bd5\u9898\u578b\uff1b"
    r"\u4ec5\u4f5c\u9898\u578b\u3001\u96be\u5ea6\u548c\u8003\u70b9\u7ea6\u675f\uff0c\u4e0d\u76f4\u63a5\u590d\u5236\u539f\u9898\u3002"
)


TEMPLATES: tuple[tuple[str, str, str], ...] = (
    (
        "PHYS-REL-EVENTS",
        U(r"\u76f8\u5bf9\u8bba\u65f6\u7a7a\u4e8b\u4ef6"),
        U(r"\u540c\u65f6\u6027\u3001\u65f6\u7a7a\u95f4\u9694\u3001Lorentz \u53d8\u6362\u548c\u4e8b\u4ef6\u987a\u5e8f\u5224\u65ad"),
    ),
    (
        "PHYS-REL-LIFETIME",
        U(r"\u5bff\u547d\u4e0e\u957f\u5ea6\u6536\u7f29"),
        U(r"\u65f6\u95f4\u81a8\u80c0\u3001\u957f\u5ea6\u6536\u7f29\u3001\u56fa\u6709\u65f6\u95f4/\u56fa\u6709\u957f\u5ea6\u548c\u5bff\u547d\u6982\u7387"),
    ),
    (
        "PHYS-REL-VELOCITY",
        U(r"\u76f8\u5bf9\u8bba\u901f\u5ea6\u53d8\u6362"),
        U(r"\u4e00\u7ef4/\u4e8c\u7ef4\u901f\u5ea6\u5408\u6210\u3001\u53c2\u8003\u7cfb\u4e92\u6362\u3001\u65b9\u5411\u548c\u7b26\u53f7"),
    ),
    (
        "PHYS-REL-DOPPLER",
        U(r"\u76f8\u5bf9\u8bba Doppler \u6548\u5e94"),
        U(r"\u7ea2\u79fb\u3001\u84dd\u79fb\u3001\u9000\u884c\u901f\u5ea6\u53cd\u6f14\u548c\u5149\u8c31\u7ebf\u89c2\u6d4b"),
    ),
    (
        "PHYS-REL-DYNAMICS",
        U(r"\u76f8\u5bf9\u8bba\u80fd\u52a8\u91cf"),
        U(r"\u76f8\u5bf9\u8bba\u52a8\u80fd\u3001\u52a8\u91cf\u3001\u8d28\u80fd\u5173\u7cfb\u548c\u9ad8\u901f\u8fd1\u4f3c"),
    ),
    (
        "PHYS-REL-DECAY",
        U(r"\u76f8\u5bf9\u8bba\u7c92\u5b50\u8870\u53d8"),
        U(r"\u80fd\u91cf\u52a8\u91cf\u5b88\u6052\u3001\u4e8c\u4f53\u8870\u53d8\u3001\u53cd\u51b2\u548c\u4e8c\u7ef4\u8fd0\u52a8\u5b66"),
    ),
)


PROBLEMS: tuple[dict[str, Any], ...] = (
    {
        "template_id": "PHYS-REL-EVENTS",
        "title": U(r"\u95ea\u5149\u4fe1\u53f7\u7684\u540c\u65f6\u6027\u4e0e\u65f6\u7a7a\u95f4\u9694"),
        "topic": U(r"Lorentz \u53d8\u6362"),
        "difficulty": 72,
        "tested_points": [U(r"\u540c\u65f6\u6027"), U(r"\u65f6\u7a7a\u95f4\u9694"), U(r"\u4e8b\u4ef6\u987a\u5e8f")],
        "statement": U(r"\u7ed9\u51fa\u4e24\u95ea\u5149\u4e8b\u4ef6\u5728 S \u7cfb\u4e2d\u7684 \u2206t,\u2206x\uff0c\u8981\u6c42\u5224\u65ad\u95f4\u9694\u7c7b\u578b\u3001\u53d8\u6362\u5230 S' \u540e\u7684\u65f6\u95f4\u987a\u5e8f\uff0c\u5e76\u8bf4\u660e\u662f\u5426\u53ef\u540c\u5730\u6216\u540c\u65f6\u3002"),
    },
    {
        "template_id": "PHYS-REL-EVENTS",
        "title": U(r"\u8fd0\u52a8\u706b\u8f66\u4e2d\u70b9\u95ea\u5149\u4e0e\u4e24\u7aef\u63a5\u6536"),
        "topic": U(r"\u76f8\u5bf9\u6027\u539f\u7406\u4e0e\u5149\u901f\u4e0d\u53d8"),
        "difficulty": 76,
        "tested_points": [U(r"\u5149\u901f\u4e0d\u53d8"), U(r"\u540c\u65f6\u6027\u7834\u7f3a"), U(r"\u56fe\u50cf\u5316\u5efa\u6a21")],
        "statement": U(r"\u706b\u8f66\u4e2d\u70b9\u53d1\u51fa\u5149\u4fe1\u53f7\u5230\u8f66\u5934\u8f66\u5c3e\uff0c\u5206\u522b\u5728\u5730\u9762\u7cfb\u548c\u8f66\u7cfb\u5206\u6790\u63a5\u6536\u987a\u5e8f\u548c\u65f6\u95f4\u95f4\u9694\u3002"),
    },
    {
        "template_id": "PHYS-REL-LIFETIME",
        "title": U(r"\u5927\u6c14\u4e2d \u03bc \u5b50\u5b58\u6d3b\u6982\u7387\u7684\u53cc\u53c2\u8003\u7cfb\u89e3\u91ca"),
        "topic": U(r"\u65f6\u95f4\u81a8\u80c0\u4e0e\u957f\u5ea6\u6536\u7f29"),
        "difficulty": 74,
        "tested_points": [U(r"\u5bff\u547d\u81a8\u80c0"), U(r"\u957f\u5ea6\u6536\u7f29"), U(r"\u6307\u6570\u8870\u53d8")],
        "statement": U(r"\u7ed9\u51fa\u03bc\u5b50\u672c\u5f81\u5bff\u547d\u3001\u9ad8\u5ea6\u548c\u901f\u5ea6\uff0c\u5728\u5730\u9762\u7cfb\u548c\u03bc\u5b50\u7cfb\u5206\u522b\u8ba1\u7b97\u662f\u5426\u53ef\u5230\u8fbe\u5730\u9762\u53ca\u5b58\u6d3b\u6982\u7387\u3002"),
    },
    {
        "template_id": "PHYS-REL-LIFETIME",
        "title": U(r"\u5b87\u5b99\u8239\u81ea\u8eab\u65f6\u95f4\u4e0e\u5730\u7403\u8ddd\u79bb\u4f30\u7b97"),
        "topic": U(r"\u65f6\u95f4\u81a8\u80c0\u4e0e\u957f\u5ea6\u6536\u7f29"),
        "difficulty": 78,
        "tested_points": [U(r"\u56fa\u6709\u65f6\u95f4"), U(r"\u56fa\u6709\u957f\u5ea6"), U(r"\u53cc\u751f\u5b50\u7c7b\u9898\u578b")],
        "statement": U(r"\u5b87\u5b99\u8239\u4ee5\u9ad8\u901f\u98de\u5411\u6052\u661f\uff0c\u7ed9\u51fa\u5730\u7403\u7cfb\u8ddd\u79bb\u548c\u8239\u5458\u7ecf\u5386\u65f6\u95f4\uff0c\u6c42\u901f\u5ea6\u3001\u5730\u7403\u7cfb\u65f6\u95f4\u548c\u8239\u5458\u770b\u5230\u7684\u6536\u7f29\u8ddd\u79bb\u3002"),
    },
    {
        "template_id": "PHYS-REL-VELOCITY",
        "title": U(r"\u4e24\u5217\u8f66\u5782\u76f4\u65b9\u5411\u9ad8\u901f\u8fd0\u52a8\u7684\u76f8\u5bf9\u901f\u5ea6"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 80,
        "tested_points": [U(r"\u4e8c\u7ef4\u901f\u5ea6\u53d8\u6362"), U(r"\u901f\u7387\u4e0d\u8d85\u5149\u901f"), U(r"\u53c2\u8003\u7cfb\u4e92\u6362")],
        "statement": U(r"\u7ed9\u51fa A\u3001B \u76f8\u5bf9\u5730\u9762\u6cbf\u5782\u76f4\u65b9\u5411\u7684\u9ad8\u901f\u8fd0\u52a8\uff0c\u6c42 B \u5728 A \u7cfb\u4e2d\u7684\u901f\u5ea6\u5206\u91cf\u3001\u901f\u7387\u548c\u65b9\u5411\u3002"),
    },
    {
        "template_id": "PHYS-REL-VELOCITY",
        "title": U(r"\u7c92\u5b50\u5728\u4e24\u7ea7\u8fd0\u52a8\u53c2\u8003\u7cfb\u4e2d\u7684\u901f\u5ea6\u53d8\u6362"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 83,
        "tested_points": [U(r"\u901f\u5ea6\u5408\u6210"), U(r"\u53cd\u5411\u53d8\u6362"), U(r"\u5206\u91cf\u53d8\u6362")],
        "statement": U(r"\u7c92\u5b50\u5728 S' \u7cfb\u4e2d\u4ee5\u901f\u7387 u' \u4e0e x' \u8f74\u6210\u89d2\u8fd0\u52a8\uff0cS' \u76f8\u5bf9 S \u9ad8\u901f\u8fd0\u52a8\uff0c\u6c42 S \u7cfb\u4e2d\u7684\u901f\u5ea6\u5206\u91cf\u548c\u89d2\u5ea6\u3002"),
    },
    {
        "template_id": "PHYS-REL-DOPPLER",
        "title": U(r"\u7c7b\u661f\u4f53\u5149\u8c31\u7ea2\u79fb\u53cd\u6f14\u9000\u884c\u901f\u5ea6"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 72,
        "tested_points": [U(r"\u76f8\u5bf9\u8bba Doppler"), U(r"\u7ea2\u79fb"), U(r"\u901f\u5ea6\u53cd\u6f14")],
        "statement": U(r"\u5df2\u77e5\u5b9e\u9a8c\u5ba4\u6ce2\u957f\u4e0e\u89c2\u6d4b\u6ce2\u957f\uff0c\u7528\u76f8\u5bf9\u8bba Doppler \u516c\u5f0f\u6c42\u9000\u884c\u901f\u5ea6\uff0c\u5e76\u4e0e\u975e\u76f8\u5bf9\u8bba\u8fd1\u4f3c\u6bd4\u8f83\u3002"),
    },
    {
        "template_id": "PHYS-REL-DOPPLER",
        "title": U(r"\u5b87\u5b99\u8239\u53d1\u5c04\u4fe1\u53f7\u7684\u63a5\u6536\u9891\u7387\u8c03\u8282"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 76,
        "tested_points": [U(r"\u7ea2\u79fb\u84dd\u79fb"), U(r"\u8fce\u5411/\u8fdc\u79bb\u7b26\u53f7"), U(r"\u9891\u7387\u53d8\u6362")],
        "statement": U(r"\u5b87\u5b99\u8239\u8fdc\u79bb\u6216\u63a5\u8fd1\u5730\u7403\u65f6\u53d1\u51fa\u65e0\u7ebf\u7535\u4fe1\u53f7\uff0c\u8981\u6c42\u6c42\u5730\u9762\u63a5\u6536\u9891\u7387\u4ee5\u53ca\u82e5\u8981\u4fdd\u6301\u63a5\u6536\u9891\u7387\u5e94\u8c03\u6574\u53d1\u5c04\u9891\u7387\u591a\u5c11\u3002"),
    },
    {
        "template_id": "PHYS-REL-DYNAMICS",
        "title": U(r"\u7531\u52a8\u80fd\u53cd\u6c42\u901f\u7387\u3001\u52a8\u91cf\u4e0e\u7ecf\u5178\u8fd1\u4f3c\u8bef\u5dee"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 78,
        "tested_points": [U(r"\u52a8\u80fd"), U(r"\u52a8\u91cf"), U(r"\u7ecf\u5178\u6781\u9650")],
        "statement": U(r"\u7ed9\u51fa\u7c92\u5b50\u52a8\u80fd K = \u03b1 mc^2\uff0c\u6c42 v/c\u3001p/(mc)\uff0c\u5e76\u8bc4\u4f30\u7ecf\u5178\u516c\u5f0f\u7684\u8bef\u5dee\u6216\u77db\u76fe\u3002"),
    },
    {
        "template_id": "PHYS-REL-DYNAMICS",
        "title": U(r"\u9ad8\u901f\u7c92\u5b50\u7684\u8d85\u76f8\u5bf9\u8bba\u52a8\u91cf\u8fd1\u4f3c\u8bc1\u660e"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 84,
        "tested_points": [U(r"\u80fd\u91cf-\u52a8\u91cf\u5173\u7cfb"), U(r"\u8fd1\u4f3c\u5c55\u5f00"), U(r"\u9ad8\u80fd\u6781\u9650")],
        "statement": U(r"\u5bf9 \u03b3 \u8fdc\u5927\u4e8e 1 \u7684\u7c92\u5b50\uff0c\u4ece E^2=p^2c^2+m^2c^4 \u51fa\u53d1\u8bc1\u660e\u52a8\u91cf\u7684\u4e00\u9636\u8fd1\u4f3c\u5f0f\u5e76\u4f30\u8ba1\u8bef\u5dee\u9636\u3002"),
    },
    {
        "template_id": "PHYS-REL-DECAY",
        "title": U(r"\u9759\u6b62\u7c92\u5b50\u4e8c\u4f53\u8870\u53d8\u7684\u4ea7\u7269\u80fd\u91cf"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 86,
        "tested_points": [U(r"\u80fd\u91cf\u5b88\u6052"), U(r"\u52a8\u91cf\u5b88\u6052"), U(r"\u4e8c\u4f53\u8870\u53d8")],
        "statement": U(r"\u9759\u6b62\u6bcd\u7c92\u5b50\u8870\u53d8\u4e3a\u4e24\u4e2a\u4e0d\u540c\u8d28\u91cf\u7684\u5b50\u7c92\u5b50\uff0c\u6c42\u4e24\u8005\u7684\u603b\u80fd\u91cf\u3001\u52a8\u80fd\u548c\u5171\u540c\u52a8\u91cf\u5927\u5c0f\u3002"),
    },
    {
        "template_id": "PHYS-REL-DECAY",
        "title": U(r"\u4e8c\u7ef4\u8870\u53d8\u4e2d\u7684\u52a8\u91cf\u4e09\u89d2\u5f62\u4e0e\u5939\u89d2"),
        "topic": U(r"\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"),
        "difficulty": 90,
        "tested_points": [U(r"\u4e8c\u7ef4\u52a8\u91cf\u5b88\u6052"), U(r"\u76f8\u5bf9\u8bba\u603b\u80fd"), U(r"\u51e0\u4f55\u7ea6\u675f")],
        "statement": U(r"\u8fd0\u52a8\u6bcd\u7c92\u5b50\u8870\u53d8\u4e3a\u4e24\u4e2a\u7c92\u5b50\uff0c\u7ed9\u51fa\u5176\u4e2d\u4e00\u4e2a\u4ea7\u7269\u4e0e\u5165\u5c04\u65b9\u5411\u7684\u5939\u89d2\uff0c\u6c42\u4e24\u4ea7\u7269\u52a8\u80fd\u548c\u53e6\u4e00\u4ea7\u7269\u65b9\u5411\u3002"),
    },
)


def import_relativity_archetypes(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, int]:
    path = ensure_seeded_database(db_path)
    with connect(path) as connection:
        source_id = upsert_practice_source(
            connection,
            "course_archetype",
            SOURCE_TITLE,
            "",
            SOURCE_NOTE,
        )
        for template_id, title, description in TEMPLATES:
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
                    template_id,
                    SUBJECT,
                    title,
                    template_id,
                    description,
                    dumps({"style": "relativity_archetype", "avoid_repeating_same_story": True}),
                    dumps({"kind": "built_in_relativity_archetype_v1"}),
                ),
            )
        for item in PROBLEMS:
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
                    item["template_id"],
                    item["title"],
                    item["statement"],
                    U(r"\u9898\u578b\u79cd\u5b50\u7528\u4e8e\u7ea6\u675f\u7ed3\u6784\u3001\u8003\u70b9\u548c\u96be\u5ea6\uff1b\u5177\u4f53\u53d8\u5f0f\u9898\u5e94\u53e6\u884c\u63a8\u5bfc\u5e76\u6821\u9a8c\u7b54\u6848\u3002"),
                    dumps(
                        [
                            U(r"\u56fa\u6709\u91cf\u8bc6\u522b\u9519\u8bef"),
                            U(r"\u7b26\u53f7\u548c\u65b9\u5411\u53d6\u53cd"),
                            U(r"\u628a\u7ecf\u5178\u516c\u5f0f\u7528\u5230\u9ad8\u901f\u533a"),
                        ]
                    ),
                    item["difficulty"],
                    "course_archetype_relativity",
                    SUBJECT,
                    item["topic"],
                    dumps(item["tested_points"] + [item["template_id"]]),
                    source_id,
                    SOURCE_NOTE,
                    dumps(item),
                ),
            )
    return {
        "templates": len(TEMPLATES),
        "problems": len(PROBLEMS),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    print(json.dumps(import_relativity_archetypes(), ensure_ascii=False, indent=2))
