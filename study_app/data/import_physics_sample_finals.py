from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from study_app.core.physics_mock_exam_reference import (
    EXAM_STRUCTURE,
    PHYSICS_FINAL_ARCHETYPES,
    REFERENCE_NOTE,
    REFERENCE_TITLE,
)
from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, ensure_seeded_database
from study_app.data.practice_repository import seed_practice_bank, upsert_practice_source
from study_app.paths import BACKUPS_DIR, DATA_DIR


REFERENCE_DIR = DATA_DIR / "reference_materials" / "physics_final_samples"


def file_catalog() -> list[dict]:
    result = []
    for path in sorted(REFERENCE_DIR.glob("*.pdf")):
        result.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "kind": "solution" if path.stem.endswith("sol") else "exam",
            }
        )
    return result


def import_sample_finals() -> dict:
    if not REFERENCE_DIR.exists():
        raise FileNotFoundError(REFERENCE_DIR)
    pdfs = file_catalog()
    if len(pdfs) != 18:
        raise ValueError(f"样卷文件不完整：预期 18 份 PDF，实际 {len(pdfs)} 份。")

    backup_dir = BACKUPS_DIR / f"physics_sample_finals_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(DEFAULT_DB_PATH) as source, sqlite3.connect(backup_dir / DEFAULT_DB_PATH.name) as target:
        source.backup(target)

    seed_practice_bank(DEFAULT_DB_PATH)
    path = ensure_seeded_database(DEFAULT_DB_PATH)
    with connect(path) as connection:
        source_id = upsert_practice_source(
            connection,
            "trusted_university",
            REFERENCE_TITLE,
            "",
            REFERENCE_NOTE + f" 本地留档：{REFERENCE_DIR}",
        )
        for item in PHYSICS_FINAL_ARCHETYPES:
            statement = (
                f"参考 {REFERENCE_TITLE} 中的同类期末大题，围绕“{item['title']}”构造一题，"
                "设置 2-4 个由概念判断、公式推导到综合计算递进的小问；不得照抄原题数值。"
            )
            connection.execute(
                """
                INSERT INTO practice_problems(
                    template_id, title, statement, answer_outline,
                    common_errors_json, difficulty_score, difficulty_source,
                    subject_hint, topic_hint, tags_json, source_id, source_note,
                    raw_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                    statement,
                    "题型种子用于约束结构和难度；具体变式题应另行推导并校验答案。",
                    dumps(["建模前提遗漏", "状态量/过程量混淆", "符号、方向或积分边界错误"]),
                    item["difficulty"],
                    "trusted_university_final",
                    "大学物理学",
                    item["topic"],
                    dumps(item["tested_points"]),
                    source_id,
                    f"来自 9 套南京大学 University Physics I 样卷及答案的题型归纳；真实大学期末来源。",
                    dumps(item),
                ),
            )

    catalog = {
        "title": REFERENCE_TITLE,
        "note": REFERENCE_NOTE,
        "structure": EXAM_STRUCTURE,
        "files": pdfs,
        "archetype_count": len(PHYSICS_FINAL_ARCHETYPES),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "usage": "真实样卷用于难度、结构和题型标尺；不计为用户作答证据。",
    }
    (REFERENCE_DIR / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"pdf_count": len(pdfs), "archetype_count": len(PHYSICS_FINAL_ARCHETYPES), "backup_dir": str(backup_dir)}


if __name__ == "__main__":
    print(json.dumps(import_sample_finals(), ensure_ascii=False, indent=2))
