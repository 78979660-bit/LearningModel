from __future__ import annotations

from datetime import date

from study_app.core.computation_context import DashboardComputationContext
from study_app.core.scoped_topic_cache import ScopedTopicCache


def _context(cache, records, *, day=date(2026, 9, 15), phase="regular",
             scope="database", model_revision=1):
    topics = [{"name": "哈希表"}, {"name": "二叉搜索树"}]
    model = {
        "revision": model_revision,
        "subjects": [{"name": "数据结构", "modules": [
            {"name": "基础", "topics": topics}
        ]}],
    }
    return DashboardComputationContext(
        model, records, day, {}, shared_cache=cache,
        scope={"source": scope},
        subject_phases={"数据结构": {"phase": phase, "exam_scope": None}},
    ), topics


def test_changed_record_invalidates_only_relevant_topic(monkeypatch):
    from study_app.core import computation_context

    calls = []
    memory_calls = []

    def bkt(_subject, _module, topic, records, _policy, *, as_of_date):
        calls.append(topic["name"])
        return {"topic": topic["name"], "score": len(records),
                "as_of_date": as_of_date.isoformat()}

    monkeypatch.setattr(computation_context, "topic_bkt_state", bkt)
    monkeypatch.setattr(
        computation_context, "topic_memory_state",
        lambda _subject, _module, topic, _records, _day, _policy, *, bkt_state:
            memory_calls.append(topic["name"]) or {"topic": topic["name"], "bkt": bkt_state},
    )
    cache = ScopedTopicCache()
    records = [
        {"id": 1, "subject": "数据结构", "topic": "哈希表", "date": "2026-09-14", "score": 50},
        {"id": 2, "subject": "数据结构", "topic": "二叉搜索树", "date": "2026-09-14", "score": 60},
    ]
    first, topics = _context(cache, records)
    for topic in topics:
        first.bkt_state("数据结构", "基础", topic)
        first.memory_state("数据结构", "基础", topic)
    assert calls == ["哈希表", "二叉搜索树"]
    assert memory_calls == calls

    changed = [{**records[0], "score": 80}, records[1]]
    second, topics = _context(cache, changed)
    for topic in topics:
        second.bkt_state("数据结构", "基础", topic)
        second.memory_state("数据结构", "基础", topic)
    assert calls == ["哈希表", "二叉搜索树", "哈希表"]
    assert memory_calls == calls

    # Caller mutation does not poison the reusable value.
    second.bkt_state("数据结构", "基础", topics[1])["score"] = -1
    third, topics = _context(cache, changed)
    assert third.bkt_state("数据结构", "基础", topics[1])["score"] == 2


def test_date_phase_scope_and_model_version_cannot_hit_old_result(monkeypatch):
    from study_app.core import computation_context

    calls = []

    def bkt(_subject, _module, topic, _records, _policy, *, as_of_date):
        calls.append(as_of_date)
        return {"topic": topic["name"], "call": len(calls)}

    monkeypatch.setattr(computation_context, "topic_bkt_state", bkt)
    cache = ScopedTopicCache()
    records = [{"id": 1, "subject": "数据结构", "topic": "哈希表", "date": "2026-09-14"}]
    options = [
        {}, {"day": date(2026, 9, 16)}, {"phase": "final_review"},
        {"scope": "json"}, {"model_revision": 2},
    ]
    for option in options:
        context, topics = _context(cache, records, **option)
        context.bkt_state("数据结构", "基础", topics[0])
    assert len(calls) == 5
    repeat, topics = _context(cache, records)
    assert repeat.bkt_state("数据结构", "基础", topics[0])["call"] == 1
    assert len(calls) == 5


def test_uncertain_dependencies_recompute_instead_of_guessing(monkeypatch):
    from study_app.core import computation_context

    calls = []
    monkeypatch.setattr(
        computation_context, "topic_bkt_state",
        lambda *_args, **_kwargs: calls.append(1) or {"call": len(calls)},
    )
    cache = ScopedTopicCache()
    for _ in range(2):
        context, topics = _context(cache, [])
        context.scope = None
        context.bkt_state("数据结构", "基础", topics[0])
    assert len(calls) == 2


def test_dashboard_cold_and_warm_results_are_identical(tmp_path, monkeypatch):
    from test_c02_computation_context import _write_fixture
    from study_app.core import computation_context, dashboard, scoped_topic_cache

    model_path, records_path, db_path = _write_fixture(tmp_path)
    monkeypatch.setattr(dashboard, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        scoped_topic_cache, "dashboard_topic_cache", ScopedTopicCache()
    )
    calls = []
    original = computation_context.topic_bkt_state

    def tracked(*args, **kwargs):
        calls.append(args[2]["name"])
        return original(*args, **kwargs)

    monkeypatch.setattr(computation_context, "topic_bkt_state", tracked)
    first = dashboard.load_dashboard_state(
        model_path, records_path, today=date(2026, 9, 15)
    )
    cold_calls = len(calls)
    second = dashboard.load_dashboard_state(
        model_path, records_path, today=date(2026, 9, 15)
    )
    assert cold_calls > 0
    assert len(calls) == cold_calls
    assert second == first
