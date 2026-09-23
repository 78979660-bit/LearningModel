from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from study_app.core.local_practice_candidates import build_candidate_pool
from study_app.core.local_practice_composer import compose_local_practice_paper
from study_app.core.local_practice_pdf import (
    LocalPracticePDFError,
    _paragraph_text,
    render_practice_pdfs,
)
from study_app.core.local_practice_spec import LocalPracticePaperSpec


def make_paper(*, long_text: bool = False, chinese: bool = False):
    prefix = "题目" if chinese else "Question"
    answer_prefix = "机密答案" if chinese else "SECRET ANSWER"
    statement = ("Long statement segment. " * 180) if long_text else "Compute the requested value."
    rows = []
    for index, difficulty in enumerate((40, 54, 58, 62, 66, 82), start=1):
        rows.append(
            {
                "template_id": "TEST",
                "title": f"{prefix} {index}",
                "statement": statement + f" Item {index}.",
                "answer_outline": f"{answer_prefix} {index}.",
                "difficulty_score": difficulty,
                "subject_hint": "高等数学" if chinese else "Mathematics",
                "topic_hint": "Series" if not chinese else "级数",
            }
        )
    spec = LocalPracticePaperSpec.create(
        subject="高等数学" if chinese else "Mathematics",
        target_difficulty=60,
        question_count=6,
        paper_date="2026-09-17",
        title="本地练习卷" if chinese else "Local Practice Paper",
    )
    return compose_local_practice_paper(spec, build_candidate_pool(rows))


def text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


class LocalPracticePDFRenderingTests(unittest.TestCase):
    def test_unsupported_integral_glyphs_get_portable_fallback(self) -> None:
        self.assertEqual(_paragraph_text("∭Ω f dV；∬D g dA"), "∫∫∫Ω f dV；∫∫D g dA")

    def test_question_and_answer_pdfs_are_separate_structurally_valid_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            tmp_path = Path(temp_directory)
            paper = make_paper()
            first_q = tmp_path / "first-questions.pdf"
            first_a = tmp_path / "first-answers.pdf"
            second_q = tmp_path / "second-questions.pdf"
            second_a = tmp_path / "second-answers.pdf"
            first = render_practice_pdfs(paper, first_q, first_a)
            second = render_practice_pdfs(paper, second_q, second_a)
            self.assertGreaterEqual(first["question_pages"], 1)
            self.assertGreaterEqual(first["answer_pages"], 1)
            self.assertEqual(second["question_pages"], first["question_pages"])
            question_text = text(first_q)
            answer_text = text(first_a)
            self.assertNotIn("SECRET ANSWER", question_text)
            self.assertIn("SECRET ANSWER 1", answer_text)
            self.assertIn("Question 1", question_text)
            self.assertIn("Question 1", answer_text)
            self.assertEqual(hashlib.sha256(first_q.read_bytes()).digest(), hashlib.sha256(second_q.read_bytes()).digest())
            self.assertEqual(hashlib.sha256(first_a.read_bytes()).digest(), hashlib.sha256(second_a.read_bytes()).digest())

    def test_long_statement_flows_across_pages_and_keeps_footer_page_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            tmp_path = Path(temp_directory)
            paper = make_paper(long_text=True)
            question_path = tmp_path / "long-questions.pdf"
            answer_path = tmp_path / "long-answers.pdf"
            result = render_practice_pdfs(paper, question_path, answer_path)
            self.assertGreater(result["question_pages"], 1)
            extracted = text(question_path)
            self.assertIn("Long statement segment", extracted)
            self.assertIn(paper.paper_id[:12], extracted)

    def test_non_ascii_requires_an_explicitly_available_font(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            tmp_path = Path(temp_directory)
            paper = make_paper(chinese=True)
            with self.assertRaisesRegex(LocalPracticePDFError, "中文字体"):
                render_practice_pdfs(
                    paper,
                    tmp_path / "q.pdf",
                    tmp_path / "a.pdf",
                    font_path=tmp_path / "missing-font.ttf",
                )

    def test_question_and_answer_paths_must_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            paper = make_paper()
            same = Path(temp_directory) / "same.pdf"
            with self.assertRaisesRegex(LocalPracticePDFError, "不同路径"):
                render_practice_pdfs(paper, same, same)
