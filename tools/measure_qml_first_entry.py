"""Capture presented geometry during first entry using an isolated profile copy."""
from __future__ import annotations

import argparse
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
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--routes", nargs="+", default=["plan", "knowledge", "assistant", "settings"])
    parser.add_argument("--preload", action="store_true")
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=options.output.resolve()) as temp:
        profile = Path(temp)
        os.environ["LEARNINGMODEL_DATA_ROOT"] = str(profile / "data")
        os.environ["LEARNINGMODEL_LEGACY_ROOT"] = str(profile / "legacy")
        os.environ["QT_QPA_PLATFORM"] = "windows"
        for name in ("learning_model_v1.json", "learning_records.json", "learning_app.sqlite"):
            shutil.copy2(options.backup / name, profile / name)
        from PySide6.QtCore import QPointF, QTimer, qVersion
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QTest
        from study_app.quick.bridge import DashboardBridge
        from study_app.quick.dashboard_service import DashboardService
        from study_app.quick.runtime import create_engine
        from study_app.quick.self_test import wait_until

        app = QGuiApplication([])
        app.setQuitOnLastWindowClosed(False)
        bridge = DashboardBridge(DashboardService(
            profile / "learning_app.sqlite", profile / "learning_model_v1.json",
            profile / "learning_records.json"), preload_pages=options.preload)
        reads = []
        for work in (bridge.workspace, bridge.insights, bridge.settings):
            execute = work._execute

            def tracked(action, *args, original_execute=execute):
                reads.append(action)
                return original_execute(action, *args)

            work._execute = tracked
        engine = create_engine(bridge)
        window = engine.rootObjects()[0]
        window.resize(1280, 850)
        window.requestActivate()
        names = {"homeOverview", "homeEmptyState", "homeTodayPlanCard", "workspaceTabs",
                 "workspacePaneLoader", "planningPane", "planScopeCard", "budgetTaskListCard",
                 "knowledgeContentStack", "knowledgeTopicDetail", "knowledgeGraphViewport", "preferencesPane",
                 "assistantWorkspaceScroll"}
        history, loads = {}, []
        bridge.workspace.planDataChanged.connect(lambda: loads.append("plan"))
        bridge.insights.knowledgeDataChanged.connect(lambda: loads.append("knowledge"))
        bridge.settings.dataChanged.connect(lambda: loads.append("settings"))
        route, start = "home", time.perf_counter()

        def walk(item):
            yield item
            if item.objectName() == "knowledgeGraphViewport":
                return  # Do not traverse every graph node on every sample.
            for child in item.childItems():
                yield from walk(child)

        preloader = next(item for item in walk(window.contentItem())
                         if item.objectName() == "startupPreloader")

        def presented_walk(item):
            # Retained pages can contain thousands of invisible items. Sampling
            # them during startup perturbs the workload being measured.
            if not item.isVisible() or item.opacity() == 0:
                return
            yield item
            if item.objectName() == "knowledgeGraphViewport":
                return
            for child in item.childItems():
                yield from presented_walk(child)

        def sample():
            values = {}
            for item in presented_walk(window.contentItem()):
                name = item.objectName()
                if name not in names or not item.isVisible() or item.opacity() == 0:
                    continue
                # Ancestor opacity is the first-presentation gate.
                parent, presented = item.parentItem(), True
                while parent:
                    if parent.opacity() == 0:
                        presented = False
                        break
                    parent = parent.parentItem()
                if not presented:
                    continue
                point = item.mapToScene(QPointF())
                values[name] = [round(v, 1) for v in
                                (point.x(), point.y(), item.width(), item.height())]
            frames = history.setdefault(route, [])
            if not frames or values != frames[-1]["geometry"]:
                frames.append({"ms": round((time.perf_counter() - start) * 1000, 1),
                               "geometry": values})

        timer = QTimer()
        timer.setInterval(40)
        timer.timeout.connect(sample)
        timer.start()

        def idle():
            return not any((bridge.busy, bridge.workspace.busy,
                            bridge.insights.busy, bridge.settings.busy))

        try:
            startup_started = time.perf_counter()
            bridge.refresh()
            assert QTest.qWaitForWindowExposed(window, 5000)
            if options.preload:
                QTest.qWait(50)
                window.grabWindow().save(str(options.output / "startup.png"))
            wait_until(lambda: bridge.hasData and idle(), timeout_ms=120000)
            if options.preload:
                wait_until(lambda: preloader.property("ready"),
                           timeout_ms=180000)
            startup_ms = round((time.perf_counter() - startup_started) * 1000, 1)
            startup_reads = list(reads)
            QTest.qWait(300)
            window.grabWindow().save(str(options.output / "home.png"))
            for route in options.routes:
                start = time.perf_counter()
                bridge.navigate(route)
                wait_until(idle, timeout_ms=120000)
                QTest.qWait(2000)
                sample()
                window.grabWindow().save(str(options.output / (route + ".png")))
                print(json.dumps({"route": route, "changes": history.get(route, [])}), flush=True)
            report = {"qt": qVersion(), "dpr": window.devicePixelRatio(),
                      "preload": options.preload, "startup_ms": startup_ms,
                      "startup_reads": startup_reads, "subsequent_reads": reads[len(startup_reads):],
                      "geometry": history, "data_deliveries": loads, "warnings": engine._qml_warnings}
            (options.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            assert not engine._qml_warnings
        finally:
            timer.stop()
            bridge.close()
            bridge.wait_for_shutdown()
            window.close()
            engine.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
