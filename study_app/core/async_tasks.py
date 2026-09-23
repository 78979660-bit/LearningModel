from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Hashable

from pathlib import Path


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    STALE = "stale"


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
        TaskStatus.CANCELLED,
        TaskStatus.STALE,
    }
)


class StaleSubjectTaskError(RuntimeError):
    """Raised immediately before save when a task's lifecycle revision is stale."""


@dataclass(frozen=True)
class CatalogRevisionToken:
    db_path: str
    catalog_revision: int


@dataclass(frozen=True)
class SubjectRevisionToken(CatalogRevisionToken):
    subject_key: str
    object_version: int


def capture_catalog_revision(db_path: Path | str) -> CatalogRevisionToken | None:
    """Return None only when F5 is not installed; never installs schema."""
    from study_app.data.database import (
        DatabaseNotInitializedError,
        connect_readonly,
        f5_catalog_is_installed,
    )
    from study_app.data.subject_repository import (
        SubjectCatalogRepository,
        SubjectLifecycleNotInstalledError,
    )

    try:
        with connect_readonly(db_path) as connection:
            if not f5_catalog_is_installed(connection):
                return None
        repository = SubjectCatalogRepository(db_path)
        revision = repository.catalog_revision()
    except (DatabaseNotInitializedError, SubjectLifecycleNotInstalledError):
        return None
    return CatalogRevisionToken(str(Path(db_path)), revision)


def capture_subject_revision(
    db_path: Path | str, subject_name: object
) -> SubjectRevisionToken | None:
    from study_app.data.database import (
        DatabaseNotInitializedError,
        connect_readonly,
        f5_catalog_is_installed,
    )
    from study_app.data.subject_repository import (
        SubjectCatalogRepository,
        SubjectLifecycleNotInstalledError,
    )

    try:
        with connect_readonly(db_path) as connection:
            if not f5_catalog_is_installed(connection):
                return None
        repository = SubjectCatalogRepository(db_path)
        subject = repository.resolve_subject(subject_name)
        revision = repository.catalog_revision()
    except (DatabaseNotInitializedError, SubjectLifecycleNotInstalledError):
        return None
    if subject.lifecycle_status != "active":
        raise StaleSubjectTaskError(f"{subject_name} 已归档，不能启动异步任务")
    return SubjectRevisionToken(
        str(Path(db_path)), revision, subject.subject_key, subject.object_version
    )


def revalidate_catalog_revision(token: CatalogRevisionToken | None) -> None:
    if token is None:
        return
    from study_app.data.subject_repository import SubjectCatalogRepository

    actual = SubjectCatalogRepository(token.db_path).catalog_revision()
    if actual != token.catalog_revision:
        raise StaleSubjectTaskError(
            f"目录修订已变化：started={token.catalog_revision}, current={actual}"
        )


def revalidate_subject_revision(token: SubjectRevisionToken | None) -> None:
    if token is None:
        return
    from study_app.data.subject_repository import SubjectCatalogRepository

    repository = SubjectCatalogRepository(token.db_path)
    subject = repository.get_subject(token.subject_key)
    actual_revision = repository.catalog_revision()
    if (
        actual_revision != token.catalog_revision
        or subject.object_version != token.object_version
        or subject.lifecycle_status != "active"
    ):
        raise StaleSubjectTaskError(
            "异步任务结果已过期：学科生命周期、对象版本或目录修订已变化"
        )


@dataclass(frozen=True)
class TaskSnapshot:
    key: Hashable
    slot: Hashable
    generation: int
    status: TaskStatus
    result: Any = None
    error: BaseException | None = None

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES


class TaskHandle:
    def __init__(self, key: Hashable, slot: Hashable, generation: int) -> None:
        self.key = key
        self.slot = slot
        self.generation = generation
        self._condition = threading.Condition()
        self._status = TaskStatus.PENDING
        self._result: Any = None
        self._error: BaseException | None = None
        self._future: Future[Any] | None = None
        self._timer: threading.Timer | None = None

    def snapshot(self) -> TaskSnapshot:
        with self._condition:
            return TaskSnapshot(
                self.key,
                self.slot,
                self.generation,
                self._status,
                self._result,
                self._error,
            )

    def wait(self, timeout: float | None = None) -> TaskSnapshot:
        with self._condition:
            self._condition.wait_for(
                lambda: self._status in TERMINAL_STATUSES,
                timeout=timeout,
            )
            return TaskSnapshot(
                self.key,
                self.slot,
                self.generation,
                self._status,
                self._result,
                self._error,
            )

    def _transition(
        self,
        status: TaskStatus,
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> bool:
        with self._condition:
            if self._status in TERMINAL_STATUSES:
                return False
            if status == TaskStatus.RUNNING and self._status != TaskStatus.PENDING:
                return False
            self._status = status
            if status == TaskStatus.SUCCEEDED:
                self._result = result
            elif status == TaskStatus.FAILED:
                self._error = error
            if status in TERMINAL_STATUSES:
                if self._timer is not None:
                    self._timer.cancel()
                self._condition.notify_all()
            return True


class AsyncTaskService:
    """Pure service-layer worker pool with deduplication and stale-result gating.

    Jobs cannot be forcibly interrupted once running. Timeout and cancel make
    their eventual result unavailable to callers, while the worker exits later.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="study-async-task",
        )
        self._lock = threading.RLock()
        self._active: dict[Hashable, TaskHandle] = {}
        self._generations: dict[Hashable, int] = {}
        self._closed = False

    def submit(
        self,
        key: Hashable,
        work: Callable[[], Any],
        *,
        slot: Hashable | None = None,
        timeout_seconds: float | None = None,
    ) -> TaskHandle:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        result_slot = key if slot is None else slot
        with self._lock:
            if self._closed:
                raise RuntimeError("async task service is closed")
            existing = self._active.get(key)
            if existing is not None and not existing.snapshot().finished:
                return existing
            generation = self._generations.get(result_slot, 0) + 1
            self._generations[result_slot] = generation
            handle = TaskHandle(key, result_slot, generation)
            self._active[key] = handle

            def run() -> None:
                if not handle._transition(TaskStatus.RUNNING):
                    return
                try:
                    result = work()
                except Exception as error:
                    with self._lock:
                        stale = self._generations.get(result_slot) != generation
                    handle._transition(
                        TaskStatus.STALE if stale else TaskStatus.FAILED,
                        error=error,
                    )
                else:
                    with self._lock:
                        stale = self._generations.get(result_slot) != generation
                    handle._transition(
                        TaskStatus.STALE if stale else TaskStatus.SUCCEEDED,
                        result=result,
                    )

            try:
                future = self._executor.submit(run)
                handle._future = future
                if timeout_seconds is not None:
                    timer = threading.Timer(
                        timeout_seconds,
                        lambda: self._finish_without_result(
                            handle, TaskStatus.TIMED_OUT
                        ),
                    )
                    timer.daemon = True
                    handle._timer = timer
                    timer.start()
            except Exception:
                self._active.pop(key, None)
                raise
            return handle

    def cancel(self, key: Hashable) -> bool:
        with self._lock:
            handle = self._active.get(key)
            if handle is None:
                return False
            return self._finish_without_result(handle, TaskStatus.CANCELLED)

    def invalidate(self, slot: Hashable) -> None:
        """Discard all pending results for a changed page/input generation."""
        with self._lock:
            self._generations[slot] = self._generations.get(slot, 0) + 1
            for handle in self._active.values():
                if handle.slot == slot:
                    self._finish_without_result(handle, TaskStatus.STALE)

    def _finish_without_result(self, handle: TaskHandle, status: TaskStatus) -> bool:
        changed = handle._transition(status)
        if changed and handle._future is not None:
            handle._future.cancel()
        return changed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for handle in self._active.values():
                self._finish_without_result(handle, TaskStatus.CANCELLED)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> AsyncTaskService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
