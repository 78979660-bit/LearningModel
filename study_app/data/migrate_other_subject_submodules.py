from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, set_setting
from study_app.data.model_progress_sync import MODEL_PATH, sync_topic_statuses_to_sqlite


HIGH_MATH = "\u9ad8\u7b49\u6570\u5b66"
DS = "\u6570\u636e\u7ed3\u6784\u4e0e\u7b97\u6cd5\u57fa\u7840"
OLD_SORT = "\u5f52\u5e76\u3001\u57fa\u6570\u4e0e\u5916\u90e8\u6392\u5e8f"
INTERNAL_SORT = "\u5f52\u5e76\u4e0e\u57fa\u6570\u6392\u5e8f"
EXTERNAL_SORT = "\u5916\u90e8\u6392\u5e8f"

CUSTOM_GROUPS: dict[tuple[str, str], list[tuple[str, list[str]]]] = {
    ("\u5927\u5b66\u7269\u7406\u5b66", "\u7b2c3\u7ae0\uff1a\u8d28\u70b9\u52a8\u529b\u5b66"): [
        ("\u529b\u4e0e\u8fd0\u52a8\u65b9\u7a0b", ["\u725b\u987f\u8fd0\u52a8\u5b9a\u5f8b\u4e0e\u53d7\u529b\u5206\u6790", "\u975e\u60ef\u6027\u7cfb\u4e0e\u60ef\u6027\u529b"]),
        ("\u529f\u4e0e\u80fd\u91cf", ["\u529f\u3001\u52bf\u80fd\u4e0e\u673a\u68b0\u80fd"]),
        ("\u89d2\u52a8\u91cf", ["\u89d2\u52a8\u91cf\u4e0e\u5b88\u6052"]),
    ],
    ("\u5927\u5b66\u7269\u7406\u5b66", "\u7b2c6\u7ae0\uff1a\u521a\u4f53\u8fd0\u52a8\u5b66\u4e0e\u52a8\u529b\u5b66"): [
        ("\u521a\u4f53\u8fd0\u52a8\u5b66", ["\u521a\u4f53\u81ea\u7531\u5ea6\u4e0e\u4f53\u5750\u6807\u7cfb", "\u5e73\u52a8\u3001\u5b9a\u8f74\u8f6c\u52a8\u4e0e\u5e73\u9762\u5e73\u884c\u8fd0\u52a8", "Euler \u89d2\u4e0e\u4e00\u822c\u8fd0\u52a8"]),
        ("\u521a\u4f53\u52a8\u529b\u5b66", ["\u8f6c\u52a8\u60ef\u91cf\u4e0e\u60ef\u91cf\u5f20\u91cf", "\u89d2\u52a8\u91cf\u3001\u529b\u77e9\u4e0e\u521a\u4f53\u80fd\u91cf"]),
    ],
    ("\u5927\u5b66\u7269\u7406\u5b66", "\u7b2c9\u7ae0\uff1a\u72ed\u4e49\u76f8\u5bf9\u8bba"): [
        ("\u76f8\u5bf9\u8bba\u65f6\u7a7a\u89c2", ["\u76f8\u5bf9\u6027\u539f\u7406\u4e0e\u5149\u901f\u4e0d\u53d8", "Lorentz \u53d8\u6362", "\u65f6\u95f4\u81a8\u80c0\u4e0e\u957f\u5ea6\u6536\u7f29"]),
        ("\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66", ["\u76f8\u5bf9\u8bba\u52a8\u529b\u5b66\u57fa\u7840"]),
    ],
    ("\u5927\u5b66\u7269\u7406\u5b66", "\u7b2c11\u7ae0\uff1a\u70ed\u529b\u5b66\u7b2c\u4e00\u5b9a\u5f8b"): [
        ("\u7b2c\u4e00\u5b9a\u5f8b\u4e0e\u5185\u80fd", ["\u70ed\u91cf\u3001\u529f\u4e0e\u5185\u80fd", "\u70ed\u529b\u5b66\u7b2c\u4e00\u5b9a\u5f8b"]),
        ("\u7406\u60f3\u6c14\u4f53\u8fc7\u7a0b", ["\u7406\u60f3\u6c14\u4f53\u8fc7\u7a0b", "\u70ed\u5bb9\u4e0e\u7edd\u70ed\u8fc7\u7a0b"]),
    ],
    ("\u5316\u5b66\u539f\u7406", "\u7b2c12\u7ae0\uff1a\u76f8\u5e73\u8861\u4e0e\u76f8\u53d8\u4e13\u9898"): [
        ("\u4e8c\u5143\u7cfb\u76f8\u5e73\u8861", ["\u4e8c\u5143\u7cfb\u76f8\u5e73\u8861\u65b9\u7a0b", "\u6c14-\u6db2\u76f8\u5e73\u8861\u65b9\u7a0b", "\u56fa-\u6db2\u76f8\u5e73\u8861\u65b9\u7a0b"]),
        ("\u76f8\u53d8\u4e0eEhrenfest\u65b9\u7a0b", ["\u4e00\u7ea7\u76f8\u53d8\u4e0e\u4e8c\u7ea7\u76f8\u53d8", "Ehrenfest \u65b9\u7a0b"]),
    ],
    ("\u5316\u5b66\u539f\u7406", "\u7b2c13\u7ae0\uff1a\u5316\u5b66\u53cd\u5e94\u52a8\u529b\u5b66"): [
        ("\u53cd\u5e94\u901f\u7387\u4e0e\u6d4b\u5b9a", ["\u70ed\u529b\u5b66\u5224\u65ad\u4e0e\u52a8\u529b\u5b66\u5224\u65ad\u7684\u533a\u522b", "\u5316\u5b66\u53cd\u5e94\u901f\u7387\u5b9a\u4e49", "\u901f\u7387\u6d4b\u5b9a\u65b9\u6cd5"]),
        ("\u53cd\u5e94\u673a\u7406\u4e0e\u901f\u7387\u5b9a\u5f8b", ["\u57fa\u5143\u53cd\u5e94\u4e0e\u53cd\u5e94\u673a\u7406", "\u8d28\u91cf\u4f5c\u7528\u5b9a\u5f8b", "\u53cd\u5e94\u7ea7\u6570"]),
        ("\u79ef\u5206\u901f\u7387\u65b9\u7a0b\u4e0e\u534a\u8870\u671f", ["\u96f6\u7ea7\u3001\u4e00\u7ea7\u3001\u4e8c\u7ea7\u3001\u4e09\u7ea7\u53cd\u5e94\u79ef\u5206\u5f0f", "\u534a\u8870\u671f\u4e0e\u521d\u59cb\u6d53\u5ea6\u5173\u7cfb"]),
    ],
}


def _split_external_sort(model: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for subject in model.get("subjects", []):
        if subject.get("name") != DS:
            continue
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                if topic.get("name") != OLD_SORT:
                    continue
                topic["name"] = INTERNAL_SORT
                external = dict(topic)
                external["name"] = EXTERNAL_SORT
                external.pop("source_json", None)
                module["topics"].append(external)
                return str(module.get("name") or ""), external
    return None


def migrate(model_path: Path | str = MODEL_PATH, db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    path = Path(model_path)
    model = json.loads(path.read_text(encoding="utf-8"))
    split = _split_external_sort(model)
    changed_by_subject: dict[str, list[tuple[str, str]]] = {}
    report = []
    for subject in model.get("subjects", []):
        subject_name = str(subject.get("name") or "")
        if subject_name == HIGH_MATH:
            continue
        for module in subject.get("modules", []):
            module_name = str(module.get("name") or "")
            definitions = CUSTOM_GROUPS.get((subject_name, module_name))
            if not definitions:
                definitions = [(str(topic.get("name") or ""), [str(topic.get("name") or "")]) for topic in module.get("topics", [])]
            module["submodules"] = [{"name": name, "topics": names, "role": "content"} for name, names in definitions]
            assigned = 0
            for topic in module.get("topics", []):
                topic_name = str(topic.get("name") or "")
                submodule = next((name for name, names in definitions if topic_name in names), "")
                if submodule:
                    topic["submodule"] = submodule
                    assigned += 1
                    changed_by_subject.setdefault(subject_name, []).append((module_name, topic_name))
            report.append({"subject": subject_name, "module": module_name, "submodule_count": len(definitions), "assigned_topics": assigned})

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    if split:
        module_name, external = split
        with connect(db_path) as connection:
            connection.execute("UPDATE topics SET name = ? WHERE name = ?", (INTERNAL_SORT, OLD_SORT))
            row = connection.execute(
                "SELECT id FROM modules WHERE name = ? AND subject_id=(SELECT id FROM subjects WHERE name=?)",
                (module_name, DS),
            ).fetchone()
            if row:
                connection.execute(
                    "INSERT OR IGNORE INTO topics(module_id,name,status,mastery,importance,difficulty,forgetting_risk,source_json) VALUES(?,?,?,?,?,?,?,?)",
                    (row["id"], EXTERNAL_SORT, external.get("status"), external.get("mastery", 0), external.get("importance", 0), external.get("difficulty", 0), external.get("forgetting_risk", 0), dumps(external)),
                )
    for subject_name, changed in changed_by_subject.items():
        sync_topic_statuses_to_sqlite(subject_name, changed, model, db_path)
    audit = {"version": "other_subject_submodules_v1", "modules": report, "split_external_sort": bool(split)}
    set_setting("curriculum_hierarchy_migration:other_subject_submodules_v1", audit, db_path)
    return audit


if __name__ == "__main__":
    print(json.dumps(migrate(), ensure_ascii=True, indent=2))
