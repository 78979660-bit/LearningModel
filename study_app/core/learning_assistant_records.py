"""Learning-assistant record builders and read-only mastery preview.

Pure-python record construction for the Personal Learning OS learning
assistant (GLM53F contract §1). Heavy dependencies (database, model sync)
are imported lazily inside functions so the module top stays import-light.

Data-authority rules (action_schema_and_permissions.md §4):
- 用户声明 > 题目级显式 > 文本推断；解释题目结果的唯一入口是
  learning_problem_result.interpret_problem_result。
- "全对" → status="all_correct", correctness=1.0；未声明独立性 →
  independence="unknown"；无错因 → error_cause=None。
- 显式难度 0 必须原样保留（禁真值链），校验复用 database 的
  _validate_problem_difficulty_fields 行为。
- preview_mastery_impact 绝不写模型文件、绝不写数据库。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

INDEPENDENCE_VALUES = ("independent", "assisted", "unknown")
PROGRESS_KINDS = (
    "covered",
    "class_completed",
    "reviewed",
    "quiz_passed",
    "self_tested",
    "mastered_claim",
)

# activity 映射（合同 §1：进度语义 → learning_records.activity）。
_PROGRESS_ACTIVITY = {
    "covered": "class",
    "class_completed": "class",
    "reviewed": "review",
    "quiz_passed": "quiz",
    "self_tested": "self_test",
    "mastered_claim": "self_test",
}

_REVISABLE_FIELDS = (
    "date",
    "subject",
    "module",
    "topic",
    "activity",
    "source",
    "score",
    "duration_minutes",
    "note",
)

_UNNAMED_TITLE = "未命名题目"


@dataclass(frozen=True)
class HomeworkFacts:
    subject: str
    completed_date: str | None
    problems: tuple[dict, ...]
    claim_all_correct: bool
    duration_minutes: float | None
    note: str
    draft_only: bool
    independence: str = "unknown"
    attachments: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ProgressFacts:
    subject: str
    target: str
    kind: str
    completed_date: str | None
    note: str


def _validate_calendar_date(value: Any, *, label: str = "record.date") -> str:
    """Lazy wrapper around database.validate_calendar_date (keeps import light)."""
    from study_app.data.database import validate_calendar_date

    return validate_calendar_date(value, label=label)


def _validate_problem_difficulty_fields(problem: dict) -> None:
    from study_app.data.database import _validate_problem_difficulty_fields as validate

    validate(problem)


def _check_independence(value: Any) -> str:
    if value not in INDEPENDENCE_VALUES:
        raise ValueError(
            f"independence 必须是 {INDEPENDENCE_VALUES} 之一：{value!r}"
        )
    return str(value)


def _finite_number(value: Any, label: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数值（字符串数值与布尔值不接受）：{value!r}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{label} 必须是有限数值：{value!r}")
    return number


def _clean_error_cause(value: Any) -> str | None:
    if isinstance(value, list):
        value = "；".join(str(item) for item in value if item)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _problem_title(problem: dict, fallback: str = _UNNAMED_TITLE) -> str:
    title = problem.get("title") or problem.get("name") or fallback
    return str(title or _UNNAMED_TITLE)


def _result_interpretation(problem: dict) -> dict:
    from learning_problem_result import interpret_problem_result

    return interpret_problem_result(problem, None)


def _explicit_result_contradicts_claim(problem: dict) -> bool:
    """True when the problem carries its own explicit (user-level) result that
    contradicts a blanket "全对" claim. Text inference ranks below the user
    declaration and is therefore overridden, not treated as contradiction."""
    interpretation = _result_interpretation(problem)
    value = interpretation.get("value")
    if value is None:
        return False
    if interpretation.get("source") not in {"explicit_numeric", "status"}:
        return False
    return float(value) < 0.995


def _advisor_suggestion_for(
    advisor_report: Any, title: str
) -> dict | None:
    if not advisor_report:
        return None
    if isinstance(advisor_report, dict):
        advisor_report = advisor_report.get("problems") or advisor_report.get(
            "suggestions"
        ) or []
    for entry in advisor_report:
        if isinstance(entry, dict) and str(entry.get("title") or "") == title:
            return entry
    return None


def normalize_homework_problems(
    problems: Any,
    *,
    claim_all_correct: bool,
    independence: str = "unknown",
    advisor_report: Any = None,
) -> tuple[dict, ...]:
    """Normalize a problem list into storable problem dicts.

    - 每题写入 independence（用户声明 > 题目级显式 > 顾问 > "unknown"）；
      database._with_inferred_problem_data 用 dict(problem) 复制全部键，
      额外键会原样存入 raw_json，因此无需记录级 independence_by_title 映射。
    - "全对" 声明 → 未被题目级显式结果反驳的题目置
      status="all_correct", correctness=1.0；被反驳的题目保留自己的结果。
    - 用户未说明错因 → error_cause=None；显式 difficulty_score=0 原样保留。
    - 顾问建议只补缺，绝不覆盖用户/题目级显式事实。
    """
    if problems is None:
        return ()
    if isinstance(problems, dict) or not hasattr(problems, "__iter__"):
        raise ValueError("problems 必须是题目对象列表")
    _check_independence(independence)
    normalized: list[dict] = []
    for index, raw in enumerate(problems):
        if not isinstance(raw, dict):
            raise ValueError(f"problems[{index}] 必须是对象")
        _validate_problem_difficulty_fields(raw)
        item = copy.deepcopy(raw)
        title = _problem_title(item)
        suggestion = _advisor_suggestion_for(advisor_report, title)

        own_independence = item.get("independence")
        if independence != "unknown":
            item["independence"] = independence
        elif own_independence in INDEPENDENCE_VALUES:
            item["independence"] = str(own_independence)
        elif suggestion and suggestion.get("independence") in INDEPENDENCE_VALUES:
            item["independence"] = str(suggestion["independence"])
        else:
            item["independence"] = "unknown"

        item["error_cause"] = (
            _clean_error_cause(item.get("error_cause"))
            or (suggestion or {}).get("error_cause")
            or None
        )

        if item.get("difficulty_score") is None and suggestion:
            score = suggestion.get("difficulty_score")
            if score is not None:
                item["difficulty_score"] = score
            if item.get("difficulty") is None and suggestion.get("difficulty_label"):
                item["difficulty"] = str(suggestion["difficulty_label"])
        if not item.get("related_topics") and suggestion:
            points = suggestion.get("knowledge_points") or []
            if points:
                item["related_topics"] = list(points)

        if claim_all_correct:
            if _explicit_result_contradicts_claim(item):
                pass  # 题目级显式结果反驳“全对”：保留题目自己的结果
            else:
                item["status"] = "all_correct"
                item["correctness"] = 1.0
        normalized.append(item)
    return tuple(normalized)


def _resolve_date(
    stated: str | None, confirmed_at: str
) -> tuple[str, str]:
    if stated:
        return _validate_calendar_date(stated), "user_stated_completed"
    return _validate_calendar_date(confirmed_at), "confirmation_fallback"


def build_record_from_homework(
    facts: HomeworkFacts,
    *,
    action_id: str,
    confirmed_at: str,
) -> tuple[dict, list[str]]:
    """Build one importable learning record from user homework facts.

    Returns (record, warnings). The record is directly usable by
    import_learning_record_once / add_learning_record.
    """
    if not isinstance(facts.subject, str) or not facts.subject.strip():
        raise ValueError("record.subject is required")
    if facts.duration_minutes is not None:
        duration = _finite_number(facts.duration_minutes, "duration_minutes")
        if duration is not None and duration <= 0:
            raise ValueError(f"duration_minutes 必须大于 0：{facts.duration_minutes!r}")
    date_value, date_basis = _resolve_date(facts.completed_date, confirmed_at)
    _check_independence(facts.independence)

    warnings: list[str] = []
    problems = normalize_homework_problems(
        facts.problems,
        claim_all_correct=facts.claim_all_correct and not facts.draft_only,
        independence=facts.independence,
    )
    if facts.draft_only:
        stripped: list[dict] = []
        for problem in problems:
            item = dict(problem)
            item.pop("status", None)
            item.pop("correctness", None)
            stripped.append(item)
        problems = tuple(stripped)
        warnings.append("题面不等于完成")
        warnings.append("仅题面草稿：本记录不含作答结果，不产生掌握度贡献")
    elif facts.claim_all_correct:
        contradicted = [
            _problem_title(p)
            for p in facts.problems
            if isinstance(p, dict) and _explicit_result_contradicts_claim(p)
        ]
        if contradicted:
            warnings.append(
                "题目级显式结果与“全对”声明冲突，已保留题目级结果："
                + "、".join(contradicted)
            )

    record: dict[str, Any] = {
        "date": date_value,
        "subject": facts.subject.strip(),
        "module": None,
        "topic": None,
        "activity": "exercise",
        "source": "outside_class",
        "note": facts.note or "",
        "problems": list(problems),
        "date_basis": date_basis,
        "confirmed_at": confirmed_at,
        "source_evidence": {"action_id": action_id},
    }
    if facts.duration_minutes is not None:
        record["duration_minutes"] = facts.duration_minutes
    if facts.draft_only:
        record["draft_only"] = True
    if facts.attachments:
        record["attachments"] = [dict(item) for item in facts.attachments]
    return record, warnings


def build_record_from_progress(
    facts: ProgressFacts,
    *,
    action_id: str,
    confirmed_at: str,
    module_name: str | None = None,
    topic_name: str | None = None,
) -> tuple[dict, list[str]]:
    """Build one importable learning-record for progress statements.

    activity 映射：covered/class_completed→class；reviewed→review；
    quiz_passed→quiz；self_tested/mastered_claim→self_test。
    mastered_claim 只写 user_fact + “自评掌握：”note，绝不直接改模型状态。
    """
    if facts.kind not in PROGRESS_KINDS:
        raise ValueError(f"未知进度类型：{facts.kind!r}（允许：{PROGRESS_KINDS}）")
    if not isinstance(facts.subject, str) or not facts.subject.strip():
        raise ValueError("record.subject is required")
    date_value, date_basis = _resolve_date(facts.completed_date, confirmed_at)

    warnings: list[str] = []
    note = facts.note or ""
    if facts.kind == "mastered_claim":
        note = f"自评掌握：{note}"
        warnings.append("自评掌握不直接改变模型状态；模型状态仍由证据同步决定")

    record: dict[str, Any] = {
        "date": date_value,
        "subject": facts.subject.strip(),
        "module": module_name or None,
        "topic": topic_name or (facts.target or None),
        "activity": _PROGRESS_ACTIVITY[facts.kind],
        "source": "outside_class",
        "note": note,
        "date_basis": date_basis,
        "confirmed_at": confirmed_at,
        "source_evidence": {"action_id": action_id},
        "progress_kind": facts.kind,
    }
    return record, warnings


def build_revision(
    original: dict,
    changes: dict,
    *,
    record_id: int,
) -> tuple[dict, list[str]]:
    """Build a full replacement record for revise_learning_record.

    changes={"fields": {...}, "problems": [完整替换列表]（可选）}。
    只允许白名单顶层字段；附件关系禁止改动（ValueError）。
    返回 (replacement, warnings)；replacement 为深拷贝，attachments 原样保留。
    """
    if not isinstance(original, dict):
        raise ValueError("original 必须是记录 raw_json 对象")
    if not isinstance(changes, dict):
        raise ValueError("changes 必须是对象")
    fields = changes.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("changes.fields 必须是对象")
    # 附件关系守卫必须先于未知键拒绝：任何触碰 attachments 的修订都按
    # “隐式改动附件关系”报错（D-02），而不是笼统的未支持键。
    if "attachments" in fields or "attachments" in changes:
        raise ValueError("修订不得隐式改动附件关系")
    unknown_top = sorted(set(changes) - {"fields", "problems"})
    if unknown_top:
        raise ValueError(f"changes 存在未支持键：{unknown_top}")
    unknown_fields = sorted(set(fields) - set(_REVISABLE_FIELDS))
    if unknown_fields:
        raise ValueError(
            f"修订仅允许以下字段：{list(_REVISABLE_FIELDS)}；收到未允许字段：{unknown_fields}"
        )

    warnings: list[str] = ["修订为全量字段替换；未列出的字段保持原值"]
    replacement = copy.deepcopy(original)
    replacement["id"] = record_id

    for key, value in fields.items():
        if key == "date":
            replacement["date"] = _validate_calendar_date(value)
        elif key == "score":
            number = _finite_number(value, "score")
            if number is not None and not 0 <= number <= 100:
                raise ValueError(f"score 必须在 0-100 范围内：{value!r}")
            replacement["score"] = value
        elif key == "duration_minutes":
            number = _finite_number(value, "duration_minutes")
            if number is not None and number <= 0:
                raise ValueError(f"duration_minutes 必须大于 0：{value!r}")
            replacement["duration_minutes"] = value
        elif key == "subject":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("record.subject is required")
            replacement["subject"] = value.strip()
        elif key in ("module", "topic", "note", "source", "activity"):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key} 必须是字符串或 null")
            replacement[key] = value
        else:  # pragma: no cover - guarded by unknown_fields above
            raise ValueError(f"修订不允许修改字段：{key}")

    if "problems" in changes:
        raw_problems = changes.get("problems")
        if raw_problems is None:
            raise ValueError("changes.problems 必须是完整替换列表，不接受 null")
        replacement["problems"] = list(
            normalize_homework_problems(
                raw_problems, claim_all_correct=False, independence="unknown"
            )
        )
        warnings.append("题目列表为完整替换，未提供的题目将被移除")

    # replacement 是 original 的深拷贝，attachments 已与 original 完全一致
    # （含“无附件”的情形）；changes 中的任何附件改动已在上方被拒绝。
    return replacement, warnings


def _load_model_copy(model_path: Any) -> tuple[dict, dict]:
    """Read the model JSON once; return (original, deep-copied working set)."""
    import json
    from pathlib import Path

    try:
        raw_text = Path(model_path).read_text(encoding="utf-8")
        model = json.loads(raw_text)
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取学习模型文件：{model_path}（{error}）") from error
    if not isinstance(model, dict):
        raise ValueError(f"学习模型文件格式非法：{model_path}")
    return model, copy.deepcopy(model)


def preview_mastery_impact(
    record: dict,
    *,
    db_path: Any,
    model_path: Any,
) -> dict:
    """Read-only what-if: replay the mastery-sync algorithm on a copy.

    复用 model_progress_sync 的同款函数（topic_observation_from_record /
    interpret_problem_result / mastery_delta / apply_mastery_contribution /
    recompute_module_mastery / recompute_subject_mastery）在深拷贝上推演。
    绝不写模型文件，绝不写数据库（不调用 sync_topic_statuses_to_sqlite，
    不 json.dump）。
    """
    model, work = _load_model_copy(model_path)
    from study_app.data.model_progress_sync import (  # heavy: lazy import
        LEARNED_STATUS,
        STATUS_RANK,
        apply_diagnostic_baseline_contribution,
        apply_mastery_contribution,
        diagnostic_baseline_topic_in_scope,
        is_diagnostic_baseline_record,
        is_learning_evidence_record,
        module_has_explicit_evidence,
        recompute_module_mastery,
        recompute_subject_mastery,
        record_learning_text,
        topic_has_learning_evidence,
    )
    from study_app.data.database import get_setting
    from study_app.core.study_phase import phase_setting_key

    notes = ["预览为只读推演，未写入模型文件或数据库"]
    matched_topics: list[dict[str, Any]] = []
    subject_mastery: list[dict[str, Any]] = []

    def _no_evidence(extra_notes: list[str]) -> dict:
        return {
            "matched_topics": [],
            "subject_mastery": [],
            "no_evidence": True,
            "notes": notes + extra_notes,
        }

    if not isinstance(record, dict):
        raise ValueError("record 必须是学习记录对象")
    subject_name = str(record.get("subject") or "")
    if record.get("draft_only"):
        return _no_evidence(["仅题面草稿（draft_only）：不产生掌握度贡献"])
    if not subject_name:
        return _no_evidence(["记录缺少学科，无法匹配模型"])
    if not is_learning_evidence_record(record):
        return _no_evidence(["记录不是学习证据类型（activity/source），不参与掌握度同步"])

    record_text = record_learning_text(record)
    diagnostic_baseline = is_diagnostic_baseline_record(record)
    try:
        phase = get_setting(phase_setting_key(subject_name), {}, db_path)
    except Exception:
        phase = {}
    if not isinstance(phase, dict):
        phase = {}

    target_subject = None
    for subject in work.get("subjects", []):
        if subject.get("name") == subject_name:
            target_subject = subject
            break
    if target_subject is None:
        return _no_evidence([f"模型中不存在学科「{subject_name}」，无匹配知识点"])

    subject_before = target_subject.get("mastery")
    for module in target_subject.get("modules", []):
        module_changed = False
        module_explicit = module_has_explicit_evidence(module, record)
        for topic in module.get("topics", []):
            topic_in_scope = (
                diagnostic_baseline_topic_in_scope(
                    subject_name,
                    str(module.get("name") or ""),
                    str(topic.get("name") or ""),
                    str(topic.get("submodule") or ""),
                    phase=phase,
                )
                if diagnostic_baseline
                else False
            )
            if (
                not topic_in_scope
                and not module_explicit
                and not topic_has_learning_evidence(topic, record_text)
            ):
                continue
            status_before = str(topic.get("status") or "")
            mastery_before = topic.get("mastery")
            topic_changed = False
            if STATUS_RANK.get(status_before, 0) < STATUS_RANK[LEARNED_STATUS]:
                topic["status"] = LEARNED_STATUS
                topic_changed = True
            if topic_in_scope:
                contribution = apply_diagnostic_baseline_contribution(
                    record, module, topic
                )
            else:
                contribution = apply_mastery_contribution(record, module, topic)
            if contribution:
                topic_changed = True
            if topic_changed:
                module_changed = True
                matched_topics.append(
                    {
                        "subject": subject_name,
                        "module": module.get("name"),
                        "topic": topic.get("name"),
                        "mastery_before": mastery_before,
                        "mastery_after": topic.get("mastery"),
                        "status_before": status_before,
                        "status_after": str(topic.get("status") or ""),
                    }
                )
        if module_changed:
            if (
                STATUS_RANK.get(str(module.get("status") or ""), 0)
                < STATUS_RANK[LEARNED_STATUS]
            ):
                module["status"] = LEARNED_STATUS
            recompute_module_mastery(module)
    recompute_subject_mastery(target_subject)
    subject_mastery.append(
        {
            "subject": subject_name,
            "before": subject_before,
            "after": target_subject.get("mastery"),
        }
    )
    # 与 sync_learning_record_to_model 一致：只处理第一个同名学科。

    if matched_topics and diagnostic_baseline:
        notes.append("识别为诊断基线记录：按考试范围基线校准推演")
    no_evidence = not matched_topics
    if no_evidence:
        notes.append("未匹配到任何模型知识点或无可解释结果，不会产生掌握度变化")
    return {
        "matched_topics": matched_topics,
        "subject_mastery": subject_mastery,
        "no_evidence": no_evidence,
        "notes": notes,
    }
