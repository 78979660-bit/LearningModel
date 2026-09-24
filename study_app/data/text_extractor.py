from __future__ import annotations

import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

from study_app.capabilities import (
    TesseractRuntime,
    find_tesseract_executable,
    resolve_tesseract_runtime,
)


MAX_FILE_CHARS = 20000
MAX_PDF_PAGES = 24
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class ExtractedText:
    path: Path
    text: str
    source: str
    warning: str = ""


def extract_text_from_files(paths: list[str | Path], max_chars: int = MAX_FILE_CHARS) -> list[ExtractedText]:
    results: list[ExtractedText] = []
    for raw_path in paths:
        path = Path(raw_path)
        results.append(extract_text(path, max_chars=max_chars))
    return results


def extract_text(path: Path, max_chars: int = MAX_FILE_CHARS) -> ExtractedText:
    suffix = path.suffix.lower()
    if not path.exists():
        return ExtractedText(path, "", "missing", "文件不存在，无法解析。")
    try:
        if suffix == ".pdf":
            text = _extract_pdf_text(path, max_chars)
            warning = "" if is_usable_text(text) else "PDF 文本层不可用或质量较低；可能需要 OCR。"
            return ExtractedText(path, text, "pdf", warning)
        if suffix == ".docx":
            return ExtractedText(path, _extract_docx_text(path, max_chars), "docx")
        if suffix in {".pptx", ".xlsx"}:
            return ExtractedText(path, _extract_office_zip_text(path, max_chars), suffix[1:])
        if suffix in IMAGE_EXTENSIONS:
            text, warning = _extract_image_text(path, max_chars)
            return ExtractedText(path, text, "ocr", warning)
        if suffix in {".txt", ".md", ".csv"}:
            return ExtractedText(path, _read_text_strict(path)[:max_chars], "text")
    except Exception as error:
        return ExtractedText(path, "", suffix.lstrip(".") or "unknown", f"解析失败：{error}")
    return ExtractedText(path, "", "unsupported", "暂不支持该文件类型的文本解析。")


def combined_extracted_text(items: list[ExtractedText], max_chars: int = MAX_FILE_CHARS) -> str:
    chunks = []
    for item in items:
        if item.text and (not item.warning or is_usable_text(item.text)):
            chunks.append(f"[{item.path.name}]\n{item.text}")
        elif item.warning:
            chunks.append(f"[{item.path.name}]\n{item.warning}")
    return "\n\n".join(chunks)[:max_chars]


def _extract_pdf_text(path: Path, max_chars: int) -> str:
    fitz_text = _extract_pdf_text_with_pymupdf(path, max_chars)
    if is_usable_text(fitz_text):
        return fitz_text

    pypdf_text = _extract_pdf_text_with_pypdf(path, max_chars)
    if is_usable_text(pypdf_text):
        return pypdf_text
    return pypdf_text or fitz_text


def _extract_pdf_text_with_pymupdf(path: Path, max_chars: int) -> str:
    try:
        import fitz
    except ImportError:
        return ""

    pieces = []
    with fitz.open(path) as document:
        for page in document[:MAX_PDF_PAGES]:
            text = page.get_text("text") or ""
            if text.strip():
                pieces.append(text)
            if sum(len(piece) for piece in pieces) >= max_chars:
                break
    return normalize_whitespace("\n".join(pieces))[:max_chars]


def _extract_pdf_text_with_pypdf(path: Path, max_chars: int) -> str:
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
    except ImportError:
        return ""

    reader = PdfReader(str(path))
    pieces = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        text = page.extract_text() or ""
        if text.strip():
            pieces.append(text)
        if sum(len(piece) for piece in pieces) >= max_chars:
            break
    return normalize_whitespace("\n".join(pieces))[:max_chars]


def _extract_docx_text(path: Path, max_chars: int) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    return normalize_whitespace("\n".join(texts))[:max_chars]


def _extract_office_zip_text(path: Path, max_chars: int) -> str:
    pieces = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml"):
                continue
            if not any(part in name for part in ["/slides/", "sharedStrings.xml", "docProps"]):
                continue
            data = archive.read(name).decode("utf-8")
            text = re.sub(r"<[^>]+>", " ", data)
            text = unescape(text)
            if text.strip():
                pieces.append(text)
            if sum(len(piece) for piece in pieces) >= max_chars:
                break
    return normalize_whitespace("\n".join(pieces))[:max_chars]


def _extract_image_text(path: Path, max_chars: int) -> tuple[str, str]:
    try:
        runtime = resolve_tesseract_runtime()
    except RuntimeError as error:
        return "", f"{error}；图片 OCR 不可用，已保留文件路径。"
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return _extract_image_text_with_cli(path, runtime, max_chars)

    try:
        pytesseract.pytesseract.tesseract_cmd = str(runtime.executable)
        image = preprocess_image_for_ocr(Image.open(path))
        text = pytesseract.image_to_string(
            image,
            lang=runtime.language,
            config=f'--psm 6 --tessdata-dir "{runtime.tessdata}"',
        )
    except Exception:
        return _extract_image_text_with_cli(path, runtime, max_chars)
    return normalize_whitespace(text)[:max_chars], ""


def _extract_image_text_with_cli(path: Path, runtime: TesseractRuntime, max_chars: int) -> tuple[str, str]:
    ocr_path = path
    temp_path = None
    try:
        temp_path = preprocess_image_file_for_ocr(path)
        ocr_path = temp_path or path
        completed = subprocess.run(
            [
                str(runtime.executable), str(ocr_path), "stdout",
                "--tessdata-dir", str(runtime.tessdata),
                "-l", runtime.language, "--psm", "6",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
        )
    except Exception as error:
        return "", f"图片 OCR 失败：{error}"
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
    text = normalize_whitespace(completed.stdout)
    if completed.returncode != 0 and not text:
        return "", completed.stderr.strip() or "图片 OCR 失败。"
    return text[:max_chars], ""


def _read_text_strict(path: Path) -> str:
    data = path.read_bytes()
    errors = []
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            from study_app.data.text_integrity import validate_text_integrity

            validate_text_integrity(text, context=f"文件 {path.name}")
            return text
        except (UnicodeDecodeError, ValueError) as error:
            errors.append(f"{encoding}: {error}")
    raise UnicodeError("无法可靠解码文本；" + "；".join(errors))


def preprocess_image_file_for_ocr(path: Path) -> Path | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    image = preprocess_image_for_ocr(Image.open(path))
    temp = Path(tempfile.NamedTemporaryFile(suffix=".png", delete=False).name)
    image.save(temp)
    return temp


def preprocess_image_for_ocr(image):
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    image = ImageOps.exif_transpose(image)
    image = image.convert("L")
    width, height = image.size
    scale = max(1, min(3, int(1800 / max(width, height)) + 1))
    if scale > 1:
        image = image.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(1.8)
    image = ImageEnhance.Sharpness(image).enhance(1.5)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    threshold = otsu_threshold(image)
    return image.point(lambda value: 255 if value > threshold else 0, mode="1").convert("L")


def otsu_threshold(image) -> int:
    histogram = image.histogram()
    total = sum(histogram)
    if total == 0:
        return 160
    sum_total = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0.0
    weight_background = 0
    best_threshold = 160
    best_variance = -1.0
    for threshold, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += threshold * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def is_usable_text(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return False
    suspicious = sum(1 for char in text if ord(char) < 32 and char not in "\n\t")
    suspicious += sum(1 for char in text if 0x7F <= ord(char) <= 0x9F)
    if suspicious / max(len(text), 1) > 0.02:
        return False
    mojibake_marks = sum(1 for char in text if char in "¹ÙÄÆÝ²£½¶")
    if mojibake_marks / max(len(text), 1) > 0.05:
        return False
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    latin1 = sum(1 for char in text if 0xA0 <= ord(char) <= 0xFF)
    if cjk == 0 and latin1 / max(len(text), 1) > 0.03:
        return False
    visible = sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return visible / max(len(text), 1) > 0.25
