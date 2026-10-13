"""Load the actual QML window and worker-backed data, including frozen resources."""
from __future__ import annotations

from datetime import date
import json

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtQuick import QQuickWindow

from study_app.quick.bridge import DashboardBridge
from study_app.quick.resources import validate_resources
from study_app.quick.runtime import create_engine, default_service
from study_app.quick.startup_diagnostics import inspect_runtime
from study_app.quick.lifecycle import LifecycleController


def _visual_item(window, name):
    def walk(item):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(window.contentItem())


def wait_until(predicate, timeout_ms=15000):
    if predicate():
        return
    loop = QEventLoop()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.start(timeout_ms)
    poll.start()
    loop.exec()
    poll.stop()
    deadline.stop()
    if not predicate():
        raise TimeoutError("QML startup/data did not become ready")


def _seed_isolated_probe(service):
    """Called only by the temporary-profile self-test entry points, on a worker."""
    from study_app.data.database import connect, get_counts, import_model_json, add_learning_record

    service.ensure_initialized()
    counts = get_counts(service.db_path)
    if counts.get("subjects", 0) or counts.get("learning_records", 0):
        raise RuntimeError("Startup fixture requires an empty isolated database")
    today = date.today().isoformat()
    model = {
        "model_name": "isolated-qml-startup-probe", "scale": "0-1",
        "initial_percent_assessment": {"subjects": []},
        "warning_policy": {"bkt_model": {"enabled": True}},
        "subjects": [{"name": "启动自检学科", "weight": 1, "status": "learning", "modules": [
            {"name": name, "weight": 1, "mastery": 0.25, "status": "learning", "topics": [
                {"name": topic, "status": "learning", "mastery": 0.25, "importance": 1,
                 "difficulty": 0.5, "forgetting_risk": 0.2}]} for name, topic in
            (("自检基础章节", "自检基础知识点"), ("自检进阶章节", "自检进阶知识点"))]}],
    }
    service.model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    with connect(service.db_path) as connection:
        import_model_json(connection, model)
    add_learning_record({"date": today, "subject": "启动自检学科", "module": "自检基础章节",
                         "topic": "自检基础知识点", "activity": "review", "source": "outside_class",
                         "duration_minutes": 15}, db_path=service.db_path, model_path=service.model_path)


def run_probe(application, service=None, *, isolated_fixture=False) -> dict:
    service = service or default_service()
    bridge = DashboardBridge(service, preload_pages=True)
    engine = None
    window = None
    lifecycle = None
    old_quit_policy = application.quitOnLastWindowClosed()
    try:
        engine = create_engine(bridge)
        warnings = engine._qml_warnings
        window = engine.rootObjects()[0]
        if not isinstance(window, QQuickWindow):
            raise RuntimeError("QML entry did not create a complete Quick window")
        if isolated_fixture:
            handle = bridge._tasks.submit("isolated-startup-fixture", lambda: _seed_isolated_probe(service))
            wait_until(lambda: handle.work_finished)
            result = handle.snapshot()
            from study_app.core.async_tasks import TaskStatus
            if result.status != TaskStatus.SUCCEEDED:
                raise RuntimeError("Isolated startup data fixture failed") from result.error
        bridge.refresh()
        wait_until(lambda: not bridge.busy)
        if bridge.error or not bridge.hasData:
            raise RuntimeError(bridge.error or "Dashboard returned no data")
        wait_until(lambda: _visual_item(window, "startupPreloader").property("ready"), timeout_ms=120000)
        diagnostics = inspect_runtime(engine, application)
        daily_report = {"status": "not-configured"}
        if bridge.dailySummary is not None:
            daily = bridge.dailySummary
            daily.enter_page()
            wait_until(lambda: not daily.busy and bool(daily.summaryData))
            if daily.error or not daily.summaryData.get("summary"):
                raise RuntimeError("Daily summary real-data startup probe failed")
            daily_report = {"status": "ok", "date": daily.summaryData["date"],
                            "mode": daily.summaryData["mode"], "external_requested": False}
        workspace_report = {"status": "not-configured", "sections": []}
        if bridge.workspace is not None:
            workspace = bridge.workspace
            workspace.run("load", {})
            wait_until(lambda: not workspace.busy)
            if workspace.error:
                raise RuntimeError("Workspace startup/data probe failed")
            workspace_report = {
                "status": "ok", "sections": ["plan", "records", "oj", "practice"],
                "record_count": len(workspace.recordsData.get("records", [])),
                "oj_problem_count": len(workspace.ojData.get("problems", [])),
                "template_count": len(workspace.practiceOptions.get("templates", [])),
            }
        insights_report = {"status": "not-configured", "pages": [], "sections": []}
        if bridge.insights is not None:
            insights = bridge.insights
            insights.run("assistant.load", {})
            wait_until(lambda: not insights.busy)
            if insights.error or "journal" not in insights.assistantData:
                raise RuntimeError("Assistant real-data startup probe failed")
            insights.run("assistant.ask", {"text": "当前学习计划是什么？", "external": False})
            wait_until(lambda: not insights.busy)
            if insights.error or not insights.answer.get("answer"):
                raise RuntimeError("Assistant local query probe failed")
            insights.run("knowledge.load", {})
            wait_until(lambda: not insights.busy)
            if insights.error or "graph" not in insights.knowledgeData:
                raise RuntimeError("Knowledge real-data startup probe failed")
            knowledge = insights.knowledgeData
            if knowledge.get("subjects") and not knowledge.get("graph", {}).get("nodes"):
                raise RuntimeError("Knowledge graph has subjects but no real nodes")
            insights_report = {
                "status": "ok", "pages": ["assistant", "knowledge"], "sections": ["graph", "alerts"],
                "subject_count": len(knowledge.get("subjects", [])),
                "graph_node_count": len(knowledge.get("graph", {}).get("nodes", [])),
                "alert_count": len(knowledge.get("alerts", [])),
                "journal_count": len(insights.assistantData.get("journal", [])),
                "local_answer": bool(insights.answer.get("answer")),
            }
        settings_report = {"status": "not-configured", "sections": []}
        if bridge.settings is not None:
            settings = bridge.settings
            settings.run("load", {})
            wait_until(lambda: not settings.busy)
            if settings.error or "llm" not in settings.settingsData or "subjects" not in settings.subjectsData:
                raise RuntimeError("Settings/subject real-data startup probe failed")
            settings.run("settings.integrity", {})
            wait_until(lambda: not settings.busy)
            if settings.error:
                raise RuntimeError("Settings database integrity probe failed")
            settings_report = {
                "status": "ok", "sections": ["preferences", "subjects"],
                "subject_count": len(settings.subjectsData.get("subjects", [])),
                "credential_present": bool(settings.settingsData["llm"].get("hasCredential")),
                "integrity": settings.receipt,
            }
        bridge.toggleSidebar()
        wait_until(lambda: bridge.sidebarCollapsed)
        for _ in range(8):
            bridge.toggleSidebar()
        bridge.toggleSidebar()
        for route in bridge.routes:
            bridge.navigate(route["key"])
            application.processEvents()
            if route["key"] == "plan" and bridge.workspace is not None:
                wait_until(lambda: not bridge.workspace.busy)
                page = _visual_item(window, "planPage")
                for index, name in enumerate(("planningPane", "recordsPane", "ojPane", "practicePane")):
                    page.setProperty("section", index)
                    wait_until(lambda: _visual_item(window, "workspacePaneLoader").property("paneReady")
                               and _visual_item(window, name) is not None)
            if route["key"] in {"assistant", "knowledge"} and bridge.insights is not None:
                wait_until(lambda: not bridge.insights.busy)
                if bridge.insights.error:
                    raise RuntimeError("Insights route read failed")
                page = _visual_item(window, route["key"] + "Page")
                if page is None:
                    raise RuntimeError("Insights QML page did not load")
                if route["key"] == "knowledge":
                    for section, name in (("graph", "graphPane"), ("alerts", "alertsPane")):
                        page.setProperty("activeTab", section)
                        application.processEvents()
                        item = _visual_item(window, name)
                        if item is None or not item.isVisible():
                            raise RuntimeError("Knowledge QML section did not load: " + name)
            if route["key"] == "settings" and bridge.settings is not None:
                wait_until(lambda: not bridge.settings.busy)
                if bridge.settings.error:
                    raise RuntimeError("Settings route read failed")
                page = _visual_item(window, "settingsPage")
                if page is None:
                    raise RuntimeError("Settings QML page did not load")
                for section, name in (("preferences", "preferencesPane"), ("subjects", "subjectsPane")):
                    page.setProperty("activeTab", section)
                    application.processEvents()
                    item = _visual_item(window, name)
                    if item is None or not item.isVisible():
                        raise RuntimeError("Settings QML section did not load: " + name)
        bridge.navigate("home")
        application.processEvents()
        floating = window.findChild(QQuickWindow, "floatWindow")
        if floating is None:
            raise RuntimeError("Floating QML window is missing")
        lifecycle = LifecycleController(enable_tray=False)
        bridge.attach_lifecycle(lifecycle)
        quit_calls = []
        lifecycle.start(window, floating, lambda: quit_calls.append(True))
        for _ in range(3):
            lifecycle.showFloat()
            application.processEvents()
            if window.isVisible() or not floating.isVisible():
                raise RuntimeError("Explicit hide-to-float failed")
            floating.restoreRequested.emit()
            application.processEvents()
            if not window.isVisible() or floating.isVisible():
                raise RuntimeError("Floating-window restore failed")
        for width, height in ((960, 640), (1440, 900)):
            window.resize(width, height)
            application.processEvents()
        window.close()
        application.processEvents()
        if not lifecycle.quitting or quit_calls != [True]:
            raise RuntimeError("Explicit quit callback failed")
        if warnings:
            raise RuntimeError("QML diagnostics: " + " | ".join(warnings))
        return {
            "status": "ok", "window": "QQuickWindow", "resources": list(validate_resources()),
            "isolated_fixture": isolated_fixture,
            "source": bridge.snapshot["source"], "active_subjects": bridge.snapshot["activeCount"],
            "record_count": bridge.snapshot["recordCount"],
            "routes": [{"key": route["key"], "migrated": route["migrated"]} for route in bridge.routes],
            "qml_warnings": list(warnings),
            "workspace": workspace_report,
            "insights": insights_report,
            "settings": settings_report,
            "daily_summary": daily_report,
            "runtime": diagnostics,
            "lifecycle": {"status": "ok", "hide_restore_cycles": 3, "close_exits": True,
                          "explicit_quit": True, "tray": "disabled-for-isolated-probe"},
        }
    finally:
        bridge.close()
        # The probe has completed/failed. Drain file-using workers before the
        # caller removes its temporary profile, including on startup timeout.
        bridge.wait_for_shutdown()
        if lifecycle is not None:
            lifecycle.stop()
        if window is not None:
            window.close()
        if engine is not None:
            engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
        application.setQuitOnLastWindowClosed(old_quit_policy)
