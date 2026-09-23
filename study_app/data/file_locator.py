from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SEARCH_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Documents",
]


def configured_search_roots() -> list[Path]:
    try:
        from study_app.data.database import get_setting

        configured = get_setting("file_search_roots", []) or []
    except Exception:
        configured = []
    roots = [Path(str(item)).expanduser() for item in configured if str(item).strip()]
    return roots or list(DEFAULT_SEARCH_ROOTS)

MAX_RESULTS = 80
MAX_DEPTH = 5
ALLOWED_SUFFIXES = {
    ".pdf",
    ".pptx",
    ".ppt",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}


@dataclass(frozen=True)
class FileMatch:
    path: Path
    score: int
    reason: str


def locate_files(query: str, roots: list[Path] | None = None, limit: int = MAX_RESULTS) -> list[FileMatch]:
    terms, hinted_roots = parse_query(query)
    search_roots = hinted_roots or roots or configured_search_roots()
    matches: list[FileMatch] = []

    for root in search_roots:
        if not root.exists():
            continue
        for path in iter_files(root):
            if hinted_roots and not terms:
                score, reason = 1, "位置匹配"
            else:
                score, reason = score_path(path, terms, query)
            if score > 0:
                matches.append(FileMatch(path=path, score=score, reason=reason))
                if len(matches) >= limit * 4:
                    break

    if hinted_roots and not terms:
        matches.sort(key=lambda item: (-safe_mtime(item.path), len(str(item.path)), str(item.path).lower()))
    else:
        matches.sort(key=lambda item: (-item.score, len(str(item.path)), str(item.path).lower()))
    deduped = []
    seen = set()
    for item in matches:
        normalized = str(item.path).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def parse_query(query: str) -> tuple[list[str], list[Path]]:
    cleaned = query.strip()
    lowered = cleaned.lower()
    roots = []

    root_hints = {
        "桌面": Path.home() / "Desktop",
        "desktop": Path.home() / "Desktop",
        "下载": Path.home() / "Downloads",
        "downloads": Path.home() / "Downloads",
        "文档": Path.home() / "Documents",
        "documents": Path.home() / "Documents",
        "坚果云": Path("G:/我的坚果云"),
        "参考书": Path("H:/参考书"),
        "课件": Path("H:/课件及被引用文献"),
        "微信": Path("E:/xwechat_files"),
    }

    for hint, root in root_hints.items():
        if hint.lower() in lowered:
            roots.append(root)

    separators = ["，", ",", "。", "、", "/", "\\", "中", "里", "的", "在", "文件", "路径"]
    text = cleaned
    for sep in separators:
        text = text.replace(sep, " ")

    terms: list[str] = []
    for rough_term in text.split():
        terms.extend(split_mixed_term(rough_term))

    ignored = {
        "桌面",
        "下载",
        "文档",
        "坚果云",
        "参考书",
        "课件",
        "微信",
        "desktop",
        "downloads",
        "documents",
    }
    terms = expand_terms(terms, root_hints)
    if not terms and cleaned:
        terms = split_mixed_term(cleaned)
    return terms, roots


def expand_terms(terms: list[str], root_hints: dict[str, Path]) -> list[str]:
    expanded = []
    seen = set()
    hint_values = list(root_hints.items())
    for term in terms:
        candidates = [term]
        for hint, root in hint_values:
            if term == hint.lower():
                candidates.extend(split_mixed_term(root.name))
                candidates.append(str(root).lower())
            elif hint.lower() in term:
                remainder = term.replace(hint.lower(), " ").strip()
                candidates.extend(split_mixed_term(hint))
                candidates.extend(split_mixed_term(root.name))
                candidates.extend(split_mixed_term(remainder))
                candidates.append(str(root).lower())
        for candidate in candidates:
            candidate = candidate.strip().lower()
            if len(candidate) < 2 or candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def split_mixed_term(term: str) -> list[str]:
    normalized = term.strip().lower()
    if not normalized:
        return []
    parts = re.findall(r"[a-z]+[0-9]*|[0-9]+|[\u4e00-\u9fff]+", normalized)
    if len(parts) <= 1:
        return [normalized] if len(normalized) >= 2 else []
    result = [part for part in parts if len(part) >= 2]
    if len(normalized) >= 2:
        result.append(normalized)
    return result


def iter_files(root: Path):
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        try:
            children = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for child in children:
            if child.is_dir():
                if not is_ignored_dir(child):
                    stack.append((child, depth + 1))
                continue
            if child.suffix.lower() in ALLOWED_SUFFIXES:
                yield child


def is_ignored_dir(path: Path) -> bool:
    name = path.name.lower()
    return name in {
        "$recycle.bin",
        ".git",
        "__pycache__",
        "node_modules",
        "appdata",
        "windows",
        "program files",
    }


def score_path(path: Path, terms: list[str], raw_query: str) -> tuple[int, str]:
    if not terms:
        return 0, ""

    path_text = str(path).lower()
    name_text = path.stem.lower()
    score = 0
    reasons = []
    for term in terms:
        if term in name_text:
            score += 12
            reasons.append(f"文件名包含 {term}")
        elif term in path_text:
            score += 6
            reasons.append(f"路径包含 {term}")
        else:
            fragments = [fragment for fragment in term.replace("-", " ").replace("_", " ").split() if fragment]
            hit_count = sum(1 for fragment in fragments if fragment in path_text)
            if hit_count:
                score += hit_count * 3
                reasons.append(f"部分匹配 {term}")

    suffix = path.suffix.lower()
    raw = raw_query.lower()
    if suffix in raw:
        score += 4
        reasons.append(f"扩展名匹配 {suffix}")
    if "pdf" in raw and suffix == ".pdf":
        score += 4
    if "图片" in raw and suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        score += 4
    return score, "；".join(reasons[:3])
