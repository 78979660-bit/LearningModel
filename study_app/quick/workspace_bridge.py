"""Explicit phase-two use cases, serialized with the dashboard's worker queue.

Slots only validate/dispatch and update Qt state. Services own validation,
preview tokens and persistence; no QML receives database handles or secrets.
"""
from __future__ import annotations

import copy
import logging

from study_app.quick.page_read_cache import PageReadCache
from study_app.quick.page_refresh import PageRefresh
import math

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from study_app.core.async_tasks import StaleSubjectTaskError, TaskStatus

LOGGER = logging.getLogger(__name__)

LABELS = {
    "load": "刷新学习工作区", "budget.preview": "预览预算计划",
    "budget.confirm": "确认预算计划", "plan.preview": "生成详细计划预览",
    "plan.confirm": "确认详细计划", "plan.check": "更新任务完成状态",
    "plan.feedback": "记录作业反馈", "plan.archive": "归档当前计划",
    "plan.phase": "更新学习阶段", "record.preview": "识别并预览记录",
    "record.save": "保存复核后的记录", "oj.problem": "保存 OJ 题目",
    "oj.mapping": "保存显式知识点映射", "oj.attempt": "保存 OJ 提交",
    "practice.generate": "生成本地练习 PDF", "practice.prompt": "生成练习提示词",
    "artifact.open": "打开已生成的产物",
    "record.search": "按描述查找本地材料",
}
PREVIEWS = {"budget.preview": "budget", "plan.preview": "plan",
            "record.preview": "record", "practice.prompt": "practice"}
CONFIRMS = {"budget.confirm": "budget", "plan.confirm": "plan",
            "record.save": "record"}
WRITES = {"budget.confirm", "plan.confirm", "plan.check", "plan.feedback",
          "plan.archive", "plan.phase", "record.save", "oj.problem", "oj.mapping", "oj.attempt"}


def _integer(value):
    """QML Number becomes float in QVariantMap; reject fractions, bool and NaN."""
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ValueError("an integer is required")


def _boolean(value):
    if type(value) is not bool:
        raise ValueError("a boolean is required")
    return value


class WorkspaceBridge(QObject):
    dataChanged = Signal()
    planDataChanged = Signal()
    stateChanged = Signal()
    previewsChanged = Signal()
    dataWritten = Signal()

    def __init__(self, plan, records, oj, practice, tasks, initialize=None, parent=None):
        super().__init__(parent)
        self._plan, self._records, self._oj, self._practice = plan, records, oj, practice
        self._tasks = tasks
        self._initialize = initialize
        self._page_reads = PageReadCache(plan)
        self._page_refresh = PageRefresh(plan, tasks, "workspace-freshness", self)
        self._data = {"plan": {}, "records": {}, "oj": {}, "practiceOptions": {}, "practice": {}}
        self._previews = {kind: {} for kind in ("budget", "plan", "record", "practice")}
        self._epochs = {kind: 0 for kind in self._previews}
        self._scope = None
        self._handle = None
        self._busy = self._closed = self._cancel_requested = False
        self._action = self._error = self._message = ""
        self._job_epoch = 0
        self._timeout_notified = False
        self._last_result = {}
        self._file_search = {}
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)

    @Property("QVariantMap", notify=planDataChanged)
    def planData(self):
        return self._data["plan"]

    @Property("QVariantMap", notify=dataChanged)
    def recordsData(self):
        return self._data["records"]

    @Property("QVariantMap", notify=dataChanged)
    def ojData(self):
        return self._data["oj"]

    @Property("QVariantMap", notify=dataChanged)
    def practiceData(self):
        return self._data["practice"]

    @Property("QVariantMap", notify=dataChanged)
    def practiceOptions(self):
        return self._data["practiceOptions"]

    @Property("QVariantMap", notify=dataChanged)
    def lastResult(self):
        return self._last_result

    @Property("QVariantMap", notify=dataChanged)
    def fileSearch(self):
        return self._file_search

    @Property("QVariantMap", notify=previewsChanged)
    def budgetPreview(self):
        return self._previews["budget"]

    @Property("QVariantMap", notify=previewsChanged)
    def planPreview(self):
        return self._previews["plan"]

    @Property("QVariantMap", notify=previewsChanged)
    def recordPreview(self):
        return self._previews["record"]

    @Property("QVariantMap", notify=previewsChanged)
    def practicePreview(self):
        return self._previews["practice"]

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=stateChanged)
    def operation(self):
        return LABELS.get(self._action, "") if self._busy else ""

    @Property(str, notify=stateChanged)
    def error(self):
        return self._error

    @Property(str, notify=stateChanged)
    def message(self):
        return self._message

    @Property(bool, notify=stateChanged)
    def canCancel(self):
        return self._busy and self._action not in WRITES and not self._cancel_requested

    @Slot(str)
    def invalidatePreview(self, kind):
        if kind not in self._previews:
            return
        self._epochs[kind] += 1
        self._previews[kind] = {}
        self.previewsChanged.emit()

    @Slot()
    def leave_page(self):
        self._page_refresh.cancel()
        for kind in self._previews:
            self.invalidatePreview(kind)

    @Slot()
    def loadForPage(self):
        if not self._closed and not self._busy:
            self._page_refresh.request(str(self._scope), self._refresh_page)

    def _refresh_page(self):
        if not self._closed and not self._busy:
            self.run("load", {})

    @Slot(str, "QVariantMap")
    def run(self, action, payload):
        """Closed allowlist of use cases; never a dynamic service/method lookup."""
        if self._closed:
            return
        if action not in LABELS:
            self._error = "未支持的操作。"
            self.stateChanged.emit()
            return
        if self._busy:
            self._error = "上一项操作仍在运行，请等待结束。"
            self.stateChanged.emit()
            return
        self._page_refresh.cancel()
        payload = copy.deepcopy(payload)
        kind = CONFIRMS.get(action)
        if kind and (not payload.get("token") or payload["token"] != self._previews[kind].get("token")):
            self._error = "预览已失效，请重新预览并复核。"
            self.stateChanged.emit()
            return
        if action == "load":
            if "scope" in payload:
                self._scope = payload["scope"] or None
            self.leave_page()
        kind = PREVIEWS.get(action)
        if kind:
            self.invalidatePreview(kind)
        self._job_epoch = self._epochs.get(kind, 0)
        self._action, self._error, self._message = action, "", ""
        self._busy, self._cancel_requested = True, False
        self._timeout_notified = False
        # Capture UI state before dispatch. Workers never access QObject state.
        scope = self._scope
        try:
            timeout = None if action in WRITES else 120
            self._handle = self._tasks.submit(
                "quick-workspace", lambda: self._execute(action, payload, scope), timeout_seconds=timeout)
        except Exception as error:
            LOGGER.warning("QML workspace submit failed: %s", type(error).__name__)
            self._busy = False
            self._error = "任务未能启动，请重试。"
        else:
            self._timer.start()
        self.stateChanged.emit()

    def _load(self, scope):
        return {"plan": self._plan.load(scope), "records": self._records.load(),
                "oj": self._oj.load(), "practiceOptions": self._practice.load()}

    def _execute(self, action, payload, scope):
        if self._initialize:
            self._initialize()
        if action == "load":
            return self._page_refresh.read(str(scope), lambda: self._page_reads.read(
                str(scope), lambda: {"loaded": self._load(scope)},
                reuse=payload.get("_reuseSnapshot") is True))
        if action == "budget.preview":
            payload["budget_minutes"] = _integer(payload.get("budget_minutes"))
            payload["estimate_minutes"] = {key: _integer(value) for key, value in payload.get("estimate_minutes", {}).items()}
            result = self._plan.preview_budget(payload)
        elif action == "budget.confirm":
            result = self._plan.confirm_budget(payload["token"])
        elif action == "plan.preview":
            result = self._plan.preview_plan(payload)
        elif action == "plan.confirm":
            result = self._plan.confirm_plan(payload["token"])
        elif action == "plan.check":
            result = self._plan.set_checked(_integer(payload["id"]), _boolean(payload["checked"]), scope=scope)
        elif action == "plan.feedback":
            result = self._plan.feedback(_integer(payload["id"]), _boolean(payload["correct"]), scope=scope)
        elif action == "plan.archive":
            result = self._plan.archive(payload.get("scope") or None)
        elif action == "plan.phase":
            result = self._plan.set_final_review_modes(payload)
        elif action == "record.preview":
            payload["attachments"] = [QUrl(value).toLocalFile() if isinstance(value, str) and value.startswith("file:") else value
                                      for value in payload.get("attachments", [])]
            result = self._records.preview(payload, recognition=payload.pop("recognition", "local"))
        elif action == "record.search":
            result = self._records.search_files(payload.get("query", ""))
        elif action == "record.save":
            result = self._records.save(payload["token"], payload["reviewed"])
        elif action == "oj.problem":
            result = self._oj.create_problem(payload)
        elif action == "oj.mapping":
            result = self._oj.save_mapping(payload)
        elif action == "oj.attempt":
            payload["duration_seconds"] = _integer(payload.get("duration_seconds"))
            result = self._oj.submit_attempt(payload)
        elif action == "practice.generate":
            for key in ("question_count", "timeout"):
                if key in payload:
                    payload[key] = _integer(payload[key])
            result = self._practice.generate_practice(payload)
        elif action == "practice.prompt":
            if "question_count" in payload:
                payload["question_count"] = _integer(payload["question_count"])
            result = self._practice.prepare_practice_prompt(payload)
        elif action == "artifact.open":
            result = self._practice.artifact_path(payload["token"])
        else:
            raise ValueError("unsupported use case")
        envelope = {"result": result}
        if action in WRITES:
            if result.get("refreshWarning"):
                envelope["reloadFailed"] = True
            # A post-commit refresh failure must never imply the write failed.
            try:
                envelope["loaded"] = self._load(scope)
            except Exception as error:
                LOGGER.warning("QML workspace post-commit reload failed: %s", type(error).__name__)
                envelope["reloadFailed"] = True
        return envelope

    @Slot()
    def _poll(self):
        if self._closed or self._handle is None:
            return
        result = self._handle.snapshot()
        if result.status == TaskStatus.TIMED_OUT and not self._timeout_notified:
            self._timeout_notified = True
            self._error = "操作超时；正在等待后台任务释放资源。"
            self.stateChanged.emit()
        # Result cancellation does not prove the worker has released its files.
        if not result.finished or not self._handle.work_finished:
            return
        self._timer.stop()
        self._handle = None
        action = self._action
        kind = PREVIEWS.get(action)
        if self._cancel_requested:
            self._message = "操作已停止，未接收结果。已运行的外部操作请核对其状态。"
        elif result.status == TaskStatus.SUCCEEDED:
            envelope = result.result
            self._page_refresh.accept(envelope)
            if "loaded" in envelope:
                changed = any(self._data.get(key) != value for key, value in envelope["loaded"].items())
                plan_changed = self._data["plan"] != envelope["loaded"].get("plan", self._data["plan"])
                self._data.update(envelope["loaded"])
                if plan_changed:
                    self.planDataChanged.emit()
                if changed:
                    self.dataChanged.emit()
            if kind:
                if self._job_epoch == self._epochs[kind]:
                    self._previews[kind] = envelope["result"]
                    self.previewsChanged.emit()
                    self._message = "预览已生成；请复核后确认。"
                else:
                    self._message = "输入已变化，旧预览已丢弃。请重新预览。"
            elif action.startswith("practice."):
                self._data["practice"] = envelope.get("result") or {}
                self.invalidatePreview("practice")
                self.dataChanged.emit()
                self._message = "练习操作已完成，请查看输出或提示词。"
            elif action == "record.search":
                self._file_search = envelope.get("result") or {}
                self.dataChanged.emit()
                self._message = "查找已完成，请明确选择要加入的材料。"
            elif action == "artifact.open":
                if QDesktopServices.openUrl(QUrl.fromLocalFile(str(envelope["result"]))):
                    self._message = "已交给系统查看器打开。"
                else:
                    self._error = "系统没有可用的文档查看器。"
            elif action in WRITES:
                self._last_result = envelope.get("result") or {}
                self.dataChanged.emit()
                self.leave_page()
                self._message = ("已保存；后续处理或刷新失败，请手动刷新，勿重复提交。" if envelope.get("reloadFailed") else "已保存并刷新真实数据。")
                self.dataWritten.emit()
            else:
                self._message = "已刷新真实数据。"
        else:
            LOGGER.warning("QML workspace failed: %s / %s", action, type(result.error).__name__)
            self._error = ("数据目录或对象已变化，请刷新后重新预览。" if isinstance(result.error, StaleSubjectTaskError)
                           else "操作失败，请核对必填项、范围、材料和预览状态，再重试。")
            if action in CONFIRMS:
                self.invalidatePreview(CONFIRMS[action])
        self._busy = False
        self.stateChanged.emit()

    @Slot()
    def cancel(self):
        if not self.canCancel:
            return
        self._cancel_requested = True
        self._tasks.cancel("quick-workspace")
        self._message = "正在停止；工作线程释放资源后结束。"
        self.stateChanged.emit()

    @Slot(str)
    def openArtifact(self, token):
        # Resolve and check files on the worker; launch the viewer on Qt only.
        self.run("artifact.open", {"token": token})

    @Slot(str)
    def copyPrompt(self, token):
        if token == self.practicePreview.get("token"):
            QGuiApplication.clipboard().setText(str(self.practicePreview.get("prompt") or ""))

    def close(self):
        self._page_refresh.cancel()
        self._closed = True
        self._timer.stop()
        self._handle = None
        self._busy = False


def create_workspace(service, tasks, parent):
    """Construct adapters without opening a DB, reading files or loading keys."""
    from study_app.quick.plan_service import PlanService
    from study_app.quick.records_service import RecordsService, OJService
    from study_app.quick.practice_service import PracticeService

    arguments = (service.db_path, service.model_path, service.records_path)
    options = {"today": service.today}
    return WorkspaceBridge(PlanService(*arguments, **options), RecordsService(*arguments, **options),
                           OJService(*arguments, **options), PracticeService(*arguments, **options),
                           tasks, initialize=service.ensure_initialized, parent=parent)
