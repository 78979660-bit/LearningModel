"""Worker-only assistant/graph use cases; QML receives bounded DTOs, never services."""
from __future__ import annotations

import copy
import logging

from study_app.quick.page_read_cache import PageReadCache
from study_app.quick.page_refresh import PageRefresh

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot

from study_app.core.async_tasks import StaleSubjectTaskError, TaskStatus
from study_app.quick.workspace_bridge import _integer, _boolean

LOGGER = logging.getLogger(__name__)
PREVIEWS = {"assistant.preview": "proposal", "assistant.retry": "proposal", "assistant.undoPreview": "undo"}
CONFIRMS = {"assistant.confirm": "proposal", "assistant.auto": "proposal", "assistant.reject": "proposal", "assistant.undo": "undo"}
WRITES = {"assistant.confirm", "assistant.auto", "assistant.reject", "assistant.undo", "assistant.policy", "alert.handle", "alert.snooze"}
ACTIONS = {"assistant.load", "assistant.ask", "knowledge.load", *PREVIEWS, *WRITES}


class InsightsBridge(QObject):
    dataChanged = Signal()
    assistantDataChanged = Signal()
    knowledgeDataChanged = Signal()
    stateChanged = Signal()
    previewsChanged = Signal()
    dataWritten = Signal()

    def __init__(self, assistant, knowledge, tasks, initialize=None, parent=None):
        super().__init__(parent)
        self._assistant, self._knowledge, self._tasks = assistant, knowledge, tasks
        self._initialize = initialize
        self._page_reads = PageReadCache(knowledge)
        self._page_refresh = PageRefresh(knowledge, tasks, "insights-freshness", self)
        self._data = {"assistant": {}, "knowledge": {}, "answer": {}, "receipt": {}}
        self._previews = {"proposal": {}, "undo": {}}
        self._epochs = {"proposal": 0, "undo": 0}
        self._epoch = self._generation = 0
        self._subject = ""
        self._handle = None
        self._pending_load = None
        self._busy = self._closed = self._cancelled = False
        self._action = self._error = self._message = ""
        self._conversation = []
        self._chat_sequence = 0
        self._chat_pending = None
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)

    @Property("QVariantMap", notify=assistantDataChanged)
    def assistantData(self):
        return self._data["assistant"]

    @Property("QVariantMap", notify=knowledgeDataChanged)
    def knowledgeData(self):
        return self._data["knowledge"]

    @Property("QVariantMap", notify=dataChanged)
    def answer(self):
        return self._data["answer"]

    @Property("QVariantMap", notify=dataChanged)
    def receipt(self):
        return self._data["receipt"]

    @Property("QVariantMap", notify=previewsChanged)
    def proposalPreview(self):
        return self._previews["proposal"]

    @Property("QVariantMap", notify=previewsChanged)
    def undoPreview(self):
        return self._previews["undo"]

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @Property(bool, notify=stateChanged)
    def canCancel(self):
        return self._busy and self._action not in WRITES and not self._cancelled

    @Property(str, notify=stateChanged)
    def error(self):
        return self._error

    @Property(str, notify=stateChanged)
    def message(self):
        return self._message

    @Slot(str)
    def invalidatePreview(self, kind):
        if kind in self._previews:
            self._epochs[kind] += 1
            self._previews[kind] = {}
            self.previewsChanged.emit()

    @Slot()
    def leave_page(self):
        self._page_refresh.cancel()
        self._generation += 1
        self._pending_load = None
        for kind in self._previews:
            self.invalidatePreview(kind)

    @Slot()
    def invalidateForDataChange(self):
        pending = self._pending_load
        self.leave_page()
        self._pending_load = pending

    @Slot()
    def loadKnowledgeForPage(self):
        self._load_for_page("knowledge.load")

    @Slot()
    def loadAssistantForPage(self):
        self._load_for_page("assistant.load")

    def _load_for_page(self, action):
        if self._closed:
            return
        if self._busy:
            self.run(action, {})
            return
        key = action if action == "assistant.load" else (action, self._subject)
        self._page_refresh.request(key, lambda: self.run(action, {}))

    @Slot(str, "QVariantMap")
    def run(self, action, payload):
        if self._closed:
            return
        if self._busy:
            if action in {"assistant.load", "knowledge.load"}:
                self._pending_load = (action, copy.deepcopy(dict(payload)))
            return
        if action not in ACTIONS:
            self._error = "不支持的学习操作。"
            self.stateChanged.emit()
            return
        self._page_refresh.cancel()
        payload = copy.deepcopy(dict(payload))
        kind = CONFIRMS.get(action)
        if kind and (not payload.get("token") or payload["token"] != self._previews[kind].get("token")):
            self._error = "预览已失效，请重新预览并复核。"
            self.stateChanged.emit()
            return
        if action == "knowledge.load":
            self._subject = str(payload.get("subjectKey", self._subject) or "")
        if action == "assistant.load":
            self.leave_page()
        kind = PREVIEWS.get(action)
        if kind:
            self.invalidatePreview(kind)
        epoch, generation, subject = self._epochs.get(kind, 0), self._generation, self._subject
        self._epoch, self._job_generation = epoch, generation
        self._action, self._error, self._message = action, "", ""
        self._busy, self._cancelled = True, False
        if action == "assistant.ask":
            self._start_chat(str(payload.get("text") or "").strip())
        try:
            self._handle = self._tasks.submit("quick-insights", lambda: self._execute(action, payload, subject),
                                              timeout_seconds=None if action in WRITES else 120)
        except Exception as error:
            LOGGER.warning("Insights dispatch failed: %s", type(error).__name__)
            self._busy = False
            self._error = "任务未能启动，请重试。"
            if action == "assistant.ask":
                self._finish_chat(error="这次没能发送出去，可以再试一次。")
        else:
            self._timer.start()
        self.stateChanged.emit()

    def _execute(self, action, payload, subject):
        if self._initialize:
            self._initialize()
        if action == "assistant.load":
            return self._page_refresh.read(action, lambda: {"assistant": self._assistant.load()})
        if action == "knowledge.load":
            return self._page_refresh.read((action, subject), lambda: self._page_reads.read(
                subject, lambda: {"knowledge": self._knowledge.load(subject)},
                reuse=payload.get("_reuseSnapshot") is True))
        paths = [QUrl(str(path)).toLocalFile() if str(path).startswith("file:") else str(path)
                 for path in payload.get("attachments", [])]
        if action == "assistant.ask":
            result = self._assistant.ask(str(payload.get("text") or ""), paths,
                                         external=_boolean(payload.get("external", False)))
        elif action == "assistant.preview":
            result = self._assistant.preview(str(payload.get("text") or ""), paths)
        elif action == "assistant.retry":
            result = self._assistant.retry(str(payload.get("actionId") or ""))
        elif action == "assistant.undoPreview":
            result = self._assistant.undo_preview()
        elif action in {"assistant.confirm", "assistant.auto"}:
            if payload.get("confirmed") is not True:
                raise ValueError("explicit review confirmation required")
            result = self._assistant.confirm(payload["token"], execution_mode="auto" if action == "assistant.auto" else "user")
        elif action == "assistant.reject":
            result = self._assistant.reject(payload["token"])
        elif action == "assistant.undo":
            if payload.get("confirmed") is not True:
                raise ValueError("explicit undo review confirmation required")
            result = self._assistant.undo(payload["token"])
        elif action == "assistant.policy":
            if payload.get("confirmed") is not True:
                raise ValueError("explicit policy confirmation required")
            result = self._assistant.policy(str(payload.get("action") or ""), _boolean(payload.get("enabled")))
        elif action in {"alert.handle", "alert.snooze"}:
            if payload.get("confirmed") is not True:
                raise ValueError("explicit alert confirmation required")
            alert_id = _integer(payload.get("alertId"))
            result = (self._knowledge.handle(alert_id) if action == "alert.handle" else
                      self._knowledge.snooze(alert_id, str(payload.get("date") or "")))
        else:
            raise ValueError("unsupported action")
        envelope = {"result": result}
        if action in WRITES:
            # Services distinguish commit from subsequent processing. Reads cannot
            # turn a successful write into a misleading retry prompt.
            envelope["reloadFailed"] = bool(result.get("refreshWarning"))
            try:
                if action.startswith("assistant."):
                    envelope["assistant"] = self._assistant.load()
                else:
                    envelope["knowledge"] = self._knowledge.load(subject)
            except Exception as error:
                LOGGER.warning("Insights post-commit refresh failed: %s", type(error).__name__)
                envelope["reloadFailed"] = True
        return envelope

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
        kind = PREVIEWS.get(action)
        if self._cancelled:
            self._message = "已停止接收结果；后台任务已结束。"
            if action == "assistant.ask":
                self._finish_chat(error="这次回答已停止。需要的话，可以重新发送。")
        elif snapshot.status == TaskStatus.SUCCEEDED:
            envelope = snapshot.result
            self._page_refresh.accept(envelope)
            changed = False
            for key in ("assistant", "knowledge"):
                if key in envelope:
                    if self._data[key] != envelope[key]:
                        changed = True
                        self._data[key] = envelope[key]
                        getattr(self, key + "DataChanged").emit()
            if kind:
                if self._epoch == self._epochs[kind] and self._job_generation == self._generation:
                    self._previews[kind] = envelope["result"]
                    self.previewsChanged.emit()
                    if envelope["result"].get("token"):
                        self._message = "预览已生成，请复核影响与披露后确认。"
                    elif kind == "undo":
                        self._message = "当前没有可撤销操作，请核对操作审计。"
                    else:
                        self._error = "未生成可确认的建议，请补充清楚的请求并检查安全策略。"
                else:
                    self._message = "输入或页面已变化，旧预览已丢弃。"
            elif action == "assistant.ask":
                if self._job_generation == self._generation:
                    self._data["answer"] = envelope["result"]
                    self._message = "查询已完成。"
                    self._finish_chat(envelope["result"])
                else:
                    self._finish_chat(error="你离开了这次问答，回答已停止。可以重新发送这个问题。")
            elif action in WRITES:
                self._data["receipt"] = envelope["result"]
                self.invalidateForDataChange()
                if envelope["result"].get("saved") is False:
                    self._message = "业务未提交，请查看操作回执并重新复核。"
                else:
                    self._message = ("已保存；后续刷新失败，请手动刷新，勿重复提交。" if envelope.get("reloadFailed")
                                     else "操作已完成，真实数据已刷新。")
                    self.dataWritten.emit()
            else:
                self._message = "已刷新真实数据。"
            if changed or action not in {"assistant.load", "knowledge.load"}:
                self.dataChanged.emit()
        else:
            LOGGER.warning("Insights use case failed: %s / %s", action, type(snapshot.error).__name__)
            self._error = ("学科或数据版本已变化，请刷新后重新预览。" if isinstance(snapshot.error, StaleSubjectTaskError)
                           else "操作未完成，请检查输入、预览状态及现有安全策略。")
            if action == "assistant.ask":
                self._error = "这次没有得到回答，可以重新发送。"
                self._finish_chat(error=self._error)
            if action in CONFIRMS:
                self.invalidatePreview(CONFIRMS[action])
        self._busy = False
        self.stateChanged.emit()
        if self._pending_load:
            pending = self._pending_load
            self._pending_load = None
            self.run(*pending)

    @Slot()
    def cancel(self):
        if self.canCancel:
            self._cancelled = True
            self._tasks.cancel("quick-insights")
            self.stateChanged.emit()

    @Slot()
    def close(self):
        self._page_refresh.cancel()
        self._closed = True
        self._pending_load = None
        self._timer.stop()
        self._handle = None
        self._busy = False

    # Append the property after the existing Qt properties and slots, keeping
    # their metadata order stable for the retained page bridges.
    @Property("QVariantList", notify=dataChanged)
    def conversation(self):
        return copy.deepcopy(self._conversation)

    def _start_chat(self, text):
        self._chat_sequence += 1
        self._chat_pending = self._chat_sequence
        self._conversation.extend([
            {"id": f"user-{self._chat_sequence}", "role": "user", "text": text},
            {"id": f"assistant-{self._chat_sequence}", "role": "assistant", "question": text,
             "pending": True, "text": "正在想想怎么帮你…", "response": {}},
        ])
        self._conversation = self._conversation[-80:]
        self.dataChanged.emit()

    def _finish_chat(self, response=None, *, error=""):
        if self._chat_pending is None:
            return
        pending_id = f"assistant-{self._chat_pending}"
        for item in self._conversation:
            if item["id"] == pending_id:
                item.update(pending=False, failed=bool(error), text=error,
                            response=copy.deepcopy(response or {}))
                break
        self._chat_pending = None
        self.dataChanged.emit()


def create_insights(service, tasks, parent):
    from study_app.quick.assistant_service import AssistantService
    from study_app.quick.knowledge_service import KnowledgeService

    arguments = (service.db_path, service.model_path, service.records_path)
    return InsightsBridge(AssistantService(*arguments, today=service.today),
                          KnowledgeService(*arguments, today=service.today), tasks,
                          initialize=service.ensure_initialized, parent=parent)
