from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from study_app.core.oj_attempts import normalize_attempted_at
from study_app.core.topic_identity import validate_topic_key


@dataclass(frozen=True)
class OJTopicEvidence:
    topic_key: str
    problem_key: str
    source_key: str
    external_problem_key: str
    title: str
    mapping_source: str
    attempt_id: int
    attempted_at: str
    result: str
    duration_seconds: int
    independence: str
    hint_level: str
    error_type: str
    notes: str


def load_oj_topic_evidence(
    topic_key: object,
    *,
    as_of_time: object = None,
    db_path: Path | str,
) -> tuple[OJTopicEvidence, ...]:
    from study_app.data import database

    valid_topic_key = validate_topic_key(topic_key)
    cutoff = None if as_of_time is None else normalize_attempted_at(as_of_time)
    rows = database.list_oj_evidence_rows(
        valid_topic_key,
        as_of_time=cutoff,
        db_path=db_path,
    )
    return tuple(OJTopicEvidence(**row) for row in rows)
