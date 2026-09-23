from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


QUESTION_MARK_RUN = re.compile(r"\?{3,}")
PRIVATE_USE_CHARACTER = re.compile(r"[\ue000-\uf8ff]")
CJK_CHARACTER = re.compile(r"[\u4e00-\u9fff]")
MOJIBAKE_MARKERS = (
    "锛",
    "銆",
    "绋",
    "閿",
    "鐢",
    "妫",
    "杩",
    "浣",
    "瀛",
    "璁",
    "棰",
    "瑙",
    "搴",
)
MOJIBAKE_CHARACTER = re.compile("[" + "".join(MOJIBAKE_MARKERS) + "]")


@dataclass(frozen=True)
class TextIntegrityIssue:
    path: str
    reason: str
    preview: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason, "preview": self.preview}


class TextIntegrityError(ValueError):
    def __init__(self, issues: list[TextIntegrityIssue], context: str = "data"):
        self.issues = issues
        details = "; ".join(
            f"{issue.path}: {issue.reason} ({issue.preview})" for issue in issues[:5]
        )
        suffix = f"; 另有 {len(issues) - 5} 处" if len(issues) > 5 else ""
        super().__init__(f"{context} 文本完整性检查失败：{details}{suffix}")


def find_text_integrity_issues(value: Any, path: str = "$") -> list[TextIntegrityIssue]:
    issues: list[TextIntegrityIssue] = []
    for item_path, text in iter_text_values(value, path):
        reason = corruption_reason(text)
        if reason:
            issues.append(TextIntegrityIssue(item_path, reason, compact_preview(text)))
    return issues


def validate_text_integrity(value: Any, context: str = "data") -> None:
    issues = find_text_integrity_issues(value)
    if issues:
        raise TextIntegrityError(issues, context=context)


def corruption_reason(text: str) -> str | None:
    if text.isascii():
        return "包含连续三个以上问号，疑似字符已被替换" if QUESTION_MARK_RUN.search(text) else None
    if "\ufffd" in text:
        return "包含 Unicode 替换字符 U+FFFD"
    if PRIVATE_USE_CHARACTER.search(text):
        return "包含 Unicode 私用区字符，疑似错误解码乱码"
    if QUESTION_MARK_RUN.search(text):
        return "包含连续三个以上问号，疑似字符已被替换"
    if looks_like_mojibake(text):
        return "疑似 UTF-8/GBK 错误解码乱码"
    return None


def looks_like_mojibake(text: str) -> bool:
    if len(text) < 6:
        return False
    marker_count = sum(1 for _ in MOJIBAKE_CHARACTER.finditer(text))
    if marker_count < 3:
        return False
    chinese_count = sum(1 for _ in CJK_CHARACTER.finditer(text))
    return marker_count >= 3 and marker_count / max(chinese_count, 1) >= 0.18


def iter_text_values(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from iter_text_values(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from iter_text_values(child, f"{path}[{index}]")


def compact_preview(text: str, limit: int = 48) -> str:
    preview = " ".join(text.split())
    if len(preview) > limit:
        preview = preview[: limit - 1] + "…"
    return repr(preview)


def scan_sqlite_text_integrity(db_path: Path | str, max_issues: int = 500) -> list[TextIntegrityIssue]:
    issues: list[TextIntegrityIssue] = []
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        tables = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        for table_row in tables:
            table = table_row["name"]
            columns = connection.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
            text_columns = [
                column["name"]
                for column in columns
                if "TEXT" in str(column["type"] or "").upper()
            ]
            if not text_columns:
                continue
            select_columns = ", ".join(
                ["rowid AS __rowid", *[quote_identifier(column) for column in text_columns]]
            )
            for row in connection.execute(f"SELECT {select_columns} FROM {quote_identifier(table)}"):
                rowid = row["__rowid"]
                for column in text_columns:
                    value = row[column]
                    if not isinstance(value, str):
                        continue
                    reason = corruption_reason(value)
                    if reason:
                        issues.append(
                            TextIntegrityIssue(
                                path=f"{table}[rowid={rowid}].{column}",
                                reason=reason,
                                preview=compact_preview(value),
                            )
                        )
                        if len(issues) >= max_issues:
                            return issues
    finally:
        connection.close()
    return issues


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
