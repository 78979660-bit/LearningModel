"""Local deterministic intent parser for the learning assistant (v1).

Pure functions, zero IO: no database, no filesystem, no network. The parser
acts as the "mock" provider of the GLM53F contract §2 — it never invents
facts (inferences stay empty); everything it outputs is either a keyword hit
or a verbatim piece of the user's text.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Callable

from study_app.core.learning_assistant_schema import (
    READONLY_ACTIONS,
    ActionProposal,
    SCHEMA_VERSION,
    default_disclosure,
    new_action_id,
    validate_action_id,
)

INTENT_VERSION = "la-intent-v1"

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_ALERT_ID_RE = re.compile(r"预警#?(\d+)")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

_ALL_CORRECT_RE = re.compile(r"全对|全部正确|都做对了")
_INDEPENDENT_RE = re.compile(r"独立完成|自己做的|独立")
_UNDO_RE = re.compile(r"撤销|回退")
_PREVIEW_RE = re.compile(r"预览|如果|假设")
_PREVIEW_TAIL_RE = re.compile(r"掌握|影响|变化")
_SNOOZE_RE = re.compile(r"延后[\s\S]*?预警")
_HANDLE_RE = re.compile(r"处理(?:这条|该)?预警")
_RECOMPUTE_RE = re.compile(r"重新计算|刷新[\s\S]*?预警")
_EXPLAIN_RE = re.compile(r"为什么[\s\S]*?预警|预警[\s\S]*?为什么|解释[\s\S]*?预警")
_HOMEWORK_CONTEXT_RE = re.compile(r"题|作业|练习")
_COUNT_RE = re.compile(r"(\d+)\s*题")

# 修订分支（A3/A11：把自然语言修订接入意图路由）。
_REVISE_WORD_RE = re.compile(r"修订|更正|修改|改正")
_RECORD_TARGET_RE = re.compile(r"记录\s*#?\s*(\d+)")
_RECENT_RECORD_RE = re.compile(r"刚才|上一?条|最近的?记录")
_REVISE_NOTE_RE = re.compile(r"备注(?:(?:改为|改成)[：:]?|[：:])\s*([\s\S]*)")
_REVISE_SCORE_RE = re.compile(r"分数(?:改为|改成)?\s*(\d{1,3})")
_REVISE_DATE_RE = re.compile(r"日期(?:改为|改成)?\s*(\d{4}-\d{2}-\d{2})")
_REVISE_QUIZ_RE = re.compile(r"(?:测验|做题)完成")
_REVISE_REVIEW_RE = re.compile(r"复习完成")
_REVISE_CLASS_RE = re.compile(r"课堂(?:完成|讲完)")
_REVISE_GUIDE_RE = re.compile(r"分数(?:改为|改成)|日期(?:改为|改成)")
_PROBLEM_REF_RE = re.compile(r"第([\一二三四五六七八九十\d]+)题")
_PROBLEM_WRONG_RE = re.compile(r"答错|错了|做错|错误")
_PROBLEM_RIGHT_RE = re.compile(r"答对|做对|正确")
_PROBLEM_CAUSE_RE = re.compile(
    r"(?:错因|原因)(?:是|为)?\s*[：:]?\s*([\s\S]+?)(?=$|[,，。！？；、])"
)
_TRAILING_PUNCT = " \u3000，。！？；、,.!?;:：…—~～-"

# 顺序即 kind 映射优先级（讲完/上完 → class_completed 必须先于 学完/看完）。
_PROGRESS_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("讲完", "class_completed"),
    ("上完", "class_completed"),
    ("学完", "covered"),
    ("看完", "covered"),
    ("复习", "reviewed"),
    ("测验", "quiz_passed"),
    ("掌握", "mastered_claim"),
)
_PROGRESS_FALLBACK_KIND = "self_tested"
_PROGRESS_TRIGGER_RE = re.compile(r"学完了|看完了|复习了|讲完了|上完了|通过了?测验|掌握了")

_FILLER_RE = re.compile(
    r"^(?:了|的|吗|呢|吧|这|该|本|把|我|你|他|今天|昨天|刚才|都|全部|已经|这一章|这章|这一课)+"
    r"|什么|怎样|怎么样|如何$"
)

_ACTION_SUMMARIES = {
    "undo_last_action": ("learning_record", "revoke", "撤销最近一次可逆的助理动作"),
    "preview_mastery_change": ("none", "preview", "只读预览掌握度影响，不写库"),
    "recompute_alerts": ("knowledge_alert", "recompute", "按证据重算知识预警"),
    "explain_alert": ("none", "read", "只读解释预警触发依据"),
    "answer_query": ("none", "read", "只读问答，不写库"),
}


def _first_date(text: str) -> str | None:
    match = _DATE_RE.search(text)
    if not match:
        return None
    value = match.group(1)
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return value  # 格式形似日期但不是真实日历日：交给上层校验拒绝
    return value



_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _strip_trailing_punct(value: Any) -> str:
    return str(value or "").strip().strip(_TRAILING_PUNCT).strip()


def _chinese_numeral(raw: str) -> int | None:
    """一..九 → 1..9；十 → 10；十一 → 11；二十 → 20；阿拉伯数字原样。"""
    text = (raw or "").strip()
    if text.isdigit():
        return int(text)
    if not text or any(ch not in "一二三四五六七八九十" for ch in text):
        return None
    if text == "十":
        return 10
    head, sep, tail = text.partition("十")
    if not sep:
        return _CN_DIGITS.get(text) if len(text) == 1 else None
    if head and head not in _CN_DIGITS:
        return None
    tens = _CN_DIGITS[head] if head else 1
    ones = 0
    if tail:
        if tail not in _CN_DIGITS:
            return None
        ones = _CN_DIGITS[tail]
    return tens * 10 + ones


def _parse_problem_corrections(text: str) -> list[dict[str, Any]]:
    """第N题 + (答错/答对 [错因…]) → 题目级修订指令列表。"""
    corrections: list[dict[str, Any]] = []
    matches = list(_PROBLEM_REF_RE.finditer(text))
    for position, match in enumerate(matches):
        segment_end = (
            matches[position + 1].start()
            if position + 1 < len(matches)
            else len(text)
        )
        segment = text[match.end():segment_end]
        wrong = _PROBLEM_WRONG_RE.search(segment)
        right = _PROBLEM_RIGHT_RE.search(segment)
        values: dict[str, Any] = {}
        if wrong and (right is None or wrong.start() < right.start()):
            values["status"] = "wrong"
            values["correctness"] = 0.0
        elif right:
            values["status"] = "correct"
            values["correctness"] = 1.0
        else:
            continue
        cause = _PROBLEM_CAUSE_RE.search(segment)
        if cause:
            cause_text = _strip_trailing_punct(cause.group(1))[:500].strip()
            if cause_text:
                values["error_cause"] = cause_text
        corrections.append(
            {
                "index": _chinese_numeral(match.group(1)),
                "title_hint": None,
                "set": values,
            }
        )
    return corrections

def _candidate_phrases(text: str) -> list[str]:
    """Candidate subject names: full text, tokens, and short CJK runs that sit
    right before progress/homework trigger words (known aliases are the
    resolver's job; we only extract noun-phrase candidates)."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = (value or "").strip(" ，。！？、：；,.!?：")
        if not value or value in seen or len(value) > 30:
            return
        seen.add(value)
        candidates.append(value)

    add(text)
    for token in _TOKEN_RE.findall(text):
        add(token)
    for match in re.finditer(
        r"([\u4e00-\u9fff]{2,12}?)(?=学完了|看完了|复习了|讲完了|上完了|通过了?测验|掌握了"
        r"|做完了|完成了|全对|全部正确|都做对了)",
        text,
    ):
        run = match.group(1)
        run = re.sub(r"^[我你他她它把把的了吗呢吧今天昨天刚才]+", "", run) or run
        for width in (6, 5, 4, 3, 2):
            if len(run) >= width:
                add(run[-width:])
    return candidates


def _resolve_subject(
    text: str, subject_resolver: Callable[[str], object | None]
) -> tuple[object | None, str | None]:
    for candidate in _candidate_phrases(text):
        try:
            subject = subject_resolver(candidate)
        except Exception:
            subject = None
        if subject is not None:
            return subject, candidate
    return None, None


def _extract_target(text: str, subject_text: str | None) -> str:
    """Progress target: text right after the trigger verb, else the clause."""
    match = _PROGRESS_TRIGGER_RE.search(text)
    if match:
        tail = text[match.end():]
        tail = tail.split("，")[0].split("。")[0].split("、")[0]
        tail = tail.strip(" ，。！？、：；,.!?：的了吗呢吧")
        if tail:
            return tail
    if subject_text and subject_text in text:
        return subject_text
    return text.strip(" ，。！？、：；,.!?：")[:40]


def _clean_target(raw: str) -> str:
    text = (raw or "").strip()
    while text:
        stripped = _FILLER_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return text or (raw or "").strip()


def _proposal(
    *,
    action_type: str,
    action_id: str,
    subject_key: str | None,
    user_facts: dict[str, Any],
    proposed_changes: list[dict[str, Any]],
    confidence: float,
    warnings: list[str],
    attachments: tuple[dict, ...],
) -> ActionProposal:
    return ActionProposal(
        schema_version=SCHEMA_VERSION,
        action_id=action_id,
        action_type=action_type,
        mode="preview",
        subject_key=subject_key,
        user_facts=user_facts,
        inferences=(),
        attachments=tuple(attachments),
        proposed_changes=list(proposed_changes),
        confidence=confidence,
        requires_confirmation=action_type not in READONLY_ACTIONS,
        external_data_disclosure=default_disclosure(
            will_contact_external=False,
            advisor_required=(action_type == "submit_homework"),
        ),
        warnings=list(warnings),
    )


def parse_intent(
    text: str,
    *,
    attachments: tuple[dict, ...] = (),
    subject_resolver: Callable[[str], object | None],
    today: datetime.date,
    action_id: str | None = None,
    last_record_id: int | None = None,
) -> ActionProposal:
    """Parse one utterance into a preview ActionProposal (no IO, no LLM)."""
    raw_text = str(text or "")
    text = raw_text.strip()
    if not text:
        raise ValueError("意图文本不能为空")
    resolved_id = validate_action_id(action_id) if action_id else new_action_id()
    warnings: list[str] = []
    user_facts: dict[str, Any] = {}

    subject: object | None = None
    subject_text: str | None = None
    canonical_name: str | None = None
    subject_key: str | None = None
    if text and subject_resolver is not None:
        subject, subject_text = _resolve_subject(text, subject_resolver)
    if subject is not None:
        canonical_name = str(getattr(subject, "canonical_name", "") or "") or None
        candidate_key = getattr(subject, "subject_key", None)
        if isinstance(candidate_key, str) and candidate_key.startswith("subject:v1:"):
            subject_key = candidate_key
        else:
            warnings.append("学科键格式异常，已忽略；执行前需人工确认学科")
        lifecycle = getattr(subject, "lifecycle_status", "active")
        if lifecycle and lifecycle != "active":
            state_text = (
                "已归档" if lifecycle == "archived" else f"状态为 {lifecycle}"
            )
            warnings.append(
                f"学科「{canonical_name or subject_text}」{state_text}，"
                "执行将被门禁拒绝"
            )

    stated_date = _first_date(text)
    alert_id_match = _ALERT_ID_RE.search(text)

    # ---- 触发规则（先命中先得） --------------------------------------
    if _UNDO_RE.search(text):
        action_type = "undo_last_action"
        entity, op, summary = _ACTION_SUMMARIES[action_type]
        changes = [{"entity": entity, "op": op, "summary": summary, "fields": {}}]
        return _proposal(
            action_type=action_type,
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=attachments,
        )
    # ---- 修订学习记录（A3/A11）：目标 = 记录编号 或 刚才/上一条 ----------
    revise_word = _REVISE_WORD_RE.search(text)
    record_id_match = _RECORD_TARGET_RE.search(text)
    recent_record = bool(_RECENT_RECORD_RE.search(text))
    if revise_word and (record_id_match or recent_record):
        revise_fields: dict[str, Any] = {}
        if _REVISE_QUIZ_RE.search(text):
            revise_fields["activity"] = "quiz"
        elif _REVISE_REVIEW_RE.search(text):
            revise_fields["activity"] = "review"
        elif _REVISE_CLASS_RE.search(text):
            revise_fields["activity"] = "class"
        note_match = _REVISE_NOTE_RE.search(text)
        if note_match:
            note_value = note_match.group(1)
            cut = _REVISE_GUIDE_RE.search(note_value)
            if cut:
                note_value = note_value[: cut.start()]
            note_value = _strip_trailing_punct(note_value)[:2000].strip()
            if note_value:
                revise_fields["note"] = note_value
        score_match = _REVISE_SCORE_RE.search(text)
        if score_match:
            revise_fields["score"] = int(score_match.group(1))
        revise_date_match = _REVISE_DATE_RE.search(text)
        if revise_date_match:
            revise_fields["date"] = revise_date_match.group(1)
        problem_corrections = _parse_problem_corrections(text)

        target_record_id: int | None = None
        if record_id_match:
            target_record_id = int(record_id_match.group(1))
            user_facts["target_record_id"] = target_record_id
        else:
            user_facts["target_recent"] = True
            if last_record_id:
                target_record_id = int(last_record_id)
                user_facts["target_record_id"] = target_record_id

        if target_record_id is not None or revise_fields or problem_corrections:
            if not (revise_fields or problem_corrections):
                warnings.append("未解析到具体更正内容；确认后本次修订不会改变记录内容。")
            revise_changes: list[dict[str, Any]] = [
                {
                    "entity": "learning_record",
                    "op": "revise",
                    "summary": (
                        f"追加式修订记录 #{target_record_id}"
                        if target_record_id is not None
                        else "追加式修订学习记录"
                    ),
                    "fields": revise_fields,
                    "problems": problem_corrections,
                }
            ]
            if target_record_id is not None:
                revise_changes[0]["target_id"] = target_record_id
            return _proposal(
                action_type="revise_learning_record",
                action_id=resolved_id,
                subject_key=None,  # 修订目标由记录 id 标识，不绑定学科
                user_facts=user_facts,
                proposed_changes=revise_changes,
                confidence=0.85
                if (revise_fields or problem_corrections)
                else 0.7,
                warnings=warnings,
                attachments=(),
            )
        # 触发了修订措辞但没有记录编号、也没有可解析的更正内容：
        # 不产生写库提案，落入只读问答并提示补齐要素。
        warnings.append(
            "修订需要记录编号与具体更正内容，例如：更正记录68：第二题答错"
        )

    if _PREVIEW_RE.search(text) and _PREVIEW_TAIL_RE.search(text):
        entity, op, summary = _ACTION_SUMMARIES["preview_mastery_change"]
        fields: dict[str, Any] = {}
        if canonical_name or subject_text:
            fields["subject"] = canonical_name or subject_text
        changes = [{"entity": entity, "op": op, "summary": summary, "fields": fields}]
        return _proposal(
            action_type="preview_mastery_change",
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=attachments,
        )

    if _SNOOZE_RE.search(text):
        user_facts["snoozed_until"] = stated_date
        if alert_id_match:
            user_facts["alert_id"] = int(alert_id_match.group(1))
        else:
            warnings.append("未指明预警编号")
        if not stated_date:
            warnings.append("未指明延后日期，需补齐后才能执行")
        entity, op, summary = "knowledge_alert", "snooze", "延后知识预警"
        fields = {"alert_id": user_facts.get("alert_id")}
        if stated_date:
            fields["snoozed_until"] = stated_date
            summary = f"延后预警至 {stated_date}"
        changes = [{"entity": entity, "op": op, "summary": summary, "fields": fields}]
        return _proposal(
            action_type="snooze_alert",
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=(),
        )

    if _HANDLE_RE.search(text):
        user_facts["alert_id"] = (
            int(alert_id_match.group(1)) if alert_id_match else None
        )
        if user_facts["alert_id"] is None:
            warnings.append("未指明预警编号")
            user_facts.pop("alert_id")
        changes = [
            {
                "entity": "knowledge_alert",
                "op": "handle",
                "summary": "标记预警为已处理（≠掌握）",
                "fields": {"alert_id": user_facts.get("alert_id")},
            }
        ]
        return _proposal(
            action_type="handle_alert",
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=(),
        )

    if _RECOMPUTE_RE.search(text):
        entity, op, summary = _ACTION_SUMMARIES["recompute_alerts"]
        changes = [{"entity": entity, "op": op, "summary": summary, "fields": {}}]
        return _proposal(
            action_type="recompute_alerts",
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=(),
        )

    if _EXPLAIN_RE.search(text):
        entity, op, summary = _ACTION_SUMMARIES["explain_alert"]
        fields = {"alert_id": int(alert_id_match.group(1))} if alert_id_match else {}
        changes = [{"entity": entity, "op": op, "summary": summary, "fields": fields}]
        return _proposal(
            action_type="explain_alert",
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=(),
        )

    all_correct_claim = bool(_ALL_CORRECT_RE.search(text))
    done_claim = bool(re.search(r"做完了|完成了", text))
    homework_context = bool(attachments) or bool(_HOMEWORK_CONTEXT_RE.search(text))
    if homework_context and (all_correct_claim or done_claim or attachments):
        if all_correct_claim:
            user_facts["correctness_claim"] = "all_correct"
        if _INDEPENDENT_RE.search(text):
            user_facts["independence"] = "independent"
        if stated_date:
            user_facts["completed_date"] = stated_date
        target = _clean_target(_extract_target(text, subject_text))
        if target:
            user_facts["target"] = target
        count = len(attachments)
        if not count:
            count_match = _COUNT_RE.search(text)
            count = int(count_match.group(1)) if count_match else 0
        if not count:
            warnings.append("未指明题目数量，登记前请补充题目明细")
        if attachments and not all_correct_claim:
            warnings.append("仅题面附件：不会记录成绩")
        fields: dict[str, Any] = {"problems": count}
        if canonical_name or subject_text:
            fields["subject"] = canonical_name or subject_text
        if target:
            fields["target"] = target
        if stated_date:
            fields["date"] = stated_date
        else:
            fields["date"] = today.isoformat()
        summary = "登记作业记录（全对）" if all_correct_claim else "登记作业记录"
        changes = [
            {"entity": "learning_record", "op": "insert", "summary": summary, "fields": fields}
        ]
        return _proposal(
            action_type="submit_homework",
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=attachments,
        )

    progress_match = _PROGRESS_TRIGGER_RE.search(text)
    if progress_match:
        kind = _PROGRESS_FALLBACK_KIND
        for word, mapped in _PROGRESS_TRIGGERS:
            if word in text:
                kind = mapped
                break
        user_facts["kind"] = kind
        if stated_date:
            user_facts["completed_date"] = stated_date
        target = _clean_target(_extract_target(text, subject_text))
        if target:
            user_facts["target"] = target
        fields: dict[str, Any] = {"kind": kind}
        if canonical_name or subject_text:
            fields["subject"] = canonical_name or subject_text
        if target:
            fields["target"] = target
        if stated_date:
            fields["date"] = stated_date
        else:
            fields["date"] = today.isoformat()
        changes = [
            {
                "entity": "learning_record",
                "op": "insert",
                "summary": "登记学习进度（自评掌握，不直接改模型状态）"
                if kind == "mastered_claim"
                else "登记学习进度",
                "fields": fields,
            }
        ]
        return _proposal(
            action_type="update_learning_progress",
            action_id=resolved_id,
            subject_key=subject_key,
            user_facts=user_facts,
            proposed_changes=changes,
            confidence=0.9,
            warnings=warnings,
            attachments=(),
        )

    # ---- 兜底：只读问答 ---------------------------------------------
    if stated_date:
        user_facts["completed_date"] = stated_date
    entity, op, summary = _ACTION_SUMMARIES["answer_query"]
    changes = [
        {
            "entity": entity,
            "op": op,
            "summary": summary,
            "fields": {"question": raw_text[:200]},
        }
    ]
    return _proposal(
        action_type="answer_query",
        action_id=resolved_id,
        subject_key=subject_key,
        user_facts=user_facts,
        proposed_changes=changes,
        confidence=0.55,
        warnings=warnings,
        attachments=(),
    )


def build_alert_action(
    action_type: str,
    alert_id: int,
    *,
    date_value: Any,
    today: datetime.date,
    action_id: str | None = None,
    subject_key: str | None = None,
) -> ActionProposal:
    """Deterministic constructor for the 预警 handle/snooze buttons."""
    if action_type not in ("handle_alert", "snooze_alert"):
        raise ValueError(f"build_alert_action 仅支持 handle_alert/snooze_alert：{action_type!r}")
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id <= 0:
        raise ValueError(f"alert_id 必须是正整数：{alert_id!r}")
    if not isinstance(today, datetime.date) or isinstance(today, datetime.datetime):
        raise ValueError(f"today 必须是 datetime.date：{today!r}")
    if isinstance(date_value, datetime.datetime):
        raise ValueError("date_value 必须是 datetime.date 或 YYYY-MM-DD 字符串")
    if isinstance(date_value, datetime.date):
        target_date = date_value
    elif isinstance(date_value, str):
        try:
            target_date = datetime.date.fromisoformat(date_value)
        except ValueError as error:
            raise ValueError(
                f"date_value 必须是真实日历日期 YYYY-MM-DD：{date_value!r}"
            ) from error
    else:
        raise ValueError(f"date_value 必须是 datetime.date 或 YYYY-MM-DD 字符串：{date_value!r}")
    if action_type == "snooze_alert" and target_date < today:
        raise ValueError("延后日期必须不早于今天")

    resolved_id = validate_action_id(action_id) if action_id else new_action_id()
    user_facts: dict[str, Any] = {"alert_id": alert_id}
    fields: dict[str, Any] = {"alert_id": alert_id}
    if action_type == "snooze_alert":
        user_facts["snoozed_until"] = target_date.isoformat()
        fields["snoozed_until"] = target_date.isoformat()
        summary = f"延后预警#{alert_id}至 {target_date.isoformat()}"
        op = "snooze"
    else:
        summary = f"标记预警#{alert_id}为已处理（≠掌握）"
        op = "handle"
    if subject_key is not None and not (
        isinstance(subject_key, str) and subject_key.startswith("subject:v1:")
    ):
        raise ValueError("subject_key 必须是 subject:v1: 形式的稳定身份或 null")
    return _proposal(
        action_type=action_type,
        action_id=resolved_id,
        subject_key=subject_key,
        user_facts=user_facts,
        proposed_changes=[
            {"entity": "knowledge_alert", "op": op, "summary": summary, "fields": fields}
        ],
        confidence=0.9,
        warnings=[],
        attachments=(),
    )
