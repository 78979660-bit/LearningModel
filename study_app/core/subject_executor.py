from __future__ import annotations

import json
import uuid
from pathlib import Path

from study_app.core.subject_capabilities import CAPABILITY_KEYS, validate_capability_key
from study_app.core.subject_changeset import canonical_hash, load_changeset
from study_app.core.subject_identity import (
    normalize_alias,
    validate_module_key,
    validate_subject_key,
)
from study_app.data.subject_repository import SubjectCatalogRepository


def _finish_attempt(db_path, attempt_id, status, error=None):
    repository = SubjectCatalogRepository(db_path)
    with repository._open(readonly=False) as connection:
        connection.execute(
            """
            UPDATE subject_operation_attempts
            SET status=?, error_message=?, finished_at=CURRENT_TIMESTAMP
            WHERE attempt_id=?
            """,
            (status, error, attempt_id),
        )


def execute_archive_changeset(
    db_path: Path | str,
    operation_id: str,
    *,
    actor: str,
    attempt_id: str | None = None,
) -> dict[str, object]:
    if not actor.strip():
        raise ValueError("actor 必须非空")
    identifier = attempt_id or f"attempt:{uuid.uuid4().hex}"
    repository = SubjectCatalogRepository(db_path)
    with repository._open(readonly=False) as connection:
        connection.execute(
            "INSERT INTO subject_operation_attempts(attempt_id,operation_id,status,actor) VALUES (?,?,'running',?)",
            (identifier, operation_id, actor.strip()),
        )
        current = connection.execute(
            "SELECT status FROM subject_change_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if current is None:
            raise LookupError(f"未知 operation_id：{operation_id}")
        if current["status"] == "completed":
            connection.execute(
                "UPDATE subject_operation_attempts SET status='idempotent_replay',finished_at=CURRENT_TIMESTAMP WHERE attempt_id=?",
                (identifier,),
            )
            connection.execute(
                "INSERT INTO subject_operation_events(operation_id,attempt_id,event_type) VALUES (?,?,'idempotent_replay')",
                (operation_id, identifier),
            )
            return {"operation_id": operation_id, "attempt_id": identifier, "status": "completed", "idempotent": True}
    try:
        prepared = load_changeset(db_path, operation_id)
        actions = prepared.payload.get("actions")
        if not isinstance(actions, list) or len(actions) != 1 or actions[0].get("type") != "archive_subject":
            raise ValueError("I08 执行器只接受单一 archive_subject 动作")
        action = actions[0]
        subject_key = str(action.get("subject_key") or "")
        reason = str(action.get("reason") or "").strip()
        if not subject_key or not reason:
            raise ValueError("归档动作缺少 subject_key 或 reason")
        with repository._open(readonly=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM subject_change_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            changeset = connection.execute(
                "SELECT * FROM subject_changesets WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            approval = connection.execute(
                "SELECT * FROM subject_approvals WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if operation["status"] != "approved" or approval is None:
                raise ValueError("变更集未处于 approved")
            if canonical_hash(json.loads(changeset["payload_json"])) != changeset["changeset_hash"]:
                raise ValueError("changeset_hash 校验失败")
            binding = {
                "changeset_hash": changeset["changeset_hash"],
                "input_version_vector": json.loads(changeset["input_version_vector_json"]),
                "manifest_version": changeset["manifest_version"],
                "schema_version": changeset["schema_version"],
                "validator_version": changeset["validator_version"],
            }
            if approval["approval_hash"] != canonical_hash(binding):
                raise ValueError("签核绑定校验失败")
            current_revision = int(connection.execute("SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1").fetchone()[0])
            if current_revision != int(operation["expected_catalog_revision"]):
                raise ValueError("catalog_revision 已漂移")
            expected_version = prepared.input_version_vector.get("subjects", {}).get(subject_key)
            subject = connection.execute(
                "SELECT * FROM subject_catalog WHERE subject_key=?", (subject_key,)
            ).fetchone()
            if subject is None:
                raise LookupError(f"未知 subject_key：{subject_key}")
            if int(subject["object_version"]) != expected_version:
                raise ValueError("目标学科 object_version 已漂移")
            if subject["lifecycle_status"] != "active":
                raise ValueError("目标学科不是 active")
            connection.execute(
                "UPDATE subject_change_operations SET status='executing',updated_at=CURRENT_TIMESTAMP WHERE operation_id=?",
                (operation_id,),
            )
            changed = connection.execute(
                """
                UPDATE subject_catalog
                SET lifecycle_status='archived',object_version=object_version+1,updated_at=CURRENT_TIMESTAMP
                WHERE subject_key=? AND lifecycle_status='active' AND object_version=?
                """,
                (subject_key, expected_version),
            ).rowcount
            if changed != 1:
                raise ValueError("归档 compare-and-swap 冲突")
            aliases = [
                row[0]
                for row in connection.execute(
                    "SELECT alias FROM subject_aliases WHERE subject_key=?", (subject_key,)
                ).fetchall()
            ]
            if aliases:
                placeholders = ",".join("?" for _ in aliases)
                connection.execute(
                    f"""
                    UPDATE study_plans SET status='archived',archived_at=CURRENT_TIMESTAMP
                    WHERE status='active' AND (
                        subject_scope IN ({placeholders}) OR EXISTS (
                            SELECT 1 FROM study_plan_items items
                            WHERE items.plan_id=study_plans.id AND items.subject_id IN ({placeholders})
                        )
                    )
                    """,
                    (*aliases, *aliases),
                )
            connection.execute(
                "UPDATE subject_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1"
            )
            connection.execute(
                """
                INSERT INTO subject_lifecycle_events(
                    operation_id,subject_key,from_status,to_status,reason,actor
                ) VALUES (?,?,'active','archived',?,?)
                """,
                (operation_id, subject_key, reason, actor.strip()),
            )
            connection.execute(
                "INSERT INTO subject_operation_events(operation_id,attempt_id,event_type,detail_json) VALUES (?,?,'archived',?)",
                (operation_id, identifier, json.dumps({"subject_key": subject_key}, sort_keys=True)),
            )
            connection.execute(
                "UPDATE subject_change_operations SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE operation_id=?",
                (operation_id,),
            )
        _finish_attempt(db_path, identifier, "succeeded")
        return {"operation_id": operation_id, "attempt_id": identifier, "status": "completed", "subject_key": subject_key, "idempotent": False}
    except Exception as error:
        with repository._open(readonly=False) as connection:
            connection.execute(
                "UPDATE subject_change_operations SET status='failed_before_commit',updated_at=CURRENT_TIMESTAMP WHERE operation_id=? AND status<>'completed'",
                (operation_id,),
            )
        _finish_attempt(db_path, identifier, "failed", f"{type(error).__name__}: {error}")
        raise


def _approved_rows(connection, operation_id: str):
    operation = connection.execute(
        "SELECT * FROM subject_change_operations WHERE operation_id=?", (operation_id,)
    ).fetchone()
    changeset = connection.execute(
        "SELECT * FROM subject_changesets WHERE operation_id=?", (operation_id,)
    ).fetchone()
    approval = connection.execute(
        "SELECT * FROM subject_approvals WHERE operation_id=?", (operation_id,)
    ).fetchone()
    if operation is None or changeset is None:
        raise LookupError(f"未知 operation_id：{operation_id}")
    if operation["status"] != "approved" or approval is None:
        raise ValueError("变更集未处于 approved")
    payload = json.loads(changeset["payload_json"])
    if canonical_hash(payload) != changeset["changeset_hash"]:
        raise ValueError("changeset_hash 校验失败")
    binding = {
        "changeset_hash": changeset["changeset_hash"],
        "input_version_vector": json.loads(changeset["input_version_vector_json"]),
        "manifest_version": changeset["manifest_version"],
        "schema_version": changeset["schema_version"],
        "validator_version": changeset["validator_version"],
    }
    if approval["approval_hash"] != canonical_hash(binding):
        raise ValueError("签核绑定校验失败")
    return operation, changeset, payload, binding["input_version_vector"]


def _check_version_vector(connection, operation, vector: dict) -> None:
    current_revision = int(
        connection.execute(
            "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
        ).fetchone()[0]
    )
    if current_revision != int(operation["expected_catalog_revision"]):
        raise ValueError("catalog_revision 已漂移")
    for subject_key, expected in vector.get("subjects", {}).items():
        row = connection.execute(
            "SELECT object_version FROM subject_catalog WHERE subject_key=?", (subject_key,)
        ).fetchone()
        actual = int(row[0]) if row else None
        if actual != expected:
            raise ValueError(f"目标学科 object_version 已漂移：{subject_key}")
    manifest_version = vector.get("manifest_version")
    if manifest_version is not None:
        row = connection.execute(
            "SELECT payload_hash,object_version FROM subject_manifest_versions WHERE manifest_version=?",
            (manifest_version,),
        ).fetchone()
        actual_hash = row["payload_hash"] if row else None
        actual_version = int(row["object_version"]) if row else None
        if actual_hash != vector.get("manifest_hash") or actual_version != vector.get(
            "manifest_object_version"
        ):
            raise ValueError("Manifest 版本向量已漂移")


def _archive_plans(connection, subject_key: str) -> list[int]:
    aliases = [
        row[0]
        for row in connection.execute(
            "SELECT alias FROM subject_aliases WHERE subject_key=?", (subject_key,)
        ).fetchall()
    ]
    if not aliases:
        return []
    placeholders = ",".join("?" for _ in aliases)
    rows = connection.execute(
        f"""
        SELECT DISTINCT plans.id
        FROM study_plans plans
        LEFT JOIN study_plan_items items ON items.plan_id=plans.id
        WHERE plans.status='active'
          AND (plans.subject_scope IN ({placeholders}) OR items.subject_id IN ({placeholders}))
        ORDER BY plans.id
        """,
        (*aliases, *aliases),
    ).fetchall()
    plan_ids = [int(row[0]) for row in rows]
    if plan_ids:
        ids = ",".join("?" for _ in plan_ids)
        connection.execute(
            f"UPDATE study_plans SET status='archived',archived_at=CURRENT_TIMESTAMP WHERE id IN ({ids})",
            tuple(plan_ids),
        )
    return plan_ids


def _create_core_plan(connection, operation_id: str, subject_key: str, canonical_name: str, core_plan: object) -> int:
    if not isinstance(core_plan, dict):
        raise ValueError("新学科缺少 core_plan")
    start_date = str(core_plan.get("start_date") or "").strip()
    end_date = str(core_plan.get("end_date") or "").strip()
    item_text = str(core_plan.get("item_text") or "").strip()
    if not start_date or not end_date or not item_text:
        raise ValueError("core_plan 必须包含 start_date、end_date 和 item_text")
    plan_json = json.dumps(
        {"kind": "subject-core-plan", "subject_key": subject_key, "item_text": item_text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    cursor = connection.execute(
        """
        INSERT INTO study_plans(
            subject_scope,start_date,end_date,input_signature,status,plan_json,
            plan_format_version
        ) VALUES (?,?,?,?,'active',?,'subject-core-v1')
        """,
        (canonical_name, start_date, end_date, f"f5:{operation_id}", plan_json),
    )
    plan_id = int(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO study_plan_items(
            plan_id,section_key,section_title,day_index,item_type,item_text,
            item_order,item_hash,subject_id
        ) VALUES (?,'core','核心计划',1,'task',?,0,?,?)
        """,
        (plan_id, item_text, f"f5:{operation_id}:core", canonical_name),
    )
    return plan_id


def _apply_switch(connection, operation_id: str, attempt_id: str, actor: str, payload: dict, changeset) -> dict:
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise ValueError("actions 必须为列表")
    activate_actions = [item for item in actions if isinstance(item, dict) and item.get("type") == "activate_subject"]
    archive_actions = [item for item in actions if isinstance(item, dict) and item.get("type") == "archive_subject"]
    if len(activate_actions) != 1 or len(archive_actions) > 1 or len(actions) != len(activate_actions) + len(archive_actions):
        raise ValueError("I09 切换只接受一个 activate_subject 和至多一个 archive_subject")
    activate = activate_actions[0]
    new_subject_key = validate_subject_key(activate.get("subject_key"))
    canonical_name = str(activate.get("canonical_name") or "").strip()
    display_name = str(activate.get("display_name") or canonical_name).strip()
    if not canonical_name or not display_name:
        raise ValueError("新学科名称不得为空")
    manifest_version = str(activate.get("manifest_version") or "")
    if not manifest_version or manifest_version != (changeset["manifest_version"] or ""):
        raise ValueError("激活动作必须绑定已签核 manifest_version")
    manifest = connection.execute(
        "SELECT payload_hash,status FROM subject_manifest_versions WHERE manifest_version=?",
        (manifest_version,),
    ).fetchone()
    if manifest is None or manifest["status"] != "adopted":
        raise ValueError("只有 adopted Manifest 可创建活动学科")
    capabilities = activate.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities 必须为对象")
    for key, value in capabilities.items():
        validate_capability_key(key)
        if type(value) is not bool:
            raise ValueError(f"能力声明必须是布尔值：{key}")
    old_subject_key = None
    archived_plan_ids: list[int] = []
    reason = str(activate.get("reason") or "启用已采纳学科").strip()
    if archive_actions:
        archive = archive_actions[0]
        old_subject_key = validate_subject_key(archive.get("subject_key"))
        old = connection.execute(
            "SELECT lifecycle_status,object_version FROM subject_catalog WHERE subject_key=?",
            (old_subject_key,),
        ).fetchone()
        if old is None or old["lifecycle_status"] != "active":
            raise ValueError("待切换旧学科不是 active")
        expected = json.loads(changeset["input_version_vector_json"])["subjects"].get(old_subject_key)
        changed = connection.execute(
            """
            UPDATE subject_catalog
            SET lifecycle_status='archived',object_version=object_version+1,updated_at=CURRENT_TIMESTAMP
            WHERE subject_key=? AND lifecycle_status='active' AND object_version=?
            """,
            (old_subject_key, expected),
        ).rowcount
        if changed != 1:
            raise ValueError("旧学科归档 compare-and-swap 冲突")
        archived_plan_ids = _archive_plans(connection, old_subject_key)
        archive_reason = str(archive.get("reason") or "被新学科替换").strip()
        connection.execute(
            """
            INSERT INTO subject_lifecycle_events(operation_id,subject_key,from_status,to_status,reason,actor)
            VALUES (?,?,'active','archived',?,?)
            """,
            (operation_id, old_subject_key, archive_reason, actor),
        )
    normalized_name = normalize_alias(canonical_name)
    connection.execute(
        """
        INSERT INTO subject_catalog(
            subject_key,canonical_name,canonical_name_normalized,display_name,lifecycle_status
        ) VALUES (?,?,?,?,'active')
        """,
        (new_subject_key, canonical_name, normalized_name, display_name),
    )
    aliases = [canonical_name, *(activate.get("aliases") or [])]
    seen: set[str] = set()
    for alias in aliases:
        alias_text = str(alias).strip()
        alias_normalized = normalize_alias(alias_text)
        if alias_normalized in seen:
            continue
        seen.add(alias_normalized)
        connection.execute(
            "INSERT INTO subject_aliases(alias_normalized,alias,subject_key) VALUES (?,?,?)",
            (alias_normalized, alias_text, new_subject_key),
        )
    for capability_key in sorted(CAPABILITY_KEYS):
        connection.execute(
            "INSERT INTO subject_capabilities(subject_key,capability_key,declared_supported) VALUES (?,?,?)",
            (new_subject_key, capability_key, int(capabilities.get(capability_key, False))),
        )
    modules = activate.get("modules") or []
    if not isinstance(modules, list):
        raise ValueError("modules 必须为列表")
    new_module_keys: list[str] = []
    seen_module_names: set[str] = set()
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise ValueError(f"modules[{index}] 必须为对象")
        module_key = validate_module_key(module.get("module_key"))
        module_name = str(module.get("canonical_name") or "").strip()
        module_display_name = str(module.get("display_name") or module_name).strip()
        if not module_name or not module_display_name:
            raise ValueError(f"modules[{index}] 名称不得为空")
        normalized_module_name = normalize_alias(module_name)
        if normalized_module_name in seen_module_names:
            raise ValueError(f"模块名称重复：{module_name}")
        seen_module_names.add(normalized_module_name)
        connection.execute(
            """
            INSERT INTO subject_module_identities(
                module_key,subject_key,canonical_name,
                canonical_name_normalized,display_name
            ) VALUES (?,?,?,?,?)
            """,
            (
                module_key,
                new_subject_key,
                module_name,
                normalized_module_name,
                module_display_name,
            ),
        )
        new_module_keys.append(module_key)
    new_plan_id = _create_core_plan(
        connection, operation_id, new_subject_key, canonical_name, activate.get("core_plan")
    )
    connection.execute(
        """
        INSERT INTO subject_lifecycle_events(operation_id,subject_key,from_status,to_status,reason,actor)
        VALUES (?,?,'absent','active',?,?)
        """,
        (operation_id, new_subject_key, reason, actor),
    )
    connection.execute(
        "UPDATE subject_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1"
    )
    revision = int(
        connection.execute(
            "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
        ).fetchone()[0]
    )
    detail = {
        "old_subject_key": old_subject_key,
        "new_subject_key": new_subject_key,
        "new_module_keys": new_module_keys,
        "archived_plan_ids": archived_plan_ids,
        "new_plan_id": new_plan_id,
        "catalog_revision": revision,
    }
    connection.execute(
        "INSERT INTO subject_operation_events(operation_id,attempt_id,event_type,detail_json) VALUES (?,?, 'switch_applied',?)",
        (operation_id, attempt_id, json.dumps(detail, ensure_ascii=False, sort_keys=True)),
    )
    return detail


def _apply_compensation(connection, operation_id: str, attempt_id: str, actor: str, payload: dict) -> dict:
    actions = payload.get("actions")
    if not isinstance(actions, list) or len(actions) != 1 or actions[0].get("type") != "compensate_switch":
        raise ValueError("补偿变更集只接受单一 compensate_switch 动作")
    action = actions[0]
    original_operation_id = str(action.get("original_operation_id") or "")
    event = connection.execute(
        """
        SELECT detail_json FROM subject_operation_events
        WHERE operation_id=? AND event_type='switch_applied'
        ORDER BY event_id DESC LIMIT 1
        """,
        (original_operation_id,),
    ).fetchone()
    if event is None:
        raise ValueError("找不到原切换操作的已提交事实")
    original = connection.execute(
        "SELECT status FROM subject_change_operations WHERE operation_id=?",
        (original_operation_id,),
    ).fetchone()
    if original is None or original["status"] not in {
        "db_committed_projection_pending", "completed", "compensation_required"
    }:
        raise ValueError("原切换操作当前不可补偿")
    detail = json.loads(event["detail_json"])
    new_subject_key = detail["new_subject_key"]
    old_subject_key = detail.get("old_subject_key")
    new_row = connection.execute(
        "SELECT lifecycle_status,object_version FROM subject_catalog WHERE subject_key=?",
        (new_subject_key,),
    ).fetchone()
    if new_row is None or new_row["lifecycle_status"] != "active":
        raise ValueError("新学科不处于可补偿 active 状态")
    connection.execute(
        """
        UPDATE subject_catalog
        SET lifecycle_status='archived',object_version=object_version+1,updated_at=CURRENT_TIMESTAMP
        WHERE subject_key=? AND lifecycle_status='active'
        """,
        (new_subject_key,),
    )
    _archive_plans(connection, new_subject_key)
    reason = str(action.get("reason") or "补偿撤销").strip()
    connection.execute(
        """
        INSERT INTO subject_lifecycle_events(operation_id,subject_key,from_status,to_status,reason,actor)
        VALUES (?,?,'active','archived',?,?)
        """,
        (operation_id, new_subject_key, reason, actor),
    )
    if old_subject_key:
        old_row = connection.execute(
            "SELECT lifecycle_status FROM subject_catalog WHERE subject_key=?",
            (old_subject_key,),
        ).fetchone()
        if old_row is None or old_row["lifecycle_status"] != "archived":
            raise ValueError("旧学科不处于可恢复 archived 状态")
        connection.execute(
            """
            UPDATE subject_catalog
            SET lifecycle_status='active',object_version=object_version+1,updated_at=CURRENT_TIMESTAMP
            WHERE subject_key=? AND lifecycle_status='archived'
            """,
            (old_subject_key,),
        )
        plan_ids = [int(value) for value in detail.get("archived_plan_ids", [])]
        if plan_ids:
            placeholders = ",".join("?" for _ in plan_ids)
            connection.execute(
                f"UPDATE study_plans SET status='active',archived_at=NULL WHERE id IN ({placeholders}) AND status='archived'",
                tuple(plan_ids),
            )
        connection.execute(
            """
            INSERT INTO subject_lifecycle_events(operation_id,subject_key,from_status,to_status,reason,actor)
            VALUES (?,?,'archived','active',?,?)
            """,
            (operation_id, old_subject_key, reason, actor),
        )
    connection.execute(
        "UPDATE subject_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1"
    )
    revision = int(
        connection.execute(
            "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
        ).fetchone()[0]
    )
    result = {
        "original_operation_id": original_operation_id,
        "old_subject_key": old_subject_key,
        "new_subject_key": new_subject_key,
        "catalog_revision": revision,
    }
    connection.execute(
        "INSERT INTO subject_operation_events(operation_id,attempt_id,event_type,detail_json) VALUES (?,?,'compensation_applied',?)",
        (operation_id, attempt_id, json.dumps(result, ensure_ascii=False, sort_keys=True)),
    )
    connection.execute(
        "UPDATE subject_change_operations SET status='compensated',updated_at=CURRENT_TIMESTAMP WHERE operation_id=?",
        (original_operation_id,),
    )
    return result


def _committed_result(connection, operation_id: str) -> dict:
    row = connection.execute(
        """
        SELECT event_type,detail_json FROM subject_operation_events
        WHERE operation_id=? AND event_type IN ('switch_applied','compensation_applied')
        ORDER BY event_id DESC LIMIT 1
        """,
        (operation_id,),
    ).fetchone()
    detail = json.loads(row["detail_json"]) if row else {}
    return {"operation_id": operation_id, **detail}


def execute_switch_changeset(
    db_path: Path | str,
    operation_id: str,
    *,
    actor: str,
    attempt_id: str | None = None,
    fail_before_commit: bool = False,
    simulate_response_loss: bool = False,
) -> dict[str, object]:
    """Execute an adopted activation/switch or an approved compensation changeset."""
    if not actor.strip():
        raise ValueError("actor 必须非空")
    identifier = attempt_id or f"attempt:{uuid.uuid4().hex}"
    repository = SubjectCatalogRepository(db_path)
    with repository._open(readonly=False) as connection:
        connection.execute(
            "INSERT INTO subject_operation_attempts(attempt_id,operation_id,status,actor) VALUES (?,?,'running',?)",
            (identifier, operation_id, actor.strip()),
        )
        current = connection.execute(
            "SELECT status FROM subject_change_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if current is None:
            raise LookupError(f"未知 operation_id：{operation_id}")
        if current["status"] in {"db_committed_projection_pending", "completed"}:
            result = _committed_result(connection, operation_id)
            connection.execute(
                "UPDATE subject_operation_attempts SET status='idempotent_replay',finished_at=CURRENT_TIMESTAMP WHERE attempt_id=?",
                (identifier,),
            )
            connection.execute(
                "INSERT INTO subject_operation_events(operation_id,attempt_id,event_type) VALUES (?,?,'idempotent_replay')",
                (operation_id, identifier),
            )
            return {
                **result,
                "attempt_id": identifier,
                "status": current["status"],
                "idempotent": True,
            }
    try:
        with repository._open(readonly=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation, changeset, payload, vector = _approved_rows(connection, operation_id)
            _check_version_vector(connection, operation, vector)
            connection.execute(
                "UPDATE subject_change_operations SET status='executing',updated_at=CURRENT_TIMESTAMP WHERE operation_id=?",
                (operation_id,),
            )
            actions = payload.get("actions")
            if isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict) and actions[0].get("type") == "compensate_switch":
                result = _apply_compensation(
                    connection, operation_id, identifier, actor.strip(), payload
                )
            else:
                result = _apply_switch(
                    connection, operation_id, identifier, actor.strip(), payload, changeset
                )
            if fail_before_commit:
                raise RuntimeError("injected failure before commit")
            task_id = f"projection:{operation_id}"
            connection.execute(
                """
                INSERT INTO subject_projection_outbox(task_id,operation_id,target_revision,status)
                VALUES (?,?,?,'pending')
                """,
                (task_id, operation_id, result["catalog_revision"]),
            )
            connection.execute(
                "UPDATE subject_change_operations SET status='db_committed_projection_pending',updated_at=CURRENT_TIMESTAMP WHERE operation_id=?",
                (operation_id,),
            )
        _finish_attempt(db_path, identifier, "succeeded")
    except Exception as error:
        with repository._open(readonly=False) as connection:
            connection.execute(
                """
                UPDATE subject_change_operations
                SET status='failed_before_commit',updated_at=CURRENT_TIMESTAMP
                WHERE operation_id=? AND status IN ('approved','executing')
                """,
                (operation_id,),
            )
        _finish_attempt(db_path, identifier, "failed", f"{type(error).__name__}: {error}")
        raise
    response = {
        "operation_id": operation_id,
        "attempt_id": identifier,
        "status": "db_committed_projection_pending",
        "projection_task_id": f"projection:{operation_id}",
        "idempotent": False,
        **result,
    }
    if simulate_response_loss:
        raise ConnectionError("simulated response loss after database commit")
    return response
