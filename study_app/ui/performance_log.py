"""Opt-in local event-loop latency log (no user content or credentials)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from time import perf_counter


def install_performance_log(app):
    destination = os.environ.get("STUDY_APP_PERF_LOG")
    if not destination:
        return
    from PySide6.QtCore import QTimer

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("study_app.ui")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    last = [None]
    timer = QTimer(app)
    timer.setInterval(16)

    def sample():
        now = perf_counter()
        delay = (now - last[0]) * 1000 - 16 if last[0] is not None else 0
        last[0] = now
        if delay >= 50:
            logger.info("event_loop_lag_ms=%.1f", delay)

    timer.timeout.connect(sample)
    timer.start()
    app._performance_timer = timer
    logger.info("performance_monitor_started interval_ms=16 threshold_ms=50")

    def close():
        timer.stop()
        logger.removeHandler(handler)
        handler.close()

    app.aboutToQuit.connect(close)
