from __future__ import annotations

from typing import Any, Iterable


def topic_submodule(topic: dict[str, Any], module: dict[str, Any] | None = None) -> str:
    direct = str(topic.get("submodule") or "").strip()
    if direct:
        return direct
    topic_name = str(topic.get("name") or "").strip()
    for item in (module or {}).get("submodules", []) or []:
        if topic_name in (item.get("topics") or []):
            return str(item.get("name") or "").strip()
    return ""


def iter_submodules(module: dict[str, Any]) -> Iterable[dict[str, Any]]:
    configured = list(module.get("submodules") or [])
    if configured:
        for item in configured:
            names = set(item.get("topics") or [])
            topics = [
                topic
                for topic in module.get("topics", []) or []
                if topic_submodule(topic, module) == item.get("name") or topic.get("name") in names
            ]
            yield {**item, "topics_data": topics}
        return
    yield {
        "name": str(module.get("name") or ""),
        "topics": [str(topic.get("name") or "") for topic in module.get("topics", []) or []],
        "topics_data": list(module.get("topics", []) or []),
    }


def submodule_mastery(item: dict[str, Any]) -> float:
    topics = [
        topic
        for topic in item.get("topics_data", [])
        if isinstance(topic.get("mastery"), (int, float))
    ]
    if not topics:
        return 0.0
    total_weight = sum(float(topic.get("importance", 1.0) or 1.0) for topic in topics) or len(topics)
    return sum(
        float(topic.get("mastery", 0.0) or 0.0) * float(topic.get("importance", 1.0) or 1.0)
        for topic in topics
    ) / total_weight


def find_topic_submodule(
    model: dict[str, Any],
    subject_name: str,
    module_name: str,
    topic_name: str,
) -> str:
    for subject in model.get("subjects", []):
        if subject.get("name") != subject_name:
            continue
        for module in subject.get("modules", []):
            if module.get("name") != module_name:
                continue
            for topic in module.get("topics", []):
                if topic.get("name") == topic_name:
                    return topic_submodule(topic, module)
    return ""
