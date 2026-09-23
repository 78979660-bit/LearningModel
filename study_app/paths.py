from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from study_app.app_metadata import APP_INTERNAL_NAME


LOGGER = logging.getLogger(__name__)
DATA_ROOT_ENV = "LEARNINGMODEL_DATA_ROOT"
LEGACY_ROOT_ENV = "LEARNINGMODEL_LEGACY_ROOT"
_THREAD_MIGRATION_LOCK = threading.Lock()


def _resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[1]


def _user_root() -> Path:
    override = os.environ.get(DATA_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / APP_INTERNAL_NAME
    if sys.platform.startswith("win"):
        return Path.home() / "AppData" / "Local" / APP_INTERNAL_NAME
    return Path.home() / ".local" / "share" / APP_INTERNAL_NAME


RESOURCE_ROOT = _resource_root()
USER_ROOT = _user_root()
DATA_DIR = USER_ROOT / "data"
BACKUPS_DIR = USER_ROOT / "backups"
LOGS_DIR = USER_ROOT / "logs"
CACHE_DIR = USER_ROOT / "cache"
EXPORTS_DIR = USER_ROOT / "exports"
DATABASE_PATH = DATA_DIR / "learning_app.sqlite"
MODEL_PATH = DATA_DIR / "learning_model_v1.json"
RECORDS_PATH = DATA_DIR / "learning_records.json"
TESSDATA_DIR = DATA_DIR / "tesseract" / "tessdata"
DEFAULT_RESOURCES_DIR = RESOURCE_ROOT / "study_app" / "resources"
DEFAULT_MODEL_PATH = DEFAULT_RESOURCES_DIR / "default_model.json"
DEFAULT_RECORDS_PATH = DEFAULT_RESOURCES_DIR / "default_records.json"
LAUNCHER_LOG_PATH = LOGS_DIR / "study_app_launcher.log"
RUNTIME_LOG_PATH = LOGS_DIR / "study_app.log"
PERFORMANCE_LOG_PATH = LOGS_DIR / "performance.jsonl"
MIGRATION_MARKER_PATH = DATA_DIR / ".legacy-migration.json"


@dataclass(frozen=True)
class LegacyMigrationResult:
    source_root: Path | None
    migrated_files: tuple[str, ...]
    used_defaults: tuple[str, ...]


def ensure_user_directories() -> None:
    for path in (DATA_DIR, BACKUPS_DIR, LOGS_DIR, CACHE_DIR, EXPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


@contextmanager
def _migration_lock(timeout_seconds: float = 30.0):
    """Serialize migration across threads and processes without stale lock files."""
    ensure_user_directories()
    lock_path = DATA_DIR / ".migration.lock"
    started = time.monotonic()
    with _THREAD_MIGRATION_LOCK:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if sys.platform.startswith("win"):
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() - started >= timeout_seconds:
                            raise TimeoutError("等待用户数据迁移锁超时")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() - started >= timeout_seconds:
                            raise TimeoutError("等待用户数据迁移锁超时")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_json(path: Path, expected_top_level: str | None = None) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    if expected_top_level is not None and expected_top_level not in payload:
        raise ValueError(f"JSON 缺少 {expected_top_level!r}：{path}")


def _validate_database(path: Path) -> None:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError(f"SQLite 完整性校验失败：{result}")
    finally:
        connection.close()


def _validate_nonempty_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"文件为空或不存在：{path}")


def _atomic_copy(source: Path, destination: Path, validator) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        validator(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    _validate_database(destination)


def _atomic_database_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        _backup_sqlite(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _legacy_root(explicit: Path | str | None = None) -> Path | None:
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"显式指定的旧版数据目录不存在：{candidate}")
        if not candidate.is_dir():
            raise NotADirectoryError(f"显式指定的旧版数据路径不是目录：{candidate}")
        return candidate
    configured = os.environ.get(LEGACY_ROOT_ENV, "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(
                f"环境变量 {LEGACY_ROOT_ENV} 指向的旧版数据目录不存在：{candidate}"
            )
        if not candidate.is_dir():
            raise NotADirectoryError(
                f"环境变量 {LEGACY_ROOT_ENV} 指向的路径不是目录：{candidate}"
            )
        return candidate
    if not getattr(sys, "frozen", False):
        candidate = RESOURCE_ROOT
        if (
            (candidate / "app_data" / "learning_app.sqlite").is_file()
            or (candidate / "learning_model_v1.json").is_file()
            or (candidate / "learning_records.json").is_file()
        ):
            return candidate
    return None


def migrate_legacy_data(
    legacy_root: Path | str | None = None,
) -> LegacyMigrationResult:
    """Copy legacy data through a verified backup without altering the source."""
    ensure_user_directories()
    source_root = _legacy_root(legacy_root)
    migrated: list[str] = []
    defaults: list[str] = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    migration_backup = BACKUPS_DIR / f"legacy_migration_{stamp}_{uuid.uuid4().hex[:8]}"

    legacy_items: tuple[tuple[str, Path, Path, object], ...] = ()
    if source_root is not None:
        legacy_items = (
            (
                "learning_app.sqlite",
                source_root / "app_data" / "learning_app.sqlite",
                DATABASE_PATH,
                _validate_database,
            ),
            (
                "learning_model_v1.json",
                source_root / "learning_model_v1.json",
                MODEL_PATH,
                lambda path: _validate_json(path, "subjects"),
            ),
            (
                "learning_records.json",
                source_root / "learning_records.json",
                RECORDS_PATH,
                lambda path: _validate_json(path, "records"),
            ),
        )

    for name, source, destination, validator in legacy_items:
        if destination.exists() or not source.is_file():
            continue
        migration_backup.mkdir(parents=True, exist_ok=True)
        backup_file = migration_backup / name
        if name.endswith(".sqlite"):
            _backup_sqlite(source, backup_file)
            _atomic_copy(backup_file, destination, _validate_database)
        else:
            _atomic_copy(source, backup_file, validator)
            _atomic_copy(backup_file, destination, validator)
        if _sha256(backup_file) != _sha256(destination):
            destination.unlink(missing_ok=True)
            raise ValueError(f"迁移校验和不一致：{name}")
        migrated.append(name)

    if source_root is not None:
        legacy_tessdata = source_root / "app_data" / "tesseract" / "tessdata"
        if legacy_tessdata.is_dir():
            for source in sorted(legacy_tessdata.iterdir(), key=lambda item: item.name):
                if (
                    not source.is_file()
                    or source.suffix.casefold() != ".traineddata"
                    or source.name in {".", ".."}
                ):
                    continue
                destination = TESSDATA_DIR / source.name
                if destination.exists():
                    continue
                relative_name = f"tesseract/tessdata/{source.name}"
                backup_file = migration_backup / "tesseract" / "tessdata" / source.name
                migration_backup.mkdir(parents=True, exist_ok=True)
                _atomic_copy(source, backup_file, _validate_nonempty_file)
                _atomic_copy(backup_file, destination, _validate_nonempty_file)
                if _sha256(backup_file) != _sha256(destination):
                    destination.unlink(missing_ok=True)
                    raise ValueError(f"迁移校验和不一致：{relative_name}")
                migrated.append(relative_name)

    if not MODEL_PATH.exists():
        _atomic_copy(
            DEFAULT_MODEL_PATH,
            MODEL_PATH,
            lambda path: _validate_json(path, "subjects"),
        )
        defaults.append(MODEL_PATH.name)
    if not RECORDS_PATH.exists():
        _atomic_copy(
            DEFAULT_RECORDS_PATH,
            RECORDS_PATH,
            lambda path: _validate_json(path, "records"),
        )
        defaults.append(RECORDS_PATH.name)

    # A no-op startup must not erase the receipt of the first real migration.
    # Keep a valid existing marker until a later run actually copies a new
    # source file or installs a missing default.
    if not migrated and not defaults and MIGRATION_MARKER_PATH.is_file():
        try:
            existing_marker = json.loads(
                MIGRATION_MARKER_PATH.read_text(encoding="utf-8")
            )
            if (
                isinstance(existing_marker, dict)
                and existing_marker.get("completed_at")
                and isinstance(existing_marker.get("migrated_files"), list)
                and isinstance(existing_marker.get("used_defaults"), list)
            ):
                return LegacyMigrationResult(source_root, (), ())
        except (OSError, UnicodeError, json.JSONDecodeError):
            LOGGER.warning("Existing legacy migration marker is unreadable; replacing it")

    marker = {
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(source_root) if source_root is not None else None,
        "migrated_files": migrated,
        "used_defaults": defaults,
    }
    temporary_marker = MIGRATION_MARKER_PATH.with_name(
        f".{MIGRATION_MARKER_PATH.name}.{uuid.uuid4().hex}.tmp"
    )
    temporary_marker.write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_marker, MIGRATION_MARKER_PATH)
    return LegacyMigrationResult(source_root, tuple(migrated), tuple(defaults))


def ensure_user_layout(
    legacy_root: Path | str | None = None,
) -> LegacyMigrationResult:
    with _migration_lock():
        return migrate_legacy_data(legacy_root)
