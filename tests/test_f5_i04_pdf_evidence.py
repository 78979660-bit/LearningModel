from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from study_app.core.subject_documents import register_pdf
from study_app.core import subject_pdf_pipeline
from study_app.core.subject_pdf_pipeline import OCRBlock, OCRPage, process_registered_pdf
from study_app.data.database import initialize_database
from study_app.data.subject_repository import install_subject_lifecycle_schema


def setup_db(tmp_path):
    db = tmp_path / "isolated.sqlite"
    initialize_database(db)
    install_subject_lifecycle_schema(db)
    return db


def make_layout_pdf(path, *, pages=1, rotate=False, with_image=False):
    pdf = canvas.Canvas(str(path), pagesize=(612, 792), invariant=True)
    for page in range(1, pages + 1):
        if rotate:
            pdf.setPageRotation(90)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(50, 740, f"Chapter {page} Mechanics")
        pdf.drawString(50, 700, "Left column momentum p = m v")
        pdf.drawString(330, 700, "Right column integral = sum")
        pdf.line(50, 650, 500, 650)
        pdf.line(50, 620, 500, 620)
        if with_image:
            image = Image.new("RGB", (80, 30), "white")
            ImageDraw.Draw(image).text((2, 2), "scan", fill="black")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            from reportlab.lib.utils import ImageReader

            pdf.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 50, 500, 80, 30)
        pdf.drawString(300, 20, str(page))
        pdf.showPage()
    pdf.save()


def fixed_ocr(_png):
    return OCRPage(
        text="OCR chapter scanned formula = x",
        confidence=0.93,
        blocks=(OCRBlock("OCR chapter scanned formula = x", (0.1, 0.1, 0.8, 0.2)),),
    )


def test_text_layout_evidence_has_page_coordinates_and_flags(tmp_path):
    db = setup_db(tmp_path)
    pdf = tmp_path / "layout.pdf"
    make_layout_pdf(pdf)
    registration = register_pdf(db, pdf, parser_name="hybrid", parser_version="1", config={}, file_version="v1")
    result = process_registered_pdf(db, registration.parse_key, ocr_provider=fixed_ocr)
    assert result.status == "completed"
    assert result.processed_pages[0].status == "success"
    assert {"two_column", "formula", "table"}.issubset(result.processed_pages[0].quality["layout_flags"])
    with sqlite3.connect(db) as connection:
        content = connection.execute("SELECT page_label, printed_page, rotation, coordinate_version FROM document_page_contents").fetchone()
        evidence = connection.execute("SELECT bbox_json, evidence_type, quality_status FROM subject_evidence").fetchall()
    assert content[0] == "1"
    assert content[1] == "1"
    assert content[2] == 0
    assert content[3] == "rotated-top-left-normalized-v1"
    assert evidence and all(row[1:] == ("explicit_source", "success") for row in evidence)
    assert all(all(0 <= value <= 1 for value in json.loads(row[0])) for row in evidence)

    rotated = tmp_path / "rotated.pdf"
    make_layout_pdf(rotated, rotate=True)
    rotated_registration = register_pdf(
        db, rotated, parser_name="hybrid", parser_version="1",
        config={"rotation_fixture": True}, file_version="v1",
    )
    process_registered_pdf(db, rotated_registration.parse_key, ocr_provider=fixed_ocr)
    with sqlite3.connect(db) as connection:
        rotated_row = connection.execute(
            "SELECT rotation, coordinate_version FROM document_page_contents WHERE parse_key=?",
            (rotated_registration.parse_key,),
        ).fetchone()
    assert rotated_row == (90, "rotated-top-left-normalized-v1")


def test_scanned_and_mixed_pages_use_controlled_local_ocr(tmp_path):
    db = setup_db(tmp_path)
    scanned = tmp_path / "scanned.pdf"
    image = Image.new("RGB", (300, 200), "white")
    ImageDraw.Draw(image).text((20, 20), "scanned", fill="black")
    image.save(tmp_path / "scan.png")
    pdf = canvas.Canvas(str(scanned), pagesize=(612, 792), invariant=True)
    pdf.drawImage(str(tmp_path / "scan.png"), 50, 500, 300, 200)
    pdf.showPage()
    pdf.save()
    registration = register_pdf(db, scanned, parser_name="hybrid", parser_version="1", config={"ocr": True}, file_version="v1")
    result = process_registered_pdf(db, registration.parse_key, ocr_provider=fixed_ocr)
    assert result.processed_pages[0].parse_route == "ocr"
    assert result.processed_pages[0].quality["ocr_confidence"] == 0.93

    mixed = tmp_path / "mixed.pdf"
    make_layout_pdf(mixed, with_image=True)
    mixed_registration = register_pdf(db, mixed, parser_name="hybrid", parser_version="1", config={"ocr": True}, file_version="v1")
    mixed_result = process_registered_pdf(db, mixed_registration.parse_key, ocr_provider=fixed_ocr)
    assert mixed_result.processed_pages[0].parse_route == "mixed"


def test_mixed_page_keeps_local_text_when_optional_ocr_is_unavailable(tmp_path):
    db = setup_db(tmp_path)
    mixed = tmp_path / "mixed-without-ocr.pdf"
    make_layout_pdf(mixed, with_image=True)
    registration = register_pdf(
        db,
        mixed,
        parser_name="hybrid",
        parser_version="1",
        config={"ocr": True},
        file_version="v1",
    )

    def unavailable(_png):
        raise RuntimeError("tesseract unavailable")

    result = process_registered_pdf(
        db,
        registration.parse_key,
        ocr_provider=unavailable,
    )

    assert result.status == "needs_review"
    assert result.processed_pages[0].status == "needs_review"
    assert result.processed_pages[0].parse_route == "mixed"
    assert result.processed_pages[0].quality["ocr_confidence"] == 0.0
    assert result.processed_pages[0].quality["ocr_error"] == "RuntimeError"
    with sqlite3.connect(db) as connection:
        text, status = connection.execute(
            """
            SELECT contents.text_content,pages.status
            FROM document_page_contents contents
            JOIN document_pages pages USING(parse_key,physical_page)
            WHERE contents.parse_key=?
            """,
            (registration.parse_key,),
        ).fetchone()
    assert "Chapter 1 Mechanics" in text
    assert status == "needs_review"


def test_scanned_page_without_optional_ocr_is_reviewable_not_failed(tmp_path):
    db = setup_db(tmp_path)
    scanned = tmp_path / "scan-without-ocr.pdf"
    image = Image.new("RGB", (300, 200), "white")
    ImageDraw.Draw(image).text((20, 20), "scanned", fill="black")
    image.save(tmp_path / "scan-without-ocr.png")
    pdf = canvas.Canvas(str(scanned), pagesize=(612, 792), invariant=True)
    pdf.drawImage(str(tmp_path / "scan-without-ocr.png"), 50, 500, 300, 200)
    pdf.showPage()
    pdf.save()
    registration = register_pdf(
        db,
        scanned,
        parser_name="hybrid",
        parser_version="1.2",
        config={"ocr": "optional-local"},
        file_version="v1",
    )

    def unavailable(_png):
        raise RuntimeError("chinese ocr unavailable")

    result = process_registered_pdf(
        db,
        registration.parse_key,
        ocr_provider=unavailable,
    )

    assert result.status == "needs_review"
    assert result.processed_pages[0].status == "needs_review"
    assert result.processed_pages[0].parse_route == "none"
    assert result.processed_pages[0].quality["ocr_confidence"] == 0.0
    assert result.processed_pages[0].quality["ocr_error"] == "RuntimeError"
    with sqlite3.connect(db) as connection:
        content = connection.execute(
            """
            SELECT text_content,parse_route FROM document_page_contents
            WHERE parse_key=?
            """,
            (registration.parse_key,),
        ).fetchone()
        evidence_count = connection.execute(
            "SELECT COUNT(*) FROM subject_evidence WHERE parse_key=?",
            (registration.parse_key,),
        ).fetchone()[0]
    assert content == ("", "none")
    assert evidence_count == 0


def test_local_tesseract_runtime_prefers_managed_chinese_data(tmp_path, monkeypatch):
    from study_app import capabilities

    executable = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    tessdata = tmp_path / "user-data" / "tesseract" / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "chi_sim.traineddata").write_bytes(b"fixture")
    (tessdata / "eng.traineddata").write_bytes(b"fixture")

    monkeypatch.setattr(capabilities, "DATA_DIR", tmp_path / "unused-data")
    monkeypatch.setattr(capabilities, "TESSDATA_DIR", tessdata)
    monkeypatch.setattr(capabilities.shutil, "which", lambda _name: str(executable))

    resolved_executable, resolved_tessdata, language = (
        subject_pdf_pipeline._local_tesseract_runtime()
    )
    assert resolved_executable == executable
    assert resolved_tessdata == tessdata
    assert language == "chi_sim+eng"


def test_low_quality_ocr_never_becomes_success(tmp_path):
    db = setup_db(tmp_path)
    pdf = tmp_path / "blank.pdf"
    make_layout_pdf(pdf)
    registration = register_pdf(db, pdf, parser_name="hybrid", parser_version="1", config={}, file_version="v1")

    def weak(_png):
        return OCRPage("bad", 0.4, (OCRBlock("bad", (0.1, 0.1, 0.2, 0.2)),))

    # Force OCR by clearing text through a genuinely blank page.
    blank = tmp_path / "really-blank.pdf"
    pdf_writer = canvas.Canvas(str(blank), pagesize=(612, 792), invariant=True)
    pdf_writer.showPage()
    pdf_writer.save()
    registration = register_pdf(db, blank, parser_name="hybrid", parser_version="1", config={}, file_version="v1")
    result = process_registered_pdf(db, registration.parse_key, ocr_provider=weak)
    assert result.status == "needs_review"
    assert result.processed_pages[0].status == "needs_review"
    with sqlite3.connect(db) as connection:
        statuses = {row[0] for row in connection.execute("SELECT quality_status FROM subject_evidence WHERE parse_key=?", (registration.parse_key,))}
    assert statuses == {"needs_review"}


def test_cancel_and_resume_do_not_duplicate_or_skip_pages(tmp_path):
    db = setup_db(tmp_path)
    pdf = tmp_path / "three-pages.pdf"
    make_layout_pdf(pdf, pages=3)
    registration = register_pdf(db, pdf, parser_name="hybrid", parser_version="1", config={}, file_version="v1")
    calls = 0

    def cancel_after_first():
        nonlocal calls
        calls += 1
        return calls > 1

    first = process_registered_pdf(db, registration.parse_key, ocr_provider=fixed_ocr, cancel_check=cancel_after_first)
    assert first.status == "paused"
    assert [item.physical_page for item in first.processed_pages] == [1]
    second = process_registered_pdf(db, registration.parse_key, ocr_provider=fixed_ocr)
    assert [item.physical_page for item in second.processed_pages] == [2, 3]
    with sqlite3.connect(db) as connection:
        pages = connection.execute("SELECT physical_page, status FROM document_pages WHERE parse_key=? ORDER BY physical_page", (registration.parse_key,)).fetchall()
        evidence_count = connection.execute("SELECT COUNT(*) FROM subject_evidence WHERE parse_key=?", (registration.parse_key,)).fetchone()[0]
        distinct_count = connection.execute("SELECT COUNT(DISTINCT evidence_id) FROM subject_evidence WHERE parse_key=?", (registration.parse_key,)).fetchone()[0]
    assert [row[0] for row in pages] == [1, 2, 3]
    assert all(row[1] == "success" for row in pages)
    assert evidence_count == distinct_count


def test_explicit_page_batches_preserve_reviewed_progress(tmp_path):
    db = setup_db(tmp_path)
    pdf = tmp_path / "three-scans.pdf"
    writer = canvas.Canvas(str(pdf), pagesize=(612, 792), invariant=True)
    for _ in range(3):
        writer.showPage()
    writer.save()
    registration = register_pdf(
        db,
        pdf,
        parser_name="hybrid",
        parser_version="1.3",
        config={"ocr": "batched"},
        file_version="v1",
    )
    calls = 0

    def weak_ocr(_png):
        nonlocal calls
        calls += 1
        return OCRPage("低置信文本", 0.4, (OCRBlock("低置信文本", (0.1, 0.1, 0.8, 0.2)),))

    first = process_registered_pdf(
        db,
        registration.parse_key,
        ocr_provider=weak_ocr,
        page_numbers=(1,),
    )
    assert first.status == "paused"
    assert first.remaining_pages == (2, 3)
    assert calls == 1

    second = process_registered_pdf(
        db,
        registration.parse_key,
        ocr_provider=weak_ocr,
        page_numbers=(2, 3),
    )
    assert second.status == "needs_review"
    assert second.remaining_pages == ()
    assert calls == 3
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_page_contents WHERE parse_key=?",
            (registration.parse_key,),
        ).fetchone()[0] == 3


def test_all_registered_pages_end_in_an_explicit_state(tmp_path):
    db = setup_db(tmp_path)
    pdf = tmp_path / "coverage.pdf"
    make_layout_pdf(pdf, pages=2)
    registration = register_pdf(db, pdf, parser_name="hybrid", parser_version="1", config={}, file_version="v1")
    process_registered_pdf(db, registration.parse_key, ocr_provider=fixed_ocr)
    with sqlite3.connect(db) as connection:
        rows = connection.execute("SELECT physical_page, status FROM document_pages WHERE parse_key=? ORDER BY physical_page", (registration.parse_key,)).fetchall()
    assert rows == [(1, "success"), (2, "success")]
