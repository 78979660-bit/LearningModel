from __future__ import annotations

from datetime import date

import pytest

from learning_bkt import bkt_topic_states, topic_bkt_state
from learning_memory import topic_memory_state
from learning_monitor import generate_report
from study_app.core.study_phase import recent_review_profile, weighted_topic_priority_states


AS_OF = date(2026, 9, 12)
POLICY = {
    "period_days": 3,
    "period_benchmark_score": 60,
    "stale_review_days": 6,
    "spaced_repetition_model": {
        "base_half_life_days": 1.2,
        "target_recall": 0.78,
        "high_risk_recall": 0.45,
    },
    "bkt_model": {
        "enabled": True,
        "target_mastery": 0.72,
        "high_risk_mastery": 0.45,
        "contextual_estimation": {"enabled": False},
        "time_effect": {"enabled": False},
        "personal_evidence_confidence": {"enabled": False},
    },
}
TOPIC = {
    "name": "Green 公式",
    "status": "learning",
    "mastery": 0.4,
    "difficulty": 0.6,
    "importance": 0.8,
    "forgetting_risk": 0.2,
}
MODEL = {
    "warning_policy": POLICY,
    "subjects": [
        {
            "name": "高等数学",
            "modules": [
                {
                    "name": "多元函数积分学",
                    "weight": 1,
                    "mastery": 0.4,
                    "topics": [TOPIC],
                }
            ],
        }
    ],
}
PAST = {
    "id": 1,
    "date": "2026-09-11",
    "subject": "高等数学",
    "module": "多元函数积分学",
    "topic": "Green 公式",
    "activity": "review",
    "source": "outside_class",
    "score": 20,
}
FUTURE = {
    "id": 2,
    "date": "2026-09-13",
    "subject": "高等数学",
    "module": "多元函数积分学",
    "topic": "Green 公式",
    "activity": "review",
    "source": "outside_class",
    "score": 100,
}


def test_memory_and_bkt_ignore_future_evidence():
    records = [PAST, FUTURE]

    memory = topic_memory_state(
        "高等数学",
        "多元函数积分学",
        TOPIC,
        records,
        AS_OF,
        POLICY,
    )
    bkt = topic_bkt_state(
        "高等数学",
        "多元函数积分学",
        TOPIC,
        records,
        POLICY,
        as_of_date=AS_OF,
    )

    assert memory["review_count"] == 1
    assert memory["last_review"] == date(2026, 9, 11)
    assert bkt["observation_count"] == 1
    assert bkt["last_observation"]["record_id"] == 1


def test_report_is_identical_with_or_without_future_evidence():
    baseline = generate_report(MODEL, [PAST], AS_OF)
    with_future = generate_report(MODEL, [PAST, FUTURE], AS_OF)

    assert with_future == baseline
    assert "2026-09-13" not in with_future


def test_recent_coverage_and_priority_share_as_of_date(monkeypatch):
    monkeypatch.setattr(
        "study_app.core.study_phase.get_subject_phase",
        lambda _subject: {
            "phase": "regular",
            "label": "常规学习",
            "recent_review_window": 5,
            "priority_weights": {
                "mastery_deficit": 0.25,
                "recent_error": 0.15,
                "forgetting_risk": 0.38,
                "recent_coverage_gap": 0.12,
                "recent_review_saturation": 0.10,
            },
        },
    )
    monkeypatch.setattr("study_app.core.study_phase.is_archived", lambda _subject: False)
    monkeypatch.setattr(
        "study_app.core.study_phase.is_in_exam_scope",
        lambda *_args, **_kwargs: True,
    )

    profile = recent_review_profile(
        "高等数学",
        [PAST, FUTURE],
        as_of_date=AS_OF,
    )
    states = bkt_topic_states(MODEL, [PAST, FUTURE], as_of_date=AS_OF)
    priorities = weighted_topic_priority_states(
        "高等数学",
        model=MODEL,
        records=[PAST, FUTURE],
        states=states,
        as_of_date=AS_OF,
    )

    assert [record["id"] for record in profile["records"]] == [1]
    assert len(priorities) == 1
    assert priorities[0]["observation_count"] == 1
    assert priorities[0]["recent_window_size"] == 1


def test_weighted_priority_requires_explicit_as_of_date():
    with pytest.raises(TypeError):
        weighted_topic_priority_states("高等数学")
