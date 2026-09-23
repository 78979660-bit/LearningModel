from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from learning_bkt import bkt_topic_states, topic_bkt_state
from learning_memory import topic_memory_state
from learning_monitor import (
    assessment_map,
    latest_outside_class_review_date,
    latest_record_date,
    load_json,
    load_records,
    parse_date,
    records_through,
    records_in_window,
    weighted_mastery,
    window_score,
)
from study_app.data.database import DEFAULT_DB_PATH, load_raw_records
from study_app.core.computation_context import DashboardComputationContext
from study_app.paths import MODEL_PATH, RECORDS_PATH


def normalize_learning_text(value: object) -> str:
    text = str(value or "").lower()
    drop_chars = set(" \t\r\n\u3000·/／、,，;；:：-—_()（）[]【】")
    return "".join(ch for ch in text if ch not in drop_chars)


@dataclass(frozen=True)
class SubjectSummary:
    name: str
    initial_score: float
    window_score: float | None
    has_window_records: bool
    mastery_score: int
    covered_mastery_score: int
    covered_topic_count: int
    total_topic_count: int
    latest_record: date | None
    latest_review: date | None
    warnings: tuple[str, ...]
    archived: bool = False
    subject_key: str | None = None
    catalog_revision: int | None = None


@dataclass(frozen=True)
class TodoItem:
    kind: str
    title: str
    detail: str
    level: str
    priority: float
    subject_id: str | None = None
    source_kind: str | None = None
    source_id: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class DashboardState:
    start: date
    today: date
    benchmark: float
    subjects: tuple[SubjectSummary, ...]
    todos: tuple[TodoItem, ...]
    memory_risks: tuple[dict, ...]
    bkt_alerts: tuple[dict, ...]
    weighted_priorities: tuple[dict, ...] = field(repr=False, default_factory=tuple)
    raw_records: tuple[dict, ...] = field(repr=False, default_factory=tuple)
    model_data: dict = field(repr=False, default_factory=dict)
    data_source: str = "SQLite"
    data_source_reason: str = ""
    catalog_revision: int | None = None
    catalog_diagnostic: str = ""

    @property
    def low_subjects(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.subjects if item.window_score is not None and item.window_score < self.benchmark)

    @property
    def stale_subjects(self) -> tuple[SubjectSummary, ...]:
        return tuple(item for item in self.subjects if any("未课外复习" in warning for warning in item.warnings))


def load_dashboard_state(
    model_path=MODEL_PATH,
    records_path=RECORDS_PATH,
    today=None,
    db_path=None,
) -> DashboardState:
    db_path = DEFAULT_DB_PATH if db_path is None else db_path
    today = parse_date(today or date.today())
    model = load_json(Path(model_path))
    from study_app.core.subject_catalog import (
        canonicalize_record_subjects,
        overlay_if_installed,
    )

    model, catalog_snapshot, catalog_diagnostic = overlay_if_installed(model, db_path)
    records, data_source, data_source_reason = load_records(
        db_path,
        records_path,
    )
    if catalog_snapshot is not None:
        records = canonicalize_record_subjects(records, catalog_snapshot)
    records = records_through(records, today)
    policy = model.get("warning_policy", {})
    from study_app.core.scoped_topic_cache import dashboard_topic_cache
    from study_app.core.study_phase import get_subject_phase

    if catalog_snapshot is None:
        subject_phases = {
            str(subject.get("name") or ""): get_subject_phase(
                str(subject.get("name") or ""), db_path=db_path
            )
            for subject in model.get("subjects", [])
            if subject.get("name")
        }
    else:
        subject_phases = {
            str(subject.get("name") or ""): {
                "phase": (
                    "archived"
                    if str(subject.get("lifecycle", {}).get("status") or "") == "archived"
                    else "regular"
                )
            }
            for subject in model.get("subjects", [])
            if subject.get("name")
        }
    computation_context = DashboardComputationContext(
        model, records, today, policy,
        shared_cache=dashboard_topic_cache,
        scope={"data_source": data_source, "model_path": str(model_path),
               "records_path": str(records_path)},
        subject_phases=subject_phases,
    )
    window_days = int(policy.get("period_days", 3))
    benchmark = float(policy.get("period_benchmark_score", 55))
    stale_days = int(policy.get("stale_review_days", 6))
    start = today - timedelta(days=window_days - 1)
    scores = assessment_map(model)
    from study_app.core.study_phase import is_in_exam_scope

    if catalog_snapshot is None:
        archived_subjects = {
            str(subject.get("name") or "")
            for subject in model.get("subjects", [])
            if subject_phases.get(str(subject.get("name") or ""), {}).get("phase")
            == "archived"
            or str(subject.get("lifecycle", {}).get("status") or "") == "archived"
        }
    else:
        archived_subjects = {
            str(subject.get("name") or "")
            for subject in model.get("subjects", [])
            if str(subject.get("lifecycle", {}).get("status") or "") == "archived"
        }

    subjects = []
    for subject in model.get("subjects", []):
        name = subject["name"]
        archived = name in archived_subjects
        subject_records = records_in_window(records, name, start, today)
        score = None if archived else window_score(subject_records, policy)
        latest = latest_record_date(records, name)
        latest_review = latest_outside_class_review_date(records, name)
        mastery_score = round(weighted_mastery(subject) * 100)
        covered_mastery, covered_count, total_count = covered_mastery_stats(
            subject,
            records,
            policy,
            as_of_date=today,
            computation_context=computation_context,
        )
        warnings = []
        if not archived and score is not None and score < benchmark:
            warnings.append(f"三天窗口分数低于标杆：{score:.1f} < {benchmark:.0f}")
        if not archived and latest_review and (today - latest_review).days >= stale_days:
            warnings.append(f"{(today - latest_review).days} 天未课外复习")

        subjects.append(
            SubjectSummary(
                name=name,
                initial_score=scores.get(name, {}).get("initial_score", mastery_score),
                window_score=score,
                has_window_records=bool(subject_records) and not archived,
                mastery_score=mastery_score,
                covered_mastery_score=covered_mastery,
                covered_topic_count=covered_count,
                total_topic_count=total_count,
                latest_record=latest,
                latest_review=latest_review,
                warnings=tuple(warnings),
                archived=archived,
                subject_key=subject.get("subject_key"),
                catalog_revision=(
                    catalog_snapshot.catalog_revision
                    if catalog_snapshot is not None
                    else None
                ),
            )
        )

    memory_risks = []
    for subject in model.get("subjects", []):
        if subject.get("name") in archived_subjects:
            continue
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                item = computation_context.memory_state(
                    subject["name"], module["name"], topic
                )
                if item["level"] != "none":
                    if not is_in_exam_scope(
                        subject["name"],
                        module.get("name"),
                        topic.get("name"),
                        phase=subject_phases.get(subject["name"]),
                    ):
                        continue
                    memory_risks.append(item)
    memory_risks.sort(key=lambda item: item["priority"], reverse=True)

    all_bkt_states = [
        item for item in computation_context.bkt_topic_states()
        if item.get("subject") not in archived_subjects
        and is_in_exam_scope(
            item.get("subject"),
            item.get("module"),
            item.get("topic"),
            phase=subject_phases.get(str(item.get("subject") or "")),
        )
    ]
    max_bkt = int(policy.get("bkt_model", {}).get("max_report_items", 8))
    bkt_alerts = [item for item in all_bkt_states if item["level"] != "none"][:max_bkt]

    from study_app.core.study_phase import weighted_topic_priority_states

    weighted_priorities = []
    for subject in model.get("subjects", []):
        if subject.get("name") in archived_subjects:
            continue
        weighted_priorities.extend(
            weighted_topic_priority_states(
                subject["name"],
                limit=6,
                model=model,
                records=records,
                states=all_bkt_states,
                as_of_date=today,
                computation_context=computation_context,
                phase=subject_phases.get(subject["name"]),
            )
        )
    weighted_priorities.sort(key=lambda item: float(item.get("weighted_priority", 0)), reverse=True)

    from study_app.core.plan_candidates import identify_model_topic

    todos = []
    for item in weighted_priorities[:8]:
        observations = int(item.get("observation_count") or 0)
        kind = "诊断" if observations == 0 else "做题"
        detail = (
            f"{item.get('phase_label', '当前模式')}加权优先级 {float(item.get('weighted_priority', 0)):.2f}；"
            f"掌握概率 {float(item.get('mastery_probability', 0)):.0%}，"
            f"回忆概率 {float(item.get('recall', 0)):.0%}；"
            f"{item.get('priority_reason', '')}"
        )
        source = identify_model_topic(
            model,
            str(item.get("subject") or ""),
            str(item.get("module") or ""),
            str(item.get("topic") or ""),
        )
        todos.append(
            TodoItem(
                kind=kind,
                title=f"{item['subject']} / {item['topic']}",
                detail=detail,
                level=item.get("level", "none"),
                priority=float(item.get("weighted_priority", 0)),
                subject_id=source.subject_id if source else None,
                source_kind=source.source_kind if source else None,
                source_id=source.source_id if source else None,
                task_id=source.task_id if source else None,
            )
        )

    todos.sort(key=lambda item: item.priority, reverse=True)
    return DashboardState(
        start=start,
        today=today,
        benchmark=benchmark,
        subjects=tuple(subjects),
        todos=tuple(todos[:6]),
        memory_risks=tuple(memory_risks),
        bkt_alerts=tuple(bkt_alerts),
        weighted_priorities=tuple(weighted_priorities),
        raw_records=tuple(records),
        model_data=model,
        data_source=data_source,
        data_source_reason=data_source_reason,
        catalog_revision=(
            catalog_snapshot.catalog_revision if catalog_snapshot is not None else None
        ),
        catalog_diagnostic=catalog_diagnostic,
    )


def covered_mastery_stats(
    subject: dict,
    records: list[dict],
    policy: dict | None = None,
    *,
    as_of_date: date | None = None,
    computation_context: DashboardComputationContext | None = None,
) -> tuple[int, int, int]:
    policy = policy or {}
    topics = []
    for module in subject.get("modules", []):
        module_name = str(module.get("name") or "")
        module_status = str(module.get("status") or "")
        module_topics = module.get("topics", []) or []
        if not module_topics:
            topics.append((module_name, module, module_status))
            continue
        for topic in module_topics:
            topics.append((module_name, topic, str(topic.get("status") or module_status)))

    total_count = len(topics)
    covered = [
        item
        for item in topics
        if is_covered_learning_item(subject["name"], item[0], item[1], item[2], records)
    ]
    if not covered:
        return round(weighted_mastery(subject) * 100), 0, total_count

    weighted_sum = 0.0
    weight_sum = 0.0
    for module_name, item, _status in covered:
        weight = float(item.get("importance", item.get("weight", 1)) or 1)
        mastery = float(item.get("mastery", 0) or 0)
        if computation_context is None:
            bkt_state = topic_bkt_state(
                subject["name"],
                module_name,
                item,
                records,
                policy,
                as_of_date=as_of_date,
            )
        else:
            bkt_state = computation_context.bkt_state(
                subject["name"], module_name, item
            )
        if bkt_state and bkt_state.get("observation_count", 0):
            mastery = float(bkt_state["mastery_probability"])
        weighted_sum += mastery * weight
        weight_sum += weight
    if weight_sum <= 0:
        return round(weighted_mastery(subject) * 100), len(covered), total_count
    return round(weighted_sum / weight_sum * 100), len(covered), total_count


def is_covered_learning_item(
    subject_name: str,
    module_name: str,
    item: dict,
    status: str,
    records: list[dict],
) -> bool:
    covered_statuses = {
        "learned",
        "learning",
        "current",
        "in_progress",
        "reviewing",
        "已学",
        "学习中",
        "当前",
    }
    if status in covered_statuses:
        return True
    topic_name = str(item.get("name") or module_name)
    evidence_terms = [term for term in {module_name, topic_name} if term]
    if not evidence_terms:
        return False
    for record in records:
        if record.get("subject") != subject_name:
            continue
        text = " ".join(
            str(record.get(key) or "")
            for key in ["module", "topic", "chapter", "note"]
        )
        related = record.get("related_topics") or []
        if isinstance(related, list):
            text += " " + " ".join(str(item) for item in related)
        normalized_text = normalize_learning_text(text)
        if any(normalize_learning_text(term) in normalized_text for term in evidence_terms):
            return True
    return False
