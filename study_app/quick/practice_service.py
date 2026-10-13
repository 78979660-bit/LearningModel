"""Qt-independent adapters for local practice PDFs and prompt copying.

The service keeps filesystem paths and workflow state in Python. QML receives
only filenames, opaque artifact tokens, and prompts that users may copy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from study_app.core.local_practice_spec import LocalPracticePaperSpec

LOGGER = logging.getLogger(__name__)


@dataclass
class _PreparedPrompt:
    token: str
    prompt: str
    subject: str
    topic: str
    kind: str = "practice"
    detail: str = ""
    created_at: float = field(default_factory=time.monotonic)


def _token() -> str:
    return secrets.token_urlsafe(24)


def _safe_detail(value: object, *, fallback: str = "操作失败，请检查输入、题库和运行依赖。") -> str:
    """Keep user-facing diagnostics useful without leaking secrets or host paths."""
    detail = str(value or "").strip()
    if not detail:
        return fallback
    detail = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|credential)\s*[:=]\s*([^\s,;]+)",
        r"\1=[已隐藏]",
        detail,
    )
    detail = re.sub(
        r"(?i)\b(?:authorization\s*[:=]\s*)?bearer\s+[^\s,;]+",
        "Bearer [已隐藏]",
        detail,
    )
    detail = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "[已隐藏]", detail)
    detail = re.sub(r"(?i)\b[A-Z]:[\\/][^\r\n,;|]*", "[本地文件]", detail)
    detail = re.sub(r"\\\\[^\\\s]+\\[^\\\s]+[^\r\n,;|]*", "[本地文件]", detail)
    detail = re.sub(r"(?<![\w:])/(?:[^\r\n,;|]+)", "[本地文件]", detail)
    return detail[:500]


def _redact_prompt_secrets(value: str) -> str:
    prompt = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|credential)\s*[:=]\s*([^\s,;]+)",
        r"\1=[已隐藏]",
        str(value or ""),
    )
    prompt = re.sub(
        r"(?i)\b(?:authorization\s*[:=]\s*)?bearer\s+[^\s,;]+",
        "Bearer [已隐藏]",
        prompt,
    )
    return re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "[已隐藏]", prompt)


def _mapping_value(mapping: Mapping[str, Any], *keys: str, default=None):
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return default


def _local_error_detail(error: Exception) -> str:
    try:
        from study_app.core.async_tasks import StaleSubjectTaskError
        from study_app.core.local_practice_composer import InsufficientPracticeCandidatesError
        from study_app.core.local_practice_pdf import LocalPracticePDFError
        from study_app.core.local_practice_service import LocalPracticeGenerationError
        from study_app.core.local_practice_spec import LocalPracticeSpecError
    except Exception:
        StaleSubjectTaskError = InsufficientPracticeCandidatesError = LocalPracticePDFError = ()
        LocalPracticeGenerationError = LocalPracticeSpecError = ()
    if isinstance(error, StaleSubjectTaskError):
        return "学科目录或状态已变化，请刷新后重新生成。"
    if isinstance(error, (LocalPracticeSpecError, InsufficientPracticeCandidatesError,
                          LocalPracticePDFError, LocalPracticeGenerationError)):
        return _safe_detail(error)
    return "本地练习卷生成失败，请检查题库、中文字体与 PDF 依赖。"


def _prompt_error_detail(error: Exception) -> str:
    try:
        from study_app.core.async_tasks import StaleSubjectTaskError
    except Exception:
        StaleSubjectTaskError = ()
    if isinstance(error, StaleSubjectTaskError):
        return "学科目录或状态已变化，请刷新后重新生成提示词。"
    if isinstance(error, ValueError):
        return _safe_detail(error)
    return "无法生成提示词，请刷新数据并检查学科范围。"


class PracticeService:
    """Adapt existing local practice and prompt builders for the QML UI."""

    PREVIEW_TTL_SECONDS = 15 * 60

    def __init__(
        self,
        db_path,
        model_path,
        records_path,
        today=None,
        initialize: Callable[[], None] | None = None,
        output_dir: Path | str | None = None,
        *,
        local_generator: Callable[..., Any] | None = None,
        local_renderer: Callable[..., dict[str, Any]] | None = None,
        dashboard_loader: Callable[..., Any] | None = None,
        prompt_builder: Callable[..., str] | None = None,
        mock_exam_prompt_builder: Callable[..., str] | None = None,
        subject_revision_capture: Callable[..., Any] | None = None,
        subject_revision_revalidate: Callable[..., Any] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        self.today = today
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self._initialize = initialize
        self._local_generator = local_generator
        self._local_renderer = local_renderer
        self._dashboard_loader = dashboard_loader
        self._prompt_builder = prompt_builder
        self._mock_exam_prompt_builder = mock_exam_prompt_builder
        self._subject_revision_capture = subject_revision_capture
        self._subject_revision_revalidate = subject_revision_revalidate
        self._lock = threading.RLock()
        self._prepared: dict[str, _PreparedPrompt] = {}
        self._artifacts: dict[str, Path] = {}

    def _initialize_once(self) -> None:
        with self._lock:
            if self._initialize is None:
                return
            self._initialize()
            self._initialize = None

    def _capture_subject_revision(self, subject: str):
        capture = self._subject_revision_capture
        if capture is None:
            from study_app.core.async_tasks import capture_subject_revision

            capture = capture_subject_revision
        return capture(self.db_path, subject) if subject else None

    def _revalidate_subject_revision(self, token) -> None:
        if token is None:
            return
        revalidate = self._subject_revision_revalidate
        if revalidate is None:
            from study_app.core.async_tasks import revalidate_subject_revision

            revalidate = revalidate_subject_revision
        revalidate(token)

    def _load_prompt_state(self, subject: str):
        self._initialize_once()
        loader = self._dashboard_loader or self._default_dashboard_loader
        revision = self._capture_subject_revision(subject)
        state = loader(
            model_path=self.model_path,
            records_path=self.records_path,
            db_path=self.db_path,
            today=self.today,
        )
        self._revalidate_subject_revision(revision)
        return state

    def _prune_prepared(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [
                token for token, item in self._prepared.items()
                if now - item.created_at >= self.PREVIEW_TTL_SECONDS
            ]
            for token in expired:
                self._prepared.pop(token, None)

    def load(self) -> dict[str, Any]:
        """Return concise local template choices and supported input bounds."""
        try:
            self._initialize_once()
            from study_app.data.practice_repository import list_practice_templates

            rows = list_practice_templates(self.db_path)
            templates = []
            for row in rows:
                template_id = str(row.get("template_id") or "").strip()
                if not template_id:
                    continue
                templates.append(
                    {
                        "id": _redact_prompt_secrets(template_id),
                        "name": _redact_prompt_secrets(str(row.get("title") or template_id)),
                        "subject": _redact_prompt_secrets(str(row.get("subject_hint") or "")),
                        "topic": _redact_prompt_secrets(str(row.get("topic_hint") or "")),
                        "description": _redact_prompt_secrets(str(row.get("description") or ""))[:240],
                        "localAllowed": template_id != "CS-OJ-PRACTICE",
                    }
                )
            return {
                "status": "ready",
                "templates": templates,
                "subjects": sorted({item["subject"] for item in templates if item["subject"]}),
                "difficultyMin": 0,
                "difficultyMax": 100,
                "questionCountMin": 1,
                "questionCountMax": 30,
            }
        except Exception as error:
            LOGGER.warning("QML practice options unavailable (%s)", type(error).__name__)
            return {
                "status": "unavailable",
                "templates": [],
                "subjects": [],
                "detail": "本地题库暂不可用，请检查数据初始化状态。",
                "difficultyMin": 0,
                "difficultyMax": 100,
                "questionCountMin": 1,
                "questionCountMax": 30,
            }

    def generate_local_practice(
        self,
        *,
        subject: object,
        template_id: object = "",
        topic: object = "",
        target_difficulty: object | None = None,
        question_count: object | None = None,
        paper_date: object | None = None,
        title: object = "",
        homework_line: object = "",
    ) -> dict[str, Any]:
        """Generate a local question/answer PDF pair using the existing core."""
        try:
            self._initialize_once()
            normalized_subject = str(subject or "").strip()
            if not normalized_subject:
                return {"status": "error", "detail": "生成练习卷前，请先选择学科。"}
            self._require_local_template(str(template_id or "").strip(), str(homework_line or ""))
            defaults: dict[str, Any] = {}
            if homework_line:
                from study_app.core.local_practice_service import spec_from_homework

                defaults = spec_from_homework(
                    str(homework_line),
                    subject=normalized_subject,
                    paper_date=paper_date or self.today or date.today(),
                ).canonical_dict()
            resolved_date = paper_date or defaults.get("paper_date") or self.today or date.today()
            spec = LocalPracticePaperSpec.create(
                subject=normalized_subject,
                template_id=(template_id if template_id not in (None, "") else defaults.get("template_id", "")),
                topic=(topic if topic not in (None, "") else ""),
                target_difficulty=(
                    target_difficulty
                    if target_difficulty is not None
                    else defaults.get("target_difficulty", 55)
                ),
                question_count=(
                    question_count
                    if question_count is not None
                    else defaults.get("question_count", 2)
                ),
                paper_date=resolved_date,
                title=title,
            )
            generator = self._local_generator or self._default_local_generator
            kwargs: dict[str, Any] = {"db_path": self.db_path, "output_directory": self.output_dir}
            if self._local_renderer is not None:
                kwargs["renderer"] = self._local_renderer
            result = generator(spec, **kwargs)
            manifest = result.manifest if isinstance(result.manifest, Mapping) else {}
            output_metadata = manifest.get("outputs", {}) if isinstance(manifest, Mapping) else {}
            actual_question_count = len(manifest.get("questions", []))
            question_token = self._register_artifact(result.question_pdf)
            answer_token = self._register_artifact(result.answer_pdf)
            manifest_token = self._register_artifact(result.manifest_json)
            return {
                "status": "generated",
                "paperId": str(result.paper_id),
                "questionFile": _redact_prompt_secrets(Path(result.question_pdf).name),
                "answerFile": _redact_prompt_secrets(Path(result.answer_pdf).name),
                "manifestFile": _redact_prompt_secrets(Path(result.manifest_json).name),
                "questionToken": question_token,
                "answerToken": answer_token,
                "manifestToken": manifest_token,
                "questionCount": actual_question_count,
                "actualQuestionCount": actual_question_count,
                "actualDistribution": dict(manifest.get("actual_distribution", {})),
                "warnings": [str(item) for item in manifest.get("warnings", [])],
                "questionPages": output_metadata.get("question_pdf", {}).get("pages"),
                "answerPages": output_metadata.get("answer_pdf", {}).get("pages"),
            }
        except Exception as error:
            LOGGER.warning("QML local practice generation failed (%s)", type(error).__name__)
            return {"status": "error", "detail": _local_error_detail(error)}

    def generate_practice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Workspace bridge entry point for explicit local PDF generation."""
        if not isinstance(payload, Mapping):
            return {"status": "error", "detail": "练习参数格式无效。"}
        values = dict(payload)
        subject = _mapping_value(values, "subject", "selected_subject", default="")
        try:
            return self.generate_local_practice(
                subject=subject,
                template_id=_mapping_value(values, "template_id", "templateId", default=""),
                topic=_mapping_value(values, "topic", "scope", default=""),
                target_difficulty=_mapping_value(values, "target_difficulty", "targetDifficulty"),
                question_count=_mapping_value(values, "question_count", "questionCount"),
                paper_date=_mapping_value(values, "paper_date", "paperDate"),
                title=values.get("title", ""),
                homework_line=_mapping_value(values, "homework_line", "homeworkLine", "line", default=""),
            )
        except Exception as error:
            LOGGER.warning("QML local practice request invalid (%s)", type(error).__name__)
            return {"status": "error", "detail": _safe_detail(error)}

    def prepare_practice_prompt(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Build and retain a prompt that the user may review and copy."""
        if not isinstance(payload, Mapping):
            return {"status": "error", "detail": "练习提示词参数格式无效。"}
        values = dict(payload)
        subject = str(_mapping_value(values, "selected_subject", "subject", default="") or "").strip()
        prompt_kind = str(_mapping_value(values, "kind", "prompt_kind", default="practice") or "practice").lower()
        try:
            if prompt_kind in {"plan_context", "practice_context", "copy_plan_context"}:
                return self._prepare_plan_practice_context(values, subject)

            if prompt_kind in {"mock_exam", "mock-exam", "mockexam"}:
                if not subject:
                    return {
                        "status": "error", "kind": "mock_exam",
                        "prompt": "", "detail": "生成模拟卷提示词前，请先选择具体学科。",
                    }
                state = self._load_prompt_state(subject)
                mode = str(values.get("mode") or "diagnostic")
                if self._mock_exam_prompt_builder is None:
                    prompt = self._default_mock_exam_prompt_builder(
                        state,
                        subject,
                        mode,
                        db_path=self.db_path,
                        model_path=self.model_path,
                    )
                else:
                    prompt = self._mock_exam_prompt_builder(state, subject, mode)
                prompt = _redact_prompt_secrets(prompt)
                token = _token()
                prepared = _PreparedPrompt(token, prompt, subject, "", kind="mock_exam")
                self._prune_prepared()
                with self._lock:
                    self._prepared[token] = prepared
                return self._prepared_dto(prepared)

            homework_line = str(
                _mapping_value(values, "homework_line", "homeworkLine", "line", default="") or ""
            ).strip()
            selected_topic = str(
                _mapping_value(values, "topic", "scope_text", "range_text", default="") or ""
            ).strip()
            prompt_builder_options: dict[str, Any] = {}
            if not homework_line:
                selected_topic = selected_topic or str(values.get("scope") or "").strip()
                template_id = str(_mapping_value(values, "template_id", "templateId", default="") or "").strip()
                if not subject:
                    raise ValueError("请选择练习学科。")
                if not template_id:
                    raise ValueError("请选择已登记的题库模板。")
                template_row = self._selected_template(template_id, subject)
                selected_topic = selected_topic or str(template_row.get("topic_hint") or "").strip() or "综合练习"
                difficulty = _mapping_value(values, "target_difficulty", "targetDifficulty", default=55)
                count = _mapping_value(values, "question_count", "questionCount", default=2)
                from study_app.core.local_practice_spec import LocalPracticePaperSpec

                selected_spec = LocalPracticePaperSpec.create(
                    subject=subject,
                    template_id=template_id,
                    topic=selected_topic,
                    target_difficulty=difficulty,
                    question_count=count,
                    paper_date=self.today or date.today(),
                    title=values.get("title", ""),
                )
                selected_topic = selected_spec.topic
                template_id = selected_spec.template_id
                template_brief = self._template_prompt_brief(template_row)
                homework_line = (
                    f"当天作业：{selected_topic}；参考难度 {selected_spec.target_difficulty:g}/100；"
                    f"题库模板 {template_id}；题量 {selected_spec.question_count} 题。"
                )
                prompt_builder_options = {
                    "template_id_override": template_id,
                    "template_brief_override": template_brief,
                    "topic_override": selected_topic,
                    "target_difficulty_override": selected_spec.target_difficulty,
                    "question_count_override": selected_spec.question_count,
                }
            elif selected_topic and selected_topic not in homework_line:
                homework_line = homework_line.rstrip("；;。 ") + f"；用户选择知识点范围：{selected_topic}。"
            from study_app.core.practice_spec import is_oj_plan_homework
            from study_app.core.study_plan_items import infer_subject_topic_from_plan_line

            subject, topic = infer_subject_topic_from_plan_line(homework_line, subject or None)
            topic = selected_topic or topic
            if is_oj_plan_homework(homework_line):
                return {
                    "status": "manual",
                    "kind": "oj_practice_list",
                    "token": "",
                    "subject": subject,
                    "topic": topic,
                    "prompt": "",
                    "detail": "OJ 作业使用 LeetCode 官方题目与测试集；不会生成通用练习提示词。",
                }
            state = self._load_prompt_state(subject)
            if self._prompt_builder is None:
                prompt = self._default_prompt_builder(
                    homework_line,
                    state,
                    subject or None,
                    db_path=self.db_path,
                    **prompt_builder_options,
                )
            else:
                prompt = self._prompt_builder(homework_line, state, subject or None)
            prompt = _redact_prompt_secrets(str(prompt))
            token = _token()
            prepared = _PreparedPrompt(token, prompt, subject, topic)
            self._prune_prepared()
            with self._lock:
                self._prepared[token] = prepared
            return self._prepared_dto(prepared)
        except Exception as error:
            LOGGER.warning("QML practice prompt preparation failed (%s)", type(error).__name__)
            return {
                "status": "error", "kind": prompt_kind,
                "prompt": "", "detail": _prompt_error_detail(error),
            }

    def _prepare_plan_practice_context(
        self, values: Mapping[str, Any], subject: str
    ) -> dict[str, Any]:
        state = self._load_prompt_state(subject)
        from study_app.core.practice_prompts import (
            build_oj_practice_list,
            build_practice_generation_prompt,
        )
        from study_app.core.practice_spec import is_oj_plan_homework
        from study_app.data.plan_repository import get_active_study_plan

        scope = subject or None
        saved = get_active_study_plan(scope, db_path=self.db_path)
        homework_items = [
            str(item.get("item_text") or "")
            for item in (saved or {}).get("items", [])
            if item.get("section_key") == "short" and item.get("item_type") == "result"
        ]
        if not homework_items:
            return {
                "status": "empty", "kind": "plan_context", "token": "",
                "prompt": "",
                "itemCount": 0, "detail": "当前范围没有已确认的每日作业。",
            }
        exports = []
        for item in homework_items:
            if is_oj_plan_homework(item):
                exports.append(build_oj_practice_list(item, state, scope, db_path=self.db_path))
            else:
                exports.append(build_practice_generation_prompt(item, state, scope, db_path=self.db_path))
        prompt = _redact_prompt_secrets("\n\n---\n\n".join(exports))
        token = _token()
        prepared = _PreparedPrompt(
            token, prompt, subject, "", kind="plan_context",
            detail="计划练习上下文仅用于复核或复制。",
        )
        self._prune_prepared()
        with self._lock:
            self._prepared[token] = prepared
        result = self._prepared_dto(prepared)
        result["itemCount"] = len(homework_items)
        result["copyable"] = True
        return result

    def _prepared_dto(self, prepared: _PreparedPrompt) -> dict[str, Any]:
        return {
            "status": "prepared",
            "kind": prepared.kind,
            "token": prepared.token,
            "subject": _redact_prompt_secrets(prepared.subject),
            "topic": _redact_prompt_secrets(prepared.topic),
            "prompt": prepared.prompt,
            "detail": prepared.detail or "提示词已生成，可复核后复制。",
            "expiresInSeconds": max(
                0, self.PREVIEW_TTL_SECONDS - int(time.monotonic() - prepared.created_at)
            ),
            "copyable": True,
        }

    def prepare_mock_exam_prompt(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        values = dict(payload) if isinstance(payload, Mapping) else {}
        values["kind"] = "mock_exam"
        return self.prepare_practice_prompt(values)

    def copy_plan_practice_context(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        values = dict(payload) if isinstance(payload, Mapping) else {}
        values["kind"] = "plan_context"
        return self.prepare_practice_prompt(values)




    def artifact_path(self, token: str) -> Path:
        """Resolve an opaque QML artifact token to its locally stored path."""
        with self._lock:
            path = self._artifacts.get(str(token or ""))
        if path is None:
            raise KeyError("练习文件已失效，请重新生成。")
        if not path.is_file():
            raise FileNotFoundError("练习文件不存在或已被移动。")
        return path

    @staticmethod
    def _require_local_template(template_id: str, homework_line: str) -> None:
        from study_app.core.practice_spec import is_oj_plan_homework
        from study_app.core.local_practice_spec import LocalPracticeSpecError

        if is_oj_plan_homework(homework_line) or template_id == "CS-OJ-PRACTICE":
            raise LocalPracticeSpecError("OJ 作业使用官方题目与测试集，不自动转换为本地练习卷。")

    def _selected_template(self, template_id: str, subject: str) -> dict[str, Any]:
        from study_app.data.practice_repository import list_practice_templates

        self._initialize_once()
        template = next(
            (
                row for row in list_practice_templates(self.db_path)
                if str(row.get("template_id") or "").strip() == template_id
            ),
            None,
        )
        if template is None:
            raise ValueError("所选题库模板不存在，请刷新模板列表后重试。")
        subject_hint = str(template.get("subject_hint") or "").strip()
        if subject_hint and not self._template_subject_matches(subject_hint, subject):
            raise ValueError("所选题库模板不适用于当前学科。")
        return template

    @staticmethod
    def _template_subject_matches(subject_hint: str, subject: str) -> bool:
        from study_app.core.active_subjects import SUBJECT_COURSE_ALIASES

        normalize = lambda value: "".join(str(value or "").split()).casefold()

        def identity(value: str) -> str:
            normalized = normalize(value)
            for canonical, aliases in SUBJECT_COURSE_ALIASES.items():
                if normalized in {normalize(canonical), *(normalize(alias) for alias in aliases)}:
                    return f"course:{canonical}"
            return f"name:{normalized}"

        hints = [
            part.strip()
            for part in re.split(r"[,，、/／|;；]", subject_hint)
            if part.strip()
        ]
        expected = identity(subject)
        return any(identity(candidate) == expected for candidate in hints)

    @staticmethod
    def _template_prompt_brief(template: Mapping[str, Any]) -> str:
        title = str(template.get("title") or template.get("template_id") or "题库模板").strip()
        description = str(template.get("description") or "").strip()
        topic = str(template.get("topic_hint") or "").strip()
        brief = title if not description or description == title else f"{title}：{description}"
        if topic and topic not in brief:
            brief += f"；适用知识点：{topic}"
        rules = template.get("generation_rules")
        if isinstance(rules, (list, tuple)):
            normalized_rules = [str(rule).strip()[:200] for rule in rules if str(rule).strip()]
            if normalized_rules:
                brief += "；生成要求：" + "；".join(normalized_rules[:8])
        return brief[:1200]

    def _register_artifact(self, path: Path | str) -> str:
        resolved = Path(path)
        token = _token()
        with self._lock:
            self._artifacts[token] = resolved
        return token

    def _default_local_generator(self, spec, **kwargs):
        from study_app.core.local_practice_service import generate_local_practice_paper

        return generate_local_practice_paper(spec, **kwargs)

    def _default_dashboard_loader(self, **kwargs):
        from study_app.core.dashboard import load_dashboard_state

        return load_dashboard_state(**kwargs)

    @staticmethod
    def _default_prompt_builder(
        homework_line,
        state,
        selected_subject,
        *,
        db_path,
        **builder_options,
    ):
        from study_app.core.practice_prompts import build_practice_generation_prompt

        return build_practice_generation_prompt(
            homework_line,
            state,
            selected_subject,
            db_path=db_path,
            **builder_options,
        )

    @staticmethod
    def _default_mock_exam_prompt_builder(state, subject, mode, *, db_path, model_path):
        from study_app.core.practice_prompts import build_mock_exam_generation_prompt

        return build_mock_exam_generation_prompt(
            state, subject, mode, db_path=db_path, model_path=model_path
        )







