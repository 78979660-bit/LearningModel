"""Native, isolated route/motion probe; GUI gaps are not GPU frame times.

Use --backup to copy an existing closed-profile backup into a temporary folder.
The default uses the small test fixture. Neither mode opens the live profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--qml-root", type=Path, help="Optional saved QML snapshot for before/after measurements")
    parser.add_argument("--rounds", type=int, default=3)
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="profile-", dir=options.output.resolve()) as temp:
        profile = Path(temp)
        os.environ["LEARNINGMODEL_DATA_ROOT"] = str(profile / "data")
        os.environ["LEARNINGMODEL_LEGACY_ROOT"] = str(profile / "legacy")
        os.environ["QT_QPA_PLATFORM"] = "windows"
        from PySide6.QtCore import QTimer, qVersion
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QTest
        from study_app.data.database import ensure_seeded_database
        from study_app.quick.bridge import DashboardBridge
        from study_app.quick.dashboard_service import DashboardService
        from study_app.quick import runtime, resources
        qml_source = options.qml_root.resolve() if options.qml_root else resources.qml_root()
        if options.qml_root:
            resources.qml_root = lambda: qml_source
            runtime.qml_root = lambda: qml_source
        create_engine = runtime.create_engine
        from study_app.quick.self_test import wait_until

        def items(item):
            yield item
            for child in item.childItems():
                yield from items(child)

        model, records, database = [profile / name for name in (
            "learning_model_v1.json", "learning_records.json", "learning_app.sqlite")]
        if options.backup:
            for path in (model, records, database):
                shutil.copy2(options.backup / path.name, path)
        else:
            shutil.copy2(ROOT / "tests/fixtures/minimal_model.json", model)
            records.write_text('{"records": []}', encoding="utf-8")
            ensure_seeded_database(database, model_path=model)
        app = QGuiApplication([])
        app.setQuitOnLastWindowClosed(False)
        bridge = DashboardBridge(DashboardService(database, model, records))
        engine = create_engine(bridge)
        window = engine.rootObjects()[0]
        window.resize(1280, 850)
        window.requestActivate()
        assert QTest.qWaitForWindowExposed(window, 5000)
        gaps, frames = [], []
        previous = time.perf_counter()

        def beat():
            nonlocal previous
            now = time.perf_counter()
            gaps.append((now - previous) * 1000)
            previous = now

        timer = QTimer()
        timer.setInterval(8)
        timer.timeout.connect(beat)
        timer.start()
        window.frameSwapped.connect(lambda: frames.append(time.perf_counter()))

        def reset():
            nonlocal previous
            gaps.clear()
            frames.clear()
            previous = time.perf_counter()

        def stats():
            ordered = sorted(gaps)
            return {"max_gui_gap_ms": round(max(gaps, default=0), 2),
                    "p95_gui_gap_ms": round(ordered[int((len(ordered)-1)*.95)], 2) if ordered else 0,
                    "gui_gap_samples": len(gaps), "delivered_frames": len(frames)}

        def idle():
            return not any((bridge.busy, bridge.workspace.busy, bridge.insights.busy, bridge.settings.busy))

        rows, motions, tabs = [], [], []
        try:
            bridge.refresh()
            wait_until(lambda: bridge.hasData and idle(), timeout_ms=120000)
            QTest.qWait(300)
            for lap in range(options.rounds):
                for route in ("plan", "knowledge", "assistant", "settings", "home"):
                    reset()
                    start = time.perf_counter()
                    bridge.navigate(route)
                    dispatch = (time.perf_counter() - start) * 1000
                    wait_until(lambda: any(i.objectName() == route + "Page" and i.isVisible()
                                            for i in items(window.contentItem())), timeout_ms=120000)
                    wait_until(lambda: all(i.property("pagePresented") is not False
                                           for i in items(window.contentItem())
                                           if i.objectName() == "pageLoader"), timeout_ms=120000)
                    if route == "plan":
                        wait_until(lambda: any(i.objectName() == "workspacePaneLoader" and i.property("paneReady")
                                                and i.property("panePresented") is not False
                                                for i in items(window.contentItem())), timeout_ms=120000)
                    ready = (time.perf_counter() - start) * 1000
                    wait_until(idle, timeout_ms=120000)
                    QTest.qWait(300)
                    row = {"round": lap, "route": route, "dispatch_ms": round(dispatch, 2),
                           "controls_ready_ms": round(ready, 2), **stats()}
                    rows.append(row)
                    print(json.dumps(row), flush=True)
                # Measure on the most involved page, with all retained pages loaded.
                bridge.navigate("plan")
                QTest.qWait(200)
                plan_page = next(i for i in items(window.contentItem()) if i.objectName() == "planPage")
                for section in (1, 2, 3, 0, 1, 2, 3, 0):
                    reset()
                    start = time.perf_counter()
                    plan_page.setProperty("section", section)
                    dispatch = (time.perf_counter() - start) * 1000
                    wait_until(lambda: any(i.objectName() == "workspacePaneLoader" and i.property("paneReady")
                                            and i.property("panePresented") is not False
                                            for i in items(window.contentItem())), timeout_ms=120000)
                    ready = (time.perf_counter() - start) * 1000
                    QTest.qWait(150)
                    row = {"round": lap, "section": section, "dispatch_ms": round(dispatch, 2),
                           "controls_ready_ms": round(ready, 2), **stats()}
                    tabs.append(row)
                    print(json.dumps({"tab": row}), flush=True)
                pane = next(i for i in items(window.contentItem()) if i.objectName() == "planningPane")
                widths = []
                callback = lambda: widths.append(pane.width())
                pane.widthChanged.connect(callback)
                reset()
                for _ in range(4):
                    bridge.toggleSidebar()
                    QTest.qWait(320)
                pane.widthChanged.disconnect(callback)
                row = {"round": lap, "page_width_updates": len(widths), **stats()}
                motions.append(row)
                print(json.dumps({"sidebar": row}), flush=True)
            for route in ("home", "plan", "knowledge", "assistant", "settings"):
                bridge.navigate(route)
                wait_until(idle, timeout_ms=120000)
                QTest.qWait(300)
                window.grabWindow().save(str(options.output / (route + ".png")))
            report = {"python": sys.version, "qt": qVersion(), "platform": app.platformName(),
                      "dpr": window.devicePixelRatio(), "data": "backup copy" if options.backup else "minimal fixture",
                      "routes": rows, "sidebar": motions, "tabs": tabs, "warnings": engine._qml_warnings,
                      "source_sha256": {str(p.relative_to(qml_source)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in qml_source.rglob("*.qml")},
                      "note": "8ms GUI timer and queued frameSwapped delivery; not GPU frame duration or a FPS guarantee."}
            (options.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            assert not engine._qml_warnings, engine._qml_warnings
        finally:
            timer.stop()
            bridge.close()
            bridge.wait_for_shutdown()
            window.close()
            engine.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
