from __future__ import annotations

import os
from pathlib import Path
import sys
import ctypes
import subprocess

import pytest

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle

from study_app.quick.lifecycle import LifecycleController, Win32TrayAdapter


_APP = None
_FLOAT_QML = Path(__file__).resolve().parents[1] / "study_app" / "quick" / "qml" / "FloatWindow.qml"


class FakeTray:
    def __init__(self, restore, show_float, quit_app, *, result=True, error=None):
        self.restore = restore
        self.show_float = show_float
        self.quit_app = quit_app
        self.result = result
        self.error = error
        self.register_calls = 0
        self.closed = False
        self.reminder_callback = None
        self.refresh_callback = None
        self.messages = []

    def register(self):
        self.register_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    def close(self):
        self.closed = True

    def set_action_callbacks(self, reminder_callback, refresh_callback):
        self.reminder_callback = reminder_callback
        self.refresh_callback = refresh_callback

    def show_message(self, title, message):
        self.messages.append((title, message))


def _app():
    global _APP
    app = QGuiApplication.instance()
    if app is None:
        old_platform = os.environ.get("QT_QPA_PLATFORM")
        if old_platform is None:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        try:
            _APP = QGuiApplication([])
        finally:
            if old_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
        app = _APP
    app.setQuitOnLastWindowClosed(False)
    if QQuickStyle.name() != "Basic":
        QQuickStyle.setStyle("Basic")
    return app


def _windows():
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(_FLOAT_QML)))
    assert component.status() == QQmlComponent.Status.Ready, [
        error.toString() for error in component.errors()
    ]
    float_window = component.create()
    assert isinstance(float_window, QQuickWindow), [
        error.toString() for error in component.errors()
    ]
    main_window = QQuickWindow()
    main_window.setObjectName("mainWindow")
    main_window.resize(600, 400)
    main_window.show()
    _app().processEvents()
    return engine, component, main_window, float_window


def _process_events(app):
    for _ in range(4):
        app.processEvents()


def test_float_window_is_qml_window_with_accessible_restore_and_quit_controls():
    _app()
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(_FLOAT_QML)))
    assert component.status() == QQmlComponent.Status.Ready, [
        error.toString() for error in component.errors()
    ]
    window = component.create()
    assert isinstance(window, QQuickWindow)
    assert window.objectName() == "floatWindow"
    assert window.property("controller") is None
    assert window.findChild(QObject, "FloatRestoreButton") is not None
    assert window.findChild(QObject, "FloatQuitButton") is not None
    window.deleteLater()
    engine.deleteLater()
    _app().processEvents()


def test_controller_exports_static_qt_slots_for_root_wiring():
    for slot in (
        "handleClose()",
        "hideToFloat()",
        "showFloat()",
        "restoreWindow()",
        "requestQuit()",
        "showTodayReminder()",
        "refreshHome()",
    ):
        assert LifecycleController.staticMetaObject.indexOfSlot(slot) >= 0


@pytest.mark.parametrize(
    ("command", "expected_callback"),
    [
        (Win32TrayAdapter.ID_REMINDER, "reminder"),
        (Win32TrayAdapter.ID_REFRESH, "refresh"),
    ],
)
def test_win32_context_menu_routes_reminder_and_refresh_without_qtwidgets(
    command, expected_callback,
):
    class FakeUser32:
        def __init__(self):
            self.items = []

        def CreatePopupMenu(self):
            return 17

        def AppendMenuW(self, menu, flags, item_id, label):
            self.items.append((flags, item_id, label))
            return 1

        def GetCursorPos(self, point_ref):
            point_ref._obj.x = 20
            point_ref._obj.y = 30
            return 1

        def SetForegroundWindow(self, _hwnd):
            return 1

        def TrackPopupMenu(self, *_args):
            return command

        def PostMessageW(self, *_args):
            return 1

        def DestroyMenu(self, _menu):
            return 1

    calls = []
    adapter = Win32TrayAdapter(123, lambda: calls.append("restore"), lambda: None, lambda: None)
    user32 = FakeUser32()
    adapter._user32 = user32
    adapter.set_action_callbacks(
        lambda: calls.append("reminder"), lambda: calls.append("refresh")
    )
    adapter._show_context_menu()

    labels = {item_id: label for _flags, item_id, label in user32.items if label}
    assert labels[Win32TrayAdapter.ID_REMINDER] == "显示今日提醒"
    assert labels[Win32TrayAdapter.ID_REFRESH] == "刷新模型数据"
    assert calls == [expected_callback]


def test_win32_reminder_balloon_uses_nim_modify_and_restores_registration_data():
    from study_app.quick import lifecycle as lifecycle_module

    class FakeShell32:
        def __init__(self):
            self.requests = []

        def Shell_NotifyIconW(self, command, data_pointer):
            data = ctypes.cast(
                data_pointer, ctypes.POINTER(lifecycle_module._NotifyIconDataW)
            ).contents
            self.requests.append((
                command, data.uFlags, data.szInfoTitle, data.szInfo,
                data.dwInfoFlags, data.uTimeout,
            ))
            return 1

    adapter = Win32TrayAdapter(123, lambda: None, lambda: None, lambda: None)
    adapter._registered = True
    adapter._shell32 = FakeShell32()
    adapter._nid = lifecycle_module._NotifyIconDataW()
    adapter._nid.uFlags = Win32TrayAdapter.NIF_MESSAGE
    adapter._nid.szInfoTitle = "Original title"
    adapter._nid.szInfo = "Original text"
    adapter._nid.dwInfoFlags = 0
    adapter._nid.uVersion = Win32TrayAdapter.NOTIFYICON_VERSION_4

    assert adapter.show_message("学习模型提醒", "今日学习\0计划", 8000)
    assert adapter._shell32.requests == [(
        Win32TrayAdapter.NIM_MODIFY,
        Win32TrayAdapter.NIF_INFO,
        "学习模型提醒",
        "今日学习计划",
        Win32TrayAdapter.NIIF_INFO,
        8000,
    )]
    assert adapter._nid.uFlags == Win32TrayAdapter.NIF_MESSAGE
    assert adapter._nid.szInfoTitle == "Original title"
    assert adapter._nid.szInfo == "Original text"
    assert adapter._nid.dwInfoFlags == 0
    assert adapter._nid.uVersion == Win32TrayAdapter.NOTIFYICON_VERSION_4


def test_controller_lifecycle_close_restore_float_close_and_stop():
    app = _app()
    engine, component, main, floating = _windows()
    trays = []

    def make_tray(hwnd, restore, show_float, quit_app):
        tray = FakeTray(restore, show_float, quit_app)
        trays.append(tray)
        return tray

    controller = LifecycleController(tray_factory=make_tray)
    assert not controller.active
    assert not controller.quitting
    assert trays == []  # Constructor performs no registration.
    assert controller.start(main, floating, lambda: None)
    assert controller.active
    assert controller.trayAvailable
    assert trays[0].register_calls == 1

    controller.showFloat()
    _process_events(app)
    assert not main.isVisible()
    assert floating.isVisible()

    restore_button = floating.findChild(QObject, "FloatRestoreButton")
    assert restore_button is not None
    floating.restoreRequested.emit()
    _process_events(app)
    assert main.isVisible()
    assert not floating.isVisible()

    controller.hideToFloat()
    _process_events(app)
    assert not main.isVisible()
    assert floating.isVisible()

    floating.close()
    _process_events(app)
    assert main.isVisible()
    assert not floating.isVisible()

    controller.stop()
    assert not controller.active
    assert not controller.trayAvailable
    assert trays[0].closed
    main.close()
    floating.close()
    engine.deleteLater()
    _process_events(app)


def test_tray_reminder_uses_loaded_home_snapshot_and_refreshes_through_bridge():
    app = _app()
    engine, component, main, floating = _windows()

    class ReminderService:
        def __init__(self):
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            return {"todos": [{
                "kind": "记忆预警",
                "title": "数学 / 函数",
                "detail": "已 3 天未复习",
            }]}

    from study_app.quick.bridge import DashboardBridge

    service = ReminderService()
    bridge = DashboardBridge(service)
    trays = []

    def make_tray(hwnd, restore, show_float, quit_app):
        tray = FakeTray(restore, show_float, quit_app)
        trays.append(tray)
        return tray

    controller = LifecycleController(tray_factory=make_tray)
    bridge.attach_lifecycle(controller)
    controller.start(main, floating, lambda: None)
    tray = trays[0]
    try:
        assert service.load_calls == 0
        tray.reminder_callback()
        assert tray.messages == [(
            "学习模型提醒", "首页数据尚未加载，请先刷新模型数据。")
        ]
        assert service.load_calls == 0

        loop = QEventLoop()
        bridge.snapshotChanged.connect(loop.quit)
        QTimer.singleShot(2000, loop.quit)
        tray.refresh_callback()
        loop.exec()
        bridge.snapshotChanged.disconnect(loop.quit)
        _process_events(app)

        assert service.load_calls == 1
        tray.reminder_callback()
        assert tray.messages[-1] == (
            "学习模型提醒", "记忆预警: 数学 / 函数\n已 3 天未复习")
    finally:
        controller.stop()
        bridge.close()
        bridge.wait_for_shutdown()
        main.close()
        floating.close()
        engine.deleteLater()
        _process_events(app)


def test_tray_failure_keeps_float_and_explicit_quit_reachable():
    app = _app()
    engine, component, main, floating = _windows()
    trays = []

    def make_tray(hwnd, restore, show_float, quit_app):
        tray = FakeTray(restore, show_float, quit_app, error=OSError("unavailable"))
        trays.append(tray)
        return tray

    quit_calls = []
    controller = LifecycleController(tray_factory=make_tray)
    assert not controller.start(main, floating, lambda: quit_calls.append("quit"))
    assert not controller.trayAvailable
    assert trays[0].closed

    controller.showFloat()
    _process_events(app)
    assert not main.isVisible()
    assert floating.isVisible()

    quit_button = floating.findChild(QObject, "FloatQuitButton")
    assert quit_button is not None
    quit_button.triggered.emit()
    _process_events(app)
    assert controller.quitting
    assert floating.isVisible()
    assert quit_calls == ["quit"]

    # Explicit quit is idempotent even when tray and float callbacks race.
    controller.requestQuit()
    _process_events(app)
    assert quit_calls == ["quit"]
    controller.stop()
    engine.deleteLater()
    _process_events(app)


def test_tray_can_be_disabled_without_constructing_an_adapter():
    app = _app()
    engine, component, main, floating = _windows()
    called = []
    controller = LifecycleController(
        tray_factory=lambda *args: called.append(args),
        enable_tray=False,
    )
    assert not controller.start(main, floating, lambda: None)
    assert called == []
    controller.showFloat()
    _process_events(app)
    assert floating.isVisible()
    assert not main.isVisible()
    controller.restoreWindow()
    _process_events(app)
    assert main.isVisible()
    controller.stop()
    main.close()
    floating.close()
    engine.deleteLater()
    _process_events(app)


def test_root_main_nested_float_closes_restores_and_destroys_after_stop():
    app = _app()
    from shiboken6 import isValid
    from study_app.quick.bridge import DashboardBridge
    from study_app.quick.runtime import create_engine

    class EmptyService:
        def load(self):
            return {}

    bridge = DashboardBridge(EmptyService())
    quit_calls = []
    controller = LifecycleController(enable_tray=False)
    bridge.attach_lifecycle(controller)
    engine = create_engine(bridge)
    assert not engine._qml_warnings
    window = engine.rootObjects()[0]
    floating = window.findChild(QQuickWindow, "floatWindow")
    assert floating is not None
    assert floating.property("controller") == controller
    controller.start(window, floating, lambda: quit_calls.append("root-quit"))

    controller.showFloat()
    _process_events(app)
    assert not window.isVisible()
    assert floating.isVisible()
    floating.close()
    _process_events(app)
    assert window.isVisible()
    assert not floating.isVisible()

    assert not controller.quitting
    assert quit_calls == []
    window.close()
    _process_events(app)
    assert controller.quitting and quit_calls == ["root-quit"]
    assert not floating.isVisible()

    # Root finally cleanup removes filters before destruction. The final close
    # must be accepted and destroying the engine must invalidate both windows.
    controller.stop()
    assert not controller.active
    assert controller.quitting
    assert window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    engine.deleteLater()
    bridge.close()
    bridge.wait_for_shutdown()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _process_events(app)
    assert not isValid(window)
    assert not isValid(floating)
    assert quit_calls == ["root-quit"]


def test_qgui_app_exec_returns_when_explicit_quit_calls_app_quit_with_float_visible():
    repo_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    script = r'''
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from study_app.quick.lifecycle import LifecycleController

app = QGuiApplication([])
QQuickStyle.setStyle("Basic")
qml_path = Path("study_app/quick/qml/FloatWindow.qml").resolve()
engine = QQmlEngine()
component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path)))
assert component.status() == QQmlComponent.Status.Ready, [e.toString() for e in component.errors()]
floating = component.create()
assert isinstance(floating, QQuickWindow), [e.toString() for e in component.errors()]
main = QQuickWindow()
main.show()
app.setQuitOnLastWindowClosed(False)
controller = LifecycleController(enable_tray=False)
quit_calls = []

def root_quit():
    # Match production: the root callback calls app.quit() directly. The
    # floating window is still visible here, so this exercises shutdown with
    # the real previously displayed QQuickWindow attached to the controller.
    quit_calls.append(floating.isVisible())
    app.quit()

controller.start(main, floating, root_quit)
controller.showFloat()
app.processEvents()
assert not main.isVisible() and floating.isVisible()
watchdog = QTimer()
watchdog.setSingleShot(True)
watchdog.timeout.connect(lambda: app.exit(17))
watchdog.start(1500)
QTimer.singleShot(0, LifecycleController.requestQuit)
QTimer.singleShot(0, LifecycleController.requestQuit)
code = app.exec()
watchdog.stop()
assert code == 0, f"QGuiApplication.exec returned watchdog code {code}"
assert controller.quitting and quit_calls == [True]
controller.stop()
engine.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()
print("explicit-quit-exec-returned")
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "explicit-quit-exec-returned" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Shell_NotifyIconW is Windows-only")
def test_win32_shell_notify_icon_registration_and_cleanup():
    app = _app()
    if app.platformName().lower() == "offscreen":
        pytest.skip("offscreen Qt windows do not provide a real Shell callback HWND")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    if not user32.FindWindowW("Shell_TrayWnd", None):
        pytest.skip("interactive Windows shell has no notification-area host")
    window = QQuickWindow()
    window.setObjectName("nativeTrayCallbackWindow")
    window.create()
    adapter = Win32TrayAdapter(int(window.winId()), lambda: None, lambda: None, lambda: None)
    try:
        assert adapter.register()
        assert adapter.registered
    finally:
        adapter.close()
        window.destroy()
    assert not adapter.registered


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native messages are Windows-only")
def test_win32_postmessage_routes_v4_mouse_and_keyboard_selection_through_qt_filter():
    app = _app()
    if app.platformName().lower() == "offscreen":
        pytest.skip("offscreen Qt windows do not receive real HWND messages")
    window = QQuickWindow()
    window.create()
    hwnd = int(window.winId())
    restored = []
    adapter = Win32TrayAdapter(
        hwnd,
        lambda: restored.append("restore"),
        lambda: restored.append("float"),
        lambda: restored.append("quit"),
    )
    # This test injects only the native notification callback message. It does
    # not register or claim a Shell notification-area icon.
    adapter._registered = True
    adapter._app = app
    app.installNativeEventFilter(adapter)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    user32.PostMessageW.restype = ctypes.c_int
    try:
        for notification in (adapter.NIN_SELECT, adapter.NIN_KEYSELECT):
            payload = (adapter.TRAY_ID << 16) | notification
            assert user32.PostMessageW(
                ctypes.c_void_p(hwnd), adapter.CALLBACK_MESSAGE, 0, payload
            )
            loop = QEventLoop()
            QTimer.singleShot(100, loop.quit)
            loop.exec()
        assert restored == ["restore", "restore"]

        wrong_icon = (0x1234 << 16) | adapter.NIN_SELECT
        assert user32.PostMessageW(
            ctypes.c_void_p(hwnd), adapter.CALLBACK_MESSAGE, 0, wrong_icon
        )
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
        assert restored == ["restore", "restore"]
    finally:
        adapter._registered = False
        adapter._app = None
        app.removeNativeEventFilter(adapter)
        window.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 icon APIs are Windows-only")
def test_win32_app_icon_is_loaded_owned_and_released_without_tray_host():
    app = _app()
    if app.platformName().lower() == "offscreen":
        pytest.skip("offscreen Qt windows do not provide a Windows GUI session")
    window = QQuickWindow()
    window.create()
    adapter = Win32TrayAdapter(int(window.winId()), lambda: None, lambda: None, lambda: None)
    try:
        adapter._load_api()
        icon = adapter._load_tray_icon()
        assert icon
        assert adapter._owns_icon
        adapter._icon = icon
        adapter.close()
        assert adapter._icon is None
        assert not adapter._owns_icon
    finally:
        adapter.close()
        window.destroy()



