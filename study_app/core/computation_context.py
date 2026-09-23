from __future__ import annotations

import json
import inspect
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from learning_bkt import iter_topic_observations, topic_bkt_state
from learning_memory import topic_memory_state
from study_app.core.scoped_topic_cache import ScopedTopicCache, TopicCacheKey, _digest


def _topic_signature(topic: dict[str, Any]) -> str:
    return json.dumps(topic, ensure_ascii=False, sort_keys=True, default=str)


@dataclass
class DashboardComputationContext:
    """Per-refresh cache bound to one model, record set, policy and date."""

    model: dict[str, Any]
    records: list[dict[str, Any]]
    as_of_date: date
    policy: dict[str, Any]
    shared_cache: ScopedTopicCache | None = None
    scope: Any = None
    subject_phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    _bkt_cache: dict[tuple[str, str, str, str, str], dict[str, Any] | None] = field(
        default_factory=dict
    )
    _memory_cache: dict[tuple[str, str, str, str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    _observation_cache: dict[
        tuple[str, str, str, str, str], tuple[dict[str, Any], ...]
    ] = field(default_factory=dict)
    _all_bkt_states: list[dict[str, Any]] | None = None
    _shared_keys: dict[tuple[str, str, str, str, str], TopicCacheKey | None] = field(
        default_factory=dict
    )
    _model_version: str | None = None
    _records_version: str | None = None
    _policy_version: str | None = None
    _bkt_accepts_observations: bool | None = None

    def _key(
        self, subject_name: str, module_name: str, topic: dict[str, Any]
    ) -> tuple[str, str, str, str, str]:
        return (
            subject_name,
            module_name,
            str(topic.get("name") or ""),
            self.as_of_date.isoformat(),
            _topic_signature(topic),
        )

    def bkt_state(
        self, subject_name: str, module_name: str, topic: dict[str, Any]
    ) -> dict[str, Any] | None:
        key = self._key(subject_name, module_name, topic)
        if key not in self._bkt_cache:
            shared_key = self._shared_key(subject_name, module_name, topic, key)
            found, cached = (
                self.shared_cache.get("bkt", shared_key)
                if self.shared_cache is not None else (False, None)
            )
            if found:
                self._bkt_cache[key] = cached
            else:
                if self._bkt_accepts_observations is None:
                    parameters = inspect.signature(topic_bkt_state).parameters
                    self._bkt_accepts_observations = (
                        "observations" in parameters
                        or any(
                            item.kind is inspect.Parameter.VAR_KEYWORD
                            for item in parameters.values()
                        )
                    )
                kwargs: dict[str, Any] = {"as_of_date": self.as_of_date}
                if self._bkt_accepts_observations:
                    kwargs["observations"] = self.observations(
                        subject_name, module_name, topic
                    )
                state = topic_bkt_state(
                    subject_name,
                    module_name,
                    topic,
                    self.records,
                    self.policy,
                    **kwargs,
                )
                self._bkt_cache[key] = state
                if self.shared_cache is not None:
                    self.shared_cache.put("bkt", shared_key, state)
        return self._bkt_cache[key]

    def observations(
        self, subject_name: str, module_name: str, topic: dict[str, Any]
    ) -> tuple[dict[str, Any], ...]:
        """Return the one normalized observation sequence shared within a refresh."""
        key = self._key(subject_name, module_name, topic)
        if key not in self._observation_cache:
            normalized = iter_topic_observations(
                subject_name,
                str(topic.get("name") or ""),
                topic,
                self.records,
                self.policy,
                as_of_date=self.as_of_date,
            )
            self._observation_cache[key] = tuple(dict(item) for item in normalized)
        return self._observation_cache[key]

    def memory_state(
        self, subject_name: str, module_name: str, topic: dict[str, Any]
    ) -> dict[str, Any]:
        key = self._key(subject_name, module_name, topic)
        if key not in self._memory_cache:
            shared_key = self._shared_key(subject_name, module_name, topic, key)
            found, cached = (
                self.shared_cache.get("memory", shared_key)
                if self.shared_cache is not None else (False, None)
            )
            if found:
                self._memory_cache[key] = cached
            else:
                state = topic_memory_state(
                    subject_name, module_name, topic, self.records,
                    self.as_of_date, self.policy,
                    bkt_state=self.bkt_state(subject_name, module_name, topic),
                )
                self._memory_cache[key] = state
                if self.shared_cache is not None:
                    self.shared_cache.put("memory", shared_key, state)
        return self._memory_cache[key]

    def _shared_key(
        self, subject_name: str, module_name: str, topic: dict[str, Any],
        local_key: tuple[str, str, str, str, str],
    ) -> TopicCacheKey | None:
        if self.shared_cache is None:
            return None
        if local_key not in self._shared_keys:
            if self._model_version is None:
                self._model_version = _digest(self.model)
                self._records_version = _digest(self.records)
                self._policy_version = _digest(self.policy)
            phase = self.subject_phases.get(subject_name)
            if phase is None or self.scope is None:
                return None
            self._shared_keys[local_key] = self.shared_cache.key(
                subject=subject_name, module=module_name, topic=topic,
                records=self.records, model=self.model,
                calculation_date=self.as_of_date.isoformat(), phase=phase,
                scope=self.scope, policy=self.policy,
                records_version=self._records_version,
                model_version=self._model_version,
                policy_version=self._policy_version,
            )
        return self._shared_keys[local_key]

    def bkt_topic_states(self) -> list[dict[str, Any]]:
        if self._all_bkt_states is None:
            states = []
            for subject in self.model.get("subjects", []):
                for module in subject.get("modules", []):
                    for topic in module.get("topics", []):
                        state = self.bkt_state(
                            subject["name"], module["name"], topic
                        )
                        if state:
                            states.append(state)
            states.sort(key=lambda item: item["priority"], reverse=True)
            self._all_bkt_states = states
        return self._all_bkt_states
