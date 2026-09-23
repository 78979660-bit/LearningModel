from __future__ import annotations

import logging
import threading

from study_app.app_metadata import (
    APP_DISPLAY_NAME,
    APP_INTERNAL_NAME,
    APP_VERSION,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
)
from study_app.core.dashboard import load_dashboard_state
from study_app.single_instance import (
    WAKE_HOST,
    WAKE_PORT,
    SingleInstanceGuard,
    WakeServer,
    send_wake_signal,
)
from study_app.ui.theme import build_stylesheet
from study_app.ui.window_shell import MainWindow


LOGGER = logging.getLogger(__name__)
WEEKLY_COLLECTION_ENABLED_SETTING = "weekly_practice_collection_enabled"


def _initialize_user_data() -> None:
    from study_app.ai.providers import load_llm_settings
    from study_app.data.database import ensure_seeded_database
    from study_app.data.practice_repository import ensure_practice_bank_seeded
    from study_app.paths import ensure_user_layout

    ensure_user_layout()
    database_path = ensure_seeded_database()
    ensure_practice_bank_seeded(database_path)
    # Loading is local-only and transparently moves any legacy plaintext API
    # credential into the current Windows user's DPAPI-protected setting.
    load_llm_settings()


def _record_optional_capability_diagnostics() -> None:
    try:
        from study_app.capabilities import detect_optional_capabilities

        capabilities = detect_optional_capabilities()
    except Exception as error:
        LOGGER.warning(
            "Optional capability detection failed (%s)",
            type(error).__name__,
        )
        return
    for name, status in sorted(capabilities.items()):
        if not isinstance(status, dict):
            LOGGER.info("Optional capability %s: %r", name, status)
            continue
        LOGGER.info(
            "Optional capability %s: available=%r; detail=%s",
            name,
            status.get("available"),
            status.get("detail", ""),
        )


def _weekly_collection_is_enabled() -> bool:
    try:
        from study_app.data.database import get_setting

        return get_setting(WEEKLY_COLLECTION_ENABLED_SETTING, False) is True
    except Exception as error:
        LOGGER.warning(
            "Weekly collection setting could not be read (%s); collection remains disabled",
            type(error).__name__,
        )
        return False


def _set_qt_application_metadata(application_type: type) -> None:
    metadata = (
        ("setApplicationName", APP_INTERNAL_NAME),
        ("setApplicationDisplayName", APP_DISPLAY_NAME),
        ("setApplicationVersion", APP_VERSION),
        ("setOrganizationName", ORGANIZATION_NAME),
        ("setOrganizationDomain", ORGANIZATION_DOMAIN),
    )
    for setter_name, value in metadata:
        setter = getattr(application_type, setter_name, None)
        if callable(setter):
            setter(value)


class WeeklyCollectionWorker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="weekly-practice-collector", daemon=True)
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if not _weekly_collection_is_enabled():
            LOGGER.info(
                "Weekly collection is disabled; enable %s explicitly to run it",
                WEEKLY_COLLECTION_ENABLED_SETTING,
            )
            return
        try:
            from study_app.data.weekly_practice_collector import collection_is_due, collect_weekly_sources

            if not self._stop_event.is_set() and collection_is_due():
                collect_weekly_sources()
        except Exception as error:
            LOGGER.error("Weekly collection failed (%s)", type(error).__name__)


def _stop_weekly_collection(worker: WeeklyCollectionWorker) -> None:
    worker.request_stop()
    worker.join(timeout=2.0)
    if worker.is_alive():
        LOGGER.warning("Weekly collection worker did not stop within 2 seconds")


def run_app() -> None:
    try:
        from PySide6.QtCore import QDate, Qt
        from PySide6.QtWidgets import QApplication
    except ImportError as error:
        raise RuntimeError(
            "PySide6 尚未安装。安装后可运行：python -m study_app\n"
            "建议命令：python -m pip install PySide6"
        ) from error

    import sys

    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        if not send_wake_signal():
            LOGGER.warning("Another instance exists but could not be brought to the foreground")
        return

    try:
        _initialize_user_data()
        _record_optional_capability_diagnostics()
        _set_qt_application_metadata(QApplication)
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
        from study_app.ui.design_components import install_chinese_dialogs
        install_chinese_dialogs(app)
        from study_app.ui.performance_log import install_performance_log
        install_performance_log(app)
        app.setQuitOnLastWindowClosed(False)
        app.setStyleSheet(build_stylesheet())
        try:
            from study_app.integrations.chatgpt_desktop_bridge import cleanup_generated_pdfs

            cleanup_generated_pdfs()
        except Exception as error:
            LOGGER.error("Generated PDF cleanup failed (%s)", type(error).__name__)
        window = MainWindow(load_dashboard_state())

        weekly_collection_thread = WeeklyCollectionWorker()
        weekly_collection_thread.start()
        window._weekly_collection_thread = weekly_collection_thread
        app.aboutToQuit.connect(
            lambda: _stop_weekly_collection(weekly_collection_thread)
        )
        wake_server = WakeServer(window.request_restore)
        if wake_server.start():
            app.aboutToQuit.connect(wake_server.stop)
        else:
            LOGGER.warning(
                "Wake listener could not bind to %s:%d; the primary instance will continue",
                WAKE_HOST,
                WAKE_PORT,
            )
        window.show()
        exit_code = app.exec()
    finally:
        instance_guard.release()
    raise SystemExit(exit_code)
