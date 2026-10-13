"""Check page freshness on the worker without putting the form into a busy state."""
from PySide6.QtCore import QObject, QTimer, Slot

from study_app.core.async_tasks import TaskStatus
from study_app.quick.page_read_cache import PageReadCache


class PageRefresh(QObject):
    def __init__(self, service, tasks, channel, parent):
        super().__init__(parent)
        self._files = PageReadCache(service)
        self._tasks, self._channel = tasks, channel
        self._known = {}
        self._handle = self._callback = None
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)

    def read(self, key, loader):
        # Called only on the serialized worker. Do not certify a snapshot if
        # files changed while it was assembled.
        before = self._files.signature() if self._files.paths else None
        result = loader()
        after = self._files.signature() if self._files.paths else None
        return {**result, "_pageRead": (key, after if before == after else None)}

    def accept(self, envelope):
        if "_pageRead" in envelope:
            key, signature = envelope["_pageRead"]
            self._known[key] = signature

    def request(self, key, callback):
        self.cancel()
        baseline = self._known.get(key)
        if baseline is None:
            callback()
            return
        try:
            self._handle = self._tasks.submit(self._channel, self._files.signature, timeout_seconds=10)
        except Exception:
            callback()
            return
        self._baseline = baseline
        self._callback = callback
        self._timer.start()

    @Slot()
    def _poll(self):
        if self._handle is None or not self._handle.work_finished:
            return
        result = self._handle.snapshot()
        callback, baseline = self._callback, self._baseline
        self.cancel()
        if callback and (result.status != TaskStatus.SUCCEEDED or result.result != baseline):
            callback()

    def cancel(self):
        # A file-stat task may finish, but cannot refresh a page after leaving.
        self._timer.stop()
        self._handle = self._callback = None
