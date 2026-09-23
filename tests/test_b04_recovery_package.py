from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from study_app.data import backup, database


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path):
    db = tmp_path / "source" / "learning.sqlite"
    model = tmp_path / "source" / "learning_model_v1.json"
    attachment = tmp_path / "source" / "materials" / "note.txt"
    model.parent.mkdir(parents=True)
    attachment.parent.mkdir(parents=True)
    model.write_text(json.dumps({"subjects": [{"name": "测试学科"}]}), encoding="utf-8")
    attachment.write_text("attachment payload", encoding="utf-8")
    database.initialize_database(db)
    with database.connect(db) as connection:
        subject_id = connection.execute(
            "INSERT INTO subjects(name, status, mastery) VALUES (?, ?, ?)",
            ("测试学科", "active", 0.4),
        ).lastrowid
        module_id = connection.execute(
            "INSERT INTO modules(subject_id, name, status, mastery) VALUES (?, ?, ?, ?)",
            (subject_id, "测试模块", "learning", 0.5),
        ).lastrowid
        connection.execute(
            "INSERT INTO topics(module_id, name, status, mastery) VALUES (?, ?, ?, ?)",
            (module_id, "测试主题", "learning", 0.6),
        )
        record_id = connection.execute(
            "INSERT INTO learning_records(record_date, subject_name, activity, source, raw_json) VALUES (?, ?, ?, ?, ?)",
            ("2026-09-12", "测试学科", "review", "outside_class", json.dumps({"date": "2026-09-12", "subject": "测试学科"})),
        ).lastrowid
        connection.execute(
            "INSERT INTO record_attachments(record_id, file_path, file_name) VALUES (?, ?, ?)",
            (record_id, str(attachment), attachment.name),
        )
        connection.execute(
            "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
            ("mastery_contribution_applied:id:1", "true"),
        )
    return db, model, attachment


def test_complete_package_contains_model_attachments_hashes_and_state(tmp_path):
    db, model, attachment = _fixture(tmp_path)

    result = backup.create_recovery_package(db, model, tmp_path / "packages")
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))

    assert result.archive.is_file()
    assert result.model_copy.read_bytes() == model.read_bytes()
    assert manifest["format_version"] == 2
    assert manifest["attachment_policy"] == "included"
    assert manifest["state_checks"]["foreign_key_violations"] == 0
    assert manifest["state_checks"]["idempotency_markers"] == 1
    assert len(manifest["attachments"]) == 1
    bundled = result.folder / manifest["attachments"][0]["bundled_path"]
    assert bundled.read_bytes() == attachment.read_bytes()
    assert all(_sha256(result.folder / item["path"]) == item["sha256"] for item in manifest["components"])


def test_isolated_round_trip_restores_database_model_and_attachment(tmp_path):
    db, model, attachment = _fixture(tmp_path)
    result = backup.create_recovery_package(db, model, tmp_path / "packages")
    target = tmp_path / "isolated-restore"

    restored = backup.restore_recovery_package(result.folder, target)

    assert restored == target.resolve()
    assert (target / "learning_model_v1.json").read_bytes() == model.read_bytes()
    with database.connect(target / "learning_app.sqlite") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        row = connection.execute("SELECT file_path FROM record_attachments").fetchone()
        restored_attachment = Path(row["file_path"])
        assert restored_attachment.is_file()
        assert restored_attachment.read_bytes() == attachment.read_bytes()
        assert connection.execute("SELECT status FROM subjects").fetchone()[0] == "active"
        assert connection.execute("SELECT mastery FROM topics").fetchone()[0] == pytest.approx(0.6)
        assert connection.execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE 'mastery_contribution_applied:%'").fetchone()[0] == 1


def test_round_trip_preserves_declared_preexisting_foreign_key_violation(tmp_path):
    db, model, _attachment = _fixture(tmp_path)
    with database.connect(db) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO problem_attempts(record_id, title, raw_json) VALUES (?, ?, ?)",
            (999, "历史孤立题", json.dumps({"title": "历史孤立题"})),
        )

    result = backup.create_recovery_package(db, model, tmp_path / "packages")
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["state_checks"]["foreign_key_violations"] == 1
    assert manifest["state_checks"]["foreign_key_check"] == [
        ["problem_attempts", 1, "learning_records", 0]
    ]

    target = tmp_path / "isolated-with-known-orphan"
    backup.restore_recovery_package(result.folder, target)
    with database.connect(target / "learning_app.sqlite") as connection:
        assert [list(row) for row in connection.execute("PRAGMA foreign_key_check")] == [
            ["problem_attempts", 1, "learning_records", 0]
        ]


def test_tampered_package_fails_without_creating_target_or_changing_source(tmp_path):
    db, model, _attachment = _fixture(tmp_path)
    source_hash = _sha256(db)
    result = backup.create_recovery_package(db, model, tmp_path / "packages")
    result.model_copy.write_text("tampered", encoding="utf-8")
    target = tmp_path / "failed-restore"

    with pytest.raises(ValueError, match="哈希不匹配"):
        backup.restore_recovery_package(result.folder, target)

    assert not target.exists()
    assert _sha256(db) == source_hash


def test_missing_attachment_rejects_and_removes_incomplete_package(tmp_path):
    db, model, attachment = _fixture(tmp_path)
    attachment.unlink()
    package_root = tmp_path / "packages"

    with pytest.raises(FileNotFoundError, match="附件不存在"):
        backup.create_recovery_package(db, model, package_root)

    assert list(package_root.iterdir()) == []


@pytest.mark.parametrize("relative_target", [None, "recovery-child"])
def test_restore_rejects_live_project_root(tmp_path, relative_target):
    db, model, _attachment = _fixture(tmp_path)
    result = backup.create_recovery_package(db, model, tmp_path / "packages")
    target = backup.USER_ROOT if relative_target is None else backup.USER_ROOT / relative_target

    with pytest.raises(ValueError, match="真实现用目录"):
        backup.restore_recovery_package(result.folder, target)
