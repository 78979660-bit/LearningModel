"""Compatibility adapter for the retired Tk dashboard widget.

The Windows release uses the PySide6 application.  Keeping this read-only
adapter preserves callers that consume the old widget's dashboard dictionary
without shipping a second GUI runtime or writing beside the executable.
"""

from __future__ import annotations

from datetime import date

from study_app.core.dashboard import load_dashboard_state
from study_app.paths import MODEL_PATH, RECORDS_PATH


def load_state(today: date | None = None) -> dict[str, object]:
    state = load_dashboard_state(
        MODEL_PATH,
        RECORDS_PATH,
        today=today or date.today(),
    )
    return {
        "today": state.today,
        "start": state.start,
        "benchmark": state.benchmark,
        "subjects": [
            {
                "name": item.name,
                "initial": item.initial_score,
                "score": item.window_score,
                "has_records": item.has_window_records,
                "mastery": item.mastery_score,
                "latest": item.latest_record,
                "latest_review": item.latest_review,
                "warnings": list(item.warnings),
                "archived": item.archived,
            }
            for item in state.subjects
        ],
        "low_subjects": list(state.low_subjects),
        "stale_subjects": [
            (item.name, (state.today - item.latest_review).days)
            for item in state.stale_subjects
            if item.latest_review is not None
        ],
        "memory_risks": list(state.memory_risks),
        "bkt_alerts": list(state.bkt_alerts),
        "todos": [
            {
                "kind": item.kind,
                "title": item.title,
                "detail": item.detail,
                "priority": item.priority,
                "level": item.level,
            }
            for item in state.todos
        ],
        "raw_records": list(state.raw_records),
        "data_source": state.data_source,
        "data_source_reason": state.data_source_reason,
    }
