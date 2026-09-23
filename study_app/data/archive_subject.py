from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from study_app.core.study_phase import ARCHIVED_PHASE, _clear_phase_cache, phase_setting_key, set_subject_phase
from study_app.data.database import (
    DEFAULT_DB_PATH,
    archive_active_study_plan,
    get_setting,
    set_setting,
)
from study_app.paths import BACKUPS_DIR, MODEL_PATH


def archive_subject(subject_name: str, reason: str, reference_category: str) -> dict:
    from study_app.data.database import connect_readonly, f5_catalog_is_installed

    with connect_readonly(DEFAULT_DB_PATH) as connection:
        if f5_catalog_is_installed(connection):
            raise RuntimeError(
                "F5 生命周期已启用；旧 archive_subject 路径已禁用，请使用已签核 SubjectChangeSet"
            )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS_DIR / f"archive_subject_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    shutil.copy2(MODEL_PATH, backup_dir / MODEL_PATH.name)
    with sqlite3.connect(DEFAULT_DB_PATH) as source, sqlite3.connect(backup_dir / DEFAULT_DB_PATH.name) as target:
        source.backup(target)

    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    matched = False
    for subject in model.get("subjects", []):
        if subject.get("name") != subject_name:
            continue
        subject["lifecycle"] = {
            "status": ARCHIVED_PHASE,
            "archived_at": date.today().isoformat(),
            "reason": reason,
            "read_only": True,
            "reference_category": reference_category,
            "reference_aliases": ["微积分", "微积分2"],
        }
        matched = True
        break
    if not matched:
        raise ValueError(f"未找到学科：{subject_name}")

    tmp_path = MODEL_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(model, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(MODEL_PATH)

    policy = set_subject_phase(subject_name, ARCHIVED_PHASE)
    policy.update(
        {
            "archive_reason": reason,
            "reference_category": reference_category,
            "reference_aliases": ["微积分", "微积分2"],
            "read_only": True,
        }
    )
    set_setting(phase_setting_key(subject_name), policy)
    _clear_phase_cache()
    archive_active_study_plan(subject_name)
    # A previously cached all-subject plan may still contain this subject.
    archive_active_study_plan(None)

    audit = {
        "subject": subject_name,
        "status": ARCHIVED_PHASE,
        "archived_at": date.today().isoformat(),
        "reason": reason,
        "reference_category": reference_category,
        "backup_dir": str(backup_dir),
        "preserved": ["learning_records", "problem_attempts", "practice_bank", "mastery", "BKT evidence"],
        "disabled": ["mastery updates", "BKT/memory warnings", "daily plans", "practice generation", "period checks"],
    }
    set_setting(f"subject_archive:{subject_name}", audit)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("subject")
    parser.add_argument("--reason", default="考试结束")
    parser.add_argument("--reference-category", default="高等数学")
    args = parser.parse_args()
    print(
        json.dumps(
            archive_subject(args.subject, args.reason, args.reference_category),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
