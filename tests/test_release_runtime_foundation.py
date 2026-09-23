from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _redirect_paths(monkeypatch, tmp_path: Path):
    from study_app import paths

    user_root = tmp_path / "user"
    mapping = {
        "USER_ROOT": user_root,
        "DATA_DIR": user_root / "data",
        "BACKUPS_DIR": user_root / "backups",
        "LOGS_DIR": user_root / "logs",
        "CACHE_DIR": user_root / "cache",
        "EXPORTS_DIR": user_root / "exports",
        "DATABASE_PATH": user_root / "data" / "learning_app.sqlite",
        "MODEL_PATH": user_root / "data" / "learning_model_v1.json",
        "RECORDS_PATH": user_root / "data" / "learning_records.json",
        "TESSDATA_DIR": user_root / "data" / "tesseract" / "tessdata",
        "LAUNCHER_LOG_PATH": user_root / "logs" / "study_app_launcher.log",
        "RUNTIME_LOG_PATH": user_root / "logs" / "study_app.log",
        "PERFORMANCE_LOG_PATH": user_root / "logs" / "performance.jsonl",
        "MIGRATION_MARKER_PATH": user_root / "data" / ".legacy-migration.json",
    }
    for name, value in mapping.items():
        monkeypatch.setattr(paths, name, value)
    return paths, mapping


def test_legacy_migration_is_verified_non_destructive_and_idempotent(
    tmp_path, monkeypatch
):
    paths, redirected = _redirect_paths(monkeypatch, tmp_path)
    legacy = tmp_path / "legacy"
    (legacy / "app_data").mkdir(parents=True)
    source_db = legacy / "app_data" / "learning_app.sqlite"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('kept')")
    source_model = legacy / "learning_model_v1.json"
    source_records = legacy / "learning_records.json"
    source_model.write_text(json.dumps({"subjects": []}), encoding="utf-8")
    source_records.write_text(json.dumps({"records": []}), encoding="utf-8")
    legacy_tessdata = legacy / "app_data" / "tesseract" / "tessdata"
    legacy_tessdata.mkdir(parents=True)
    source_language = legacy_tessdata / "chi_sim.traineddata"
    source_language.write_bytes(b"privacy-safe-language-fixture")
    before = {
        path: _sha256(path)
        for path in (source_db, source_model, source_records, source_language)
    }

    first = paths.ensure_user_layout(legacy)
    second = paths.ensure_user_layout(legacy)

    assert set(first.migrated_files) == {
        "learning_app.sqlite",
        "learning_model_v1.json",
        "learning_records.json",
        "tesseract/tessdata/chi_sim.traineddata",
    }
    assert second.migrated_files == ()
    assert all(_sha256(path) == digest for path, digest in before.items())
    assert redirected["DATABASE_PATH"].is_file()
    assert redirected["MODEL_PATH"].is_file()
    assert redirected["RECORDS_PATH"].is_file()
    assert (redirected["TESSDATA_DIR"] / "chi_sim.traineddata").is_file()
    assert list(redirected["BACKUPS_DIR"].glob("legacy_migration_*"))
    marker = json.loads(redirected["MIGRATION_MARKER_PATH"].read_text(encoding="utf-8"))
    assert marker["source_root"] == str(legacy.resolve())


def test_noop_startup_preserves_initial_migration_receipt(tmp_path, monkeypatch):
    paths, redirected = _redirect_paths(monkeypatch, tmp_path)
    legacy = tmp_path / "legacy"
    (legacy / "app_data").mkdir(parents=True)
    with sqlite3.connect(legacy / "app_data" / "learning_app.sqlite") as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
    (legacy / "learning_model_v1.json").write_text(
        json.dumps({"subjects": []}), encoding="utf-8"
    )
    (legacy / "learning_records.json").write_text(
        json.dumps({"records": []}), encoding="utf-8"
    )

    paths.ensure_user_layout(legacy)
    receipt_before = redirected["MIGRATION_MARKER_PATH"].read_bytes()
    empty_resource_root = tmp_path / "empty-resource"
    empty_resource_root.mkdir()
    monkeypatch.setattr(paths, "RESOURCE_ROOT", empty_resource_root)

    result = paths.ensure_user_layout()

    assert result.migrated_files == ()
    assert result.used_defaults == ()
    assert redirected["MIGRATION_MARKER_PATH"].read_bytes() == receipt_before


def test_user_layout_is_thread_safe(tmp_path, monkeypatch):
    paths, redirected = _redirect_paths(monkeypatch, tmp_path)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _index: paths.ensure_user_layout(), range(48)))

    assert len(results) == 48
    assert redirected["MODEL_PATH"].is_file()
    assert redirected["RECORDS_PATH"].is_file()
    assert json.loads(
        redirected["MIGRATION_MARKER_PATH"].read_text(encoding="utf-8")
    )["completed_at"]


def test_explicit_missing_legacy_root_fails_without_completion_marker(
    tmp_path, monkeypatch
):
    paths, redirected = _redirect_paths(monkeypatch, tmp_path)
    missing = tmp_path / "missing-legacy-root"

    with pytest.raises(FileNotFoundError, match="旧版数据目录不存在"):
        paths.ensure_user_layout(missing)

    assert not redirected["MIGRATION_MARKER_PATH"].exists()


def test_configured_legacy_file_fails_without_completion_marker(
    tmp_path, monkeypatch
):
    paths, redirected = _redirect_paths(monkeypatch, tmp_path)
    legacy_file = tmp_path / "not-a-directory"
    legacy_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(paths.LEGACY_ROOT_ENV, str(legacy_file))

    with pytest.raises(NotADirectoryError, match="不是目录"):
        paths.ensure_user_layout()

    assert not redirected["MIGRATION_MARKER_PATH"].exists()


def test_database_upgrade_quarantines_known_orphans_and_preserves_backup(tmp_path):
    from study_app.data import database

    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(database.SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO problem_attempts(record_id, title, raw_json)
            VALUES (999, '历史孤儿题目', '{}')
            """
        )
        connection.commit()
    finally:
        connection.close()

    database.initialize_database(path)

    with sqlite3.connect(path) as connection:
        orphan_count = connection.execute(
            "SELECT COUNT(*) FROM problem_attempts WHERE record_id=999"
        ).fetchone()[0]
        quarantine = connection.execute(
            """
            SELECT source_table, reason, payload_sha256
            FROM migration_quarantine
            """
        ).fetchall()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert orphan_count == 0
    assert quarantine[0][0:2] == ("problem_attempts", "missing_learning_record")
    assert len(quarantine[0][2]) == 64
    assert violations == []
    backups = list((tmp_path / "backups").glob("legacy-before-schema-1-to-*.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM problem_attempts WHERE record_id=999"
        ).fetchone()[0] == 1


def test_database_refuses_newer_schema_without_writing(tmp_path):
    from study_app.data import database

    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, 'future')",
            (database.APPLICATION_SCHEMA_VERSION + 1,),
        )
    before = _sha256(path)
    with pytest.raises(database.DatabaseVersionTooNewError):
        database.initialize_database(path)
    assert _sha256(path) == before


def test_same_version_with_incomplete_schema_does_not_return_success(tmp_path):
    from study_app.data import database

    path = tmp_path / "partial.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, 'partial')",
            (database.APPLICATION_SCHEMA_VERSION,),
        )
        for statement in (
            "CREATE TABLE learning_records(id INTEGER)",
            "CREATE TABLE knowledge_topic_registry(topic_key TEXT)",
            "CREATE TABLE oj_problems(problem_key TEXT)",
            "CREATE TABLE subject_catalog(subject_key TEXT)",
        ):
            connection.execute(statement)

    before = _sha256(path)
    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        database.initialize_database(path)
    assert _sha256(path) == before


def test_empty_f5_catalog_is_legacy_compatible_until_startup_marks_it_strict(
    tmp_path,
):
    from study_app.data import database

    path = tmp_path / "legacy-compat.sqlite"
    database.initialize_database(path)
    with database.connect(path) as connection:
        assert database.f5_catalog_is_installed(connection) is False

    # Bare schema installation remains compatible with old isolated workflows.
    legacy_record_id = database.add_learning_record(
        {
            "date": "2026-09-22",
            "subject": "旧版测试学科",
            "activity": "exercise",
            "source": "local",
        },
        db_path=path,
    )
    assert legacy_record_id > 0

    clean_path = tmp_path / "clean-startup.sqlite"
    empty_model = tmp_path / "empty-model.json"
    empty_model.write_text('{"subjects": []}', encoding="utf-8")
    database.ensure_seeded_database(clean_path, model_path=empty_model)
    with database.connect(clean_path) as connection:
        assert database.f5_catalog_is_installed(connection) is True
    with pytest.raises(ValueError, match="尚未登记到 F5 目录"):
        database.add_learning_record(
            {
                "date": "2026-09-22",
                "subject": "未登记学科",
                "activity": "exercise",
                "source": "local",
            },
            db_path=clean_path,
        )


def test_legacy_model_bootstraps_complete_f5_topology_and_is_idempotent(tmp_path):
    from study_app.core.subject_catalog import (
        load_catalog_snapshot,
        overlay_model_with_catalog,
    )
    from study_app.data import database

    path = tmp_path / "legacy-bootstrap.sqlite"
    model_path = tmp_path / "legacy-model.json"
    model = {
        "model_name": "legacy-test-v1",
        "subjects": [
            {
                "name": "测试学科",
                "display_name": "测试学科显示名",
                "aliases": ["测试别名"],
                "mastery": 0.37,
                "capabilities": {"study_plan": False, "oj": True},
                "modules": [
                    {
                        "name": "测试模块",
                        "mastery": 0.41,
                        "topics": [
                            {
                                "name": "测试知识点",
                                "mastery": 0.43,
                                "importance": 0.8,
                                "difficulty": 0.6,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")

    database.ensure_seeded_database(path, model_path=model_path)
    with database.connect(path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "subject_catalog",
                "subject_aliases",
                "subject_capabilities",
                "subject_module_identities",
                "subject_structure_versions",
                "subject_structure_modules",
                "subject_structure_topics",
                "knowledge_topic_registry",
            )
        }
        capability_rows = dict(
            connection.execute(
                "SELECT capability_key, declared_supported FROM subject_capabilities"
            ).fetchall()
        )
        receipt = json.loads(
            connection.execute(
                "SELECT value_json FROM app_settings WHERE key=?",
                (database.F5_LEGACY_BOOTSTRAP_SETTING,),
            ).fetchone()[0]
        )
        first_rows = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "subject_catalog_state",
                "subject_catalog",
                "subject_aliases",
                "subject_capabilities",
                "subject_module_identities",
                "subject_structure_versions",
                "subject_structure_modules",
                "subject_structure_topics",
            )
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert counts == {
        "subject_catalog": 1,
        "subject_aliases": 2,
        "subject_capabilities": 7,
        "subject_module_identities": 1,
        "subject_structure_versions": 1,
        "subject_structure_modules": 1,
        "subject_structure_topics": 1,
        "knowledge_topic_registry": 1,
    }
    assert capability_rows["study_plan"] == 0
    assert capability_rows["generic_practice"] == 1
    assert capability_rows["oj"] == 1
    assert receipt["subjects"] == 1
    assert receipt["modules"] == 1
    assert receipt["topics"] == 1

    snapshot = load_catalog_snapshot(path)
    overlaid = overlay_model_with_catalog(model, snapshot)
    assert len(overlaid["subjects"]) == 1
    projected = overlaid["subjects"][0]
    assert projected["mastery"] == 0.37
    assert projected["modules"][0]["mastery"] == 0.41
    assert projected["modules"][0]["topics"][0]["mastery"] == 0.43

    database.ensure_seeded_database(path, model_path=model_path)
    with database.connect(path) as connection:
        second_rows = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in first_rows
        }
    assert second_rows == first_rows


def test_lenient_first_seed_preserves_unknown_history_but_not_future_writes(tmp_path):
    from study_app.data import database

    path = tmp_path / "legacy-history.sqlite"
    model_path = tmp_path / "model.json"
    records_path = tmp_path / "records.json"
    model_path.write_text(
        json.dumps({"subjects": [{"name": "当前学科", "modules": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    records_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "date": "2026-01-01",
                        "subject": "已移除的历史学科",
                        "activity": "review",
                        "source": "legacy",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    database.import_current_json_files(
        path,
        model_path=model_path,
        records_path=records_path,
        strict=False,
    )
    records = database.load_raw_records(path)
    assert records[0]["subject"] == "已移除的历史学科"
    assert records[0]["data_warning"] == "legacy_lenient_import"
    with pytest.raises(ValueError, match="尚未登记到 F5 目录"):
        database.add_learning_record(
            {
                "date": "2026-09-22",
                "subject": "已移除的历史学科",
                "activity": "review",
                "source": "local",
            },
            db_path=path,
        )


def test_f5_bootstrap_never_merges_into_an_existing_catalog(tmp_path):
    from study_app.data import database
    from study_app.data.subject_repository import SubjectCatalogRepository

    path = tmp_path / "existing-catalog.sqlite"
    database.initialize_database(path)
    repository = SubjectCatalogRepository(path)
    repository.create_subject("已签核学科", capabilities={"study_plan": True})
    with database.connect(path) as connection:
        before = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "subject_catalog_state",
                "subject_catalog",
                "subject_aliases",
                "subject_capabilities",
                "subject_structure_versions",
            )
        }
        connection.execute(
            "INSERT INTO subjects(name, source_json) VALUES ('尚未迁移的旧学科', '{}')"
        )
        assert database._bootstrap_f5_catalog_from_legacy_tables(connection) is None
        after = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in before
        }
    assert after == before


def test_normalized_legacy_identity_collision_rolls_back_without_partial_catalog(
    tmp_path,
):
    from study_app.data import database

    path = tmp_path / "identity-collision.sqlite"
    database.initialize_database(path)
    colliding_model = {
        "subjects": [
            {"name": "A", "modules": []},
            {"name": "Ａ", "modules": []},
        ]
    }
    with pytest.raises(RuntimeError, match="规范化后冲突"):
        with database.connect(path) as connection:
            database.import_model_json(connection, colliding_model)
    with database.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM subject_catalog").fetchone()[0] == 0
        assert connection.execute(
            "SELECT 1 FROM app_settings WHERE key=?",
            (database.F5_LEGACY_BOOTSTRAP_SETTING,),
        ).fetchone() is None


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="DPAPI is Windows-only")
def test_dpapi_round_trip_does_not_embed_plaintext():
    from study_app.security import protect_secret, unprotect_secret

    secret = "release-test-secret-not-real"
    protected = protect_secret(secret)
    assert secret not in protected
    assert protected.startswith("dpapi-v1:")
    assert unprotect_secret(protected) == secret


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="DPAPI is Windows-only")
def test_provider_migrates_plaintext_key_to_dpapi():
    from study_app.ai import providers

    legacy = {
        "enabled": True,
        "provider": "openai",
        "api_key": "release-test-key-not-real",
        "enabled_features": ["record_parser"],
        "feature_defaults_version": 3,
    }
    with (
        patch.object(providers, "get_setting", return_value=legacy),
        patch.object(providers, "set_setting") as persist,
    ):
        loaded = providers.load_llm_settings()
    saved = persist.call_args.args[1]
    assert loaded.api_key == legacy["api_key"]
    assert saved["api_key"] == ""
    assert legacy["api_key"] not in saved["api_key_protected"]
    assert saved["api_key_protected"].startswith("dpapi-v1:")
