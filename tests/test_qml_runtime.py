from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_socket_wake_dispatches_restore_on_gui_thread():
    import threading
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from study_app.quick.runtime import WakeBridge
    from study_app.quick.self_test import wait_until

    app = QApplication.instance() or QApplication(["wake-affinity-test"])
    restored = []
    class Lifecycle:
        active = True
        quitting = False
        def restoreWindow(self):
            restored.append(QThread.currentThread())
    lifecycle = Lifecycle()
    wake = WakeBridge(lifecycle)
    thread = threading.Thread(target=wake.restoreRequested.emit)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert restored == []
    wait_until(lambda: bool(restored))
    assert restored == [app.thread()]
    lifecycle.quitting = True
    wake.restoreRequested.emit()
    app.processEvents()
    assert len(restored) == 1


def test_closing_during_first_load_keeps_guard_until_worker_finishes(tmp_path):
    """A second process must not enter schema initialization after early close."""
    legacy = tmp_path / "empty-legacy"
    legacy.mkdir()
    environment = dict(os.environ)
    environment.update(LEARNINGMODEL_DATA_ROOT=str(tmp_path / "user"),
                       LEARNINGMODEL_LEGACY_ROOT=str(legacy), QT_QPA_PLATFORM="offscreen")
    script = r'''
import threading
import time
from PySide6.QtCore import QTimer
from study_app.quick import runtime
from study_app import single_instance
finished = threading.Event()
started = threading.Event()
class Service:
    def load(self):
        started.set()
        time.sleep(0.25)
        finished.set()
        return {}
class Guard:
    def acquire(self):
        return True
    def release(self):
        assert started.is_set(), "worker never started"
        assert finished.is_set(), "guard released during initialization"
class Server:
    def __init__(self, callback):
        pass
    def start(self):
        return True
    def stop(self):
        pass
single_instance.SingleInstanceGuard = Guard
single_instance.WakeServer = Server
runtime.default_service = lambda: Service()
real_engine = runtime.create_engine
def engine_with_early_close(bridge):
    engine = real_engine(bridge)
    QTimer.singleShot(75, bridge.lifecycle.requestQuit)
    return engine
runtime.create_engine = engine_with_early_close
assert runtime.run_app() == 0
print("guard-drained-ok")
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], env=environment,
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "guard-drained-ok" in result.stdout
