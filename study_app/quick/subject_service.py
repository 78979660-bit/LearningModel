"""Path-injected QML adapter for explicitly confirmed subject lifecycle changes.

This module deliberately delegates all lifecycle and impact decisions to
``core.subject_management``. The adapter only keeps opaque, process-local UI
handles and converts the core result into small QML-safe dictionaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from typing import Any, Callable

from study_app.core import subject_management
from study_app.data.subject_repository import SubjectCatalogRepository


_PREVIEW_TTL_SECONDS = 30 * 60
_MAX_PREVIEWS = 32
_MAX_OPERATION_HANDLES = 256


class SubjectOperationOutcomeUnknownError(RuntimeError):
    """A lifecycle call raised and its operation event could not be checked."""


@dataclass
class _Preview:
    token: str
    core_operation_id: str
    created_at: float
    kind: str
    name: str
    subject_key: str
    module_count: int
    topic_count: int
    impact: dict[str, Any]
    confirming: bool = False


@dataclass(frozen=True)
class _Operation:
    handle: str
    core_operation_id: str
    kind: str
    name: str
    subject_key: str


class SubjectService:
    """Qt-independent subject listing, preview, confirmation, and retry."""

    def __init__(
        self,
        db_path: str | Path,
        model_path: str | Path,
        records_path: str | Path,
        initialize: Callable[[], Any] | None = None,
    ) -> None:
        # Store paths only. The initializer and every database access are lazy.
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        self._initialize = initialize
        self._initialized = False
        self._lock = threading.RLock()
        self._previews: dict[str, _Preview] = {}
        self._operations: dict[str, _Operation] = {}
        self._operation_handles: dict[str, str] = {}

    def _ensure_initialized(self) -> None:
        with self._lock:
            if self._initialized:
                return
            if self._initialize is not None:
                self._initialize()
            self._initialized = True
            self._initialize = None

    def load(self) -> dict[str, Any]:
        """Return catalog state plus a sanitized lifecycle and projection audit."""
        self._ensure_initialized()
        repository = SubjectCatalogRepository(self.db_path)
        with repository.transaction(snapshot=True) as connection:
            revision = int(
                connection.execute(
                    "SELECT catalog_revision FROM subject_catalog_state WHERE singleton=1"
                ).fetchone()[0]
            )
            subject_rows = connection.execute(
                """
                SELECT subject.subject_key,subject.canonical_name,subject.display_name,
                       subject.lifecycle_status,subject.object_version,
                       (SELECT count(*) FROM subject_structure_versions version
                        JOIN subject_structure_modules module
                          ON module.structure_version=version.structure_version
                        WHERE version.subject_key=subject.subject_key AND version.is_current=1) AS module_count,
                       (SELECT count(*) FROM subject_structure_versions version
                        JOIN subject_structure_topics topic
                          ON topic.structure_version=version.structure_version
                        WHERE version.subject_key=subject.subject_key AND version.is_current=1) AS topic_count
                FROM subject_catalog subject
                ORDER BY subject.canonical_name_normalized
                """
            ).fetchall()
            capability_rows = connection.execute(
                "SELECT subject_key,capability_key,declared_supported FROM subject_capabilities "
                "ORDER BY subject_key,capability_key"
            ).fetchall()
            operation_rows = connection.execute(
                """
                SELECT operation.operation_id,operation.status,operation.expected_catalog_revision,
                       operation.updated_at,changeset.payload_json,
                       outbox.status AS projection_status
                FROM subject_change_operations operation
                JOIN subject_changesets changeset ON changeset.operation_id=operation.operation_id
                LEFT JOIN subject_projection_outbox outbox ON outbox.operation_id=operation.operation_id
                ORDER BY operation.updated_at DESC,operation.operation_id DESC
                LIMIT 100
                """
            ).fetchall()
            lifecycle_rows = connection.execute(
                """
                SELECT event.subject_key,subject.canonical_name,event.from_status,event.to_status,
                       event.reason,event.created_at
                FROM subject_lifecycle_events event
                JOIN subject_catalog subject ON subject.subject_key=event.subject_key
                ORDER BY event.lifecycle_event_id DESC LIMIT 50
                """
            ).fetchall()

        capabilities: dict[str, dict[str, bool]] = {}
        for row in capability_rows:
            capabilities.setdefault(str(row["subject_key"]), {})[
                str(row["capability_key"])
            ] = bool(row["declared_supported"])
        subjects = [
            {
                "subjectKey": str(row["subject_key"]),
                "name": str(row["canonical_name"]),
                "displayName": str(row["display_name"]),
                "lifecycleStatus": str(row["lifecycle_status"]),
                "objectVersion": int(row["object_version"]),
                "moduleCount": int(row["module_count"] or 0),
                "topicCount": int(row["topic_count"] or 0),
                "capabilities": capabilities.get(str(row["subject_key"]), {}),
            }
            for row in subject_rows
        ]
        operations = self._operation_audit(operation_rows)
        history = [
            {
                "subjectKey": str(row["subject_key"]),
                "name": str(row["canonical_name"]),
                "fromStatus": str(row["from_status"]),
                "toStatus": str(row["to_status"]),
                "reason": str(row["reason"]),
                "createdAt": str(row["created_at"]),
            }
            for row in lifecycle_rows
        ]
        return {
            "catalogRevision": revision,
            "subjects": subjects,
            "operations": operations,
            "history": history,
        }

    def _operation_audit(self, rows) -> list[dict[str, Any]]:
        audit: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                actions = payload.get("actions") or []
                action = actions[0] if len(actions) == 1 else {}
            except (TypeError, json.JSONDecodeError):
                continue
            if action.get("type") != subject_management.VERSION:
                continue
            kind = str(action.get("kind") or "")
            if kind not in subject_management.LABELS:
                continue
            name = str(action.get("name") or "")
            subject_key = str(action.get("subject_key") or "")
            core_operation_id = str(row["operation_id"])
            projection_status = row["projection_status"]
            operation_handle = ""
            can_retry = False
            if projection_status in {"pending", "failed"}:
                draft = _Preview(
                    token="",
                    core_operation_id=core_operation_id,
                    created_at=0,
                    kind=kind,
                    name=name,
                    subject_key=subject_key,
                    module_count=0,
                    topic_count=0,
                    impact={},
                )
                try:
                    proof = self._checked_operation_event(draft)
                except (OSError, sqlite3.Error):
                    proof = None
                if proof is not None:
                    operation_handle = self._remember_operation_fields(
                        core_operation_id, kind, name, subject_key
                    )
                    can_retry = True
            expected_diff = payload.get("expected_diff") or {}
            impact = action.get("impact") or {}
            audit.append(
                {
                    "operationId": operation_handle,
                    "name": name,
                    "subjectKey": subject_key,
                    "kind": kind,
                    "label": subject_management.LABELS[kind],
                    "status": str(row["status"]),
                    "projectionStatus": str(projection_status or ""),
                    "canRetryProjection": can_retry,
                    "expectedCatalogRevision": int(row["expected_catalog_revision"]),
                    "updatedAt": str(row["updated_at"]),
                    "recordCount": int((impact.get("records") or 0)),
                    "planCount": len(impact.get("plan_ids") or []),
                    "moduleCount": int(expected_diff.get("module_count") or 0),
                    "topicCount": int(expected_diff.get("topic_count") or 0),
                }
            )
        return audit

    def preview(
        self,
        kind: str,
        name: str = "",
        outline: str = "",
        subject_key: str | None = None,
    ) -> dict[str, Any]:
        """Prepare one core changeset and expose only an opaque local token."""
        self._ensure_initialized()
        if kind not in subject_management.LABELS:
            raise ValueError("不支持的学科管理操作")
        if kind == "introduce":
            if subject_key not in (None, ""):
                raise ValueError("引入学科不接受已有学科身份")
        elif not isinstance(subject_key, str) or not subject_key.strip():
            raise ValueError("请选择目标学科")

        prepared = subject_management.prepare_management(
            self.db_path,
            kind,
            name=name,
            outline=outline,
            subject_key=subject_key,
        )
        action = prepared.payload["actions"][0]
        raw_impact = action.get("impact") or {}
        raw_scopes = raw_impact.get("plan_scopes") or []
        impact = {
            "records": int(raw_impact.get("records") or 0),
            "plans": len(raw_impact.get("plan_ids") or []),
            # Scope labels help make shared-plan effects legible without
            # exposing plan IDs, hashes, backup paths, or core operation IDs.
            "planScopes": sorted({str(scope) for scope in raw_scopes if scope is not None}),
        }
        modules = action.get("modules") or []
        token = secrets.token_urlsafe(32)
        draft = _Preview(
            token=token,
            core_operation_id=prepared.operation_id,
            created_at=time.monotonic(),
            kind=kind,
            name=str(action["name"]),
            subject_key=str(action["subject_key"]),
            module_count=len(modules),
            topic_count=sum(len(module.get("topics") or []) for module in modules),
            impact=impact,
        )
        with self._lock:
            self._purge_expired_previews_locked()
            self._previews[token] = draft
            while len(self._previews) > _MAX_PREVIEWS:
                oldest = min(self._previews.values(), key=lambda item: item.created_at)
                self._previews.pop(oldest.token, None)
        return {
            "token": token,
            "kind": kind,
            "label": subject_management.LABELS[kind],
            "name": draft.name,
            "subjectKey": draft.subject_key,
            "moduleCount": draft.module_count,
            "topicCount": draft.topic_count,
            "impact": impact,
            "requiresConfirmation": True,
        }

    def import_outline(self, path: str | Path) -> dict[str, Any]:
        """Read a small explicit outline as a draft; this never prepares or saves."""
        candidate = Path(path)
        suffix = candidate.suffix.casefold()
        if suffix not in {".txt", ".md", ".json"}:
            raise ValueError("仅支持 TXT、MD 或 JSON 目录文件")
        try:
            size = candidate.stat().st_size
        except OSError as error:
            raise ValueError("无法读取所选目录文件") from error
        if size > 300_000:
            raise ValueError("目录文件不能超过 300 KB")
        try:
            with candidate.open("rb") as stream:
                raw = stream.read(300_001)
            if len(raw) > 300_000:
                raise ValueError("目录文件不能超过 300 KB")
            text = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("目录文件必须使用 UTF-8 编码") from error
        except OSError as error:
            raise ValueError("无法读取所选目录文件") from error

        name: str | None = None
        if suffix == ".json":
            try:
                payload = json.loads(text, object_pairs_hook=self._unique_json_object)
            except json.JSONDecodeError as error:
                raise ValueError("JSON 目录格式无效") from error
            if not isinstance(payload, dict) or set(payload) != {"name", "modules"}:
                raise ValueError("JSON 目录必须且只能包含 name 和 modules 字段")
            raw_name = payload["name"]
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ValueError("JSON 目录的 name 必须是非空文本")
            name = raw_name
            raw_modules = payload["modules"]
            if not isinstance(raw_modules, list) or not raw_modules or len(raw_modules) > 100:
                raise ValueError("JSON 目录需要 1–100 个模块")
            lines: list[str] = []
            for module in raw_modules:
                if not isinstance(module, dict) or set(module) != {"name", "topics"}:
                    raise ValueError("每个模块必须且只能包含 name 和 topics")
                module_name = module["name"]
                topics = module["topics"]
                if not isinstance(module_name, str) or not module_name.strip():
                    raise ValueError("模块 name 必须是非空文本")
                if not isinstance(topics, list) or not topics:
                    raise ValueError("每个模块都需要至少一个知识点")
                for topic in topics:
                    if not isinstance(topic, str) or not topic.strip():
                        raise ValueError("知识点必须是非空文本")
                    lines.append(f"{module_name} / {topic}")
            text = "\n".join(lines)

        modules = subject_management.parse_outline(text)
        normalized_outline = "\n".join(
            f"{module['name']} / {topic}"
            for module in modules
            for topic in module["topics"]
        )
        result: dict[str, Any] = {
            "outline": normalized_outline,
            "filename": candidate.name,
        }
        if name is not None:
            result["name"] = name
        return result

    @staticmethod
    def _unique_json_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 目录存在重复字段")
            result[key] = value
        return result

    def confirm(
        self,
        token: str,
        typed_name: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        """Execute only the displayed preview after full-name acknowledgement."""
        self._ensure_initialized()
        if not isinstance(token, str) or not token:
            raise ValueError("预览已失效，请重新预览")
        if confirmed is not True:
            raise ValueError("请先勾选影响说明")
        with self._lock:
            self._purge_expired_previews_locked()
            draft = self._previews.get(token)
            if draft is None:
                raise ValueError("预览已失效，请重新预览")
            if draft.confirming:
                raise ValueError("此学科操作正在处理中")
            if not isinstance(typed_name, str) or typed_name != draft.name:
                raise ValueError("请准确输入学科全名")
            draft.confirming = True

        try:
            core_result: dict[str, Any] | None = None
            core_raised_after_call = False
            try:
                core_result = subject_management.execute_management(
                    self.db_path,
                    draft.core_operation_id,
                    confirmation_name=typed_name,
                    acknowledged=True,
                )
            except Exception as error:
                core_raised_after_call = True
                try:
                    proof = self._checked_operation_event(draft)
                except Exception as proof_error:
                    raise SubjectOperationOutcomeUnknownError(
                        "学科操作结果暂时无法核实；请先刷新状态再继续。"
                    ) from proof_error
                if proof is None:
                    raise
                # The core transaction committed before a later read/projection
                # step failed. Report persistence only because the matching
                # operation event proves this exact change was applied.
                core_result = {
                    "operation_id": draft.core_operation_id,
                    "kind": draft.kind,
                    "name": draft.name,
                    "subject_key": draft.subject_key,
                    "status": "db_committed_projection_pending",
                    "warning": f"学科变更已保存；后续目录更新待重试（{type(error).__name__}）。",
                }
            try:
                proof = self._checked_operation_event(draft)
            except Exception as proof_error:
                raise SubjectOperationOutcomeUnknownError(
                    "学科操作返回后，暂时无法核实对应的保存记录；请先刷新状态再继续。"
                ) from proof_error
            if proof is None:
                raise SubjectOperationOutcomeUnknownError(
                    "学科操作返回后，暂时无法核实对应的保存记录；请先刷新状态再继续。"
                )
            handle = self._remember_operation(draft)
            status = str(core_result.get("status") or proof.get("status") or "db_committed_projection_pending")
            projection_warning = ""
            if status == "db_committed_projection_pending" or core_result.get("idempotent") or core_raised_after_call:
                try:
                    outbox = self._outbox_row(
                        "projection:" + draft.core_operation_id,
                        draft.core_operation_id,
                    )
                except Exception:
                    outbox = None
                    projection_warning = "学科变更已保存；目录更新状态暂时无法核实，请稍后重试。"
                if outbox and outbox["status"] in {"published", "superseded"}:
                    status = "completed"
                    core_result["warning"] = ""
                elif outbox and outbox["status"] in {"pending", "failed"}:
                    status = "db_committed_projection_pending"
                elif outbox is None:
                    status = "db_committed_projection_pending"
                    if not projection_warning:
                        projection_warning = "学科变更已保存，但找不到对应目录导出任务。"
                else:
                    status = "db_committed_projection_pending"
                    projection_warning = "学科变更已保存；目录更新状态无法解释，请稍后重试。"
            pending = status == "db_committed_projection_pending"
            warning = str(core_result.get("warning") or "")
            if projection_warning:
                warning = projection_warning
            if pending and not warning:
                warning = "学科变更已保存，目录导出待重试。"
            return {
                "saved": True,
                "operationId": handle,
                "subjectKey": draft.subject_key,
                "name": draft.name,
                "kind": draft.kind,
                "status": status,
                "idempotent": bool(core_result.get("idempotent", False)),
                "backupVerified": bool(proof.get("backup_path")),
                "projectionPending": pending,
                "warning": warning,
                "message": self._result_message(status, bool(core_result.get("idempotent", False))),
            }
        finally:
            with self._lock:
                current = self._previews.get(token)
                if current is not None:
                    current.confirming = False

    def retry_projection(self, operation_id: str) -> dict[str, Any]:
        """Retry only the projection outbox item for a locally returned handle."""
        self._ensure_initialized()
        with self._lock:
            operation = self._operations.get(operation_id) if isinstance(operation_id, str) else None
        if operation is None:
            raise ValueError("学科操作已失效，请重新加载后查看状态")
        proof = self._checked_operation_event(
            _Preview(
                token="",
                core_operation_id=operation.core_operation_id,
                created_at=0,
                kind=operation.kind,
                name=operation.name,
                subject_key=operation.subject_key,
                module_count=0,
                topic_count=0,
                impact={},
            )
        )
        if proof is None:
            raise ValueError("找不到此学科操作的保存凭据，不能重试目录导出")
        from study_app.core.subject_projection import process_projection_outbox

        task_id = "projection:" + operation.core_operation_id
        try:
            projection = process_projection_outbox(
                self.db_path,
                self.db_path.with_name("subject_catalog_projection.json"),
                task_id=task_id,
            )
        except Exception as error:
            # The lifecycle commit remains proven even if the projection retry
            # itself failed. Query the exact outbox item only to describe its
            # retry state; no unrelated operation can satisfy this check.
            try:
                outbox = self._outbox_row(task_id, operation.core_operation_id)
            except Exception:
                outbox = None
            if outbox is None:
                return {
                    "saved": True,
                    "operationId": operation.handle,
                    "subjectKey": operation.subject_key,
                    "name": operation.name,
                    "kind": operation.kind,
                    "status": "db_committed_projection_pending",
                    "idempotent": False,
                    "backupVerified": bool(proof.get("backup_path")),
                    "projectionPending": True,
                    "warning": "学科变更已保存；目录更新状态暂时无法读取，请稍后重试。",
                    "message": "学科已保存；目录更新状态待核实。",
                }
            if outbox["status"] in {"published", "superseded"}:
                return {
                    "saved": True,
                    "operationId": operation.handle,
                    "subjectKey": operation.subject_key,
                    "name": operation.name,
                    "kind": operation.kind,
                    "status": "completed",
                    "idempotent": False,
                    "backupVerified": bool(proof.get("backup_path")),
                    "projectionPending": False,
                    "warning": "",
                    "message": "学科已保存，目录已更新。" if outbox["status"] == "published" else "学科已保存；目录已由更新后的版本接替。",
                }
            if outbox["status"] not in {"pending", "failed"}:
                raise SubjectOperationOutcomeUnknownError(
                    "学科变更已保存，但目录导出状态无法解释。"
                ) from error
            return {
                "saved": True,
                "operationId": operation.handle,
                "subjectKey": operation.subject_key,
                "name": operation.name,
                "kind": operation.kind,
                "status": "db_committed_projection_pending",
                "idempotent": False,
                "backupVerified": bool(proof.get("backup_path")),
                "projectionPending": True,
                "warning": "学科变更已保存，目录导出仍待重试。",
                "message": "学科已保存；目录导出暂未完成。",
            }
        status = str(projection.get("status") or "published")
        pending = status not in {"published", "superseded"}
        return {
            "saved": True,
            "operationId": operation.handle,
            "subjectKey": operation.subject_key,
            "name": operation.name,
            "kind": operation.kind,
            "status": "db_committed_projection_pending" if pending else "completed",
            "idempotent": bool(projection.get("idempotent", False)),
            "backupVerified": bool(proof.get("backup_path")),
            "projectionPending": pending,
            "warning": "" if not pending else "学科变更已保存，目录导出仍待重试。",
            "message": "学科已保存，目录已更新。" if status == "published" else "学科已保存；目录已由更新后的版本接替。",
        }

    def _checked_operation_event(self, draft: _Preview) -> dict[str, Any] | None:
        repository = SubjectCatalogRepository(self.db_path)
        with repository.transaction() as connection:
            row = connection.execute(
                "SELECT detail_json FROM subject_operation_events "
                "WHERE operation_id=? AND event_type='subject_management_applied' "
                "ORDER BY event_id DESC LIMIT 1",
                (draft.core_operation_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            detail = json.loads(row["detail_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if (
            detail.get("operation_id") != draft.core_operation_id
            or detail.get("kind") != draft.kind
            or detail.get("name") != draft.name
            or detail.get("subject_key") != draft.subject_key
        ):
            return None
        return detail

    def _outbox_row(self, task_id: str, core_operation_id: str) -> dict[str, Any] | None:
        repository = SubjectCatalogRepository(self.db_path)
        with repository.transaction() as connection:
            row = connection.execute(
                "SELECT task_id,operation_id,status FROM subject_projection_outbox WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None or row["operation_id"] != core_operation_id:
            return None
        return dict(row)

    def _remember_operation(self, draft: _Preview) -> str:
        return self._remember_operation_fields(
            draft.core_operation_id, draft.kind, draft.name, draft.subject_key
        )

    def _remember_operation_fields(
        self, core_operation_id: str, kind: str, name: str, subject_key: str
    ) -> str:
        with self._lock:
            handle = self._operation_handles.get(core_operation_id)
            if handle:
                return handle
            handle = "subject-op:" + secrets.token_urlsafe(24)
            operation = _Operation(
                handle=handle,
                core_operation_id=core_operation_id,
                kind=kind,
                name=name,
                subject_key=subject_key,
            )
            self._operations[handle] = operation
            self._operation_handles[core_operation_id] = handle
            while len(self._operations) > _MAX_OPERATION_HANDLES:
                oldest_handle = next(iter(self._operations))
                oldest = self._operations.pop(oldest_handle)
                self._operation_handles.pop(oldest.core_operation_id, None)
            return handle

    def _purge_expired_previews_locked(self) -> None:
        now = time.monotonic()
        expired = [
            token
            for token, draft in self._previews.items()
            if now - draft.created_at > _PREVIEW_TTL_SECONDS
        ]
        for token in expired:
            self._previews.pop(token, None)

    @staticmethod
    def _result_message(status: str, idempotent: bool) -> str:
        if idempotent:
            return "此预览对应的学科操作已保存，已返回原操作结果。"
        if status == "db_committed_projection_pending":
            return "学科变更已保存，目录导出待重试。"
        return "学科变更已保存，备份已校验，目录已更新。"
