"""章节图缓存键、降级诊断与零写入测试（chapter-graph-v1 合同 §13/§17.13/15/18）。

全部夹具位于临时目录；对合成数据库构建快照前后文件字节必须完全一致
（零业务写入）；绝不触碰 app_data/。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from study_app.core.chapter_graph_projection import (
    ChapterGraphSnapshot,
    build_chapter_graph_snapshot,
    list_graph_subjects,
    snapshot_cache_key,
    ChapterGraphCache,
)
from study_app.core.topic_identity import topic_key_for_id
from study_app.data.database import KNOWLEDGE_PREREQUISITES_TABLE_SQL
from data_test_support import file_sha256, initialize_legacy_base_database
from study_app.data.subject_repository import (
    SubjectCatalogRepository,
    install_subject_lifecycle_schema,
)
from study_app.core.subject_catalog import adopt_structure


AS_OF = "2026-09-20"
SUBJECT_NAME = "离散数学"


def _uuid_factory(start: int):
    counter = iter(range(start, start + 500))
    return lambda: uuid.UUID(f"00000000-0000-0000-0000-{next(counter):012x}")


def _new_db(tmp_path: Path, name: str = "chapter_graph_cache.sqlite") -> Path:
    db_path = tmp_path / name
    initialize_legacy_base_database(db_path)
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


def _create_subject(db_path: Path, name: str, module_names: tuple[str, ...], *, start: int = 17):
    repo = SubjectCatalogRepository(db_path, uuid_factory=_uuid_factory(start))
    subject = repo.create_subject(name)
    modules = tuple(
        repo.create_module(subject.subject_key, module_name) for module_name in module_names
    )
    return repo, subject, modules




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


_ADOPT_UUIDS = iter(range(900, 90000))


def _adopt_structure(db_path: Path, subject, module_topic_keys: dict) -> str:
    return adopt_structure(
        db_path,
        subject.subject_key,
        module_topic_keys,
        source_reference="test:chapter-graph-cache",
        uuid_factory=lambda: uuid.UUID(
            f"00000000-0000-0000-0000-{next(_ADOPT_UUIDS):012x}"
        ),
    )


def _add_record(db_path: Path, *, date: str, topic: str, score: float | None = None) -> None:
    payload = {
        "date": date,
        "subject": SUBJECT_NAME,
        "module": None,
        "topic": topic,
        "activity": "review",
        "source": "outside_class",
        "score": score,
        "note": "",
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO learning_records(
                record_date, subject_name, module_name, topic_name, activity,
                source, score, duration_minutes, note, raw_json
            ) VALUES (?, ?, NULL, ?, 'review', 'outside_class', ?, NULL, '', ?)
            """,
            (date, SUBJECT_NAME, topic, score, json.dumps(payload, ensure_ascii=False)),
        )


@pytest.fixture
def graph_db(tmp_path):
    """带结构（未绑定知识点）的活动学科 + 无结构学科。"""
    db_path = _new_db(tmp_path)
    _install_f2_tables(db_path)
    _repo, subject, modules = _create_subject(
        db_path, SUBJECT_NAME, ("命题逻辑", "集合与关系"), start=17
    )
    _adopt_structure(db_path, subject, {module.module_key: () for module in modules})
    _repo2, bare, _m2 = _create_subject(db_path, "物理学", (), start=300)
    return db_path, subject, bare


# ---------------------------------------------------------------- 缓存键


def test_snapshot_cache_key_shape(graph_db):
    db_path, subject, _bare = graph_db
    key = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert isinstance(key, tuple)
    assert len(key) == 6
    assert key[0] == subject.subject_key
    assert isinstance(key[1], str) and key[1].startswith("structure:v1:")
    assert isinstance(key[2], int)
    assert key[3] == AS_OF
    assert isinstance(key[4], str) and len(key[4]) == 64  # sha256 记录摘要
    assert key[5] == (0, 0)  # 先修关系版本 = (count, max_rowid)


def test_snapshot_cache_key_is_deterministic(graph_db):
    db_path, subject, _bare = graph_db
    first = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    for _ in range(3):
        assert snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF) == first


def test_cache_key_changes_when_catalog_revision_changes(graph_db):
    db_path, subject, _bare = graph_db
    before = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 2"
        )
    after = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert after != before
    assert after[2] == before[2] + 2


def test_cache_key_changes_when_structure_version_changes(tmp_path):
    db_path = _new_db(tmp_path)
    _repo, subject, _modules = _create_subject(db_path, SUBJECT_NAME, (), start=17)
    _adopt_structure(db_path, subject, {})  # 空结构：可直接改版本字符串
    before = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE subject_structure_versions SET structure_version = ? WHERE subject_key = ?",
            ("structure:v1:" + uuid.UUID("12345678123456781234567812345678").hex, subject.subject_key),
        )
    after = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert after != before
    assert after[1] != before[1]


def test_cache_key_changes_when_as_of_changes(graph_db):
    db_path, subject, _bare = graph_db
    before = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    after = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date="2026-09-21")
    assert after != before
    assert after[3] == "2026-09-21"


def test_cache_key_changes_when_records_change(graph_db):
    db_path, subject, _bare = graph_db
    before = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    _add_record(db_path, date="2026-09-01", topic="命题", score=88)
    after = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert after != before
    assert after[4] != before[4]


def test_cache_key_changes_when_prerequisites_change(tmp_path):
    db_path = _new_db(tmp_path)
    _install_f2_tables(db_path)
    _repo, subject, modules = _create_subject(
        db_path, SUBJECT_NAME, ("命题逻辑", "集合"), start=17
    )
    keys = _register_legacy_topics(
        db_path, SUBJECT_NAME, {"命题逻辑": ("命题",), "集合": ("集合运算",)}
    )
    _adopt_structure(
        db_path,
        subject,
        {
            modules[0].module_key: keys["命题逻辑"],
            modules[1].module_key: keys["集合"],
        },
    )
    before = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO knowledge_prerequisites(
                topic_key, prerequisite_topic_key, source, source_json
            ) VALUES (?, ?, 'user_confirmed', '{}')
            """,
            (keys["命题逻辑"][0], keys["集合"][0]),
        )
    after = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert after != before
    assert after[5] == (1, 1)


def test_cache_key_structure_missing_variant_for_incomplete_databases(tmp_path, graph_db):
    db_path, subject, _bare = graph_db
    # 数据库文件不存在。
    missing_path = tmp_path / "not_initialized.sqlite"
    key = snapshot_cache_key(subject.subject_key, db_path=missing_path, as_of_date=AS_OF)
    assert "structure_missing" in key
    # 只有 F1 基础表、没有 F5 目录表。
    f1_only = tmp_path / "f1_only.sqlite"
    initialize_legacy_base_database(f1_only)
    key = snapshot_cache_key(subject.subject_key, db_path=f1_only, as_of_date=AS_OF)
    assert "structure_missing" in key


def test_snapshot_cache_key_rejects_invalid_as_of(graph_db):
    db_path, subject, _bare = graph_db
    with pytest.raises(ValueError):
        snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date="2026/09/20")


# ---------------------------------------------------------------- 构建诊断


def test_build_reports_structure_missing_for_missing_db(tmp_path):
    snapshot = build_chapter_graph_snapshot(
        "subject:v1:" + "0" * 32,
        db_path=tmp_path / "absent.sqlite",
        as_of_date=AS_OF,
    )
    assert snapshot.diagnostics == ("structure_missing",)
    assert snapshot.nodes == ()
    assert snapshot.edges == ()


def test_build_reports_structure_missing_without_f5(tmp_path):
    f1_only = tmp_path / "f1_only.sqlite"
    initialize_legacy_base_database(f1_only)
    snapshot = build_chapter_graph_snapshot(
        "subject:v1:" + "0" * 32, db_path=f1_only, as_of_date=AS_OF
    )
    assert snapshot.diagnostics == ("structure_missing",)


def test_build_reports_structure_version_missing(graph_db):
    db_path, _subject, bare = graph_db
    snapshot = build_chapter_graph_snapshot(bare.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert snapshot.diagnostics == ("structure_version_missing",)
    assert snapshot.nodes == ()
    assert snapshot.structure_version is None


def test_build_reports_structure_empty(tmp_path):
    db_path = _new_db(tmp_path)
    _repo, subject, _modules = _create_subject(db_path, SUBJECT_NAME, (), start=17)
    _adopt_structure(db_path, subject, {})
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert snapshot.diagnostics == ("structure_empty",)
    assert snapshot.nodes == ()
    assert snapshot.structure_version is not None


def test_build_reports_registry_missing(tmp_path):
    db_path = _new_db(tmp_path)  # F5 已装，但 F2 缺表
    _repo, subject, modules = _create_subject(
        db_path, SUBJECT_NAME, ("命题逻辑",), start=17
    )
    _adopt_structure(db_path, subject, {modules[0].module_key: ()})
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert "registry_missing" in snapshot.diagnostics
    assert len(snapshot.nodes) == 1
    assert "structure_topics_unbound" in snapshot.nodes[0].diagnostics


def test_build_reports_unknown_subject(graph_db):
    db_path, _subject, _bare = graph_db
    snapshot = build_chapter_graph_snapshot(
        "subject:v1:" + "f" * 32, db_path=db_path, as_of_date=AS_OF
    )
    assert snapshot.diagnostics == ("subject_unknown",)


def test_build_snapshot_matches_cache_key_components(graph_db):
    db_path, subject, _bare = graph_db
    key = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert snapshot.structure_version == key[1]
    assert snapshot.catalog_revision == key[2]
    assert snapshot.as_of_date == key[3]


# ---------------------------------------------------------------- 缓存对象


def test_chapter_graph_cache_round_trip_and_miss(graph_db):
    db_path, subject, _bare = graph_db
    key = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    snapshot = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    cache = ChapterGraphCache()
    assert cache.get(key) is None  # 未命中
    cache.put(key, snapshot)
    assert cache.get(key) is snapshot
    other_key = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date="2026-09-25")
    assert cache.get(other_key) is None
    with pytest.raises(TypeError):
        cache.put(key, {"not": "a snapshot"})
    assert ChapterGraphCache().get(key) is None  # 进程实例之间互不共享


def test_cache_never_returns_stale_snapshot_after_input_change(graph_db):
    db_path, subject, _bare = graph_db
    cache = ChapterGraphCache()
    old_key = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    old_snapshot = build_chapter_graph_snapshot(
        subject.subject_key, db_path=db_path, as_of_date=AS_OF
    )
    cache.put(old_key, old_snapshot)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1"
        )
    new_key = snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    assert new_key != old_key
    assert cache.get(new_key) is None  # 新键必然未命中 → UI 必须重建
    fresh = build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    cache.put(new_key, fresh)
    assert cache.get(new_key) is fresh


# ---------------------------------------------------------------- 学科清单


def test_list_graph_subjects_orders_active_first_then_archived(tmp_path):
    db_path = _new_db(tmp_path)
    _install_f2_tables(db_path)
    _repo, algebra, algebra_modules = _create_subject(db_path, "代数", ("第一章",), start=17)
    _adopt_structure(db_path, algebra, {algebra_modules[0].module_key: ()})
    _repo2, physics, _m2 = _create_subject(db_path, "物理学", (), start=100)
    _repo3, chemistry, _m3 = _create_subject(db_path, "化学", ("有机",), start=200)
    _adopt_structure(db_path, chemistry, {})
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE subject_catalog SET lifecycle_status = 'archived' WHERE subject_key = ?",
            (chemistry.subject_key,),
        )

    rows = list_graph_subjects(db_path)
    assert [row["subject_key"] for row in rows] == [algebra.subject_key, physics.subject_key]
    assert all(row["lifecycle_status"] == "active" for row in rows)
    assert rows[0]["has_current_structure"] is True  # 有结构版本且 ≥1 模块
    assert rows[1]["has_current_structure"] is False

    with_history = list_graph_subjects(db_path, include_archived=True)
    assert [row["subject_key"] for row in with_history] == [
        algebra.subject_key,
        physics.subject_key,
        chemistry.subject_key,
    ]
    assert with_history[2]["lifecycle_status"] == "archived"
    assert with_history[2]["display_name"] == "化学"


# ---------------------------------------------------------------- 零写入


def test_building_snapshots_never_modifies_database_bytes(tmp_path, graph_db):
    db_path, subject, bare = graph_db
    _add_record(db_path, date="2026-09-01", topic="命题", score=75)
    before = file_sha256(db_path)
    assert before is not None

    for _ in range(2):
        build_chapter_graph_snapshot(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    list_graph_subjects(db_path)
    list_graph_subjects(db_path, include_archived=True)
    snapshot_cache_key(subject.subject_key, db_path=db_path, as_of_date=AS_OF)
    build_chapter_graph_snapshot(
        subject.subject_key, db_path=db_path, as_of_date=AS_OF, include_archived=True
    )
    build_chapter_graph_snapshot(bare.subject_key, db_path=db_path, as_of_date=AS_OF)

    assert file_sha256(db_path) == before
