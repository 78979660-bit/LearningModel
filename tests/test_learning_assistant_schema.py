"""learning_assistant_schema（learning-assistant-action-v1）契约测试。

覆盖：最小提案往返、必填字段缺失、未知顶层键、schema_version 不匹配、
未知动作类型白名单、只读动作不得要求确认、confidence 数值域、
subject_key 稳定身份形式、inferences/attachments/proposed_changes 条目校验、
permission_class 十种动作的完整映射。
"""
from __future__ import annotations

import math

import pytest

from study_app.core.learning_assistant_schema import (
    ACTION_TYPES,
    PROPOSAL_MODES,
    ProposalSchemaError,
    READONLY_ACTIONS,
    SCHEMA_VERSION,
    default_disclosure,
    new_action_id,
    permission_class,
    proposal_from_dict,
    validate_proposal_dict,
)

SUBJECT_KEY = "subject:v1:" + "a" * 32


def _minimal_proposal(**overrides):
    base = {
        "schema_version": SCHEMA_VERSION,
        "action_id": "la-" + "1" * 32,
        "action_type": "submit_homework",
        "mode": "preview",
        "requires_confirmation": True,
    }
    base.update(overrides)
    return base


def test_valid_minimal_proposal_round_trips():
    raw = _minimal_proposal()
    normalized = validate_proposal_dict(raw)
    proposal = proposal_from_dict(raw)
    assert proposal.to_dict() == normalized
    # 缺省字段按 schema 填充默认值
    assert normalized["user_facts"] == {}
    assert normalized["inferences"] == []
    assert normalized["attachments"] == []
    assert normalized["proposed_changes"] == []
    assert normalized["confidence"] == 0.0
    assert normalized["subject_key"] is None
    assert normalized["external_data_disclosure"] == {}
    assert normalized["warnings"] == []


def test_full_proposal_round_trips_without_loss():
    raw = _minimal_proposal(
        action_type="submit_homework",
        subject_key=SUBJECT_KEY,
        user_facts={"correctness_claim": "all_correct", "independence": "independent"},
        inferences=[{"field": "count", "value": 2, "basis": "附件解析"}],
        attachments=[
            {
                "path": "C:/tmp/homework.txt",
                "file_name": "homework.txt",
                "sha256": "ab" * 32,
                "kind": "txt",
                "preflight_status": "ready",
            }
        ],
        proposed_changes=[
            {
                "entity": "learning_record",
                "op": "insert",
                "summary": "登记作业记录（全对）",
                "fields": {"problems": 2},
                "target_id": 12,
            }
        ],
        confidence=0.9,
        requires_confirmation=True,
        external_data_disclosure=default_disclosure(advisor_required=True),
        warnings=["未指明日期，默认今天"],
    )
    normalized = validate_proposal_dict(raw)
    proposal = proposal_from_dict(raw)
    assert proposal.to_dict() == normalized
    assert proposal.user_facts["correctness_claim"] == "all_correct"
    assert proposal.attachments[0]["sha256"] == "ab" * 32
    assert proposal.proposed_changes[0]["target_id"] == 12


@pytest.mark.parametrize(
    "field",
    ["action_id", "action_type", "mode", "requires_confirmation", "schema_version"],
)
def test_missing_required_field_is_rejected(field):
    raw = _minimal_proposal()
    raw.pop(field)
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "缺少必填字段" in str(excinfo.value)
    assert field in str(excinfo.value)


def test_unknown_top_level_key_is_rejected():
    raw = _minimal_proposal(surprise_field={"nested": True})
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "未定义的顶层字段" in str(excinfo.value)
    assert "surprise_field" in str(excinfo.value)


@pytest.mark.parametrize(
    "bad_version", ["learning-assistant-action-v2", "v1", "", 1, None, ["list"]]
)
def test_schema_version_mismatch_is_rejected(bad_version):
    raw = _minimal_proposal(schema_version=bad_version)
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "schema_version" in str(excinfo.value)
    assert SCHEMA_VERSION in str(excinfo.value)


@pytest.mark.parametrize(
    "bad_action",
    ["delete_all_records", "submit_homework ", "SUBMIT_HOMEWORK", "", None, 3],
)
def test_unknown_action_type_is_rejected(bad_action):
    raw = _minimal_proposal(action_type=bad_action)
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "未知动作类型" in str(excinfo.value)
    assert "白名单" in str(excinfo.value)


def test_invalid_mode_is_rejected():
    raw = _minimal_proposal(mode="executed")
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert str(PROPOSAL_MODES) in str(excinfo.value)


@pytest.mark.parametrize("action_type", sorted(READONLY_ACTIONS))
def test_readonly_action_must_not_require_confirmation(action_type):
    raw = _minimal_proposal(action_type=action_type, requires_confirmation=True)
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "只读动作不得要求确认" in str(excinfo.value)
    # 只读动作 requires_confirmation=False 合法
    ok = _minimal_proposal(action_type=action_type, requires_confirmation=False)
    assert validate_proposal_dict(ok)["requires_confirmation"] is False


@pytest.mark.parametrize(
    "action_type",
    sorted(set(ACTION_TYPES) - set(READONLY_ACTIONS)),
)
def test_every_non_readonly_action_must_require_confirmation(action_type):
    raw = _minimal_proposal(
        action_type=action_type,
        requires_confirmation=False,
        proposed_changes=[],
    )
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "所有非只读动作必须要求确认" in str(excinfo.value)


def test_action_cannot_smuggle_unrelated_backend_operation():
    raw = _minimal_proposal(
        action_type="submit_homework",
        proposed_changes=[
            {
                "entity": "knowledge_alert",
                "op": "handle",
                "summary": "借登记作业修改预警",
                "fields": {},
            }
        ],
    )
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "不属于动作" in str(excinfo.value)
    assert "白名单" in str(excinfo.value)


@pytest.mark.parametrize(
    "bad_confidence",
    [
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        1.5,
        -0.1,
        2,
        "0.9",
        None,
    ],
)
def test_invalid_confidence_is_rejected(bad_confidence):
    raw = _minimal_proposal(confidence=bad_confidence)
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "confidence" in str(excinfo.value)


@pytest.mark.parametrize("good_confidence", [0.0, 0.9, 1.0, 0, 1])
def test_valid_confidence_is_normalized_to_float(good_confidence):
    raw = _minimal_proposal(confidence=good_confidence)
    normalized = validate_proposal_dict(raw)
    assert normalized["confidence"] == float(good_confidence)
    assert isinstance(normalized["confidence"], float)


@pytest.mark.parametrize(
    "bad_subject_key", ["数学", "subject:", "subject:v2:" + "a" * 32, 123, ""]
)
def test_invalid_subject_key_is_rejected(bad_subject_key):
    raw = _minimal_proposal(subject_key=bad_subject_key)
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(raw)
    assert "subject_key" in str(excinfo.value)


@pytest.mark.parametrize(
    "good_subject_key", [None, SUBJECT_KEY, "subject:v1:short"]
)
def test_valid_subject_key_passes(good_subject_key):
    raw = _minimal_proposal(subject_key=good_subject_key)
    assert validate_proposal_dict(raw)["subject_key"] == good_subject_key


def test_inference_entry_validation():
    # 缺 field / 空 field
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(inferences=[{"value": 1}]))
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(inferences=[{"field": "", "value": 1}]))
    # 缺 value
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(inferences=[{"field": "count"}]))
    # 未知键
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(
            _minimal_proposal(inferences=[{"field": "count", "value": 1, "extra": 2}])
        )
    assert "未定义字段" in str(excinfo.value)
    # basis 必须是字符串或省略
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(
            _minimal_proposal(inferences=[{"field": "count", "value": 1, "basis": 7}])
        )
    # 合法条目
    ok = validate_proposal_dict(
        _minimal_proposal(
            inferences=[{"field": "count", "value": 2, "basis": "附件解析"}]
        )
    )
    assert ok["inferences"] == [{"field": "count", "value": 2, "basis": "附件解析"}]
    # inferences 必须是数组
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(inferences={"field": "x"}))


def test_attachment_entry_validation():
    # 缺 path
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(attachments=[{"file_name": "a.txt"}]))
    # path 空白
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(attachments=[{"path": "   "}]))
    # 可选键类型非法
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(
            _minimal_proposal(attachments=[{"path": "a.txt", "sha256": ["ff"] * 32}])
        )
    # 合法条目
    ok = validate_proposal_dict(
        _minimal_proposal(
            attachments=[
                {
                    "path": "C:/tmp/a.txt",
                    "file_name": "a.txt",
                    "sha256": "ab" * 32,
                    "kind": "txt",
                    "preflight_status": "ready",
                }
            ]
        )
    )
    assert ok["attachments"][0]["kind"] == "txt"


def test_proposed_change_entry_validation():
    entry = {"entity": "learning_record", "op": "insert", "summary": "登记作业"}
    for missing in ("entity", "op", "summary"):
        broken = {key: value for key, value in entry.items() if key != missing}
        with pytest.raises(ProposalSchemaError) as excinfo:
            validate_proposal_dict(_minimal_proposal(proposed_changes=[broken]))
        assert missing in str(excinfo.value)
    # fields 必须是对象
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(
            _minimal_proposal(
                proposed_changes=[dict(entry, fields=["not", "a", "map"])]
            )
        )
    # 未知键
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(
            _minimal_proposal(proposed_changes=[dict(entry, mystery=1)])
        )
    assert "未定义字段" in str(excinfo.value)
    # 合法条目（fields + target_id）
    ok = validate_proposal_dict(
        _minimal_proposal(
            proposed_changes=[
                dict(entry, fields={"problems": 2}, target_id=7)
            ]
        )
    )
    assert ok["proposed_changes"][0]["fields"] == {"problems": 2}


def test_container_types_are_checked():
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(user_facts=["not", "a", "map"]))
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(external_data_disclosure=[]))
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(warnings="不是数组"))
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(warnings=[1, 2]))
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict(_minimal_proposal(requires_confirmation="yes"))
    with pytest.raises(ProposalSchemaError):
        validate_proposal_dict("not even a mapping")


def test_invalid_action_id_is_rejected():
    for bad in ("short", "带空格 id123456", "la-" + "x" * 100, 12345, None):
        raw = _minimal_proposal(action_id=bad)
        with pytest.raises(ProposalSchemaError) as excinfo:
            validate_proposal_dict(raw)
        assert "action_id" in str(excinfo.value)


def test_new_action_id_is_stable_format_and_unique():
    first = new_action_id()
    second = new_action_id()
    assert first != second
    validate_action_id_ok = validate_proposal_dict(_minimal_proposal(action_id=first))
    assert validate_action_id_ok["action_id"] == first
    assert first.startswith("la-")
    assert 8 <= len(first) <= 64


def test_permission_class_covers_all_ten_actions():
    expected = {
        "answer_query": "readonly",
        "preview_mastery_change": "readonly",
        "explain_alert": "readonly",
        "undo_last_action": "explicit",
        "recompute_alerts": "auto_configurable",
        "submit_homework": "confirm",
        "update_learning_progress": "confirm",
        "revise_learning_record": "confirm",
        "handle_alert": "confirm",
        "snooze_alert": "confirm",
    }
    assert len(ACTION_TYPES) == 10
    assert set(expected) == set(ACTION_TYPES)
    for action_type, klass in expected.items():
        assert permission_class(action_type) == klass, action_type
    # 权限类别与动作分组一致：只读动作永远可以 requires_confirmation=False
    for action_type in READONLY_ACTIONS:
        assert permission_class(action_type) == "readonly"


def test_nan_confidence_error_message_names_the_field():
    with pytest.raises(ProposalSchemaError) as excinfo:
        validate_proposal_dict(_minimal_proposal(confidence=math.nan))
    assert "confidence" in str(excinfo.value)
