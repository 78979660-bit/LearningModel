"""Reproduce the review findings against persistence and file boundaries."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import Mock, patch
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from study_app.core.budgeted_day_plan import BudgetPlanInput, build_budgeted_day_plan
from study_app.core.plan_candidates import CandidateCollection, PlanCandidate, task_id_for_source
from study_app.data import database, db_runtime
from study_app.data.subject_repository import SubjectCatalogRepository, install_subject_lifecycle_schema
from tools.extract_source_notices import collect


def make_plan(subject="A"):
    item = PlanCandidate(task_id_for_source(subject, "model_topic_practice", "topic:first"),
                         subject, "model_topic_practice", "topic:first", "Practice", {}, 20, "user")
    return build_budgeted_day_plan(
        BudgetPlanInput("2026-09-16", 60, (subject,), as_of_date="2026-09-16"),
        CandidateCollection((item,), ()),
    )


@pytest.mark.parametrize("section", ["selected", "completed"])
@pytest.mark.parametrize("subject_state", ["archived", "unregistered"])
def test_budget_write_rechecks_lifecycle_and_preserves_active_plan(tmp_path, section, subject_state):
    db = tmp_path / "plans.sqlite"
    database.initialize_database(db)
    install_subject_lifecycle_schema(db)
    repo = SubjectCatalogRepository(db)
    subject = repo.create_subject("A", capabilities={"study_plan": True})
    plan = make_plan()
    old_id = database.create_budgeted_day_plan("2026-09-16", 60, plan, db)
    if subject_state == "archived":
        with database.connect(db) as connection:
            connection.execute("UPDATE subject_catalog SET lifecycle_status='archived' WHERE subject_key=?",
                               (subject.subject_key,))
    else:
        plan = make_plan("Unknown")
    if section == "completed":
        plan = replace(plan, completed=plan.selected, selected=())
    with pytest.raises(ValueError):
        database.create_budgeted_day_plan("2026-09-16", 60, plan, db)
    with database.connect_readonly(db) as connection:
        assert [tuple(row) for row in connection.execute("SELECT id,status FROM study_plans")] == [(old_id, "active")]


def test_budget_excluded_archived_subject_remains_explainable(tmp_path):
    db = tmp_path / "plans.sqlite"
    database.initialize_database(db)
    install_subject_lifecycle_schema(db)
    SubjectCatalogRepository(db).create_subject("A", capabilities={"study_plan": True})
    with database.connect(db) as connection:
        connection.execute("UPDATE subject_catalog SET lifecycle_status='archived'")
    plan = make_plan()
    plan = replace(plan, excluded=plan.selected, selected=(), planned_minutes=0, remaining_minutes=60)
    database.create_budgeted_day_plan("2026-09-16", 60, plan, db)
    saved = database.get_budgeted_day_plan("2026-09-16", db_path=db)
    assert saved["items"][0]["section_key"] == "excluded"


@pytest.mark.parametrize("failure_step", [1, 2])
def test_readonly_probe_failure_closes_connection_and_preserves_cause(tmp_path, failure_step):
    db = tmp_path / "probe.sqlite"
    db.touch()
    connection = Mock()
    error = sqlite3.OperationalError("probe failed")
    connection.execute.side_effect = [error] if failure_step == 1 else [Mock(), error]
    with patch.object(db_runtime.sqlite3, "connect", return_value=connection):
        with pytest.raises(db_runtime.DatabaseNotInitializedError) as raised:
            db_runtime.connect_readonly(db)
    connection.close.assert_called_once_with()
    assert raised.value.__cause__ is error


def test_readonly_connect_failure_does_not_mask_sqlite_error(tmp_path):
    db = tmp_path / "probe.sqlite"
    db.touch()
    error = sqlite3.OperationalError("open failed")
    with patch.object(db_runtime.sqlite3, "connect", side_effect=error):
        with pytest.raises(db_runtime.DatabaseNotInitializedError) as raised:
            db_runtime.connect_readonly(db)
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("first_parent", ["record_parser", "attachment_parser"])
def test_settings_clears_children_only_after_both_parsers_are_off(first_parent):
    from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QPushButton
    from study_app.ai.providers import LLMSettings, LLM_FEATURES
    from study_app.ui import settings_page

    app = QApplication.instance() or QApplication([])
    children = {"error_classifier", "knowledge_mapping", "difficulty_calibration"}
    settings = LLMSettings(
        enabled=True, provider="deepseek", model="deepseek-chat", custom_base_url="",
        api_key="", single_call_token_limit=8000, daily_budget_cny=0,
        allow_upload_images=False, allow_upload_pdfs=False,
        enabled_features=tuple(children | {"record_parser", "attachment_parser"}),
    )
    with (patch.object(settings_page, "load_llm_settings", return_value=settings),
          patch.object(settings_page, "list_llm_call_audits", return_value=[]),
          patch.object(settings_page, "save_llm_settings") as save,
          patch.object(QMessageBox, "information")):
        page = settings_page.settings_page()
        try:
            by_label = {item.text(): item for item in page.findChildren(QCheckBox)}
            boxes = {key: by_label[label] for key, label in LLM_FEATURES.items()}
            boxes[first_parent].setChecked(False)
            assert all(boxes[key].isEnabled() and boxes[key].isChecked() for key in children)
            other = "attachment_parser" if first_parent == "record_parser" else "record_parser"
            boxes[other].setChecked(False)
            assert all(not boxes[key].isEnabled() and not boxes[key].isChecked() for key in children)
            boxes[other].setChecked(True)
            assert all(boxes[key].isEnabled() and not boxes[key].isChecked() for key in children)
            boxes[other].setChecked(False)
            # A programmatic stale selection must also be filtered by persistence.
            boxes["error_classifier"].setChecked(True)
            next(button for button in page.findChildren(QPushButton)
                 if button.text() == "\u4fdd\u5b58 LLM \u8bbe\u7f6e").click()
            assert not children.intersection(save.call_args.args[0].enabled_features)
        finally:
            page.close()


@pytest.mark.parametrize("filename", ["../outside.zip", "nested/file.zip", "C:/outside.zip",
                                    "C:\\outside.zip", "\\\\server\\share\\file.zip", ".", "..", "", None])
def test_notice_manifest_rejects_unsafe_filename_before_open(tmp_path, filename):
    archives = tmp_path / "archives"
    archives.mkdir()
    (archives / "SOURCE-MANIFEST.json").write_text(json.dumps({"archives": [
        {"filename": filename, "sha256": "0" * 64}]}), encoding="utf-8")
    with patch.object(Path, "open", autospec=True, wraps=Path.open) as opened:
        # Use a narrow spy: manifest is read through read_text, archives use open.
        def guarded_open(path, mode="r", *args, **kwargs):
            if mode == "rb":
                pytest.fail("Archive was opened before filename validation")
            return original_open(path, mode, *args, **kwargs)

        opened.side_effect = guarded_open
        with pytest.raises(ValueError, match="Unsafe archive filename"):
            collect(archives, tmp_path / "notices")
        assert not any(call.args[1:] == ("rb",) for call in opened.call_args_list)


original_open = Path.open


def test_notice_extraction_accepts_verified_local_archive(tmp_path):
    archives = tmp_path / "archives"
    archives.mkdir()
    archive = archives / "package.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("package/LICENSE", "license text")
    (archives / "SOURCE-MANIFEST.json").write_text(json.dumps({"archives": [
        {"filename": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}]}), encoding="utf-8")
    output = tmp_path / "notices"
    collect(archives, output)
    assert (output / "package/package/LICENSE").read_text() == "license text"
