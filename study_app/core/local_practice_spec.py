from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any


class LocalPracticeSpecError(ValueError):
    """Raised when a local practice-paper request is not explicit and valid."""


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _required_text(value: object, field_name: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        raise LocalPracticeSpecError(f"{field_name} 不能为空。")
    return re.sub(r"\s+", " ", text)


def _optional_text(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).strip())


def _finite_difficulty(value: object) -> float:
    if isinstance(value, bool) or isinstance(value, str) or not isinstance(value, (int, float)):
        raise LocalPracticeSpecError("target_difficulty 必须是 0 到 100 的数值，不能是布尔值或字符串。")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 100:
        raise LocalPracticeSpecError("target_difficulty 必须是 0 到 100 的有限数。")
    return number


def _question_count(value: object) -> int:
    if isinstance(value, bool) or isinstance(value, str) or not isinstance(value, int):
        raise LocalPracticeSpecError("question_count 必须是 1 到 30 的整数，不能是布尔值或字符串。")
    if value < 1 or value > 30:
        raise LocalPracticeSpecError("question_count 必须位于 1 到 30。")
    return value


def _paper_date(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise LocalPracticeSpecError("paper_date 必须是 YYYY-MM-DD 真实日期。")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise LocalPracticeSpecError("paper_date 必须是 YYYY-MM-DD 真实日期。") from error
    if value != parsed.isoformat():
        raise LocalPracticeSpecError("paper_date 必须使用规范 YYYY-MM-DD 格式。")
    return value


def safe_filename_component(value: object, *, maximum_length: int = 48) -> str:
    text = _required_text(value, "文件名组成部分")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text).strip(" ._")
    text = re.sub(r"_+", "_", text)
    if not text:
        text = "practice"
    stem = text.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        text = f"_{text}"
    return text[:maximum_length].rstrip(" ._") or "practice"


@dataclass(frozen=True)
class LocalPracticePaperSpec:
    subject: str
    target_difficulty: float
    question_count: int
    paper_date: str
    template_id: str = ""
    topic: str = ""
    title: str = ""

    @classmethod
    def create(
        cls,
        *,
        subject: object,
        target_difficulty: object,
        question_count: object,
        paper_date: object,
        template_id: object = "",
        topic: object = "",
        title: object = "",
    ) -> "LocalPracticePaperSpec":
        normalized_subject = _required_text(subject, "subject")
        normalized_title = _optional_text(title) or f"{normalized_subject} 本地练习卷"
        return cls(
            subject=normalized_subject,
            target_difficulty=_finite_difficulty(target_difficulty),
            question_count=_question_count(question_count),
            paper_date=_paper_date(paper_date),
            template_id=_optional_text(template_id),
            topic=_optional_text(topic),
            title=normalized_title,
        )

    def canonical_dict(self) -> dict[str, Any]:
        difficulty: int | float = self.target_difficulty
        if self.target_difficulty.is_integer():
            difficulty = int(self.target_difficulty)
        return {
            "paper_date": self.paper_date,
            "question_count": self.question_count,
            "subject": self.subject,
            "target_difficulty": difficulty,
            "template_id": self.template_id,
            "title": self.title,
            "topic": self.topic,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def file_stem(self, paper_id: str) -> str:
        normalized_id = str(paper_id or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_id):
            raise LocalPracticeSpecError("paper_id 必须是 64 位 SHA256 十六进制字符串。")
        return f"{safe_filename_component(self.subject)}_{self.paper_date}_{normalized_id[:12]}"
