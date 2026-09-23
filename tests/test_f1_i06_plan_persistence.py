from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from study_app.core.budgeted_day_plan import BudgetPlanInput, build_budgeted_day_plan
from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source
from study_app.data import database


def candidate(name: str, minutes: int, subject: str = "A") -> PlanCandidate:
    kind, source_id = "model_topic_practice", f"topic:{name}"
    return PlanCandidate(task_id_for_source(subject, kind, source_id), subject, kind,
                         source_id, name, {"priority": 0.5}, minutes, "user")


def day_plan(*items: PlanCandidate, budget: int = 60):
    return build_budgeted_day_plan(
        BudgetPlanInput("2026-09-16", budget, ("A", "B"), as_of_date="2026-09-16"),
        CandidateCollection(tuple(items), ()),
    )


class BudgetPlanPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "plans.sqlite"
        database.initialize_database(self.db_path)

    @staticmethod
    def legacy_item(item_hash: str = "legacy") -> dict:
        return {"section_key": "short", "section_title": "今日计划", "day_index": 1,
                "item_type": "check", "item_text": "旧文本任务", "item_hash": item_hash}

    def active_rows(self) -> list[tuple]:
        with database.connect_readonly(self.db_path) as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT id, subject_scope, status FROM study_plans WHERE status='active' ORDER BY id")]

    def test_one_canonical_plan_archives_every_old_active_scope_and_views_share_budget(self) -> None:
        old_all = database.create_study_plan(None, "2026-09-16", "2026-09-16", "all",
                                             {"short": ["all"]}, [self.legacy_item("all")], self.db_path)
        old_a = database.create_study_plan("A", "2026-09-16", "2026-09-16", "a",
                                           {"short": ["a"]}, [self.legacy_item("a")], self.db_path)
        first, second = candidate("first", 20, "A"), candidate("second", 30, "B")
        plan_id = database.create_budgeted_day_plan("2026-09-16", 60,
                                                    day_plan(first, second), self.db_path)
        self.assertEqual(self.active_rows(), [(plan_id, database.BUDGET_PLAN_SCOPE, "active")])
        with database.connect_readonly(self.db_path) as connection:
            statuses = {row["id"]: row["status"] for row in connection.execute(
                "SELECT id, status FROM study_plans")}
        self.assertEqual((statuses[old_all], statuses[old_a]), ("archived", "archived"))
        all_view = database.get_budgeted_day_plan("2026-09-16", db_path=self.db_path)
        a_view = database.get_budgeted_day_plan("2026-09-16", "A", self.db_path)
        self.assertEqual(all_view["id"], a_view["id"])
        self.assertEqual(all_view["budget_minutes"], a_view["budget_minutes"])
        self.assertEqual(all_view["summary"], a_view["summary"])
        self.assertEqual([row["task_id"] for row in a_view["items"]], [first.task_id])

    def test_regeneration_carries_task_completion_and_result_without_duplicate(self) -> None:
        first, second = candidate("first", 20), candidate("second", 30)
        old_id = database.create_budgeted_day_plan("2026-09-16", 60,
                                                   day_plan(first, second), self.db_path)
        old = database.get_budgeted_day_plan("2026-09-16", db_path=self.db_path)
        first_row = next(row for row in old["items"] if row["task_id"] == first.task_id)
        database.update_study_plan_item_state(first_row["id"], result="wrong", db_path=self.db_path)
        new_id = database.create_budgeted_day_plan("2026-09-16", 60,
                                                   day_plan(second, first), self.db_path)
        self.assertNotEqual(old_id, new_id)
        current = database.get_budgeted_day_plan("2026-09-16", db_path=self.db_path)
        carried = next(row for row in current["items"] if row["task_id"] == first.task_id)
        self.assertEqual((carried["checked"], carried["result"]), (1, "wrong"))
        self.assertEqual(len({row["task_id"] for row in current["items"]}), len(current["items"]))
        with database.connect_readonly(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM study_plans WHERE id=?", (old_id,)).fetchone()[0], "archived")

    def test_insert_failure_rolls_back_archive_and_new_plan(self) -> None:
        first = candidate("first", 20)
        old_id = database.create_budgeted_day_plan("2026-09-16", 60, day_plan(first), self.db_path)
        with database.connect(self.db_path) as connection:
            connection.execute("""
                CREATE TRIGGER reject_budget_item BEFORE INSERT ON study_plan_items
                WHEN NEW.task_id IS NOT NULL BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            database.create_budgeted_day_plan("2026-09-16", 60,
                                              day_plan(candidate("new", 10)), self.db_path)
        self.assertEqual(self.active_rows(), [(old_id, database.BUDGET_PLAN_SCOPE, "active")])
        with database.connect_readonly(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM study_plans").fetchone()[0], 1)

    def test_legacy_completed_unknown_blocks_pending_and_preserves_old_plan(self) -> None:
        old_id = database.create_study_plan(None, "2026-09-16", "2026-09-16", "legacy",
                                            {"short": ["legacy"]}, [self.legacy_item()], self.db_path)
        legacy = database.get_active_study_plan(None, self.db_path)
        database.update_study_plan_item_state(legacy["items"][0]["id"], checked=True,
                                              db_path=self.db_path)
        with self.assertRaisesRegex(ValueError, "完成占用未知"):
            database.create_budgeted_day_plan("2026-09-16", 60,
                                              day_plan(candidate("new", 10)), self.db_path)
        self.assertEqual(self.active_rows(), [(old_id, "", "active")])
        empty = day_plan(budget=60)
        new_id = database.create_budgeted_day_plan("2026-09-16", 60, empty, self.db_path)
        current = database.get_budgeted_day_plan("2026-09-16", db_path=self.db_path)
        self.assertEqual(current["id"], new_id)
        self.assertTrue(current["summary"]["completed_occupancy_unknown"])
        self.assertEqual(current["summary"]["legacy_completed_unknown_count"], 1)
        with database.connect_readonly(self.db_path) as connection:
            old = connection.execute("SELECT status FROM study_plans WHERE id=?", (old_id,)).fetchone()
            evidence = connection.execute(
                """SELECT states.checked FROM study_plan_item_states states
                   JOIN study_plan_items items ON items.id=states.item_id
                   WHERE items.plan_id=?""", (old_id,)).fetchone()
        self.assertEqual(old["status"], "archived")
        self.assertEqual(evidence["checked"], 1)

    def test_schema_must_be_explicitly_installed(self) -> None:
        other = Path(self.temp_dir.name) / "unmigrated.sqlite"
        with closing(sqlite3.connect(other)) as connection:
            connection.executescript("""
                CREATE TABLE study_plans (id INTEGER PRIMARY KEY);
                CREATE TABLE study_plan_items (id INTEGER PRIMARY KEY);
            """)
        with self.assertRaises(database.DatabaseNotInitializedError):
            database.create_budgeted_day_plan("2026-09-16", 60,
                                              day_plan(candidate("new", 10)), other)
        with closing(sqlite3.connect(other)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(study_plans)")}
        self.assertNotIn("plan_date", columns)

    def test_duplicate_task_is_rejected_before_archive(self) -> None:
        first = candidate("first", 20)
        old_id = database.create_budgeted_day_plan("2026-09-16", 60, day_plan(first), self.db_path)
        valid = day_plan(candidate("new", 10))
        duplicate_plan = type(valid)(
            selected=(valid.selected[0], valid.selected[0]), excluded=valid.excluded,
            completed=valid.completed, planned_minutes=20, remaining_minutes=40,
            over_budget_completed_minutes=0, input_signature="duplicate")
        with self.assertRaisesRegex(ValueError, "重复任务 ID"):
            database.create_budgeted_day_plan("2026-09-16", 60, duplicate_plan, self.db_path)
        self.assertEqual(self.active_rows(), [(old_id, database.BUDGET_PLAN_SCOPE, "active")])

    def test_forged_totals_and_estimates_are_rejected_before_archive(self) -> None:
        first = candidate("first", 20)
        old_id = database.create_budgeted_day_plan("2026-09-16", 60, day_plan(first), self.db_path)
        valid = day_plan(candidate("new", 10))
        bad_total = type(valid)(
            selected=valid.selected, excluded=valid.excluded, completed=valid.completed,
            planned_minutes=9, remaining_minutes=51, over_budget_completed_minutes=0,
            input_signature="forged-total")
        bad_decision = type(valid.selected[0])(
            **{**valid.selected[0].__dict__, "estimated_minutes": 0})
        bad_estimate = type(valid)(
            selected=(bad_decision,), excluded=valid.excluded, completed=valid.completed,
            planned_minutes=0, remaining_minutes=60, over_budget_completed_minutes=0,
            input_signature="forged-estimate")
        for forged in (bad_total, bad_estimate):
            with self.subTest(signature=forged.input_signature), self.assertRaises(ValueError):
                database.create_budgeted_day_plan("2026-09-16", 60, forged, self.db_path)
        self.assertEqual(self.active_rows(), [(old_id, database.BUDGET_PLAN_SCOPE, "active")])


if __name__ == "__main__":
    unittest.main()
