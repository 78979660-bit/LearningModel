from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from study_app.core.dashboard import DashboardState, SubjectSummary, TodoItem


def _state() -> DashboardState:
    active = SubjectSummary(
        name="活动学科",
        initial_score=60,
        window_score=50,
        has_window_records=True,
        mastery_score=55,
        covered_mastery_score=55,
        covered_topic_count=1,
        total_topic_count=1,
        latest_record=date(2026, 9, 12),
        latest_review=date(2026, 9, 12),
        warnings=("三天窗口分数低于标杆",),
    )
    archived = SubjectSummary(
        name="封存学科",
        initial_score=80,
        window_score=None,
        has_window_records=False,
        mastery_score=80,
        covered_mastery_score=80,
        covered_topic_count=1,
        total_topic_count=1,
        latest_record=date(2026, 9, 10),
        latest_review=date(2026, 9, 10),
        warnings=(),
        archived=True,
    )
    return DashboardState(
        start=date(2026, 9, 10),
        today=date(2026, 9, 12),
        benchmark=60,
        subjects=(active, archived),
        todos=(TodoItem("做题", "活动学科 / 主题", "统一待办", "high", 1.0),),
        memory_risks=({"subject": "活动学科", "topic": "主题", "priority": 1.0},),
        bkt_alerts=({"subject": "活动学科", "topic": "主题", "priority": 1.0},),
        raw_records=({"id": 1, "subject": "活动学科"}, {"id": 2, "subject": "封存学科"}),
        data_source="SQLite",
    )


def test_widget_is_an_adapter_over_dashboard_state(monkeypatch):
    import learning_desktop_widget as widget

    dashboard_state = _state()
    calls = []

    def load_dashboard(model_path, records_path, *, today):
        calls.append((model_path, records_path, today))
        return dashboard_state

    monkeypatch.setattr(widget, "load_dashboard_state", load_dashboard)
    state = widget.load_state(today=dashboard_state.today)

    assert len(calls) == 1
    assert state["raw_records"] == list(dashboard_state.raw_records)
    assert state["memory_risks"] == list(dashboard_state.memory_risks)
    assert state["bkt_alerts"] == list(dashboard_state.bkt_alerts)
    assert [item["title"] for item in state["todos"]] == ["活动学科 / 主题"]
    assert state["data_source"] == "SQLite"


def test_archived_subject_is_history_only_not_active_todo(monkeypatch):
    import learning_desktop_widget as widget

    monkeypatch.setattr(widget, "load_dashboard_state", lambda *_args, **_kwargs: _state())
    state = widget.load_state(today=date(2026, 9, 12))

    archived = next(item for item in state["subjects"] if item["name"] == "封存学科")
    assert archived["archived"] is True
    assert all("封存学科" not in item["title"] for item in state["todos"])
    assert all(item.get("subject") != "封存学科" for item in state["memory_risks"])
    assert all(item.get("subject") != "封存学科" for item in state["bkt_alerts"])


def test_dashboard_json_fallback_exposes_source_and_reason(tmp_path, monkeypatch):
    from study_app.core import dashboard

    model_path = tmp_path / "model.json"
    records_path = tmp_path / "records.json"
    sqlite_path = tmp_path / "broken.sqlite"
    model_path.write_text(json.dumps({"subjects": [], "warning_policy": {}}), encoding="utf-8")
    records_path.write_text(
        json.dumps({"records": [{"id": 1, "date": "2026-09-12"}]}),
        encoding="utf-8",
    )
    sqlite_path.write_bytes(b"not sqlite")
    monkeypatch.setattr(dashboard, "DEFAULT_DB_PATH", sqlite_path)

    state = dashboard.load_dashboard_state(
        model_path,
        records_path,
        today=date(2026, 9, 12),
    )

    assert state.data_source == "JSON fallback"
    assert "SQLite 读取失败" in state.data_source_reason
    assert [record["id"] for record in state.raw_records] == [1]
