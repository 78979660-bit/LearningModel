from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from study_app.paths import DATA_DIR, TESSDATA_DIR


@dataclass(frozen=True)
class TesseractRuntime:
    executable: Path
    tessdata: Path
    language: str


def _tesseract_executable_candidates() -> tuple[Path, ...]:
    discovered = shutil.which("tesseract")
    candidates = (
        DATA_DIR / "tesseract" / "tesseract.exe",
        Path(discovered) if discovered else None,
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    )
    return tuple(dict.fromkeys(path for path in candidates if path is not None and path.is_file()))


def find_tesseract_executable() -> Path | None:
    """Find an installed executable; runtime resolution also checks language data."""
    return next(iter(_tesseract_executable_candidates()), None)


def resolve_tesseract_runtime(executable: Path | None = None) -> TesseractRuntime:
    """Select the same language data for image and PDF OCR without running OCR."""
    executables = (executable,) if executable is not None else _tesseract_executable_candidates()
    if not executables:
        raise RuntimeError("未找到本地 Tesseract 可执行文件")

    configured_prefix = os.environ.get("TESSDATA_PREFIX", "").strip()
    for candidate_executable in executables:
        candidates = [TESSDATA_DIR]
        if configured_prefix:
            prefix = Path(configured_prefix)
            candidates.extend((prefix, prefix / "tessdata"))
        candidates.append(candidate_executable.parent / "tessdata")
        for tessdata in dict.fromkeys(candidates):
            if (tessdata / "chi_sim.traineddata").is_file():
                language = "chi_sim+eng" if (tessdata / "eng.traineddata").is_file() else "chi_sim"
                return TesseractRuntime(candidate_executable, tessdata, language)
    raise RuntimeError("缺少简体中文 OCR 语言包 chi_sim")


def detect_optional_capabilities() -> dict[str, dict[str, object]]:
    tesseract = find_tesseract_executable()
    runtime = None
    runtime_error = ""
    if tesseract is not None:
        try:
            runtime = resolve_tesseract_runtime()
        except RuntimeError as error:
            runtime_error = str(error)
    selected_executable = runtime.executable if runtime is not None else tesseract

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    chatgpt_candidates = (
        local_app_data / "OpenAI" / "ChatGPT" / "ChatGPT.exe",
        local_app_data / "Programs" / "ChatGPT" / "ChatGPT.exe",
    )
    chatgpt = next((path for path in chatgpt_candidates if path.is_file()), None)

    return {
        "ocr": {
            "available": runtime is not None,
            "executable_found": tesseract is not None,
            "language_data_found": runtime is not None,
            "runtime_ready": None if runtime is not None else False,
            "executable": str(selected_executable) if selected_executable is not None else "",
            "language": runtime.language if runtime is not None else "",
            "tessdata_dir": str(runtime.tessdata) if runtime is not None else "",
            "detail": (
                f"已找到 Tesseract 可执行文件：{selected_executable}；已检测到 {runtime.language} 语言包，实际执行尚未验证"
                if runtime is not None
                else f"已找到 Tesseract 可执行文件：{tesseract}；{runtime_error}，OCR 功能不可用"
                if tesseract is not None
                else "未检测到 Tesseract 可执行文件；OCR 功能不可用"
            ),
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
