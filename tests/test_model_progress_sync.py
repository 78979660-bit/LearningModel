from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from data_test_support import IsolatedDatabaseTestCase
from study_app.data import database, model_progress_sync


class ModelProgressSyncTests(IsolatedDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            subject_id = connection.execute(
                "INSERT INTO subjects(name, source_json) VALUES (?, ?)",
                ("测试同步学科", "{}"),
            ).lastrowid
            module_id = connection.execute(
                "INSERT INTO modules(subject_id, name, weight, source_json) VALUES (?, ?, ?, ?)",
                (subject_id, "测试模块", 1.0, "{}"),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO topics(
                    module_id, name, status, mastery, importance,
                    difficulty, forgetting_risk, source_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_id,
                    "测试主题",
                    "not_started",
                    0.2,
                    1.0,
                    0.6,
                    0.4,
                    "{}",
                ),
            )
        self.model_path = self.temp_root / "model.json"
        self.model_path.write_text(
            json.dumps(
                {
                    "subjects": [
                        {
                            "name": "测试同步学科",
                            "mastery": 0.2,
                            "modules": [
                                {
                                    "name": "测试模块",
                                    "weight": 1.0,
                                    "mastery": 0.2,
                                    "status": "not_started",
                                    "topics": [
                                        {
                                            "name": "测试主题",
                                            "status": "not_started",
                                            "mastery": 0.2,
                                            "difficulty": 0.6,
                                            "forgetting_risk": 0.4,
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.record = {
            "id": 1,
            "date": "2026-07-19",
            "subject": "测试同步学科",
            "module": "测试模块",
            "topic": "测试主题",
            "activity": "practice",
            "source": "self_study",
            "score": 80,
            "note": "测试主题练习",
        }

    def test_sync_learning_record_uses_temp_db_for_all_settings(self) -> None:
        original_get = database.get_setting
        original_set = database.set_setting

        def guarded_get(key, default=None, db_path=None):
            self.assertEqual(Path(db_path), self.db_path)
            return original_get(key, default, db_path)

        def guarded_set(key, value, db_path=None, **kwargs):
            self.assertEqual(Path(db_path), self.db_path)
            return original_set(key, value, db_path, **kwargs)

        with (
            patch.object(model_progress_sync, "get_setting", side_effect=guarded_get),
            patch.object(model_progress_sync, "set_setting", side_effect=guarded_set),
        ):
            changed = model_progress_sync.sync_learning_record_to_model(
                self.record,
                model_path=self.model_path,
                db_path=self.db_path,
            )

        self.assertEqual(changed, ["测试主题"])
        self.assertTrue(
            original_get("mastery_contribution_applied:id:1", False, self.db_path)
        )
        audit = original_get("last_mastery_contribution_audit", {}, self.db_path)
        self.assertEqual(audit["record_id"], 1)
        self.assertEqual(audit["items"][0]["topic"], "测试主题")

    def test_diagnostic_scope_never_uses_default_phase_or_model_paths(self) -> None:
        from study_app.core import study_phase

        database.set_setting(
            study_phase.phase_setting_key("测试同步学科"),
            {
                "phase": study_phase.FINAL_REVIEW_PHASE,
                "exam_scope": {"modules": ["测试模块"]},
            },
            self.db_path,
        )
        record = {
            **self.record,
            "activity": "review",
            "source": "outside_class",
            "note": "综合诊断卷",
        }
        with (
            patch.object(
                study_phase,
                "get_subject_phase",
                side_effect=AssertionError("default phase path used"),
            ) as default_phase,
            patch.object(
                study_phase,
                "load_learning_model",
                side_effect=AssertionError("default model path used"),
            ) as default_model,
        ):
            changed = model_progress_sync.sync_learning_record_to_model(
                record,
                model_path=self.model_path,
                db_path=self.db_path,
            )

        default_phase.assert_not_called()
        default_model.assert_not_called()
        self.assertEqual(changed, ["测试主题"])

    def test_add_learning_record_syncs_only_explicit_model_and_database(self) -> None:
        record = {key: value for key, value in self.record.items() if key != "id"}

        record_id = database.add_learning_record(
            record,
            self.db_path,
            model_path=self.model_path,
        )

        model = json.loads(self.model_path.read_text(encoding="utf-8"))
        topic = model["subjects"][0]["modules"][0]["topics"][0]
        self.assertEqual(record_id, 1)
        self.assertEqual(topic["status"], model_progress_sync.LEARNED_STATUS)
        self.assertGreater(topic["mastery"], 0.2)
        result = database.get_setting(
            "model_progress_sync_last_result",
            {},
            self.db_path,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["changed_topics"], ["测试主题"])

    def test_explicit_model_seed_precedes_add_learning_record_on_empty_temp_db(self) -> None:
        empty_db = self.temp_root / "empty.sqlite"
        record = {key: value for key, value in self.record.items() if key != "id"}
        with patch.object(
            database,
            "import_current_json_files",
            side_effect=AssertionError("default production seed used"),
        ) as default_seed:
            database.ensure_seeded_database(empty_db, model_path=self.model_path)
            record_id = database.add_learning_record(
                record,
                empty_db,
                model_path=self.model_path,
            )

        default_seed.assert_not_called()
        self.assertEqual(record_id, 1)
        with database.connect(empty_db) as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM subjects").fetchone()[0],
                "测试同步学科",
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0],
                1,
            )

    def test_explicit_model_seed_precedes_direct_sync_on_empty_temp_db(self) -> None:
        empty_db = self.temp_root / "direct-empty.sqlite"
        with patch.object(
            database,
            "import_current_json_files",
            side_effect=AssertionError("default production seed used"),
        ) as default_seed:
            database.ensure_seeded_database(empty_db, model_path=self.model_path)
            changed = model_progress_sync.sync_learning_record_to_model(
                self.record,
                model_path=self.model_path,
                db_path=empty_db,
            )

        default_seed.assert_not_called()
        self.assertEqual(changed, ["测试主题"])
        with database.connect(empty_db) as connection:
            topic = connection.execute(
                "SELECT status, mastery FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()
        self.assertEqual(topic["status"], model_progress_sync.LEARNED_STATUS)
        self.assertGreater(topic["mastery"], 0.2)

    def test_sync_learning_record_reads_archived_phase_from_temp_db(self) -> None:
        from study_app.core.study_phase import ARCHIVED_PHASE, phase_setting_key

        database.set_setting(
            phase_setting_key("测试同步学科"),
            {"phase": ARCHIVED_PHASE},
            self.db_path,
        )
        original_model = self.model_path.read_bytes()
        with patch(
            "study_app.core.study_phase.is_archived",
            side_effect=AssertionError("default phase lookup escaped temp db"),
        ):
            changed = model_progress_sync.sync_learning_record_to_model(
                self.record,
                model_path=self.model_path,
                db_path=self.db_path,
            )

        self.assertEqual(changed, [])
        self.assertEqual(self.model_path.read_bytes(), original_model)
        with database.connect(self.db_path) as connection:
            status = connection.execute(
                "SELECT status FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()[0]
        self.assertEqual(status, "not_started")

    def test_sync_learning_record_is_idempotent_and_keeps_json_sqlite_in_sync(self) -> None:
        first_changed = model_progress_sync.sync_learning_record_to_model(
            self.record,
            model_path=self.model_path,
            db_path=self.db_path,
        )
        first_model = json.loads(self.model_path.read_text(encoding="utf-8"))
        with database.connect(self.db_path) as connection:
            first_sqlite = connection.execute(
                "SELECT status, mastery, source_json FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()

        second_changed = model_progress_sync.sync_learning_record_to_model(
            self.record,
            model_path=self.model_path,
            db_path=self.db_path,
        )
        second_model = json.loads(self.model_path.read_text(encoding="utf-8"))
        with database.connect(self.db_path) as connection:
            second_sqlite = connection.execute(
                "SELECT status, mastery, source_json FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()

        topic = first_model["subjects"][0]["modules"][0]["topics"][0]
        self.assertEqual(first_changed, ["测试主题"])
        self.assertEqual(second_changed, [])
        self.assertEqual(second_model, first_model)
        self.assertEqual(tuple(second_sqlite), tuple(first_sqlite))
        self.assertEqual(first_sqlite["status"], topic["status"])
        self.assertAlmostEqual(first_sqlite["mastery"], topic["mastery"])
        self.assertEqual(
            first_model["subjects"][0]["mastery"],
            first_model["subjects"][0]["modules"][0]["mastery"],
        )

    def test_setting_failure_rolls_back_model_sqlite_and_sync_settings(self) -> None:
        original_model = self.model_path.read_bytes()
        original_set = model_progress_sync.set_setting

        def fail_idempotency_marker(key, value, db_path=database.DEFAULT_DB_PATH, **kwargs):
            if key.startswith("mastery_contribution_applied:"):
                raise RuntimeError("simulated marker failure")
            return original_set(key, value, db_path, **kwargs)

        with patch.object(
            model_progress_sync,
            "set_setting",
            side_effect=fail_idempotency_marker,
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated marker failure"):
                model_progress_sync.sync_learning_record_to_model(
                    self.record,
                    model_path=self.model_path,
                    db_path=self.db_path,
                )

        self.assertEqual(self.model_path.read_bytes(), original_model)
        self.assertFalse(self.model_path.with_suffix(".json.tmp").exists())
        with database.connect(self.db_path) as connection:
            topic = connection.execute(
                "SELECT status, mastery FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()
        self.assertEqual(topic["status"], "not_started")
        self.assertEqual(topic["mastery"], 0.2)
        self.assertEqual(
            database.get_setting("last_mastery_contribution_audit", {}, self.db_path),
            {},
        )
        self.assertFalse(
            database.get_setting(
                "mastery_contribution_applied:id:1",
                False,
                self.db_path,
            )
        )

    def test_sqlite_failure_rolls_back_model_and_topic_updates(self) -> None:
        original_model = self.model_path.read_bytes()
        original_sync = model_progress_sync._sync_topic_statuses

        def fail_after_updates(connection, subject_name, changed, model):
            original_sync(connection, subject_name, changed, model)
            raise RuntimeError("simulated sqlite failure")

        with patch.object(
            model_progress_sync,
            "_sync_topic_statuses",
            side_effect=fail_after_updates,
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated sqlite failure"):
                model_progress_sync.sync_learning_record_to_model(
                    self.record,
                    model_path=self.model_path,
                    db_path=self.db_path,
                )

        self.assertEqual(self.model_path.read_bytes(), original_model)
        with database.connect(self.db_path) as connection:
            topic = connection.execute(
                "SELECT status, mastery FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()
        self.assertEqual((topic["status"], topic["mastery"]), ("not_started", 0.2))

    def test_empty_subject_short_circuits_without_data_access(self) -> None:
        original_model = self.model_path.read_bytes()
        with (
            patch.object(
                model_progress_sync,
                "ensure_seeded_database",
                side_effect=AssertionError("database accessed"),
            ) as ensure_database,
            patch.object(
                model_progress_sync.Path,
                "read_bytes",
                side_effect=AssertionError("model accessed"),
            ) as read_model,
        ):
            changed = model_progress_sync.sync_learning_record_to_model(
                {**self.record, "subject": ""},
                model_path=self.model_path,
                db_path=self.db_path,
            )

        self.assertEqual(changed, [])
        ensure_database.assert_not_called()
        read_model.assert_not_called()
        self.assertEqual(self.model_path.read_bytes(), original_model)

    def test_sync_existing_learning_records_uses_only_explicit_paths(self) -> None:
        with database.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO learning_records(
                    record_date, subject_name, module_name, topic_name,
                    activity, source, score, note, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.record["date"],
                    self.record["subject"],
                    self.record["module"],
                    self.record["topic"],
                    self.record["activity"],
                    self.record["source"],
                    self.record["score"],
                    self.record["note"],
                    json.dumps(self.record, ensure_ascii=False),
                ),
            )
        original_get = database.get_setting
        original_set = database.set_setting

        def guarded_get(key, default=None, db_path=None):
            self.assertEqual(Path(db_path), self.db_path)
            return original_get(key, default, db_path)

        def guarded_set(key, value, db_path=None, **kwargs):
            self.assertEqual(Path(db_path), self.db_path)
            return original_set(key, value, db_path, **kwargs)

        with (
            patch.object(model_progress_sync, "get_setting", side_effect=guarded_get),
            patch.object(model_progress_sync, "set_setting", side_effect=guarded_set),
        ):
            changed = model_progress_sync.sync_existing_learning_records(
                model_path=self.model_path,
                db_path=self.db_path,
            )

        self.assertEqual(changed, {"测试同步学科": ["测试主题"]})

    def test_sync_existing_records_seeds_empty_db_from_explicit_model_only(self) -> None:
        empty_db = self.temp_root / "existing-empty.sqlite"
        with patch.object(
            database,
            "import_current_json_files",
            side_effect=AssertionError("default production seed used"),
        ) as default_seed:
            changed = model_progress_sync.sync_existing_learning_records(
                model_path=self.model_path,
                db_path=empty_db,
            )

        default_seed.assert_not_called()
        self.assertEqual(changed, {})
        with database.connect(empty_db) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
                1,
            )

    def test_default_scope_preserves_is_final_review_monkeypatch_short_circuit(self) -> None:
        from study_app.core import study_phase

        with (
            patch.object(study_phase, "is_final_review", return_value=False) as final_review,
            patch.object(
                study_phase,
                "get_subject_phase",
                side_effect=AssertionError("phase lookup should be skipped"),
            ) as phase_lookup,
        ):
            self.assertTrue(study_phase.is_in_exam_scope("测试同步学科", "测试模块"))

        final_review.assert_called_once_with("测试同步学科")
        phase_lookup.assert_not_called()

    def test_temp_write_failure_leaves_no_partial_files_or_state_changes(self) -> None:
        original_model = self.model_path.read_bytes()
        original_write_text = Path.write_text

        def fail_temp_write(path, text, *args, **kwargs):
            if path.parent == self.model_path.parent and path.name.startswith(self.model_path.name):
                path.write_bytes(b"partial")
                raise OSError("simulated temp write failure")
            return original_write_text(path, text, *args, **kwargs)

        with patch.object(Path, "write_text", new=fail_temp_write):
            with self.assertRaisesRegex(OSError, "simulated temp write failure"):
                model_progress_sync.sync_learning_record_to_model(
                    self.record,
                    model_path=self.model_path,
                    db_path=self.db_path,
                )

        self.assertEqual(self.model_path.read_bytes(), original_model)
        self.assertEqual(list(self.model_path.parent.glob(f"{self.model_path.name}.*tmp*")), [])
        self.assertEqual(list(self.model_path.parent.glob(f"{self.model_path.name}.*rollback*")), [])
        with database.connect(self.db_path) as connection:
            topic = connection.execute(
                "SELECT status, mastery FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()
        self.assertEqual((topic["status"], topic["mastery"]), ("not_started", 0.2))

    def test_public_sqlite_sync_hook_still_observes_and_can_abort_sync(self) -> None:
        original_model = self.model_path.read_bytes()
        with patch.object(
            model_progress_sync,
            "sync_topic_statuses_to_sqlite",
            side_effect=RuntimeError("public sync hook failure"),
        ) as sync_hook:
            with self.assertRaisesRegex(RuntimeError, "public sync hook failure"):
                model_progress_sync.sync_learning_record_to_model(
                    self.record,
                    model_path=self.model_path,
                    db_path=self.db_path,
                )

        sync_hook.assert_called_once()
        self.assertEqual(self.model_path.read_bytes(), original_model)

    def test_replace_failure_is_recovered_from_sqlite_journal_on_next_sync(self) -> None:
        original_model = self.model_path.read_bytes()
        original_replace = Path.replace
        failed = False

        def fail_model_replace(path, target):
            nonlocal failed
            if not failed and Path(target) == self.model_path and path.suffix == ".tmp":
                failed = True
                raise OSError("simulated model replace failure")
            return original_replace(path, target)

        with patch.object(Path, "replace", new=fail_model_replace):
            with self.assertRaisesRegex(OSError, "simulated model replace failure"):
                model_progress_sync.sync_learning_record_to_model(
                    self.record,
                    model_path=self.model_path,
                    db_path=self.db_path,
                )

        self.assertEqual(self.model_path.read_bytes(), original_model)
        with database.connect(self.db_path) as connection:
            topic = connection.execute(
                "SELECT status, mastery FROM topics WHERE name = ?",
                ("测试主题",),
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'pending_model_sync:%'"
            ).fetchone()[0]
        self.assertEqual(topic["status"], model_progress_sync.LEARNED_STATUS)
        self.assertGreater(topic["mastery"], 0.2)
        self.assertEqual(pending, 1)

        self.assertEqual(
            model_progress_sync.sync_learning_record_to_model(
                self.record,
                model_path=self.model_path,
                db_path=self.db_path,
            ),
            [],
        )
        recovered = json.loads(self.model_path.read_text(encoding="utf-8"))
        recovered_topic = recovered["subjects"][0]["modules"][0]["topics"][0]
        self.assertEqual(recovered_topic["status"], topic["status"])
        self.assertEqual(recovered_topic["mastery"], topic["mastery"])
        with database.connect(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'pending_model_sync:%'"
                ).fetchone()[0],
                0,
            )

    def test_public_sync_serializes_same_model_path(self) -> None:
        active = 0
        maximum_active = 0
        state_lock = threading.Lock()

        def tracked_inner(record, model_path, db_path):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.04)
            with state_lock:
                active -= 1
            return []

        with patch.object(
            model_progress_sync,
            "_sync_learning_record_to_model_unlocked",
            side_effect=tracked_inner,
        ):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(
                    executor.map(
                        lambda _index: model_progress_sync.sync_learning_record_to_model(
                            self.record,
                            model_path=self.model_path,
                            db_path=self.db_path,
                        ),
                        range(4),
                    )
                )

        self.assertEqual(results, [[], [], [], []])
        self.assertEqual(maximum_active, 1)

    def test_posix_case_distinct_model_paths_have_distinct_journal_keys(self) -> None:
        class ResolvedPath:
            def __init__(self, value):
                self.value = value

            def resolve(self):
                return self.value

        upper = ResolvedPath("/tmp/Model.json")
        lower = ResolvedPath("/tmp/model.json")
        with patch.object(model_progress_sync.os, "name", "posix"):
            self.assertNotEqual(
                model_progress_sync._pending_model_sync_key(upper),
                model_progress_sync._pending_model_sync_key(lower),
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
