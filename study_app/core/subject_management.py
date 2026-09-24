"""Local, explicitly confirmed subject management. Never exposed as an LLM tool."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from study_app.core.subject_changeset import (
    approve_changeset, canonical_hash,
    load_changeset, prepare_changeset,
)
from study_app.core.subject_identity import normalize_alias, normalize_name
from study_app.data.subject_repository import SubjectCatalogRepository

VERSION = 'subject-management-v1'
LABELS = {'introduce': '引入学科', 'remove': '移除学科（保留历史）', 'restore': '恢复学科'}


def list_subjects(db_path):
    with SubjectCatalogRepository(db_path).transaction() as connection:
        return [dict(row) for row in connection.execute(
            'SELECT subject_key,canonical_name,display_name,lifecycle_status FROM subject_catalog ORDER BY canonical_name_normalized'
        )]


def parse_outline(text):
    """Only explicit user-supplied module/topic lines; no generated identities accepted."""
    if len(text) > 100_000:
        raise ValueError('目录过长，最多 100,000 字符')
    modules = {}
    seen = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split('/', 1)
        if len(parts) != 2:
            raise ValueError('每行格式为：章节 / 知识点')
        module, topic = [normalize_name(value) for value in parts]
        identity = (normalize_alias(module), normalize_alias(topic))
        if identity in seen:
            raise ValueError(f'目录中存在重复知识点：{line}')
        seen.add(identity)
        modules.setdefault(module, []).append(topic)
    if not modules or len(seen) > 500 or len(modules) > 100:
        raise ValueError('请提供 1–500 个知识点，章节最多 100 个')
    if len({normalize_alias(name) for name in modules}) != len(modules):
        raise ValueError('章节名称存在大小写或全半角重复')
    return [{'name': name, 'topics': topics} for name, topics in modules.items()]


def _impact(connection, key):
    aliases = [row[0] for row in connection.execute(
        'SELECT alias FROM subject_aliases WHERE subject_key=? ORDER BY alias', (key,))]
    # Names and formal identity are both supported by older/newer plan writers.
    aliases.append(key)
    placeholders = ','.join('?' for _ in aliases)
    records = [dict(row) for row in connection.execute(
        f'SELECT * FROM learning_records WHERE subject_name IN ({placeholders}) ORDER BY id', aliases)]
    plans = [dict(row) for row in connection.execute(
        f'''SELECT DISTINCT p.* FROM study_plans p LEFT JOIN study_plan_items i ON i.plan_id=p.id
        WHERE p.status='active' AND (p.subject_scope IN ({placeholders}) OR i.subject_id IN ({placeholders})) ORDER BY p.id''',
        (*aliases, *aliases))]
    items = []
    for plan in plans:
        items.extend(dict(row) for row in connection.execute(
            'SELECT * FROM study_plan_items WHERE plan_id=? ORDER BY id', (plan['id'],)))
    return {'records': len(records), 'plan_ids': [row['id'] for row in plans],
            'plan_scopes': [row['subject_scope'] for row in plans],
            'fingerprint': canonical_hash({'records': records, 'plans': plans, 'items': items})}


def prepare_management(db_path, kind, *, name='', outline='', subject_key=None):
    if kind not in LABELS:
        raise ValueError('不支持的学科管理操作')
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction() as connection:
        connection.execute('BEGIN')
        revision = connection.execute('SELECT catalog_revision FROM subject_catalog_state').fetchone()[0]
        if kind == 'introduce':
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_topic_registry' AND type='table'").fetchone():
                raise ValueError('知识点身份结构尚未安装，不能安全引入学科')
            name = normalize_name(name, label='学科名称')
            if '/' in name:
                raise ValueError('学科名称不能包含 /')
            modules = parse_outline(outline)
            if connection.execute('SELECT 1 FROM subject_aliases WHERE alias_normalized=?', (normalize_alias(name),)).fetchone():
                raise ValueError('该名称或别名已存在；已移除学科请使用恢复入口')
            if any(normalize_alias(row[0]) == normalize_alias(name) for row in connection.execute('SELECT name FROM subjects')):
                raise ValueError('已有同名历史学科，请先核对目录，不能覆盖或重新绑定历史')
            key = 'subject:v1:' + uuid.uuid4().hex
            version = None
            impact = {'records': 0, 'plan_ids': [], 'plan_scopes': [], 'fingerprint': None}
        else:
            row = connection.execute('SELECT * FROM subject_catalog WHERE subject_key=?', (subject_key,)).fetchone()
            expected = 'active' if kind == 'remove' else 'archived'
            if row is None or row['lifecycle_status'] != expected:
                raise ValueError('目标不存在或状态已变化，请重新选择学科')
            name, key, version = row['canonical_name'], row['subject_key'], row['object_version']
            modules = []
            impact = _impact(connection, key)
    vector = {'catalog_revision': revision, 'subjects': {key: version},
              'manifest_version': None, 'manifest_hash': None, 'manifest_object_version': None}
    action = {'type': VERSION, 'kind': kind, 'name': name, 'subject_key': key, 'modules': modules, 'impact': impact}
    return prepare_changeset(db_path, operation_id='subject-ui:' + uuid.uuid4().hex,
        payload={'schema_version': 'subject-changeset-v1', 'actions': [action],
                 'expected_diff': {'operation': LABELS[kind], 'subject': name, 'impact': impact,
                                   'module_count': len(modules), 'topic_count': sum(len(m['topics']) for m in modules)}},
        input_version_vector=vector, manifest_version=None, validator_version=VERSION, target_subject_keys=(key,))


def _backup(db_path, operation_id):
    destination = Path(db_path).parent / 'backups' / 'subject-management'
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / (operation_id.split(':')[-1] + '-' + uuid.uuid4().hex + '.sqlite')
    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro', uri=True) as source, sqlite3.connect(path) as backup:
        source.backup(backup)
        if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('备份校验失败，已中止操作')
    return str(path)


def _introduce(connection, action, operation_id):
    from study_app.core.subject_capabilities import CAPABILITY_KEYS
    from study_app.core.topic_identity import topic_key_for_id
    name, key = action['name'], action['subject_key']
    if any(normalize_alias(row[0]) == normalize_alias(name) for row in connection.execute('SELECT name FROM subjects')):
        raise ValueError('已有同名历史学科，不能覆盖；请重新预览')
    if connection.execute('SELECT 1 FROM subject_aliases WHERE alias_normalized=?', (normalize_alias(name),)).fetchone():
        raise ValueError('学科名称已被占用，请重新预览')
    connection.execute("INSERT INTO subject_catalog(subject_key,canonical_name,canonical_name_normalized,display_name,lifecycle_status) VALUES (?,?,?,?,'active')", (key, name, normalize_alias(name), name))
    connection.execute('INSERT INTO subject_aliases(alias_normalized,alias,subject_key) VALUES (?,?,?)', (normalize_alias(name), name, key))
    for capability in sorted(CAPABILITY_KEYS):
        connection.execute('INSERT INTO subject_capabilities(subject_key,capability_key,declared_supported) VALUES (?,?,?)',
                           (key, capability, int(capability in {'study_plan', 'generic_practice'})))
    sid = connection.execute('INSERT INTO subjects(name) VALUES (?)', (name,)).lastrowid
    structure = 'structure:v1:' + uuid.uuid4().hex
    connection.execute('INSERT INTO subject_structure_versions(structure_version,subject_key,source_reference,is_current) VALUES (?,?,?,1)', (structure, key, operation_id))
    for module_order, module in enumerate(action['modules']):
        mid = connection.execute('INSERT INTO modules(subject_id,name) VALUES (?,?)', (sid, module['name'])).lastrowid
        module_key = 'module:v1:' + uuid.uuid4().hex
        connection.execute('INSERT INTO subject_module_identities(module_key,subject_key,canonical_name,canonical_name_normalized,display_name) VALUES (?,?,?,?,?)',
                           (module_key, key, module['name'], normalize_alias(module['name']), module['name']))
        connection.execute('INSERT INTO subject_structure_modules VALUES (?,?,?)', (structure, module_key, module_order))
        for topic_order, topic in enumerate(module['topics']):
            tid = connection.execute('INSERT INTO topics(module_id,name) VALUES (?,?)', (mid, topic)).lastrowid
            generation = 0
            topic_key = topic_key_for_id(tid, generation)
            while connection.execute('SELECT 1 FROM knowledge_topic_registry WHERE topic_key=?', (topic_key,)).fetchone():
                generation += 1
                topic_key = topic_key_for_id(tid, generation)
            connection.execute('INSERT INTO knowledge_topic_registry(topic_key,topic_id) VALUES (?,?)', (topic_key, tid))
            connection.execute('INSERT INTO subject_structure_topics(structure_version,module_key,topic_key,topic_order) VALUES (?,?,?,?)', (structure, module_key, topic_key, topic_order))


def execute_management(db_path, operation_id, *, confirmation_name, acknowledged=False):
    """Explicit UI confirmation, backup, immutable binding, CAS and atomic writes."""
    from study_app.core.subject_executor import _approved_rows, _check_version_vector
    prepared = load_changeset(db_path, operation_id)
    actions = prepared.payload.get('actions', [])
    if len(actions) != 1 or actions[0].get('type') != VERSION or prepared.validator_version != VERSION:
        raise ValueError('不是受支持的学科管理预览')
    action = actions[0]
    if action['kind'] not in LABELS or acknowledged is not True or confirmation_name != action['name']:
        raise ValueError('必须勾选影响说明，并准确输入学科全名')
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction() as connection:
        if prepared.status in {'completed', 'db_committed_projection_pending'}:
            event = connection.execute("SELECT detail_json FROM subject_operation_events WHERE operation_id=? AND event_type='subject_management_applied'", (operation_id,)).fetchone()
            if event:
                return {**json.loads(event[0]), 'idempotent': True}
    if prepared.status not in {'prepared', 'approved'}:
        raise ValueError('预览已失效，请重新预览')
    approve_changeset(db_path, operation_id, approver='local-user-explicit-confirmation')
    attempt = 'attempt:' + uuid.uuid4().hex
    try:
        with repository.transaction(readonly=False) as connection:
            connection.execute('BEGIN IMMEDIATE')
            operation, changeset, payload, vector = _approved_rows(connection, operation_id)
            _check_version_vector(connection, operation, vector)
            action = payload['actions'][0]
            if action['kind'] != 'introduce' and _impact(connection, action['subject_key']) != action['impact']:
                raise ValueError('预览后的记录或计划发生变化，请重新预览')
            backup_path = _backup(db_path, operation_id)
            connection.execute("INSERT INTO subject_operation_attempts(attempt_id,operation_id,status,actor) VALUES (?,?,'running','local-user')", (attempt, operation_id))
            kind, key = action['kind'], action['subject_key']
            before = 'absent' if kind == 'introduce' else 'active' if kind == 'remove' else 'archived'
            after = 'archived' if kind == 'remove' else 'active'
            if kind == 'introduce':
                _introduce(connection, action, operation_id)
            else:
                changed = connection.execute('UPDATE subject_catalog SET lifecycle_status=?,object_version=object_version+1,updated_at=CURRENT_TIMESTAMP WHERE subject_key=? AND lifecycle_status=?', (after, key, before)).rowcount
                if changed != 1:
                    raise ValueError('学科状态已变化')
                if kind == 'remove':
                    for plan_id in action['impact']['plan_ids']:
                        connection.execute("UPDATE study_plans SET status='archived',archived_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'", (plan_id,))
            connection.execute('UPDATE subject_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1')
            revision = connection.execute('SELECT catalog_revision FROM subject_catalog_state').fetchone()[0]
            connection.execute('INSERT INTO subject_lifecycle_events(operation_id,subject_key,from_status,to_status,reason,actor) VALUES (?,?,?,?,?,?)', (operation_id, key, before, after, LABELS[kind], 'local-user'))
            result = {'operation_id': operation_id, 'subject_key': key, 'name': action['name'], 'kind': kind,
                      'backup_path': backup_path, 'catalog_revision': revision, 'status': 'db_committed_projection_pending'}
            connection.execute("INSERT INTO subject_operation_events(operation_id,attempt_id,event_type,detail_json) VALUES (?,?,'subject_management_applied',?)", (operation_id, attempt, json.dumps(result, ensure_ascii=False)))
            connection.execute("INSERT INTO subject_projection_outbox(task_id,operation_id,target_revision,status) VALUES (?,?,?,'pending')", ('projection:'+operation_id, operation_id, revision))
            connection.execute("UPDATE subject_operation_attempts SET status='succeeded',finished_at=CURRENT_TIMESTAMP WHERE attempt_id=?", (attempt,))
            connection.execute("UPDATE subject_change_operations SET status='db_committed_projection_pending',updated_at=CURRENT_TIMESTAMP WHERE operation_id=?", (operation_id,))
    except Exception as error:
        with repository.transaction(readonly=False) as connection:
            connection.execute("UPDATE subject_change_operations SET status='failed_before_commit',updated_at=CURRENT_TIMESTAMP WHERE operation_id=? AND status='approved'", (operation_id,))
            connection.execute("INSERT INTO subject_operation_events(operation_id,event_type,detail_json) VALUES (?,'subject_management_failed',?)", (operation_id, json.dumps({'error': str(error)}, ensure_ascii=False)))
        raise
    try:
        from study_app.core.subject_projection import process_projection_outbox
        process_projection_outbox(db_path, Path(db_path).with_name('subject_catalog_projection.json'), task_id='projection:'+operation_id)
        result['status'] = 'completed'
    except Exception:
        result['warning'] = '学科变更已保存；目录导出待重试。请勿重复创建学科。'
    return result
