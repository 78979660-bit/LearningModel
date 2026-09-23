from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from study_app.core.physics_ii_mock_exam_reference import (
    PHYSICS_II_ARCHETYPES,
    REFERENCE_NOTE,
    REFERENCE_TITLE,
)
from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, ensure_seeded_database
from study_app.data.practice_repository import seed_practice_bank, upsert_practice_source
from study_app.paths import BACKUPS_DIR, DATA_DIR


REFERENCE_DIR = DATA_DIR / "reference_materials" / "physics_ii_final_samples"


def file_catalog() -> list[dict]:
    return [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "kind": "solution" if path.stem.endswith("sol") else "exam",
        }
        for path in sorted(REFERENCE_DIR.glob("*.pdf"))
    ]


def import_sample_finals() -> dict:
    pdfs = file_catalog()
    if len(pdfs) != 18:
        raise ValueError(f"样卷文件不完整：预期 18 份去重 PDF，实际 {len(pdfs)} 份。")
    if len({item["sha256"] for item in pdfs}) != 18:
        raise ValueError("正式留档中仍存在重复 PDF。")

    backup_dir = BACKUPS_DIR / f"physics_ii_sample_finals_{datetime.now():%Y%m%d_%H%M%S}"
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
        for item in PHYSICS_II_ARCHETYPES:
            statement = (
                f"参考 {REFERENCE_TITLE} 中的同类期末大题，围绕“{item['title']}”构造一题，"
                "设置 2-4 个由概念、推导到计算递进的小问；不得照抄原题数值或完整题面。"
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
                    statement=excluded.statement, answer_outline=excluded.answer_outline,
                    common_errors_json=excluded.common_errors_json,
                    difficulty_score=excluded.difficulty_score,
                    difficulty_source=excluded.difficulty_source,
                    subject_hint=excluded.subject_hint, topic_hint=excluded.topic_hint,
                    tags_json=excluded.tags_json, source_id=excluded.source_id,
                    source_note=excluded.source_note, raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    item["template_id"], item["title"], statement,
                    "题型种子用于约束结构和难度；具体变式题应另行推导并校验答案。",
                    dumps(["物理模型或守恒律选择错误", "边界条件/归一化遗漏", "量子数或符号判断错误"]),
                    item["difficulty"], "trusted_university_final",
                    "大学物理学", item["topic"], dumps(item["tested_points"]),
                    source_id,
                    "来自 9 套南京大学 University Physics II 样卷及答案的题型归纳；"
                    "当前模型未覆盖现代物理时仅作储备，不进入模拟卷。",
                    dumps(item),
                ),
            )

    catalog = {
        "title": REFERENCE_TITLE,
        "note": REFERENCE_NOTE,
        "files": pdfs,
        "archetype_count": len(PHYSICS_II_ARCHETYPES),
        "duplicates_skipped": 20,
        "combined_compilations_skipped": 2,
        "scope_status": "reserve_until_modern_physics_is_in_exam_scope",
        "usage": "真实样卷用于难度、结构和题型标尺；不计为用户作答证据。",
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }
    (REFERENCE_DIR / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "pdf_count": len(pdfs),
        "archetype_count": len(PHYSICS_II_ARCHETYPES),
        "backup_dir": str(backup_dir),
        "scope_status": catalog["scope_status"],
    }


if __name__ == "__main__":
    print(json.dumps(import_sample_finals(), ensure_ascii=False, indent=2))
