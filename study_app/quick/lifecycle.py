"""Window and native Windows tray lifecycle for the QML application.

The controller does no registration in its constructor. The application root calls
start after creating the QGuiApplication and windows, then calls stop from its
shutdown finally block. Worker draining and single-instance release stay with
that root so GUI callbacks never block on them.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QObject,
    Property,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication


LOGGER = logging.getLogger(__name__)


class LifecycleController(QObject):
    """Coordinates main-window, floating-window, tray, and explicit quit flows."""

    trayAvailabilityChanged = Signal()
    lifecycleStateChanged = Signal()

    # The application has one window lifecycle. Static Qt slots avoid binding
    # dynamically-created instance callables through Qt native slot machinery.
    _active_controller: "LifecycleController | None" = None

    def __init__(
        self,
        tray_factory: Callable[..., object] | None = None,
        enable_tray: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._tray_factory = tray_factory
        self._enable_tray = bool(enable_tray)
        self._app: QGuiApplication | None = None
        self._window = None
        self._float_window = None
        self._quit_callback: Callable[[], object] | None = None
        self._tray = None
        self._tray_available = False
        self._active = False
        self._allow_close = False
        self._quit_requested = False
        self._float_restore_signal = None
        self._float_quit_signal = None
        self._tray_reminder_text = None
        self._tray_refresh_callback = None

    @Property(bool, notify=trayAvailabilityChanged)
    def trayAvailable(self) -> bool:
        return self._tray_available

    @Property(bool, notify=lifecycleStateChanged)
    def active(self) -> bool:
        return self._active

    @Property(bool, notify=lifecycleStateChanged)
    def quitting(self) -> bool:
        return self._quit_requested

    def set_tray_actions(self, reminder_text_provider, refresh_callback) -> None:
        """Connect safe, non-blocking homepage actions to the native tray."""
        if reminder_text_provider is not None and not callable(reminder_text_provider):
            raise TypeError("reminder_text_provider must be callable or None")
        if refresh_callback is not None and not callable(refresh_callback):
            raise TypeError("refresh_callback must be callable or None")
        self._tray_reminder_text = reminder_text_provider
        self._tray_refresh_callback = refresh_callback
        self._configure_tray_actions()

    def start(self, window, float_window, quit_callback: Callable[[], object]) -> bool:
        """Attach to already-created windows and attempt tray registration.

        Returns whether a native tray icon was registered. Failure leaves the
        floating QML window available for explicit hide actions.
        """
        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("LifecycleController.start requires QGuiApplication")
        if QThread.currentThread() is not app.thread():
            raise RuntimeError("LifecycleController.start must run on the GUI thread")
        if window is None or float_window is None:
            raise ValueError("main and floating QML windows are required")
        if not callable(quit_callback):
            raise TypeError("quit_callback must be callable")
        if self._active:
            raise RuntimeError("LifecycleController is already started")
        if LifecycleController._active_controller not in (None, self):
            raise RuntimeError("another lifecycle controller is active")

        self._app = app
        self._window = window
        self._float_window = float_window
        self._quit_callback = quit_callback
        self._allow_close = False
        self._quit_requested = False
        self._active = True
        LifecycleController._active_controller = self
        app.setQuitOnLastWindowClosed(False)
        self.lifecycleStateChanged.emit()

        try:
            window.installEventFilter(self)
            float_window.installEventFilter(self)
            self._float_restore_signal = getattr(float_window, "restoreRequested", None)
            self._float_quit_signal = getattr(float_window, "quitRequested", None)
            if self._float_restore_signal is not None:
                self._float_restore_signal.connect(LifecycleController.restoreWindow)
            if self._float_quit_signal is not None:
                self._float_quit_signal.connect(LifecycleController.requestQuit)
        except Exception:
            self.stop()
            raise

        if self._enable_tray:
            factory = self._tray_factory or _default_tray_factory
            try:
                self._tray = factory(
                    int(window.winId()),
                    LifecycleController.restoreWindow,
                    LifecycleController.showFloat,
                    LifecycleController.requestQuit,
                )
                if self._tray is not None:
                    self._configure_tray_actions()
                    self._tray_available = bool(self._tray.register())
            except Exception:
                # The tray is optional. Keep the QML float window as the recovery
                # path and do not expose native paths or Win32 diagnostics to QML.
                LOGGER.info("Native tray unavailable; floating-window fallback remains active",
                            exc_info=True)
                self._close_tray()
                self._tray_available = False
        self.trayAvailabilityChanged.emit()
        return self._tray_available

    def stop(self) -> None:
        """Idempotently detach windows and unregister native tray resources."""
        if not self._active and self._tray is None:
            return
        self._allow_close = True
        self._quit_requested = True
        self.lifecycleStateChanged.emit()
        try:
            self._close_tray()
        finally:
            window = self._window
            float_window = self._float_window
            try:
                if window is not None:
                    window.removeEventFilter(self)
            except RuntimeError:
                pass
            try:
                if float_window is not None:
                    float_window.removeEventFilter(self)
            except RuntimeError:
                pass
            if self._float_restore_signal is not None:
                try:
                    self._float_restore_signal.disconnect(LifecycleController.restoreWindow)
                except (RuntimeError, TypeError):
                    pass
            if self._float_quit_signal is not None:
                try:
                    self._float_quit_signal.disconnect(LifecycleController.requestQuit)
                except (RuntimeError, TypeError):
                    pass
            self._active = False
            self._tray_available = False
            if LifecycleController._active_controller is self:
                LifecycleController._active_controller = None
            self._app = None
            self._window = None
            self._float_window = None
            self._quit_callback = None
            self._float_restore_signal = None
            self._float_quit_signal = None
            self.lifecycleStateChanged.emit()
            self.trayAvailabilityChanged.emit()

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Close or not self._active:
            return False
        if watched is self._window:
            if self._allow_close:
                return False
            event.ignore()
            self._request_quit()
            return True
        if watched is self._float_window:
            if self._allow_close or self._quit_requested:
                # During explicit shutdown, allow Qt's close-all sequence to
                # destroy both QQuickWindows. Outside shutdown, float close is
                # a restore action and must not lose the application.
                return False
            event.ignore()
            self._restore_window()
            return True
        return False

    @Slot()
    @staticmethod
    def handleClose():
        controller = LifecycleController._active_controller
        if controller is not None:
            controller._handle_close()

    @Slot()
    @staticmethod
    def hideToFloat():
        controller = LifecycleController._active_controller
        if controller is not None:
            if not controller._show_float_and_hide_main():
                controller._show_main()

    @Slot()
    @staticmethod
    def showFloat():
        controller = LifecycleController._active_controller
        if controller is not None:
            if not controller._show_float_and_hide_main():
                controller._show_main()

    @Slot()
    @staticmethod
    def restoreWindow():
        controller = LifecycleController._active_controller
        if controller is not None:
            controller._restore_window()

    @Slot()
    @staticmethod
    def requestQuit():
        controller = LifecycleController._active_controller
        if controller is not None:
            controller._request_quit()

    @Slot()
    @staticmethod
    def showTodayReminder():
        controller = LifecycleController._active_controller
        if controller is not None:
            controller._show_today_reminder()

    @Slot()
    @staticmethod
    def refreshHome():
        controller = LifecycleController._active_controller
        if controller is not None:
            controller._refresh_home()

    def _show_float_and_hide_main(self) -> bool:
        if not self._active or self._float_window is None or self._window is None:
            return False
        try:
            self._float_window.show()
            self._float_window.raise_()
            self._float_window.requestActivate()
            self._window.hide()
            return True
        except RuntimeError:
            return False

    def _show_main(self) -> None:
        if not self._active or self._window is None:
            return
        try:
            self._window.showNormal()
            self._window.raise_()
            self._window.requestActivate()
            if self._float_window is not None:
                self._float_window.hide()
        except RuntimeError:
            LOGGER.info("Could not restore the main QML window")

    def _restore_window(self) -> None:
        self._show_main()

    def _handle_close(self) -> None:
        if not self._active or self._quit_requested:
            return
        self._request_quit()

    def _request_quit(self) -> None:
        if not self._active or self._quit_requested:
            return
        self._quit_requested = True
        self._allow_close = True
        self.lifecycleStateChanged.emit()
        callback = self._quit_callback
        if callback is not None:
            # The root owns app.quit(), worker draining, and single-instance
            # release. Dispatch in the event loop without waiting here. Keep
            # the windows in their current state: app.quit() ends the event
            # loop, while aboutToQuit/final cleanup owns QObject destruction.
            # If Qt delivers close events during shutdown, _allow_close lets
            # both windows accept them.
            QTimer.singleShot(0, callback)

    def _configure_tray_actions(self) -> None:
        tray = self._tray
        configure = getattr(tray, "set_action_callbacks", None) if tray is not None else None
        if callable(configure):
            configure(LifecycleController.showTodayReminder, LifecycleController.refreshHome)

    def _show_today_reminder(self) -> None:
        tray = self._tray
        show_message = getattr(tray, "show_message", None) if tray is not None else None
        if not callable(show_message):
            return
        text = "首页数据尚未加载，请先刷新模型数据。"
        provider = self._tray_reminder_text
        if provider is not None:
            try:
                text = str(provider())
            except Exception as error:
                LOGGER.info("Could not read the current tray reminder (%s)", type(error).__name__)
                text = "今日提醒暂时无法读取，请刷新首页数据。"
        try:
            show_message("学习模型提醒", text)
        except Exception as error:
            LOGGER.info("Could not show the tray reminder (%s)", type(error).__name__)

    def _refresh_home(self) -> None:
        callback = self._tray_refresh_callback
        if callback is None:
            return
        try:
            callback()
        except Exception as error:
            LOGGER.info("Could not start the tray homepage refresh (%s)", type(error).__name__)

    def _close_tray(self) -> None:
        tray = self._tray
        self._tray = None
        if tray is not None:
            try:
                tray.close()
            except Exception:
                LOGGER.info("Native tray cleanup failed", exc_info=True)
        if self._tray_available:
            self._tray_available = False
            self.trayAvailabilityChanged.emit()


def _default_tray_factory(hwnd: int, on_restore, on_float, on_quit):
    if sys.platform != "win32":
        return None
    return Win32TrayAdapter(hwnd, on_restore, on_float, on_quit)


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _TimeoutOrVersion(ctypes.Union):
    _fields_ = [("uTimeout", ctypes.c_uint32), ("uVersion", ctypes.c_uint32)]


class _NotifyIconDataW(ctypes.Structure):
    """Current Windows SDK NOTIFYICONDATAW layout."""

    _anonymous_ = ("timeout_or_version",)
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint32),
        ("uFlags", ctypes.c_uint32),
        ("uCallbackMessage", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint32),
        ("dwStateMask", ctypes.c_uint32),
        ("szInfo", ctypes.c_wchar * 256),
        ("timeout_or_version", _TimeoutOrVersion),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint32),
        ("guidItem", _Guid),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MessagePrefix(ctypes.Structure):
    """The MSG prefix needed by QAbstractNativeEventFilter."""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
    ]


class Win32TrayAdapter(QAbstractNativeEventFilter):
    """Small native Shell_NotifyIconW adapter; it imports no QtWidgets types."""

    WM_APP = 0x8000
    CALLBACK_MESSAGE = WM_APP + 0x4A1
    TRAY_ID = 0x4A1
    NIM_ADD = 0x0
    NIM_MODIFY = 0x1
    NIM_DELETE = 0x2
    NIM_SETVERSION = 0x4
    NIF_MESSAGE = 0x1
    NIF_ICON = 0x2
    NIF_TIP = 0x4
    NIF_INFO = 0x10
    NIF_SHOWTIP = 0x80
    NIIF_INFO = 0x1
    NOTIFYICON_VERSION_4 = 4
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_CONTEXTMENU = 0x007B
    NIN_SELECT = 0x0400
    NIN_KEYSELECT = 0x0401
    WM_NULL = 0x0000
    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100
    MF_STRING = 0x0000
    MF_SEPARATOR = 0x0800
    ID_RESTORE = 4101
    ID_FLOAT = 4102
    ID_QUIT = 4103
    ID_REMINDER = 4104
    ID_REFRESH = 4105

    def __init__(self, hwnd: int, on_restore, on_float, on_quit):
        super().__init__()
        self._hwnd = int(hwnd)
        self._on_restore = on_restore
        self._on_float = on_float
        self._on_quit = on_quit
        self._on_reminder = None
        self._on_refresh = None
        self._app = None
        self._shell32 = None
        self._user32 = None
        self._nid = None
        self._icon = None
        self._owns_icon = False
        self._taskbar_created = 0
        self._registered = False

    @property
    def registered(self) -> bool:
        return self._registered

    def register(self) -> bool:
        if sys.platform != "win32":
            return False
        if self._registered:
            return True
        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("tray registration requires QGuiApplication")
        if self._hwnd <= 0:
            raise ValueError("tray callback window handle is invalid")

        self._load_api()
        icon = self._load_tray_icon()
        self._icon = icon
        data = _NotifyIconDataW()
        data.cbSize = ctypes.sizeof(_NotifyIconDataW)
        data.hWnd = ctypes.c_void_p(self._hwnd)
        data.uID = self.TRAY_ID
        data.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP | self.NIF_SHOWTIP
        data.uCallbackMessage = self.CALLBACK_MESSAGE
        data.hIcon = icon
        data.szTip = "和纸学习室"
        self._nid = data

        try:
            if not self._shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(data)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._registered = True
            data.uVersion = self.NOTIFYICON_VERSION_4
            if not self._shell32.Shell_NotifyIconW(self.NIM_SETVERSION, ctypes.byref(data)):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

        self._app = app
        self._taskbar_created = self._user32.RegisterWindowMessageW("TaskbarCreated")
        app.installNativeEventFilter(self)
        return True

    def close(self) -> None:
        app = self._app
        self._app = None
        if app is not None:
            try:
                app.removeNativeEventFilter(self)
            except RuntimeError:
                pass
        try:
            if self._registered and self._shell32 is not None and self._nid is not None:
                self._shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(self._nid))
        finally:
            self._registered = False
            self._nid = None
            self._destroy_icon()

    def set_action_callbacks(self, on_reminder, on_refresh) -> None:
        if on_reminder is not None and not callable(on_reminder):
            raise TypeError("on_reminder must be callable or None")
        if on_refresh is not None and not callable(on_refresh):
            raise TypeError("on_refresh must be callable or None")
        self._on_reminder = on_reminder
        self._on_refresh = on_refresh

    def show_message(self, title: str, message: str, timeout_ms: int = 8000) -> bool:
        """Show an explicit user-requested Shell balloon without QtWidgets."""
        if not self._registered or self._shell32 is None or self._nid is None:
            return False
        data = self._nid
        saved = (data.uFlags, data.szInfoTitle, data.szInfo, data.dwInfoFlags, data.uVersion)
        try:
            data.uFlags = self.NIF_INFO
            data.szInfoTitle = str(title).replace("\0", "")[:63]
            data.szInfo = str(message).replace("\0", "")[:255]
            data.dwInfoFlags = self.NIIF_INFO
            data.uTimeout = max(1000, min(int(timeout_ms), 30_000))
            return bool(self._shell32.Shell_NotifyIconW(self.NIM_MODIFY, ctypes.byref(data)))
        except (OSError, TypeError, ValueError, OverflowError):
            LOGGER.info("Native tray reminder balloon could not be shown", exc_info=True)
            return False
        finally:
            data.uFlags, data.szInfoTitle, data.szInfo, data.dwInfoFlags, data.uVersion = saved

    def nativeEventFilter(self, event_type, message):
        if not self._registered:
            return False, 0
        try:
            prefix = ctypes.cast(int(message), ctypes.POINTER(_MessagePrefix)).contents
        except (TypeError, ValueError, OSError):
            return False, 0
        hwnd = int(prefix.hwnd or 0)
        message_id = int(prefix.message)
        if self._taskbar_created and message_id == self._taskbar_created:
            self._readd_icon()
            return False, 0
        if hwnd != self._hwnd or message_id != self.CALLBACK_MESSAGE:
            return False, 0
        callback_data = int(prefix.lParam)
        icon_id = (callback_data >> 16) & 0xFFFF
        if icon_id != self.TRAY_ID:
            return False, 0
        notification = callback_data & 0xFFFF
        if notification in (
            self.NIN_SELECT,
            self.NIN_KEYSELECT,
            self.WM_LBUTTONUP,
            self.WM_LBUTTONDBLCLK,
        ):
            self._on_restore()
        elif notification in (self.WM_RBUTTONUP, self.WM_CONTEXTMENU):
            self._show_context_menu()
        return True, 0

    def _load_api(self) -> None:
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._shell32.Shell_NotifyIconW.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_NotifyIconDataW),
        ]
        self._shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self._user32.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._user32.LoadIconW.restype = ctypes.c_void_p
        self._user32.LoadImageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        self._user32.LoadImageW.restype = ctypes.c_void_p
        self._user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self._user32.GetSystemMetrics.restype = ctypes.c_int
        self._user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        self._user32.DestroyIcon.restype = wintypes.BOOL
        self._user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
        self._user32.RegisterWindowMessageW.restype = ctypes.c_uint32
        self._user32.CreatePopupMenu.restype = ctypes.c_void_p
        self._user32.AppendMenuW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_wchar_p,
        ]
        self._user32.AppendMenuW.restype = wintypes.BOOL
        self._user32.TrackPopupMenu.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._user32.TrackPopupMenu.restype = ctypes.c_uint
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(_Point)]
        self._user32.GetCursorPos.restype = wintypes.BOOL
        self._user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._user32.PostMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        self._user32.PostMessageW.restype = wintypes.BOOL
        self._user32.DestroyMenu.argtypes = [ctypes.c_void_p]
        self._user32.DestroyMenu.restype = wintypes.BOOL

    def _load_tray_icon(self):
        from study_app.paths import RESOURCE_ROOT

        icon_path = RESOURCE_ROOT / "study_app" / "ui" / "assets" / "app.ico"
        if icon_path.is_file():
            width = self._user32.GetSystemMetrics(49) or 16  # SM_CXSMICON
            height = self._user32.GetSystemMetrics(50) or 16  # SM_CYSMICON
            icon = self._user32.LoadImageW(
                None,
                str(icon_path),
                1,  # IMAGE_ICON
                width,
                height,
                0x0010,  # LR_LOADFROMFILE; this adapter owns the returned HICON
            )
            if not icon:
                raise ctypes.WinError(ctypes.get_last_error())
            self._owns_icon = True
            return icon

        # LoadIcon returns a shared system handle. It is intentionally not
        # destroyed by _destroy_icon.
        icon = self._user32.LoadIconW(
            None, ctypes.cast(ctypes.c_void_p(32512), ctypes.c_wchar_p)
        )
        if not icon:
            raise ctypes.WinError(ctypes.get_last_error())
        self._owns_icon = False
        return icon

    def _destroy_icon(self) -> None:
        icon = self._icon
        owns_icon = self._owns_icon
        self._icon = None
        self._owns_icon = False
        if icon and owns_icon and self._user32 is not None:
            self._user32.DestroyIcon(ctypes.c_void_p(int(icon)))

    def _readd_icon(self) -> None:
        if not self._registered or self._nid is None:
            return
        if self._shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(self._nid)):
            self._nid.uVersion = self.NOTIFYICON_VERSION_4
            self._shell32.Shell_NotifyIconW(self.NIM_SETVERSION, ctypes.byref(self._nid))

    def _show_context_menu(self) -> None:
        menu = self._user32.CreatePopupMenu()
        if not menu:
            return
        try:
            self._user32.AppendMenuW(menu, self.MF_STRING, self.ID_RESTORE, "打开学习面板")
            self._user32.AppendMenuW(menu, self.MF_STRING, self.ID_REMINDER, "显示今日提醒")
            self._user32.AppendMenuW(menu, self.MF_STRING, self.ID_REFRESH, "刷新模型数据")
            self._user32.AppendMenuW(menu, self.MF_STRING, self.ID_FLOAT, "显示浮窗")
            self._user32.AppendMenuW(menu, self.MF_SEPARATOR, 0, None)
            self._user32.AppendMenuW(menu, self.MF_STRING, self.ID_QUIT, "退出程序")
            point = _Point()
            self._user32.GetCursorPos(ctypes.byref(point))
            self._user32.SetForegroundWindow(ctypes.c_void_p(self._hwnd))
            command = self._user32.TrackPopupMenu(
                menu,
                self.TPM_RIGHTBUTTON | self.TPM_RETURNCMD,
                point.x,
                point.y,
                0,
                ctypes.c_void_p(self._hwnd),
                None,
            )
            # Required by Windows to dismiss the menu on the next click.
            self._user32.PostMessageW(
                ctypes.c_void_p(self._hwnd), self.WM_NULL, 0, 0
            )
            if command == self.ID_RESTORE:
                self._on_restore()
            elif command == self.ID_REMINDER and self._on_reminder is not None:
                self._on_reminder()
            elif command == self.ID_REFRESH and self._on_refresh is not None:
                self._on_refresh()
            elif command == self.ID_FLOAT:
                self._on_float()
            elif command == self.ID_QUIT:
                self._on_quit()
        finally:
            self._user32.DestroyMenu(menu)
