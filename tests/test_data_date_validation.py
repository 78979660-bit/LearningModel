# -*- coding: utf-8 -*-
"""A-01 定向测试：真实日历日期验证（BUG-03；data_contract_v1 §4.3）。

覆盖分支：
- 非法日历日期在任何数据库写入前拒绝，失败零残留；
- 合法闰日与普通边界日期被接受；
- 缺失 / 空值 / 非补零 / 错误分隔符 / 非日期字符串 / 越界月份分别拒绝；
- 既有异常日期在读取时产生 data_warning 诊断，窗口统计不崩溃。

隔离：临时 SQLite + learning_model_v1.json 副本；真实数据零写入。
"""
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from study_app.data.database import (
    add_learning_record,
    ensure_seeded_database,
    initialize_database,
    load_raw_records,
)
from learning_monitor import records_in_window

ROOT = Path(__file__).resolve().parents[1]

BASE_RECORD = {
    "subject": "测试学科",
    "module": "测试模块",
    "topic": "测试知识点",
    "activity": "exercise",
    "source": "outside_class",
    "score": 50,
}


def _model_copy(tmp_path: Path) -> Path:
    copy_path = tmp_path / "model_copy.json"
    copy_path.write_bytes((ROOT / "tests" / "fixtures" / "minimal_model.json").read_bytes())
    return copy_path


def _tmp_db(tmp_path: Path, name: str = "a01.sqlite") -> Path:
    db = tmp_path / name
    initialize_database(db)
    return db


def _counts(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("learning_records", "problem_attempts", "record_attachments")
        }
    finally:
        conn.close()


def test_invalid_calendar_date_rejected_before_any_write(tmp_path):
    db = _tmp_db(tmp_path)
    with pytest.raises(ValueError):
        add_learning_record(
            {**BASE_RECORD, "date": "2026-02-30"},
            db_path=db,
            model_path=_model_copy(tmp_path),
        )
    assert _counts(db) == {
        "learning_records": 0,
        "problem_attempts": 0,
        "record_attachments": 0,
    }


def test_leap_and_normal_dates_accepted(tmp_path):
    db = tmp_path / "a01_ok.sqlite"
    model_copy = _model_copy(tmp_path)
    ensure_seeded_database(db, model_path=model_copy)
    rid1 = add_learning_record(
        {**BASE_RECORD, "date": "2028-02-29", "score": 60},
        db_path=db,
        model_path=model_copy,
    )
    rid2 = add_learning_record(
        {**BASE_RECORD, "date": "2026-09-12"},
        db_path=db,
        model_path=model_copy,
    )
    assert rid1 > 0 and rid2 > 0 and rid1 != rid2


@pytest.mark.parametrize(
    "bad",
    [None, "", "2026-9-1", "2026/09/12", "not-a-date", "2026-13-01"],
)
def test_malformed_dates_rejected(tmp_path, bad):
    db = _tmp_db(tmp_path, f"a01_bad.sqlite")
    with pytest.raises(ValueError):
        add_learning_record(
            {**BASE_RECORD, "date": bad},
            db_path=db,
            model_path=_model_copy(tmp_path),
        )
    assert _counts(db)["learning_records"] == 0


def test_legacy_invalid_date_diagnosed_on_read_and_window_survives(tmp_path):
    db = _tmp_db(tmp_path, "a01_legacy.sqlite")
    assert load_raw_records(db) == []  # C-03：只读回读不种子化或清除异常行
    conn = sqlite3.connect(db)
    try:
        legacy = {
            "date": "2026-02-30",
            "subject": "测试学科",
            "topic": "历史异常",
            "activity": "exercise",
            "source": "outside_class",
            "score": 50,
        }
        conn.execute(
            """
            INSERT INTO learning_records(
                record_date, subject_name, topic_name, activity, source,
                score, note, raw_json
            ) VALUES ('2026-02-30', '测试学科', '历史异常', 'exercise',
                      'outside_class', 50, '', ?)
            """,
            (json.dumps(legacy, ensure_ascii=False),),
        )
        conn.commit()
    finally:
        conn.close()

    records = load_raw_records(db)
    target = [item for item in records if item.get("date") == "2026-02-30"]
    assert target and target[0].get("data_warning") == "invalid_date"

    got = records_in_window(records, "测试学科", date(2026, 2, 28), date(2026, 3, 2))
    assert got == []
