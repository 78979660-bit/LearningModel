from __future__ import annotations

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton

from study_app.core.oj_attempts import OJAttempt
from study_app.core.oj_history import build_oj_history
from study_app.core.oj_identity import OJProblem, problem_key_for
from study_app.core.oj_topics import OJTopicMapping
from study_app.core.topic_identity import topic_key_for_id
from study_app.data.database import DatabaseNotInitializedError
from study_app.ui.oj_page import OJPageEntry, oj_page


def page_entry(external: str, title: str, attempt_count: int = 2) -> OJPageEntry:
    problem_key = problem_key_for("leetcode", external)
    problem = OJProblem(problem_key, "leetcode", external, title, "https://example.test/p")
    mapping = OJTopicMapping(problem_key, topic_key_for_id(1), "manual", "用户确认")
    values = [
        OJAttempt(
            problem_key,
            "2026-09-17T12:00:00Z",
            "wrong",
            600,
            "guided",
            "concept",
            "algorithm",
            "状态转移遗漏",
            None,
            1,
        ),
        OJAttempt(
            problem_key,
            "2026-09-18T01:00:00Z",
            "accepted",
            420,
            "independent",
            "none",
            "none",
            "独立通过",
            None,
            2,
        ),
    ][:attempt_count]
    return OJPageEntry(problem, (mapping,), build_oj_history(values))


class OJPageReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_list_detail_history_and_retry_summary_show_frozen_fields(self) -> None:
        page = oj_page(data_loader=lambda: (page_entry("1", "Two Sum"),))
        problem_list = page.findChild(QListWidget, "OJProblemList")
        self.assertEqual(problem_list.count(), 1)
        self.assertIn("leetcode / 1", problem_list.item(0).text())
        self.assertIn("Two Sum", page.findChild(QLabel, "OJTitle").text())
        self.assertIn("ojp:v1:", page.findChild(QLabel, "OJIdentity").text())
        self.assertIn("manual", page.findChild(QLabel, "OJTopics").text())
        summary = page.findChild(QLabel, "OJRetrySummary").text()
        self.assertIn("尝试 2 次", summary)
        self.assertIn("首次 答案错误", summary)
        self.assertIn("最新 通过", summary)
        self.assertIn("首次通过序号 2", summary)
        history = page.findChild(QLabel, "OJAttemptHistory").text()
        for text in (
            "#1 首次",
            "#2 复做",
            "600 秒",
            "420 秒",
            "guided",
            "independent",
            "concept",
            "algorithm",
        ):
            self.assertIn(text, history)
        self.assertEqual(page.findChildren(QPushButton), [])
        page.close()

    def test_empty_state_is_normal(self) -> None:
        page = oj_page(data_loader=lambda: ())
        self.assertEqual(page.findChild(QListWidget, "OJProblemList").count(), 0)
        self.assertIn("共 0 道", page.findChild(QLabel, "OJProblemCount").text())
        self.assertEqual(page.findChild(QLabel, "OJAttemptHistory").text(), "—")
        page.close()

    def test_missing_schema_shows_readonly_diagnostic(self) -> None:
        def missing():
            raise DatabaseNotInitializedError("oj_problems missing")

        page = oj_page(data_loader=missing)
        diagnostic = page.findChild(QLabel, "OJDiagnostic")
        self.assertTrue(diagnostic.isVisibleTo(page))
        self.assertIn("F3 结构尚未安装", diagnostic.text())
        self.assertIn("不会自动修改", diagnostic.text())
        self.assertEqual(page.findChild(QListWidget, "OJProblemList").count(), 0)
        self.assertEqual(page.findChildren(QPushButton), [])
        page.close()

    def test_refresh_preserves_selected_problem(self) -> None:
        entries = (page_entry("1", "One"), page_entry("2", "Two", 1))
        loader = Mock(return_value=entries)
        page = oj_page(data_loader=loader)
        problem_list = page.findChild(QListWidget, "OJProblemList")
        problem_list.setCurrentRow(1)
        selected = problem_list.currentItem().data(256)
        page._refresh_oj()
        self.app.processEvents()
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(problem_list.currentItem().data(256), selected)
        self.assertIn("Two", page.findChild(QLabel, "OJTitle").text())
        page.close()


if __name__ == "__main__":
    unittest.main()
