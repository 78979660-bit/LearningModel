from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from study_app.core.local_practice_service import (
    default_local_practice_output_directory,
    generate_local_practice_paper,
    spec_from_homework,
)
from study_app.core.local_practice_spec import LocalPracticePaperSpec
from study_app.data.database import connect, initialize_database


def prepare_bank(path: Path) -> Path:
    db_path = initialize_database(path)
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO practice_templates(template_id, title, description) VALUES ('CALC-SERIES', 'T', 'D')"
        )
        source_id = connection.execute(
            "INSERT INTO practice_sources(source_type, title) VALUES ('manual', 'fixture') RETURNING id"
        ).fetchone()["id"]
        for index, difficulty in enumerate((40, 54, 58, 62, 66, 82), start=1):
            connection.execute(
                """
                INSERT INTO practice_problems(
                    template_id, title, statement, answer_outline, difficulty_score,
                    subject_hint, topic_hint, source_id
                ) VALUES ('CALC-SERIES', ?, ?, ?, ?, '高等数学', '级数', ?)
                """,
                (f"题目 {index}", f"题面 {index}", f"答案 {index}", difficulty, source_id),
            )
    return db_path


def paper_spec() -> LocalPracticePaperSpec:
    return LocalPracticePaperSpec.create(
        subject="高等数学",
        template_id="CALC-SERIES",
        target_difficulty=60,
        question_count=6,
        paper_date="2026-09-17",
    )


def fake_renderer(counter=None, *, fail=False):
    def render(_paper, question_path, answer_path, **_kwargs):
        if counter is not None:
            counter["calls"] += 1
        question_path.write_bytes(b"%PDF-1.4\nQUESTION\n%%EOF\n")
        if fail:
            raise RuntimeError("render failed")
        answer_path.write_bytes(b"%PDF-1.4\nANSWER\n%%EOF\n")
        return {"question_pages": 1, "answer_pages": 1}
    return render


def test_bundle_commit_manifest_hashes_and_idempotency(tmp_path: Path) -> None:
    db_path = prepare_bank(tmp_path / "bank.sqlite")
    output = tmp_path / "output"
    counter = {"calls": 0}
    first = generate_local_practice_paper(
        paper_spec(), db_path=db_path, output_directory=output, renderer=fake_renderer(counter)
    )
    second = generate_local_practice_paper(
        paper_spec(), db_path=db_path, output_directory=output, renderer=fake_renderer(counter)
    )
    assert counter["calls"] == 1
    assert first.bundle_directory == second.bundle_directory
    assert first.question_pdf.is_file() and first.answer_pdf.is_file() and first.manifest_json.is_file()
    assert sorted(path.name for path in first.bundle_directory.iterdir()) == sorted(
        [first.question_pdf.name, first.answer_pdf.name, first.manifest_json.name]
    )
    manifest = json.loads(first.manifest_json.read_text(encoding="utf-8"))
    assert manifest["paper_id"] == first.paper_id
    assert manifest["recorded_date"] == "2026-09-17"
    assert manifest["outputs"]["question_pdf"]["sha256"] == hashlib.sha256(first.question_pdf.read_bytes()).hexdigest()
    assert manifest["outputs"]["answer_pdf"]["sha256"] == hashlib.sha256(first.answer_pdf.read_bytes()).hexdigest()


def test_render_failure_leaves_no_final_or_staging_bundle(tmp_path: Path) -> None:
    db_path = prepare_bank(tmp_path / "bank.sqlite")
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="render failed"):
        generate_local_practice_paper(
            paper_spec(), db_path=db_path, output_directory=output, renderer=fake_renderer(fail=True)
        )
    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_homework_adapter_uses_explicit_plan_fields() -> None:
    spec = spec_from_homework(
        "当天作业：参考难度 72/100；题库模板 CALC-SERIES；题量 4 题。",
        subject="高等数学",
        paper_date="2026-09-17",
    )
    assert spec.template_id == "CALC-SERIES"
    assert spec.target_difficulty == 72
    assert spec.question_count == 4


def test_default_output_isolated_from_chatgpt_temp(monkeypatch, tmp_path: Path) -> None:
    from study_app.core import local_practice_service

    export_root = tmp_path / "LearningModel" / "exports"
    monkeypatch.setattr(local_practice_service, "EXPORTS_DIR", export_root)
    path = default_local_practice_output_directory()
    assert path == export_root / "local_practice_papers"
    assert "generated_pdfs_temp" not in str(path)
