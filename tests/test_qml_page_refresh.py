import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from study_app.core.async_tasks import AsyncTaskService
from study_app.quick.page_refresh import PageRefresh
from study_app.quick.self_test import wait_until


@pytest.fixture
def observer(tmp_path):
    app = QApplication.instance() or QApplication([])
    parent = QObject()
    tasks = AsyncTaskService(max_workers=1)
    service = SimpleNamespace(db_path=tmp_path / 'db', model_path=tmp_path / 'model',
                              records_path=tmp_path / 'records')
    for path in vars(service).values():
        path.write_text('initial')
    watcher = PageRefresh(service, tasks, 'freshness-test', parent)
    yield watcher, tasks, service
    watcher.cancel()
    tasks.close()
    tasks.wait_for_shutdown()


def establish(watcher, tasks):
    handle = tasks.submit('read', lambda: watcher.read('plan', lambda: {'data': 'loaded'}))
    wait_until(lambda: handle.work_finished)
    watcher.accept(handle.snapshot().result)


@pytest.mark.parametrize('filename', ['db', 'model', 'records', 'db-wal', 'db-journal'])
def test_unchanged_reentry_is_quiet_and_external_change_refreshes(observer, filename):
    watcher, tasks, service = observer
    establish(watcher, tasks)
    calls, threads = [], []
    original = watcher._files.signature
    def signature():
        threads.append(threading.get_ident())
        return original()
    watcher._files.signature = signature
    watcher.request('plan', lambda: calls.append('changed'))
    wait_until(lambda: watcher._handle is None)
    assert calls == []
    assert threads and all(t != threading.get_ident() for t in threads)
    (service.db_path.parent / filename).write_text('new contents')
    watcher.request('plan', lambda: calls.append('changed'))
    wait_until(lambda: watcher._handle is None)
    assert calls == ['changed']


def test_leaving_while_probe_waits_does_not_refresh_new_page(observer):
    watcher, tasks, service = observer
    establish(watcher, tasks)
    service.model_path.write_text('changed')
    release = threading.Event()
    blocker = tasks.submit('block', lambda: release.wait(3))
    calls = []
    watcher.request('plan', lambda: calls.append('late'))
    pending = watcher._handle
    watcher.cancel()
    release.set()
    wait_until(lambda: blocker.work_finished and pending.work_finished)
    watcher._poll()
    assert calls == []


def test_change_during_read_never_certifies_stale_data(observer):
    watcher, tasks, service = observer
    def mutate():
        service.records_path.write_text('changed during read')
        return {'data': 'stale'}
    handle = tasks.submit('read', lambda: watcher.read('plan', mutate))
    wait_until(lambda: handle.work_finished)
    watcher.accept(handle.snapshot().result)
    calls = []
    watcher.request('plan', lambda: calls.append('reload'))
    assert calls == ['reload']
