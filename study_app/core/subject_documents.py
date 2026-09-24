from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from study_app.data.subject_repository import SubjectCatalogRepository


PDF_MIME = "application/pdf"
_PARSER_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_SECURITY_TOKENS = (
    (b"/JavaScript", "javascript"),
    (b"/JS", "javascript-action"),
    (b"/EmbeddedFile", "embedded-file"),
    (b"/OpenAction", "open-action"),
    (b"/AA", "additional-action"),
)
PREFLIGHT_POLICY_VERSION = "pdf-preflight-v1.2"


@dataclass(frozen=True)
class DocumentLimits:
    warning_bytes: int = 256 * 1024 * 1024
    max_bytes: int = 1024 * 1024 * 1024
    max_pages: int = 5000
    scan_chunk_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if (
            self.warning_bytes <= 0
            or self.max_bytes <= 0
            or self.max_pages <= 0
            or self.scan_chunk_bytes <= 0
        ):
            raise ValueError("PDF 资源上限必须为正整数")
        if self.warning_bytes > self.max_bytes:
            raise ValueError("PDF 告警阈值不得超过硬上限")


@dataclass(frozen=True)
class PagePreflight:
    physical_page: int
    status: str
    blank_suspected: bool
    error_message: str | None = None


@dataclass(frozen=True)
class DocumentRegistration:
    document_hash: str
    parse_key: str
    config_hash: str
    mime_type: str
    byte_size: int
    page_count: int | None
    encrypted: bool
    preflight_status: str
    diagnostics: tuple[str, ...]
    security_flags: tuple[str, ...]
    pages: tuple[PagePreflight, ...]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_json(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("解析配置不得包含 NaN 或 Infinity")
        return value
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("解析配置对象键必须是字符串")
            result[key] = _normalize_json(item)
        return result
    raise ValueError(f"解析配置包含非 JSON 类型：{type(value).__name__}")


def canonical_config(config: Mapping[str, object]) -> tuple[str, str]:
    normalized = _normalize_json(config)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_key_for(
    document_hash: str,
    parser_name: str,
    parser_version: str,
    config_hash: str,
) -> str:
    if not _PARSER_TOKEN.fullmatch(parser_name):
        raise ValueError("parser_name 格式无效")
    if not _PARSER_TOKEN.fullmatch(parser_version):
        raise ValueError("parser_version 格式无效")
    payload = "\0".join((document_hash, parser_name, parser_version, config_hash))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _security_flags(path: Path, chunk_size: int) -> tuple[str, ...]:
    """Scan PDF activity tokens without loading the complete file into memory."""
    overlap = max(len(token) for token, _label in _SECURITY_TOKENS) - 1
    found: set[str] = set()
    tail = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk_size), b""):
            window = tail + block
            for token, label in _SECURITY_TOKENS:
                if token in window:
                    found.add(label)
            tail = window[-overlap:]
    return tuple(label for _token, label in _SECURITY_TOKENS if label in found)


def _inspection_config(limits: DocumentLimits, policy_version: str) -> dict[str, object]:
    return {
        "policy_version": policy_version,
        "warning_bytes": limits.warning_bytes,
        "max_bytes": limits.max_bytes,
        "max_pages": limits.max_pages,
        "scan_chunk_bytes": limits.scan_chunk_bytes,
    }


def _inspection_key(document_hash: str, config_hash: str) -> str:
    return hashlib.sha256(
        f"{document_hash}\0{config_hash}".encode("utf-8")
    ).hexdigest()


def inspect_pdf(path: Path | str, limits: DocumentLimits = DocumentLimits()) -> tuple:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(file_path)
    size = file_path.stat().st_size
    with file_path.open("rb") as stream:
        header = stream.read(8)
    mime = PDF_MIME if header.startswith(b"%PDF-") else "application/octet-stream"
    diagnostics: list[str] = []
    if size > limits.max_bytes:
        diagnostics.append(f"文件超过 {limits.max_bytes} 字节硬上限")
    elif size > limits.warning_bytes:
        diagnostics.append(
            f"文件超过 {limits.warning_bytes} 字节告警阈值；允许继续本地流式处理"
        )
    if mime != PDF_MIME:
        diagnostics.append("文件签名不是 PDF")
    security = (
        _security_flags(file_path, limits.scan_chunk_bytes)
        if mime == PDF_MIME and size <= limits.max_bytes
        else ()
    )
    if security:
        diagnostics.append("检测到不执行的 PDF 活动/嵌入对象：" + ", ".join(security))
    if diagnostics and (size > limits.max_bytes or mime != PDF_MIME):
        return mime, size, None, False, "rejected", tuple(diagnostics), security, ()

    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path, strict=False)
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            diagnostics.append("PDF 已加密，未提供解密授权")
            try:
                page_count = len(reader.pages)
            except Exception:
                page_count = None
            return (
                mime,
                size,
                page_count,
                True,
                "needs_review",
                tuple(diagnostics),
                security,
                tuple(
                    PagePreflight(index, "needs_review", False, "encrypted")
                    for index in range(1, (page_count or 0) + 1)
                ),
            )
        page_count = len(reader.pages)
        if page_count > limits.max_pages:
            diagnostics.append(f"页数超过 {limits.max_pages} 页上限")
            return mime, size, page_count, False, "rejected", tuple(diagnostics), security, ()
        pages: list[PagePreflight] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                blank = not str(page.extract_text() or "").strip()
            except Exception as error:
                pages.append(PagePreflight(index, "needs_review", False, type(error).__name__))
                continue
            pages.append(
                PagePreflight(
                    index,
                    "needs_review" if blank else "pending",
                    blank,
                    "blank-page" if blank else None,
                )
            )
        if not pages:
            diagnostics.append("PDF 不包含页面")
        if any(page.blank_suspected for page in pages):
            diagnostics.append("存在空白或扫描页，需后续 OCR/人工复核")
        review_needed = bool(security) or not pages or any(
            page.blank_suspected or page.status == "needs_review" for page in pages
        )
        status = "needs_review" if review_needed else "ready"
        return mime, size, page_count, False, status, tuple(diagnostics), security, tuple(pages)
    except ImportError as error:
        raise RuntimeError("缺少 pypdf，无法执行 PDF 安全预检") from error
    except Exception as error:
        diagnostics.append(f"PDF 损坏或无法解析：{type(error).__name__}")
        return mime, size, None, False, "failed", tuple(diagnostics), security, ()


def register_pdf(
    db_path: Path | str,
    file_path: Path | str,
    *,
    parser_name: str,
    parser_version: str,
    config: Mapping[str, object],
    file_version: str,
    source_note: str = "",
    external_allowed: bool = False,
    limits: DocumentLimits = DocumentLimits(),
    preflight_policy_version: str = PREFLIGHT_POLICY_VERSION,
) -> DocumentRegistration:
    path = Path(file_path)
    if not isinstance(file_version, str) or not file_version.strip():
        raise ValueError("file_version 必须是非空字符串")
    if type(external_allowed) is not bool:
        raise ValueError("external_allowed 必须是布尔值")
    if not _PARSER_TOKEN.fullmatch(preflight_policy_version):
        raise ValueError("preflight_policy_version 格式无效")
    document_hash = _file_hash(path)
    inspection_config_json, inspection_config_hash = canonical_config(
        _inspection_config(limits, preflight_policy_version)
    )
    inspection_key = _inspection_key(document_hash, inspection_config_hash)
    effective_config = dict(config)
    effective_config["_preflight"] = {
        "inspection_key": inspection_key,
        **_inspection_config(limits, preflight_policy_version),
    }
    config_json, config_hash = canonical_config(effective_config)
    parse_key = parse_key_for(document_hash, parser_name, parser_version, config_hash)
    (
        mime,
        size,
        page_count,
        encrypted,
        preflight_status,
        diagnostics,
        security,
        pages,
    ) = inspect_pdf(path, limits)
    parse_status = {
        "ready": "registered",
        "needs_review": "needs_review",
        "rejected": "rejected",
        "failed": "failed",
    }[preflight_status]
    repository = SubjectCatalogRepository(db_path)
    with repository.transaction(readonly=False) as connection:
        existing_document = connection.execute(
            """
            SELECT page_count, encrypted, preflight_status,
                   diagnostics_json, security_flags_json
            FROM subject_documents WHERE document_hash = ?
            """,
            (document_hash,),
        ).fetchone()
        if existing_document is not None:
            inspection_count = connection.execute(
                """
                SELECT COUNT(*) FROM document_preflight_inspections
                WHERE document_hash = ?
                """,
                (document_hash,),
            ).fetchone()[0]
            if inspection_count == 0:
                legacy_json, legacy_hash = canonical_config(
                    {
                        "policy_version": "legacy-unversioned-v1.1",
                        "source": "subject_documents-current-summary",
                    }
                )
                connection.execute(
                    """
                    INSERT INTO document_preflight_inspections(
                        inspection_key, document_hash, policy_version,
                        config_hash, config_json, status, page_count,
                        encrypted, diagnostics_json, security_flags_json
                    ) VALUES (?, ?, 'legacy-unversioned-v1.1', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _inspection_key(document_hash, legacy_hash),
                        document_hash,
                        legacy_hash,
                        legacy_json,
                        existing_document["preflight_status"],
                        existing_document["page_count"],
                        existing_document["encrypted"],
                        existing_document["diagnostics_json"],
                        existing_document["security_flags_json"],
                    ),
                )
        connection.execute(
            """
            INSERT INTO subject_documents(
                document_hash, original_name, file_path, mime_type, byte_size,
                page_count, encrypted, file_version, source_note,
                external_allowed, preflight_status, diagnostics_json,
                security_flags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_hash) DO UPDATE SET
                original_name=excluded.original_name,
                file_path=excluded.file_path,
                mime_type=excluded.mime_type,
                byte_size=excluded.byte_size,
                page_count=excluded.page_count,
                encrypted=excluded.encrypted,
                file_version=excluded.file_version,
                source_note=excluded.source_note,
                external_allowed=excluded.external_allowed,
                preflight_status=excluded.preflight_status,
                diagnostics_json=excluded.diagnostics_json,
                security_flags_json=excluded.security_flags_json
            """,
            (
                document_hash,
                path.name,
                str(path.resolve()),
                mime,
                size,
                page_count,
                int(encrypted),
                file_version.strip(),
                str(source_note),
                int(external_allowed),
                preflight_status,
                json.dumps(diagnostics, ensure_ascii=False),
                json.dumps(security, ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO document_preflight_inspections(
                inspection_key, document_hash, policy_version, config_hash,
                config_json, status, page_count, encrypted,
                diagnostics_json, security_flags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inspection_key,
                document_hash,
                preflight_policy_version,
                inspection_config_hash,
                inspection_config_json,
                preflight_status,
                page_count,
                int(encrypted),
                json.dumps(diagnostics, ensure_ascii=False),
                json.dumps(security, ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO document_parses(
                parse_key, document_hash, parser_name, parser_version,
                config_hash, config_json, status, page_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parse_key,
                document_hash,
                parser_name,
                parser_version,
                config_hash,
                config_json,
                parse_status,
                page_count,
            ),
        )
        existing = connection.execute(
            "SELECT COUNT(*) FROM document_pages WHERE parse_key = ?", (parse_key,)
        ).fetchone()[0]
        if existing == 0:
            connection.executemany(
                """
                INSERT INTO document_pages(
                    parse_key, physical_page, status, blank_suspected, error_message
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        parse_key,
                        page.physical_page,
                        page.status,
                        int(page.blank_suspected),
                        page.error_message,
                    )
                    for page in pages
                ],
            )
    return DocumentRegistration(
        document_hash=document_hash,
        parse_key=parse_key,
        config_hash=config_hash,
        mime_type=mime,
        byte_size=size,
        page_count=page_count,
        encrypted=encrypted,
        preflight_status=preflight_status,
        diagnostics=diagnostics,
        security_flags=security,
        pages=pages,
    )
