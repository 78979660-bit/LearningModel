"""Pure-function coverage for the learning-assistant intent routing table.

No database, no filesystem, no network: ``parse_intent`` and
``build_alert_action`` are deterministic routing functions. A shim subject
resolver stands in for the F5 catalog; every routed proposal must survive the
frozen action-schema round trip (``proposal_from_dict(proposal.to_dict())``).
"""
from __future__ import annotations

import datetime
import types

import pytest

from study_app.core.learning_assistant_intent import build_alert_action, parse_intent
from study_app.core.learning_assistant_schema import (
    READONLY_ACTIONS,
    proposal_from_dict,
)

ACTIVE_KEY = "subject:v1:" + "a" * 32
ARCHIVED_KEY = "subject:v1:" + "b" * 32
TODAY = datetime.date(2026, 10, 15)


def _shim_resolver() -> types.SimpleNamespace:
    """Resolver shim: returns a catalog-like subject when the text names one."""

    def resolve(name: str):
        text = name or ""
        if "测试学科" in text:
            return types.SimpleNamespace(
                subject_key=ACTIVE_KEY,
                canonical_name="测试学科",
                lifecycle_status="active",
            )
        if "归档学科" in text:
            return types.SimpleNamespace(
                subject_key=ARCHIVED_KEY,
                canonical_name="归档学科",
                lifecycle_status="archived",
            )
        return None

    return resolve


def _parse(text: str, *, attachments: tuple[dict, ...] = ()) -> object:
    return parse_intent(
        text,
        attachments=tuple(attachments),
        subject_resolver=_shim_resolver(),
        today=TODAY,
    )


def _round_trips(proposal) -> None:
    restored = proposal_from_dict(proposal.to_dict())
    assert restored.to_dict() == proposal.to_dict()


def test_all_correct_homework_routes_to_submit_with_claims():
    proposal = _parse("测试学科 基础主题 作业3题全对，2026-10-01 做完了")
    assert proposal.action_type == "submit_homework"
    assert proposal.subject_key == ACTIVE_KEY
    assert proposal.user_facts["correctness_claim"] == "all_correct"
    # 独立性只有在用户明确说出（独立完成）时才登记。
    assert "independence" not in proposal.user_facts
    assert proposal.user_facts["completed_date"] == "2026-10-01"
    change = proposal.proposed_changes[0]
    assert change["entity"] == "learning_record"
    assert change["op"] == "insert"
    assert change["fields"]["problems"] == 3
    assert change["fields"]["date"] == "2026-10-01"
    assert proposal.requires_confirmation is True
    assert proposal.action_type not in READONLY_ACTIONS
    _round_trips(proposal)


def test_independence_recorded_only_when_stated():
    stated = _parse("测试学科 作业2题全对 独立完成")
    assert stated.action_type == "submit_homework"
    assert stated.user_facts["correctness_claim"] == "all_correct"
    assert stated.user_facts["independence"] == "independent"
    _round_trips(stated)


def test_attachment_only_homework_is_draft_submit_without_claim():
    attachment = {
        "path": "C:/tmp/homework_scan.txt",
        "file_name": "homework_scan.txt",
        "kind": "txt",
        "preflight_status": "ready",
    }
    proposal = _parse("数学 作业", attachments=(attachment,))
    assert proposal.action_type == "submit_homework"
    # 仅题面草稿语义：绝不虚构“全对”声明。
    assert "correctness_claim" not in proposal.user_facts
    assert "completed_date" not in proposal.user_facts
    assert proposal.attachments[0]["path"] == attachment["path"]
    change = proposal.proposed_changes[0]
    assert change["fields"]["problems"] == 1
    assert change["fields"]["date"] == TODAY.isoformat()
    assert any("不会记录成绩" in warning for warning in proposal.warnings)
    assert proposal.requires_confirmation is True
    _round_trips(proposal)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("测试学科 基础主题 复习了", "reviewed"),
        ("测试学科 第三章 讲完了", "class_completed"),
        ("测试学科 通过了测验", "quiz_passed"),
        ("测试学科 基础主题 掌握了", "mastered_claim"),
    ],
)
def test_progress_statements_route_to_update_learning_progress(text, kind):
    proposal = _parse(text)
    assert proposal.action_type == "update_learning_progress"
    assert proposal.user_facts["kind"] == kind
    # 未声明日期：不虚构 completed_date，change 字段回退到今天。
    assert "completed_date" not in proposal.user_facts
    assert proposal.proposed_changes[0]["fields"]["date"] == TODAY.isoformat()
    assert proposal.subject_key == ACTIVE_KEY
    assert proposal.requires_confirmation is True
    _round_trips(proposal)
def test_undo_routes_to_explicit_confirm_action():
    proposal = _parse("撤销刚才那一步")
    assert proposal.action_type == "undo_last_action"
    assert proposal.user_facts == {}
    change = proposal.proposed_changes[0]
    assert change["entity"] == "learning_record"
    assert change["op"] == "revoke"
    assert proposal.requires_confirmation is True
    _round_trips(proposal)


def test_mastery_preview_is_readonly_and_needs_no_confirmation():
    proposal = _parse("如果我掌握了 基础主题 会怎样")
    assert proposal.action_type == "preview_mastery_change"
    assert proposal.requires_confirmation is False
    assert proposal.action_type in READONLY_ACTIONS
    assert proposal.is_readonly is True
    _round_trips(proposal)


def test_recompute_alerts_routes_to_auto_eligible_action():
    proposal = _parse("重新计算预警")
    assert proposal.action_type == "recompute_alerts"
    change = proposal.proposed_changes[0]
    assert change["entity"] == "knowledge_alert"
    assert change["op"] == "recompute"
    assert proposal.requires_confirmation is True
    _round_trips(proposal)


def test_explain_alert_is_readonly_and_carries_alert_id():
    proposal = _parse("解释预警3")
    assert proposal.action_type == "explain_alert"
    assert proposal.requires_confirmation is False
    assert proposal.proposed_changes[0]["fields"] == {"alert_id": 3}
    _round_trips(proposal)


def test_handle_alert_routes_with_alert_id_fact():
    proposal = _parse("处理预警3")
    assert proposal.action_type == "handle_alert"
    assert proposal.user_facts["alert_id"] == 3
    change = proposal.proposed_changes[0]
    assert change["entity"] == "knowledge_alert"
    assert change["op"] == "handle"
    assert proposal.requires_confirmation is True
    _round_trips(proposal)


def test_snooze_alert_routes_with_alert_id_and_target_date():
    proposal = _parse("延后预警3到2026-10-01")
    assert proposal.action_type == "snooze_alert"
    assert proposal.user_facts["alert_id"] == 3
    assert proposal.user_facts["snoozed_until"] == "2026-10-01"
    change = proposal.proposed_changes[0]
    assert change["op"] == "snooze"
    assert change["fields"]["snoozed_until"] == "2026-10-01"
    assert proposal.warnings == []
    assert proposal.requires_confirmation is True
    _round_trips(proposal)


def test_gibberish_falls_back_to_readonly_answer_query():
    text = "今天想随便聊聊学习以外的事情"
    proposal = _parse(text)
    assert proposal.warnings == []
    assert proposal.requires_confirmation is False
    assert proposal.is_readonly is True
    assert proposal.confidence == 0.55
    assert proposal.proposed_changes[0]["op"] == "read"
    assert proposal.proposed_changes[0]["fields"]["question"] == text
    _round_trips(proposal)


def test_invalid_calendar_date_is_never_accepted_as_completed_date():
    proposal = _parse("测试学科 作业2题全对，2026-02-30 做完了")
    assert proposal.action_type == "submit_homework"
    # 解析器原样转交（交给上层校验），上游校验必须拒绝这个假日历日。
    assert proposal.user_facts["completed_date"] == "2026-02-30"

    from study_app.core.learning_assistant_records import (
        HomeworkFacts,
        build_record_from_homework,
    )
    from study_app.data.database import validate_calendar_date

    with pytest.raises(ValueError):
        validate_calendar_date(
            proposal.user_facts["completed_date"],
            label="user_facts.completed_date",
        )
    facts = HomeworkFacts(
        subject="测试学科",
        completed_date=proposal.user_facts["completed_date"],
        problems=(),
        claim_all_correct=True,
        duration_minutes=None,
        note="",
        draft_only=False,
    )
    with pytest.raises(ValueError):
        build_record_from_homework(
            facts, action_id=proposal.action_id, confirmed_at=TODAY.isoformat()
        )
    _round_trips(proposal)


def test_archived_subject_produces_gate_warning():
    proposal = _parse("归档学科 复习了")
    assert proposal.action_type == "update_learning_progress"
    assert proposal.subject_key == ARCHIVED_KEY
    assert any("归档" in warning for warning in proposal.warnings)
    _round_trips(proposal)


def test_every_routed_proposal_survives_schema_round_trip():
    texts = (
        "测试学科 基础主题 作业3题全对，2026-10-01 做完了",
        "测试学科 作业2题全对 独立完成",
        "数学 作业",
        "测试学科 基础主题 复习了",
        "测试学科 第三章 讲完了",
        "测试学科 通过了测验",
        "测试学科 基础主题 掌握了",
        "撤销刚才那一步",
        "如果我掌握了 基础主题 会怎样",
        "重新计算预警",
        "解释预警3",
        "处理预警3",
        "延后预警3到2026-10-01",
        "今天想随便聊聊学习以外的事情",
        "归档学科 复习了",
    )
    for text in texts:
        proposal = _parse(text)
        restored = proposal_from_dict(proposal.to_dict())
        assert restored.to_dict() == proposal.to_dict()
        assert restored.action_type == proposal.action_type
        assert restored.action_id == proposal.action_id


def test_build_alert_action_constructs_handle_and_snooze():
    handle = build_alert_action(
        "handle_alert", 7, date_value="2026-10-14", today=TODAY
    )
    assert handle.action_type == "handle_alert"
    assert handle.user_facts == {"alert_id": 7}
    assert handle.proposed_changes[0]["op"] == "handle"
    assert handle.requires_confirmation is True
    _round_trips(handle)

    snooze = build_alert_action(
        "snooze_alert", 7, date_value=datetime.date(2026, 10, 20), today=TODAY
    )
    assert snooze.user_facts == {"alert_id": 7, "snoozed_until": "2026-10-20"}
    assert snooze.proposed_changes[0]["fields"]["snoozed_until"] == "2026-10-20"
    assert "2026-10-20" in snooze.proposed_changes[0]["summary"]
    _round_trips(snooze)


def test_build_alert_action_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        build_alert_action("delete_alert", 7, date_value="2026-10-20", today=TODAY)
    with pytest.raises(ValueError):
        build_alert_action("handle_alert", 0, date_value="2026-10-20", today=TODAY)
    with pytest.raises(ValueError):
        build_alert_action("handle_alert", -3, date_value="2026-10-20", today=TODAY)
    with pytest.raises(ValueError):
        # 形似日期但不是真实日历日。
        build_alert_action("handle_alert", 7, date_value="2026-02-30", today=TODAY)
    with pytest.raises(ValueError):
        # 延后日期不得早于今天。
        build_alert_action("snooze_alert", 7, date_value="2026-10-01", today=TODAY)
    with pytest.raises(ValueError):
        build_alert_action(
            "handle_alert",
            7,
            date_value="2026-10-20",
            today=TODAY,
            subject_key="not-a-subject-key",
        )


# ---- 修订分支（A3/A11：revise_learning_record 自然语言路由）----------------


def test_revise_record_with_target_and_activity_routes_to_revise():
    proposal = _parse("把学习记录 68 修订为测验完成")
    assert proposal.action_type == "revise_learning_record"
    # 目标由记录 id 标识，不绑定学科键。
    assert proposal.subject_key is None
    assert proposal.user_facts["target_record_id"] == 68
    assert proposal.requires_confirmation is True
    change = proposal.proposed_changes[0]
    assert change["entity"] == "learning_record"
    assert change["op"] == "revise"
    assert change["target_id"] == 68
    assert change["summary"] == "追加式修订记录 #68"
    assert change["fields"]["activity"] == "quiz"
    _round_trips(proposal)


def test_revise_problem_correction_with_error_cause():
    proposal = _parse("更正记录68：第二题答错，错因是计算失误")
    assert proposal.action_type == "revise_learning_record"
    assert proposal.user_facts["target_record_id"] == 68
    change = proposal.proposed_changes[0]
    assert change["target_id"] == 68
    corrections = change["problems"]
    assert len(corrections) == 1
    assert corrections[0]["index"] == 2
    assert corrections[0]["set"] == {
        "status": "wrong",
        "correctness": 0.0,
        "error_cause": "计算失误",
    }
    assert proposal.confidence == 0.85
    _round_trips(proposal)


def test_revise_problem_correct_and_chinese_numeral_index():
    proposal = _parse("更正记录7：第十一题答对")
    change = proposal.proposed_changes[0]
    assert change["target_id"] == 7
    assert change["problems"] == [
        {
            "index": 11,
            "title_hint": None,
            "set": {"status": "correct", "correctness": 1.0},
        }
    ]
    _round_trips(proposal)


def test_revise_recent_record_uses_last_record_id_fallback():
    resolved = parse_intent(
        "修改刚才的学习记录",
        subject_resolver=_shim_resolver(),
        today=TODAY,
        last_record_id=77,
    )
    assert resolved.action_type == "revise_learning_record"
    assert resolved.user_facts["target_recent"] is True
    assert resolved.user_facts["target_record_id"] == 77
    assert resolved.proposed_changes[0]["target_id"] == 77
    assert resolved.requires_confirmation is True
    _round_trips(resolved)

    unresolved = _parse("修改刚才的学习记录")
    assert unresolved.action_type == "answer_query"
    assert unresolved.is_readonly is True
    assert any("记录编号" in warning for warning in unresolved.warnings)
    _round_trips(unresolved)


def test_revise_note_score_and_date_branches():
    note = _parse("修改记录5：备注改为：先复习错题。")
    assert note.proposed_changes[0]["fields"]["note"] == "先复习错题"

    score = _parse("修改记录5 分数改为 88")
    assert score.proposed_changes[0]["fields"]["score"] == 88

    date_revision = _parse("修改记录5 日期改为2026-10-01")
    assert date_revision.proposed_changes[0]["fields"]["date"] == "2026-10-01"

    for proposal in (note, score, date_revision):
        assert proposal.action_type == "revise_learning_record"
        assert proposal.proposed_changes[0]["target_id"] == 5
        assert proposal.user_facts["target_record_id"] == 5
        assert proposal.requires_confirmation is True
        _round_trips(proposal)


def test_revise_proposals_survive_schema_round_trip():
    texts = (
        "把学习记录 68 修订为测验完成",
        "更正记录68：第二题答错，错因是计算失误",
        "更正记录7：第十一题答对",
        "修改记录5：备注改为：先复习错题",
        "修改记录5 分数改为 88",
        "修改记录5 日期改为2026-10-01",
        "修改刚才的学习记录",  # 无编号：落回只读问答
    )
    for text in texts:
        proposal = _parse(text)
        restored = proposal_from_dict(proposal.to_dict())
        assert restored.to_dict() == proposal.to_dict()
        assert restored.action_type == proposal.action_type
