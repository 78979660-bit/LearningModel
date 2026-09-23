from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from study_app.core.discrete_math_taxonomy import (
    DISCRETE_TEMPLATE_INFO,
    MODULE_TOPICS,
    classify_discrete_text,
    template_for_module,
)
from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, require_initialized_database
from study_app.data.practice_repository import upsert_practice_source
from study_app.data.text_integrity import validate_text_integrity
from study_app.paths import BACKUPS_DIR, DATA_DIR


DEFAULT_OCR_JSONL = DATA_DIR / "reference_materials" / "discrete_math" / "rosen_layout_ocr.jsonl"
CATALOG_PATH = DATA_DIR / "reference_materials" / "discrete_math" / "practice_import_catalog.json"
BOOK_SOURCE_TITLE = "Rosen《离散数学及其应用》原书第8版课后习题"
HOMEWORK_SOURCE_TITLE = "离散数学 hw1-hw2 课程作业"


@dataclass(frozen=True)
class Section:
    code: str
    title: str
    module: str
    topic: str
    start_page: int

    @property
    def chapter(self) -> int:
        return int(self.code.split(".", 1)[0])


@dataclass
class ParsedProblem:
    number: int
    statement: str
    physical_pages: list[int]
    marker: str = ""
    shared_context: str = ""


SECTIONS: tuple[Section, ...] = (
    Section("1.1", "命题逻辑", "命题逻辑", "命题与逻辑联结词", 32),
    Section("1.2", "命题逻辑的应用", "命题逻辑", "命题符号化与条件语句", 46),
    Section("1.3", "命题等价式", "命题逻辑", "真值表与逻辑等价", 54),
    Section("1.4", "谓词和量词", "谓词逻辑", "谓词与量词", 65),
    Section("1.5", "嵌套量词", "谓词逻辑", "量词否定与逻辑等价", 82),
    Section("1.6", "推理规则", "谓词逻辑", "谓词逻辑推理", 93),
    Section("1.7", "证明导论", "证明方法", "直接证明与反证法", 103),
    Section("1.8", "证明的方法和策略", "证明方法", "分情况证明与等价性证明", 112),
    Section("2.1", "集合", "集合及其运算", "集合概念与成员关系", 133),
    Section("2.2", "集合运算", "集合及其运算", "集合运算与恒等式", 143),
    Section("2.3", "函数", "关系与函数", "函数、单射、满射与双射", 154),
    Section("2.4", "序列与求和", "序列、求和与矩阵", "序列", 169),
    Section("2.5", "集合的基数", "序列、求和与矩阵", "可数集与不可数集", 181),
    Section("2.6", "矩阵", "序列、求和与矩阵", "矩阵", 188),
    Section("3.1", "算法", "算法", "算法的概念", 201),
    Section("3.2", "函数的增长", "算法", "函数的增长", 214),
    Section("3.3", "算法的复杂度", "算法", "算法复杂度", 227),
    Section("4.1", "整除性和模算术", "数论和密码学", "整除性与模运算", 244),
    Section("4.2", "整数表示和算法", "数论和密码学", "整数表示与算法", 251),
    Section("4.3", "素数和最大公约数", "数论和密码学", "素数与最大公约数", 260),
    Section("4.4", "解同余方程", "数论和密码学", "同余方程", 276),
    Section("4.5", "同余的应用", "数论和密码学", "同余应用", 286),
    Section("4.6", "密码学", "数论和密码学", "密码学", 291),
    Section("5.1", "数学归纳法", "归纳与递归", "数学归纳法", 309),
    Section("5.2", "强归纳法和良序性", "归纳与递归", "强归纳法与良序性", 327),
    Section("5.3", "递归定义与结构归纳法", "归纳与递归", "递归定义与结构归纳", 337),
    Section("5.4", "递归算法", "归纳与递归", "递归算法", 351),
    Section("5.5", "程序正确性", "归纳与递归", "程序正确性", 360),
    Section("6.1", "计数基础", "计数", "基本计数原理", 373),
    Section("6.2", "鸽巢原理", "计数", "鸽巢原理", 385),
    Section("6.3", "排列与组合", "计数", "排列与组合", 392),
    Section("6.4", "二项式系数和恒等式", "计数", "二项式系数", 398),
    Section("6.5", "排列和组合的推广", "计数", "广义排列组合", 406),
    Section("6.6", "生成排列和组合", "计数", "组合对象生成", 416),
    Section("7.1", "离散概率引论", "离散概率", "离散概率导论", 426),
    Section("7.2", "概率论", "离散概率", "概率论基础", 433),
    Section("7.3", "贝叶斯定理", "离散概率", "贝叶斯定理", 445),
    Section("7.4", "期望值和方差", "离散概率", "期望与方差", 453),
    Section("8.1", "递推关系的应用", "高级计数技术", "递推关系的应用", 473),
    Section("8.2", "求解线性递推关系", "高级计数技术", "线性递推关系", 484),
    Section("8.3", "分治算法和递推关系", "高级计数技术", "分治递推", 494),
    Section("8.4", "生成函数", "高级计数技术", "生成函数", 502),
    Section("8.5", "容斥", "高级计数技术", "容斥原理", 515),
    Section("8.6", "容斥原理的应用", "高级计数技术", "容斥应用", 519),
    Section("9.1", "关系及其性质", "关系与函数", "关系的表示与性质", 531),
    Section("9.2", "n元关系及其应用", "关系与函数", "关系的表示与性质", 541),
    Section("9.3", "关系的表示", "关系与函数", "关系的表示与性质", 549),
    Section("9.4", "关系的闭包", "关系与函数", "关系运算与闭包", 555),
    Section("9.5", "等价关系", "关系与函数", "等价关系与等价类", 564),
    Section("9.6", "偏序", "关系与函数", "偏序关系与Hasse图", 573),
    Section("10.1", "图和图模型", "图", "图模型", 594),
    Section("10.2", "图的术语和几种特殊图", "图", "图的术语与特殊图", 604),
    Section("10.3", "图的表示和图的同构", "图", "图的表示与同构", 618),
    Section("10.4", "连通性", "图", "连通性", 629),
    Section("10.5", "欧拉通路与哈密顿通路", "图", "欧拉路与哈密顿路", 642),
    Section("10.6", "最短通路问题", "图", "最短路", 654),
    Section("10.7", "平面图", "图", "平面图", 664),
    Section("10.8", "图着色", "图", "图着色", 671),
    Section("11.1", "树的概述", "树", "树概论", 689),
    Section("11.2", "树的应用", "树", "树的应用", 699),
    Section("11.3", "树的遍历", "树", "树的遍历", 712),
    Section("11.4", "生成树", "树", "生成树", 724),
    Section("11.5", "最小生成树", "树", "最小生成树", 735),
    Section("12.1", "布尔函数", "布尔代数", "布尔函数", 747),
    Section("12.2", "布尔函数的表示", "布尔代数", "布尔函数表示", 753),
    Section("12.3", "逻辑门电路", "布尔代数", "逻辑门", 756),
    Section("12.4", "电路的极小化", "布尔代数", "布尔函数最小化", 762),
    Section("13.1", "语言和文法", "计算模型", "语言与文法", 779),
    Section("13.2", "带输出的有限状态机", "计算模型", "带输出的有限状态机", 789),
    Section("13.3", "不带输出的有限状态机", "计算模型", "无输出的有限状态机", 795),
    Section("13.4", "语言的识别", "计算模型", "语言识别", 808),
    Section("13.5", "图灵机", "计算模型", "图灵机", 817),
)

CHAPTER_END_PAGES = {1: 132, 2: 200, 3: 243, 4: 308, 5: 372, 6: 425, 7: 472, 8: 530, 9: 593, 10: 688, 11: 746, 12: 778, 13: 831}
BACKMATTER_HEADINGS = ("复习题", "补充练习", "计算机课题", "计算和探索", "写作课题")
STOP_HEADINGS = ("关键术语和结论", *BACKMATTER_HEADINGS)
QUESTION_RE = re.compile(r"^\s*([*xX※★]{0,2})\s*(\d{1,3})\s*[.．,，、]\s*(.*)$")
GROUP_RE = re.compile(r"练习\s*(\d{1,3})\s*[-一~～—至到]+\s*(\d{1,3})")


def normalized_heading(text: str) -> str:
    return re.sub(r"[\s:：·•.。]+", "", text or "")


def clean_line(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    # Tesseract frequently keeps the vertical page-rule immediately before an
    # exercise heading or question number.  It is layout noise, not a math
    # absolute-value bar in these positions.
    value = re.sub(r"^[|\\]\s*(?=(?:练习|[*xX※★]{0,2}\s*\d{1,3}\s*[.．,，、]))", "", value)
    value = re.sub(r"\s+([，。；：、！？）》】])", r"\1", value)
    value = re.sub(r"([（《【])\s+", r"\1", value)
    return value


def load_ocr_pages(path: Path) -> dict[int, str]:
    pages: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        pages[int(item["physical_page"])] = str(item.get("text") or "")
    missing = [page for page in range(32, 832) if page not in pages]
    if missing:
        raise ValueError(f"版式 OCR 不完整，缺少页面：{missing[:10]}")
    return pages


def page_lines(pages: dict[int, str], start: int, end: int) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for page in range(start, end + 1):
        for raw_line in pages[page].splitlines():
            line = clean_line(raw_line)
            if line:
                result.append((page, line))
    return result


def line_has_heading(line: str, headings: Iterable[str]) -> str:
    normalized = normalized_heading(line)
    for heading in headings:
        needle = normalized_heading(heading)
        if normalized == needle or (normalized.startswith(needle) and len(normalized) <= len(needle) + 5):
            return heading
    return ""


def candidate_exercise_starts(lines: list[tuple[int, str]]) -> list[int]:
    candidates: list[int] = []
    for index, (_page, line) in enumerate(lines):
        compact = normalized_heading(line)
        if compact == "练习" or compact.startswith("练习1"):
            tail = [part for _tail_page, tail_line in lines[index:index + 120] for part in split_inline_markers(tail_line)]
            hits = {int(match.group(2)) for match in (QUESTION_RE.match(value) for value in tail) if match}
            if {1, 2}.issubset(hits) or compact.startswith("练习1"):
                candidates.append(index)
            elif compact == "练习":
                # Diagram-heavy exercises can have no OCR-visible 1/2 marker;
                # the standalone heading is still the authoritative boundary.
                candidates.append(index)
    return candidates


def split_inline_markers(line: str) -> list[str]:
    if QUESTION_RE.match(line):
        return [line]
    positions = []
    for match in re.finditer(r"(?<!\d)([*★]{0,2}\s*\d{1,3}[.．,，、])(?=\s*\D)", line):
        preceding = line[match.start() - 1] if match.start() else ""
        if (
            match.start() == 0
            or preceding.isspace()
            or preceding in "。！？；：?）)]}"
            or line[max(0, match.start() - 2):match.start()] == "练习"
        ):
            positions.append(match.start())
    if not positions:
        return [line]
    # Only split markers embedded after an exercise label.  Number sequences
    # inside a problem (for example "10, 11, 100, 101") otherwise look just
    # like several question starts.
    prefix = line[:positions[0]].strip()
    if not normalized_heading(prefix).startswith("练习"):
        return [line]
    result: list[str] = []
    if positions[0] > 0:
        prefix = line[:positions[0]].strip()
        if prefix:
            result.append(prefix)
    result.extend(
        line[start:end].strip()
        for start, end in zip(positions, positions[1:] + [len(line)])
        if line[start:end].strip()
    )
    return result


def parse_numbered_questions(lines: list[tuple[int, str]]) -> tuple[list[ParsedProblem], list[int]]:
    expanded: list[tuple[int, str]] = []
    for page, line in lines:
        expanded.extend((page, part) for part in split_inline_markers(line))
    problems: list[ParsedProblem] = []
    current: ParsedProblem | None = None
    shared_ranges: list[tuple[int, int, str, int]] = []
    pending_context: tuple[int, int, list[str], int] | None = None
    preamble: list[tuple[int, str]] = []
    for page, line in expanded:
        group_match = GROUP_RE.search(line)
        if group_match:
            if current is not None:
                problems.append(current)
                current = None
            if pending_context is not None:
                shared_ranges.append(
                    (pending_context[0], pending_context[1], " ".join(pending_context[2]), pending_context[3])
                )
            pending_context = (int(group_match.group(1)), int(group_match.group(2)), [line], page)
            continue
        # A recurring OCR error turns the first marker into "Le".  This
        # correction is deliberately limited to the first problem after the
        # exercise heading.
        if current is None and not problems:
            first_problem = re.match(r"^(?:Le|l[eE]?|I[eE]?)\s+(.{4,})$", line)
            if first_problem:
                line = "1. " + first_problem.group(1)
        marker = QUESTION_RE.match(line)
        if marker:
            number = int(marker.group(2))
            previous_number = current.number if current is not None else (problems[-1].number if problems else None)
            pending_bridge = (
                pending_context is not None
                and pending_context[0] <= number <= pending_context[1] + 3
            )
            plausible_next = (
                previous_number is None
                or (number > previous_number and number <= previous_number + 3)
                or pending_bridge
            )
            if number < 1 or not plausible_next:
                if pending_context is not None:
                    pending_context[2].append(line)
                elif current is not None:
                    current.statement = clean_line(current.statement + " " + line)
                    if page not in current.physical_pages:
                        current.physical_pages.append(page)
                else:
                    preamble.append((page, line))
                continue
            if current is not None:
                problems.append(current)
            if pending_context is not None and number >= pending_context[0]:
                shared_ranges.append(
                    (pending_context[0], pending_context[1], " ".join(pending_context[2]), pending_context[3])
                )
                pending_context = None
            context = next((text for start, end, text, _context_page in reversed(shared_ranges) if start <= number <= end), "")
            lead = clean_line(marker.group(3))
            if number == 1 and preamble:
                lead = clean_line(" ".join(text for _preamble_page, text in preamble) + " " + lead)
            elif number > 1 and not problems and preamble:
                inferred = clean_line(" ".join(text for _preamble_page, text in preamble))
                if len(inferred) >= 8:
                    problems.append(
                        ParsedProblem(
                            number=1,
                            statement=inferred,
                            physical_pages=sorted({preamble_page for preamble_page, _text in preamble}),
                        )
                    )
            current = ParsedProblem(number=number, statement=lead, physical_pages=[page], marker=marker.group(1), shared_context=context)
            continue
        if pending_context is not None:
            pending_context[2].append(line)
        elif current is not None:
            current.statement = clean_line(current.statement + " " + line)
            if page not in current.physical_pages:
                current.physical_pages.append(page)
        else:
            preamble.append((page, line))
    if current is not None:
        problems.append(current)
    if pending_context is not None:
        shared_ranges.append((pending_context[0], pending_context[1], " ".join(pending_context[2]), pending_context[3]))

    # Figure-only problems may expose just the shared range (for example
    # "练习 1—4") while the individual diagrams contain no OCR-readable text.
    # Keep one traceable record per numbered problem instead of dropping them.
    existing_numbers = {problem.number for problem in problems}
    for start, end, context, context_page in shared_ranges:
        if end < start or end - start > 100:
            continue
        for number in range(start, end + 1):
            if number in existing_numbers:
                continue
            problems.append(
                ParsedProblem(
                    number=number,
                    statement=clean_line(f"{context} [本题题图或分项需参照教材原页。]"),
                    physical_pages=[context_page],
                    shared_context=context,
                )
            )
            existing_numbers.add(number)

    # Once the exercise heading has been found, question numbers are unique in
    # the section.  De-duplicate OCR echoes and retain the most informative copy.
    by_number: dict[int, ParsedProblem] = {}
    for problem in problems:
        if len(clean_line(problem.statement)) < 4:
            continue
        previous = by_number.get(problem.number)
        if previous is None or len(problem.statement) > len(previous.statement):
            by_number[problem.number] = problem
    selected = [by_number[number] for number in sorted(by_number)]
    if not selected:
        return [], []
    max_number = selected[-1].number
    missing = sorted(set(range(1, max_number + 1)) - {item.number for item in selected})
    if missing:
        for number in missing:
            nearest = min(selected, key=lambda item: abs(item.number - number))
            selected.append(
                ParsedProblem(
                    number=number,
                    statement="[OCR 未能可靠提取本题题面，请参照教材原页。]",
                    physical_pages=list(nearest.physical_pages),
                )
            )
        selected.sort(key=lambda item: item.number)
    return selected, missing


def section_exercises(pages: dict[int, str], section: Section, end_page: int, *, last_in_chapter: bool) -> tuple[list[ParsedProblem], dict]:
    lines = page_lines(pages, section.start_page, end_page)
    if last_in_chapter:
        stop_index = next((index for index, (_page, line) in enumerate(lines) if line_has_heading(line, STOP_HEADINGS)), len(lines))
        lines = lines[:stop_index]
    starts = candidate_exercise_starts(lines)
    if not starts:
        # Fallback: use the last occurrence of question 1 that starts a reasonably long numbered run.
        one_indexes = [index for index, (_page, line) in enumerate(lines) if (match := QUESTION_RE.match(line)) and int(match.group(2)) == 1]
        starts = one_indexes
    best: tuple[list[ParsedProblem], list[int], int] = ([], [], -1)
    for start in starts:
        problems, missing = parse_numbered_questions(lines[start:])
        score = len(problems) - (len(missing) * 3)
        if score > best[2]:
            best = (problems, missing, score)
    return best[0], {"missing_numbers": best[1], "candidate_starts": len(starts), "line_count": len(lines)}


def chapter_backmatter(pages: dict[int, str], chapter: int, start_page: int, end_page: int) -> list[tuple[str, list[ParsedProblem], list[int]]]:
    lines = page_lines(pages, start_page, end_page)
    heading_indexes: list[tuple[int, str]] = []
    for index, (_page, line) in enumerate(lines):
        heading = line_has_heading(line, BACKMATTER_HEADINGS)
        if heading:
            heading_indexes.append((index, heading))
    groups = []
    for position, (index, heading) in enumerate(heading_indexes):
        end = heading_indexes[position + 1][0] if position + 1 < len(heading_indexes) else len(lines)
        problems, missing = parse_numbered_questions(lines[index + 1:end])
        if problems:
            groups.append((heading, problems, missing))
    return groups


def classify_problem(section: Section, statement: str) -> tuple[str, str, str]:
    template_id, module, topic = classify_discrete_text(
        statement,
        fallback_module=section.module,
        fallback_topic=section.topic,
    )
    # Keep section identity unless the text contains a concrete keyword from another topic.
    if module != section.module and section.chapter not in {1, 2}:
        template_id, _description = template_for_module(section.module)
        module, topic = section.module, section.topic
    return template_id, module, topic


def difficulty_for(marker: str, topic: str, topic_difficulties: dict[str, float]) -> float:
    base = topic_difficulties.get(topic, 0.62) * 100
    stars = marker.count("*") + marker.count("★") + marker.lower().count("x")
    if stars >= 2:
        return min(95.0, max(base, 86.0))
    if stars == 1:
        return min(90.0, max(base, 74.0))
    return min(82.0, max(25.0, round(base)))


def load_topic_difficulties(db_path: Path) -> dict[str, float]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = connection.execute(
        """
        SELECT t.name, t.difficulty
        FROM topics t JOIN modules m ON m.id=t.module_id JOIN subjects s ON s.id=m.subject_id
        WHERE s.name='离散数学'
        """
    ).fetchall()
    connection.close()
    return {str(name): float(difficulty or 0.62) for name, difficulty in rows}


def build_textbook_payloads(pages: dict[int, str], topic_difficulties: dict[str, float]) -> tuple[list[dict], dict]:
    payloads: list[dict] = []
    report = {"sections": [], "backmatter": []}
    for index, section in enumerate(SECTIONS):
        next_section = SECTIONS[index + 1] if index + 1 < len(SECTIONS) else None
        last_in_chapter = next_section is None or next_section.chapter != section.chapter
        end_page = (next_section.start_page - 1) if not last_in_chapter else CHAPTER_END_PAGES[section.chapter]
        problems, diagnostics = section_exercises(pages, section, end_page, last_in_chapter=last_in_chapter)
        report["sections"].append({"section": section.code, "count": len(problems), **diagnostics})
        for problem in problems:
            statement = clean_line((problem.shared_context + " " if problem.shared_context else "") + problem.statement)
            template_id, module, topic = classify_problem(section, statement)
            difficulty = difficulty_for(problem.marker, topic, topic_difficulties)
            payloads.append(
                {
                    "template_id": template_id,
                    "title": f"Rosen 8e {section.code} 练习 {problem.number}",
                    "statement": statement,
                    "answer_outline": "",
                    "common_errors": [],
                    "difficulty_score": difficulty,
                    "difficulty_source": "rosen_8e_marker_and_topic_default",
                    "subject": "离散数学",
                    "topic": topic,
                    "tags": [module, topic, f"第{section.chapter}章", section.code, "教材课后习题"],
                    "source_note": f"{BOOK_SOURCE_TITLE}；{section.code} {section.title}；PDF物理页 {problem.physical_pages[0]}-{problem.physical_pages[-1]}。",
                    "raw": {
                        "source_kind": "textbook_exercise",
                        "book": BOOK_SOURCE_TITLE,
                        "chapter": section.chapter,
                        "section": section.code,
                        "section_title": section.title,
                        "exercise_number": problem.number,
                        "physical_pages": problem.physical_pages,
                        "difficulty_marker": problem.marker,
                        "answer_available": False,
                        "ocr_route": "tesseract-chi_sim+eng-psm6-layout",
                    },
                }
            )
        if last_in_chapter:
            groups = chapter_backmatter(pages, section.chapter, section.start_page, CHAPTER_END_PAGES[section.chapter])
            for heading, group_problems, missing in groups:
                report["backmatter"].append({"chapter": section.chapter, "group": heading, "count": len(group_problems), "missing_numbers": missing})
                for problem in group_problems:
                    statement = clean_line((problem.shared_context + " " if problem.shared_context else "") + problem.statement)
                    template_id, module, topic = classify_problem(section, statement)
                    difficulty = difficulty_for(problem.marker, topic, topic_difficulties)
                    payloads.append(
                        {
                            "template_id": template_id,
                            "title": f"Rosen 8e 第{section.chapter}章 {heading} {problem.number}",
                            "statement": statement,
                            "answer_outline": "",
                            "common_errors": [],
                            "difficulty_score": difficulty,
                            "difficulty_source": "rosen_8e_marker_and_topic_default",
                            "subject": "离散数学",
                            "topic": topic,
                            "tags": [module, topic, f"第{section.chapter}章", heading, "教材章末习题"],
                            "source_note": f"{BOOK_SOURCE_TITLE}；第{section.chapter}章 {heading}；PDF物理页 {problem.physical_pages[0]}-{problem.physical_pages[-1]}。",
                            "raw": {
                                "source_kind": "textbook_chapter_backmatter",
                                "book": BOOK_SOURCE_TITLE,
                                "chapter": section.chapter,
                                "group": heading,
                                "exercise_number": problem.number,
                                "physical_pages": problem.physical_pages,
                                "difficulty_marker": problem.marker,
                                "answer_available": False,
                                "ocr_route": "tesseract-chi_sim+eng-psm6-layout",
                            },
                        }
                    )
    # Title is the idempotency key within a template; reject duplicate generated identities.
    identities = [(item["template_id"], item["title"]) for item in payloads]
    if len(identities) != len(set(identities)):
        raise ValueError("教材题目生成了重复的 template_id/title 标识")
    return payloads, report


def update_homework_classification(connection) -> int:
    rows = connection.execute(
        """
        SELECT p.id, p.title, p.statement, p.topic_hint, p.tags_json, p.raw_json
        FROM practice_problems p
        WHERE p.subject_hint='离散数学' AND p.title GLOB 'HW[12] 第*题'
        ORDER BY p.id
        """
    ).fetchall()
    source_id = upsert_practice_source(
        connection,
        "uploaded_homework",
        HOMEWORK_SOURCE_TITLE,
        "",
        "用户提供的 hw1.pdf 与 hw2.pdf；共22道题，用户已确认全部正确。",
    )
    updated = 0
    for row in rows:
        template_id, module, topic = classify_discrete_text(
            f"{row['topic_hint']} {row['statement']}",
            fallback_module="命题逻辑" if str(row["title"]).startswith("HW1") else "证明方法",
            fallback_topic=str(row["topic_hint"] or "").split("、", 1)[0],
        )
        raw = json.loads(row["raw_json"] or "{}")
        raw.update(
            {
                "source_kind": "uploaded_homework",
                "classification_version": "discrete-math-taxonomy-v1",
                "module": module,
                "topic": topic,
                "answer_available": bool(str(raw.get("answer_outline") or "").strip()),
            }
        )
        tags = list(dict.fromkeys([module, topic, *json.loads(row["tags_json"] or "[]"), "课程作业"]))
        connection.execute(
            """
            UPDATE practice_problems
            SET template_id=?, topic_hint=?, tags_json=?, source_id=?, source_note=?, raw_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                template_id,
                topic,
                dumps(tags),
                source_id,
                f"{HOMEWORK_SOURCE_TITLE}；题面由课程作业解析并按离散数学知识点重新分类。",
                dumps(raw),
                row["id"],
            ),
        )
        updated += 1
    return updated


def ensure_templates(connection) -> None:
    for template_id, (module, description) in DISCRETE_TEMPLATE_INFO.items():
        connection.execute(
            """
            INSERT INTO practice_templates(
                template_id, subject_hint, topic_hint, title, description,
                generation_rules_json, source_json, updated_at
            ) VALUES (?, '离散数学', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(template_id) DO UPDATE SET
                subject_hint=excluded.subject_hint, topic_hint=excluded.topic_hint,
                title=excluded.title, description=excluded.description,
                generation_rules_json=excluded.generation_rules_json,
                source_json=excluded.source_json, updated_at=CURRENT_TIMESTAMP
            """,
            (
                template_id,
                module,
                module,
                description,
                dumps([
                    "优先选择与当前知识点和章节一致的真实教材题或课程作业题。",
                    "教材题若依赖图表，必须同时向用户提示原书物理页码。",
                    "没有标准答案的题不得伪装为已验证答案题；提交后由学习助理核验。",
                    "难度优先服从教材星号分级，其次使用知识点基准。",
                ]),
                dumps({"kind": "discrete_math_taxonomy", "version": 1}),
            ),
        )


def import_payloads(connection, payloads: list[dict], source_id: int) -> int:
    imported = 0
    for item in payloads:
        validate_text_integrity(item, context="离散数学教材题库")
        raw = dict(item["raw"])
        raw["statement_sha256"] = hashlib.sha256(item["statement"].encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO practice_problems(
                template_id, title, statement, answer_outline,
                common_errors_json, difficulty_score, difficulty_source,
                subject_hint, topic_hint, tags_json, source_id, source_note,
                raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(template_id, title) DO UPDATE SET
                statement=excluded.statement, answer_outline=excluded.answer_outline,
                common_errors_json=excluded.common_errors_json,
                difficulty_score=excluded.difficulty_score,
                difficulty_source=excluded.difficulty_source,
                subject_hint=excluded.subject_hint, topic_hint=excluded.topic_hint,
                tags_json=excluded.tags_json, source_id=excluded.source_id,
                source_note=excluded.source_note, raw_json=excluded.raw_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                item["template_id"], item["title"], item["statement"], item["answer_outline"],
                dumps(item["common_errors"]), item["difficulty_score"], item["difficulty_source"],
                item["subject"], item["topic"], dumps(item["tags"]), source_id,
                item["source_note"], dumps(raw),
            ),
        )
        imported += 1
    return imported


def write_catalog(path: Path, payloads: list[dict], report: dict, homework_count: int, backup_path: Path | None) -> None:
    by_template: dict[str, int] = {}
    by_chapter: dict[str, int] = {}
    for item in payloads:
        by_template[item["template_id"]] = by_template.get(item["template_id"], 0) + 1
        chapter = str(item["raw"]["chapter"])
        by_chapter[chapter] = by_chapter.get(chapter, 0) + 1
    catalog = {
        "source": BOOK_SOURCE_TITLE,
        "ocr_source": str(path),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "textbook_problem_count": len(payloads),
        "homework_problem_count": homework_count,
        "by_template": dict(sorted(by_template.items())),
        "by_chapter": dict(sorted(by_chapter.items(), key=lambda item: int(item[0]))),
        "answer_policy": "教材和作业未附标准答案，answer_outline 留空；不得进入要求本地标准答案的自动组卷池。",
        "backup_path": str(backup_path) if backup_path else "",
        "parser_report": report,
    }
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Rosen 8e exercises and course homework into the discrete mathematics practice bank.")
    parser.add_argument("--ocr-jsonl", type=Path, default=DEFAULT_OCR_JSONL)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db_path = require_initialized_database(args.db)
    pages = load_ocr_pages(args.ocr_jsonl)
    topic_difficulties = load_topic_difficulties(db_path)
    payloads, report = build_textbook_payloads(pages, topic_difficulties)
    summary = {
        "textbook_problem_count": len(payloads),
        "section_problem_count": sum(item["count"] for item in report["sections"]),
        "backmatter_problem_count": sum(item["count"] for item in report["backmatter"]),
        "sections_with_zero": [item["section"] for item in report["sections"] if not item["count"]],
        "sections_with_missing_numbers": [item for item in report["sections"] if item["missing_numbers"]],
    }
    if args.dry_run:
        print(json.dumps({"summary": summary, "report": report}, ensure_ascii=False, indent=2))
        return

    backup_dir = BACKUPS_DIR / f"discrete_math_practice_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_path = backup_dir / db_path.name
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)

    with connect(db_path) as connection:
        ensure_templates(connection)
        source_id = upsert_practice_source(
            connection,
            "textbook_exercise",
            BOOK_SOURCE_TITLE,
            "",
            "用户提供的扫描版教材；题面由本地中文 OCR 提取，保留 PDF 物理页码与 OCR 路由。",
        )
        imported = import_payloads(connection, payloads, source_id)
        homework_count = update_homework_classification(connection)

    write_catalog(args.ocr_jsonl, payloads, report, homework_count, backup_path)
    print(json.dumps({**summary, "imported": imported, "homework_updated": homework_count, "backup": str(backup_path), "catalog": str(CATALOG_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
