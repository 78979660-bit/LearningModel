from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from study_app.core.async_tasks import AsyncTaskService
from study_app.quick.daily_summary_bridge import DailySummaryBridge
from study_app.quick.self_test import wait_until


class PaneService:
    def __init__(self):
        self.enabled = False
        self.fail_mode = False
        self.mode_requests = []

    def current_date(self):
        return "2026-10-05"

    def load(self):
        return {
            "date": "2026-10-05",
            "summary": {
                "overview": ["今天还没有学习记录。"],
                "risks": ["当前没有强预警。"],
                "actions": ["补充一条学习记录。"],
                "source": ["生成方式：本地规则总结。"],
            },
            "mode": "local",
            "dataSource": "SQLite",
            "cacheHit": False,
            "stale": False,
            "llmModeEnabled": self.enabled,
            "canGenerateEnhanced": False,
            "enhancedStatus": ("已启用测试切换。" if self.enabled
                               else "主页 LLM 增强模式未启用；本地总结仍可使用。"),
        }

    def set_llm_mode(self, enabled):
        self.mode_requests.append(enabled)
        if self.fail_mode:
            raise RuntimeError("synthetic settings write failed")
        self.enabled = enabled
        return {"saved": True, "enabled": enabled}


def test_home_summary_pane_loads_real_bridge_dto_and_exposes_explicit_actions():
    app = QApplication.instance() or QApplication(["daily-summary-pane-test"])
    # Like the production entry, select a customizable style before the first
    # Controls import. Otherwise this standalone test locks the whole host to
    # the Windows native style and contaminates later window fixtures.
    if QQuickStyle.name() != "Basic":
        QQuickStyle.setStyle("Basic")
    tasks = AsyncTaskService(max_workers=1)
    service = PaneService()
    bridge = DailySummaryBridge(service, tasks)
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    pane_path = Path(__file__).parents[1] / "study_app" / "quick" / "qml" / "pages" / "HomeSummaryPane.qml"
    component.loadUrl(QUrl.fromLocalFile(str(pane_path)))
    assert component.isReady(), [error.toString() for error in component.errors()]
    pane = component.createWithInitialProperties({"bridge": bridge})
    assert pane is not None, [error.toString() for error in component.errors()]
    try:
        wait_until(lambda: not bridge.busy)
        assert pane.property("implicitHeight") > 0
        assert bridge.summaryData["mode"] == "local"
        generate = pane.findChild(QObject, "dailySummaryGenerateEnhanced")
        assert generate is not None
        assert not generate.property("enabled")
        toggle = pane.findChild(QObject, "dailySummaryLlmMode")
        assert toggle is not None
        assert not toggle.property("checked")

        toggle.clicked.emit()
        wait_until(lambda: not bridge.busy)
        assert service.enabled is True
        assert bridge.summaryData["llmModeEnabled"] is True
        assert toggle.property("checked") is True

        toggle.clicked.emit()
        wait_until(lambda: not bridge.busy)
        assert service.enabled is False
        assert bridge.summaryData["llmModeEnabled"] is False
        assert toggle.property("checked") is False

        service.fail_mode = True
        toggle.setProperty("checked", True)
        toggle.clicked.emit()
        wait_until(lambda: not bridge.busy)
        assert service.mode_requests == [True, False, True]
        assert service.enabled is False
        assert toggle.property("checked") is False
    finally:
        bridge.close()
        tasks.wait_for_shutdown()
        engine.deleteLater()
        app.processEvents()
