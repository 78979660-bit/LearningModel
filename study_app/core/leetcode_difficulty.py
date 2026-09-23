from __future__ import annotations

import re
from dataclasses import dataclass


LABEL_BASE = {
    "Easy": 34.0,
    "Medium": 58.0,
    "Hard": 78.0,
}


TOPIC_BASE_ADJUST = {
    "数组、哈希与前缀结构": -2.0,
    "双指针与滑动窗口": 0.0,
    "栈、队列与单调结构": 1.0,
    "链表与指针技巧": 0.0,
    "二叉树、BST与堆": 2.0,
    "堆与优先队列": 3.0,
    "递归、回溯与分治": 4.0,
    "图搜索、并查集与最短路": 5.0,
    "动态规划基础": 5.0,
    "字符串与模式匹配": 4.0,
}


FEATURE_WEIGHTS = (
    ("design", 7.0, ("design", "cache", "twitter", "stream", "online", "implement")),
    ("advanced_graph", 8.0, ("dijkstra", "tarjan", "critical connection", "shortest path", "network delay", "word ladder")),
    ("two_dimensional_dp", 7.0, ("edit distance", "regular expression", "wildcard", "longest common subsequence", "maximal square")),
    ("state_compression_or_backpack", 5.0, ("partition", "target sum", "coin change", "dice", "stone weight ii")),
    ("monotonic_structure", 5.0, ("monotonic", "histogram", "rectangle", "next greater", "stock span")),
    ("heap_invariant", 5.0, ("median", "top k", "kth", "scheduler", "skyline", "hire", "cpu")),
    ("backtracking_pruning", 5.0, ("sudoku", "n-queens", "combination", "permutation", "subsets", "parentheses")),
    ("multi_source_or_grid", 4.0, ("grid", "matrix", "island", "oranges", "rooms", "flood fill")),
    ("binary_search_variant", 4.0, ("rotated", "first and last", "median of two", "sorted matrix")),
    ("string_algorithm", 5.0, ("kmp", "palindrome", "decode", "anagram", "substring", "word break")),
    ("linked_boundary", 3.0, ("reverse", "rotate", "random pointer", "cycle", "k-group")),
    ("simulation_or_boundary", 2.0, ("spiral", "path", "calculator", "remove", "merge intervals")),
)


KNOWN_OVERRIDES = {
    1: 35,
    4: 95,
    10: 90,
    23: 84,
    25: 82,
    30: 86,
    32: 83,
    37: 86,
    42: 82,
    44: 87,
    51: 82,
    72: 82,
    76: 84,
    84: 86,
    85: 91,
    124: 85,
    126: 93,
    127: 82,
    146: 74,
    214: 88,
    218: 91,
    224: 82,
    239: 80,
    295: 81,
    301: 89,
    332: 87,
    355: 76,
    778: 84,
    827: 87,
    857: 88,
    1192: 90,
}


@dataclass(frozen=True)
class LeetCodeDifficulty:
    score: float
    band: str
    reasons: tuple[str, ...]


def leetcode_band(score: float) -> str:
    if score >= 85:
        return "very_hard"
    if score >= 75:
        return "hard"
    if score >= 62:
        return "medium_hard"
    if score >= 48:
        return "medium"
    if score >= 36:
        return "easy_medium"
    return "easy"


def score_leetcode_problem(
    number: int | str | None,
    title: str,
    official_label: str,
    topic: str,
    abstract: str = "",
) -> LeetCodeDifficulty:
    number_int = _to_int(number)
    if number_int in KNOWN_OVERRIDES:
        score = float(KNOWN_OVERRIDES[number_int])
        return LeetCodeDifficulty(
            score=score,
            band=leetcode_band(score),
            reasons=(f"known_override:{number_int}", f"official:{official_label}", f"topic:{topic}"),
        )

    label = _normalize_label(official_label)
    score = LABEL_BASE.get(label, LABEL_BASE["Medium"])
    reasons = [f"official:{label}"]

    topic_adjust = TOPIC_BASE_ADJUST.get(topic, 0.0)
    if topic_adjust:
        score += topic_adjust
        reasons.append(f"topic_adjust:{topic_adjust:+.0f}")

    haystack = f"{title} {abstract} {topic}".lower()
    for feature, weight, keywords in FEATURE_WEIGHTS:
        if any(keyword in haystack for keyword in keywords):
            score += weight
            reasons.append(f"{feature}:{weight:+.0f}")

    if re.search(r"\bk\b|top-k|top k|kth|第\s*k", haystack):
        score += 3.0
        reasons.append("k_parameter:+3")
    if any(term in haystack for term in ("边界", "去重", "重复", "in-place", "原地", "状态维护")):
        score += 2.0
        reasons.append("boundary_state:+2")
    if label == "Easy":
        score = min(score, 52.0)
    elif label == "Medium":
        score = min(score, 78.0)
    else:
        score = max(score, 74.0)

    score = max(25.0, min(96.0, score))
    return LeetCodeDifficulty(score=round(score, 1), band=leetcode_band(score), reasons=tuple(reasons))


def _normalize_label(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"easy", "简单"}:
        return "Easy"
    if text in {"hard", "困难"}:
        return "Hard"
    return "Medium"


def _to_int(value: int | str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
