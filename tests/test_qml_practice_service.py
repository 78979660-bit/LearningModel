from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from study_app.core.dashboard import DashboardState, SubjectSummary
from study_app.core.local_practice_pdf import LocalPracticePDFError
from study_app.data.database import connect, initialize_database
from study_app.quick.practice_service import PracticeService


AS_OF = "2026-10-04"


def _practice_db(path: Path, *, question_count: int = 5) -> Path:
    db_path = initialize_database(path)
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO practice_templates(template_id, subject_hint, topic_hint, title, description) "
            "VALUES ('QML-TEST', 'Algebra', 'Linear equations', 'QML worksheet', 'isolated fixture')"
        )
        source_id = connection.execute(
            "INSERT INTO practice_sources(source_type, title) VALUES ('manual', 'fixture') RETURNING id"
        ).fetchone()["id"]
        for index in range(question_count):
            connection.execute(
                """
                INSERT INTO practice_problems(
                    template_id, title, statement, answer_outline, difficulty_score,
                    subject_hint, topic_hint, source_id
                ) VALUES ('QML-TEST', ?, ?, ?, ?, 'Algebra', 'Linear equations', ?)
                """,
                (
                    f"Fixture question {index + 1}",
                    f"Solve x + {index + 1} = {index + 4}.",
                    f"x = {3 - index}.",
                    40 + index * 8,
                    source_id,
                ),
            )
    return db_path


def _service_paths(tmp_path: Path):
    db_path = _practice_db(tmp_path / "practice.sqlite")
    model_path = tmp_path / "model.json"
    records_path = tmp_path / "records.json"
    model_path.write_text(json.dumps({"subjects": []}), encoding="utf-8")
    records_path.write_text(json.dumps({"records": []}), encoding="utf-8")
    return db_path, model_path, records_path


def test_real_local_practice_pdfs_are_parseable_and_paths_stay_behind_tokens(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    output_dir = tmp_path / "exports"
    service = PracticeService(
        db_path,
        model_path,
        records_path,
        today=AS_OF,
        output_dir=output_dir,
    )

    choices = service.load()
    assert choices["status"] == "ready"
    assert choices["templates"] == [
        {
            "id": "QML-TEST",
            "name": "QML worksheet",
            "subject": "Algebra",
            "topic": "Linear equations",
            "description": "isolated fixture",
            "localAllowed": True,
        }
    ]

    result = service.generate_local_practice(
        subject="Algebra",
        template_id="QML-TEST",
        topic="Linear equations",
        target_difficulty=60,
        question_count=4,
        paper_date=AS_OF,
        title="Algebra worksheet",
    )

    assert result["status"] == "generated"
    assert result["questionCount"] == 4
    question_path = service.artifact_path(result["questionToken"])
    answer_path = service.artifact_path(result["answerToken"])
    manifest_path = service.artifact_path(result["manifestToken"])
    question_reader = PdfReader(question_path, strict=False)
    answer_reader = PdfReader(answer_path, strict=False)
    question_text = "\n".join(page.extract_text() or "" for page in question_reader.pages)
    answer_text = "\n".join(page.extract_text() or "" for page in answer_reader.pages)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert len(question_reader.pages) >= 1
    assert len(answer_reader.pages) >= 1
    assert "Algebra worksheet" in question_text
    assert "Solve x +" in question_text
    assert "Algebra worksheet" in answer_text
    assert "x =" in answer_text
    assert manifest["paper_id"] == result["paperId"]
    assert len(manifest["questions"]) == 4
    assert manifest["outputs"]["question_pdf"]["pages"] == len(question_reader.pages)
    assert str(db_path) not in json.dumps(result)
    assert str(question_path) not in json.dumps(result)


def test_local_pdf_failure_cleans_staging_and_returns_sanitized_dto(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    output_dir = tmp_path / "failed-exports"

    def fail_after_question_pdf(_paper, question_path, _answer_path, **_kwargs):
        question_path.write_bytes(b"%PDF-1.4\npartial\n%%EOF\n")
        raise LocalPracticePDFError(
            "PDF render failed api_key=do-not-expose "
            "C:\\Users\\Private User\\Bank Folder\\bank.sqlite, "
            "drive_forward=C:/Users/Private User/Bank Folder/bank.sqlite, "
            "UNC=\\\\server\\share folder\\bank.sqlite, "
            "POSIX=/private/user docs/bank.sqlite, Bearer abcdefghijklmnopqrstuvwxyz"
        )

    service = PracticeService(
        db_path,
        model_path,
        records_path,
        output_dir=output_dir,
        local_renderer=fail_after_question_pdf,
    )

    result = service.generate_local_practice(
        subject="Algebra",
        template_id="QML-TEST",
        target_difficulty=60,
        question_count=3,
        paper_date=AS_OF,
    )

    assert result["status"] == "error"
    assert "PDF render failed" in result["detail"]
    assert "do-not-expose" not in result["detail"]
    assert "api_key" in result["detail"]
    assert "bank.sqlite" not in result["detail"]
    assert "Private User" not in result["detail"]
    assert "server" not in result["detail"]
    assert "private/user" not in result["detail"]
    assert "abcdefghijklmnopqrstuvwxyz" not in result["detail"]
    assert list(output_dir.iterdir()) == []


def _prompt_service(tmp_path: Path, **kwargs) -> PracticeService:
    db_path, model_path, records_path = _service_paths(tmp_path)
    return PracticeService(
        db_path,
        model_path,
        records_path,
        today=AS_OF,
        dashboard_loader=lambda **_kwargs: object(),
        prompt_builder=lambda line, _state, subject: f"PREVIEW[{subject}]: {line}",
        **kwargs,
    )




def test_default_practice_prompt_reads_seeds_from_injected_database(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    summary = SubjectSummary(
        name="Algebra",
        initial_score=0,
        window_score=None,
        has_window_records=False,
        mastery_score=0,
        covered_mastery_score=0,
        covered_topic_count=0,
        total_topic_count=0,
        latest_record=None,
        latest_review=None,
        warnings=(),
    )
    state = DashboardState(
        start=date.fromisoformat(AS_OF),
        today=date.fromisoformat(AS_OF),
        benchmark=55,
        subjects=(summary,),
        todos=(),
        memory_risks=(),
        bkt_alerts=(),
        raw_records=(),
        model_data={"subjects": []},
    )
    service = PracticeService(
        db_path,
        model_path,
        records_path,
        today=AS_OF,
        dashboard_loader=lambda **_kwargs: state,
        subject_revision_capture=lambda *_args: None,
        subject_revision_revalidate=lambda _token: None,
    )

    preview = service.prepare_practice_prompt(
        {
            "subject": "Algebra",
            "template_id": "QML-TEST",
            "topic": "Linear equations",
            "target_difficulty": 55,
            "question_count": 2,
        }
    )

    assert preview["status"] == "prepared"
    assert "模板编号：QML-TEST" in preview["prompt"]
    assert "模板说明：QML worksheet：isolated fixture；适用知识点：Linear equations" in preview["prompt"]
    assert "知识点：Linear equations" in preview["prompt"]
    assert "参考难度：55/100" in preview["prompt"]
    assert "题量：2" in preview["prompt"]
    assert "当前作业规格：Linear equations；参考难度 55/100；题库模板 QML-TEST；题量 2 题。" in preview["prompt"]
    assert "Fixture question" in preview["prompt"]
    assert "历史样题种子" in preview["prompt"]

    invalid = service.prepare_practice_prompt(
        {
            "subject": "Algebra",
            "template_id": "NOT-REGISTERED",
            "target_difficulty": 55,
            "question_count": 2,
        }
    )
    cross_subject = service.prepare_practice_prompt(
        {
            "subject": "Physics",
            "template_id": "QML-TEST",
            "target_difficulty": 55,
            "question_count": 2,
        }
    )
    assert invalid["status"] == "error"
    assert invalid["detail"] == "所选题库模板不存在，请刷新模板列表后重试。"
    assert cross_subject["status"] == "error"
    assert cross_subject["detail"] == "所选题库模板不适用于当前学科。"


def test_default_mock_exam_prompt_uses_injected_profile_paths(tmp_path):
    db_path, model_path, records_path = _service_paths(tmp_path)
    summary = SubjectSummary(
        name="Algebra",
        initial_score=0,
        window_score=None,
        has_window_records=False,
        mastery_score=0,
        covered_mastery_score=0,
        covered_topic_count=0,
        total_topic_count=0,
        latest_record=None,
        latest_review=None,
        warnings=(),
    )
    state = DashboardState(
        start=date.fromisoformat(AS_OF),
        today=date.fromisoformat(AS_OF),
        benchmark=55,
        subjects=(summary,),
        todos=(),
        memory_risks=(),
        bkt_alerts=(),
        raw_records=(),
        model_data={"subjects": []},
    )
    service = PracticeService(
        db_path,
        model_path,
        records_path,
        today=AS_OF,
        dashboard_loader=lambda **_kwargs: state,
        subject_revision_capture=lambda *_args: None,
        subject_revision_revalidate=lambda _token: None,
    )

    preview = service.prepare_practice_prompt(
        {"kind": "mock_exam", "subject": "Algebra", "mode": "diagnostic"}
    )

    assert preview["status"] == "prepared"
    assert preview["kind"] == "mock_exam"
    assert preview["copyable"] is True
    assert "模拟卷准备度" in preview["prompt"]


def test_mock_exam_uses_existing_prompt_builder_for_copy_only_output(tmp_path):
    def mock_builder(state, subject, mode):
        return f"MOCK[{subject}|{mode}|{type(state).__name__}]"

    service = _prompt_service(tmp_path, mock_exam_prompt_builder=mock_builder)
    preview = service.prepare_practice_prompt(
        {"kind": "mock_exam", "subject": "Algebra", "mode": "diagnostic"}
    )

    assert preview["status"] == "prepared"
    assert preview["kind"] == "mock_exam"
    assert preview["copyable"] is True
    assert "MOCK[Algebra|diagnostic|object]" == preview["prompt"]
    assert not hasattr(service, "execute_chatgpt_workflow")


def test_plan_practice_context_reuses_existing_prompt_builders(tmp_path, monkeypatch):
    from study_app.core import practice_prompts
    from study_app.data import plan_repository

    monkeypatch.setattr(
        plan_repository,
        "get_active_study_plan",
        lambda scope, db_path: {
            "items": [
                {"section_key": "short", "item_type": "result", "item_text": "当天作业：级数练习"},
                {"section_key": "short", "item_type": "result", "item_text": "当天作业：函数极限练习"},
                {"section_key": "short", "item_type": "check", "item_text": "复盘错因"},
            ]
        },
    )
    monkeypatch.setattr(
        practice_prompts,
        "build_practice_generation_prompt",
        lambda item, state, scope, **_kwargs: f"PROMPT[{scope}]:{item}",
    )
    service = _prompt_service(tmp_path)
    preview = service.prepare_practice_prompt(
        {"kind": "plan_context", "subject": "Algebra"}
    )

    assert preview["status"] == "prepared"
    assert preview["kind"] == "plan_context"
    assert preview["copyable"] is True
    assert preview["itemCount"] == 2
    assert preview["prompt"].count("PROMPT[Algebra]") == 2










def test_oj_practice_prompt_stays_manual_without_desktop_execution(tmp_path):
    service = _prompt_service(tmp_path)
    preview = service.prepare_practice_prompt(
        {"homework_line": "CS-OJ-PRACTICE：LeetCode 原题训练", "subject": "计算机科学"}
    )

    assert preview["status"] == "manual"
    assert preview["kind"] == "oj_practice_list"
    assert preview["token"] == ""


def test_practice_service_has_no_chatgpt_desktop_generation_surface(tmp_path):
    import importlib.util

    service = _prompt_service(tmp_path)
    assert not hasattr(service, "execute_chatgpt_workflow")
    assert not hasattr(service, "cancel_chatgpt_workflow")
    assert not hasattr(service, "request_cancel")
    assert importlib.util.find_spec("study_app.integrations.chatgpt_desktop_bridge") is None
