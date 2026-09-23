from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_app.core.oj_topics import normalize_topic_keys, validate_mapping_source
from study_app.core.topic_identity import topic_key_for_id
from study_app.data import database


class OJTopicMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "oj-topics.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL)
            subject_id = connection.execute(
                "INSERT INTO subjects(name, source_json) VALUES (?, '{}')",
                ("计算机科学",),
            ).lastrowid
            module_id = connection.execute(
                "INSERT INTO modules(subject_id, name, source_json) VALUES (?, ?, '{}')",
                (subject_id, "算法"),
            ).lastrowid
            self.topic_ids = [
                int(
                    connection.execute(
                        "INSERT INTO topics(module_id, name, source_json) VALUES (?, ?, '{}')",
                        (module_id, name),
                    ).lastrowid
                )
                for name in ("动态规划", "图搜索")
            ]
        database.register_topic_identities(self.db_path)
        database.install_oj_schema(self.db_path)
        self.problem = database.register_oj_problem(
            "leetcode", "1", "动态规划关键词题名", db_path=self.db_path
        )
        self.topic_keys = tuple(topic_key_for_id(item) for item in self.topic_ids)

    def test_mapping_input_is_strict(self) -> None:
        self.assertEqual(
            normalize_topic_keys(list(reversed(self.topic_keys))),
            tuple(sorted(self.topic_keys)),
        )
        for value in (None, "topic", {"topic"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_topic_keys(value)
        with self.assertRaises(ValueError):
            normalize_topic_keys([self.topic_keys[0], self.topic_keys[0]])
        for value in (None, "keyword", "llm", True):
            with self.subTest(source=value), self.assertRaises(ValueError):
                validate_mapping_source(value)

    def test_explicit_many_to_many_mapping_round_trip(self) -> None:
        mappings = database.replace_oj_problem_topics(
            self.problem.problem_key,
            list(reversed(self.topic_keys)),
            mapping_source="manual",
            note="用户确认",
            db_path=self.db_path,
        )
        self.assertEqual(tuple(item.topic_key for item in mappings), tuple(sorted(self.topic_keys)))
        self.assertTrue(all(item.mapping_source == "manual" for item in mappings))
        self.assertTrue(all(item.note == "用户确认" for item in mappings))

        second = database.register_oj_problem(
            "local", "dp-1", "同一知识点的另一题", db_path=self.db_path
        )
        database.replace_oj_problem_topics(
            second.problem_key,
            [self.topic_keys[0]],
            mapping_source="offline_import",
            db_path=self.db_path,
        )
        self.assertEqual(len(database.list_oj_problem_topics(second.problem_key, self.db_path)), 1)

    def test_title_keyword_never_creates_implicit_mapping(self) -> None:
        self.assertEqual(
            database.list_oj_problem_topics(self.problem.problem_key, self.db_path),
            (),
        )

    def test_replacement_is_atomic_for_missing_problem_or_topic(self) -> None:
        original = database.replace_oj_problem_topics(
            self.problem.problem_key,
            [self.topic_keys[0]],
            db_path=self.db_path,
        )
        with self.assertRaises(LookupError):
            database.replace_oj_problem_topics(
                self.problem.problem_key,
                [self.topic_keys[1], topic_key_for_id(999999)],
                db_path=self.db_path,
            )
        self.assertEqual(
            database.list_oj_problem_topics(self.problem.problem_key, self.db_path),
            original,
        )
        missing_problem = database.register_oj_problem(
            "local", "gone", "删除后的题", db_path=self.db_path
        )
        with database.connect(self.db_path) as connection:
            connection.execute(
                "DELETE FROM oj_problems WHERE problem_key = ?",
                (missing_problem.problem_key,),
            )
        with self.assertRaises(LookupError):
            database.replace_oj_problem_topics(
                missing_problem.problem_key,
                [self.topic_keys[0]],
                db_path=self.db_path,
            )

    def test_empty_replacement_unmaps_without_deleting_problem_or_topic(self) -> None:
        database.replace_oj_problem_topics(
            self.problem.problem_key, self.topic_keys, db_path=self.db_path
        )
        self.assertEqual(
            database.replace_oj_problem_topics(
                self.problem.problem_key, [], db_path=self.db_path
            ),
            (),
        )
        self.assertEqual(
            database.get_oj_problem(self.problem.problem_key, self.db_path), self.problem
        )
        self.assertIsNotNone(database.get_topic_identity(self.topic_keys[0], self.db_path))

    def test_mapping_reader_is_byte_for_byte_read_only(self) -> None:
        database.replace_oj_problem_topics(
            self.problem.problem_key, self.topic_keys, db_path=self.db_path
        )
        before = self.db_path.read_bytes()
        self.assertEqual(len(database.list_oj_problem_topics(self.problem.problem_key, self.db_path)), 2)
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
