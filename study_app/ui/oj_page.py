from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from study_app.core.oj_history import OJProblemHistory, build_oj_history
from study_app.core.oj_identity import OJProblem
from study_app.core.oj_topics import OJTopicMapping
from study_app.data.database import (
    DEFAULT_DB_PATH,
    DatabaseNotInitializedError,
    list_oj_attempts,
    list_oj_problem_topics,
    list_oj_problems,
    list_topic_identities,
    append_oj_attempt,
    register_oj_problem,
    replace_oj_problem_topics,
)


RESULT_LABELS = {
    "accepted": "通过",
    "partial": "部分通过",
    "wrong": "答案错误",
    "runtime_error": "运行错误",
    "time_limit": "超时",
    "memory_limit": "超内存",
    "compile_error": "编译错误",
    "abandoned": "放弃",
}


@dataclass(frozen=True)
class OJPageEntry:
    problem: OJProblem
    mappings: tuple[OJTopicMapping, ...]
    history: OJProblemHistory


def load_oj_page_entries(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple[OJPageEntry, ...]:
    return tuple(
        OJPageEntry(
            problem=problem,
            mappings=list_oj_problem_topics(problem.problem_key, db_path),
            history=build_oj_history(list_oj_attempts(problem.problem_key, db_path)),
        )
        for problem in list_oj_problems(db_path)
    )


def _result(value: str | None) -> str:
    return "未记录" if value is None else RESULT_LABELS[value]


def _summary_text(history: OJProblemHistory) -> str:
    item = history.summary
    independent = "是" if item.ever_independent_accepted else "否"
    first_ac = "未通过" if item.attempts_to_first_accepted is None else str(item.attempts_to_first_accepted)
    improvement = (
        "不足两次"
        if item.recent_duration_improvement_seconds is None
        else f"{item.recent_duration_improvement_seconds:+d} 秒（正数=变快）"
    )
    return (
        f"尝试 {item.attempt_count} 次｜首次 {_result(item.first_result)}｜"
        f"最新 {_result(item.latest_result)}｜最佳 {_result(item.best_result)}｜"
        f"曾独立通过 {independent}｜首次通过序号 {first_ac}｜"
        f"最近耗时改善 {improvement}"
    )


def _history_text(history: OJProblemHistory) -> str:
    if not history.attempts:
        return "尚无提交。"
    return "\n".join(
        (
            f"#{item.attempt_number}{' 复做' if item.is_retry else ' 首次'}｜"
            f"{item.attempt.attempted_at}｜{_result(item.attempt.result)}｜"
            f"{item.attempt.duration_seconds} 秒｜{item.attempt.independence}｜"
            f"提示 {item.attempt.hint_level}｜错误 {item.attempt.error_type}｜"
            f"{item.attempt.notes or '无备注'}"
        )
        for item in history.attempts
    )


def oj_page(
    *,
    data_loader: Callable[[], tuple[OJPageEntry, ...]] | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    enable_problem_form: bool = False,
    topic_loader=None,
    problem_writer=None,
    mapping_writer=None,
    enable_attempt_form: bool = False,
    attempt_writer=None,
):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QComboBox,
        QGridLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )

    loader = data_loader or (lambda: load_oj_page_entries(db_path))
    load_topics = topic_loader or (lambda: list_topic_identities(db_path))
    write_problem = problem_writer or register_oj_problem
    write_mappings = mapping_writer or replace_oj_problem_topics
    write_attempt = attempt_writer or append_oj_attempt
    entries: list[OJPageEntry] = []

    scroll = QScrollArea()
    scroll.setObjectName("OJPage")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(24, 22, 24, 24)
    layout.setSpacing(14)

    heading = QLabel("OJ 学习闭环")
    heading.setObjectName("HeroTitle")
    layout.addWidget(heading)
    hint = QLabel("题目身份、知识点映射与提交历史均从本地 SQLite 回读，不连接外部 OJ。")
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    diagnostic = QLabel()
    diagnostic.setObjectName("OJDiagnostic")
    diagnostic.setWordWrap(True)
    diagnostic.hide()
    layout.addWidget(diagnostic)
    count = QLabel()
    count.setObjectName("OJProblemCount")
    layout.addWidget(count)
    problem_list = QListWidget()
    problem_list.setObjectName("OJProblemList")
    problem_list.setMinimumHeight(220)
    problem_list.setAlternatingRowColors(True)
    layout.addWidget(problem_list)

    detail = QWidget()
    detail.setObjectName("OJDetail")
    grid = QGridLayout(detail)
    fields = {}
    specs = (
        ("identity", "稳定身份", "OJIdentity"),
        ("title", "题目", "OJTitle"),
        ("topics", "显式知识点", "OJTopics"),
        ("summary", "复做摘要", "OJRetrySummary"),
        ("history", "提交历史", "OJAttemptHistory"),
    )
    for row, (key, caption, object_name) in enumerate(specs):
        label = QLabel(caption)
        label.setObjectName("Muted")
        value = QLabel("—")
        value.setObjectName(object_name)
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(value, row, 1)
        fields[key] = value
    grid.setColumnStretch(1, 1)
    layout.addWidget(detail)

    problem_form = None
    if enable_problem_form:
        problem_form = QWidget()
        problem_form.setObjectName("OJProblemForm")
        form = QGridLayout(problem_form)
        form_fields = (
            ("source", "来源代码", "OJSourceInput", "例如 leetcode / luogu / local"),
            ("external", "来源题号", "OJExternalKeyInput", "例如 1 / P1000 / dp-001"),
            ("title", "题目标题", "OJTitleInput", "仅用于展示，不参与稳定身份"),
            ("url", "来源 URL", "OJURLInput", "可空，本地不访问该 URL"),
            ("topics", "topic_key", "OJTopicKeysInput", "逗号分隔；可空，不会自动猜测"),
        )
        inputs = {}
        for row, (key, caption, object_name, placeholder) in enumerate(form_fields):
            label = QLabel(caption)
            label.setObjectName("Muted")
            value = QLineEdit()
            value.setObjectName(object_name)
            value.setPlaceholderText(placeholder)
            form.addWidget(label, row, 0)
            form.addWidget(value, row, 1)
            inputs[key] = value
        available = QLabel()
        available.setObjectName("OJAvailableTopics")
        available.setWordWrap(True)
        form.addWidget(available, len(form_fields), 0, 1, 2)
        action_status = QLabel()
        action_status.setObjectName("OJProblemActionStatus")
        action_status.setWordWrap(True)
        form.addWidget(action_status, len(form_fields) + 1, 0, 1, 2)
        save_problem = QPushButton("注册题目并保存显式映射")
        save_problem.setObjectName("OJSaveProblem")
        form.addWidget(save_problem, len(form_fields) + 2, 1)
        problem_form._inputs = inputs
        problem_form._available = available
        problem_form._status = action_status
        problem_form._save = save_problem
        layout.addWidget(problem_form)

    attempt_form = None
    if enable_attempt_form:
        from study_app.core.oj_attempts import (
            ERROR_TYPES,
            HINT_LEVELS,
            INDEPENDENCE_LEVELS,
            RESULTS,
        )

        attempt_form = QWidget()
        attempt_form.setObjectName("OJAttemptForm")
        form = QGridLayout(attempt_form)
        attempted_at = QLineEdit()
        attempted_at.setObjectName("OJAttemptedAtInput")
        attempted_at.setPlaceholderText("带时区 ISO-8601，例如 2026-09-17T20:00:00+08:00")
        duration = QLineEdit()
        duration.setObjectName("OJDurationInput")
        duration.setPlaceholderText("正整数秒")
        result = QComboBox()
        result.setObjectName("OJResultInput")
        for value in RESULTS:
            result.addItem(RESULT_LABELS[value], value)
        independence = QComboBox()
        independence.setObjectName("OJIndependenceInput")
        for value in INDEPENDENCE_LEVELS:
            independence.addItem(value, value)
        hint_level = QComboBox()
        hint_level.setObjectName("OJHintLevelInput")
        for value in HINT_LEVELS:
            hint_level.addItem(value, value)
        error_type = QComboBox()
        error_type.setObjectName("OJErrorTypeInput")
        for value in ERROR_TYPES:
            error_type.addItem(value, value)
        notes = QLineEdit()
        notes.setObjectName("OJAttemptNotesInput")
        source_attempt_key = QLineEdit()
        source_attempt_key.setObjectName("OJSourceAttemptKeyInput")
        source_attempt_key.setPlaceholderText("可空；导入或重试时用于幂等")
        attempt_inputs = {
            "attempted_at": attempted_at,
            "duration": duration,
            "result": result,
            "independence": independence,
            "hint_level": hint_level,
            "error_type": error_type,
            "notes": notes,
            "source_attempt_key": source_attempt_key,
        }
        specs = (
            ("提交时间", attempted_at),
            ("结果", result),
            ("耗时（秒）", duration),
            ("独立性", independence),
            ("提示级别", hint_level),
            ("错误类型", error_type),
            ("备注", notes),
            ("幂等键", source_attempt_key),
        )
        for row, (caption, widget) in enumerate(specs):
            label = QLabel(caption)
            label.setObjectName("Muted")
            form.addWidget(label, row, 0)
            form.addWidget(widget, row, 1)
        attempt_status = QLabel()
        attempt_status.setObjectName("OJAttemptActionStatus")
        attempt_status.setWordWrap(True)
        form.addWidget(attempt_status, len(specs), 0, 1, 2)
        save_attempt = QPushButton("追加提交")
        save_attempt.setObjectName("OJSaveAttempt")
        form.addWidget(save_attempt, len(specs) + 1, 1)
        attempt_form._inputs = attempt_inputs
        attempt_form._status = attempt_status
        attempt_form._save = save_attempt
        layout.addWidget(attempt_form)
    layout.addStretch()
    scroll.setWidget(content)

    def selected_key() -> str | None:
        item = problem_list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def entry_for(key: str | None) -> OJPageEntry | None:
        return next((item for item in entries if item.problem.problem_key == key), None)

    def render_detail() -> None:
        entry = entry_for(selected_key())
        if entry is None:
            for value in fields.values():
                value.setText("—")
            return
        problem = entry.problem
        fields["identity"].setText(
            f"{problem.source_key} / {problem.external_problem_key}\n{problem.problem_key}"
        )
        fields["title"].setText(
            problem.title + (f"\n{problem.source_url}" if problem.source_url else "")
        )
        fields["topics"].setText(
            "\n".join(
                f"{item.topic_key}｜{item.mapping_source}｜{item.note or '无备注'}"
                for item in entry.mappings
            )
            or "未建立显式知识点映射"
        )
        fields["summary"].setText(_summary_text(entry.history))
        fields["history"].setText(_history_text(entry.history))

    def render_list(preferred_key: str | None = None) -> None:
        problem_list.clear()
        for entry in entries:
            problem = entry.problem
            row = QListWidgetItem(
                f"{problem.source_key} / {problem.external_problem_key}｜{problem.title}\n"
                f"尝试 {entry.history.summary.attempt_count} 次｜"
                f"最新 {_result(entry.history.summary.latest_result)}"
            )
            row.setData(Qt.ItemDataRole.UserRole, problem.problem_key)
            problem_list.addItem(row)
        count.setText(f"共 {len(entries)} 道稳定 OJ 题目")
        target = 0 if problem_list.count() else -1
        if preferred_key is not None:
            for index in range(problem_list.count()):
                if problem_list.item(index).data(Qt.ItemDataRole.UserRole) == preferred_key:
                    target = index
                    break
        if target >= 0:
            problem_list.setCurrentRow(target)
        else:
            render_detail()

    def load_entries() -> None:
        entries.clear()
        diagnostic.hide()
        diagnostic.setText("")
        try:
            loaded = tuple(loader())
            if any(not isinstance(item, OJPageEntry) for item in loaded):
                raise ValueError("OJ 页数据必须是 OJPageEntry")
            entries.extend(loaded)
        except DatabaseNotInitializedError as error:
            diagnostic.setText(
                "F3 结构尚未安装；当前仅显示诊断，不会自动修改现用数据库。"
                f"\n{error}"
            )
            diagnostic.show()
        except Exception as error:
            diagnostic.setText(f"OJ 只读加载失败：{error}")
            diagnostic.show()

    def refresh_oj(preferred_key: str | None = None) -> None:
        preferred = preferred_key or selected_key()
        page_scroll = scroll.verticalScrollBar().value()
        list_scroll = problem_list.verticalScrollBar().value()
        load_entries()
        render_list(preferred)

        def restore() -> None:
            scroll.verticalScrollBar().setValue(
                min(page_scroll, scroll.verticalScrollBar().maximum())
            )
            problem_list.verticalScrollBar().setValue(
                min(list_scroll, problem_list.verticalScrollBar().maximum())
            )

        restore()
        QTimer.singleShot(0, restore)

    if problem_form is not None:
        inputs = problem_form._inputs
        available = problem_form._available
        action_status = problem_form._status
        try:
            topic_rows = tuple(load_topics())
            available_keys = {item.topic_key for item in topic_rows}
            available.setText(
                "可用 topic_key：\n"
                + (
                    "\n".join(
                        f"{item.topic_key}｜{item.subject_name} / {item.module_name} / {item.topic_name}"
                        for item in topic_rows
                    )
                    or "无（可先只注册题目）"
                )
            )
        except Exception as error:
            available_keys = set()
            available.setText(f"知识点身份不可用：{error}")

        def save_problem_action() -> None:
            from study_app.core.oj_topics import normalize_topic_keys

            raw_topic_keys = [
                item.strip()
                for item in inputs["topics"].text().replace("\n", ",").split(",")
                if item.strip()
            ]
            try:
                topic_keys = normalize_topic_keys(raw_topic_keys)
                missing = sorted(set(topic_keys) - available_keys)
                if missing:
                    raise ValueError("不存在的 topic_key：" + ", ".join(missing))
                problem = write_problem(
                    inputs["source"].text(),
                    inputs["external"].text(),
                    inputs["title"].text(),
                    inputs["url"].text(),
                    db_path,
                )
                write_mappings(
                    problem.problem_key,
                    topic_keys,
                    mapping_source="manual",
                    db_path=db_path,
                )
            except Exception as error:
                action_status.setText(f"保存失败：{error}")
                return
            action_status.setText(f"已保存：{problem.problem_key}")
            for value in inputs.values():
                value.clear()
            refresh_oj(problem.problem_key)

        problem_form._save.clicked.connect(save_problem_action)

    if attempt_form is not None:
        attempt_inputs = attempt_form._inputs
        attempt_status = attempt_form._status

        def align_error_type() -> None:
            result_value = attempt_inputs["result"].currentData()
            error_value = attempt_inputs["error_type"].currentData()
            target = None
            if result_value == "accepted" and error_value != "none":
                target = "none"
            elif result_value != "accepted" and error_value == "none":
                target = "unknown"
            if target is not None:
                attempt_inputs["error_type"].setCurrentIndex(
                    attempt_inputs["error_type"].findData(target)
                )

        def save_attempt_action() -> None:
            problem_key = selected_key()
            try:
                if problem_key is None:
                    raise ValueError("请先选择一道 OJ 题目")
                raw_duration = attempt_inputs["duration"].text().strip()
                if not raw_duration or not raw_duration.isascii() or not raw_duration.isdigit():
                    raise ValueError("duration_seconds 必须是正整数")
                saved = write_attempt(
                    problem_key=problem_key,
                    attempted_at=attempt_inputs["attempted_at"].text(),
                    result=attempt_inputs["result"].currentData(),
                    duration_seconds=int(raw_duration),
                    independence=attempt_inputs["independence"].currentData(),
                    hint_level=attempt_inputs["hint_level"].currentData(),
                    error_type=attempt_inputs["error_type"].currentData(),
                    notes=attempt_inputs["notes"].text(),
                    source_attempt_key=(
                        attempt_inputs["source_attempt_key"].text().strip() or None
                    ),
                    db_path=db_path,
                )
            except Exception as error:
                attempt_status.setText(f"保存失败：{error}")
                return
            attempt_status.setText(f"已追加提交 #{saved.attempt_id}")
            for key in ("attempted_at", "duration", "notes", "source_attempt_key"):
                attempt_inputs[key].clear()
            refresh_oj(problem_key)

        attempt_inputs["result"].currentIndexChanged.connect(
            lambda _index: align_error_type()
        )
        align_error_type()
        attempt_form._save.clicked.connect(save_attempt_action)

    problem_list.currentItemChanged.connect(lambda _current, _previous: render_detail())
    scroll._refresh_oj = refresh_oj
    load_entries()
    render_list()
    return scroll
