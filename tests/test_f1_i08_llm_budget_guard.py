from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from study_app.ai.study_plan_generator import build_budget_suggestion_prompt
from study_app.ai.study_plan_service import guard_llm_budget_suggestion
from study_app.core.budgeted_day_plan import BudgetPlanInput, build_budgeted_day_plan
from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source


def candidate(name: str, minutes: int, subject: str) -> PlanCandidate:
    kind, source_id = "model_topic_practice", f"topic:{name}"
    return PlanCandidate(task_id_for_source(subject, kind, source_id), subject, kind,
                         source_id, name, {"priority": 0.5}, minutes, "user")


def local_plan():
    candidates = (candidate("A40", 40, "A"), candidate("B15", 15, "B"))
    return build_budgeted_day_plan(
        BudgetPlanInput("2026-09-16", 60, ("A", "B"), as_of_date="2026-09-16"),
        CandidateCollection(candidates, ()),
    )


def mirror(plan) -> dict:
    return {
        "tasks": [
            {"task_id": item.task_id, "subject_id": item.subject_id,
             "estimated_minutes": item.estimated_minutes,
             "estimate_source": item.estimate_source}
            for item in plan.selected
        ],
        "explanation": "先做本地排序靠前的任务。",
    }


class LlmBudgetGuardTests(unittest.TestCase):
    def test_exact_mirror_accepts_only_explanation_and_keeps_plan_identity(self) -> None:
        plan = local_plan()
        with patch("study_app.ai.study_plan_generator.audited_chat_completion_json") as remote:
            result = guard_llm_budget_suggestion(plan, mirror(plan), ("A", "B"))
        self.assertTrue(result.accepted)
        self.assertIs(result.plan, plan)
        self.assertEqual(result.diagnostic_codes, ())
        self.assertEqual(result.explanation, "先做本地排序靠前的任务。")
        remote.assert_not_called()

    def test_added_or_missing_task_is_rejected_and_local_plan_is_preserved(self) -> None:
        plan = local_plan()
        added = mirror(plan)
        added["tasks"].append({
            "task_id": "task:v1:untrusted", "subject_id": "A",
            "estimated_minutes": 5, "estimate_source": "user",
        })
        result = guard_llm_budget_suggestion(plan, added, ("A", "B"))
        self.assertFalse(result.accepted)
        self.assertIs(result.plan, plan)
        self.assertIn("unknown_task", result.diagnostic_codes)
        self.assertIn("added_task", result.diagnostic_codes)
        self.assertIn("已保留本地计划", result.message)

        missing = mirror(plan)
        missing["tasks"].pop()
        result = guard_llm_budget_suggestion(plan, missing, ("A", "B"))
        self.assertFalse(result.accepted)
        self.assertIn("missing_local_task", result.diagnostic_codes)

    def test_estimate_tamper_and_over_budget_are_both_reported(self) -> None:
        plan = local_plan()
        suggestion = mirror(plan)
        suggestion["tasks"][0]["estimated_minutes"] = 50
        result = guard_llm_budget_suggestion(plan, suggestion, ("A", "B"))
        self.assertFalse(result.accepted)
        self.assertIn("estimate_changed", result.diagnostic_codes)
        self.assertIn("budget_exceeded", result.diagnostic_codes)
        self.assertEqual(result.plan.planned_minutes, 55)
        self.assertEqual(result.plan.remaining_minutes, 5)

    def test_estimate_source_tamper_is_rejected(self) -> None:
        plan = local_plan()
        suggestion = mirror(plan)
        suggestion["tasks"][0]["estimate_source"] = "confirmed_template"
        result = guard_llm_budget_suggestion(plan, suggestion, ("A", "B"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.diagnostic_codes, ("estimate_source_changed",))
        self.assertIs(result.plan, plan)

    def test_subject_change_or_archived_subject_is_rejected(self) -> None:
        plan = local_plan()
        suggestion = mirror(plan)
        suggestion["tasks"][0]["subject_id"] = "ARCHIVED"
        result = guard_llm_budget_suggestion(plan, suggestion, ("A", "B"))
        self.assertFalse(result.accepted)
        self.assertIn("inactive_subject", result.diagnostic_codes)
        self.assertIn("subject_changed", result.diagnostic_codes)

    def test_duplicate_and_invalid_shape_are_rejected(self) -> None:
        plan = local_plan()
        suggestion = mirror(plan)
        suggestion["tasks"] = [suggestion["tasks"][0], deepcopy(suggestion["tasks"][0])]
        result = guard_llm_budget_suggestion(plan, suggestion, ("A", "B"))
        self.assertFalse(result.accepted)
        self.assertIn("duplicate_task_id", result.diagnostic_codes)
        self.assertIn("missing_local_task", result.diagnostic_codes)
        malformed = guard_llm_budget_suggestion(plan, {"tasks": "all"}, ("A", "B"))
        self.assertEqual(malformed.diagnostic_codes, ("invalid_task_list",))

    def test_prompt_freezes_every_local_task_field(self) -> None:
        plan = local_plan()
        prompt = build_budget_suggestion_prompt(plan)
        self.assertIn("不得新增、删除、改学科、改预计分钟或改来源", prompt)
        for item in plan.selected:
            self.assertIn(item.task_id, prompt)
            self.assertIn(item.subject_id, prompt)
            self.assertIn(str(item.estimated_minutes), prompt)
            self.assertIn(item.estimate_source, prompt)


if __name__ == "__main__":
    unittest.main()
