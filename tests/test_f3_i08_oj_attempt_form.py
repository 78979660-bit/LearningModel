from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QPushButton

from study_app.data import database
from study_app.ui.oj_page import oj_page


def set_combo(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index < 0:
        raise AssertionError(value)
    combo.setCurrentIndex(index)


class OJAttemptFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "attempt-form.sqlite"
        database.initialize_database(self.db_path)
        database.install_oj_schema(self.db_path)
        self.problem = database.register_oj_problem(
            "leetcode", "1", "Two Sum", db_path=self.db_path
        )

    def page(self):
        return oj_page(db_path=self.db_path, enable_attempt_form=True)

    @staticmethod
    def fill(
        page,
        *,
        result="wrong",
        duration="600",
        independence="guided",
        hint="concept",
        error="algorithm",
        source_key="manual:1",
    ):
        page.findChild(QLineEdit, "OJAttemptedAtInput").setText(
            "2026-09-17T20:00:00+08:00"
        )
        page.findChild(QLineEdit, "OJDurationInput").setText(duration)
        set_combo(page.findChild(QComboBox, "OJResultInput"), result)
        set_combo(page.findChild(QComboBox, "OJIndependenceInput"), independence)
        set_combo(page.findChild(QComboBox, "OJHintLevelInput"), hint)
        set_combo(page.findChild(QComboBox, "OJErrorTypeInput"), error)
        page.findChild(QLineEdit, "OJAttemptNotesInput").setText("状态转移遗漏")
        page.findChild(QLineEdit, "OJSourceAttemptKeyInput").setText(source_key)

    def test_all_frozen_fields_append_and_refresh_history(self) -> None:
        page = self.page()
        self.fill(page)
        page.findChild(QPushButton, "OJSaveAttempt").click()
        self.app.processEvents()
        attempts = database.list_oj_attempts(self.problem.problem_key, self.db_path)
        self.assertEqual(len(attempts), 1)
        saved = attempts[0]
        self.assertEqual(saved.attempted_at, "2026-09-17T12:00:00Z")
        self.assertEqual(saved.result, "wrong")
        self.assertEqual(saved.duration_seconds, 600)
        self.assertEqual(saved.independence, "guided")
        self.assertEqual(saved.hint_level, "concept")
        self.assertEqual(saved.error_type, "algorithm")
        self.assertEqual(saved.notes, "状态转移遗漏")
        self.assertIn("#1 首次", page.findChild(QLabel, "OJAttemptHistory").text())
        self.assertIn("已追加提交", page.findChild(QLabel, "OJAttemptActionStatus").text())
        page.close()

    def test_invalid_duration_preserves_user_input_and_writes_nothing(self) -> None:
        page = self.page()
        self.fill(page, duration="6.5")
        page.findChild(QPushButton, "OJSaveAttempt").click()
        self.assertEqual(database.list_oj_attempts(self.problem.problem_key, self.db_path), ())
        self.assertEqual(page.findChild(QLineEdit, "OJDurationInput").text(), "6.5")
        self.assertEqual(
            page.findChild(QLineEdit, "OJAttemptedAtInput").text(),
            "2026-09-17T20:00:00+08:00",
        )
        self.assertIn("保存失败", page.findChild(QLabel, "OJAttemptActionStatus").text())
        page.close()

    def test_result_change_aligns_error_and_manual_conflict_is_rejected(self) -> None:
        page = self.page()
        result = page.findChild(QComboBox, "OJResultInput")
        error = page.findChild(QComboBox, "OJErrorTypeInput")
        set_combo(result, "accepted")
        self.assertEqual(error.currentData(), "none")
        self.fill(page, result="accepted", independence="independent", hint="none", error="algorithm")
        page.findChild(QPushButton, "OJSaveAttempt").click()
        self.assertEqual(database.list_oj_attempts(self.problem.problem_key, self.db_path), ())
        self.assertIn("error_type", page.findChild(QLabel, "OJAttemptActionStatus").text())
        page.close()

    def test_same_idempotency_key_replay_does_not_duplicate_history(self) -> None:
        page = self.page()
        self.fill(page, source_key="repeat:1")
        page.findChild(QPushButton, "OJSaveAttempt").click()
        first_status = page.findChild(QLabel, "OJAttemptActionStatus").text()
        self.fill(page, source_key="repeat:1")
        page.findChild(QPushButton, "OJSaveAttempt").click()
        self.app.processEvents()
        self.assertEqual(len(database.list_oj_attempts(self.problem.problem_key, self.db_path)), 1)
        self.assertEqual(page.findChild(QLabel, "OJAttemptActionStatus").text(), first_status)
        self.assertIn("尝试 1 次", page.findChild(QLabel, "OJRetrySummary").text())
        page.close()


if __name__ == "__main__":
    unittest.main()
