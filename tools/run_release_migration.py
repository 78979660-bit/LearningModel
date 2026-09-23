from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_source_files(legacy_root: Path) -> dict[str, Path]:
    candidates = {
        "database": legacy_root / "app_data" / "learning_app.sqlite",
        "model": legacy_root / "learning_model_v1.json",
        "records": legacy_root / "learning_records.json",
        "tessdata_chi_sim": legacy_root
        / "app_data"
        / "tesseract"
        / "tessdata"
        / "chi_sim.traineddata",
        "tessdata_eng": legacy_root
        / "app_data"
        / "tesseract"
        / "tessdata"
        / "eng.traineddata",
        "tessdata_osd": legacy_root
        / "app_data"
        / "tesseract"
        / "tessdata"
        / "osd.traineddata",
    }
    return {name: path for name, path in candidates.items() if path.is_file()}


def table_count(connection: sqlite3.Connection, table: str) -> int | None:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if present is None:
        return None
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def database_snapshot(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = (
            "subjects",
            "modules",
            "topics",
            "learning_records",
            "problem_attempts",
            "migration_quarantine",
            "practice_templates",
            "subject_catalog",
            "subject_aliases",
            "subject_capabilities",
            "subject_module_identities",
            "subject_structure_versions",
            "subject_structure_topics",
            "knowledge_topic_registry",
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        schema_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        schema_version = (
            int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
            )
            if schema_table is not None
            else 0
        )
        return {
            "integrity_check": str(integrity[0]) if integrity else None,
            "foreign_key_violations": len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            ),
            "schema_version": schema_version,
            "counts": {table: table_count(connection, table) for table in tables},
        }
    finally:
        connection.close()


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: run_release_migration.py LEGACY_ROOT USER_ROOT RECEIPT_JSON"
        )
    legacy_root = Path(sys.argv[1]).resolve()
    user_root = Path(sys.argv[2]).resolve()
    receipt_path = Path(sys.argv[3]).resolve()
    if not legacy_root.is_dir():
        raise FileNotFoundError(legacy_root)
    if user_root.exists() and any(user_root.iterdir()):
        raise RuntimeError(f"destination must be absent or empty: {user_root}")

    source_files = existing_source_files(legacy_root)
    source_hashes_before = {name: sha256(path) for name, path in source_files.items()}
    source_database_before = database_snapshot(source_files["database"])

    # Environment variables are set by the caller before this process starts,
    # so imports below resolve the real release data paths exactly once.
    from study_app.ai.providers import SETTINGS_KEY, load_llm_settings
    from study_app.data.database import (
        DATABASE_PATH,
        get_setting,
        ensure_seeded_database,
    )
    from study_app.data.practice_repository import ensure_practice_bank_seeded
    from study_app.paths import (
        BACKUPS_DIR,
        MIGRATION_MARKER_PATH,
        MODEL_PATH,
        RECORDS_PATH,
        TESSDATA_DIR,
        USER_ROOT,
        ensure_user_layout,
    )

    if USER_ROOT.resolve() != user_root:
        raise RuntimeError(f"resolved USER_ROOT mismatch: {USER_ROOT} != {user_root}")

    migration = ensure_user_layout()
    database_path = ensure_seeded_database()
    ensure_practice_bank_seeded(database_path)
    llm_settings = load_llm_settings()

    raw_llm = dict(get_setting(SETTINGS_KEY, {}) or {})
    source_hashes_after = {name: sha256(path) for name, path in source_files.items()}
    source_database_after = database_snapshot(source_files["database"])
    destination_snapshot = database_snapshot(Path(DATABASE_PATH))
    marker = json.loads(MIGRATION_MARKER_PATH.read_text(encoding="utf-8"))

    copied_file_hashes = {
        "model_matches_source": sha256(Path(MODEL_PATH))
        == source_hashes_before.get("model"),
        "records_match_source": sha256(Path(RECORDS_PATH))
        == source_hashes_before.get("records"),
    }
    for name, source in source_files.items():
        if not name.startswith("tessdata_"):
            continue
        destination = Path(TESSDATA_DIR) / source.name
        copied_file_hashes[f"{name}_matches_source"] = (
            destination.is_file() and sha256(destination) == source_hashes_before[name]
        )

    backup_files = [
        path.relative_to(BACKUPS_DIR).as_posix()
        for path in sorted(Path(BACKUPS_DIR).rglob("*"))
        if path.is_file()
    ]
    receipt = {
        "status": "ok",
        "legacy_root": str(legacy_root),
        "user_root": str(user_root),
        "source_files": {
            name: {"path": str(path), "bytes": path.stat().st_size}
            for name, path in source_files.items()
        },
        "source_hashes_unchanged": source_hashes_before == source_hashes_after,
        "source_database_unchanged": source_database_before == source_database_after,
        "source_database": source_database_before,
        "migration": {
            "source_root": str(migration.source_root) if migration.source_root else None,
            "migrated_files": list(migration.migrated_files),
            "used_defaults": list(migration.used_defaults),
            "marker": marker,
        },
        "destination_database": destination_snapshot,
        "copied_file_hashes": copied_file_hashes,
        "backup_files": backup_files,
        "credentials": {
            "plaintext_api_key_empty": not bool(raw_llm.get("api_key")),
            "protected_api_key_present": bool(raw_llm.get("api_key_protected")),
            "credential_available_after_dpapi_roundtrip": bool(llm_settings.api_key),
        },
    }

    if not receipt["source_hashes_unchanged"]:
        raise RuntimeError("legacy source file hash changed during migration")
    if not receipt["source_database_unchanged"]:
        raise RuntimeError("legacy source database changed during migration")
    if destination_snapshot["integrity_check"] != "ok":
        raise RuntimeError("destination database integrity check failed")
    if destination_snapshot["foreign_key_violations"] != 0:
        raise RuntimeError("destination database has foreign-key violations")
    if destination_snapshot["schema_version"] != 5:
        raise RuntimeError("destination database did not reach schema version 5")
    if not all(copied_file_hashes.values()):
        raise RuntimeError("one or more copied data files failed hash verification")
    if not receipt["credentials"]["plaintext_api_key_empty"]:
        raise RuntimeError("plaintext API key remains after migration")

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": receipt["status"],
        "source_hashes_unchanged": receipt["source_hashes_unchanged"],
        "schema_version": destination_snapshot["schema_version"],
        "foreign_key_violations": destination_snapshot["foreign_key_violations"],
        "counts": destination_snapshot["counts"],
        "migrated_files": receipt["migration"]["migrated_files"],
        "credential_flags": receipt["credentials"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
