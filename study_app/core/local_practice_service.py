from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from study_app.core.local_practice_candidates import build_candidate_pool
from study_app.core.local_practice_composer import ComposedPracticePaper, compose_local_practice_paper
from study_app.core.local_practice_pdf import render_practice_pdfs
from study_app.core.local_practice_spec import LocalPracticePaperSpec
from study_app.core.practice_spec import (
    extract_homework_exercise_count,
    infer_practice_template,
    planned_homework_difficulty,
)
from study_app.data.database import DEFAULT_DB_PATH
from study_app.data.practice_repository import list_local_practice_candidates
from study_app.paths import EXPORTS_DIR


class LocalPracticeGenerationError(RuntimeError):
    """Raised when a complete local practice-paper bundle cannot be committed."""


@dataclass(frozen=True)
class LocalPracticeGenerationResult:
    paper_id: str
    bundle_directory: Path
    question_pdf: Path
    answer_pdf: Path
    manifest_json: Path
    manifest: dict[str, Any]


def default_local_practice_output_directory() -> Path:
    return EXPORTS_DIR / "local_practice_papers"


def spec_from_homework(
    homework: str,
    *,
    subject: str,
    paper_date: date | str | None = None,
) -> LocalPracticePaperSpec:
    text = str(homework or "").strip()
    if not text:
        raise LocalPracticeGenerationError("作业内容为空，无法生成本地练习卷。")
    template_id, _brief = infer_practice_template(text)
    return LocalPracticePaperSpec.create(
        subject=subject,
        template_id=template_id,
        topic="",
        target_difficulty=planned_homework_difficulty(text),
        question_count=extract_homework_exercise_count(text),
        paper_date=paper_date or date.today(),
        title=f"{str(subject or '').strip()} 本地练习卷",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_existing_result(bundle_directory: Path, paper: ComposedPracticePaper) -> LocalPracticeGenerationResult | None:
    manifest_paths = list(bundle_directory.glob("*_manifest.json")) if bundle_directory.is_dir() else []
    if len(manifest_paths) != 1:
        return None
    try:
        manifest = json.loads(manifest_paths[0].read_text(encoding="utf-8"))
        if manifest.get("paper_id") != paper.paper_id:
            return None
        outputs = manifest["outputs"]
        question_pdf = bundle_directory / outputs["question_pdf"]["filename"]
        answer_pdf = bundle_directory / outputs["answer_pdf"]["filename"]
        if not question_pdf.is_file() or not answer_pdf.is_file():
            return None
        if _sha256(question_pdf) != outputs["question_pdf"]["sha256"]:
            return None
        if _sha256(answer_pdf) != outputs["answer_pdf"]["sha256"]:
            return None
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return LocalPracticeGenerationResult(
        paper_id=paper.paper_id,
        bundle_directory=bundle_directory,
        question_pdf=question_pdf,
        answer_pdf=answer_pdf,
        manifest_json=manifest_paths[0],
        manifest=manifest,
    )


def generate_local_practice_paper(
    spec: LocalPracticePaperSpec,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    output_directory: Path | str | None = None,
    font_path: Path | str | None = None,
    renderer: Callable[..., dict[str, Any]] = render_practice_pdfs,
) -> LocalPracticeGenerationResult:
    from study_app.core.async_tasks import (
        capture_subject_revision,
        revalidate_subject_revision,
    )

    revision_token = capture_subject_revision(db_path, spec.subject)
    rows = list_local_practice_candidates(
        subject=spec.subject,
        template_id=spec.template_id,
        topic=spec.topic,
        db_path=db_path,
    )
    paper = compose_local_practice_paper(spec, build_candidate_pool(rows))
    output_root = Path(output_directory) if output_directory is not None else default_local_practice_output_directory()
    output_root.mkdir(parents=True, exist_ok=True)
    stem = spec.file_stem(paper.paper_id)
    bundle_directory = output_root / stem
    existing = _load_existing_result(bundle_directory, paper)
    if existing is not None:
        return existing
    if bundle_directory.exists():
        raise LocalPracticeGenerationError(
            f"同名输出目录已存在但校验失败，请人工检查后重试：{bundle_directory}"
        )

    staging = Path(tempfile.mkdtemp(prefix=f".{stem}.", suffix=".tmp", dir=output_root))
    question_name = f"{stem}_questions.pdf"
    answer_name = f"{stem}_answers.pdf"
    manifest_name = f"{stem}_manifest.json"
    question_path = staging / question_name
    answer_path = staging / answer_name
    manifest_path = staging / manifest_name
    try:
        render_result = renderer(
            paper,
            question_path,
            answer_path,
            font_path=font_path,
        )
        question_sha = _sha256(question_path)
        answer_sha = _sha256(answer_path)
        manifest = paper.manifest_core()
        manifest["recorded_date"] = spec.paper_date
        manifest["outputs"] = {
            "answer_pdf": {
                "filename": answer_name,
                "pages": int(render_result["answer_pages"]),
                "sha256": answer_sha,
            },
            "question_pdf": {
                "filename": question_name,
                "pages": int(render_result["question_pages"]),
                "sha256": question_sha,
            },
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        revalidate_subject_revision(revision_token)
        os.replace(staging, bundle_directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return LocalPracticeGenerationResult(
        paper_id=paper.paper_id,
        bundle_directory=bundle_directory,
        question_pdf=bundle_directory / question_name,
        answer_pdf=bundle_directory / answer_name,
        manifest_json=bundle_directory / manifest_name,
        manifest=manifest,
    )
