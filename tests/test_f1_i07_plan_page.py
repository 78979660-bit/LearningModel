from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from study_app.core.plan_candidates import task_id_for_source
from study_app.core.task_estimates import CONFIRMED_TEMPLATE_ESTIMATE, TaskEstimate


class BudgetPlanPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def state():
        subjects = (
            SimpleNamespace(name="A", archived=False),
            SimpleNamespace(name="B", archived=False),
        )
        todos = []
        for subject, name, priority in (("A", "A40", 0.1), ("B", "B30", 0.8), ("B", "B15", 0.1)):
            kind, source_id = "model_topic_practice", f"topic:{name}"
            todos.append(SimpleNamespace(
                kind="练习", title=name, detail="", level="normal", priority=priority,
                subject_id=subject, source_kind=kind, source_id=source_id,
                task_id=task_id_for_source(subject, kind, source_id),
            ))
        return SimpleNamespace(
            today=date(2026, 9, 16), subjects=subjects, todos=tuple(todos),
            memory_risks=(), bkt_alerts=(), low_subjects=(), stale_subjects=(),
        )

    @staticmethod
    def saved_from(plan, plan_id: int = 42, scope: str | None = None):
        rows = []
        for section, decisions in (("selected", plan.selected), ("completed", plan.completed),
                                   ("excluded", plan.excluded)):
            for decision in decisions:
                if scope and decision.subject_id != scope:
                    continue
                rows.append({
                    "id": len(rows) + 1, "section_key": section,
                    "item_text": decision.title, "task_id": decision.task_id,
                    "subject_id": decision.subject_id,
                    "estimated_minutes": decision.estimated_minutes,
                    "estimate_source": decision.estimate_source,
                    "selection_reason": {"rule_code": decision.rule_code,
                                         "reason": decision.reason,
                                         "ranking_evidence": dict(decision.ranking_evidence)},
                    "excluded_reason": decision.rule_code if section == "excluded" else None,
                    "checked": section == "completed", "result": None,
                })
        return {
            "id": plan_id, "plan_date": "2026-09-16", "budget_minutes": 60,
            "summary": {"planned_minutes": plan.planned_minutes,
                        "remaining_minutes": plan.remaining_minutes,
                        "over_budget_completed_minutes": plan.over_budget_completed_minutes,
                        "completed_occupancy_unknown": plan.completed_occupancy_unknown},
            "items": rows,
        }

    def test_budget_inputs_generate_shared_scope_result_and_survive_refresh(self) -> None:
        from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton, QSpinBox
        from study_app.ui import study_plan_page

        state = self.state()
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        saved_plan = None

        def save_result(_day, _minutes, plan):
            nonlocal saved_plan
            saved_plan = self.saved_from(plan)
            return saved_plan["id"]

        def read_result(_day, scope=None):
            if saved_plan is None:
                return None
            if scope is None:
                return saved_plan
            scoped = dict(saved_plan)
            scoped["items"] = [row for row in saved_plan["items"] if row["subject_id"] == scope]
            return scoped

        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "get_active_study_plan", return_value=None),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(study_plan_page, "get_study_day_budget", return_value=None),
            patch.object(study_plan_page, "get_subject_exam_date", return_value=None),
            patch.object(study_plan_page, "get_task_estimate", return_value=None),
            patch.object(study_plan_page, "get_budgeted_day_plan", side_effect=read_result) as get_plan,
            patch.object(study_plan_page, "save_study_day_budget") as save_budget,
            patch.object(study_plan_page, "save_subject_exam_date") as save_exam,
            patch.object(study_plan_page, "save_task_estimate") as save_estimate,
            patch.object(study_plan_page, "create_budgeted_day_plan", side_effect=save_result) as create_plan,
            patch.object(study_plan_page, "update_study_plan_item_state") as update_state,
            patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]),
            patch("PySide6.QtWidgets.QMessageBox.warning") as warning,
        ):
            page = study_plan_page.study_plan_page(state)
            try:
                content = page.widget()
                budget = content.findChild(QLineEdit, "BudgetMinutesInput")
                exams = {field.property("subjectId"): field for field in
                         content.findChildren(QLineEdit, "SubjectExamDateInput")}
                estimates = {field.property("taskId"): field for field in
                             content.findChildren(QSpinBox, "TaskEstimateInput")}
                toggle = content.findChild(QPushButton, "BudgetOptionsToggle")
                self.assertFalse(toggle.isChecked())
                self.assertTrue(content._budget_options_panel.isHidden())
                self.assertTrue(all(field.value() > 0 for field in estimates.values()))
                self.assertTrue(all("助理估时" in field.suffix() for field in estimates.values()))
                toggle.click()
                self.app.processEvents()
                self.assertFalse(content._budget_options_panel.isHidden())
                budget.setText("60")
                exams["A"].setText("2026-09-18")
                by_title = {todo.title: todo.task_id for todo in state.todos}
                for title, minutes in (("A40", 40), ("B30", 30), ("B15", 15)):
                    estimates[by_title[title]].setValue(minutes)
                button = next(item for item in content.findChildren(QPushButton)
                              if item.text() == "预览今日安排")
                button.click()
                self.app.processEvents()

                create_plan.assert_not_called()
                save_budget.assert_not_called()
                confirm = content.findChild(QPushButton, "ConfirmBudgetPlan")
                self.assertTrue(confirm.isEnabled())
                budget.setText("59")
                self.assertFalse(confirm.isEnabled())
                create_plan.assert_not_called()
                budget.setText("60")
                button.click()
                self.app.processEvents()
                self.assertTrue(confirm.isEnabled())
                confirm.click()
                self.app.processEvents()
                save_budget.assert_called_once_with("2026-09-16", 60)
                self.assertEqual(save_exam.call_count, 2)
                self.assertEqual(save_estimate.call_count, 2)
                create_plan.assert_called_once()
                self.assertEqual([item.title for item in create_plan.call_args.args[2].selected],
                                 ["A40", "B15"])
                self.assertEqual([item.title for item in create_plan.call_args.args[2].excluded],
                                 ["B30"])
                labels = [label.text() for label in content.findChildren(QLabel)]
                self.assertTrue(any("已安排 55 分钟；剩余 5 分钟" in text for text in labels))
                self.assertTrue(any("未安排 · B30" in text and "剩余" in text for text in labels))
                warning.assert_not_called()

                scope = content.findChildren(QComboBox)[0]
                scope.setCurrentIndex(1)
                self.app.processEvents()
                self.assertEqual(create_plan.call_count, 1)
                self.assertEqual(get_plan.call_args.args, ("2026-09-16", "A"))
                labels = [label.text() for label in content.findChildren(QLabel)]
                self.assertTrue(any("已安排 55 分钟；剩余 5 分钟" in text for text in labels))

                budget.setText("77")
                exams["A"].setText("2026-09-20")
                estimates[by_title["A40"]].setValue(44)
                page._set_dashboard_state(state)
                self.assertEqual(budget.text(), "77")
                self.assertEqual(exams["A"].text(), "2026-09-20")
                self.assertEqual(estimates[by_title["A40"]].value(), 44)

                checkbox = content.findChild(QCheckBox, "BudgetPlanItem")
                self.assertIsNotNone(checkbox)
                checkbox.click()
                self.app.processEvents()
                update_state.assert_called_once_with(checkbox.property("itemId"), checked=True)
            finally:
                page.deleteLater()
                self.app.processEvents()

    def test_missing_schema_is_visible_and_disables_budget_write(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton
        from study_app.ui import study_plan_page

        state = self.state()
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "get_active_study_plan", return_value=None),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(study_plan_page, "get_study_day_budget",
                         side_effect=study_plan_page.DatabaseNotInitializedError("missing")),
        ):
            page = study_plan_page.study_plan_page(state)
            try:
                content = page.widget()
                button = next(item for item in content.findChildren(QPushButton)
                              if item.text() == "预览今日安排")
                self.assertFalse(button.isEnabled())
                self.assertTrue(any("时间预算暂不可用" in label.text()
                                    for label in content.findChildren(QLabel)))
            finally:
                page.deleteLater()
                self.app.processEvents()

    def test_plan_page_shrinks_to_narrow_viewport_without_horizontal_overflow(self) -> None:
        from PySide6.QtCore import Qt
        from study_app.ui import study_plan_page

        state = self.state()
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")
        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "get_active_study_plan", return_value=None),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(
                study_plan_page,
                "get_study_day_budget",
                side_effect=study_plan_page.DatabaseNotInitializedError("missing"),
            ),
        ):
            page = study_plan_page.study_plan_page(state)
            try:
                page.resize(720, 620)
                page.show()
                self.app.processEvents()
                self.assertEqual(
                    page.horizontalScrollBarPolicy(),
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
                )
                self.assertLessEqual(page.widget().width(), page.viewport().width())
            finally:
                page.close()
                page.deleteLater()
                self.app.processEvents()

    def test_unchanged_confirmed_template_keeps_its_source(self) -> None:
        from PySide6.QtWidgets import QLineEdit, QPushButton
        from study_app.ui import study_plan_page

        state = self.state()
        template_task = state.todos[0].task_id
        phase = ModuleType("study_app.core.study_phase")
        phase.is_final_review = Mock(return_value=False)
        phase.exam_scope_label = Mock(return_value="")

        def estimate_for(candidate):
            if candidate.task_id == template_task:
                return TaskEstimate(template_task, 25, CONFIRMED_TEMPLATE_ESTIMATE)
            return None

        with (
            patch.dict(sys.modules, {"study_app.core.study_phase": phase}),
            patch.object(study_plan_page, "load_dashboard_state", return_value=state),
            patch.object(study_plan_page, "get_active_study_plan", return_value=None),
            patch.object(study_plan_page, "final_review_subject_names", return_value=()),
            patch.object(study_plan_page, "get_study_day_budget", return_value=None),
            patch.object(study_plan_page, "get_subject_exam_date", return_value=None),
            patch.object(study_plan_page, "get_task_estimate", side_effect=estimate_for),
            patch.object(study_plan_page, "get_budgeted_day_plan", return_value=None),
            patch.object(study_plan_page, "save_study_day_budget"),
            patch.object(study_plan_page, "save_subject_exam_date"),
            patch.object(study_plan_page, "save_task_estimate") as save_estimate,
            patch.object(study_plan_page, "create_budgeted_day_plan") as create_plan,
            patch.object(study_plan_page, "plan_day_feedback_details", return_value=[]),
            patch("PySide6.QtWidgets.QMessageBox.warning"),
        ):
            page = study_plan_page.study_plan_page(state)
            try:
                content = page.widget()
                content.findChild(QLineEdit, "BudgetMinutesInput").setText("100")
                next(button for button in content.findChildren(QPushButton)
                     if button.text() == "预览今日安排").click()
                self.app.processEvents()
                create_plan.assert_not_called()
                content.findChild(QPushButton, "ConfirmBudgetPlan").click()
                self.app.processEvents()
                save_estimate.assert_not_called()
                result = create_plan.call_args.args[2]
                selected = next(item for item in result.selected if item.task_id == template_task)
                self.assertEqual(selected.estimate_source, CONFIRMED_TEMPLATE_ESTIMATE)
            finally:
                page.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
