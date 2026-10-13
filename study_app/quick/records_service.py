"""Qt-independent services for study records and OJ learning evidence.

The public methods in this module return plain, JSON-serializable dictionaries.
Database handles, local attachment paths, model contents, and LLM credentials
stay inside the service instance.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import inspect
import re
import secrets
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from study_app.core.async_tasks import (
    CatalogRevisionToken,
    capture_catalog_revision,
    revalidate_catalog_revision,
)
from study_app.core.oj_history import build_oj_history
from study_app.data import database
from study_app.data import oj_repository


RECORD_FIELDS = (
    "date",
    "subject",
    "module",
    "topic",
    "activity",
    "source",
    "score",
    "duration_minutes",
    "note",
    "problems",
)
PROBLEM_REVIEW_FIELDS = frozenset(
    {
        "title",
        "statement",
        "answer_result",
        "status",
        "correctness",
        "partial_credit",
        "error_cause",
        "error_category",
        "related_topics",
        "difficulty",
        "difficulty_score",
        "difficulty_source",
        "difficulty_confidence",
        "difficulty_reasons",
    }
)
PROBLEM_DTO_FIELDS = PROBLEM_REVIEW_FIELDS | frozenset(
    {"statement_source", "parser_source", "attachment_sources"}
)
RECOGNITION_INPUT_FIELDS = frozenset(
    {
        "answer_result",
        "error_cause",
        "errors",
        "free_context",
        "problem_count",
        "problem_title",
        "problem_titles",
    }
)
ACTIVITIES = frozenset(
    {"review", "exercise", "class", "review_exercise", "self_test"}
)
SOURCES = frozenset({"outside_class", "classroom", "self_study"})
_MAX_PREVIEW_AGE_SECONDS = 30 * 60


class StaleRecordPreviewError(ValueError):
    """A reviewed record no longer matches the inputs or catalog it previewed."""


@dataclass(frozen=True)
class _AttachmentSnapshot:
    file_id: str
    path: Path
    file_name: str
    file_ext: str
    file_size: int
    modified_ns: int
    changed_ns: int
    inode: int

    def dto(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "file_ext": self.file_ext,
            "file_size": self.file_size,
        }

    def record_value(self) -> dict[str, Any]:
        return {
            "file_path": str(self.path),
            "file_name": self.file_name,
            "file_ext": self.file_ext,
            "file_size": self.file_size,
        }


@dataclass
class _RecordPreview:
    draft: dict[str, Any]
    attachments: tuple[_AttachmentSnapshot, ...]
    catalog_revision: CatalogRevisionToken | None
    recognition_mode: str
    created_at: float
    saving: bool = False


@dataclass(frozen=True)
class _SearchFileReference:
    path: Path
    file_name: str
    file_ext: str
    file_size: int
    modified_ns: int
    changed_ns: int
    inode: int
    created_at: float


class _DatabaseService:
    """Shared initialization and path handling for the Qt-independent services."""

    def __init__(
        self,
        db_path: Path | str,
        model_path: Path | str | None,
        records_path: Path | str | None,
        today: date | str | None,
        initialize: Callable[[], Any] | None,
    ) -> None:
        self.db_path = Path(db_path)
        self.model_path = None if model_path is None else Path(model_path)
        self.records_path = None if records_path is None else Path(records_path)
        self.today = today
        self._initialize = initialize
        self._initialized = False
        self._initialization_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        if self._initialize is None or self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            self._initialize()
            self._initialized = True

    def _today_text(self) -> str:
        value = self.today
        if value is None:
            return date.today().isoformat()
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            return value
        raise ValueError("today 必须是日期、ISO 日期字符串或 null")

    def _topic_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "topic_key": item.topic_key,
                "subject_name": item.subject_name,
                "module_name": item.module_name,
                "topic_name": item.topic_name,
            }
            for item in database.list_topic_identities(self.db_path)
        ]

    @staticmethod
    def _safe_filename(value: Any) -> str:
        text = str(value or "")
        return PureWindowsPath(text).name if "\\" in text else Path(text).name


class RecordsService(_DatabaseService):
    """Read, preview, and explicitly save learning records without importing Qt."""

    def __init__(
        self,
        db_path: Path | str,
        model_path: Path | str | None = None,
        records_path: Path | str | None = None,
        today: date | str | None = None,
        initialize: Callable[[], Any] | None = None,
        *,
        recognition_provider: Callable[[dict[str, Any]], Any] | None = None,
        attachment_extractor: Callable[[list[Path]], Any] | None = None,
        settings_loader: Callable[[], Any] | None = None,
        file_searcher: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(db_path, model_path, records_path, today, initialize)
        self._recognition_provider = recognition_provider
        self._attachment_extractor = attachment_extractor
        self._settings_loader = settings_loader
        self._file_searcher = file_searcher
        self._previews: dict[str, _RecordPreview] = {}
        self._search_files: dict[str, _SearchFileReference] = {}
        self._previews_lock = threading.RLock()

    def search_files(self, query: str) -> dict[str, Any]:
        """Search configured local roots and return path-free, temporary handles."""
        self._ensure_initialized()
        if not isinstance(query, str) or not query.strip():
            raise ValueError("请先输入文件描述")
        query = query.strip()[:200]
        query_dto = Path(query).name if Path(query).is_absolute() else query
        try:
            from study_app.data.file_locator import DEFAULT_SEARCH_ROOTS, locate_files

            roots_value = database.get_setting("file_search_roots", [], self.db_path) or []
            roots = [Path(str(item)).expanduser() for item in roots_value if str(item).strip()]
            roots = roots or list(DEFAULT_SEARCH_ROOTS)
            matches = (
                self._file_searcher(query, roots=roots)
                if self._file_searcher is not None
                else locate_files(query, roots=roots)
            )
        except Exception:
            # Do not pass filesystem roots or exception details to the GUI.
            return {"query": query_dto, "files": [], "warnings": ["文件搜索暂不可用。"]}
        files = []
        now = time.monotonic()
        with self._previews_lock:
            self._purge_search_files(now)
            for match in list(matches or [])[:80]:
                try:
                    path = Path(getattr(match, "path", match)).expanduser().resolve()
                except (OSError, TypeError, ValueError):
                    continue
                try:
                    stat = path.stat()
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                file_token = secrets.token_urlsafe(24)
                file_name = path.name
                file_ext = path.suffix.lower()
                self._search_files[file_token] = _SearchFileReference(
                    path=path,
                    file_name=file_name,
                    file_ext=file_ext,
                    file_size=int(stat.st_size),
                    modified_ns=int(stat.st_mtime_ns),
                    changed_ns=int(stat.st_ctime_ns),
                    inode=int(getattr(stat, "st_ino", 0)),
                    created_at=now,
                )
                files.append(
                    {
                        "file_token": file_token,
                        "file_name": file_name,
                        "file_ext": file_ext,
                        "file_size": int(stat.st_size),
                        "score": int(getattr(match, "score", 0) or 0),
                        "reason": "匹配文件描述",
                    }
                )
        return {"query": query_dto, "files": files, "warnings": []}

    def load(self, limit: int = 50) -> dict[str, Any]:
        """Return the persisted subject/topic catalog and recent records."""
        self._ensure_initialized()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit 必须是 1-500 的整数")
        subjects = database.list_subject_names(self.db_path)
        modules = [
            {"subject_name": subject, "module_name": module}
            for subject in subjects
            for module in database.list_module_names(subject, self.db_path)
        ]
        catalog = {
            "subjects": [{"name": name} for name in subjects],
            "modules": modules,
            "topics": self._topic_rows(),
        }
        records = [
            self._record_list_dto(item)
            for item in database.list_recent_records(self.db_path, limit=limit)
        ]
        return {"catalog": catalog, "records": records}

    def preview(
        self,
        payload: dict[str, Any],
        recognition: str = "local",
    ) -> dict[str, Any]:
        """Build a reviewable draft; no database writes occur in this method.

        ``recognition`` is deliberately opt-in. ``local`` never loads an LLM
        setting or calls a network provider. Passing ``enhanced`` is an explicit
        request and is still gated by the saved feature configuration.
        """
        self._ensure_initialized()
        if not isinstance(payload, dict):
            raise ValueError("preview payload 必须是对象")
        if recognition not in {"manual", "local", "enhanced"}:
            raise ValueError("recognition 必须为 manual、local 或 enhanced")
        if "search_file_tokens" in payload:
            search_attachments = self._resolve_search_file_tokens(payload["search_file_tokens"])
        else:
            search_attachments = []
        attachment_values = payload.get("attachments", [])
        if not isinstance(attachment_values, list):
            raise ValueError("attachments 必须是路径列表")
        attachments = self._snapshot_attachments([*attachment_values, *search_attachments])
        record_payload = {key: value for key, value in payload.items() if key != "search_file_tokens"}
        if recognition == "manual":
            attachment_text = ""
            attachment_warnings: list[str] = []
            blocks: list[Any] = []
        else:
            _extracted_items, attachment_text, attachment_warnings = self._extract_attachments(
                [item.path for item in attachments]
            )
            try:
                blocks = self._problem_blocks(attachment_text)
            except Exception:
                blocks = []
                attachment_warnings.append("本地题面结构解析失败；可手动编辑草稿。")
        draft = self._build_draft(record_payload, blocks, attachments, recognition, attachment_text)
        enhanced_configuration_failed = False
        try:
            enhanced_available = (
                self._enhanced_available(has_attachments=bool(attachments))
                if recognition == "enhanced" else False
            )
        except Exception:
            enhanced_available = False
            enhanced_configuration_failed = recognition == "enhanced"
        actual_mode = "manual" if recognition == "manual" else "local"
        warnings = list(attachment_warnings)

        if recognition == "enhanced":
            if enhanced_configuration_failed:
                warnings.append("LLM 增强配置暂不可读取；本次仅采用本地识别。")
            elif not enhanced_available:
                warnings.append("LLM 增强识别未启用；本次仅采用本地识别。")
            else:
                try:
                    llm_payload = self._llm_payload(payload, attachment_text, attachments)
                    problems = self._run_recognition(llm_payload)
                    if problems:
                        from study_app.core.record_draft_inference import (
                            classify_error_category,
                            infer_problem_fields,
                        )

                        context = self._inference_record_context(draft)
                        reviewed = []
                        for raw in problems:
                            problem = self._problem_dto(raw)
                            problem.setdefault("attachment_sources", [item.file_name for item in attachments])
                            problem.setdefault("error_category", classify_error_category(problem.get("error_cause", "")))
                            reviewed.append(self._problem_dto(infer_problem_fields(problem, context)))
                        draft["problems"] = reviewed
                        actual_mode = "enhanced"
                except Exception:
                    # Provider errors may contain endpoint or credential details.
                    # Keep the DTO diagnostic safe and retain the local draft.
                    warnings.append("LLM 增强识别失败；本次保留本地识别结果。")

        self._validate_record_draft(draft)
        if draft.get("score") is None and recognition != "manual":
            from study_app.core.record_draft_inference import estimate_record_score_from_problems

            draft["score"] = estimate_record_score_from_problems(
                draft["problems"],
                self._inference_record_context(draft),
                self.model_path,
                use_default_model=False,
            )
        revision = capture_catalog_revision(self.db_path)
        token = secrets.token_urlsafe(32)
        preview = _RecordPreview(
            draft=deepcopy(draft),
            attachments=attachments,
            catalog_revision=revision,
            recognition_mode=actual_mode,
            created_at=time.monotonic(),
        )
        with self._previews_lock:
            self._purge_expired_previews()
            self._previews[token] = preview
        return {
            "token": token,
            "draft": deepcopy(draft),
            "files": [item.dto() for item in attachments],
            "warnings": warnings,
            "recognition": {
                "mode": actual_mode,
                "enhanced_available": enhanced_available,
                "evidence": {
                    "source": "llm" if actual_mode == "enhanced" else actual_mode,
                    "attachment_count": len(attachments),
                    "problem_blocks": len(blocks),
                    "extracted_text": bool(attachment_text),
                },
            },
        }

    def save(self, token: str, reviewed_fields: dict[str, Any]) -> dict[str, Any]:
        """Persist a previously previewed record after the user reviewed it."""
        self._ensure_initialized()
        if not isinstance(token, str) or not token:
            raise ValueError("preview token 缺失")
        if not isinstance(reviewed_fields, dict):
            raise ValueError("reviewed_fields 必须是对象")
        with self._previews_lock:
            self._purge_expired_previews()
            preview = self._previews.get(token)
            if preview is None:
                raise StaleRecordPreviewError("预览已失效，请重新预览后再保存。")
            if preview.saving:
                raise ValueError("该记录正在保存，请等待本次操作完成。")
            preview.saving = True
        try:
            return self._save_preview(token, preview, reviewed_fields)
        finally:
            with self._previews_lock:
                if self._previews.get(token) is preview:
                    preview.saving = False

    def _save_preview(
        self,
        token: str,
        preview: _RecordPreview,
        reviewed_fields: dict[str, Any],
    ) -> dict[str, Any]:
        if any(key in reviewed_fields for key in ("attachments", "files", "file_ids")):
            raise ValueError("附件在预览后不可替换；如需更改附件，请重新预览。")
        allowed = set(RECORD_FIELDS) | set(RECOGNITION_INPUT_FIELDS)
        unknown = sorted(set(reviewed_fields) - allowed)
        if unknown:
            raise ValueError("reviewed_fields 含不支持的字段：" + ", ".join(unknown))
        for field in (*RECORD_FIELDS, *RECOGNITION_INPUT_FIELDS):
            if field == "problems" or field not in reviewed_fields:
                continue
            if reviewed_fields[field] != preview.draft.get(field):
                with self._previews_lock:
                    self._previews.pop(token, None)
                raise StaleRecordPreviewError(
                    f"{field} 在预览后发生变化，请重新预览再保存。"
                )

        try:
            self._assert_attachments_unchanged(preview.attachments)
            revalidate_catalog_revision(preview.catalog_revision)
        except Exception as error:
            with self._previews_lock:
                self._previews.pop(token, None)
            if isinstance(error, StaleRecordPreviewError):
                raise
            raise StaleRecordPreviewError("目录或附件已变化，请重新预览后再保存。") from error

        record = deepcopy(preview.draft)
        record["problems"] = self._reviewed_problems(
            reviewed_fields.get("problems", preview.draft["problems"]),
            preview.draft["problems"],
        )
        record.pop("answer_result", None)
        record.pop("error_cause", None)
        record.pop("errors", None)
        record.pop("free_context", None)
        record.pop("problem_title", None)
        record.pop("problem_titles", None)
        record.pop("problem_count", None)
        record["attachments"] = [item.record_value() for item in preview.attachments]
        self._validate_record_draft(record)
        # Consume the token before the repository write so duplicate concurrent
        # submissions cannot insert the same reviewed record twice.
        with self._previews_lock:
            if self._previews.get(token) is not preview:
                raise StaleRecordPreviewError("预览已失效，请重新预览后再保存。")
            self._previews.pop(token, None)
        add_kwargs: dict[str, Any] = {"db_path": self.db_path}
        # When a test/isolated service omits model_path, never let the legacy
        # repository silently fall back to the user's global model file.
        add_kwargs["model_path"] = self.model_path or self.db_path.with_name(
            f"{self.db_path.stem}.model.json"
        )
        refresh_warning = False
        try:
            record_id = database.add_learning_record(record, **add_kwargs)
        except database.LearningRecordPostCommitError as error:
            # The repository identifies its committed primary transaction;
            # no read-back is needed to establish that saving succeeded.
            record_id = error.record_id
            refresh_warning = True
        saved = {**record, "id": record_id}
        result = {"saved": True, "recordId": record_id}
        try:
            result["record"] = self._record_dto(saved)
        except Exception:
            refresh_warning = True
        if refresh_warning:
            result["refreshWarning"] = "学习记录已保存；后续处理或刷新失败，请勿重复提交。"
        return result

    def _build_draft(
        self,
        payload: dict[str, Any],
        blocks: list[Any],
        attachments: tuple[_AttachmentSnapshot, ...],
        recognition: str,
        attachment_text: str = "",
    ) -> dict[str, Any]:
        allowed = set(RECORD_FIELDS) | set(RECOGNITION_INPUT_FIELDS) | {"attachments"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError("record payload 含不支持的字段：" + ", ".join(unknown))
        errors = payload.get("error_cause", payload.get("errors", ""))
        if isinstance(errors, list):
            errors = "；".join(str(item) for item in errors if item)
        if errors is None:
            errors = ""
        if not isinstance(errors, str):
            raise ValueError("errors 必须是字符串或字符串列表")
        titles = payload.get("problem_titles", [])
        if isinstance(titles, str):
            titles = [item.strip() for item in titles.replace("\n", ",").split(",") if item.strip()]
        if not isinstance(titles, list) or any(not isinstance(item, str) for item in titles):
            raise ValueError("problem_titles 必须是字符串列表")
        count = payload.get("problem_count")
        if isinstance(count, str) and count.strip().isascii() and count.strip().isdigit():
            count = int(count.strip())
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 80
        ):
            raise ValueError("problem_count 必须为 0-80 的整数")
        normalized_payload = {
            **payload,
            "errors": deepcopy(payload.get("errors", errors)),
            "error_cause": errors.strip(),
            "problem_titles": [item.strip() for item in titles if item.strip()],
            "problem_count": count,
        }
        if payload.get("problems") is not None:
            problems_value = payload["problems"]
            if not isinstance(problems_value, list):
                raise ValueError("problems 必须是对象列表")
            problems = [self._problem_dto(item) for item in problems_value]
        elif recognition == "manual":
            problems = self._manual_problems(normalized_payload, attachments)
        else:
            problems = self._local_problems(normalized_payload, blocks, attachments, attachment_text)
        draft = {
            "date": payload.get("date") or self._today_text(),
            "subject": payload.get("subject", ""),
            "module": payload.get("module") or "",
            "topic": payload.get("topic") or "",
            "activity": payload.get("activity") or "review",
            "source": payload.get("source") or "outside_class",
            "score": payload.get("score"),
            "duration_minutes": payload.get("duration_minutes"),
            "note": payload.get("note") or "",
            "problems": problems,
            "answer_result": str(payload.get("answer_result") or "").strip(),
            "error_cause": errors.strip(),
            "errors": deepcopy(payload.get("errors", errors)),
            "free_context": str(payload.get("free_context") or "").strip(),
            "problem_title": str(payload.get("problem_title") or "").strip(),
            "problem_titles": [item.strip() for item in titles if item.strip()],
            "problem_count": count,
        }
        return draft

    def _manual_problems(
        self,
        payload: dict[str, Any],
        attachments: tuple[_AttachmentSnapshot, ...],
    ) -> list[dict[str, Any]]:
        titles = list(payload.get("problem_titles") or [])
        if payload.get("problem_title"):
            titles.insert(0, str(payload["problem_title"]).strip())
        if not titles and payload.get("problem_count"):
            titles = [f"第{index + 1}题" for index in range(payload["problem_count"])]
        context = str(payload.get("free_context") or "").strip()
        answer_result = str(payload.get("answer_result") or "").strip()
        error_cause = payload.get("error_cause", payload.get("errors", ""))
        if isinstance(error_cause, list):
            error_cause = "；".join(str(item) for item in error_cause if item)
        if not titles and (context or answer_result or error_cause):
            titles = [str(payload.get("topic") or "").strip() or "手动记录题目"]
        file_names = [item.file_name for item in attachments]
        candidates = [
            {
                "title": title,
                "statement": context,
                "statement_source": "manual",
                "parser_source": "manual",
                "attachment_sources": file_names,
                "related_topics": [str(payload.get("topic") or "").strip()]
                if str(payload.get("topic") or "").strip() else [],
                "answer_result": answer_result if len(titles) == 1 else "",
                "error_cause": str(error_cause or "") if len(titles) == 1 else "",
            }
            for title in titles[:80]
        ]
        # A manual preview only shapes fields the user supplied. Do not run
        # title inference, correctness classification, or difficulty scoring
        # until the user reviews and explicitly saves the draft.
        return [self._problem_dto(item) for item in candidates]

    def _local_problems(
        self,
        payload: dict[str, Any],
        blocks: list[Any],
        attachments: tuple[_AttachmentSnapshot, ...],
        attachment_text: str = "",
    ) -> list[dict[str, Any]]:
        from study_app.ai.attachment_parser import AttachmentProblemBlock
        from study_app.core.record_draft_inference import infer_problems_from_inputs

        note = str(payload.get("note") or "").strip()
        free_context = str(payload.get("free_context") or "").strip()
        if free_context:
            note = "\n".join(value for value in (note, free_context) if value)
        problem_count = payload.get("problem_count")
        if problem_count and not any(marker in note for marker in ("题", "第")):
            note = "\n".join(value for value in (note, f"做了 {problem_count} 题") if value)
        answer_result = str(payload.get("answer_result") or "").strip()
        error_cause = payload.get("error_cause", payload.get("errors", ""))
        if isinstance(error_cause, list):
            error_cause = "；".join(str(item) for item in error_cause if item)
        error_cause = str(error_cause or "").strip()
        supplied_titles = list(payload.get("problem_titles") or [])
        if payload.get("problem_title"):
            supplied_titles.insert(0, str(payload["problem_title"]).strip())
        supplied_titles = list(dict.fromkeys(item for item in supplied_titles if item))
        if supplied_titles:
            blocks = [
                AttachmentProblemBlock(title=title, statement=free_context, source="manual")
                for title in supplied_titles
            ]
        context = self._inference_record_context(payload)
        inferred = infer_problems_from_inputs(
            note=note,
            answer_result=answer_result,
            error_cause=error_cause,
            topic=str(payload.get("topic") or ""),
            module=str(payload.get("module") or ""),
            attachments=[item.path for item in attachments],
            extracted_text=attachment_text,
            problem_blocks=blocks,
            record_context=context,
        )
        return [self._problem_dto(item) for item in inferred]

    @staticmethod
    def _inference_record_context(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject": str(record.get("subject") or "").strip(),
            "module": str(record.get("module") or "").strip(),
            "topic": str(record.get("topic") or "").strip(),
            "note": str(record.get("note") or record.get("free_context") or "").strip(),
        }

    def _validate_record_draft(self, record: dict[str, Any]) -> None:
        from study_app.data.database import _optional_float, validate_calendar_date
        from study_app.data.text_integrity import validate_text_integrity

        if not isinstance(record.get("subject"), str) or not record["subject"].strip():
            raise ValueError("record.subject is required")
        subject = record["subject"].strip()
        record["subject"] = subject
        if subject not in database.list_subject_names(self.db_path):
            raise ValueError(f"未找到学科：{subject}")
        module = record.get("module") or None
        if module is not None and not isinstance(module, str):
            raise ValueError("record.module 必须是字符串")
        module = module.strip() if module else None
        record["module"] = module or ""
        if module is not None and module not in database.list_module_names(subject, self.db_path):
            raise ValueError(f"学科 {subject} 下未找到模块：{module}")
        if not isinstance(record.get("topic"), str):
            raise ValueError("record.topic 必须是字符串")
        record["topic"] = record["topic"].strip()
        record["date"] = validate_calendar_date(record.get("date"), label="record.date")
        if record.get("activity") not in ACTIVITIES:
            raise ValueError(f"activity 必须是 {sorted(ACTIVITIES)} 之一")
        if record.get("source") not in SOURCES:
            raise ValueError(f"source 必须是 {sorted(SOURCES)} 之一")
        score = _optional_float(record.get("score"), label="record.score")
        duration = _optional_float(record.get("duration_minutes"), label="record.duration_minutes")
        if score is not None and score > 100:
            raise ValueError("record.score 必须在 0-100 范围内")
        if duration is not None and duration <= 0:
            raise ValueError("record.duration_minutes 必须大于 0")
        if not isinstance(record.get("note"), str):
            raise ValueError("record.note 必须是字符串")
        problems = record.get("problems")
        if not isinstance(problems, list):
            raise ValueError("problems 必须是对象列表")
        for problem in problems:
            if not isinstance(problem, dict):
                raise ValueError("problems 中的每一项都必须是对象")
            database._validate_problem_difficulty_fields(problem)
        validate_text_integrity(
            {key: record[key] for key in RECORD_FIELDS if key in record},
            context="学习记录预览",
        )

    @staticmethod
    def _problem_dto(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("problem 必须是对象")
        result = {key: deepcopy(item) for key, item in value.items() if key in PROBLEM_DTO_FIELDS}
        title = result.get("title") or value.get("name") or "未命名题目"
        result["title"] = str(title).strip()[:300] or "未命名题目"
        result["statement"] = str(result.get("statement") or "")[:12000]
        result["error_cause"] = str(result.get("error_cause") or "")[:4000]
        for field in (
            "answer_result",
            "status",
            "error_category",
            "difficulty",
            "difficulty_source",
            "statement_source",
            "parser_source",
        ):
            if field in result and result[field] is not None:
                if not isinstance(result[field], str):
                    raise ValueError(f"problem.{field} 必须是字符串")
        for field in ("difficulty_confidence",):
            if field in result and result[field] is not None:
                if isinstance(result[field], bool) or not isinstance(result[field], (int, float)):
                    raise ValueError(f"problem.{field} 必须是数值")
        if "difficulty_reasons" in result:
            reasons = result["difficulty_reasons"]
            if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
                raise ValueError("difficulty_reasons 必须是字符串数组")
        if "related_topics" in result:
            topics = result["related_topics"]
            if not isinstance(topics, list) or any(not isinstance(item, str) for item in topics):
                raise ValueError("related_topics 必须是字符串数组")
            result["related_topics"] = [item.strip() for item in topics if item.strip()]
        if "attachment_sources" in result:
            names = result["attachment_sources"]
            if not isinstance(names, list) or any(not isinstance(item, str) for item in names):
                raise ValueError("attachment_sources 必须是字符串数组")
            result["attachment_sources"] = [RecordsService._safe_filename(item) for item in names]
        return result

    def _reviewed_problems(
        self,
        value: Any,
        original: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ValueError("reviewed problems 必须是对象列表")
        reviewed: list[dict[str, Any]] = []
        for index, raw in enumerate(value):
            if not isinstance(raw, dict):
                raise ValueError(f"reviewed problems[{index}] 必须是对象")
            unknown = sorted(set(raw) - PROBLEM_DTO_FIELDS)
            if unknown:
                raise ValueError(
                    f"reviewed problems[{index}] 含不支持的字段：" + ", ".join(unknown)
                )
            item = self._problem_dto(raw)
            previous = original[index] if index < len(original) else {}
            # Provenance is owned by the preview. The client may edit what the
            # user reviewed, while source labels and file references stay fixed.
            for field in ("statement_source", "parser_source", "attachment_sources"):
                if field in previous:
                    item[field] = deepcopy(previous[field])
                else:
                    item.pop(field, None)
            item.setdefault("parser_source", "manual")
            reviewed.append(item)
        from study_app.core.learning_assistant_records import normalize_homework_problems

        return [
            self._problem_dto(item)
            for item in normalize_homework_problems(
                reviewed, claim_all_correct=False, independence="unknown"
            )
        ]

    def _snapshot_attachments(self, value: Any) -> tuple[_AttachmentSnapshot, ...]:
        if not isinstance(value, list):
            raise ValueError("attachments 必须是路径列表")
        snapshots = []
        seen = set()
        for index, raw in enumerate(value):
            if isinstance(raw, str):
                raw_path = raw
            elif isinstance(raw, dict):
                raw_path = raw.get("path", raw.get("file_path"))
            else:
                raise ValueError(f"attachments[{index}] 必须是文件路径或对象")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError(f"attachments[{index}] 缺少本地文件路径")
            try:
                path = Path(raw_path).expanduser().resolve()
            except (OSError, TypeError, ValueError) as error:
                raise ValueError(f"附件不可读取（第 {index + 1} 个文件）") from error
            try:
                stat = path.stat()
            except OSError as error:
                raise ValueError(f"附件不可读取（第 {index + 1} 个文件）") from error
            if not path.is_file():
                raise ValueError(f"附件不是有效文件（第 {index + 1} 个文件）")
            identity = str(path).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            snapshots.append(
                _AttachmentSnapshot(
                    file_id=secrets.token_urlsafe(12),
                    path=path,
                    file_name=path.name,
                    file_ext=path.suffix.lower(),
                    file_size=int(stat.st_size),
                    modified_ns=int(stat.st_mtime_ns),
                    changed_ns=int(stat.st_ctime_ns),
                    inode=int(getattr(stat, "st_ino", 0)),
                )
            )
        return tuple(snapshots)

    def _resolve_search_file_tokens(self, value: Any) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("search_file_tokens 必须是文件令牌列表")
        if len(value) > 80:
            raise ValueError("一次最多选择 80 个搜索文件")
        paths: list[str] = []
        now = time.monotonic()
        with self._previews_lock:
            self._purge_search_files(now)
            for token in value:
                reference = self._search_files.get(token)
                if reference is None:
                    raise StaleRecordPreviewError("文件搜索结果已失效，请重新搜索并选择文件。")
                try:
                    stat = reference.path.stat()
                except OSError as error:
                    self._search_files.pop(token, None)
                    raise StaleRecordPreviewError("搜索文件已移动或删除，请重新搜索。") from error
                if (
                    not reference.path.is_file()
                    or int(stat.st_size) != reference.file_size
                    or int(stat.st_mtime_ns) != reference.modified_ns
                    or int(stat.st_ctime_ns) != reference.changed_ns
                    or int(getattr(stat, "st_ino", 0)) != reference.inode
                ):
                    self._search_files.pop(token, None)
                    raise StaleRecordPreviewError("搜索文件已变化，请重新搜索。")
                paths.append(str(reference.path))
        return paths

    def _purge_search_files(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        expired = [
            token
            for token, reference in self._search_files.items()
            if now - reference.created_at > _MAX_PREVIEW_AGE_SECONDS
        ]
        for token in expired:
            self._search_files.pop(token, None)

    def _assert_attachments_unchanged(
        self, snapshots: tuple[_AttachmentSnapshot, ...]
    ) -> None:
        for item in snapshots:
            try:
                stat = item.path.stat()
            except OSError as error:
                raise StaleRecordPreviewError("预览附件已移动或删除，请重新预览。") from error
            if (
                not item.path.is_file()
                or int(stat.st_size) != item.file_size
                or int(stat.st_mtime_ns) != item.modified_ns
                or int(stat.st_ctime_ns) != item.changed_ns
                or int(getattr(stat, "st_ino", 0)) != item.inode
            ):
                raise StaleRecordPreviewError("预览附件已变化，请重新预览。")

    def _extract_attachments(self, paths: list[Path]) -> tuple[list[Any], str, list[str]]:
        if not paths:
            return [], "", []
        try:
            raw_extracted_text = False
            if self._attachment_extractor is None:
                from study_app.data.text_extractor import extract_text_from_files

                items = extract_text_from_files(paths)
            else:
                extracted = self._attachment_extractor(paths)
                if isinstance(extracted, str):
                    items = []
                    combined = extracted
                    raw_extracted_text = True
                else:
                    items = list(extracted or [])
            if not raw_extracted_text:
                from study_app.data.text_extractor import is_usable_text

                combined = "\n\n".join(
                    f"[{Path(getattr(item, 'path', '附件')).name}]\n{item.text}"
                    for item in items
                    if getattr(item, "text", "")
                    and (
                        not getattr(item, "warning", "")
                        or is_usable_text(getattr(item, "text", ""))
                    )
                )
        except Exception:
            return [], "", ["附件文本提取失败；请检查文件或重新选择材料。"]
        warnings = [
            f"{Path(getattr(item, 'path', '附件')).name or '附件'}：文本提取不可用。"
            for item in items
            if getattr(item, "warning", "")
        ]
        sanitized_text = str(combined or "")
        for path in paths:
            private_path = str(path.resolve())
            for variant in {private_path, private_path.replace("/", "\\"), private_path.replace("\\", "/")}:
                sanitized_text = re.sub(
                    re.escape(variant),
                    path.name,
                    sanitized_text,
                    flags=re.IGNORECASE,
                )
        return items, sanitized_text[:20000], warnings

    @staticmethod
    def _problem_blocks(text: str) -> list[Any]:
        from study_app.ai.attachment_parser import parse_problem_blocks_from_text

        return parse_problem_blocks_from_text(text, limit=80)

    def _enhanced_available(self, *, has_attachments: bool) -> bool:
        settings = self._load_settings()
        if settings is None:
            return False
        enabled_features = set(getattr(settings, "enabled_features", ()) or ())
        return bool(
            getattr(settings, "is_cloud_enabled", False)
            and (
                "record_parser" in enabled_features
                or (has_attachments and "attachment_parser" in enabled_features)
            )
        )

    def _load_settings(self) -> Any:
        if self._settings_loader is not None:
            return self._call_with_db_path(self._settings_loader)
        from study_app.ai.providers import load_llm_settings

        return self._call_with_db_path(load_llm_settings)

    def _run_recognition(self, payload: dict[str, Any]) -> Any:
        if self._recognition_provider is not None:
            return self._call_with_db_path(self._recognition_provider, payload)
        from study_app.ai.record_parser import parse_record_payload_with_llm

        return self._call_with_db_path(parse_record_payload_with_llm, payload)

    def _call_with_db_path(self, function: Callable[..., Any], *args: Any) -> Any:
        try:
            parameters = inspect.signature(function).parameters.values()
            accepts_db_path = any(
                parameter.name == "db_path" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_db_path = False
        if accepts_db_path:
            return function(*args, db_path=self.db_path)
        return function(*args)

    @staticmethod
    def _llm_payload(
        payload: dict[str, Any],
        attachment_text: str,
        attachments: tuple[_AttachmentSnapshot, ...],
    ) -> dict[str, Any]:
        return {
            "subject": str(payload.get("subject") or "").strip(),
            "module": str(payload.get("module") or "").strip(),
            "topic": str(payload.get("topic") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
            "answer_result": str(payload.get("answer_result") or "").strip(),
            "error_cause": str(payload.get("error_cause", payload.get("errors")) or "").strip(),
            "attachment_text": attachment_text[:12000],
            "attachments": [item.file_name for item in attachments],
        }

    def _purge_expired_previews(self) -> None:
        now = time.monotonic()
        expired = [
            token
            for token, item in self._previews.items()
            if now - item.created_at > _MAX_PREVIEW_AGE_SECONDS
        ]
        for token in expired:
            self._previews.pop(token, None)

    @classmethod
    def _record_list_dto(cls, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(value["id"]),
            "date": str(value.get("record_date") or ""),
            "subject": str(value.get("subject_name") or ""),
            "module": str(value.get("module_name") or ""),
            "topic": str(value.get("topic_name") or ""),
            "activity": str(value.get("activity") or ""),
            "source": str(value.get("source") or ""),
            "score": value.get("score"),
            "duration_minutes": value.get("duration_minutes"),
            "note": str(value.get("note") or ""),
            "problem_count": int(value.get("problem_count") or 0),
            "attachments": [
                {
                    "file_name": cls._safe_filename(item.get("file_name") or ""),
                    "file_ext": str(item.get("file_ext") or ""),
                    "file_size": item.get("file_size"),
                }
                for item in value.get("attachments", [])
            ],
        }

    @classmethod
    def _record_dto(cls, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(value["id"]),
            "date": str(value.get("date") or ""),
            "subject": str(value.get("subject") or ""),
            "module": str(value.get("module") or ""),
            "topic": str(value.get("topic") or ""),
            "activity": str(value.get("activity") or ""),
            "source": str(value.get("source") or ""),
            "score": value.get("score"),
            "duration_minutes": value.get("duration_minutes"),
            "note": str(value.get("note") or ""),
            "problems": [cls._problem_dto(item) for item in value.get("problems", [])],
            "attachments": [
                {
                    "file_name": cls._safe_filename(
                        item.get("file_name") or item.get("file_path") or ""
                    ),
                    "file_ext": str(item.get("file_ext") or Path(item.get("file_path") or "").suffix.lower()),
                    "file_size": item.get("file_size"),
                }
                for item in value.get("attachments", [])
            ],
        }


class OJService(_DatabaseService):
    """Qt-independent OJ problem, explicit topic mapping, and attempt service."""

    def __init__(
        self,
        db_path: Path | str,
        model_path: Path | str | None = None,
        records_path: Path | str | None = None,
        today: date | str | None = None,
        initialize: Callable[[], Any] | None = None,
        *,
        problem_reader: Callable[..., Any] | None = None,
        mapping_reader: Callable[..., Any] | None = None,
        attempt_reader: Callable[..., Any] | None = None,
        topic_reader: Callable[..., Any] | None = None,
        problem_writer: Callable[..., Any] | None = None,
        mapping_writer: Callable[..., Any] | None = None,
        attempt_writer: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(db_path, model_path, records_path, today, initialize)
        self._problem_reader = problem_reader or oj_repository.list_oj_problems
        self._mapping_reader = mapping_reader or oj_repository.list_oj_problem_topics
        self._attempt_reader = attempt_reader or oj_repository.list_oj_attempts
        self._topic_reader = topic_reader or database.list_topic_identities
        self._problem_writer = problem_writer or oj_repository.register_oj_problem
        self._mapping_writer = mapping_writer or oj_repository.replace_oj_problem_topics
        self._attempt_writer = attempt_writer or oj_repository.append_oj_attempt

    def load(self) -> dict[str, Any]:
        """Return the real OJ problem, mapping, attempt, and topic lists."""
        self._ensure_initialized()
        topics = [self._topic_dto(item) for item in self._topic_reader(self.db_path)]
        problems = []
        mappings = []
        attempts = []
        for problem in self._problem_reader(self.db_path):
            problem_key = problem.problem_key
            problem_mappings = self._mapping_reader(problem_key, self.db_path)
            problem_attempts = self._attempt_reader(problem_key, self.db_path)
            mappings.extend(self._mapping_dto(item) for item in problem_mappings)
            history = build_oj_history(problem_attempts)
            history_attempts = []
            for item in history.attempts:
                attempt_dto = {
                    **self._attempt_dto(item.attempt),
                    "attempt_number": item.attempt_number,
                    "is_retry": item.is_retry,
                }
                history_attempts.append(attempt_dto)
                attempts.append(attempt_dto)
            summary = history.summary
            problems.append(
                {
                    **self._problem_dto(problem),
                    "mappings": [self._mapping_dto(item) for item in problem_mappings],
                    "attempt_count": summary.attempt_count,
                    "latest_result": summary.latest_result,
                    "best_result": summary.best_result,
                    "history": {
                        "attempts": history_attempts,
                        "summary": {
                            "problem_key": summary.problem_key,
                            "attempt_count": summary.attempt_count,
                            "first_result": summary.first_result,
                            "latest_result": summary.latest_result,
                            "best_result": summary.best_result,
                            "ever_independent_accepted": summary.ever_independent_accepted,
                            "attempts_to_first_accepted": summary.attempts_to_first_accepted,
                            "recent_duration_improvement_seconds": (
                                summary.recent_duration_improvement_seconds
                            ),
                        },
                    },
                }
            )
        return {
            "topics": topics,
            "problems": problems,
            "mappings": mappings,
            "attempts": attempts,
        }

    def create_problem(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        if not isinstance(payload, dict):
            raise ValueError("problem payload 必须是对象")
        allowed = {
            "source_key", "platform", "external_problem_key", "external_id", "title",
            "source_url", "url",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError("problem payload 含不支持的字段：" + ", ".join(unknown))
        source_key = self._coalesce_alias(payload, "source_key", "platform")
        external_problem_key = self._coalesce_alias(
            payload, "external_problem_key", "external_id"
        )
        source_url = self._coalesce_alias(payload, "source_url", "url") or ""
        source_url = self._validate_public_source_url(source_url)
        problem = self._problem_writer(
            source_key,
            external_problem_key,
            payload.get("title"),
            source_url,
            db_path=self.db_path,
        )
        return {"problem": self._problem_dto(problem)}

    @staticmethod
    def _coalesce_alias(payload: dict[str, Any], primary: str, alias: str) -> Any:
        if primary in payload and alias in payload and payload[primary] != payload[alias]:
            raise ValueError(f"{primary} 与 {alias} 不得冲突")
        return payload.get(primary, payload.get(alias))

    def save_mapping(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Replace mappings only with topic identities present in the catalog."""
        self._ensure_initialized()
        if not isinstance(payload, dict):
            raise ValueError("mapping payload 必须是对象")
        allowed = {"problem_key", "topic_keys", "note"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError("mapping payload 含不支持的字段：" + ", ".join(unknown))
        topic_keys = payload.get("topic_keys")
        if not isinstance(topic_keys, list) or not topic_keys:
            raise ValueError("请显式选择至少一个知识点后再保存映射。")
        available = {item["topic_key"] for item in self._topic_rows()}
        missing = sorted(set(topic_keys) - available)
        if missing:
            raise ValueError("所选知识点不在当前目录中：" + ", ".join(missing))
        rows = self._mapping_writer(
            payload.get("problem_key"),
            topic_keys,
            mapping_source="manual",
            note=payload.get("note", ""),
            db_path=self.db_path,
        )
        return {"mappings": [self._mapping_dto(item) for item in rows]}

    def submit_attempt(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one validated attempt after an explicit topic mapping exists."""
        self._ensure_initialized()
        if not isinstance(payload, dict):
            raise ValueError("attempt payload 必须是对象")
        allowed = {
            "problem_key",
            "attempted_at",
            "result",
            "duration_seconds",
            "independence",
            "hint_level",
            "error_type",
            "notes",
            "source_attempt_key",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError("attempt payload 含不支持的字段：" + ", ".join(unknown))
        problem_key = payload.get("problem_key")
        mappings = self._mapping_reader(problem_key, self.db_path)
        if not mappings:
            raise ValueError("请先为这道 OJ 题明确选择至少一个知识点，再提交记录。")
        attempt = self._attempt_writer(
            problem_key=problem_key,
            attempted_at=payload.get("attempted_at"),
            result=payload.get("result"),
            duration_seconds=payload.get("duration_seconds"),
            independence=payload.get("independence"),
            hint_level=payload.get("hint_level"),
            error_type=payload.get("error_type"),
            notes=payload.get("notes", ""),
            source_attempt_key=payload.get("source_attempt_key"),
            db_path=self.db_path,
        )
        return {"attempt": self._attempt_dto(attempt)}

    @staticmethod
    def _topic_dto(value: Any) -> dict[str, Any]:
        return {
            "topic_key": value.topic_key,
            "subject_name": value.subject_name,
            "module_name": value.module_name,
            "topic_name": value.topic_name,
        }

    @staticmethod
    def _problem_dto(value: Any) -> dict[str, Any]:
        return {
            "problem_key": value.problem_key,
            "source_key": value.source_key,
            "external_problem_key": value.external_problem_key,
            "title": value.title,
            "source_url": OJService._public_source_url(value.source_url),
        }

    @staticmethod
    def _validate_public_source_url(value: Any) -> Any:
        if not isinstance(value, str):
            return value  # The repository applies the canonical type/length validation.
        value = value.strip()
        try:
            parts = urlsplit(value)
            query_names = {name.casefold() for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
        except ValueError as error:
            raise ValueError("source_url 格式无效") from error
        sensitive_names = {
            "api_key", "apikey", "access_token", "auth_token", "authorization",
            "credential", "password", "passwd", "secret", "token",
        }
        if (
            parts.username is not None
            or parts.password is not None
            or query_names & sensitive_names
            or parts.scheme.casefold() == "file"
            or Path(value).is_absolute()
        ):
            raise ValueError("source_url 只能是公开外部链接，不得包含用户名、密码或访问令牌")
        return value

    @staticmethod
    def _public_source_url(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            parts = urlsplit(value.strip())
            if parts.username is not None or parts.password is not None:
                return ""
            if parts.scheme.casefold() == "file" or Path(value).is_absolute():
                return ""
            sensitive_names = {
                "api_key", "apikey", "access_token", "auth_token", "authorization",
                "credential", "password", "passwd", "secret", "token",
            }
            safe_query = [
                (key, item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
                if key.casefold() not in sensitive_names
            ]
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_query), parts.fragment))
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _mapping_dto(value: Any) -> dict[str, Any]:
        return {
            "problem_key": value.problem_key,
            "topic_key": value.topic_key,
            "mapping_source": value.mapping_source,
            "note": value.note,
        }

    @staticmethod
    def _attempt_dto(value: Any) -> dict[str, Any]:
        return {
            "attempt_id": int(value.attempt_id),
            "problem_key": value.problem_key,
            "attempted_at": value.attempted_at,
            "result": value.result,
            "duration_seconds": int(value.duration_seconds),
            "independence": value.independence,
            "hint_level": value.hint_level,
            "error_type": value.error_type,
            "notes": value.notes,
            "source_attempt_key": value.source_attempt_key,
        }
