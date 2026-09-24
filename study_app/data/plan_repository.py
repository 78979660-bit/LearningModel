"""Study plan, day budget, exam date, and task estimate persistence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from study_app.data.catalog_guards import require_f5_subject_write_allowed
from study_app.data.db_runtime import (
    DatabaseNotInitializedError,
    connect,
    connect_readonly,
    dumps,
    require_initialized_database,
)

from study_app.paths import DATABASE_PATH

DEFAULT_DB_PATH = DATABASE_PATH


def study_plan_signature(state: dict[str, Any]) -> str:
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


STUDY_DAY_BUDGET_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS study_day_budgets (
    plan_date TEXT PRIMARY KEY,
    available_minutes INTEGER NOT NULL
        CHECK(typeof(available_minutes) = 'integer' AND available_minutes BETWEEN 0 AND 1440),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _require_study_day_budget_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'study_day_budgets'"
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "今日预算数据表尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def save_study_day_budget(
    plan_date: object,
    available_minutes: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Validate both fields before opening a write connection."""
    from study_app.core.day_budget_input import validate_day_budget_input

    budget = validate_day_budget_input(plan_date, available_minutes)
    path = require_initialized_database(db_path)
    _require_study_day_budget_table(path)
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO study_day_budgets(plan_date, available_minutes)
            VALUES (?, ?)
            ON CONFLICT(plan_date) DO UPDATE SET
                available_minutes = excluded.available_minutes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (budget.plan_date, budget.available_minutes),
        )
    return budget


def get_study_day_budget(
    plan_date: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Read an existing day's budget without initializing or changing schema."""
    from study_app.core.day_budget_input import StudyDayBudget, validate_day_budget_date

    valid_date = validate_day_budget_date(plan_date)
    _require_study_day_budget_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            "SELECT plan_date, available_minutes FROM study_day_budgets WHERE plan_date = ?",
            (valid_date,),
        ).fetchone()
    if row is None:
        return None
    return StudyDayBudget(
        plan_date=row["plan_date"],
        available_minutes=row["available_minutes"],
    )


STUDY_SUBJECT_EXAM_DATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS study_subject_exam_dates (
    subject_id INTEGER PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
    exam_date TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _require_subject_exam_dates_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'study_subject_exam_dates'"
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "学科考试日期数据表尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def save_subject_exam_date(
    state: object,
    subject_name: str,
    exam_date: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str | None:
    """Only a current activity subject can set or clear its exam date."""
    from study_app.core.active_subjects import require_activity_subject
    from study_app.core.day_budget_input import validate_subject_exam_date

    valid_date = validate_subject_exam_date(exam_date)
    require_activity_subject(state, subject_name, "修改考试日期")
    path = require_initialized_database(db_path)
    _require_subject_exam_dates_table(path)
    with connect(path) as connection:
        subject = connection.execute(
            "SELECT id, status FROM subjects WHERE name = ?",
            (subject_name,),
        ).fetchone()
        if subject is None:
            raise LookupError(f"未找到学科：{subject_name}")
        if subject["status"] == "archived":
            raise ValueError(f"{subject_name} 已封存，不再修改考试日期。")
        connection.execute(
            """
            INSERT INTO study_subject_exam_dates(subject_id, exam_date)
            VALUES (?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET
                exam_date = excluded.exam_date,
                updated_at = CURRENT_TIMESTAMP
            """,
            (subject["id"], valid_date),
        )
    return valid_date


def get_subject_exam_date(
    subject_name: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str | None:
    """Historical reads remain available after a subject is archived."""
    from study_app.core.day_budget_input import validate_subject_exam_date

    _require_subject_exam_dates_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            """
            SELECT subjects.id, study_subject_exam_dates.exam_date
            FROM subjects
            LEFT JOIN study_subject_exam_dates
                ON study_subject_exam_dates.subject_id = subjects.id
            WHERE subjects.name = ?
            """,
            (subject_name,),
        ).fetchone()
    if row is None:
        raise LookupError(f"未找到学科：{subject_name}")
    return validate_subject_exam_date(row["exam_date"])


STUDY_TASK_ESTIMATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS study_task_estimates (
    task_id TEXT PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    estimated_minutes INTEGER NOT NULL
        CHECK(typeof(estimated_minutes) = 'integer' AND estimated_minutes BETWEEN 1 AND 1440),
    source TEXT NOT NULL CHECK(source IN ('user', 'confirmed_template')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _require_task_estimates_table(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'study_task_estimates'"
        ).fetchone()
    if exists is None:
        raise DatabaseNotInitializedError(
            "任务估时数据表尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def save_task_estimate(
    state: object,
    candidate: object,
    estimated_minutes: object,
    source: object = "user",
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Validate before writes, and require an explicitly installed estimate table."""
    from study_app.core.active_subjects import require_activity_subject
    from study_app.core.task_estimates import make_task_estimate

    estimate = make_task_estimate(candidate, estimated_minutes, source)
    require_activity_subject(state, candidate.subject_id, "修改任务估时")
    path = require_initialized_database(db_path)
    _require_task_estimates_table(path)
    with connect(path) as connection:
        subject = connection.execute(
            "SELECT id, status FROM subjects WHERE name = ?", (candidate.subject_id,)
        ).fetchone()
        if subject is None:
            raise LookupError(f"未找到学科：{candidate.subject_id}")
        if subject["status"] == "archived":
            raise ValueError(f"{candidate.subject_id} 已封存，不再修改任务估时")
        connection.execute(
            """
            INSERT INTO study_task_estimates
                (task_id, subject_id, source_kind, source_id, estimated_minutes, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                estimated_minutes = excluded.estimated_minutes,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (estimate.task_id, subject["id"], candidate.source_kind,
             candidate.source_id, estimate.estimated_minutes, estimate.source),
        )
    return estimate


def get_task_estimate(
    candidate: object,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Historical estimates can be read, including after subject archival."""
    from study_app.core.task_estimates import TaskEstimate, validate_candidate_identity

    validate_candidate_identity(candidate)
    _require_task_estimates_table(db_path)
    with connect_readonly(db_path) as connection:
        row = connection.execute(
            """
            SELECT e.task_id, e.estimated_minutes, e.source
            FROM study_task_estimates e JOIN subjects s ON s.id = e.subject_id
            WHERE e.task_id = ? AND s.name = ? AND e.source_kind = ? AND e.source_id = ?
            """,
            (candidate.task_id, candidate.subject_id, candidate.source_kind, candidate.source_id),
        ).fetchone()
    if row is None:
        return None
    return TaskEstimate(row["task_id"], row["estimated_minutes"], row["source"])


def _scope_key(subject_scope: str | None) -> str:
    return subject_scope or ""


BUDGET_PLAN_FORMAT_VERSION = "budget-v1"
BUDGET_PLAN_SCOPE = "__budget_day__"
F1_I06_MIGRATION_STATEMENTS = (
    "ALTER TABLE study_plans ADD COLUMN plan_date TEXT",
    "ALTER TABLE study_plans ADD COLUMN budget_minutes INTEGER",
    "ALTER TABLE study_plans ADD COLUMN plan_format_version TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN task_id TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN subject_id TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN estimated_minutes INTEGER",
    "ALTER TABLE study_plan_items ADD COLUMN estimate_source TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN selection_reason_json TEXT",
    "ALTER TABLE study_plan_items ADD COLUMN excluded_reason TEXT",
    "CREATE UNIQUE INDEX uq_budget_plan_task ON study_plan_items(plan_id, task_id) WHERE task_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_active_budget_plan_date ON study_plans(plan_date) "
    "WHERE plan_format_version = 'budget-v1' AND status = 'active'",
)


def _require_budget_plan_schema(db_path: Path | str) -> None:
    required_plan = {"plan_date", "budget_minutes", "plan_format_version"}
    required_item = {
        "task_id", "subject_id", "estimated_minutes", "estimate_source",
        "selection_reason_json", "excluded_reason",
    }
    with connect_readonly(db_path) as connection:
        plan_columns = {row["name"] for row in connection.execute("PRAGMA table_info(study_plans)")}
        item_columns = {row["name"] for row in connection.execute("PRAGMA table_info(study_plan_items)")}
    if not required_plan <= plan_columns or not required_item <= item_columns:
        raise DatabaseNotInitializedError(
            "规范预算计划结构尚未安装；真实数据库需先完成独立 CR-F1-01 结构变更"
        )


def create_budgeted_day_plan(
    plan_date: object,
    available_minutes: object,
    plan: object,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    """Atomically replace all active plans with one canonical day plan."""
    from study_app.core.day_budget_input import validate_day_budget_input
    from study_app.core.study_plan_items import build_budgeted_plan_items
    from study_app.core.task_estimates import validate_estimate_source, validate_estimated_minutes
    from study_app.data.text_integrity import validate_text_integrity

    budget = validate_day_budget_input(plan_date, available_minutes)
    rows = build_budgeted_plan_items(plan)
    selected_minutes = 0
    completed_minutes = 0
    for row in rows:
        minutes = row["estimated_minutes"]
        if row["section_key"] == "selected" and minutes is None:
            raise ValueError("待执行任务必须有可信预计时间")
        if minutes is not None:
            valid_minutes = validate_estimated_minutes(minutes)
            validate_estimate_source(row["estimate_source"])
            if row["section_key"] == "selected":
                selected_minutes += valid_minutes
            elif row["section_key"] == "completed":
                completed_minutes += valid_minutes
    expected_planned = selected_minutes + completed_minutes
    if plan.planned_minutes != expected_planned:
        raise ValueError("预算计划合计分钟与条目不一致")
    expected_overage = max(0, completed_minutes - budget.available_minutes)
    if plan.over_budget_completed_minutes != expected_overage:
        raise ValueError("已完成超额分钟与条目不一致")
    if selected_minutes and getattr(plan, "completed_occupancy_unknown", False):
        raise ValueError("完成占用未知时不能新增待执行任务")
    if plan.planned_minutes > budget.available_minutes and expected_overage == 0:
        raise ValueError("预算计划总分钟超过当日预算")
    if plan.remaining_minutes != max(0, budget.available_minutes - plan.planned_minutes):
        raise ValueError("预算计划剩余分钟与输入不一致")
    if len({row["task_id"] for row in rows if row["task_id"] is not None}) != sum(
        row["task_id"] is not None for row in rows
    ):
        raise ValueError("规范计划中存在重复任务 ID")
    validate_text_integrity(rows, context="规范预算计划条目")
    path = require_initialized_database(db_path)
    _require_budget_plan_schema(path)
    with connect(path) as connection:
        old_states = {
            row["task_id"]: (row["checked"], row["result"])
            for row in connection.execute(
                """
                SELECT items.task_id,
                       CASE WHEN COALESCE(states.checked, 0) = 1 OR states.result IS NOT NULL
                            THEN 1 ELSE 0 END AS checked,
                       states.result
                FROM study_plan_items items
                JOIN study_plans plans ON plans.id = items.plan_id
                LEFT JOIN study_plan_item_states states ON states.item_id = items.id
                WHERE items.task_id IS NOT NULL AND plans.status = 'active'
                """
            )
        }
        legacy_completed = connection.execute(
            """
            SELECT COUNT(*)
            FROM study_plan_items items
            JOIN study_plans plans ON plans.id = items.plan_id
            JOIN study_plan_item_states states ON states.item_id = items.id
            WHERE plans.status = 'active' AND items.task_id IS NULL
              AND (states.checked = 1 OR states.result IS NOT NULL)
            """
        ).fetchone()[0]
        if legacy_completed and any(row["section_key"] == "selected" for row in rows):
            raise ValueError("旧计划存在完成占用未知项；补估时前不能新增待执行任务")
        connection.execute(
            "UPDATE study_plans SET status = 'archived', archived_at = CURRENT_TIMESTAMP "
            "WHERE status = 'active'"
        )
        summary = {
            "format": BUDGET_PLAN_FORMAT_VERSION,
            "planned_minutes": plan.planned_minutes,
            "remaining_minutes": plan.remaining_minutes,
            "over_budget_completed_minutes": plan.over_budget_completed_minutes,
            "completed_occupancy_unknown": bool(plan.completed_occupancy_unknown or legacy_completed),
            "legacy_completed_unknown_count": legacy_completed,
        }
        cursor = connection.execute(
            """
            INSERT INTO study_plans(
                subject_scope, start_date, end_date, input_signature, status, plan_json,
                plan_date, budget_minutes, plan_format_version
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (BUDGET_PLAN_SCOPE, budget.plan_date, budget.plan_date, plan.input_signature,
             dumps(summary), budget.plan_date, budget.available_minutes, BUDGET_PLAN_FORMAT_VERSION),
        )
        plan_id = int(cursor.lastrowid)
        for order, row in enumerate(rows):
            item_cursor = connection.execute(
                """
                INSERT INTO study_plan_items(
                    plan_id, section_key, section_title, day_index, item_type, item_text,
                    item_order, item_hash, task_id, subject_id, estimated_minutes,
                    estimate_source, selection_reason_json, excluded_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (plan_id, row["section_key"], row["section_title"], row["day_index"],
                 row["item_type"], row["item_text"], order, row["item_hash"], row["task_id"],
                 row["subject_id"], row["estimated_minutes"], row["estimate_source"],
                 dumps(row["selection_reason"]), row["excluded_reason"]),
            )
            prior = old_states.get(row["task_id"])
            if prior is not None or row["initial_checked"]:
                checked, result = prior if prior is not None else (1, None)
                connection.execute(
                    "INSERT INTO study_plan_item_states(item_id, checked, result) VALUES (?, ?, ?)",
                    (int(item_cursor.lastrowid), checked, result),
                )
    return plan_id


def get_budgeted_day_plan(
    plan_date: object,
    subject_scope: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    from study_app.core.day_budget_input import validate_day_budget_date

    valid_date = validate_day_budget_date(plan_date)
    _require_budget_plan_schema(db_path)
    with connect_readonly(db_path) as connection:
        plan_row = connection.execute(
            """
            SELECT id, plan_date, budget_minutes, input_signature, plan_json, created_at
            FROM study_plans
            WHERE plan_date = ? AND plan_format_version = ? AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """,
            (valid_date, BUDGET_PLAN_FORMAT_VERSION),
        ).fetchone()
        if plan_row is None:
            return None
        item_rows = connection.execute(
            """
            SELECT items.id, items.section_key, items.item_text, items.item_order,
                   items.task_id, items.subject_id, items.estimated_minutes,
                   items.estimate_source, items.selection_reason_json, items.excluded_reason,
                   CASE WHEN COALESCE(states.checked, 0) = 1 OR states.result IS NOT NULL
                        THEN 1 ELSE 0 END AS checked,
                   states.result
            FROM study_plan_items items
            LEFT JOIN study_plan_item_states states ON states.item_id = items.id
            WHERE items.plan_id = ? AND (? IS NULL OR items.subject_id = ?)
            ORDER BY items.item_order
            """,
            (plan_row["id"], subject_scope, subject_scope),
        ).fetchall()
    result = dict(plan_row)
    result["summary"] = json.loads(result.pop("plan_json"))
    result["items"] = []
    for row in item_rows:
        item = dict(row)
        item["selection_reason"] = json.loads(item.pop("selection_reason_json"))
        result["items"].append(item)
    return result


def create_study_plan(
    subject_scope: str | None,
    start_date: str,
    end_date: str,
    input_signature: str,
    plan: dict[str, Any],
    items: list[dict[str, Any]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    from study_app.data.text_integrity import validate_text_integrity

    validate_text_integrity(plan, context="学习计划")
    validate_text_integrity(items, context="学习计划条目")
    path = require_initialized_database(db_path)
    scope = _scope_key(subject_scope)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        gated_subjects = {
            str(value).strip()
            for value in (
                subject_scope,
                *(item.get("subject_id") for item in items),
            )
            if value is not None and str(value).strip()
        }
        for gated_subject in sorted(gated_subjects):
            require_f5_subject_write_allowed(connection, gated_subject)
        connection.execute(
            """
            UPDATE study_plans
            SET status = 'archived', archived_at = CURRENT_TIMESTAMP
            WHERE COALESCE(subject_scope, '') = ? AND status = 'active'
            """,
            (scope,),
        )
        cursor = connection.execute(
            """
            INSERT INTO study_plans(subject_scope, start_date, end_date, input_signature, status, plan_json)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (scope, start_date, end_date, input_signature, dumps(plan)),
        )
        plan_id = int(cursor.lastrowid)
        for order, item in enumerate(items):
            connection.execute(
                """
                INSERT INTO study_plan_items(
                    plan_id, section_key, section_title, day_index, item_type,
                    item_text, item_order, item_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    item["section_key"],
                    item["section_title"],
                    item.get("day_index"),
                    item["item_type"],
                    item["item_text"],
                    order,
                    item["item_hash"],
                ),
            )
    return plan_id


def get_active_study_plan(subject_scope: str | None, db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    scope = _scope_key(subject_scope)
    with connect_readonly(db_path) as connection:
        plan_row = connection.execute(
            """
            SELECT id, subject_scope, start_date, end_date, input_signature, plan_json, created_at
            FROM study_plans
            WHERE COALESCE(subject_scope, '') = ? AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (scope,),
        ).fetchone()
        if not plan_row:
            return None
        item_rows = connection.execute(
            """
            SELECT
                study_plan_items.id, section_key, section_title, day_index, item_type,
                item_text, item_order, item_hash,
                CASE
                    WHEN COALESCE(study_plan_item_states.checked, 0) = 1
                         OR study_plan_item_states.result IS NOT NULL
                    THEN 1 ELSE 0
                END AS checked,
                study_plan_item_states.result AS result
            FROM study_plan_items
            LEFT JOIN study_plan_item_states
                ON study_plan_item_states.item_id = study_plan_items.id
            WHERE plan_id = ?
            ORDER BY item_order
            """,
            (plan_row["id"],),
        ).fetchall()
    plan = dict(plan_row)
    plan["plan"] = json.loads(plan.pop("plan_json"))
    plan["items"] = [dict(row) for row in item_rows]
    return plan


def recent_study_plan_texts(
    subject_scope: str | None,
    limit: int = 5,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[str]:
    scope = _scope_key(subject_scope)
    with connect_readonly(db_path) as connection:
        rows = connection.execute(
            """
            SELECT plan_json
            FROM study_plans
            WHERE COALESCE(subject_scope, '') = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (scope, max(1, int(limit))),
        ).fetchall()
    texts = []
    for row in rows:
        plan = json.loads(row["plan_json"])
        text_parts: list[str] = []

        def collect_text(value: object) -> None:
            if isinstance(value, dict):
                for nested in value.values():
                    collect_text(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_text(nested)
            elif value is not None:
                text_parts.append(str(value))

        collect_text(plan)
        texts.append("\n".join(text_parts))
    return texts


def archive_active_study_plan(subject_scope: str | None, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    path = require_initialized_database(db_path)
    scope = _scope_key(subject_scope)
    with connect(path) as connection:
        connection.execute(
            """
            UPDATE study_plans
            SET status = 'archived', archived_at = CURRENT_TIMESTAMP
            WHERE COALESCE(subject_scope, '') = ? AND status = 'active'
            """,
            (scope,),
        )


def update_study_plan_item_state(
    item_id: int,
    checked: bool | None = None,
    result: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    path = require_initialized_database(db_path)
    with connect(path) as connection:
        row = connection.execute(
            "SELECT item_id FROM study_plan_item_states WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row:
            assignments = []
            values: list[Any] = []
            if checked is not None:
                assignments.append("checked = ?")
                values.append(1 if checked else 0)
            if result is not None:
                assignments.append("result = ?")
                values.append(result)
            if not assignments:
                return
            values.append(item_id)
            connection.execute(
                f"""
                UPDATE study_plan_item_states
                SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ?
                """,
                values,
            )
        else:
            connection.execute(
                """
                INSERT INTO study_plan_item_states(item_id, checked, result)
                VALUES (?, ?, ?)
                """,
                (item_id, 1 if checked else 0, result),
            )
