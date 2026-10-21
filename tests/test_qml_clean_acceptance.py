"""Real Windows SQLite count probes must never use the self-test fixture counts."""
from pathlib import Path
import hashlib
import shutil
import sqlite3
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows SQLite acceptance API")
SCRIPT = Path(__file__).resolve().parents[1] / "tools/clean_windows_acceptance.ps1"
TABLES = ("subjects", "modules", "topics", "learning_records", "problem_attempts")


def probe(tmp_path, database):
    # Extract only the public read-only probe; never execute Install/Finish here.
    source = SCRIPT.read_text("utf-8")
    start = source.index("function Get-ActualDatabaseCounts(")
    end = source.index("\nNew-Item -ItemType Directory -Path $results", start)
    function = source[start:end]
    runner = tmp_path / "probe.ps1"
    runner.write_text("param([string]$Database)\n$ErrorActionPreference='Stop'\n" + function +
                      '\nGet-ActualDatabaseCounts $Database | ConvertTo-Json -Compress\n', encoding="utf-8")
    shell = shutil.which("pwsh")
    assert shell, "PowerShell 7 required for Windows acceptance probe verification"
    return subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(runner),
                           "-Database", str(database)], capture_output=True, text=True,
                          encoding="utf-8", timeout=30)


def test_counts_actual_nonempty_unicode_database_without_modification(tmp_path):
    import json
    database = tmp_path / "真实验收[数据].sqlite"
    with sqlite3.connect(database) as connection:
        for index, table in enumerate(TABLES, 1):
            connection.execute(f"CREATE TABLE {table} (id INTEGER)")
            connection.executemany(f"INSERT INTO {table} VALUES (?)", [(n,) for n in range(index)])
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = probe(tmp_path, database)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {table: n for n, table in enumerate(TABLES, 1)}
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_missing_actual_database_is_rejected_without_creation(tmp_path):
    database = tmp_path / "missing.sqlite"
    result = probe(tmp_path, database)
    assert result.returncode != 0 and "Actual acceptance database is missing" in result.stderr
    assert not database.exists()


def test_wrong_schema_is_rejected_without_mutation(tmp_path):
    database = tmp_path / "wrong-schema.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = probe(tmp_path, database)
    assert result.returncode != 0 and "Actual database count failed" in result.stderr
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
