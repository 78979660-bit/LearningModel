from __future__ import annotations

import hashlib
import json

from study_app.core.async_tasks import AsyncTaskService, TaskStatus
from study_app.core.dashboard import DashboardState
from study_app.paths import MODEL_PATH
from study_app.ui.activity_view import record_subject_names


ACTIVITY_OPTIONS = [
    ("复习", "review"),
    ("做题", "exercise"),
    ("课堂学习", "class"),
    ("复习并练习", "review_exercise"),
    ("自测", "self_test"),
]

SOURCE_OPTIONS = [
    ("课外", "outside_class"),
    ("课堂", "classroom"),
    ("自学", "self_study"),
]


RECORD_RECOGNITION_TASK_TIMEOUT_SECONDS = 30.0

class AddRecordDialog:
    def __new__(cls, owner=None, on_saved=None, subject_names: tuple[str, ...] = ()):
        from datetime import date
        from pathlib import Path
        import re

        from PySide6.QtCore import QDate, QTimer, Qt
        from PySide6.QtWidgets import (
            QComboBox,
            QDateEdit,
            QFileDialog,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QMessageBox,
            QPushButton,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )

        from study_app.data.database import list_module_names
        from study_app.data.file_locator import locate_files
        from study_app.data.text_extractor import combined_extracted_text, extract_text_from_files
        from study_app.ai.attachment_parser import parse_problem_blocks_from_text

        class NoWheelComboBox(QComboBox):
            def wheelEvent(self, event):
                event.ignore()

        class _AddRecordDialog(QWidget):
            def __init__(self, parent):
                super().__init__(parent)
                self.attachments = []
                self.problems = []
                self.extracted_items = []
                self.extracted_context = ""
                self.attachment_problem_blocks = []
                self.on_saved = on_saved

                layout = QVBoxLayout(self)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(16)

                hero = QWidget()
                hero.setObjectName("RecordHero")
                hero_layout = QVBoxLayout(hero)
                hero_layout.setContentsMargins(18, 16, 18, 16)
                hero_layout.setSpacing(6)
                eyebrow = QLabel("RECORD INTAKE")
                eyebrow.setObjectName("RecordEyebrow")
                hero_layout.addWidget(eyebrow)
                heading = QLabel("新增学习记录")
                heading.setObjectName("HeroTitle")
                hero_layout.addWidget(heading)
                hint = QLabel("记录事实即可：学了什么、做题情况、错因和相关文件。题目列表、正确率与难度会由系统识别。")
                hint.setObjectName("RecordHint")
                hint.setWordWrap(True)
                hero_layout.addWidget(hint)
                layout.addWidget(hero)

                content = QVBoxLayout()
                content.setSpacing(14)
                layout.addLayout(content)

                basic_card, basic_form = self.make_card("基础信息", "选择学科、主题和材料来源，附件只保存路径。")
                content.addWidget(basic_card)

                work_card, work_form = self.make_card("学习与做题情况", "写自然语言描述即可，系统会尝试识别题目和作答结果。")
                content.addWidget(work_card)

                self.date_input = QDateEdit()
                self.date_input.setCalendarPopup(True)
                today = date.today()
                self.date_input.setDate(QDate.fromString(today.isoformat(), "yyyy-MM-dd"))

                self.subject_input = NoWheelComboBox()
                self.subject_input.setEditable(False)
                self.subject_input.addItems(subject_names)
                self.subject_input.currentTextChanged.connect(self.refresh_modules)

                self.module_input = NoWheelComboBox()
                self.module_input.setEditable(True)
                self.refresh_modules(self.subject_input.currentText())

                self.topic_input = QLineEdit()
                self.activity_input = NoWheelComboBox()
                for label, value in ACTIVITY_OPTIONS:
                    self.activity_input.addItem(label, value)
                self.source_input = NoWheelComboBox()
                for label, value in SOURCE_OPTIONS:
                    self.source_input.addItem(label, value)
                self.score_input = QLineEdit()
                self.score_input.setPlaceholderText("可空，0-100")
                self.duration_input = QLineEdit()
                self.duration_input.setPlaceholderText("可空，分钟")
                self.note_input = QTextEdit()
                self.note_input.setPlaceholderText("例如：复习了 KMP 前缀函数，或课上学习了第九章")
                self.note_input.setMinimumHeight(82)
                self.problem_result_input = QTextEdit()
                self.problem_result_input.setPlaceholderText("例如：做了11到18题，错了16、17题，其余全对")
                self.problem_result_input.setMinimumHeight(86)
                self.problem_error_cause_input = QTextEdit()
                self.problem_error_cause_input.setPlaceholderText("例如：16题补面面积计算错误；17题通量计算错误")
                self.problem_error_cause_input.setMinimumHeight(76)
                self.problem_list = QListWidget()
                self.problem_list.setMinimumHeight(118)
                self.problem_list.currentRowChanged.connect(self.show_problem_preview)
                self.problem_preview = QTextEdit()
                self.problem_preview.setReadOnly(True)
                self.problem_preview.setMinimumHeight(160)
                self.problem_preview.setPlaceholderText("识别题目后，选中某一题可查看题面、正确率、难度依据和错因。")
                self.extraction_status = QLabel("附件解析会在点击“识别题目”时自动运行。")
                self.extraction_status.setObjectName("StatusLabel")
                self.extraction_status.setWordWrap(True)
                recognize_problem_button = QPushButton("识别题目")
                recognize_problem_button.setObjectName("PrimaryButton")
                recognize_problem_button.clicked.connect(self.recognize_problems)
                clear_problem_button = QPushButton("清空识别结果")
                clear_problem_button.setObjectName("GhostButton")
                clear_problem_button.clicked.connect(self.clear_recognized_problems)
                problem_buttons = QHBoxLayout()
                problem_buttons.addWidget(recognize_problem_button)
                problem_buttons.addWidget(clear_problem_button)

                self.attachment_list = QListWidget()
                self.attachment_list.setMinimumHeight(84)
                self.file_hint_input = QLineEdit()
                self.file_hint_input.setPlaceholderText("例如：坚果云 chap6 / 桌面 微积分2 / 下载 化学原理总结")
                search_file_button = QPushButton("按描述找文件")
                search_file_button.setObjectName("GhostButton")
                search_file_button.clicked.connect(self.search_files_by_hint)
                hint_buttons = QHBoxLayout()
                hint_buttons.addWidget(self.file_hint_input)
                hint_buttons.addWidget(search_file_button)
                self.search_result_list = QListWidget()
                self.search_result_list.setMinimumHeight(110)
                add_result_button = QPushButton("加入选中结果")
                add_result_button.setObjectName("PrimaryButton")
                add_result_button.clicked.connect(self.add_selected_search_results)
                add_file_button = QPushButton("添加文件")
                add_file_button.setObjectName("GhostButton")
                add_file_button.clicked.connect(self.add_files)
                remove_file_button = QPushButton("移除选中")
                remove_file_button.setObjectName("GhostButton")
                remove_file_button.clicked.connect(self.remove_selected_files)
                file_buttons = QHBoxLayout()
                file_buttons.addWidget(add_result_button)
                file_buttons.addWidget(add_file_button)
                file_buttons.addWidget(remove_file_button)

                self.add_form_row(basic_form, "日期", self.date_input)
                self.add_form_row(basic_form, "学科", self.subject_input)
                self.add_form_row(basic_form, "模块", self.module_input)
                self.add_form_row(basic_form, "主题", self.topic_input)
                self.add_form_row(basic_form, "活动", self.activity_input)
                self.add_form_row(basic_form, "来源", self.source_input)
                self.add_form_row(basic_form, "分数", self.score_input)
                self.add_form_row(basic_form, "时长", self.duration_input)
                self.add_form_row(basic_form, "备注", self.note_input)
                self.add_form_row(basic_form, "描述找文件", hint_buttons)
                self.add_form_row(basic_form, "搜索结果", self.search_result_list)
                self.add_form_row(basic_form, "附件", self.attachment_list)
                basic_form.addRow("", file_buttons)

                self.add_form_row(work_form, "做题情况", self.problem_result_input)
                self.add_form_row(work_form, "错因", self.problem_error_cause_input)
                self.add_form_row(work_form, "附件解析", self.extraction_status)
                self.add_form_row(work_form, "识别结果", self.problem_list)
                self.add_form_row(work_form, "题目预览", self.problem_preview)
                work_form.addRow("", problem_buttons)

                actions = QHBoxLayout()
                actions.addStretch()
                clear_button = QPushButton("清空")
                clear_button.setObjectName("GhostButton")
                clear_button.clicked.connect(self.reset_form)
                save_button = QPushButton("保存记录")
                save_button.setObjectName("PrimaryButton")
                save_button.clicked.connect(self.save_record)
                actions.addWidget(clear_button)
                actions.addWidget(save_button)
                layout.addLayout(actions)

                self._recognition_service = AsyncTaskService(max_workers=2)
                self._recognition_handle = None
                self._recognition_signature = ""
                self._recognition_open = True
                self._recognition_timer = QTimer(self)
                self._recognition_timer.setInterval(20)
                self._recognition_timer.timeout.connect(self._poll_recognition_result)
                self._recognition_timer.start()
                self.destroyed.connect(self._close_recognition_tasks)

            def closeEvent(self, event):
                self._close_recognition_tasks()
                super().closeEvent(event)

            def _close_recognition_tasks(self):
                if not self._recognition_open:
                    return
                self._recognition_open = False
                self._recognition_timer.stop()
                self._recognition_service.invalidate("record-editor")
                self._recognition_service.close()

            def _invalidate_recognition(self):
                self._recognition_service.invalidate("record-editor")
                self._recognition_handle = None
                self._recognition_signature = ""

            def _recognition_input_signature(self):
                inputs = {
                    "subject": self.subject_input.currentText(),
                    "module": self.module_input.currentText(),
                    "topic": self.topic_input.text(),
                    "note": self.note_input.toPlainText(),
                    "answer_result": self.problem_result_input.toPlainText(),
                    "error_cause": self.problem_error_cause_input.toPlainText(),
                    "score": self.score_input.text(),
                    "attachments": list(self.attachments),
                    "extracted_context": self.extracted_context,
                    "problems": list(self.problems),
                }
                raw = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
                return hashlib.sha256(raw.encode("utf-8")).hexdigest()

            def make_card(self, title, subtitle=""):
                card = QWidget()
                card.setObjectName("Card")
                layout = QVBoxLayout(card)
                layout.setContentsMargins(18, 16, 18, 18)
                layout.setSpacing(12)
                label = QLabel(title)
                label.setObjectName("CardTitle")
                layout.addWidget(label)
                if subtitle:
                    hint = QLabel(subtitle)
                    hint.setObjectName("Muted")
                    hint.setWordWrap(True)
                    layout.addWidget(hint)
                form = QFormLayout()
                form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
                form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
                form.setVerticalSpacing(12)
                form.setHorizontalSpacing(14)
                layout.addLayout(form)
                return card, form

            def add_form_row(self, form, label_text, field):
                label = QLabel(label_text)
                label.setObjectName("FormLabel")
                label.setFixedWidth(78)
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
                form.addRow(label, field)

            def refresh_modules(self, subject_name):
                current = self.module_input.currentText() if hasattr(self, "module_input") else ""
                self.module_input.clear()
                if subject_name:
                    self.module_input.addItems(list_module_names(subject_name))
                if current:
                    self.module_input.setCurrentText(current)

            def add_files(self):
                paths, _ = QFileDialog.getOpenFileNames(
                    self,
                    "选择学习材料或题目文件",
                    "",
                    "学习材料 (*.pdf *.pptx *.ppt *.docx *.xlsx *.png *.jpg *.jpeg *.webp);;所有文件 (*.*)",
                )
                for path in paths:
                    if path and path not in self.attachments:
                        self.attachments.append(path)
                        self.attachment_list.addItem(path)

            def search_files_by_hint(self):
                query = self.file_hint_input.text().strip()
                if not query:
                    QMessageBox.information(self, "请输入描述", "例如：坚果云 chap6，或 桌面 微积分2。")
                    return
                self.search_result_list.clear()
                matches = locate_files(query)
                if not matches:
                    self.search_result_list.addItem("未找到匹配文件。可以换更短的关键词，例如 chap6 或 微积分2。")
                    return
                for match in matches:
                    self.search_result_list.addItem(str(match.path))

            def add_selected_search_results(self):
                for item in self.search_result_list.selectedItems():
                    text = item.text()
                    if text.startswith("未找到"):
                        continue
                    path = text
                    if path and path not in self.attachments:
                        self.attachments.append(path)
                        self.attachment_list.addItem(path)

            def remove_selected_files(self):
                for item in self.attachment_list.selectedItems():
                    path = item.text()
                    if path in self.attachments:
                        self.attachments.remove(path)
                    self.attachment_list.takeItem(self.attachment_list.row(item))

            def recognize_problems(self):
                if self.should_use_llm_for_record_parser():
                    if self.recognize_problems_with_llm(show_unavailable=False):
                        return
                self.recognize_problems_locally()

            def recognize_problems_locally(self):
                self.extract_attachment_context()
                self.problems = self.infer_problems_from_inputs()
                self.problem_list.clear()
                if not self.problems:
                    self.problem_list.addItem("未识别到具体题目；保存时会只记录学习概况。")
                    self.problem_preview.clear()
                    return
                for problem in self.problems:
                    self.problem_list.addItem(self._problem_display_text(problem))
                self.problem_list.setCurrentRow(0)
                wrong_count = sum(1 for problem in self.problems if problem.get("status") == "wrong")
                correct_count = sum(1 for problem in self.problems if problem.get("status") == "correct")
                difficulties = [
                    float(problem["difficulty_score"])
                    for problem in self.problems
                    if isinstance(problem.get("difficulty_score"), (int, float))
                ]
                difficulty_text = (
                    f"；平均难度 {sum(difficulties) / len(difficulties):.0f}"
                    if difficulties else ""
                )
                estimated_score = self.estimate_record_score_from_problems()
                score_text = ""
                if estimated_score is not None:
                    score_text = f"；估算记录分 {estimated_score:.1f}"
                    if not self.score_input.text().strip():
                        self.score_input.setText(f"{estimated_score:.1f}")
                self.extraction_status.setText(
                    f"已识别 {len(self.problems)} 道题：正确 {correct_count}，错误 {wrong_count}{difficulty_text}{score_text}。"
                )

            def clear_recognized_problems(self):
                self._invalidate_recognition()
                self.problems = []
                self.problem_list.clear()
                self.problem_preview.clear()

            def should_use_llm_for_record_parser(self):
                try:
                    from study_app.ai.providers import is_llm_feature_enabled

                    if is_llm_feature_enabled("record_parser"):
                        return True
                    return bool(self.attachments) and is_llm_feature_enabled("attachment_parser")
                except Exception:
                    return False

            def recognize_problems_with_llm(self, show_unavailable=True):
                from study_app.ai.record_parser import parse_record_payload_with_llm

                if not self._recognition_open:
                    return False
                self.extract_attachment_context()
                payload = {
                    "subject": self.subject_input.currentText().strip(),
                    "module": self.module_input.currentText().strip(),
                    "topic": self.topic_input.text().strip(),
                    "note": self.note_input.toPlainText().strip(),
                    "answer_result": self.problem_result_input.toPlainText().strip(),
                    "error_cause": self.problem_error_cause_input.toPlainText().strip(),
                    "attachment_text": self.extracted_context[:12000],
                    "attachments": [Path(path).name for path in self.attachments],
                }
                signature = self._recognition_input_signature()
                active = self._recognition_handle
                if active is not None and not active.snapshot().finished:
                    if self._recognition_signature == signature:
                        return True
                    self._invalidate_recognition()
                elif active is not None:
                    self._invalidate_recognition()
                self._recognition_signature = signature
                self._recognition_show_unavailable = show_unavailable
                self.extraction_status.setText("LLM增强识别中；可继续编辑，保存前请等待识别完成。")
                self._recognition_handle = self._recognition_service.submit(
                    ("record_parser", signature),
                    lambda: parse_record_payload_with_llm(payload),
                    slot="record-editor",
                    timeout_seconds=RECORD_RECOGNITION_TASK_TIMEOUT_SECONDS,
                )
                return True

            def _poll_recognition_result(self):
                if not self._recognition_open or self._recognition_handle is None:
                    return
                snapshot = self._recognition_handle.snapshot()
                if not snapshot.finished:
                    return
                signature = self._recognition_signature
                self._recognition_handle = None
                if signature != self._recognition_input_signature():
                    self.extraction_status.setText("识别输入已变化；已忽略旧 LLM 结果。")
                    return
                if snapshot.status == TaskStatus.SUCCEEDED:
                    if snapshot.result:
                        self._apply_llm_problems(snapshot.result)
                    elif not self.problems:
                        self.recognize_problems_locally()
                        self.extraction_status.setText("LLM 未返回可用题目；已改用本地规则。")
                    return
                if snapshot.status not in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
                    return
                from study_app.ai.llm_client import LLMNotConfiguredError

                if snapshot.status == TaskStatus.TIMED_OUT:
                    message = "LLM增强识别超时，已改用本地规则。"
                    title = "LLM增强识别超时"
                elif isinstance(snapshot.error, LLMNotConfiguredError):
                    message = f"LLM增强识别不可用，已改用本地规则：{snapshot.error}"
                    title = "LLM增强识别不可用"
                else:
                    message = f"LLM增强识别失败，已改用本地规则：{snapshot.error}"
                    title = "LLM增强识别失败"
                if not self.problems:
                    self.recognize_problems_locally()
                self.extraction_status.setText(message)
                if title == "LLM增强识别失败":
                    QMessageBox.critical(self, title, message)
                else:
                    QMessageBox.warning(self, title, message)

            def _apply_llm_problems(self, problems):
                self.problems = problems
                for problem in self.problems:
                    problem.setdefault("attachment_sources", [Path(path).name for path in self.attachments])
                    problem.setdefault("error_category", self.classify_error_category(problem.get("error_cause", "")))
                    self.infer_problem_fields(problem)
                self.problem_list.clear()
                for problem in self.problems:
                    self.problem_list.addItem(self._problem_display_text(problem))
                self.problem_list.setCurrentRow(0)
                estimated_score = self.estimate_record_score_from_problems()
                if estimated_score is not None and not self.score_input.text().strip():
                    self.score_input.setText(f"{estimated_score:.1f}")
                self.extraction_status.setText(
                    f"LLM增强识别完成：{len(self.problems)} 道题"
                    + (f"；估算记录分 {estimated_score:.1f}" if estimated_score is not None else "")
                    + "。"
                )

            def show_problem_preview(self, row):
                if row < 0 or row >= len(self.problems):
                    self.problem_preview.clear()
                    return
                self.problem_preview.setPlainText(self._problem_preview_text(self.problems[row]))

            def infer_problems_from_inputs(self):
                answer_result = self.problem_result_input.toPlainText().strip()
                error_cause = self.problem_error_cause_input.toPlainText().strip()
                note = self.note_input.toPlainText().strip()
                manual_context = "\n".join([note, answer_result, error_cause, *self.attachments])
                context = "\n".join([manual_context, self.extracted_context])
                titles = self.extract_problem_titles(manual_context)
                if not titles:
                    titles = [block.title for block in self.attachment_problem_blocks[:30]]
                if not titles:
                    titles = self.extract_problem_titles(self.extracted_context)
                if not titles and (answer_result or error_cause):
                    titles = [self.topic_input.text().strip() or self.attachment_based_title() or "识别题目"]
                wrong_titles = self.extract_wrong_titles(context)
                error_by_title = self.extract_error_causes_by_title(error_cause)
                statement_by_title = {
                    block.title: block.statement
                    for block in self.attachment_problem_blocks
                }
                problems = []
                for title in titles:
                    problem_error = error_by_title.get(title, error_cause if title in wrong_titles else "")
                    statement = statement_by_title.get(title) or self.inferred_statement_for(title, context)
                    problem = {
                        "title": title,
                        "statement": statement,
                        "statement_source": "attachment_excerpt" if title in statement_by_title or "附件题面摘录" in statement else "generated_summary",
                        "answer_result": self.answer_result_for(title, answer_result, wrong_titles, len(titles)),
                        "error_cause": problem_error,
                        "error_category": self.classify_error_category(problem_error),
                        "related_topics": [self.topic_input.text().strip()] if self.topic_input.text().strip() else [],
                        "parser_source": "local_rule",
                        "attachment_sources": [Path(path).name for path in self.attachments],
                    }
                    self.infer_problem_fields(problem)
                    problems.append(problem)
                return problems

            def extract_problem_titles(self, text):
                titles = []
                for start, end in re.findall(r"(?:第)?(\d+)\s*(?:到|-|~|至)\s*(\d+)\s*题", text):
                    a, b = int(start), int(end)
                    if 0 < a <= b and b - a <= 80:
                        titles.extend([f"第{number}题" for number in range(a, b + 1)])
                for end in re.findall(r"前\s*(\d+)\s*题", text):
                    count = min(int(end), 80)
                    titles.extend([f"第{number}题" for number in range(1, count + 1)])
                for number in re.findall(r"(?:第|例)\s*(\d+(?:\.\d+)*)\s*题?", text):
                    title = f"第{number}题" if "." not in number else f"例{number}"
                    if title not in titles:
                        titles.append(title)
                if not titles:
                    match = re.search(r"(?:做了|完成了|练了)\s*(\d+)\s*题", text)
                    if match:
                        count = min(int(match.group(1)), 50)
                        titles.extend([f"第{number}题" for number in range(1, count + 1)])
                if not titles:
                    for number in re.findall(r"(?:^|\n)\s*((?:\d+\.)?\d+)\s+.{6,}", text):
                        title = f"第{number}题"
                        if title not in titles:
                            titles.append(title)
                        if len(titles) >= 30:
                            break
                return list(dict.fromkeys(titles))

            def extract_wrong_titles(self, text):
                wrong = set()
                for group in re.findall(r"错(?:了|误)?\s*(?:第)?([\d一二三四五六七八九十两、，,\s和]+)\s*题", text):
                    for token in re.findall(r"\d+|[一二三四五六七八九十两]+", group):
                        number = self.parse_problem_number(token)
                        if number is not None:
                            wrong.add(f"第{number}题")
                for token in re.findall(r"(?:错(?:因|在)?|错误在|没掌握|不会|算错)\s*(?:第)?(\d+|[一二三四五六七八九十两]+)\s*题", text):
                    number = self.parse_problem_number(token)
                    if number is not None:
                        wrong.add(f"第{number}题")
                return wrong

            def extract_error_causes_by_title(self, text):
                result = {}
                pattern = r"(?:第)?(\d+|[一二三四五六七八九十两]+)\s*题?(?:错因(?:是|为)?|因为|因|错在)([^。；;\n]+)"
                for raw_number, reason in re.findall(pattern, text):
                    number = self.parse_problem_number(raw_number)
                    if number is not None:
                        result[f"第{number}题"] = reason.strip(" ：:，,")
                return result

            def classify_error_category(self, text):
                value = (text or "").strip()
                if not value:
                    return "none"
                rules = [
                    ("boundary_omission", ["边界", "端点", "零点", "特殊情况", "特殊点", "奇点", "范围", "定义域"]),
                    ("condition_misjudgment", ["条件", "适用", "方向", "取向", "补面", "符号", "判断错误"]),
                    ("calculation_error", ["计算", "算错", "代入", "化简", "积分算错", "面积算错", "通量计算"]),
                    ("method_gap", ["不会", "没有掌握", "不熟", "忘记方法", "势函数", "公式遗忘"]),
                    ("modeling_error", ["建模", "列式", "区域设置", "变量", "换元", "坐标"]),
                    ("concept_forgetting", ["概念", "定义", "遗忘", "忘了", "记错"]),
                    ("time_management", ["时间", "来不及", "粗心", "审题太快"]),
                ]
                for category, keywords in rules:
                    if any(keyword in value for keyword in keywords):
                        return category
                return "condition_misjudgment"

            def parse_problem_number(self, value):
                value = str(value).strip()
                if value.isdigit():
                    return int(value)
                digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
                if value == "十":
                    return 10
                if value.startswith("十"):
                    return 10 + digits.get(value[1:], 0)
                if "十" in value:
                    left, right = value.split("十", 1)
                    return digits.get(left, 0) * 10 + digits.get(right, 0)
                return digits.get(value)

            def attachment_based_title(self):
                if not self.attachments:
                    return ""
                return Path(self.attachments[0]).stem

            def inferred_statement_for(self, title, context):
                snippet = self.extract_statement_snippet(title, self.extracted_context)
                pieces = [
                    f"系统从做题描述中识别：{title}",
                    f"主题：{self.topic_input.text().strip()}" if self.topic_input.text().strip() else "",
                    f"模块：{self.module_input.currentText().strip()}" if self.module_input.currentText().strip() else "",
                    "附件：" + "；".join(Path(path).name for path in self.attachments) if self.attachments else "",
                    f"附件题面摘录：{snippet}" if snippet else "",
                    context[:500],
                ]
                return "\n".join(piece for piece in pieces if piece)

            def extract_statement_snippet(self, title, text):
                if not text:
                    return ""
                numbers = re.findall(r"\d+(?:\.\d+)*", title)
                if not numbers:
                    return ""
                number = numbers[0]
                patterns = [
                    rf"(?:第\s*)?{re.escape(number)}\s*题?[^\n]*(?:\n[^\n]*){{0,3}}",
                    rf"(?m)^\s*{re.escape(number)}\s+[^\n]*(?:\n[^\n]*){{0,3}}",
                    rf"例\s*{re.escape(number)}[^\n]*(?:\n[^\n]*){{0,3}}",
                ]
                for pattern in patterns:
                    match = re.search(pattern, text)
                    if match:
                        return match.group(0).strip()[:1000]
                return ""

            def extract_attachment_context(self):
                if not self.attachments:
                    self.extracted_items = []
                    self.extracted_context = ""
                    self.attachment_problem_blocks = []
                    self.extraction_status.setText("未添加附件。")
                    return
                self.extraction_status.setText("正在解析附件文本...")
                self.extracted_items = extract_text_from_files(self.attachments)
                self.extracted_context = combined_extracted_text(self.extracted_items)
                self.attachment_problem_blocks = parse_problem_blocks_from_text(self.extracted_context)
                text_count = sum(1 for item in self.extracted_items if item.text and not item.warning)
                warnings = [item.warning for item in self.extracted_items if item.warning]
                if text_count:
                    block_text = f"；识别到 {len(self.attachment_problem_blocks)} 个题面块" if self.attachment_problem_blocks else ""
                    self.extraction_status.setText(f"已解析 {text_count} 个附件{block_text}；将用于识别题目与题面。")
                elif warnings:
                    self.extraction_status.setText(warnings[0])
                else:
                    self.extraction_status.setText("附件中未提取到可用文本。")

            def answer_result_for(self, title, answer_result, wrong_titles, total_count):
                if title in wrong_titles:
                    return "错误"
                if "全对" in answer_result or "其余都对" in answer_result or "其余全对" in answer_result:
                    return "全对"
                if total_count == 1:
                    return answer_result
                return "全对"

            def _problem_display_text(self, problem):
                from learning_problem_result import interpret_problem_result
                difficulty = problem.get("difficulty_score")
                correctness_value = interpret_problem_result(problem)["value"]
                correctness = None if correctness_value is None else correctness_value * 100
                error = problem.get("error_cause")
                suffix = f" | 错因：{error}" if error else ""
                correctness_text = "待判断" if correctness is None else f"{correctness:.1f}%"
                difficulty_text = "待判断" if difficulty is None else f"{difficulty:.0f}"
                statement = problem.get("statement") or ""
                source = "有题面" if "附件题面摘录" in statement else "题面摘要"
                return f"{problem['title']} | 正确率 {correctness_text} | 难度 {difficulty_text} | {source}{suffix}"

            def _problem_preview_text(self, problem):
                from learning_problem_result import interpret_problem_result
                correctness_value = interpret_problem_result(problem)["value"]
                correctness = None if correctness_value is None else correctness_value * 100
                correctness_text = "待判断" if correctness is None else f"{correctness:.1f}%"
                difficulty_score = problem.get("difficulty_score")
                difficulty_text = "待判断" if difficulty_score is None else f"{difficulty_score:.0f}"
                confidence = problem.get("difficulty_confidence")
                confidence_text = "" if confidence is None else f"（置信度 {float(confidence):.0%}）"
                reasons = problem.get("difficulty_reasons") or []
                if reasons:
                    reason_text = "\n".join(f"- {reason}" for reason in reasons[:5])
                else:
                    reason_text = "- 暂无明确难度依据，使用主题和作答信息估计。"
                statement = (problem.get("statement") or "").strip()
                if len(statement) > 1200:
                    statement = statement[:1200] + "\n..."
                source = {
                    "attachment_excerpt": "附件题面摘录",
                    "generated_summary": "系统摘要",
                }.get(problem.get("statement_source"), problem.get("statement_source") or "未知")
                attachments = "、".join(problem.get("attachment_sources") or []) or "无"
                contribution = self.problem_score_contribution_text(problem)
                return (
                    f"题目：{problem.get('title', '未命名题目')}\n"
                    f"状态：{problem.get('status') or '待判断'}\n"
                    f"正确率：{correctness_text}\n"
                    f"难度：{difficulty_text}{confidence_text}\n"
                    f"估分影响：{contribution}\n"
                    f"错因分类：{self.error_category_label(problem.get('error_category'))}\n"
                    f"映射知识点：{'、'.join(problem.get('related_topics') or []) or '暂无'}\n"
                    f"题面来源：{source}\n"
                    f"附件来源：{attachments}\n"
                    f"错因：{problem.get('error_cause') or '无'}\n\n"
                    f"难度依据：\n{reason_text}\n\n"
                    f"题面/摘要：\n{statement or '暂无题面。'}"
                )

            def error_category_label(self, category):
                labels = {
                    "concept_forgetting": "概念遗忘",
                    "condition_misjudgment": "条件/方向误判",
                    "calculation_error": "计算错误",
                    "modeling_error": "建模/列式错误",
                    "boundary_omission": "边界/特殊情况遗漏",
                    "method_gap": "方法未掌握",
                    "time_management": "时间/审题问题",
                    "none": "无",
                }
                return labels.get(category or "none", str(category))

            def problem_score_contribution_text(self, problem):
                from learning_problem_result import interpret_problem_result
                difficulty = problem.get("difficulty_score")
                correctness_value = interpret_problem_result(problem)["value"]
                correctness = None if correctness_value is None else correctness_value * 100
                if not isinstance(difficulty, (int, float)) or not isinstance(correctness, (int, float)):
                    return "待判断"
                if correctness >= 99:
                    if difficulty >= 75:
                        return "高难度做对，对本次记录分和掌握度是强正向证据。"
                    if difficulty <= 35:
                        return "基础题做对，是稳定性证据，加分较温和。"
                    return "中等题做对，是正向掌握证据。"
                if correctness <= 1:
                    if difficulty <= 35:
                        return "基础题做错，会明显拉低本次记录分，建议优先复盘。"
                    if difficulty >= 75:
                        return "高难度做错，扣分相对克制，但会保留为重点观察。"
                    return "中等题做错，会降低本次记录分并触发复盘。"
                return "部分正确，会按正确率和难度折算。"

            def estimate_record_score_from_problems(self):
                if not self.problems:
                    return None
                try:
                    from learning_monitor import load_json, record_score

                    model = load_json(MODEL_PATH)
                    policy = model.get("warning_policy", {})
                except Exception:
                    policy = {}
                    try:
                        from learning_monitor import record_score
                    except Exception:
                        return None
                record = {
                    "subject": self.subject_input.currentText().strip(),
                    "module": self.module_input.currentText().strip(),
                    "topic": self.topic_input.text().strip(),
                    "note": self.note_input.toPlainText().strip(),
                    "problems": list(self.problems),
                }
                def fallback_problem_weighted_score():
                    from learning_problem_result import interpret_problem_result

                    scores = []
                    weights = []
                    for problem in self.problems:
                        interpretation = interpret_problem_result(problem)
                        correctness = interpretation["value"]
                        if correctness is None:
                            continue
                        difficulty = problem.get("difficulty_score")
                        difficulty = float(difficulty) if isinstance(difficulty, (int, float)) else 55.0
                        ratio = max(0.0, min(1.0, difficulty / 100))
                        correct_score = 58 + (100 - 58) * (ratio ** 1.15)
                        wrong_score = 8 + (58 - 8) * (ratio ** 1.25)
                        score = wrong_score + (correct_score - wrong_score) * correctness
                        weight = 0.65 + 0.85 * ratio
                        scores.append(score)
                        weights.append(weight)
                    if not scores:
                        return None
                    return sum(score * weight for score, weight in zip(scores, weights)) / sum(weights)

                score = record_score(record, policy)
                if score is None:
                    score = fallback_problem_weighted_score()
                if score is None:
                    return None
                return max(0.0, min(100.0, float(score)))
            def infer_problem_fields(self, problem):
                from study_app.data.database import _infer_correctness_from_text
                try:
                    from learning_difficulty import infer_problem_difficulty
                except Exception:
                    infer_problem_difficulty = None

                correctness = _infer_correctness_from_text(problem)
                if correctness is not None:
                    problem["correctness"] = correctness * 100
                    problem["partial_credit"] = correctness
                    if correctness >= 0.995:
                        problem["status"] = "correct"
                    elif correctness <= 0.005:
                        problem["status"] = "wrong"
                    else:
                        problem["status"] = "partial"

                if infer_problem_difficulty is not None:
                    if (
                        problem.get("difficulty_source") == "llm"
                        and problem.get("difficulty_score") is not None
                    ):
                        return
                    record = {
                        "subject": self.subject_input.currentText().strip(),
                        "module": self.module_input.currentText().strip(),
                        "topic": self.topic_input.text().strip(),
                        "note": self.note_input.toPlainText().strip(),
                    }
                    inferred = infer_problem_difficulty(record, problem)
                    problem["difficulty"] = inferred.get("difficulty")
                    problem["difficulty_score"] = inferred.get("difficulty_score")
                    problem["difficulty_source"] = inferred.get("source")
                    problem["difficulty_confidence"] = inferred.get("confidence")
                    if inferred.get("reasons"):
                        problem["difficulty_reasons"] = inferred["reasons"]

            def has_pending_problem(self):
                return bool(
                    self.problem_result_input.toPlainText().strip()
                    or self.problem_error_cause_input.toPlainText().strip()
                )

            def record_payload(self):
                from pathlib import Path

                attachments = []
                for path in self.attachments:
                    file_path = Path(path)
                    attachments.append(
                        {
                            "file_path": str(file_path),
                            "file_name": file_path.name,
                            "file_ext": file_path.suffix.lower(),
                            "file_size": file_path.stat().st_size if file_path.exists() else None,
                        }
                    )
                return {
                    "date": self.date_input.date().toString("yyyy-MM-dd"),
                    "subject": self.subject_input.currentText().strip(),
                    "module": self.module_input.currentText().strip(),
                    "topic": self.topic_input.text().strip(),
                    "activity": self.activity_input.currentData(),
                    "source": self.source_input.currentData(),
                    "score": float(self.score_input.text().strip())
                    if self.score_input.text().strip()
                    else None,
                    "duration_minutes": float(self.duration_input.text().strip())
                    if self.duration_input.text().strip()
                    else None,
                    "note": self.note_input.toPlainText().strip(),
                    "problems": list(self.problems),
                    "attachments": attachments,
                }

            def save_record(self):
                if self._recognition_handle is not None:
                    QMessageBox.information(
                        self,
                        "正在识别题目",
                        "请等待当前识别完成后再保存，避免保存未确认的题目结果。",
                    )
                    return
                if self.has_pending_problem() and not self.problems:
                    self.recognize_problems()
                    if self._recognition_handle is not None:
                        QMessageBox.information(
                            self,
                            "正在识别题目",
                            "请等待当前识别完成后再保存，避免保存未确认的题目结果。",
                        )
                        return
                payload = self.record_payload()
                if not payload["subject"]:
                    QMessageBox.warning(self, "缺少学科", "请填写或选择学科。")
                    return
                if not payload["topic"] and not payload["note"] and not payload["problems"]:
                    QMessageBox.warning(self, "缺少内容", "请至少填写主题、备注或题目。")
                    return
                for field_name, label in [("score", "分数"), ("duration_minutes", "时长")]:
                    value = payload[field_name]
                    if not value:
                        continue
                    try:
                        number = float(value)
                    except ValueError:
                        QMessageBox.warning(self, "格式错误", f"{label}需要填写数字，或留空。")
                        return
                    if field_name == "score" and not 0 <= number <= 100:
                        QMessageBox.warning(self, "分数范围错误", "分数应在 0 到 100 之间。")
                        return
                if self.on_saved:
                    self.on_saved(payload, self)

            def reset_form(self):
                self._invalidate_recognition()
                self.topic_input.clear()
                self.score_input.clear()
                self.duration_input.clear()
                self.note_input.clear()
                self.problem_result_input.clear()
                self.problem_error_cause_input.clear()
                self.problem_list.clear()
                self.problem_preview.clear()
                self.search_result_list.clear()
                self.attachment_list.clear()
                self.file_hint_input.clear()
                self.attachments = []
                self.problems = []
                self.extracted_items = []
                self.extracted_context = ""
                self.extraction_status.setText("附件解析会在点击“识别题目”时自动运行。")

        return _AddRecordDialog(owner)

def add_record_page(save_callback, state: DashboardState):
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(24, 22, 24, 24)
    layout.setSpacing(16)
    editor = AddRecordDialog(content, save_callback, record_subject_names(state))
    layout.addWidget(editor)
    layout.addStretch()
    scroll.setWidget(content)
    return scroll
