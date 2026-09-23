from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch


class PracticeBankDiagnosticsTests(unittest.TestCase):
    def test_model_difficulty_lookup_uses_absolute_path(self) -> None:
        from study_app.core.practice_bank import model_topic_difficulty_standard

        model = {
            "subjects": [
                {
                    "name": "高等数学",
                    "modules": [
                        {
                            "topics": [
                                {
                                    "name": "常数项级数与判敛法",
                                    "difficulty_standard": {"score": 80},
                                }
                            ]
                        }
                    ],
                }
            ]
        }

        def guarded_read(path: Path, *args, **kwargs) -> str:
            self.assertTrue(path.is_absolute())
            return json.dumps(model, ensure_ascii=False)

        with patch.object(Path, "read_text", autospec=True, side_effect=guarded_read):
            difficulty = model_topic_difficulty_standard(
                "高等数学", "常数项级数与判敛法"
            )

        self.assertEqual(difficulty, 80)

    def test_model_failure_cannot_make_scope_validation_pass(self) -> None:
        from study_app.core import study_phase

        with (
            patch.object(study_phase, "is_final_review", return_value=True),
            patch.object(
                study_phase,
                "get_subject_phase",
                return_value={"exam_scope": {"chapters": [1]}},
            ),
            patch.object(
                study_phase,
                "load_learning_model",
                side_effect=OSError("model unavailable"),
            ),
        ):
            with self.assertRaises(study_phase.ScopeValidationError):
                study_phase.out_of_exam_scope_references("高等数学", "复习第一章")

    def test_scope_validation_allows_model_without_subject(self) -> None:
        from study_app.core import study_phase

        with (
            patch.object(study_phase, "is_final_review", return_value=True),
            patch.object(
                study_phase,
                "get_subject_phase",
                return_value={"exam_scope": {"chapters": [1]}},
            ),
            patch.object(study_phase, "load_learning_model", return_value={"subjects": []}),
        ):
            violations = study_phase.out_of_exam_scope_references(
                "高等数学", "复习第一章"
            )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()