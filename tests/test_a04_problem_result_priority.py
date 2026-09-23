# -*- coding: utf-8 -*-
"""A-04 定向测试：题目证据优先级（BUG-01；data_contract_v1 §4.1）。

四象限验收（BKT/计分/同步对同一夹具语义一致）：
- 题目错误 + 记录总分 80 → 保持 0.0（不得抬升）；
- 题目正确 + 记录低分 → 保持 1.0；
- 题目部分正确 → 保持 0.5；
- 题目结果缺失 + 有总分 → 允许记录级兜底，且来源可识别（record_fallback）。
"""
import pytest

from learning_bkt import iter_topic_observations, topic_bkt_state
from learning_problem_result import interpret_problem_result

POLICY = {
    "bkt_model": {
        "enabled": True,
        "contextual_estimation": {"enabled": False},
        "time_effect": {"enabled": False},
        "personal_evidence_confidence": {"enabled": False},
    }
}
TOPIC = {"name": "哈希表", "mastery": 0.3, "difficulty": 0.5, "importance": 0.7}


def _record(score, problems):
    record = {
        "id": 101,
        "date": "2026-09-01",
        "subject": "数据结构与算法基础",
        "module": "查找、排序与算法分析",
        "topic": "哈希表",
        "activity": "exercise",
        "source": "outside_class",
    }
    if score is not None:
        record["score"] = score
    if problems is not None:
        record["problems"] = problems
    return record


def _observations(problem, score=80):
    records = [_record(score, [problem])]
    return iter_topic_observations(
        "数据结构与算法基础", "哈希表", TOPIC, records, POLICY
    )


# —— 四象限（BKT 观测层） ——
def test_wrong_problem_not_lifted_by_record_score():
    obs = _observations(
        {"title": "两数之和", "status": "wrong", "related_topics": ["哈希表"]},
        score=80,
    )
    assert obs[0]["correctness"] == 0.0
    assert obs[0]["result_source"] == "status"


def test_correct_problem_not_lowered_by_low_record_score():
    obs = _observations(
        {"title": "两数之和", "status": "correct", "related_topics": ["哈希表"]},
        score=30,
    )
    assert obs[0]["correctness"] == 1.0


def test_partial_problem_keeps_ratio():
    obs = _observations(
        {"title": "两数之和", "status": "partial", "related_topics": ["哈希表"]},
        score=65,
    )
    assert obs[0]["correctness"] == 0.5


def test_missing_result_uses_record_fallback_with_source():
    obs = _observations({"title": "两数之和", "related_topics": ["哈希表"]}, score=80)
    assert obs[0]["result_source"] == "record_fallback"
    assert obs[0]["correctness"] == 0.8


def test_bkt_state_uses_entry_semantics():
    records = [
        _record(
            80,
            [{"title": "两数之和", "status": "wrong", "related_topics": ["哈希表"]}],
        )
    ]
    state = topic_bkt_state(
        "数据结构与算法基础", "模块", TOPIC, records, POLICY
    )
    assert state["observation_count"] == 1
    assert state["last_observation"]["correctness"] == 0.0


# —— 共用入口单元语义 ——
def test_entry_p0_numeric_beats_status():
    result = interpret_problem_result({"correctness": 0.0, "status": "correct"})
    assert result == {"value": 0.0, "source": "explicit_numeric"}


def test_entry_percent_form_normalized():
    assert interpret_problem_result({"correctness": 80})["value"] == 0.8


def test_entry_partial_credit_fallback():
    assert interpret_problem_result({"partial_credit": 0.7})["value"] == 0.7


def test_entry_correct_bool_status():
    assert interpret_problem_result({"correct": True}) == {
        "value": 1.0,
        "source": "status",
    }


def test_entry_text_inference():
    assert (
        interpret_problem_result({"title": "T", "answer_result": "全对"})["value"]
        == 1.0
    )
    assert (
        interpret_problem_result({"title": "T", "answer_result": "10题错2题"})["value"]
        == 0.8
    )


def test_entry_record_fallback_source():
    assert interpret_problem_result({"title": "T"}, {"score": 80}) == {
        "value": 0.8,
        "source": "record_fallback",
    }


def test_entry_nothing_interpretable():
    assert interpret_problem_result({"title": "T"}) == {"value": None, "source": None}


# —— 消费者一致性（同一夹具；未修改这些消费者） ——
def test_database_and_monitor_agree_with_entry():
    from learning_monitor import difficulty_problem_score
    from study_app.data.database import _problem_correctness

    problem = {
        "title": "两数之和",
        "status": "wrong",
        "related_topics": ["哈希表"],
        "difficulty_score": 55,
    }
    record = _record(80, [problem])
    entry_value = interpret_problem_result(problem, record)["value"]
    assert entry_value == 0.0
    assert _problem_correctness(problem) == entry_value

    policy = {
        "difficulty_scoring": {
            "enabled": True,
            "numeric_scoring": {"enabled": True},
        }
    }
    score = difficulty_problem_score(problem, policy, record)
    expected = 8 + (58 - 8) * (0.55 ** 1.25)  # correctness = 0.0 的错题得分
    assert score == pytest.approx(expected)


def test_problem_associated_topic_record_fallback():
    """仅题目关联知识点（记录文本不关联）时，缺失结果仍应产生 record_fallback 观测。"""
    record = {
        "id": 102,
        "date": "2026-09-02",
        "subject": "数据结构与算法基础",
        "module": "图结构",
        "topic": "图的遍历",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [{"title": "两数之和", "related_topics": ["哈希表"]}],
    }
    obs = iter_topic_observations(
        "数据结构与算法基础", "哈希表", TOPIC, [record], POLICY
    )
    assert obs and obs[0]["result_source"] == "record_fallback"
    assert obs[0]["correctness"] == 0.8


CONFLICT_PROBLEM = {"partial_credit": 1.0, "correctness": 0.0, "difficulty_score": 55}


class TestConsumerUnification:
    """冲突夹具 correctness=0、partial_credit=1：所有消费者必须统一为 0.0。"""

    def test_shared_entry(self):
        assert interpret_problem_result(CONFLICT_PROBLEM)["value"] == 0.0

    def test_database(self):
        from study_app.data.database import _problem_correctness

        assert _problem_correctness(CONFLICT_PROBLEM) == 0.0

    def test_bkt_fraction(self):
        from learning_bkt import problem_correctness_fraction

        assert problem_correctness_fraction(CONFLICT_PROBLEM) == 0.0

        # 模型同步的观测级语义（含 result_sources）由
        # test_model_sync_uses_entry_and_reports_source 覆盖。

    def test_monitor_fraction(self):
        from learning_monitor import problem_correctness_fraction

        assert problem_correctness_fraction(CONFLICT_PROBLEM) == 0.0

    def test_monitor_scoring_conflict_scores_as_wrong(self):
        from learning_monitor import difficulty_problem_score

        record = _record(80, [CONFLICT_PROBLEM])
        policy = {
            "difficulty_scoring": {
                "enabled": True,
                "numeric_scoring": {"enabled": True},
            }
        }
        score = difficulty_problem_score(CONFLICT_PROBLEM, policy, record)
        expected = 8 + 50 * (0.55 ** 1.25)  # correctness = 0.0 的错题得分
        assert score == pytest.approx(expected)


def test_monitor_scoring_missing_result_uses_record_fallback():
    """结果缺失 + 记录总分 80：难度计分按 0.8 计，不再返回 None。"""
    from learning_monitor import difficulty_problem_score

    problem = {"title": "两数之和", "related_topics": ["哈希表"], "difficulty_score": 55}
    record = _record(80, [problem])
    policy = {
        "difficulty_scoring": {
            "enabled": True,
            "numeric_scoring": {"enabled": True},
        }
    }
    score = difficulty_problem_score(problem, policy, record)
    wrong_score = 8 + 50 * (0.55 ** 1.25)
    correct_score = 58 + 42 * (0.55 ** 1.15)
    assert score == pytest.approx(wrong_score + (correct_score - wrong_score) * 0.8)


class TestUIEstimatedScore:
    def test_conflict_problem_scores_as_wrong(self):
        """界面估计分走 record_score→难度计分链：冲突夹具按显式错误 0.0 计。"""
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest.mock import Mock

        from PySide6.QtWidgets import QApplication
        from study_app.ui import record_editor

        QApplication.instance() or QApplication([])
        with patch_app("study_app.data.database.list_module_names", ["数据结构"]):
            editor = record_editor.AddRecordDialog(None, Mock(), ("计算机科学",))
        editor.problems = [dict(CONFLICT_PROBLEM)]
        assert editor.estimate_record_score_from_problems() == pytest.approx(
            8 + 50 * (0.55 ** 1.25)
        )


def patch_app(target, return_value):
    from unittest.mock import patch

    return patch(target, return_value=return_value)
def patch_app(target, return_value):
    from unittest.mock import patch

    return patch(target, return_value=return_value)


def test_mixed_problems_keep_fallback_evidence():
    """同一记录：明确错误题 + 结果缺失题 → 两种观测并存，兜底不被丢弃。"""
    record = {
        "id": 103,
        "date": "2026-09-03",
        "subject": "数据结构与算法基础",
        "module": "查找、排序与算法分析",
        "topic": "哈希表",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [
            {"title": "两数之和", "status": "wrong", "related_topics": ["哈希表"]},
            {"title": "缺失结果题", "related_topics": ["哈希表"]},
        ],
    }
    obs = iter_topic_observations(
        "数据结构与算法基础", "哈希表", TOPIC, [record], POLICY
    )
    sources = [o["result_source"] for o in obs]
    assert "status" in sources
    assert "record_fallback" in sources
    fallback = [o for o in obs if o["result_source"] == "record_fallback"]
    assert fallback[0]["correctness"] == 0.8


def test_model_sync_uses_entry_and_reports_source():
    from study_app.data.model_progress_sync import topic_observation_from_record

    topic = {"name": "哈希表", "difficulty": 0.5}
    conflict = {
        "id": 201,
        "date": "2026-09-01",
        "subject": "计算机科学",
        "module": "查找、排序与算法分析",
        "topic": "哈希表",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [
            {
                "title": "两数之和",
                "partial_credit": 1.0,
                "correctness": 0.0,
                "related_topics": ["哈希表"],
            }
        ],
    }
    result = topic_observation_from_record(conflict, topic)
    assert result["correctness"] == 0.0
    assert result["result_sources"] == ["explicit_numeric"]

    missing = {
        "id": 202,
        "date": "2026-09-02",
        "subject": "计算机科学",
        "module": "查找、排序与算法分析",
        "topic": "哈希表",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [{"title": "两数之和", "related_topics": ["哈希表"]}],
    }
    result = topic_observation_from_record(missing, topic)
    assert result["correctness"] == 0.8
    assert result["result_sources"] == ["record_fallback"]


class TestUIDisplaySemantics:
    """仅 status="wrong" 的题目：界面正确率/估分影响不得显示待判断。"""

    def _editor(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest.mock import Mock

        from PySide6.QtWidgets import QApplication
        from study_app.ui import record_editor

        QApplication.instance() or QApplication([])
        with patch_app("study_app.data.database.list_module_names", ["数据结构"]):
            return record_editor.AddRecordDialog(None, Mock(), ("计算机科学",))

    def _problem(self):
        return {
            "title": "两数之和",
            "status": "wrong",
            "related_topics": ["哈希表"],
            "difficulty_score": 55,
        }

    def test_display_text_shows_zero_not_pending(self):
        editor = self._editor()
        text = editor._problem_display_text(self._problem())
        assert "正确率 0.0%" in text
        assert "待判断" not in text

    def test_preview_shows_zero_and_impact_not_pending(self):
        editor = self._editor()
        preview = editor._problem_preview_text(self._problem())
        assert "正确率：0.0%" in preview
        assert "估分影响：中等题做错" in preview
        assert "待判断" not in preview
