# -*- coding: utf-8 -*-
"""A-03 定向测试：显式难度零值保留与非法输入拒绝（BUG-11；data_contract_v1 §4.2.5-6）。

冻结语义：
- difficulty_score=0（int/float）保留为显式 0、标签 easy、source=explicit；
- 字符串数值、布尔值、越界值（<0 或 >100）、非有限值一律拒绝（raise ValueError）；
- difficulty 字段：缺失/空白视为缺失；字符串数值拒绝；未知标签返回 None；
- difficulty_value / difficulty_percent 回退链与标签别名行为不变。
"""
import pytest

from learning_difficulty import existing_difficulty, infer_problem_difficulty


# —— 冻结：显式合法数值保留（原 BUG-11 主诉求）——
def test_explicit_zero_difficulty_score_preserved():
    result = existing_difficulty({"difficulty_score": 0})
    assert result == {"label": "easy", "score": 0.0}


def test_explicit_zero_float_preserved():
    assert existing_difficulty({"difficulty_score": 0.0})["score"] == 0.0


def test_explicit_zero_survives_infer_entrypoint():
    result = infer_problem_difficulty({"subject": "未知学科"}, {"difficulty_score": 0})
    assert result["difficulty_score"] == 0.0
    assert result["difficulty"] == "easy"
    assert result["source"] == "explicit"


def test_explicit_boundary_values_preserved():
    assert existing_difficulty({"difficulty_score": 100})["score"] == 100.0


# —— 冻结：非法输入拒绝（字符串数值 / 布尔 / 越界 / 非有限 / 非法类型）——
@pytest.mark.parametrize(
    "bad",
    [
        "0",
        "55",
        "1e2",
        ".5",
        "5.",
        "+1e1",
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        -3,
        120,
        {"a": 1},
        [55],
    ],
)
def test_invalid_difficulty_score_rejected(bad):
    with pytest.raises(ValueError):
        existing_difficulty({"difficulty_score": bad})


@pytest.mark.parametrize(
    "numeric_label",
    ["0", "1e2", ".5", "5.", "+1e1", "-2.5e-3", "inf", "nan"],
)
def test_numeric_string_label_rejected(numeric_label):
    with pytest.raises(ValueError):
        existing_difficulty({"difficulty": numeric_label})


def test_out_of_range_via_value_and_percent_rejected():
    with pytest.raises(ValueError):
        existing_difficulty({"difficulty_value": -3})
    with pytest.raises(ValueError):
        existing_difficulty({"difficulty_percent": 120})


# —— 冻结：缺失语义 → 推断链 ——
def test_missing_fields_return_none_for_inference_chain():
    assert existing_difficulty({}) is None
    assert existing_difficulty({"difficulty_score": None, "difficulty": None}) is None


def test_whitespace_difficulty_treated_as_missing():
    assert existing_difficulty({"difficulty": "   "}) is None


# —— 保持不变：回退链与别名 ——
def test_difficulty_value_and_percent_fallbacks_preserved():
    assert existing_difficulty({"difficulty_value": 70})["score"] == 70.0
    assert existing_difficulty({"difficulty_percent": 90})["score"] == 90.0


def test_label_aliases_unchanged():
    assert existing_difficulty({"difficulty": "easy"})["score"] == 25.0
    assert existing_difficulty({"difficulty": "中等"})["score"] == 55.0
    assert existing_difficulty({"difficulty": "困难"})["score"] == 85.0


def test_unknown_label_returns_none():
    assert existing_difficulty({"difficulty": "未知标签"}) is None
