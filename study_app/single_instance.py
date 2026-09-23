from __future__ import annotations

import ctypes
import logging
import socket
import sys
import threading
from ctypes import wintypes
from typing import Callable

from study_app.app_metadata import APP_INTERNAL_NAME


LOGGER = logging.getLogger(__name__)


ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = rf"Local\{APP_INTERNAL_NAME}.SingleInstance"
WAKE_HOST = "127.0.0.1"
WAKE_PORT = 47631
WAKE_MESSAGE = b"show"


class SingleInstanceGuard:
    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self.handle = None
        self._kernel32 = None

    def acquire(self) -> bool:
        if not sys.platform.startswith("win"):
            return True
        if self.handle:
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self.name)
        last_error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(last_error)
        if last_error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        self._kernel32 = kernel32
        return True

    def release(self) -> None:
        if not self.handle or not sys.platform.startswith("win"):
            return
        handle = self.handle
        self.handle = None
        kernel32 = self._kernel32
        self._kernel32 = None
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(handle):
            LOGGER.warning(
                "Windows mutex handle close failed (error=%d)",
                ctypes.get_last_error(),
            )


def send_wake_signal() -> bool:
    try:
        with socket.create_connection((WAKE_HOST, WAKE_PORT), timeout=0.4) as client:
            client.sendall(WAKE_MESSAGE)
        return True
    except OSError:
        return False


class WakeServer:
    def __init__(self, callback: Callable[[], None]):
        self.callback = callback
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.running = False

    def start(self) -> bool:
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((WAKE_HOST, WAKE_PORT))
            server.listen(4)
            server.settimeout(0.5)
        except OSError:
            return False
        self.socket = server
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        self.running = False
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

    def _serve(self) -> None:
        while self.running and self.socket is not None:
            try:
                client, _address = self.socket.accept()
            except TimeoutError:
                continue
            except OSError as error:
                if self.running:
                    LOGGER.error(
                        "Wake server accept failed (%s)",
                        type(error).__name__,
                    )
                break
            with client:
                try:
                    message = client.recv(32)
                except OSError:
                    continue
            if message.strip() == WAKE_MESSAGE:
                try:
                    self.callback()
                except Exception as error:
                    LOGGER.error(
                        "Wake callback failed (%s)",
                        type(error).__name__,
                    )
