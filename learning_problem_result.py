"""Shared problem-result interpretation entry (data_contract_v1 §4.1).

单一解释入口：BKT、难度计分、掌握度同步与界面回读都必须经由本函数解释
题目结果，禁止各处自行复制解释逻辑。

优先级（高级别存在时低级别不参与）：
P0 显式数值 correctness / partial_credit（correctness 优先，percent 形式归一）
P1 correct 布尔与 status 枚举映射
P2 answer_result 等结果文本推断（复用数据层 _infer_correctness_from_text）
P3 记录级兜底（仅当调用方传入 record；来源标记 record_fallback）

硬性规则：记录总分永远不会改写 P0-P2 已确定的题目结果。
布尔/NaN 等非法数值在本层按「缺失」跳过——非法输入的拒绝由校验层
（validation / record_parser，A-02）负责。
"""

from __future__ import annotations

_STATUS_RESULTS = {
    "completed": 1.0,
    "solved": 1.0,
    "correct": 1.0,
    "accepted": 1.0,
    "ac": 1.0,
    "all_correct": 1.0,
    "全对": 1.0,
    "做出": 1.0,
    "not_solved": 0.0,
    "wrong": 0.0,
    "incorrect": 0.0,
    "failed": 0.0,
    "wa": 0.0,
    "未做出": 0.0,
    "没做出来": 0.0,
    "partial": 0.5,
    "partial_wrong": 0.5,
    "partially_correct": 0.5,
    "部分正确": 0.5,
    "部分出错": 0.5,
}


def _finite_unit_fraction(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number > 1:
        number = number / 100
    return max(0.0, min(1.0, number))


def _record_correctness(record):
    if record is None:
        return None
    score = record.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return max(0.0, min(1.0, float(score) / 100))
    result = record.get("result") or {}
    correct = result.get("correct")
    total = result.get("total")
    if isinstance(correct, (int, float)) and isinstance(total, (int, float)) and total > 0:
        partial = result.get("partial_wrong", 0) or 0
        return max(0.0, min(1.0, (float(correct) + 0.5 * float(partial)) / float(total)))
    wrong = result.get("wrong")
    if (
        isinstance(correct, (int, float))
        and isinstance(wrong, (int, float))
        and correct + wrong > 0
    ):
        return max(0.0, min(1.0, float(correct) / float(correct + wrong)))
    rating = record.get("self_rating")
    if isinstance(rating, (int, float)) and not isinstance(rating, bool):
        return max(0.0, min(1.0, float(rating) / 100))
    return None


def _text_inferred_correctness(problem):
    try:
        from study_app.data.database import _infer_correctness_from_text
    except Exception:
        return None
    return _infer_correctness_from_text(problem)


def interpret_problem_result(problem, record=None):
    """解释题目结果，返回 {"value": 0..1 | None, "source": ...}。

    source 取值：explicit_numeric / status / inferred_text / record_fallback / None。
    record 仅在允许记录级兜底的调用方传入（缺省 None = 仅题目级）。
    """
    problem = problem or {}

    for key in ("correctness", "partial_credit"):
        value = _finite_unit_fraction(problem.get(key))
        if value is None:
            continue
        return {"value": value, "source": "explicit_numeric"}

    correct_flag = problem.get("correct")
    if isinstance(correct_flag, bool):
        return {"value": 1.0 if correct_flag else 0.0, "source": "status"}

    status = problem.get("status")
    if status is not None:
        mapped = _STATUS_RESULTS.get(str(status).strip().lower())
        if mapped is not None:
            return {"value": mapped, "source": "status"}

    text_value = _text_inferred_correctness(problem)
    if text_value is not None:
        return {"value": max(0.0, min(1.0, text_value)), "source": "inferred_text"}

    if record is not None:
        value = _record_correctness(record)
        if value is not None:
            return {"value": value, "source": "record_fallback"}

    return {"value": None, "source": None}
