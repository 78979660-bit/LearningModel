"""Self-test must drain file-using workers before its isolated profile is removed."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from study_app.quick import self_test


def test_timeout_waits_for_worker_before_temporary_profile_cleanup(monkeypatch):
    app = QApplication.instance() or QApplication(["qml-probe-tests"])
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    drained = threading.Event()
    real_wait = self_test.wait_until
    real_shutdown = self_test.DashboardBridge.wait_for_shutdown

    def short_deadline(predicate):
        # The worker is deliberately unfinished when the real event-loop deadline expires.
        real_wait(started.is_set, timeout_ms=2000)
        real_wait(predicate, timeout_ms=50)

    def shutdown(bridge):
        drained.set()
        release.set()
        real_shutdown(bridge)

    monkeypatch.setattr(self_test, "wait_until", short_deadline)
    monkeypatch.setattr(self_test.DashboardBridge, "wait_for_shutdown", shutdown)
    with tempfile.TemporaryDirectory(prefix="qml-probe-timeout-") as directory:
        profile = Path(directory)

        class Service:
            def load(self):
                with (profile / "initializing.sqlite").open("wb") as stream:
                    started.set()
                    if not release.wait(5):
                        raise RuntimeError("test worker was not released")
                    stream.write(b"worker completed before cleanup")
                finished.set()
                return {}

        try:
            with pytest.raises(TimeoutError, match="did not become ready"):
                self_test.run_probe(app, Service())
            assert started.is_set(), "timeout did not overlap a running worker"
            assert drained.is_set(), "probe returned without waiting for shutdown"
            assert finished.is_set(), "probe returned while a profile file was still open"
            assert (profile / "initializing.sqlite").read_bytes() == b"worker completed before cleanup"
        finally:
            # Also allow the unfixed implementation to fail without leaking a worker/file.
            release.set()
            assert finished.wait(5)
    assert not profile.exists()


def test_engine_startup_failure_also_closes_and_drains_bridge(monkeypatch):
    app = QApplication.instance() or QApplication(["qml-probe-tests"])
    real_bridge = self_test.DashboardBridge
    bridges = []

    class Bridge(real_bridge):
        def __init__(self, service, *, preload_pages=False):
            super().__init__(service, preload_pages=preload_pages)
            self.drained = False
            bridges.append(self)

        def wait_for_shutdown(self):
            super().wait_for_shutdown()
            self.drained = True

    def fail_engine(bridge):
        raise RuntimeError("injected engine startup failure")

    monkeypatch.setattr(self_test, "DashboardBridge", Bridge)
    monkeypatch.setattr(self_test, "create_engine", fail_engine)
    with pytest.raises(RuntimeError, match="injected engine startup failure"):
        self_test.run_probe(app, object())
    assert len(bridges) == 1
    assert bridges[0]._closed and bridges[0].drained
