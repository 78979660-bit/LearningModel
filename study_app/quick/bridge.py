"""Small explicit QObject API between QML and worker-backed services."""
from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from study_app.core.async_tasks import AsyncTaskService, TaskStatus


LOGGER = logging.getLogger(__name__)
ROUTES = (
    {"key": "home", "label": "首页", "migrated": True, "description": "活动学科、已确认计划与本地模型推荐"},
    {"key": "plan", "label": "学习计划", "migrated": True, "description": "计划预览与确认、任务反馈、学习记录、OJ 和练习 PDF"},
    {"key": "assistant", "label": "学习助理", "migrated": True, "description": "本地查询、建议审批与撤销、执行策略及审计"},
    {"key": "knowledge", "label": "知识图谱", "migrated": True, "description": "真实章节图谱、知识状态、预警处理及暂缓"},
    {"key": "settings", "label": "设置", "migrated": True, "description": "服务与隐私设置、备份检查及学科安全管理"},
)


class DashboardBridge(QObject):
    snapshotChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()
    currentPageChanged = Signal()
    sidebarCollapsedChanged = Signal()
    lifecycleChanged = Signal()

    def __init__(self, service, parent=None, *, preload_pages=False):
        super().__init__(parent)
        self._service = service
        self._preload_pages = bool(preload_pages)
        self._tasks = AsyncTaskService(max_workers=1)
        self._snapshot = {}
        self._has_loaded_snapshot = False
        self._busy = False
        self._error = ""
        self._page = "home"
        self._collapsed = False
        self._closed = False
        self._pending = False
        self._handle = None
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)
        self._page_reload_timer = QTimer(self)
        self._page_reload_timer.setSingleShot(True)
        self._page_reload_timer.setInterval(20)
        self._page_reload_timer.timeout.connect(self._reload_visible_page)
        self._workspace = None
        self._insights = None
        self._settings = None
        self._daily = None
        self._lifecycle = None
        self._maintenance_handle = None
        self._maintenance_started = False
        self._maintenance_timer = QTimer(self)
        self._maintenance_timer.setInterval(1000)
        self._maintenance_timer.timeout.connect(self._poll_maintenance)
        self._calendar_date = date.today().isoformat()
        self._calendar_timer = QTimer(self)
        self._calendar_timer.setInterval(60_000)
        self._calendar_timer.timeout.connect(self._check_date)
        if all(hasattr(service, name) for name in ("db_path", "model_path", "records_path", "ensure_initialized")):
            from study_app.quick.workspace_bridge import create_workspace
            self._workspace = create_workspace(service, self._tasks, self)
            self._workspace.dataWritten.connect(self.refresh)
            from study_app.quick.insights_bridge import create_insights
            self._insights = create_insights(service, self._tasks, self)
            self._insights.dataWritten.connect(self.refresh)
            self._workspace.dataWritten.connect(self._insights.invalidateForDataChange)
            self._insights.dataWritten.connect(self._workspace.leave_page)
            from study_app.quick.settings_bridge import create_settings
            self._settings = create_settings(service, self._tasks, self)
            self._settings.dataWritten.connect(self.refresh)
            self._settings.dataWritten.connect(self._workspace.leave_page)
            self._settings.dataWritten.connect(self._insights.invalidateForDataChange)
            self._workspace.dataWritten.connect(self._settings.invalidateForDataChange)
            self._insights.dataWritten.connect(self._settings.invalidateForDataChange)
            from study_app.quick.daily_summary_bridge import create_daily_summary
            self._daily = create_daily_summary(service, self._tasks, self)
            self.snapshotChanged.connect(self._refresh_daily)
            self._workspace.dataWritten.connect(self._daily.invalidateForDataChange)
            self._insights.dataWritten.connect(self._daily.invalidateForDataChange)
            self._settings.dataWritten.connect(self._daily.invalidateForSettingsChange)
            self._daily.dataWritten.connect(self.refresh)
            self._daily.dataWritten.connect(self._settings.invalidateForDataChange)
            for writer in (self._workspace, self._insights, self._settings, self._daily):
                writer.dataWritten.connect(self._queue_visible_page_reload)
            if getattr(service, "today", None) is None:
                self._calendar_timer.start()

    @Property(QObject, constant=True)
    def workspace(self):
        return self._workspace

    @Property("QVariantMap", notify=snapshotChanged)
    def snapshot(self):
        return self._snapshot

    @Property(bool, notify=snapshotChanged)
    def hasData(self):
        return bool(self._snapshot)

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=errorChanged)
    def error(self):
        return self._error

    @Property(str, notify=currentPageChanged)
    def currentPage(self):
        return self._page

    @Property(bool, notify=sidebarCollapsedChanged)
    def sidebarCollapsed(self):
        return self._collapsed

    @Property("QVariantList", constant=True)
    def routes(self):
        return [dict(route) for route in ROUTES]

    @Property(QObject, constant=True)
    def insights(self):
        return self._insights

    @Property(QObject, constant=True)
    def settings(self):
        return self._settings

    @Property(QObject, constant=True)
    def dailySummary(self):
        return self._daily

    @Property(QObject, notify=lifecycleChanged)
    def lifecycle(self):
        return self._lifecycle

    @Property(bool, constant=True)
    def preloadPages(self):
        return self._preload_pages

    def attach_lifecycle(self, controller):
        self._lifecycle = controller
        configure_tray = getattr(controller, "set_tray_actions", None)
        if callable(configure_tray):
            configure_tray(self.todayReminderText, self.refresh)
        self.lifecycleChanged.emit()

    @Slot(result=str)
    def todayReminderText(self) -> str:
        """Format the current safe homepage snapshot without starting a read."""
        if not self._has_loaded_snapshot:
            return "首页数据尚未加载，请先刷新模型数据。"
        todos = self._snapshot.get("todos", [])
        if not isinstance(todos, list):
            todos = []
        top = next((item for item in todos if isinstance(item, dict)), None)
        if top is None:
            return "当前没有强预警事项。"
        kind = str(top.get("kind") or "提醒").strip()
        title = str(top.get("title") or "").strip()
        detail = str(top.get("detail") or "").strip()
        headline = f"{kind}: {title}" if title else kind
        return f"{headline}\n{detail}" if detail else headline

    @Slot()
    def start_maintenance(self):
        if self._closed or self._maintenance_started or self._daily is None:
            return
        self._maintenance_started = True
        from study_app.quick.maintenance_service import StartupMaintenanceService
        service = StartupMaintenanceService(self._service.db_path, self._service.ensure_initialized)
        self._maintenance_handle = self._tasks.submit("startup-maintenance", service.run)
        self._maintenance_timer.start()

    @Slot()
    def _poll_maintenance(self):
        if self._closed or self._maintenance_handle is None or not self._maintenance_handle.work_finished:
            return
        result = self._maintenance_handle.snapshot()
        self._maintenance_handle = None
        self._maintenance_timer.stop()
        if result.status != TaskStatus.SUCCEEDED:
            LOGGER.warning("QML startup maintenance failed (%s)", type(result.error).__name__)
        elif result.result.get("collected"):
            self._workspace.leave_page()
            self._insights.invalidateForDataChange()
            self.refresh()

    @Slot()
    def _queue_visible_page_reload(self):
        """Invalidating previews also cancels the current entry freshness probe.

        If a write completes after navigation, that page is already entered and
        will not ask again. Re-read it after its outstanding work settles.
        """
        writer = self.sender()
        target = {"plan": self._workspace, "settings": self._settings,
                  "assistant": self._insights, "knowledge": self._insights}.get(self._page)
        if target is None:
            return  # Home is refreshed by every writer's existing connection.
        if target is writer:
            # Writes already reload their own service. Insights shares two
            # pages and reloads only the service that performed the write.
            if target is not self._insights or (
                self._page == "assistant" and writer._action.startswith("assistant.")
            ) or (self._page == "knowledge" and writer._action.startswith("alert.")):
                return
        self._page_reload_timer.start()

    @Slot()
    def _reload_visible_page(self):
        if self._closed:
            return
        target = {"plan": self._workspace, "settings": self._settings,
                  "assistant": self._insights, "knowledge": self._insights}.get(self._page)
        if target is None:
            return
        if target.busy:
            self._page_reload_timer.start()
            return
        action = {"assistant": "assistant.load", "knowledge": "knowledge.load"}.get(self._page, "load")
        target.run(action, {})

    @Slot()
    def _refresh_daily(self):
        if self._daily is not None and not self._closed:
            self._daily.invalidateForDataChange()
            if self._page == "home":
                self._daily.run("load", {})

    @Slot()
    def _check_date(self):
        current = date.today().isoformat()
        if current != self._calendar_date and not self._closed:
            self._calendar_date = current
            if self._daily is not None:
                self._daily.invalidateForDateChange()
            self.refresh()

    @Slot(str)
    def navigate(self, page):
        if self._closed or page not in {route["key"] for route in ROUTES}:
            return
        if page != self._page:
            if self._page == "home" and self._daily is not None:
                self._daily.leave_page()
            if self._page == "plan" and self._workspace is not None:
                self._workspace.leave_page()
            if self._page in {"assistant", "knowledge"} and self._insights is not None:
                self._insights.leave_page()
            if self._page == "settings" and self._settings is not None:
                self._settings.leave_page()
            self._page = page
            self.currentPageChanged.emit()

    @Slot()
    def toggleSidebar(self):
        if not self._closed:
            self._collapsed = not self._collapsed
            self.sidebarCollapsedChanged.emit()

    @Slot()
    def refresh(self):
        if self._closed:
            return
        if self._busy:
            self._pending = True  # coalesce: one latest read after this request
            return
        self._busy = True
        self.busyChanged.emit()
        if self._error:
            self._error = ""
            self.errorChanged.emit()
        try:
            self._handle = self._tasks.submit("dashboard", self._service.load,
                                             timeout_seconds=60)
        except Exception as error:
            LOGGER.warning("QML dashboard submit failed: %s", type(error).__name__)
            self._busy = False
            self._error = "读取任务未能启动，请重试。"
            self.busyChanged.emit()
            self.errorChanged.emit()
            return
        self._timer.start()

    @Slot()
    def _poll(self):
        if self._closed or self._handle is None:
            return
        result = self._handle.snapshot()
        if not result.finished or not self._handle.work_finished:
            return
        self._timer.stop()
        self._handle = None
        if result.status == TaskStatus.SUCCEEDED:
            self._snapshot = result.result
            self._has_loaded_snapshot = True
            self.snapshotChanged.emit()
        else:
            # Exception text can contain credentials/paths from future services.
            LOGGER.warning("QML dashboard read failed: %s / %s", result.status.value,
                           type(result.error).__name__ if result.error else "no exception")
            self._error = ("数据读取超时，请重试。" if result.status == TaskStatus.TIMED_OUT
                           else "数据读取失败，请重试或在设置页检查数据。")
            self.errorChanged.emit()
        self._busy = False
        self.busyChanged.emit()
        if self._pending:
            self._pending = False
            self.refresh()

    @Slot()
    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._workspace is not None:
            self._workspace.close()
        if self._insights is not None:
            self._insights.close()
        if self._settings is not None:
            self._settings.close()
        if self._daily is not None:
            self._daily.close()
        self._calendar_timer.stop()
        self._maintenance_timer.stop()
        self._pending = False
        self._timer.stop()
        self._page_reload_timer.stop()
        self._tasks.close()
        self._handle = None
        self._busy = False
        self.busyChanged.emit()

    def wait_for_shutdown(self):
        """Process cleanup only; never called by a QML slot or active GUI."""
        self._tasks.wait_for_shutdown()
