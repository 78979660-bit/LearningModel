from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_app.core.topic_identity import (
    TOPIC_IDENTITY_VERSION,
    topic_key_for_id,
    validate_topic_key,
    validate_topic_row_id,
)
from study_app.data import database
from data_test_support import initialize_legacy_base_database


class TopicIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "identity.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL)
            subject_id = connection.execute(
                "INSERT INTO subjects(name, source_json) VALUES (?, '{}')",
                ("测试学科",),
            ).lastrowid
            module_id = connection.execute(
                """
                INSERT INTO modules(subject_id, name, source_json)
                VALUES (?, ?, '{}')
                """,
                (subject_id, "测试模块"),
            ).lastrowid
            self.topic_ids = [
                int(
                    connection.execute(
                        """
                        INSERT INTO topics(module_id, name, source_json)
                        VALUES (?, ?, '{}')
                        """,
                        (module_id, name),
                    ).lastrowid
                )
                for name in ("知识点A", "知识点B", "知识点C")
            ]

    def test_key_format_and_strict_row_id_validation(self) -> None:
        key = topic_key_for_id(1)
        self.assertEqual(len(key), 73)
        self.assertEqual(validate_topic_key(key), key)
        self.assertEqual(validate_topic_row_id(1), 1)
        for value in (None, True, False, 0, -1, 1.0, "1"):
            with self.subTest(topic_id=value), self.assertRaises(ValueError):
                validate_topic_row_id(value)
        for value in (None, True, -1, 1.0, "1"):
            with self.subTest(generation=value), self.assertRaises(ValueError):
                topic_key_for_id(1, value)
        for value in (None, "", "topic:v1:xyz", key.upper(), True):
            with self.subTest(topic_key=value), self.assertRaises(ValueError):
                validate_topic_key(value)

    def test_explicit_registration_is_idempotent_and_readers_do_not_seed(self) -> None:
        self.assertEqual(database.list_topic_identities(self.db_path), ())
        with database.connect_readonly(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_topic_registry"
                ).fetchone()[0],
                0,
            )

        first = database.register_topic_identities(self.db_path)
        second = database.register_topic_identities(self.db_path)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        self.assertEqual({item.topic_id for item in first}, set(self.topic_ids))
        self.assertTrue(all(item.identity_version == TOPIC_IDENTITY_VERSION for item in first))

    def test_path_rename_keeps_key_and_resolves_current_names(self) -> None:
        identities = database.register_topic_identities(self.db_path)
        original = next(item for item in identities if item.topic_name == "知识点A")
        with database.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE topics SET name = ? WHERE id = ?",
                ("知识点A-改名", original.topic_id),
            )

        current = database.get_topic_identity(original.topic_key, self.db_path)
        self.assertEqual(current.topic_key, original.topic_key)
        self.assertEqual(current.topic_name, "知识点A-改名")
        self.assertIsNone(
            database.resolve_topic_identity(
                "测试学科", "测试模块", "知识点A", self.db_path
            )
        )
        self.assertEqual(
            database.resolve_topic_identity(
                "测试学科", "测试模块", "知识点A-改名", self.db_path
            ).topic_key,
            original.topic_key,
        )

    def test_explicit_rebind_preserves_key_and_rejects_occupied_target(self) -> None:
        identities = database.register_topic_identities(self.db_path)
        by_name = {item.topic_name: item for item in identities}
        with database.connect(self.db_path) as connection:
            connection.execute(
                "DELETE FROM knowledge_topic_registry WHERE topic_key = ?",
                (by_name["知识点C"].topic_key,),
            )

        rebound = database.rebind_topic_identity(
            by_name["知识点A"].topic_key,
            by_name["知识点C"].topic_id,
            self.db_path,
        )
        self.assertEqual(rebound.topic_key, by_name["知识点A"].topic_key)
        self.assertEqual(rebound.topic_name, "知识点C")
        with self.assertRaises(ValueError):
            database.rebind_topic_identity(
                rebound.topic_key,
                by_name["知识点B"].topic_id,
                self.db_path,
            )
        self.assertEqual(
            database.get_topic_identity(rebound.topic_key, self.db_path).topic_id,
            by_name["知识点C"].topic_id,
        )
        refreshed = database.register_topic_identities(self.db_path)
        old_row_identity = next(
            item for item in refreshed if item.topic_id == by_name["知识点A"].topic_id
        )
        self.assertNotEqual(old_row_identity.topic_key, rebound.topic_key)

    def test_registration_preserves_existing_key_and_allocates_around_collision(self) -> None:
        key_for_second = topic_key_for_id(self.topic_ids[1])
        with database.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO knowledge_topic_registry(topic_key, topic_id, identity_version)
                VALUES (?, ?, ?)
                """,
                (key_for_second, self.topic_ids[0], TOPIC_IDENTITY_VERSION),
            )
        identities = database.register_topic_identities(self.db_path)
        with database.connect_readonly(self.db_path) as connection:
            rows = connection.execute(
                "SELECT topic_key, topic_id FROM knowledge_topic_registry"
            ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            database.get_topic_identity(key_for_second, self.db_path).topic_id,
            self.topic_ids[0],
        )
        second = next(item for item in identities if item.topic_id == self.topic_ids[1])
        self.assertNotEqual(second.topic_key, key_for_second)

    def test_general_schema_does_not_install_f2_table_before_cr(self) -> None:
        unmigrated = Path(self.temp_dir.name) / "unmigrated.sqlite"
        initialize_legacy_base_database(unmigrated)
        with database.connect_readonly(unmigrated) as connection:
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'knowledge_topic_registry'
                    """
                ).fetchone()
            )
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.list_topic_identities(unmigrated)

    def test_missing_table_is_not_reinstalled_by_read_or_write_api(self) -> None:
        with database.connect(self.db_path) as connection:
            connection.execute("DROP TABLE knowledge_topic_registry")
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.list_topic_identities(self.db_path)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.register_topic_identities(self.db_path)
        with database.connect_readonly(self.db_path) as connection:
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'knowledge_topic_registry'
                    """
                ).fetchone()
            )

    def test_invalid_input_does_not_create_missing_database(self) -> None:
        missing = Path(self.temp_dir.name) / "missing" / "identity.sqlite"
        with self.assertRaises(ValueError):
            database.get_topic_identity("bad-key", missing)
        with self.assertRaises(ValueError):
            database.rebind_topic_identity(topic_key_for_id(1), True, missing)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_identity_readers_are_byte_for_byte_read_only(self) -> None:
        identities = database.register_topic_identities(self.db_path)
        target = identities[0]
        before = self.db_path.read_bytes()
        self.assertEqual(database.list_topic_identities(self.db_path), identities)
        self.assertEqual(
            database.get_topic_identity(target.topic_key, self.db_path), target
        )
        self.assertEqual(
            database.resolve_topic_identity(
                target.subject_name,
                target.module_name,
                target.topic_name,
                self.db_path,
            ),
            target,
        )
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
