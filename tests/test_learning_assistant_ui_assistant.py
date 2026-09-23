"""Headless UI tests for the rewritten learning-assistant page (query_page).

The engine class is patched in the ``study_app.core.learning_assistant_engine``
namespace (query_page imports it lazily inside ``make_engine``), and every
settings/provider/alerts source is patched so no test ever touches the real
``app_data`` store. Qt runs with QT_QPA_PLATFORM=offscreen.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QWidget,
)

import pytest

from study_app.core.dashboard import DashboardState
from study_app.core.learning_assistant_engine import LearningAssistantError
from study_app.core.learning_assistant_schema import (
    READONLY_ACTIONS,
    SCHEMA_VERSION,
    ActionProposal,
    new_action_id,
)

CHIP_READONLY = "只读问答"
CHIP_PENDING = "待确认"
CHIP_DONE = "已完成"
CHIP_FAILED = "失败"


def _state() -> DashboardState:
    today = date(2026, 9, 15)
    return DashboardState(
        start=today,
        today=today,
        benchmark=60,
        subjects=(),
        todos=(),
        memory_risks=(),
        bkt_alerts=(),
        raw_records=(),
    )


def _proposal(action_type: str) -> ActionProposal:
    return ActionProposal(
        schema_version=SCHEMA_VERSION,
        action_id=new_action_id(),
        action_type=action_type,
        mode="preview",
        requires_confirmation=action_type not in READONLY_ACTIONS,
    )


def _readonly_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        proposal=_proposal("answer_query"),
        confirmation_token=None,
        mastery_preview={
            "matched_topics": [],
            "subject_mastery": [],
            "no_evidence": True,
            "notes": [],
        },
        plan_impact={"affected": False, "note": "无"},
        warnings=(),
    )


def _homework_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        proposal=_proposal("submit_homework"),
        confirmation_token="stub-confirmation-token",
        mastery_preview={
            "matched_topics": [],
            "subject_mastery": [],
            "no_evidence": True,
            "notes": [],
        },
        plan_impact={"affected": False, "note": "无"},
        warnings=(),
    )


def _receipt(**overrides) -> SimpleNamespace:
    payload = {
        "status": "completed",
        "message": "已登记学习记录 #1；掌握度与预警已按证据重算。",
        "mastery_changes": (
            {"topic": "基础主题", "mastery_before": 0.2, "mastery_after": 0.25},
        ),
        "undo_token": {"kind": "record", "record_id": 1},
        "warnings": (),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _make_stub_engine(calls: dict, *, bundle=None, receipt=None, confirm_error=None):
    """Build an engine stub class; the page creates one instance per worker."""

    class StubLearningAssistantEngine:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def create_proposal(self, text, attachment_paths=(), state=None):
            calls["create"] = calls.get("create", 0) + 1
            return bundle if bundle is not None else _readonly_bundle()

        def confirm_and_execute(
            self,
            action_id,
            *,
            confirmation_token=None,
            execution_mode="user",
            edited_text=None,
        ):
            calls["confirm"] = calls.get("confirm", 0) + 1
            calls["confirmation_token"] = confirmation_token
            calls["execution_mode"] = execution_mode
            if confirm_error is not None:
                raise confirm_error
            return receipt if receipt is not None else _receipt()

        def reject_proposal(self, action_id, reason=""):
            calls["reject"] = calls.get("reject", 0) + 1
            return {"action_id": action_id, "state": "rejected"}

        def retry_proposal(self, action_id):
            calls["retry"] = calls.get("retry", 0) + 1
            return bundle if bundle is not None else _readonly_bundle()

        def undo_last(self):
            calls["undo"] = calls.get("undo", 0) + 1
            return _receipt(
                message="已撤销记录 #1：掌握度与预警已按基线重放。",
                undo_token=None,
                mastery_changes=(),
            )

        def last_undoable(self):
            return {
                "action_id": "stub",
                "undo_token": {"kind": "record", "record_id": 1},
            }

        def recompute_alerts_now(self, as_of=None):
            return _receipt(message="已按当前证据幂等重算知识预警。")

        def answer(self, question, state=None):
            return {"answer": ["本地回答"], "evidence": [], "source": []}

        def explain_alert(self, alert_id, state=None):
            return {"available": False, "reason": "stub"}

        def load_journal(self):
            return []

    return StubLearningAssistantEngine


@pytest.fixture()
def ui_env(monkeypatch):
    """Patch providers/policy/alerts sources; no database or file access."""
    from study_app.ai import natural_query, providers
    from study_app.core import learning_assistant_engine as engine_module
    from study_app.core import learning_assistant_policy as policy_module
    from study_app.data import database as database_module

    monkeypatch.setattr(
        providers, "load_llm_settings", lambda: SimpleNamespace(enabled=False)
    )
    monkeypatch.setattr(
        providers, "provider_status", lambda _settings=None: "LLM：本地模式"
    )
    monkeypatch.setattr(
        providers,
        "is_llm_feature_enabled",
        lambda _feature, _settings=None: False,
    )
    monkeypatch.setattr(
        natural_query,
        "answer_query_locally",
        lambda _question, _state, _records: {
            "answer": ["本地查询"], "evidence": [], "source": ["本地"],
        },
    )
    monkeypatch.setattr(
        policy_module,
        "load_privacy_settings",
        lambda db_path=None: {"advisor_mode": "mock", "allow_external_intent": False},
    )
    monkeypatch.setattr(
        policy_module,
        "load_auto_exec_policy",
        lambda db_path=None: {"recompute_alerts": False},
    )
    monkeypatch.setattr(policy_module, "can_auto_execute", lambda *_a, **_k: False)
    monkeypatch.setattr(
        database_module,
        "list_knowledge_alerts",
        lambda *args, **kwargs: (),
    )

    def install_engine(monkeypatch_target, calls, **stub_kwargs):
        stub = _make_stub_engine(calls, **stub_kwargs)
        monkeypatch_target.setattr(engine_module, "LearningAssistantEngine", stub)
        return stub

    return SimpleNamespace(install_engine=install_engine)


def _build_page(ui_env, monkeypatch, calls, **stub_kwargs):
    from study_app.ui import query_page as module

    ui_env.install_engine(monkeypatch, calls, **stub_kwargs)
    application = QApplication.instance() or QApplication([])
    page = module.query_page(_state())
    page.show()
    application.processEvents()
    return page


def _widgets(page):
    content = page.widget()
    return (
        content,
        content.findChild(QTextEdit, "QueryQuestionInput"),
        content.findChild(QPushButton, "PrimaryButton"),
        content.findChild(QLabel, "AssistantModeChip"),
        content.findChild(QWidget, "AssistantProposalCard"),
        content.findChild(QPushButton, "AssistantConfirmButton"),
        content.findChild(QLabel, "AssistantReceiptLabel"),
        content.findChild(QPushButton, "AssistantUndoButton"),
        content.findChild(QPushButton, "AssistantRetryButton"),
    )


def _pump_until(application, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Qt UI did not reach expected state")


def test_readonly_ask_shows_readonly_chip_and_never_confirms(monkeypatch, ui_env):
    application = QApplication.instance() or QApplication([])
    calls: dict = {}
    page = _build_page(ui_env, monkeypatch, calls)
    content, question, _send, chip, card, _confirm, _receipt_label, _undo, _retry = (
        _widgets(page)
    )
    try:
        question.setPlainText("今天应该复习什么？")
        content._query_handler()
        _pump_until(application, lambda: calls.get("create", 0) == 1)
        application.processEvents()
        assert chip.text() == CHIP_READONLY
        assert card.isHidden() is True  # no actionable proposal card
        assert calls.get("confirm", 0) == 0
    finally:
        page.close()
        application.processEvents()


def test_actionable_proposal_shows_pending_chip_card_and_confirm(monkeypatch, ui_env):
    application = QApplication.instance() or QApplication([])
    calls: dict = {}
    page = _build_page(ui_env, monkeypatch, calls, bundle=_homework_bundle())
    _content, question, send, chip, card, confirm, _receipt_label, _undo, _retry = (
        _widgets(page)
    )
    try:
        question.setPlainText("数学作业做完了，帮我登记。")
        send.click()
        _pump_until(application, lambda: not card.isHidden())
        assert chip.text() == CHIP_PENDING
        assert card.isHidden() is False
        assert confirm.isEnabled() is True
        assert calls.get("confirm", 0) == 0  # waiting for explicit confirmation
    finally:
        page.close()
        application.processEvents()


def test_confirm_executes_renders_mastery_receipt_and_allows_undo(monkeypatch, ui_env):
    application = QApplication.instance() or QApplication([])
    calls: dict = {}
    page = _build_page(
        ui_env, monkeypatch, calls, bundle=_homework_bundle(), receipt=_receipt()
    )
    _content, question, send, chip, card, confirm, receipt_label, undo, _retry = (
        _widgets(page)
    )
    original_question = QMessageBox.question

    def always_yes(*_args, **_kwargs):
        return QMessageBox.StandardButton.Yes

    try:
        question.setPlainText("数学作业做完了，帮我登记。")
        send.click()
        _pump_until(application, lambda: not card.isHidden())
        confirm.click()
        _pump_until(application, lambda: chip.text() == CHIP_DONE)
        assert calls.get("confirm", 0) == 1
        text = receipt_label.text()
        assert "已完成" in text
        assert "0.20" in text and "0.25" in text  # mastery_before → mastery_after
        assert "基础主题" in text
        assert "可撤销" in text
        assert undo.isEnabled() is True

        monkeypatch.setattr(QMessageBox, "question", always_yes)
        undo.click()
        _pump_until(application, lambda: calls.get("undo", 0) == 1)
        _pump_until(application, lambda: "已撤销记录 #1" in receipt_label.text())
        assert chip.text() == CHIP_DONE
    finally:
        monkeypatch.setattr(QMessageBox, "question", original_question)
        page.close()
        application.processEvents()


def test_failed_confirm_shows_failure_enables_retry_and_keeps_input(monkeypatch, ui_env):
    application = QApplication.instance() or QApplication([])
    calls: dict = {}
    page = _build_page(
        ui_env,
        monkeypatch,
        calls,
        bundle=_homework_bundle(),
        confirm_error=LearningAssistantError("模拟本地提交失败"),
    )
    _content, question, send, chip, card, confirm, receipt_label, _undo, retry = (
        _widgets(page)
    )
    try:
        question.setPlainText("英语作业全对，帮我登记。")
        send.click()
        _pump_until(application, lambda: not card.isHidden())
        assert chip.text() == CHIP_PENDING
        confirm.click()
        _pump_until(application, lambda: chip.text() == CHIP_FAILED)
        assert calls.get("confirm", 0) == 1
        assert retry.isEnabled() is True
        assert confirm.isEnabled() is True  # user may adjust and retry
        assert "失败" in receipt_label.text()
        assert "模拟本地提交失败" in receipt_label.text()
        assert question.toPlainText() == "英语作业全对，帮我登记。"
    finally:
        page.close()
        application.processEvents()


def test_duplicate_send_starts_only_one_proposal_job(monkeypatch, ui_env):
    application = QApplication.instance() or QApplication([])
    from study_app.ai import natural_query, providers

    calls: dict = {}
    release = threading.Event()
    llm_started = threading.Event()

    def slow_llm(*_args, **_kwargs):
        llm_started.set()
        release.wait(2)
        return {"answer": ["LLM 结果"], "evidence": [], "source": ["LLM"]}

    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _f, _s=None: True)
    monkeypatch.setattr(natural_query, "answer_query_with_llm", slow_llm)
    page = _build_page(ui_env, monkeypatch, calls)
    _content, question, send, chip, _card, _confirm, _receipt_label, _undo, _retry = (
        _widgets(page)
    )
    body = page.findChild(QLabel, "ListTitle")
    try:
        question.setPlainText("今天应该复习什么？")
        send.click()
        assert llm_started.wait(2)
        send.click()  # identical input while the first request is in flight
        application.processEvents()
        assert calls.get("create", 0) == 1
        assert question.isEnabled() is True
        release.set()
        _pump_until(application, lambda: "LLM 结果" in body.text())
        assert chip.text() == CHIP_READONLY
    finally:
        release.set()
        page.close()
        application.processEvents()


def test_slow_llm_times_out_keeps_input_enabled_and_falls_back_local(monkeypatch, ui_env):
    application = QApplication.instance() or QApplication([])
    from study_app.ai import natural_query, providers
    from study_app.ui import query_page as module

    calls: dict = {}
    release = threading.Event()

    def very_slow_llm(*_args, **_kwargs):
        release.wait(2)  # far beyond the patched query timeout
        return {"answer": ["迟到结果"], "evidence": [], "source": ["LLM"]}

    monkeypatch.setattr(providers, "is_llm_feature_enabled", lambda _f, _s=None: True)
    monkeypatch.setattr(natural_query, "answer_query_with_llm", very_slow_llm)
    monkeypatch.setattr(module, "QUERY_TASK_TIMEOUT_SECONDS", 0.05)
    page = _build_page(ui_env, monkeypatch, calls)
    _content, question, send, chip, _card, _confirm, _receipt_label, _undo, _retry = (
        _widgets(page)
    )
    body = page.findChild(QLabel, "ListTitle")
    try:
        question.setPlainText("今天应该复习什么？")
        send.click()
        _pump_until(application, lambda: "本地查询" in body.text())
        assert question.isEnabled() is True  # UI never blocked while waiting
        _pump_until(application, lambda: "LLM 查询超时" in body.text())
        assert "本地查询" in body.text()  # local fallback preserved
        assert "迟到结果" not in body.text()
        assert chip.text() == CHIP_READONLY
    finally:
        release.set()
        page.close()
        application.processEvents()


def test_alert_summary_reports_missing_structure_and_counts(monkeypatch, ui_env):
    from study_app.data import database as database_module
    from study_app.ui import query_page as module

    application = QApplication.instance() or QApplication([])

    def raise_missing(*_args, **_kwargs):
        raise database_module.DatabaseNotInitializedError("知识预警表尚未安装")

    monkeypatch.setattr(database_module, "list_knowledge_alerts", raise_missing)
    page = module.query_page(_state())
    page.show()
    application.processEvents()
    try:
        summary = page.findChild(QLabel, "AssistantAlertSummary")
        jump_button = page.findChild(QPushButton, "AssistantAlertsJumpButton")
        assert "结构未安装" in summary.text()
        assert jump_button.isHidden()

        rows = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        monkeypatch.setattr(
            database_module,
            "list_knowledge_alerts",
            lambda *args, **kwargs: list(rows),
        )
        page._set_dashboard_state(_state())
        application.processEvents()
        assert "知识预警" in summary.text()
        assert "2" in summary.text()
    finally:
        page.close()
        application.processEvents()
