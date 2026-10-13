"""A confirmation commits all budget inputs and its plan, or none of them."""
import sqlite3

import pytest

from study_app.data.db_runtime import connect, connect_readonly
from study_app.data.plan_repository import get_budgeted_day_plan
from study_app.quick import plan_service
from study_app.quick.plan_service import StalePlanPreviewError
import test_qml_plan_service as plan_test_support


@pytest.fixture
def profile():
    fixture = plan_test_support.QmlPlanServiceTests()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def preview(profile, *, minutes=90, estimate=45, exam="2026-12-02"):
    return profile.service.preview_budget({
        "scope": "测试学科", "budget_minutes": minutes,
        "exam_dates": {"测试学科": exam},
        "estimate_minutes": {profile.candidate.task_id: estimate},
    })["token"]


def snapshot(profile):
    tables = ("study_day_budgets", "study_subject_exam_dates", "study_task_estimates",
              "study_plans", "study_plan_items", "study_plan_item_states")
    with connect_readonly(profile.db_path) as connection:
        return {table: [tuple(row) for row in connection.execute(
            f"SELECT * FROM {table} ORDER BY rowid")] for table in tables}


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("step", ["save_study_day_budget", "save_subject_exam_date",
                                  "save_task_estimate", "create_budgeted_day_plan"])
def test_intermediate_failure_rolls_back_every_input_and_plan(profile, monkeypatch, existing, step):
    if existing:
        profile.service.confirm_budget(preview(profile, minutes=60, estimate=30, exam="2026-11-01"))
    token = preview(profile)
    before = snapshot(profile)
    original = getattr(plan_service, step)

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected write-stage failure")

    monkeypatch.setattr(plan_service, step, fail_after_write)
    with pytest.raises(RuntimeError, match="write-stage"):
        profile.service.confirm_budget(token)
    assert snapshot(profile) == before
    with pytest.raises(StalePlanPreviewError):
        profile.service.confirm_budget(token)


def test_sql_item_failure_also_restores_previously_active_plan(profile):
    profile.service.confirm_budget(preview(profile, minutes=60, estimate=30, exam="2026-11-01"))
    token = preview(profile)
    before = snapshot(profile)
    with connect(profile.db_path) as connection:
        connection.execute("""CREATE TRIGGER fail_budget_item BEFORE INSERT ON study_plan_items
            BEGIN SELECT RAISE(ABORT, 'injected item failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="item failure"):
        profile.service.confirm_budget(token)
    assert snapshot(profile) == before


def test_failure_at_commit_rolls_back_all_inputs_and_plan(profile, monkeypatch):
    profile.service.confirm_budget(preview(profile, minutes=60, estimate=30, exam="2026-11-01"))
    token = preview(profile)
    before = snapshot(profile)
    with connect(profile.db_path) as connection:
        connection.execute("""CREATE TABLE commit_gate (
            subject_id INTEGER REFERENCES subjects(id) DEFERRABLE INITIALLY DEFERRED)""")
    original = plan_service.create_budgeted_day_plan

    def defer_failure(*args, **kwargs):
        plan_id = original(*args, **kwargs)
        # SQLite accepts this INSERT, then rejects COMMIT after every business
        # statement has succeeded. It must still produce no partial write.
        kwargs["connection"].execute("INSERT INTO commit_gate VALUES (-999)")
        return plan_id

    monkeypatch.setattr(plan_service, "create_budgeted_day_plan", defer_failure)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        profile.service.confirm_budget(token)
    assert snapshot(profile) == before
    with connect_readonly(profile.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM commit_gate").fetchone()[0] == 0


def test_read_failure_after_commit_reports_saved_and_cannot_replay(profile, monkeypatch):
    token = preview(profile)
    original_read = plan_service.get_budgeted_day_plan

    def fail_post_commit(*args, **kwargs):
        saved = original_read(*args, **kwargs)
        if saved is not None:
            raise RuntimeError("private-path-or-secret")
        return saved

    monkeypatch.setattr(plan_service, "get_budgeted_day_plan", fail_post_commit)
    result = profile.service.confirm_budget(token)
    saved = get_budgeted_day_plan(profile.today.isoformat(), db_path=profile.db_path)
    assert result["saved"] is True and result["planId"] == saved["id"]
    assert result["refreshWarning"] and "private" not in str(result)
    assert saved["budget_minutes"] == 90
    with pytest.raises(StalePlanPreviewError):
        profile.service.confirm_budget(token)
