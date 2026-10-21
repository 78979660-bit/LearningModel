"""Independent QGuiApplication + QQuickWindow runtime (no QWidget embedding)."""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QIcon, QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtQuick import QQuickWindow

from study_app.app_metadata import (
    APP_INTERNAL_NAME, APP_VERSION, ORGANIZATION_DOMAIN, ORGANIZATION_NAME,
)
from study_app.paths import DATABASE_PATH, MODEL_PATH, RECORDS_PATH, RESOURCE_ROOT
from study_app.quick.bridge import DashboardBridge
from study_app.quick.dashboard_service import DashboardService
from study_app.quick.resources import qml_root, validate_resources


LOGGER = logging.getLogger(__name__)


def default_service() -> DashboardService:
    def initialize():
        from study_app.data.database import ensure_seeded_database
        from study_app.data.practice_repository import ensure_practice_templates_seeded
        ensure_seeded_database()
        ensure_practice_templates_seeded(DATABASE_PATH)

    return DashboardService(DATABASE_PATH, MODEL_PATH, RECORDS_PATH, initialize=initialize)


def create_engine(bridge: DashboardBridge) -> QQmlApplicationEngine:
    validate_resources()
    app = QCoreApplication.instance()
    if not getattr(app, "_washi_font_registered", False):
        font_id = QFontDatabase.addApplicationFont(str(qml_root() / "assets/fonts/NotoSerifSC[wght].ttf"))
        if font_id < 0 or "Noto Serif SC" not in QFontDatabase.applicationFontFamilies(font_id):
            raise RuntimeError("Bundled Washi interface font could not be loaded")
        font = QFont("Noto Serif SC")
        font.setPixelSize(16)
        app.setFont(font)
        app._washi_font_registered = True
    from study_app.quick.startup_diagnostics import install_translations
    install_translations(QCoreApplication.instance())
    if QQuickStyle.name() != "Basic":
        QQuickStyle.setStyle("Basic")
    engine = QQmlApplicationEngine()
    engine._qml_warnings = []
    engine.warnings.connect(lambda errors: engine._qml_warnings.extend(
        item.toString() for item in errors))
    engine.setInitialProperties({"bridge": bridge})
    engine.load(QUrl.fromLocalFile(str(qml_root() / "Main.qml")))
    if not engine.rootObjects():
        engine.deleteLater()
        raise RuntimeError("QML root window could not be created: " + " | ".join(engine._qml_warnings))
    if engine._qml_warnings:
        engine.deleteLater()
        raise RuntimeError("QML startup diagnostics: " + " | ".join(engine._qml_warnings))
    return engine


class WakeBridge(QObject):
    restoreRequested = Signal()

    def __init__(self, lifecycle, parent=None):
        super().__init__(parent)
        self._lifecycle = lifecycle
        self.restoreRequested.connect(self.restore, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def restore(self):
        # The socket listener emits from a Python worker. Only this QObject's
        # GUI-thread slot may touch the windows; a free/static callback cannot
        # establish receiver thread affinity.
        if self._lifecycle.active and not self._lifecycle.quitting:
            self._lifecycle.restoreWindow()


def run_app() -> int:
    from study_app.single_instance import SingleInstanceGuard, WakeServer, send_wake_signal

    guard = SingleInstanceGuard()  # shared with Widgets; serializes schema startup
    if not guard.acquire():
        send_wake_signal()
        return 0
    bridge = None
    server = None
    lifecycle = None
    engine = None
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QGuiApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName(APP_INTERNAL_NAME)
        app.setApplicationDisplayName("和纸学习室")
        app.setApplicationVersion(APP_VERSION)
        app.setOrganizationName(ORGANIZATION_NAME)
        app.setOrganizationDomain(ORGANIZATION_DOMAIN)
        app.setWindowIcon(QIcon(str(RESOURCE_ROOT / "study_app/ui/assets/app.ico")))
        bridge = DashboardBridge(default_service(), preload_pages=True)
        from study_app.quick.lifecycle import LifecycleController
        lifecycle = LifecycleController(enable_tray=app.platformName() == "windows")
        bridge.attach_lifecycle(lifecycle)
        engine = create_engine(bridge)
        window = engine.rootObjects()[0]
        float_window = window.findChild(QQuickWindow, "floatWindow")
        if float_window is None:
            raise RuntimeError("QML recovery window is unavailable")
        lifecycle.start(window, float_window, app.quit)
        wake = WakeBridge(lifecycle, app)
        server = WakeServer(wake.restoreRequested.emit)
        if not server.start():
            LOGGER.warning("QML wake listener could not bind")
        app.aboutToQuit.connect(bridge.close)
        app.aboutToQuit.connect(lifecycle.stop)
        QTimer.singleShot(0, bridge.refresh)
        QTimer.singleShot(0, bridge.start_maintenance)
        return app.exec()
    finally:
        if lifecycle is not None:
            lifecycle.stop()
        if bridge is not None:
            bridge.close()
            bridge.wait_for_shutdown()
        if server is not None:
            server.stop()
        if engine is not None:
            for window in engine.rootObjects():
                window.close()
            engine.deleteLater()
        guard.release()
