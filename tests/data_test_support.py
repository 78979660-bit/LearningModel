from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from study_app.data.database import connect, initialize_database
from study_app.paths import DATABASE_PATH, MODEL_PATH, RECORDS_PATH


ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = DATABASE_PATH
PROTECTED_DATA_FILES = (
    MAIN_DB,
    MAIN_DB.with_name(f"{MAIN_DB.name}-wal"),
    MAIN_DB.with_name(f"{MAIN_DB.name}-shm"),
    MODEL_PATH,
    RECORDS_PATH,
    ROOT / "learning_model_v1.json",
    ROOT / "learning_records.json",
    ROOT / "study_app" / "data" / "schema.sql",
)


def file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def initialize_legacy_base_database(path: Path) -> Path:
    """Create a pre-unified-schema fixture for explicit migration tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(
            (ROOT / "study_app" / "data" / "schema.sql").read_text(
                encoding="utf-8"
            )
        )
    return path


def import_legacy_model_json(
    connection: sqlite3.Connection, model: dict[str, Any]
) -> None:
    """Seed legacy model rows without installing any post-F1 schemas.

    ``study_app.data.database.import_model_json`` now completes the unified
    F2/F5 bootstrap after inserting model rows.  Explicit migration tests need
    the historical boundary instead: populated F1 tables with those schemas
    genuinely absent.  Keep that fixture construction local to tests rather
    than weakening the production bootstrap.
    """

    compact_json = lambda value: json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    )
    score_items = model.get("initial_percent_assessment", {}).get("subjects", [])
    initial_scores = {
        item.get("name"): item.get("initial_score") for item in score_items
    }
    for key, value in (
        ("warning_policy", model.get("warning_policy", {})),
        ("scale", model.get("scale", {})),
    ):
        connection.execute(
            "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
            (key, compact_json(value)),
        )

    for subject in model.get("subjects", []):
        modules = subject.get("modules", [])
        total_weight = sum(module.get("weight", 0) for module in modules)
        weighted_mastery = (
            sum(
                module.get("mastery", 0) * module.get("weight", 0)
                for module in modules
            )
            / total_weight
            if total_weight
            else 0.0
        )
        subject_cursor = connection.execute(
            """
            INSERT INTO subjects(
                name, weight, initial_score, mastery, status,
                current_anchor, source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subject.get("name"),
                subject.get("weight", 0),
                initial_scores.get(subject.get("name")),
                weighted_mastery,
                subject.get("status"),
                subject.get("current_anchor"),
                compact_json(subject),
            ),
        )
        subject_id = int(subject_cursor.lastrowid)

        for module in modules:
            module_cursor = connection.execute(
                """
                INSERT INTO modules(
                    subject_id, name, weight, mastery, confidence,
                    status, source_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject_id,
                    module.get("name"),
                    module.get("weight", 0),
                    module.get("mastery", 0),
                    module.get("confidence"),
                    module.get("status"),
                    compact_json(module),
                ),
            )
            module_id = int(module_cursor.lastrowid)
            for topic in module.get("topics", []):
                connection.execute(
                    """
                    INSERT INTO topics(
                        module_id, name, status, mastery, importance,
                        difficulty, forgetting_risk, source_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        module_id,
                        topic.get("name"),
                        topic.get("status"),
                        topic.get("mastery", 0),
                        topic.get("importance", 0),
                        topic.get("difficulty", 0),
                        topic.get("forgetting_risk", 0),
                        compact_json(topic),
                    ),
                )


class IsolatedDatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._protected_hashes = {
            path: file_sha256(path) for path in PROTECTED_DATA_FILES
        }

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            changed = [
                path
                for path, expected in cls._protected_hashes.items()
                if file_sha256(path) != expected
            ]
            if changed:
                raise AssertionError(f"测试修改了受保护数据文件：{changed}")
        finally:
            super().tearDownClass()

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="study-app-test-")
        self.temp_root = Path(self._temp_dir.name)
        self.db_path = self.temp_root / "learning.sqlite"

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def initialize_seed_guarded_database(self) -> Path:
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO subjects(name, source_json) VALUES (?, ?)",
                ("测试占位学科", "{}"),
            )
        return self.db_path
