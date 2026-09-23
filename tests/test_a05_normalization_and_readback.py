# -*- coding: utf-8 -*-
"""A-05 定向测试：规范化写入与权威回读（BUG-02；data_contract_v1 §4.4）。

覆盖分支：
- 仅 answer_result="全对" 时，推断正确度在关系表/回读/模型消费三处一致；
- 逐字段比较（title/status/correctness/difficulty/error_cause）；
- 规范化失败零残留；
- 损坏 raw_json 走可诊断兼容路径；
- 旧版 raw_json（缺推断字段）原样兼容加载。

隔离：临时 SQLite + learning_model_v1.json 副本；真实数据零写入。
"""
import json
import sqlite3
from pathlib import Path

import pytest

from learning_bkt import iter_topic_observations
from study_app.data.database import (
    add_learning_record,
    ensure_seeded_database,
    initialize_database,
    add_learning_record,
    initialize_database,
    load_raw_records,
)

ROOT = Path(__file__).resolve().parents[1]

BKT_POLICY = {
    "bkt_model": {
        "enabled": True,
        "contextual_estimation": {"enabled": False},
        "time_effect": {"enabled": False},
        "personal_evidence_confidence": {"enabled": False},
    }
}


def _model_copy(tmp_path: Path) -> Path:
    copy_path = tmp_path / "model_copy.json"
    copy_path.write_bytes((ROOT / "tests" / "fixtures" / "minimal_model.json").read_bytes())
    return copy_path


def _add_full_answer_result(db: Path, model_copy: Path) -> int:
    ensure_seeded_database(db, model_path=model_copy)
    return add_learning_record(
        {
            "date": "2026-09-01",
            "subject": "测试学科",
            "module": "测试模块",
            "topic": "测试知识点",
            "activity": "exercise",
            "source": "outside_class",
            "score": 100,
            "problems": [{"title": "题目A", "answer_result": "全对"}],
        },
        db_path=db,
        model_path=model_copy,
    )


def _counts(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("learning_records", "problem_attempts", "record_attachments")
        }
    finally:
        conn.close()


def test_inferred_correctness_consistent_everywhere(tmp_path):
    db = tmp_path / "a05.sqlite"
    rid = _add_full_answer_result(db, _model_copy(tmp_path))

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    attempt = conn.execute(
        "SELECT * FROM problem_attempts WHERE record_id = ?", (rid,)
    ).fetchone()
    conn.close()
    assert attempt["correctness"] == 1.0

    loaded = load_raw_records(db)
    problem = [
        item
        for item in loaded
        if item.get("subject") == "测试学科" and item.get("id") == rid
    ][0]["problems"][0]
    assert problem.get("partial_credit") == 1.0
    assert problem.get("status") == "correct"

    topic = {"name": "题目A", "mastery": 0.2, "difficulty": 0.5, "importance": 0.7}
    obs = iter_topic_observations("测试学科", "题目A", topic, loaded, BKT_POLICY)
    assert len(obs) == 1
    assert obs[0]["correctness"] == 1.0


def test_field_by_field_attempt_matches_raw(tmp_path):
    db = tmp_path / "a05b.sqlite"
    rid = _add_full_answer_result(db, _model_copy(tmp_path))

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    attempt = conn.execute(
        "SELECT * FROM problem_attempts WHERE record_id = ?", (rid,)
    ).fetchone()
    conn.close()
    raw_problem = load_raw_records(db)[0]["problems"][0]

    assert attempt["title"] == raw_problem["title"]
    assert attempt["status"] == raw_problem["status"]
    assert attempt["correctness"] == raw_problem["partial_credit"]
    assert (attempt["difficulty_score"] or 0) == (raw_problem.get("difficulty_score") or 0)
    assert attempt["error_cause"] == raw_problem.get("error_cause")


def test_normalization_failure_leaves_zero_residue(tmp_path):
    db = tmp_path / "a05c.sqlite"
    initialize_database(db)
    with pytest.raises(ValueError):
        add_learning_record(
            {
                "date": "2026-09-01",
                "subject": "测试学科",
                "topic": "测试知识点",
                "activity": "exercise",
                "source": "outside_class",
                "score": 50,
                "problems": [{"title": "坏难度题", "difficulty_score": -3}],
            },
            db_path=db,
            model_path=_model_copy(tmp_path),
        )
    assert _counts(db) == {
        "learning_records": 0,
        "problem_attempts": 0,
        "record_attachments": 0,
    }
    conn = sqlite3.connect(db)
    try:
        sync_settings = conn.execute(
            "SELECT COUNT(*) FROM app_settings WHERE key = 'model_progress_sync_last_result'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert sync_settings == 0


def _seed_then_insert_raw(db: Path, record: dict, raw_json: str) -> None:
    assert db.is_file()  # C-03：夹具必须先显式初始化，读取不再触发种子导入
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT INTO learning_records(
                record_date, subject_name, module_name, topic_name, activity,
                source, score, duration_minutes, note, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("date"),
                record.get("subject"),
                record.get("module"),
                record.get("topic"),
                record.get("activity"),
                record.get("source"),
                record.get("score"),
                record.get("duration_minutes"),
                record.get("note"),
                raw_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_corrupt_raw_json_diagnosed_not_fatal(tmp_path):
    db = tmp_path / "a05d.sqlite"
    initialize_database(db)
    _seed_then_insert_raw(
        db,
        {"date": "2026-09-05", "subject": "隔离学科", "topic": "损坏记录"},
        "not-json{",
    )
    records = load_raw_records(db)
    target = [item for item in records if item.get("subject") == "隔离学科"]
    assert target and target[0].get("data_warning") == "invalid_raw_json"


def test_legacy_problem_without_inferred_fields_loads_unchanged(tmp_path):
    db = tmp_path / "a05e.sqlite"
    initialize_database(db)
    legacy = {
        "date": "2026-09-06",
        "subject": "隔离学科",
        "topic": "旧版题目",
        "activity": "exercise",
        "source": "outside_class",
        "score": 70,
        "problems": [{"title": "旧题", "answer_result": "全对"}],
    }
    _seed_then_insert_raw(db, legacy, json.dumps(legacy, ensure_ascii=False))
    records = load_raw_records(db)
    target = [item for item in records if item.get("subject") == "隔离学科"]
    assert target and target[0]["problems"][0]["title"] == "旧题"
    # 兼容路径不得伪造证据：原字段缺失仍缺失
    assert "correctness" not in target[0]["problems"][0]


def test_add_rejects_string_score(tmp_path):
    """阻断2：score="80" 等字符串数值必须拒绝，不得转成 80.0。"""
    db = tmp_path / "a05f.sqlite"
    initialize_database(db)
    with pytest.raises(ValueError):
        add_learning_record(
            {
                "date": "2026-09-01",
                "subject": "测试学科",
                "topic": "测试知识点",
                "activity": "exercise",
                "source": "outside_class",
                "score": "80",
            },
            db_path=db,
            model_path=_model_copy(tmp_path),
        )
    assert _counts(db)["learning_records"] == 0


def test_add_rejects_numeric_string_label(tmp_path):
    """阻断2：difficulty="1e2" 等字符串数值必须拒绝。"""
    db = tmp_path / "a05g.sqlite"
    initialize_database(db)
    with pytest.raises(ValueError):
        add_learning_record(
            {
                "date": "2026-09-01",
                "subject": "测试学科",
                "topic": "测试知识点",
                "activity": "exercise",
                "source": "outside_class",
                "problems": [{"title": "T", "difficulty": "1e2"}],
            },
            db_path=db,
            model_path=_model_copy(tmp_path),
        )
    assert _counts(db)["learning_records"] == 0


def test_import_preserves_existing_on_invalid_date(tmp_path):
    """阻断1：规范化（含日期校验）必须先于 DELETE——非法导入不得清掉既有记录。"""
    db = tmp_path / "a05h.sqlite"
    initialize_database(db)
    model_copy = _model_copy(tmp_path)
    add_learning_record(
        {
            "date": "2026-09-01",
            "subject": "隔离学科",
            "topic": "既有记录",
            "activity": "exercise",
            "source": "outside_class",
            "score": 80,
        },
        db_path=db,
        model_path=model_copy,
    )
    conn = sqlite3.connect(db)
    with pytest.raises(ValueError):
        import_records_json_records(conn, [
            {
                "date": "2026-02-30",
                "subject": "隔离学科",
                "topic": "非法导入",
                "activity": "exercise",
                "source": "outside_class",
            }
        ])
    conn.close()
    records = load_raw_records(db)
    kept = [item for item in records if item.get("subject") == "隔离学科"]
    assert len(kept) == 1 and kept[0].get("topic") == "既有记录"


def test_import_rejects_numeric_string_label(tmp_path):
    db = tmp_path / "a05i.sqlite"
    initialize_database(db)
    conn = sqlite3.connect(db)
    with pytest.raises(ValueError):
        import_records_json_records(conn, [
            {
                "date": "2026-09-01",
                "subject": "隔离学科",
                "topic": "测试知识点",
                "activity": "exercise",
                "source": "outside_class",
                "problems": [{"title": "T", "difficulty": "1e2"}],
            }
        ])
    conn.close()
    records = load_raw_records(db)
    assert [item for item in records if item.get("subject") == "隔离学科"] == []


def test_load_consistency_diag(tmp_path):
    """阻断3：raw 1.0 vs attempts 0.0 → 读取以 raw 为权威并产生一致性诊断。"""
    db = tmp_path / "a05j.sqlite"
    initialize_database(db)
    raw = {
        "date": "2026-09-07",
        "subject": "隔离学科",
        "topic": "一致性",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [
            {"title": "冲突题", "correctness": 100, "partial_credit": 1.0}
        ],
    }
    _seed_then_insert_raw(db, raw, json.dumps(raw, ensure_ascii=False))
    conn = sqlite3.connect(db)
    record_id = conn.execute(
        "SELECT id FROM learning_records WHERE subject_name = '隔离学科'"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO problem_attempts(
            record_id, title, correctness, difficulty_score, raw_json
        ) VALUES (?, '冲突题', 0.0, 55, '{}')
        """,
        (record_id,),
    )
    conn.execute(
        "UPDATE problem_attempts SET correctness = 0.0 WHERE title = '冲突题'"
    )
    conn.commit()
    conn.close()
    records = load_raw_records(db)
    target = [item for item in records if item.get("subject") == "隔离学科"]
    assert target and target[0]["problems"][0]["partial_credit"] == 1.0
    diag = target[0].get("consistency_diag")
    assert diag and diag["mismatch_count"] >= 1


def test_practice_candidates_not_doubled(tmp_path, monkeypatch):
    """阻断4：每个规范化题目只进入 practice_candidates 一次。"""
    import study_app.data.database as database_module

    captured = {}

    def fake_auto_import(record, problems, db_path=None):
        captured["titles"] = [item.get("title") for item in problems]

    monkeypatch.setattr(database_module, "_auto_import_practice_problems", fake_auto_import)
    db = tmp_path / "a05k.sqlite"
    _add_full_answer_result(db, _model_copy(tmp_path))
    assert captured["titles"] == ["题目A"]


def import_records_json_records(connection, records):
    """A-05 调用链包装：在调用方事务上下文中执行导入（异常向上传播）。"""
    from study_app.data.database import import_records_json

    import_records_json(connection, records)


class TestUIDisplaySemantics:
    """仅 status="wrong" 的题目：界面正确率/估分影响不得显示待判断。"""

    def _editor(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest.mock import Mock

        from PySide6.QtWidgets import QApplication
        from study_app.ui import record_editor

        QApplication.instance() or QApplication([])
        with patch("study_app.data.database.list_module_names", return_value=["数据结构"]):
            return record_editor.AddRecordDialog(None, Mock(), ("计算机科学",))

    def _problem(self):
        return {
            "title": "两数之和",
            "status": "wrong",
            "related_topics": ["哈希表"],
            "difficulty_score": 55,
        }

    def test_display_text_shows_zero_not_pending(self):
        from unittest.mock import patch

        editor = self._editor()
        with patch(
            "study_app.data.database.list_module_names", return_value=["数据结构"]
        ):
            text = editor._problem_display_text(self._problem())
        assert "正确率 0.0%" in text
        assert "待判断" not in text

    def test_preview_shows_zero_and_impact_not_pending(self):
        editor = self._editor()
        preview = editor._problem_preview_text(self._problem())
        assert "正确率：0.0%" in preview
        assert "估分影响：中等题做错" in preview
        assert "待判断" not in preview


def patch(target, return_value):
    from unittest.mock import patch as _patch

    return _patch(target, return_value=return_value)


def test_import_cross_record_isolation(tmp_path):
    """阻断1：A/B 两条记录导入后标量与明细互不串写。"""
    db = tmp_path / "a05l.sqlite"
    initialize_database(db)
    assert load_raw_records(db) == []  # C-03：只读回读不会触发种子导入
    conn = sqlite3.connect(db)
    records = [
        {
            "date": "2026-09-01",
            "subject": "隔离学科",
            "topic": "主题A",
            "activity": "exercise",
            "source": "outside_class",
            "problems": [
                {"title": "题目A1", "answer_result": "全对", "related_topics": ["主题A"]}
            ],
        },
        {
            "date": "2026-09-02",
            "subject": "隔离学科",
            "topic": "主题B",
            "activity": "exercise",
            "source": "outside_class",
            "problems": [
                {"title": "题目B1", "answer_result": "全对", "related_topics": ["主题B"]}
            ],
        },
    ]
    import_records_json_records(conn, records)
    conn.commit()
    conn.close()
    loaded = load_raw_records(db)
    by_topic = {
        item["topic"]: item
        for item in loaded
        if item.get("subject") == "隔离学科"
    }
    assert by_topic["主题A"]["problems"][0]["title"] == "题目A1"
    assert by_topic["主题B"]["problems"][0]["title"] == "题目B1"


def test_import_strict_rejects_bad_scalars_and_text(tmp_path):
    """阻断2：导入 strict 路径拒绝越界/零时长/字符串数值/乱码文本。"""
    db = tmp_path / "a05m.sqlite"
    initialize_database(db)
    conn = sqlite3.connect(db)
    cases = [
        {"score": 120},
        {"duration_minutes": 0},
        {"score": "80"},
        {"note": "?????"},
    ]
    for index, extra in enumerate(cases, start=1):
        record = {
            "date": "2026-09-0%d" % index,
            "subject": "隔离学科",
            "topic": "测试知识点",
            "activity": "exercise",
            "source": "outside_class",
        }
        record.update(extra)
        with pytest.raises(ValueError):
            import_records_json_records(conn, [record])
    conn.close()
    records = load_raw_records(db)
    assert [item for item in records if item.get("subject") == "隔离学科"] == []


def test_add_rejects_score_and_duration_out_of_contract(tmp_path):
    db = tmp_path / "a05n.sqlite"
    initialize_database(db)
    model_copy = _model_copy(tmp_path)
    with pytest.raises(ValueError):
        add_learning_record(
            {
                "date": "2026-09-01",
                "subject": "测试学科",
                "topic": "测试知识点",
                "activity": "exercise",
                "source": "outside_class",
                "score": 120,
            },
            db_path=db,
            model_path=model_copy,
        )
    with pytest.raises(ValueError):
        add_learning_record(
            {
                "date": "2026-09-01",
                "subject": "测试学科",
                "topic": "测试知识点",
                "activity": "exercise",
                "source": "outside_class",
                "duration_minutes": 0,
            },
            db_path=db,
            model_path=model_copy,
        )
    assert _counts(db)["learning_records"] == 0


def test_consistency_diag_covers_fields_and_count(tmp_path):
    """阻断3：字段差异与题目数不一致均产生 consistency_diag。"""
    db = tmp_path / "a05o.sqlite"
    initialize_database(db)
    raw = {
        "date": "2026-09-08",
        "subject": "隔离学科",
        "topic": "一致性",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [
            {
                "title": "T1",
                "correctness": 100,
                "partial_credit": 1.0,
                "status": "correct",
                "difficulty_score": 55,
            }
        ],
    }
    _seed_then_insert_raw(db, raw, json.dumps(raw, ensure_ascii=False))
    conn = sqlite3.connect(db)
    record_id = conn.execute(
        "SELECT id FROM learning_records WHERE subject_name = '隔离学科'"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO problem_attempts(
            record_id, title, status, correctness, difficulty_score, raw_json
        ) VALUES (?, 'T1', 'wrong', 0.0, 80, '{}')
        """,
        (record_id,),
    )
    conn.execute(
        """
        INSERT INTO problem_attempts(
            record_id, title, status, correctness, difficulty_score, raw_json
        ) VALUES (?, 'T2', 'correct', 1.0, 55, '{}')
        """,
        (record_id,),
    )
    conn.commit()
    conn.close()
    records = load_raw_records(db)
    target = [item for item in records if item.get("subject") == "隔离学科"]
    diag = target[0].get("consistency_diag")
    assert diag and (diag.get("mismatch_count") or diag.get("count_mismatch"))


def test_partial_credit_above_one_rejected(tmp_path):
    """阻断1：partial_credit 仅允许 0-1，50 等百分数形式拒绝。"""
    db = tmp_path / "a05pc.sqlite"
    initialize_database(db)
    with pytest.raises(ValueError):
        add_learning_record(
            {
                "date": "2026-09-01",
                "subject": "测试学科",
                "topic": "测试知识点",
                "activity": "exercise",
                "source": "outside_class",
                "problems": [{"title": "T", "partial_credit": 50}],
            },
            db_path=db,
            model_path=_model_copy(tmp_path),
        )
    assert _counts(db)["learning_records"] == 0


def test_correctness_one_not_misdivided_in_diag(tmp_path):
    """阻断2：correctness=1（0-1 形式）不得被诊断除以 100 误判。"""
    db = tmp_path / "a05c1.sqlite"
    initialize_database(db)
    raw = {
        "date": "2026-09-09",
        "subject": "隔离学科",
        "topic": "一致性",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [{"title": "C1", "correctness": 1, "partial_credit": 1.0}],
    }
    _seed_then_insert_raw(db, raw, json.dumps(raw, ensure_ascii=False))
    conn = sqlite3.connect(db)
    record_id = conn.execute(
        "SELECT id FROM learning_records WHERE subject_name = '隔离学科'"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO problem_attempts(
            record_id, title, correctness, related_topics_json, raw_json
        ) VALUES (?, 'C1', 1.0, '[]', '{}')
        """,
        (record_id,),
    )
    conn.commit()
    conn.close()
    records = load_raw_records(db)
    target = [item for item in records if item.get("subject") == "隔离学科"]
    assert target and "consistency_diag" not in target[0]


def test_related_topics_whitespace_json_not_flagged(tmp_path):
    """阻断3：related_topics 仅 JSON 空格格式不同时不产生诊断。"""
    db = tmp_path / "a05rt.sqlite"
    initialize_database(db)
    raw = {
        "date": "2026-09-10",
        "subject": "隔离学科",
        "topic": "一致性",
        "activity": "exercise",
        "source": "outside_class",
        "score": 80,
        "problems": [
            {
                "title": "C2",
                "correctness": 100,
                "partial_credit": 1.0,
                "related_topics": ["哈希表", "AVL 树"],
            }
        ],
    }
    _seed_then_insert_raw(db, raw, json.dumps(raw, ensure_ascii=False))
    conn = sqlite3.connect(db)
    record_id = conn.execute(
        "SELECT id FROM learning_records WHERE subject_name = '隔离学科'"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO problem_attempts(
            record_id, title, correctness, related_topics_json, raw_json
        ) VALUES (?, 'C2', 1.0, '[ "哈希表" ,  "AVL 树" ]', '{}')
        """.replace("AVL 树", "AVL 树"),
        (record_id,),
    )
    conn.commit()
    conn.close()
    records = load_raw_records(db)
    target = [item for item in records if item.get("subject") == "隔离学科"]
    assert target and "consistency_diag" not in target[0]


def test_ensure_seeded_lenient_imports_legacy(tmp_path):
    """阻断3未闭环项：ensure_seeded 宽松种子路径导入含旧格式字段的 legacy JSON 成功。"""
    db = tmp_path / "a05len.sqlite"
    initialize_database(db)
    legacy_records = {
        "records": [
            {
                "date": "2026-06-01",
                "subject": "旧学科",
                "topic": "旧题目",
                "activity": "exercise",
                "source": "outside_class",
                "score": "80",
                "problems": [{"title": "旧题", "answer_result": "全对"}],
            }
        ]
    }
    records_path = tmp_path / "legacy_records.json"
    records_path.write_text(
        json.dumps(legacy_records, ensure_ascii=False), encoding="utf-8"
    )
    model_path = _model_copy(tmp_path)
    from study_app.data.database import import_current_json_files

    import_current_json_files(
        db, model_path=model_path, records_path=records_path, strict=False
    )
    conn = sqlite3.connect(db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM learning_records WHERE subject_name = '旧学科'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_ensure_seeded_imports_once_and_ids_do_not_skip(tmp_path, monkeypatch):
    """签核前整改：ensure_seeded 仅导入一次；重复调用零写入；新记录 id 不跳号。"""
    db = tmp_path / "a05seed.sqlite"
    initialize_database(db)

    # Exercise the legacy import using synthetic records, never checkout data.
    import study_app.data.database as database_module
    monkeypatch.setattr(database_module, "ROOT", tmp_path)
    (tmp_path / "learning_model_v1.json").write_bytes(_model_copy(tmp_path).read_bytes())
    (tmp_path / "learning_records.json").write_text(
        json.dumps({"records": [{
            "date": "2026-01-01", "subject": "测试学科",
            "module": "测试模块", "topic": "测试知识点",
            "activity": "exercise", "source": "outside_class", "score": 50,
        }]}, ensure_ascii=False), encoding="utf-8",
    )

    ensure_seeded_database(db)
    conn = sqlite3.connect(db)
    first_count = conn.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0]
    first_max_id = conn.execute("SELECT MAX(id) FROM learning_records").fetchone()[0]
    conn.close()
    assert first_count > 0
    # 判别性断言：空库首播后 MAX(id) 必须等于 COUNT。
    # 双重导入会留下「1..N 已删、N+1..2N 存活」的跳号（MAX ≈ 2×COUNT），此处即失败。
    assert first_max_id == first_count

    ensure_seeded_database(db)
    conn = sqlite3.connect(db)
    second_count = conn.execute("SELECT COUNT(*) FROM learning_records").fetchone()[0]
    second_max_id = conn.execute("SELECT MAX(id) FROM learning_records").fetchone()[0]
    conn.close()
    assert second_count == first_count
    assert second_max_id == first_max_id

    rid = add_learning_record(
        {
            "date": "2026-09-11",
            "subject": "测试学科",
            "topic": "新记录",
            "activity": "exercise",
            "source": "outside_class",
            "score": 80,
        },
        db_path=db,
        model_path=_model_copy(tmp_path),
    )
    assert rid == first_max_id + 1
