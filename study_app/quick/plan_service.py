"""Qt-independent service facade for study-plan workflows.

The service keeps database connections, model internals, and credentials on the
Python side. Methods return small JSON-compatible dictionaries suitable for a
QML bridge. Preview tokens refer to private, in-memory drafts and are required
for every explicit save.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable

from study_app.core.active_subjects import (
    dashboard_activity_subject_names,
    dashboard_activity_subjects,
    require_activity_subject,
)
from study_app.core.async_tasks import (
    StaleSubjectTaskError,
    capture_catalog_revision,
    capture_subject_revision,
    revalidate_catalog_revision,
    revalidate_subject_revision,
)
from study_app.core.budgeted_day_plan import BudgetPlanInput, build_budgeted_day_plan
from study_app.core.dashboard import DashboardState, load_dashboard_state
from study_app.core.day_budget_input import (
    validate_day_budget_input,
    validate_subject_exam_date,
)
from study_app.core.plan_candidates import (
    CandidateCollection,
    PlanCandidate,
    build_plan_candidates,
    identify_model_topic,
)
from study_app.core.study_plan_feedback import (
    plan_day_feedback_details,
    planned_homework_score_result,
)
from study_app.core.study_plan_items import (
    build_budgeted_plan_items,
    build_study_plan_items,
    infer_subject_topic_from_plan_line,
)
from study_app.core.task_estimates import (
    CONFIRMED_TEMPLATE_ESTIMATE,
    TaskEstimate,
    USER_ESTIMATE,
    make_assistant_task_estimate,
    make_task_estimate,
)
from study_app.data import database, plan_repository, settings_audit_repository
from study_app.data.db_runtime import DatabaseNotInitializedError, connect_readonly
from study_app.data.plan_repository import (
    archive_active_study_plan,
    create_budgeted_day_plan,
    create_study_plan,
    get_active_study_plan,
    get_budgeted_day_plan,
    get_study_day_budget,
    get_subject_exam_date,
    get_task_estimate,
    recent_study_plan_texts,
    save_study_day_budget,
    save_subject_exam_date,
    save_task_estimate,
    update_study_plan_item_state,
)


_PLAN_KEYS = (
    "judgement",
    "goals",
    "short",
    "diagnostic",
    "record_template",
    "expected",
    "evidence",
)
_PREVIEW_TTL_SECONDS = 30 * 60
_MAX_PREVIEWS = 32
_PLAN_REFRESH_WARNING = "计划已保存，但后续刷新失败，请重新加载。"
_PROGRESS_REFRESH_WARNING = "进度已保存，但后续刷新失败，请重新加载。"
_FEEDBACK_REFRESH_WARNING = "反馈已保存，但后续处理失败，请重新加载。"
_PHASE_REFRESH_WARNING = "复习模式已更新，但计划归档或刷新失败，请重新加载。"


class StalePlanPreviewError(RuntimeError):
    """A preview was replayed, expired, or its source data changed."""


@dataclass(frozen=True)
class _PreviewDraft:
    kind: str
    created_at: float
    fingerprint: str
    revision_token: Any
    subject_scope: str | None
    payload: dict[str, Any]
    value: Any


class PlanService:
    """Path-injected study-plan reads, previews, confirmations, and feedback."""

    def __init__(
        self,
        db_path: str | Path,
        model_path: str | Path,
        records_path: str | Path,
        today: date | str | None = None,
        initialize: Callable[[], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        self.today = today
        self._initialize = initialize
        self._initialized = False
        self._lock = threading.RLock()
        self._drafts: dict[str, _PreviewDraft] = {}

    def _ensure_initialized(self) -> None:
        with self._lock:
            if self._initialized:
                return
            if self._initialize is not None:
                self._initialize()
            self._initialized = True
            self._initialize = None

    def _load_state(self) -> DashboardState:
        self._ensure_initialized()
        return load_dashboard_state(
            model_path=self.model_path,
            records_path=self.records_path,
            today=self.today,
            db_path=self.db_path,
        )

    @staticmethod
    def _scope_value(scope: object) -> str | None:
        if isinstance(scope, dict):
            scope = scope.get("subject") or scope.get("subjectId")
        if scope is None or scope == "":
            return None
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("学科范围必须是非空文本或全部活动学科")
        return scope.strip()

    def _validate_scope(self, state: DashboardState, scope: object, purpose: str) -> str | None:
        subject_scope = self._scope_value(scope)
        if subject_scope:
            require_activity_subject(state, subject_scope, purpose)
        return subject_scope

    def _revision_token(self, subject_scope: str | None):
        if subject_scope:
            return capture_subject_revision(self.db_path, subject_scope)
        return capture_catalog_revision(self.db_path)

    @staticmethod
    def _revalidate(revision_token) -> None:
        if revision_token is None:
            return
        if hasattr(revision_token, "subject_key"):
            revalidate_subject_revision(revision_token)
        else:
            revalidate_catalog_revision(revision_token)

    @classmethod
    def _assert_revision(cls, revision_token) -> None:
        try:
            cls._revalidate(revision_token)
        except StaleSubjectTaskError as error:
            raise StalePlanPreviewError("学科目录版本已变化，请重新预览后确认。") from error

    @staticmethod
    def _file_digest(path: Path) -> str | None:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()
        except FileNotFoundError:
            return None

    @staticmethod
    def _json_digest(value: object) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _candidate_collection(
        self, state: DashboardState, subject_scope: str | None = None
    ) -> CandidateCollection:
        return build_plan_candidates(state, subject_scope)

    def _saved_estimates(
        self, collection: CandidateCollection
    ) -> dict[str, TaskEstimate]:
        estimates: dict[str, TaskEstimate] = {}
        for candidate in collection.candidates:
            try:
                estimate = get_task_estimate(candidate, db_path=self.db_path)
            except DatabaseNotInitializedError:
                break
            if estimate is not None:
                estimates[candidate.task_id] = estimate
        return estimates

    def _estimates_with_defaults(
        self, collection: CandidateCollection
    ) -> dict[str, TaskEstimate]:
        estimates = self._saved_estimates(collection)
        for candidate in collection.candidates:
            estimates.setdefault(candidate.task_id, make_assistant_task_estimate(candidate))
        return estimates

    def _phase_map(self, state: DashboardState) -> dict[str, dict[str, Any]]:
        from study_app.core.study_phase import get_subject_phase

        phases: dict[str, dict[str, Any]] = {}
        for name in dashboard_activity_subject_names(state):
            phases[name] = get_subject_phase(name, db_path=self.db_path)
        return phases

    @staticmethod
    def _state_fingerprint_payload(state: DashboardState) -> dict[str, Any]:
        # This stays private; only its digest is retained with a preview token.
        return asdict(state)

    def _database_fingerprint_payload(
        self,
        state: DashboardState,
        subject_scope: str | None,
        collection: CandidateCollection,
    ) -> dict[str, Any]:
        today = state.today.isoformat()
        result: dict[str, Any] = {
            "catalogRevision": capture_catalog_revision(self.db_path),
            "phases": self._phase_map(state),
            "activePlan": get_active_study_plan(subject_scope, db_path=self.db_path),
            "allBudgetPlan": None,
            "dayBudget": None,
            "examDates": {},
            "estimates": {},
        }
        try:
            result["allBudgetPlan"] = get_budgeted_day_plan(today, db_path=self.db_path)
        except DatabaseNotInitializedError:
            result["allBudgetPlan"] = {"schema": "unavailable"}
        try:
            budget = get_study_day_budget(today, db_path=self.db_path)
            result["dayBudget"] = (
                {"planDate": budget.plan_date, "availableMinutes": budget.available_minutes}
                if budget is not None
                else None
            )
        except DatabaseNotInitializedError:
            result["dayBudget"] = {"schema": "unavailable"}
        for name in dashboard_activity_subject_names(state):
            try:
                result["examDates"][name] = get_subject_exam_date(name, db_path=self.db_path)
            except DatabaseNotInitializedError:
                result["examDates"][name] = "schema-unavailable"
            except LookupError:
                result["examDates"][name] = "subject-unavailable"
        for candidate in collection.candidates:
            try:
                estimate = get_task_estimate(candidate, db_path=self.db_path)
            except DatabaseNotInitializedError:
                result["estimates"][candidate.task_id] = "schema-unavailable"
            else:
                result["estimates"][candidate.task_id] = (
                    asdict(estimate) if estimate is not None else None
                )
        return result

    def _fingerprint(
        self,
        state: DashboardState,
        subject_scope: str | None,
        collection: CandidateCollection | None = None,
    ) -> str:
        collection = collection or self._candidate_collection(state)
        payload = {
            "dbPathIdentity": str(self.db_path.resolve()),
            "modelDigest": self._file_digest(self.model_path),
            "recordsDigest": self._file_digest(self.records_path),
            "state": self._state_fingerprint_payload(state),
            "database": self._database_fingerprint_payload(state, subject_scope, collection),
        }
        return self._json_digest(payload)

    def _remember_preview(
        self,
        kind: str,
        state: DashboardState,
        subject_scope: str | None,
        payload: dict[str, Any],
        value: Any,
        collection: CandidateCollection | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        draft = _PreviewDraft(
            kind=kind,
            created_at=time.monotonic(),
            fingerprint=self._fingerprint(state, subject_scope, collection),
            revision_token=self._revision_token(subject_scope),
            subject_scope=subject_scope,
            payload=dict(payload),
            value=value,
        )
        with self._lock:
            self._drafts[token] = draft
            while len(self._drafts) > _MAX_PREVIEWS:
                self._drafts.pop(next(iter(self._drafts)))
        return token

    def _consume_preview(self, token: object, expected_kind: str) -> _PreviewDraft:
        if not isinstance(token, str) or not token:
            raise StalePlanPreviewError("预览令牌无效，请重新预览。")
        with self._lock:
            draft = self._drafts.pop(token, None)
        if draft is None or draft.kind != expected_kind:
            raise StalePlanPreviewError("预览已失效或已确认，请重新预览。")
        if time.monotonic() - draft.created_at > _PREVIEW_TTL_SECONDS:
            raise StalePlanPreviewError("预览已超时，请重新预览。")
        return draft

    @staticmethod
    def _clean_value(value: object) -> object:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {
                str(key): PlanService._clean_value(item)
                for key, item in value.items()
                if str(key).lower() not in {"api_key", "api_key_protected", "password", "secret", "token", "connection", "db_path"}
            }
        if isinstance(value, (list, tuple)):
            return [PlanService._clean_value(item) for item in value]
        return str(value)

    @classmethod
    def _plan_dto(cls, plan: object) -> dict[str, list[str]]:
        if not isinstance(plan, dict):
            return {key: [] for key in _PLAN_KEYS}
        return {
            key: [str(item) for item in plan.get(key, []) if isinstance(item, str)]
            if isinstance(plan.get(key, []), list)
            else []
            for key in _PLAN_KEYS
        }

    @classmethod
    def _detailed_plan_dto(cls, saved: dict[str, Any] | None) -> dict[str, Any] | None:
        if not saved:
            return None
        items = []
        for item in saved.get("items", []):
            items.append(
                {
                    "id": int(item["id"]),
                    "sectionKey": str(item.get("section_key") or ""),
                    "sectionTitle": str(item.get("section_title") or ""),
                    "dayIndex": item.get("day_index"),
                    "itemType": str(item.get("item_type") or ""),
                    "itemText": str(item.get("item_text") or ""),
                    "itemOrder": int(item.get("item_order") or 0),
                    "itemHash": str(item.get("item_hash") or ""),
                    "checked": bool(item.get("checked")),
                    "result": item.get("result"),
                }
            )
        return {
            "id": int(saved["id"]),
            "subjectScope": saved.get("subject_scope") or None,
            "startDate": str(saved.get("start_date") or ""),
            "endDate": str(saved.get("end_date") or ""),
            "createdAt": str(saved.get("created_at") or ""),
            "plan": cls._plan_dto(saved.get("plan")),
            "items": items,
        }

    @classmethod
    def _budget_plan_dto(cls, saved: dict[str, Any] | None) -> dict[str, Any] | None:
        if not saved:
            return None
        summary = saved.get("summary") or {}
        items = []
        for item in saved.get("items", []):
            reason = item.get("selection_reason")
            if not isinstance(reason, dict):
                reason = {}
            items.append(
                {
                    "id": int(item["id"]),
                    "sectionKey": str(item.get("section_key") or ""),
                    "itemText": str(item.get("item_text") or ""),
                    "taskId": item.get("task_id"),
                    "subjectId": item.get("subject_id"),
                    "estimatedMinutes": item.get("estimated_minutes"),
                    "estimateSource": item.get("estimate_source"),
                    "checked": bool(item.get("checked")),
                    "result": item.get("result"),
                    "excludedReason": item.get("excluded_reason"),
                    "selectionReason": {
                        "ruleCode": reason.get("rule_code"),
                        "reason": str(reason.get("reason") or ""),
                        "rankingEvidence": cls._clean_value(reason.get("ranking_evidence") or {}),
                    },
                }
            )
        return {
            "id": int(saved["id"]),
            "planDate": str(saved.get("plan_date") or ""),
            "budgetMinutes": int(saved.get("budget_minutes") or 0),
            "createdAt": str(saved.get("created_at") or ""),
            "summary": {
                "plannedMinutes": int(summary.get("planned_minutes") or 0),
                "remainingMinutes": int(summary.get("remaining_minutes") or 0),
                "overBudgetCompletedMinutes": int(summary.get("over_budget_completed_minutes") or 0),
                "completedOccupancyUnknown": bool(summary.get("completed_occupancy_unknown")),
            },
            "items": items,
        }

    def _catalog_dto(self, state: DashboardState, selected_scope: str | None) -> dict[str, Any]:
        from study_app.core.curriculum_hierarchy import topic_submodule
        from study_app.core.study_phase import is_in_exam_scope

        active_names = set(dashboard_activity_subject_names(state))
        phases = self._phase_map(state)
        subjects = []
        for subject in state.model_data.get("subjects", []):
            name = str(subject.get("name") or "")
            if name not in active_names:
                continue
            phase = phases.get(name, {})
            raw_exam_scope = phase.get("exam_scope")
            exam_scope = raw_exam_scope if isinstance(raw_exam_scope, dict) else {}
            modules = []
            for module in subject.get("modules", []) or []:
                module_name = str(module.get("name") or "")
                submodules = [
                    {"name": str(item.get("name") or "")}
                    for item in module.get("submodules", []) or []
                    if str(item.get("name") or "").strip()
                ]
                topics = []
                for topic in module.get("topics", []) or []:
                    topic_name = str(topic.get("name") or "")
                    if not topic_name:
                        continue
                    submodule = topic_submodule(topic, module)
                    topics.append(
                        {
                            "name": topic_name,
                            "submodule": submodule,
                            "inExamScope": bool(
                                is_in_exam_scope(
                                    name,
                                    module_name,
                                    topic_name,
                                    submodule,
                                    phase=phase,
                                    infer_submodule=False,
                                )
                            ),
                        }
                    )
                modules.append({"name": module_name, "submodules": submodules, "topics": topics})
            subjects.append(
                {
                    "name": name,
                    "phase": str(phase.get("phase") or "regular"),
                    "phaseLabel": str(phase.get("label") or "常规学习"),
                    "examScope": {
                        "label": str(exam_scope.get("label") or ""),
                        "modules": self._clean_value(exam_scope.get("modules") or []),
                        "submodules": self._clean_value(exam_scope.get("included_submodules") or []),
                        "topics": self._clean_value(exam_scope.get("included_topics") or []),
                        "excludedSubmodules": self._clean_value(exam_scope.get("excluded_submodules") or []),
                        "excludedTopics": self._clean_value(exam_scope.get("excluded_topics") or []),
                    },
                    "modules": modules,
                }
            )
        return {
            "subjects": subjects,
            "selectedScope": {"subject": selected_scope, "module": None, "topic": None},
        }

    def _load_exam_dates(self, state: DashboardState) -> dict[str, str | None]:
        result = {}
        for name in dashboard_activity_subject_names(state):
            try:
                result[name] = get_subject_exam_date(name, db_path=self.db_path)
            except (DatabaseNotInitializedError, LookupError):
                result[name] = None
        return result

    def _task_rows(
        self,
        state: DashboardState,
        collection: CandidateCollection | None = None,
        estimates: dict[str, TaskEstimate] | None = None,
    ) -> list[dict[str, Any]]:
        collection = collection or self._candidate_collection(state)
        estimates = estimates if estimates is not None else self._saved_estimates(collection)
        phases = self._phase_map(state)
        identity_by_task: dict[str, tuple[str, str]] = {}
        for subject in state.model_data.get("subjects", []):
            subject_name = str(subject.get("name") or "")
            for module in subject.get("modules", []) or []:
                module_name = str(module.get("name") or "")
                for topic in module.get("topics", []) or []:
                    source = identify_model_topic(
                        state.model_data,
                        subject_name,
                        module_name,
                        str(topic.get("name") or ""),
                    )
                    if source is not None:
                        identity_by_task[source.task_id] = (module_name, str(topic.get("name") or ""))
        rows = []
        for candidate in collection.candidates:
            estimate = estimates.get(candidate.task_id)
            if estimate is None:
                estimate = make_assistant_task_estimate(candidate)
            module, topic = identity_by_task.get(candidate.task_id, ("", ""))
            rows.append(
                {
                    "taskId": candidate.task_id,
                    "subjectId": candidate.subject_id,
                    "title": candidate.title,
                    "module": module,
                    "topic": topic,
                    "estimatedMinutes": int(estimate.estimated_minutes),
                    "estimateSource": estimate.source,
                    "phase": str(phases.get(candidate.subject_id, {}).get("phase") or "regular"),
                }
            )
        return rows

    def _read_current_budget_plan(self, state: DashboardState, scope: str | None):
        try:
            return get_budgeted_day_plan(state.today.isoformat(), scope, db_path=self.db_path)
        except DatabaseNotInitializedError:
            return None

    def load(self, scope: str | dict[str, Any] | None = None) -> dict[str, Any]:
        state = self._load_state()
        subject_scope = self._validate_scope(state, scope, "查看学习计划")
        collection = self._candidate_collection(state, subject_scope)
        estimates = self._saved_estimates(collection)
        active_plan = get_active_study_plan(subject_scope, db_path=self.db_path)
        budget = None
        try:
            saved_budget = get_study_day_budget(state.today.isoformat(), db_path=self.db_path)
            if saved_budget is not None:
                budget = {
                    "planDate": saved_budget.plan_date,
                    "availableMinutes": saved_budget.available_minutes,
                }
        except DatabaseNotInitializedError:
            pass
        saved_budget_plan = self._read_current_budget_plan(state, subject_scope)
        day_feedback = (
            plan_day_feedback_details(active_plan, state, subject_scope)
            if active_plan is not None
            else []
        )
        feedback_dto = [
            {
                "dayIndex": int(item.get("day_index") or 0),
                "complete": bool(item.get("complete")),
                "line": str(item.get("line") or ""),
                "popup": str(item.get("popup") or ""),
            }
            for item in day_feedback
        ]
        unmapped = [
            {
                "title": str(item.title),
                "reason": str(item.reason),
                "subjectId": item.subject_id,
            }
            for item in collection.unmapped
        ]
        return {
            "date": state.today.isoformat(),
            "catalog": self._catalog_dto(state, subject_scope),
            "currentPlan": self._detailed_plan_dto(active_plan),
            "budget": budget,
            "budgetPlan": self._budget_plan_dto(saved_budget_plan),
            "examDates": self._load_exam_dates(state),
            "tasks": self._task_rows(state, collection, estimates),
            "unmapped": unmapped,
            "dayFeedback": feedback_dto,
        }

    @staticmethod
    def _payload_dict(payload: object, name: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError(f"{name} 必须是对象")
        return payload

    def preview_budget(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._payload_dict(payload, "预算预览")
        state = self._load_state()
        subject_scope = self._validate_scope(state, payload.get("scope"), "预览今日预算计划")
        budget_value = payload.get("budget_minutes")
        if type(budget_value) is not int:
            raise ValueError("今日可用时间须为 0～1440 的整数分钟")
        budget = validate_day_budget_input(state.today.isoformat(), budget_value)
        collection = self._candidate_collection(state)
        candidate_by_id = {candidate.task_id: candidate for candidate in collection.candidates}
        loaded_estimates = self._estimates_with_defaults(collection)
        estimate_values = payload.get("estimate_minutes") or {}
        if not isinstance(estimate_values, dict):
            raise ValueError("estimate_minutes 必须是任务 ID 到分钟数的对象")
        estimates = dict(loaded_estimates)
        new_estimates: dict[str, TaskEstimate] = {}
        for raw_task_id, minutes in estimate_values.items():
            task_id = str(raw_task_id)
            if task_id not in candidate_by_id:
                raise ValueError("估时包含不在当前推荐中的任务")
            if type(minutes) is not int or not 0 <= minutes <= 1440:
                raise ValueError("任务预计分钟须为 0～1440 的整数")
            if minutes <= 0:
                continue
            existing = estimates.get(task_id)
            estimate = (
                existing
                if (
                    existing is not None
                    and existing.estimated_minutes == minutes
                    and existing.source == USER_ESTIMATE
                )
                else make_task_estimate(candidate_by_id[task_id], minutes, USER_ESTIMATE)
            )
            estimates[task_id] = estimate
            if estimate is not existing:
                new_estimates[task_id] = estimate

        raw_exam_dates = payload.get("exam_dates") or {}
        if not isinstance(raw_exam_dates, dict):
            raise ValueError("exam_dates 必须是学科到日期的对象")
        exam_dates = self._load_exam_dates(state)
        for raw_name, value in raw_exam_dates.items():
            name = str(raw_name)
            if name not in exam_dates:
                raise ValueError("考试日期包含非活动学科")
            exam_dates[name] = validate_subject_exam_date(value)

        prior = get_budgeted_day_plan(state.today.isoformat(), db_path=self.db_path)
        prior_done = {
            row["task_id"]: row
            for row in (prior or {}).get("items", [])
            if row.get("task_id") and row.get("checked")
        }
        if set(prior_done) - set(candidate_by_id):
            raise ValueError("已有完成任务不在当前推荐中。请保留当前计划，刷新数据核对后再调整。")
        enriched = tuple(
            PlanCandidate(
                **{
                    **asdict(candidate),
                    "estimated_minutes": estimates[candidate.task_id].estimated_minutes,
                    "estimate_source": estimates[candidate.task_id].source,
                    "completion_state": (
                        "checked" if candidate.task_id in prior_done else candidate.completion_state
                    ),
                }
            )
            if candidate.task_id in estimates
            else candidate
            for candidate in collection.candidates
        )
        plan = build_budgeted_day_plan(
            BudgetPlanInput(
                budget.plan_date,
                budget.available_minutes,
                dashboard_activity_subject_names(state),
                subject_scope,
                exam_dates,
                budget.plan_date,
            ),
            CandidateCollection(enriched, collection.unmapped),
        )
        rows = []
        for section, decisions in (
            ("selected", plan.selected),
            ("completed", plan.completed),
            ("excluded", plan.excluded),
        ):
            for decision in decisions:
                rows.append(
                    {
                        "key": decision.task_id or f"unmapped:{len(rows)}",
                        "taskId": decision.task_id,
                        "title": decision.title,
                        "subjectId": decision.subject_id,
                        "estimatedMinutes": decision.estimated_minutes,
                        "estimateSource": decision.estimate_source,
                        "section": section,
                        "checked": section == "completed",
                        "excludedReason": decision.rule_code if section == "excluded" else None,
                        "reason": str(decision.reason),
                        "rankingEvidence": self._clean_value(decision.ranking_evidence),
                    }
                )
        token = self._remember_preview(
            "budget",
            state,
            subject_scope,
            payload,
            {
                "budget": budget,
                "exam_dates": exam_dates,
                "estimates": estimates,
                "new_estimates": new_estimates,
                "candidate_by_id": candidate_by_id,
                "collection": collection,
                "plan": plan,
            },
            collection,
        )
        return {
            "token": token,
            "summary": {
                "plannedMinutes": plan.planned_minutes,
                "remainingMinutes": plan.remaining_minutes,
                "overBudgetCompletedMinutes": plan.over_budget_completed_minutes,
                "completedOccupancyUnknown": plan.completed_occupancy_unknown,
            },
            "rows": rows,
        }

    def confirm_budget(self, token: str) -> dict[str, Any]:
        draft = self._consume_preview(token, "budget")
        value = draft.value
        budget = value["budget"]
        # All inputs and the replacement plan form one confirmation. Helpers
        # borrow this transaction and never commit intermediate changes.
        with database.connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_revision(draft.revision_token)
            state = self._load_state()
            collection = self._candidate_collection(state)
            if self._fingerprint(state, draft.subject_scope, collection) != draft.fingerprint:
                raise StalePlanPreviewError("目录、数据或预算输入已变化，请重新预览后确认。")
            self._assert_revision(draft.revision_token)
            save_study_day_budget(
                budget.plan_date,
                budget.available_minutes,
                db_path=self.db_path,
                connection=connection,
            )
            for name, exam_date in value["exam_dates"].items():
                save_subject_exam_date(state, name, exam_date, db_path=self.db_path,
                                       connection=connection)
            for task_id, estimate in value["new_estimates"].items():
                save_task_estimate(
                    state,
                    value["candidate_by_id"][task_id],
                    estimate.estimated_minutes,
                    estimate.source,
                    db_path=self.db_path,
                    connection=connection,
                )
            plan_id = create_budgeted_day_plan(
                budget.plan_date,
                budget.available_minutes,
                value["plan"],
                db_path=self.db_path,
                connection=connection,
            )
        # Leaving the context successfully is the commit point. A failed read
        # afterwards cannot change the outcome of this confirmation.
        result = {"saved": True, "planId": plan_id}
        try:
            saved = get_budgeted_day_plan(budget.plan_date, db_path=self.db_path)
            result["budgetPlan"] = self._budget_plan_dto(saved)
        except Exception:
            result["refreshWarning"] = "预算计划已保存；回读失败，请手动刷新。"
        return result

    def preview_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._payload_dict(payload, "详细计划预览")
        state = self._load_state()
        subject_scope = self._validate_scope(state, payload.get("scope"), "预览学习计划")
        enhanced = payload.get("enhanced", False)
        if type(enhanced) is not bool:
            raise ValueError("enhanced 必须是布尔值")
        revision_token = self._revision_token(subject_scope)
        from study_app.ai.study_plan_service import (
            current_study_plan_signature,
            generate_study_plan_with_optional_llm,
        )
        from study_app.core.local_study_plan import generate_study_plan

        if enhanced:
            plan, source = generate_study_plan_with_optional_llm(
                state,
                subject_scope,
                revision_token=revision_token,
                db_path=self.db_path,
                model_path=self.model_path,
                records_path=self.records_path,
            )
        else:
            plan = generate_study_plan(
                state, subject_scope, db_path=self.db_path, model_path=self.model_path
            )
            source = "local"
        signature = current_study_plan_signature(
            state,
            subject_scope,
            db_path=self.db_path,
            model_path=self.model_path,
            records_path=self.records_path,
        )
        self._revalidate(revision_token)
        candidate_collection = self._candidate_collection(state)
        item_rows = build_study_plan_items(plan)
        token = self._remember_preview(
            "plan",
            state,
            subject_scope,
            payload,
            {"plan": plan, "signature": signature, "generationDate": state.today.isoformat(), "items": item_rows, "source": source},
            candidate_collection,
        )
        return {
            "token": token,
            "source": str(source),
            "plan": self._plan_dto(plan),
            "items": [
                {
                    "sectionKey": str(item["section_key"]),
                    "sectionTitle": str(item["section_title"]),
                    "dayIndex": item.get("day_index"),
                    "itemType": str(item["item_type"]),
                    "itemText": str(item["item_text"]),
                    "itemOrder": int(item["item_order"]),
                    "itemHash": str(item["item_hash"]),
                }
                for item in item_rows
            ],
        }

    def confirm_plan(self, token: str) -> dict[str, Any]:
        draft = self._consume_preview(token, "plan")
        self._assert_revision(draft.revision_token)
        state = self._load_state()
        collection = self._candidate_collection(state)
        if self._fingerprint(state, draft.subject_scope, collection) != draft.fingerprint:
            raise StalePlanPreviewError("目录、学习数据或现有计划已变化，请重新预览后确认。")
        self._assert_revision(draft.revision_token)
        value = draft.value
        self._assert_revision(draft.revision_token)
        plan_id = create_study_plan(
            draft.subject_scope,
            value["generationDate"],
            value["generationDate"],
            value["signature"],
            value["plan"],
            value["items"],
            db_path=self.db_path,
        )
        refresh_warning = False
        try:
            settings_audit_repository.delete_settings_by_prefix(
                "daily_summary_cache:", db_path=self.db_path
            )
        except Exception:
            refresh_warning = True
        current_plan = None
        try:
            saved = get_active_study_plan(draft.subject_scope, db_path=self.db_path)
            current_plan = self._detailed_plan_dto(saved)
        except Exception:
            refresh_warning = True
        result = {
            "saved": True,
            "planId": int(plan_id),
            "source": str(value["source"]),
            "currentPlan": current_plan,
        }
        if refresh_warning:
            result["refreshWarning"] = _PLAN_REFRESH_WARNING
        return result

    def _current_active_item(
        self, item_id: object, subject_scope: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        if type(item_id) is not int or item_id <= 0:
            raise ValueError("计划条目 ID 无效")
        candidates = []
        if subject_scope is not None:
            candidates.append(get_active_study_plan(subject_scope, db_path=self.db_path))
        else:
            candidates.append(get_active_study_plan(None, db_path=self.db_path))
        try:
            budget_plan = get_budgeted_day_plan(
                self._load_state().today.isoformat(), db_path=self.db_path
            )
        except DatabaseNotInitializedError:
            budget_plan = None
        for plan in candidates:
            for item in (plan or {}).get("items", []):
                if int(item.get("id") or 0) == item_id:
                    return "detailed", item
        for item in (budget_plan or {}).get("items", []):
            if int(item.get("id") or 0) == item_id:
                return "budget", item
        raise LookupError("计划条目已不在当前活动计划中")

    def set_checked(
        self,
        item_id: int,
        checked: bool,
        scope: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if type(checked) is not bool:
            raise ValueError("checked 必须是布尔值")
        state = self._load_state()
        subject_scope = self._validate_scope(state, scope, "更新计划进度")
        _kind, _item = self._current_active_item(item_id, subject_scope)
        update_study_plan_item_state(item_id, checked=checked, db_path=self.db_path)
        review_feedback = []
        current_plan = None
        budget_plan = None
        refresh_warning = False
        try:
            saved = self._read_current_budget_plan(state, subject_scope)
            active = get_active_study_plan(subject_scope, db_path=self.db_path)
            if checked and active:
                shown_key = "study_plan_day_feedback_shown"
                previously_shown = settings_audit_repository.get_setting(
                    shown_key, [], db_path=self.db_path
                )
                shown = set(previously_shown if isinstance(previously_shown, list) else [])
                markers = set(shown)
                for feedback in plan_day_feedback_details(active, self._load_state(), subject_scope):
                    marker = f"{active['id']}:{feedback['day_index']}"
                    if feedback.get("complete") and marker not in shown:
                        review_feedback.append(
                            {
                                "dayIndex": int(feedback["day_index"]),
                                "line": str(feedback.get("line") or ""),
                                "popup": str(feedback.get("popup") or ""),
                            }
                        )
                        markers.add(marker)
                if markers != shown:
                    settings_audit_repository.set_setting(
                        shown_key, sorted(markers), db_path=self.db_path
                    )
            current_plan = self._detailed_plan_dto(active)
            budget_plan = self._budget_plan_dto(saved)
        except Exception:
            refresh_warning = True
        result = {
            "updated": True,
            "currentPlan": current_plan,
            "budgetPlan": budget_plan,
            "reviewFeedback": review_feedback,
        }
        if refresh_warning:
            result["refreshWarning"] = _PROGRESS_REFRESH_WARNING
        return result

    def feedback(
        self,
        item_id: int,
        is_correct: bool,
        scope: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if type(is_correct) is not bool:
            raise ValueError("is_correct 必须是布尔值")
        state = self._load_state()
        subject_scope = self._validate_scope(state, scope, "记录计划作业反馈")
        kind, item = self._current_active_item(item_id, subject_scope)
        if kind != "detailed" or item.get("item_type") != "result":
            raise ValueError("只能对当前详细计划中的作业条目提交正确或错误反馈")
        if item.get("result") is not None:
            raise ValueError("该计划作业已记录反馈")

        import re

        line = str(item.get("item_text") or "")
        feedback_subject_scope = subject_scope
        if feedback_subject_scope is None:
            active_subject_names = [str(row.name) for row in state.subjects if row.name]
            matching_names = [name for name in active_subject_names if name in line]
            if len(matching_names) == 1:
                feedback_subject_scope = matching_names[0]
            elif len(active_subject_names) == 1:
                feedback_subject_scope = active_subject_names[0]
            else:
                active_plan = get_active_study_plan(None, db_path=self.db_path)
                day_index = item.get("day_index")
                related_text = " ".join(
                    str(row.get("item_text") or "")
                    for row in (active_plan or {}).get("items", [])
                    if row.get("day_index") == day_index
                    and row.get("section_key") == "short"
                )
                matching_names = [name for name in active_subject_names if name in related_text]
                if len(matching_names) == 1:
                    feedback_subject_scope = matching_names[0]
        subject, topic = infer_subject_topic_from_plan_line(line, feedback_subject_scope)
        subject_before = next((row for row in state.subjects if row.name == subject), None)
        feedback_key = f"study_plan_item_feedback:{item_id}"
        snapshot = settings_audit_repository.get_setting(
            feedback_key, {}, db_path=self.db_path
        )
        if not isinstance(snapshot, dict):
            snapshot = {}
        if subject_before and not snapshot.get("before_captured"):
            snapshot.update(
                {
                    "before_captured": True,
                    "window_score_before": subject_before.window_score,
                    "covered_mastery_before": subject_before.covered_mastery_score,
                    "total_mastery_before": subject_before.mastery_score,
                }
            )
        match = re.search(r"参考难度\s*(\d+(?:\.\d+)?)\s*/\s*100", line)
        difficulty = float(match.group(1)) if match else None
        score_result = planned_homework_score_result(
            difficulty, is_correct, model_path=self.model_path
        )
        result_name = "完成正确" if is_correct else "完成有误"
        result_code = "correct" if is_correct else "wrong"
        record = {
            "date": state.today.isoformat(),
            "subject": subject,
            "module": None,
            "topic": topic,
            "activity": "review_exercise",
            "source": "outside_class",
            "score": score_result.score,
            "note": f"学习计划项{result_name}：{line}",
            "problems": [
                {
                    "title": f"学习计划作业：{topic}",
                    "statement": line,
                    "answer_result": "全对" if is_correct else "错误",
                    "status": result_code,
                    "correctness": 100 if is_correct else 0,
                    "partial_credit": 1.0 if is_correct else 0.0,
                    "error_cause": "" if is_correct else "学习计划作业完成有误，需要复盘错因",
                    "related_topics": [topic],
                    "difficulty_score": difficulty,
                    "difficulty_source": "planned_reference" if difficulty is not None else "planned_homework",
                }
            ],
        }
        record_note = str(record["note"])
        prior_record_ids = self._feedback_record_ids(
            state.today.isoformat(), subject, record_note
        )
        refresh_warning = False
        try:
            record_id = database.add_learning_record(
                record,
                db_path=self.db_path,
                study_plan_item_id=item_id,
                study_plan_result=result_code,
                model_path=self.model_path,
            )
        except database.LearningRecordPostCommitError as error:
            record_id = error.record_id
            refresh_warning = True
        except Exception:
            record_id = self._recover_committed_feedback_record(
                item_id,
                result_code,
                state.today.isoformat(),
                subject,
                record_note,
                prior_record_ids,
            )
            if record_id is None:
                raise
            refresh_warning = True
        current_plan = None
        try:
            snapshot.update(
                {
                    "result_score": score_result.score,
                    "score_source": score_result.source,
                    "score_fallback_reason": score_result.fallback_reason,
                }
            )
            settings_audit_repository.set_setting(feedback_key, snapshot, db_path=self.db_path)
            settings_audit_repository.delete_settings_by_prefix(
                "daily_summary_cache:", db_path=self.db_path
            )
            refreshed = self._load_state()
            subject_after = next((row for row in refreshed.subjects if row.name == subject), None)
            if subject_after:
                snapshot.update(
                    {
                        "window_score_after": subject_after.window_score,
                        "covered_mastery_after": subject_after.covered_mastery_score,
                        "total_mastery_after": subject_after.mastery_score,
                    }
                )
                settings_audit_repository.set_setting(feedback_key, snapshot, db_path=self.db_path)
            active = get_active_study_plan(subject_scope, db_path=self.db_path)
            current_plan = self._detailed_plan_dto(active)
        except Exception:
            refresh_warning = True
        result = {
            "saved": True,
            "itemId": item_id,
            "recordId": int(record_id),
            "result": result_code,
            "score": score_result.score,
            "scoreSource": score_result.source,
            "fallbackReason": score_result.fallback_reason,
            "subject": subject,
            "topic": topic,
            "currentPlan": current_plan,
        }
        if refresh_warning:
            result["refreshWarning"] = _FEEDBACK_REFRESH_WARNING
        return result

    def _feedback_record_ids(self, record_date: str, subject: str, note: str) -> set[int]:
        with connect_readonly(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id FROM learning_records
                WHERE record_date = ? AND subject_name = ? AND note = ?
                """,
                (record_date, subject, note),
            ).fetchall()
        return {int(row["id"]) for row in rows}

    def _recover_committed_feedback_record(
        self,
        item_id: int,
        result_code: str,
        record_date: str,
        subject: str,
        note: str,
        prior_record_ids: set[int],
    ) -> int | None:
        with connect_readonly(self.db_path) as connection:
            state = connection.execute(
                "SELECT result FROM study_plan_item_states WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if state is None or state["result"] != result_code:
                return None
            rows = connection.execute(
                """
                SELECT id FROM learning_records
                WHERE record_date = ? AND subject_name = ? AND note = ?
                ORDER BY id DESC
                """,
                (record_date, subject, note),
            ).fetchall()
        return next(
            (int(row["id"]) for row in rows if int(row["id"]) not in prior_record_ids),
            None,
        )

    def _write_phase(self, subject: str, final_review: bool) -> dict[str, Any]:
        from study_app.core.study_phase import set_subject_phase

        return set_subject_phase(
            subject,
            "final_review" if final_review else "regular",
            db_path=self.db_path,
        )

    def set_final_review_modes(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._payload_dict(payload, "期末复习设置")
        selected = payload.get("states", payload)
        if not isinstance(selected, dict):
            raise ValueError("states 必须是学科到布尔值的对象")
        state = self._load_state()
        active_names = set(dashboard_activity_subject_names(state))
        validated = []
        for raw_name, enabled in selected.items():
            name = str(raw_name)
            if name not in active_names:
                raise ValueError("期末复习设置包含非活动学科")
            if type(enabled) is not bool:
                raise ValueError("期末复习状态必须是布尔值")
            validated.append((name, enabled))

        from study_app.core.study_phase import get_subject_phase

        targets = []
        for name, enabled in validated:
            current = get_subject_phase(name, db_path=self.db_path).get("phase") == "final_review"
            if current != enabled:
                targets.append((name, enabled))

        changes = []
        refresh_warning = False
        for name, enabled in targets:
            try:
                policy = self._write_phase(name, enabled)
            except Exception:
                if not self._phase_matches(name, enabled):
                    if not changes:
                        raise
                    refresh_warning = True
                    break
                refresh_warning = True
                policy = {"phase": "final_review" if enabled else "regular"}
            changes.append({"subject": name, "phase": str(policy.get("phase") or "regular")})
            try:
                archive_active_study_plan(name, db_path=self.db_path)
            except Exception:
                refresh_warning = True
                break
        if changes:
            try:
                archive_active_study_plan(None, db_path=self.db_path)
                settings_audit_repository.delete_settings_by_prefix(
                    "daily_summary_cache:", db_path=self.db_path
                )
            except Exception:
                refresh_warning = True
        result = {"changed": changes, "updated": bool(changes)}
        if refresh_warning:
            result["refreshWarning"] = _PHASE_REFRESH_WARNING
        return result

    def _phase_matches(self, subject: str, final_review: bool) -> bool:
        from study_app.core.study_phase import phase_setting_key

        try:
            value = settings_audit_repository.get_setting(
                phase_setting_key(subject), {}, db_path=self.db_path
            )
        except Exception:
            return False
        expected = "final_review" if final_review else "regular"
        return isinstance(value, dict) and value.get("phase") == expected

    def archive(self, scope: str | dict[str, Any] | None = None) -> dict[str, Any]:
        state = self._load_state()
        subject_scope = self._validate_scope(state, scope, "归档学习计划")
        archive_active_study_plan(subject_scope, db_path=self.db_path)
        return {"archived": True, "updated": True, "scope": subject_scope}
