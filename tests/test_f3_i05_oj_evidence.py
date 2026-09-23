from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_app.core.oj_evidence import load_oj_topic_evidence
from study_app.data import database


class OJEvidenceProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "evidence.sqlite"
        database.initialize_database(self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute(database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL)
            subject = connection.execute(
                "INSERT INTO subjects(name, source_json) VALUES (?, '{}')",
                ("计算机科学",),
            ).lastrowid
            module = connection.execute(
                "INSERT INTO modules(subject_id, name, source_json) VALUES (?, ?, '{}')",
                (subject, "算法"),
            ).lastrowid
            for name in ("动态规划", "图论"):
                connection.execute(
                    "INSERT INTO topics(module_id, name, source_json) VALUES (?, ?, '{}')",
                    (module, name),
                )
        self.topics = database.register_topic_identities(self.db_path)
        database.install_oj_schema(self.db_path)
        self.mapped = database.register_oj_problem(
            "leetcode", "1", "普通题名", db_path=self.db_path
        )
        self.keyword_only = database.register_oj_problem(
            "local", "2", "动态规划关键词但无映射", db_path=self.db_path
        )

    def add_attempt(self, problem_key: str, when: str, key: str):
        return database.append_oj_attempt(
            problem_key=problem_key,
            attempted_at=when,
            result="wrong",
            duration_seconds=300,
            independence="guided",
            hint_level="concept",
            error_type="algorithm",
            source_attempt_key=key,
            db_path=self.db_path,
        )

    def test_unmapped_keyword_problem_never_leaks_into_evidence(self) -> None:
        self.add_attempt(self.keyword_only.problem_key, "2026-09-17T08:00:00Z", "k1")
        self.assertEqual(
            load_oj_topic_evidence(self.topics[0].topic_key, db_path=self.db_path),
            (),
        )

    def test_explicit_mapping_projects_traceable_frozen_fields(self) -> None:
        database.replace_oj_problem_topics(
            self.mapped.problem_key,
            [self.topics[0].topic_key],
            mapping_source="manual",
            db_path=self.db_path,
        )
        saved = self.add_attempt(self.mapped.problem_key, "2026-09-17T08:00:00Z", "m1")
        evidence = load_oj_topic_evidence(self.topics[0].topic_key, db_path=self.db_path)
        self.assertEqual(len(evidence), 1)
        item = evidence[0]
        self.assertEqual(item.problem_key, self.mapped.problem_key)
        self.assertEqual(item.attempt_id, saved.attempt_id)
        self.assertEqual(item.mapping_source, "manual")
        self.assertEqual(item.result, "wrong")
        self.assertEqual(item.independence, "guided")
        self.assertEqual(item.hint_level, "concept")

    def test_one_attempt_can_be_explicit_evidence_for_multiple_topics(self) -> None:
        database.replace_oj_problem_topics(
            self.mapped.problem_key,
            [item.topic_key for item in self.topics],
            mapping_source="offline_import",
            db_path=self.db_path,
        )
        saved = self.add_attempt(self.mapped.problem_key, "2026-09-17T08:00:00Z", "multi")
        ids = {
            load_oj_topic_evidence(item.topic_key, db_path=self.db_path)[0].attempt_id
            for item in self.topics
        }
        self.assertEqual(ids, {saved.attempt_id})

    def test_as_of_time_excludes_future_attempts(self) -> None:
        database.replace_oj_problem_topics(
            self.mapped.problem_key, [self.topics[0].topic_key], db_path=self.db_path
        )
        first = self.add_attempt(self.mapped.problem_key, "2026-09-17T08:00:00Z", "t1")
        self.add_attempt(self.mapped.problem_key, "2026-09-18T08:00:00Z", "t2")
        evidence = load_oj_topic_evidence(
            self.topics[0].topic_key,
            as_of_time="2026-09-17T23:59:59+00:00",
            db_path=self.db_path,
        )
        self.assertEqual([item.attempt_id for item in evidence], [first.attempt_id])

    def test_projection_is_byte_for_byte_read_only_and_unknown_topic_fails(self) -> None:
        database.replace_oj_problem_topics(
            self.mapped.problem_key, [self.topics[0].topic_key], db_path=self.db_path
        )
        self.add_attempt(self.mapped.problem_key, "2026-09-17T08:00:00Z", "ro")
        before = self.db_path.read_bytes()
        self.assertEqual(
            len(load_oj_topic_evidence(self.topics[0].topic_key, db_path=self.db_path)),
            1,
        )
        self.assertEqual(self.db_path.read_bytes(), before)
        unknown = self.topics[0].topic_key[:-1] + ("0" if self.topics[0].topic_key[-1] != "0" else "1")
        with self.assertRaises(LookupError):
            load_oj_topic_evidence(unknown, db_path=self.db_path)


if __name__ == "__main__":
    unittest.main()
