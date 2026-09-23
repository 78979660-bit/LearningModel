from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes


DPAPI_PREFIX = "dpapi-v1:"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"LearningModel:llm-api-key:v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


def _require_windows() -> None:
    if not sys.platform.startswith("win"):
        raise RuntimeError("DPAPI 凭据保护仅在 Windows 上可用")


def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    _require_windows()
    value_blob, value_buffer = _blob(secret.encode("utf-8"))
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptProtectData(
        ctypes.byref(value_blob),
        "LearningModel credential",
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    del value_buffer, entropy_buffer
    if not success:
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
    return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(protected: str) -> str:
    if not protected:
        return ""
    _require_windows()
    if not protected.startswith(DPAPI_PREFIX):
        raise ValueError("不支持的凭据格式")
    encrypted = base64.b64decode(
        protected.removeprefix(DPAPI_PREFIX).encode("ascii"), validate=True
    )
    value_blob, value_buffer = _blob(encrypted)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptUnprotectData(
        ctypes.byref(value_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    del value_buffer, entropy_buffer
    if not success:
        raise ctypes.WinError()
    try:
        clear = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
    return clear.decode("utf-8")

