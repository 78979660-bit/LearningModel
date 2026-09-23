from __future__ import annotations

import os
import subprocess
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class AppRuntimeContractTests(unittest.TestCase):
    def test_module_is_lazy_and_main_window_is_identity_facade(self) -> None:
        code = (
            "import sys; from study_app.ui import app_runtime; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'study_app.ui.main_window' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        from study_app.ui import app_runtime, main_window

        self.assertIs(main_window.run_app, app_runtime.run_app)

    @staticmethod
    def fake_qt_modules():
        core = ModuleType("PySide6.QtCore")
        widgets = ModuleType("PySide6.QtWidgets")


        class FakeApplication:
            policy = None
            instance = None

            @classmethod
            def setHighDpiScaleFactorRoundingPolicy(cls, policy):
                cls.policy = policy

            def __init__(self, args):
                type(self).instance = self
                self.args = args
                self.quit_on_last = None
                self.stylesheet = None
                self.aboutToQuit = SimpleNamespace(connect=Mock())

            def setQuitOnLastWindowClosed(self, value):
                self.quit_on_last = value

            def setStyleSheet(self, value):
                self.stylesheet = value

            def installTranslator(self, translator):
                self.translator = translator

            def exec(self):
                return 23

        policy = object()
        class FakeTranslator:
            def __init__(self, parent):
                self.parent = parent

        core.QTranslator = FakeTranslator
        core.QDate = object
        core.Qt = SimpleNamespace(HighDpiScaleFactorRoundingPolicy=SimpleNamespace(PassThrough=policy))
        widgets.QApplication = FakeApplication
        return core, widgets, FakeApplication, policy

    def test_existing_instance_wake_returns_before_application_construction(self) -> None:
        from study_app.ui import app_runtime

        core, widgets, application, _policy = self.fake_qt_modules()
        instance_guard = SimpleNamespace(
            acquire=Mock(return_value=False),
            release=Mock(),
        )
        with (
            patch.dict(sys.modules, {"PySide6.QtCore": core, "PySide6.QtWidgets": widgets}),
            patch.object(app_runtime, "SingleInstanceGuard", return_value=instance_guard),
            patch.object(app_runtime, "send_wake_signal", return_value=True) as wake,
            patch.object(app_runtime, "MainWindow") as window,
        ):
            self.assertIsNone(app_runtime.run_app())
        instance_guard.acquire.assert_called_once_with()
        instance_guard.release.assert_not_called()
        wake.assert_called_once_with()
        window.assert_not_called()
        self.assertIsNone(application.instance)

    def test_full_startup_wires_application_window_worker_and_wake_server(self) -> None:
        from study_app.ui import app_runtime

        core, widgets, application, policy = self.fake_qt_modules()
        cleanup_module = ModuleType("study_app.integrations.chatgpt_desktop_bridge")
        cleanup_module.cleanup_generated_pdfs = Mock()
        window = SimpleNamespace(request_restore=Mock(), show=Mock())
        wake_server = SimpleNamespace(start=Mock(return_value=True), stop=Mock())
        wake_factory = Mock(return_value=wake_server)
        weekly_worker = SimpleNamespace(
            start=Mock(),
            request_stop=Mock(),
            join=Mock(),
            is_alive=Mock(return_value=False),
            run=Mock(),
        )
        weekly_factory = Mock(return_value=weekly_worker)
        instance_guard = SimpleNamespace(
            acquire=Mock(return_value=True),
            release=Mock(),
        )
        argv = ["study-app", "--test"]
        with (
            patch.dict(
                sys.modules,
                {
                    "PySide6.QtCore": core,
                    "PySide6.QtWidgets": widgets,
                    "study_app.integrations.chatgpt_desktop_bridge": cleanup_module,
                },
            ),
            patch.object(sys, "argv", argv),
            patch.object(app_runtime, "SingleInstanceGuard", return_value=instance_guard),
            patch.object(app_runtime, "send_wake_signal") as wake_signal,
            patch.object(app_runtime, "_initialize_user_data") as initialize_user_data,
            patch.object(
                app_runtime,
                "_record_optional_capability_diagnostics",
            ) as record_capabilities,
            patch.object(app_runtime, "build_stylesheet", return_value="QSS"),
            patch.object(app_runtime, "load_dashboard_state", return_value="STATE"),
            patch.object(app_runtime, "MainWindow", return_value=window) as window_factory,
            patch.object(app_runtime, "WakeServer", wake_factory),
            patch.object(
                app_runtime,
                "WeeklyCollectionWorker",
                weekly_factory,
                create=True,
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "23"):
                app_runtime.run_app()

        instance_guard.acquire.assert_called_once_with()
        instance_guard.release.assert_called_once_with()
        wake_signal.assert_not_called()
        initialize_user_data.assert_called_once_with()
        record_capabilities.assert_called_once_with()
        app = application.instance
        self.assertEqual(app.args, argv)
        self.assertIs(application.policy, policy)
        self.assertFalse(app.quit_on_last)
        self.assertEqual(app.stylesheet, "QSS")
        self.assertIs(app.translator.parent, app)
        self.assertEqual(app.translator.translate("QPlatformTheme", "Cancel"), "取消")
        cleanup_module.cleanup_generated_pdfs.assert_called_once_with()
        window_factory.assert_called_once_with("STATE")
        self.assertIs(window._weekly_collection_thread, weekly_worker)
        weekly_factory.assert_called_once_with()
        weekly_worker.start.assert_called_once_with()
        wake_factory.assert_called_once_with(window.request_restore)
        wake_server.start.assert_called_once_with()
        app.aboutToQuit.connect.assert_any_call(wake_server.stop)
        shutdown_callbacks = [
            call.args[0]
            for call in app.aboutToQuit.connect.call_args_list
            if call.args[0] is not wake_server.stop
        ]
        self.assertEqual(len(shutdown_callbacks), 1)
        shutdown_callbacks[0]()
        weekly_worker.request_stop.assert_called_once_with()
        weekly_worker.join.assert_called_once_with(timeout=2.0)
        window.show.assert_called_once_with()

        collector_module = ModuleType("study_app.data.weekly_practice_collector")
        collector_module.collection_is_due = Mock(return_value=True)
        collector_module.collect_weekly_sources = Mock(
            side_effect=OSError("weekly database locked")
        )
        with (
            patch.dict(
                sys.modules,
                {"study_app.data.weekly_practice_collector": collector_module},
            ),
            patch.object(app_runtime, "_weekly_collection_is_enabled", return_value=True),
            self.assertLogs("study_app.ui.app_runtime", level="ERROR") as captured,
        ):
            from study_app.ui.app_runtime import WeeklyCollectionWorker

            WeeklyCollectionWorker().run()
        diagnostic = "\n".join(captured.output)
        self.assertIn("OSError", diagnostic)
        self.assertNotIn("weekly database locked", diagnostic)


if __name__ == "__main__":
    unittest.main()
