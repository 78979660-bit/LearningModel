"""Fixed worker-backed settings/lifecycle API. No database or keys in QML."""
from __future__ import annotations

import copy
import logging
from study_app.quick.page_refresh import PageRefresh

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from study_app.core.async_tasks import StaleSubjectTaskError, TaskStatus
from study_app.quick.credential_prompt import prompt_credential

LOGGER = logging.getLogger(__name__)
WRITES = {"settings.save", "settings.credential", "subject.confirm", "subject.retryProjection"}
ACTIONS = {"load", "settings.save", "settings.backup", "settings.integrity",
           "subject.preview", "subject.confirm", "subject.retryProjection", "subject.importOutline"}


class SettingsBridge(QObject):
    dataChanged = Signal()
    stateChanged = Signal()
    previewsChanged = Signal()
    outlineDraftChanged = Signal()
    dataWritten = Signal()

    def __init__(self, settings, subjects, tasks, initialize=None, parent=None, *, credential_prompt=None):
        super().__init__(parent)
        self._settings, self._subjects, self._tasks = settings, subjects, tasks
        self._initialize = initialize
        self._page_refresh = PageRefresh(settings, tasks, "settings-freshness", self)
        self._credential_prompt = credential_prompt or prompt_credential
        self._data = {"settings": {}, "subjects": {}, "receipt": {}, "subjectResult": {}, "outlineDraft": {}}
        self._preview = {}
        self._epoch = self._generation = 0
        self._job_epoch = self._job_generation = 0
        self._closed = self._busy = self._cancelled = self._pending_load = False
        self._handle = None
        self._action = self._error = self._message = ""
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)

    @Property("QVariantMap", notify=dataChanged)
    def settingsData(self):
        return self._data["settings"]

    @Property("QVariantMap", notify=dataChanged)
    def subjectsData(self):
        return self._data["subjects"]

    @Property("QVariantMap", notify=dataChanged)
    def receipt(self):
        return self._data["receipt"]

    @Property("QVariantMap", notify=dataChanged)
    def subjectResult(self):
        return self._data["subjectResult"]

    @Property("QVariantMap", notify=outlineDraftChanged)
    def outlineDraft(self):
        return self._data["outlineDraft"]

    @Property("QVariantMap", notify=previewsChanged)
    def subjectPreview(self):
        return self._preview

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @Property(bool, notify=stateChanged)
    def canCancel(self):
        return self._busy and self._action in {"load", "settings.integrity", "subject.preview", "subject.importOutline"} and not self._cancelled

    @Property(str, notify=stateChanged)
    def error(self):
        return self._error

    @Property(str, notify=stateChanged)
    def message(self):
        return self._message

    @Slot(str)
    def invalidatePreview(self, kind):
        if kind == "subject":
            existed = bool(self._preview)
            self._epoch += 1
            self._preview = {}
            self.previewsChanged.emit()
            if existed:
                self._message = "预览已失效，请重新预览并复核。"
                self.stateChanged.emit()

    @Slot()
    def leave_page(self):
        self._page_refresh.cancel()
        self._generation += 1
        self._pending_load = False
        self.invalidatePreview("subject")
        self._data["outlineDraft"] = {}

    @Slot()
    def invalidateForDataChange(self):
        pending = self._pending_load
        self.leave_page()
        self._pending_load = pending

    @Slot()
    def loadForPage(self):
        if not self._closed:
            self._page_refresh.request("load", lambda: self.run("load", {}))

    @Slot(str, "QVariantMap")
    def run(self, action, payload):
        if self._closed:
            return
        if action not in ACTIONS:
            self._error = "不支持的设置操作。"
            self.stateChanged.emit()
            return
        if self._busy:
            if action == "load":
                self._pending_load = True
            return
        payload = copy.deepcopy(dict(payload))
        if action == "subject.confirm":
            if not payload.get("token") or payload["token"] != self._preview.get("token"):
                self._error = "预览已失效，请重新预览并复核。"
                self.stateChanged.emit()
                return
            if payload.get("confirmed") is not True:
                self._error = "请先勾选影响说明并准确输入学科全名。"
                self.stateChanged.emit()
                return
        if action in {"load", "subject.preview", "subject.importOutline"}:
            self.invalidatePreview("subject")
        self._dispatch(action, payload)

    def _dispatch(self, action, payload):
        self._page_refresh.cancel()
        self._action, self._error, self._message = action, "", ""
        self._busy, self._cancelled = True, False
        self._job_epoch, self._job_generation = self._epoch, self._generation
        try:
            self._handle = self._tasks.submit("quick-settings", lambda: self._execute(action, payload),
                                              timeout_seconds=None if action in WRITES or action == "settings.backup" else 120)
        except Exception as error:
            LOGGER.warning("Settings dispatch failed: %s", type(error).__name__)
            self._busy = False
            self._error = "任务未能启动，请重试。"
        else:
            self._timer.start()
        self.stateChanged.emit()

    def _execute(self, action, payload):
        if self._initialize:
            self._initialize()
        if action == "load":
            return self._page_refresh.read("load", lambda: {"settings": self._settings.load(), "subjects": self._subjects.load()})
        if action == "subject.preview":
            return {"result": self._subjects.preview(str(payload.get("kind") or ""),
                       name=str(payload.get("name") or ""), outline=str(payload.get("outline") or ""),
                       subject_key=payload.get("subjectKey"))}
        if action == "subject.importOutline":
            url = QUrl(str(payload.get("url") or ""))
            if not url.isLocalFile():
                raise ValueError("outline import requires a local file")
            return {"result": self._subjects.import_outline(url.toLocalFile())}
        if action == "settings.save":
            result = self._settings.save(payload)
        elif action == "settings.credential":
            result = self._settings.set_credential(
                payload["provider"], payload.get("key", ""),
                clear=payload.get("clear", False), expected_scope=payload["scope"],
            )
        elif action == "settings.backup":
            result = self._settings.backup()
        elif action == "settings.integrity":
            result = self._settings.check_integrity()
        elif action == "subject.confirm":
            result = self._subjects.confirm(payload["token"], str(payload.get("typedName") or ""), payload.get("confirmed") is True)
        elif action == "subject.retryProjection":
            result = self._subjects.retry_projection(str(payload.get("operationId") or ""))
        else:
            raise ValueError("unsupported settings operation")
        envelope = {"result": result, "reloadFailed": bool(result.get("refreshWarning"))}
        if action in WRITES:
            try:
                envelope.update(settings=self._settings.load(), subjects=self._subjects.load())
            except Exception as error:
                LOGGER.warning("Settings post-commit refresh failed: %s", type(error).__name__)
                envelope["reloadFailed"] = True
        return envelope

    def _configured_provider(self, provider):
        current = self._data["settings"].get("llm", {})
        if current.get("provider") != provider:
            self._error = "请先保存当前服务商设置，再配置该服务商的凭据。"
            self.stateChanged.emit()
            return None
        if not current.get("credentialScope"):
            self._error = "请先刷新已保存的接口设置，再配置凭据。"
            self.stateChanged.emit()
            return None
        return current

    @Slot(str)
    def promptCredential(self, provider):
        if self._closed or self._busy:
            return
        current = self._configured_provider(provider)
        if current is None:
            return
        self._busy = True
        self._action, self._error = "settings.credential", ""
        self.stateChanged.emit()
        try:
            parent = QGuiApplication.focusWindow()
            key = self._credential_prompt(current.get("providerLabel", provider), int(parent.winId()) if parent else 0)
        except Exception as error:
            LOGGER.warning("Credential input failed: %s", type(error).__name__)
            self._busy = False
            self._error = "凭据输入未完成；已有凭据未修改。"
            self.stateChanged.emit()
            return
        self._busy = False
        if key is None or self._closed:
            self._message = "已取消凭据配置，已有凭据未修改。"
            self.stateChanged.emit()
            return
        self._dispatch("settings.credential", {
            "provider": provider, "key": key, "clear": False,
            "scope": current["credentialScope"],
        })

    @Slot(str, bool)
    def clearCredential(self, provider, confirmed):
        if self._closed or self._busy or confirmed is not True:
            return
        current = self._configured_provider(provider)
        if current is None:
            return
        self._dispatch("settings.credential", {
            "provider": provider, "clear": True, "scope": current["credentialScope"],
        })

    @Slot()
    def openBackupFolder(self):
        if self._closed or self._busy:
            return
        # The path is service-owned, never an arbitrary URL supplied by QML.
        location = self._settings.backup_root
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(location))):
            self._error = "无法打开备份目录，请先创建备份。"
            self.stateChanged.emit()

    @Slot()
    def _poll(self):
        if self._closed or self._handle is None:
            return
        snapshot = self._handle.snapshot()
        if snapshot.status == TaskStatus.TIMED_OUT and not self._error:
            self._error = "操作超时；正在等待后台任务释放资源。"
            self.stateChanged.emit()
        if not snapshot.finished or not self._handle.work_finished:
            return
        self._timer.stop()
        self._handle = None
        action = self._action
        if self._cancelled:
            self._message = "已停止接收结果；后台任务已结束。"
        elif snapshot.status == TaskStatus.SUCCEEDED:
            envelope = snapshot.result
            self._page_refresh.accept(envelope)
            changed = False
            for key in ("settings", "subjects"):
                if key in envelope:
                    changed = changed or self._data[key] != envelope[key]
                    self._data[key] = envelope[key]
            if action == "subject.preview":
                if self._job_epoch == self._epoch and self._job_generation == self._generation:
                    self._preview = envelope["result"]
                    self.previewsChanged.emit()
                    self._message = "预览已生成，请复核影响并准确输入学科全名。"
                else:
                    self._message = "输入或页面已变化，旧预览已丢弃。"
            elif action == "subject.importOutline":
                if self._job_epoch == self._epoch and self._job_generation == self._generation:
                    self._data["outlineDraft"] = envelope["result"]
                    self.outlineDraftChanged.emit()
                    self._message = "目录已载入，尚未保存；请生成预览并复核。"
                else:
                    self._message = "输入或页面已变化，旧目录读取结果已丢弃。"
            elif action == "load":
                self._message = "已读取真实设置与学科目录。"
            else:
                result = envelope["result"]
                self._data["receipt"] = result
                if action.startswith("subject."):
                    self._data["subjectResult"] = result
                if action in WRITES:
                    self.invalidateForDataChange()
                    if result.get("saved") is False:
                        self._message = "业务未提交，请查看回执并重新复核。"
                    else:
                        self._message = ("已保存；后续刷新失败，请手动刷新，勿重复提交。" if envelope.get("reloadFailed")
                                         else "已保存；目录投影待重试，请在学科管理中重试同步。" if result.get("projectionPending")
                                         else "已保存；服务商或接口身份已变化，旧凭据已清除，请重新配置凭据。" if result.get("credentialReset")
                                         else "操作已完成。")
                        self.dataWritten.emit()
                else:
                    self._message = "操作已完成，请查看结果。"
            if changed or action != "load":
                self.dataChanged.emit()
        else:
            LOGGER.warning("Settings use case failed: %s / %s", action, type(snapshot.error).__name__)
            self._error = ("操作结果暂时无法核实，请先刷新并检查学科状态，不要重复执行。" if type(snapshot.error).__name__ == "SubjectOperationOutcomeUnknownError"
                           else "学科或数据版本已变化，请刷新后重新预览。" if isinstance(snapshot.error, StaleSubjectTaskError)
                           else "操作未完成，请核对输入、确认信息和现有安全流程。")
            if action == "subject.confirm":
                self.invalidatePreview("subject")
        self._busy = False
        self.stateChanged.emit()
        if self._pending_load:
            self._pending_load = False
            self.run("load", {})

    @Slot()
    def cancel(self):
        if self.canCancel:
            self._cancelled = True
            self._tasks.cancel("quick-settings")
            self.stateChanged.emit()

    @Slot()
    def close(self):
        self._page_refresh.cancel()
        self._closed = True
        self._pending_load = False
        self._timer.stop()
        self._handle = None
        self._busy = False


def create_settings(service, tasks, parent):
    from study_app.quick.settings_service import SettingsService
    from study_app.quick.subject_service import SubjectService
    arguments = (service.db_path, service.model_path, service.records_path)
    return SettingsBridge(SettingsService(*arguments), SubjectService(*arguments), tasks,
                          initialize=service.ensure_initialized, parent=parent)
