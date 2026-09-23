from __future__ import annotations

import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch

from data_test_support import IsolatedDatabaseTestCase
from study_app.data import backup, database


class DataBackupTests(IsolatedDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.initialize_seed_guarded_database()
        with database.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO learning_records(
                    record_date, subject_name, activity, source, raw_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "2026-07-18",
                    "测试占位学科",
                    "review",
                    "outside_class",
                    json.dumps(
                        {"date": "2026-07-18", "subject": "测试占位学科"},
                        ensure_ascii=False,
                    ),
                ),
            )

    def test_create_backup_contains_consistent_database_export_manifest_and_zip(self) -> None:
        result = backup.create_backup(
            self.db_path, self.temp_root / "backups", include_archive=True
        )

        self.assertTrue(result.database_copy.is_file())
        self.assertTrue(result.manifest.is_file())
        self.assertTrue(result.archive.is_file())
        manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
        exported = json.loads(
            (result.folder / "learning_records_export.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["counts"]["subjects"], 1)
        self.assertEqual(manifest["counts"]["learning_records"], 1)
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(exported["records"][0]["subject"], "测试占位学科")
        self.assertEqual(
            backup.integrity_check(result.database_copy),
            "SQLite: ok；外键检查通过；文本编码检查通过",
        )
        with database.connect(result.database_copy) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0],
                1,
            )
        with zipfile.ZipFile(result.archive) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"learning_app.sqlite", "learning_records_export.json", "manifest.json"},
            )

    def test_backup_exports_rebuilt_record_when_raw_json_is_malformed(self) -> None:
        with database.connect(self.db_path) as connection:
            connection.execute("UPDATE learning_records SET raw_json = ?", ("{broken",))

        result = backup.create_backup(
            self.db_path,
            self.temp_root / "malformed-record",
            include_archive=False,
        )

        exported = json.loads(
            (result.folder / "learning_records_export.json").read_text(encoding="utf-8")
        )
        self.assertEqual(exported["records"][0]["subject"], "测试占位学科")
        self.assertEqual(exported["records"][0]["data_warning"], "invalid_raw_json")
        with database.connect(result.database_copy) as connection:
            copied_raw = connection.execute(
                "SELECT raw_json FROM learning_records"
            ).fetchone()["raw_json"]
        self.assertEqual(copied_raw, "{broken")

    def test_create_backup_without_archive_and_missing_source(self) -> None:
        result = backup.create_backup(
            self.db_path, self.temp_root / "folder-only", include_archive=False
        )
        self.assertFalse(result.archive.exists())
        with self.assertRaisesRegex(FileNotFoundError, "数据库不存在"):
            backup.create_backup(
                self.temp_root / "missing.sqlite", self.temp_root / "unused"
            )

    def test_create_backup_uses_distinct_folders_within_same_second(self) -> None:
        backup_root = self.temp_root / "same-second"
        with patch.object(backup, "datetime") as clock:
            clock.now.return_value = datetime(2026, 7, 19, 17, 30, 0)
            first = backup.create_backup(self.db_path, backup_root, include_archive=False)
            second = backup.create_backup(self.db_path, backup_root, include_archive=False)

        self.assertNotEqual(first.folder, second.folder)
        self.assertTrue(first.database_copy.is_file())
        self.assertTrue(second.database_copy.is_file())

    def test_concurrent_same_second_backups_are_unique_and_complete(self) -> None:
        backup_root = self.temp_root / "concurrent"
        with patch.object(backup, "datetime") as clock:
            clock.now.return_value = datetime(2026, 7, 19, 17, 30, 0)
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda _index: backup.create_backup(
                            self.db_path,
                            backup_root,
                            include_archive=False,
                        ),
                        range(8),
                    )
                )

        self.assertEqual(len({result.folder for result in results}), 8)
        for result in results:
            self.assertFalse(result.archive.exists())
            self.assertEqual(
                backup.integrity_check(result.database_copy),
                "SQLite: ok；外键检查通过；文本编码检查通过",
            )

    def test_create_backup_failure_removes_partial_artifacts(self) -> None:
        backup_root = self.temp_root / "failed-backup"

        def fail_after_partial_archive(_folder, archive):
            archive.write_bytes(b"PARTIAL")
            raise RuntimeError("simulated zip failure")

        with patch.object(backup, "_zip_folder", side_effect=fail_after_partial_archive):
            with self.assertRaisesRegex(RuntimeError, "simulated zip failure"):
                backup.create_backup(self.db_path, backup_root, include_archive=True)

        self.assertEqual(list(backup_root.iterdir()), [])

    def test_orphan_archive_is_never_overwritten_or_returned(self) -> None:
        backup_root = self.temp_root / "orphan-archive"
        backup_root.mkdir()
        orphan = backup_root / "learning_backup_20260719_173000.zip"
        orphan.write_bytes(b"ORIGINAL")
        with patch.object(backup, "datetime") as clock:
            clock.now.return_value = datetime(2026, 7, 19, 17, 30, 0)
            archived = backup.create_backup(self.db_path, backup_root, include_archive=True)
            folder_only = backup.create_backup(self.db_path, backup_root, include_archive=False)

        self.assertEqual(orphan.read_bytes(), b"ORIGINAL")
        self.assertNotEqual(archived.archive, orphan)
        self.assertTrue(archived.archive.is_file())
        self.assertNotEqual(folder_only.archive, orphan)
        self.assertFalse(folder_only.archive.exists())

    def test_existing_backup_directory_is_never_modified(self) -> None:
        backup_root = self.temp_root / "existing-folder"
        existing = backup_root / "learning_backup_20260719_173000"
        existing.mkdir(parents=True)
        sentinel = existing / "sentinel.txt"
        sentinel.write_bytes(b"ORIGINAL")
        with patch.object(backup, "datetime") as clock:
            clock.now.return_value = datetime(2026, 7, 19, 17, 30, 0)
            result = backup.create_backup(self.db_path, backup_root, include_archive=False)

        self.assertEqual(sentinel.read_bytes(), b"ORIGINAL")
        self.assertNotEqual(result.folder, existing)
        self.assertTrue(result.database_copy.is_file())

    def test_orphan_archive_survives_new_backup_failure(self) -> None:
        backup_root = self.temp_root / "orphan-failure"
        backup_root.mkdir()
        orphan = backup_root / "learning_backup_20260719_173000.zip"
        orphan.write_bytes(b"ORIGINAL")

        def fail_after_partial_archive(_folder, archive):
            archive.write_bytes(b"PARTIAL")
            raise RuntimeError("simulated zip failure")

        with (
            patch.object(backup, "datetime") as clock,
            patch.object(backup, "_zip_folder", side_effect=fail_after_partial_archive),
        ):
            clock.now.return_value = datetime(2026, 7, 19, 17, 30, 0)
            with self.assertRaisesRegex(RuntimeError, "simulated zip failure"):
                backup.create_backup(self.db_path, backup_root, include_archive=True)

        self.assertEqual(orphan.read_bytes(), b"ORIGINAL")
        self.assertEqual(list(backup_root.iterdir()), [orphan])

    def test_reservation_cleanup_failure_preserves_mkdir_error(self) -> None:
        backup_root = self.temp_root / "reservation-double-failure"
        backup_root.mkdir()
        primary_error = RuntimeError("PRIMARY_MKDIR")

        def fail_candidate_mkdir(path, *args, **kwargs):
            if path == backup_root:
                return None
            raise primary_error

        with (
            patch.object(type(backup_root), "mkdir", autospec=True, side_effect=fail_candidate_mkdir),
            patch.object(
                type(backup_root),
                "unlink",
                autospec=True,
                side_effect=PermissionError("CLEANUP_UNLINK"),
            ),
        ):
            with self.assertRaises(RuntimeError) as captured:
                backup._reserve_backup_paths(backup_root, "20260719_173000")

        self.assertIs(captured.exception, primary_error)

    def test_backup_of_empty_explicit_database_never_seeds_production_data(self) -> None:
        empty_db = self.temp_root / "empty.sqlite"
        database.initialize_database(empty_db)
        original_bytes = empty_db.read_bytes()

        with patch.object(
            database,
            "import_current_json_files",
            side_effect=AssertionError("production seed read"),
        ) as production_seed:
            result = backup.create_backup(
                empty_db,
                self.temp_root / "empty-backup",
                include_archive=False,
            )

        production_seed.assert_not_called()
        self.assertEqual(empty_db.read_bytes(), original_bytes)
        exported = json.loads(
            (result.folder / "learning_records_export.json").read_text(encoding="utf-8")
        )
        self.assertEqual(exported, {"records": []})
        with database.connect(result.database_copy) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0], 0)

    def test_online_backup_reads_committed_wal_with_source_connection_open(self) -> None:
        source_connection = database.connect(self.db_path)
        try:
            source_connection.execute("PRAGMA journal_mode = WAL")
            source_connection.execute(
                "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
                ("wal-marker", '"committed"'),
            )
            source_connection.commit()

            result = backup.create_backup(
                self.db_path, self.temp_root / "wal-backup", include_archive=False
            )

            with database.connect(result.database_copy) as copied:
                marker = copied.execute(
                    "SELECT value_json FROM app_settings WHERE key = ?", ("wal-marker",)
                ).fetchone()[0]
            self.assertEqual(marker, '"committed"')
            self.assertEqual(
                backup.integrity_check(result.database_copy),
                "SQLite: ok；外键检查通过；文本编码检查通过",
            )
        finally:
            source_connection.close()

    def test_restore_copies_database_and_controls_safety_backup(self) -> None:
        source = backup.create_backup(
            self.db_path, self.temp_root / "source-backup", include_archive=False
        ).database_copy
        target = self.temp_root / "restore" / "target.sqlite"
        database.initialize_database(target)

        with patch.object(backup, "create_backup") as safety_backup:
            restored = backup.restore_database_from_backup(source, target, safety_backup=True)
        self.assertEqual(restored, target)
        safety_backup.assert_called_once_with(target)
        with database.connect(target) as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM subjects").fetchone()[0],
                "测试占位学科",
            )

        second_target = self.temp_root / "restore" / "without-safety.sqlite"
        with patch.object(backup, "create_backup") as safety_backup:
            backup.restore_database_from_backup(source, second_target, safety_backup=False)
        safety_backup.assert_not_called()
        self.assertEqual(
            backup.integrity_check(second_target),
            "SQLite: ok；外键检查通过；文本编码检查通过",
        )

    def test_restore_with_safety_backup_stays_under_temp_backup_root(self) -> None:
        source = backup.create_backup(
            self.db_path,
            self.temp_root / "source-for-restore",
            include_archive=False,
        ).database_copy
        target = self.temp_root / "restore-real" / "target.sqlite"
        database.initialize_database(target)
        with database.connect(target) as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
                ("before-restore", '"present"'),
            )
        safety_root = self.temp_root / "safety-backups"

        restored = backup.restore_database_from_backup(
            source,
            target,
            safety_backup=True,
            backup_root=safety_root,
        )

        self.assertEqual(restored, target)
        folders = [path for path in safety_root.iterdir() if path.is_dir()]
        self.assertEqual(len(folders), 1)
        safety_copy = folders[0] / "learning_app.sqlite"
        with database.connect(safety_copy) as connection:
            marker = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                ("before-restore",),
            ).fetchone()[0]
        self.assertEqual(marker, '"present"')
        with database.connect(target) as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM subjects").fetchone()[0],
                "测试占位学科",
            )

    def test_safety_backup_failure_leaves_target_unchanged(self) -> None:
        source = backup.create_backup(
            self.db_path,
            self.temp_root / "source-for-failed-restore",
            include_archive=False,
        ).database_copy
        target = self.temp_root / "failed-restore" / "target.sqlite"
        database.initialize_database(target)
        with database.connect(target) as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
                ("original", '"unchanged"'),
            )
        original_bytes = target.read_bytes()

        with patch.object(
            backup,
            "_backup_sqlite",
            side_effect=RuntimeError("simulated backup failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated backup failure"):
                backup.restore_database_from_backup(
                    source,
                    target,
                    safety_backup=True,
                    backup_root=self.temp_root / "failed-safety-backups",
                )

        self.assertEqual(target.read_bytes(), original_bytes)
        with database.connect(target) as connection:
            marker = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                ("original",),
            ).fetchone()[0]
        self.assertEqual(marker, '"unchanged"')

    def test_restore_copy_failure_after_safety_backup_leaves_target_unchanged(self) -> None:
        source = backup.create_backup(
            self.db_path,
            self.temp_root / "source-for-copy-failure",
            include_archive=False,
        ).database_copy
        target = self.temp_root / "copy-failure" / "target.sqlite"
        database.initialize_database(target)
        with database.connect(target) as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
                ("original", '"unchanged"'),
            )
        original_bytes = target.read_bytes()

        def partially_write_then_fail(_source, destination):
            destination = type(target)(destination)
            destination.write_bytes(b"NEW-")
            raise RuntimeError("simulated restore copy failure")

        with patch.object(
            backup.shutil,
            "copy2",
            side_effect=partially_write_then_fail,
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated restore copy failure"):
                backup.restore_database_from_backup(
                    source,
                    target,
                    safety_backup=True,
                    backup_root=self.temp_root / "copy-failure-safety",
                )

        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(list(target.parent.glob(f"{target.name}.*.tmp")), [])
        with database.connect(target) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT value_json FROM app_settings WHERE key = ?",
                    ("original",),
                ).fetchone()[0],
                '"unchanged"',
            )

    def test_integrity_check_reports_text_corruption(self) -> None:
        with database.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
                ("broken", '"???"'),
            )

        result = backup.integrity_check(self.db_path)

        self.assertIn("SQLite: ok", result)
        self.assertIn("文本编码异常 1 处", result)
        self.assertIn("app_settings", result)


if __name__ == "__main__":
    import unittest

    unittest.main()
