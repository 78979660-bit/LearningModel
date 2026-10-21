"""Isolated source entry-point comparison; never opens an existing profile.

Run separately with --mode quick and --mode widgets. Quick exercises the real
runtime, named mutex, wake listener and worker shutdown using a unique mutex and
an ephemeral localhost port, without disturbing an existing application.
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("quick", "widgets"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    faulthandler.dump_traceback_later(30, exit=True)
    report = {"mode": args.mode, "isolated": True, "external_requests": False}
    with tempfile.TemporaryDirectory(prefix="phase5-entry-") as temporary:
        root = Path(temporary)
        legacy = root / "empty-legacy"
        legacy.mkdir()
        os.environ["LEARNINGMODEL_DATA_ROOT"] = str(root / "user")
        os.environ["LEARNINGMODEL_LEGACY_ROOT"] = str(legacy)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication
        from study_app import single_instance

        unique_name = "Local\\LearningModel.Phase5." + uuid.uuid4().hex
        guard_type = single_instance.SingleInstanceGuard
        single_instance.SingleInstanceGuard = lambda: guard_type(unique_name)
        with socket.socket() as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            single_instance.WAKE_PORT = port_probe.getsockname()[1]
        errors = []
        started_at = time.monotonic()

        if args.mode == "quick":
            from study_app.quick import runtime
            actual_create_engine = runtime.create_engine
            child_results = []
            worker = None

            def create_engine(bridge):
                engine = actual_create_engine(bridge)
                window = engine.rootObjects()[0]
                timer = QTimer(bridge)
                engine._phase5_timer = timer
                timer.setInterval(30)
                report["heartbeats"] = 0
                phase = 0

                def poll():
                    nonlocal phase, worker
                    report["heartbeats"] += 1
                    if report["heartbeats"] == 1:
                        print("entry-probe: first heartbeat", flush=True)
                    try:
                        if time.monotonic() - started_at > 20:
                            raise TimeoutError("source entry-point probe timed out")
                        if phase == 0 and bridge.hasData and not bridge.busy:
                            assert not engine._qml_warnings
                            assert "PySide6.QtWidgets" not in sys.modules
                            report["gui"] = type(QGuiApplication.instance()).__name__
                            report["tray_registered"] = bridge.lifecycle.trayAvailable
                            report["device_pixel_ratio"] = window.devicePixelRatio()
                            print("entry-probe: real data ready, hiding main", flush=True)
                            window.close()
                            assert not window.isVisible()
                            phase = 1

                            def second_process():
                                code = ("import sys; from study_app.single_instance import SingleInstanceGuard; "
                                        "guard=SingleInstanceGuard(sys.argv[1]); "
                                        "assert not guard.acquire(); print('guard-blocked')")
                                result = subprocess.run([sys.executable, "-B", "-c", code, unique_name],
                                                        cwd=ROOT, capture_output=True, text=True, timeout=10)
                                child_results.append(result)
                                if result.returncode == 0:
                                    single_instance.send_wake_signal()

                            worker = threading.Thread(target=second_process)
                            worker.start()
                        elif phase == 1 and child_results:
                            child = child_results[0]
                            assert child.returncode == 0, child.stderr
                            if window.isVisible():
                                print("entry-probe: wake restored main, requesting quit", flush=True)
                                report["second_process_blocked"] = True
                                report["wake_restored_main"] = True
                                phase = 2
                                timer.stop()
                                bridge.lifecycle.requestQuit()
                                print("entry-probe: quit dispatched", bridge.lifecycle.quitting, flush=True)
                    except Exception as error:
                        print("entry-probe: failed", type(error).__name__, str(error), flush=True)
                        errors.append(type(error).__name__ + ": " + str(error))
                        timer.stop()
                        bridge.lifecycle.requestQuit()

                timer.timeout.connect(poll)
                timer.start()
                return engine

            runtime.create_engine = create_engine
            assert runtime.run_app() == 0
            if worker is not None:
                worker.join(15)
                assert not worker.is_alive()
            # The real guard must be released only after runtime has drained.
            guard = guard_type(unique_name)
            assert guard.acquire()
            guard.release()
            report["guard_released_after_shutdown"] = True
        else:
            from study_app.ui import app_runtime
            actual_factory = app_runtime.create_main_window
            app_runtime.SingleInstanceGuard = lambda: guard_type(unique_name)

            def create_window(state):
                window = actual_factory(state)
                def check():
                    try:
                        assert window.isVisible()
                        assert window.stack.count() == 5
                        for index in range(5):
                            window.stack.setCurrentIndex(index)
                        report["gui"] = type(QGuiApplication.instance()).__name__
                        report["pages"] = window.stack.count()
                        report["device_pixel_ratio"] = window.devicePixelRatioF()
                    except Exception as error:
                        errors.append(type(error).__name__ + ": " + str(error))
                    finally:
                        window.quit_application()
                QTimer.singleShot(500, check)
                return window

            app_runtime.create_main_window = create_window
            try:
                app_runtime.run_app()
            except SystemExit as exit_result:
                assert exit_result.code == 0
        if errors:
            raise RuntimeError(" | ".join(errors))
        report["status"] = "ok"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
