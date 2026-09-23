from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from study_app.data.subject_repository import SubjectCatalogRepository
from study_app.paths import TESSDATA_DIR


COORDINATE_VERSION = "rotated-top-left-normalized-v1"
HEADING_PATTERN = re.compile(r"^(第?[一二三四五六七八九十0-9]+[章节篇部]|chapter\s+\d+)", re.I)
PRINTED_PAGE_PATTERN = re.compile(r"^\s*(?:第\s*)?([0-9]{1,5})(?:\s*页)?\s*$")
FORMULA_PATTERN = re.compile(r"[=∑∫√±×÷∞≤≥]|\b(?:sin|cos|lim|log)\b", re.I)


@dataclass(frozen=True)
class OCRBlock:
    text: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class OCRPage:
    text: str
    confidence: float
    blocks: tuple[OCRBlock, ...]


@dataclass(frozen=True)
class PageProcessResult:
    physical_page: int
    status: str
    parse_route: str
    evidence_count: int
    quality: dict[str, object]


@dataclass(frozen=True)
class PipelineResult:
    parse_key: str
    status: str
    processed_pages: tuple[PageProcessResult, ...]
    remaining_pages: tuple[int, ...]


def _ratio_metrics(text: str) -> tuple[float, float]:
    characters = [character for character in text if not character.isspace()]
    if not characters:
        return 0.0, 1.0
    bad = sum(
        character == "\ufffd" or ord(character) < 32 or ord(character) == 127
        for character in characters
    )
    garble = bad / len(characters)
    return 1.0 - garble, garble


def _normalize_bbox(
    bbox: Iterable[float], width: float, height: float
) -> tuple[float, float, float, float]:
    values = tuple(float(value) for value in bbox)
    if len(values) != 4 or width <= 0 or height <= 0:
        raise ValueError("无效页面 bbox")
    x0, y0, x1, y1 = values
    normalized = (
        max(0.0, min(1.0, x0 / width)),
        max(0.0, min(1.0, y0 / height)),
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
    )
    if normalized[2] < normalized[0] or normalized[3] < normalized[1]:
        raise ValueError("bbox 坐标顺序无效")
    return normalized


def _page_labels(path: Path, count: int) -> tuple[str, ...]:
    try:
        from pypdf import PdfReader

        labels = tuple(str(label) for label in PdfReader(path, strict=False).page_labels)
    except Exception:
        labels = ()
    if len(labels) != count:
        return tuple(str(index) for index in range(1, count + 1))
    return labels


def _local_tesseract_runtime() -> tuple[Path, Path, str]:
    executable_candidates = (
        Path(value) if (value := shutil.which("tesseract")) else None,
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    )
    executable = next(
        (candidate for candidate in executable_candidates if candidate and candidate.is_file()),
        None,
    )
    if executable is None:
        raise RuntimeError("未找到本地 Tesseract 可执行文件")

    managed_tessdata = TESSDATA_DIR
    system_tessdata = executable.parent / "tessdata"
    tessdata = (
        managed_tessdata
        if (managed_tessdata / "chi_sim.traineddata").is_file()
        else system_tessdata
    )
    if not (tessdata / "chi_sim.traineddata").is_file():
        raise RuntimeError("缺少简体中文 OCR 语言包 chi_sim")
    language = "chi_sim+eng" if (tessdata / "eng.traineddata").is_file() else "chi_sim"
    return executable, tessdata, language


def _default_ocr(png_bytes: bytes) -> OCRPage:
    try:
        import pytesseract
        from PIL import Image

        executable, tessdata, language = _local_tesseract_runtime()
        pytesseract.pytesseract.tesseract_cmd = str(executable)
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
        image = Image.open(io.BytesIO(png_bytes))
        data = pytesseract.image_to_data(
            image,
            lang=language,
            output_type=pytesseract.Output.DICT,
        )
    except Exception as error:
        raise RuntimeError(f"本地 OCR 不可用：{type(error).__name__}") from error
    blocks: list[OCRBlock] = []
    confidences: list[float] = []
    width, height = image.size
    for index, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text or "").strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index]) / 100
        except (ValueError, TypeError, KeyError, IndexError):
            confidence = 0.0
        confidences.append(max(0.0, min(1.0, confidence)))
        left = float(data["left"][index])
        top = float(data["top"][index])
        right = left + float(data["width"][index])
        bottom = top + float(data["height"][index])
        blocks.append(OCRBlock(text, (left / width, top / height, right / width, bottom / height)))
    return OCRPage(
        text="\n".join(block.text for block in blocks),
        confidence=(sum(confidences) / len(confidences) if confidences else 0.0),
        blocks=tuple(blocks),
    )


def _status_for(quality: dict[str, object], has_text: bool) -> str:
    if not has_text:
        return "needs_review"
    usable = float(quality["usable_character_ratio"])
    garble = float(quality["garble_ratio"])
    coverage = float(quality["block_coverage"])
    ordering = float(quality["reading_order_score"])
    ocr_confidence = quality.get("ocr_confidence")
    ocr_success = ocr_confidence is None or float(ocr_confidence) >= 0.85
    if usable >= 0.92 and garble <= 0.01 and coverage >= 0.85 and ordering >= 0.90 and ocr_success:
        return "success"
    ocr_partial = ocr_confidence is None or float(ocr_confidence) >= 0.65
    if usable >= 0.70 and garble <= 0.05 and coverage >= 0.60 and ordering >= 0.70 and ocr_partial:
        return "partial_success"
    return "needs_review"


def _printed_page(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in (*lines[:2], *lines[-2:]):
        match = PRINTED_PAGE_PATTERN.fullmatch(line)
        if match:
            return match.group(1)
    return None


def _object_type(text: str, layout_flags: set[str], fallback: bool) -> str:
    if fallback:
        return "controlled_fallback"
    if HEADING_PATTERN.match(text.strip()):
        return "heading"
    if FORMULA_PATTERN.search(text):
        return "formula"
    if "table" in layout_flags:
        return "table"
    return "paragraph"


def process_registered_pdf(
    db_path: Path | str,
    parse_key: str,
    *,
    ocr_provider: Callable[[bytes], OCRPage] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_page_pixels: int = 100_000_000,
    page_numbers: Iterable[int] | None = None,
) -> PipelineResult:
    if max_page_pixels <= 0:
        raise ValueError("max_page_pixels 必须为正整数")
    requested_pages: frozenset[int] | None = None
    if page_numbers is not None:
        normalized_pages = tuple(page_numbers)
        if not normalized_pages or any(
            type(page) is not int or page < 1 for page in normalized_pages
        ):
            raise ValueError("page_numbers 必须包含正整数页码")
        if len(set(normalized_pages)) != len(normalized_pages):
            raise ValueError("page_numbers 不得包含重复页码")
        requested_pages = frozenset(normalized_pages)
    repository = SubjectCatalogRepository(db_path)
    with repository._open() as connection:
        parse = connection.execute(
            """
            SELECT parses.*, documents.file_path, documents.encrypted
            FROM document_parses parses
            JOIN subject_documents documents USING(document_hash)
            WHERE parses.parse_key = ?
            """,
            (parse_key,),
        ).fetchone()
        if parse is None:
            raise LookupError(f"未知 parse_key：{parse_key}")
        if requested_pages is not None and parse["page_count"] is not None:
            if max(requested_pages) > int(parse["page_count"]):
                raise ValueError("page_numbers 超出文档页数")
        pending = tuple(
            int(row[0])
            for row in connection.execute(
                """
                SELECT physical_page FROM document_pages
                WHERE parse_key = ? AND status IN ('pending', 'needs_review', 'failed')
                ORDER BY physical_page
                """,
                (parse_key,),
            ).fetchall()
        )
        if requested_pages is not None:
            pending = tuple(page for page in pending if page in requested_pages)
    if parse["encrypted"]:
        raise ValueError("加密 PDF 未获解密授权，不能进入解析")
    path = Path(parse["file_path"])
    try:
        import fitz
    except ImportError as error:
        raise RuntimeError("缺少 PyMuPDF，无法提取 PDF") from error
    document = fitz.open(path)
    labels = _page_labels(path, document.page_count)
    provider = ocr_provider or _default_ocr
    processed: list[PageProcessResult] = []
    try:
        for physical_page in pending:
            if cancel_check is not None and cancel_check():
                with repository._open(readonly=False) as connection:
                    connection.execute(
                        "UPDATE document_parses SET status = 'paused' WHERE parse_key = ?",
                        (parse_key,),
                    )
                remaining = tuple(page for page in pending if page >= physical_page)
                return PipelineResult(parse_key, "paused", tuple(processed), remaining)
            page = document.load_page(physical_page - 1)
            try:
                raw_blocks = [
                    block for block in page.get_text("blocks", sort=True)
                    if len(block) >= 5 and str(block[4] or "").strip()
                ]
                width, height = float(page.rect.width), float(page.rect.height)
                text_blocks = [
                    (str(block[4]).strip(), _normalize_bbox(block[:4], width, height))
                    for block in raw_blocks
                ]
                has_images = bool(page.get_images(full=True))
                route = "mixed" if text_blocks and has_images else "text"
                ocr_confidence: float | None = None
                ocr_error: str | None = None
                if not text_blocks or (has_images and sum(len(text) for text, _ in text_blocks) < 80):
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    if pix.width * pix.height > max_page_pixels:
                        raise ValueError("页面像素超过资源上限")
                    try:
                        ocr = provider(pix.tobytes("png"))
                    except Exception as error:
                        ocr_confidence = 0.0
                        ocr_error = type(error).__name__
                        route = "mixed" if text_blocks else "none"
                    else:
                        ocr_confidence = ocr.confidence
                        ocr_blocks = [
                            (block.text.strip(), tuple(block.bbox))
                            for block in ocr.blocks if block.text.strip()
                        ]
                        if text_blocks:
                            text_blocks.extend(ocr_blocks)
                            route = "mixed"
                        else:
                            text_blocks = ocr_blocks
                            route = "ocr"
                text = "\n".join(block_text for block_text, _ in text_blocks)
                usable, garble = _ratio_metrics(text)
                xs = [bbox[0] for _text, bbox in text_blocks]
                two_column = bool(xs and any(x < 0.35 for x in xs) and any(x > 0.45 for x in xs))
                drawings = page.get_drawings()
                layout_flags: set[str] = set()
                if two_column:
                    layout_flags.add("two_column")
                if drawings:
                    layout_flags.add("table")
                if FORMULA_PATTERN.search(text):
                    layout_flags.add("formula")
                quality: dict[str, object] = {
                    "usable_character_ratio": round(usable, 6),
                    "garble_ratio": round(garble, 6),
                    "block_coverage": 1.0 if text_blocks else 0.0,
                    "reading_order_score": 1.0 if text_blocks else 0.0,
                    "ocr_confidence": None if ocr_confidence is None else round(ocr_confidence, 6),
                    "ocr_error": ocr_error,
                    "layout_flags": sorted(layout_flags),
                }
                status = _status_for(quality, bool(text.strip()))
                heading_found = any(HEADING_PATTERN.match(value.strip()) for value, _ in text_blocks)
                controlled_fallback = bool(text_blocks) and not heading_found
                chunks: list[tuple[str, tuple[float, float, float, float], bool]] = []
                if controlled_fallback:
                    for block_text, bbox in text_blocks:
                        for offset in range(0, len(block_text), 8000):
                            chunks.append((block_text[offset : offset + 8000], bbox, True))
                else:
                    chunks = [(value, bbox, False) for value, bbox in text_blocks]
                page_label = labels[physical_page - 1]
                printed = _printed_page(text)
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                with repository._open(readonly=False) as connection:
                    connection.execute(
                        "DELETE FROM subject_evidence WHERE parse_key = ? AND physical_page = ?",
                        (parse_key, physical_page),
                    )
                    connection.execute(
                        """
                        INSERT INTO document_page_contents(
                            parse_key, physical_page, page_label, printed_page,
                            parse_route, rotation, coordinate_version, text_hash,
                            text_content, quality_json, controlled_fallback
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(parse_key, physical_page) DO UPDATE SET
                            page_label=excluded.page_label,
                            printed_page=excluded.printed_page,
                            parse_route=excluded.parse_route,
                            rotation=excluded.rotation,
                            coordinate_version=excluded.coordinate_version,
                            text_hash=excluded.text_hash,
                            text_content=excluded.text_content,
                            quality_json=excluded.quality_json,
                            controlled_fallback=excluded.controlled_fallback
                        """,
                        (
                            parse_key,
                            physical_page,
                            page_label,
                            printed,
                            route if text_blocks else "none",
                            int(page.rotation),
                            COORDINATE_VERSION,
                            text_hash,
                            text,
                            json.dumps(quality, ensure_ascii=False, sort_keys=True),
                            int(controlled_fallback),
                        ),
                    )
                    for excerpt, bbox, fallback in chunks:
                        excerpt_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                        bbox_json = json.dumps([round(value, 8) for value in bbox], separators=(",", ":"))
                        evidence_id = hashlib.sha256(
                            f"{parse_key}\0{physical_page}\0{bbox_json}\0{excerpt_hash}".encode("utf-8")
                        ).hexdigest()
                        connection.execute(
                            """
                            INSERT INTO subject_evidence(
                                evidence_id, parse_key, physical_page, page_label,
                                printed_page, bbox_json, rotation, coordinate_version,
                                object_type, evidence_type, excerpt, excerpt_hash,
                                quality_status, controlled_fallback
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'explicit_source', ?, ?, ?, ?)
                            """,
                            (
                                evidence_id,
                                parse_key,
                                physical_page,
                                page_label,
                                printed,
                                bbox_json,
                                int(page.rotation),
                                COORDINATE_VERSION,
                                _object_type(excerpt, layout_flags, fallback),
                                excerpt,
                                excerpt_hash,
                                status,
                                int(fallback),
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE document_pages
                        SET status = ?, blank_suspected = ?, quality_json = ?,
                            error_message = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE parse_key = ? AND physical_page = ?
                        """,
                        (
                            status,
                            int(not bool(text.strip())),
                            json.dumps(quality, ensure_ascii=False, sort_keys=True),
                            parse_key,
                            physical_page,
                        ),
                    )
                processed.append(PageProcessResult(physical_page, status, route, len(chunks), quality))
            except Exception as error:
                with repository._open(readonly=False) as connection:
                    connection.execute(
                        """
                        UPDATE document_pages SET status='failed', error_message=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE parse_key=? AND physical_page=?
                        """,
                        (f"{type(error).__name__}: {error}", parse_key, physical_page),
                    )
                processed.append(
                    PageProcessResult(physical_page, "failed", "none", 0, {"error": type(error).__name__})
                )
        with repository._open(readonly=False) as connection:
            counts = dict(
                connection.execute(
                    """
                    SELECT status, COUNT(*) FROM document_pages
                    WHERE parse_key=? GROUP BY status
                    """,
                    (parse_key,),
                ).fetchall()
            )
            remaining = tuple(
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT pages.physical_page
                    FROM document_pages pages
                    LEFT JOIN document_page_contents contents
                      ON contents.parse_key=pages.parse_key
                     AND contents.physical_page=pages.physical_page
                    WHERE pages.parse_key=? AND contents.physical_page IS NULL
                    ORDER BY pages.physical_page
                    """,
                    (parse_key,),
                ).fetchall()
            )
            final_status = (
                "failed"
                if counts.get("failed")
                else "paused"
                if remaining
                else "needs_review"
                if counts.get("needs_review") or counts.get("partial_success")
                else "completed"
            )
            connection.execute(
                "UPDATE document_parses SET status=? WHERE parse_key=?",
                (final_status, parse_key),
            )
        return PipelineResult(parse_key, final_status, tuple(processed), remaining)
    finally:
        document.close()
