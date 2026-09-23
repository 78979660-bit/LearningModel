PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_call_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    feature TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    estimated_tokens INTEGER NOT NULL DEFAULT 0,
    upload_summary_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    error_message TEXT,
    parent_audit_id INTEGER REFERENCES llm_call_audits(id)
);

CREATE INDEX IF NOT EXISTS idx_llm_call_audits_created
ON llm_call_audits(created_at DESC);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    weight REAL NOT NULL DEFAULT 0,
    initial_score REAL,
    mastery REAL NOT NULL DEFAULT 0,
    status TEXT,
    current_anchor TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0,
    mastery REAL NOT NULL DEFAULT 0,
    confidence REAL,
    status TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(subject_id, name)
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT,
    mastery REAL NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 0,
    forgetting_risk REAL NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(module_id, name)
);

CREATE TABLE IF NOT EXISTS learning_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_date TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    module_name TEXT,
    topic_name TEXT,
    activity TEXT,
    source TEXT,
    score REAL,
    duration_minutes REAL,
    note TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_learning_records_date
ON learning_records(record_date);

CREATE INDEX IF NOT EXISTS idx_learning_records_subject_date
ON learning_records(subject_name, record_date);

CREATE TABLE IF NOT EXISTS learning_record_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    actor TEXT NOT NULL,
    prior_contributions_json TEXT NOT NULL DEFAULT '[]',
    source_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(record_id, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_record_revisions_source_key
ON learning_record_revisions(source_key) WHERE source_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS problem_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES learning_records(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    statement TEXT,
    status TEXT,
    correctness REAL,
    difficulty_label TEXT,
    difficulty_score REAL,
    error_cause TEXT,
    related_topics_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_problem_attempts_record
ON problem_attempts(record_id);

CREATE TABLE IF NOT EXISTS record_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES learning_records(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_ext TEXT,
    mime_hint TEXT,
    file_size INTEGER,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_record_attachments_record
ON record_attachments(record_id);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    subject_name TEXT,
    module_name TEXT,
    topic_name TEXT,
    title TEXT NOT NULL,
    detail TEXT,
    priority REAL NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL DEFAULT '{}',
    dismissed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_open
ON alerts(dismissed_at, severity, priority);

CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_scope TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    input_signature TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    plan_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    plan_date TEXT,
    budget_minutes INTEGER,
    plan_format_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_study_plans_active
ON study_plans(subject_scope, status, created_at);

CREATE TABLE IF NOT EXISTS study_plan_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    section_title TEXT NOT NULL,
    day_index INTEGER,
    item_type TEXT NOT NULL,
    item_text TEXT NOT NULL,
    item_order INTEGER NOT NULL,
    item_hash TEXT NOT NULL,
    task_id TEXT,
    subject_id TEXT,
    estimated_minutes INTEGER,
    estimate_source TEXT,
    selection_reason_json TEXT,
    excluded_reason TEXT,
    UNIQUE(plan_id, item_hash)
);

CREATE INDEX IF NOT EXISTS idx_study_plan_items_plan
ON study_plan_items(plan_id, item_order);

CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_plan_task
ON study_plan_items(plan_id, task_id)
WHERE task_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_budget_plan_date
ON study_plans(plan_date)
WHERE plan_format_version = 'budget-v1' AND status = 'active';

CREATE TABLE IF NOT EXISTS study_plan_item_states (
    item_id INTEGER PRIMARY KEY REFERENCES study_plan_items(id) ON DELETE CASCADE,
    checked INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_day_budgets (
    plan_date TEXT PRIMARY KEY,
    available_minutes INTEGER NOT NULL
        CHECK(typeof(available_minutes) = 'integer' AND available_minutes BETWEEN 0 AND 1440),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_subject_exam_dates (
    subject_id INTEGER PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
    exam_date TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_task_estimates (
    task_id TEXT PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    estimated_minutes INTEGER NOT NULL
        CHECK(typeof(estimated_minutes) = 'integer' AND estimated_minutes BETWEEN 1 AND 1440),
    source TEXT NOT NULL CHECK(source IN ('user', 'confirmed_template')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS practice_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id TEXT NOT NULL UNIQUE,
    subject_hint TEXT,
    topic_hint TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    generation_rules_json TEXT NOT NULL DEFAULT '[]',
    source_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS practice_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, title, url)
);

CREATE TABLE IF NOT EXISTS practice_problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id TEXT NOT NULL REFERENCES practice_templates(template_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    answer_outline TEXT,
    common_errors_json TEXT NOT NULL DEFAULT '[]',
    difficulty_score REAL NOT NULL,
    difficulty_source TEXT NOT NULL DEFAULT 'seed',
    subject_hint TEXT,
    topic_hint TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_id INTEGER REFERENCES practice_sources(id) ON DELETE SET NULL,
    source_note TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(template_id, title)
);

CREATE INDEX IF NOT EXISTS idx_practice_problems_template
ON practice_problems(template_id, difficulty_score);

CREATE INDEX IF NOT EXISTS idx_practice_problems_subject_topic
ON practice_problems(subject_hint, topic_hint);

CREATE TABLE IF NOT EXISTS practice_collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    discovered_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS practice_collection_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    institution TEXT NOT NULL,
    subject_hint TEXT,
    topic_hint TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    document_type TEXT NOT NULL DEFAULT 'assignment',
    quality_score REAL NOT NULL DEFAULT 0,
    estimated_difficulty REAL,
    status TEXT NOT NULL DEFAULT 'discovered',
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_practice_collection_candidates_quality
ON practice_collection_candidates(status, quality_score DESC, estimated_difficulty DESC);

CREATE TABLE IF NOT EXISTS practice_collection_backlog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL DEFAULT '',
    template_id TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    target_difficulty REAL,
    request_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    first_requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE(subject, template_id, topic)
);

CREATE INDEX IF NOT EXISTS idx_practice_collection_backlog_priority
ON practice_collection_backlog(status, request_count DESC, last_requested_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
