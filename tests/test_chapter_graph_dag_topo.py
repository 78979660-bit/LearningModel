"""章节图强先修 DAG 校验与稳定拓扑排序测试（chapter-graph-v1 合同 §8/§9/§17.7-10）。

单元测试直接构造 ChapterNode/ChapterEdge；集成测试使用临时合成数据库
验证 knowledge_prerequisites → 模块级强边的投影方向与诊断。
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from study_app.core.chapter_graph_projection import (
    ChapterEdge,
    ChapterGraphSnapshot,
    ChapterNode,
    build_chapter_graph_snapshot,
)
from study_app.core.chapter_graph_topology import topological_rank, validate_strong_dag
from study_app.core.topic_identity import topic_key_for_id
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


def _node(key: str, order: int, name: str | None = None) -> ChapterNode:
    return ChapterNode(
        module_key=key,
        display_name=name or key,
        module_order=order,
        topological_rank=0,
        learning_state="unlearned",
        mastery_point=None,
        mastery_interval=None,
        coverage_count=0,
        topic_count=0,
        evidence_confidence=0.0,
    )


def _edge(f: str, t: str, relation: str = "strong_prerequisite") -> ChapterEdge:
    return ChapterEdge(
        from_module_key=f,
        to_module_key=t,
        relation_type=relation,
        source="user_confirmed",
    )


def _ranked(nodes, edges) -> tuple[str, ...]:
    return tuple(node.module_key for node in topological_rank(nodes, edges))


def _uuid_factory(start: int):
    counter = iter(range(start, start + 500))
    return lambda: uuid.UUID(f"00000000-0000-0000-0000-{next(counter):012x}")


def _new_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "chapter_graph_topo.sqlite"
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


def _add_prerequisite(db_path: Path, topic_key: str, prerequisite_topic_key: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO knowledge_prerequisites(
                topic_key, prerequisite_topic_key, source, source_json
            ) VALUES (?, ?, 'user_confirmed', '{}')
            """,
            (topic_key, prerequisite_topic_key),
        )


# ---------------------------------------------------------------- 校验单元测试


def test_empty_edge_set_is_valid():
    assert validate_strong_dag((), ()) == ()
    assert validate_strong_dag((_edge("a", "b"),), ("a", "b")) == ()


def test_self_loop_rejected():
    diagnostics = validate_strong_dag((_edge("a", "a"),), ("a", "b"))
    assert "self_loop:a" in diagnostics


def test_duplicate_edge_rejected():
    diagnostics = validate_strong_dag((_edge("a", "b"), _edge("a", "b")), ("a", "b"))
    assert "duplicate_edge:a->b" in diagnostics
    # 去重后的单条边仍然合法（除其他问题外无新增诊断）。
    assert "duplicate_edge:a->b" not in validate_strong_dag((_edge("a", "b"),), ("a", "b"))


def test_unknown_endpoint_rejected():
    diagnostics = validate_strong_dag((_edge("a", "ghost"),), ("a", "b"))
    assert "endpoint_unknown:ghost" in diagnostics
    diagnostics = validate_strong_dag((_edge("phantom", "a"),), ("a", "b"))
    assert "endpoint_unknown:phantom" in diagnostics


def test_cross_subject_endpoint_rejected_by_membership():
    """跨学科端点不在本学科 module_key 集合内 → 按成员资格拒绝。"""
    diagnostics = validate_strong_dag(
        (_edge("module:other-subject", "a"),),
        ("a", "b"),
    )
    assert "endpoint_unknown:module:other-subject" in diagnostics


def test_three_node_cycle_rejected_with_full_path():
    diagnostics = validate_strong_dag(
        (_edge("A", "B"), _edge("B", "C"), _edge("C", "A")),
        ("A", "B", "C"),
    )
    assert "循环路径: A → B → C → A" in diagnostics


def test_two_node_cycle_message():
    diagnostics = validate_strong_dag((_edge("B", "A"), _edge("A", "B")), ("A", "B"))
    assert "循环路径: A → B → A" in diagnostics


def test_supporting_edges_never_validated_or_ranked():
    # 辅助关系可以成环，也不进入校验/拓扑（§8.3）。
    cycle = (
        _edge("a", "b", relation="supporting_relation"),
        _edge("b", "a", relation="supporting_relation"),
    )
    assert validate_strong_dag(cycle, ("a", "b")) == ()
    nodes = (_node("a", 0), _node("b", 1))
    ranked = topological_rank(nodes, cycle)
    assert all(node.topological_rank == 0 for node in ranked)


def test_cycle_with_supporting_noise_still_detected():
    edges = (
        _edge("A", "B"),
        _edge("B", "C"),
        _edge("C", "A"),
        _edge("A", "C", relation="supporting_relation"),
    )
    diagnostics = validate_strong_dag(edges, ("A", "B", "C"))
    assert "循环路径: A → B → C → A" in diagnostics
    assert not any(d.startswith("endpoint_unknown") for d in diagnostics)


# ---------------------------------------------------------------- 排序单元测试


def test_rank_respects_strong_direction():
    nodes = (_node("module:a", 0, "甲"), _node("module:b", 1, "乙"))
    ranked = topological_rank(nodes, (_edge("module:a", "module:b"),))
    ranks = {node.module_key: node.topological_rank for node in ranked}
    assert ranks["module:a"] == 0
    assert ranks["module:b"] == 1
    assert ranks["module:b"] > ranks["module:a"]
    assert _ranked(nodes, (_edge("module:a", "module:b"),)) == ("module:a", "module:b")
    # 反向输入 → 方向随边反转。
    ranked_reverse = topological_rank(nodes, (_edge("module:b", "module:a"),))
    ranks_reverse = {node.module_key: node.topological_rank for node in ranked_reverse}
    assert ranks_reverse["module:b"] < ranks_reverse["module:a"]


def test_diamond_layers_and_wave_order():
    nodes = (
        _node("d", 3),
        _node("b", 1),
        _node("c", 2),
        _node("a", 0),
    )
    edges = (_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d"))
    ranked = topological_rank(nodes, edges)
    ranks = {node.module_key: node.topological_rank for node in ranked}
    assert ranks == {"a": 0, "b": 1, "c": 1, "d": 2}
    assert _ranked(nodes, edges) == ("a", "b", "c", "d")


def test_tie_break_module_order_then_nfkc_name_then_key():
    nodes = (
        _node("k:c", 0, "ｚ"),  # 全角ｚ，NFKC 后为 z
        _node("k:a", 0, "z"),
        _node("k:b", 0, "y"),
    )
    ranked = topological_rank(nodes, ())
    assert _ranked(nodes, ()) == ("k:b", "k:a", "k:c")

    # module_order 优先于名称。
    nodes = (_node("k:a", 2, "A"), _node("k:b", 1, "B"))
    assert _ranked(nodes, ()) == ("k:b", "k:a")

    # 名称 NFKC 相同 → 按 module_key。
    nodes = (_node("k:z", 0, "ｚ"), _node("k:a", 0, "z"))
    assert _ranked(nodes, ()) == ("k:a", "k:z")


def test_longest_path_ranking_beats_single_wave():
    nodes = (_node("a", 0), _node("b", 1), _node("c", 2), _node("r", 3))
    edges = (_edge("a", "b"), _edge("b", "c"), _edge("r", "c"))
    ranked = topological_rank(nodes, edges)
    ranks = {node.module_key: node.topological_rank for node in ranked}
    assert ranks["c"] == 2  # 1 + max(rank(b)=1, rank(r)=0)
    assert ranks["b"] == 1


def test_isolated_nodes_after_all_waves_stable_by_module_order():
    nodes = (_node("iso_late", 5), _node("b", 1), _node("a", 0), _node("iso_early", 3))
    edges = (_edge("a", "b"),)
    ranked = topological_rank(nodes, edges)
    ranks = {node.module_key: node.topological_rank for node in ranked}
    assert (ranks["a"], ranks["b"]) == (0, 1)
    assert ranks["iso_early"] == ranks["iso_late"] == 2
    assert _ranked(nodes, edges) == ("a", "b", "iso_early", "iso_late")


def test_repeated_calls_are_identical():
    nodes = (
        _node("m3", 2, "丙"),
        _node("m1", 0, "甲"),
        _node("m2", 1, "乙"),
        _node("iso", 9, "孤"),
    )
    edges = (_edge("m1", "m2"), _edge("m2", "m3"))
    first = topological_rank(nodes, edges)
    for _ in range(10):
        assert topological_rank(nodes, edges) == first


def test_topological_rank_rejects_cycles_with_full_path():
    nodes = (_node("A", 0), _node("B", 1))
    with pytest.raises(ValueError, match="循环路径: A → B → A"):
        topological_rank(nodes, (_edge("A", "B"), _edge("B", "A")))


def test_topological_rank_rejects_duplicates_and_self_loops():
    with pytest.raises(ValueError, match="重复 module_key"):
        topological_rank((_node("a", 0), _node("a", 1)), ())
    with pytest.raises(ValueError, match="自环"):
        topological_rank((_node("a", 0),), (_edge("a", "a"),))


# ------------------------------------------------------ 投影集成（合成数据库）


def _three_module_db(tmp_path):
    db_path = _new_db(tmp_path)
    _install_f2_tables(db_path)
    repo = SubjectCatalogRepository(db_path, uuid_factory=_uuid_factory(17))
    subject = repo.create_subject(SUBJECT_NAME)
    modules = tuple(
        repo.create_module(subject.subject_key, name) for name in ("命题逻辑", "集合", "关系")
    )
    keys_by_module = _register_legacy_topics(
        db_path,
        SUBJECT_NAME,
        {"命题逻辑": ("命题",), "集合": ("集合运算",), "关系": ("二元关系",)},
    )
    _adopt_structure(
        db_path,
        subject,
        {
            modules[0].module_key: keys_by_module["命题逻辑"],
            modules[1].module_key: keys_by_module["集合"],
            modules[2].module_key: keys_by_module["关系"],
        },
    )
    topic_keys = {
        "命题": keys_by_module["命题逻辑"][0],
        "集合运算": keys_by_module["集合"][0],
        "二元关系": keys_by_module["关系"][0],
    }
    return db_path, subject, modules, topic_keys


_ADOPT_UUIDS = iter(range(900, 90000))


def _adopt_structure(db_path: Path, subject, module_topic_keys: dict) -> str:
    return adopt_structure(
        db_path,
        subject.subject_key,
        module_topic_keys,
        source_reference="test:chapter-graph-topo",
        uuid_factory=lambda: uuid.UUID(
            f"00000000-0000-0000-0000-{next(_ADOPT_UUIDS):012x}"
        ),
    )


def test_strong_edge_direction_ranks_and_order_conflict(tmp_path):
    db_path, subject, modules, keys = _three_module_db(tmp_path)
    # 关系(模块3, order 2) → 命题逻辑(模块1, order 0)：拓扑优先于课程顺序。
    _add_prerequisite(db_path, keys["命题"], keys["二元关系"])
    # 集合(模块2, order 1) → 命题逻辑(模块1, order 0)：与课程顺序一致。
    _add_prerequisite(db_path, keys["集合运算"], keys["命题"])
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)

    by_module = {node.module_key: node for node in snapshot.nodes}
    assert len(snapshot.edges) == 2
    assert {edge.from_module_key for edge in snapshot.edges} == {
        modules[2].module_key,
        modules[0].module_key,
    }
    edge_to_logic = next(
        edge for edge in snapshot.edges if edge.to_module_key == modules[0].module_key
    )
    assert edge_to_logic.from_module_key == modules[2].module_key
    assert edge_to_logic.relation_type == "strong_prerequisite"
    assert edge_to_logic.source == "user_confirmed"
    assert edge_to_logic.evidence_refs == (f"{keys['二元关系']}->{keys['命题']}",)
    assert snapshot.relations_present is True

    assert by_module[modules[2].module_key].topological_rank == 0
    assert by_module[modules[0].module_key].topological_rank == 1
    assert by_module[modules[1].module_key].topological_rank == 2

    # order_conflict：2>0 冲突波及两端；1→0 的一致边不标记…（命题逻辑 order0 在后）。
    assert "order_conflict" in by_module[modules[2].module_key].diagnostics
    assert "order_conflict" in by_module[modules[0].module_key].diagnostics
    assert "order_conflict" not in by_module[modules[1].module_key].diagnostics

    # 快照节点保持正式课程顺序（module_order 升序）。
    assert [node.module_order for node in snapshot.nodes] == [0, 1, 2]


def test_unmapped_and_self_loop_prerequisites_dropped_with_diagnostics(tmp_path):
    db_path, subject, modules, keys = _three_module_db(tmp_path)
    # 注册一个未绑定到当前结构的知识点 → 端点无法映射。
    with sqlite3.connect(db_path) as connection:
        extra_topic_id = connection.execute(
            "INSERT INTO topics(module_id, name, status, mastery, importance, difficulty, source_json)"
            " VALUES ((SELECT id FROM modules WHERE name='命题逻辑'), '未绑定主题', 'learning', 0, 0, 0, '{}')"
        ).lastrowid
        extra_key = topic_key_for_id(extra_topic_id)
        connection.execute(
            "INSERT INTO knowledge_topic_registry(topic_key, topic_id, identity_version)"
            " VALUES (?, ?, 'topic-identity-v1')",
            (extra_key, extra_topic_id),
        )
    _add_prerequisite(db_path, keys["命题"], extra_key)  # 前置端点未绑定 → 丢弃
    _add_prerequisite(db_path, keys["集合运算"], keys["命题"])
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert "prerequisite_endpoint_unmapped" in snapshot.diagnostics
    assert len(snapshot.edges) == 1
    assert snapshot.edges[0].from_module_key == modules[0].module_key
    assert snapshot.edges[0].to_module_key == modules[1].module_key


def test_module_level_self_loop_dropped(tmp_path):
    db_path = _new_db(tmp_path)
    _install_f2_tables(db_path)
    repo = SubjectCatalogRepository(db_path, uuid_factory=_uuid_factory(17))
    subject = repo.create_subject(SUBJECT_NAME)
    module = repo.create_module(subject.subject_key, "单章")
    keys_by_module = _register_legacy_topics(db_path, SUBJECT_NAME, {"单章": ("甲", "乙")})
    _adopt_structure(db_path, subject, {module.module_key: keys_by_module["单章"]})
    # 同一模块内的两个知识点互为先修 → 模块自环，投影丢弃并诊断。
    _add_prerequisite(db_path, keys_by_module["单章"][0], keys_by_module["单章"][1])
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert snapshot.edges == ()
    assert "prerequisite_self_loop_dropped" in snapshot.diagnostics
    assert "relations_missing" in snapshot.diagnostics
    assert len(snapshot.nodes) == 1


def test_historical_cycle_falls_back_to_course_order_with_path_diagnostic(tmp_path):
    db_path, subject, modules, keys = _three_module_db(tmp_path)
    _add_prerequisite(db_path, keys["命题"], keys["集合运算"])  # 集合 → 命题逻辑
    _add_prerequisite(db_path, keys["集合运算"], keys["命题"])  # 命题逻辑 → 集合（成环）
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert len(snapshot.edges) == 2  # 历史边保留为只读数据
    cycle_diags = [
        item
        for item in snapshot.diagnostics
        if item.startswith("relations_cycle:循环路径: ")
    ]
    assert cycle_diags, snapshot.diagnostics
    message = cycle_diags[0]
    assert message.count("→") >= 2
    assert modules[0].module_key in message and modules[1].module_key in message
    # 回退：不伪造拓扑，按正式课程顺序编号。
    for index, node in enumerate(snapshot.nodes):
        assert node.topological_rank == index
    assert [node.module_order for node in snapshot.nodes] == [0, 1, 2]


def test_no_strong_edges_reports_relations_missing(tmp_path):
    db_path, subject, _modules, _keys = _three_module_db(tmp_path)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert snapshot.edges == ()
    assert snapshot.relations_present is False
    assert "relations_missing" in snapshot.diagnostics
    assert all(node.topological_rank == 0 for node in snapshot.nodes)
