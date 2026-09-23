from __future__ import annotations

from study_app.core.discrete_math_taxonomy import (
    DISCRETE_TEMPLATE_INFO,
    classify_discrete_text,
    discrete_template_ids,
)
from study_app.core.practice_bank import known_template_ids, template_for_topic


def test_discrete_taxonomy_has_one_template_per_module() -> None:
    assert len(DISCRETE_TEMPLATE_INFO) == 16
    assert discrete_template_ids() <= known_template_ids()


def test_discrete_topic_routes_to_specific_template() -> None:
    assert template_for_topic("离散数学 / 偏序关系与Hasse图", "离散数学")[0] == "DM-REL-FUNCTION"
    assert template_for_topic("离散数学 / 图着色", "离散数学")[0] == "DM-GRAPHS"
    assert template_for_topic("离散数学 / 图灵机", "离散数学")[0] == "DM-COMPUTATION"


def test_keyword_classification_preserves_fine_grained_topic() -> None:
    template_id, module, topic = classify_discrete_text("用卡诺图求布尔函数的最小积之和表达式")
    assert template_id == "DM-BOOLEAN"
    assert module == "布尔代数"
    assert topic == "布尔函数最小化"

