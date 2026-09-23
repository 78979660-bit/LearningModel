from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from study_app.app_metadata import APP_INTERNAL_NAME, APP_VERSION
from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, initialize_database


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrustedSource:
    name: str
    institution: str
    url: str
    subject: str
    topic: str
    allowed_domains: tuple[str, ...]


TRUSTED_SOURCES = (
    TrustedSource(
        "MIT OCW 6.006 Assignments",
        "MIT",
        "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-fall-2011/pages/assignments/",
        "数据结构与算法基础",
        "算法与数据结构",
        ("ocw.mit.edu",),
    ),
    TrustedSource(
        "MIT OCW 6.006 Exams",
        "MIT",
        "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-fall-2011/pages/exams/",
        "数据结构与算法基础",
        "第1至第9章综合",
        ("ocw.mit.edu",),
    ),
    TrustedSource(
        "MIT OCW 18.02 Assignments",
        "MIT",
        "https://ocw.mit.edu/courses/18-02sc-multivariable-calculus-fall-2010/",
        "高等数学",
        "多元微积分",
        ("ocw.mit.edu",),
    ),
    TrustedSource(
        "MIT OCW 18.014 Calculus with Theory",
        "MIT",
        "https://ocw.mit.edu/courses/18-014-calculus-with-theory-fall-2010/pages/assignments/",
        "高等数学",
        "级数与证明",
        ("ocw.mit.edu",),
    ),
    TrustedSource(
        "MIT OCW 18.022 Calculus of Several Variables",
        "MIT",
        "https://ocw.mit.edu/courses/18-022-calculus-of-several-variables-fall-2010/pages/assignments/",
        "高等数学",
        "多元微积分综合",
        ("ocw.mit.edu",),
    ),
    TrustedSource(
        "MIT OCW 8.01 Assignments",
        "MIT",
        "https://ocw.mit.edu/courses/8-01sc-classical-mechanics-fall-2016/",
        "大学物理学",
        "大学物理",
        ("ocw.mit.edu",),
    ),
    TrustedSource(
        "MIT OCW 5.111 Assignments",
        "MIT",
        "https://ocw.mit.edu/courses/5-111sc-principles-of-chemical-science-fall-2014/",
        "化学原理",
        "化学原理",
        ("ocw.mit.edu",),
    ),
    TrustedSource(
        "Princeton COS 226 Assignments",
        "Princeton",
        "https://www.cs.princeton.edu/courses/archive/spring24/cos226/assignments.php",
        "数据结构与算法基础",
        "算法与数据结构",
        ("cs.princeton.edu",),
    ),
    TrustedSource(
        "Princeton COS 226 Exams",
        "Princeton",
        "https://www.cs.princeton.edu/courses/archive/spring24/cos226/exams.php",
        "数据结构与算法基础",
        "第1至第9章综合",
        ("cs.princeton.edu",),
    ),
    TrustedSource("Princeton Algorithms Stacks and Queues Exercises", "Princeton", "https://algs4.cs.princeton.edu/13stacks/", "数据结构与算法基础", "第3章：栈和队列", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Analysis Exercises", "Princeton", "https://algs4.cs.princeton.edu/14analysis/", "数据结构与算法基础", "第1章：绪论 / 算法复杂度分析", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Union Find Exercises", "Princeton", "https://algs4.cs.princeton.edu/15uf/", "数据结构与算法基础", "第6章：集合与字典 / 等价类与并查集", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Elementary Sorts Exercises", "Princeton", "https://algs4.cs.princeton.edu/21elementary/", "数据结构与算法基础", "第9章：排序 / 插入排序与希尔排序", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Mergesort Exercises", "Princeton", "https://algs4.cs.princeton.edu/22mergesort/", "数据结构与算法基础", "第9章：排序 / 归并、基数与外部排序", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Quicksort Exercises", "Princeton", "https://algs4.cs.princeton.edu/23quicksort/", "数据结构与算法基础", "第9章：排序 / 交换排序与快速排序", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Priority Queues Exercises", "Princeton", "https://algs4.cs.princeton.edu/24pq/", "数据结构与算法基础", "第5章：树 / 堆与优先队列", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Symbol Tables Exercises", "Princeton", "https://algs4.cs.princeton.edu/31elementary/", "数据结构与算法基础", "第7章：搜索结构 / 静态搜索结构", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms BST Exercises", "Princeton", "https://algs4.cs.princeton.edu/32bst/", "数据结构与算法基础", "第7章：搜索结构 / 二叉搜索树", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Hash Tables Exercises", "Princeton", "https://algs4.cs.princeton.edu/34hash/", "数据结构与算法基础", "第6章：集合与字典 / 字典与哈希表", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Graph Exercises", "Princeton", "https://algs4.cs.princeton.edu/41graph/", "数据结构与算法基础", "第8章：图 / 图的存储与遍历", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Digraph Exercises", "Princeton", "https://algs4.cs.princeton.edu/42digraph/", "数据结构与算法基础", "第8章：图 / 拓扑排序与关键路径", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms MST Exercises", "Princeton", "https://algs4.cs.princeton.edu/43mst/", "数据结构与算法基础", "第8章：图 / 最小生成树与最短路径", ("algs4.cs.princeton.edu",)),
    TrustedSource("Princeton Algorithms Shortest Paths Exercises", "Princeton", "https://algs4.cs.princeton.edu/44sp/", "数据结构与算法基础", "第8章：图 / 最小生成树与最短路径", ("algs4.cs.princeton.edu",)),
)

DOCUMENT_KEYWORDS = (
    "problem set",
    "problem-set",
    "pset",
    "assignment",
    "exercise",
    "exam",
    "quiz",
    "homework",
    "solution",
)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href", "")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = ""
            self._text = []


def fetch_html(url: str, timeout: int = 20) -> str:
    request = Request(
        url,
        headers={"User-Agent": f"{APP_INTERNAL_NAME}/{APP_VERSION} educational-source-indexer"},
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def discover_source(source: TrustedSource) -> list[dict]:
    parser = LinkParser()
    parser.feed(fetch_html(source.url))
    results = []
    if "Exercises" in source.name:
        results.append(
            {
                "source_name": source.name,
                "institution": source.institution,
                "subject_hint": source.subject,
                "topic_hint": source.topic,
                "title": source.name,
                "url": source.url,
                "document_type": "exercise",
                "quality_score": 92,
                "estimated_difficulty": 74,
                "raw": {"landing_page": source.url, "chapter_exercises": True},
            }
        )
    for href, text in parser.links:
        url = urljoin(source.url, href)
        domain = urlparse(url).netloc.lower()
        joined = f"{text} {url}".lower()
        if not any(domain == allowed or domain.endswith("." + allowed) for allowed in source.allowed_domains):
            continue
        if not any(keyword in joined for keyword in DOCUMENT_KEYWORDS) and not url.lower().endswith(".pdf"):
            continue
        title = normalize_title(text or url.rsplit("/", 1)[-1])
        quality = quality_score(source, title, url)
        if quality < 65:
            continue
        results.append(
            {
                "source_name": source.name,
                "institution": source.institution,
                "subject_hint": source.subject,
                "topic_hint": source.topic,
                "title": title,
                "url": url,
                "document_type": document_type(title, url),
                "quality_score": quality,
                "estimated_difficulty": estimated_difficulty(title, url),
                "raw": {"landing_page": source.url},
            }
        )
    return deduplicate(results)


def quality_score(source: TrustedSource, title: str, url: str) -> int:
    score = 65
    joined = f"{title} {url}".lower()
    if source.institution in {"MIT", "Princeton", "UC Berkeley"}:
        score += 15
    if url.lower().endswith(".pdf"):
        score += 5
    if any(word in joined for word in ("problem set", "assignment", "exam", "pset")):
        score += 8
    if "solution" in joined:
        score += 4
    return min(100, score)


def estimated_difficulty(title: str, url: str) -> int:
    joined = f"{title} {url}".lower()
    score = 68
    if any(word in joined for word in ("exam", "final", "challenge", "advanced")):
        score += 12
    if any(word in joined for word in ("midterm", "problem set", "pset")):
        score += 6
    if "solution" in joined:
        score += 2
    return min(92, score)


def document_type(title: str, url: str) -> str:
    joined = f"{title} {url}".lower()
    for kind in ("solution", "exam", "quiz", "problem set", "assignment", "exercise"):
        if kind in joined:
            return kind.replace(" ", "_")
    return "document"


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:240] or "Untitled course document"


def deduplicate(items: list[dict]) -> list[dict]:
    seen = set()
    results = []
    for item in items:
        key = item["url"].split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        item["url"] = key
        results.append(item)
    return results


def finish_collection_run(
    run_id: int,
    status: str,
    discovered: int,
    accepted: int,
    error_message: str,
    db_path=DEFAULT_DB_PATH,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE practice_collection_runs
            SET finished_at = CURRENT_TIMESTAMP, status = ?, discovered_count = ?,
                accepted_count = ?, error_message = ?
            WHERE id = ?
            """,
            (status, discovered, accepted, error_message, run_id),
        )


def collect_weekly_sources(db_path=DEFAULT_DB_PATH) -> dict:
    from study_app.data.collection_backlog import list_collection_backlog, resolve_filled_collection_gaps
    from study_app.data.ds_collection_gaps import audit_and_register_ds_collection_gaps

    initialize_database(db_path)
    from study_app.core.async_tasks import (
        capture_catalog_revision,
        capture_subject_revision,
        revalidate_catalog_revision,
    )

    lifecycle_revision = capture_catalog_revision(db_path)
    ds_gaps_before = audit_and_register_ds_collection_gaps(db_path=db_path)
    with connect(db_path) as connection:
        run_id = connection.execute(
            "INSERT INTO practice_collection_runs(status) VALUES ('running') RETURNING id"
        ).fetchone()["id"]
    discovered = []
    errors = []
    for source in TRUSTED_SOURCES:
        try:
            discovered.extend(discover_source(source))
        except Exception as error:
            errors.append(f"{source.name}: {error}")
    accepted = 0
    try:
        with connect(db_path) as connection:
            revalidate_catalog_revision(lifecycle_revision)
            for item in deduplicate(discovered):
                capture_subject_revision(db_path, item["subject_hint"])
                connection.execute(
                    """
                    INSERT INTO practice_collection_candidates(
                        source_name, institution, subject_hint, topic_hint, title, url,
                        document_type, quality_score, estimated_difficulty, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        title = excluded.title,
                        quality_score = excluded.quality_score,
                        estimated_difficulty = excluded.estimated_difficulty,
                        last_seen_at = CURRENT_TIMESTAMP,
                        raw_json = excluded.raw_json
                    """,
                    (
                        item["source_name"],
                        item["institution"],
                        item["subject_hint"],
                        item["topic_hint"],
                        item["title"],
                        item["url"],
                        item["document_type"],
                        item["quality_score"],
                        item["estimated_difficulty"],
                        dumps(item["raw"]),
                    ),
                )
                accepted += 1
    except Exception as error:
        try:
            finish_collection_run(
                run_id,
                "failed",
                len(discovered),
                0,
                f"collection failed: {type(error).__name__}",
                db_path,
            )
        except Exception as status_error:
            LOGGER.error(
                "Failed to persist weekly collection failure (%s)",
                type(status_error).__name__,
            )
        raise
    processing = None
    try:
        from study_app.data.practice_candidate_processor import process_candidates

        revalidate_catalog_revision(lifecycle_revision)
        priority_processing = []
        for gap in list_collection_backlog(limit=8, db_path=db_path):
            priority_processing.append(
                {
                    "gap": gap,
                    "result": process_candidates(
                        limit=6,
                        min_quality=80,
                        subject_hint=gap["subject"] or None,
                        topic_hint=gap["topic"] or None,
                        target_difficulty=gap["target_difficulty"],
                        db_path=db_path,
                    ),
                }
            )
        processing = {
            "priority_backlog": priority_processing,
            "高等数学": process_candidates(
                limit=8, min_quality=85, subject_hint="高等数学", db_path=db_path
            ),
            "数据结构与算法基础": process_candidates(
                limit=8, min_quality=85, subject_hint="数据结构与算法基础", db_path=db_path
            ),
            "大学物理学": process_candidates(
                limit=5, min_quality=85, subject_hint="大学物理学", db_path=db_path
            ),
            "化学原理": process_candidates(
                limit=5, min_quality=85, subject_hint="化学原理", db_path=db_path
            ),
        }
        resolve_filled_collection_gaps(db_path)
    except Exception as error:
        errors.append(f"candidate processing: {error}")
    chapter_exercises = None
    ds_reclassification = None
    ds_gaps_after = None
    try:
        from study_app.data.ds_collection_gaps import reclassify_trusted_ds_bank

        chapter_exercises = collect_princeton_chapter_exercises(db_path)
        ds_reclassification = reclassify_trusted_ds_bank(db_path)
        ds_gaps_after = audit_and_register_ds_collection_gaps(db_path=db_path)
        resolve_filled_collection_gaps(db_path)
    except Exception as error:
        errors.append(f"data-structure targeted collection: {error}")
    finish_collection_run(
        run_id,
        "partial" if errors else "success",
        len(discovered),
        accepted,
        "\n".join(errors),
        db_path,
    )
    return {
        "run_id": run_id,
        "discovered": len(discovered),
        "accepted": accepted,
        "processing": processing,
        "data_structure_gaps_before": len(ds_gaps_before),
        "data_structure_chapter_exercises": chapter_exercises,
        "data_structure_reclassification": ds_reclassification,
        "data_structure_gaps_after": len(ds_gaps_after or []),
        "errors": errors,
    }


def collection_status(db_path=DEFAULT_DB_PATH) -> dict:
    from study_app.core.composite_templates import COMPOSITE_TEMPLATES
    from study_app.data.collection_backlog import list_collection_backlog

    from study_app.data.database import connect_readonly

    with connect_readonly(db_path) as connection:
        run = connection.execute(
            "SELECT * FROM practice_collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        counts = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN quality_score >= 85 THEN 1 ELSE 0 END), 0) AS high_quality,
                   COALESCE(SUM(CASE WHEN estimated_difficulty >= 80 THEN 1 ELSE 0 END), 0) AS high_difficulty
            FROM practice_collection_candidates
            """
        ).fetchone()
        statuses = connection.execute(
            "SELECT status, COUNT(*) AS count FROM practice_collection_candidates GROUP BY status"
        ).fetchall()
        imported = connection.execute(
            "SELECT COUNT(*) AS count FROM practice_problems WHERE difficulty_source = ?",
            ("trusted_university_estimate",),
        ).fetchone()["count"]
        composite_coverage = {}
        for template in COMPOSITE_TEMPLATES:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count, ROUND(MAX(difficulty_score), 1) AS maximum
                FROM practice_problems
                WHERE template_id = ?
                """,
                (template.template_id,),
            ).fetchone()
            composite_coverage[template.template_id] = {
                "title": template.title,
                "subject": template.subject,
                "count": int(row["count"]),
                "maximum_difficulty": row["maximum"],
            }
    return {
        "latest_run": dict(run) if run else None,
        "candidates": dict(counts),
        "candidate_statuses": {row["status"]: row["count"] for row in statuses},
        "imported_trusted_problems": int(imported),
        "composite_coverage": composite_coverage,
        "pending_backlog": list_collection_backlog(limit=20, db_path=db_path),
        "trusted_sources": [source.name for source in TRUSTED_SOURCES],
    }


def collect_princeton_chapter_exercises(db_path=DEFAULT_DB_PATH) -> dict:
    from study_app.data.practice_candidate_processor import process_candidate

    initialize_database(db_path)
    results = []
    for source in TRUSTED_SOURCES:
        if "Princeton Algorithms" not in source.name or "Exercises" not in source.name:
            continue
        candidate = {
            "source_name": source.name,
            "institution": source.institution,
            "subject_hint": source.subject,
            "topic_hint": source.topic,
            "title": source.name,
            "url": source.url,
            "document_type": "exercise",
            "quality_score": 92,
            "estimated_difficulty": 74,
        }
        try:
            results.append({"source": source.name, **process_candidate(candidate, import_threshold=65)})
        except Exception as error:
            results.append({"source": source.name, "error": str(error)})
    return {
        "sources": len(results),
        "parsed": sum(int(item.get("parsed", 0)) for item in results),
        "imported": sum(int(item.get("imported", 0)) for item in results),
        "results": results,
    }


def collection_is_due(db_path=DEFAULT_DB_PATH, interval_days: int = 7) -> bool:
    from study_app.data.database import connect_readonly

    with connect_readonly(db_path) as connection:
        row = connection.execute(
            """
            SELECT finished_at
            FROM practice_collection_runs
            WHERE status IN ('success', 'partial')
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row or not row["finished_at"]:
        return True
    finished = datetime.fromisoformat(str(row["finished_at"]))
    return datetime.now() - finished >= timedelta(days=interval_days)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect trusted university practice sources.")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    result = collection_status() if args.status else collect_weekly_sources()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
