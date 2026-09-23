"""
Problem-statement difficulty analysis.

This module is used when a concrete problem statement is available. It estimates
difficulty from the task itself rather than only from broad topic labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


STATEMENT_FIELDS = (
    "statement",
    "question",
    "question_text",
    "problem_text",
    "content",
    "stem",
    "题面",
)


@dataclass
class Feature:
    name: str
    score: int
    reason: str


def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(normalize_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(normalize_text(item) for item in value.values())
    return str(value)


def problem_statement(problem: dict) -> str:
    for field in STATEMENT_FIELDS:
        text = normalize_text(problem.get(field)).strip()
        if text:
            return text
    return ""


def count_matches(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword.lower() in lowered]


def subject_knowledge_keywords(subject: str) -> dict[str, list[str]]:
    return {
        "高等数学": {
            "basic": ["极限", "连续", "偏导", "全微分", "定义", "直接计算"],
            "medium": ["二重积分", "三重积分", "曲线积分", "曲面积分", "换元", "参数方程", "梯度", "级数"],
            "hard": ["Gauss", "Green", "Stokes", "散度", "通量", "闭曲面", "Jacobian", "证明", "分片", "奇点"],
        },
        "数据结构与算法基础": {
            "basic": ["数组", "链表", "栈", "队列", "遍历", "模拟", "计数"],
            "medium": ["哈希", "滑动窗口", "并查集", "二叉搜索树", "堆", "排序", "DFS", "BFS"],
            "hard": ["KMP", "前缀函数", "动态规划", "图最短路", "最小生成树", "构造", "贪心证明", "复杂度证明"],
        },
        "大学物理学": {
            "basic": ["速度", "加速度", "位移", "受力分析", "理想气体", "温度"],
            "medium": ["动量", "能量", "角动量", "碰撞", "振动", "波动方程", "热力学第一定律"],
            "hard": ["刚体", "惯量张量", "非惯性系", "耦合振动", "相对论", "热力学函数", "本征模"],
        },
        "化学原理": {
            "basic": ["定义", "概念", "相", "组分", "自由度", "基础计算"],
            "medium": ["相律", "相平衡", "酸碱平衡", "沉淀溶解", "氧化还原", "化学动力学"],
            "hard": ["相图计算", "二组分相图", "能斯特", "Nernst", "电极电势", "反应机理", "速率方程"],
        },
    }.get(subject, {"basic": [], "medium": [], "hard": []})


def general_features(text: str) -> list[Feature]:
    features: list[Feature] = []
    lowered = text.lower()

    if any(word in text for word in ["证明", "推导", "说明为什么", "必要性", "充分性"]):
        features.append(Feature("proof_or_derivation", 2, "需要证明或推导，不只是代入计算"))

    if any(word in text for word in ["构造", "设计一个算法", "给出算法", "优化", "最优"]):
        features.append(Feature("construction", 2, "需要构造方法或算法设计"))

    if any(word in text for word in ["分段", "分片", "分类讨论", "边界", "方向", "取向", "奇点", "补面"]):
        features.append(Feature("trap_or_case_split", 2, "存在方向、边界、奇点、补面或分类讨论等易错点"))

    if any(word in text for word in ["转化为", "等价于", "化为", "建模", "映射", "换元", "变换"]):
        features.append(Feature("transformation", 2, "需要先做问题转化或模型转换"))

    if any(word in text for word in ["多个", "同时", "综合", "结合", "以及", "并且"]):
        features.append(Feature("multi_concept_signal", 1, "题面暗示多个条件或知识点联合使用"))

    formula_count = len(re.findall(r"[∫∑∂∇]|\\int|\\sum|\\partial|\\nabla|O\(", text))
    if formula_count >= 3:
        features.append(Feature("symbolic_density", 2, "公式/符号密度较高，计算和结构识别压力较大"))
    elif formula_count == 1 or formula_count == 2:
        features.append(Feature("symbolic_density", 1, "包含一定公式或符号操作"))

    if any(word in lowered for word in ["n log n", "o(n", "o(", "复杂度", "时间复杂度", "空间复杂度"]):
        features.append(Feature("complexity_constraint", 2, "包含复杂度约束或复杂度分析"))

    if len(text) > 450:
        features.append(Feature("long_statement", 1, "题面较长，信息提取成本较高"))

    return features


def knowledge_features(subject: str, text: str) -> tuple[list[Feature], list[str]]:
    rules = subject_knowledge_keywords(subject)
    hard = count_matches(text, rules["hard"])
    medium = count_matches(text, rules["medium"])
    basic = count_matches(text, rules["basic"])

    features: list[Feature] = []
    if hard:
        features.append(Feature("hard_knowledge", 3, "命中高难知识点：" + "、".join(hard)))
    if medium:
        features.append(Feature("medium_knowledge", 2, "命中中等知识点：" + "、".join(medium)))
    if basic and not hard and not medium:
        features.append(Feature("basic_knowledge", -1, "主要命中基础知识点：" + "、".join(basic)))

    knowledge_points = hard + medium + basic
    return features, knowledge_points


def error_category_features(category: str) -> list[Feature]:
    """Use the observed error type as weak evidence about hidden difficulty."""
    if not category or category == "none":
        return []
    rules = {
        "boundary_omission": Feature(
            "error_boundary_omission",
            2,
            "错因显示边界、端点、零点或特殊情况遗漏，题目含隐藏讨论点",
        ),
        "condition_misjudgment": Feature(
            "error_condition_misjudgment",
            2,
            "错因显示条件、方向、取向或补面判断失误，题目判定成本较高",
        ),
        "modeling_error": Feature(
            "error_modeling",
            2,
            "错因显示建模、变量范围或坐标转换问题，需要先建立正确模型",
        ),
        "method_gap": Feature(
            "error_method_gap",
            2,
            "错因显示关键方法未掌握或遗忘，题目依赖特定解法框架",
        ),
        "concept_forgetting": Feature(
            "error_concept_forgetting",
            1,
            "错因显示概念或公式记忆不稳，题目对基础提取有要求",
        ),
        "calculation_error": Feature(
            "error_calculation",
            0,
            "错因主要是计算失误，暂不单独抬高题目结构难度",
        ),
        "time_management": Feature(
            "error_time_management",
            0,
            "错因主要是时间或审题节奏，暂不单独抬高题目结构难度",
        ),
    }
    feature = rules.get(category)
    return [feature] if feature else []


def mapped_topic_features(topics) -> list[Feature]:
    topic_list = [item for item in normalize_text(topics).split() if item]
    if not topic_list:
        return []

    features: list[Feature] = []
    if len(set(topic_list)) >= 3:
        features.append(
            Feature(
                "multi_mapped_topics",
                1,
                "系统映射到多个知识点，说明题目需要跨点联动",
            )
        )

    hard_signals = ["Gauss", "Green", "Stokes", "KMP", "AVL", "势函数", "无旋场", "通量", "相图", "Nernst"]
    matched = [signal for signal in hard_signals if signal.lower() in normalize_text(topics).lower()]
    if matched:
        features.append(
            Feature(
                "mapped_hard_topic",
                1,
                "映射知识点包含高负荷主题：" + "、".join(matched),
            )
        )
    return features


def difficulty_score_from_raw(score: int, statement: str) -> float:
    base = 22 + score * 10
    if len(statement) > 800:
        base += 4
    if len(statement) > 1400:
        base += 4
    return max(5.0, min(98.0, float(base)))


def classify_score(score: int) -> str:
    if score >= 5:
        return "hard"
    if score >= 2:
        return "medium"
    return "easy"


def confidence_from_features(features: list[Feature], statement: str) -> float:
    if not statement:
        return 0.0
    if len(features) >= 4:
        return 0.82
    if len(features) >= 2:
        return 0.72
    return 0.58


def analyze_problem_statement(record: dict, problem: dict) -> dict:
    statement = problem_statement(problem)
    if not statement:
        return {
            "has_statement": False,
            "difficulty": None,
            "confidence": 0.0,
            "knowledge_points": [],
            "reasons": [],
            "features": [],
        }

    subject = record.get("subject", "")
    general_text = normalize_text(
        [
            statement,
            problem.get("title"),
            problem.get("error_cause"),
        ]
    )
    analysis_text = normalize_text(
        [
            statement,
            problem.get("title"),
            problem.get("related_topics"),
            problem.get("error_category"),
            problem.get("error_cause"),
            record.get("topic"),
            record.get("module"),
        ]
    )
    features = general_features(general_text)
    knowledge, knowledge_points = knowledge_features(subject, analysis_text)
    features.extend(knowledge)
    features.extend(error_category_features(problem.get("error_category", "")))
    features.extend(mapped_topic_features(problem.get("related_topics")))

    raw_score = sum(feature.score for feature in features)
    difficulty_score = difficulty_score_from_raw(raw_score, statement)
    difficulty = classify_score(raw_score)
    confidence = confidence_from_features(features, statement)

    return {
        "has_statement": True,
        "difficulty": difficulty,
        "confidence": confidence,
        "knowledge_points": knowledge_points,
        "reasons": [feature.reason for feature in features],
        "features": [
            {"name": feature.name, "score": feature.score, "reason": feature.reason}
            for feature in features
        ],
        "raw_score": raw_score,
        "difficulty_score": difficulty_score,
    }


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Analyze a concrete problem statement.")
    parser.add_argument("--subject", default="数据结构与算法基础")
    parser.add_argument("--statement", required=True)
    args = parser.parse_args()

    result = analyze_problem_statement(
        {"subject": args.subject},
        {"statement": args.statement},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
