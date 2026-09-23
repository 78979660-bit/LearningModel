from __future__ import annotations

import unittest
from dataclasses import replace

from study_app.core.budgeted_day_plan import BudgetPlanInput, build_budgeted_day_plan
from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source


def item(name: str, minutes: object, subject: str = "A", priority: object = 0.0,
         completion: str = "pending", **evidence: object) -> PlanCandidate:
    kind, source_id = "model_topic_practice", f"topic:{name}"
    return PlanCandidate(task_id_for_source(subject, kind, source_id), subject, kind,
                         source_id, name, {"priority": priority, **evidence},
                         minutes, "user" if minutes is not None else None, completion)


def plan(minutes: int, candidates: tuple[PlanCandidate, ...],
         exams: dict[str, str | None] | None = None, scope: str | None = None,
         active: tuple[str, ...] = ("A", "B")):
    return build_budgeted_day_plan(
        BudgetPlanInput("2026-09-16", minutes, active, scope, exams or {}, "2026-09-16"),
        CandidateCollection(candidates, ()),
    )


def titles(decisions) -> list[str]:
    return [decision.title for decision in decisions]


def reasons(decisions) -> dict[str, str]:
    return {decision.title: decision.rule_code for decision in decisions}


class BudgetSelectionTests(unittest.TestCase):
    def test_a1_exam_nearness_and_budget_limit(self) -> None:
        result = plan(60, (item("B30", 30, "B", 0.99), item("A40", 40, "A", 0.1)),
                      {"A": "2026-09-18"})
        self.assertEqual(titles(result.selected), ["A40"])
        self.assertEqual(reasons(result.excluded)["B30"], "budget_insufficient")
        self.assertEqual((result.planned_minutes, result.remaining_minutes), (40, 20))
        self.assertEqual(result.selected[0].rule_code, "exam_imminent")
        self.assertEqual(result.selected[0].ranking_evidence["exam_days_remaining"], 2)
        self.assertEqual(result.selected[0].ranking_evidence["estimate_source"], "user")

    def test_a2_skips_large_then_fits_shorter_task(self) -> None:
        result = plan(60, (item("B15", 15, "B", 0.1), item("B30", 30, "B", 0.8),
                           item("A40", 40, "A", 0.1)), {"A": "2026-09-18"})
        self.assertEqual(titles(result.selected), ["A40", "B15"])
        self.assertEqual(reasons(result.excluded)["B30"], "budget_insufficient")
        self.assertEqual((result.planned_minutes, result.remaining_minutes), (55, 5))

    def test_a3_completed_occupies_day_budget_even_in_other_scope(self) -> None:
        done = item("done20", 20, "B", completion="result_wrong")
        result = plan(45, (item("long30", 30, "A", 0.9), item("short20", 20, "A", 0.1), done),
                      scope="A")
        self.assertEqual(titles(result.selected), ["short20"])
        self.assertEqual(titles(result.completed), ["done20"])
        self.assertEqual(result.completed[0].rule_code, "completed_consumes_budget")
        self.assertEqual(reasons(result.excluded)["long30"], "budget_insufficient")
        self.assertEqual((result.planned_minutes, result.remaining_minutes), (40, 5))

    def test_a4_archived_and_a5_zero_budget(self) -> None:
        archived = plan(30, (item("archived10", 10, "B", 1), item("active25", 25, "A", 0.1)),
                        active=("A",))
        self.assertEqual(titles(archived.selected), ["active25"])
        self.assertEqual(reasons(archived.excluded)["archived10"], "subject_archived_or_unknown")
        empty = plan(0, (item("first", 10), item("second", 15)))
        self.assertEqual(empty.selected, ())
        self.assertEqual(empty.remaining_minutes, 0)
        self.assertEqual(set(reasons(empty.excluded).values()), {"budget_insufficient"})

    def test_a6_missing_invalid_estimate_and_source_never_schedule(self) -> None:
        base = item("missing", None)
        candidates = (base, item("nan", float("nan")), item("bool", True),
                      item("zero", 0), item("decimal", 3.5),
                      replace(item("wrong_source", 10), estimate_source="historical_actual"))
        result = plan(20, candidates)
        self.assertEqual(result.selected, ())
        self.assertEqual(reasons(result.excluded)["missing"], "missing_estimate")
        self.assertTrue(all(code == "invalid_estimate" for title, code in
                            reasons(result.excluded).items() if title != "missing"))
        self.assertEqual((result.planned_minutes, result.remaining_minutes), (0, 20))

    def test_a7_past_and_unset_exam_are_neutral_and_labelled(self) -> None:
        result = plan(20, (item("past", 10, "A"), item("unset", 10, "B")),
                      {"A": "2026-09-15", "B": None})
        self.assertEqual(len(result.selected), 2)
        by_title = {entry.title: entry for entry in result.selected}
        self.assertEqual(by_title["past"].ranking_evidence["exam_status"], "past")
        self.assertEqual(by_title["unset"].ranking_evidence["exam_status"], "unset")
        self.assertIn("考试日期已过", by_title["past"].reason)
        self.assertIn("未设考试日期", by_title["unset"].reason)

    def test_a9_future_signal_does_not_change_order_or_reason(self) -> None:
        baseline = item("baseline", 10, priority=0.5)
        future = item("future", 10, priority={"value": 1.0, "evidence_date": "2026-09-17"})
        result = plan(10, (future, baseline))
        self.assertEqual(titles(result.selected), ["baseline"])
        self.assertEqual(result.selected[0].rule_code, "todo_priority")
        self.assertEqual(reasons(result.excluded)["future"], "budget_insufficient")
        self.assertEqual({entry.title: entry.ranking_evidence["priority"] for entry in
                          (*result.selected, *result.excluded)}["future"], 0.0)
        source_future = item("source_future", 1, source_created_date="2026-09-17")
        self.assertEqual(reasons(plan(20, (source_future,)).excluded)["source_future"], "future_source")

    def test_tie_breaks_by_task_id_and_reordered_input_has_same_signature(self) -> None:
        first, second = item("first", 10), item("second", 10)
        one = plan(10, (first, second))
        two = plan(10, (second, first))
        self.assertEqual(titles(one.selected), titles(two.selected))
        self.assertEqual(one.input_signature, two.input_signature)
        self.assertEqual(one.selected[0].task_id, min(first.task_id, second.task_id))

    def test_duplicate_id_never_schedules_regardless_of_input_order(self) -> None:
        first = item("repeat", 10)
        second = replace(first, title="changed display", estimated_minutes=5)
        one = plan(20, (first, second))
        two = plan(20, (second, first))
        self.assertEqual(one.selected, ())
        self.assertEqual(two.selected, ())
        self.assertEqual({entry.rule_code for entry in one.excluded}, {"duplicate_task_id"})
        self.assertEqual(one.input_signature, two.input_signature)

    def test_signature_covers_budget_exam_estimate_completion_and_activity(self) -> None:
        first = item("source", 10)
        baseline = plan(20, (first,))
        variants = (
            plan(21, (first,)),
            plan(20, (first,), {"A": "2026-09-18"}),
            plan(20, (replace(first, estimated_minutes=11),)),
            plan(20, (replace(first, completion_state="checked"),)),
            plan(20, (first,), active=("B",)),
        )
        self.assertTrue(all(value.input_signature != baseline.input_signature for value in variants))

    def test_budget_decrease_preserves_completed_and_reports_overage(self) -> None:
        done = item("done20", 20, completion="checked")
        result = plan(10, (done, item("new5", 5)))
        self.assertEqual(result.selected, ())
        self.assertEqual(titles(result.completed), ["done20"])
        self.assertEqual((result.planned_minutes, result.remaining_minutes,
                          result.over_budget_completed_minutes), (20, 0, 10))
        self.assertEqual(reasons(result.excluded)["new5"], "budget_insufficient")

    def test_unknown_completed_estimate_blocks_new_pending(self) -> None:
        result = plan(30, (item("old_done", None, completion="checked"), item("new", 10)))
        self.assertTrue(result.completed_occupancy_unknown)
        self.assertEqual(result.selected, ())
        self.assertEqual(reasons(result.excluded)["new"], "completed_occupancy_unknown")
        self.assertEqual(result.completed[0].rule_code, "completed_occupancy_unknown")

    def test_ranking_after_exam_uses_priority_forgetting_gap_coverage(self) -> None:
        candidates = (item("coverage", 5, coverage_value=1),
                      item("gap", 5, mastery_gap=1),
                      item("forgetting", 5, forgetting_risk=1),
                      item("priority", 5, priority=1))
        result = plan(20, candidates)
        self.assertEqual(titles(result.selected),
                         ["priority", "forgetting", "gap", "coverage"])
        self.assertEqual([entry.rule_code for entry in result.selected],
                         ["todo_priority", "forgetting_risk", "mastery_gap", "coverage_value"])


if __name__ == "__main__":
    unittest.main()
