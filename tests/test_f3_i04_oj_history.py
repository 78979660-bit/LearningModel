from __future__ import annotations

import unittest

from study_app.core.oj_attempts import OJAttempt
from study_app.core.oj_history import build_oj_history
from study_app.core.oj_identity import problem_key_for


def attempt(
    attempt_id: int,
    attempted_at: str,
    result: str,
    duration: int,
    *,
    independence: str = "guided",
    problem_key: str | None = None,
) -> OJAttempt:
    return OJAttempt(
        problem_key=problem_key or problem_key_for("leetcode", "1"),
        attempted_at=attempted_at,
        result=result,
        duration_seconds=duration,
        independence=independence,
        hint_level="none" if independence == "independent" else "concept",
        error_type="none" if result == "accepted" else "algorithm",
        notes="",
        source_attempt_key=None,
        attempt_id=attempt_id,
    )


class OJHistoryTests(unittest.TestCase):
    def test_empty_history_has_explicit_missing_summary(self) -> None:
        history = build_oj_history([])
        self.assertEqual(history.attempts, ())
        self.assertEqual(history.summary.attempt_count, 0)
        self.assertIsNone(history.summary.problem_key)
        self.assertIsNone(history.summary.first_result)
        self.assertIsNone(history.summary.recent_duration_improvement_seconds)

    def test_wrong_then_independent_accepted_matches_frozen_example(self) -> None:
        history = build_oj_history(
            [
                attempt(1, "2026-09-17T12:00:00Z", "wrong", 600),
                attempt(
                    2,
                    "2026-09-18T01:00:00Z",
                    "accepted",
                    420,
                    independence="independent",
                ),
            ]
        )
        self.assertEqual([item.attempt_number for item in history.attempts], [1, 2])
        self.assertEqual([item.is_retry for item in history.attempts], [False, True])
        self.assertEqual(history.summary.first_result, "wrong")
        self.assertEqual(history.summary.latest_result, "accepted")
        self.assertEqual(history.summary.best_result, "accepted")
        self.assertEqual(history.summary.attempts_to_first_accepted, 2)
        self.assertTrue(history.summary.ever_independent_accepted)
        self.assertEqual(history.summary.recent_duration_improvement_seconds, 180)

    def test_best_does_not_replace_latest_or_original_facts(self) -> None:
        source = [
            attempt(1, "2026-09-17T12:00:00Z", "accepted", 300),
            attempt(2, "2026-09-18T12:00:00Z", "wrong", 500),
        ]
        history = build_oj_history(source)
        self.assertEqual(history.summary.best_result, "accepted")
        self.assertEqual(history.summary.latest_result, "wrong")
        self.assertEqual(history.attempts[0].attempt, source[0])
        self.assertEqual(history.attempts[1].attempt, source[1])
        self.assertEqual(history.summary.recent_duration_improvement_seconds, -200)

    def test_order_is_time_then_immutable_id(self) -> None:
        later_id = attempt(9, "2026-09-17T12:00:00Z", "wrong", 20)
        earlier_id = attempt(3, "2026-09-17T12:00:00Z", "partial", 30)
        later_time = attempt(1, "2026-09-18T12:00:00Z", "accepted", 10)
        history = build_oj_history([later_time, later_id, earlier_id])
        self.assertEqual(
            [item.attempt.attempt_id for item in history.attempts], [3, 9, 1]
        )
        self.assertEqual(history.summary.attempts_to_first_accepted, 3)

    def test_mixed_problem_or_duplicate_id_is_rejected(self) -> None:
        first = attempt(1, "2026-09-17T12:00:00Z", "wrong", 10)
        other = attempt(
            2,
            "2026-09-18T12:00:00Z",
            "wrong",
            10,
            problem_key=problem_key_for("leetcode", "2"),
        )
        with self.assertRaises(ValueError):
            build_oj_history([first, other])
        with self.assertRaises(ValueError):
            build_oj_history([first, attempt(1, "2026-09-19T12:00:00Z", "wrong", 10)])


if __name__ == "__main__":
    unittest.main()
