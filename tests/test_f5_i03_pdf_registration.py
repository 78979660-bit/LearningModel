from __future__ import annotations

import sqlite3

from pypdf import PdfWriter

from study_app.core.subject_documents import DocumentLimits, register_pdf
from study_app.data.database import initialize_database
from study_app.data.subject_repository import install_subject_lifecycle_schema


def make_pdf(path, *, pages=1, encrypted=False, attachment=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if attachment:
        writer.add_attachment("payload.txt", b"not executed")
    if encrypted:
        writer.encrypt("secret")
    with path.open("wb") as stream:
        writer.write(stream)


def isolated_db(tmp_path):
    db_path = tmp_path / "isolated.sqlite"
    initialize_database(db_path)
    install_subject_lifecycle_schema(db_path)
    return db_path


def test_same_document_and_config_is_idempotent(tmp_path):
    db_path = isolated_db(tmp_path)
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf, pages=2)
    first = register_pdf(
        db_path, pdf, parser_name="pypdf", parser_version="5.0",
        config={"ocr": False, "dpi": 200}, file_version="v1",
    )
    second = register_pdf(
        db_path, pdf, parser_name="pypdf", parser_version="5.0",
        config={"dpi": 200, "ocr": False}, file_version="v1",
    )
    assert second.parse_key == first.parse_key
    assert second.document_hash == first.document_hash
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subject_documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM document_parses").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0] == 2


def test_different_ocr_config_creates_new_parse_version(tmp_path):
    db_path = isolated_db(tmp_path)
    pdf = tmp_path / "scan.pdf"
    make_pdf(pdf)
    first = register_pdf(
        db_path, pdf, parser_name="hybrid", parser_version="1",
        config={"ocr": "tesseract", "dpi": 200}, file_version="v1",
    )
    second = register_pdf(
        db_path, pdf, parser_name="hybrid", parser_version="1",
        config={"ocr": "tesseract", "dpi": 300}, file_version="v1",
    )
    assert first.document_hash == second.document_hash
    assert first.parse_key != second.parse_key


def test_different_file_versions_coexist_by_hash(tmp_path):
    db_path = isolated_db(tmp_path)
    first_path = tmp_path / "book-v1.pdf"
    second_path = tmp_path / "book-v2.pdf"
    make_pdf(first_path, pages=1)
    make_pdf(second_path, pages=2)
    first = register_pdf(db_path, first_path, parser_name="pypdf", parser_version="1", config={}, file_version="v1")
    second = register_pdf(db_path, second_path, parser_name="pypdf", parser_version="1", config={}, file_version="v2")
    assert first.document_hash != second.document_hash
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM subject_documents").fetchone()[0] == 2


def test_corrupt_encrypted_blank_and_over_limit_are_diagnostic(tmp_path):
    db_path = isolated_db(tmp_path)
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.7\nnot-a-real-pdf")
    encrypted = tmp_path / "encrypted.pdf"
    make_pdf(encrypted, encrypted=True)
    blank = tmp_path / "blank.pdf"
    make_pdf(blank)
    large = tmp_path / "large.pdf"
    make_pdf(large, pages=2)
    bad = register_pdf(db_path, corrupt, parser_name="pypdf", parser_version="1", config={}, file_version="bad")
    locked = register_pdf(db_path, encrypted, parser_name="pypdf", parser_version="1", config={}, file_version="locked")
    empty = register_pdf(db_path, blank, parser_name="pypdf", parser_version="1", config={}, file_version="blank")
    limited = register_pdf(
        db_path, large, parser_name="pypdf", parser_version="1", config={}, file_version="large",
        limits=DocumentLimits(
            warning_bytes=1,
            max_bytes=large.stat().st_size - 1,
            max_pages=1,
        ),
    )
    assert bad.preflight_status == "failed"
    assert locked.encrypted is True and locked.preflight_status == "needs_review"
    assert empty.pages[0].blank_suspected is True
    assert limited.preflight_status == "rejected"


def test_non_pdf_and_page_limit_are_rejected(tmp_path):
    db_path = isolated_db(tmp_path)
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"PNG\r\n")
    many = tmp_path / "many.pdf"
    make_pdf(many, pages=3)
    not_pdf = register_pdf(db_path, fake, parser_name="pypdf", parser_version="1", config={}, file_version="fake")
    too_many = register_pdf(
        db_path, many, parser_name="pypdf", parser_version="1", config={}, file_version="many",
        limits=DocumentLimits(warning_bytes=512 * 1024, max_bytes=1024 * 1024, max_pages=2),
    )
    assert not_pdf.mime_type == "application/octet-stream"
    assert not_pdf.preflight_status == "rejected"
    assert too_many.page_count == 3
    assert too_many.preflight_status == "rejected"


def test_embedded_objects_are_only_flagged_never_executed(tmp_path):
    db_path = isolated_db(tmp_path)
    marker = tmp_path / "executed.txt"
    pdf = tmp_path / "embedded.pdf"
    make_pdf(pdf, attachment=True)
    result = register_pdf(
        db_path, pdf, parser_name="pypdf", parser_version="1",
        config={"attachment_command": str(marker)}, file_version="v1",
    )
    assert "embedded-file" in result.security_flags
    assert not marker.exists()


def test_warning_threshold_allows_local_registration(tmp_path):
    db_path = isolated_db(tmp_path)
    pdf = tmp_path / "warning.pdf"
    make_pdf(pdf, pages=1)
    result = register_pdf(
        db_path,
        pdf,
        parser_name="pypdf",
        parser_version="1.2",
        config={},
        file_version="v1",
        limits=DocumentLimits(
            warning_bytes=pdf.stat().st_size - 1,
            max_bytes=pdf.stat().st_size + 1,
        ),
    )
    assert result.preflight_status == "needs_review"  # blank page, not file size
    assert any("告警阈值" in message for message in result.diagnostics)
    assert not any("硬上限" in message for message in result.diagnostics)


def test_security_scan_detects_token_across_chunk_boundary(tmp_path):
    db_path = isolated_db(tmp_path)
    pdf = tmp_path / "split-token.pdf"
    make_pdf(pdf, pages=1)
    with pdf.open("ab") as stream:
        stream.write(b"x" * 7 + b"/JavaScript")
    result = register_pdf(
        db_path,
        pdf,
        parser_name="pypdf",
        parser_version="1.2",
        config={},
        file_version="v1",
        limits=DocumentLimits(scan_chunk_bytes=8),
    )
    assert "javascript" in result.security_flags


def test_reinspection_preserves_old_result_and_updates_current_summary(tmp_path):
    db_path = isolated_db(tmp_path)
    pdf = tmp_path / "reinspect.pdf"
    make_pdf(pdf, pages=1)
    size = pdf.stat().st_size
    rejected = register_pdf(
        db_path,
        pdf,
        parser_name="pypdf",
        parser_version="1.2",
        config={},
        file_version="v1",
        limits=DocumentLimits(warning_bytes=1, max_bytes=size - 1),
    )
    accepted = register_pdf(
        db_path,
        pdf,
        parser_name="pypdf",
        parser_version="1.2",
        config={},
        file_version="v1",
        limits=DocumentLimits(warning_bytes=1, max_bytes=size + 1),
    )
    assert rejected.parse_key != accepted.parse_key
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_preflight_inspections"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM document_parses WHERE status='rejected'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT preflight_status FROM subject_documents"
        ).fetchone()[0] == accepted.preflight_status


def test_registration_never_calls_path_read_bytes(tmp_path, monkeypatch):
    db_path = isolated_db(tmp_path)
    pdf = tmp_path / "streamed.pdf"
    make_pdf(pdf, pages=1)

    def forbidden_read_bytes(self):
        raise AssertionError("whole-file read is forbidden")

    monkeypatch.setattr(type(pdf), "read_bytes", forbidden_read_bytes)
    result = register_pdf(
        db_path,
        pdf,
        parser_name="pypdf",
        parser_version="1.2",
        config={},
        file_version="v1",
    )
    assert result.document_hash


def test_legacy_unversioned_preflight_is_backfilled_before_reinspection(tmp_path):
    db_path = isolated_db(tmp_path)
    pdf = tmp_path / "legacy.pdf"
    make_pdf(pdf, pages=1)
    first = register_pdf(
        db_path,
        pdf,
        parser_name="pypdf",
        parser_version="1.1",
        config={},
        file_version="v1",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM document_preflight_inspections WHERE document_hash=?",
            (first.document_hash,),
        )
        connection.execute(
            "UPDATE subject_documents SET preflight_status='rejected', diagnostics_json='[\"legacy limit\"]' WHERE document_hash=?",
            (first.document_hash,),
        )
    register_pdf(
        db_path,
        pdf,
        parser_name="pypdf",
        parser_version="1.2",
        config={},
        file_version="v1",
    )
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT policy_version, status, diagnostics_json
            FROM document_preflight_inspections
            WHERE document_hash=? ORDER BY policy_version
            """,
            (first.document_hash,),
        ).fetchall()
    assert len(rows) == 2
    assert any(row[0] == "legacy-unversioned-v1.1" and row[1] == "rejected" for row in rows)
    assert any("legacy limit" in row[2] for row in rows)
