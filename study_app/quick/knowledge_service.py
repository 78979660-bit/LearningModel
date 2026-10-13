"""Path-injected QML adapter for the real knowledge graph and alert workflow.

The adapter builds read-only graph, topic, explanation, and alert DTOs from the
existing core projections. Alert actions delegate to the existing atomic core
transitions and require the alert revision last shown to the user to still be
current.
"""
from __future__ import annotations

from datetime import date, datetime
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping
import unicodedata

from study_app.core.async_tasks import (
    StaleSubjectTaskError,
    capture_catalog_revision,
    revalidate_catalog_revision,
)
from study_app.core.computation_context import DashboardComputationContext
from study_app.core.dashboard import DashboardState, load_dashboard_state
from study_app.core.chapter_graph_projection import (
    build_chapter_graph_snapshot,
    list_graph_subjects,
)
from study_app.core.knowledge_alerts import KnowledgeAlert, handle_alert, snooze_alert
from study_app.core.knowledge_explanations import (
    KnowledgeExplanation,
    explain_knowledge_topic,
)
from study_app.core.knowledge_states import ClassifiedTopic, classify_topics
from study_app.core.mastery_trace import MasteryTraceReader, list_topic_mastery_trace
from study_app.core.subject_catalog import CatalogSnapshot, load_catalog_snapshot
from study_app.core.topic_insights import build_topic_insights
from study_app.data import database
from study_app.data.alert_repository import _require_knowledge_alert_tables_from_connection
from study_app.data.db_runtime import DatabaseNotInitializedError


class StaleKnowledgeAlertError(StaleSubjectTaskError):
    """The alert changed after it was loaded for the user's confirmation."""


_ALERT_STATUSES = {"active", "snoozed", "handled", "resolved"}
_ALERT_STATUS_ORDER = {"active": 0, "snoozed": 1, "handled": 2, "resolved": 3}


def _coerce_today(value: date | str | None) -> date | str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise TypeError("today 必须是 date、YYYY-MM-DD 字符串或 None")


def _unpack_paths(value: object) -> tuple[object, object, object]:
    if isinstance(value, Mapping):
        candidates = (
            ("db_path", "model_path", "records_path"),
            ("database_path", "model_path", "records_path"),
        )
        for keys in candidates:
            if all(key in value for key in keys):
                return tuple(value[key] for key in keys)  # type: ignore[return-value]
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return value[0], value[1], value[2]
    if all(hasattr(value, name) for name in ("db_path", "model_path", "records_path")):
        return value.db_path, value.model_path, value.records_path
    raise TypeError("paths 必须提供 db_path、model_path 和 records_path")


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _json_safe(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _explanation_dto(explanation: KnowledgeExplanation | None) -> dict[str, Any] | None:
    if explanation is None:
        return None
    return {
        "version": explanation.explanation_version,
        "topicKey": explanation.topic_key,
        "topicPath": list(explanation.topic_path),
        "asOfDate": explanation.as_of_date.isoformat(),
        "layers": [
            {
                "name": layer.name,
                "facts": [
                    {
                        "code": fact.code,
                        "label": fact.label,
                        "value": fact.value,
                        "sourceReference": fact.source_reference,
                        "status": fact.status,
                    }
                    for fact in layer.facts
                ],
            }
            for layer in explanation.layers
        ],
        "diagnostics": list(explanation.diagnostics),
    }


def _event_dto(event) -> dict[str, Any]:
    return {
        "id": event.id,
        "alertId": event.alert_id,
        "eventType": event.event_type,
        "fromStatus": event.from_status,
        "toStatus": event.to_status,
        "effectiveDate": event.effective_date.isoformat(),
        "actor": event.actor,
        "detail": _json_safe(event.detail),
        "createdAt": event.created_at,
    }


def _alert_revision_from_row(row: sqlite3.Row, event_id: int | None) -> tuple[object, ...]:
    return (
        row["status"],
        row["fingerprint"],
        row["rule_version"],
        row["evidence_version"],
        row["updated_at"],
        row["snoozed_until"],
        row["handled_at"],
        row["resolved_at"],
        row["as_of_date"],
        event_id,
    )


def _alert_revision(alert: KnowledgeAlert, events: tuple) -> tuple[object, ...]:
    return (
        alert.status,
        alert.fingerprint,
        alert.rule_version,
        alert.evidence_version,
        alert.updated_at,
        _date_text(alert.snoozed_until),
        alert.handled_at,
        alert.resolved_at,
        _date_text(alert.as_of_date),
        events[-1].id if events else None,
    )


def _normalize_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _subject_topic_index(catalog: CatalogSnapshot | None) -> tuple[dict[str, tuple[str, str]], dict[str, object]]:
    topic_locations: dict[str, tuple[str, str]] = {}
    subjects: dict[str, object] = {}
    if catalog is None:
        return topic_locations, subjects
    for subject in catalog.subjects:
        subjects[subject.subject_key] = subject
        for module in subject.modules:
            for topic in module.topics:
                topic_locations[topic.topic_key] = (subject.subject_key, module.module_key)
    return topic_locations, subjects


class KnowledgeService:
    """Read real chapter/topic evidence and perform confirmed alert actions.

    The normal form matches the other QML services:
    ``KnowledgeService(db_path, model_path, records_path, today=None)``.
    A path bundle with those three named fields is also accepted for callers
    that already carry a path object. Construction stores paths only; disk and
    database access happens in ``load`` or a requested alert action.
    """

    def __init__(
        self,
        db_path: str | Path | object,
        model_path: str | Path | date | None = None,
        records_path: str | Path | None = None,
        today: date | str | None = None,
    ) -> None:
        if records_path is None and (
            isinstance(db_path, Mapping)
            or isinstance(db_path, (tuple, list))
            or all(hasattr(db_path, name) for name in ("db_path", "model_path", "records_path"))
        ):
            if model_path is not None:
                if today is not None:
                    raise TypeError("today 只能传入一次")
                today = model_path if isinstance(model_path, (date, str)) else None
            db_path, model_path, records_path = _unpack_paths(db_path)
        if model_path is None or records_path is None:
            raise TypeError("db_path、model_path 和 records_path 都是必需路径")
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        self.today = _coerce_today(today)
        self._last_as_of_date: date | None = None
        self._seen_alert_revisions: dict[int, tuple[object, ...]] = {}
        self._seen_alert_subject_keys: dict[int, str] = {}
        self._seen_subject_versions: dict[str, tuple[object, ...]] = {}

    def _load_state(self, context_sink=None) -> DashboardState:
        return load_dashboard_state(
            model_path=self.model_path,
            records_path=self.records_path,
            db_path=self.db_path,
            today=self.today,
            **({"context_sink": context_sink} if context_sink is not None else {}),
        )

    @staticmethod
    def _classified_topics(state: DashboardState, db_path: Path,
                           context=None) -> tuple[ClassifiedTopic, ...]:
        try:
            identities = database.list_topic_identities(db_path=db_path)
            prerequisites = database.list_prerequisite_edges(db_path=db_path)
        except (DatabaseNotInitializedError, sqlite3.Error):
            return ()
        model = state.model_data
        records = list(state.raw_records)
        # Classification uses the exact model/records/date just computed above.
        # Keep the cache local to this read so writes and date changes refresh it.
        context = context or DashboardComputationContext(
            model, records, state.today, model.get("warning_policy", {})
        )
        insights = build_topic_insights(
            model,
            records,
            state.today,
            context,
            identities,
            prerequisites,
        )
        return classify_topics(insights, include_archived=True)

    def _trace(self, item: ClassifiedTopic, reader=None) -> dict[str, Any] | None:
        insight = item.insight
        try:
            if reader is not None:
                return reader.trace(insight.subject_name, insight.module_name, insight.topic_name)
            return list_topic_mastery_trace(
                insight.subject_name,
                insight.module_name,
                insight.topic_name,
                model_path=self.model_path,
                db_path=self.db_path,
            )
        except (DatabaseNotInitializedError, LookupError, OSError, ValueError, sqlite3.Error):
            return None

    @staticmethod
    def _topic_dto(
        item: ClassifiedTopic,
        explanation: KnowledgeExplanation,
    ) -> dict[str, Any]:
        insight = item.insight
        recent_error = insight.recent_error
        return {
            "key": insight.topic_key,
            "label": insight.topic_name,
            "subject": insight.subject_name,
            "module": insight.module_name,
            "state": item.primary_state,
            "stateLabel": item.primary_state.replace("_", " "),
            "reasonCodes": list(item.reason_codes),
            "secondaryFlags": list(item.secondary_flags),
            "recommendedAction": item.recommended_action,
            "recommendedDate": _date_text(item.effective_recommended_date),
            "masteryPoint": insight.mastery_point,
            "masteryInterval": (
                None if insight.mastery_interval is None else list(insight.mastery_interval)
            ),
            "masterySource": insight.mastery_source,
            "evidenceConfidence": insight.evidence_confidence,
            "observationCount": insight.observation_count,
            "exerciseCount": insight.exercise_count,
            "evidenceVersion": insight.evidence_version,
            "lastObservationDate": _date_text(insight.last_observation_date),
            "lastObservationCorrectness": insight.last_observation_correctness,
            "lastReviewDate": _date_text(insight.last_review_date),
            "recallProbability": insight.recall_probability,
            "targetRecall": insight.target_recall,
            "directPrerequisiteKeys": list(insight.direct_prerequisite_keys),
            "unresolvedPrerequisiteKeys": list(insight.unresolved_prerequisite_keys),
            "recentError": (
                None
                if recent_error is None
                else {
                    "recordId": recent_error.record_id,
                    "date": recent_error.occurred_on.isoformat(),
                    "title": recent_error.title,
                    "correctness": recent_error.correctness,
                    "resultSource": recent_error.result_source,
                    "cause": recent_error.error_cause,
                }
            ),
            "explanation": _explanation_dto(explanation),
        }

    @staticmethod
    def _graph_dto(snapshot, subject_label: str) -> dict[str, Any]:
        grouped: dict[int, list[object]] = {}
        for node in snapshot.nodes:
            grouped.setdefault(node.topological_rank, []).append(node)
        positions: dict[str, dict[str, int]] = {}
        for rank, nodes in grouped.items():
            nodes.sort(key=lambda item: (item.module_order, _normalize_name(item.display_name), item.module_key))
            for index, node in enumerate(nodes):
                positions[node.module_key] = {"x": 42 + int(rank) * 290, "y": 40 + index * 152}
        max_rank = max(grouped, default=0)
        max_rows = max((len(nodes) for nodes in grouped.values()), default=0)
        nodes = [
            {
                "key": node.module_key,
                "label": node.display_name,
                "rank": node.topological_rank,
                "order": node.module_order,
                "state": node.learning_state,
                "masteryPoint": node.mastery_point,
                "masteryInterval": (
                    None if node.mastery_interval is None else list(node.mastery_interval)
                ),
                "coverageCount": node.coverage_count,
                "topicCount": node.topic_count,
                "evidenceConfidence": node.evidence_confidence,
                "diagnostics": list(node.diagnostics),
                "position": positions[node.module_key],
            }
            for node in snapshot.nodes
        ]
        edges = [
            {
                "from": edge.from_module_key,
                "to": edge.to_module_key,
                "relationType": edge.relation_type,
                "source": edge.source,
                "evidenceRefs": list(edge.evidence_refs),
                "diagnostics": list(edge.diagnostics),
            }
            for edge in snapshot.edges
        ]
        return {
            "subjectKey": snapshot.subject_key,
            "subjectLabel": subject_label,
            "schemaVersion": snapshot.schema_version,
            "structureVersion": snapshot.structure_version,
            "asOfDate": snapshot.as_of_date,
            "nodes": nodes,
            "edges": edges,
            "details": {},
            "diagnostics": list(snapshot.diagnostics),
            "width": 84 + max_rank * 290 + 226,
            "height": 80 + max_rows * 152,
        }

    @staticmethod
    def _alert_dto(alert: KnowledgeAlert, events: tuple, explanation=None) -> dict[str, Any]:
        topic = alert.snapshot.get("topic", {})
        if not isinstance(topic, dict):
            topic = {}
        evidence = alert.snapshot.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        mastery = alert.snapshot.get("mastery", {})
        if not isinstance(mastery, dict):
            mastery = {}
        memory = alert.snapshot.get("memory", {})
        if not isinstance(memory, dict):
            memory = {}
        return {
            "id": alert.id,
            "key": alert.topic_key,
            "topicKey": alert.topic_key,
            "subject": str(topic.get("subject_name") or "未知学科"),
            "module": str(topic.get("module_name") or "未知模块"),
            "topic": str(topic.get("topic_name") or alert.topic_key),
            "path": " / ".join(
                str(topic.get(key) or fallback)
                for key, fallback in (
                    ("subject_name", "未知学科"),
                    ("module_name", "未知模块"),
                    ("topic_name", alert.topic_key),
                )
            ),
            "type": alert.alert_type,
            "status": alert.status,
            "asOfDate": alert.as_of_date.isoformat(),
            "recommendedDate": _date_text(alert.recommended_date),
            "priority": alert.priority,
            "ruleVersion": alert.rule_version,
            "evidenceVersion": alert.evidence_version,
            "fingerprint": alert.fingerprint,
            "updatedAt": alert.updated_at,
            "snoozedUntil": _date_text(alert.snoozed_until),
            "handledAt": alert.handled_at,
            "resolvedAt": alert.resolved_at,
            "createdAt": alert.created_at,
            "snapshot": _json_safe(alert.snapshot),
            "evidence": {
                "observationCount": evidence.get("observation_count"),
                "exerciseCount": evidence.get("exercise_count"),
                "confidence": evidence.get("confidence"),
                "recentError": _json_safe(
                    evidence.get("recent_error")
                ),
                "masteryPoint": mastery.get("point"),
                "masteryInterval": _json_safe(mastery.get("interval")),
                "recallProbability": memory.get("recall_probability"),
                "targetRecall": memory.get("target_recall"),
            },
            "events": [_event_dto(event) for event in events],
            "explanation": _explanation_dto(explanation),
        }

    @staticmethod
    def _alert_subject_key(
        alert: KnowledgeAlert,
        topic_locations: Mapping[str, tuple[str, str]],
        selected_subject,
    ) -> str | None:
        location = topic_locations.get(alert.topic_key)
        if location is not None:
            return location[0]
        topic = alert.snapshot.get("topic", {})
        name = _normalize_name(topic.get("subject_name") if isinstance(topic, dict) else "")
        if selected_subject is not None:
            candidates = {
                selected_subject.canonical_name,
                selected_subject.display_name,
                *selected_subject.aliases,
            }
            if name and name in {_normalize_name(value) for value in candidates}:
                return selected_subject.subject_key
        return None

    def _alerts_dto(
        self,
        classified_by_key: Mapping[str, ClassifiedTopic],
        topic_locations: Mapping[str, tuple[str, str]],
        selected_subject,
        trace_reader=None,
    ) -> tuple[list[dict[str, Any]], dict[int, tuple[object, ...]], dict[int, str]]:
        try:
            alerts = database.list_knowledge_alerts(db_path=self.db_path)
            all_events = database.list_knowledge_alert_events(db_path=self.db_path)
        except (DatabaseNotInitializedError, sqlite3.Error):
            return [], {}, {}
        events_by_alert: dict[int, list] = {}
        for event in all_events:
            events_by_alert.setdefault(event.alert_id, []).append(event)

        visible = [
            alert
            for alert in alerts
            if selected_subject is None
            or self._alert_subject_key(alert, topic_locations, selected_subject)
            == selected_subject.subject_key
        ]
        visible.sort(
            key=lambda item: (
                _ALERT_STATUS_ORDER.get(item.status, 9),
                -float(item.priority),
                item.id,
            )
        )
        seen_revisions: dict[int, tuple[object, ...]] = {}
        seen_subject_keys: dict[int, str] = {}
        result = []
        for alert in visible:
            events = tuple(events_by_alert.get(alert.id, ()))
            seen_revisions[alert.id] = _alert_revision(alert, events)
            alert_subject_key = self._alert_subject_key(
                alert, topic_locations, selected_subject
            )
            if alert_subject_key:
                seen_subject_keys[alert.id] = alert_subject_key
            classified = classified_by_key.get(alert.topic_key)
            compatible = (
                classified is not None
                and alert.rule_version == classified.rule_version
                and alert.evidence_version == classified.insight.evidence_version
                and alert.alert_type == classified.primary_state
                and alert.as_of_date == classified.insight.as_of_date
            )
            explanation = None
            if compatible and classified is not None:
                explanation = explain_knowledge_topic(
                    classified,
                    classified_by_key,
                    self._trace(classified, trace_reader),
                    alert=alert,
                    alert_events=events,
                )
            result.append(self._alert_dto(alert, events, explanation))
        return result, seen_revisions, seen_subject_keys

    def load(self, subject_key: str = "") -> dict[str, Any]:
        """Return a JSON-compatible snapshot built from current local data."""
        if isinstance(subject_key, Mapping):
            subject_key = str(subject_key.get("subjectKey") or "")
        if not isinstance(subject_key, str):
            raise ValueError("subject_key 必须是文本")
        requested_key = subject_key.strip()
        catalog_token = capture_catalog_revision(self.db_path)
        contexts = []
        state = self._load_state(contexts.append)
        self._last_as_of_date = state.today

        subject_rows = list_graph_subjects(self.db_path, include_archived=False)
        subjects = [
            {
                "key": item["subject_key"],
                "label": item["display_name"],
                "hasCurrentStructure": bool(item["has_current_structure"]),
            }
            for item in subject_rows
        ]
        valid_keys = {item["key"] for item in subjects}
        if requested_key and requested_key not in valid_keys:
            raise ValueError("所选学科已不在活动目录中，请刷新后重试")
        default_subject = next(
            (item for item in subjects if item["hasCurrentStructure"]),
            subjects[0] if subjects else None,
        )
        selected_key = requested_key or (default_subject["key"] if default_subject else "")
        selected_row = next((item for item in subject_rows if item["subject_key"] == selected_key), None)
        selected_label = selected_row["display_name"] if selected_row else ""

        graph = {
            "subjectKey": selected_key,
            "subjectLabel": selected_label,
            "schemaVersion": "chapter-graph-v1",
            "structureVersion": None,
            "asOfDate": state.today.isoformat(),
            "nodes": [],
            "edges": [],
            "details": {},
            "diagnostics": [],
            "width": 310,
            "height": 112,
        }
        if selected_key:
            snapshot = build_chapter_graph_snapshot(
                selected_key,
                db_path=self.db_path,
                as_of_date=state.today.isoformat(),
                include_archived=False,
            )
            graph = self._graph_dto(snapshot, selected_label)

        try:
            catalog = load_catalog_snapshot(self.db_path)
        except (DatabaseNotInitializedError, sqlite3.Error, RuntimeError):
            catalog = None
        topic_locations, subjects_by_key = _subject_topic_index(catalog)
        subject_versions = {
            key: (
                subject.lifecycle_status,
                subject.object_version,
                subject.structure_version,
            )
            for key, subject in subjects_by_key.items()
        }
        classified = self._classified_topics(state, self.db_path, contexts[0] if contexts else None)
        classified_by_key = {item.insight.topic_key: item for item in classified}
        trace_reader = MasteryTraceReader(model_path=self.model_path, db_path=self.db_path)

        selected_catalog_subject = (
            subjects_by_key.get(selected_key) if selected_key else None
        )
        details: dict[str, Any] = {}
        if selected_catalog_subject is not None:
            for module in selected_catalog_subject.modules:
                topics = []
                for topic in module.topics:
                    item = classified_by_key.get(topic.topic_key)
                    if item is None:
                        continue
                    explanation = explain_knowledge_topic(
                        item,
                        classified_by_key,
                        self._trace(item, trace_reader),
                    )
                    topics.append(self._topic_dto(item, explanation))
                details[module.module_key] = {
                    "key": module.module_key,
                    "label": module.display_name,
                    "topics": topics,
                }
        graph["details"] = details

        alerts, seen_revisions, seen_subject_keys = self._alerts_dto(
            classified_by_key, topic_locations, selected_catalog_subject, trace_reader
        )
        revalidate_catalog_revision(catalog_token)
        self._seen_alert_revisions = seen_revisions
        self._seen_alert_subject_keys = seen_subject_keys
        self._seen_subject_versions = subject_versions
        return {
            "asOfDate": state.today.isoformat(),
            "subjects": subjects,
            "selectedSubjectKey": selected_key,
            "graph": graph,
            "alerts": alerts,
            "alertStatuses": sorted(_ALERT_STATUSES),
        }

    def _action_date(self) -> date:
        if self._last_as_of_date is not None:
            return self._last_as_of_date
        if isinstance(self.today, date) and not isinstance(self.today, datetime):
            return self.today
        if isinstance(self.today, str):
            return date.fromisoformat(database.validate_calendar_date(self.today, label="today"))
        return date.today()

    @staticmethod
    def _validate_alert_id(alert_id: int) -> int:
        if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
            raise ValueError("alert_id 必须是正整数")
        return alert_id

    def _perform_alert_action(self, alert_id: int, callback):
        expected = self._seen_alert_revisions.get(alert_id)
        if expected is None:
            raise StaleKnowledgeAlertError("请先刷新预警列表，再确认这项操作。")
        with database.connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _require_knowledge_alert_tables_from_connection(connection)
                row = connection.execute(
                    "SELECT * FROM knowledge_alerts WHERE id = ?", (alert_id,)
                ).fetchone()
                if row is None:
                    raise LookupError(f"未找到知识预警实例：{alert_id}")
                event_row = connection.execute(
                    "SELECT id FROM knowledge_alert_events WHERE alert_id = ? ORDER BY id DESC LIMIT 1",
                    (alert_id,),
                ).fetchone()
                current_revision = _alert_revision_from_row(
                    row, None if event_row is None else int(event_row["id"])
                )
                if current_revision != expected:
                    raise StaleKnowledgeAlertError("预警状态或证据已变化，请刷新后重新确认。")
                subject_key = self._seen_alert_subject_keys.get(alert_id)
                expected_subject = (
                    None
                    if subject_key is None
                    else self._seen_subject_versions.get(subject_key)
                )
                if expected_subject is not None:
                    current_subject = connection.execute(
                        """
                        SELECT catalog.lifecycle_status, catalog.object_version,
                               versions.structure_version
                        FROM subject_catalog AS catalog
                        LEFT JOIN subject_structure_versions AS versions
                          ON versions.subject_key = catalog.subject_key
                         AND versions.is_current = 1
                        WHERE catalog.subject_key = ?
                        ORDER BY versions.structure_version
                        LIMIT 1
                        """,
                        (subject_key,),
                    ).fetchone()
                    actual_subject = (
                        None
                        if current_subject is None
                        else (
                            current_subject["lifecycle_status"],
                            int(current_subject["object_version"]),
                            current_subject["structure_version"],
                        )
                    )
                    if actual_subject != expected_subject or actual_subject[0] != "active":
                        raise StaleKnowledgeAlertError(
                            "学科状态或结构已变化，请刷新后重新确认预警操作。"
                        )
                saved = callback(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        # A successful commit is authoritative. Follow-up refresh is the
        # bridge's separate best-effort step, so it cannot make this save look
        # unsuccessful if a later read fails.
        self._seen_alert_revisions.pop(alert_id, None)
        self._seen_alert_subject_keys.pop(alert_id, None)
        return saved

    @staticmethod
    def _saved_alert_result(alert: KnowledgeAlert) -> dict[str, Any]:
        return {
            "saved": True,
            "id": alert.id,
            "status": alert.status,
            "asOfDate": alert.as_of_date.isoformat(),
            "snoozedUntil": _date_text(alert.snoozed_until),
            "handledAt": alert.handled_at,
            "updatedAt": alert.updated_at,
        }

    def handle(self, alert_id: int) -> dict[str, Any]:
        """Mark the latest observed active alert handled, with its core audit event."""
        valid_id = self._validate_alert_id(alert_id)
        effective_date = self._action_date()
        alert = self._perform_alert_action(
            valid_id,
            lambda connection: handle_alert(
                valid_id,
                effective_date,
                connection,
                actor="desktop_local_user",
            ),
        )
        return self._saved_alert_result(alert)

    def snooze(self, alert_id: int, snoozed_until: str) -> dict[str, Any]:
        """Snooze an observed alert through the existing atomic core transition."""
        valid_id = self._validate_alert_id(alert_id)
        date_text = database.validate_calendar_date(snoozed_until, label="snoozed_until")
        as_of_date = self._action_date()
        target_date = date.fromisoformat(date_text)
        alert = self._perform_alert_action(
            valid_id,
            lambda connection: snooze_alert(
                valid_id,
                target_date,
                as_of_date,
                connection,
                actor="desktop_local_user",
            ),
        )
        return self._saved_alert_result(alert)
