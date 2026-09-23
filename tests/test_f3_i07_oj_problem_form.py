from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QListWidget, QPushButton

from study_app.data import database
from study_app.ui.oj_page import oj_page


class OJProblemFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "oj-form.sqlite"
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
            connection.execute(
                "INSERT INTO topics(module_id, name, source_json) VALUES (?, ?, '{}')",
                (module, "动态规划"),
            )
        self.topic = database.register_topic_identities(self.db_path)[0]
        database.install_oj_schema(self.db_path)

    def page(self):
        return oj_page(db_path=self.db_path, enable_problem_form=True)

    @staticmethod
    def fill(page, *, source="leetcode", external="1", title="Two Sum", topic=""):
        page.findChild(QLineEdit, "OJSourceInput").setText(source)
        page.findChild(QLineEdit, "OJExternalKeyInput").setText(external)
        page.findChild(QLineEdit, "OJTitleInput").setText(title)
        page.findChild(QLineEdit, "OJURLInput").setText("https://example.test/problem")
        page.findChild(QLineEdit, "OJTopicKeysInput").setText(topic)

    def test_registers_problem_and_explicit_mapping_then_refreshes(self) -> None:
        page = self.page()
        self.fill(page, topic=self.topic.topic_key)
        page.findChild(QPushButton, "OJSaveProblem").click()
        self.app.processEvents()
        problems = database.list_oj_problems(self.db_path)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].source_key, "leetcode")
        mappings = database.list_oj_problem_topics(problems[0].problem_key, self.db_path)
        self.assertEqual([item.topic_key for item in mappings], [self.topic.topic_key])
        self.assertEqual(mappings[0].mapping_source, "manual")
        self.assertEqual(page.findChild(QListWidget, "OJProblemList").count(), 1)
        self.assertIn("已保存", page.findChild(QLabel, "OJProblemActionStatus").text())
        self.assertEqual(page.findChild(QLineEdit, "OJTitleInput").text(), "")
        page.close()

    def test_unknown_topic_fails_before_problem_write_and_preserves_input(self) -> None:
        page = self.page()
        unknown = self.topic.topic_key[:-1] + ("0" if self.topic.topic_key[-1] != "0" else "1")
        self.fill(page, title="Keep Me", topic=unknown)
        page.findChild(QPushButton, "OJSaveProblem").click()
        self.app.processEvents()
        self.assertEqual(database.list_oj_problems(self.db_path), ())
        self.assertEqual(page.findChild(QLineEdit, "OJTitleInput").text(), "Keep Me")
        self.assertEqual(page.findChild(QLineEdit, "OJTopicKeysInput").text(), unknown)
        self.assertIn("不存在的 topic_key", page.findChild(QLabel, "OJProblemActionStatus").text())
        page.close()

    def test_same_title_different_source_identity_remains_separate(self) -> None:
        page = self.page()
        self.fill(page, source="leetcode", external="1", title="Same")
        page.findChild(QPushButton, "OJSaveProblem").click()
        self.fill(page, source="local", external="1", title="Same")
        page.findChild(QPushButton, "OJSaveProblem").click()
        self.app.processEvents()
        problems = database.list_oj_problems(self.db_path)
        self.assertEqual(len(problems), 2)
        self.assertNotEqual(problems[0].problem_key, problems[1].problem_key)
        self.assertEqual(page.findChild(QListWidget, "OJProblemList").count(), 2)
        page.close()


if __name__ == "__main__":
    unittest.main()
