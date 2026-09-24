from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from functools import lru_cache

from study_app.ai.study_plan_service import (
    current_study_plan_signature,
    generate_study_plan_with_optional_llm,
    llm_plan_unavailable_hint,
    should_refresh_plan_for_model,
    should_upgrade_plan_to_llm,
)
from study_app.core.dashboard import DashboardState, load_dashboard_state
from study_app.core.async_tasks import (
    capture_subject_revision,
    revalidate_subject_revision,
)
from study_app.core.budgeted_day_plan import BudgetPlanInput, BudgetedDayPlan, build_budgeted_day_plan
from study_app.core.day_budget_input import StudyDayBudget, validate_day_budget_input, validate_subject_exam_date
from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, build_plan_candidates
from study_app.core.practice_prompts import (
    build_mock_exam_generation_prompt,
    build_oj_practice_list,
    build_practice_generation_prompt,
)
from study_app.core.practice_spec import is_oj_plan_homework
from study_app.core.study_plan_feedback import (
    PlannedHomeworkScoreResult,
    plan_day_feedback_details,
    planned_homework_score_result,
)
from study_app.core.study_plan_items import (
    build_study_plan_items,
    humanize_plan_text,
    infer_subject_topic_from_plan_line,
    is_plan_homework_item,
    split_short_plan_item,
)
from study_app.core.task_estimates import (
    CONFIRMED_TEMPLATE_ESTIMATE,
    TaskEstimate,
    USER_ESTIMATE,
    make_assistant_task_estimate,
    make_task_estimate,
)
from study_app.data.database import (
    DEFAULT_DB_PATH,
    DatabaseNotInitializedError,
    add_learning_record,
    archive_active_study_plan,
    create_budgeted_day_plan,
    create_study_plan,
    delete_settings_by_prefix,
    get_active_study_plan,
    get_budgeted_day_plan,
    get_setting,
    get_study_day_budget,
    get_subject_exam_date,
    get_task_estimate,
    save_study_day_budget,
    save_subject_exam_date,
    save_task_estimate,
    set_setting,
    update_study_plan_item_state,
)
from study_app.ui.activity_view import (
    activity_subject_names, final_review_subject_names, plan_subject_scope_options,
)


_PLAN_GENERATION_WORKERS: set[object] = set()
_CHATGPT_BRIDGE_WORKERS: set[object] = set()
_CHATGPT_BRIDGE_LOCK = threading.Lock()
_CHATGPT_BRIDGE_SHUTDOWN_STARTED = False
LOGGER = logging.getLogger(__name__)


def _shutdown_chatgpt_bridge_workers() -> None:
    global _CHATGPT_BRIDGE_SHUTDOWN_STARTED
    with _CHATGPT_BRIDGE_LOCK:
        if _CHATGPT_BRIDGE_SHUTDOWN_STARTED:
            return
        workers = list(_CHATGPT_BRIDGE_WORKERS)
        _CHATGPT_BRIDGE_SHUTDOWN_STARTED = True

    deadline = time.monotonic() + 4.0
    cleanup_temp_artifacts = None
    try:
        from study_app.integrations.chatgpt_desktop_bridge import (
            cancel_chatgpt_pdf_generation,
            cleanup_chatgpt_temp_artifacts as cleanup_temp_artifacts,
            request_chatgpt_pdf_shutdown,
        )

        request_chatgpt_pdf_shutdown()
        result = cancel_chatgpt_pdf_generation(deadline=deadline)
        if not result.success:
            LOGGER.error("ChatGPT PDF shutdown cancel failed (%s)", result.code)
    except Exception as error:
        LOGGER.error(
            "ChatGPT PDF shutdown cancel raised (%s)",
            type(error).__name__,
        )
    finally:
        for worker in workers:
            remaining = min(2.0, max(0.0, deadline - time.monotonic()))
            remaining_ms = int(remaining * 1000)
            if remaining_ms <= 0 or not worker.wait(remaining_ms):
                LOGGER.error("ChatGPT PDF worker cleanup timed out")
        if cleanup_temp_artifacts is not None:
            try:
                cleanup_temp_artifacts()
            except Exception as error:
                LOGGER.error(
                    "ChatGPT temporary artifact cleanup failed (%s)",
                    type(error).__name__,
                )


def generate_local_practice_for_plan_line(line: str, subject_name: str):
    """Generate a local paper without invoking ChatGPT, a browser, or UI automation."""
    normalized_subject = str(subject_name or "").strip()
    if not normalized_subject:
        raise ValueError("生成本地练习卷前，请先选择一个具体学科。")
    if is_oj_plan_homework(line):
        raise ValueError("OJ 作业使用官方题目与测试集，不自动转换为本地练习卷。")
    from study_app.core.local_practice_service import (
        generate_local_practice_paper,
        spec_from_homework,
    )

    spec = spec_from_homework(line, subject=normalized_subject)
    return generate_local_practice_paper(spec)


def local_practice_success_message(result) -> str:
    return (
        "本地练习卷已生成，不依赖 ChatGPT 或网络。\n\n"
        f"题目卷：{result.question_pdf}\n"
        f"答案卷：{result.answer_pdf}\n"
        f"生成记录：{result.manifest_json}"
    )


@dataclass(frozen=True)
class _BudgetPlanDraft:
    budget: StudyDayBudget
    exam_dates: dict[str, str | None]
    estimates: dict[str, TaskEstimate]
    new_estimates: dict[str, TaskEstimate]
    plan: BudgetedDayPlan


@dataclass
class _HomeworkResultDraft:
    subject: str
    result_text: str
    item_id: int | None
    feedback_key: str
    feedback_snapshot: dict
    score_result: PlannedHomeworkScoreResult
    record: dict

    @property
    def fallback_hint(self) -> str:
        reason = self.score_result.fallback_reason
        return f"\n\n评分已回退到内置规则：{reason}。" if reason else ""


def _prepare_homework_result(
    line: str,
    is_correct: bool,
    item_id: int | None,
    state: DashboardState,
    subject_scope: str | None,
) -> _HomeworkResultDraft:
    """Capture the pre-result evidence and construct a learning record."""
    from datetime import date
    import re

    subject, topic = infer_subject_topic_from_plan_line(line, subject_scope)
    subject_before = next((item for item in state.subjects if item.name == subject), None)
    feedback_key = f"study_plan_item_feedback:{item_id}" if item_id is not None else ""
    feedback_snapshot = get_setting(feedback_key, {}) if feedback_key else {}
    if subject_before and not feedback_snapshot.get("before_captured"):
        feedback_snapshot.update(
            {
                "before_captured": True,
                "window_score_before": subject_before.window_score,
                "covered_mastery_before": subject_before.covered_mastery_score,
                "total_mastery_before": subject_before.mastery_score,
            }
        )
        set_setting(feedback_key, feedback_snapshot)
    difficulty_match = re.search(r"参考难度\s*(\d+(?:\.\d+)?)\s*/\s*100", line)
    difficulty_score = float(difficulty_match.group(1)) if difficulty_match else None
    score_result = planned_homework_score_result(difficulty_score, is_correct)
    result_text = "完成正确" if is_correct else "完成有误"
    record = {
        "date": date.today().isoformat(),
        "subject": subject,
        "module": None,
        "topic": topic,
        "activity": "review_exercise",
        "source": "outside_class",
        "score": score_result.score,
        "note": f"学习计划项{result_text}：{line}",
        "problems": [
            {
                "title": f"学习计划作业：{topic}",
                "statement": line,
                "answer_result": "全对" if is_correct else "错误",
                "status": "correct" if is_correct else "wrong",
                "correctness": 100 if is_correct else 0,
                "partial_credit": 1.0 if is_correct else 0.0,
                "error_cause": "" if is_correct else "学习计划作业完成有误，需要复盘错因",
                "related_topics": [topic],
                "difficulty_score": difficulty_score,
                "difficulty_source": "planned_reference" if difficulty_score is not None else "planned_homework",
            }
        ],
    }
    return _HomeworkResultDraft(
        subject, result_text, item_id, feedback_key, feedback_snapshot, score_result, record
    )


def _commit_homework_result(draft: _HomeworkResultDraft, is_correct: bool) -> None:
    add_learning_record(
        draft.record,
        study_plan_item_id=draft.item_id,
        study_plan_result="correct" if is_correct else "wrong",
    )


def _refresh_homework_feedback(draft: _HomeworkResultDraft) -> DashboardState:
    """Store the scoring provenance, then capture evidence after the record write."""
    if draft.feedback_key:
        draft.feedback_snapshot.update(
            {
                "result_score": draft.score_result.score,
                "score_source": draft.score_result.source,
                "score_fallback_reason": draft.score_result.fallback_reason,
            }
        )
        set_setting(draft.feedback_key, draft.feedback_snapshot)
    delete_settings_by_prefix("daily_summary_cache:")
    state = load_dashboard_state()
    subject_after = next((item for item in state.subjects if item.name == draft.subject), None)
    if draft.feedback_key and subject_after:
        draft.feedback_snapshot.update(
            {
                "window_score_after": subject_after.window_score,
                "covered_mastery_after": subject_after.covered_mastery_score,
                "total_mastery_after": subject_after.mastery_score,
            }
        )
        set_setting(draft.feedback_key, draft.feedback_snapshot)
    return state


def _prepare_budget_plan(
    state: DashboardState,
    subject_scope: str | None,
    raw_budget: str,
    exam_date_values: dict[str, str],
    estimate_minutes: dict[str, int],
    candidate_collection: CandidateCollection,
    candidate_by_id: dict[str, PlanCandidate],
    loaded_estimates: dict[str, TaskEstimate],
) -> _BudgetPlanDraft:
    """Validate a budget edit and build a draft before the page saves anything."""
    raw_budget = raw_budget.strip()
    if not raw_budget:
        raise ValueError("请填写今日可用时间（0–1440 分钟）")
    if not raw_budget.isdecimal() or len(raw_budget) > 4 or not 0 <= int(raw_budget) <= 1440:
        raise ValueError("今日可用时间须为 0–1440 的整数分钟")
    budget = validate_day_budget_input(state.today.isoformat(), int(raw_budget))
    exam_dates = {
        subject_name: validate_subject_exam_date(value.strip())
        for subject_name, value in exam_date_values.items()
    }
    estimates = dict(loaded_estimates)
    new_estimates = {}
    for task_id, minutes in estimate_minutes.items():
        if minutes > 0:
            existing = loaded_estimates.get(task_id)
            estimate = (
                existing
                if existing is not None and existing.estimated_minutes == minutes
                else make_task_estimate(candidate_by_id[task_id], minutes, USER_ESTIMATE)
            )
            estimates[task_id] = estimate
            if estimate is not existing:
                new_estimates[task_id] = estimate

    prior = get_budgeted_day_plan(state.today.isoformat())
    prior_done = {row["task_id"]: row for row in (prior or {}).get("items", []) if row["checked"]}
    if set(prior_done) - set(candidate_by_id):
        raise ValueError("已有完成任务不在当前推荐中。请保留当前计划，刷新数据核对后再调整。")
    enriched = tuple(
        replace(
            candidate,
            estimated_minutes=estimates[candidate.task_id].estimated_minutes,
            estimate_source=estimates[candidate.task_id].source,
            completion_state="checked" if candidate.task_id in prior_done else candidate.completion_state,
        )
        if candidate.task_id in estimates else candidate
        for candidate in candidate_collection.candidates
    )
    plan = build_budgeted_day_plan(
        BudgetPlanInput(
            budget.plan_date,
            budget.available_minutes,
            activity_subject_names(state),
            subject_scope,
            exam_dates,
            budget.plan_date,
        ),
        CandidateCollection(enriched, candidate_collection.unmapped),
    )
    return _BudgetPlanDraft(budget, exam_dates, estimates, new_estimates, plan)


def _preview_budget_plan(draft: _BudgetPlanDraft) -> dict:
    plan = draft.plan
    rows = []
    for section, decisions in (
        ("selected", plan.selected),
        ("completed", plan.completed),
        ("excluded", plan.excluded),
    ):
        for decision in decisions:
            rows.append(
                {
                    "id": -(len(rows) + 1),
                    "item_text": decision.title,
                    "estimated_minutes": decision.estimated_minutes,
                    "checked": section == "completed",
                    "excluded_reason": decision.rule_code if section == "excluded" else None,
                    "selection_reason": {
                        "reason": decision.reason,
                        "ranking_evidence": dict(decision.ranking_evidence),
                    },
                }
            )
    return {
        "summary": {
            "planned_minutes": plan.planned_minutes,
            "remaining_minutes": plan.remaining_minutes,
            "over_budget_completed_minutes": plan.over_budget_completed_minutes,
            "completed_occupancy_unknown": plan.completed_occupancy_unknown,
        },
        "items": rows,
    }


def _save_budget_plan(
    draft: _BudgetPlanDraft,
    state: DashboardState,
    candidate_by_id: dict[str, PlanCandidate],
) -> None:
    """Persist a confirmed budget draft and its supporting inputs."""
    budget = draft.budget
    save_study_day_budget(budget.plan_date, budget.available_minutes)
    for subject_name, exam_date in draft.exam_dates.items():
        save_subject_exam_date(state, subject_name, exam_date)
    for task_id, estimate in draft.new_estimates.items():
        save_task_estimate(
            state, candidate_by_id[task_id], estimate.estimated_minutes, estimate.source
        )
    create_budgeted_day_plan(budget.plan_date, budget.available_minutes, draft.plan)


def _reusable_active_plan(
    state: DashboardState, subject_name: str | None, force_regenerate: bool
) -> dict | None:
    active_plan = get_active_study_plan(subject_name)
    if active_plan and not (
        force_regenerate
        or should_refresh_plan_for_model(active_plan, state, subject_name)
        or should_upgrade_plan_to_llm(active_plan)
    ):
        return active_plan
    return None


def _save_generated_plan(
    plan: dict,
    subject_name: str | None,
    generation_date,
    input_signature: str,
    revision_token,
) -> None:
    revalidate_subject_revision(revision_token)
    create_study_plan(
        subject_name,
        generation_date.isoformat(),
        generation_date.isoformat(),
        input_signature,
        plan,
        build_study_plan_items(plan),
    )
    delete_settings_by_prefix("daily_summary_cache:")


def _apply_final_review_modes(initial_states: dict[str, bool], selected_states: dict[str, bool]) -> list[str]:
    from study_app.core.study_phase import set_subject_phase

    changed = []
    for subject_name, enabled in selected_states.items():
        if enabled == initial_states[subject_name]:
            continue
        set_subject_phase(subject_name, "final_review" if enabled else "regular")
        archive_active_study_plan(subject_name)
        changed.append(subject_name)
    if changed:
        archive_active_study_plan(None)
        delete_settings_by_prefix("daily_summary_cache:")
    return changed


@dataclass(frozen=True)
class _CompletedDayFeedback:
    messages: tuple[str, ...]
    shown_key: str
    previous_markers: frozenset[str]
    current_markers: frozenset[str]


def _completed_day_feedback(
    saved_plan: dict, state: DashboardState, subject_scope: str | None
) -> _CompletedDayFeedback:
    shown_key = "study_plan_day_feedback_shown"
    shown = set(get_setting(shown_key, []) or [])
    new_shown = set(shown)
    messages = []
    for feedback in plan_day_feedback_details(saved_plan, state, subject_scope):
        marker = f"{saved_plan['id']}:{feedback['day_index']}"
        if not feedback["complete"] or marker in shown:
            continue
        messages.append(feedback["popup"])
        new_shown.add(marker)
    return _CompletedDayFeedback(
        tuple(messages), shown_key, frozenset(shown), frozenset(new_shown)
    )


def _save_completed_day_feedback_markers(feedback: _CompletedDayFeedback) -> None:
    if feedback.current_markers != feedback.previous_markers:
        set_setting(feedback.shown_key, sorted(feedback.current_markers))


def _set_plan_item_checked(item_id: int, checked: bool) -> None:
    update_study_plan_item_state(item_id, checked=checked)


def _start_plan_generation_state() -> DashboardState:
    delete_settings_by_prefix("daily_summary_cache:")
    return load_dashboard_state()


def _archive_current_plan(subject_scope: str | None) -> None:
    archive_active_study_plan(subject_scope)
    delete_settings_by_prefix("daily_summary_cache:")


class _PlanGenerationSession:
    """Own one page's worker, watchdog, and stale-result token."""

    def __init__(self, page, worker, relay_type, on_completed, on_failed, on_timeout):
        from PySide6.QtCore import QTimer

        self.page = page
        self.worker = worker
        self.token = object()
        self.on_completed = on_completed
        self.on_failed = on_failed
        self.on_timeout = on_timeout
        page._plan_generation_token = self.token
        page._plan_worker = worker
        page.destroyed.connect(self._page_destroyed)

        self.watchdog = QTimer(page)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self._timeout)
        page._plan_worker_watchdog = self.watchdog
        self.relay = relay_type(
            on_completed=self._completed,
            on_failed=self._failed,
            on_finished=self._finished,
            parent=page,
        )
        worker.completed.connect(self.relay.completed)
        worker.failed.connect(self.relay.failed)
        worker.finished.connect(self.relay.finished)

    def _is_current(self):
        return getattr(self.page, "_plan_generation_token", None) is self.token

    def _clear(self):
        if not self._is_current():
            return
        self.page._plan_worker = None
        self.page._plan_generation_token = None
        self.page._plan_worker_watchdog = None
        self.watchdog.stop()
        self.watchdog.deleteLater()

    def _page_destroyed(self):
        self.page._plan_worker = None
        self.page._plan_generation_token = None
        self.page._plan_worker_watchdog = None

    def _completed(self, plan, source):
        if self._is_current():
            self._clear()
            self.on_completed(plan, source)

    def _failed(self, error):
        if self._is_current():
            self._clear()
            self.on_failed(error)

    def _timeout(self):
        if self._is_current():
            self.worker.requestInterruption()
            self._clear()
            self.on_timeout()

    def _finished(self):
        self._clear()

    def start(self):
        self.watchdog.start(90000)
        try:
            self.worker.start()
        except Exception as error:
            self._clear()
            self.relay.deleteLater()
            self.on_failed(str(error))
            return False
        return True


class _ChatGPTPDFSession:
    """Own the PDF worker UI state and ignore callbacks from stale attempts."""

    def __init__(self, page, progress_label, stop_button, relay_type, thread_type):
        self.page = page
        self.progress_label = progress_label
        self.stop_button = stop_button
        self.relay_type = relay_type
        self.thread_type = thread_type

    def set_busy(self, busy: bool):
        self.stop_button.setVisible(busy)
        self.stop_button.setDisabled(not busy)

    def _watch_global_worker(self, worker):
        def release_busy():
            if getattr(self.page, "_chatgpt_bridge_page_alive", False):
                self.set_busy(False)

        relay = self.relay_type(on_finished=release_busy, parent=self.page)
        worker.finished.connect(relay.finished)
        if worker.isFinished():
            relay.finished()

    def stop(self):
        from PySide6.QtWidgets import QMessageBox

        with _CHATGPT_BRIDGE_LOCK:
            workers = list(_CHATGPT_BRIDGE_WORKERS)
        for worker in workers:
            if worker.isRunning():
                try:
                    result = worker.cancel()
                except Exception:
                    QMessageBox.warning(
                        self.page,
                        "停止 PDF 生成失败",
                        "未能停止当前 PDF 生成任务，请稍后重试。",
                    )
                    return
                if not result.success:
                    QMessageBox.warning(
                        self.page,
                        "停止 PDF 生成失败",
                        "未能停止当前 PDF 生成任务，请稍后重试。",
                    )
                    return
        for worker in workers:
            worker.cancelled = True
        self.set_busy(False)
        self.progress_label.setText("已手动停止 ChatGPT PDF 生成，并清理临时提示词。")
        QMessageBox.information(self.page, "已停止", "已停止当前 ChatGPT PDF 生成流程。")

    def _detach(self, worker, token):
        workers = getattr(self.page, "_chatgpt_bridge_threads", [])
        if worker in workers:
            workers.remove(worker)
        if not getattr(self.page, "_chatgpt_bridge_page_alive", False):
            return False
        is_current = getattr(self.page, "_chatgpt_pdf_active_token", None) is token
        if is_current and not any(item.isRunning() for item in workers):
            self.set_busy(False)
        return is_current

    def _completed(self, result, original_prompt, worker, token):
        from PySide6.QtWidgets import QApplication, QMessageBox

        if not self._detach(worker, token):
            return
        if worker.cancelled:
            self.progress_label.setText("ChatGPT PDF 生成已手动停止。")
            return
        if result.success:
            if result.code != "pdf_opened":
                self.progress_label.setText("PDF 已下载，但未能自动打开。")
                QMessageBox.warning(
                    self.page,
                    "PDF 已下载但未打开",
                    f"PDF 已安全下载，但系统未能自动打开文件。\n\n文件位置：\n{result.pdf_path}",
                )
                return
            self.progress_label.setText("ChatGPT 已生成 PDF，并已自动打开。")
            QMessageBox.information(
                self.page,
                "PDF 已生成",
                f"ChatGPT 已完成生成，PDF 已归档并自动打开。\n\n临时文件位置：\n{result.pdf_path}",
            )
            return
        QApplication.clipboard().setText(original_prompt)
        self.progress_label.setText("ChatGPT 自动生成 PDF 失败，提示词已复制。")
        QMessageBox.warning(
            self.page,
            "ChatGPT 自动生成失败",
            "ChatGPT 未能完成 PDF 生成。为避免丢失，出题提示词已复制到剪贴板。",
        )

    def _failed(self, _error, worker, token):
        from PySide6.QtWidgets import QMessageBox

        if not self._detach(worker, token):
            return
        if worker.cancelled:
            self.progress_label.setText("ChatGPT PDF 生成已手动停止。")
            return
        self.progress_label.setText("ChatGPT 自动生成 PDF 失败，请检查桌面端状态后重试。")
        QMessageBox.warning(
            self.page,
            "ChatGPT 自动生成失败",
            "ChatGPT 桌面桥接发生错误，请检查桌面端状态后重试。",
        )

    def _show_existing_worker(self, worker):
        from PySide6.QtWidgets import QMessageBox

        self.set_busy(True)
        if worker is not None:
            self._watch_global_worker(worker)
        QMessageBox.information(
            self.page,
            "正在生成 PDF",
            "已有一个 ChatGPT PDF 生成任务正在运行。你可以点击“停止 PDF 生成”后再启动新的任务。",
        )

    def start(self, prompt: str):
        from PySide6.QtWidgets import QMessageBox

        with _CHATGPT_BRIDGE_LOCK:
            active_threads = list(_CHATGPT_BRIDGE_WORKERS)
        if active_threads:
            self._show_existing_worker(active_threads[0])
            return
        task_token = object()
        self.page._chatgpt_pdf_active_token = task_token
        self.set_busy(True)
        self.progress_label.setText("正在让 ChatGPT 生成 PDF；生成期间可以继续使用学习应用...")
        thread = self.thread_type(prompt, self.page)
        thread.task_token = task_token
        bridge_threads = getattr(self.page, "_chatgpt_bridge_threads", [])
        bridge_threads.append(thread)
        self.page._chatgpt_bridge_threads = bridge_threads
        relay = self.relay_type(
            on_completed=lambda result, original_prompt: self._completed(
                result, original_prompt, thread, task_token
            ),
            on_failed=lambda error: self._failed(error, thread, task_token),
            parent=self.page,
        )
        thread.completed.connect(relay.completed)
        thread.failed.connect(relay.failed)
        thread.finished.connect(relay.finished)
        try:
            started = thread.start()
        except Exception:
            bridge_threads.remove(thread)
            relay.deleteLater()
            self.set_busy(False)
            QMessageBox.warning(
                self.page,
                "启动 PDF 生成失败",
                "未能启动 PDF 生成任务，请稍后重试。",
            )
            return
        if not started:
            bridge_threads.remove(thread)
            relay.deleteLater()
            with _CHATGPT_BRIDGE_LOCK:
                active_threads = list(_CHATGPT_BRIDGE_WORKERS)
            self._show_existing_worker(active_threads[0] if active_threads else None)


def _budget_plan_row(row: dict, *, preview: bool, on_check):
    """Build one budgeted task row and its optional evidence disclosure."""
    from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

    completed = bool(row["checked"])
    excluded = bool(row["excluded_reason"])
    status = "已完成" if completed else "未安排" if excluded else "待执行"
    minutes = f" · {row['estimated_minutes']} 分钟" if row["estimated_minutes"] else ""
    reason = row["selection_reason"].get("reason") or row["excluded_reason"] or ""
    if excluded:
        label = QLabel(f"{status} · {row['item_text']}{minutes} · {reason}")
        label.setObjectName("BudgetExcludedItem")
        label.setWordWrap(True)
        return label, None

    from study_app.ui.design_components import disclosure

    task = QWidget()
    task_layout = QVBoxLayout(task)
    task_layout.setContentsMargins(0, 8, 0, 8)
    task_heading = QLabel(f"{status} · {row['item_text']}{minutes}")
    task_heading.setWordWrap(True)
    task_heading.setObjectName("ListTitle")
    task_layout.addWidget(task_heading)
    detail = QLabel(reason)
    detail.setWordWrap(True)
    detail.setObjectName("Muted")
    evidence = (row.get("selection_reason") or {}).get("ranking_evidence") or {}
    evidence_labels = {
        "priority": "模型排序分值",
        "forgetting_risk": "遗忘风险权重",
        "mastery_gap": "掌握缺口权重",
        "coverage_value": "覆盖权重",
        "exam_date": "考试日期",
        "estimate_source": "估时来源",
    }

    def evidence_value(key, value):
        if key == "estimate_source":
            return {
                "confirmed_template": "规则估时",
                "user": "手动估时",
                "user_estimate": "手动估时",
            }.get(value, value)
        return f"{value:.3f}" if isinstance(value, float) else str(value)

    extra = [
        f"{label}：{evidence_value(key, evidence[key])}"
        for key, label in evidence_labels.items()
        if evidence.get(key) is not None
    ]
    detail.setText(reason + ("\n" + "；".join(extra) if extra else ""))
    details = disclosure("查看依据", detail)
    task_layout.addWidget(details)
    checkbox = QCheckBox("已完成" if completed else "标记完成")
    checkbox.setAccessibleName(f"完成任务：{row['item_text']}")
    checkbox.setObjectName("BudgetPlanItem")
    checkbox.setProperty("itemId", row["id"])
    checkbox.setChecked(completed)
    checkbox.setEnabled(not preview)
    if preview:
        checkbox.setToolTip("确认安排后可记录完成状态")
    checkbox.stateChanged.connect(
        lambda value, item_id=row["id"]: on_check(item_id, bool(value))
    )
    task_layout.addWidget(checkbox)
    return task, details


@lru_cache(maxsize=1)
def _worker_types():
    """Build Qt worker and relay types once, when the first plan page opens."""
    from PySide6.QtCore import QObject, Signal, Slot

    class WorkerSignalRelay(QObject):
        def __init__(
            self,
            *,
            on_completed=None,
            on_failed=None,
            on_finished=None,
            parent=None,
        ):
            super().__init__(parent)
            self._on_completed = on_completed
            self._on_failed = on_failed
            self._on_finished = on_finished
            self._finished_called = False

        @Slot(object, str)
        def completed(self, result, source):
            if self._on_completed is not None:
                self._on_completed(result, source)

        @Slot(str)
        def failed(self, error):
            if self._on_failed is not None:
                self._on_failed(error)

        @Slot()
        def finished(self):
            if self._finished_called:
                return
            self._finished_called = True
            if self._on_finished is not None:
                self._on_finished()
            self.deleteLater()

    class PlanGenerationThread(QObject):
        completed = Signal(object, str)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, dashboard_state, subject_name, revision_token=None, parent=None):
            super().__init__(None)
            self.dashboard_state = dashboard_state
            self.subject_name = subject_name
            self.revision_token = revision_token
            self._finished = threading.Event()
            self._interruption_requested = threading.Event()
            self._thread = None

        def start(self):
            if self.isRunning():
                return
            self._finished.clear()
            self._interruption_requested.clear()
            _PLAN_GENERATION_WORKERS.add(self)
            try:
                self._thread = threading.Thread(
                    target=self.run,
                    name="study-plan-generation",
                    daemon=True,
                )
                self._thread.start()
            except Exception:
                _PLAN_GENERATION_WORKERS.discard(self)
                self._thread = None
                self._finished.set()
                raise

        def isRunning(self):
            return self._thread is not None and self._thread.is_alive()

        def isFinished(self):
            return self._finished.is_set()

        def wait(self, timeout=None):
            seconds = None if timeout is None else max(0, timeout) / 1000
            return self._finished.wait(seconds)

        def requestInterruption(self):
            self._interruption_requested.set()

        def isInterruptionRequested(self):
            return self._interruption_requested.is_set()

        def run(self):
            try:
                if self.revision_token is None:
                    plan, source = generate_study_plan_with_optional_llm(
                        self.dashboard_state,
                        self.subject_name,
                    )
                else:
                    plan, source = generate_study_plan_with_optional_llm(
                        self.dashboard_state,
                        self.subject_name,
                        revision_token=self.revision_token,
                    )
                self.completed.emit(plan, source)
            except Exception as error:
                self.failed.emit(str(error))
            finally:
                _PLAN_GENERATION_WORKERS.discard(self)
                self._finished.set()
                self.finished.emit()

    class ChatGPTBridgeThread(QObject):
        completed = Signal(object, str)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, prompt, parent=None):
            super().__init__(None)
            self.prompt = prompt
            self.cancelled = False
            self._finished = threading.Event()
            self._thread = None

        def start(self):
            if self.isRunning():
                return False
            with _CHATGPT_BRIDGE_LOCK:
                if _CHATGPT_BRIDGE_SHUTDOWN_STARTED or _CHATGPT_BRIDGE_WORKERS:
                    return False
                _CHATGPT_BRIDGE_WORKERS.add(self)
            self._finished.clear()
            try:
                self._thread = threading.Thread(
                    target=self.run,
                    name="chatgpt-pdf-generation",
                    daemon=True,
                )
                self._thread.start()
            except Exception:
                with _CHATGPT_BRIDGE_LOCK:
                    _CHATGPT_BRIDGE_WORKERS.discard(self)
                self._thread = None
                self._finished.set()
                raise
            return True

        def isRunning(self):
            return self._thread is not None and self._thread.is_alive()

        def isFinished(self):
            return self._finished.is_set()

        def wait(self, timeout=None):
            seconds = None if timeout is None else max(0, timeout) / 1000
            return self._finished.wait(seconds)

        def run(self):
            from study_app.integrations.chatgpt_desktop_bridge import generate_pdf_with_chatgpt

            try:
                self.completed.emit(generate_pdf_with_chatgpt(self.prompt), self.prompt)
            except Exception as error:
                self.failed.emit(str(error))
            finally:
                with _CHATGPT_BRIDGE_LOCK:
                    _CHATGPT_BRIDGE_WORKERS.discard(self)
                self._finished.set()
                self.finished.emit()

        def cancel(self):
            from study_app.integrations.chatgpt_desktop_bridge import cancel_chatgpt_pdf_generation

            return cancel_chatgpt_pdf_generation()

    return WorkerSignalRelay, PlanGenerationThread, ChatGPTBridgeThread


def _edit_final_review_modes(parent, state: DashboardState) -> list[str] | None:
    from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
    from study_app.core.study_phase import exam_scope_label, is_final_review

    dialog = QDialog(parent)
    dialog.setWindowTitle("管理期末复习模式")
    dialog.setMinimumWidth(520)
    dialog_layout = QVBoxLayout(dialog)
    dialog_layout.setContentsMargins(24, 22, 24, 22)
    dialog_layout.setSpacing(14)

    dialog_title = QLabel("选择进入期末复习阶段的学科")
    dialog_title.setObjectName("SectionTitle")
    dialog_hint = QLabel(
        "启用后，该学科仍使用统一的加权优先级排序，但掌握度缺口权重会提高，"
        "并增加跨章节综合练习比例；无题目证据的知识点按初始掌握度参与排序。"
    )
    dialog_hint.setObjectName("Muted")
    dialog_hint.setWordWrap(True)
    dialog_layout.addWidget(dialog_title)
    dialog_layout.addWidget(dialog_hint)

    phase_checks = {}
    initial_states = {}
    for subject_name in final_review_subject_names(state):
        checked = is_final_review(subject_name)
        initial_states[subject_name] = checked
        scope_label = exam_scope_label(subject_name)
        checkbox = QCheckBox(
            f"{subject_name}（考试范围：{scope_label}）"
            if scope_label
            else subject_name
        )
        checkbox.setChecked(checked)
        phase_checks[subject_name] = checkbox
        dialog_layout.addWidget(checkbox)

    button_row = QHBoxLayout()
    cancel_button = QPushButton("取消")
    cancel_button.setObjectName("GhostButton")
    save_button = QPushButton("保存设置")
    save_button.setObjectName("PrimaryButton")
    button_row.addStretch()
    button_row.addWidget(cancel_button)
    button_row.addWidget(save_button)
    dialog_layout.addLayout(button_row)

    cancel_button.clicked.connect(dialog.reject)

    def save_modes():
        _apply_final_review_modes(
            initial_states,
            {name: checkbox.isChecked() for name, checkbox in phase_checks.items()},
        )
        dialog.accept()

    save_button.clicked.connect(save_modes)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return

    changed_text = [
        f"{name}：{'已启用' if phase_checks[name].isChecked() else '已退出'}"
        for name in phase_checks
        if phase_checks[name].isChecked() != initial_states[name]
    ]
    return changed_text


class _BudgetPlanPanel:
    """今日预算、估时输入以及预览确认流程。"""

    def __init__(self, page):
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget
        self.page = page
        self.budget_toggle = QPushButton("考试日期与单项估时 · 展开")
        self.budget_toggle.setObjectName("BudgetOptionsToggle")
        self.budget_toggle.setCheckable(True)
        self.budget_toggle.setChecked(False)
        self.budget_toggle.setToolTip("查看考试日期与每项任务估时；修改后需重新预览并确认。")
        self.widget = QWidget()
        self.widget.setObjectName("Card")
        budget_layout = QVBoxLayout(self.widget)
        budget_layout.setContentsMargins(18, 16, 18, 16)
        budget_layout.setSpacing(10)
        budget_title = QLabel("今天能学多久")
        budget_title.setObjectName("CardTitle")
        budget_layout.addWidget(budget_title)
        budget_row = QHBoxLayout()
        minutes_label = QLabel("今日可用时间（分钟）")
        budget_row.addWidget(minutes_label)
        self.budget_minutes_input = QLineEdit()
        self.budget_minutes_input.setObjectName("BudgetMinutesInput")
        self.budget_minutes_input.setPlaceholderText("0～1440")
        minutes_label.setBuddy(self.budget_minutes_input)
        self.budget_minutes_input.setAccessibleName("今日可用时间（分钟）")
        budget_row.addWidget(self.budget_minutes_input)
        budget_row.addStretch()
        budget_layout.addLayout(budget_row)
        self.load_label = QLabel("可用时间未设置；请输入 0–1440 分钟。")
        self.load_label.setObjectName("PlanLoadPreview")
        self.load_label.setWordWrap(True)
        budget_layout.addWidget(self.load_label)
        budget_layout.addWidget(self.budget_toggle)
        self.options_panel = QWidget()
        options_layout = QVBoxLayout(self.options_panel)
        options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_panel.hide()
        budget_layout.addWidget(self.options_panel)
        self.exam_inputs: dict[str, QLineEdit] = {}
        for subject_name in activity_subject_names(self.page.state):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{subject_name}考试日期"))
            field = QLineEdit()
            field.setObjectName("SubjectExamDateInput")
            field.setProperty("subjectId", subject_name)
            field.setPlaceholderText("可空；YYYY-MM-DD")
            row.addWidget(field)
            row.addStretch()
            options_layout.addLayout(row)
            self.exam_inputs[subject_name] = field
        self.candidate_collection = build_plan_candidates(self.page.state)
        self.estimate_inputs: dict[str, QSpinBox] = {}
        self.candidate_by_id = {candidate.task_id: candidate for candidate in self.candidate_collection.candidates}
        for candidate in self.candidate_collection.candidates:
            row = QHBoxLayout()
            task_label = QLabel(f"{candidate.subject_id} · {candidate.title}")
            task_label.setWordWrap(True)
            row.addWidget(task_label, 1)
            field = QSpinBox()
            field.setObjectName("TaskEstimateInput")
            field.setProperty("taskId", candidate.task_id)
            field.setRange(1, 1440)
            field.setSuffix(" 分钟 · 助理估时")
            row.addWidget(field)
            row.addStretch()
            options_layout.addLayout(row)
            self.estimate_inputs[candidate.task_id] = field
        self.budget_apply_button = QPushButton("预览今日安排")
        self.budget_apply_button.setObjectName("PrimaryButton")
        class BudgetStatusLabel(QLabel):
            def setText(self, text):
                super().setText(text)
                self.setVisible(bool(text))
        self.budget_status = BudgetStatusLabel()
        self.budget_status.setObjectName("StatusLabel")
        self.budget_status.setWordWrap(True)
        budget_actions = QHBoxLayout()
        budget_actions.addWidget(self.budget_apply_button)
        self.confirm_budget_button = QPushButton("确认安排")
        self.confirm_budget_button.setObjectName("ConfirmBudgetPlan")
        self.confirm_budget_button.setEnabled(False)
        self.confirm_budget_button.setToolTip("先预览安排，再确认保存")
        budget_actions.addWidget(self.confirm_budget_button)
        budget_actions.addStretch()
        budget_layout.addLayout(budget_actions)
        self.preview_signature = None
        self.task_rows = {}
        budget_layout.addWidget(self.budget_status)
        budget_result_host = QWidget()
        self.budget_result_layout = QVBoxLayout(budget_result_host)
        self.budget_result_layout.setContentsMargins(0, 0, 0, 0)
        self.budget_result_layout.setSpacing(8)
        budget_layout.addWidget(budget_result_host)
        self.widget.setVisible(True)
        self.page.content._budget_options_panel = self.options_panel
        self.page.content._budget_options_toggle = self.budget_toggle
        self.budget_toggle.toggled.connect(self.toggle_budget_panel)
        self._load_saved_inputs()
        self._connect_inputs()

    def _load_saved_inputs(self):
        self.budget_schema_ready = True
        self.loaded_estimates = {}
        try:
            saved_budget = get_study_day_budget(self.page.state.today.isoformat())
            if saved_budget is not None:
                self.budget_minutes_input.setText(str(saved_budget.available_minutes))
            for subject_name, field in self.exam_inputs.items():
                saved_date = get_subject_exam_date(subject_name)
                field.setText(saved_date or "")
            for task_id, candidate in self.candidate_by_id.items():
                estimate = get_task_estimate(candidate)
                if estimate is None:
                    estimate = make_assistant_task_estimate(candidate)
                self.loaded_estimates[task_id] = estimate
                field = self.estimate_inputs[task_id]
                field.setValue(estimate.estimated_minutes)
                if estimate.source == USER_ESTIMATE:
                    field.setSuffix(" 分钟 · 已保存")
                elif estimate.source == CONFIRMED_TEMPLATE_ESTIMATE:
                    field.setSuffix(" 分钟 · 助理估时")
            get_budgeted_day_plan(self.page.state.today.isoformat())
        except DatabaseNotInitializedError:
            self.budget_schema_ready = False
            self.budget_status.setText("时间预算暂不可用。请在设置中检查数据结构，已有学习记录会保留。")
            self.budget_apply_button.setDisabled(True)

    def _connect_inputs(self):
        for task_id, field in self.estimate_inputs.items():
            field.valueChanged.connect(
                lambda value, current_task_id=task_id: self.mark_manual_estimate(current_task_id, value)
            )
        self.budget_apply_button.clicked.connect(lambda _checked=False: self.generate_budgeted_plan(preview_only=True))
        self.confirm_budget_button.clicked.connect(lambda _checked=False: self.generate_budgeted_plan(require_preview=True))
        for field in [self.budget_minutes_input, *self.exam_inputs.values()]:
            field.textChanged.connect(self.invalidate_preview)
        for field in self.estimate_inputs.values():
            field.valueChanged.connect(self.invalidate_preview)
        self.invalidate_preview()
        if self.budget_schema_ready:
            self.budget_status.setText("")

    def toggle_budget_panel(self, expanded: bool):
        self.options_panel.setVisible(expanded)
        self.budget_toggle.setText(
            "考试日期与单项估时 · 收起" if expanded else "考试日期与单项估时 · 展开"
        )

    def clear_budget_results(self):
        self.task_rows.clear()
        while self.budget_result_layout.count():
            item = self.budget_result_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def render_budgeted_plan(self, saved_plan=None, *, preview=False):
        from PySide6.QtWidgets import QLabel
        self.clear_budget_results()
        if not self.budget_schema_ready:
            return
        if saved_plan is None:
            saved_plan = get_budgeted_day_plan(
                self.page.state.today.isoformat(), self.page.subject_scope.currentData() or None
            )
        if not saved_plan:
            note = QLabel("暂无今日安排")
            note.setObjectName("Muted")
            self.budget_result_layout.addWidget(note)
            return
        summary = saved_plan["summary"]
        summary_text = (
            f"已安排 {summary['planned_minutes']} 分钟；剩余 {summary['remaining_minutes']} 分钟"
        )
        summary_text = ("未保存预览 · " if preview else f"已保存预算 {saved_plan.get('budget_minutes', '未知')} 分钟 · ") + summary_text
        if summary.get("over_budget_completed_minutes"):
            summary_text += f"；已完成超出新预算 {summary['over_budget_completed_minutes']} 分钟"
        if summary.get("completed_occupancy_unknown"):
            summary_text += "；存在完成占用未知项，补估时前不新增任务"
        summary_label = QLabel(summary_text)
        summary_label.setObjectName("BudgetSummaryLabel")
        summary_label.setWordWrap(True)
        self.budget_result_layout.addWidget(summary_label)
        if not saved_plan["items"]:
            empty = QLabel("当前学科暂无任务")
            empty.setWordWrap(True)
            self.budget_result_layout.addWidget(empty)
        active_rows = [r for r in saved_plan["items"] if not r["excluded_reason"]]
        if active_rows and all(r["checked"] for r in active_rows):
            self.budget_result_layout.addWidget(QLabel("今日任务已完成"))

        def update_budget_check(item_id, checked):
            _set_plan_item_checked(item_id, checked)
            self.invalidate_preview()
            self.render_budgeted_plan()
            self.budget_status.setText("完成状态已保存")

        for row in saved_plan["items"]:
            widget, details = _budget_plan_row(
                row, preview=preview, on_check=update_budget_check
            )
            self.budget_result_layout.addWidget(widget)
            if details is not None:
                self.task_rows[row["id"]] = (widget, details)

    def invalidate_preview(self, *_args):
        self.preview_signature = None
        self.confirm_budget_button.setEnabled(False)
        if not self.budget_schema_ready:
            return
        raw = self.budget_minutes_input.text().strip()
        total = sum(field.value() for field in self.estimate_inputs.values())
        try:
            available = int(raw)
            if not 0 <= available <= 1440:
                raise ValueError()
            delta = total - available
            self.load_label.setText(f"候选 {total} 分钟 · 预算 {available} 分钟 · " +
                              (f"超出 {delta} 分钟" if delta > 0 else f"余量 {abs(delta)} 分钟"))
        except ValueError:
            self.load_label.setText(f"候选 {total} 分钟 · 预算请输入 0–1440 分钟")
        self.budget_status.setText("输入已变更，请重新预览")

    def generate_budgeted_plan(self, *, preview_only=False, require_preview=False):
        from PySide6.QtCore import QTimer
        scroll_position = self.page.scroll.verticalScrollBar().value()
        try:
            draft = _prepare_budget_plan(
                self.page.state,
                self.page.subject_scope.currentData() or None,
                self.budget_minutes_input.text(),
                {name: field.text() for name, field in self.exam_inputs.items()},
                {task_id: field.value() for task_id, field in self.estimate_inputs.items()},
                self.candidate_collection,
                self.candidate_by_id,
                self.loaded_estimates,
            )
            result = draft.plan
            if preview_only:
                self.preview_signature = result.input_signature
                self.render_budgeted_plan(_preview_budget_plan(draft), preview=True)
                self.budget_status.setText(
                    f"预览：安排 {len(result.selected)} 项，保留已完成 {len(result.completed)} 项，"
                    f"未安排 {len(result.excluded)} 项。确认后保存。"
                )
                self.confirm_budget_button.setEnabled(True)
                QTimer.singleShot(0, lambda: self.page.scroll.verticalScrollBar().setValue(scroll_position))
                return
            if require_preview and self.preview_signature != result.input_signature:
                self.invalidate_preview()
                self.budget_status.setText("任务状态已变化，请重新预览后确认。")
                return
            _save_budget_plan(draft, self.page.state, self.candidate_by_id)
            self.loaded_estimates = draft.estimates
            self.preview_signature = None
            self.budget_apply_button.setFocus()
            self.confirm_budget_button.setEnabled(False)
            self.render_budgeted_plan()
            self.budget_status.setText("今日安排已保存")
            QTimer.singleShot(0, lambda: self.page.scroll.verticalScrollBar().setValue(scroll_position))
        except Exception as error:
            self.budget_status.setText(f"时间预算计划未保存：{error}")
            self.confirm_budget_button.setEnabled(False)
            self.preview_signature = None

    def mark_manual_estimate(self, task_id: str, value: int):
        existing = self.loaded_estimates.get(task_id)
        field = self.estimate_inputs[task_id]
        if existing is None or existing.estimated_minutes != value:
            field.setSuffix(" 分钟 · 手动")
        elif existing.source == USER_ESTIMATE:
            field.setSuffix(" 分钟 · 已保存")
        else:
            field.setSuffix(" 分钟 · 助理估时")


class _DetailedPlanPanel:
    """详细计划展示、完成反馈和出卷操作。"""

    def __init__(self, page):
        from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget
        self.page = page
        self._build_tools()

        self.phase_status = QLabel()
        self.phase_status.setObjectName("StatusLabel")
        self.phase_status.setWordWrap(True)
        self.layout.addWidget(self.phase_status)
        self.plan_host = QWidget()
        self.plan_layout = QVBoxLayout(self.plan_host)
        self.plan_layout.setContentsMargins(0, 0, 0, 0)
        self.plan_layout.setSpacing(14)
        self.progress_label = QLabel("计划进度：尚未生成")
        self.progress_label.setObjectName("StatusLabel")
        self.progress_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.layout.addWidget(self.progress_label)
        self.layout.addWidget(self.progress_bar)
        placeholder = QLabel("暂无计划")
        placeholder.setObjectName("Muted")
        placeholder.setWordWrap(True)
        self.plan_layout.addWidget(placeholder)
        self.layout.addWidget(self.plan_host)
        self.pdf_session = _ChatGPTPDFSession(
            self.page.content, self.progress_label, self.stop_pdf_button, self.page.WorkerSignalRelay, self.page.ChatGPTBridgeThread
        )
        self.page.content._chatgpt_pdf_session = self.pdf_session
        self.stop_pdf_button.clicked.connect(self.pdf_session.stop)

    def _build_tools(self):
        from PySide6.QtWidgets import QComboBox, QGridLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

        actions = QGridLayout()
        actions.setHorizontalSpacing(10)
        actions.setVerticalSpacing(10)
        for column in range(4):
            actions.setColumnStretch(column, 1)
        self.generate_button = QPushButton("生成今日计划")
        self.generate_button.setObjectName("PrimaryButton")
        self.final_review_button = QPushButton("期末复习模式")
        self.final_review_button.setObjectName("GhostButton")
        self.clear_button = QPushButton("清空计划")
        self.clear_button.setObjectName("GhostButton")
        self.copy_context_button = QPushButton("复制题库上下文")
        self.copy_context_button.setObjectName("GhostButton")
        self.mock_exam_mode = QComboBox()
        self.mock_exam_mode.addItem("诊断卷", "diagnostic")
        self.mock_exam_mode.addItem("标准期末卷", "standard")
        self.mock_exam_mode.addItem("冲刺专题卷", "sprint")
        self.mock_exam_button = QPushButton("生成模拟卷 PDF")
        self.mock_exam_button.setObjectName("GhostButton")
        self.stop_pdf_button = QPushButton("停止 PDF 生成")
        self.stop_pdf_button.setObjectName("DangerButton")
        self.stop_pdf_button.setVisible(False)
        self.stop_pdf_button.setDisabled(True)
        for control in (
            self.generate_button,
            self.final_review_button,
            self.clear_button,
            self.copy_context_button,
            self.mock_exam_mode,
            self.mock_exam_button,
            self.stop_pdf_button,
        ):
            control.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
        actions.addWidget(self.generate_button, 0, 0, 1, 4)
        actions.addWidget(self.final_review_button, 1, 0, 1, 2)
        actions.addWidget(self.clear_button, 1, 2)
        actions.addWidget(self.copy_context_button, 1, 3)
        actions.addWidget(self.mock_exam_mode, 2, 0, 1, 2)
        actions.addWidget(self.mock_exam_button, 2, 2)
        actions.addWidget(self.stop_pdf_button, 2, 3)
        self.widget = QWidget()
        self.layout = QVBoxLayout(self.widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addLayout(actions)

    def clear_layout(self):
        while self.plan_layout.count():
            item = self.plan_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_generation_busy(self, busy: bool):
        self.generate_button.setDisabled(busy)
        self.final_review_button.setDisabled(busy)
        self.clear_button.setDisabled(busy)
        self.mock_exam_button.setDisabled(busy)
        self.mock_exam_mode.setDisabled(busy)
        self.page.subject_scope.setDisabled(busy)
        if busy:
            self.generate_button.setText("正在生成...")
            self.progress_bar.setRange(0, 0)
            self.progress_label.setText("正在生成计划…")
        else:
            self.generate_button.setText("生成今日计划")
            self.progress_bar.setRange(0, 100)

    def refresh_phase_status(self):
        from study_app.core.study_phase import exam_scope_label, is_final_review

        active = [name for name in final_review_subject_names(self.page.state) if is_final_review(name)]
        if active:
            active_text = "、".join(
                f"{name}（考试范围：{exam_scope_label(name)}）"
                if exam_scope_label(name)
                else name
                for name in active
            )
            self.phase_status.setText(
                "期末复习模式已启用：" + active_text
                + "。这些学科将提高掌握度缺口权重与综合题比例；无题目证据时按初始掌握度参与排序。"
            )
        else:
            self.phase_status.setText("当前没有学科启用期末复习模式。")

    def manage_final_review_modes(self):
        from PySide6.QtWidgets import QMessageBox

        changed_text = _edit_final_review_modes(self.page.content, self.page.state)
        if changed_text is None:
            return
        self.page.state = load_dashboard_state()
        self.refresh_phase_status()
        self.render_saved_plan(get_active_study_plan(self.page.subject_scope.currentData() or None))
        if changed_text:
            QMessageBox.information(
                self.page.content,
                "期末复习模式已更新",
                "\n".join(changed_text) + "\n\n相关旧计划已归档，请重新生成今日计划。",
            )

    def finish_plan_generation(
        self,
        plan,
        plan_source: str,
        subject_name: str | None,
        generation_date,
        input_signature: str,
        revision_token,
    ):
        from PySide6.QtWidgets import QMessageBox
        self.set_generation_busy(False)
        if plan_source != "local":
            self.progress_label.setText(plan_source)
        _save_generated_plan(
            plan, subject_name, generation_date, input_signature, revision_token
        )
        self.render_saved_plan(get_active_study_plan(subject_name), allow_auto_refresh=False, reload_state=False)
        if (
            plan_source.startswith("LLM 增强计划生成失败")
            or plan_source.startswith("已显示本地计划；LLM 增强未通过校验")
        ):
            QMessageBox.warning(
                self.page.content,
                "LLM 计划生成失败",
                "LLM 增强计划未能生成可保存结果；系统已保留本地计划。\n\n"
                f"详细原因：{plan_source}",
            )

    def fail_plan_generation(self, error: str):
        from PySide6.QtWidgets import QMessageBox
        self.set_generation_busy(False)
        self.progress_label.setText("计划生成失败，请检查网络或 LLM 设置后重试。")
        QMessageBox.warning(self.page.content, "计划生成失败", error)

    def render_plan(self, force_regenerate: bool = False):
        from PySide6.QtWidgets import QMessageBox
        if getattr(self.page.content, "_plan_worker", None) is not None:
            worker = self.page.content._plan_worker
            if worker.isRunning():
                return
        self.page.state = _start_plan_generation_state()
        self.clear_layout()
        subject_name = self.page.subject_scope.currentData() or None
        plan_llm_hint = llm_plan_unavailable_hint()
        active_plan = _reusable_active_plan(self.page.state, subject_name, force_regenerate)
        if active_plan:
            self.render_saved_plan(active_plan)
            if plan_llm_hint:
                QMessageBox.information(self.page.content, "未使用 LLM 生成计划", plan_llm_hint)
            return
        self.set_generation_busy(True)
        generation_date = self.page.state.today
        generation_signature = current_study_plan_signature(self.page.state, subject_name)
        try:
            lifecycle_revision_token = (
                capture_subject_revision(DEFAULT_DB_PATH, subject_name)
                if subject_name
                else None
            )
        except Exception as error:
            self.fail_plan_generation(str(error))
            return
        worker = self.page.PlanGenerationThread(
            self.page.state, subject_name, lifecycle_revision_token, self.page.content
        )

        def on_completed(plan, source):
            self.finish_plan_generation(
                plan,
                source,
                subject_name,
                generation_date,
                generation_signature,
                lifecycle_revision_token,
            )

        def on_timeout():
            self.set_generation_busy(False)
            self.progress_label.setText("LLM 计划生成超过 90 秒，已停止等待。请稍后重试，或临时切换为本地模式。")
            QMessageBox.warning(
                self.page.content,
                "LLM 计划生成超时",
                "LLM 计划生成超过 90 秒仍未返回，系统已停止等待，避免界面长时间卡住。\n\n"
                "如果后台请求稍后返回，应用会忽略这次过期结果；你可以重新点击生成计划。",
            )

        session = _PlanGenerationSession(
            self.page.content, worker, self.page.WorkerSignalRelay,
            on_completed, self.fail_plan_generation, on_timeout,
        )
        if not session.start():
            return
        if plan_llm_hint:
            QMessageBox.information(self.page.content, "未使用 LLM 生成计划", plan_llm_hint)

    def render_saved_plan(self, saved_plan, *, allow_auto_refresh: bool = True, reload_state: bool = True):
        from PySide6.QtWidgets import QLabel
        from PySide6.QtCore import QTimer

        subject_name = self.page.subject_scope.currentData() or None
        if reload_state:
            self.page.state = load_dashboard_state()
        if allow_auto_refresh and saved_plan and should_refresh_plan_for_model(saved_plan, self.page.state, subject_name):
            self.render_plan()
            return
        scroll_position = self.page.scroll.verticalScrollBar().value()
        self.clear_layout()
        self.progress_bar.setVisible(bool(saved_plan))
        self.clear_button.setEnabled(bool(saved_plan))
        self.clear_button.setToolTip("归档当前详细计划" if saved_plan else "尚无可清空的详细计划")
        if not saved_plan:
            self.progress_label.setText("计划进度：尚未生成")
            self.progress_bar.setValue(0)
            note = QLabel("暂无计划")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.plan_layout.addWidget(note)
            return
        plan = saved_plan["plan"]
        item_by_hash = {
            (item["section_key"], item["item_type"], item["item_text"]): item
            for item in saved_plan["items"]
        }
        created_at = saved_plan.get("created_at") or ""
        checkboxes = []

        def update_progress():
            total = len(checkboxes)
            done = sum(1 for item in checkboxes if item.isChecked())
            percent = round(done / total * 100) if total else 0
            suffix = f"  | 当前计划生成于 {created_at}" if created_at else ""
            self.progress_label.setText(f"计划进度：{done}/{total} 项已完成{suffix}")
            self.progress_bar.setValue(percent)

        for title, key, interactive in [
            ("当前判断", "judgement", False),
            ("今日目标", "goals", False),
            ("今日计划", "short", True),
            ("错题诊断表", "diagnostic", False),
            ("推荐记录格式", "record_template", False),
            ("预期模型变化", "expected", False),
            ("生成依据", "evidence", False),
        ]:
            card, card_checks = plan_section_card(
                title,
                plan[key],
                interactive=interactive,
                section_key=key,
                subject_scope=subject_name,
                on_result=self.record_plan_result,
                on_check=self.record_plan_check,
                on_copy_practice_prompt=self.copy_practice_prompt if key == "short" else None,
                on_send_chatgpt=self.send_practice_prompt_to_chatgpt if key == "short" else None,
                on_generate_local_pdf=self.generate_local_practice_pdf if key == "short" else None,
                item_by_hash=item_by_hash,
                result_predicate=is_plan_homework_item,
                show_checkbox=True,
                subdivide_multiline=key == "short",
            )
            for checkbox in card_checks:
                checkbox.stateChanged.connect(update_progress)
            checkboxes.extend(card_checks)
            self.plan_layout.addWidget(card)
            if key == "short":
                summary = plan_day_feedback_card(saved_plan, self.page.state, subject_name)
                if summary is not None:
                    self.plan_layout.addWidget(summary)
        update_progress()
        restore_scroll_timer = QTimer(self.page.scroll)
        restore_scroll_timer.setSingleShot(True)
        restore_scroll_timer.timeout.connect(
            lambda: self.page.scroll.verticalScrollBar().setValue(scroll_position)
        )
        restore_scroll_timer.start(0)

    def clear_plan(self):
        from PySide6.QtWidgets import QLabel
        _archive_current_plan(self.page.subject_scope.currentData() or None)
        self.clear_layout()
        self.progress_label.setText("计划进度：尚未生成")
        self.progress_bar.setValue(0)
        note = QLabel("计划已清空")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        self.plan_layout.addWidget(note)

    def record_plan_check(self, item_id: int, checked: bool):
        _set_plan_item_checked(item_id, checked)
        if checked:
            from PySide6.QtCore import QTimer

            self.maybe_show_completed_day_feedback()
            QTimer.singleShot(
                0,
                lambda: self.render_saved_plan(get_active_study_plan(self.page.subject_scope.currentData() or None)),
            )

    def record_plan_result(self, line: str, is_correct: bool, item_id: int | None = None):
        from PySide6.QtWidgets import QMessageBox
        draft = _prepare_homework_result(
            line, is_correct, item_id, self.page.state, self.page.subject_scope.currentData() or None
        )
        try:
            _commit_homework_result(draft, is_correct)
        except Exception as error:
            QMessageBox.critical(self.page.content, "记录失败", str(error))
            return False
        try:
            self.page.state = _refresh_homework_feedback(draft)
            self.maybe_show_completed_day_feedback()
            self.render_saved_plan(get_active_study_plan(self.page.subject_scope.currentData() or None))
        except Exception:
            QMessageBox.warning(
                self.page.content,
                "记录已保存",
                "学习记录已写入，但页面刷新失败。" + draft.fallback_hint,
            )
            return True
        QMessageBox.information(
            self.page.content,
            "已记录",
            f"已按“{draft.result_text}”写入学习记录，难度折算分 {draft.score_result.score:.1f}。"
            + draft.fallback_hint,
        )
        return True

    def copy_practice_prompt(self, line: str):
        from PySide6.QtWidgets import QApplication, QMessageBox

        prompt = build_practice_generation_prompt(line, self.page.state, self.page.subject_scope.currentData() or None)
        QApplication.clipboard().setText(prompt)
        if is_oj_plan_homework(line):
            QMessageBox.information(self.page.content, "已复制", "已复制 LeetCode 原题清单，可直接按官方链接练习并提交。")
        else:
            QMessageBox.information(self.page.content, "已复制", "已复制出题提示词，可直接粘贴给 Codex、GPT 或其他 LLM。")

    def send_practice_prompt_to_chatgpt(self, line: str):
        if is_oj_plan_homework(line):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                self.page.content,
                "无需生成 PDF",
                "算法设计与 OJ 训练使用 LeetCode 官方原题和官方测试集；系统已在计划中直接匹配题目，不再生成出题提示词。",
            )
            return
        prompt = build_practice_generation_prompt(line, self.page.state, self.page.subject_scope.currentData() or None)
        self.pdf_session.start(prompt)

    def generate_local_practice_pdf(self, line: str):
        from PySide6.QtWidgets import QMessageBox

        subject_name = str(self.page.subject_scope.currentData() or "").strip()
        try:
            result = generate_local_practice_for_plan_line(line, subject_name)
        except Exception as error:
            message = str(error).strip() or "本地练习卷生成失败，请检查题库答案和运行依赖。"
            QMessageBox.warning(self.page.content, "本地练习卷生成失败", message)
            return
        QMessageBox.information(self.page.content, "本地练习卷已生成", local_practice_success_message(result))

    def copy_plan_practice_context(self):
        from PySide6.QtWidgets import QApplication, QMessageBox

        saved = get_active_study_plan(self.page.subject_scope.currentData() or None)
        if not saved:
            QMessageBox.information(self.page.content, "暂无计划", "请先生成今日计划，再复制题库上下文。")
            return
        homework_items = [
            item["item_text"]
            for item in saved.get("items", [])
            if item.get("section_key") == "short" and item.get("item_type") == "result"
        ]
        if not homework_items:
            QMessageBox.information(self.page.content, "暂无作业", "当前计划中还没有可导出的每日作业。")
            return
        exports = []
        for item in homework_items:
            if is_oj_plan_homework(item):
                exports.append(build_oj_practice_list(item, self.page.state, self.page.subject_scope.currentData() or None))
            else:
                exports.append(build_practice_generation_prompt(item, self.page.state, self.page.subject_scope.currentData() or None))
        QApplication.clipboard().setText("\n\n---\n\n".join(exports))
        QMessageBox.information(self.page.content, "已复制", f"已复制 {len(exports)} 条练习上下文。OJ 作业会直接复制 LeetCode 原题清单。")

    def send_mock_exam_pdf(self):
        from PySide6.QtWidgets import QMessageBox

        subject_name = self.page.subject_scope.currentData() or None
        if not subject_name:
            QMessageBox.information(self.page.content, "请选择学科", "生成模拟卷前，请先在计划范围中选择一个具体学科。")
            return
        try:
            prompt = build_mock_exam_generation_prompt(
                self.page.state, subject_name,
                str(self.mock_exam_mode.currentData() or "diagnostic"),
            )
        except Exception as error:
            QMessageBox.warning(self.page.content, "模拟卷提示词生成失败", str(error))
            return
        self.pdf_session.start(prompt)

    def maybe_show_completed_day_feedback(self):
        from PySide6.QtWidgets import QMessageBox
        saved = get_active_study_plan(self.page.subject_scope.currentData() or None)
        if not saved:
            return
        feedback = _completed_day_feedback(saved, self.page.state, self.page.subject_scope.currentData() or None)
        for message in feedback.messages:
            QMessageBox.information(self.page.content, "当天计划已完成", message)
        _save_completed_day_feedback_markers(feedback)


class _StudyPlanPage:
    """组合两个面板，共享当前仪表盘状态和学科选择。"""

    def __init__(self, state: DashboardState):
        from PySide6.QtWidgets import QApplication, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
        from PySide6.QtCore import Qt
        self.state = state
        self.WorkerSignalRelay, self.PlanGenerationThread, self.ChatGPTBridgeThread = _worker_types()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setMinimumWidth(0)
        self.content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.content._chatgpt_bridge_page_alive = True
        self.content.destroyed.connect(self.mark_chatgpt_page_destroyed)
        app = QApplication.instance()
        if app is not None and not getattr(app, "_chatgpt_bridge_shutdown_connected", False):
            app.aboutToQuit.connect(_shutdown_chatgpt_bridge_workers)
            app._chatgpt_bridge_shutdown_connected = True
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(30, 28, 30, 30)
        self.layout.setSpacing(18)
        self._build_header()

        self.budget = _BudgetPlanPanel(self)
        self.layout.addWidget(self.budget.widget)
        self.detail = _DetailedPlanPanel(self)

        generate_handler = lambda _checked=False: self.detail.render_plan(force_regenerate=True)
        clear_handler = lambda _checked=False: self.detail.clear_plan()
        self.detail.generate_button.clicked.connect(generate_handler)
        self.detail.final_review_button.clicked.connect(lambda _checked=False: self.detail.manage_final_review_modes())
        self.detail.clear_button.clicked.connect(clear_handler)
        self.detail.copy_context_button.clicked.connect(lambda _checked=False: self.detail.copy_plan_practice_context())
        self.detail.mock_exam_button.clicked.connect(lambda _checked=False: self.detail.send_mock_exam_pdf())
        self.subject_scope.currentIndexChanged.connect(self.refresh_scope_views)
        self.detail.refresh_phase_status()
        self.detail.render_saved_plan(get_active_study_plan(self.subject_scope.currentData() or None), reload_state=False)
        self.budget.render_budgeted_plan()
        self.content._plan_callbacks = (
            self.detail.render_plan, self.detail.render_saved_plan,
            self.detail.clear_plan, self.detail.clear_layout,
            generate_handler, clear_handler,
        )
        self.content._budget_plan_callbacks = (self.budget.generate_budgeted_plan, self.budget.render_budgeted_plan)
        self.scroll._set_dashboard_state = self.update_dashboard_state
        from study_app.ui.design_components import disclosure, readable_page
        self.layout.addWidget(disclosure("详细学习方案与出卷工具", self.detail.widget))
        self.layout.addStretch()
        readable_page(self.scroll, self.content)
        self.scroll._open_task = self.open_task
        self.scroll.setWidget(self.content)

    def _build_header(self):
        from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

        hero = QWidget()
        hero.setObjectName("RecordHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        hero_layout.setSpacing(6)
        title = QLabel("学习计划")
        title.setObjectName("HeroTitle")
        hero_layout.addWidget(title)
        self.layout.addWidget(hero)

        scope_label = QLabel("计划范围")
        scope_label.setObjectName("FormLabel")
        self.subject_scope = QComboBox()
        for label, value in plan_subject_scope_options(self.state):
            self.subject_scope.addItem(label, value)
        self.subject_scope.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        scope_row = QHBoxLayout()
        scope_label.setBuddy(self.subject_scope)
        scope_row.addWidget(scope_label)
        scope_row.addWidget(self.subject_scope, 1)
        self.layout.addLayout(scope_row)

    def mark_chatgpt_page_destroyed(self):
        setattr(self.content, "_chatgpt_bridge_page_alive", False)

    def refresh_scope_views(self, _index=None):
        self.budget.invalidate_preview()
        self.detail.render_saved_plan(get_active_study_plan(self.subject_scope.currentData() or None), reload_state=False)
        self.budget.render_budgeted_plan()

    def update_dashboard_state(self, new_state: DashboardState):
        self.state = new_state

    def open_task(self, item_id=None):
        from PySide6.QtCore import QTimer
        self.budget.render_budgeted_plan()
        if item_id in self.budget.task_rows:
            task, details = self.budget.task_rows[item_id]
            details.toggle.setChecked(True)
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(task, 0, 24))
        else:
            self.scroll.verticalScrollBar().setValue(0)


def study_plan_page(state: DashboardState):
    page = _StudyPlanPage(state)
    # 页面重建时，每个滚动容器保留自己的状态和回调实例。
    page.scroll._plan_page = page
    return page.scroll


def plan_day_feedback_card(saved_plan: dict, state: DashboardState, subject_scope: str | None):
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    summaries = [item["line"] for item in plan_day_feedback_details(saved_plan, state, subject_scope)]
    if not summaries:
        return None
    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    heading = QLabel("每日完成反馈")
    heading.setObjectName("CardTitle")
    layout.addWidget(heading)
    for line in summaries:
        label = QLabel(humanize_plan_text(line))
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        layout.addWidget(label)
    return card

def plan_section_card(
    title: str,
    lines: list[str],
    interactive: bool = False,
    section_key: str = "",
    subject_scope: str | None = None,
    on_result=None,
    on_check=None,
    on_copy_practice_prompt=None,
    on_send_chatgpt=None,
    on_generate_local_pdf=None,
    item_by_hash: dict[str, dict] | None = None,
    result_predicate=None,
    show_checkbox: bool = True,
    subdivide_multiline: bool = False,
):
    from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    heading = QLabel(title)
    heading.setObjectName("CardTitle")
    layout.addWidget(heading)
    checkboxes = []
    handlers = []
    def add_interactive_row(text: str, row_kind: str = "plain"):
        plan_item = (item_by_hash or {}).get((section_key, row_kind, text))
        display_text = humanize_plan_text(text)
        row = QGridLayout()
        row.setSpacing(8)
        for column in range(3):
            row.setColumnStretch(column, 1)
        use_checkbox = show_checkbox or row_kind == "check"
        checkbox = QCheckBox() if use_checkbox else None
        label = QLabel(display_text)
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        if checkbox is not None:
            if plan_item and plan_item.get("checked"):
                checkbox.setChecked(True)
                checkbox.setEnabled(False)
            if plan_item and on_check:
                item_id = int(plan_item["id"])

                def check_handler(state, saved_item_id=item_id, box=checkbox):
                    on_check(saved_item_id, box.isChecked())
                    if box.isChecked():
                        box.setEnabled(False)

                checkbox.stateChanged.connect(check_handler)
                handlers.append(check_handler)
            row.addWidget(checkbox, 0, 0)
        row.addWidget(label, 0, 1 if checkbox is not None else 0, 1, 2 if checkbox is not None else 3)
        action_buttons = []
        is_homework_row = (
            row_kind == "result"
            or (row_kind == "plain" and (result_predicate is None or result_predicate(text)))
        )
        is_oj_row = is_oj_plan_homework(text)
        if is_homework_row and plan_item and on_result and not is_oj_row:
            item_id = int(plan_item["id"])
            result_buttons = []
            for button_text, is_correct, object_name in (
                ("完成正确", True, "PrimaryButton"),
                ("完成有误", False, "DangerButton"),
            ):
                result_button = QPushButton(button_text)
                result_button.setObjectName(object_name)
                result_button.setDisabled(bool(plan_item.get("result")))
                result_buttons.append(result_button)

                def result_handler(
                    _checked=False,
                    source_text=text,
                    correct=is_correct,
                    saved_item_id=item_id,
                    row_buttons=result_buttons,
                ):
                    for button in row_buttons:
                        button.setDisabled(True)
                    if on_result(source_text, correct, saved_item_id) is False:
                        for button in row_buttons:
                            button.setDisabled(False)

                result_button.clicked.connect(result_handler)
                handlers.append(result_handler)
                action_buttons.append(result_button)
        if is_homework_row and on_copy_practice_prompt:
            copy_button = QPushButton("复制 LeetCode 清单" if is_oj_row else "复制出题提示词")
            copy_button.setObjectName("GhostButton")

            def copy_handler(_checked=False, source_text=text):
                on_copy_practice_prompt(source_text)

            copy_button.clicked.connect(copy_handler)
            handlers.append(copy_handler)
            action_buttons.append(copy_button)
        if is_homework_row and on_generate_local_pdf and not is_oj_row:
            local_button = QPushButton("本地生成练习卷")
            local_button.setObjectName("PrimaryButton")

            def local_handler(_checked=False, source_text=text):
                on_generate_local_pdf(source_text)

            local_button.clicked.connect(local_handler)
            handlers.append(local_handler)
            action_buttons.append(local_button)
        if is_homework_row and on_send_chatgpt and not is_oj_row:
            send_button = QPushButton("让 ChatGPT 生成 PDF")
            send_button.setObjectName("GhostButton")

            def send_handler(_checked=False, source_text=text):
                on_send_chatgpt(source_text)

            send_button.clicked.connect(send_handler)
            handlers.append(send_handler)
            action_buttons.append(send_button)
        for index, button in enumerate(action_buttons):
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            row.addWidget(button, 1 + index // 3, index % 3)
        layout.addLayout(row)
        if checkbox is not None:
            checkboxes.append(checkbox)

    for line in lines:
        if interactive:
            if subdivide_multiline:
                for row_kind, text in split_short_plan_item(line):
                    if row_kind == "heading":
                        day_label = QLabel(text)
                        day_label.setObjectName("CardTitle")
                        day_label.setWordWrap(True)
                        layout.addWidget(day_label)
                    elif row_kind == "info":
                        info_label = QLabel(text)
                        info_label.setObjectName("ListTitle")
                        info_label.setWordWrap(True)
                        layout.addWidget(info_label)
                    else:
                        add_interactive_row(text, row_kind)
            else:
                add_interactive_row(line)
        else:
            label = QLabel(line)
            label.setObjectName("ListTitle")
            label.setWordWrap(True)
            layout.addWidget(label)
    card._plan_result_handlers = handlers
    return card, checkboxes
