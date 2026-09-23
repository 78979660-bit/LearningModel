from __future__ import annotations

import os
import threading
import time
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from study_app.core.dashboard import DashboardState


LOCAL = {"overview": ["本地概况"], "risks": [], "actions": [], "source": ["本地"]}
LLM = {"overview": ["LLM 概况"], "risks": [], "actions": [], "source": ["LLM"]}


def _state():
    return DashboardState(
        start=date(2026, 9, 13), today=date(2026, 9, 15), benchmark=55,
        subjects=(), todos=(), memory_risks=(), bkt_alerts=(), raw_records=(),
    )


def _body(card) -> str:
    return "\n".join(label.text() for label in card._daily_summary_widgets)


def _pump_until(application, predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("daily summary did not reach expected UI state")


def _patch_inputs(monkeypatch, module, daily_summary):
    from study_app.ai import providers

    monkeypatch.setattr(daily_summary, "local_daily_summary", lambda *_args: LOCAL)
    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _feature: True)
    monkeypatch.setattr(module, "get_setting", lambda *_args: None)
    monkeypatch.setattr(module, "daily_summary_cache_signature", lambda *_args: "sig")
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)


def test_slow_summary_keeps_ui_responsive_and_caches_only_after_success(monkeypatch):
    from study_app.ai import daily_summary
    from study_app.ui import dashboard_widgets

    application = QApplication.instance() or QApplication([])
    started = threading.Event()
    release = threading.Event()
    calls = []
    writes = []

    def slow(*_args):
        calls.append(threading.get_ident())
        started.set()
        release.wait(1)
        return LLM

    _patch_inputs(monkeypatch, dashboard_widgets, daily_summary)
    monkeypatch.setattr(daily_summary, "generate_daily_summary_with_llm", slow)
    monkeypatch.setattr(dashboard_widgets, "set_setting", lambda *args: writes.append(args))
    card = dashboard_widgets.daily_summary_card(_state())
    try:
        card._daily_summary_handler()
        assert started.wait(1)
        card._daily_summary_handler()
        marker = []
        QTimer.singleShot(0, lambda: marker.append("responsive"))
        application.processEvents()
        assert marker == ["responsive"]
        assert len(calls) == 1 and calls[0] != threading.get_ident()
        assert writes == []
        assert "本地概况" in _body(card)
        release.set()
        _pump_until(application, lambda: "LLM 概况" in _body(card))
        assert writes == [(
            "daily_summary_cache:2026-09-15",
            {"mode": "llm", "summary": LLM, "signature": "sig"},
        )]
    finally:
        release.set()
        card.close()
        application.processEvents()


def test_failure_and_timeout_preserve_local_summary_without_cache_write(monkeypatch):
    from study_app.ai import daily_summary
    from study_app.ui import dashboard_widgets

    application = QApplication.instance() or QApplication([])
    _patch_inputs(monkeypatch, dashboard_widgets, daily_summary)
    writes = []
    monkeypatch.setattr(dashboard_widgets, "set_setting", lambda *args: writes.append(args))

    def failure(*_args):
        raise RuntimeError("offline")

    monkeypatch.setattr(daily_summary, "generate_daily_summary_with_llm", failure)
    failed = dashboard_widgets.daily_summary_card(_state())
    try:
        failed._daily_summary_handler()
        _pump_until(application, lambda: failed._daily_summary_lifecycle["handle"] is None)
        assert "本地概况" in _body(failed)
        assert writes == []
    finally:
        failed.close()

    release = threading.Event()
    monkeypatch.setattr(dashboard_widgets, "DAILY_SUMMARY_TASK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(
        daily_summary, "generate_daily_summary_with_llm",
        lambda *_args: (release.wait(1), LLM)[1],
    )
    timed_out = dashboard_widgets.daily_summary_card(_state())
    try:
        timed_out._daily_summary_handler()
        _pump_until(application, lambda: timed_out._daily_summary_lifecycle["handle"] is None)
        assert "本地概况" in _body(timed_out)
        assert "LLM 概况" not in _body(timed_out)
        assert writes == []
        release.set()
        time.sleep(0.04)
        application.processEvents()
        assert writes == []
    finally:
        release.set()
        timed_out.close()
        application.processEvents()


def test_card_close_discards_late_summary_without_cache_or_control_update(monkeypatch):
    from study_app.ai import daily_summary
    from study_app.ui import dashboard_widgets

    application = QApplication.instance() or QApplication([])
    started = threading.Event()
    release = threading.Event()
    writes = []

    def slow(*_args):
        started.set()
        release.wait(1)
        return LLM

    _patch_inputs(monkeypatch, dashboard_widgets, daily_summary)
    monkeypatch.setattr(daily_summary, "generate_daily_summary_with_llm", slow)
    monkeypatch.setattr(dashboard_widgets, "set_setting", lambda *args: writes.append(args))
    card = dashboard_widgets.daily_summary_card(_state())
    try:
        card._daily_summary_handler()
        assert started.wait(1)
        original = _body(card)
        card.close()
        assert not card._daily_summary_lifecycle["open"]
        release.set()
        time.sleep(0.04)
        application.processEvents()
        assert _body(card) == original
        assert writes == []
    finally:
        release.set()
        card.close()
        application.processEvents()


def test_cache_write_failure_does_not_replace_local_summary(monkeypatch):
    from study_app.ai import daily_summary
    from study_app.ui import dashboard_widgets

    application = QApplication.instance() or QApplication([])
    _patch_inputs(monkeypatch, dashboard_widgets, daily_summary)
    monkeypatch.setattr(daily_summary, "generate_daily_summary_with_llm", lambda *_args: LLM)

    def fail_write(*_args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(dashboard_widgets, "set_setting", fail_write)
    card = dashboard_widgets.daily_summary_card(_state())
    try:
        card._daily_summary_handler()
        _pump_until(application, lambda: card._daily_summary_lifecycle["handle"] is None)
        assert "本地概况" in _body(card)
        assert "LLM 概况" not in _body(card)
    finally:
        card.close()
        application.processEvents()
