"""Frozen BKT priors and answer forecasts from a dated model snapshot.

This is an evaluation path. It does not alter the live BKT or model file.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date
from statistics import mean
from typing import Any

from learning_bkt import (
    bkt_params,
    iter_topic_observations,
    parse_date,
    status_prior,
    time_decay_mastery,
    topic_bkt_state,
    topic_prior,
)


VERSION = "frozen_prior_time_backtest_v1"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def freeze_prior_catalog(model: dict[str, Any], *, snapshot_id: str, snapshot_date: date | str) -> dict[str, Any]:
    """Capture the prior used by the existing BKT from a point-in-time snapshot.

    A missing explicit mastery_prior uses the snapshot's mastery, never a later
    mutable model. Its older evidence provenance is stated rather than invented.
    """
    as_of = parse_date(snapshot_date)
    if not snapshot_id or as_of is None:
        raise ValueError("snapshot_id and a valid snapshot_date are required")
    policy = model.get("warning_policy", {})
    if not policy.get("bkt_model", {}).get("enabled", False):
        raise ValueError("snapshot BKT is disabled")
    topics: dict[str, dict[str, Any]] = {}
    for subject in model.get("subjects", []):
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                key = (subject["name"], module["name"], topic["name"])
                serialized_key = json.dumps(key, ensure_ascii=False)
                if serialized_key in topics:
                    raise ValueError(f"duplicate topic: {key}")
                explicit = topic.get("mastery_prior")
                snapshot_mastery = topic.get("mastery")
                if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
                    source = "snapshot_explicit_mastery_prior"
                    source_value = float(explicit)
                elif isinstance(snapshot_mastery, (int, float)) and not isinstance(snapshot_mastery, bool):
                    source = "snapshot_mastery_older_provenance_unverified"
                    source_value = float(snapshot_mastery)
                else:
                    source = "snapshot_status_fallback"
                    source_value = status_prior(topic.get("status"))
                if not math.isfinite(source_value) or not 0 <= source_value <= 1:
                    raise ValueError(f"invalid frozen prior: {key}")
                frozen_topic = dict(topic)
                frozen_topic["mastery_prior"] = source_value
                topics[serialized_key] = {
                    "source": source,
                    "source_value": source_value,
                    "status_at_snapshot": topic.get("status"),
                    "effective_prior": topic_prior(frozen_topic, policy),
                    "display_mastery_at_snapshot": snapshot_mastery,
                    "topic": frozen_topic,
                }
    return {
        "version": VERSION,
        "snapshot_id": snapshot_id,
        "snapshot_date": as_of.isoformat(),
        "snapshot_sha256": _digest(model),
        "model_version": str(model.get("model_name") or "unknown"),
        "topics": topics,
    }


def _metrics(events: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    if not events:
        return {"count": 0, "record_batch_count": 0, "brier": None, "log_loss": None,
                "ece_5_bins": None, "brier_record_bootstrap_95": None, "calibration_bins": []}
    brier_values = [(item["forecast"] - item["outcome"]) ** 2 for item in events]
    log_values = [
        -(item["outcome"] * math.log(max(item["forecast"], 1e-12))
          + (1 - item["outcome"]) * math.log(max(1 - item["forecast"], 1e-12)))
        for item in events
    ]
    bins: list[list[dict[str, Any]]] = [[] for _ in range(5)]
    for item in events:
        bins[min(4, int(item["forecast"] * 5))].append(item)
    ece = sum(
        len(bucket) / len(events) * abs(mean(item["forecast"] for item in bucket) - mean(item["outcome"] for item in bucket))
        for bucket in bins if bucket
    )
    groups: dict[Any, list[float]] = {}
    for item, loss in zip(events, brier_values):
        groups.setdefault(item["record_id"], []).append(loss)
    group_values = list(groups.values())
    rng = random.Random(seed)
    boot = sorted(
        mean(loss for group in (rng.choice(group_values) for _ in group_values) for loss in group)
        for _ in range(200)
    )
    return {
        "count": len(events),
        "record_batch_count": len(groups),
        "brier": mean(brier_values),
        "log_loss": mean(log_values),
        "ece_5_bins": ece,
        "brier_record_bootstrap_95": [boot[5], boot[194]],
        "calibration_bins": [
            {"count": len(bucket), "mean_forecast": mean(item["forecast"] for item in bucket),
             "observed_rate": mean(item["outcome"] for item in bucket)}
            for bucket in bins if bucket
        ],
    }


def time_backtest(
    model_snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    snapshot_id: str,
    snapshot_date: date | str,
    test_from: date | str,
    seed: int = 0,
) -> dict[str, Any]:
    """Predict each next binary answer using only strictly earlier record dates.

    Records on the same day form one batch: none of their outcomes can update
    another forecast from that day. Only records after the model snapshot enter
    training or testing, preventing the snapshot's old evidence from being reused.
    Item difficulty is the frozen topic difficulty, because historical item
    difficulty provenance cannot prove that it was known before the answer.
    """
    catalog = freeze_prior_catalog(model_snapshot, snapshot_id=snapshot_id, snapshot_date=snapshot_date)
    snapshot_cutoff = parse_date(snapshot_date)
    test_cutoff = parse_date(test_from)
    if test_cutoff is None or test_cutoff <= snapshot_cutoff:
        raise ValueError("test_from must be later than snapshot_date")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    dated = []
    for record in records:
        record_date = parse_date(record.get("date"))
        if record_date is None:
            raise ValueError(f"record has invalid date: {record.get('id')}")
        if record_date > snapshot_cutoff:
            dated.append((record_date, record))
    dated.sort(key=lambda pair: (pair[0], str(pair[1].get("id", ""))))
    policy = model_snapshot.get("warning_policy", {})
    events: list[dict[str, Any]] = []
    skipped_fractional = 0
    for key, frozen in catalog["topics"].items():
        subject_name, module_name, topic_name = json.loads(key)
        topic = frozen["topic"]
        for current_date, record in dated:
            if current_date < test_cutoff:
                continue
            current = iter_topic_observations(subject_name, topic_name, topic, [record], policy)
            if not current:
                continue
            previous = [item for day, item in dated if day < current_date]
            state = topic_bkt_state(subject_name, module_name, topic, previous, policy)
            prior_observations = iter_topic_observations(subject_name, topic_name, topic, previous, policy)
            sequence_state = state["raw_sequence_probability"]
            gap = (current_date - prior_observations[-1]["date"]).days if prior_observations else None
            predicted_state = time_decay_mastery(sequence_state, gap, topic, policy)
            context = {
                "attempt_index": len(prior_observations) + 1,
                "days_since_previous": gap,
                "source": record.get("source", ""),
                "activity": record.get("activity", ""),
                "has_partial_credit": False,
                "has_error_cause": False,
            }
            difficulty_score = float(topic.get("difficulty", 0.55)) * 100
            guess, slip, _learn = bkt_params(topic, difficulty_score, policy, context)
            forecast = predicted_state * (1 - slip) + (1 - predicted_state) * guess
            training_ids = [item.get("id") for item in previous]
            for observation_index, observation in enumerate(current, start=1):
                outcome = float(observation["correctness"])
                if outcome not in (0.0, 1.0):
                    skipped_fractional += 1
                    continue
                events.append({
                    "date": current_date.isoformat(),
                    "record_id": record.get("id"),
                    "subject": subject_name, "module": module_name, "topic": topic_name,
                    "prior_source": frozen["source"],
                    "baseline_prior": frozen["effective_prior"],
                    "evidence_updated_state": sequence_state,
                    "display_value": state["mastery_probability"],
                    "snapshot_display_value": frozen["display_mastery_at_snapshot"],
                    "forecast": forecast, "outcome": outcome,
                    "observation_index": observation_index,
                    "observation_title": observation.get("title"),
                    "result_source": observation.get("result_source"),
                    "training_record_ids": training_ids,
                    "difficulty_source": "snapshot_topic_difficulty",
                })
    events.sort(key=lambda item: (item["date"], str(item["record_id"]), item["subject"], item["module"], item["topic"]))
    return {
        "version": VERSION,
        "snapshot_id": snapshot_id,
        "snapshot_date": snapshot_cutoff.isoformat(),
        "snapshot_sha256": catalog["snapshot_sha256"],
        "model_version": catalog["model_version"],
        "test_from": test_cutoff.isoformat(),
        "seed": seed,
        "records_sha256": _digest(records),
        "topic_count": len(catalog["topics"]),
        "prior_sources": {source: sum(item["source"] == source for item in catalog["topics"].values())
                          for source in sorted({item["source"] for item in catalog["topics"].values()})},
        "skipped_fractional_outcomes": skipped_fractional,
        "metrics": _metrics(events, seed),
        "events": events,
    }
