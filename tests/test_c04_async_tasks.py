from __future__ import annotations

import threading
import time

import pytest

from study_app.core.async_tasks import AsyncTaskService, TaskStatus


def _await_status(handle, status: TaskStatus, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if handle.snapshot().status == status:
            return
        time.sleep(0.005)
    raise AssertionError(f"task did not reach {status}: {handle.snapshot()}")


def test_single_task_state_transitions_and_result():
    started = threading.Event()
    release = threading.Event()
    with AsyncTaskService(max_workers=1) as service:
        handle = service.submit("query", lambda: (started.set(), release.wait(1), 42)[2])
        assert started.wait(1)
        _await_status(handle, TaskStatus.RUNNING)
        release.set()
        snapshot = handle.wait(1)
        assert snapshot.status == TaskStatus.SUCCEEDED
        assert snapshot.result == 42
        assert snapshot.error is None


def test_queued_task_moves_pending_to_running_then_success():
    blocker_started = threading.Event()
    blocker_release = threading.Event()
    second_started = threading.Event()
    with AsyncTaskService(max_workers=1) as service:
        blocker = service.submit(
            "blocker", lambda: (blocker_started.set(), blocker_release.wait(1))[1]
        )
        assert blocker_started.wait(1)
        second = service.submit("queued", lambda: (second_started.set(), 9)[1])
        assert second.snapshot().status == TaskStatus.PENDING
        blocker_release.set()
        assert blocker.wait(1).status == TaskStatus.SUCCEEDED
        assert second_started.wait(1)
        assert second.wait(1).status == TaskStatus.SUCCEEDED
        assert second.snapshot().result == 9


def test_same_key_deduplicates_running_job():
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def work():
        nonlocal calls
        calls += 1
        started.set()
        release.wait(1)
        return "done"

    with AsyncTaskService(max_workers=2) as service:
        first = service.submit("same", work)
        assert started.wait(1)
        second = service.submit("same", lambda: "unexpected")
        assert first is second
        release.set()
        assert first.wait(1).result == "done"
        assert calls == 1


def test_timeout_excludes_eventual_slow_result():
    release = threading.Event()
    with AsyncTaskService(max_workers=1) as service:
        handle = service.submit(
            "slow", lambda: (release.wait(1), "late")[1], timeout_seconds=0.02
        )
        assert handle.wait(1).status == TaskStatus.TIMED_OUT
        release.set()
        time.sleep(0.02)
        snapshot = handle.snapshot()
        assert snapshot.status == TaskStatus.TIMED_OUT
        assert snapshot.result is None


def test_cancel_running_job_excludes_late_result():
    started = threading.Event()
    release = threading.Event()
    with AsyncTaskService(max_workers=1) as service:
        handle = service.submit(
            "cancel", lambda: (started.set(), release.wait(1), "late")[2]
        )
        assert started.wait(1)
        assert service.cancel("cancel")
        assert not service.cancel("cancel")
        assert handle.wait(1).status == TaskStatus.CANCELLED
        release.set()
        time.sleep(0.02)
        assert handle.snapshot().result is None


def test_cancel_queued_job_prevents_execution():
    blocker_started = threading.Event()
    blocker_release = threading.Event()
    queued_started = threading.Event()
    with AsyncTaskService(max_workers=1) as service:
        service.submit(
            "blocker", lambda: (blocker_started.set(), blocker_release.wait(1))[1]
        )
        assert blocker_started.wait(1)
        queued = service.submit("queued", lambda: queued_started.set())
        assert queued.snapshot().status == TaskStatus.PENDING
        assert service.cancel("queued")
        blocker_release.set()
        assert queued.wait(1).status == TaskStatus.CANCELLED
        assert not queued_started.wait(0.03)


def test_out_of_order_completion_discards_older_generation():
    old_started = threading.Event()
    old_release = threading.Event()
    with AsyncTaskService(max_workers=2) as service:
        old = service.submit(
            "query:old", lambda: (old_started.set(), old_release.wait(1), "old")[2],
            slot="query-page",
        )
        assert old_started.wait(1)
        new = service.submit("query:new", lambda: "new", slot="query-page")
        assert new.wait(1).status == TaskStatus.SUCCEEDED
        assert new.snapshot().result == "new"
        old_release.set()
        assert old.wait(1).status == TaskStatus.STALE
        assert old.snapshot().result is None


def test_input_invalidation_discards_previous_result():
    release = threading.Event()
    with AsyncTaskService(max_workers=1) as service:
        handle = service.submit("task", lambda: (release.wait(1), 7)[1], slot="page")
        service.invalidate("page")
        assert handle.wait(1).status == TaskStatus.STALE
        release.set()
        assert handle.snapshot().result is None


def test_failure_and_closed_service_are_explicit():
    with AsyncTaskService(max_workers=1) as service:
        handle = service.submit("failure", lambda: 1 / 0)
        snapshot = handle.wait(1)
        assert snapshot.status == TaskStatus.FAILED
        assert isinstance(snapshot.error, ZeroDivisionError)
        assert snapshot.result is None
    with pytest.raises(RuntimeError, match="closed"):
        service.submit("after-close", lambda: 1)


def test_invalid_timeout_is_rejected_without_starting_job():
    with AsyncTaskService() as service:
        with pytest.raises(ValueError, match="positive"):
            service.submit("invalid", lambda: 1, timeout_seconds=0)
