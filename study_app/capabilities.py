from __future__ import annotations

import os
import shutil
from pathlib import Path

from study_app.paths import DATA_DIR


def detect_optional_capabilities() -> dict[str, dict[str, object]]:
    managed_tesseract = DATA_DIR / "tesseract" / "tesseract.exe"
    tesseract = managed_tesseract if managed_tesseract.is_file() else None
    if tesseract is None:
        discovered = shutil.which("tesseract")
        tesseract = Path(discovered) if discovered else None

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    chatgpt_candidates = (
        local_app_data / "OpenAI" / "ChatGPT" / "ChatGPT.exe",
        local_app_data / "Programs" / "ChatGPT" / "ChatGPT.exe",
    )
    chatgpt = next((path for path in chatgpt_candidates if path.is_file()), None)

    return {
        "ocr": {
            "available": tesseract is not None,
            "detail": str(tesseract) if tesseract is not None else "未检测到 Tesseract；OCR 功能不可用",
        },
        "chatgpt_desktop": {
            "available": chatgpt is not None,
            "detail": str(chatgpt) if chatgpt is not None else "未检测到 ChatGPT 桌面应用；桌面桥接功能不可用",
        },
        "network_services": {
            "available": None,
            "detail": "启动时不联网探测；核心学习流程可离线使用",
        },
    }

