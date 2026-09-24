from learning_memory import is_outside_class_review as memory_rule
from learning_monitor import is_outside_class_review as monitor_rule


def test_monitor_and_memory_share_the_same_review_rule():
    assert monitor_rule is memory_rule
    examples = (
        ({"source": "outside_class"}, True),
        ({"source": "classroom", "activity": "review"}, False),
        ({"activity": "exercise"}, True),
        ({"tags": ["课外复习"]}, True),
        ({"activity": "lecture"}, False),
    )
    for record, expected in examples:
        assert monitor_rule(record) is expected
