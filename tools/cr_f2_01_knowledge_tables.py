"""CR-F2-01 — controlled installation of the four F2 knowledge tables.

Fixed SQL source of truth: the DDL constants in study_app/data/database.py
(the exact statements tests install). Identity mapping replicates
database.register_topic_identities inside the same transaction so the whole
migration is one atomic unit on production.

Subcommands
  --check DB            read-only preflight (F2 presence, topic count,
                        integrity, FK violation fingerprint)
  --rehearse DB DIR     copy DB into an isolated directory, migrate the copy,
                        verify, print the expected/actual diff
  --apply DB            production migration (guards + backup must exist;
                        one BEGIN IMMEDIATE transaction)
  --verify DB BASELINE  post-condition verification (structure + mapping +
                        integrity + FK baseline)
  --baseline DB OUT     capture the FK violation fingerprint to JSON

Run with the project root as cwd:  py -3.14 tools/cr_f2_01_knowledge_tables.py ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from study_app.core.topic_identity import TOPIC_IDENTITY_VERSION, topic_key_for_id  # noqa: E402
from study_app.data.database import (  # noqa: E402
    KNOWLEDGE_ALERTS_TABLE_SQL,
    KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
    KNOWLEDGE_ALERT_INDEXES_SQL,
    KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL,
    KNOWLEDGE_PREREQUISITES_TABLE_SQL,
    KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL,
)

F2_TABLES = (
    "knowledge_topic_registry",
    "knowledge_prerequisites",
    "knowledge_alerts",
    "knowledge_alert_events",
)
FIXED_SQL = (
    KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL,
    KNOWLEDGE_PREREQUISITES_TABLE_SQL,
    KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL,
    KNOWLEDGE_ALERTS_TABLE_SQL,
    KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
    KNOWLEDGE_ALERT_INDEXES_SQL,
)
EXPECTED_ADDED_OBJECTS = {
    "table:knowledge_topic_registry",
    "table:knowledge_prerequisites",
    "table:knowledge_alerts",
    "table:knowledge_alert_events",
    "index:idx_knowledge_prerequisites_reverse",
    "index:idx_knowledge_alerts_status_due",
    "index:idx_knowledge_alert_events_alert",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def fk_fingerprint(connection: sqlite3.Connection) -> list[list[str]]:
    rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    return sorted([str(value) for value in row] for row in rows)


def schema_objects(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {f"{row[0]}:{row[1]}": (row[2] or "") for row in rows}


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    counts = {}
    for name in sorted(names):
        counts[name] = int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    return counts


def topic_rows(connection: sqlite3.Connection) -> list[int]:
    return [
        int(row[0])
        for row in connection.execute(
            """
            SELECT topics.id AS topic_id
            FROM topics
            JOIN modules ON modules.id = topics.module_id
            JOIN subjects ON subjects.id = modules.subject_id
            ORDER BY topics.id
            """
        )
    ]


def preflight(db_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        present = {
            name: bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone()
            )
            for name in F2_TABLES
        }
        registry_rows = 0
        if present["knowledge_topic_registry"]:
            registry_rows = int(
                connection.execute("SELECT COUNT(*) FROM knowledge_topic_registry").fetchone()[0]
            )
        payload = {
            "db": str(db_path),
            "db_sha256": sha256_file(db_path),
            "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "f2_tables_present": present,
            "topic_count": len(topic_rows(connection)),
            "registry_rows": registry_rows,
            "fk_violations": fk_fingerprint(connection),
            "counts": table_counts(connection),
        }
        payload["all_f2_missing"] = not any(present.values())
        payload["all_f2_present"] = all(present.values())
        return payload
    finally:
        connection.close()


def apply_migration(db_path: Path) -> dict:
    """Install the four tables + identity mapping in ONE transaction."""
    connection = sqlite3.connect(str(db_path), timeout=60)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        before_objects = schema_objects(connection)
        before_fks = fk_fingerprint(connection)
        before_counts = table_counts(connection)
        topics_before = len(topic_rows(connection))
        for name in F2_TABLES:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone():
                raise RuntimeError(f"守卫失败：{name} 已存在，拒绝重复迁移")
        for statement in FIXED_SQL:
            for part in statement.split(";"):
                part = part.strip()
                if part:
                    connection.execute(part)
        # Identity mapping inside the same transaction (mirrors the
        # database.register_topic_identities generation loop).
        existing_keys: set[str] = set()
        pending: list[tuple[str, int, str]] = []
        for topic_id in topic_rows(connection):
            generation = 0
            key = topic_key_for_id(topic_id, generation)
            while key in existing_keys:
                generation += 1
                key = topic_key_for_id(topic_id, generation)
            existing_keys.add(key)
            pending.append((key, topic_id, TOPIC_IDENTITY_VERSION))
        connection.executemany(
            "INSERT INTO knowledge_topic_registry(topic_key, topic_id, identity_version) VALUES (?, ?, ?)",
            pending,
        )
        registry_count = int(
            connection.execute("SELECT COUNT(*) FROM knowledge_topic_registry").fetchone()[0]
        )
        if registry_count != topics_before:
            raise RuntimeError(
                f"身份映射数量不符：registry={registry_count}, topics={topics_before}"
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"事务内 integrity_check 失败：{integrity}")
        after_fks = fk_fingerprint(connection)
        if after_fks != before_fks:
            raise RuntimeError("外键异常集合在事务内发生变化，回滚")
        after_objects = schema_objects(connection)
        added = {key: after_objects[key] for key in after_objects.keys() - before_objects.keys()}
        unexpected = set(added) - EXPECTED_ADDED_OBJECTS
        if unexpected:
            raise RuntimeError(f"出现预期之外的结构对象：{sorted(unexpected)}")
        missing = EXPECTED_ADDED_OBJECTS - set(added)
        if missing:
            raise RuntimeError(f"缺少预期结构对象：{sorted(missing)}")
        after_counts = table_counts(connection)
        for name, count in before_counts.items():
            if after_counts.get(name) != count:
                raise RuntimeError(f"既有表 {name} 行数变化：{count} → {after_counts.get(name)}")
        connection.commit()
        return {
            "committed": True,
            "registry_rows": registry_count,
            "added_objects": sorted(added),
            "fk_violations": after_fks,
            "counts": after_counts,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify(db_path: Path, baseline_fks: list[list[str]] | None) -> dict:
    from study_app.data.database import (
        get_topic_identity,
        list_knowledge_alert_events,
        list_knowledge_alerts,
        list_topic_identities,
    )

    report = preflight(db_path)
    report["verified_all_present"] = report["all_f2_present"]
    readback_ok = True
    try:
        identities = list_topic_identities(db_path)
        readback_ok = readback_ok and len(identities) == report["topic_count"]
        if identities:
            sample = get_topic_identity(identities[0].topic_key, db_path)
            readback_ok = readback_ok and sample is not None
        alerts = list_knowledge_alerts(db_path=db_path)
        events = list_knowledge_alert_events(db_path=db_path)
        report["alerts_rows"] = len(alerts)
        report["alert_events_rows"] = len(events)
    except Exception as error:  # noqa: BLE001
        readback_ok = False
        report["readback_error"] = f"{type(error).__name__}: {error}"
    report["readback_ok"] = readback_ok
    if baseline_fks is not None:
        current = [list(item) for item in report["fk_violations"]]
        report["fk_baseline_unchanged"] = current == [list(item) for item in baseline_fks]
    report["ok"] = bool(
        report["verified_all_present"]
        and report["integrity_check"] == "ok"
        and report["readback_ok"]
        and report.get("fk_baseline_unchanged", True)
        and report["registry_rows"] == report["topic_count"]
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="CR-F2-01 controlled F2 migration")
    parser.add_argument("--check", metavar="DB")
    parser.add_argument("--rehearse", nargs=2, metavar=("DB", "WORKDIR"))
    parser.add_argument("--apply", metavar="DB")
    parser.add_argument("--verify", nargs=2, metavar=("DB", "BASELINE_JSON"))
    parser.add_argument("--baseline", nargs=2, metavar=("DB", "OUT_JSON"))
    parser.add_argument(
        "--evidence",
        metavar="DIR",
        default=str(ROOT / "GLM53F_assistant_evidence" / "migrations"),
    )
    args = parser.parse_args()
    evidence_dir = Path(args.evidence)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.check:
        report = preflight(Path(args.check))
        out = evidence_dir / f"cr_f2_01_check_{stamp}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            json.dumps(
                {
                    "db_sha256": report["db_sha256"],
                    "integrity_check": report["integrity_check"],
                    "all_f2_missing": report["all_f2_missing"],
                    "all_f2_present": report["all_f2_present"],
                    "topic_count": report["topic_count"],
                    "fk_violations_count": len(report["fk_violations"]),
                    "evidence": str(out),
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0

    if args.baseline:
        db_path, out_path = Path(args.baseline[0]), Path(args.baseline[1])
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            payload = {"captured_at": stamp, "fk_violations": fk_fingerprint(connection)}
        finally:
            connection.close()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"baseline written: {out_path} ({len(payload['fk_violations'])} violations)")
        return 0

    if args.rehearse:
        source, workdir = Path(args.rehearse[0]), Path(args.rehearse[1])
        workdir.mkdir(parents=True, exist_ok=True)
        replica = workdir / "rehearsal_copy.sqlite"
        if replica.exists():
            replica.unlink()
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        replica_connection = sqlite3.connect(str(replica))
        try:
            source_connection.backup(replica_connection)
        finally:
            source_connection.close()
            replica_connection.close()
        baseline = preflight(source)
        result = apply_migration(replica)
        final = verify(replica, baseline["fk_violations"])
        payload = {
            "source": str(source),
            "source_sha256": baseline["db_sha256"],
            "replica": str(replica),
            "before": {
                "topic_count": baseline["topic_count"],
                "fk_violations": baseline["fk_violations"],
            },
            "migration": result,
            "after": {
                "integrity_check": final["integrity_check"],
                "registry_rows": final["registry_rows"],
                "readback_ok": final["readback_ok"],
                "fk_baseline_unchanged": final.get("fk_baseline_unchanged"),
                "ok": final["ok"],
            },
        }
        out = evidence_dir / f"cr_f2_01_rehearsal_{stamp}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            json.dumps(
                {**payload["migration"], "ok": final["ok"], "evidence": str(out)},
                ensure_ascii=False,
                indent=1,
                default=str,
            )
        )
        return 0 if final["ok"] else 2

    if args.apply:
        db_path = Path(args.apply)
        guard = preflight(db_path)
        if not guard["all_f2_missing"]:
            print("守卫失败：目标库并非『四表全缺』状态，拒绝执行。", file=sys.stderr)
            return 3
        if guard["integrity_check"] != "ok":
            print("守卫失败：integrity_check 未通过。", file=sys.stderr)
            return 3
        baseline_path = evidence_dir / f"cr_f2_01_pre_apply_baseline_{stamp}.json"
        baseline_path.write_text(
            json.dumps(guard, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        result = apply_migration(db_path)
        final = verify(db_path, guard["fk_violations"])
        payload = {
            "db": str(db_path),
            "pre_sha256": guard["db_sha256"],
            "post_sha256": sha256_file(db_path),
            "migration": result,
            "verification": {
                key: final.get(key)
                for key in (
                    "integrity_check",
                    "registry_rows",
                    "topic_count",
                    "readback_ok",
                    "fk_baseline_unchanged",
                    "ok",
                    "alerts_rows",
                    "alert_events_rows",
                )
            },
            "applied_at": stamp,
        }
        out = evidence_dir / f"cr_f2_01_apply_receipt_{stamp}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        return 0 if final["ok"] else 2

    if args.verify:
        db_path, baseline_path = Path(args.verify[0]), Path(args.verify[1])
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        fks = baseline.get("fk_violations") or baseline.get("before", {}).get("fk_violations")
        report = verify(db_path, fks)
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0 if report["ok"] else 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
