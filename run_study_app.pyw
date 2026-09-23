from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _self_test_output(arguments: list[str]) -> Path | None:
    if "--self-test" not in arguments:
        return None
    index = arguments.index("--self-test")
    if index + 1 < len(arguments) and not arguments[index + 1].startswith("--"):
        return Path(arguments[index + 1]).expanduser().resolve()
    from study_app.paths import CACHE_DIR

    return CACHE_DIR / "release-self-test.json"


def _configure_runtime() -> None:
    from study_app.app_metadata import APP_DISPLAY_NAME, APP_VERSION
    from study_app.paths import (
        PERFORMANCE_LOG_PATH,
        RUNTIME_LOG_PATH,
        ensure_user_directories,
    )

    ensure_user_directories()
    os.environ.setdefault("STUDY_APP_PERF_LOG", str(PERFORMANCE_LOG_PATH))
    logging.basicConfig(
        filename=RUNTIME_LOG_PATH,
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger(__name__).info("Starting %s %s", APP_DISPLAY_NAME, APP_VERSION)


def _run_self_test(destination: Path) -> None:
    import importlib

    from study_app.app_metadata import APP_VERSION
    from study_app.capabilities import detect_optional_capabilities
    from study_app.data.database import (
        APPLICATION_SCHEMA_VERSION,
        DEFAULT_DB_PATH,
        ensure_seeded_database,
        get_counts,
    )
    from study_app.data.practice_repository import ensure_practice_bank_seeded
    from study_app.paths import MODEL_PATH, RECORDS_PATH, USER_ROOT

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    smoke_modules = (
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PIL.Image",
        "fitz",
        "pymupdf",
        "pypdf",
        "pytesseract",
        "reportlab.pdfgen.canvas",
        "study_app.core.subject_pdf_pipeline",
        "study_app.ui.main_window",
    )
    for module_name in smoke_modules:
        importlib.import_module(module_name)
    from PySide6.QtWidgets import QApplication

    qt_application = QApplication.instance() or QApplication(
        ["LearningModel", "--self-test"]
    )
    qt_application.processEvents()

    ensure_seeded_database()
    ensure_practice_bank_seeded()
    payload = {
        "status": "ok",
        "version": APP_VERSION,
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "user_root": str(USER_ROOT),
        "database": str(DEFAULT_DB_PATH),
        "model_exists": MODEL_PATH.is_file(),
        "records_exists": RECORDS_PATH.is_file(),
        "counts": get_counts(),
        "capabilities": detect_optional_capabilities(),
        "module_smoke": list(smoke_modules),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _show_startup_error() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "学习模型启动失败。诊断信息已写入用户日志目录。",
            "学习模型",
            0x10,
        )
    except Exception:
        pass


def _write_startup_failure() -> None:
    import tempfile

    from study_app.paths import LAUNCHER_LOG_PATH

    report = traceback.format_exc() + "\n"
    candidates = (
        LAUNCHER_LOG_PATH,
        Path(tempfile.gettempdir()) / "LearningModel-startup-error.log",
    )
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as log:
                log.write(report)
            return
        except OSError:
            continue


def main() -> None:
    self_test = None
    try:
        _configure_runtime()
        self_test = _self_test_output(sys.argv[1:])
        if self_test is not None:
            _run_self_test(self_test)
            return

        from study_app.ui.app_runtime import run_app

        run_app()
    except Exception:
        try:
            _write_startup_failure()
        finally:
            if self_test is None:
                _show_startup_error()
        raise


if __name__ == "__main__":
    main()
