"""Open the full Quick app on a fresh, non-sensitive SQLite review fixture.

This is a visual/interaction probe, not a release acceptance substitute. No
credentials or network calls are configured. The report and optional screenshots
are retained in a new directory; every shutdown drains background tasks.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--capture", action="store_true", help="Capture five routes automatically, then exit")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=820)
    options = parser.parse_args()
    if not 1 <= options.seconds <= 1800:
        parser.error("seconds must be 1..1800")
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "legacy").mkdir()
    os.environ["LEARNINGMODEL_DATA_ROOT"] = str(output / "profile")
    os.environ["LEARNINGMODEL_LEGACY_ROOT"] = str(output / "legacy")
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
    from PySide6.QtGui import QGuiApplication
    from study_app.data.database import ensure_seeded_database, add_learning_record
    from study_app.data.practice_repository import ensure_practice_templates_seeded
    from study_app.quick.bridge import DashboardBridge
    from study_app.quick.dashboard_service import DashboardService
    from study_app.quick.plan_service import PlanService
    from study_app.quick.runtime import create_engine
    from study_app.quick.self_test import wait_until

    today = date.today()
    model = {"model_name": "isolated-visual-review", "scale": "0-1", "subjects": [],
             "initial_percent_assessment": {"subjects": []},
             "warning_policy": {"bkt_model": {"enabled": True}}}
    for index, (subject, module, topic) in enumerate((
        ("数学验收", "逻辑基础", "命题与证明"),
        ("程序设计验收", "数据结构", "递归与树"),
        ("物理验收", "基础力学", "运动与能量"),
    )):
        model["subjects"].append({"name": subject, "weight": 1, "status": "learning",
            "modules": [{"name": module, "weight": 1, "status": "learning", "mastery": .25 + .15 * index,
                         "topics": [{"name": topic, "status": "learning", "mastery": .25 + .15 * index,
                                     "importance": 1, "difficulty": .5, "forgetting_risk": .5}]}]})
    model_path, records_path, db_path = output / "model.json", output / "records.json", output / "review.sqlite"
    model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    records_path.write_text('{"records": []}', encoding="utf-8")
    ensure_seeded_database(db_path, model_path=model_path)
    ensure_practice_templates_seeded(db_path)
    for subject in model["subjects"]:
        module = subject["modules"][0]
        add_learning_record({"date": today.isoformat(), "subject": subject["name"], "module": module["name"],
            "topic": module["topics"][0]["name"], "activity": "review", "source": "outside_class",
            "duration_minutes": 15, "score": 60, "note": "非敏感隔离视觉验收记录"},
            db_path=db_path, model_path=model_path)
    plans = PlanService(db_path, model_path, records_path, today=today)
    preview = plans.preview_budget({"budget_minutes": 120})
    plans.confirm_budget(preview["token"])

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QGuiApplication(["Washi visual review"])
    bridge = DashboardBridge(DashboardService(db_path, model_path, records_path, today=today.isoformat()))
    engine = None
    report = {"isolated": True, "external_requests": False, "platform": app.platformName()}
    try:
        engine = create_engine(bridge)
        window = engine.rootObjects()[0]
        window.resize(options.width, options.height)
        window.requestActivate()
        report["dpr"] = window.devicePixelRatio()
        bridge.refresh()
        wait_until(lambda: bridge.hasData and not bridge.busy)
        report["record_count"] = bridge.snapshot["recordCount"]
        report["today_plan_status"] = bridge.snapshot["todayPlan"]["status"]
        if options.capture:
            from PySide6.QtTest import QTest
            for route in bridge.routes:
                bridge.navigate(route["key"])
                wait_until(lambda: not bridge.busy and not bridge.workspace.busy and not bridge.insights.busy and not bridge.settings.busy)
                QTest.qWait(500)
                assert window.grabWindow().save(str(output / (route["key"] + ".png")))
            bridge.navigate("home")
            bridge.toggleSidebar()
            QTest.qWait(300)
            assert window.grabWindow().save(str(output / "home-collapsed.png"))
        else:
            QTimer.singleShot(options.seconds * 1000, app.quit)
            app.exec()
        report["warnings"] = list(engine._qml_warnings)
        report["status"] = "ok" if not report["warnings"] else "qml-warning"
    finally:
        bridge.close()
        bridge.wait_for_shutdown()
        if engine is not None:
            for window in engine.rootObjects():
                window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        report["workers_drained"] = True
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
