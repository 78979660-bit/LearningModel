"""Engine-level coverage for the learning assistant orchestrator.

Every test runs against a temp database + temp model file; the real
``app_data`` store is never touched. Fixture stack: BKT-enabled warning
policy (insights hard-require it), F5 subject catalog, the four knowledge
alert tables, and the local ``mock`` advisor privacy mode.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from study_app.core.learning_assistant_engine import (
    ActionNotUndoableError,
    AdvisorRequiredError,
    LearningAssistantError,
    LearningAssistantEngine,
    StaleProposalError,
)
from study_app.core.learning_assistant_policy import save_privacy_settings
from study_app.data import database
from study_app.data.mastery_replay import list_record_revision_history
from study_app.data.subject_repository import SubjectCatalogRepository

SUBMIT_TEXT = "测试学科 基础主题 作业2题全对，2026-10-01 做完了"
PROGRESS_TEXT = "测试学科 基础主题 复习了"
HOMEWORK_TEXT = (
    "第1题 基础主题：证明基本极限存在并求极限值\n"
    "解：过程完整，结论正确\n"
    "\n"
    "第2题 计算复合函数在基础主题下的极限值\n"
    "解：过程完整，结论正确\n"
)
BASELINE_MASTERY = 0.2


def _model() -> dict:
    return {
        "model_name": "la_engine_fixture_v1",
        "warning_policy": {
            "bkt_model": {
                "enabled": True,
                "base_guess": 0.22,
                "base_slip": 0.12,
                "base_learn": 0.08,
            }
        },
        "subjects": [
            {
                "name": "测试学科",
                "mastery": 0.3,
                "weight": 1.0,
                "status": "active",
                "modules": [
                    {
                        "name": "极限模块",
                        "weight": 1.0,
                        "mastery": 0.3,
                        "status": "active",
                        "topics": [
                            {
                                "name": "基础主题",
                                "mastery": BASELINE_MASTERY,
                                "difficulty": 0.6,
                                "status": "learning",
                                "importance": 1,
                                "forgetting_risk": 0.2,
                            }
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture()
def env(tmp_path):
    db_path = tmp_path / "la_engine.sqlite"
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(_model(), ensure_ascii=False), encoding="utf-8")
    database.initialize_database(db_path)
    with database.connect(db_path) as connection:
        database.import_model_json(connection, _model())
        for constant in (
            database.KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_TABLE_SQL,
            database.KNOWLEDGE_ALERTS_TABLE_SQL,
            database.KNOWLEDGE_ALERT_EVENTS_TABLE_SQL,
            database.KNOWLEDGE_PREREQUISITES_REVERSE_INDEX_SQL,
            database.KNOWLEDGE_ALERT_INDEXES_SQL,
        ):
            for statement in constant.split(";"):
                if statement.strip():
                    connection.execute(statement)
    database.register_topic_identities(db_path)
    # Unified model import backfills the F5 catalog.  Reuse that canonical
    # identity instead of creating a duplicate and weakening uniqueness.
    subject = SubjectCatalogRepository(db_path).resolve_subject("测试学科")
    assert subject.canonical_name == "测试学科"
    save_privacy_settings(
        {"advisor_mode": "mock", "allow_external_intent": False}, db_path
    )
    engine = LearningAssistantEngine(db_path=db_path, model_path=model_path)
    return SimpleNamespace(
        engine=engine, db_path=db_path, model_path=model_path, tmp_path=tmp_path
    )


def _write_attachment(tmp_path: Path) -> Path:
    attachment = tmp_path / "homework.txt"
    attachment.write_text(HOMEWORK_TEXT, encoding="utf-8")
    return attachment


def _submit_bundle(env) -> SimpleNamespace:
    attachment = _write_attachment(env.tmp_path)
    bundle = env.engine.create_proposal(SUBMIT_TEXT, [str(attachment)])
    assert bundle.proposal.action_type == "submit_homework"
    return bundle


def _confirm(engine: LearningAssistantEngine, bundle):
    return engine.confirm_and_execute(
        bundle.proposal.action_id,
        confirmation_token=bundle.confirmation_token,
        execution_mode="user",
    )


def _model_topic_mastery(model_path: Path) -> float:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    topic = model["subjects"][0]["modules"][0]["topics"][0]
    assert topic["name"] == "基础主题"
    return float(topic["mastery"])


def _readonly_answer_state() -> SimpleNamespace:
    return SimpleNamespace(
        raw_records=(),
        memory_risks=(),
        bkt_alerts=(),
        subjects=(),
        todos=(),
        benchmark=60.0,
    )


def test_readonly_answer_leaves_counts_and_model_bytes_untouched(env):
    before_counts = database.get_counts(env.db_path)
    before_model = env.model_path.read_bytes()

    answer = env.engine.answer("哪里最薄弱？", _readonly_answer_state())
    assert answer["answer"]

    assert database.get_counts(env.db_path) == before_counts
    assert env.model_path.read_bytes() == before_model


def test_write_requires_matching_confirmation_token_and_never_journals_secret(env):
    bundle = _submit_bundle(env)
    assert bundle.confirmation_token
    entry = env.engine.load_action(bundle.proposal.action_id)
    assert entry["confirmation_token_sha256"]
    assert bundle.confirmation_token not in json.dumps(entry, ensure_ascii=False)

    with pytest.raises(LearningAssistantError, match="确认令牌"):
        env.engine.confirm_and_execute(
            bundle.proposal.action_id,
            confirmation_token=None,
        )
    with pytest.raises(LearningAssistantError, match="确认令牌"):
        env.engine.confirm_and_execute(
            bundle.proposal.action_id,
            confirmation_token="wrong-token",
        )
    assert database.get_counts(env.db_path)["learning_records"] == 0
    assert env.engine.load_action(bundle.proposal.action_id)["state"] == "awaiting_confirmation"


def test_auto_execution_rechecks_action_policy_inside_engine(env):
    bundle = _submit_bundle(env)
    with pytest.raises(LearningAssistantError, match="自动执行白名单"):
        env.engine.confirm_and_execute(
            bundle.proposal.action_id,
            confirmation_token=bundle.confirmation_token,
            execution_mode="auto",
        )
    assert database.get_counts(env.db_path)["learning_records"] == 0


def test_submit_homework_with_attachment_writes_record_and_mastery(env):
    bundle = _submit_bundle(env)
    assert len(bundle.proposal.attachments) == 1
    action_id = bundle.proposal.action_id

    receipt = _confirm(env.engine, bundle)
    assert receipt.status == "completed"
    assert receipt.record_id is not None
    assert receipt.mastery_changes
    assert receipt.alert_result is not None
    assert receipt.undo_token == {
        "kind": "record",
        "record_id": receipt.record_id,
    }
    assert _model_topic_mastery(env.model_path) > BASELINE_MASTERY

    with database.connect_readonly(env.db_path) as connection:
        row = connection.execute(
            "SELECT raw_json FROM learning_records WHERE id=?",
            (receipt.record_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT COUNT(*) FROM problem_attempts"
        ).fetchone()[0]
    assert row is not None
    raw = json.loads(row["raw_json"])
    assert len(raw["problems"]) == 2
    for problem in raw["problems"]:
        assert problem["status"] == "all_correct"
        assert problem["correctness"] == 1.0
        assert problem["independence"] == "unknown"
        assert problem["error_cause"] is None
    assert attempts == 2


def test_second_confirm_replays_same_record_without_new_writes(env):
    bundle = _submit_bundle(env)
    first = _confirm(env.engine, bundle)
    assert first.replayed is False

    counts_before = database.get_counts(env.db_path)
    model_before = env.model_path.read_bytes()

    second = _confirm(env.engine, bundle)
    assert second.replayed is True
    assert second.record_id == first.record_id
    assert second.status == "completed"
    assert database.get_counts(env.db_path) == counts_before
    assert env.model_path.read_bytes() == model_before


def test_unset_advisor_blocks_submit_until_mock_mode_enabled(env):
    save_privacy_settings(
        {"advisor_mode": "unset", "allow_external_intent": False}, env.db_path
    )
    bundle = _submit_bundle(env)
    action_id = bundle.proposal.action_id

    with pytest.raises(AdvisorRequiredError):
        _confirm(env.engine, bundle)

    with database.connect_readonly(env.db_path) as connection:
        records = connection.execute(
            "SELECT COUNT(*) FROM learning_records"
        ).fetchone()[0]
    assert records == 0
    entry = env.engine.load_action(action_id)
    assert entry is not None
    assert entry["state"] == "awaiting_confirmation"

    save_privacy_settings(
        {"advisor_mode": "mock", "allow_external_intent": False}, env.db_path
    )
    retry = _submit_bundle(env)
    receipt = _confirm(env.engine, retry)
    assert receipt.status == "completed"
    assert receipt.record_id is not None


def test_rejected_proposal_refuses_confirmation(env):
    bundle = _submit_bundle(env)
    action_id = bundle.proposal.action_id

    engine_result = env.engine.reject_proposal(action_id, "用户取消提案")
    assert engine_result["state"] == "rejected"

    with pytest.raises(LearningAssistantError):
        _confirm(env.engine, bundle)

    entry = env.engine.load_action(action_id)
    assert entry["state"] == "rejected"
    with database.connect_readonly(env.db_path) as connection:
        records = connection.execute(
            "SELECT COUNT(*) FROM learning_records"
        ).fetchone()[0]
    assert records == 0


def test_stale_version_token_moves_proposal_to_business_invalid(env):
    engine = env.engine
    engine._capture_version_token = lambda subject_key: {
        "catalog_revision": 5,
        "subject_key": subject_key,
        "object_version": 9,
    }
    bundle = _submit_bundle(env)
    action_id = bundle.proposal.action_id
    assert bundle.version_token["object_version"] == 9

    engine._capture_version_token = lambda subject_key: {
        "catalog_revision": 5,
        "subject_key": subject_key,
        "object_version": 10,
    }
    with pytest.raises(StaleProposalError):
        _confirm(engine, bundle)

    entry = engine.load_action(action_id)
    assert entry["state"] == "business_invalid"
    assert database.get_counts(env.db_path)["learning_records"] == 0


def test_concurrent_confirms_execute_exactly_once(env):
    engine = env.engine
    bundle = _submit_bundle(env)
    action_id = bundle.proposal.action_id

    results: list = []
    errors: list = []
    lock = threading.Lock()

    def worker():
        try:
            receipt = _confirm(engine, bundle)
        except Exception as error:  # noqa: BLE001 - concurrency boundary
            with lock:
                errors.append(error)
            return
        with lock:
            results.append(receipt)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sum(1 for receipt in results if not receipt.replayed) == 1
    assert sum(1 for receipt in results if receipt.replayed) + len(errors) == 7

    with database.connect_readonly(env.db_path) as connection:
        records = connection.execute(
            "SELECT COUNT(*) FROM learning_records"
        ).fetchone()[0]
        attempts = connection.execute(
            "SELECT COUNT(*) FROM problem_attempts"
        ).fetchone()[0]
    assert records == 1
    assert attempts == 2

    entry = engine.load_action(action_id)
    assert entry["state"] == "completed"


def test_undo_revokes_record_and_restores_baseline_mastery(env):
    bundle = _submit_bundle(env)
    receipt = _confirm(env.engine, bundle)
    record_id = receipt.record_id
    assert _model_topic_mastery(env.model_path) > BASELINE_MASTERY

    undoable = env.engine.last_undoable()
    assert undoable is not None
    assert undoable["undo_token"]["kind"] == "record"
    assert undoable["undo_token"]["record_id"] == record_id

    undo_receipt = env.engine.undo_last()
    assert undo_receipt.status == "completed"
    assert undo_receipt.record_id == record_id

    assert abs(_model_topic_mastery(env.model_path) - BASELINE_MASTERY) < 1e-6

    with database.connect_readonly(env.db_path) as connection:
        raw = json.loads(
            connection.execute(
                "SELECT raw_json FROM learning_records WHERE id=?",
                (record_id,),
            ).fetchone()["raw_json"]
        )
    assert raw["_revoked"] is True

    history = list_record_revision_history(record_id, db_path=env.db_path)
    assert [item["action"] for item in history] == ["created", "revoked"]

    original_entry = env.engine.load_action(bundle.proposal.action_id)
    assert original_entry["state"] == "undone"


def test_undo_refuses_progress_only_record_without_trace(env):
    bundle = env.engine.create_proposal(PROGRESS_TEXT, [])
    assert bundle.proposal.action_type == "update_learning_progress"
    receipt = _confirm(env.engine, bundle)
    assert receipt.status == "completed"
    assert receipt.record_id is not None

    undoable = env.engine.last_undoable()
    assert undoable is not None
    assert undoable["undo_token"]["record_id"] == receipt.record_id

    with pytest.raises(ActionNotUndoableError) as excinfo:
        env.engine.undo_last()
    assert "修订" in str(excinfo.value)


def test_journal_lists_entries_and_loads_action(env):
    bundle = _submit_bundle(env)
    receipt = _confirm(env.engine, bundle)

    journal = env.engine.load_journal()
    matches = [
        item for item in journal if item["action_id"] == bundle.proposal.action_id
    ]
    assert len(matches) == 1
    assert matches[0]["action_type"] == "submit_homework"
    assert matches[0]["status"] == "completed"

    entry = env.engine.load_action(bundle.proposal.action_id)
    assert entry is not None
    assert entry["action_id"] == bundle.proposal.action_id
    assert entry["state"] == "completed"
    assert entry["receipt"]["record_id"] == receipt.record_id


def test_nl_revise_end_to_end_marks_problem_wrong_and_keeps_attachments(env):
    bundle = _submit_bundle(env)
    receipt = _confirm(env.engine, bundle)
    record_id = receipt.record_id
    assert _model_topic_mastery(env.model_path) > BASELINE_MASTERY

    with database.connect_readonly(env.db_path) as connection:
        raw_before = json.loads(
            connection.execute(
                "SELECT raw_json FROM learning_records WHERE id=?",
                (record_id,),
            ).fetchone()["raw_json"]
        )

    revise_bundle = env.engine.create_proposal(
        f"更正记录{record_id}：第一题答错，错因是计算失误"
    )
    assert revise_bundle.proposal.action_type == "revise_learning_record"
    change = revise_bundle.proposal.proposed_changes[0]
    assert change["target_id"] == record_id
    assert change["problems"] == [
        {
            "index": 1,
            "title_hint": None,
            "set": {
                "status": "wrong",
                "correctness": 0.0,
                "error_cause": "计算失误",
            },
        }
    ]

    revise_receipt = _confirm(env.engine, revise_bundle)
    assert revise_receipt.status == "completed"
    assert revise_receipt.record_id == record_id
    assert "追加式修订" in revise_receipt.message
    assert "历史" in revise_receipt.message

    with database.connect_readonly(env.db_path) as connection:
        raw_after = json.loads(
            connection.execute(
                "SELECT raw_json FROM learning_records WHERE id=?",
                (record_id,),
            ).fetchone()["raw_json"]
        )
    problems = raw_after["problems"]
    assert len(problems) == 2
    assert problems[0]["status"] == "wrong"
    assert problems[0]["correctness"] == 0.0
    assert problems[0]["error_cause"] == "计算失误"
    # 第二题未被本次修订触碰。
    assert problems[1]["status"] == "all_correct"
    assert problems[1]["correctness"] == 1.0
    # D-02：附件关系绝不因修订而变化。
    assert raw_after.get("attachments") == raw_before.get("attachments")

    history = list_record_revision_history(record_id, db_path=env.db_path)
    assert [item["action"] for item in history] == ["created", "revised"]

    # 掌握度变化写进回执：第一题改错后该知识点掌握度应下降。
    assert revise_receipt.mastery_changes
    assert any(
        float(item["mastery_after"]) < float(item["mastery_before"])
        for item in revise_receipt.mastery_changes
    )
