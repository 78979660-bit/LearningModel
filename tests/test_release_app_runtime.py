from __future__ import annotations

import os
import sys
import unittest
import builtins
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _NativeFunction:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[object, ...]] = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class ReleaseSingleInstanceTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("win"), "Windows mutex test")
    def test_real_windows_mutex_excludes_then_allows_reacquire(self) -> None:
        from study_app.single_instance import SingleInstanceGuard

        name = rf"Local\LearningModel.ReleaseTest.{os.getpid()}"
        first = SingleInstanceGuard(name)
        second = SingleInstanceGuard(name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
        finally:
            first.release()
            second.release()

    def test_mutex_uses_fixed_learning_model_identity_and_releases_handle(self) -> None:
        from study_app import single_instance

        self.assertEqual(
            single_instance.MUTEX_NAME,
            r"Local\LearningModel.SingleInstance",
        )
        kernel32 = SimpleNamespace(
            CreateMutexW=_NativeFunction(101),
            CloseHandle=_NativeFunction(True),
        )
        guard = single_instance.SingleInstanceGuard()
        with (
            patch.object(single_instance.sys, "platform", "win32"),
            patch.object(single_instance.ctypes, "WinDLL", return_value=kernel32),
            patch.object(single_instance.ctypes, "set_last_error"),
            patch.object(single_instance.ctypes, "get_last_error", return_value=0),
        ):
            self.assertTrue(guard.acquire())
            self.assertEqual(guard.handle, 101)
            guard.release()

        self.assertIsNone(guard.handle)
        self.assertEqual(kernel32.CreateMutexW.calls, [(None, False, single_instance.MUTEX_NAME)])
        self.assertEqual(kernel32.CloseHandle.calls, [(101,)])

    def test_existing_mutex_closes_duplicate_handle_and_reports_secondary(self) -> None:
        from study_app import single_instance

        kernel32 = SimpleNamespace(
            CreateMutexW=_NativeFunction(202),
            CloseHandle=_NativeFunction(True),
        )
        guard = single_instance.SingleInstanceGuard()
        with (
            patch.object(single_instance.sys, "platform", "win32"),
            patch.object(single_instance.ctypes, "WinDLL", return_value=kernel32),
            patch.object(single_instance.ctypes, "set_last_error"),
            patch.object(
                single_instance.ctypes,
                "get_last_error",
                return_value=single_instance.ERROR_ALREADY_EXISTS,
            ),
        ):
            self.assertFalse(guard.acquire())

        self.assertIsNone(guard.handle)
        self.assertEqual(kernel32.CloseHandle.calls, [(202,)])


class ReleaseAppRuntimeTests(unittest.TestCase):
    @staticmethod
    def _fake_qt_modules():
        package = ModuleType("PySide6")
        package.__path__ = []
        core = ModuleType("PySide6.QtCore")
        widgets = ModuleType("PySide6.QtWidgets")

        class FakeApplication:
            metadata: dict[str, str] = {}
            instance = None

            @classmethod
            def setApplicationName(cls, value):
                cls.metadata["application_name"] = value

            @classmethod
            def setApplicationDisplayName(cls, value):
                cls.metadata["display_name"] = value

            @classmethod
            def setApplicationVersion(cls, value):
                cls.metadata["version"] = value

            @classmethod
            def setOrganizationName(cls, value):
                cls.metadata["organization_name"] = value

            @classmethod
            def setOrganizationDomain(cls, value):
                cls.metadata["organization_domain"] = value

            @classmethod
            def setHighDpiScaleFactorRoundingPolicy(cls, value):
                cls.rounding_policy = value

            def __init__(self, arguments):
                type(self).instance = self
                self.arguments = arguments
                self.aboutToQuit = SimpleNamespace(connect=Mock())

            def setQuitOnLastWindowClosed(self, value):
                self.quit_on_last_window = value

            def setStyleSheet(self, value):
                self.stylesheet = value

            def exec(self):
                return 7

        policy = object()
        core.QDate = object
        core.Qt = SimpleNamespace(
            HighDpiScaleFactorRoundingPolicy=SimpleNamespace(PassThrough=policy)
        )
        widgets.QApplication = FakeApplication
        return package, core, widgets, FakeApplication

    def test_missing_qt_raises_loggable_startup_error(self) -> None:
        from study_app.ui import app_runtime

        real_import = builtins.__import__

        def import_without_qt(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith("PySide6"):
                raise ImportError("simulated missing Qt runtime")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=import_without_qt):
            with self.assertRaisesRegex(RuntimeError, "PySide6 尚未安装"):
                app_runtime.run_app()

    def test_primary_instance_continues_when_wake_port_cannot_bind(self) -> None:
        from study_app.ui import app_runtime

        package, core, widgets, application_type = self._fake_qt_modules()
        design = ModuleType("study_app.ui.design_components")
        design.install_chinese_dialogs = Mock()
        performance = ModuleType("study_app.ui.performance_log")
        performance.install_performance_log = Mock()
        cleanup = ModuleType("study_app.integrations.chatgpt_desktop_bridge")
        cleanup.cleanup_generated_pdfs = Mock()
        guard = SimpleNamespace(acquire=Mock(return_value=True), release=Mock())
        wake_server = SimpleNamespace(start=Mock(return_value=False), stop=Mock())
        window = SimpleNamespace(request_restore=Mock(), show=Mock())
        worker = SimpleNamespace(start=Mock(), request_stop=Mock(), join=Mock(), is_alive=Mock())

        with (
            patch.dict(
                sys.modules,
                {
                    "PySide6": package,
                    "PySide6.QtCore": core,
                    "PySide6.QtWidgets": widgets,
                    "study_app.ui.design_components": design,
                    "study_app.ui.performance_log": performance,
                    "study_app.integrations.chatgpt_desktop_bridge": cleanup,
                },
            ),
            patch.object(app_runtime, "SingleInstanceGuard", return_value=guard),
            patch.object(app_runtime, "send_wake_signal") as wake_signal,
            patch.object(app_runtime, "_initialize_user_data") as initialize,
            patch.object(app_runtime, "_record_optional_capability_diagnostics") as diagnostics,
            patch.object(app_runtime, "build_stylesheet", return_value="QSS"),
            patch.object(app_runtime, "load_dashboard_state", return_value="STATE"),
            patch.object(app_runtime, "create_main_window", return_value=window),
            patch.object(app_runtime, "WeeklyCollectionWorker", return_value=worker),
            patch.object(app_runtime, "WakeServer", return_value=wake_server),
            self.assertLogs("study_app.ui.app_runtime", level="WARNING") as captured,
        ):
            with self.assertRaisesRegex(SystemExit, "7"):
                app_runtime.run_app()

        guard.acquire.assert_called_once_with()
        guard.release.assert_called_once_with()
        wake_signal.assert_not_called()
        initialize.assert_called_once_with()
        diagnostics.assert_called_once_with()
        wake_server.start.assert_called_once_with()
        window.show.assert_called_once_with()
        self.assertIn("primary instance will continue", "\n".join(captured.output))
        self.assertEqual(
            application_type.metadata,
            {
                "application_name": "LearningModel",
                "display_name": "学习模型",
                "version": "0.1.0",
                "organization_name": "LearningModelProject",
                "organization_domain": "learningmodel.local",
            },
        )

    def test_secondary_instance_uses_mutex_before_sending_wake(self) -> None:
        from study_app.ui import app_runtime

        package, core, widgets, application_type = self._fake_qt_modules()
        guard = SimpleNamespace(acquire=Mock(return_value=False), release=Mock())
        with (
            patch.dict(
                sys.modules,
                {
                    "PySide6": package,
                    "PySide6.QtCore": core,
                    "PySide6.QtWidgets": widgets,
                },
            ),
            patch.object(app_runtime, "SingleInstanceGuard", return_value=guard),
            patch.object(app_runtime, "send_wake_signal", return_value=True) as wake,
            patch.object(app_runtime, "_initialize_user_data") as initialize,
        ):
            self.assertIsNone(app_runtime.run_app())

        guard.acquire.assert_called_once_with()
        guard.release.assert_not_called()
        wake.assert_called_once_with()
        initialize.assert_not_called()
        self.assertIsNone(application_type.instance)

    def test_startup_uses_unified_layout_and_database_seed(self) -> None:
        from study_app.ai import providers
        from study_app.data import database, practice_repository
        from study_app import paths
        from study_app.ui import app_runtime

        database_path = Path("release-test.sqlite")
        with (
            patch.object(paths, "ensure_user_layout") as layout,
            patch.object(database, "ensure_seeded_database", return_value=database_path) as seed,
            patch.object(practice_repository, "ensure_practice_bank_seeded") as practice_seed,
            patch.object(providers, "load_llm_settings") as load_settings,
        ):
            app_runtime._initialize_user_data()

        layout.assert_called_once_with()
        seed.assert_called_once_with()
        practice_seed.assert_called_once_with(database_path)
        load_settings.assert_called_once_with()

    def test_weekly_collection_requires_explicit_boolean_setting(self) -> None:
        from study_app.data import database
        from study_app.ui import app_runtime

        collector = ModuleType("study_app.data.weekly_practice_collector")
        collector.collection_is_due = Mock(return_value=True)
        collector.collect_weekly_sources = Mock()
        with (
            patch.dict(
                sys.modules,
                {"study_app.data.weekly_practice_collector": collector},
            ),
            patch.object(database, "get_setting", return_value=False) as get_setting,
        ):
            app_runtime.WeeklyCollectionWorker().run()

        get_setting.assert_called_once_with(
            app_runtime.WEEKLY_COLLECTION_ENABLED_SETTING,
            False,
        )
        collector.collection_is_due.assert_not_called()
        collector.collect_weekly_sources.assert_not_called()

        with (
            patch.dict(
                sys.modules,
                {"study_app.data.weekly_practice_collector": collector},
            ),
            patch.object(database, "get_setting", return_value=True),
        ):
            app_runtime.WeeklyCollectionWorker().run()

        collector.collection_is_due.assert_called_once_with()
        collector.collect_weekly_sources.assert_called_once_with()

    def test_capability_diagnostics_are_local_detector_results(self) -> None:
        from study_app import capabilities
        from study_app.ui import app_runtime

        result = {
            "network_services": {
                "available": None,
                "detail": "启动时不联网探测",
            }
        }
        with (
            patch.object(capabilities, "detect_optional_capabilities", return_value=result) as detect,
            self.assertLogs("study_app.ui.app_runtime", level="INFO") as captured,
        ):
            app_runtime._record_optional_capability_diagnostics()

        detect.assert_called_once_with()
        self.assertIn("启动时不联网探测", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
