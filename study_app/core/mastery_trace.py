from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path
from typing import Any

from study_app.data.database import DEFAULT_DB_PATH, connect_readonly


TRACE_VERSION = "mastery-evidence-trace-v1"
SYNC_ALGORITHM_VERSION = "mastery-progress-sync-v1"


def record_fingerprint(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_data(topic: dict[str, Any]) -> dict[str, Any]:
    source = topic.get("source_json")
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError:
            return {}
    return source if isinstance(source, dict) else {}


def append_mastery_trace(topic: dict[str, Any], event: dict[str, Any]) -> None:
    source = _source_data(topic)
    history = source.get("mastery_contribution_trace_v1")
    if not isinstance(history, list):
        history = []
    history.append(event)
    source["mastery_contribution_trace_v1"] = history
    topic["source_json"] = source


def freeze_replay_baseline(
    topic: dict[str, Any], before: dict[str, Any], *,
    record_id: int | None, db_path: Path | str,
) -> None:
    """Freeze the pre-contribution state and exclude all older database evidence."""
    source = _source_data(topic)
    if isinstance(source.get("mastery_replay_baseline_v1"), dict):
        return
    with connect_readonly(db_path) as connection:
        prior_ids = [int(row[0]) for row in connection.execute(
            "SELECT id FROM learning_records WHERE id != ? ORDER BY id",
            (record_id if record_id is not None else -1,),
        )]
    initial_source = _source_data(before)
    initial_source.pop("mastery_contribution_trace_v1", None)
    initial_source.pop("mastery_replay_baseline_v1", None)
    source["mastery_replay_baseline_v1"] = {
        "mastery": before.get("mastery"),
        "status": before.get("status"),
        "forgetting_risk": before.get("forgetting_risk"),
        "source_json": copy.deepcopy(initial_source),
        "excluded_prior_record_ids": prior_ids,
        "prior_provenance": "unverified",
    }
    topic["source_json"] = source


class MasteryTraceReader:
    """A single read-only snapshot for one page load; never retained across loads."""

    def __init__(self, *, model_path: Path | str, db_path: Path | str = DEFAULT_DB_PATH):
        self.model_path, self.db_path = Path(model_path), db_path
        self._topics = None
        self._records = None
        self._fingerprints = {}

    def topic(self, subject_name, module_name, topic_name):
        if self._topics is None:
            model = json.loads(self.model_path.read_text(encoding="utf-8"))
            self._topics = {}
            seen_subjects = set()
            for subject in model.get("subjects", []):
                if subject.get("name") in seen_subjects:
                    continue
                seen_subjects.add(subject.get("name"))
                seen_modules = set()
                for module in subject.get("modules", []):
                    if module.get("name") in seen_modules:
                        continue
                    seen_modules.add(module.get("name"))
                    for topic in module.get("topics", []):
                        # Preserve first-match behavior of the individual reader.
                        self._topics.setdefault(
                            (subject.get("name"), module.get("name"), topic.get("name")), topic)
        key = (subject_name, module_name, topic_name)
        if key not in self._topics:
            raise LookupError(f"未找到知识点：{subject_name}/{module_name}/{topic_name}")
        return self._topics[key]

    def record_status(self, record_id, fingerprint):
        if self._records is None:
            self._records = {}
            with connect_readonly(self.db_path) as connection:
                for row in connection.execute("SELECT id, raw_json FROM learning_records"):
                    try:
                        record = json.loads(row["raw_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(record, dict):
                        record["id"] = row["id"]
                        self._records[row["id"]] = record
        record = self._records.get(record_id)
        if record is None:
            return "missing"
        if record_id not in self._fingerprints:
            self._fingerprints[record_id] = record_fingerprint(record)
        return ("verified" if self._fingerprints[record_id] == fingerprint
                else "changed_or_unverifiable")

    def trace(self, subject_name, module_name, topic_name):
        return list_topic_mastery_trace(
            subject_name, module_name, topic_name,
            model_path=self.model_path, db_path=self.db_path, reader=self)


def list_topic_mastery_trace(
    subject_name: str, module_name: str, topic_name: str,
    *, model_path: Path | str, db_path: Path | str = DEFAULT_DB_PATH,
    reader: MasteryTraceReader | None = None,
) -> dict[str, Any]:
    """Read-only contribution diagnosis; missing legacy provenance remains unknown."""
    reader = reader or MasteryTraceReader(model_path=model_path, db_path=db_path)
    topic = reader.topic(subject_name, module_name, topic_name)

    source = _source_data(topic)
    history = source.get("mastery_contribution_trace_v1")
    if not isinstance(history, list):
        history = []
    contributions = []
    for event in history:
        if not isinstance(event, dict):
            continue
        item = json.loads(json.dumps(event, ensure_ascii=False, default=str))
        item["record_status"] = reader.record_status(
            item.get("record_id"), item.get("record_fingerprint"))
        contributions.append(item)

    legacy_update = source.get("last_mastery_update") or source.get(
        "diagnostic_baseline_update"
    )
    legacy_gap = bool(legacy_update) and not contributions
    if contributions and isinstance(legacy_update, dict):
        legacy_gap = legacy_update.get("record_id") != contributions[-1].get("record_id")
    return {
        "subject": subject_name,
        "module": module_name,
        "topic": topic_name,
        "current_mastery": topic.get("mastery"),
        "contributions": contributions,
        "legacy_provenance_gap": legacy_gap,
        "coverage": "partial_legacy" if legacy_gap else (
            "traced" if contributions else "no_recorded_contribution"
        ),
        "contribution_chain_scope": "since_trace_v1",
        "prior_baseline_status": "unverified" if contributions else "unknown",
        "unverified_prior_mastery": (
            contributions[0].get("old_mastery") if contributions else topic.get("mastery")
        ),
    }
