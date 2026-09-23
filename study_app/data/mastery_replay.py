"""Versioned learning evidence and deterministic mastery replay (MODEL-01)."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from study_app.core.mastery_trace import (
    TRACE_VERSION, append_mastery_trace, freeze_replay_baseline,
    record_fingerprint,
)
from study_app.data import database as db
from study_app.data import model_progress_sync as sync
from study_app.data.text_integrity import validate_text_integrity
from study_app.core.study_phase import phase_setting_key


REPLAY_VERSION = "mastery-evidence-replay-v1"


class ReplayProvenanceError(ValueError):
    """A contribution has no frozen replay start or its source is unverifiable."""


def _require_revision_schema(connection: Any) -> None:
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='learning_record_revisions'"
    ).fetchone() is None:
        raise db.DatabaseNotInitializedError(
            "D-02 修订历史表未初始化；禁止对现用库隐式迁移"
        )


def _normalized_revision(record: dict[str, Any], record_id: int) -> dict[str, Any]:
    validate_text_integrity(record, context="学习记录修订")
    db.validate_calendar_date(record.get("date"))
    normalized_problems = None
    if record.get("problems") is not None:
        if not isinstance(record["problems"], list):
            raise ValueError("record.problems 必须为列表")
        normalized_problems = []
        for problem in record["problems"]:
            if not isinstance(problem, dict):
                raise ValueError("record.problems 元素必须为对象")
            db._validate_problem_difficulty_fields(problem)
            normalized_problems.append(db._with_inferred_problem_data(record, problem))
    result = {
        **record,
        "id": record_id,
        "date": record["date"],
        "subject": record.get("subject"),
        "module": record.get("module") or None,
        "topic": record.get("topic") or None,
        "activity": record.get("activity") or "review",
        "source": record.get("source") or "outside_class",
        "score": db._optional_float(record.get("score")),
        "duration_minutes": db._optional_float(record.get("duration_minutes")),
        "note": record.get("note") or "",
        "_normalization_version": db.NORMALIZATION_VERSION,
    }
    if not result["subject"]:
        raise ValueError("record.subject is required")
    if result["score"] is not None and not 0 <= result["score"] <= 100:
        raise ValueError("record.score 必须在 0-100 范围内")
    if result["duration_minutes"] is not None and result["duration_minutes"] <= 0:
        raise ValueError("record.duration_minutes 必须大于 0")
    if normalized_problems is not None:
        result["problems"] = normalized_problems
    return result


def _topic_source(topic: dict[str, Any]) -> dict[str, Any]:
    source = topic.get("source_json")
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError:
            source = None
    return source if isinstance(source, dict) else {}


def _freeze_new_mapping(
    model: dict[str, Any], before: dict[str, Any], after: dict[str, Any],
    *, db_path: Path, record_id: int,
) -> None:
    """A revised mapping gets its own current, explicitly unverified start."""
    for subject in model.get("subjects", []):
        if subject.get("name") not in {before.get("subject"), after.get("subject")}:
            continue
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                previous = sync.topic_has_learning_evidence(
                    topic, sync.record_learning_text(before)
                ) if before.get("subject") == subject.get("name") else False
                incoming = sync.topic_has_learning_evidence(
                    topic, sync.record_learning_text(after)
                ) if after.get("subject") == subject.get("name") else False
                if not (previous or incoming):
                    continue
                source = _topic_source(topic)
                if source.get("mastery_contribution_trace_v1") and not isinstance(
                    source.get("mastery_replay_baseline_v1"), dict
                ):
                    raise ReplayProvenanceError(
                        f"知识点 {topic.get('name')} 有贡献但缺少冻结起点"
                    )
                if not isinstance(source.get("mastery_replay_baseline_v1"), dict):
                    if "mastery_replay_baseline_v1" not in module:
                        module["mastery_replay_baseline_v1"] = {
                            "status": module.get("status"),
                            "prior_provenance": "unverified",
                        }
                    freeze_replay_baseline(
                        topic, copy.deepcopy(topic),
                        record_id=record_id, db_path=db_path,
                    )
                elif incoming:
                    baseline = source["mastery_replay_baseline_v1"]
                    excluded = list(baseline.get("excluded_prior_record_ids", []))
                    if record_id in excluded:
                        if any(
                            isinstance(event, dict) and event.get("record_id") == record_id
                            for event in source.get("mastery_contribution_trace_v1") or []
                        ):
                            raise ReplayProvenanceError(
                                "记录同时处于冻结前排除集和已贡献链，来源冲突"
                            )
                        baseline["excluded_prior_record_ids"] = [
                            item for item in excluded if item != record_id
                        ]
                        topic["source_json"] = source


def _require_record_traced(model: dict[str, Any], record_id: int) -> None:
    for subject in model.get("subjects", []):
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                for event in _topic_source(topic).get("mastery_contribution_trace_v1") or []:
                    if isinstance(event, dict) and event.get("record_id") == record_id:
                        return
    raise ReplayProvenanceError(
        f"记录 {record_id} 无 trace v1 贡献；旧历史起点不可验证，拒绝重算"
    )


def _contributions_for_record(model: dict[str, Any], record_id: int) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(event)
        for subject in model.get("subjects", [])
        for module in subject.get("modules", [])
        for topic in module.get("topics", [])
        for event in _topic_source(topic).get("mastery_contribution_trace_v1") or []
        if isinstance(event, dict) and event.get("record_id") == record_id
    ]


def _validate_event_sources(model: dict[str, Any], db_path: Path) -> None:
    expected: set[tuple[int, str]] = set()
    for subject in model.get("subjects", []):
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                for event in _topic_source(topic).get("mastery_contribution_trace_v1") or []:
                    if isinstance(event, dict) and isinstance(event.get("record_id"), int):
                        expected.add((event["record_id"], event.get("record_fingerprint")))
    if not expected:
        return
    found: set[tuple[int, str]] = set()
    current: dict[int, str] = {}
    latest: dict[int, tuple[int, str]] = {}
    with db.connect_readonly(db_path) as connection:
        for row in connection.execute("SELECT id, raw_json FROM learning_records"):
            try:
                raw = json.loads(row["raw_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                raw["id"] = int(row["id"])
                fingerprint = record_fingerprint(raw)
                found.add((int(row["id"]), fingerprint))
                current[int(row["id"])] = fingerprint
        for row in connection.execute(
            "SELECT record_id, version, raw_json, payload_sha256 "
            "FROM learning_record_revisions"
        ):
            try:
                raw = json.loads(row["raw_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                raw["id"] = int(row["record_id"])
                fingerprint = record_fingerprint(raw)
                if fingerprint != row["payload_sha256"]:
                    raise ReplayProvenanceError(
                        f"记录 {row['record_id']} 的修订历史摘要不匹配"
                    )
                found.add((int(row["record_id"]), fingerprint))
                old = latest.get(int(row["record_id"]))
                if old is None or int(row["version"]) > old[0]:
                    latest[int(row["record_id"])] = (
                        int(row["version"]), fingerprint,
                    )
    for record_id, fingerprint in current.items():
        version = latest.get(record_id)
        if version is not None and fingerprint != version[1]:
            raise ReplayProvenanceError(
                f"记录 {record_id} 的现用内容与最新修订版本不一致"
            )
        if version is None and any(item[0] == record_id for item in expected) and (
            record_id, fingerprint
        ) not in expected:
            raise ReplayProvenanceError(
                f"记录 {record_id} 的现用内容未登记修订"
            )
    if not expected.issubset(found):
        raise ReplayProvenanceError("贡献事件的原始记录版本无法在数据库中验证")


def _replay_model(
    model: dict[str, Any], records: list[dict[str, Any]],
    *, db_path: Path,
) -> dict[str, list[tuple[str, str]]]:
    changed: dict[str, list[tuple[str, str]]] = {}
    for subject in model.get("subjects", []):
        subject_name = subject.get("name")
        phase = db.get_setting(phase_setting_key(subject_name), {}, db_path)
        for module in subject.get("modules", []):
            module_name = module.get("name")
            module_changed = False
            module_evidence_seen = False
            module_baseline = module.get("mastery_replay_baseline_v1")
            if isinstance(module_baseline, dict):
                module["status"] = module_baseline.get("status")
            for topic in module.get("topics", []):
                source = _topic_source(topic)
                baseline = source.get("mastery_replay_baseline_v1")
                if source.get("mastery_contribution_trace_v1") and not isinstance(baseline, dict):
                    raise ReplayProvenanceError(
                        f"知识点 {subject_name}/{module_name}/{topic.get('name')} "
                        "有 trace v1 贡献但没有稳定起点"
                    )
                if not isinstance(baseline, dict):
                    continue
                excluded = set(baseline.get("excluded_prior_record_ids", []))
                topic["mastery"] = baseline.get("mastery")
                topic["status"] = baseline.get("status")
                topic["forgetting_risk"] = baseline.get("forgetting_risk")
                topic["source_json"] = copy.deepcopy(baseline.get("source_json") or {})
                topic["source_json"]["mastery_replay_baseline_v1"] = copy.deepcopy(baseline)
                baseline_hash = hashlib.sha256(
                    json.dumps(baseline, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                for record in records:
                    if record.get("subject") != subject_name or record.get("id") in excluded:
                        continue
                    record_text = sync.record_learning_text(record)
                    diagnostic = sync.is_diagnostic_baseline_record(record)
                    in_scope = diagnostic and sync.diagnostic_baseline_topic_in_scope(
                        subject_name, str(module_name or ""),
                        str(topic.get("name") or ""),
                        str(topic.get("submodule") or ""),
                        phase=phase,
                    )
                    if not (in_scope or sync.module_has_explicit_evidence(module, record)
                            or sync.topic_has_learning_evidence(topic, record_text)):
                        continue
                    module_evidence_seen = True
                    if sync.STATUS_RANK.get(str(topic.get("status") or ""), 0) < sync.STATUS_RANK[sync.LEARNED_STATUS]:
                        topic["status"] = sync.LEARNED_STATUS
                    contribution = (
                        sync.apply_diagnostic_baseline_contribution(record, module, topic)
                        if in_scope else sync.apply_mastery_contribution(record, module, topic)
                    )
                    if not contribution:
                        continue
                    evidence_items = contribution.pop("_trace_evidence_items", [])
                    append_mastery_trace(topic, {
                        "trace_version": TRACE_VERSION,
                        "sync_algorithm_version": REPLAY_VERSION,
                        "model_version": REPLAY_VERSION,
                        "model_identity": model.get("model_name"),
                        "model_snapshot_sha256_before": baseline_hash,
                        "normalization_version": record.get("_normalization_version"),
                        "record_id": record.get("id"),
                        "record_token": f"id:{record.get('id')}",
                        "record_date": record.get("date"),
                        "record_fingerprint": record_fingerprint(record),
                        "subject": subject_name,
                        "module": module_name,
                        "topic": topic.get("name"),
                        "mode": contribution.get("mode", "mastery_update"),
                        "old_mastery": contribution.get("old_mastery"),
                        "new_mastery": contribution.get("new_mastery"),
                        "delta": contribution.get("delta"),
                        "result_correctness": contribution.get("correctness"),
                        "difficulty_score": contribution.get("difficulty_score"),
                        "evidence_items": evidence_items,
                    })
                module_changed = True
                changed.setdefault(str(subject_name), []).append(
                    (str(module_name), str(topic.get("name")))
                )
            if module_changed:
                if module_evidence_seen and sync.STATUS_RANK.get(
                    str(module.get("status") or ""), 0
                ) < sync.STATUS_RANK[sync.LEARNED_STATUS]:
                    module["status"] = sync.LEARNED_STATUS
                sync.recompute_module_mastery(module)
        if str(subject_name) in changed:
            sync.recompute_subject_mastery(subject)
    return changed


def _commit_replay(
    connection: Any, model: dict[str, Any], records: list[dict[str, Any]],
    *, db_path: Path, model_path: Path,
) -> tuple[dict[str, list[tuple[str, str]]], Path]:
    changed = _replay_model(model, records, db_path=db_path)
    for subject_name, topics in changed.items():
        sync.sync_topic_statuses_to_sqlite(
            subject_name, topics, model, db_path, connection=connection
        )
    model_json = json.dumps(model, ensure_ascii=False, indent=2) + "\n"
    temporary = model_path.with_name(f"{model_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(model_json, encoding="utf-8")
    db.set_setting(
        sync._pending_model_sync_key(model_path),
        {"model_path": str(model_path.resolve()), "model_json": model_json},
        db_path, connection=connection,
    )
    return changed, temporary


def _finish_replay(temporary: Path, model_path: Path, db_path: Path) -> None:
    try:
        temporary.replace(model_path)
        with db.connect(db_path) as connection:
            connection.execute(
                "DELETE FROM app_settings WHERE key = ?",
                (sync._pending_model_sync_key(model_path),),
            )
    finally:
        temporary.unlink(missing_ok=True)


def _perform(
    *, db_path: Path | str, model_path: Path | str,
    mutation: Callable[[Any, dict[str, Any]], Any] | None = None,
) -> tuple[Any, dict[str, list[tuple[str, str]]]]:
    database_path = db.require_initialized_database(db_path)
    path = Path(model_path)
    with sync._exclusive_model_sync(path):
        sync._recover_pending_model_sync(path, database_path)
        model = json.loads(path.read_text(encoding="utf-8"))
        with db.connect_readonly(database_path) as connection:
            _require_revision_schema(connection)
        _validate_event_sources(model, database_path)
        temporary = None
        try:
            with db.connect(database_path) as connection:
                _require_revision_schema(connection)
                result = mutation(connection, model) if mutation else None
                records = db.load_raw_records_from_connection(connection)
                changed, temporary = _commit_replay(
                    connection, model, records,
                    db_path=database_path, model_path=path,
                )
            _finish_replay(temporary, path, database_path)
            return result, changed
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise


def replay_mastery(*, db_path: Path | str, model_path: Path | str) -> dict[str, list[tuple[str, str]]]:
    """Recompute active evidence, using only each topic's frozen forward baseline."""
    return _perform(db_path=db_path, model_path=model_path)[1]


def _record_row(connection: Any, record_id: int) -> Any:
    row = connection.execute(
        "SELECT * FROM learning_records WHERE id = ?", (record_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"学习记录不存在：{record_id}")
    return row


def _append_version(
    connection: Any, record_id: int, action: str,
    raw: dict[str, Any], *, source_key: str | None = None,
    actor: str = "local_application",
    prior_contributions: list[dict[str, Any]] | None = None,
) -> None:
    last = connection.execute(
        "SELECT MAX(version) FROM learning_record_revisions WHERE record_id = ?",
        (record_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO learning_record_revisions(record_id, version, action, raw_json, "
        "payload_sha256, actor, prior_contributions_json, source_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record_id, int(last or 0) + 1, action, db.dumps(raw),
            record_fingerprint({**raw, "id": record_id}), actor,
            db.dumps(prior_contributions or []), source_key,
        ),
    )


def _replace_attempts(connection: Any, record_id: int, record: dict[str, Any]) -> None:
    connection.execute("DELETE FROM problem_attempts WHERE record_id = ?", (record_id,))
    for problem in record.get("problems") or []:
        connection.execute(
            "INSERT INTO problem_attempts(record_id, title, statement, status, "
            "correctness, difficulty_label, difficulty_score, error_cause, "
            "related_topics_json, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, db._problem_title(problem, record.get("topic") or "未命名题目"),
                db._problem_statement(problem), problem.get("status"),
                db._problem_correctness(problem), problem.get("difficulty"),
                problem.get("difficulty_score"), db._problem_error_cause(problem),
                db.dumps(problem.get("related_topics", [])), db.dumps(problem),
            ),
        )


def revise_learning_record(
    record_id: int, replacement: dict[str, Any], *,
    db_path: Path | str, model_path: Path | str,
    actor: str = "local_application",
) -> dict[str, list[tuple[str, str]]]:
    """Keep the record ID and append both its original and replacement versions."""
    normalized = _normalized_revision(replacement, record_id)

    def mutate(connection: Any, model: dict[str, Any]) -> None:
        row = _record_row(connection, record_id)
        original = json.loads(row["raw_json"])
        original["id"] = record_id
        if original.get("_revoked"):
            raise ValueError("已撤销记录不能隐式恢复")
        _require_record_traced(model, record_id)
        prior_attachments = original.get("attachments")
        incoming_attachments = normalized.get("attachments", prior_attachments)
        if incoming_attachments != prior_attachments:
            raise ValueError("D-02 修订不得隐式改动附件关系")
        if prior_attachments is not None:
            normalized["attachments"] = copy.deepcopy(prior_attachments)
        if record_fingerprint(original) == record_fingerprint(normalized):
            return
        _freeze_new_mapping(
            model, original, normalized,
            db_path=Path(db_path), record_id=record_id,
        )
        if connection.execute(
            "SELECT 1 FROM learning_record_revisions WHERE record_id = ?",
            (record_id,),
        ).fetchone() is None:
            _append_version(connection, record_id, "created", original, actor=actor)
        _append_version(
            connection, record_id, "revised", normalized, actor=actor,
            prior_contributions=_contributions_for_record(model, record_id),
        )
        connection.execute(
            "UPDATE learning_records SET record_date=?, subject_name=?, module_name=?, "
            "topic_name=?, activity=?, source=?, score=?, duration_minutes=?, note=?, "
            "raw_json=? WHERE id=?",
            (
                normalized["date"], normalized["subject"], normalized["module"],
                normalized["topic"], normalized["activity"], normalized["source"],
                normalized["score"], normalized["duration_minutes"], normalized["note"],
                db.dumps(normalized), record_id,
            ),
        )
        _replace_attempts(connection, record_id, normalized)

    return _perform(db_path=db_path, model_path=model_path, mutation=mutate)[1]


def revoke_learning_record(
    record_id: int, *, db_path: Path | str, model_path: Path | str,
    actor: str = "local_application",
) -> dict[str, list[tuple[str, str]]]:
    """Preserve version and attachments while removing active model evidence."""

    def mutate(connection: Any, model: dict[str, Any]) -> None:
        row = _record_row(connection, record_id)
        original = json.loads(row["raw_json"])
        original["id"] = record_id
        if original.get("_revoked"):
            return
        _require_record_traced(model, record_id)
        _freeze_new_mapping(
            model, original, original,
            db_path=Path(db_path), record_id=record_id,
        )
        if connection.execute(
            "SELECT 1 FROM learning_record_revisions WHERE record_id = ?",
            (record_id,),
        ).fetchone() is None:
            _append_version(connection, record_id, "created", original, actor=actor)
        revoked = {**original, "_revoked": True}
        _append_version(
            connection, record_id, "revoked", revoked, actor=actor,
            prior_contributions=_contributions_for_record(model, record_id),
        )
        connection.execute(
            "UPDATE learning_records SET raw_json = ? WHERE id = ?",
            (db.dumps(revoked), record_id),
        )

    return _perform(db_path=db_path, model_path=model_path, mutation=mutate)[1]


def import_learning_record_once(
    source_key: str, record: dict[str, Any], *,
    db_path: Path | str, model_path: Path | str,
) -> int:
    """An explicit external evidence key identifies one logical import."""
    if not isinstance(source_key, str) or not source_key.strip():
        raise ValueError("source_key 必须是非空稳定来源键")
    path = Path(model_path)
    database_path = db.require_initialized_database(db_path)
    with sync._exclusive_model_sync(path):
        with db.connect(database_path) as connection:
            _require_revision_schema(connection)
            row = connection.execute(
                "SELECT record_id FROM learning_record_revisions WHERE source_key = ?",
                (source_key,),
            ).fetchone()
            if row:
                return int(row[0])
            for row in connection.execute("SELECT id, raw_json FROM learning_records"):
                try:
                    existing = json.loads(row["raw_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if existing.get("_external_evidence_key") == source_key:
                    _append_version(
                        connection, int(row["id"]), "created", existing,
                        source_key=source_key, actor="external_import",
                    )
                    return int(row["id"])
        payload = {**record, "_external_evidence_key": source_key}
        record_id = db.add_learning_record(
            payload, db_path=database_path, model_path=path
        )
        with db.connect(database_path) as connection:
            _require_revision_schema(connection)
            stored = json.loads(_record_row(connection, record_id)["raw_json"])
            _append_version(
                connection, record_id, "created", stored,
                source_key=source_key, actor="external_import",
            )
        return record_id


def list_record_revision_history(
    record_id: int, *, db_path: Path | str,
) -> list[dict[str, Any]]:
    """Read-only record versions and the contributions each action superseded."""
    with db.connect_readonly(db_path) as connection:
        _require_revision_schema(connection)
        rows = connection.execute(
            "SELECT version, action, raw_json, payload_sha256, actor, "
            "prior_contributions_json, source_key, created_at "
            "FROM learning_record_revisions WHERE record_id=? ORDER BY version",
            (record_id,),
        ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["raw_json"])
        payload["id"] = record_id
        if record_fingerprint(payload) != row["payload_sha256"]:
            raise ReplayProvenanceError(
                f"记录 {record_id} 的历史版本 {row['version']} 摘要不匹配"
            )
        result.append({
            "version": row["version"], "action": row["action"],
            "record": payload, "payload_sha256": row["payload_sha256"],
            "actor": row["actor"], "source_key": row["source_key"],
            "created_at": row["created_at"],
            "prior_contributions": json.loads(row["prior_contributions_json"]),
        })
    return result
