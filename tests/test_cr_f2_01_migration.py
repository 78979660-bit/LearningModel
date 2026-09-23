"""CR-F2-01 controlled F2 knowledge-table migration — tool contract tests.

The tool module is loaded by file path (it inserts the project root into
sys.path itself). Every test runs against copies of a temporary seeded
database; the real ``app_data`` store is never touched.
"""
from __future__ import annotations

import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

from study_app.data import database
from data_test_support import (
    import_legacy_model_json,
    initialize_legacy_base_database,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "cr_f2_01_knowledge_tables.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "cr_f2_01_knowledge_tables_under_test", TOOL_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _load_tool()


def _model() -> dict:
    """Tiny model: 2 subjects / 3 topics in total."""
    return {
        "warning_policy": {},
        "initial_percent_assessment": {"subjects": []},
        "subjects": [
            {
                "name": "S1",
                "weight": 1,
                "status": "active",
                "modules": [
                    {
                        "name": "M1",
                        "weight": 1,
                        "status": "active",
                        "topics": [
                            {"name": "T1", "status": "learning", "mastery": 0.5},
                            {"name": "T2", "status": "learning", "mastery": 0.4},
                        ],
                    }
                ],
            },
            {
                "name": "S2",
                "weight": 1,
                "status": "active",
                "modules": [
                    {
                        "name": "M2",
                        "weight": 1,
                        "status": "active",
                        "topics": [
                            {"name": "T3", "status": "learning", "mastery": 0.6},
                        ],
                    }
                ],
            },
        ],
    }


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "seeded.sqlite"
    initialize_legacy_base_database(db_path)
    with database.connect(db_path) as connection:
        import_legacy_model_json(connection, _model())
    return db_path


def _copy(source: Path, name: str) -> Path:
    target = source.with_name(name)
    shutil.copy2(source, target)
    return target


def test_preflight_reports_all_f2_missing_on_seeded_db(seeded_db):
    report = TOOL.preflight(seeded_db)

    assert report["all_f2_missing"] is True
    assert report["all_f2_present"] is False
    assert report["integrity_check"] == "ok"
    assert report["fk_violations"] == []
    assert report["topic_count"] == 3
    assert report["registry_rows"] == 0
    assert not any(report["f2_tables_present"].values())


def test_apply_migration_installs_exactly_expected_objects_and_registry(seeded_db):
    replica = _copy(seeded_db, "migrated.sqlite")

    result = TOOL.apply_migration(replica)

    assert result["committed"] is True
    assert result["registry_rows"] == 3
    assert set(result["added_objects"]) == TOOL.EXPECTED_ADDED_OBJECTS
    # the diff is exactly 4 tables + 3 indexes, nothing else
    assert len(result["added_objects"]) == len(TOOL.EXPECTED_ADDED_OBJECTS) == 7
    assert TOOL.EXPECTED_ADDED_OBJECTS == {
        "table:knowledge_topic_registry",
        "table:knowledge_prerequisites",
        "table:knowledge_alerts",
        "table:knowledge_alert_events",
        "index:idx_knowledge_prerequisites_reverse",
        "index:idx_knowledge_alerts_status_due",
        "index:idx_knowledge_alert_events_alert",
    }

    with sqlite3.connect(str(replica)) as replica_connection, sqlite3.connect(
        str(seeded_db)
    ) as source_connection:
        after = TOOL.schema_objects(replica_connection)
        before = TOOL.schema_objects(source_connection)
    diff = set(after) - set(before)
    assert diff == TOOL.EXPECTED_ADDED_OBJECTS


def test_verify_passes_after_migration(seeded_db):
    replica = _copy(seeded_db, "verified.sqlite")
    TOOL.apply_migration(replica)

    report = TOOL.verify(replica, [])

    assert report["ok"] is True
    assert report["integrity_check"] == "ok"
    assert report["verified_all_present"] is True
    assert report["readback_ok"] is True
    assert report["fk_baseline_unchanged"] is True
    assert report["registry_rows"] == report["topic_count"] == 3
    assert report["alerts_rows"] == 0
    assert report["alert_events_rows"] == 0


def test_double_apply_is_rejected(seeded_db):
    replica = _copy(seeded_db, "twice.sqlite")
    TOOL.apply_migration(replica)

    with pytest.raises(RuntimeError, match="守卫失败"):
        TOOL.apply_migration(replica)


def test_fk_baseline_preserved_through_migration(seeded_db):
    # a pre-existing FK violation must survive the migration untouched
    plain = sqlite3.connect(str(seeded_db))  # foreign_keys defaults to OFF
    try:
        plain.execute(
            """
            INSERT INTO problem_attempts(record_id, title, raw_json)
            VALUES (9999, '孤儿练习行', '{}')
            """
        )
        plain.commit()
    finally:
        plain.close()

    baseline = TOOL.preflight(seeded_db)["fk_violations"]
    assert len(baseline) == 1

    replica = _copy(seeded_db, "orphan_migrated.sqlite")
    result = TOOL.apply_migration(replica)
    assert result["fk_violations"] == baseline

    report = TOOL.verify(replica, baseline)
    assert report["fk_baseline_unchanged"] is True
    assert report["fk_violations"] == baseline
    assert report["integrity_check"] == "ok"
    # the migration itself still succeeded and verified
    assert report["ok"] is True


def test_verify_fails_on_unmigrated_copy(seeded_db):
    replica = _copy(seeded_db, "unmigrated.sqlite")

    report = TOOL.verify(replica, [])

    assert report["ok"] is False
    assert report["verified_all_present"] is False
    assert report["readback_ok"] is False
    assert "readback_error" in report
