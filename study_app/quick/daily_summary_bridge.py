"""Allowlisted QObject facade for the home daily-summary service."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from study_app.ai.llm_client import LLMNotConfiguredError
from study_app.core.async_tasks import StaleSubjectTaskError, TaskStatus
from study_app.quick.daily_summary_service import (
    DailySummaryPermissionError,
    DailySummaryStaleError,
)

LOGGER = logging.getLogger(__name__)
TASK_KEY = "quick-daily-summary"
ACTIONS = frozenset({"load", "refresh", "generateEnhanced", "setLlmMode", "setLlmEnabled"})
_WRITE_ACTIONS = frozenset({"setLlmMode", "setLlmEnabled"})
_CANCELLABLE_ACTIONS = frozenset({"load", "refresh", "generateEnhanced"})
_PAID_REFRESH_WARNING = "已生成并缓存，但刷新未完成；请刷新查看，不要重复生成。"
_PAID_UNCONFIRMED_WARNING = "云端生成结果未能确认；请求可能已完成，请刷新查看后再生成，避免重复请求。"


class DailySummaryBridge(QObject):
    dataChanged = Signal()
    stateChanged = Signal()
    dataWritten = Signal()

    def __init__(self, service, tasks, parent=None):
        super().__init__(parent)
        self._service, self._tasks = service, tasks
        self._data: dict = {}
        self._epoch = 0
        self._generation = 0
        self._job_epoch = 0
        self._job_generation = 0
        self._job_date = ""
        self._closed = False
        self._busy = False
        self._cancelled = False
        self._page_active = False
        self._pending_load = False
        self._refresh_after_write = False
        self._handle = None
        self._action = ""
        self._error = ""
        self._message = ""
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)

    @Property("QVariantMap", notify=dataChanged)
    def summaryData(self):
        return dict(self._data)

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @Property(bool, notify=stateChanged)
    def canCancel(self):
        return (
            self._busy
            and not self._cancelled
            and self._action in _CANCELLABLE_ACTIONS
            and self._handle is not None
            and not self._handle.snapshot().finished
            and not self._handle.work_finished
        )

    @Property(str, notify=stateChanged)
    def error(self):
        return self._error

    @Property(str, notify=stateChanged)
    def message(self):
        return self._message

    @Slot(str, "QVariantMap")
    def run(self, action, payload):
        if self._closed:
            return
        if action not in ACTIONS:
            self._error = "不支持的每日总结操作。"
            self.stateChanged.emit()
            return
        if not self._page_active:
            return
        if action == "generateEnhanced" and self._data.get("canGenerateEnhanced") is not True:
            self._error = (
                _PAID_UNCONFIRMED_WARNING
                if self._data.get("generationOutcomeUnknown") is True
                else _PAID_REFRESH_WARNING
                if self._data.get("refreshWarning") is True
                else "每日总结数据已变化，请先成功刷新后再生成。"
                if self._data.get("stale") is True
                else str(self._data.get("enhancedStatus") or "请先成功刷新每日总结，再尝试云端生成。")
            )
            self.stateChanged.emit()
            return
        if self._busy:
            if action in {"load", "refresh"}:
                self._pending_load = True
            return

        payload = dict(payload or {})
        if action in {"setLlmMode", "setLlmEnabled"}:
            if set(payload) != {"enabled"} or not isinstance(payload.get("enabled"), bool):
                self._error = "LLM 模式操作参数无效。"
                self.stateChanged.emit()
                return
        elif payload:
            self._error = "每日总结操作不接受额外参数。"
            self.stateChanged.emit()
            return

        if action in _WRITE_ACTIONS:
            # Restore any optimistic QML Switch change from the service DTO
            # before the background transaction begins.
            self.dataChanged.emit()
        if action in {"load", "refresh", "generateEnhanced"}:
            self._generation += 1
        self._dispatch(action, payload)

    def _dispatch(self, action, payload):
        self._action = action
        self._error = ""
        self._message = ""
        self._busy = True
        self._cancelled = False
        self._job_epoch = self._epoch
        self._job_generation = self._generation
        self._job_date = self._service.current_date()
        try:
            self._handle = self._tasks.submit(
                TASK_KEY,
                lambda: self._execute(action, payload),
                timeout_seconds=None if action in _WRITE_ACTIONS else 90,
            )
        except Exception as error:
            LOGGER.warning("QML daily-summary dispatch failed (%s)", type(error).__name__)
            self._handle = None
            self._busy = False
            self._error = "每日总结任务未能启动，请重试。"
        else:
            self._timer.start()
        self.stateChanged.emit()

    def _execute(self, action, payload):
        if action in {"load", "refresh"}:
            return self._service.load()
        if action == "generateEnhanced":
            return self._service.generate_enhanced()
        if action in _WRITE_ACTIONS:
            return self._service.set_llm_mode(payload["enabled"])
        raise ValueError("unsupported daily-summary action")

    @Slot()
    def _poll(self):
        if self._handle is None:
            return
        snapshot = self._handle.snapshot()
        if snapshot.status == TaskStatus.TIMED_OUT and not self._error:
            self._error = "操作已超时；正在等待后台任务结束。"
            self.stateChanged.emit()
        # A timeout/cancel is terminal for the result, but the executor may
        # still be in I/O. Keep busy locked until the Future has actually ended.
        if not snapshot.finished or not self._handle.work_finished:
            return

        self._timer.stop()
        self._handle = None
        action = self._action
        generation_was_unconfirmed = self._data.get("generationOutcomeUnknown") is True
        result_is_current = (
            self._job_epoch == self._epoch
            and self._job_generation == self._generation
            and self._job_date == self._service.current_date()
        )
        if self._closed:
            pass
        elif self._cancelled:
            if action == "generateEnhanced":
                self._lock_unconfirmed_generation()
            else:
                self._message = "已停止接收结果；后台任务已结束。"
        elif snapshot.status == TaskStatus.SUCCEEDED:
            if action in _WRITE_ACTIONS:
                # This action returns only the transaction receipt. A separate
                # load follows after invalidation, so a failed refresh can
                # never make the committed write appear retryable.
                self._invalidate("设置已保存，正在刷新每日总结。")
                self.dataWritten.emit()
                if isinstance(snapshot.result, dict) and snapshot.result.get("saved") is True:
                    self._refresh_after_write = True
                    self._pending_load = self._page_active
                    self._data = {
                        **self._data,
                        "llmModeEnabled": bool(snapshot.result.get("enabled")),
                        "stale": True,
                    }
                    self.dataChanged.emit()
                    self._message = "LLM 模式已保存，正在刷新每日总结。"
                else:
                    self._error = "LLM 模式未确认保存；请刷新设置状态后再操作。"
            elif result_is_current:
                self._data = snapshot.result
                self.dataChanged.emit()
                if action in {"load", "refresh"} and generation_was_unconfirmed:
                    cached_enhanced = (
                        isinstance(snapshot.result, dict)
                        and snapshot.result.get("mode") == "enhanced"
                        and snapshot.result.get("cacheHit") is True
                    )
                    self._message = (
                        "刷新已找到并载入此前缓存的云端总结；请查看当前结果。"
                        if cached_enhanced
                        else "刷新未找到匹配的缓存总结；上次请求可能已完成但结果未保存，再次生成可能产生新费用。"
                    )
                else:
                    self._message = (
                        _PAID_REFRESH_WARNING
                        if action == "generateEnhanced"
                        and isinstance(snapshot.result, dict)
                        and snapshot.result.get("refreshWarning") is True
                        else "已生成并缓存云端每日总结。"
                        if action == "generateEnhanced"
                        else "LLM 模式已保存，并已刷新每日总结。"
                        if action in {"load", "refresh"} and self._refresh_after_write
                        else "已读取真实每日学习总结。"
                        if action in {"load", "refresh"}
                        else "每日总结已更新。"
                    )
                if action in {"load", "refresh"}:
                    self._refresh_after_write = False
            elif action == "generateEnhanced":
                # The cloud response and cache commit succeeded, but a newer
                # epoch/date owns the visible page now. Keep a small receipt,
                # never paint the old summary as current or invite a second
                # billable request until a successful refresh.
                self._data = {
                    **self._data,
                    "stale": True,
                    "refreshWarning": True,
                    "canGenerateEnhanced": False,
                    "lastGeneration": {
                        "saved": True,
                        "date": str(
                            (snapshot.result.get("date") if isinstance(snapshot.result, dict) else "")
                            or self._job_date
                        ),
                    },
                }
                self.dataChanged.emit()
                self._message = (
                    "已生成并缓存，但页面数据或日期已变化；请刷新查看，不要重复生成。"
                )
            else:
                self._message = "数据、设置或日期已变化，旧结果已丢弃。"
        elif action == "generateEnhanced" and snapshot.status in {
            TaskStatus.TIMED_OUT,
            TaskStatus.CANCELLED,
            TaskStatus.STALE,
        }:
            self._lock_unconfirmed_generation()
        else:
            error = snapshot.error
            LOGGER.warning(
                "QML daily-summary action failed: %s / %s",
                action,
                type(error).__name__ if error else snapshot.status.value,
            )
            if action in {"load", "refresh"} and self._refresh_after_write:
                self._error = "LLM 模式已保存，但每日总结刷新失败；请刷新查看状态，不要重复设置操作。"
                self._refresh_after_write = False
            elif isinstance(error, DailySummaryPermissionError):
                self._error = str(error)
            elif isinstance(error, (DailySummaryStaleError, StaleSubjectTaskError)):
                self._error = "记录、目录或设置已变化；请刷新后重新生成。"
            elif snapshot.status == TaskStatus.TIMED_OUT:
                self._error = (
                    "每日总结读取超时，请刷新重试。"
                    if action in {"load", "refresh"}
                    else _PAID_UNCONFIRMED_WARNING
                )
            elif isinstance(error, LLMNotConfiguredError):
                self._error = "云端请求未通过本地配置或额度检查；本地总结仍保留。"
            else:
                # Provider, database and transport exception messages can
                # contain credentials, URLs, paths, or user text.
                self._error = "每日总结未完成；本地总结仍保留，请检查设置或稍后重试。"
            if action == "generateEnhanced" and isinstance(
                error, (DailySummaryStaleError, StaleSubjectTaskError)
            ):
                self._lock_unconfirmed_generation(
                    "记录、目录或设置已变化；请刷新确认当前总结内容。"
                )
            elif action == "generateEnhanced" and not isinstance(
                error, DailySummaryPermissionError
            ):
                # A failed worker may have lost the response after the provider
                # processed it. Require a successful read to reconcile the
                # cache before allowing another potentially billable request.
                self._lock_unconfirmed_generation()
            else:
                self._message = ""
                self.dataChanged.emit()
        self._busy = False
        self._cancelled = False
        if not self._closed:
            self.stateChanged.emit()
        if self._pending_load and not self._closed:
            self._pending_load = False
            self.run("load", {})

    def _lock_unconfirmed_generation(self, error=""):
        self._data = {
            **self._data,
            "stale": True,
            "canGenerateEnhanced": False,
            "generationOutcomeUnknown": True,
        }
        self.dataChanged.emit()
        self._error = error
        self._message = _PAID_UNCONFIRMED_WARNING

    @Slot()
    def cancel(self):
        if not self.canCancel:
            return
        self._cancelled = True
        self._tasks.cancel(TASK_KEY)
        self._message = "已停止接收结果；等待后台任务结束。"
        self.stateChanged.emit()

    @Slot()
    def invalidateForDataChange(self):
        self._invalidate("记录或数据已变化，请刷新每日总结。")

    @Slot()
    def invalidateForSettingsChange(self):
        self._invalidate("LLM 设置已变化，请刷新每日总结。")

    @Slot()
    def invalidateForDateChange(self):
        self._invalidate("日期已变化，请刷新每日总结。")

    @Slot()
    def enter_page(self):
        if self._closed:
            return
        self._page_active = True
        self.run("load", {})

    @Slot()
    def leave_page(self):
        if self._closed:
            return
        self._page_active = False
        self._pending_load = False
        self._invalidate("已离开首页；旧总结结果不会应用。")

    def _invalidate(self, message):
        if self._closed:
            return
        self._epoch += 1
        self._generation += 1
        if self._data:
            self._data = {
                **self._data,
                "stale": True,
                "canGenerateEnhanced": False,
            }
            self.dataChanged.emit()
        self._message = message
        self.stateChanged.emit()

    @Slot()
    def close(self):
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        self._generation += 1
        self._pending_load = False
        if self.canCancel:
            self._cancelled = True
            self._tasks.cancel(TASK_KEY)
        # Retain the timer and task handle until work_finished, including for
        # non-cancellable settings writes. This facade never owns the pool.
        if self._handle is None:
            self._timer.stop()
            self._busy = False


def create_daily_summary(service, tasks, parent):
    from study_app.quick.daily_summary_service import DailySummaryService

    summary_service = DailySummaryService(
        service.db_path,
        service.model_path,
        service.records_path,
        today=getattr(service, "today", None),
        initialize=service.ensure_initialized,
    )
    return DailySummaryBridge(summary_service, tasks, parent)


__all__ = ["DailySummaryBridge", "create_daily_summary"]
