from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from study_app.core.study_phase import DEFAULT_FINAL_REVIEW_EXAM_SCOPES, set_subject_exam_scope
from study_app.data.database import DEFAULT_DB_PATH, connect, set_setting
from study_app.data.model_progress_sync import MODEL_PATH, sync_topic_statuses_to_sqlite


SUBJECT = "\u9ad8\u7b49\u6570\u5b66"
LEGACY_COMPOSITE = "\u79ef\u5206\u4e0e\u573a\u8bba\u516c\u5f0f\u7efc\u5408\u9898"
RENAMED_COMPOSITE = "\u79ef\u5206\u516c\u5f0f\u7efc\u5408\u9898"

SUBMODULES: dict[str, list[tuple[str, list[str]]]] = {
    "\u591a\u5143\u51fd\u6570\u5fae\u5206\u5b66": [
        ("\u6982\u5ff5\u3001\u6781\u9650\u4e0e\u8fde\u7eed", ["\u591a\u5143\u51fd\u6570\u6982\u5ff5\u4e0e\u6781\u9650\u8fde\u7eed"]),
        ("\u591a\u5143\u5fae\u5206\u8fd0\u7b97", ["\u504f\u5bfc\u6570\u4e0e\u5168\u5fae\u5206", "\u590d\u5408\u51fd\u6570\u4e0e\u9690\u51fd\u6570\u6c42\u5bfc"]),
        ("\u51e0\u4f55\u5e94\u7528\u4e0e\u6781\u503c", ["\u65b9\u5411\u5bfc\u6570\u4e0e\u68af\u5ea6", "\u591a\u5143\u51fd\u6570\u6781\u503c\u4e0e\u6761\u4ef6\u6781\u503c"]),
    ],
    "\u591a\u5143\u51fd\u6570\u79ef\u5206\u5b66": [
        ("\u91cd\u79ef\u5206", ["\u4e8c\u91cd\u79ef\u5206", "\u4e09\u91cd\u79ef\u5206"]),
        ("\u66f2\u7ebf\u79ef\u5206", ["\u66f2\u7ebf\u79ef\u5206"]),
        ("\u66f2\u9762\u79ef\u5206", ["\u66f2\u9762\u79ef\u5206"]),
        ("\u4e09\u5927\u79ef\u5206\u516c\u5f0f", ["Green \u516c\u5f0f", "Stokes \u516c\u5f0f", "Gauss \u516c\u5f0f"]),
        ("\u5411\u91cf\u573a\u4e0e\u573a\u8bba", ["\u65cb\u5ea6", "\u68af\u5ea6\u3001\u6563\u5ea6\u3001\u65cb\u5ea6\u7684\u7efc\u5408\u7406\u89e3", "\u65e0\u6e90\u573a\u4e0e\u65e0\u65cb\u573a"]),
    ],
    "\u7ea7\u6570": [
        ("\u6570\u9879\u7ea7\u6570\u4e0e\u5224\u655b", ["\u5e38\u6570\u9879\u7ea7\u6570\u4e0e\u5224\u655b\u6cd5", "\u65e0\u7a77\u7ea7\u6570\u4e0e\u90e8\u5206\u548c", "\u51e0\u4f55\u7ea7\u6570", "\u7ea7\u6570\u6536\u655b\u7684\u57fa\u672c\u6027\u8d28", "\u6b63\u9879\u7ea7\u6570\u4e0e\u6bd4\u8f83\u5224\u522b\u6cd5", "Cauchy\u6839\u503c\u5224\u522b\u4e0eD'Alembert\u6bd4\u503c\u5224\u522b", "Dirichlet\u4e0eAbel\u6570\u9879\u7ea7\u6570\u5224\u522b"]),
        ("\u5e42\u7ea7\u6570\u4e0e\u51fd\u6570\u5c55\u5f00", ["\u5e42\u7ea7\u6570\u4e0e\u6536\u655b\u534a\u5f84", "\u51fd\u6570\u5c55\u5f00\u4e3a\u5e42\u7ea7\u6570"]),
        ("\u51fd\u6570\u9879\u7ea7\u6570\u4e0e\u9010\u9879\u8fd0\u7b97", ["\u4e00\u81f4\u6536\u655b\u6027\u4e0e\u9010\u9879\u8fd0\u7b97"]),
        ("\u5085\u91cc\u53f6\u7ea7\u6570", ["\u5085\u91cc\u53f6\u7ea7\u6570"]),
    ],
    "\u7efc\u5408\u5e94\u7528\u4e0e\u9898\u578b": [
        ("\u79ef\u5206\u7efc\u5408", ["\u6362\u5143\u3001\u533a\u57df\u4e0e\u5750\u6807\u7cfb\u9009\u62e9", RENAMED_COMPOSITE]),
        ("\u7ea7\u6570\u7efc\u5408", ["\u7ea7\u6570\u7efc\u5408\u8ba1\u7b97\u4e0e\u8bc1\u660e"]),
    ],
}


def migrate(
    model_path: Path | str = MODEL_PATH,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    path = Path(model_path)
    model = json.loads(path.read_text(encoding="utf-8"))
    changed: list[tuple[str, str]] = []
    report: list[dict[str, Any]] = []
    for subject in model.get("subjects", []):
        if subject.get("name") != SUBJECT:
            continue
        for module in subject.get("modules", []):
            module_name = str(module.get("name") or "")
            definitions = SUBMODULES.get(module_name)
            if not definitions:
                continue
            for topic in module.get("topics", []):
                if topic.get("name") == LEGACY_COMPOSITE:
                    topic["name"] = RENAMED_COMPOSITE
            module["submodules"] = [
                {
                    "name": name,
                    "topics": topic_names,
                    "role": "assessment" if module_name == "\u7efc\u5408\u5e94\u7528\u4e0e\u9898\u578b" else "content",
                }
                for name, topic_names in definitions
            ]
            assigned = 0
            for topic in module.get("topics", []):
                topic_name = str(topic.get("name") or "")
                submodule_name = next((name for name, names in definitions if topic_name in names), "")
                if submodule_name:
                    topic["submodule"] = submodule_name
                    assigned += 1
                    changed.append((module_name, topic_name))
            report.append({"module": module_name, "submodule_count": len(definitions), "assigned_topics": assigned})
        break

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE topics SET name = ?
            WHERE name = ? AND module_id IN (
                SELECT modules.id FROM modules
                JOIN subjects ON subjects.id = modules.subject_id
                WHERE subjects.name = ?
            )
            """,
            (RENAMED_COMPOSITE, LEGACY_COMPOSITE, SUBJECT),
        )
    sync_topic_statuses_to_sqlite(SUBJECT, changed, model, db_path)
    set_subject_exam_scope(SUBJECT, DEFAULT_FINAL_REVIEW_EXAM_SCOPES[SUBJECT])
    audit = {"version": "high_math_submodules_v1", "subject": SUBJECT, "modules": report}
    set_setting("curriculum_hierarchy_migration:high_math_submodules_v1", audit, db_path)
    return audit


if __name__ == "__main__":
    print(json.dumps(migrate(), ensure_ascii=True, indent=2))
