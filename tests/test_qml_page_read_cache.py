from types import SimpleNamespace
import pytest
from study_app.quick.page_read_cache import PageReadCache

@pytest.mark.parametrize("changed", ["db", "model", "records", "db-wal", "db-journal"])
def test_page_snapshot_invalidates_on_files_and_manual_refresh(tmp_path, changed):
    service = SimpleNamespace(db_path=tmp_path/'db', model_path=tmp_path/'model', records_path=tmp_path/'records')
    for path in (service.db_path, service.model_path, service.records_path):
        path.write_text('initial')
    cache = PageReadCache(service)
    calls = []
    def load():
        calls.append(1)
        return {"items": [len(calls)]}
    first = cache.read('subject', load, reuse=True)
    first['items'].append(99)
    assert cache.read('subject', load, reuse=True) == {"items": [1]}
    (tmp_path/changed).write_text('changed-content')
    assert cache.read('subject', load, reuse=True) == {"items": [2]}
    assert cache.read('subject', load) == {"items": [3]}
    assert cache.read('other-subject', load, reuse=True) == {"items": [4]}


def test_snapshot_expiry_and_mutation_during_read(tmp_path, monkeypatch):
    import study_app.quick.page_read_cache as module
    service = SimpleNamespace(db_path=tmp_path/'db', model_path=tmp_path/'model', records_path=tmp_path/'records')
    cache = PageReadCache(service)
    clock = [0]
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0])
    calls = []
    def load():
        calls.append(1)
        return len(calls)
    assert cache.read('x', load, True) == 1
    clock[0] = 31
    assert cache.read('x', load, True) == 2
    def mutating_load():
        service.db_path.write_text('new')
        return 100
    cache.read('y', mutating_load)
    assert 'y' not in cache.entries
