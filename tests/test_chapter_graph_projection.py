"""章节图投影 schema 与 16 章节投影测试（chapter-graph-v1 合同 §6/§17.1/§17.2）。

夹具全部为临时目录中的合成数据库（沿用 tests/test_f5_i02_catalog_projection.py
的插入风格）；绝不触碰 app_data/，零生产写入。
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from study_app.core.chapter_graph_projection import (
    GRAPH_SCHEMA_VERSION,
    ChapterEdge,
    ChapterGraphSnapshot,
    ChapterNode,
    build_chapter_graph_snapshot,
    list_graph_subjects,
)
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


def _uuid_factory(start: int):
    counter = iter(range(start, start + 500))

    def factory() -> uuid.UUID:
        return uuid.UUID(f"00000000-0000-0000-0000-{next(counter):012x}")

    return factory


def _new_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "chapter_graph.sqlite"
    initialize_database(db_path)
    install_subject_lifecycle_schema(db_path)
    return db_path


def _create_subject(db_path: Path, name: str, module_names: tuple[str, ...], *, start: int = 17):
    repo = SubjectCatalogRepository(db_path, uuid_factory=_uuid_factory(start))
    subject = repo.create_subject(name)
    modules = tuple(
        repo.create_module(subject.subject_key, module_name) for module_name in module_names
    )
    return repo, subject, modules


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
    """插入旧模型 subjects/modules/topics 行并登记 F2 身份；返回每个模块的 topic_key。"""
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


_ADOPT_UUIDS = iter(range(900, 90000))


def _adopt_structure(db_path: Path, subject, module_topic_keys: dict) -> str:
    return adopt_structure(
        db_path,
        subject.subject_key,
        module_topic_keys,
        source_reference="test:chapter-graph",
        uuid_factory=lambda: uuid.UUID(
            f"00000000-0000-0000-0000-{next(_ADOPT_UUIDS):012x}"
        ),
    )


@pytest.fixture
def sixteen_module_db(tmp_path):
    """16 章节合成学科：现行结构存在但知识点未绑定（生产现状）。"""
    db_path = _new_db(tmp_path)
    module_names = tuple(f"第{i:02d}章" for i in range(1, 17))
    _repo, subject, modules = _create_subject(db_path, "离散数学", module_names)
    _adopt_structure(
        db_path,
        subject,
        {module.module_key: () for module in modules},
    )
    return db_path, subject, modules


def test_graph_schema_version_constant():
    assert GRAPH_SCHEMA_VERSION == "chapter-graph-v1"


def test_sixteen_chapters_get_exactly_one_node_each(sixteen_module_db):
    db_path, subject, modules = sixteen_module_db
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert len(snapshot.nodes) == 16
    module_keys = [node.module_key for node in snapshot.nodes]
    assert module_keys == [module.module_key for module in modules]
    assert len(set(module_keys)) == 16  # 每个正式章节恰好一个节点，无丢失
    assert [node.module_order for node in snapshot.nodes] == list(range(16))
    assert snapshot.subject_key == subject.subject_key
    assert snapshot.structure_version.startswith("structure:v1:")
    assert isinstance(snapshot.catalog_revision, int)
    assert snapshot.as_of_date == AS_OF


def test_unbound_sixteen_module_nodes_are_unlearned_and_ranked(sixteen_module_db):
    db_path, subject, _modules = sixteen_module_db
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    for node in snapshot.nodes:
        assert node.learning_state == "unlearned"
        assert node.topic_count == 0
        assert node.coverage_count == 0
        assert node.mastery_point is None  # 未知掌握度绝不写成 0
        assert node.mastery_interval is None
        assert node.evidence_confidence == 0.0
        assert "structure_topics_unbound" in node.diagnostics
    assert snapshot.relations_present is False
    assert "relations_missing" in snapshot.diagnostics
    assert snapshot.edges == ()
    # 无强边时全部孤立：同处第 0 层，按 module_order 稳定排列。
    assert all(node.topological_rank == 0 for node in snapshot.nodes)


def test_snapshot_to_dict_has_exact_contract_keys(sixteen_module_db):
    db_path, subject, _modules = sixteen_module_db
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    payload = snapshot.to_dict()
    assert set(payload) == {
        "schema_version",
        "subject_key",
        "structure_version",
        "catalog_revision",
        "as_of_date",
        "nodes",
        "edges",
        "diagnostics",
    }
    assert payload["nodes"], "合成夹具必须有节点"
    assert set(payload["nodes"][0]) == {
        "module_key",
        "display_name",
        "module_order",
        "topological_rank",
        "learning_state",
        "mastery_point",
        "mastery_interval",
        "coverage_count",
        "topic_count",
        "evidence_confidence",
        "diagnostics",
    }
    # JSON 可序列化（UI 诊断面板直接消费）。
    encoded = json.dumps(payload, ensure_ascii=False)
    assert isinstance(encoded, str)


def test_from_dict_round_trip(sixteen_module_db):
    db_path, subject, _modules = sixteen_module_db
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    restored = ChapterGraphSnapshot.from_dict(snapshot.to_dict())
    assert restored == snapshot
    assert restored.nodes == snapshot.nodes
    assert restored.relations_present == snapshot.relations_present


def _valid_snapshot_dict(sixteen_module_db) -> dict:
    db_path, subject, _modules = sixteen_module_db
    return build_chapter_graph_snapshot(
        subject.subject_key, db_path=db_path, as_of_date=AS_OF
    ).to_dict()


def _mutated(payload: dict, **changes) -> dict:
    cloned = json.loads(json.dumps(payload, ensure_ascii=False))
    for key, value in changes.items():
        if key.startswith("node."):
            cloned["nodes"][0][key[len("node."):]] = value
        elif key.startswith("edge."):
            cloned["edges"].append(
                {
                    "from_module_key": cloned["nodes"][0]["module_key"],
                    "to_module_key": cloned["nodes"][1]["module_key"],
                    "relation_type": "strong_prerequisite",
                    "source": "user_confirmed",
                    "evidence_refs": [],
                    "diagnostics": [],
                }
            )
            cloned["edges"][-1][key[len("edge."):]] = value
        else:
            cloned[key] = value
    return cloned


def test_from_dict_rejects_missing_extra_version_and_invalid_values(sixteen_module_db):
    payload = _valid_snapshot_dict(sixteen_module_db)

    missing = {k: v for k, v in payload.items() if k != "edges"}
    with pytest.raises(ValueError, match="缺少字段"):
        ChapterGraphSnapshot.from_dict(missing)

    extra = dict(payload)
    extra["unexpected"] = 1
    with pytest.raises(ValueError, match="未知字段"):
        ChapterGraphSnapshot.from_dict(extra)

    wrong_version = dict(payload)
    wrong_version["schema_version"] = "chapter-graph-v2"
    with pytest.raises(ValueError, match="版本不兼容"):
        ChapterGraphSnapshot.from_dict(wrong_version)

    with pytest.raises(ValueError, match="非法学习状态"):
        ChapterGraphSnapshot.from_dict(_mutated(payload, **{"node.learning_state": "learned"}))

    with pytest.raises(ValueError, match="未知字段"):
        ChapterGraphSnapshot.from_dict(_mutated(payload, **{"node.extra": 1}))

    with pytest.raises(ValueError, match="掌握点|mastery_point"):
        ChapterGraphSnapshot.from_dict(_mutated(payload, **{"node.mastery_point": float("nan")}))

    with pytest.raises(ValueError, match="下界"):
        ChapterGraphSnapshot.from_dict(
            _mutated(payload, **{"node.mastery_interval": [0.8, 0.2]})
        )

    with pytest.raises(ValueError, match="真实日历日期|格式"):
        ChapterGraphSnapshot.from_dict(_mutated(payload, as_of_date="2026-9-1"))

    with pytest.raises(ValueError, match="非负整数"):
        ChapterGraphSnapshot.from_dict(_mutated(payload, catalog_revision=True))

    with pytest.raises(ValueError, match="JSON 对象"):
        ChapterGraphSnapshot.from_dict(["not", "a", "dict"])

    with pytest.raises(ValueError, match="非法关系类型"):
        ChapterGraphSnapshot.from_dict(
            _mutated(payload, **{"edge.relation_type": "hinted_similarity"})
        )


def test_snapshot_and_nodes_are_frozen(sixteen_module_db):
    db_path, subject, _modules = sixteen_module_db
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    node = snapshot.nodes[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.mastery_point = 0.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.diagnostics = ()  # type: ignore[misc]


def test_relations_present_flag_reflects_strong_edges():
    node_a = ChapterNode(
        module_key="module:a",
        display_name="A",
        module_order=0,
        topological_rank=0,
        learning_state="unlearned",
        mastery_point=None,
        mastery_interval=None,
        coverage_count=0,
        topic_count=0,
        evidence_confidence=0.0,
    )
    node_b = dataclasses.replace(node_a, module_key="module:b", module_order=1)
    empty = ChapterGraphSnapshot(
        schema_version=GRAPH_SCHEMA_VERSION,
        subject_key="subject:v1:" + "0" * 32,
        structure_version=None,
        catalog_revision=None,
        as_of_date=AS_OF,
        nodes=(node_a, node_b),
        edges=(),
    )
    assert empty.relations_present is False
    supporting_only = ChapterGraphSnapshot(
        schema_version=GRAPH_SCHEMA_VERSION,
        subject_key=empty.subject_key,
        structure_version=None,
        catalog_revision=None,
        as_of_date=AS_OF,
        nodes=(node_a, node_b),
        edges=(
            ChapterEdge(
                from_module_key="module:a",
                to_module_key="module:b",
                relation_type="supporting_relation",
                source="derived_topic_evidence",
            ),
        ),
    )
    assert supporting_only.relations_present is False
    strong = ChapterGraphSnapshot(
        schema_version=GRAPH_SCHEMA_VERSION,
        subject_key=empty.subject_key,
        structure_version=None,
        catalog_revision=None,
        as_of_date=AS_OF,
        nodes=(node_a, node_b),
        edges=(
            ChapterEdge(
                from_module_key="module:a",
                to_module_key="module:b",
                relation_type="strong_prerequisite",
                source="user_confirmed",
            ),
        ),
    )
    assert strong.relations_present is True


def test_list_graph_subjects_reports_structure_presence(tmp_path):
    db_path = _new_db(tmp_path)
    _repo, structured, _modules = _create_subject(db_path, "代数", ("第一章",), start=17)
    _adopt_structure(db_path, structured, {})
    _repo2, bare, _m2 = _create_subject(db_path, "物理", (), start=200)
    rows = list_graph_subjects(db_path)
    assert [row["subject_key"] for row in rows] == [structured.subject_key, bare.subject_key]
    assert rows[0]["has_current_structure"] is False  # 结构存在但 0 模块 → False
    assert rows[1]["has_current_structure"] is False
    by_key = {row["subject_key"]: row for row in rows}
    assert by_key[structured.subject_key]["lifecycle_status"] == "active"
    assert by_key[structured.subject_key]["display_name"] == "代数"
