from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

from study_app.core.local_practice_composer import ComposedPracticePaper


class LocalPracticePDFError(RuntimeError):
    """Raised when a local practice PDF cannot be rendered or verified."""


_CJK_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsunb.ttf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
)


def _contains_non_ascii(values: Iterable[str]) -> bool:
    return any(any(ord(character) > 127 for character in value) for value in values)


def _resolve_font(paper: ComposedPracticePaper, font_path: Path | str | None) -> tuple[str, str]:
    texts = [paper.spec.title, paper.spec.subject]
    for question in paper.questions:
        texts.extend((question.title, question.statement, question.answer))
    needs_cjk = _contains_non_ascii(texts)
    explicit = Path(font_path) if font_path is not None else None
    if explicit is not None:
        candidates = (explicit,)
    else:
        configured = os.environ.get("LOCAL_PRACTICE_FONT", "").strip()
        candidates = ((Path(configured),) if configured else ()) + _CJK_FONT_CANDIDATES
    selected = next((path for path in candidates if path.is_file()), None)
    if selected is None:
        if needs_cjk:
            raise LocalPracticePDFError(
                "未找到可嵌入的中文字体；请安装黑体／宋体，或设置 LOCAL_PRACTICE_FONT。"
            )
        return "Helvetica", "Helvetica-Bold"
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as error:
        raise LocalPracticePDFError("缺少 ReportLab；请按 requirements-desktop.txt 安装依赖。") from error
    digest = hashlib.sha256(selected.read_bytes()).hexdigest()[:12]
    regular_name = f"LocalPractice-{digest}"
    if regular_name not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont(regular_name, str(selected)))
        except Exception as error:
            raise LocalPracticePDFError(f"无法加载练习卷字体：{selected.name}") from error
    return regular_name, regular_name


def _paragraph_text(value: str) -> str:
    # Common Windows CJK fonts do not consistently contain the Unicode
    # double/triple-integral glyphs.  Use equivalent repeated integral signs
    # so printed worksheets never contain tofu boxes.
    portable = str(value or "").replace("∭", "∫∫∫").replace("∬", "∫∫")
    return escape(portable).replace("\n", "<br/>")


def _styles(font_name: str, bold_font_name: str):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm

    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "LocalPracticeTitle",
            parent=sample["Title"],
            fontName=bold_font_name,
            fontSize=20,
            leading=27,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17233D"),
            spaceAfter=8 * mm,
            wordWrap="CJK",
        ),
        "meta": ParagraphStyle(
            "LocalPracticeMeta",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=9,
            leading=14,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#4A5568"),
            spaceAfter=3 * mm,
            wordWrap="CJK",
        ),
        "question": ParagraphStyle(
            "LocalPracticeQuestion",
            parent=sample["Heading2"],
            fontName=bold_font_name,
            fontSize=12,
            leading=18,
            textColor=colors.HexColor("#163D77"),
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "LocalPracticeBody",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=10.5,
            leading=17,
            textColor=colors.HexColor("#202938"),
            spaceAfter=4 * mm,
            wordWrap="CJK",
            splitLongWords=True,
        ),
        "answer": ParagraphStyle(
            "LocalPracticeAnswer",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=10,
            leading=16,
            leftIndent=4 * mm,
            borderColor=colors.HexColor("#D8E2F0"),
            borderWidth=0.6,
            borderPadding=6,
            backColor=colors.HexColor("#F7FAFC"),
            spaceAfter=5 * mm,
            wordWrap="CJK",
            splitLongWords=True,
        ),
    }


def _footer(canvas, document, paper: ComposedPracticePaper, font_name: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D8E2F0"))
    canvas.setLineWidth(0.4)
    canvas.line(18 * mm, 15 * mm, 192 * mm, 15 * mm)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.setFont(font_name, 8)
    canvas.drawString(18 * mm, 10 * mm, f"{paper.paper_id[:12]}")
    canvas.drawRightString(192 * mm, 10 * mm, f"{canvas.getPageNumber()}")
    canvas.restoreState()


def _render_pdf(
    paper: ComposedPracticePaper,
    output_path: Path,
    *,
    answers: bool,
    font_path: Path | str | None,
) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as canvas_module
        from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as error:
        raise LocalPracticePDFError("缺少 ReportLab；请按 requirements-desktop.txt 安装依赖。") from error

    font_name, bold_font_name = _resolve_font(paper, font_path)
    styles = _styles(font_name, bold_font_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    title_suffix = "答案与解析" if answers else "题目卷"

    class InvariantCanvas(canvas_module.Canvas):
        def __init__(self, *args, **kwargs):
            kwargs["invariant"] = 1
            super().__init__(*args, **kwargs)
            self.setTitle(f"{paper.spec.title} - {title_suffix}")
            self.setAuthor("Personal Learning OS")
            self.setCreator("Personal Learning OS local practice renderer")
            self.setSubject(paper.paper_id)

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title=f"{paper.spec.title} - {title_suffix}",
        author="Personal Learning OS",
        subject=paper.paper_id,
        pageCompression=1,
    )
    story = [Paragraph(_paragraph_text(f"{paper.spec.title} - {title_suffix}"), styles["title"])]
    distribution = paper.actual_distribution
    story.append(
        Paragraph(
            _paragraph_text(
                f"学科：{paper.spec.subject}　日期：{paper.spec.paper_date}　题量：{len(paper.questions)}\n"
                f"目标难度：{paper.spec.target_difficulty:g}/100　"
                f"实际分布：基础 {distribution['foundation']} / 核心 {distribution['core']} / 挑战 {distribution['challenge']}"
            ),
            styles["meta"],
        )
    )
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#9FB3CF")))
    story.append(Spacer(1, 3 * mm))
    for index, question in enumerate(paper.questions, start=1):
        story.append(
            Paragraph(
                _paragraph_text(f"{index}. {question.title}　[{question.difficulty:g}/100]"),
                styles["question"],
            )
        )
        if answers:
            story.append(Paragraph(_paragraph_text(question.answer), styles["answer"]))
        else:
            story.append(Paragraph(_paragraph_text(question.statement), styles["body"]))
            story.append(Spacer(1, 24 * mm))
        if index != len(paper.questions):
            story.append(HRFlowable(width="100%", thickness=0.35, color=colors.HexColor("#E1E8F0")))
    document.build(
        story,
        onFirstPage=lambda canvas, doc: _footer(canvas, doc, paper, font_name),
        onLaterPages=lambda canvas, doc: _footer(canvas, doc, paper, font_name),
        canvasmaker=InvariantCanvas,
    )


def validate_pdf_structure(path: Path | str) -> int:
    pdf_path = Path(path)
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise LocalPracticePDFError("缺少 pypdf；请按 requirements-desktop.txt 安装依赖。") from error
    try:
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
        if page_count < 1:
            raise LocalPracticePDFError(f"PDF 没有页面：{pdf_path.name}")
        _ = reader.metadata
        for page in reader.pages:
            page.extract_text()
    except LocalPracticePDFError:
        raise
    except Exception as error:
        raise LocalPracticePDFError(f"PDF 结构校验失败：{pdf_path.name}") from error
    return page_count


def render_practice_pdfs(
    paper: ComposedPracticePaper,
    question_path: Path | str,
    answer_path: Path | str,
    *,
    font_path: Path | str | None = None,
) -> dict[str, Any]:
    question_pdf = Path(question_path)
    answer_pdf = Path(answer_path)
    if question_pdf.resolve() == answer_pdf.resolve():
        raise LocalPracticePDFError("题目 PDF 与答案 PDF 必须使用不同路径。")
    _render_pdf(paper, question_pdf, answers=False, font_path=font_path)
    _render_pdf(paper, answer_pdf, answers=True, font_path=font_path)
    return {
        "question_pages": validate_pdf_structure(question_pdf),
        "answer_pages": validate_pdf_structure(answer_pdf),
        "question_pdf": str(question_pdf),
        "answer_pdf": str(answer_pdf),
    }
