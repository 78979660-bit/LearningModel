"""
Rule-based difficulty classifier for learning records.

The classifier is intentionally conservative: explicit difficulty metadata wins.
When metadata is missing, it infers a first-pass easy/medium/hard label from the
subject, topic, problem title, tags, and short note.
"""

from __future__ import annotations

import math

from learning_problem_analysis import analyze_problem_statement


DIFFICULTIES = {"easy", "medium", "hard"}


DIFFICULTY_LABEL_SCORES = {
    "easy": 25.0,
    "medium": 55.0,
    "hard": 85.0,
}


KNOWN_PROBLEMS = {
    ("leetcode", "重复的DNA序列"): "medium",
    ("leetcode", "repeated dna sequences"): "medium",
    ("leetcode", "最短回文串"): "hard",
    ("leetcode", "shortest palindrome"): "hard",
}


SUBJECT_RULES = {
    "高等数学": {
        "hard": [
            "Gauss",
            "Stokes",
            "Green",
            "场论",
            "闭曲面",
            "第二型曲面积分",
            "三重积分换元",
            "Jacobian",
            "级数综合",
            "证明",
        ],
        "medium": [
            "曲面积分",
            "曲线积分",
            "三重积分",
            "二重积分",
            "换元",
            "坐标系",
            "梯度",
            "方向导数",
            "条件极值",
            "幂级数",
        ],
        "easy": [
            "偏导数",
            "全微分",
            "极限",
            "连续",
            "基础",
            "概念",
            "公式代入",
        ],
    },
    "数据结构与算法基础": {
        "hard": [
            "最短回文串",
            "KMP",
            "前缀函数",
            "动态规划",
            "最短路",
            "最小生成树",
            "AVL 删除",
            "红黑树",
            "复杂度证明",
        ],
        "medium": [
            "AVL",
            "哈希",
            "散列",
            "并查集",
            "字典",
            "二叉搜索树",
            "堆",
            "DFS",
            "BFS",
            "排序",
            "字符串匹配",
            "滑动窗口",
        ],
        "easy": [
            "数组",
            "顺序表",
            "链表",
            "栈",
            "队列",
            "遍历",
            "集合表示",
            "概念",
            "定义",
        ],
    },
    "大学物理学": {
        "hard": [
            "刚体",
            "惯量张量",
            "Euler",
            "非惯性系",
            "耦合振动",
            "本征模",
            "相对论",
            "热力学函数",
        ],
        "medium": [
            "牛顿",
            "动量",
            "角动量",
            "能量",
            "碰撞",
            "振动",
            "波动方程",
            "驻波",
            "热力学第一定律",
        ],
        "easy": [
            "位移",
            "速度",
            "加速度",
            "参考系",
            "温度",
            "理想气体状态方程",
            "基础概念",
        ],
    },
    "化学原理": {
        "hard": [
            "相图计算",
            "二组分相图",
            "Nernst",
            "能斯特",
            "电极电势",
            "反应机理",
            "速率方程",
        ],
        "medium": [
            "相律",
            "相平衡",
            "溶液",
            "酸碱平衡",
            "沉淀溶解平衡",
            "氧化还原",
            "化学动力学",
        ],
        "easy": [
            "相",
            "组分",
            "自由度",
            "定义",
            "概念",
            "基础",
        ],
    },
}


def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(normalize_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(normalize_text(item) for item in value.values())
    return str(value)


def clamp_difficulty_score(value) -> float | None:
    if isinstance(value, (int, float)):
        return max(0.0, min(100.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(100.0, float(value.strip())))
        except ValueError:
            return None
    return None


def label_from_score(score: float) -> str:
    if score >= 75:
        return "hard"
    if score >= 40:
        return "medium"
    return "easy"


def score_from_label(label: str) -> float:
    return DIFFICULTY_LABEL_SCORES.get(label, 55.0)


def _is_numeric_string(text: str) -> bool:
    """True 当字符串可被解析为数值（含科学计数法/点号/正负号/inf/nan 等全部形式）。"""
    try:
        float(text)
    except ValueError:
        return False
    return True

def existing_difficulty(problem: dict) -> dict | None:
    numeric = None
    for label, candidate in (
        ("difficulty_score", problem.get("difficulty_score")),
        ("difficulty_value", problem.get("difficulty_value")),
        ("difficulty_percent", problem.get("difficulty_percent")),
    ):
        if candidate is None:
            continue
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            raise ValueError(f"{label} 必须是数值（字符串数值与布尔值不接受）：{candidate!r}")
        number = float(candidate)
        if not math.isfinite(number):
            raise ValueError(f"{label} 必须是有限数值：{candidate!r}")
        if number < 0 or number > 100:
            raise ValueError(f"{label} 必须在 0-100 范围内：{candidate!r}")
        numeric = number
        break
    if numeric is not None:
        return {"label": label_from_score(numeric), "score": numeric}
    if numeric is not None:
        return {"label": label_from_score(numeric), "score": numeric}

    value = problem.get("difficulty") or problem.get("level")
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    if _is_numeric_string(value):
        raise ValueError(
            f"difficulty 字符串数值不接受，请改用数值字段 difficulty_score：{value!r}"
        )
    value = value.lower()
    aliases = {
        "简单": "easy",
        "easy": "easy",
        "e": "easy",
        "中等": "medium",
        "medium": "medium",
        "m": "medium",
        "困难": "hard",
        "hard": "hard",
        "h": "hard",
    }
    label = aliases.get(value)
    if not label:
        return None
    return {"label": label, "score": score_from_label(label)}


def known_problem_difficulty(problem: dict) -> str | None:
    platform = normalize_text(problem.get("platform")).lower()
    title = normalize_text(problem.get("title")).lower()
    for (known_platform, known_title), difficulty in KNOWN_PROBLEMS.items():
        if known_platform in platform and known_title.lower() in title:
            return difficulty
    return None


def keyword_difficulty(subject: str, text: str) -> tuple[str, list[str]]:
    rules = SUBJECT_RULES.get(subject, {})
    haystack = text.lower()
    matched = {"hard": [], "medium": [], "easy": []}
    for level in ("hard", "medium", "easy"):
        for keyword in rules.get(level, []):
            if keyword.lower() in haystack:
                matched[level].append(keyword)

    if matched["hard"]:
        return "hard", matched["hard"]
    if matched["medium"]:
        return "medium", matched["medium"]
    if matched["easy"]:
        return "easy", matched["easy"]
    return "medium", []


def infer_problem_difficulty(record: dict, problem: dict | None = None) -> dict:
    problem = problem or {}

    explicit = existing_difficulty(problem)
    if explicit:
        return {
            "difficulty": explicit["label"],
            "difficulty_score": explicit["score"],
            "source": "explicit",
            "confidence": 1.0,
            "matched_keywords": [],
        }

    statement_analysis = analyze_problem_statement(record, problem)
    if statement_analysis["has_statement"]:
        return {
            "difficulty": statement_analysis["difficulty"],
            "difficulty_score": statement_analysis["difficulty_score"],
            "source": "statement_analysis",
            "confidence": statement_analysis["confidence"],
            "matched_keywords": statement_analysis["knowledge_points"],
            "reasons": statement_analysis["reasons"],
            "statement_features": statement_analysis["features"],
        }

    known = known_problem_difficulty(problem)
    if known:
        return {
            "difficulty": known,
            "difficulty_score": score_from_label(known),
            "source": "known_problem",
            "confidence": 0.95,
            "matched_keywords": [],
        }

    subject = record.get("subject", "")
    text = normalize_text(
        [
            record.get("module"),
            record.get("topic"),
            record.get("related_topics"),
            record.get("tags"),
            record.get("note"),
            record.get("error_causes"),
            problem.get("title"),
            problem.get("related_topics"),
        ]
    )
    difficulty, keywords = keyword_difficulty(subject, text)
    confidence = 0.7 if keywords else 0.45
    return {
        "difficulty": difficulty,
        "difficulty_score": score_from_label(difficulty),
        "source": "keyword_rule" if keywords else "default_medium",
        "confidence": confidence,
        "matched_keywords": keywords,
        "reasons": [],
    }


def annotate_problem_difficulties(record: dict) -> dict:
    updated = dict(record)
    problems = []
    for problem in record.get("problems", []):
        item = dict(problem)
        inferred = infer_problem_difficulty(record, item)
        item.setdefault("difficulty", inferred["difficulty"])
        item.setdefault("difficulty_score", inferred["difficulty_score"])
        item.setdefault("difficulty_source", inferred["source"])
        item.setdefault("difficulty_confidence", inferred["confidence"])
        if inferred["matched_keywords"]:
            item.setdefault("difficulty_matched_keywords", inferred["matched_keywords"])
        if inferred.get("reasons"):
            item.setdefault("difficulty_reasons", inferred["reasons"])
        problems.append(item)
    if problems:
        updated["problems"] = problems
    return updated


def main() -> None:
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Infer problem difficulties in learning records.")
    from study_app.paths import RECORDS_PATH

    parser.add_argument("--records", default=str(RECORDS_PATH))
    args = parser.parse_args()

    data = json.loads(Path(args.records).read_text(encoding="utf-8"))
    for record in data.get("records", []):
        if not record.get("problems"):
            continue
        print(f"{record.get('date')} {record.get('subject')} / {record.get('topic')}")
        for problem in record["problems"]:
            inferred = infer_problem_difficulty(record, problem)
            title = problem.get("title", "untitled")
            print(
                f"- {title}: {inferred['difficulty']} "
                f"[{inferred['difficulty_score']:.0f}/100] "
                f"({inferred['source']}, confidence={inferred['confidence']:.2f})"
            )
            for reason in inferred.get("reasons", [])[:3]:
                print(f"  reason: {reason}")


if __name__ == "__main__":
    main()
