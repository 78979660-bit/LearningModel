"""章节知识图谱：强先修 DAG 校验与确定性拓扑排序（合同 §8.3/§9）。

只处理 ``strong_prerequisite`` 边；``supporting_relation`` 不进入 DAG、
不影响层级（合同 §4.3/§9.1）。全部计算为本地确定性纯函数：无随机、
无时间、无集合迭代序依赖——相同输入在任何机器、任何重复运行都得到
相同输出。所有名字只作为普通数据显示/排序，绝不解释为指令（§11）。

规则：
- 校验（:func:`validate_strong_dag`）：自环、重复边、端点未知（含跨学科：
  以 module_key 集合成员资格判定）、循环；循环诊断给出完整路径
  ``循环路径: A → B → C → A``。空诊断 = 合法。
- 排序（:func:`topological_rank`）：稳定 Kahn + 最长路径分层，
  ``rank(node) = 1 + max(rank(强前驱))``（无强前驱为 0），层内平局依次按
  ``(module_order, NFKC 规范化 display_name, module_key)``；孤立章节
  （无任何强边）排在全部强边层之后，同一新层内按上述平局规则以
  module_order 稳定排列。排序输入中的重复模块键/自环/循环直接抛出
  ``ValueError``（循环消息含完整路径）；端点不在节点集合内的强边忽略
  （拒绝由 :func:`validate_strong_dag` 负责）。
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from typing import Iterable

from study_app.core.chapter_graph_projection import ChapterEdge, ChapterNode


STRONG_RELATION = "strong_prerequisite"
CYCLE_MESSAGE_PREFIX = "循环路径: "


def _strong_pairs(edges: Iterable[object]) -> list[tuple[str, str]]:
    """去重后的强边 (from, to) 对，按字典序稳定排序。"""
    pairs: set[tuple[str, str]] = set()
    for edge in edges:
        if getattr(edge, "relation_type", STRONG_RELATION) != STRONG_RELATION:
            continue
        pairs.add((str(edge.from_module_key), str(edge.to_module_key)))
    return sorted(pairs)


def _node_sort_key(node: ChapterNode) -> tuple[int, str, str]:
    return (
        node.module_order,
        unicodedata.normalize("NFKC", str(node.display_name)),
        node.module_key,
    )


def _find_cycle(
    nodes: list[str], adjacency: dict[str, list[str]]
) -> tuple[str, ...] | None:
    """确定性 DFS 找环；返回环路径（不重复首节点）或 None。"""
    state: dict[str, int] = {}
    for root in nodes:
        if state.get(root, 0) != 0:
            continue
        state[root] = 1
        path = [root]
        stack = [(root, iter(adjacency.get(root, ())))]
        while stack:
            node, neighbors = stack[-1]
            advanced = False
            for neighbor in neighbors:
                neighbor_state = state.get(neighbor, 0)
                if neighbor_state == 1:
                    return tuple(path[path.index(neighbor) :])
                if neighbor_state == 0:
                    state[neighbor] = 1
                    path.append(neighbor)
                    stack.append((neighbor, iter(adjacency.get(neighbor, ()))))
                    advanced = True
                    break
            if not advanced:
                state[node] = 2
                path.pop()
                stack.pop()
    return None


def _cycle_message(cycle: tuple[str, ...]) -> str:
    return CYCLE_MESSAGE_PREFIX + " → ".join((*cycle, cycle[0]))


def validate_strong_dag(edges: Iterable[object], module_keys: Iterable[object]) -> tuple[str, ...]:
    """校验强先修边集合；返回诊断元组，空元组 = 合法 DAG。

    - 自环（from == to）→ ``self_loop:<key>``；
    - 重复强边 → ``duplicate_edge:<from>-><to>``；
    - 端点不在 module_keys 中（含跨学科端点）→ ``endpoint_unknown:<key>``；
    - 循环 → ``循环路径: A → B → C → A``（完整路径）。
    非 strong 边一律忽略（辅助关系允许成环，且不参与拓扑，§8.3）。
    """
    valid = frozenset(str(key) for key in module_keys)
    self_loops: set[str] = set()
    unknown: set[str] = set()
    duplicates: set[str] = set()
    seen: set[tuple[str, str]] = set()
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if getattr(edge, "relation_type", STRONG_RELATION) != STRONG_RELATION:
            continue
        from_key = str(edge.from_module_key)
        to_key = str(edge.to_module_key)
        if (from_key, to_key) in seen:
            duplicates.add(f"{from_key}->{to_key}")
            continue
        seen.add((from_key, to_key))
        if from_key == to_key:
            self_loops.add(from_key)
            continue
        if from_key not in valid:
            unknown.add(from_key)
        if to_key not in valid:
            unknown.add(to_key)
        if from_key in valid and to_key in valid:
            adjacency.setdefault(from_key, []).append(to_key)
    for neighbors in adjacency.values():
        neighbors.sort()
    diagnostics = [f"self_loop:{key}" for key in sorted(self_loops)]
    diagnostics.extend(f"duplicate_edge:{item}" for item in sorted(duplicates))
    diagnostics.extend(f"endpoint_unknown:{key}" for key in sorted(unknown))
    cycle = _find_cycle(sorted(valid | {key for pair in seen for key in pair}), adjacency)
    if cycle is not None:
        diagnostics.append(_cycle_message(cycle))
    return tuple(diagnostics)


def topological_rank(
    nodes: Iterable[ChapterNode], strong_edges: Iterable[ChapterEdge]
) -> tuple[ChapterNode, ...]:
    """确定性拓扑排序：返回按 ``(rank, module_order, NFKC 名称, module_key)``
    排列、且 ``topological_rank`` 已赋值的新节点元组。

    - rank 为最长路径分层：``rank = 1 + max(强前驱 rank)``，根为 0；
    - 层内平局按 ``(module_order, NFKC(display_name), module_key)``；
    - 孤立章节排在所有强边层之后的新层（同层按上述平局规则稳定排列）；
    - 重复模块键、强自环、强循环 → ``ValueError``（循环消息含完整路径）；
    - 端点不在节点集合内的强边被忽略（校验职责在 validate_strong_dag）。
    """
    by_key: dict[str, ChapterNode] = {}
    for node in nodes:
        if not isinstance(node, ChapterNode):
            raise ValueError("topological_rank 只能接收 ChapterNode")
        if node.module_key in by_key:
            raise ValueError(f"重复 module_key：{node.module_key}")
        by_key[node.module_key] = node
    successors: dict[str, set[str]] = {key: set() for key in by_key}
    predecessors: dict[str, set[str]] = {key: set() for key in by_key}
    for from_key, to_key in _strong_pairs(strong_edges):
        if from_key not in by_key or to_key not in by_key:
            continue
        if from_key == to_key:
            raise ValueError(f"强先修自环无法排序：{from_key}")
        successors[from_key].add(to_key)
        predecessors[to_key].add(from_key)

    indegree = {key: len(predecessors[key]) for key in by_key}
    ranks = {key: 0 for key in by_key}
    current = sorted(
        (key for key, degree in indegree.items() if degree == 0),
        key=lambda key: _node_sort_key(by_key[key]),
    )
    processed = 0
    while current:
        following: list[str] = []
        for key in current:
            processed += 1
            for successor in sorted(successors[key]):
                ranks[successor] = max(ranks[successor], ranks[key] + 1)
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    following.append(successor)
        current = sorted(following, key=lambda key: _node_sort_key(by_key[key]))
    if processed < len(by_key):
        adjacency = {
            key: sorted(successors[key])
            for key in by_key
            if successors[key]
        }
        cycle = _find_cycle(sorted(by_key), adjacency)
        if cycle is None:  # 防御：理论上不可达。
            raise ValueError("强先修图存在循环，无法拓扑排序")
        raise ValueError(_cycle_message(cycle))

    isolated = {key for key in by_key if not predecessors[key] and not successors[key]}
    if isolated:
        non_isolated_max = max(
            (ranks[key] for key in by_key if key not in isolated), default=-1
        )
        for key in isolated:
            ranks[key] = non_isolated_max + 1
    ordered = sorted(by_key.values(), key=lambda node: (ranks[node.module_key],) + _node_sort_key(node))
    return tuple(replace(node, topological_rank=ranks[node.module_key]) for node in ordered)
