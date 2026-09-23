from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from study_app.data.database import (
    DEFAULT_DB_PATH,
    connect,
    get_counts,
    load_raw_records_from_connection,
)
from study_app.data.text_integrity import corruption_reason
from study_app.paths import BACKUPS_DIR, USER_ROOT


BACKUP_ROOT = BACKUPS_DIR


@dataclass(frozen=True)
class BackupResult:
    folder: Path
    archive: Path
    database_copy: Path
    manifest: Path
    model_copy: Path | None = None
    attachments_dir: Path | None = None


def create_backup(
    db_path: Path | str = DEFAULT_DB_PATH,
    backup_root: Path | str = BACKUP_ROOT,
    include_archive: bool = True,
) -> BackupResult:
    source_db = Path(db_path)
    if not source_db.exists():
        raise FileNotFoundError(f"数据库不存在：{source_db}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(backup_root)
    folder, archive = _reserve_backup_paths(root, timestamp)
    try:
        database_copy = folder / "learning_app.sqlite"
        _backup_sqlite(source_db, database_copy)

        with connect(database_copy) as backup_connection:
            exported_records = load_raw_records_from_connection(backup_connection)

        records_path = folder / "learning_records_export.json"
        records_path.write_text(
            json.dumps({"records": exported_records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        manifest = folder / "manifest.json"
        manifest_data = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_database": str(source_db),
            "database_copy": str(database_copy.name),
            "records_export": str(records_path.name),
            "counts": get_counts(database_copy),
            "format_version": 1,
        }
        manifest.write_text(
            json.dumps(manifest_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if include_archive:
            _zip_folder(folder, archive)
        else:
            archive.unlink()
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return BackupResult(folder=folder, archive=archive, database_copy=database_copy, manifest=manifest)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_recovery_package(
    db_path: Path | str,
    model_path: Path | str,
    package_root: Path | str,
    *,
    attachment_base: Path | str = USER_ROOT,
) -> BackupResult:
    """Create a self-contained, checksummed recovery unit without touching live data."""
    result = create_backup(db_path, package_root, include_archive=False)
    folder = result.folder
    archive = result.archive
    try:
        source_model = Path(model_path)
        if not source_model.is_file():
            raise FileNotFoundError(f"学习模型不存在：{source_model}")
        model_copy = folder / "learning_model_v1.json"
        shutil.copy2(source_model, model_copy)

        records_path = folder / "learning_records_export.json"
        records = json.loads(records_path.read_text(encoding="utf-8")).get("records", [])
        attachments_dir = folder / "attachments"
        attachment_entries = []
        copied_paths: dict[Path, Path] = {}
        base = Path(attachment_base)
        for record in records:
            for attachment in record.get("attachments", []) or []:
                original_text = str(attachment.get("file_path") or "").strip()
                if not original_text:
                    raise FileNotFoundError("附件记录缺少 file_path")
                original = Path(original_text)
                source = original if original.is_absolute() else base / original
                source = source.resolve()
                if not source.is_file():
                    raise FileNotFoundError(f"附件不存在：{source}")
                bundled = copied_paths.get(source)
                if bundled is None:
                    digest = _sha256(source)
                    safe_name = source.name.replace("/", "_").replace("\\", "_")
                    bundled = Path("attachments") / f"{digest[:16]}_{safe_name}"
                    target = folder / bundled
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    copied_paths[source] = bundled
                bundled_file = folder / bundled
                attachment_entries.append(
                    {
                        "record_id": record.get("id"),
                        "original_path": original_text,
                        "bundled_path": bundled.as_posix(),
                        "sha256": _sha256(bundled_file),
                        "size": bundled_file.stat().st_size,
                    }
                )

        with connect(result.database_copy) as connection:
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            foreign_key_check = sorted([list(row) for row in foreign_keys])
            state_checks = {
                "app_settings": connection.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0],
                "idempotency_markers": connection.execute(
                    "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'mastery_contribution_applied:%'"
                ).fetchone()[0],
                "subject_statuses": connection.execute(
                    "SELECT COUNT(*) FROM subjects WHERE status IS NOT NULL"
                ).fetchone()[0],
                "topic_mastery_values": connection.execute(
                    "SELECT COUNT(*) FROM topics WHERE mastery IS NOT NULL"
                ).fetchone()[0],
                "foreign_key_violations": len(foreign_keys),
                "foreign_key_check": foreign_key_check,
            }

        components = []
        for component_type, component_path in (
            ("database", result.database_copy),
            ("records_export", records_path),
            ("learning_model", model_copy),
        ):
            components.append(
                {
                    "type": component_type,
                    "path": component_path.relative_to(folder).as_posix(),
                    "sha256": _sha256(component_path),
                    "size": component_path.stat().st_size,
                }
            )
        for bundled in sorted(set(copied_paths.values()), key=lambda item: item.as_posix()):
            component_path = folder / bundled
            components.append(
                {
                    "type": "attachment",
                    "path": bundled.as_posix(),
                    "sha256": _sha256(component_path),
                    "size": component_path.stat().st_size,
                }
            )

        manifest_data = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "format_version": 2,
            "application_version": "personal-learning-os-recovery-v1",
            "attachment_policy": "included",
            "database_copy": result.database_copy.name,
            "model_copy": model_copy.name,
            "records_export": records_path.name,
            "counts": get_counts(result.database_copy),
            "state_checks": state_checks,
            "attachments": attachment_entries,
            "components": components,
        }
        result.manifest.write_text(
            json.dumps(manifest_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _zip_folder(folder, archive)
        return BackupResult(
            folder=folder,
            archive=archive,
            database_copy=result.database_copy,
            manifest=result.manifest,
            model_copy=model_copy,
            attachments_dir=attachments_dir,
        )
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        archive.unlink(missing_ok=True)
        raise


def restore_recovery_package(
    package_folder: Path | str,
    target_root: Path | str,
) -> Path:
    """Verify and restore a complete package into a new isolated directory."""
    source = Path(package_folder).resolve()
    target = Path(target_root).resolve()
    live_root = USER_ROOT.resolve()
    if target == live_root or live_root in target.parents:
        raise ValueError("禁止在真实现用目录直接恢复")
    if target.exists():
        raise FileExistsError(f"恢复目标必须是不存在的隔离目录：{target}")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 2:
        raise ValueError("不支持的恢复包版本")
    if manifest.get("attachment_policy") != "included":
        raise ValueError("恢复包未包含附件实体")

    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("恢复包缺少组件清单")
    for component in components:
        relative = Path(str(component.get("path") or ""))
        component_path = (source / relative).resolve()
        try:
            component_path.relative_to(source)
        except ValueError as error:
            raise ValueError("恢复包组件路径越界") from error
        if not component_path.is_file():
            raise FileNotFoundError(f"恢复包组件不存在：{relative}")
        if _sha256(component_path) != component.get("sha256"):
            raise ValueError(f"恢复包组件哈希不匹配：{relative}")
        if component_path.stat().st_size != component.get("size"):
            raise ValueError(f"恢复包组件大小不匹配：{relative}")

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        staged.mkdir()
        for component in components:
            relative = Path(component["path"])
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
        shutil.copy2(manifest_path, staged / "manifest.json")

        restored_db = staged / manifest["database_copy"]
        attachment_entries = manifest.get("attachments", [])
        with connect(restored_db) as connection:
            for attachment in attachment_entries:
                final_path = target / Path(attachment["bundled_path"])
                connection.execute(
                    "UPDATE record_attachments SET file_path = ? WHERE record_id = ? AND file_path = ?",
                    (
                        str(final_path),
                        attachment.get("record_id"),
                        attachment.get("original_path"),
                    ),
                )
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        actual_foreign_keys = sorted([list(row) for row in foreign_keys])
        expected_foreign_keys = manifest.get("state_checks", {}).get(
            "foreign_key_check", []
        )
        if actual_foreign_keys != expected_foreign_keys:
            raise sqlite3.IntegrityError("恢复后的外键状态与 manifest 不一致")
        if get_counts(restored_db) != manifest.get("counts"):
            raise ValueError("恢复后的数据库计数与 manifest 不一致")
        os.replace(staged, target)
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
    return target


def _reserve_backup_paths(root: Path, timestamp: str) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    for sequence in range(1000):
        suffix = "" if sequence == 0 else f"_{sequence:03d}"
        folder = root / f"learning_backup_{timestamp}{suffix}"
        archive = folder.with_suffix(".zip")
        try:
            archive.touch(exist_ok=False)
        except FileExistsError:
            continue
        try:
            folder.mkdir(exist_ok=False)
        except FileExistsError:
            archive.unlink()
            continue
        except Exception:
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return folder, archive
    raise FileExistsError(f"无法为 {timestamp} 分配备份目录和归档名。")


def restore_database_from_backup(
    backup_database: Path | str,
    target_db: Path | str = DEFAULT_DB_PATH,
    safety_backup: bool = True,
    backup_root: Path | str | None = None,
) -> Path:
    source = Path(backup_database)
    target = Path(target_db)
    if not source.exists():
        raise FileNotFoundError(f"备份数据库不存在：{source}")
    if safety_backup and target.exists():
        if backup_root is None:
            create_backup(target)
        else:
            create_backup(target, backup_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, staged)
        with staged.open("rb+") as staged_file:
            os.fsync(staged_file.fileno())
        connection = sqlite3.connect(staged)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
        finally:
            connection.close()
        if not row or row[0] != "ok":
            raise sqlite3.DatabaseError(f"备份数据库完整性检查失败：{row[0] if row else 'unknown'}")
        if foreign_key_violations:
            raise sqlite3.DatabaseError(
                "备份数据库外键检查失败："
                f"发现 {len(foreign_key_violations)} 处约束异常"
            )
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return target


def integrity_check(db_path: Path | str = DEFAULT_DB_PATH) -> str:
    with connect(db_path) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        sqlite_result = row[0] if row else "unknown"
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        text_issues = scan_database_text_integrity(connection)
    foreign_key_result = (
        "外键检查通过"
        if not foreign_key_violations
        else f"外键异常 {len(foreign_key_violations)} 处"
    )
    if text_issues:
        examples = "；".join(text_issues[:5])
        suffix = f"；另有 {len(text_issues) - 5} 处" if len(text_issues) > 5 else ""
        return (
            f"SQLite: {sqlite_result}；{foreign_key_result}；"
            f"文本编码异常 {len(text_issues)} 处：{examples}{suffix}"
        )
    return f"SQLite: {sqlite_result}；{foreign_key_result}；文本编码检查通过"


def scan_database_text_integrity(connection: sqlite3.Connection) -> list[str]:
    issues: list[str] = []
    tables = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    for table_row in tables:
        table = table_row[0]
        columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        text_columns = [column[1] for column in columns if "TEXT" in str(column[2]).upper()]
        if not text_columns:
            continue
        selected = ", ".join(f'"{column}"' for column in text_columns)
        rows = connection.execute(f'SELECT rowid, {selected} FROM "{table}"')
        for row in rows:
            row_id = row[0]
            for index, column in enumerate(text_columns, start=1):
                value = row[index]
                if isinstance(value, str):
                    reason = corruption_reason(value)
                    if reason:
                        issues.append(f"{table}#{row_id}.{column}: {reason}")
    return issues


def _backup_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    try:
        target_connection = sqlite3.connect(target)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()


def _zip_folder(folder: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for path in folder.rglob("*"):
            if path.is_file():
                zip_file.write(path, path.relative_to(folder))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Backup or check the study app SQLite data.")
    parser.add_argument("--check", action="store_true", help="Run SQLite integrity_check only.")
    parser.add_argument("--no-zip", action="store_true", help="Create folder backup without zip archive.")
    args = parser.parse_args()

    if args.check:
        print(integrity_check())
        return

    result = create_backup(include_archive=not args.no_zip)
    print(result.folder)
    if result.archive.exists():
        print(result.archive)


if __name__ == "__main__":
    main()
