"""Knowledge alert instances, events, and lifecycle persistence."""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from study_app.data.db_runtime import (
    DEFAULT_DB_PATH,
    DatabaseNotInitializedError,
    connect_readonly,
)


KNOWLEDGE_ALERTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(fingerprint) = 64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
    topic_key TEXT NOT NULL
        REFERENCES knowledge_topic_registry(topic_key) ON DELETE RESTRICT,
    alert_type TEXT NOT NULL
        CHECK(alert_type IN ('recent_failure', 'insufficient_evidence', 'overdue_review')),
    status TEXT NOT NULL
        CHECK(status IN ('active', 'snoozed', 'handled', 'resolved')),
    rule_version TEXT NOT NULL,
    evidence_version TEXT NOT NULL,
    condition_cycle TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    recommended_date TEXT,
    priority REAL NOT NULL CHECK(priority >= 0),
    snapshot_json TEXT NOT NULL,
    snoozed_until TEXT,
    handled_at TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


KNOWLEDGE_ALERT_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL
        REFERENCES knowledge_alerts(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL
        CHECK(to_status IN ('active', 'snoozed', 'handled', 'resolved')),
    effective_date TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


KNOWLEDGE_ALERT_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_alerts_status_due
ON knowledge_alerts(status, snoozed_until, recommended_date);
CREATE INDEX IF NOT EXISTS idx_knowledge_alert_events_alert
ON knowledge_alert_events(alert_id, id);
"""


def _require_knowledge_alert_tables_from_connection(
    connection: sqlite3.Connection,
) -> None:
    names = {
        row[0]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name IN (
                'knowledge_topic_registry',
                'knowledge_alerts',
                'knowledge_alert_events'
            )
            """
        ).fetchall()
    }
    missing = {
        "knowledge_topic_registry",
        "knowledge_alerts",
        "knowledge_alert_events",
    } - names
    if missing:
        raise DatabaseNotInitializedError(
            "知识预警表尚未安装；真实数据库需先完成独立 CR-F2-01 结构变更："
            + "、".join(sorted(missing))
        )


def _require_knowledge_alert_tables(db_path: Path | str) -> None:
    with connect_readonly(db_path) as connection:
        _require_knowledge_alert_tables_from_connection(connection)


def _parse_iso_date(value: object, *, label: str, optional: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} 不是有效日期")
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} 不是有效日期：{value!r}") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{label} 必须使用 YYYY-MM-DD：{value!r}")
    return parsed


def _parse_json_object(value: object, *, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} JSON 损坏") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return parsed


def _knowledge_alert_from_row(row):
    from study_app.core.knowledge_alerts import KnowledgeAlert

    return KnowledgeAlert(
        id=int(row["id"]),
        fingerprint=row["fingerprint"],
        topic_key=row["topic_key"],
        alert_type=row["alert_type"],
        status=row["status"],
        rule_version=row["rule_version"],
        evidence_version=row["evidence_version"],
        condition_cycle=row["condition_cycle"],
        as_of_date=_parse_iso_date(row["as_of_date"], label="as_of_date"),
        recommended_date=_parse_iso_date(
            row["recommended_date"], label="recommended_date", optional=True
        ),
        priority=float(row["priority"]),
        snapshot=_parse_json_object(row["snapshot_json"], label="snapshot_json"),
        snoozed_until=_parse_iso_date(
            row["snoozed_until"], label="snoozed_until", optional=True
        ),
        handled_at=row["handled_at"],
        resolved_at=row["resolved_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _knowledge_alert_event_from_row(row):
    from study_app.core.knowledge_alerts import KnowledgeAlertEvent

    return KnowledgeAlertEvent(
        id=int(row["id"]),
        alert_id=int(row["alert_id"]),
        event_type=row["event_type"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        effective_date=_parse_iso_date(
            row["effective_date"], label="effective_date"
        ),
        actor=row["actor"],
        detail=_parse_json_object(row["detail_json"], label="detail_json"),
        created_at=row["created_at"],
    )


def list_knowledge_alerts(
    status: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Read F2 alert instances without installing schema or changing state."""
    allowed = {"active", "snoozed", "handled", "resolved"}
    if status is not None and status not in allowed:
        raise ValueError(f"未知预警状态：{status!r}")
    _require_knowledge_alert_tables(db_path)
    query = "SELECT * FROM knowledge_alerts"
    parameters: tuple[object, ...] = ()
    if status is not None:
        query += " WHERE status = ?"
        parameters = (status,)
    query += " ORDER BY id"
    with connect_readonly(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return tuple(_knowledge_alert_from_row(row) for row in rows)


def list_knowledge_alert_events(
    alert_id: int | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
):
    """Read immutable lifecycle events without creating missing tables."""
    if alert_id is not None and (
        isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0
    ):
        raise ValueError("alert_id 必须是正整数")
    _require_knowledge_alert_tables(db_path)
    query = "SELECT * FROM knowledge_alert_events"
    parameters: tuple[object, ...] = ()
    if alert_id is not None:
        query += " WHERE alert_id = ?"
        parameters = (alert_id,)
    query += " ORDER BY id"
    with connect_readonly(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return tuple(_knowledge_alert_event_from_row(row) for row in rows)


def _validate_alert_snapshot(snapshot: object, as_of_date: datetime.date) -> None:
    from study_app.core.knowledge_alerts import ALERT_TYPES, KnowledgeAlertSnapshot
    from study_app.core.topic_identity import validate_topic_key

    if not isinstance(snapshot, KnowledgeAlertSnapshot):
        raise ValueError("snapshots 只能包含 KnowledgeAlertSnapshot")
    validate_topic_key(snapshot.topic_key)
    if snapshot.alert_type not in ALERT_TYPES:
        raise ValueError(f"未知预警类型：{snapshot.alert_type!r}")
    if (
        len(snapshot.fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in snapshot.fingerprint)
    ):
        raise ValueError("预警 fingerprint 必须是 64 位小写十六进制")
    for label, value in (
        ("rule_version", snapshot.rule_version),
        ("evidence_version", snapshot.evidence_version),
        ("condition_cycle", snapshot.condition_cycle),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} 必须是非空字符串")
    if snapshot.as_of_date != as_of_date:
        raise ValueError("预警快照日期与协调日期不一致")
    if snapshot.recommended_date is not None and (
        not isinstance(snapshot.recommended_date, datetime.date)
        or isinstance(snapshot.recommended_date, datetime.datetime)
    ):
        raise ValueError("recommended_date 必须是 date 或 None")
    if (
        isinstance(snapshot.priority, bool)
        or not isinstance(snapshot.priority, (int, float))
        or not math.isfinite(float(snapshot.priority))
        or snapshot.priority < 0
    ):
        raise ValueError("priority 必须是非负有限数值")
    if not isinstance(snapshot.snapshot, dict):
        raise ValueError("snapshot 必须是结构化对象")
    identity = {
        "topic_key": snapshot.topic_key,
        "alert_type": snapshot.alert_type,
        "rule_version": snapshot.rule_version,
        "evidence_version": snapshot.evidence_version,
        "condition_cycle": snapshot.condition_cycle,
    }
    expected_fingerprint = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if snapshot.fingerprint != expected_fingerprint:
        raise ValueError("预警 fingerprint 与身份字段不一致")
    try:
        json.dumps(
            snapshot.snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("snapshot 必须是可序列化且不含非有限数值的 JSON 对象") from error


def _insert_alert_event(
    connection: sqlite3.Connection,
    *,
    alert_id: int,
    event_type: str,
    from_status: str | None,
    to_status: str,
    effective_date: datetime.date,
    detail: dict[str, object],
    actor: str = "system",
) -> None:
    connection.execute(
        """
        INSERT INTO knowledge_alert_events(
            alert_id, event_type, from_status, to_status,
            effective_date, actor, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            event_type,
            from_status,
            to_status,
            effective_date.isoformat(),
            actor,
            json.dumps(
                detail,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )


def reconcile_knowledge_alert_snapshots(
    snapshots: object,
    as_of_date: datetime.date,
    connection: sqlite3.Connection,
):
    """Atomically reconcile computed candidates against dedicated F2 alert tables."""
    from study_app.core.knowledge_alerts import AlertReconcileResult

    if not isinstance(as_of_date, datetime.date) or isinstance(
        as_of_date, datetime.datetime
    ):
        raise ValueError("as_of_date 必须是 date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    values = tuple(snapshots)
    for snapshot in values:
        _validate_alert_snapshot(snapshot, as_of_date)
    fingerprints = [snapshot.fingerprint for snapshot in values]
    slots = [(snapshot.topic_key, snapshot.alert_type) for snapshot in values]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("预警快照包含重复 fingerprint")
    if len(set(slots)) != len(slots):
        raise ValueError("同一知识点与预警类型只能有一个当前快照")

    _require_knowledge_alert_tables_from_connection(connection)
    registry_keys = {
        row[0]
        for row in connection.execute(
            "SELECT topic_key FROM knowledge_topic_registry"
        ).fetchall()
    }
    missing = sorted({snapshot.topic_key for snapshot in values} - registry_keys)
    if missing:
        raise LookupError("未找到知识点身份：" + "、".join(missing))

    cursor = connection.execute("SELECT * FROM knowledge_alerts ORDER BY id")
    columns = tuple(description[0] for description in cursor.description)
    existing = tuple(dict(zip(columns, row)) for row in cursor.fetchall())
    allowed_statuses = {"active", "snoozed", "handled", "resolved"}
    for row in existing:
        if row["status"] not in allowed_statuses:
            raise ValueError(f"知识预警状态损坏：{row['status']!r}")
        if row["status"] == "snoozed":
            _parse_iso_date(row["snoozed_until"], label="snoozed_until")

    current_by_slot = {
        (snapshot.topic_key, snapshot.alert_type): snapshot for snapshot in values
    }
    existing_by_fingerprint = {row["fingerprint"]: row for row in existing}
    resolve_plan: list[tuple[dict[str, object], str]] = []
    reactivate_plan: list[dict[str, object]] = []
    unchanged_ids: set[int] = set()

    for row in existing:
        row_id = int(row["id"])
        if row["status"] not in {"active", "snoozed"}:
            continue
        current = current_by_slot.get((row["topic_key"], row["alert_type"]))
        if current is None:
            resolve_plan.append((row, "condition_cleared"))
        elif current.fingerprint != row["fingerprint"]:
            resolve_plan.append((row, "condition_superseded"))
        elif row["status"] == "snoozed":
            snoozed_until = _parse_iso_date(
                row["snoozed_until"], label="snoozed_until"
            )
            if snoozed_until <= as_of_date:
                reactivate_plan.append(row)
            else:
                unchanged_ids.add(row_id)
        else:
            unchanged_ids.add(row_id)

    create_plan = []
    for snapshot in values:
        row = existing_by_fingerprint.get(snapshot.fingerprint)
        if row is None:
            create_plan.append(snapshot)
        elif int(row["id"]) not in {
            int(item[0]["id"]) for item in resolve_plan
        } and int(row["id"]) not in {int(item["id"]) for item in reactivate_plan}:
            unchanged_ids.add(int(row["id"]))

    created_ids: list[int] = []
    resolved_ids: list[int] = []
    reactivated_ids: list[int] = []
    connection.execute("SAVEPOINT reconcile_knowledge_alerts")
    try:
        for row, reason in resolve_plan:
            alert_id = int(row["id"])
            previous_snooze = row["snoozed_until"]
            connection.execute(
                """
                UPDATE knowledge_alerts
                SET status = 'resolved', resolved_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (as_of_date.isoformat(), alert_id),
            )
            _insert_alert_event(
                connection,
                alert_id=alert_id,
                event_type=reason,
                from_status=str(row["status"]),
                to_status="resolved",
                effective_date=as_of_date,
                detail=(
                    {}
                    if previous_snooze is None
                    else {"snoozed_until": previous_snooze}
                ),
            )
            resolved_ids.append(alert_id)

        for row in reactivate_plan:
            alert_id = int(row["id"])
            previous_snooze = str(row["snoozed_until"])
            connection.execute(
                """
                UPDATE knowledge_alerts
                SET status = 'active', snoozed_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (alert_id,),
            )
            _insert_alert_event(
                connection,
                alert_id=alert_id,
                event_type="snooze_expired_reactivated",
                from_status="snoozed",
                to_status="active",
                effective_date=as_of_date,
                detail={"snoozed_until": previous_snooze},
            )
            reactivated_ids.append(alert_id)

        for snapshot in create_plan:
            cursor = connection.execute(
                """
                INSERT INTO knowledge_alerts(
                    fingerprint, topic_key, alert_type, status,
                    rule_version, evidence_version, condition_cycle,
                    as_of_date, recommended_date, priority, snapshot_json
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.fingerprint,
                    snapshot.topic_key,
                    snapshot.alert_type,
                    snapshot.rule_version,
                    snapshot.evidence_version,
                    snapshot.condition_cycle,
                    snapshot.as_of_date.isoformat(),
                    (
                        None
                        if snapshot.recommended_date is None
                        else snapshot.recommended_date.isoformat()
                    ),
                    float(snapshot.priority),
                    json.dumps(
                        snapshot.snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ),
            )
            alert_id = int(cursor.lastrowid)
            _insert_alert_event(
                connection,
                alert_id=alert_id,
                event_type="created",
                from_status=None,
                to_status="active",
                effective_date=as_of_date,
                detail={"fingerprint": snapshot.fingerprint},
            )
            created_ids.append(alert_id)
        connection.execute("RELEASE SAVEPOINT reconcile_knowledge_alerts")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT reconcile_knowledge_alerts")
        connection.execute("RELEASE SAVEPOINT reconcile_knowledge_alerts")
        raise

    return AlertReconcileResult(
        created_ids=tuple(created_ids),
        reactivated_ids=tuple(reactivated_ids),
        resolved_ids=tuple(resolved_ids),
        unchanged_ids=tuple(sorted(unchanged_ids)),
    )


def handle_knowledge_alert(
    alert_id: int,
    effective_date: datetime.date,
    actor: str,
    connection: sqlite3.Connection,
):
    """Persist the sole I06 transition and its event in one savepoint."""
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError("alert_id 必须是正整数")
    if not isinstance(effective_date, datetime.date) or isinstance(
        effective_date, datetime.datetime
    ):
        raise ValueError("effective_date 必须是 date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    if (
        not isinstance(actor, str)
        or not 1 <= len(actor) <= 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            for character in actor
        )
    ):
        raise ValueError("actor 必须是 1～64 位本地技术标识")

    _require_knowledge_alert_tables_from_connection(connection)
    row = connection.execute(
        "SELECT * FROM knowledge_alerts WHERE id = ?",
        (alert_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"未找到知识预警实例：{alert_id}")
    current = _knowledge_alert_from_row(row)
    if effective_date < current.as_of_date:
        raise ValueError("处理生效日不得早于预警快照日期")
    if current.status == "handled":
        return current
    if current.status != "active":
        raise ValueError(
            f"只有 active 预警可以处理；当前状态为 {current.status}"
        )

    connection.execute("SAVEPOINT handle_knowledge_alert")
    try:
        cursor = connection.execute(
            """
            UPDATE knowledge_alerts
            SET status = 'handled', handled_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active'
            """,
            (effective_date.isoformat(), alert_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("预警状态已变化，处理操作未执行")
        _insert_alert_event(
            connection,
            alert_id=alert_id,
            event_type="handled",
            from_status="active",
            to_status="handled",
            effective_date=effective_date,
            detail={},
            actor=actor,
        )
        updated_row = connection.execute(
            "SELECT * FROM knowledge_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
        if updated_row is None:
            raise RuntimeError("处理后的预警实例不可读")
        updated = _knowledge_alert_from_row(updated_row)
        connection.execute("RELEASE SAVEPOINT handle_knowledge_alert")
        return updated
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT handle_knowledge_alert")
        connection.execute("RELEASE SAVEPOINT handle_knowledge_alert")
        raise


def snooze_knowledge_alert(
    alert_id: int,
    snoozed_until: datetime.date,
    as_of_date: datetime.date,
    actor: str,
    connection: sqlite3.Connection,
):
    """Persist active/snoozed -> snoozed and append its complete date history."""
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError("alert_id 必须是正整数")
    for label, value in (
        ("snoozed_until", snoozed_until),
        ("as_of_date", as_of_date),
    ):
        if not isinstance(value, datetime.date) or isinstance(
            value, datetime.datetime
        ):
            raise ValueError(f"{label} 必须是 date")
    if snoozed_until < as_of_date:
        raise ValueError("延后日期不得早于 as_of_date")
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connection 必须是 sqlite3.Connection")
    if (
        not isinstance(actor, str)
        or not 1 <= len(actor) <= 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            for character in actor
        )
    ):
        raise ValueError("actor 必须是 1～64 位本地技术标识")

    _require_knowledge_alert_tables_from_connection(connection)
    row = connection.execute(
        "SELECT * FROM knowledge_alerts WHERE id = ?",
        (alert_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"未找到知识预警实例：{alert_id}")
    current = _knowledge_alert_from_row(row)
    if as_of_date < current.as_of_date:
        raise ValueError("操作日期不得早于预警快照日期")
    if current.status not in {"active", "snoozed"}:
        raise ValueError(
            "只有 active 或 snoozed 预警可以延后；"
            f"当前状态为 {current.status}"
        )
    if current.status == "snoozed":
        if current.snoozed_until is None:
            raise ValueError("snoozed 预警缺少延后到期日")
        if current.snoozed_until <= as_of_date:
            raise ValueError("延后已到期；必须先按当前条件执行协调重算")
    latest_event_row = connection.execute(
        """
        SELECT effective_date
        FROM knowledge_alert_events
        WHERE alert_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (alert_id,),
    ).fetchone()
    if latest_event_row is None:
        raise ValueError("预警实例缺少创建事件，不能延后")
    latest_event_date = _parse_iso_date(
        latest_event_row["effective_date"], label="latest_event.effective_date"
    )
    if as_of_date < latest_event_date:
        raise ValueError("操作日期不得早于上一条生命周期事件")

    previous_status = current.status
    previous_until = current.snoozed_until
    event_type = "snoozed" if previous_status == "active" else "resnoozed"
    connection.execute("SAVEPOINT snooze_knowledge_alert")
    try:
        cursor = connection.execute(
            """
            UPDATE knowledge_alerts
            SET status = 'snoozed', snoozed_until = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (snoozed_until.isoformat(), alert_id, previous_status),
        )
        if cursor.rowcount != 1:
            raise ValueError("预警状态已变化，延后操作未执行")
        _insert_alert_event(
            connection,
            alert_id=alert_id,
            event_type=event_type,
            from_status=previous_status,
            to_status="snoozed",
            effective_date=as_of_date,
            detail={
                "previous_snoozed_until": (
                    None if previous_until is None else previous_until.isoformat()
                ),
                "snoozed_until": snoozed_until.isoformat(),
            },
            actor=actor,
        )
        updated_row = connection.execute(
            "SELECT * FROM knowledge_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
        if updated_row is None:
            raise RuntimeError("延后后的预警实例不可读")
        updated = _knowledge_alert_from_row(updated_row)
        connection.execute("RELEASE SAVEPOINT snooze_knowledge_alert")
        return updated
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT snooze_knowledge_alert")
        connection.execute("RELEASE SAVEPOINT snooze_knowledge_alert")
        raise
