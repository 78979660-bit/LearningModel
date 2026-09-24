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
