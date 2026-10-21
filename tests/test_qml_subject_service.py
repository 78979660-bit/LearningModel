import json
import sqlite3

import pytest

from study_app.data.database import initialize_database
from study_app.data.subject_repository import install_subject_lifecycle_schema
from study_app.quick.subject_service import SubjectOperationOutcomeUnknownError, SubjectService


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "subjects.sqlite"
    initialize_database(path)
    install_subject_lifecycle_schema(path)
    from study_app.data.database import KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL

    with sqlite3.connect(path) as connection:
        connection.execute(KNOWLEDGE_TOPIC_REGISTRY_TABLE_SQL)
    return path


def service_for(db_path, *, initialize=None):
    return SubjectService(
        db_path,
        db_path.with_name("model.json"),
        db_path.with_name("records.json"),
        initialize=initialize,
    )


def introduce(service, name="数学", outline="基础 / 集合\n基础 / 函数"):
    preview = service.preview("introduce", name=name, outline=outline)
    result = service.confirm(preview["token"], name, True)
    return preview, result


def test_constructor_is_lazy_and_load_exposes_active_archive_capabilities_and_revision(db_path):
    initialized = []
    service = service_for(db_path, initialize=lambda: initialized.append(True))
    assert initialized == []

    data = service.load()
    assert initialized == [True]
    assert data["catalogRevision"] == 0
    assert data["subjects"] == []

    _, result = introduce(service)
    data = service.load()
    subject = data["subjects"][0]
    assert subject["subjectKey"] == result["subjectKey"]
    assert subject["lifecycleStatus"] == "active"
    assert subject["moduleCount"] == 1
    assert subject["topicCount"] == 2
    assert subject["capabilities"]["study_plan"] is True
    assert data["catalogRevision"] == 1
    assert data["history"][0]["toStatus"] == "active"
    assert data["operations"][0]["projectionStatus"] == "published"
    assert "operation_id" not in data["operations"][0]
    assert "changeset_hash" not in data["operations"][0]


def test_introduce_requires_full_name_and_acknowledgement_then_rejects_duplicate(db_path):
    service = service_for(db_path)
    preview = service.preview(
        "introduce", name="离散数学", outline="逻辑 / 命题"
    )
    assert preview["requiresConfirmation"] is True
    assert preview["moduleCount"] == 1
    assert preview["topicCount"] == 1
    assert preview["token"]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM subject_catalog").fetchone()[0] == 0

    with pytest.raises(ValueError, match="勾选"):
        service.confirm(preview["token"], "离散数学", False)
    with pytest.raises(ValueError, match="全名"):
        service.confirm(preview["token"], "离散", True)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM subject_catalog").fetchone()[0] == 0

    result = service.confirm(preview["token"], "离散数学", True)
    assert result["saved"] is True
    assert result["backupVerified"] is True
    assert result["projectionPending"] is False
    assert result["operationId"].startswith("subject-op:")
    repeated = service.confirm(preview["token"], "离散数学", True)
    assert repeated["saved"] is True
    assert repeated["idempotent"] is True
    assert repeated["projectionPending"] is False
    assert repeated["operationId"] == result["operationId"]

    with pytest.raises(ValueError, match="已存在"):
        service.preview("introduce", name="离散数学", outline="逻辑 / 其他")


def test_stale_record_impact_invalidates_remove_preview(db_path):
    service = service_for(db_path)
    _, created = introduce(service)
    preview = service.preview("remove", subject_key=created["subjectKey"])
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO learning_records(record_date,subject_name,raw_json) VALUES (?,?,?)",
            ("2026-10-05", "数学", "{}"),
        )

    with pytest.raises(ValueError, match="重新预览"):
        service.confirm(preview["token"], "数学", True)
    assert service.load()["subjects"][0]["lifecycleStatus"] == "active"


def test_fractional_repository_record_can_be_archived_and_restored(db_path):
    service = service_for(db_path)
    _, created = introduce(service)
    from study_app.data.database import add_learning_record

    record_id = add_learning_record(
        {
            "date": "2026-10-05",
            "subject": "数学",
            "module": "基础",
            "topic": "集合",
            "activity": "review",
            "source": "self_study",
            "score": 82.5,
            "duration_minutes": 12.5,
            "note": "fractional repository metadata",
            "problems": [
                {
                    "title": "集合题",
                    "status": "correct",
                    "difficulty_score": 67.5,
                }
            ],
        },
        db_path=db_path,
    )

    removal = service.preview("remove", subject_key=created["subjectKey"])
    assert removal["impact"]["records"] == 1
    removed = service.confirm(removal["token"], "数学", True)
    assert removed["saved"] is True
    assert next(
        row for row in service.load()["subjects"] if row["subjectKey"] == created["subjectKey"]
    )["lifecycleStatus"] == "archived"

    restore = service.preview("restore", subject_key=created["subjectKey"])
    restored = service.confirm(restore["token"], "数学", True)
    assert restored["saved"] is True
    assert next(
        row for row in service.load()["subjects"] if row["subjectKey"] == created["subjectKey"]
    )["lifecycleStatus"] == "active"
    with sqlite3.connect(db_path) as connection:
        record = connection.execute(
            "SELECT id,score,duration_minutes FROM learning_records WHERE id=?", (record_id,)
        ).fetchone()
    assert record == (record_id, 82.5, 12.5)


def test_fractional_metadata_change_after_preview_rejects_stale_archive(db_path):
    service = service_for(db_path)
    _, created = introduce(service)
    from study_app.data.database import add_learning_record

    add_learning_record(
        {
            "date": "2026-10-05",
            "subject": "数学",
            "score": 82.5,
            "duration_minutes": 12.5,
            "note": "fractional repository metadata",
        },
        db_path=db_path,
    )
    preview = service.preview("remove", subject_key=created["subjectKey"])
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE learning_records SET duration_minutes=12.75 WHERE subject_name='数学'")

    with pytest.raises(ValueError, match="重新预览"):
        service.confirm(preview["token"], "数学", True)
    state = service.load()
    assert next(
        row for row in state["subjects"] if row["subjectKey"] == created["subjectKey"]
    )["lifecycleStatus"] == "active"
    assert any(
        row["kind"] == "remove" and row["status"] == "failed_before_commit"
        for row in state["operations"]
    )
    assert len(state["history"]) == 1


def test_backup_failure_never_reports_saved_or_changes_catalog(db_path, monkeypatch):
    service = service_for(db_path)
    preview = service.preview("introduce", name="物理", outline="力学 / 运动")
    import study_app.core.subject_management as management

    monkeypatch.setattr(
        management,
        "_backup",
        lambda *_: (_ for _ in ()).throw(OSError("backup unavailable")),
    )
    with pytest.raises(OSError, match="backup unavailable"):
        service.confirm(preview["token"], "物理", True)
    assert service.load()["subjects"] == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM topics").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM subject_operation_events WHERE event_type='subject_management_applied'"
        ).fetchone()[0] == 0


def test_remove_preview_counts_records_and_shared_plans_then_restore_keeps_same_key(db_path):
    service = service_for(db_path)
    _, math_result = introduce(service, "数学")
    introduce(service, "物理", "力学 / 运动")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO learning_records(record_date,subject_name,raw_json) VALUES (?,?,?)",
            ("2026-10-05", "数学", "{}"),
        )
        plan_id = connection.execute(
            """INSERT INTO study_plans(subject_scope,start_date,end_date,input_signature,status,plan_json)
               VALUES (?,?,?,?, 'active', ?)""",
            ("全部活动学科", "2026-10-05", "2026-10-06", "shared", json.dumps({})),
        ).lastrowid
        connection.execute(
            """INSERT INTO study_plan_items(
                   plan_id,section_key,section_title,item_type,item_text,item_order,item_hash,subject_id
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (plan_id, "math", "数学", "task", "复习数学", 0, "math-item", "数学"),
        )
        connection.execute(
            """INSERT INTO study_plan_items(
                   plan_id,section_key,section_title,item_type,item_text,item_order,item_hash,subject_id
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (plan_id, "physics", "物理", "task", "复习物理", 1, "physics-item", "物理"),
        )

    removal = service.preview("remove", subject_key=math_result["subjectKey"])
    assert removal["impact"] == {
        "records": 1,
        "plans": 1,
        "planScopes": ["全部活动学科"],
    }
    removed = service.confirm(removal["token"], "数学", True)
    assert removed["saved"] is True
    archived_subject = next(
        row for row in service.load()["subjects"] if row["subjectKey"] == math_result["subjectKey"]
    )
    assert archived_subject["lifecycleStatus"] == "archived"
    assert archived_subject["subjectKey"] == math_result["subjectKey"]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM learning_records").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM study_plans WHERE id=?", (plan_id,)).fetchone()[0] == "archived"

    restore = service.preview("restore", subject_key=math_result["subjectKey"])
    restored = service.confirm(restore["token"], "数学", True)
    assert restored["saved"] is True
    active_subject = next(
        row for row in service.load()["subjects"] if row["subjectKey"] == math_result["subjectKey"]
    )
    assert active_subject["lifecycleStatus"] == "active"
    assert active_subject["subjectKey"] == math_result["subjectKey"]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM learning_records").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM study_plans WHERE id=?", (plan_id,)).fetchone()[0] == "archived"


def test_projection_failure_is_saved_and_exact_retry_is_available_from_audit(db_path, monkeypatch):
    service = service_for(db_path)
    import study_app.core.subject_projection as projection

    original = projection.process_projection_outbox
    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("projection unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(projection, "process_projection_outbox", fail_once)
    preview = service.preview("introduce", name="统计学", outline="描述统计 / 均值")
    result = service.confirm(preview["token"], "统计学", True)
    assert result["saved"] is True
    assert result["projectionPending"] is True
    assert result["backupVerified"] is True

    audit = service.load()["operations"][0]
    assert audit["canRetryProjection"] is True
    assert audit["operationId"] == result["operationId"]
    retried = service.retry_projection(audit["operationId"])
    assert retried["saved"] is True
    assert retried["projectionPending"] is False
    assert retried["status"] == "completed"
    assert attempts == [True, True]
    assert service.load()["operations"][0]["canRetryProjection"] is False


def test_repeated_projection_failure_still_proves_only_the_committed_operation(db_path, monkeypatch):
    service = service_for(db_path)
    import study_app.core.subject_projection as projection

    monkeypatch.setattr(
        projection,
        "process_projection_outbox",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("projection unavailable")),
    )
    preview = service.preview("introduce", name="概率论", outline="概率 / 条件概率")
    result = service.confirm(preview["token"], "概率论", True)
    assert result["saved"] is True
    assert result["projectionPending"] is True
    retry = service.retry_projection(result["operationId"])
    assert retry["saved"] is True
    assert retry["projectionPending"] is True
    assert "已保存" in retry["message"]
    assert len(service.load()["subjects"]) == 1


def test_postcommit_exception_is_reported_saved_only_after_matching_event_proof(db_path, monkeypatch):
    service = service_for(db_path)
    import study_app.core.subject_management as management

    original = management.execute_management

    def raise_after_commit(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("injected readback failure")

    monkeypatch.setattr(management, "execute_management", raise_after_commit)
    preview = service.preview("introduce", name="运筹学", outline="线性规划 / 可行域")
    result = service.confirm(preview["token"], "运筹学", True)
    assert result["saved"] is True
    assert result["projectionPending"] is False
    assert result["status"] == "completed"
    assert result["warning"] == ""


def test_success_event_from_another_operation_never_proves_this_preview(db_path, monkeypatch):
    service = service_for(db_path)
    first_preview, first_result = introduce(service, "已存在的真实操作", "基础 / 条目")
    first_core_id = service._previews[first_preview["token"]].core_operation_id
    second = service.preview("introduce", name="未执行操作", outline="基础 / 其他条目")
    import study_app.core.subject_management as management

    monkeypatch.setattr(
        management,
        "execute_management",
        lambda *_args, **_kwargs: {
            "operation_id": first_core_id,
            "subject_key": first_result["subjectKey"],
            "name": "已存在的真实操作",
            "kind": "introduce",
            "status": "completed",
        },
    )
    with pytest.raises(SubjectOperationOutcomeUnknownError, match="无法核实"):
        service.confirm(second["token"], "未执行操作", True)
    assert [row["name"] for row in service.load()["subjects"]] == ["已存在的真实操作"]


def test_outline_import_reads_explicit_text_and_strict_json_without_persisting(db_path, tmp_path):
    initialized = []
    service = service_for(db_path, initialize=lambda: initialized.append(True))
    text_path = tmp_path / "explicit-outline.txt"
    text_path.write_text("基础 / 集合\n基础 / 函数\n", encoding="utf-8-sig")
    imported_text = service.import_outline(text_path)
    assert imported_text == {
        "outline": "基础 / 集合\n基础 / 函数",
        "filename": "explicit-outline.txt",
    }

    json_path = tmp_path / "outline.json"
    json_path.write_text(
        json.dumps(
            {
                "name": "数学",
                "modules": [{"name": "基础", "topics": ["集合", "函数"]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported_json = service.import_outline(json_path)
    assert imported_json == {
        "name": "数学",
        "outline": "基础 / 集合\n基础 / 函数",
        "filename": "outline.json",
    }
    assert initialized == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM subject_catalog").fetchone()[0] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "数学", "modules": [{"name": "基础", "topics": ["集合"]}], "extra": 1},
        {"modules": [{"name": "基础", "topics": ["集合"]}]},
        {"name": "数学", "modules": [{"name": "基础", "topics": [1]}]},
        {"name": "数学", "modules": [{"name": "基础", "topics": ["集合"]}] * 101},
    ],
)
def test_outline_import_rejects_invalid_json_shapes(db_path, tmp_path, payload):
    service = service_for(db_path)
    json_path = tmp_path / "invalid.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        service.import_outline(json_path)


def test_outline_import_rejects_oversized_files_and_non_utf8(db_path, tmp_path):
    service = service_for(db_path)
    large_path = tmp_path / "large.txt"
    large_path.write_bytes(b"x" * (300 * 1024 + 1))
    with pytest.raises(ValueError, match="300 KB"):
        service.import_outline(large_path)
    invalid_encoding = tmp_path / "invalid.txt"
    invalid_encoding.write_bytes(b"\xff\xfe\x00\x00")
    with pytest.raises(ValueError, match="UTF-8"):
        service.import_outline(invalid_encoding)
