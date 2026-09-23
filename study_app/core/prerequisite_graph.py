from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from study_app.core.topic_identity import validate_topic_key


PREREQUISITE_GRAPH_VERSION = "prerequisite-graph-v1"
ALLOWED_PREREQUISITE_SOURCES = frozenset(
    {"user_confirmed", "controlled_local_config"}
)
_FORBIDDEN_METADATA_KEYS = frozenset(
    {"api_key", "authorization", "prompt", "secret", "token"}
)


@dataclass(frozen=True)
class PrerequisiteEdge:
    topic_key: str
    prerequisite_topic_key: str
    source: str
    source_data: dict[str, object]


def validate_prerequisite_source(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_PREREQUISITE_SOURCES:
        allowed = "、".join(sorted(ALLOWED_PREREQUISITE_SOURCES))
        raise ValueError(f"先修关系来源必须是：{allowed}")
    return value


def validate_prerequisite_source_data(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("先修关系来源元数据必须是对象")
    def collect_forbidden_keys(item: object) -> set[str]:
        if isinstance(item, dict):
            found = {
                str(key).lower()
                for key in item
                if str(key).lower() in _FORBIDDEN_METADATA_KEYS
            }
            for nested in item.values():
                found.update(collect_forbidden_keys(nested))
            return found
        if isinstance(item, (list, tuple)):
            found: set[str] = set()
            for nested in item:
                found.update(collect_forbidden_keys(nested))
            return found
        return set()

    forbidden = sorted(collect_forbidden_keys(value))
    if forbidden:
        raise ValueError(f"先修关系来源元数据包含禁止字段：{'、'.join(forbidden)}")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as error:
        raise ValueError("先修关系来源元数据必须可 JSON 序列化") from error
    if len(encoded.encode("utf-8")) > 4096:
        raise ValueError("先修关系来源元数据超过 4096 字节")
    return json.loads(encoded)


def make_prerequisite_edge(
    topic_key: object,
    prerequisite_topic_key: object,
    source: object,
    source_data: object = None,
) -> PrerequisiteEdge:
    topic = validate_topic_key(topic_key)
    prerequisite = validate_topic_key(prerequisite_topic_key)
    if topic == prerequisite:
        raise ValueError("知识点不能以自身作为先修关系")
    return PrerequisiteEdge(
        topic_key=topic,
        prerequisite_topic_key=prerequisite,
        source=validate_prerequisite_source(source),
        source_data=validate_prerequisite_source_data(source_data),
    )


def validate_prerequisite_replacement(
    topic_key: object,
    prerequisite_keys: object,
    source: object,
    source_data: object = None,
) -> tuple[str, tuple[PrerequisiteEdge, ...]]:
    topic = validate_topic_key(topic_key)
    if isinstance(prerequisite_keys, (str, bytes)) or not isinstance(
        prerequisite_keys, (list, tuple)
    ):
        raise ValueError("prerequisite_keys 必须是知识点键数组")
    normalized = [validate_topic_key(item) for item in prerequisite_keys]
    if len(set(normalized)) != len(normalized):
        raise ValueError("先修关系数组包含重复知识点")
    valid_source = validate_prerequisite_source(source)
    valid_source_data = validate_prerequisite_source_data(source_data)
    edges = tuple(
        make_prerequisite_edge(topic, item, valid_source, valid_source_data)
        for item in sorted(normalized)
    )
    return topic, edges


def validate_acyclic_prerequisite_graph(
    edges: Iterable[PrerequisiteEdge],
) -> tuple[PrerequisiteEdge, ...]:
    normalized = tuple(edges)
    pairs: set[tuple[str, str]] = set()
    adjacency: dict[str, set[str]] = {}
    for edge in normalized:
        if not isinstance(edge, PrerequisiteEdge):
            raise ValueError("先修图只能包含 PrerequisiteEdge")
        pair = (edge.topic_key, edge.prerequisite_topic_key)
        if pair in pairs:
            raise ValueError("先修图包含重复边")
        pairs.add(pair)
        adjacency.setdefault(edge.topic_key, set()).add(
            edge.prerequisite_topic_key
        )
        adjacency.setdefault(edge.prerequisite_topic_key, set())

    visiting: list[str] = []
    visiting_set: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting_set:
            start = visiting.index(node)
            cycle = visiting[start:] + [node]
            raise ValueError("先修关系形成环：" + " -> ".join(cycle))
        if node in visited:
            return
        visiting.append(node)
        visiting_set.add(node)
        for prerequisite in sorted(adjacency.get(node, ())):
            visit(prerequisite)
        visiting.pop()
        visiting_set.remove(node)
        visited.add(node)

    for node in sorted(adjacency):
        visit(node)
    return tuple(
        sorted(
            normalized,
            key=lambda item: (item.topic_key, item.prerequisite_topic_key),
        )
    )


def upstream_prerequisite_paths(
    topic_key: object,
    edges: Iterable[PrerequisiteEdge],
) -> tuple[tuple[str, ...], ...]:
    topic = validate_topic_key(topic_key)
    normalized = validate_acyclic_prerequisite_graph(edges)
    adjacency: dict[str, list[str]] = {}
    for edge in normalized:
        adjacency.setdefault(edge.topic_key, []).append(
            edge.prerequisite_topic_key
        )

    paths: list[tuple[str, ...]] = []

    def walk(node: str, prefix: tuple[str, ...]) -> None:
        for prerequisite in sorted(adjacency.get(node, ())):
            path = prefix + (prerequisite,)
            paths.append(path)
            walk(prerequisite, path)

    walk(topic, ())
    return tuple(paths)
