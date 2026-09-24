from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class RecordEditorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def wait_for_ui(cls, predicate, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cls.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("record recognition result did not arrive")

    def test_module_is_lazy_and_main_window_is_identity_facade(self) -> None:
        code = (
            "import sys; from study_app.ui import record_editor; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'study_app.ui.main_window' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        from study_app.ui import main_window, record_editor

        for name in ("ACTIVITY_OPTIONS", "SOURCE_OPTIONS", "AddRecordDialog", "add_record_page"):
            self.assertIs(getattr(main_window, name), getattr(record_editor, name))

    def test_record_editor_factory_reuses_qt_type_without_sharing_form_inputs(self) -> None:
        from PySide6.QtWidgets import QWidget

        from study_app.ui import record_editor

        first_saved = Mock()
        second_saved = Mock()
        with patch("study_app.data.database.list_module_names", return_value=[]):
            first = record_editor.AddRecordDialog(None, first_saved, ("数学",))
            second = record_editor.create_record_editor(None, second_saved, ("物理",))
        self.assertIs(record_editor.AddRecordDialog, record_editor.create_record_editor)
        self.assertIs(type(first), type(second))
        self.assertIs(type(first), record_editor._RecordEditorType.build())
        self.assertIsInstance(first, QWidget)
        self.assertIs(first.on_saved, first_saved)
        self.assertIs(second.on_saved, second_saved)
        self.assertEqual(first.subject_input.currentText(), "数学")
        self.assertEqual(second.subject_input.currentText(), "物理")
        first.deleteLater()
        second.deleteLater()

    def test_editor_payload_and_save_callback_preserve_contract(self) -> None:
        from study_app.ui import record_editor

        saved = Mock()
        with patch("study_app.data.database.list_module_names", return_value=["数据结构"]):
            editor = record_editor.AddRecordDialog(None, saved, ("计算机科学",))
        self.assertEqual(editor.subject_input.currentText(), "计算机科学")
        self.assertEqual(editor.module_input.currentText(), "数据结构")

        editor.topic_input.setText("图搜索")
        editor.score_input.setText("88")
        editor.duration_input.setText("45")
        editor.note_input.setPlainText("复习 BFS")
        editor.problems = [{"title": "题1", "status": "correct"}]
        payload = editor.record_payload()
        self.assertEqual(
            {key: payload[key] for key in ("subject", "module", "topic", "score", "duration_minutes", "note")},
            {
                "subject": "计算机科学",
                "module": "数据结构",
                "topic": "图搜索",
                "score": 88.0,
                "duration_minutes": 45.0,
                "note": "复习 BFS",
            },
        )
        self.assertEqual(payload["problems"], [{"title": "题1", "status": "correct"}])
        self.assertEqual(payload["attachments"], [])
        editor.save_record()
        saved.assert_called_once_with(payload, editor)

    def test_validation_failures_are_observable_and_do_not_save(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from study_app.ui import record_editor

        saved = Mock()
        with (
            patch("study_app.data.database.list_module_names", return_value=[]),
            patch.object(QMessageBox, "warning") as warning,
        ):
            editor = record_editor.AddRecordDialog(None, saved, ())
            editor.save_record()
            warning.assert_called_with(editor, "缺少学科", "请填写或选择学科。")
            editor.subject_input.addItem("计算机科学")
            editor.topic_input.setText("图")
            editor.score_input.setText("101")
            editor.save_record()
            self.assertEqual(warning.call_args.args[1:], ("分数范围错误", "分数应在 0 到 100 之间。"))
        saved.assert_not_called()

    def test_add_record_page_uses_canonical_subject_policy_and_editor(self) -> None:
        from PySide6.QtWidgets import QWidget
        from study_app.core.dashboard import DashboardState
        from datetime import date
        from study_app.ui import record_editor

        state = DashboardState(date(2026, 7, 15), date(2026, 7, 17), 60, (), (), (), ())
        callback = Mock()
        fake_editor = QWidget()
        with (
            patch.object(record_editor, "record_subject_names", return_value=("计算机科学",)) as subjects,
            patch.object(record_editor, "create_record_editor", return_value=fake_editor) as dialog,
        ):
            page = record_editor.add_record_page(callback, state)
        subjects.assert_called_once_with(state)
        dialog.assert_called_once()
        self.assertIs(dialog.call_args.args[1], callback)
        self.assertEqual(dialog.call_args.args[2], ("计算机科学",))
        self.assertIsNotNone(page.widget())


    def test_llm_recognize_failure_preserves_existing_problems_and_does_not_save(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from study_app.ai.validation import LLMValidationError
        from study_app.ui import record_editor

        saved = Mock()
        with (
            patch("study_app.data.database.list_module_names", return_value=["数据结构"]),
            patch.object(QMessageBox, "warning"),
            patch.object(QMessageBox, "critical") as critical,
            patch(
                "study_app.ai.record_parser.parse_record_payload_with_llm",
                side_effect=LLMValidationError("第 1 个题目 correctness 必须是有限数字。"),
            ),
        ):
            editor = record_editor.AddRecordDialog(None, saved, ("计算机科学",))
            editor.problems = [{"title": "既有有效题目", "status": "correct"}]
            recognized = editor.recognize_problems_with_llm(show_unavailable=False)
            self.wait_for_ui(lambda: editor._recognition_handle is None)

        self.assertTrue(recognized)
        self.assertEqual(editor.problems, [{"title": "既有有效题目", "status": "correct"}])
        critical.assert_called_once()
        saved.assert_not_called()
        editor.close()


if __name__ == "__main__":
    unittest.main()
