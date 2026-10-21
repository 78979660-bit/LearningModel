"""Read-only, Qt-independent projection of existing services for the homepage.

No database connections, credentials, model internals or mutation functions are
passed to QML. Call ``load`` from a worker: even initialization belongs there.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from datetime import datetime, timedelta, timezone
import logging

from study_app.core.active_subjects import dashboard_activity_subjects
from study_app.core.async_tasks import capture_catalog_revision, revalidate_catalog_revision
from study_app.core.dashboard import load_dashboard_state
from study_app.data.database import DatabaseNotInitializedError
from study_app.data.plan_repository import get_active_study_plan, get_budgeted_day_plan

LOGGER = logging.getLogger(__name__)


class DashboardService:
    def __init__(self, db_path, model_path, records_path, today=None,
                 initialize: Callable[[], None] | None = None):
        self.db_path = Path(db_path)
        self.model_path = Path(model_path)
        self.records_path = Path(records_path)
        self.today = today
        self._initialize = initialize

    def ensure_initialized(self) -> None:
        """Worker-only startup shared with the serialized QML workspace queue."""
        if self._initialize is not None:
            self._initialize()
            self._initialize = None  # retry on failure; never seed on each refresh

    def load(self) -> dict:
        self.ensure_initialized()
        token = capture_catalog_revision(self.db_path)
        state = load_dashboard_state(
            model_path=self.model_path, records_path=self.records_path,
            db_path=self.db_path, today=self.today,
        )
        active = dashboard_activity_subjects(state)
        names = {item.name for item in active}
        archived_names = {item.name for item in state.subjects if item.archived}
        todos = [item for item in state.todos
                 if item.title.partition(" /")[0].strip() not in archived_names]
        memory = [item for item in state.memory_risks if item.get("subject") in names]
        bkt = [item for item in state.bkt_alerts if item.get("subject") in names]
        snapshot = {
            "date": state.today.isoformat(),
            "updatedAt": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "rangeLabel": f"{state.start.isoformat()} 至 {state.today.isoformat()} · 周期标杆 {state.benchmark:g}",
            "source": state.data_source,
            "sourceReason": ("SQLite 当前不可用；使用 JSON 兼容记录，请在设置页检查数据。"
                             if state.data_source_reason else ""),
            "catalogDiagnostic": ("学科目录使用兼容模式；请在设置页检查目录状态。"
                                  if state.catalog_diagnostic else ""),
            "activeCount": len(active),
            "archivedCount": len(archived_names),
            "recordCount": len(state.raw_records),
            "metrics": [
                {"label": "推荐任务", "value": len(todos), "detail": "模型推荐，尚未等同已确认计划"},
                {"label": "遗忘风险", "value": len(memory), "detail": "活动学科的知识点"},
                {"label": "需要巩固", "value": len(bkt), "detail": "BKT 估计低于阈值"},
                {"label": "低分学科", "value": len(set(state.low_subjects) & names), "detail": "低于周期标杆"},
            ],
            "subjects": [
                {"name": item.name, "mastery": item.mastery_score,
                 "initialScore": item.initial_score, "windowScore": item.window_score,
                 "hasWindowRecords": item.has_window_records,
                 "coveredMastery": item.covered_mastery_score,
                 "covered": item.covered_topic_count, "total": item.total_topic_count,
                 "latestRecord": item.latest_record.isoformat() if item.latest_record else "暂无记录",
                 "latestReview": item.latest_review.isoformat() if item.latest_review else "暂无课外复习",
                 "warnings": list(item.warnings), "subjectKey": item.subject_key or ""}
                for item in active
            ],
            "todos": [{"title": item.title, "detail": item.detail, "kind": item.kind} for item in todos],
            "memoryRisks": [self._risk_row(item, "memory") for item in memory[:8]],
            "bktAlerts": [self._risk_row(item, "bkt") for item in bkt[:8]],
            "todayPlan": self._today_plan(state.today.isoformat()),
            "recentRecords": [
                {"date": str(item.get("date") or ""), "subject": str(item.get("subject") or ""),
                 "detail": str(item.get("topic") or item.get("module") or item.get("activity") or "学习记录")}
                for item in sorted(
                    (item for item in state.raw_records if item.get("subject") in names),
                    key=lambda item: (str(item.get("date") or ""), str(item.get("id") or "")),
                    reverse=True,
                )[:8]
            ],
        }
        revalidate_catalog_revision(token)
        return snapshot

    @staticmethod
    def _risk_row(item: dict, kind: str) -> dict:
        probability = item.get("recall" if kind == "memory" else "mastery_probability")
        detail = str(item.get("reason") or item.get("priority_reason") or item.get("level") or "")
        if isinstance(probability, (int, float)):
            detail = f"{'回忆' if kind == 'memory' else '掌握'}概率 {probability:.0%} · {detail}"
        return {"title": f"{item.get('subject', '')} / {item.get('topic', '')}", "detail": detail}

    def _today_plan(self, plan_date: str) -> dict:
        try:
            plan = get_budgeted_day_plan(plan_date, db_path=self.db_path)
            if plan is not None:
                rows = [item for item in plan["items"] if not item.get("excluded_reason") or item.get("checked")]
                summary = plan["summary"]
                completed = sum(bool(item.get("checked")) for item in rows)
                detail = (f"已完成 {completed}/{len(rows)} 项 · "
                          f"安排 {summary['planned_minutes']} 分钟 · 预算 {plan['budget_minutes']} 分钟 · "
                          f"余量 {summary['remaining_minutes']} 分钟")
                if summary.get("over_budget_completed_minutes"):
                    detail += f" · 已完成用时超预算 {summary['over_budget_completed_minutes']} 分钟"
                if summary.get("completed_occupancy_unknown"):
                    detail += " · 部分完成任务估时未知"
                return {"status": "confirmed", "title": "今日已确认计划", "detail": detail,
                        "budgetMinutes": plan["budget_minutes"],
                        "plannedMinutes": summary["planned_minutes"],
                        "remainingMinutes": summary["remaining_minutes"],
                        "items": [{"title": str(item["item_text"]), "checked": bool(item.get("checked")),
                                   "minutes": item.get("estimated_minutes")} for item in rows]}
            legacy = get_active_study_plan(None, db_path=self.db_path)
            if legacy is not None:
                return {"status": "legacy", "title": "已有学习计划",
                        "detail": "今日预算未确认 · 时长未知；请在学习计划页查看与调整。", "items": []}
            return {"status": "empty", "title": "暂无今日计划",
                    "detail": "可在学习计划页预览并确认；下方推荐不代表已安排。", "items": []}
        except DatabaseNotInitializedError:
            return {"status": "unavailable", "title": "计划结构尚未初始化",
                    "detail": "请使用原入口完成受支持的数据初始化后刷新。", "items": []}
        except Exception as error:
            LOGGER.warning("QML plan read failed: %s", type(error).__name__)
            return {"status": "error", "title": "今日计划暂时无法读取",
                    "detail": "其他首页数据已保留，请刷新重试或在学习计划页检查。", "items": []}
