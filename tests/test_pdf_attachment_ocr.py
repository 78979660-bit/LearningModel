from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest
from PIL import Image

from study_app.data import text_extractor as extractor


DIGITAL = "Digital page with sufficient readable text to preserve without OCR."


def pdf(tmp_path: Path, pages: list[str]) -> Path:
    path = tmp_path / "中文资料 带空格.pdf"
    image = io.BytesIO()
    Image.new("RGB", (240, 100), "white").save(image, format="PNG")
    with fitz.open() as document:
        for kind in pages:
            page = document.new_page(width=300, height=200)
            if kind == "scan":
                page.insert_image(page.rect, stream=image.getvalue())
            elif kind == "text":
                page.insert_text((10, 30), DIGITAL, fontsize=6)
        document.save(path)
    return path


def runtime(monkeypatch, operation):
    monkeypatch.setattr(extractor, "resolve_tesseract_runtime", lambda: object())
    monkeypatch.setattr(extractor, "_extract_image_text_with_cli", operation)


def test_scanned_pdf_reaches_ocr_and_cleans_rendered_files(tmp_path, monkeypatch):
    images = []
    def recognize(path, _runtime, max_chars):
        assert path.is_file()
        with Image.open(path) as image:
            assert max(image.size) <= 3000
        images.append(path)
        return "数学 2+3=5", ""
    runtime(monkeypatch, recognize)
    result = extractor.extract_text(pdf(tmp_path, ["scan"]))
    assert result.text == "数学 2+3=5"
    assert not result.warning
    assert all(not path.exists() for path in images)


def test_mixed_pdf_preserves_page_order_and_does_not_ocr_digital_pages(tmp_path, monkeypatch):
    calls = []
    def recognize(path, _runtime, _max_chars):
        calls.append(path)
        return "Recognized scanned page content.", ""
    runtime(monkeypatch, recognize)
    result = extractor.extract_text(pdf(tmp_path, ["text", "scan", "text"]))
    assert result.text == DIGITAL + "\nRecognized scanned page content.\n" + DIGITAL
    assert not result.warning
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["missing", "timeout", "empty"])
def test_scan_failure_keeps_digital_text_and_warns_about_partial_extraction(tmp_path, monkeypatch, failure):
    def recognize(*_args):
        if failure == "timeout":
            raise TimeoutError("private path must not appear in warning")
        return "", "" if failure == "empty" else "OCR unavailable"
    runtime(monkeypatch, recognize)
    if failure == "missing":
        monkeypatch.setattr(extractor, "resolve_tesseract_runtime", lambda: (_ for _ in ()).throw(RuntimeError("private runtime path")))
    result = extractor.extract_text(pdf(tmp_path, ["text", "scan"]))
    assert result.text == DIGITAL
    assert "第 2 页" in result.warning
    assert "private" not in result.warning


def test_character_budget_and_page_limit_bound_ocr_work(tmp_path, monkeypatch):
    calls = []
    def recognize(_path, _runtime, remaining):
        calls.append(remaining)
        return "abcdef"[:remaining], ""
    runtime(monkeypatch, recognize)
    monkeypatch.setattr(extractor, "MAX_PDF_PAGES", 2)
    path = pdf(tmp_path, ["scan"] * 4)
    assert extractor.extract_text(path, max_chars=4).text == "abcd"
    assert calls == [4]
    calls.clear()
    assert extractor.extract_text(path).text == "abcdef\nabcdef"
    assert len(calls) == 2


def test_blank_pdf_does_not_fabricate_ocr_content(tmp_path, monkeypatch):
    runtime(monkeypatch, lambda *_args: pytest.fail("Blank page should not invoke OCR"))
    result = extractor.extract_text(pdf(tmp_path, ["blank"]))
    assert result.text == ""
    assert result.warning
