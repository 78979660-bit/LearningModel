from __future__ import annotations

import sqlite3

from data_test_support import IsolatedDatabaseTestCase
from study_app.data import database


class DataStudyPlanTests(IsolatedDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.initialize_seed_guarded_database()

    @staticmethod
    def item(item_hash: str, text: str = "复习主题乙") -> dict:
        return {
            "section_key": "short",
            "section_title": "短期计划",
            "day_index": 1,
            "item_type": "task",
            "item_text": text,
            "item_hash": item_hash,
        }

    def create_plan(self, scope: str | None, signature: str, items: list[dict] | None = None) -> int:
        return database.create_study_plan(
            scope,
            "2026-07-18",
            "2026-07-25",
            signature,
            {"status": "local", "signature": signature},
            items if items is not None else [self.item(f"item-{signature}")],
            self.db_path,
        )

    def test_create_plan_archives_only_same_scope(self) -> None:
        old_all = self.create_plan(None, "all-old")
        subject_plan = self.create_plan("测试占位学科", "subject")
        new_all = self.create_plan(None, "all-new")

        with database.connect(self.db_path) as connection:
            rows = {
                row["id"]: row["status"]
                for row in connection.execute("SELECT id, status FROM study_plans")
            }
        self.assertEqual(rows[old_all], "archived")
        self.assertEqual(rows[subject_plan], "active")
        self.assertEqual(rows[new_all], "active")
        self.assertEqual(database.get_active_study_plan(None, self.db_path)["id"], new_all)
        self.assertEqual(
            database.get_active_study_plan("测试占位学科", self.db_path)["id"],
            subject_plan,
        )

    def test_duplicate_item_hash_rolls_back_new_plan_and_prior_archive(self) -> None:
        existing = self.create_plan(None, "stable")

        with self.assertRaises(sqlite3.IntegrityError):
            self.create_plan(
                None,
                "broken",
                [self.item("duplicate", "任务一"), self.item("duplicate", "任务二")],
            )

        active = database.get_active_study_plan(None, self.db_path)
        with database.connect(self.db_path) as connection:
            plan_count = connection.execute("SELECT COUNT(*) FROM study_plans").fetchone()[0]
        self.assertEqual(active["id"], existing)
        self.assertEqual(active["input_signature"], "stable")
        self.assertEqual(plan_count, 1)

    def test_active_plan_round_trip_and_item_state_transitions(self) -> None:
        plan_id = self.create_plan(
            "测试占位学科",
            "round-trip",
            [self.item("first", "任务一"), self.item("second", "任务二")],
        )
        plan = database.get_active_study_plan("测试占位学科", self.db_path)
        first_id, second_id = (item["id"] for item in plan["items"])

        database.update_study_plan_item_state(first_id, checked=True, db_path=self.db_path)
        database.update_study_plan_item_state(first_id, result="wrong", db_path=self.db_path)
        database.update_study_plan_item_state(second_id, result="correct", db_path=self.db_path)
        database.update_study_plan_item_state(second_id, db_path=self.db_path)

        updated = database.get_active_study_plan("测试占位学科", self.db_path)
        self.assertEqual(updated["id"], plan_id)
        self.assertEqual(updated["subject_scope"], "测试占位学科")
        self.assertEqual(updated["start_date"], "2026-07-18")
        self.assertEqual(updated["end_date"], "2026-07-25")
        self.assertEqual(updated["input_signature"], "round-trip")
        self.assertEqual(updated["plan"], {"status": "local", "signature": "round-trip"})
        self.assertEqual(
            [
                {
                    key: item[key]
                    for key in (
                        "section_key",
                        "section_title",
                        "day_index",
                        "item_type",
                        "item_text",
                        "item_order",
                        "item_hash",
                    )
                }
                for item in updated["items"]
            ],
            [
                {
                    "section_key": "short",
                    "section_title": "短期计划",
                    "day_index": 1,
                    "item_type": "task",
                    "item_text": "任务一",
                    "item_order": 0,
                    "item_hash": "first",
                },
                {
                    "section_key": "short",
                    "section_title": "短期计划",
                    "day_index": 1,
                    "item_type": "task",
                    "item_text": "任务二",
                    "item_order": 1,
                    "item_hash": "second",
                },
            ],
        )
        self.assertEqual(
            [(item["checked"], item["result"]) for item in updated["items"]],
            [(1, "wrong"), (1, "correct")],
        )

    def test_archiving_none_scope_leaves_named_scope_active(self) -> None:
        self.create_plan(None, "all")
        named_id = self.create_plan("测试占位学科", "named")

        database.archive_active_study_plan(None, self.db_path)

        self.assertIsNone(database.get_active_study_plan(None, self.db_path))
        self.assertEqual(
            database.get_active_study_plan("测试占位学科", self.db_path)["id"],
            named_id,
        )

    def test_recent_plan_texts_are_scope_filtered_and_newest_first(self) -> None:
        self.create_plan("测试占位学科", "first")
        self.create_plan(None, "all")
        self.create_plan("测试占位学科", "second")

        texts = database.recent_study_plan_texts(
            "测试占位学科", limit=2, db_path=self.db_path
        )

        self.assertEqual(len(texts), 2)
        self.assertIn("second", texts[0])
        self.assertIn("first", texts[1])
        self.assertNotIn("all", "\n".join(texts))


if __name__ == "__main__":
    import unittest

    unittest.main()
