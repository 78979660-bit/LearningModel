from __future__ import annotations

from typing import TYPE_CHECKING

from study_app.ui.activity_view import activity_subject_names, activity_subjects


if TYPE_CHECKING:
    from study_app.core.dashboard import DashboardState


def matching_daily_summary_cache(
    cached: object,
    current_signature: str,
    subjects,
) -> tuple[dict, str] | None:
    if not isinstance(cached, dict) or cached.get("signature") != current_signature:
        return None
    summary = cached.get("summary")
    if not isinstance(summary, dict):
        return None
    from study_app.ai.daily_summary import validate_cached_daily_summary
    from study_app.ai.validation import LLMValidationError

    try:
        validate_cached_daily_summary(summary, subjects)
    except LLMValidationError:
        return None
    return summary, str(cached.get("mode") or "")


def daily_summary_cache_signature(state: DashboardState, records: list[dict]) -> str:
    import hashlib
    import json

    today = state.today.isoformat()
    active_names = set(activity_subject_names(state))
    active_records = [record for record in records if record.get("subject") in active_names]
    today_records = [record for record in active_records if str(record.get("date") or record.get("record_date")) == today]
    memory_risks = [item for item in state.memory_risks if item.get("subject") in active_names][:12]
    bkt_alerts = [item for item in state.bkt_alerts if item.get("subject") in active_names][:12]
    payload = {
        "version": "daily-summary-cache-v5-total-counts",
        "date": today,
        "record_count": len(active_records),
        "today_record_ids": [
            record.get("id") or record.get("created_at") or record.get("note")
            for record in today_records[-20:]
        ],
        "risk_counts": [len(state.memory_risks), len(state.bkt_alerts)],
        "memory_risks": [
            (item.get("subject"), item.get("topic"), round(float(item.get("recall", 0)), 4), item.get("last_review"))
            for item in memory_risks
        ],
        "bkt_alerts": [
            (
                item.get("subject"),
                item.get("topic"),
                round(float(item.get("mastery_probability", 0)), 4),
            )
            for item in bkt_alerts
        ],
        "subjects": [
            (
                subject.name,
                subject.window_score,
                subject.covered_mastery_score,
                subject.covered_topic_count,
                subject.total_topic_count,
                subject.mastery_score,
            )
            for subject in activity_subjects(state)
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
