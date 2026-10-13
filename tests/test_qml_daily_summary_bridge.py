from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from study_app.core.async_tasks import AsyncTaskService
from study_app.quick.daily_summary_bridge import DailySummaryBridge
from study_app.quick.daily_summary_service import DailySummaryStaleError
from study_app.quick.self_test import wait_until


class SlowService:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.date = "2026-10-05"

    def current_date(self):
        return self.date

    def load(self):
        self.started.set()
        self.release.wait(3)
        return {"date": self.date, "summary": {"overview": ["synthetic test result"]}}


def _application():
    return QApplication.instance() or QApplication(["daily-summary-bridge-test"])


def test_cancel_waits_for_work_finished_and_discards_result():
    app = _application()
    tasks = AsyncTaskService(max_workers=1)
    service = SlowService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        assert service.started.wait(1)
        assert bridge.busy and bridge.canCancel
        bridge.cancel()
        QTest.qWait(60)
        app.processEvents()
        assert bridge.busy
        assert not bridge.canCancel
        service.release.set()
        wait_until(lambda: not bridge.busy)
        assert not bridge.summaryData
        assert "已停止接收结果" in bridge.message
    finally:
        service.release.set()
        bridge.close()
        tasks.wait_for_shutdown()


def test_invalidation_and_date_change_drop_inflight_snapshot():
    app = _application()
    tasks = AsyncTaskService(max_workers=1)
    service = SlowService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        assert service.started.wait(1)
        bridge.invalidateForDataChange()
        service.release.set()
        wait_until(lambda: not bridge.busy)
        assert not bridge.summaryData
        assert "旧结果已丢弃" in bridge.message

        # A distinct request begun on the original date is also stale when the
        # calendar boundary advances while its worker is still running.
        service.release.clear()
        service.started.clear()
        bridge.run("load", {})
        assert service.started.wait(1)
        service.date = "2026-10-06"
        service.release.set()
        wait_until(lambda: not bridge.busy)
        assert not bridge.summaryData
        assert "旧结果已丢弃" in bridge.message
    finally:
        service.release.set()
        bridge.close()
        tasks.wait_for_shutdown()


def test_only_allowlisted_actions_run_and_errors_are_redacted():
    app = _application()
    tasks = AsyncTaskService(max_workers=1)

    class FailingService:
        def current_date(self):
            return "2026-10-05"

        def load(self):
            raise RuntimeError("qml-test-credential C:/private/profile.sqlite")

    bridge = DailySummaryBridge(FailingService(), tasks)
    try:
        bridge.run("deleteEverything", {})
        assert not bridge.busy
        assert "不支持" in bridge.error
        bridge.enter_page()
        wait_until(lambda: not bridge.busy)
        assert "qml-test-credential" not in bridge.error
        assert "private/profile" not in bridge.error
        assert "每日总结未完成" in bridge.error
    finally:
        bridge.close()
        tasks.wait_for_shutdown()


def test_close_keeps_running_task_locked_until_worker_exits():
    _application()
    tasks = AsyncTaskService(max_workers=1)
    service = SlowService()
    bridge = DailySummaryBridge(service, tasks)
    bridge.enter_page()
    assert service.started.wait(1)
    bridge.close()
    assert bridge.busy
    service.release.set()
    wait_until(lambda: not bridge.busy)
    tasks.wait_for_shutdown()


def test_committed_mode_change_survives_summary_refresh_failure_without_retrying_write():
    _application()
    tasks = AsyncTaskService(max_workers=1)

    class WriteThenReadService:
        def __init__(self):
            self.enabled = False
            self.writes = []

        def current_date(self):
            return "2026-10-05"

        def set_llm_mode(self, enabled):
            self.enabled = enabled
            self.writes.append(enabled)
            return {"saved": True, "enabled": enabled}

        def load(self):
            raise RuntimeError("qml-test-credential C:/private/profile.sqlite")

    service = WriteThenReadService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        wait_until(lambda: not bridge.busy)
        bridge.run("setLlmMode", {"enabled": True})
        wait_until(lambda: not bridge.busy)
        assert service.writes == [True]
        assert service.enabled is True
        assert "已保存" in bridge.error
        assert "不要重复设置操作" in bridge.error
        assert "qml-test-credential" not in bridge.error
        assert "private/profile" not in bridge.error
    finally:
        bridge.close()
        tasks.wait_for_shutdown()


def test_leave_page_discards_result_but_allows_fresh_load_after_return():
    _application()
    tasks = AsyncTaskService(max_workers=1)
    service = SlowService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        assert service.started.wait(1)
        bridge.leave_page()
        assert bridge.busy  # Leaving invalidates reception; it does not unlock I/O.
        service.release.set()
        wait_until(lambda: not bridge.busy)
        assert not bridge.summaryData

        service.started.clear()
        bridge.enter_page()
        assert service.started.wait(1)
        wait_until(lambda: not bridge.busy)
        assert bridge.summaryData["summary"]["overview"] == ["synthetic test result"]
    finally:
        service.release.set()
        bridge.close()
        tasks.wait_for_shutdown()


def test_committed_refresh_warning_blocks_repeat_until_successful_load():
    _application()
    tasks = AsyncTaskService(max_workers=1)

    class PaidService:
        def __init__(self):
            self.generate_calls = 0
            self.load_calls = 0

        def current_date(self):
            return "2026-10-05"

        def load(self):
            self.load_calls += 1
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["本地摘要"]},
                "mode": "local",
                "canGenerateEnhanced": True,
                "enhancedStatus": "已允许显式生成云端每日总结。",
                "stale": False,
            }

        def generate_enhanced(self):
            self.generate_calls += 1
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["已生成并缓存的摘要"]},
                "mode": "enhanced",
                "canGenerateEnhanced": False,
                "stale": True,
                "refreshWarning": True,
            }

    service = PaidService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        wait_until(lambda: not bridge.busy)
        bridge.run("generateEnhanced", {})
        wait_until(lambda: not bridge.busy)
        assert service.generate_calls == 1
        assert bridge.summaryData["mode"] == "enhanced"
        assert bridge.summaryData["canGenerateEnhanced"] is False
        assert "已生成并缓存" in bridge.message
        assert "刷新未完成" in bridge.message
        assert "不要重复生成" in bridge.message

        bridge.run("generateEnhanced", {})
        assert not bridge.busy
        assert service.generate_calls == 1
        assert "不要重复生成" in bridge.error

        bridge.run("refresh", {})
        wait_until(lambda: not bridge.busy)
        assert bridge.summaryData["stale"] is False
        assert bridge.summaryData["canGenerateEnhanced"] is True
    finally:
        bridge.close()
        tasks.wait_for_shutdown()


def test_epoch_stale_paid_generation_keeps_receipt_and_blocks_repeat_until_load():
    _application()
    tasks = AsyncTaskService(max_workers=1)

    class DelayedPaidService:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.generate_calls = 0

        def current_date(self):
            return "2026-10-05"

        def load(self):
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["当前本地摘要"]},
                "mode": "local",
                "canGenerateEnhanced": True,
                "stale": False,
            }

        def generate_enhanced(self):
            self.generate_calls += 1
            self.started.set()
            self.release.wait(3)
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["已完成但过期的付费结果"]},
                "mode": "enhanced",
                "canGenerateEnhanced": True,
                "signature": "saved-signature",
            }

    service = DelayedPaidService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        wait_until(lambda: not bridge.busy)
        bridge.run("generateEnhanced", {})
        assert service.started.wait(1)
        bridge.invalidateForDataChange()
        service.release.set()
        wait_until(lambda: not bridge.busy)
        assert bridge.summaryData["summary"]["overview"] == ["当前本地摘要"]
        assert bridge.summaryData["lastGeneration"] == {
            "saved": True,
            "date": "2026-10-05",
        }
        assert bridge.summaryData["canGenerateEnhanced"] is False
        assert "已生成并缓存" in bridge.message
        assert "不要重复生成" in bridge.message

        bridge.run("generateEnhanced", {})
        assert service.generate_calls == 1
        assert "不要重复生成" in bridge.error
    finally:
        service.release.set()
        bridge.close()
        tasks.wait_for_shutdown()


def test_cancelled_generation_with_committed_cache_blocks_repeat_until_refresh():
    _application()
    tasks = AsyncTaskService(max_workers=1)

    class PaidService:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.generate_calls = 0
            self.cache_commits = 0

        def current_date(self):
            return "2026-10-05"

        def load(self):
            if self.cache_commits:
                return {
                    "date": "2026-10-05",
                    "summary": {"overview": ["已读取缓存的付费摘要"]},
                    "mode": "enhanced",
                    "canGenerateEnhanced": True,
                    "cacheHit": True,
                    "stale": False,
                }
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["本地摘要"]},
                "mode": "local",
                "canGenerateEnhanced": True,
                "stale": False,
            }

        def generate_enhanced(self):
            self.generate_calls += 1
            # Model a completed, cached provider response whose worker has not
            # yet returned its receipt to the GUI thread.
            self.cache_commits += 1
            self.started.set()
            self.release.wait(3)
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["已缓存的付费摘要"]},
                "mode": "enhanced",
                "canGenerateEnhanced": False,
                "cacheHit": True,
            }

    service = PaidService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        wait_until(lambda: not bridge.busy)
        bridge.run("generateEnhanced", {})
        assert service.started.wait(1)
        bridge.cancel()
        service.release.set()
        wait_until(lambda: not bridge.busy)

        assert service.cache_commits == 1
        assert bridge.summaryData.get("canGenerateEnhanced") is False
        assert "刷新" in bridge.message
        bridge.run("generateEnhanced", {})
        assert service.generate_calls == 1

        bridge.run("refresh", {})
        wait_until(lambda: not bridge.busy)
        assert bridge.summaryData["mode"] == "enhanced"
        assert bridge.summaryData["cacheHit"] is True
        assert "找到并载入" in bridge.message
        assert bridge.summaryData["canGenerateEnhanced"] is True
    finally:
        service.release.set()
        bridge.close()
        tasks.wait_for_shutdown()


def test_unconfirmed_generation_without_cache_can_retry_after_warning_refresh():
    _application()
    tasks = AsyncTaskService(max_workers=1)

    class UncachedPaidService:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.generate_calls = 0

        def current_date(self):
            return "2026-10-05"

        def load(self):
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["本地摘要"]},
                "mode": "local",
                "canGenerateEnhanced": True,
                "cacheHit": False,
                "stale": False,
            }

        def generate_enhanced(self):
            self.generate_calls += 1
            self.started.set()
            self.release.wait(3)
            if self.generate_calls == 1:
                raise DailySummaryStaleError("synthetic input drift after provider response")
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["第二次测试生成"]},
                "mode": "enhanced",
                "canGenerateEnhanced": False,
            }

    service = UncachedPaidService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        wait_until(lambda: not bridge.busy)
        bridge.run("generateEnhanced", {})
        assert service.started.wait(1)
        service.release.set()
        wait_until(lambda: not bridge.busy)

        assert bridge.summaryData["canGenerateEnhanced"] is False
        assert "目录或设置已变化" in bridge.error
        assert "结果未能确认" in bridge.message
        bridge.run("generateEnhanced", {})
        assert service.generate_calls == 1

        bridge.run("refresh", {})
        wait_until(lambda: not bridge.busy)
        assert bridge.summaryData["mode"] == "local"
        assert bridge.summaryData["canGenerateEnhanced"] is True
        assert "未找到匹配的缓存" in bridge.message
        assert "可能已完成但结果未保存" in bridge.message
        assert "再次生成可能产生新费用" in bridge.message

        bridge.run("generateEnhanced", {})
        wait_until(lambda: not bridge.busy)
        assert service.generate_calls == 2
        assert bridge.summaryData["mode"] == "enhanced"
    finally:
        service.release.set()
        bridge.close()
        tasks.wait_for_shutdown()


def test_timed_out_generation_keeps_lock_until_worker_exits_then_reads_cache():
    _application()

    class FastTimeoutTasks(AsyncTaskService):
        def submit(self, key, work, *, slot=None, timeout_seconds=None):
            if key == "quick-daily-summary" and timeout_seconds is not None:
                timeout_seconds = 0.15
            return super().submit(
                key, work, slot=slot, timeout_seconds=timeout_seconds
            )

    class PaidService:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.generate_calls = 0
            self.cached = False

        def current_date(self):
            return "2026-10-05"

        def load(self):
            if self.cached:
                return {
                    "date": "2026-10-05",
                    "summary": {"overview": ["已从缓存读取"]},
                    "mode": "enhanced",
                    "canGenerateEnhanced": True,
                    "cacheHit": True,
                    "stale": False,
                }
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["本地摘要"]},
                "mode": "local",
                "canGenerateEnhanced": True,
                "cacheHit": False,
                "stale": False,
            }

        def generate_enhanced(self):
            self.generate_calls += 1
            self.cached = True
            self.started.set()
            self.release.wait(3)
            return {
                "date": "2026-10-05",
                "summary": {"overview": ["超时后已缓存"]},
                "mode": "enhanced",
                "canGenerateEnhanced": False,
                "cacheHit": True,
            }

    tasks = FastTimeoutTasks(max_workers=1)
    service = PaidService()
    bridge = DailySummaryBridge(service, tasks)
    try:
        bridge.enter_page()
        wait_until(lambda: not bridge.busy)
        bridge.run("generateEnhanced", {})
        assert service.started.wait(1)
        wait_until(lambda: "超时" in bridge.error)
        assert bridge.busy
        assert not bridge.canCancel

        service.release.set()
        wait_until(lambda: not bridge.busy)
        assert bridge.summaryData["canGenerateEnhanced"] is False
        assert "结果未能确认" in bridge.message
        bridge.run("generateEnhanced", {})
        assert service.generate_calls == 1

        bridge.run("refresh", {})
        wait_until(lambda: not bridge.busy)
        assert bridge.summaryData["cacheHit"] is True
        assert "找到并载入" in bridge.message
    finally:
        service.release.set()
        bridge.close()
        tasks.wait_for_shutdown()
