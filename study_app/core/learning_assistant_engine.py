"""Learning assistant orchestrator.

The model (or the local deterministic intent parser) only ever produces
proposals; this engine validates them deterministically, previews impact,
executes confirmed actions inside the existing repository/transaction
boundaries, journalling every lifecycle state, and provides undo.
No free-form SQL, no direct model-JSON writes, no repository bypass.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from study_app.core.learning_assistant_schema import (
    SCHEMA_VERSION,
    ActionProposal,
    default_disclosure,
    new_action_id,
    proposal_from_dict,
)
from study_app.data.database import (
    DEFAULT_DB_PATH,
    DatabaseNotInitializedError,
    connect,
    get_setting,
    list_subject_names,
    set_setting,
)
from study_app.paths import MODEL_PATH

JOURNAL_INDEX_KEY = "learning_assistant_journal"
JOURNAL_CAP = 400
ACTION_KEY_PREFIX = "learning_assistant_action_"

JOURNAL_STATES = (
    "validated",
    "awaiting_confirmation",
    "rejected",
    "business_invalid",
    "adopted",
    "local_commit_failed",
    "completed",
    "undone",
)


class LearningAssistantError(RuntimeError):
    """Base class for assistant orchestration failures."""


class StaleProposalError(LearningAssistantError):
    """Proposal version vector no longer matches the world."""


class AdvisorRequiredError(LearningAssistantError):
    """AGENTS.md advisor analysis is required but unavailable."""


class ActionNotUndoableError(LearningAssistantError):
    """No undoable action is available in the journal."""


@dataclass(frozen=True)
class PreviewBundle:
    proposal: ActionProposal
    mastery_preview: dict[str, Any]
    plan_impact: dict[str, Any]
    warnings: tuple[str, ...]
    version_token: dict[str, Any]
    # Returned only to the local UI process.  The journal stores a SHA-256
    # digest, never the bearer token itself.
    confirmation_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ActionReceipt:
    action_id: str
    action_type: str
    status: str
    record_id: int | None = None
    alert_id: int | None = None
    mastery_changes: tuple[dict, ...] = ()
    alert_result: dict[str, Any] | None = None
    message: str = ""
    warnings: tuple[str, ...] = ()
    executed_at: str = ""
    undo_token: dict[str, Any] | None = None
    disclosure: dict[str, Any] = field(default_factory=dict)
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "status": self.status,
            "record_id": self.record_id,
            "alert_id": self.alert_id,
            "mastery_changes": [dict(item) for item in self.mastery_changes],
            "alert_result": self.alert_result,
            "message": self.message,
            "warnings": list(self.warnings),
            "executed_at": self.executed_at,
            "undo_token": self.undo_token,
            "disclosure": dict(self.disclosure),
            "replayed": self.replayed,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ActionReceipt":
        return cls(
            action_id=raw["action_id"],
            action_type=raw["action_type"],
            status=raw["status"],
            record_id=raw.get("record_id"),
            alert_id=raw.get("alert_id"),
            mastery_changes=tuple(raw.get("mastery_changes") or ()),
            alert_result=raw.get("alert_result"),
            message=raw.get("message", ""),
            warnings=tuple(raw.get("warnings") or ()),
            executed_at=raw.get("executed_at", ""),
            undo_token=raw.get("undo_token"),
            disclosure=dict(raw.get("disclosure") or {}),
            replayed=bool(raw.get("replayed")),
        )


class _AuditRef:
    """Minimal shim so audit.mark_* helpers can address a raw audit id."""

    def __init__(self, audit_id: int) -> None:
        self.audit_id = int(audit_id)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _confirmation_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_confirmation_token() -> str:
    return secrets.token_urlsafe(32)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LearningAssistantEngine:
    """Validates, previews, executes, journals and undoes assistant actions."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        model_path: Path | str | None = None,
        actor: str = "learning_assistant",
    ) -> None:
        self.db_path = db_path
        self.model_path = Path(model_path) if model_path is not None else MODEL_PATH
        self.actor = actor
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Journal helpers
    # ------------------------------------------------------------------
    def _action_key(self, action_id: str) -> str:
        return f"{ACTION_KEY_PREFIX}{action_id}"

    def _load_entry(self, action_id: str) -> dict[str, Any] | None:
        entry = get_setting(self._action_key(action_id), None, self.db_path)
        return entry if isinstance(entry, dict) else None

    def _save_entry(self, entry: dict[str, Any]) -> None:
        set_setting(self._action_key(entry["action_id"]), entry, self.db_path)

    def _register_index(self, action_id: str) -> None:
        index = get_setting(JOURNAL_INDEX_KEY, [], self.db_path)
        if not isinstance(index, list):
            index = []
        if action_id not in index:
            index.append(action_id)
        set_setting(JOURNAL_INDEX_KEY, index[-JOURNAL_CAP:], self.db_path)

    def _transition(self, entry: dict[str, Any], state: str, detail: str = "") -> None:
        entry["state"] = state
        entry.setdefault("history", []).append(
            {"state": state, "at": _now_iso(), "detail": detail}
        )
        self._save_entry(entry)

    def load_action(self, action_id: str) -> dict[str, Any] | None:
        return self._load_entry(action_id)

    def load_journal(self) -> list[dict[str, Any]]:
        index = get_setting(JOURNAL_INDEX_KEY, [], self.db_path)
        if not isinstance(index, list):
            return []
        result = []
        for action_id in index:
            entry = self._load_entry(str(action_id))
            if not entry:
                continue
            proposal = entry.get("proposal") or {}
            receipt = entry.get("receipt") or {}
            result.append(
                {
                    "action_id": entry.get("action_id"),
                    "action_type": proposal.get("action_type"),
                    "status": entry.get("state"),
                    "created_at": entry.get("created_at"),
                    "message": receipt.get("message", ""),
                }
            )
        return result

    # ------------------------------------------------------------------
    # Subject resolution and version tokens
    # ------------------------------------------------------------------
    def _subject_resolver(self) -> Callable[[str], object | None]:
        try:
            from study_app.core.subject_catalog import load_catalog_snapshot
            from study_app.data.database import connect_readonly, f5_catalog_is_installed

            with connect_readonly(self.db_path) as connection:
                if not f5_catalog_is_installed(connection):
                    raise LookupError("F5 catalog is in legacy compatibility mode")
            snapshot = load_catalog_snapshot(self.db_path)
            alias_map = snapshot.alias_map()
            subjects_by_key = snapshot.by_key()

            def resolve(name: str):
                try:
                    return snapshot.resolve(name)
                except (LookupError, ValueError):
                    pass
                # Intent extraction often yields a short surrounding phrase
                # such as "数学作业" rather than the bare alias.  Resolve the
                # longest unique catalog alias instead of falling back to the
                # legacy model or guessing between overlapping subject names.
                try:
                    from study_app.core.subject_identity import normalize_alias

                    normalized = normalize_alias(name)
                except ValueError:
                    return None
                matches = [
                    (len(alias), subject_key)
                    for alias, subject_key in alias_map.items()
                    if alias in normalized or normalized in alias
                ]
                if not matches:
                    return None
                longest = max(length for length, _key in matches)
                keys = {
                    subject_key
                    for length, subject_key in matches
                    if length == longest
                }
                if len(keys) != 1:
                    return None
                return subjects_by_key[next(iter(keys))]

            return resolve
        except Exception:
            names = list_subject_names(self.db_path)

            class _ShimSubject:
                def __init__(self, canonical: str) -> None:
                    self.subject_key = None
                    self.canonical_name = canonical
                    self.lifecycle_status = "active"

            def resolve(name: str):
                text = (name or "").strip()
                if not text:
                    return None
                for candidate in names:
                    if candidate and (candidate in text or text in candidate):
                        return _ShimSubject(candidate)
                return None

            return resolve

    def _capture_version_token(self, subject_key: str | None) -> dict[str, Any]:
        from study_app.core.async_tasks import capture_catalog_revision

        token: dict[str, Any] = {
            "catalog_revision": None,
            "subject_key": subject_key,
            "object_version": None,
        }
        try:
            catalog_token = capture_catalog_revision(self.db_path)
            if catalog_token is not None:
                token["catalog_revision"] = catalog_token.catalog_revision
        except Exception:
            token["catalog_revision"] = None
        if subject_key:
            try:
                from study_app.core.async_tasks import capture_subject_revision

                subject_name_token = self._canonical_name(subject_key) or subject_key
                subject_token = capture_subject_revision(self.db_path, subject_name_token)
                if subject_token is not None:
                    token["object_version"] = subject_token.object_version
            except Exception:
                token["object_version"] = None
        return token

    def _revalidate_version_token(self, stored: dict[str, Any]) -> None:
        fresh = self._capture_version_token(stored.get("subject_key"))
        for key in ("catalog_revision", "object_version"):
            before, after = stored.get(key), fresh.get(key)
            if before is not None and after is not None and before != after:
                raise StaleProposalError(
                    f"提案已过期（{key} 已变化：{before} → {after}），请重新预览"
                )

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------
    def _probe_attachments(self, paths: list[str | Path]) -> tuple[dict, ...]:
        probed: list[dict[str, Any]] = []
        for raw_path in paths:
            path = Path(raw_path)
            item: dict[str, Any] = {
                "path": str(path),
                "file_name": path.name,
                "sha256": None,
                "size_bytes": None,
                "kind": path.suffix.lower().lstrip("."),
                "preflight_status": "missing",
                "external_allowed": False,
            }
            if not path.exists() or not path.is_file():
                item["preflight_status"] = "missing"
                probed.append(item)
                continue
            item["size_bytes"] = path.stat().st_size
            try:
                item["sha256"] = _sha256_file(path)
            except OSError:
                item["sha256"] = None
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                try:
                    from study_app.core.subject_documents import DocumentLimits, inspect_pdf

                    inspection = inspect_pdf(path, DocumentLimits())
                    status_value = getattr(inspection, "status", None)
                    if isinstance(status_value, str):
                        item["preflight_status"] = status_value
                    else:
                        item["preflight_status"] = "ready"
                except Exception as error:
                    item["preflight_status"] = "failed"
                    item["preflight_error"] = type(error).__name__
            else:
                item["preflight_status"] = "ready"
            probed.append(item)
        seen_hashes: set[str] = set()
        for item in probed:
            digest = item.get("sha256")
            if digest:
                if digest in seen_hashes:
                    item["duplicate_of_previous"] = True
                seen_hashes.add(digest)
        return tuple(probed)

    def _extract_draft_problems(
        self, attachments: tuple[dict, ...]
    ) -> tuple[dict, ...]:
        paths = [
            item["path"]
            for item in attachments
            if item.get("preflight_status") in {"ready", "needs_review"}
        ]
        if not paths:
            return ()
        try:
            from study_app.ai.attachment_parser import parse_problem_blocks_from_text
            from study_app.data.text_extractor import (
                combined_extracted_text,
                extract_text_from_files,
            )

            extracted = extract_text_from_files(paths)
            combined = combined_extracted_text(extracted)
            blocks = parse_problem_blocks_from_text(combined)
            return tuple(
                {
                    "title": block.title,
                    "statement": block.statement,
                    "source": getattr(block, "source", "attachment_excerpt"),
                }
                for block in blocks or ()
            )
        except Exception:
            return ()

    # ------------------------------------------------------------------
    # Mastery preview
    # ------------------------------------------------------------------
    def _preview_mastery(self, record: dict) -> dict[str, Any]:
        try:
            from study_app.core.learning_assistant_records import preview_mastery_impact

            return preview_mastery_impact(
                record, db_path=self.db_path, model_path=self.model_path
            )
        except Exception as error:
            return {
                "matched_topics": [],
                "subject_mastery": [],
                "no_evidence": True,
                "notes": [f"掌握度预览不可用：{type(error).__name__}: {error}"],
            }

    # ------------------------------------------------------------------
    # Proposal creation
    # ------------------------------------------------------------------
    def _latest_record_id(self) -> int | None:
        """最近一条学习记录的 id（“修改刚才的学习记录”用它定位目标）。"""
        try:
            from study_app.data.database import list_recent_records

            rows = list_recent_records(self.db_path, limit=1)
            return int(rows[0]["id"]) if rows else None
        except Exception:
            return None

    def create_proposal(
        self,
        text: str,
        attachment_paths: list[str | Path] | tuple[str | Path, ...] = (),
        state: Any = None,
    ) -> PreviewBundle:
        from study_app.core.learning_assistant_intent import parse_intent

        text = (text or "").strip()
        if not text and not attachment_paths:
            raise LearningAssistantError("请输入指令或选择附件后再试。")
        probed = self._probe_attachments(list(attachment_paths))
        draft_problems = self._extract_draft_problems(probed)
        resolver = self._subject_resolver()
        proposal = parse_intent(
            text,
            attachments=probed,
            subject_resolver=resolver,
            today=date.today(),
            action_id=new_action_id(),
            last_record_id=self._latest_record_id(),
        )
        warnings = list(proposal.warnings)
        for item in probed:
            if item.get("preflight_status") == "missing":
                warnings.append(f"附件不存在或不可读：{item.get('file_name')}")
            elif item.get("preflight_status") in {"rejected", "failed"}:
                warnings.append(
                    f"附件未通过安全预检（{item.get('preflight_status')}）：{item.get('file_name')}"
                )
            elif item.get("duplicate_of_previous"):
                warnings.append(
                    f"附件与之前选择的文件内容相同（按 sha256 识别）：{item.get('file_name')}"
                )
        if draft_problems:
            warnings.append(
                f"已从附件解析出 {len(draft_problems)} 个题面；仅有题面不等于已完成或做对。"
            )

        # Draft record for previews of record-creating actions.
        draft_record: dict[str, Any] | None = None
        mastery_preview: dict[str, Any] = {
            "matched_topics": [],
            "subject_mastery": [],
            "no_evidence": True,
            "notes": [],
        }
        if proposal.action_type in {"submit_homework", "update_learning_progress"}:
            draft_record = self._draft_record(
                proposal, draft_problems, warnings, source_text=text
            )
            if draft_record is not None:
                mastery_preview = self._preview_mastery(draft_record)

        plan_impact = {
            "affected": False,
            "note": "未关联学习计划条目；确认后不会改动计划状态。",
        }
        confirmation_token = None if proposal.is_readonly else _new_confirmation_token()
        entry = {
            "action_id": proposal.action_id,
            "created_at": _now_iso(),
            "state": "validated" if proposal.is_readonly else "awaiting_confirmation",
            "proposal": proposal.to_dict(),
            "source_text": text,
            "draft_problems": [dict(item) for item in draft_problems],
            "draft_record": draft_record,
            "mastery_preview": mastery_preview,
            "plan_impact": plan_impact,
            "version_token": self._capture_version_token(proposal.subject_key),
            "confirmation_token_sha256": (
                _confirmation_token_digest(confirmation_token)
                if confirmation_token is not None
                else None
            ),
            "confirmation_consumed_at": None,
            "history": [],
            "receipt": None,
        }
        self._transition(
            entry,
            entry["state"],
            "本地确定性校验通过" if proposal.action_type != "answer_query" else "只读提案",
        )
        self._register_index(proposal.action_id)
        return PreviewBundle(
            proposal=proposal,
            mastery_preview=mastery_preview,
            plan_impact=plan_impact,
            warnings=tuple(warnings),
            version_token=dict(entry["version_token"]),
            confirmation_token=confirmation_token,
        )

    def _draft_record(
        self,
        proposal: ActionProposal,
        draft_problems: tuple[dict, ...],
        warnings: list[str],
        source_text: str = "",
    ) -> dict[str, Any] | None:
        from study_app.core.learning_assistant_records import (
            HomeworkFacts,
            ProgressFacts,
            build_record_from_homework,
            build_record_from_progress,
        )

        facts_map = proposal.user_facts
        subject_name = self._subject_name(proposal)
        confirmed_at = date.today().isoformat()
        try:
            if proposal.action_type == "submit_homework":
                facts = HomeworkFacts(
                    subject=subject_name,
                    completed_date=facts_map.get("completed_date"),
                    problems=tuple(draft_problems),
                    claim_all_correct=facts_map.get("correctness_claim") == "all_correct",
                    duration_minutes=facts_map.get("duration_minutes"),
                    note=str(facts_map.get("note") or ""),
                    draft_only=(
                    bool(proposal.attachments)
                    and facts_map.get("correctness_claim") != "all_correct"
                ),
                    independence=str(facts_map.get("independence") or "unknown"),
                    attachments=tuple(
                        {
                            "file_path": item.get("path"),
                            "file_name": item.get("file_name"),
                            "sha256": item.get("sha256"),
                        }
                        for item in proposal.attachments
                    ),
                )
                record, extra_warnings = build_record_from_homework(
                    facts, action_id=proposal.action_id, confirmed_at=confirmed_at
                )
                module_name, topic_name = self._resolve_topic_target(
                    subject_name,
                    str(source_text or facts_map.get("target") or ""),
                )
                if module_name:
                    record["module"] = module_name
                if topic_name:
                    record["topic"] = topic_name
            else:
                facts = ProgressFacts(
                    subject=subject_name,
                    target=str(facts_map.get("target") or ""),
                    kind=str(
                    facts_map.get("kind") or facts_map.get("progress_kind") or "reviewed"
                ),
                    completed_date=facts_map.get("completed_date"),
                    note=str(facts_map.get("note") or ""),
                )
                module_name, topic_name = self._resolve_topic_target(
                    subject_name, facts.target
                )
                record, extra_warnings = build_record_from_progress(
                    facts,
                    action_id=proposal.action_id,
                    confirmed_at=confirmed_at,
                    module_name=module_name,
                    topic_name=topic_name,
                )
            warnings.extend(extra_warnings)
            return record
        except ValueError as error:
            warnings.append(f"记录预览暂不可用：{error}")
            return None

    def _subject_name(self, proposal: ActionProposal) -> str:
        name = str(proposal.user_facts.get("subject") or "").strip()
        if not name:
            for change in proposal.proposed_changes:
                fields = change.get("fields") or {}
                candidate = str(fields.get("subject") or "").strip()
                if candidate:
                    name = candidate
                    break
        if not name:
            name = self._canonical_name(proposal.subject_key) or ""
        return name

    def _canonical_name(self, subject_key: str | None) -> str | None:
        if not subject_key:
            return None
        try:
            from study_app.core.subject_catalog import load_catalog_snapshot

            snapshot = load_catalog_snapshot(self.db_path)
            subject = snapshot.by_key().get(subject_key)
            return subject.canonical_name if subject else None
        except Exception:
            return None

    def _resolve_topic_target(
        self, subject_name: str, target: str
    ) -> tuple[str | None, str | None]:
        target = (target or "").strip()
        if not target:
            return None, None
        try:
            model = json.loads(self.model_path.read_text(encoding="utf-8"))
        except Exception:
            return None, None
        for subject in model.get("subjects", []):
            if subject.get("name") != subject_name:
                continue
            for module in subject.get("modules", []):
                module_name_text = str(module.get("name", ""))
                if target and (target in module_name_text or module_name_text in target):
                    return module.get("name"), None
                for topic in module.get("topics", []):
                    topic_name_text = str(topic.get("name", ""))
                    if target and (
                        target in topic_name_text or topic_name_text in target
                    ):
                        return module.get("name"), topic.get("name")
        return None, None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def confirm_and_execute(
        self,
        action_id: str,
        *,
        confirmation_token: str | None,
        execution_mode: str = "user",
        edited_text: str | None = None,
    ) -> ActionReceipt:
        with self._lock:
            entry = self._load_entry(action_id)
            if not entry:
                raise LearningAssistantError(f"未知动作：{action_id}")
            stored_receipt = entry.get("receipt")
            if entry.get("state") == "completed" and stored_receipt:
                return ActionReceipt.from_dict({**stored_receipt, "replayed": True})
            if entry.get("state") in {"rejected", "undone", "business_invalid"}:
                raise LearningAssistantError(
                    f"动作 {action_id} 已处于终态 {entry.get('state')}，不能再次执行"
                )
            proposal = proposal_from_dict(entry.get("proposal"))
            if proposal.is_readonly:
                raise LearningAssistantError("只读动作没有后台执行通道。")
            if entry.get("state") != "awaiting_confirmation":
                raise LearningAssistantError(
                    f"动作 {action_id} 当前状态为 {entry.get('state')!r}，"
                    "只有待确认提案可以执行"
                )
            if execution_mode not in {"user", "auto"}:
                raise LearningAssistantError(f"未知执行授权模式：{execution_mode!r}")
            if execution_mode == "auto":
                from study_app.core.learning_assistant_policy import can_auto_execute

                if not can_auto_execute(proposal.action_type, db_path=self.db_path):
                    raise LearningAssistantError(
                        f"动作 {proposal.action_type} 不在当前自动执行白名单中"
                    )
            stored_digest = str(entry.get("confirmation_token_sha256") or "")
            supplied_digest = (
                _confirmation_token_digest(confirmation_token)
                if isinstance(confirmation_token, str) and confirmation_token
                else ""
            )
            if not stored_digest or not hmac.compare_digest(stored_digest, supplied_digest):
                raise LearningAssistantError("确认令牌缺失或无效，拒绝执行后台写操作。")
            try:
                self._revalidate_version_token(entry.get("version_token") or {})
            except StaleProposalError as error:
                self._transition(entry, "business_invalid", str(error))
                raise
            try:
                receipt = self._dispatch(proposal, entry)
            except LearningAssistantError:
                raise
            except Exception as error:
                self._transition(entry, "local_commit_failed", f"{type(error).__name__}: {error}")
                raise LearningAssistantError(
                    f"本地提交失败（事务已回滚或未开始）：{error}"
                ) from error
            entry["receipt"] = receipt.to_dict()
            entry["confirmation_consumed_at"] = _now_iso()
            self._transition(entry, receipt.status, receipt.message)
            return receipt

    def _dispatch(self, proposal: ActionProposal, entry: dict[str, Any]) -> ActionReceipt:
        handlers = {
            "submit_homework": self._execute_record_action,
            "update_learning_progress": self._execute_record_action,
            "revise_learning_record": self._execute_revise,
            "handle_alert": self._execute_handle_alert,
            "snooze_alert": self._execute_snooze_alert,
            "recompute_alerts": self._execute_recompute,
        }
        handler = handlers.get(proposal.action_type)
        if handler is None:
            raise LearningAssistantError(
                f"动作 {proposal.action_type} 不能由确认通道执行"
            )
        return handler(proposal, entry)

    def _mark_audit(self, audit_id: int | None, marker_name: str, *args: Any) -> None:
        if not audit_id:
            return
        try:
            import study_app.ai.audit as audit_module

            marker = getattr(audit_module, marker_name)
            marker(_AuditRef(audit_id), *args)
        except Exception:
            pass

    def _execute_record_action(
        self, proposal: ActionProposal, entry: dict[str, Any]
    ) -> ActionReceipt:
        from study_app.data.mastery_replay import import_learning_record_once

        warnings: list[str] = []
        record = self._final_record(
            proposal, entry, warnings, source_text=str(entry.get("source_text") or "")
        )
        try:
            record_id = import_learning_record_once(
                f"la:{proposal.action_id}",
                record,
                db_path=self.db_path,
                model_path=self.model_path,
            )
        except Exception as error:
            self._mark_audit(None, "mark_audit_local_commit_failed")
            raise
        mastery_changes = self._read_back_mastery(record_id, entry)
        alert_result = self._post_write_recompute(warnings)
        sync_result = get_setting(
            "model_progress_sync_last_result", {}, self.db_path
        )
        if isinstance(sync_result, dict) and sync_result.get("status") not in (
            None,
            "ok",
            "success",
            "completed",
        ):
            warnings.append(
                "学习记录已写入，但模型同步状态为："
                f"{sync_result.get('status')}；可在恢复后重放，不会重复贡献。"
            )
        return ActionReceipt(
            action_id=proposal.action_id,
            action_type=proposal.action_type,
            status="completed",
            record_id=record_id,
            mastery_changes=tuple(mastery_changes),
            alert_result=alert_result,
            message=(
                f"已登记学习记录 #{record_id}（幂等键 la:{proposal.action_id}）；"
                "掌握度与预警已按证据重算。"
            ),
            warnings=tuple(warnings),
            executed_at=_now_iso(),
            undo_token={"kind": "record", "record_id": record_id},
            disclosure=dict(proposal.external_data_disclosure),
        )

    def _final_record(
        self,
        proposal: ActionProposal,
        entry: dict[str, Any],
        warnings: list[str],
        source_text: str = "",
    ) -> dict[str, Any]:
        from study_app.core.learning_assistant_advisor import (
            AdvisorUnavailableError,
            analyze_homework,
        )
        from study_app.core.learning_assistant_policy import load_privacy_settings
        from study_app.core.learning_assistant_records import (
            HomeworkFacts,
            ProgressFacts,
            build_record_from_homework,
            build_record_from_progress,
        )

        facts_map = proposal.user_facts
        subject_name = self._subject_name(proposal)
        if not subject_name:
            raise LearningAssistantError("未能解析稳定学科身份，拒绝写入。")
        confirmed_at = date.today().isoformat()
        draft_problems = tuple(
            dict(item) for item in entry.get("draft_problems") or ()
        )
        advisor_report = None
        advisor_suggestions: dict[str, dict] = {}
        if proposal.action_type == "submit_homework" and not facts_map.get("draft_only"):
            privacy = load_privacy_settings(self.db_path)
            mode = privacy.get("advisor_mode", "unset")
            try:
                advisor_report = analyze_homework(
                    draft_problems
                    or (
                        {
                            "title": str(facts_map.get("subject") or "作业"),
                            "statement": str(facts_map.get("note") or ""),
                        },
                    ),
                    mode=mode,
                    action_id=proposal.action_id,
                )
                advisor_suggestions = {
                    item["title"]: item for item in advisor_report.suggestions
                }
                if advisor_report.audit_id:
                    self._mark_audit(advisor_report.audit_id, "mark_audit_awaiting_confirmation")
            except AdvisorUnavailableError as error:
                self._transition(
                    entry, "awaiting_confirmation", f"顾问阻塞：{error}"
                )
                raise AdvisorRequiredError(
                    f"按项目规则，登记已完成作业前必须完成顾问分析；当前不可用：{error}"
                ) from error

        def merged_problems(problems: tuple[dict, ...]) -> tuple[dict, ...]:
            merged = []
            for problem in problems:
                item = dict(problem)
                title = str(item.get("title") or "")
                suggestion = advisor_suggestions.get(title)
                if suggestion:
                    if item.get("difficulty_score") is None and suggestion.get(
                        "difficulty_score"
                    ) is not None:
                        item["difficulty_score"] = suggestion["difficulty_score"]
                    if suggestion.get("knowledge_points") and not item.get(
                        "related_topics"
                    ):
                        item["related_topics"] = list(suggestion["knowledge_points"])
                    if suggestion.get("error_cause"):
                        item["error_cause_suggestion"] = suggestion["error_cause"]
                    if item.get("independence") in (None, "unknown") and suggestion.get(
                        "independence"
                    ) in {"independent", "assisted"}:
                        item["independence"] = suggestion["independence"]
                merged.append(item)
            return tuple(merged)

        if proposal.action_type == "submit_homework":
            facts = HomeworkFacts(
                subject=subject_name,
                completed_date=facts_map.get("completed_date"),
                problems=merged_problems(draft_problems),
                claim_all_correct=facts_map.get("correctness_claim") == "all_correct",
                duration_minutes=facts_map.get("duration_minutes"),
                note=str(facts_map.get("note") or ""),
                draft_only=(
                    bool(proposal.attachments)
                    and facts_map.get("correctness_claim") != "all_correct"
                ),
                independence=str(facts_map.get("independence") or "unknown"),
                attachments=tuple(
                    {
                        "file_path": item.get("path"),
                        "file_name": item.get("file_name"),
                        "sha256": item.get("sha256"),
                    }
                    for item in proposal.attachments
                ),
            )
            record, extra_warnings = build_record_from_homework(
                facts, action_id=proposal.action_id, confirmed_at=confirmed_at
            )
            module_name, topic_name = self._resolve_topic_target(
                subject_name,
                str(source_text or facts_map.get("target") or ""),
            )
            if module_name:
                record["module"] = module_name
            if topic_name:
                record["topic"] = topic_name
        else:
            facts = ProgressFacts(
                subject=subject_name,
                target=str(facts_map.get("target") or ""),
                kind=str(
                    facts_map.get("kind") or facts_map.get("progress_kind") or "reviewed"
                ),
                completed_date=facts_map.get("completed_date"),
                note=str(facts_map.get("note") or ""),
            )
            module_name, topic_name = self._resolve_topic_target(
                subject_name,
                str(facts.target or source_text or ""),
            )
            record, extra_warnings = build_record_from_progress(
                facts,
                action_id=proposal.action_id,
                confirmed_at=confirmed_at,
                module_name=module_name,
                topic_name=topic_name,
            )
        warnings.extend(extra_warnings)
        if advisor_report is not None and advisor_report.audit_id:
            self._mark_audit(advisor_report.audit_id, "mark_audit_completed")
        return record

    def _execute_revise(
        self, proposal: ActionProposal, entry: dict[str, Any]
    ) -> ActionReceipt:
        from study_app.core.learning_assistant_records import build_revision
        from study_app.data.mastery_replay import (
            list_record_revision_history,
            revise_learning_record,
        )

        target_id = None
        changes: dict[str, Any] = {}
        problem_corrections: list[dict[str, Any]] = []
        for change in proposal.proposed_changes:
            if change.get("entity") != "learning_record":
                continue
            target_id = change.get("target_id", target_id)
            changes = change.get("fields", {}) or {}
            corrections = change.get("problems")
            if isinstance(corrections, list):
                problem_corrections.extend(
                    item for item in corrections if isinstance(item, dict)
                )
        if not target_id:
            target_id = proposal.user_facts.get("target_record_id")
        if not target_id:
            raise LearningAssistantError("修订提案缺少目标记录 id")
        record_id = int(target_id)
        from study_app.data.database import connect_readonly

        with connect_readonly(self.db_path) as connection:
            row = connection.execute(
                "SELECT raw_json FROM learning_records WHERE id=?",
                (record_id,),
            ).fetchone()
        if row is None:
            raise LearningAssistantError(f"目标记录 #{record_id} 不存在")
        original = json.loads(row["raw_json"])
        original["id"] = record_id

        # 题目级修订：先在原题列表副本上定位目标题，再整体替换。
        problems = [dict(item) for item in (original.get("problems") or [])]
        for correction in problem_corrections:
            target = None
            idx = correction.get("index")
            hint = correction.get("title_hint")
            if idx is not None and 1 <= int(idx) <= len(problems):
                target = problems[int(idx) - 1]
            elif hint:
                for problem in problems:
                    if hint in str(problem.get("title") or ""):
                        target = problem
                        break
            if target is None:
                raise LearningAssistantError(
                    f"修订目标题目未找到：index={idx} hint={hint}"
                )
            target.update(correction.get("set") or {})

        revision_changes: dict[str, Any] = {"fields": changes}
        if problem_corrections:
            # build_revision 只认完整替换列表：仅在确实存在题目级更正时传入。
            revision_changes["problems"] = problems

        mastery_before = self._model_topic_snapshot()
        replacement, revision_warnings = build_revision(
            original, revision_changes, record_id=record_id
        )
        revise_learning_record(
            record_id,
            replacement,
            db_path=self.db_path,
            model_path=self.model_path,
            actor=f"la:{proposal.action_id[:48]}",
        )
        mastery_changes = self._mastery_delta(mastery_before)
        warnings = list(revision_warnings)
        alert_result = self._post_write_recompute(warnings)
        try:
            history_length = len(
                list_record_revision_history(record_id, db_path=self.db_path)
            )
        except Exception:
            history_length = 0
        return ActionReceipt(
            action_id=proposal.action_id,
            action_type=proposal.action_type,
            status="completed",
            record_id=record_id,
            mastery_changes=mastery_changes,
            alert_result=alert_result,
            message=(
                f"记录 #{record_id} 已追加式修订（原版本保留，可追溯）；"
                f"当前版本历史 {history_length} 条。"
            ),
            warnings=tuple(warnings),
            executed_at=_now_iso(),
            undo_token=None,
            disclosure=dict(proposal.external_data_disclosure),
        )

    def _execute_handle_alert(
        self, proposal: ActionProposal, entry: dict[str, Any]
    ) -> ActionReceipt:
        return self._alert_state_action(proposal, "handle")

    def _execute_snooze_alert(
        self, proposal: ActionProposal, entry: dict[str, Any]
    ) -> ActionReceipt:
        return self._alert_state_action(proposal, "snooze")

    def _alert_state_action(
        self, proposal: ActionProposal, kind: str
    ) -> ActionReceipt:
        from study_app.core.knowledge_alerts import handle_alert, snooze_alert

        alert_id = int(proposal.user_facts.get("alert_id") or 0)
        if alert_id <= 0:
            raise LearningAssistantError("提案缺少有效 alert_id")
        target_date_text = proposal.user_facts.get("snoozed_until") or proposal.user_facts.get(
            "effective_date"
        )
        if not target_date_text:
            target_date_text = date.today().isoformat()
        target_date = date.fromisoformat(str(target_date_text))
        actor = f"la:{proposal.action_id}"[:64]
        try:
            with connect(self.db_path) as connection:
                if kind == "handle":
                    handle_alert(alert_id, target_date, connection, actor=actor)
                else:
                    snooze_alert(
                        alert_id,
                        target_date,
                        date.today(),
                        connection,
                        actor=actor,
                    )
        except ValueError as error:
            current = self._alert_status(alert_id)
            already = (
                current == "handled"
                if kind == "handle"
                else current == "snoozed"
                and self._alert_snoozed_until(alert_id) == target_date.isoformat()
            )
            if already:
                return ActionReceipt(
                    action_id=proposal.action_id,
                    action_type=proposal.action_type,
                    status="completed",
                    alert_id=alert_id,
                    message="预警已处于目标状态（幂等重放，无重复事件）。",
                    executed_at=_now_iso(),
                    undo_token=None,
                    disclosure=dict(proposal.external_data_disclosure),
                )
            raise LearningAssistantError(f"预警状态变更被拒绝：{error}") from error
        label = "已处理" if kind == "handle" else f"已延后至 {target_date.isoformat()}"
        return ActionReceipt(
            action_id=proposal.action_id,
            action_type=proposal.action_type,
            status="completed",
            alert_id=alert_id,
            message=f"预警 #{alert_id} {label}；这不代表知识点已掌握，原始证据未修改。",
            executed_at=_now_iso(),
            undo_token=None,
            disclosure=dict(proposal.external_data_disclosure),
        )

    def _alert_status(self, alert_id: int) -> str | None:
        try:
            from study_app.data.database import list_knowledge_alerts

            for alert in list_knowledge_alerts(db_path=self.db_path):
                if alert.id == alert_id:
                    return alert.status
        except Exception:
            return None
        return None

    def _alert_snoozed_until(self, alert_id: int) -> str | None:
        try:
            from study_app.data.database import list_knowledge_alerts

            for alert in list_knowledge_alerts(db_path=self.db_path):
                if alert.id == alert_id:
                    return (
                        alert.snoozed_until.isoformat()
                        if alert.snoozed_until is not None
                        else None
                    )
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # Alert recompute
    # ------------------------------------------------------------------
    def _classified_topics(self, as_of: date):
        from study_app.core.computation_context import DashboardComputationContext
        from study_app.core.knowledge_states import classify_topics
        from study_app.core.topic_insights import build_topic_insights
        from study_app.data.database import (
            list_prerequisite_edges,
            list_topic_identities,
            load_raw_records,
        )

        identities = list_topic_identities(self.db_path)
        edges = list_prerequisite_edges(self.db_path)
        model = json.loads(self.model_path.read_text(encoding="utf-8"))
        records = load_raw_records(self.db_path)
        context = DashboardComputationContext(
            model, list(records), as_of, model.get("warning_policy", {})
        )
        insights = build_topic_insights(
            model, list(records), as_of, context, identities, edges
        )
        return classify_topics(insights, include_archived=False)

    def _execute_recompute(
        self, proposal: ActionProposal | None, entry: dict[str, Any] | None = None
    ) -> ActionReceipt:
        as_of = date.today()
        action_id = proposal.action_id if proposal is not None else new_action_id()
        warnings: list[str] = []
        alert_result = self._recompute(as_of, warnings)
        return ActionReceipt(
            action_id=action_id,
            action_type="recompute_alerts",
            status="completed",
            alert_result=alert_result,
            message=(
                "已按当前证据幂等重算知识预警："
                f"新建 {alert_result.get('created', 0)}，"
                f"复活 {alert_result.get('reactivated', 0)}，"
                f"解除 {alert_result.get('resolved', 0)}，"
                f"保持 {alert_result.get('unchanged', 0)}。"
            ),
            warnings=tuple(warnings),
            executed_at=_now_iso(),
            undo_token={"kind": "recompute"},
            disclosure=default_disclosure(),
        )

    def _recompute(self, as_of: date, warnings: list[str]) -> dict[str, Any]:
        from study_app.core.knowledge_alerts import reconcile_knowledge_alerts

        try:
            classified = self._classified_topics(as_of)
            with connect(self.db_path) as connection:
                result = reconcile_knowledge_alerts(classified, as_of, connection)
            return {
                "created": len(result.created_ids),
                "reactivated": len(result.reactivated_ids),
                "resolved": len(result.resolved_ids),
                "unchanged": len(result.unchanged_ids),
            }
        except DatabaseNotInitializedError as error:
            warnings.append(f"知识预警结构未安装，已跳过重算：{error}")
            return {"skipped": "f2_missing"}
        except Exception as error:
            warnings.append(f"预警重算失败：{type(error).__name__}: {error}")
            return {"skipped": "error"}

    def _post_write_recompute(self, warnings: list[str]) -> dict[str, Any] | None:
        """A18: one idempotent alert recompute right after evidence writes."""
        result = self._recompute(date.today(), warnings)
        return result if result else None

    def recompute_alerts_now(self, as_of: date | None = None) -> ActionReceipt:
        with self._lock:
            action_id = new_action_id()
            proposal = ActionProposal(
                schema_version=SCHEMA_VERSION,
                action_id=action_id,
                action_type="recompute_alerts",
                mode="confirmed",
                requires_confirmation=False,
            )
            entry = {
                "action_id": action_id,
                "created_at": _now_iso(),
                "state": "validated",
                "proposal": proposal.to_dict(),
                "history": [],
                "receipt": None,
            }
            receipt = self._execute_recompute(proposal, entry)
            entry["receipt"] = receipt.to_dict()
            self._transition(entry, receipt.status, receipt.message)
            self._register_index(action_id)
            return receipt

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------
    def last_undoable(self) -> dict[str, Any] | None:
        index = get_setting(JOURNAL_INDEX_KEY, [], self.db_path)
        if not isinstance(index, list):
            return None
        for action_id in reversed(index):
            entry = self._load_entry(str(action_id))
            if not entry or entry.get("state") != "completed":
                continue
            receipt = entry.get("receipt") or {}
            token = receipt.get("undo_token")
            if token:
                return {
                    "action_id": entry.get("action_id"),
                    "action_type": (entry.get("proposal") or {}).get("action_type"),
                    "undo_token": token,
                    "message": receipt.get("message", ""),
                }
        return None

    def undo_last(self) -> ActionReceipt:
        from study_app.data.mastery_replay import revoke_learning_record

        with self._lock:
            target = self.last_undoable()
            if not target:
                raise ActionNotUndoableError("没有可撤销的动作。")
            token = target["undo_token"]
            warnings: list[str] = []
            message = ""
            if token.get("kind") == "record":
                try:
                    revoke_learning_record(
                        int(token["record_id"]),
                        db_path=self.db_path,
                        model_path=self.model_path,
                        actor="learning_assistant_undo",
                    )
                except Exception as error:
                    from study_app.data.mastery_replay import ReplayProvenanceError

                    if isinstance(error, ReplayProvenanceError):
                        raise ActionNotUndoableError(
                            "该记录没有掌握度贡献痕迹（trace 缺失），系统拒绝无证据重算；"
                            "请改用『修订学习记录』流程修正。"
                        ) from error
                    raise
                alert_result = self._post_write_recompute(warnings)
                message = (
                    f"已撤销记录 #{token['record_id']}：记录标记 revoked（历史保留），"
                    "掌握度/计划/预警已按基线重放。"
                )
                alert_id = None
            elif token.get("kind") == "recompute":
                alert_result = self._recompute(date.today(), warnings)
                message = "已再次幂等重算预警（撤销语义：回到与当前证据一致的状态）。"
                alert_id = None
            else:
                raise ActionNotUndoableError(f"未知的撤销类型：{token}")
            action_id = new_action_id()
            receipt = ActionReceipt(
                action_id=action_id,
                action_type="undo_last_action",
                status="completed",
                record_id=int(token["record_id"]) if token.get("kind") == "record" else None,
                alert_id=alert_id,
                alert_result=alert_result,
                message=message,
                warnings=tuple(warnings),
                executed_at=_now_iso(),
                undo_token=None,
                disclosure=default_disclosure(),
            )
            original = self._load_entry(str(target["action_id"]))
            if original:
                self._transition(original, "undone", f"被 {action_id} 撤销")
            entry = {
                "action_id": action_id,
                "created_at": _now_iso(),
                "state": "validated",
                "proposal": {
                    "schema_version": SCHEMA_VERSION,
                    "action_id": action_id,
                    "action_type": "undo_last_action",
                    "mode": "confirmed",
                    "requires_confirmation": False,
                },
                "history": [],
                "receipt": receipt.to_dict(),
            }
            self._transition(entry, "completed", message)
            self._register_index(action_id)
            return receipt

    def reject_proposal(self, action_id: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            entry = self._load_entry(action_id)
            if not entry:
                raise LearningAssistantError(f"未知动作：{action_id}")
            if entry.get("state") == "completed":
                raise LearningAssistantError("动作已执行，不能拒绝。")
            self._transition(entry, "rejected", reason or "用户取消提案")
            return {"action_id": action_id, "state": "rejected"}

    def retry_proposal(self, action_id: str) -> PreviewBundle:
        entry = self._load_entry(action_id)
        if not entry:
            raise LearningAssistantError(f"未知动作：{action_id}")
        proposal_raw = entry.get("proposal") or {}
        return self.create_proposal(
            str(proposal_raw.get("user_facts", {}).get("source_text") or ""),
            [],
        )

    # ------------------------------------------------------------------
    # Read-only paths
    # ------------------------------------------------------------------
    def answer(self, question: str, state: Any = None) -> dict[str, Any]:
        from study_app.ai.natural_query import answer_query_locally
        from study_app.core.dashboard import load_dashboard_state
        from study_app.data.database import load_raw_records

        current = state
        if current is None:
            try:
                current = load_dashboard_state()
            except Exception:
                current = None
        records = list(getattr(current, "raw_records", ()) or []) if current is not None else []
        if current is None:
            try:
                records = load_raw_records(self.db_path)
            except Exception:
                records = []
        if current is None:
            raise LearningAssistantError("本地数据暂不可用，无法回答。")
        return answer_query_locally(question, current, records)

    def explain_alert(self, alert_id: int, state: Any = None) -> dict[str, Any]:
        try:
            from study_app.data.database import (
                list_knowledge_alert_events,
                list_knowledge_alerts,
            )

            alerts = list_knowledge_alerts(db_path=self.db_path)
            alert = next((item for item in alerts if item.id == int(alert_id)), None)
            if alert is None:
                return {"available": False, "reason": f"预警 #{alert_id} 不存在"}
            events = list_knowledge_alert_events(int(alert_id), self.db_path)
        except DatabaseNotInitializedError as error:
            return {"available": False, "reason": f"知识预警结构未安装：{error}"}
        snapshot = alert.snapshot or {}
        classification = snapshot.get("classification", {}) or {}
        evidence = snapshot.get("evidence", {}) or {}
        topic = snapshot.get("topic", {}) or {}
        memory = snapshot.get("memory", {}) or {}
        lines = [
            f"预警 #{alert.id}（{alert.alert_type}）状态：{alert.status}",
            f"主题：{topic.get('subject_name', '')} / {topic.get('module_name', '')} / {topic.get('topic_name', '')}",
            f"触发规则版本：{alert.rule_version}；条件周期：{alert.condition_cycle}",
            f"触发原因码：{'、'.join(classification.get('reason_codes', []) or [])}",
            f"建议动作：{classification.get('recommended_action', '未知')}",
            f"证据数量：观察 {evidence.get('observation_count', 0)} 条，练习 {evidence.get('exercise_count', 0)} 条，"
            f"置信度 {evidence.get('confidence', 0):.0%}",
            f"最近证据：{json.dumps(evidence.get('recent_error'), ensure_ascii=False) if evidence.get('recent_error') else '无可证明的最近错误'}",
            f"记忆召回概率：{memory.get('recall_probability', 0):.0%}（目标 {memory.get('target_recall', 0):.0%}）",
            "事件历史：" + "；".join(
                f"{event.created_at} {event.from_status or '∅'}→{event.to_status}（{event.actor}）"
                for event in events
            ),
            "说明：处理或延后预警不代表知识点已掌握，也不会修改任何原始学习证据。",
        ]
        return {"available": True, "lines": lines, "alert_id": alert.id}

    def f2_available(self) -> bool:
        try:
            from study_app.data.database import list_topic_identities

            list_topic_identities(self.db_path)
            return True
        except DatabaseNotInitializedError:
            return False
        except Exception:
            return False

    def _model_topic_snapshot(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        """{(subject, module, topic): {"mastery":…, "status":…}} 只读快照。"""
        try:
            model = json.loads(self.model_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        snapshot: dict[tuple[str, str, str], dict[str, Any]] = {}
        for subject in model.get("subjects", []):
            for module in subject.get("modules", []):
                for topic in module.get("topics", []):
                    key = (
                        str(subject.get("name") or ""),
                        str(module.get("name") or ""),
                        str(topic.get("name") or ""),
                    )
                    snapshot[key] = {
                        "mastery": topic.get("mastery"),
                        "status": topic.get("status"),
                    }
        return snapshot

    def _mastery_delta(
        self, before: dict[tuple[str, str, str], dict[str, Any]]
    ) -> tuple[dict[str, Any], ...]:
        """修订前后模型知识点的掌握度差异（写入回执供用户核对）。"""
        after_snapshot = self._model_topic_snapshot()
        changes: list[dict[str, Any]] = []
        for key, after in after_snapshot.items():
            before_item = before.get(key)
            if before_item is None or before_item == after:
                continue
            changes.append(
                {
                    "subject": key[0],
                    "module": key[1],
                    "topic": key[2],
                    "mastery_before": before_item.get("mastery"),
                    "mastery_after": after.get("mastery"),
                    "status_before": before_item.get("status"),
                    "status_after": after.get("status"),
                }
            )
        return tuple(changes)

    def _read_back_mastery(
        self, record_id: int, entry: dict[str, Any]
    ) -> list[dict[str, Any]]:
        preview = entry.get("mastery_preview") or {}
        matched = preview.get("matched_topics") or []
        if not matched:
            return []
        try:
            model = json.loads(self.model_path.read_text(encoding="utf-8"))
        except Exception:
            return [dict(item) for item in matched]
        index = {}
        for subject in model.get("subjects", []):
            for module in subject.get("modules", []):
                for topic in module.get("topics", []):
                    index[
                        (
                            subject.get("name"),
                            module.get("name"),
                            topic.get("name"),
                        )
                    ] = topic
        refreshed = []
        for item in matched:
            topic = index.get(
                (
                    item.get("subject"),
                    item.get("module"),
                    item.get("topic"),
                )
            )
            view = dict(item)
            if topic is not None:
                view["mastery_after"] = topic.get("mastery")
                view["status_after"] = topic.get("status")
            refreshed.append(view)
        return refreshed
