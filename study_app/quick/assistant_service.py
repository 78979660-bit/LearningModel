"""Qt-independent adapter for local learning Q&A and assistant actions.

The service owns engine confirmation and undo capabilities. Its public DTOs
contain opaque, process-local handles only; QML never receives the engine's
confirmation secret or undo token. Constructing the service only stores paths
and options. Database, model, attachment, provider, and network work happens
inside an explicit method call.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any


_TOKEN_TTL_SECONDS = 30 * 60
_MAX_TOKENS = 48
_PRIVATE_KEYS = frozenset(
    {
        "api_key",
        "api_key_protected",
        "confirmation_token",
        "confirmation_token_sha256",
        "undo_token",
        "db_path",
        "model_path",
        "records_path",
        "path",
        "source_text",
        "attachment_paths",
        "file_paths",
    }
)


class AssistantService:
    """Path-injected pure-Python facade for the existing learning assistant."""

    def __init__(
        self,
        db_path: str | Path,
        model_path: str | Path,
        records_path: str | Path,
        today: date | str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        if isinstance(today, str):
            today = date.fromisoformat(today)
        if isinstance(today, datetime) or (today is not None and not isinstance(today, date)):
            raise TypeError("today 必须是 datetime.date、ISO 日期字符串或 None")
        self.today = today
        self._lock = threading.RLock()
        # token -> private operation data. Never serialize this mapping.
        self._tokens: dict[str, dict[str, Any]] = {}

    def _engine(self):
        from study_app.core.learning_assistant_engine import LearningAssistantEngine

        return LearningAssistantEngine(
            db_path=self.db_path,
            model_path=self.model_path,
            records_path=self.records_path,
            today=self.today,
        )

    def _issue_token(self, entry: dict[str, Any]) -> str:
        now = time.monotonic()
        with self._lock:
            expired = [
                token
                for token, item in self._tokens.items()
                if now - item["created_at"] > _TOKEN_TTL_SECONDS
            ]
            for token in expired:
                self._tokens.pop(token, None)
            while len(self._tokens) >= _MAX_TOKENS:
                oldest = min(self._tokens, key=lambda token: self._tokens[token]["created_at"])
                self._tokens.pop(oldest, None)
            token = secrets.token_urlsafe(24)
            self._tokens[token] = {**entry, "created_at": now}
            return token

    def _take_token(self, token: str, kind: str) -> dict[str, Any] | None:
        if not isinstance(token, str) or not token:
            return None
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None or entry.get("kind") != kind:
                return None
            self._tokens.pop(token, None)
            if time.monotonic() - entry["created_at"] > _TOKEN_TTL_SECONDS:
                return None
            return entry

    def load(self) -> dict[str, Any]:
        """Return a compact journal, current policy, privacy settings, and undo state."""
        from study_app.ai.providers import load_llm_settings
        from study_app.core.learning_assistant_policy import (
            load_auto_exec_policy,
            load_privacy_settings,
        )
        from study_app.core.learning_assistant_schema import ACTION_LABELS

        engine = self._engine()
        policy = dict(load_auto_exec_policy(self.db_path))
        privacy = dict(load_privacy_settings(self.db_path))
        llm_settings = load_llm_settings(db_path=self.db_path)
        privacy["llm"] = {
            "enabled": bool(llm_settings.is_cloud_enabled),
            "provider": str(llm_settings.provider),
            "model": self._safe_model_reference(llm_settings.model),
            "natural_query_enabled": bool(
                llm_settings.is_cloud_enabled
                and "natural_query" in llm_settings.enabled_features
            ),
            "credential_configured": bool(llm_settings.api_key),
            "image_upload_enabled": bool(llm_settings.allow_upload_images),
            "pdf_upload_enabled": bool(llm_settings.allow_upload_pdfs),
        }
        target = engine.last_undoable()
        undo = self._safe_undo_review(target)
        return {
            "journal": self._json_safe(engine.load_journal()),
            "policy": self._json_safe(policy),
            "privacy": self._json_safe(privacy),
            "undo": undo,
            "policyActions": [
                {
                    "key": action,
                    "label": ACTION_LABELS.get(action, action),
                    "enabled": bool(policy.get(action, False)),
                }
                for action in sorted(policy)
            ],
        }

    def ask(
        self,
        question: str,
        attachments: list[str] | tuple[str, ...],
        external: bool = False,
    ) -> dict[str, Any]:
        """Answer from the injected local snapshot; external LLM use is opt-in.

        Attachments are accepted for a single entry point with the UI but are
        never read or transmitted by Q&A. The action preview method owns local
        attachment parsing.
        """
        from study_app.ai import natural_query, providers
        from study_app.ai.validation import LLMValidationError
        from study_app.core.dashboard import load_dashboard_state

        text = str(question or "").strip()
        if not text:
            return {
                "answer": {
                    "answer": ["请输入问题或学习请求。"],
                    "evidence": [],
                    "source": ["生成方式：本地查询。"],
                },
                "external_requested": bool(external),
                "external_used": False,
                "external_error": "",
            }
        state = load_dashboard_state(
            model_path=self.model_path,
            records_path=self.records_path,
            today=self.today,
            db_path=self.db_path,
        )
        records = list(getattr(state, "raw_records", ()) or ())
        local_answer = natural_query.answer_query_locally(text, state, records)
        result = {
            "answer": self._json_safe(local_answer),
            "external_requested": bool(external),
            "external_used": False,
            "external_error": "",
        }
        if not external:
            return result
        try:
            enabled = providers.is_llm_feature_enabled(
                "natural_query", db_path=self.db_path
            )
        except Exception:
            result["external_error"] = "外部查询设置暂不可用，已保留本地回答。"
            return result
        if not enabled:
            result["external_error"] = "外部自然语言查询未启用，已使用本地回答。"
            return result
        try:
            llm_answer = natural_query.answer_query_with_llm(
                text, state, records, db_path=self.db_path
            )
        except LLMValidationError:
            result["external_error"] = "外部模型已返回，但回答格式不符合要求，暂用本地回答。"
            return result
        except Exception:
            # Provider exceptions can include endpoint details or credential
            # diagnostics. The public DTO reports only the safe fallback.
            result["external_error"] = "外部查询暂不可用，已保留本地回答。"
            return result
        result["answer"] = self._json_safe(llm_answer)
        result["external_used"] = True
        return result

    def preview(self, text: str, paths: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Create a journaled assistant proposal and a private one-time handle."""
        try:
            bundle = self._engine().create_proposal(
                str(text or ""), self._normalize_paths(paths)
            )
        except Exception as error:
            return self._invalid_preview(error)
        return self._preview_dto(bundle)

    def confirm(
        self,
        token: str,
        execution_mode: str = "user",
    ) -> dict[str, Any]:
        """Execute one exact preview; engine tokens remain private to this service."""
        entry = self._take_token(token, "preview")
        if entry is None:
            return self._failure_receipt(
                "preview_expired", "提案确认信息已失效，请重新预览。"
            )
        if not entry.get("confirmation_allowed"):
            return self._failure_receipt(
                "readonly_action", "这是只读问答，不需要也不能确认写入。"
            )
        if execution_mode not in {"user", "auto"}:
            return self._failure_receipt("invalid_mode", "执行方式无效。")
        engine = self._engine()
        action_id = entry["action_id"]
        try:
            receipt = engine.confirm_and_execute(
                action_id,
                confirmation_token=entry.get("confirmation_secret"),
                execution_mode=execution_mode,
            )
        except Exception as error:
            committed = self._recover_confirm_commit(engine, action_id, entry, error)
            if committed is not None:
                return committed
            return self._failure_receipt(
                self._error_code(error), self._safe_failure_message(error), action_id=action_id
            )
        return self._receipt_dto(receipt, saved=receipt.status == "completed")

    def reject(self, token: str) -> dict[str, Any]:
        """Reject one pending preview without exposing its engine action secret."""
        entry = self._take_token(token, "preview")
        if entry is None:
            return {
                "saved": False,
                "rejected": False,
                "status": "expired",
                "actionId": "",
            }
        try:
            result = self._engine().reject_proposal(entry["action_id"])
        except Exception as error:
            try:
                stored = self._engine().load_action(entry["action_id"])
            except Exception:
                stored = None
            if isinstance(stored, dict) and stored.get("state") == "rejected":
                return {
                    "saved": True,
                    "rejected": True,
                    "status": "rejected",
                    "actionId": entry["action_id"],
                    "refreshWarning": True,
                    "refreshWarningText": "拒绝已保存，但历史回读失败。",
                    "errorCode": self._error_code(error),
                }
            return {
                "saved": False,
                "rejected": False,
                "status": self._error_code(error),
                "actionId": entry["action_id"],
                "message": self._safe_failure_message(error),
            }
        return {
            "saved": result.get("state") == "rejected",
            "rejected": result.get("state") == "rejected",
            "status": result.get("state", "rejected"),
            "actionId": entry["action_id"],
        }

    def retry(self, action_id: str) -> dict[str, Any]:
        """Rebuild a failed/stale action from its journaled source text."""
        if not isinstance(action_id, str) or not action_id:
            return self._invalid_preview(ValueError("invalid action id"))
        try:
            bundle = self._engine().retry_proposal(action_id)
        except Exception as error:
            return self._invalid_preview(error)
        with self._lock:
            for token, entry in list(self._tokens.items()):
                if entry.get("action_id") == action_id:
                    self._tokens.pop(token, None)
        return self._preview_dto(bundle)

    def undo_preview(self) -> dict[str, Any]:
        """Return a review and one-time handle for the current undoable action."""
        target = self._engine().last_undoable()
        review = self._safe_undo_review(target)
        if not target:
            return {"available": False, "token": None, "review": review}
        token = self._issue_token(
            {
                "kind": "undo",
                "action_id": str(target.get("action_id") or ""),
                "undo_fingerprint": self._token_digest(target.get("undo_token")),
                "undo_state": self._json_safe(target.get("undo_token")),
                "action_type": target.get("action_type"),
            }
        )
        return {"available": True, "token": token, "review": review}

    def undo(self, token: str) -> dict[str, Any]:
        """Execute undo only if the reviewed action remains the latest undoable."""
        entry = self._take_token(token, "undo")
        if entry is None:
            return self._failure_receipt(
                "undo_expired", "撤销确认信息已失效，请重新检查可撤销操作。"
            )
        engine = self._engine()
        current = engine.last_undoable()
        if (
            not current
            or current.get("action_id") != entry.get("action_id")
            or self._token_digest(current.get("undo_token"))
            != entry.get("undo_fingerprint")
        ):
            return self._failure_receipt(
                "undo_stale", "可撤销操作已变化，请重新检查后再试。",
                action_id=entry.get("action_id", ""),
            )
        from study_app.core.learning_assistant_schema import new_action_id

        undo_action_id = new_action_id()
        try:
            receipt = engine.undo_last(action_id=undo_action_id)
        except Exception as error:
            if self._undo_commit_evidence(entry, undo_action_id):
                return {
                    "saved": True,
                    "status": "completed",
                    "actionId": undo_action_id,
                    "actionType": "undo_last_action",
                    "message": "撤销已提交；回执更新未完成，请重新加载历史操作。",
                    "warnings": [],
                    "refreshWarning": True,
                    "refreshWarningText": "撤销已保存，后续回执更新失败。",
                    "errorCode": self._error_code(error),
                }
            return self._failure_receipt(
                self._error_code(error), self._safe_failure_message(error),
                action_id=entry.get("action_id", ""),
            )
        return self._receipt_dto(receipt, saved=receipt.status == "completed")

    def policy(self, action: str, enabled: bool) -> dict[str, Any]:
        """Save one action's current auto-execution setting."""
        from study_app.core.learning_assistant_policy import (
            load_auto_exec_policy,
            save_auto_exec_policy,
        )

        if not isinstance(action, str) or not isinstance(enabled, bool):
            return {"saved": False, "message": "策略参数无效。"}
        try:
            current = dict(load_auto_exec_policy(self.db_path))
            if action not in current:
                return {"saved": False, "message": "此动作不在自动执行白名单内。"}
            current[action] = enabled
            saved_policy = save_auto_exec_policy(current, self.db_path)
        except Exception:
            # A write may have committed before a later adapter error. Verify
            # the requested value against the persisted policy before reporting.
            try:
                saved_policy = dict(load_auto_exec_policy(self.db_path))
            except Exception:
                saved_policy = {}
            if saved_policy.get(action) is enabled:
                return {
                    "saved": True,
                    "action": action,
                    "enabled": enabled,
                    "policy": self._json_safe(saved_policy),
                    "refreshWarning": True,
                    "refreshWarningText": "策略已保存，但回读失败，请重新加载。",
                }
            return {"saved": False, "message": "策略保存失败，请稍后重试。"}
        return {
            "saved": True,
            "action": action,
            "enabled": bool(saved_policy.get(action, False)),
            "policy": self._json_safe(saved_policy),
            "refreshWarning": False,
            "refreshWarningText": "",
        }

    def _preview_dto(self, bundle) -> dict[str, Any]:
        from study_app.core.learning_assistant_schema import (
            ACTION_LABELS,
            AUTO_ELIGIBLE_ACTIONS,
        )

        proposal = bundle.proposal
        public_proposal = self._public_proposal(proposal.to_dict())
        confirmation_allowed = not proposal.is_readonly
        auto_eligible = proposal.action_type in AUTO_ELIGIBLE_ACTIONS
        preview_warnings = list(bundle.warnings or ())
        try:
            auto_enabled = bool(
                auto_eligible and self._engine().can_auto_execute(proposal)
            )
        except Exception:
            # A policy read failure must never turn an otherwise valid preview
            # into an orphaned confirmation secret or an automatic execution.
            auto_enabled = False
            preview_warnings.append("自动执行策略暂不可用；此提案只能手动确认。")
        token = self._issue_token(
            {
                "kind": "preview",
                "action_id": proposal.action_id,
                "action_type": proposal.action_type,
                "confirmation_secret": bundle.confirmation_token,
                "confirmation_allowed": confirmation_allowed,
                "user_facts": self._json_safe(proposal.user_facts or {}),
            }
        )
        disclosure = self._json_safe(proposal.external_data_disclosure or {})
        impact = self._json_safe(
            {
                "mastery": bundle.mastery_preview or {},
                "plan": bundle.plan_impact or {},
            }
        )
        warnings = self._json_safe(preview_warnings)
        label = ACTION_LABELS.get(proposal.action_type, proposal.action_type)
        summary_lines = [f"动作：{label}", f"编号：{proposal.action_id}"]
        facts = proposal.user_facts or {}
        subject = facts.get("subject") or facts.get("subject_name")
        if subject:
            summary_lines.append(f"学科：{subject}")
        for change in proposal.proposed_changes or ():
            if isinstance(change, dict) and change.get("summary"):
                summary_lines.append(f"变更：{change['summary']}")
        if disclosure.get("will_contact_external"):
            fields = "、".join(str(item) for item in disclosure.get("fields_sent", ()))
            summary_lines.append(
                "外发：" + str(disclosure.get("provider") or "外部顾问")
                + (f"（{fields}）" if fields else "")
            )
        else:
            summary_lines.append("外发：无")
        summary_lines.extend(f"⚠ {item}" for item in warnings)
        if confirmation_allowed:
            summary_lines.append("确认执行前不会写入业务数据。")
        else:
            summary_lines.append("只读提案：不会写入业务数据。")
        return {
            "token": token,
            "summary": "\n".join(summary_lines),
            "disclosure": disclosure,
            "warnings": warnings,
            "impact": impact,
            "proposal": public_proposal,
            "confirmationAllowed": confirmation_allowed,
            "autoEligible": auto_eligible,
            "autoEnabled": auto_enabled,
        }

    def _invalid_preview(self, error: Exception) -> dict[str, Any]:
        return {
            "token": None,
            "summary": "无法生成可确认的提案，请补充清楚的学习请求后重试。",
            "disclosure": {"will_contact_external": False, "fields_sent": []},
            "warnings": ["本次未生成有效提案；没有执行任何写入。"],
            "impact": {},
            "proposal": {
                "action_id": "",
                "action_type": "invalid",
                "requires_confirmation": True,
                "warnings": [],
            },
            "confirmationAllowed": False,
            "autoEligible": False,
            "autoEnabled": False,
            "error": "无法生成有效提案，请补充清楚的学习请求后重试。",
            "errorCode": self._error_code(error),
        }

    @classmethod
    def _public_proposal(cls, raw: dict[str, Any]) -> dict[str, Any]:
        result = cls._json_safe(raw)
        facts = result.get("user_facts")
        if isinstance(facts, dict):
            result["user_facts"] = {
                key: value for key, value in facts.items()
                if key.lower() not in _PRIVATE_KEYS
            }
        result["attachments"] = [
            {
                key: value
                for key, value in attachment.items()
                if key in {"file_name", "kind", "preflight_status", "size_bytes", "external_allowed"}
            }
            for attachment in result.get("attachments", [])
            if isinstance(attachment, dict)
        ]
        return result

    @classmethod
    def _safe_undo_review(cls, target: dict[str, Any] | None) -> dict[str, Any]:
        if not target:
            return {"available": False, "action_id": "", "action_type": "", "message": ""}
        from study_app.core.learning_assistant_schema import ACTION_LABELS

        action_type = str(target.get("action_type") or "")
        return {
            "available": True,
            "action_id": str(target.get("action_id") or ""),
            "action_type": action_type,
            "label": ACTION_LABELS.get(action_type, action_type),
            "message": str(target.get("message") or ""),
        }

    @staticmethod
    def _normalize_paths(paths: list[str] | tuple[str, ...] | str | None) -> list[str]:
        if paths is None:
            return []
        if isinstance(paths, (str, Path)):
            values = [paths]
        else:
            values = list(paths)
        return [str(path) for path in values if isinstance(path, (str, Path)) and str(path)]

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): cls._json_safe(item)
                for key, item in value.items()
                if str(key).lower() not in _PRIVATE_KEYS
            }
        if isinstance(value, (tuple, list)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Path):
            return value.name
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @classmethod
    def _token_digest(cls, value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _recover_confirm_commit(
        self,
        engine,
        action_id: str,
        token_entry: dict[str, Any],
        error: Exception,
    ) -> dict[str, Any] | None:
        """Treat proven post-commit failures as saved and ask the UI to refresh."""
        try:
            entry = engine.load_action(action_id)
        except Exception:
            entry = None
        receipt_raw = entry.get("receipt") if isinstance(entry, dict) else None
        if isinstance(receipt_raw, dict) and receipt_raw.get("status") == "completed":
            from study_app.core.learning_assistant_engine import ActionReceipt

            return self._receipt_dto(ActionReceipt.from_dict(receipt_raw), saved=True, warning=error)
        if isinstance(entry, dict) and entry.get("state") == "completed":
            receipt_raw = entry.get("receipt") or {}
            return self._recovered_receipt(action_id, token_entry, receipt_raw, error)
        evidence = self._confirm_commit_evidence(action_id, token_entry)
        if evidence is None:
            return None
        return self._recovered_receipt(action_id, token_entry, evidence, error)

    def _confirm_commit_evidence(
        self, action_id: str, token_entry: dict[str, Any]
    ) -> dict[str, Any] | None:
        action_type = token_entry.get("action_type")
        try:
            from study_app.data.db_runtime import connect_readonly

            with connect_readonly(self.db_path) as connection:
                if action_type in {"submit_homework", "update_learning_progress"}:
                    row = connection.execute(
                        "SELECT record_id FROM learning_record_revisions WHERE source_key = ?",
                        (f"la:{action_id}",),
                    ).fetchone()
                    if row is not None:
                        return {"record_id": int(row[0]), "action_type": action_type}
                if action_type == "revise_learning_record":
                    actor = f"la:{action_id[:48]}"
                    row = connection.execute(
                        "SELECT record_id FROM learning_record_revisions "
                        "WHERE actor = ? AND action = 'revised' ORDER BY id DESC LIMIT 1",
                        (actor,),
                    ).fetchone()
                    if row is not None:
                        return {"record_id": int(row[0]), "action_type": action_type}
            if action_type in {"handle_alert", "snooze_alert"}:
                facts = token_entry.get("user_facts") or {}
                alert_id = int(facts.get("alert_id") or 0)
                if alert_id <= 0:
                    return None
                from study_app.data.database import list_knowledge_alert_events

                expected_actor = f"la:{action_id}"[:64]
                if action_type == "handle_alert":
                    target_date = str(
                        facts.get("effective_date")
                        or (self.today or date.today()).isoformat()
                    )
                    matching_event = any(
                        event.alert_id == alert_id
                        and event.actor == expected_actor
                        and event.event_type == "handled"
                        and event.to_status == "handled"
                        and event.effective_date.isoformat() == target_date
                        for event in list_knowledge_alert_events(
                            alert_id, self.db_path
                        )
                    )
                else:
                    target_date = str(facts.get("snoozed_until") or "")
                    effective_date = (self.today or date.today()).isoformat()
                    matching_event = any(
                        event.alert_id == alert_id
                        and event.actor == expected_actor
                        and event.event_type in {"snoozed", "resnoozed"}
                        and event.to_status == "snoozed"
                        and event.effective_date.isoformat() == effective_date
                        and str(event.detail.get("snoozed_until") or "") == target_date
                        for event in list_knowledge_alert_events(
                            alert_id, self.db_path
                        )
                    )
                if matching_event:
                    return {"alert_id": alert_id, "action_type": action_type}
        except Exception:
            return None
        return None

    def _recovered_receipt(
        self,
        action_id: str,
        token_entry: dict[str, Any],
        evidence: dict[str, Any],
        error: Exception,
    ) -> dict[str, Any]:
        return {
            "saved": True,
            "status": "completed",
            "actionId": action_id,
            "actionType": str(evidence.get("action_type") or token_entry.get("action_type") or ""),
            "recordId": evidence.get("record_id"),
            "alertId": evidence.get("alert_id"),
            "message": "业务数据已提交；后续回执更新未完成，请重新加载历史操作。",
            "warnings": [],
            "refreshWarning": True,
            "refreshWarningText": "操作已保存，后续处理失败。",
            "errorCode": self._error_code(error),
        }

    def _undo_commit_evidence(
        self, token_entry: dict[str, Any], undo_action_id: str
    ) -> bool:
        undo_state = token_entry.get("undo_state") or {}
        if undo_state.get("kind") != "record":
            return False
        try:
            from study_app.data.db_runtime import connect_readonly

            actor = f"la:undo:{undo_action_id}"[:64]
            with connect_readonly(self.db_path) as connection:
                row = connection.execute(
                    "SELECT raw_json FROM learning_records WHERE id = ?",
                    (int(undo_state["record_id"]),),
                ).fetchone()
                revision = connection.execute(
                    "SELECT raw_json FROM learning_record_revisions "
                    "WHERE record_id = ? AND action = 'revoked' AND actor = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (int(undo_state["record_id"]), actor),
                ).fetchone()
            return bool(
                row
                and json.loads(row[0]).get("_revoked")
                and revision
                and json.loads(revision[0]).get("_revoked")
            )
        except Exception:
            return False

    @classmethod
    def _receipt_dto(
        cls,
        receipt,
        *,
        saved: bool,
        warning: Exception | None = None,
    ) -> dict[str, Any]:
        return {
            "saved": bool(saved),
            "status": str(getattr(receipt, "status", "completed" if saved else "failed")),
            "actionId": str(getattr(receipt, "action_id", "") or ""),
            "actionType": str(getattr(receipt, "action_type", "") or ""),
            "recordId": getattr(receipt, "record_id", None),
            "alertId": getattr(receipt, "alert_id", None),
            "masteryChanges": cls._json_safe(getattr(receipt, "mastery_changes", ()) or ()),
            "alertResult": cls._json_safe(getattr(receipt, "alert_result", None)),
            "message": str(getattr(receipt, "message", "") or ""),
            "warnings": cls._json_safe(getattr(receipt, "warnings", ()) or ()),
            "executedAt": str(getattr(receipt, "executed_at", "") or ""),
            "disclosure": cls._json_safe(getattr(receipt, "disclosure", {}) or {}),
            "refreshWarning": warning is not None,
            "refreshWarningText": (
                "操作已保存，后续回执更新失败，请重新加载。" if warning is not None else ""
            ),
            **({"errorCode": cls._error_code(warning)} if warning is not None else {}),
        }

    @classmethod
    def _failure_receipt(
        cls, code: str, message: str, *, action_id: str = ""
    ) -> dict[str, Any]:
        return {
            "saved": False,
            "status": "failed",
            "actionId": str(action_id or ""),
            "actionType": "",
            "message": message,
            "warnings": [],
            "refreshWarning": False,
            "refreshWarningText": "",
            "errorCode": code,
        }

    @staticmethod
    def _error_code(error: Exception) -> str:
        return type(error).__name__

    @staticmethod
    def _safe_model_reference(value: object) -> str:
        text = str(value or "")
        if len(text) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", text):
            return ""
        return text

    @staticmethod
    def _safe_failure_message(error: Exception) -> str:
        from study_app.core.learning_assistant_engine import (
            ActionNotUndoableError,
            StaleProposalError,
        )

        if isinstance(error, StaleProposalError):
            return "提案已过期，请重新预览后确认。"
        if isinstance(error, ActionNotUndoableError):
            return "当前没有可撤销的操作。"
        return "本地操作未能完成，请检查数据后重新预览或重试。"
