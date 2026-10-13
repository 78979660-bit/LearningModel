from __future__ import annotations

import json
from datetime import datetime

import pytest

from study_app.core.budgeted_day_plan import BudgetPlanInput, build_budgeted_day_plan
from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source
from study_app.data import database
from study_app.data.database import add_learning_record, ensure_seeded_database
from study_app.quick.dashboard_service import DashboardService


AS_OF = "2026-10-04"


def _model_payload() -> dict:
    return {
        "model_name": "qml-dashboard-isolated-test",
        "warning_policy": {
            "period_days": 3,
            "period_benchmark_score": 55,
            "stale_review_days": 6,
            "bkt_model": {"enabled": False},
        },
        "subjects": [
            {
                "name": "测试活动学科",
                "mastery": 0.4,
                "modules": [
                    {
                        "name": "测试模块",
                        "weight": 1,
                        "mastery": 0.4,
                        "status": "learning",
                        "topics": [
                            {
                                "name": "测试知识点",
                                "status": "learning",
                                "mastery": 0.4,
                                "importance": 1,
                                "difficulty": 0.5,
                            }
                        ],
                    }
                ],
            },
            {
                "name": "测试归档学科",
                "mastery": 0.8,
                "lifecycle": {"status": "archived"},
                "modules": [
                    {
                        "name": "归档模块",
                        "weight": 1,
                        "mastery": 0.8,
                        "topics": [
                            {
                                "name": "归档知识点",
                                "status": "learning",
                                "mastery": 0.8,
                                "importance": 1,
                                "difficulty": 0.5,
                            }
                        ],
                    }
                ],
            },
        ],
    }


def _seeded_service_paths(tmp_path):
    db_path = tmp_path / "isolated-dashboard.sqlite"
    model_path = tmp_path / "minimal-model.json"
    records_path = tmp_path / "empty-records.json"
    model_path.write_text(
        json.dumps(_model_payload(), ensure_ascii=False), encoding="utf-8"
    )
    records_path.write_text('{"records": []}', encoding="utf-8")
    ensure_seeded_database(db_path, model_path=model_path)
    return db_path, model_path, records_path


def test_dashboard_service_uses_isolated_sqlite_and_refreshes_real_activity(
    tmp_path,
):
    db_path, model_path, records_path = _seeded_service_paths(tmp_path)
    service = DashboardService(
        db_path=db_path,
        model_path=model_path,
        records_path=records_path,
        today=AS_OF,
    )

    before = service.load()

    assert before["date"] == AS_OF
    assert before["source"] == "SQLite"
    assert before["activeCount"] == 1
    assert before["archivedCount"] == 1
    assert [item["name"] for item in before["subjects"]] == ["测试活动学科"]
    assert before["recordCount"] == 0
    assert before["recentRecords"] == []

    add_learning_record(
        {
            "date": AS_OF,
            "subject": "测试活动学科",
            "module": "测试模块",
            "topic": "测试知识点",
            "activity": "review",
            "source": "outside_class",
            "score": 86,
            "duration_minutes": 20,
            "note": "隔离测试记录",
        },
        db_path=db_path,
        model_path=model_path,
    )

    after = service.load()

    assert after["source"] == "SQLite"
    assert after["recordCount"] == 1
    assert [item["name"] for item in after["subjects"]] == ["测试活动学科"]
    assert after["subjects"][0]["latestRecord"] == AS_OF
    assert after["subjects"][0]["windowScore"] == 86
    assert after["subjects"][0]["covered"] == 1
    assert after["recentRecords"] == [
        {"date": AS_OF, "subject": "测试活动学科", "detail": "测试知识点"}
    ]
    assert datetime.fromisoformat(after["updatedAt"]).tzinfo is not None


def test_dashboard_service_runs_optional_initializer_once(tmp_path):
    db_path, model_path, records_path = _seeded_service_paths(tmp_path)
    calls = []
    service = DashboardService(
        db_path=db_path,
        model_path=model_path,
        records_path=records_path,
        today=AS_OF,
        initialize=lambda: calls.append("initialized"),
    )

    service.load()
    service.load()

    assert calls == ["initialized"]


def _budgeted_plan():
    candidate = PlanCandidate(
        task_id=task_id_for_source(
            "测试活动学科", "model_topic_practice", "topic:test-dashboard"
        ),
        subject_id="测试活动学科",
        source_kind="model_topic_practice",
        source_id="topic:test-dashboard",
        title="经确认的隔离任务",
        priority_evidence={"priority": 0.5},
        estimated_minutes=25,
        estimate_source="user",
    )
    return build_budgeted_day_plan(
        BudgetPlanInput(
            AS_OF, 60, ("测试活动学科",), as_of_date=AS_OF
        ),
        CandidateCollection((candidate,), ()),
    )


def test_dashboard_service_projects_confirmed_budget_plan_dto(tmp_path):
    db_path, model_path, records_path = _seeded_service_paths(tmp_path)
    database.create_budgeted_day_plan(AS_OF, 60, _budgeted_plan(), db_path)
    service = DashboardService(db_path, model_path, records_path, today=AS_OF)

    snapshot = service.load()

    assert snapshot["todayPlan"]["status"] == "confirmed"
    assert snapshot["todayPlan"]["budgetMinutes"] == 60
    assert snapshot["todayPlan"]["plannedMinutes"] == 25
    assert snapshot["todayPlan"]["remainingMinutes"] == 35
    assert snapshot["todayPlan"]["items"] == [
        {"title": "经确认的隔离任务", "checked": False, "minutes": 25}
    ]
    assert "预算 60 分钟" in snapshot["todayPlan"]["detail"]


def test_dashboard_service_projects_legacy_plan_dto_without_claiming_a_budget(
    tmp_path,
):
    db_path, model_path, records_path = _seeded_service_paths(tmp_path)
    database.create_study_plan(
        None,
        AS_OF,
        AS_OF,
        "legacy-dashboard-test",
        {"short": ["legacy"]},
        [
            {
                "section_key": "short",
                "section_title": "今日计划",
                "day_index": 1,
                "item_type": "check",
                "item_text": "旧格式隔离任务",
                "item_hash": "legacy-dashboard-test",
            }
        ],
        db_path,
    )
    service = DashboardService(db_path, model_path, records_path, today=AS_OF)

    snapshot = service.load()

    assert snapshot["todayPlan"]["status"] == "legacy"
    assert snapshot["todayPlan"]["items"] == []
    assert "时长未知" in snapshot["todayPlan"]["detail"]
    assert snapshot["recordCount"] == 0


def test_plan_read_failure_degrades_plan_only_and_sanitizes_details(
    tmp_path, monkeypatch
):
    import study_app.quick.dashboard_service as dashboard_service_module

    db_path, model_path, records_path = _seeded_service_paths(tmp_path)
    add_learning_record(
        {
            "date": AS_OF,
            "subject": "测试活动学科",
            "module": "测试模块",
            "topic": "测试知识点",
            "activity": "review",
            "source": "outside_class",
            "score": 86,
        },
        db_path=db_path,
        model_path=model_path,
    )
    secret_detail = "api_key=hidden C:\\private\\budget.sqlite"

    def fail_plan_read(*_args, **_kwargs):
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(
        dashboard_service_module, "get_budgeted_day_plan", fail_plan_read
    )
    service = DashboardService(db_path, model_path, records_path, today=AS_OF)

    snapshot = service.load()

    assert snapshot["todayPlan"]["status"] == "error"
    assert secret_detail not in snapshot["todayPlan"]["detail"]
    assert "budget.sqlite" not in snapshot["todayPlan"]["detail"]
    assert snapshot["date"] == AS_OF
    assert snapshot["source"] == "SQLite"
    assert snapshot["activeCount"] == 1
    assert snapshot["recordCount"] == 1
    assert snapshot["subjects"][0]["name"] == "测试活动学科"
    assert snapshot["recentRecords"][0]["subject"] == "测试活动学科"


def test_dashboard_diagnostics_expose_only_sanitized_dto_text(tmp_path, monkeypatch):
    import study_app.quick.dashboard_service as dashboard_service_module
    from study_app.core.dashboard import DashboardState, SubjectSummary

    secret_detail = "credential=hidden C:\\Users\\private\\learning.sqlite"
    as_of = datetime.fromisoformat(f"{AS_OF}T00:00:00").date()
    subject = SubjectSummary(
        name="隔离活动学科",
        initial_score=60,
        window_score=None,
        has_window_records=False,
        mastery_score=60,
        covered_mastery_score=60,
        covered_topic_count=0,
        total_topic_count=0,
        latest_record=None,
        latest_review=None,
        warnings=(),
        archived=False,
    )
    state = DashboardState(
        start=as_of,
        today=as_of,
        benchmark=55,
        subjects=(subject,),
        todos=(),
        memory_risks=(),
        bkt_alerts=(),
        data_source="JSON fallback",
        data_source_reason=secret_detail,
        catalog_diagnostic=secret_detail,
    )
    monkeypatch.setattr(
        dashboard_service_module, "capture_catalog_revision", lambda _path: None
    )
    monkeypatch.setattr(
        dashboard_service_module, "revalidate_catalog_revision", lambda _token: None
    )
    monkeypatch.setattr(
        dashboard_service_module, "load_dashboard_state", lambda **_kwargs: state
    )
    service = DashboardService(
        tmp_path / "unused.sqlite",
        tmp_path / "unused-model.json",
        tmp_path / "unused-records.json",
        today=AS_OF,
    )
    monkeypatch.setattr(
        service,
        "_today_plan",
        lambda _date: {"status": "empty", "title": "", "detail": "", "items": []},
    )

    snapshot = service.load()
    public_text = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["sourceReason"] == (
        "SQLite 当前不可用；使用 JSON 兼容记录，请在设置页检查数据。"
    )
    assert snapshot["catalogDiagnostic"] == (
        "学科目录使用兼容模式；请在设置页检查目录状态。"
    )
    assert secret_detail not in public_text
    assert "credential=hidden" not in public_text
    assert "learning.sqlite" not in public_text


def test_catalog_revision_drift_rejects_the_loaded_dashboard_snapshot(
    tmp_path, monkeypatch
):
    import study_app.quick.dashboard_service as dashboard_service_module
    from study_app.core.async_tasks import StaleSubjectTaskError

    db_path, model_path, records_path = _seeded_service_paths(tmp_path)
    service = DashboardService(db_path, model_path, records_path, today=AS_OF)
    real_load_dashboard_state = dashboard_service_module.load_dashboard_state

    def load_then_advance_catalog(**kwargs):
        snapshot_state = real_load_dashboard_state(**kwargs)
        with database.connect(db_path) as connection:
            connection.execute(
                "UPDATE subject_catalog_state "
                "SET catalog_revision = catalog_revision + 1 WHERE singleton = 1"
            )
        return snapshot_state

    monkeypatch.setattr(
        dashboard_service_module, "load_dashboard_state", load_then_advance_catalog
    )

    with pytest.raises(StaleSubjectTaskError, match="目录修订已变化"):
        service.load()
