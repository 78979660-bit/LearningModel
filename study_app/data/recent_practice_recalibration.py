from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from learning_bkt import iter_topic_observations, topic_bkt_state
from study_app.data.database import DEFAULT_DB_PATH, load_raw_records, set_setting
from study_app.data.model_progress_sync import (
    MODEL_PATH,
    recompute_module_mastery,
    recompute_subject_mastery,
    sync_topic_statuses_to_sqlite,
)
from study_app.paths import LOGS_DIR


SUBJECT = "\u9ad8\u7b49\u6570\u5b66"
AUDIT_PATH = LOGS_DIR / "recent_practice_recalibration_audit.json"
ALGORITHM_VERSION = "recent_practice_bkt_blend_v1"


def latest_practice_record_ids(records: list[dict[str, Any]], count: int = 5) -> list[int]:
    candidates = [
        record
        for record in records
        if record.get("subject") == SUBJECT
        and record.get("problems")
        and isinstance(record.get("id"), int)
    ]
    return [int(record["id"]) for record in candidates[-max(1, count) :]]


def recalibration_blend(state: dict[str, Any], recent_record_count: int) -> float:
    confidence = float(state.get("evidence_confidence", 0.0) or 0.0)
    record_bonus = min(0.10, max(0, recent_record_count - 1) * 0.04)
    return min(0.60, 0.22 + 0.48 * confidence + record_bonus)


def recalibrate_recent_practice(
    record_ids: list[int] | None = None,
    *,
    recent_count: int = 5,
    model_path: Path | str = MODEL_PATH,
    db_path: Path | str = DEFAULT_DB_PATH,
    apply: bool = False,
) -> dict[str, Any]:
    path = Path(model_path)
    model = json.loads(path.read_text(encoding="utf-8"))
    records = load_raw_records(db_path)
    selected_ids = sorted(set(record_ids or latest_practice_record_ids(records, recent_count)))
    selected_set = set(selected_ids)
    policy = model.get("warning_policy", {})
    changes: list[dict[str, Any]] = []
    changed_pairs: list[tuple[str, str]] = []
    maintenance_pairs: list[tuple[str, str]] = []

    for subject in model.get("subjects", []):
        if subject.get("name") != SUBJECT:
            continue
        for module in subject.get("modules", []):
            module_name = str(module.get("name") or "")
            for topic in module.get("topics", []):
                source = topic.get("source_json")
                if not isinstance(source, dict):
                    source = {}
                previous = source.get("recent_practice_recalibration")
                same_selection = (
                    previous.get("selection_record_ids") == selected_ids
                    if isinstance(previous, dict) and previous.get("selection_record_ids") is not None
                    else isinstance(previous, dict)
                    and set(previous.get("record_ids") or []).issubset(selected_set)
                )
                if (
                    isinstance(previous, dict)
                    and previous.get("version") == ALGORITHM_VERSION
                    and same_selection
                ):
                    if not isinstance(topic.get("mastery_prior"), (int, float)):
                        topic["mastery_prior"] = float(previous.get("old_mastery", topic.get("mastery", 0.0)) or 0.0)
                        maintenance_pairs.append((module_name, str(topic.get("name") or "")))
                    continue
                observations = iter_topic_observations(SUBJECT, str(topic.get("name") or ""), topic, records, policy)
                recent = [item for item in observations if item.get("record_id") in selected_set]
                if not recent:
                    continue
                state = topic_bkt_state(SUBJECT, module_name, topic, records, policy)
                if not state or not state.get("observation_count"):
                    continue

                old = float(topic.get("mastery", 0.0) or 0.0)
                target = float(state.get("mastery_probability", old) or old)
                recent_records = {item.get("record_id") for item in recent if item.get("record_id") is not None}
                blend = recalibration_blend(state, len(recent_records))
                raw_delta = (target - old) * blend
                delta = max(-0.12, min(0.18, raw_delta))
                new = max(0.0, min(1.0, old + delta))
                if abs(new - old) < 0.001:
                    continue

                recent_accuracy = sum(float(item.get("correctness", 0.0)) for item in recent) / len(recent)
                recent_difficulty = sum(float(item.get("difficulty_score", 55.0)) for item in recent) / len(recent)
                if not isinstance(topic.get("mastery_prior"), (int, float)):
                    topic["mastery_prior"] = round(old, 4)
                topic["mastery"] = round(new, 4)
                source["recent_practice_recalibration"] = {
                    "version": ALGORITHM_VERSION,
                    "selection_record_ids": selected_ids,
                    "record_ids": sorted(recent_records),
                    "old_mastery": round(old, 4),
                    "bkt_target": round(target, 4),
                    "new_mastery": round(new, 4),
                    "blend": round(blend, 4),
                    "recent_accuracy": round(recent_accuracy, 4),
                    "recent_difficulty": round(recent_difficulty, 1),
                    "observation_count": len(recent),
                }
                topic["source_json"] = source
                changed_pairs.append((module_name, str(topic.get("name") or "")))
                changes.append(
                    {
                        "module": module_name,
                        "topic": topic.get("name"),
                        "old_mastery": round(old * 100, 1),
                        "bkt_target": round(target * 100, 1),
                        "new_mastery": round(new * 100, 1),
                        "delta": round((new - old) * 100, 1),
                        "recent_accuracy": round(recent_accuracy * 100, 1),
                        "recent_difficulty": round(recent_difficulty, 1),
                        "recent_record_count": len(recent_records),
                        "observation_count": len(recent),
                    }
                )
            recompute_module_mastery(module)
        recompute_subject_mastery(subject)
        break

    audit = {
        "version": ALGORITHM_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "subject": SUBJECT,
        "record_ids": selected_ids,
        "applied": bool(apply),
        "changes": changes,
    }
    if apply and (changes or maintenance_pairs):
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
        sync_topic_statuses_to_sqlite(SUBJECT, changed_pairs + maintenance_pairs, model, db_path)
        if changes:
            set_setting("recent_practice_recalibration_audit", audit, db_path)
            AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=True, indent=2) + "\n", encoding="ascii")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-ids", nargs="*", type=int)
    parser.add_argument("--recent-count", type=int, default=5)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = recalibrate_recent_practice(
        args.record_ids,
        recent_count=args.recent_count,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
