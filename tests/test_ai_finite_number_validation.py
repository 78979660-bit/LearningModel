# -*- coding: utf-8 -*-
"""A-02 定向测试：非有限数与布尔数值验证（BUG-06；data_contract_v1 §4.2）。

覆盖分支：
- require_number_range 拒绝 NaN/±inf/bool，保留 0/1/100 等合法边界；
- validate_record_parser_output 拒绝 related_topics 中的非字符串元素；
- normalize_llm_problem 双重拒绝（NaN/inf/bool 不再被规范化为全对或难度 100）；
- 缺失字段保持缺失语义；difficulty_score=0 保留。
"""
import math

import pytest

from study_app.ai.validation import (
    LLMValidationError,
    require_number_range,
    require_string_list,
    validate_record_parser_output,
)
from study_app.ai.record_parser import normalize_llm_problem


class TestRequireNumberRange:
    @pytest.mark.parametrize(
        "bad",
        [float("nan"), float("inf"), float("-inf"), True, False],
    )
    def test_non_finite_and_bool_rejected(self, bad):
        with pytest.raises(LLMValidationError):
            require_number_range(bad, "correctness", 0, 1)

    @pytest.mark.parametrize("good", [0, 1, 0.5, 100])
    def test_valid_boundaries_preserved(self, good):
        assert require_number_range(good, "value", 0, 100) == float(good)

    def test_out_of_range_still_rejected(self):
        with pytest.raises(LLMValidationError):
            require_number_range(120, "difficulty_score", 0, 100)


class TestRelatedTopicsElements:
    def test_non_string_element_rejected(self):
        with pytest.raises(LLMValidationError):
            validate_record_parser_output(
                {"problems": [{"title": "题目T", "related_topics": ["哈希表", 3]}]}
            )

    def test_empty_string_element_rejected(self):
        with pytest.raises(LLMValidationError):
            validate_record_parser_output(
                {"problems": [{"title": "题目T", "related_topics": ["  "]}]}
            )

    def test_string_elements_accepted(self):
        validate_record_parser_output(
            {"problems": [{"title": "题目T", "related_topics": ["哈希表", "AVL 树"]}]}
        )


class TestNormalizeDoubleRejection:
    def test_nan_correctness_rejected(self):
        with pytest.raises(LLMValidationError):
            normalize_llm_problem({"title": "T", "correctness": float("nan")})

    def test_inf_difficulty_rejected(self):
        with pytest.raises(LLMValidationError):
            normalize_llm_problem({"title": "T", "difficulty_score": float("inf")})

    def test_bool_correctness_rejected(self):
        with pytest.raises(LLMValidationError):
            normalize_llm_problem({"title": "T", "correctness": True})

    def test_zero_difficulty_preserved(self):
        item = normalize_llm_problem({"title": "T", "difficulty_score": 0})
        assert item["difficulty_score"] == 0.0
        assert item["difficulty"] == "easy"

    def test_missing_fields_stay_missing(self):
        item = normalize_llm_problem({"title": "T"})
        assert "correctness" not in item
        assert "status" not in item
        assert "difficulty_score" not in item

    def test_valid_values_still_normalized(self):
        item = normalize_llm_problem(
            {"title": "T", "correctness": 0.5, "difficulty_score": 55}
        )
        assert item["status"] == "partial"
        assert item["answer_result"] == "部分正确"
        assert item["difficulty_score"] == 55.0


class TestRequireStringList:
    @pytest.mark.parametrize("bad", [[123], [True], ["ok", 3], ["ok", "  "], [""]])
    def test_non_string_and_blank_elements_rejected(self, bad):
        with pytest.raises(LLMValidationError):
            require_string_list(bad, "items")

    def test_valid_list_preserved_and_stripped(self):
        assert require_string_list([" 哈希表 ", "AVL"], "items") == ["哈希表", "AVL"]

    def test_empty_list_below_minimum_rejected(self):
        with pytest.raises(LLMValidationError):
            require_string_list([], "items")


class TestRejectionChain:
    def test_rejected_llm_result_causes_zero_write_and_zero_sync(self, monkeypatch):
        """校验拒绝后：记录写入与模型同步均不得被调用（data_contract_v1 §4.2.4）。"""
        import study_app.ai.record_parser as record_parser_module
        import study_app.data.database as database_module
        import study_app.data.model_progress_sync as sync_module

        calls = {"add_learning_record": 0, "sync_learning_record_to_model": 0}

        def _record_write(*args, **kwargs):
            calls["add_learning_record"] += 1
            return 1

        def _model_sync(*args, **kwargs):
            calls["sync_learning_record_to_model"] += 1
            return []

        monkeypatch.setattr(
            record_parser_module,
            "parse_record_with_llm",
            lambda payload: {"problems": [{"title": "T", "correctness": float("nan")}]},
        )
        monkeypatch.setattr(database_module, "add_learning_record", _record_write)
        monkeypatch.setattr(sync_module, "sync_learning_record_to_model", _model_sync)

        with pytest.raises(LLMValidationError):
            record_parser_module.parse_record_payload_with_llm({"note": "x"})

        assert calls == {"add_learning_record": 0, "sync_learning_record_to_model": 0}
