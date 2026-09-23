from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class AttachmentProblemBlock:
    title: str
    statement: str
    source: str = "attachment_excerpt"


def normalize_title(kind: str, number: str) -> str:
    number = number.strip().rstrip(".")
    if kind.lower() in {"problem", "question", "exercise"}:
        return f"{kind.title()} {number}"
    if kind == "例" or "." in number:
        return f"例{number}"
    return f"第{number}题"


def parse_problem_blocks_from_text(text: str, limit: int = 80) -> list[AttachmentProblemBlock]:
    """Split OCR/PDF text into rough problem blocks.

    The parser is intentionally conservative. It looks for line-level problem
    markers first, then falls back to short inline markers for OCR output.
    """
    if not text:
        return []

    matches = list(problem_marker_matches(text))
    blocks: list[AttachmentProblemBlock] = []
    seen: set[str] = set()
    for index, match in enumerate(matches[:limit]):
        kind, number = marker_kind_and_number(match)
        title = normalize_title(kind, number)
        if title in seen:
            continue
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        statement = clean_statement(text[start:end])
        if len(statement) < 8:
            continue
        blocks.append(AttachmentProblemBlock(title=title, statement=statement[:1800]))
        seen.add(title)
    return blocks


def problem_marker_matches(text: str):
    patterns = [
        r"(?im)^\s*(problem|question|exercise)\s+(\d{1,3}(?:[.\-]\d{1,3})?)\s*[:.)\-]?\s+",
        r"(?m)^\s*(第)\s*(\d{1,3}(?:\.\d{1,3})?)\s*题?[\.、：:）)]?\s+",
        r"(?m)^\s*(例)\s*(\d{1,3}(?:\.\d{1,3})?)\s*[\.、：:）)]?\s+",
    ]
    matches = []
    for pattern in patterns:
        matches.extend(re.finditer(pattern, text))
    matches.sort(key=lambda item: item.start())
    return dedupe_close_matches(matches)


def dedupe_close_matches(matches):
    deduped = []
    for match in matches:
        if deduped and match.start() - deduped[-1].start() < 3:
            continue
        deduped.append(match)
    return deduped


def marker_kind_and_number(match) -> tuple[str, str]:
    kind = match.group(1) or ""
    number = match.group(2)
    return kind, number


def clean_statement(value: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)
