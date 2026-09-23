from __future__ import annotations

import copy
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any

from learning_bkt import problem_mentions_topic, record_mentions_topic as bkt_mentions
from learning_memory import record_mentions_topic as memory_mentions


def _digest(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _relevant_record(record: dict[str, Any], subject: str, topic: str) -> bool:
    if record.get("subject") != subject:
        return False
    if bkt_mentions(record, subject, topic) or memory_mentions(record, subject, topic):
        return True
    return any(
        isinstance(problem, dict) and problem_mentions_topic(record, problem, topic)
        for problem in (record.get("problems") or [])
    )


@dataclass(frozen=True)
class TopicCacheKey:
    subject: str
    module: str
    topic: str
    record_version: str
    model_version: str
    calculation_date: str
    phase_version: str
    scope_version: str
    policy_version: str
    topic_version: str


class ScopedTopicCache:
    """Bounded in-memory cache; uncertain dependencies never produce a hit."""

    def __init__(self, max_entries: int = 2048):
        self.max_entries = max_entries
        self._lock = RLock()
        self._values: OrderedDict[tuple[str, TopicCacheKey], Any] = OrderedDict()
        self._record_versions: OrderedDict[tuple[str, str, str], str] = OrderedDict()

    def key(
        self, *, subject: str, module: str, topic: dict[str, Any],
        records: list[dict[str, Any]], model: dict[str, Any],
        calculation_date: str, phase: dict[str, Any], scope: Any,
        policy: dict[str, Any], records_version: str | None = None,
        model_version: str | None = None,
        policy_version: str | None = None,
    ) -> TopicCacheKey | None:
        topic_name = str(topic.get("name") or "")
        if not subject or not topic_name or not isinstance(records, list):
            return None
        if any(not isinstance(record, dict) for record in records):
            return None
        snapshot = records_version or _digest(records)
        relevance_key = (snapshot, subject, topic_name)
        with self._lock:
            record_version = self._record_versions.get(relevance_key)
            if record_version is not None:
                self._record_versions.move_to_end(relevance_key)
        if record_version is None:
            try:
                relevant = [
                    record for record in records
                    if _relevant_record(record, subject, topic_name)
                ]
            except (TypeError, ValueError, KeyError):
                return None
            record_version = _digest(relevant)
            with self._lock:
                self._record_versions[relevance_key] = record_version
                while len(self._record_versions) > self.max_entries * 4:
                    self._record_versions.popitem(last=False)
        return TopicCacheKey(
            subject=subject, module=module, topic=topic_name,
            record_version=record_version, model_version=model_version or _digest(model),
            calculation_date=calculation_date, phase_version=_digest(phase),
            scope_version=_digest(scope), policy_version=policy_version or _digest(policy),
            topic_version=_digest(topic),
        )

    def get(self, kind: str, key: TopicCacheKey | None) -> tuple[bool, Any]:
        if key is None:
            return False, None
        composite = (kind, key)
        with self._lock:
            if composite not in self._values:
                return False, None
            self._values.move_to_end(composite)
            return True, copy.deepcopy(self._values[composite])

    def put(self, kind: str, key: TopicCacheKey | None, value: Any) -> None:
        if key is None or self.max_entries <= 0:
            return
        composite = (kind, key)
        with self._lock:
            self._values[composite] = copy.deepcopy(value)
            self._values.move_to_end(composite)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._record_versions.clear()


dashboard_topic_cache = ScopedTopicCache()
