"""章节图学习状态与掌握度聚合测试（chapter-graph-v1 合同 §7.2/§7.3/§17.3-6）。

全部使用临时目录合成数据库：F5 学科/模块/结构 + F2 注册 + 合成学习记录；
未知掌握度必须为 ``None``，绝不伪装成 ``0%``。
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from pathlib import Path

import pytest

from study_app.core.chapter_graph_projection import (
    build_chapter_graph_snapshot,
    list_graph_subjects,
)
from study_app.core.topic_identity import topic_key_for_id
from study_app.core.topic_insights import mastery_interval as existing_mastery_interval
from study_app.data.database import (
    KNOWLEDGE_PREREQUISITES_TABLE_SQL,
    initialize_database,
)
from study_app.data.subject_repository import (
    SubjectCatalogRepository,
    install_subject_lifecycle_schema,
)
from study_app.core.subject_catalog import adopt_structure


AS_OF = "2026-09-20"
SUBJECT_NAME = "离散数学"
MODULE_A = "集合与关系"
MODULE_B = "图论基础"
TOPIC_1 = "等价关系"
TOPIC_2 = "函数"
TOPIC_3 = "树"


def _uuid_factory(start: int):
    counter = iter(range(start, start + 500))
    return lambda: uuid.UUID(f"00000000-0000-0000-0000-{next(counter):012x}")


def _new_db(tmp_path: Path, name: str = "chapter_graph_states") -> Path:
    db_path = tmp_path / f"{name}.sqlite"
    initialize_database(db_path)
    install_subject_lifecycle_schema(db_path)
    return db_path
    initialize_database(db_path)
    install_subject_lifecycle_schema(db_path)
    return db_path


def _install_f2_tables(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_topic_registry(
                topic_key TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL UNIQUE REFERENCES topics(id) ON DELETE RESTRICT,
                identity_version TEXT NOT NULL
            )
            """
        )
        connection.executescript(KNOWLEDGE_PREREQUISITES_TABLE_SQL)


def _register_legacy_topics(
    db_path: Path, subject_name: str, module_topics: dict[str, tuple[str, ...]]
) -> dict[str, tuple[str, ...]]:
    keys_by_module: dict[str, tuple[str, ...]] = {}
    with sqlite3.connect(db_path) as connection:
        legacy_subject_id = connection.execute(
            "INSERT INTO subjects(name, status) VALUES (?, 'active')",
            (subject_name,),
        ).lastrowid
        for module_name, topic_names in module_topics.items():
            legacy_module_id = connection.execute(
                "INSERT INTO modules(subject_id, name) VALUES (?, ?)",
                (legacy_subject_id, module_name),
            ).lastrowid
            keys: list[str] = []
            for topic_name in topic_names:
                topic_id = connection.execute(
                    """
                    INSERT INTO topics(module_id, name, status, mastery, importance, difficulty, source_json)
                    VALUES (?, ?, 'learning', 0, 0, 0, '{}')
                    """,
                    (legacy_module_id, topic_name),
                ).lastrowid
                topic_key = topic_key_for_id(topic_id)
                connection.execute(
                    """
                    INSERT INTO knowledge_topic_registry(topic_key, topic_id, identity_version)
                    VALUES (?, ?, 'topic-identity-v1')
                    """,
                    (topic_key, topic_id),
                )
                keys.append(topic_key)
            keys_by_module[module_name] = tuple(keys)
    return keys_by_module


def _create_subject(db_path: Path, name: str, module_names: tuple[str, ...], *, start: int = 17):
    repo = SubjectCatalogRepository(db_path, uuid_factory=_uuid_factory(start))
    subject = repo.create_subject(name)
    modules = tuple(
        repo.create_module(subject.subject_key, module_name) for module_name in module_names
    )
    return repo, subject, modules


_ADOPT_UUIDS = iter(range(900, 90000))


def _adopt_structure(db_path: Path, subject, module_topic_keys: dict) -> str:
    return adopt_structure(
        db_path,
        subject.subject_key,
        module_topic_keys,
        source_reference="test:chapter-graph-states",
        uuid_factory=lambda: uuid.UUID(
            f"00000000-0000-0000-0000-{next(_ADOPT_UUIDS):012x}"
        ),
    )


def _set_importance(db_path: Path, subject, topic_key: str, importance_bp: int | None) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE subject_structure_topics SET importance_bp = ?
            WHERE topic_key = ? AND structure_version = (
                SELECT structure_version FROM subject_structure_versions
                WHERE subject_key = ? AND is_current = 1
            )
            """,
            (importance_bp, topic_key, subject.subject_key),
        )


def _add_record(
    db_path: Path,
    *,
    date: str,
    topic: str,
    activity: str = "review",
    score: object = None,
    problems: list | None = None,
) -> None:
    payload = {
        "date": date,
        "subject": SUBJECT_NAME,
        "module": None,
        "topic": topic,
        "activity": activity,
        "source": "outside_class",
        "score": score,
        "note": "",
        "problems": problems or [],
    }
    raw = json.dumps(payload, ensure_ascii=False)  # NaN/Infinity 按字面写入，测试非有限守卫
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO learning_records(
                record_date, subject_name, module_name, topic_name, activity,
                source, score, duration_minutes, note, raw_json
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, '', ?)
            """,
            (date, SUBJECT_NAME, topic, activity, "outside_class", score, raw),
        )


def _archive_subject(db_path: Path, subject) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE subject_catalog SET lifecycle_status = 'archived' WHERE subject_key = ?",
            (subject.subject_key,),
        )


def _node_by_display(snapshot, display_name: str):
    matches = [node for node in snapshot.nodes if node.display_name == display_name]
    assert len(matches) == 1, f"节点缺失或重复：{display_name}"
    return matches[0]


@pytest.fixture
def unbound_db(tmp_path):
    """F5 + F2 已安装、当前结构存在、但零知识点绑定（生产六学科现状）。"""
    db_path = _new_db(tmp_path, "unbound")
    _install_f2_tables(db_path)
    _repo, subject, modules = _create_subject(
        db_path, SUBJECT_NAME, (MODULE_A, MODULE_B), start=17
    )
    _adopt_structure(db_path, subject, {module.module_key: () for module in modules})
    return db_path, subject, modules


@pytest.fixture
def bound_db(tmp_path):
    """两个模块、三个绑定知识点；importance_bp 留空由各测试自行设置。"""
    db_path = _new_db(tmp_path, "bound")
    _install_f2_tables(db_path)
    _repo, subject, modules = _create_subject(
        db_path, SUBJECT_NAME, (MODULE_A, MODULE_B), start=17
    )
    keys_by_module = _register_legacy_topics(
        db_path,
        SUBJECT_NAME,
        {MODULE_A: (TOPIC_1, TOPIC_2), MODULE_B: (TOPIC_3,)},
    )
    _adopt_structure(
        db_path,
        subject,
        {
            modules[0].module_key: keys_by_module[MODULE_A],
            modules[1].module_key: keys_by_module[MODULE_B],
        },
    )
    topic_keys = {
        TOPIC_1: keys_by_module[MODULE_A][0],
        TOPIC_2: keys_by_module[MODULE_A][1],
        TOPIC_3: keys_by_module[MODULE_B][0],
    }
    return db_path, subject, modules, topic_keys


def test_unbound_topics_yield_unlearned_and_never_zero_mastery(unbound_db):
    db_path, subject, _modules = unbound_db
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert len(snapshot.nodes) == 2
    for node in snapshot.nodes:
        assert node.learning_state == "unlearned"
        assert node.topic_count == 0
        assert node.coverage_count == 0
        assert node.mastery_point is None
        assert node.mastery_point != 0.0  # 缺失值 ≠ 0 分
        assert node.mastery_interval is None
        assert node.evidence_confidence == 0.0
        assert "structure_topics_unbound" in node.diagnostics


def test_bound_topics_without_evidence_stay_unlearned(bound_db):
    db_path, subject, _modules, _keys = bound_db
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "unlearned"
    assert module_a.topic_count == 2
    assert module_a.coverage_count == 0
    assert module_a.mastery_point is None
    assert "structure_topics_unbound" not in module_a.diagnostics
    assert module_a.evidence_confidence == 0.0


def test_partial_coverage_marks_partial_without_mastery_point(bound_db):
    db_path, subject, _modules, _keys = bound_db
    _add_record(db_path, date="2026-09-01", topic=TOPIC_1)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "partial"
    assert module_a.coverage_count == 1
    assert module_a.topic_count == 2
    assert module_a.mastery_point is None
    assert module_a.mastery_point != 0.0
    assert "mastery_topics_excluded:1" in module_a.diagnostics
    # 未被覆盖的模块保持未学。
    assert _node_by_display(snapshot, MODULE_B).learning_state == "unlearned"


def test_full_coverage_without_assessment_is_learned_unassessed(bound_db):
    db_path, subject, _modules, _keys = bound_db
    _add_record(db_path, date="2026-09-01", topic=TOPIC_1, activity="class")
    _add_record(db_path, date="2026-09-02", topic=TOPIC_2, activity="review")
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "learned_unassessed"
    assert module_a.coverage_count == module_a.topic_count == 2
    assert module_a.mastery_point is None
    assert module_a.mastery_interval is None
    assert module_a.evidence_confidence == 0.0
    assert "mastery_topics_excluded:2" in module_a.diagnostics


def test_learned_assessed_weighted_by_importance_bp(bound_db):
    db_path, subject, _modules, keys = bound_db
    _set_importance(db_path, subject, keys[TOPIC_1], 7500)
    _set_importance(db_path, subject, keys[TOPIC_2], 2500)
    _add_record(db_path, date="2026-09-01", topic=TOPIC_1, activity="self_test", score=80)
    _add_record(db_path, date="2026-09-02", topic=TOPIC_2, activity="self_test", score=40)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "learned_assessed"
    # Beta(2,2) 收缩后：0.75*0.56 + 0.25*0.48 = 0.54。
    assert module_a.mastery_point == pytest.approx(0.54)
    assert module_a.mastery_point is not None and math.isfinite(module_a.mastery_point)
    # 每个知识点 1 次观察，证据比为 1/(1+4)=0.2。
    assert module_a.evidence_confidence == pytest.approx(0.2)
    expected_interval = existing_mastery_interval(module_a.mastery_point, 0.2, 2)
    assert module_a.mastery_interval is not None
    assert module_a.mastery_interval[0] == pytest.approx(expected_interval[0])
    assert module_a.mastery_interval[1] == pytest.approx(expected_interval[1])
    assert module_a.mastery_interval[0] < module_a.mastery_point < module_a.mastery_interval[1]


def test_missing_importance_bp_falls_back_to_equal_weights(bound_db):
    db_path, subject, _modules, _keys = bound_db
    _add_record(db_path, date="2026-09-01", topic=TOPIC_1, activity="self_test", score=80)
    _add_record(db_path, date="2026-09-02", topic=TOPIC_2, activity="self_test", score=40)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "learned_assessed"
    # 等权聚合两个收缩后的知识点估计。
    assert module_a.mastery_point == pytest.approx(0.52)


def test_partially_missing_importance_bp_uses_mean_of_known_weights(bound_db):
    db_path, subject, _modules, keys = bound_db
    _set_importance(db_path, subject, keys[TOPIC_1], 7500)
    # TOPIC_2 保持 NULL → 权重取已有均值 7500 → 等价等权。
    _add_record(db_path, date="2026-09-01", topic=TOPIC_1, activity="self_test", score=80)
    _add_record(db_path, date="2026-09-02", topic=TOPIC_2, activity="self_test", score=40)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.mastery_point == pytest.approx(0.52)


def test_problem_related_topics_provide_evidence(bound_db):
    db_path, subject, _modules, _keys = bound_db
    _add_record(
        db_path,
        date="2026-09-03",
        topic="别的知识点",
        activity="exercise",
        problems=[{"title": "题1", "related_topics": [TOPIC_3], "correctness": 0.7}],
    )
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_b = _node_by_display(snapshot, MODULE_B)
    assert module_b.coverage_count == 1
    assert module_b.learning_state == "learned_assessed"
    assert module_b.mastery_point == pytest.approx(0.54)


def test_partial_coverage_exposes_provisional_shrunk_mastery(bound_db):
    db_path, subject, _modules, _keys = bound_db
    _add_record(db_path, date="2026-09-01", topic=TOPIC_1, activity="self_test", score=100)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "partial"
    assert module_a.coverage_count == 1
    assert module_a.mastery_point == pytest.approx(0.6)
    assert module_a.mastery_point < 1.0
    # 单个已评估知识点占本章 1/2，置信度再折半。
    assert module_a.evidence_confidence == pytest.approx(0.1)


def test_non_finite_and_out_of_range_mastery_inputs_are_excluded(bound_db):
    db_path, subject, _modules, _keys = bound_db
    # NaN 分数：覆盖成立但评估证据无效。
    _add_record(db_path, date="2026-09-01", topic=TOPIC_1, activity="self_test", score=float("nan"))
    # Infinity correctness：同样只排除评估值。
    _add_record(
        db_path,
        date="2026-09-02",
        topic=TOPIC_2,
        activity="exercise",
        problems=[{"title": "题2", "related_topics": [TOPIC_2], "correctness": float("inf")}],
    )
    # 越界分数：>100 拒绝。
    _add_record(db_path, date="2026-09-03", topic=TOPIC_3, activity="self_test", score=150)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "learned_unassessed"
    assert module_a.mastery_point is None
    assert "mastery_invalid_evidence_excluded:2" in module_a.diagnostics
    module_b = _node_by_display(snapshot, MODULE_B)
    assert module_b.learning_state == "learned_unassessed"
    assert "mastery_invalid_evidence_excluded:1" in module_b.diagnostics
    for node in snapshot.nodes:
        assert node.mastery_point is None or math.isfinite(node.mastery_point)
        assert 0.0 <= node.evidence_confidence <= 1.0


def test_records_after_as_of_date_are_ignored(bound_db):
    db_path, subject, _modules, _keys = bound_db
    _add_record(db_path, date="2026-09-25", topic=TOPIC_1, activity="self_test", score=90)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.learning_state == "unlearned"
    assert module_a.coverage_count == 0
    assert module_a.mastery_point is None


def test_other_subject_records_do_not_leak(bound_db, tmp_path):
    db_path, subject, _modules, _keys = bound_db
    _repo2, other, _m = _create_subject(db_path, "物理学", ("力学",), start=400)
    _register_legacy_topics(db_path, "物理学", {"力学": ("动量",)})
    _adopt_structure(db_path, other, {_m[0].module_key: ()})
    with sqlite3.connect(db_path) as connection:
        payload = json.dumps(
            {
                "date": "2026-09-01",
                "subject": "物理学",
                "topic": TOPIC_1,
                "activity": "review",
                "score": 90,
            },
            ensure_ascii=False,
        )
        connection.execute(
            """
            INSERT INTO learning_records(
                record_date, subject_name, module_name, topic_name, activity,
                source, score, duration_minutes, note, raw_json
            ) VALUES ('2026-09-01', '物理学', NULL, ?, 'review', 'outside_class', 90, NULL, '', ?)
            """,
            (TOPIC_1, payload),
        )
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    module_a = _node_by_display(snapshot, MODULE_A)
    assert module_a.coverage_count == 0
    assert module_a.learning_state == "unlearned"


def test_archived_subject_hidden_by_default_and_readonly_when_enabled(tmp_path):
    db_path = _new_db(tmp_path)
    _install_f2_tables(db_path)
    _repo, subject, modules = _create_subject(db_path, "陈旧学科", ("旧章节",), start=17)
    _adopt_structure(db_path, subject, {modules[0].module_key: ()})
    _archive_subject(db_path, subject)

    hidden = build_chapter_graph_snapshot(
        subject.subject_key, db_path=db_path, as_of_date=AS_OF, include_archived=False
    )
    assert hidden.nodes == ()
    assert hidden.edges == ()
    assert hidden.diagnostics == ("subject_archived",)

    history = build_chapter_graph_snapshot(
        subject.subject_key, db_path=db_path, as_of_date=AS_OF, include_archived=True
    )
    assert len(history.nodes) == 1
    node = history.nodes[0]
    assert node.learning_state == "archived"
    assert "archived_readonly" in node.diagnostics
    assert "archived_computed_state:unlearned" in node.diagnostics
    assert node.mastery_point is None

    # 默认学科清单不包含归档学科；显式开启后只读出现且排在最后。
    assert list_graph_subjects(db_path) == ()
    rows = list_graph_subjects(db_path, include_archived=True)
    assert [row["lifecycle_status"] for row in rows] == ["archived"]
    assert rows[0]["subject_key"] == subject.subject_key


def test_unknown_mastery_is_never_zero_anywhere(bound_db, unbound_db):
    for db_path, subject in (bound_db[:2], unbound_db[:2]):
        snapshot = build_chapter_graph_snapshot(
            subject.subject_key, db_path=db_path, as_of_date=AS_OF
        )
        for node in snapshot.nodes:
            if node.learning_state in {"unlearned", "partial", "learned_unassessed", "archived"}:
                assert node.mastery_point is None
                assert node.mastery_point != 0.0
                assert node.mastery_interval is None
