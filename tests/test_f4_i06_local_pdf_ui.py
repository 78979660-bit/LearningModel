from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from study_app.ui.study_plan_page import (
    generate_local_practice_for_plan_line,
    local_practice_success_message,
    plan_section_card,
)


def app():
    return QApplication.instance() or QApplication([])


def test_non_oj_card_exposes_local_button_and_preserves_source_text() -> None:
    app()
    callback = Mock()
    line = "当天作业：参考难度 60/100；题库模板 CALC-SERIES；题量 4 题。"
    card, _checkboxes = plan_section_card(
        "今日作业",
        [line],
        interactive=True,
        on_generate_local_pdf=callback,
        on_send_chatgpt=Mock(),
    )
    buttons = {button.text(): button for button in card.findChildren(QPushButton)}
    assert "本地生成练习卷" in buttons
    assert "让 ChatGPT 生成 PDF" in buttons
    buttons["本地生成练习卷"].click()
    callback.assert_called_once_with(line)
    card.deleteLater()


def test_oj_card_never_exposes_local_or_chatgpt_pdf_button() -> None:
    app()
    card, _checkboxes = plan_section_card(
        "今日作业",
        ["当天作业：题库模板 CS-OJ-PRACTICE；LeetCode OJ 原题；题量 2 题。"],
        interactive=True,
        on_generate_local_pdf=Mock(),
        on_send_chatgpt=Mock(),
    )
    labels = {button.text() for button in card.findChildren(QPushButton)}
    assert "本地生成练习卷" not in labels
    assert "让 ChatGPT 生成 PDF" not in labels
    card.deleteLater()


def test_plan_line_adapter_passes_explicit_subject_and_never_calls_chatgpt() -> None:
    fake_spec = object()
    fake_result = object()
    with (
        patch("study_app.core.local_practice_service.spec_from_homework", return_value=fake_spec) as build,
        patch("study_app.core.local_practice_service.generate_local_practice_paper", return_value=fake_result) as generate,
        patch("study_app.integrations.chatgpt_desktop_bridge.generate_pdf_with_chatgpt") as chatgpt,
    ):
        result = generate_local_practice_for_plan_line("本地作业", "高等数学")
    assert result is fake_result
    build.assert_called_once_with("本地作业", subject="高等数学")
    generate.assert_called_once_with(fake_spec)
    chatgpt.assert_not_called()


def test_subject_and_oj_guards_fail_before_service_import_work() -> None:
    with patch("study_app.core.local_practice_service.generate_local_practice_paper") as generate:
        try:
            generate_local_practice_for_plan_line("普通作业", "")
        except ValueError as error:
            assert "选择一个具体学科" in str(error)
        else:
            raise AssertionError("missing subject was accepted")
        try:
            generate_local_practice_for_plan_line("LeetCode OJ 原题", "计算机科学")
        except ValueError as error:
            assert "不自动转换" in str(error)
        else:
            raise AssertionError("OJ homework was accepted")
    generate.assert_not_called()


def test_success_message_lists_all_three_outputs(tmp_path: Path) -> None:
    result = SimpleNamespace(
        question_pdf=tmp_path / "questions.pdf",
        answer_pdf=tmp_path / "answers.pdf",
        manifest_json=tmp_path / "manifest.json",
    )
    message = local_practice_success_message(result)
    assert "不依赖 ChatGPT 或网络" in message
    assert "questions.pdf" in message and "answers.pdf" in message and "manifest.json" in message
