"""Python-owned Windows credential input; credentials never enter the QML engine.

The application continues to use a QGuiApplication and QQuickWindow. This is a
native modal Windows prompt, not a QWidget or a QML password field. Storage is
owned by the existing provider service, not Windows Credential Manager.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def prompt_credential(provider_label: str, parent_handle: int = 0) -> str | None:
    if sys.platform != "win32":
        raise RuntimeError("Native credential input is available on Windows")

    class CredentialInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("parent", wintypes.HWND),
                    ("message", wintypes.LPCWSTR), ("caption", wintypes.LPCWSTR),
                    ("banner", wintypes.HBITMAP)]

    prompt = ctypes.WinDLL("credui", use_last_error=True).CredUIPromptForCredentialsW
    prompt.argtypes = [ctypes.POINTER(CredentialInfo), wintypes.LPCWSTR, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG,
                       wintypes.LPWSTR, wintypes.ULONG, ctypes.POINTER(wintypes.BOOL), wintypes.DWORD]
    prompt.restype = wintypes.DWORD
    info = CredentialInfo(ctypes.sizeof(CredentialInfo), parent_handle,
                          "在密码栏输入本次服务商的 API 密钥。取消不修改已有凭据。",
                          "和纸学习室 · 配置服务凭据", None)
    username = ctypes.create_unicode_buffer(provider_label, 514)
    password = ctypes.create_unicode_buffer(514)
    save = wintypes.BOOL(False)
    # GENERIC | ALWAYS_SHOW_UI | DO_NOT_PERSIST | EXCLUDE_CERTIFICATES | KEEP_USERNAME
    flags = 0x40000 | 0x80 | 0x2 | 0x8 | 0x100000
    try:
        result = prompt(ctypes.byref(info), "LearningModel/provider", None, 0,
                        username, len(username), password, len(password), ctypes.byref(save), flags)
        if result == 1223:  # ERROR_CANCELLED
            return None
        if result != 0:
            raise RuntimeError("Native credential prompt failed")
        value = password.value.strip()
        if not value:
            raise ValueError("Credential must not be empty")
        return value
    finally:
        ctypes.memset(ctypes.addressof(password), 0, ctypes.sizeof(password))
        ctypes.memset(ctypes.addressof(username), 0, ctypes.sizeof(username))
