from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_app.core.prerequisite_graph import (
    make_prerequisite_edge,
    validate_prerequisite_source,
)
from study_app.data import database
from data_test_support import initialize_legacy_base_database


class PrerequisiteGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "prerequisites.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL)
            connection.execute(database.KNOWLEDGE_PREREQUISITES_TABLE_SQL)
            connection.execute(database.KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL)
            active_subject = connection.execute(
                """
                INSERT INTO subjects(name, status, source_json)
                VALUES (?, ?, '{}')
                """,
                ("活动学科", "active"),
            ).lastrowid
            archived_subject = connection.execute(
                """
                INSERT INTO subjects(name, status, source_json)
                VALUES (?, ?, '{}')
                """,
                ("封存学科", "archived"),
            ).lastrowid
            active_module = connection.execute(
                """
                INSERT INTO modules(subject_id, name, source_json)
                VALUES (?, ?, '{}')
                """,
                (active_subject, "活动模块"),
            ).lastrowid
            archived_module = connection.execute(
                """
                INSERT INTO modules(subject_id, name, source_json)
                VALUES (?, ?, '{}')
                """,
                (archived_subject, "封存模块"),
            ).lastrowid
            for name in ("A", "B", "C", "D"):
                connection.execute(
                    """
                    INSERT INTO topics(module_id, name, source_json)
                    VALUES (?, ?, '{}')
                    """,
                    (active_module, name),
                )
            connection.execute(
                """
                INSERT INTO topics(module_id, name, source_json)
                VALUES (?, ?, '{}')
                """,
                (archived_module, "Z"),
            )
        identities = database.register_topic_identities(self.db_path)
        self.keys = {item.topic_name: item.topic_key for item in identities}

    def replace(self, topic: str, prerequisites: list[str], **kwargs):
        return database.replace_prerequisite_edges(
            self.keys[topic],
            [self.keys[item] for item in prerequisites],
            kwargs.pop("source", "user_confirmed"),
            kwargs.pop("source_data", {"note": "用户确认"}),
            self.db_path,
        )

    def test_explicit_edges_round_trip_direct_and_upstream_queries(self) -> None:
        self.replace("B", ["A"])
        self.replace("C", ["B"])
        self.replace("D", ["A", "C"])

        direct = database.get_direct_prerequisites(self.keys["D"], self.db_path)
        self.assertEqual(
            [edge.prerequisite_topic_key for edge in direct],
            [self.keys["A"], self.keys["C"]],
        )
        dependents = database.get_direct_dependents(self.keys["A"], self.db_path)
        self.assertEqual(
            [edge.topic_key for edge in dependents],
            [self.keys["B"], self.keys["D"]],
        )
        self.assertEqual(
            database.get_upstream_prerequisite_paths(self.keys["D"], self.db_path),
            (
                (self.keys["A"],),
                (self.keys["C"],),
                (self.keys["C"], self.keys["B"]),
                (self.keys["C"], self.keys["B"], self.keys["A"]),
            ),
        )

    def test_cycle_rejection_keeps_existing_graph_unchanged(self) -> None:
        self.replace("B", ["A"])
        self.replace("C", ["B"])
        before = database.list_prerequisite_edges(self.db_path)
        with self.assertRaisesRegex(ValueError, "形成环"):
            self.replace("A", ["C"])
        self.assertEqual(database.list_prerequisite_edges(self.db_path), before)

    def test_self_duplicate_unknown_and_mixed_replacement_are_atomic(self) -> None:
        self.replace("D", ["A"])
        before = database.list_prerequisite_edges(self.db_path)
        with self.assertRaisesRegex(ValueError, "自身"):
            self.replace("A", ["A"])
        with self.assertRaisesRegex(ValueError, "重复"):
            database.replace_prerequisite_edges(
                self.keys["D"],
                [self.keys["A"], self.keys["A"]],
                "user_confirmed",
                {},
                self.db_path,
            )
        unknown = "topic:v1:" + "f" * 64
        with self.assertRaises(LookupError):
            database.replace_prerequisite_edges(
                self.keys["D"],
                [self.keys["B"], unknown],
                "user_confirmed",
                {},
                self.db_path,
            )
        self.assertEqual(database.list_prerequisite_edges(self.db_path), before)

    def test_only_confirmed_local_sources_and_safe_metadata_are_accepted(self) -> None:
        for source in ("llm", "inferred", "", None, True):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_prerequisite_source(source)
        for source_data in (
            {"api_key": "SECRET"},
            {"prompt": "PRIVATE"},
            {"audit": {"token": "SECRET"}},
            {"nested": object()},
            "not-an-object",
        ):
            with self.subTest(source_data=source_data), self.assertRaises(ValueError):
                database.replace_prerequisite_edges(
                    self.keys["B"],
                    [self.keys["A"]],
                    "user_confirmed",
                    source_data,
                    self.db_path,
                )
        edge = make_prerequisite_edge(
            self.keys["B"],
            self.keys["A"],
            "controlled_local_config",
            {"ticket": "confirmed-1"},
        )
        self.assertEqual(edge.source, "controlled_local_config")

    def test_idempotent_replace_does_not_change_database_bytes(self) -> None:
        self.replace("B", ["A"])
        before = self.db_path.read_bytes()
        self.replace("B", ["A"])
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_archived_subject_edge_is_preserved_for_history_queries(self) -> None:
        self.replace("A", ["Z"])
        edge = database.get_direct_prerequisites(self.keys["A"], self.db_path)[0]
        archived = database.get_topic_identity(
            edge.prerequisite_topic_key, self.db_path
        )
        self.assertEqual(archived.subject_name, "封存学科")
        self.assertEqual(edge.source_data, {"note": "用户确认"})

    def test_missing_table_and_general_schema_never_install_prerequisites(self) -> None:
        unmigrated = Path(self.temp_dir.name) / "unmigrated.sqlite"
        initialize_legacy_base_database(unmigrated)
        with database.connect_readonly(unmigrated) as connection:
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'knowledge_prerequisites'
                    """
                ).fetchone()
            )
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.list_prerequisite_edges(unmigrated)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.replace_prerequisite_edges(
                self.keys["B"],
                [self.keys["A"]],
                "user_confirmed",
                {},
                unmigrated,
            )

    def test_read_queries_are_byte_for_byte_read_only(self) -> None:
        self.replace("B", ["A"])
        before = self.db_path.read_bytes()
        self.assertEqual(len(database.list_prerequisite_edges(self.db_path)), 1)
        self.assertEqual(
            len(database.get_direct_prerequisites(self.keys["B"], self.db_path)),
            1,
        )
        self.assertEqual(
            len(database.get_direct_dependents(self.keys["A"], self.db_path)),
            1,
        )
        self.assertEqual(
            database.get_upstream_prerequisite_paths(
                self.keys["B"], self.db_path
            ),
            ((self.keys["A"],),),
        )
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
