from __future__ import annotations

import json
from collections import Counter
from datetime import date


MODEL = {
    "warning_policy": {
        "period_days": 3,
        "period_benchmark_score": 60,
        "bkt_model": {"enabled": True, "target_mastery": 0.72},
        "spaced_repetition_model": {"target_recall": 0.78},
    },
    "subjects": [
        {
            "name": "测试学科",
            "mastery": 0.4,
            "modules": [
                {
                    "name": "测试模块",
                    "status": "learning",
                    "topics": [
                        {"name": "主题甲", "status": "learning", "mastery": 0.4},
                        {"name": "主题乙", "status": "learning", "mastery": 0.5},
                    ],
                }
            ],
        }
    ],
}
RECORDS = [
    {
        "id": 1,
        "date": "2026-09-14",
        "subject": "测试学科",
        "module": "测试模块",
        "topic": "主题甲",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [
            {
                "title": "主题甲练习",
                "related_topics": ["主题甲"],
                "correctness": 80,
                "difficulty_score": 50,
            }
        ],
    }
]


def _write_fixture(tmp_path):
    from study_app.data import database

    model_path = tmp_path / "model.json"
    records_path = tmp_path / "records.json"
    db_path = tmp_path / "learning.sqlite"
    model_path.write_text(json.dumps(MODEL, ensure_ascii=False), encoding="utf-8")
    records_path.write_text(json.dumps({"records": RECORDS}, ensure_ascii=False), encoding="utf-8")
    database.initialize_database(db_path)
    with database.connect(db_path) as connection:
        database.import_model_json(connection, MODEL)
        database.import_records_json(connection, RECORDS)
    return model_path, records_path, db_path


def test_each_topic_parameter_key_computes_once_per_dashboard_refresh(tmp_path, monkeypatch):
    import learning_bkt
    from study_app.core import computation_context, dashboard

    model_path, records_path, db_path = _write_fixture(tmp_path)
    monkeypatch.setattr(dashboard, "DEFAULT_DB_PATH", db_path)
    bkt_calls = Counter()
    memory_calls = Counter()
    original_bkt = computation_context.topic_bkt_state
    original_memory = computation_context.topic_memory_state

    def bkt(*args, **kwargs):
        key = (args[0], args[1], args[2]["name"], kwargs.get("as_of_date"))
        bkt_calls[key] += 1
        return original_bkt(*args, **kwargs)

    def memory(*args, **kwargs):
        key = (args[0], args[1], args[2]["name"], args[4])
        memory_calls[key] += 1
        return original_memory(*args, **kwargs)

    monkeypatch.setattr(computation_context, "topic_bkt_state", bkt)
    monkeypatch.setattr(learning_bkt, "topic_bkt_state", bkt)
    monkeypatch.setattr(computation_context, "topic_memory_state", memory)

    state = dashboard.load_dashboard_state(
        model_path, records_path, today=date(2026, 9, 15)
    )

    assert state.subjects
    assert bkt_calls and max(bkt_calls.values()) == 1
    assert memory_calls and max(memory_calls.values()) == 1


def test_cached_context_matches_uncached_reference_for_all_topic_results():
    from study_app.core.computation_context import DashboardComputationContext

    context = DashboardComputationContext(
        MODEL, RECORDS, date(2026, 9, 15), MODEL["warning_policy"]
    )
    cached_bkt = context.bkt_topic_states()
    cached_memory = []
    for subject in MODEL["subjects"]:
        for module in subject["modules"]:
            for topic in module["topics"]:
                cached_memory.append(
                    context.memory_state(subject["name"], module["name"], topic)
                )

    from learning_bkt import bkt_topic_states
    from learning_memory import topic_memory_state

    reference_bkt = bkt_topic_states(MODEL, RECORDS, as_of_date=date(2026, 9, 15))
    reference_memory = [
        topic_memory_state(
            subject["name"], module["name"], topic, RECORDS, date(2026, 9, 15), MODEL["warning_policy"]
        )
        for subject in MODEL["subjects"]
        for module in subject["modules"]
        for topic in module["topics"]
    ]
    assert cached_bkt == reference_bkt
    assert cached_memory == reference_memory
