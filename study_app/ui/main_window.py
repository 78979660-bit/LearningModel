from __future__ import annotations

from datetime import datetime, timedelta

from study_app.core.active_subjects import require_activity_subject
from study_app.core.dashboard import DashboardState, load_dashboard_state
from study_app.core.practice_bank import known_template_ids
from study_app.core.practice_prompts import (
    _short_seed_text,
    _topic_related,
    build_mock_exam_generation_prompt,
    build_oj_practice_list,
    build_practice_generation_prompt,
    mock_exam_seed_context,
    practice_distribution_guidance,
    practice_seed_context,
    recent_practice_context,
)
from study_app.core.practice_spec import (
    _extract_homework_component_counts,
    _extract_homework_components,
    desired_practice_seed_count,
    extract_homework_exercise_count,
    extract_template_brief,
    infer_practice_template,
    is_oj_plan_homework,
    normalize_homework_count,
    planned_homework_difficulty,
    safe_int,
)
from study_app.core.study_plan_feedback import (
    plan_day_feedback_details,
    plan_day_feedback_lines,
    planned_homework_score,
)
from study_app.core.study_plan_items import (
    build_study_plan_items,
    humanize_plan_text,
    infer_subject_topic_from_plan_line,
    is_plan_homework_item,
    split_short_plan_item,
    study_plan_item_hash,
)
from study_app.core.study_plan_homework import (
    _completion_standard_for_topic,
    _daily_plan,
    _homework_for_topic,
    _is_cs_oj_focus,
    _lowest_cs_oj_focus,
    _matches_plan_subject,
    _oj_daily_plan_text,
    _oj_seed_line_scores,
    _oj_seed_lines,
    _oj_topic_hint,
)
from study_app.core.local_study_plan import (
    _balanced_homework_topic,
    apply_dynamic_difficulty_to_plan,
    generate_study_plan,
    shrink_risk_item,
    study_plan_llm_payload,
)
from study_app.ai.study_plan_service import (
    _plan_text,
    current_study_plan_signature,
    generate_study_plan_with_optional_llm,
    llm_plan_unavailable_hint,
    should_refresh_plan_for_model,
    should_upgrade_plan_to_llm,
)
from study_app.ai.providers import LLMSettings, load_llm_settings, save_llm_settings
from study_app.data.database import (
    add_learning_record,
    archive_active_study_plan,
    create_study_plan,
    delete_settings_by_prefix,
    get_active_study_plan,
    get_setting,
    list_recent_records,
    set_setting,
    update_study_plan_item_state,
)
from study_app.single_instance import WakeServer, send_wake_signal
from study_app.ui.activity_view import (
    ALL_ACTIVE_SUBJECTS_LABEL,
    HomepageActivity,
    activity_subject_names,
    activity_subjects,
    filter_homepage_activity,
    final_review_subject_names,
    plan_subject_scope_label,
    plan_subject_scope_options,
    record_subject_names,
)
from study_app.ui.daily_summary_support import (
    daily_summary_cache_signature,
    matching_daily_summary_cache,
)
from study_app.ui.record_view_model import recent_record_display_lines
from study_app.ui.theme import PALETTE, build_stylesheet
from study_app.ui.dashboard_widgets import (
    daily_summary_card,
    metric_card,
    risk_card,
    simple_page,
    subject_card,
    todo_card,
)
from study_app.ui.query_page import query_page
from study_app.ui.record_editor import ACTIVITY_OPTIONS, SOURCE_OPTIONS, AddRecordDialog, add_record_page
from study_app.ui.records_page import recent_records_card, records_page
from study_app.ui.settings_page import (
    BACKUP_ROOT,
    LLM_FEATURES,
    PROVIDERS,
    create_backup,
    integrity_check,
    list_llm_call_audits,
    provider_status,
    settings_page,
)
from study_app.ui.study_plan_page import plan_day_feedback_card, plan_section_card, study_plan_page
from study_app.ui.window_shell import FloatingIcon, MainWindow
from study_app.ui.app_runtime import run_app
































def _legacy_plan_day_feedback_details(saved_plan: dict, state: DashboardState, subject_scope: str | None) -> list[dict[str, object]]:
    by_day: dict[int, list[dict]] = {}
    for item in saved_plan.get("items", []):
        if item.get("section_key") == "short" and item.get("day_index"):
            by_day.setdefault(int(item["day_index"]), []).append(item)

    subject_map = {subject.name: subject for subject in state.subjects}
    details = []
    for day_index in sorted(by_day):
        items = by_day[day_index]
        check_items = [item for item in items if item.get("item_type") == "check"]
        result_items = [item for item in items if item.get("item_type") == "result"]
        checked_count = sum(1 for item in check_items if item.get("checked"))
        result_count = sum(1 for item in result_items if item.get("result"))
        total_count = len(check_items) + len(result_items)
        done_count = checked_count + result_count
        checks_done = checked_count == len(check_items) if check_items else True
        results_done = result_count == len(result_items) if result_items else False
        complete = checks_done and results_done and total_count > 0

        result_scores = [
            planned_homework_score(
                planned_homework_difficulty(item.get("item_text", "")),
                item.get("result") == "correct",
            )
            for item in result_items
            if item.get("result")
        ]
        score = sum(result_scores) / len(result_scores) if result_scores else 0
        first_result_text = result_items[0]["item_text"] if result_items else ""
        subject_name, topic = infer_subject_topic_from_plan_line(first_result_text, subject_scope)
        subject = subject_map.get(subject_name)
        snapshots = [
            get_setting(f"study_plan_item_feedback:{item['id']}", {}) or {}
            for item in result_items
            if item.get("id") is not None
        ]
        snapshots = [snapshot for snapshot in snapshots if snapshot.get("before_captured")]
        if subject:
            base_score = subject.window_score if subject.window_score is not None else subject.initial_score
            mastery = subject.covered_mastery_score
            total_mastery = subject.mastery_score
        else:
            base_score = state.benchmark
            mastery = 60
            total_mastery = 60

        window_before = next(
            (snapshot.get("window_score_before") for snapshot in snapshots if snapshot.get("window_score_before") is not None),
            None,
        )
        window_after = next(
            (snapshot.get("window_score_after") for snapshot in reversed(snapshots) if snapshot.get("window_score_after") is not None),
            subject.window_score if subject else None,
        )
        mastery_before = next(
            (snapshot.get("covered_mastery_before") for snapshot in snapshots if snapshot.get("covered_mastery_before") is not None),
            mastery,
        )
        mastery_after_snapshot = next(
            (snapshot.get("covered_mastery_after") for snapshot in reversed(snapshots) if snapshot.get("covered_mastery_after") is not None),
            None,
        )
        total_mastery_after = next(
            (snapshot.get("total_mastery_after") for snapshot in reversed(snapshots) if snapshot.get("total_mastery_after") is not None),
            total_mastery,
        )
        score_delta = score - float(window_before if window_before is not None else base_score)
        benchmark_delta = score - float(state.benchmark)
        correctness = (
            sum(1 for item in result_items if item.get("result") == "correct") / len(result_items)
            if result_items else 0
        )
        mastery_delta = round((correctness - 0.6) * 8)
        if correctness == 1:
            mastery_delta = max(mastery_delta, 3)
        elif correctness == 0:
            mastery_delta = min(mastery_delta, -3)
        mastery = int(mastery_before)
        mastery_after = (
            int(mastery_after_snapshot)
            if mastery_after_snapshot is not None
            else max(0, min(100, mastery + mastery_delta))
        )
        mastery_delta = mastery_after - mastery
        direction = "+" if score_delta >= 0 else ""
        benchmark_direction = "+" if benchmark_delta >= 0 else ""
        mastery_direction = "+" if mastery_delta >= 0 else ""
        progress = f"{done_count}/{total_count}" if total_count else "0/0"
        if not snapshots:
            window_change = (
                f"当前 {float(window_after):.1f}（旧完成记录未保存写入前快照）"
                if window_after is not None
                else "暂无可计算窗口分"
            )
            mastery_change = f"当前 {mastery}（旧完成记录未保存前后快照）"
        elif window_before is None:
            window_change = f"本周期此前无评分 -> {window_after:.1f}" if window_after is not None else "暂无可计算窗口分"
            mastery_change = f"{mastery} -> {mastery_after}（{mastery_direction}{mastery_delta}）"
        elif window_after is not None:
            window_delta = float(window_after) - float(window_before)
            window_change = f"{float(window_before):.1f} -> {float(window_after):.1f}（{window_delta:+.1f}）"
            mastery_change = f"{mastery} -> {mastery_after}（{mastery_direction}{mastery_delta}）"
        else:
            window_change = f"写入前 {float(window_before):.1f}，写入后暂无可计算窗口分"
            mastery_change = f"{mastery} -> {mastery_after}（{mastery_direction}{mastery_delta}）"
        if complete:
            line = (
                f"今日进度 {progress}，已完成。{subject_name} / {topic}；"
                f"作业折算分 {score:.0f}，相对标杆 {benchmark_direction}{benchmark_delta:.1f}，"
                f"三天窗口分变化 {window_change}；"
                f"已学掌握度 {mastery_change}，整门课总掌握度 {total_mastery_after}；"
                f"遗忘风险预计下降，BKT 证据更新为 {'正向' if correctness >= 0.8 else '需继续观察'}。"
            )
            popup = (
                "今日计划已完成。\n\n"
                f"学科与知识点：{subject_name} / {topic}\n"
                f"当天进度：{progress}\n"
                f"作业折算分：{score:.0f}\n"
                f"相对标杆：{benchmark_direction}{benchmark_delta:.1f}\n"
                f"三天窗口分变化：{window_change}\n"
                f"已学掌握度：{mastery_change}\n"
                f"整门课总掌握度：{total_mastery_after}\n"
                f"记忆/BKT影响：遗忘风险预计下降；"
                f"{'作业正确，BKT掌握证据增强。' if correctness >= 0.8 else '作业存在错误，BKT会保留该题型为观察重点。'}"
            )
        else:
            result_label = "待完成"
            if result_items and result_items[0].get("result") == "correct":
                result_label = "已做对"
            elif result_items and result_items[0].get("result") == "wrong":
                result_label = "有误"
            line = (
                f"今日进度 {progress}；执行项 {checked_count}/{len(check_items)}；"
                f"作业状态 {result_label}。完成后将弹出分数、掌握度与记忆/BKT变化。"
            )
            popup = ""
        details.append(
            {
                "day_index": day_index,
                "complete": complete,
                "line": line,
                "popup": popup,
            }
        )
    return details
