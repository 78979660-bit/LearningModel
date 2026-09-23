PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS subject_catalog_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    catalog_revision INTEGER NOT NULL DEFAULT 0 CHECK(catalog_revision >= 0)
);

INSERT OR IGNORE INTO subject_catalog_state(singleton, catalog_revision)
VALUES (1, 0);

CREATE TABLE IF NOT EXISTS subject_catalog (
    subject_key TEXT PRIMARY KEY CHECK(subject_key GLOB 'subject:v1:[0-9a-f]*'),
    canonical_name TEXT NOT NULL,
    canonical_name_normalized TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('active', 'archived')),
    object_version INTEGER NOT NULL DEFAULT 1 CHECK(object_version >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_aliases (
    alias_normalized TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    alias_version INTEGER NOT NULL DEFAULT 1 CHECK(alias_version >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_capabilities (
    subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    capability_key TEXT NOT NULL CHECK(capability_key IN (
        'study_plan', 'generic_practice', 'local_practice_pdf', 'mock_exam',
        'oj', 'formula_rendering', 'weekly_source_collection'
    )),
    declared_supported INTEGER NOT NULL CHECK(declared_supported IN (0, 1)),
    declaration_version INTEGER NOT NULL DEFAULT 1 CHECK(declaration_version >= 1),
    PRIMARY KEY(subject_key, capability_key)
);

CREATE TABLE IF NOT EXISTS subject_module_identities (
    module_key TEXT PRIMARY KEY CHECK(module_key GLOB 'module:v1:[0-9a-f]*'),
    subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    canonical_name TEXT NOT NULL,
    canonical_name_normalized TEXT NOT NULL,
    display_name TEXT NOT NULL,
    object_version INTEGER NOT NULL DEFAULT 1 CHECK(object_version >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(subject_key, canonical_name_normalized)
);

CREATE TABLE IF NOT EXISTS subject_structure_versions (
    structure_version TEXT PRIMARY KEY CHECK(structure_version GLOB 'structure:v1:[0-9a-f]*'),
    subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    manifest_version TEXT,
    source_reference TEXT NOT NULL,
    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
    object_version INTEGER NOT NULL DEFAULT 1 CHECK(object_version >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_subject_current_structure
ON subject_structure_versions(subject_key)
WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS subject_structure_modules (
    structure_version TEXT NOT NULL REFERENCES subject_structure_versions(structure_version) ON DELETE RESTRICT,
    module_key TEXT NOT NULL REFERENCES subject_module_identities(module_key) ON DELETE RESTRICT,
    module_order INTEGER NOT NULL CHECK(module_order >= 0),
    PRIMARY KEY(structure_version, module_key),
    UNIQUE(structure_version, module_order)
);

CREATE TABLE IF NOT EXISTS subject_structure_topics (
    structure_version TEXT NOT NULL REFERENCES subject_structure_versions(structure_version) ON DELETE RESTRICT,
    module_key TEXT NOT NULL REFERENCES subject_module_identities(module_key) ON DELETE RESTRICT,
    topic_key TEXT NOT NULL REFERENCES knowledge_topic_registry(topic_key) ON DELETE RESTRICT,
    topic_order INTEGER NOT NULL CHECK(topic_order >= 0),
    importance_bp INTEGER CHECK(importance_bp BETWEEN 0 AND 10000),
    difficulty_bp INTEGER CHECK(difficulty_bp BETWEEN 0 AND 10000),
    PRIMARY KEY(structure_version, module_key, topic_key),
    UNIQUE(structure_version, module_key, topic_order)
);

CREATE TABLE IF NOT EXISTS subject_identity_relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('split_from', 'merged_from')),
    source_subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    target_subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    decision_reference TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(source_subject_key <> target_subject_key),
    UNIQUE(relation_type, source_subject_key, target_subject_key)
);

CREATE TABLE IF NOT EXISTS subject_documents (
    document_hash TEXT PRIMARY KEY CHECK(length(document_hash) = 64),
    original_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
    page_count INTEGER CHECK(page_count IS NULL OR page_count >= 0),
    encrypted INTEGER NOT NULL CHECK(encrypted IN (0, 1)),
    file_version TEXT NOT NULL,
    source_note TEXT NOT NULL DEFAULT '',
    external_allowed INTEGER NOT NULL DEFAULT 0 CHECK(external_allowed IN (0, 1)),
    preflight_status TEXT NOT NULL CHECK(preflight_status IN ('ready', 'needs_review', 'rejected', 'failed')),
    diagnostics_json TEXT NOT NULL DEFAULT '[]',
    security_flags_json TEXT NOT NULL DEFAULT '[]',
    registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_preflight_inspections (
    inspection_key TEXT PRIMARY KEY CHECK(length(inspection_key) = 64),
    document_hash TEXT NOT NULL REFERENCES subject_documents(document_hash) ON DELETE RESTRICT,
    policy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL CHECK(length(config_hash) = 64),
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ready', 'needs_review', 'rejected', 'failed')),
    page_count INTEGER CHECK(page_count IS NULL OR page_count >= 0),
    encrypted INTEGER NOT NULL CHECK(encrypted IN (0, 1)),
    diagnostics_json TEXT NOT NULL DEFAULT '[]',
    security_flags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_hash, config_hash)
);

CREATE TABLE IF NOT EXISTS document_parses (
    parse_key TEXT PRIMARY KEY CHECK(length(parse_key) = 64),
    document_hash TEXT NOT NULL REFERENCES subject_documents(document_hash) ON DELETE RESTRICT,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    config_hash TEXT NOT NULL CHECK(length(config_hash) = 64),
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('registered', 'paused', 'completed', 'needs_review', 'failed', 'rejected')),
    page_count INTEGER CHECK(page_count IS NULL OR page_count >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_hash, parser_name, parser_version, config_hash)
);

CREATE TABLE IF NOT EXISTS document_pages (
    parse_key TEXT NOT NULL REFERENCES document_parses(parse_key) ON DELETE RESTRICT,
    physical_page INTEGER NOT NULL CHECK(physical_page >= 1),
    status TEXT NOT NULL CHECK(status IN ('pending', 'success', 'partial_success', 'needs_review', 'failed')),
    blank_suspected INTEGER NOT NULL DEFAULT 0 CHECK(blank_suspected IN (0, 1)),
    quality_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(parse_key, physical_page)
);

CREATE TABLE IF NOT EXISTS document_page_contents (
    parse_key TEXT NOT NULL REFERENCES document_parses(parse_key) ON DELETE RESTRICT,
    physical_page INTEGER NOT NULL,
    page_label TEXT,
    printed_page TEXT,
    parse_route TEXT NOT NULL CHECK(parse_route IN ('text', 'ocr', 'mixed', 'none')),
    rotation INTEGER NOT NULL,
    coordinate_version TEXT NOT NULL,
    text_hash TEXT NOT NULL CHECK(length(text_hash) = 64),
    text_content TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    controlled_fallback INTEGER NOT NULL DEFAULT 0 CHECK(controlled_fallback IN (0, 1)),
    PRIMARY KEY(parse_key, physical_page),
    FOREIGN KEY(parse_key, physical_page)
        REFERENCES document_pages(parse_key, physical_page) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS subject_evidence (
    evidence_id TEXT PRIMARY KEY CHECK(length(evidence_id) = 64),
    parse_key TEXT NOT NULL,
    physical_page INTEGER NOT NULL,
    page_label TEXT,
    printed_page TEXT,
    bbox_json TEXT NOT NULL,
    rotation INTEGER NOT NULL,
    coordinate_version TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK(object_type IN ('heading', 'paragraph', 'formula', 'table', 'ocr_text', 'controlled_fallback')),
    evidence_type TEXT NOT NULL CHECK(evidence_type IN ('explicit_source', 'llm_inference', 'user_decision')),
    excerpt TEXT NOT NULL,
    excerpt_hash TEXT NOT NULL CHECK(length(excerpt_hash) = 64),
    quality_status TEXT NOT NULL CHECK(quality_status IN ('success', 'partial_success', 'needs_review', 'failed')),
    controlled_fallback INTEGER NOT NULL DEFAULT 0 CHECK(controlled_fallback IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(parse_key, physical_page)
        REFERENCES document_pages(parse_key, physical_page) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS subject_manifest_versions (
    manifest_version TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    input_vector_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
    status TEXT NOT NULL CHECK(status IN ('draft', 'pending_review', 'rejected', 'adopted')),
    object_version INTEGER NOT NULL DEFAULT 1 CHECK(object_version >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_change_operations (
    operation_id TEXT PRIMARY KEY,
    changeset_hash TEXT NOT NULL CHECK(length(changeset_hash) = 64),
    status TEXT NOT NULL CHECK(status IN (
        'prepared', 'approved', 'executing', 'db_committed_projection_pending',
        'completed', 'failed_before_commit', 'compensation_required', 'compensated'
    )),
    expected_catalog_revision INTEGER NOT NULL CHECK(expected_catalog_revision >= 0),
    target_subject_keys_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_changesets (
    operation_id TEXT PRIMARY KEY REFERENCES subject_change_operations(operation_id) ON DELETE RESTRICT,
    changeset_hash TEXT NOT NULL CHECK(length(changeset_hash) = 64),
    payload_json TEXT NOT NULL,
    input_version_vector_json TEXT NOT NULL,
    manifest_version TEXT,
    schema_version TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_approvals (
    approval_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE REFERENCES subject_change_operations(operation_id) ON DELETE RESTRICT,
    approval_hash TEXT NOT NULL CHECK(length(approval_hash) = 64),
    changeset_hash TEXT NOT NULL CHECK(length(changeset_hash) = 64),
    input_version_vector_json TEXT NOT NULL,
    manifest_version TEXT,
    schema_version TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    approver TEXT NOT NULL,
    approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_operation_preflight_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('operation_hash_conflict', 'prepared', 'approved')),
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_operation_attempts (
    attempt_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES subject_change_operations(operation_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed', 'conflict', 'idempotent_replay')),
    actor TEXT NOT NULL,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS subject_operation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES subject_change_operations(operation_id) ON DELETE RESTRICT,
    attempt_id TEXT REFERENCES subject_operation_attempts(attempt_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_lifecycle_events (
    lifecycle_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES subject_change_operations(operation_id) ON DELETE RESTRICT,
    subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    from_status TEXT NOT NULL CHECK(from_status IN ('absent', 'active', 'archived')),
    to_status TEXT NOT NULL CHECK(to_status IN ('active', 'archived')),
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(operation_id, subject_key, from_status, to_status)
);

CREATE TABLE IF NOT EXISTS subject_projection_outbox (
    task_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES subject_change_operations(operation_id) ON DELETE RESTRICT,
    target_revision INTEGER NOT NULL CHECK(target_revision >= 0),
    status TEXT NOT NULL CHECK(status IN ('pending', 'published', 'failed', 'superseded')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(operation_id, target_revision)
);

CREATE TABLE IF NOT EXISTS historical_correction_requests (
    correction_id TEXT PRIMARY KEY,
    subject_key TEXT NOT NULL REFERENCES subject_catalog(subject_key) ON DELETE RESTRICT,
    record_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    discovery_date TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('requested', 'applied', 'rejected')),
    applied_revision INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS historical_correction_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    correction_id TEXT NOT NULL REFERENCES historical_correction_requests(correction_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK(event_type IN ('requested', 'applied', 'rejected')),
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(correction_id, event_type)
);

CREATE TRIGGER IF NOT EXISTS trg_subject_changesets_no_update
BEFORE UPDATE ON subject_changesets
BEGIN SELECT RAISE(ABORT, 'subject_changesets are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_subject_changesets_no_delete
BEFORE DELETE ON subject_changesets
BEGIN SELECT RAISE(ABORT, 'subject_changesets are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_subject_approvals_no_update
BEFORE UPDATE ON subject_approvals
BEGIN SELECT RAISE(ABORT, 'subject_approvals are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_subject_approvals_no_delete
BEFORE DELETE ON subject_approvals
BEGIN SELECT RAISE(ABORT, 'subject_approvals are immutable'); END;

CREATE INDEX IF NOT EXISTS idx_document_parses_document
ON document_parses(document_hash, created_at);

CREATE INDEX IF NOT EXISTS idx_document_pages_status
ON document_pages(parse_key, status, physical_page);

CREATE INDEX IF NOT EXISTS idx_subject_evidence_page
ON subject_evidence(parse_key, physical_page, quality_status);

CREATE INDEX IF NOT EXISTS idx_subject_operations_status
ON subject_change_operations(status, created_at);

CREATE INDEX IF NOT EXISTS idx_subject_attempts_operation
ON subject_operation_attempts(operation_id, started_at);

CREATE INDEX IF NOT EXISTS idx_subject_lifecycle_events_subject
ON subject_lifecycle_events(subject_key, created_at);

CREATE INDEX IF NOT EXISTS idx_subject_projection_outbox_status
ON subject_projection_outbox(status, target_revision, created_at);

CREATE INDEX IF NOT EXISTS idx_subject_aliases_subject
ON subject_aliases(subject_key);

CREATE INDEX IF NOT EXISTS idx_subject_modules_subject
ON subject_module_identities(subject_key, canonical_name_normalized);

CREATE INDEX IF NOT EXISTS idx_subject_structure_topics_topic
ON subject_structure_topics(topic_key, structure_version);
