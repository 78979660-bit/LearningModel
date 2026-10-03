from __future__ import annotations

from study_app.core.dashboard import covered_mastery_stats, is_covered_learning_item


def test_learned_status_counts_without_records_but_module_only_record_does_not_cover_other_topics():
    subject = {
        "name": "测试学科",
        "modules": [{
            "name": "第一章",
            "weight": 1,
            "mastery": 0.3,
            "topics": [
                {"name": "甲知识点", "status": "learned_needs_review", "mastery": 0.6},
                {"name": "乙知识点", "status": "not_started", "mastery": 0.0},
            ],
        }],
    }
    policy = {"bkt_model": {"enabled": False}}

    assert covered_mastery_stats(subject, [], policy) == (60, 1, 2)
    module_record = [{"subject": "测试学科", "module": "第一章"}]
    assert covered_mastery_stats(subject, module_record, policy) == (60, 1, 2)
    topic_record = module_record + [{"subject": "测试学科", "topic": "乙知识点"}]
    assert covered_mastery_stats(subject, topic_record, policy) == (30, 2, 2)


def test_module_without_topics_can_use_module_level_record():
    assert is_covered_learning_item(
        "测试学科", "第一章", {"name": "第一章"}, "not_started",
        [{"subject": "测试学科", "module": "第一章"}],
        module_only=True,
    )


def test_topic_named_like_module_needs_topic_evidence():
    assert not is_covered_learning_item(
        "测试学科", "第一章", {"name": "第一章"}, "not_started",
        [{"subject": "测试学科", "module": "第一章"}],
    )
