from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SettingsPageContractTests(unittest.TestCase):
    def wait_for_maintenance(self, page):
        from PySide6.QtTest import QTest
        import time
        deadline = time.monotonic() + 3
        while page._maintenance_job["handle"] is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(page._maintenance_job["handle"])

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def llm_settings():
        from study_app.ai.providers import LLMSettings

        api_key = "".join(("SECRET", "-SENTINEL", "-42"))
        return LLMSettings(
            enabled=True,
            provider="deepseek",
            model="deepseek-chat",
            custom_base_url="",
            api_key=api_key,
            single_call_token_limit=8000,
            daily_budget_cny=3.0,
            allow_upload_images=False,
            allow_upload_pdfs=False,
            enabled_features=("daily_summary", "natural_query"),
        )

    def test_module_is_lazy_and_main_window_is_identity_facade(self) -> None:
        code = (
            "import sys; from study_app.ui import settings_page; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'study_app.ui.main_window' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        from study_app.ui import main_window, settings_page as settings_module

        self.assertIs(main_window.settings_page, settings_module.settings_page)

    def test_slow_check_is_nonblocking_and_duplicate_clicks_are_ignored(self):
        import time
        from PySide6.QtWidgets import QPushButton, QLabel
        from study_app.ui import settings_page
        def slow_check():
            time.sleep(0.15)
            return 'SQLite: ok；文本编码检查通过'
        with (
            patch.object(settings_page, 'load_llm_settings', return_value=self.llm_settings()),
            patch.object(settings_page, 'list_llm_call_audits', return_value=[]),
            patch.object(settings_page, 'integrity_check', side_effect=slow_check) as check,
        ):
            page = settings_page.settings_page()
            button = next(b for b in page.findChildren(QPushButton) if b.text() == '检查数据库')
            button.click()
            self.assertIsNotNone(page._maintenance_job['handle'])
            self.assertFalse(button.isEnabled())
            button.click()
            self.assertTrue(any('正在检查数据' in label.text() for label in page.findChildren(QLabel)))
            self.wait_for_maintenance(page)
            check.assert_called_once_with()
            self.assertTrue(button.isEnabled())
            page.close()

    def test_main_window_retains_settings_dependency_compat_exports(self) -> None:
        from study_app.ui import main_window, settings_page as settings_module

        for name in (
            "BACKUP_ROOT",
            "create_backup",
            "integrity_check",
            "LLM_FEATURES",
            "PROVIDERS",
            "provider_status",
            "list_llm_call_audits",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(main_window, name), getattr(settings_module, name))

    def test_backup_check_save_and_local_mode_handlers(self) -> None:
        from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton
        from study_app.ui import settings_page

        archive = Path(os.environ.get("TEMP", ".")) / "settings-test.zip"
        folder = archive.parent / "settings-test"
        result = SimpleNamespace(archive=archive, folder=folder)
        with (
            patch.object(settings_page, "load_llm_settings", return_value=self.llm_settings()),
            patch.object(settings_page, "list_llm_call_audits", return_value=[]),
            patch.object(settings_page, "provider_status", side_effect=lambda value: f"状态:{value.provider}"),
            patch.object(settings_page, "create_backup", return_value=result) as create_backup,
            patch.object(settings_page, "integrity_check", return_value="ok") as integrity_check,
            patch.object(settings_page, "save_llm_settings") as save_settings,
            patch.object(QMessageBox, "information"),
        ):
            page = settings_page.settings_page()
            content = page.widget()
            buttons = {button.text(): button for button in content.findChildren(QPushButton)}
            buttons["立即备份"].click()
            self.wait_for_maintenance(page)
            buttons["检查数据库"].click()
            self.wait_for_maintenance(page)
            buttons["保存 LLM 设置"].click()
            buttons["切回仅本地"].click()

        create_backup.assert_called_once_with()
        integrity_check.assert_called_once_with()
        self.assertEqual(save_settings.call_count, 2)
        first, second = (call.args[0] for call in save_settings.call_args_list)
        self.assertEqual((first.enabled, first.provider, first.model), (True, "deepseek", "deepseek-chat"))
        self.assertEqual((second.enabled, second.provider, second.model), (False, "local", ""))
        texts = "\n".join(label.text() for label in content.findChildren(QLabel))
        self.assertIn("数据库检查结果：ok", texts)
        self.assertIn("状态:local", texts)

    def test_saved_cloud_status_keeps_key_and_explains_record_subfeatures(self) -> None:
        from PySide6.QtWidgets import QCheckBox, QLabel, QMessageBox, QPushButton
        from study_app.ai import providers
        from study_app.ui import settings_page

        with (
            patch.object(settings_page, "load_llm_settings", return_value=self.llm_settings()),
            patch.object(settings_page, "list_llm_call_audits", return_value=[]),
            patch.object(providers, "protect_secret", return_value="encrypted-test-key"),
            patch.object(providers, "set_setting") as persist,
            patch.object(QMessageBox, "information"),
        ):
            page = settings_page.settings_page()
            content = page.widget()
            checkboxes = {item.text(): item for item in content.findChildren(QCheckBox)}
            self.assertFalse(checkboxes["记录解析：LLM 错因归类"].isEnabled())
            checkboxes["新增记录智能解析"].setChecked(True)
            self.assertTrue(checkboxes["记录解析：LLM 错因归类"].isEnabled())
            checkboxes["记录解析：LLM 错因归类"].setChecked(True)
            button = next(
                item for item in content.findChildren(QPushButton)
                if item.text() == "保存 LLM 设置"
            )
            button.click()
            texts = "\n".join(label.text() for label in content.findChildren(QLabel))
            page.close()

        self.assertNotIn("尚未填写 API Key", texts)
        self.assertIn("已启用 DeepSeek", texts)
        self.assertIn("以下三项仅控制云端记录/附件解析", texts)
        self.assertEqual(persist.call_args.args[1]["api_key_protected"], "encrypted-test-key")
        self.assertEqual(set(persist.call_args.args[1]["enabled_features"]), {
            "record_parser", "error_classifier", "daily_summary", "natural_query"
        })

    def test_invalid_numeric_fields_and_backup_failures_are_observable(self) -> None:
        from PySide6.QtWidgets import QLineEdit, QMessageBox, QPushButton
        from study_app.ui import settings_page

        with (
            patch.object(settings_page, "load_llm_settings", return_value=self.llm_settings()),
            patch.object(settings_page, "list_llm_call_audits", return_value=[]),
            patch.object(settings_page, "create_backup", side_effect=RuntimeError("backup failed")),
            patch.object(settings_page, "integrity_check", side_effect=RuntimeError("check failed")),
            patch.object(settings_page, "save_llm_settings") as save_settings,
            patch.object(QMessageBox, "critical") as critical,
            patch.object(QMessageBox, "warning") as warning,
        ):
            page = settings_page.settings_page()
            content = page.widget()
            buttons = {button.text(): button for button in content.findChildren(QPushButton)}
            buttons["立即备份"].click()
            self.wait_for_maintenance(page)
            buttons["检查数据库"].click()
            self.wait_for_maintenance(page)
            token_input = next(edit for edit in content.findChildren(QLineEdit) if edit.text() == "8000")
            token_input.setText("not-a-number")
            buttons["保存 LLM 设置"].click()

        self.assertEqual(critical.call_count, 2)
        warning.assert_called_once()
        save_settings.assert_not_called()

    def test_llm_audit_success_and_failure_rows_preserve_safe_summary(self) -> None:
        from PySide6.QtWidgets import QLabel
        from study_app.ui import settings_page

        audits = [
            {
                "created_at": "2026-07-18 10:00",
                "feature": "daily_summary",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "status": "success",
                "estimated_tokens": 123,
                "upload_summary": {"records": {"type": "list"}},
                "error_message": "",
            },
            {
                "created_at": "2026-07-18 10:01",
                "feature": "natural_query",
                "provider": "custom",
                "model": "model-x",
                "status": "failed",
                "estimated_tokens": 45,
                "upload_summary": {},
                "error_message": "timeout",
            },
        ]
        with (
            patch.object(settings_page, "load_llm_settings", return_value=self.llm_settings()),
            patch.object(settings_page, "list_llm_call_audits", return_value=audits),
        ):
            page = settings_page.settings_page()
        labels = page.widget().findChildren(QLabel)
        texts = "\n".join(label.text() for label in labels)
        self.assertIn("daily_summary | deepseek / deepseek-chat | success | ~123 tokens", texts)
        self.assertIn("上传字段：records:list", texts)
        self.assertIn("natural_query | custom / model-x | failed | ~45 tokens", texts)
        self.assertIn("失败原因：timeout", texts)
        self.assertNotIn(self.llm_settings().api_key, texts)
        self.assertEqual(
            next(label for label in labels if label.text() == "失败原因：timeout").objectName(),
            "WarningText",
        )

    def test_open_backup_folder_is_isolated_from_the_real_directory(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from study_app.ui import settings_page

        with TemporaryDirectory(prefix="settings-page-") as temp_dir:
            backup_root = Path(temp_dir) / "backups"
            with (
                patch.object(settings_page, "BACKUP_ROOT", backup_root),
                patch.object(settings_page, "load_llm_settings", return_value=self.llm_settings()),
                patch.object(settings_page, "list_llm_call_audits", return_value=[]),
                patch("os.startfile", create=True) as startfile,
            ):
                page = settings_page.settings_page()
                content = page.widget()
                button = next(
                    item for item in content.findChildren(QPushButton) if item.text() == "打开备份目录"
                )
                button.click()
            self.assertTrue(backup_root.is_dir())
            startfile.assert_called_once_with(backup_root)


if __name__ == "__main__":
    unittest.main()
