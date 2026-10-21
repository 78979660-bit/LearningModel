from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description="和纸学习室 Qt Quick 迁移入口")
    parser.add_argument("--data-root", type=Path, help="显式用户数据根；需在 Qt/服务导入前设置")
    parser.add_argument("--self-test", type=Path, metavar="REPORT", help="用自动创建的隔离数据运行启动自检")
    options = parser.parse_args()
    if options.self_test is not None:
        # Self-test always overrides inherited profiles, before importing paths.
        with tempfile.TemporaryDirectory(prefix="learning-qml-selftest-") as temporary:
            root = Path(temporary)
            legacy = root / "empty-legacy"
            legacy.mkdir()
            os.environ["LEARNINGMODEL_DATA_ROOT"] = str(root / "user")
            os.environ["LEARNINGMODEL_LEGACY_ROOT"] = str(legacy)
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtGui import QGuiApplication
            from study_app.quick.self_test import run_probe
            app = QGuiApplication(["LearningModel", "--qml-self-test"])
            report = run_probe(app, isolated_fixture=True)
            destination = options.self_test.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    if options.data_root is not None:
        os.environ["LEARNINGMODEL_DATA_ROOT"] = str(options.data_root.resolve())
    from study_app.paths import RUNTIME_LOG_PATH, ensure_user_directories
    ensure_user_directories()
    logging.basicConfig(filename=RUNTIME_LOG_PATH, level=logging.INFO, encoding="utf-8")
    from study_app.quick.runtime import run_app
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
