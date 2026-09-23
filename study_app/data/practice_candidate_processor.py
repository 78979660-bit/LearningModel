from __future__ import annotations

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from learning_difficulty import infer_problem_difficulty
from study_app.app_metadata import APP_INTERNAL_NAME, APP_VERSION
from study_app.ai.attachment_parser import AttachmentProblemBlock, parse_problem_blocks_from_text
from study_app.core.practice_bank import template_for_topic
from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, initialize_database
from study_app.data.practice_repository import (
    ensure_practice_templates_seeded,
    import_practice_problem,
)
from study_app.data.text_extractor import extract_text, is_usable_text, normalize_whitespace
from study_app.paths import CACHE_DIR


CACHE_ROOT = CACHE_DIR / "practice_source_cache"
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._href = ""
        self._link_text: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "nav", "footer"}:
            self._ignored += 1
        if tag.lower() == "a":
            self._href = dict(attrs).get("href", "")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._link_text).strip()))
            self._href = ""
            self._link_text = []
        if tag.lower() in {"script", "style", "nav", "footer"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        if data.strip():
            self.text.append(data)
            if self._href:
                self._link_text.append(data)


def fetch_bytes(url: str, timeout: int = 30) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={"User-Agent": f"{APP_INTERNAL_NAME}/{APP_VERSION} educational-source-parser"},
    )
    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        data = response.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("document exceeds download limit")
    return data, content_type


def candidate_text(candidate: dict) -> tuple[str, str]:
    data, content_type = fetch_bytes(candidate["url"])
    return document_text(data, content_type, candidate["url"], depth=0)


def document_text(data: bytes, content_type: str, url: str, depth: int) -> tuple[str, str]:
    if content_type == "application/pdf" or data.startswith(b"%PDF"):
        return extract_downloaded_pdf(data, url), url

    parser = PageParser()
    parser.feed(data.decode("utf-8"))
    document_links = [
        urljoin(url, href)
        for href, text in parser.links
        if (
            ".pdf" in href.lower()
            or "(pdf)" in text.lower()
            or "download file" in text.lower()
            or (depth < 2 and "/resources/" in href.lower())
        )
    ]
    for document_url in document_links[:8]:
        try:
            child_data, child_type = fetch_bytes(document_url)
            text, resolved_url = document_text(child_data, child_type, document_url, depth + 1)
            if is_usable_text(text):
                return text, resolved_url
        except Exception:
            continue
    return normalize_whitespace("\n".join(parser.text))[:80000], url


def extract_downloaded_pdf(data: bytes, url: str) -> str:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    path = CACHE_ROOT / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".pdf")
    if not path.exists():
        path.write_bytes(data)
    return extract_text(path, max_chars=80000).text


def classify_topic(subject: str, text: str, fallback: str) -> str:
    if subject == "数据结构与算法基础":
        from study_app.core.ds_course_taxonomy import classify_ds_text

        classified = classify_ds_text(text)
        if classified:
            return f"{classified.chapter} / {classified.topic}"
    joined = text.lower()
    rules = {
        "数据结构与算法基础": [
            ("AVL 树旋转与插入删除", ("avl", "rotation", "balanced tree")),
            ("哈希表", ("hash", "probing", "hash table")),
            ("复杂度分析", ("recurrence", "complexity", "asymptotic", "big-o")),
            ("图结构", ("graph algorithm", "digital circuit layout", "shortest path", "minimum spanning", "vertex", "vertices")),
            ("二叉搜索树", ("binary search tree", "bst", "range index", "b-tree", "rank(")),
        ],
        "高等数学": [
            ("曲线与曲面积分", ("stokes", "gauss", "surface integral", "line integral", "flux")),
            ("三重积分", ("triple integral", "cylindrical coordinates", "spherical coordinates")),
            ("多元函数微分学", ("partial derivative", "gradient", "lagrange multiplier")),
            ("级数", ("series", "convergence", "power series")),
        ],
        "大学物理学": [
            ("刚体与转动", ("angular momentum", "torque", "rotational")),
            ("力学", ("newton", "kinematics", "collision", "momentum")),
            ("热力学", ("thermodynamic", "entropy", "heat engine")),
        ],
        "化学原理": [
            ("化学平衡与相平衡", ("equilibrium", "phase", "chemical potential")),
            ("化学动力学", ("kinetic", "rate law", "arrhenius")),
            ("热化学", ("enthalpy", "entropy", "gibbs")),
        ],
    }
    for topic, keywords in rules.get(subject, []):
        if any(keyword in joined for keyword in keywords):
            return topic
    return fallback


def statement_quality(statement: str) -> int:
    score = 0
    length = len(statement.strip())
    lowered = statement.lower()
    navigation_markers = (
        "course info",
        "learning resource types",
        "download course",
        "terms and conditions",
        "you are leaving",
        "previous\n|\nnext",
    )
    if sum(marker in lowered for marker in navigation_markers) >= 2:
        return 0
    command_markers = ("?", "show", "prove", "compute", "find", "determine", "design", "calculate", "derive", "explain")
    if not any(token in lowered for token in command_markers):
        return 0
    if 80 <= length <= 1800:
        score += 45
    elif length >= 45:
        score += 25
    if any(token in lowered for token in command_markers):
        score += 25
    if re.search(r"[=+\-*/^]|O\(|\b\d+\b", statement):
        score += 15
    if len(statement.split()) >= 12:
        score += 15
    return min(100, score)


def difficulty_for_block(candidate: dict, topic: str, statement: str) -> float:
    base = float(candidate.get("estimated_difficulty") or 70)
    inferred = infer_problem_difficulty(
        {"subject": candidate["subject_hint"], "topic": topic},
        {"title": candidate["title"], "statement": statement},
    )
    inferred_score = float(inferred.get("difficulty_score") or base)
    points = [int(value) for value in re.findall(r"\[(\d{1,3})\s+points?\]", statement, flags=re.I)]
    point_boost = 0
    if points:
        maximum = max(points)
        point_boost = 12 if maximum >= 40 else 8 if maximum >= 25 else 4 if maximum >= 10 else 0
    return max(45.0, min(95.0, round(0.65 * base + 0.35 * inferred_score + point_boost, 1)))


def process_candidate(
    candidate: dict,
    import_threshold: int = 75,
    *,
    db_path=DEFAULT_DB_PATH,
    connection=None,
) -> dict:
    text, resolved_url = candidate_text(candidate)
    blocks = parse_problem_blocks_from_text(text, limit=60)
    if not blocks and "algs4.cs.princeton.edu" in candidate["url"]:
        blocks = parse_algs4_exercises(text, limit=60)
    imported_ids = []
    rejected = 0
    for block in blocks:
        if statement_quality(block.statement) < import_threshold:
            rejected += 1
            continue
        from study_app.core.composite_templates import composite_template_for_text

        composite = composite_template_for_text(block.statement)
        topic = (
            " + ".join(composite.components)
            if composite and composite.subject == candidate["subject_hint"]
            else classify_topic(candidate["subject_hint"], block.statement, candidate["topic_hint"])
        )
        template_id = composite.template_id if composite else template_for_topic(topic)[0]
        imported_ids.append(
            import_practice_problem(
                {
                    "template_id": template_id,
                    "title": f"{candidate['institution']} {candidate['title']} - {block.title}",
                    "statement": block.statement,
                    "difficulty_score": difficulty_for_block(candidate, topic, block.statement),
                    "difficulty_source": "trusted_university_estimate",
                    "subject": candidate["subject_hint"],
                    "topic": topic,
                    "tags": [candidate["institution"], candidate["document_type"], "trusted_university_source"],
                    "source_note": f"{candidate['institution']} 官方课程题目，经本地解析与质量校验。",
                    "source": {
                        "type": "trusted_university",
                        "title": candidate["title"],
                        "url": resolved_url,
                        "note": candidate["source_name"],
                    },
                },
                db_path=db_path,
                connection=connection,
            )
        )
    return {"parsed": len(blocks), "imported": len(imported_ids), "rejected": rejected, "ids": imported_ids}


def parse_algs4_exercises(text: str, limit: int = 60) -> list[AttachmentProblemBlock]:
    start = text.find("Exercises")
    if start < 0:
        return []
    section = normalize_whitespace(text[start + len("Exercises"):])
    section = re.split(r"\b(?:Q \+ A|Web Exercises|Selected Solutions|Creative Problems)\b", section, maxsplit=1)[0]
    command = (
        r"(?=\b(?:Add|Give|Suppose|Write|What|Develop|Create|Prove|Design|Implement|"
        r"Devise|Show|Find|Determine|Explain|Which|Given|Describe|Draw|Trace)\b)"
    )
    chunks = re.split(command, section)
    blocks = []
    seen = set()
    for chunk in chunks:
        statement = re.split(r"\b(?:Solution|Answer|Hint|Partial solution)\s*[:.]?", chunk, maxsplit=1)[0].strip()
        if not (60 <= len(statement) <= 1800):
            continue
        if statement_quality(statement) < 55:
            continue
        key = re.sub(r"\W+", "", statement.lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        blocks.append(
            AttachmentProblemBlock(
                title=f"Exercise {len(blocks) + 1}",
                statement=statement,
                source="princeton_algs4_exercise",
            )
        )
        if len(blocks) >= limit:
            break
    return blocks


def process_candidates(
    limit: int = 8,
    min_quality: int = 85,
    subject_hint: str | None = None,
    topic_hint: str | None = None,
    target_difficulty: float | None = None,
    db_path=DEFAULT_DB_PATH,
) -> dict:
    path = ensure_practice_templates_seeded(db_path)
    subject_clause = "AND subject_hint = ?" if subject_hint else ""
    params = [min_quality]
    if subject_hint:
        params.append(subject_hint)
    topic_order = ""
    if topic_hint:
        topic_tokens = [
            token.strip()
            for token in re.split(r"[+/、，,]", topic_hint)
            if len(token.strip()) >= 2
        ][:4]
        topic_checks = []
        for token in topic_tokens or [topic_hint]:
            topic_checks.append("(topic_hint LIKE ? OR title LIKE ? OR raw_json LIKE ?)")
            needle = f"%{token}%"
            params.extend([needle, needle, needle])
        topic_order = f"CASE WHEN {' OR '.join(topic_checks)} THEN 0 ELSE 1 END,"
    difficulty_order = ""
    if target_difficulty is not None:
        difficulty_order = "ABS(COALESCE(estimated_difficulty, 70) - ?),"
        params.append(target_difficulty)
    params.append(limit)
    with connect(path) as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM practice_collection_candidates
            WHERE status IN ('discovered', 'parse_failed') AND quality_score >= ?
              AND document_type != 'solution'
              AND title NOT LIKE '%Solution%'
              {subject_clause}
            ORDER BY
                {topic_order}
                {difficulty_order}
                CASE
                    WHEN url LIKE '%/resources/%' OR url LIKE '%.pdf%' THEN 0
                    WHEN title LIKE '%Problem Set%' OR title LIKE '%Assignment%' THEN 1
                    ELSE 2
                END,
                estimated_difficulty DESC, quality_score DESC, id
            LIMIT ?
            """,
            params,
        ).fetchall()
    total = {"documents": len(rows), "parsed": 0, "imported": 0, "rejected": 0, "errors": []}
    for row in rows:
        candidate = dict(row)
        try:
            with connect(path) as connection:
                result = process_candidate(
                    candidate,
                    db_path=path,
                    connection=connection,
                )
                raw = json.loads(candidate.get("raw_json") or "{}")
                raw["processing"] = result
                connection.execute(
                    "UPDATE practice_collection_candidates SET status = ?, raw_json = ? WHERE id = ?",
                    ("imported" if result["imported"] else "needs_review", dumps(raw), candidate["id"]),
                )
        except Exception as error:
            total["errors"].append(f"{candidate['title']}: {error}")
            with connect(path) as connection:
                connection.execute(
                    "UPDATE practice_collection_candidates SET status = 'parse_failed' WHERE id = ?",
                    (candidate["id"],),
                )
        else:
            total["parsed"] += result["parsed"]
            total["imported"] += result["imported"]
            total["rejected"] += result["rejected"]
    return total


def reset_trusted_imports(db_path=DEFAULT_DB_PATH) -> dict:
    initialize_database(db_path)
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id FROM practice_problems WHERE difficulty_source = ?",
            ("trusted_university_estimate",),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        connection.execute(
            "DELETE FROM practice_problems WHERE difficulty_source = ?",
            ("trusted_university_estimate",),
        )
        connection.execute(
            """
            UPDATE practice_collection_candidates
            SET status = 'discovered'
            WHERE status IN ('imported', 'needs_review', 'parse_failed')
            """
        )
    return {"removed_problem_ids": ids, "removed_count": len(ids)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse trusted university candidates into practice problems.")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--min-quality", type=int, default=85)
    parser.add_argument("--reset-trusted", action="store_true")
    args = parser.parse_args()
    result = reset_trusted_imports() if args.reset_trusted else process_candidates(args.limit, args.min_quality)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
