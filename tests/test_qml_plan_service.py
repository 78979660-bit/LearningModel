from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from study_app.core.dashboard import load_dashboard_state
from study_app.core.plan_candidates import (
    CandidateCollection,
    PlanCandidate,
    build_plan_candidates,
    identify_model_topic,
)
from study_app.data.database import connect, import_model_json, initialize_database
from study_app.data.db_runtime import connect_readonly
from study_app.data.plan_repository import (
    get_active_study_plan,
    get_budgeted_day_plan,
    get_task_estimate,
)
from study_app.data.subject_repository import SubjectCatalogRepository
from study_app.quick.plan_service import PlanService, StalePlanPreviewError


class QmlPlanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="qml-plan-service-")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "study.sqlite"
        self.model_path = self.root / "model.json"
        self.records_path = self.root / "records.json"
        self.today = date(2026, 10, 4)
        self.model = {
            "model_name": "isolated-test-model",
            "scale": "0-1",
            "subjects": [
                {
                    "name": "测试学科",
                    "weight": 1,
                    "status": "learning",
                    "modules": [
                        {
                            "name": "测试模块",
                            "weight": 1,
                            "mastery": 0.25,
                            "status": "learning",
                            "topics": [
                                {
                                    "id": "test-topic-1",
                                    "name": "测试知识点",
                                    "status": "learning",
                                    "mastery": 0.25,
                                    "importance": 1,
                                    "difficulty": 0.5,
                                    "forgetting_risk": 0.8,
                                }
                            ],
                        }
                    ],
                }
            ],
            "initial_percent_assessment": {
                "subjects": [{"name": "测试学科", "initial_score": 45}]
            },
            "warning_policy": {
                "period_days": 3,
                "period_benchmark_score": 55,
                "stale_review_days": 6,
            },
        }
        self.model_path.write_text(
            json.dumps(self.model, ensure_ascii=False), encoding="utf-8"
        )
        self.records_path.write_text("[]", encoding="utf-8")
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            import_model_json(connection, self.model)
        self.service = PlanService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=self.today,
        )
        source = identify_model_topic(
            self.model, "测试学科", "测试模块", "测试知识点"
        )
        assert source is not None
        self.candidate = PlanCandidate(
            task_id=source.task_id,
            subject_id=source.subject_id,
            source_kind=source.source_kind,
            source_id=source.source_id,
            title="测试学科 / 测试知识点",
            priority_evidence={"kind": "诊断", "priority": 0.8, "detail": "隔离测试"},
        )
        # Supply one deterministic source-identified task while retaining the
        # real temporary model, records, catalog, and database repositories.
        self.service._candidate_collection = lambda _state, _scope=None: CandidateCollection(
            (self.candidate,), ()
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _new_profile_service(self, name: str) -> PlanService:
        profile = self.root / name
        profile.mkdir()
        db_path = profile / "study.sqlite"
        model_path = profile / "model.json"
        records_path = profile / "records.json"
        model_path.write_text(json.dumps(deepcopy(self.model), ensure_ascii=False), encoding="utf-8")
        records_path.write_text("[]", encoding="utf-8")
        initialize_database(db_path)
        with connect(db_path) as connection:
            import_model_json(connection, self.model)
        return PlanService(db_path, model_path, records_path, today=self.today)

    def test_load_returns_json_safe_allowlisted_dto_from_injected_paths(self) -> None:
        loaded = self.service.load("测试学科")

        self.assertEqual(loaded["catalog"]["selectedScope"]["subject"], "测试学科")
        self.assertEqual(loaded["catalog"]["subjects"][0]["modules"][0]["topics"][0]["name"], "测试知识点")
        self.assertEqual(loaded["examDates"], {"测试学科": None})
        self.assertEqual(loaded["tasks"][0]["taskId"], self.candidate.task_id)
        encoded = json.dumps(loaded, ensure_ascii=False)
        self.assertNotIn(str(self.db_path), encoded)
        self.assertNotIn("api_key", encoded.lower())
        self.assertNotIn("connection", encoded.lower())

    def test_load_filters_real_candidates_by_selected_subject(self) -> None:
        other = deepcopy(self.model["subjects"][0])
        other["name"] = "另一学科"
        other["modules"][0]["topics"][0]["id"] = "other-topic-1"
        self.model["subjects"].append(other)
        self.model["warning_policy"]["bkt_model"] = {"enabled": True}
        # Use actual candidates and a fresh database, without the fixture's stub.
        service = self._new_profile_service("multi-subject-scope")
        self.assertEqual(
            {row["subjectId"] for row in service.load()["tasks"]},
            {"测试学科", "另一学科"},
        )
        for subject in ("测试学科", "另一学科"):
            with self.subTest(subject=subject):
                loaded = service.load(subject)
                self.assertEqual(loaded["catalog"]["selectedScope"]["subject"], subject)
                self.assertTrue(loaded["tasks"])
                self.assertEqual({row["subjectId"] for row in loaded["tasks"]}, {subject})

        self.assertEqual(
            {row["subjectId"] for row in service.load()["tasks"]},
            {"测试学科", "另一学科"},
        )

    def test_budget_confirmation_requires_preview_and_persists_to_injected_database(self) -> None:
        preview = self.service.preview_budget(
            {
                "scope": "测试学科",
                "budget_minutes": 40,
                "exam_dates": {"测试学科": "2026-12-01"},
                "estimate_minutes": {self.candidate.task_id: 30},
            }
        )

        self.assertTrue(preview["token"])
        self.assertEqual(preview["summary"]["plannedMinutes"], 30)
        self.assertEqual(preview["rows"][0]["taskId"], self.candidate.task_id)
        confirmed = self.service.confirm_budget(preview["token"])

        self.assertTrue(confirmed["saved"])
        stored = get_budgeted_day_plan(
            self.today.isoformat(), db_path=self.db_path
        )
        self.assertEqual(stored["budget_minutes"], 40)
        estimate = get_task_estimate(self.candidate, db_path=self.db_path)
        self.assertEqual((estimate.estimated_minutes, estimate.source), (30, "user"))
        self.assertEqual(self.service.load("测试学科")["examDates"]["测试学科"], "2026-12-01")
        with self.assertRaises(StalePlanPreviewError):
            self.service.confirm_budget(preview["token"])

    def test_budget_preview_is_rejected_when_catalog_revision_drifts(self) -> None:
        preview = self.service.preview_budget(
            {"scope": None, "budget_minutes": 40, "estimate_minutes": {}}
        )
        before = SubjectCatalogRepository(self.db_path).catalog_revision()
        with connect(self.db_path) as connection:
            connection.execute(
                "UPDATE subject_catalog_state SET catalog_revision = catalog_revision + 1"
            )
        self.assertEqual(
            SubjectCatalogRepository(self.db_path).catalog_revision(), before + 1
        )

        with self.assertRaises(StalePlanPreviewError):
            self.service.confirm_budget(preview["token"])
        self.assertIsNone(
            get_budgeted_day_plan(self.today.isoformat(), db_path=self.db_path)
        )

    def test_detail_plan_preview_confirmation_check_and_homework_feedback(self) -> None:
        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        self.assertEqual(preview["source"], "local")
        self.assertTrue(any(item["itemType"] == "result" for item in preview["items"]))
        with self.assertRaises(StalePlanPreviewError):
            self.service.confirm_plan("not-a-preview-token")

        confirmed = self.service.confirm_plan(preview["token"])
        current = confirmed["currentPlan"]
        homework = next(item for item in current["items"] if item["itemType"] == "result")
        review = next(item for item in current["items"] if item["itemType"] == "check")

        checked = self.service.set_checked(review["id"], True, "测试学科")
        self.assertTrue(checked["updated"])
        feedback = self.service.feedback(homework["id"], True, "测试学科")
        self.assertTrue(feedback["saved"])
        self.assertEqual(feedback["result"], "correct")
        self.assertGreater(feedback["recordId"], 0)
        self.assertEqual(feedback["currentPlan"]["id"], current["id"])
        with connect_readonly(self.db_path) as connection:
            record_count_before = connection.execute(
                "SELECT COUNT(*) FROM learning_records WHERE note LIKE '学习计划项完成正确：%'"
            ).fetchone()[0]
            item_state = connection.execute(
                "SELECT result FROM study_plan_item_states WHERE item_id = ?",
                (homework["id"],),
            ).fetchall()
        self.assertEqual(len(item_state), 1)
        with self.assertRaises(ValueError):
            self.service.feedback(homework["id"], True, "测试学科")
        with connect_readonly(self.db_path) as connection:
            record_count_after = connection.execute(
                "SELECT COUNT(*) FROM learning_records WHERE note LIKE '学习计划项完成正确：%'"
            ).fetchone()[0]
        self.assertEqual(record_count_after, record_count_before)

    def test_confirm_plan_reports_post_commit_readback_failure_as_saved(self) -> None:
        import study_app.quick.plan_service as plan_module

        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        original_create = plan_module.create_study_plan
        original_read = plan_module.get_active_study_plan
        committed = {"value": False}

        def create_then_mark_committed(*args, **kwargs):
            plan_id = original_create(*args, **kwargs)
            committed["value"] = True
            return plan_id

        def fail_only_after_commit(subject_scope, *, db_path):
            if committed["value"]:
                raise OSError("refresh readback unavailable")
            return original_read(subject_scope, db_path=db_path)

        with (
            patch.object(plan_module, "create_study_plan", side_effect=create_then_mark_committed),
            patch.object(plan_module, "get_active_study_plan", side_effect=fail_only_after_commit),
        ):
            result = self.service.confirm_plan(preview["token"])

        self.assertTrue(result["saved"])
        self.assertEqual(result["currentPlan"], None)
        self.assertTrue(result["refreshWarning"])
        persisted = original_read("测试学科", db_path=self.db_path)
        self.assertIsNotNone(persisted)

    def test_confirm_plan_pre_commit_failure_still_raises(self) -> None:
        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        with patch(
            "study_app.quick.plan_service.create_study_plan",
            side_effect=OSError("insert rejected"),
        ):
            with self.assertRaisesRegex(OSError, "insert rejected"):
                self.service.confirm_plan(preview["token"])
        self.assertIsNone(get_active_study_plan("测试学科", db_path=self.db_path))

    def test_confirm_plan_cache_failure_preserves_saved_identity_and_prevents_replay(self) -> None:
        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        with patch(
            "study_app.quick.plan_service.settings_audit_repository.delete_settings_by_prefix",
            side_effect=OSError("private-cache-path"),
        ):
            result = self.service.confirm_plan(preview["token"])
        persisted = get_active_study_plan("测试学科", db_path=self.db_path)
        self.assertTrue(result["saved"])
        self.assertEqual(result["planId"], persisted["id"])
        self.assertEqual(result["currentPlan"]["id"], persisted["id"])
        self.assertTrue(result["refreshWarning"])
        self.assertNotIn("private-cache-path", str(result))
        with self.assertRaises(StalePlanPreviewError):
            self.service.confirm_plan(preview["token"])

    def test_feedback_cache_failure_preserves_saved_record_and_completion(self) -> None:
        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        plan = self.service.confirm_plan(preview["token"])["currentPlan"]
        homework = next(item for item in plan["items"] if item["itemType"] == "result")
        with patch(
            "study_app.quick.plan_service.settings_audit_repository.delete_settings_by_prefix",
            side_effect=OSError("private-cache-path"),
        ):
            result = self.service.feedback(homework["id"], True, "测试学科")
        self.assertTrue(result["saved"])
        self.assertTrue(result["refreshWarning"])
        with connect_readonly(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM learning_records WHERE id = ?", (result["recordId"],)
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT result FROM study_plan_item_states WHERE item_id = ?", (homework["id"],)
            ).fetchone()[0], "correct")
        with self.assertRaises(ValueError):
            self.service.feedback(homework["id"], True, "测试学科")

    def test_unscoped_homework_feedback_uses_the_active_profile_subject(self) -> None:
        preview = self.service.preview_plan({"scope": None, "enhanced": False})
        plan_text = "\n".join(
            line for section in preview["plan"].values() for line in section
        )
        self.assertNotIn("微积分", plan_text)
        self.assertNotIn("Gauss", plan_text)
        confirmed = self.service.confirm_plan(preview["token"])
        homework = next(
            item
            for item in confirmed["currentPlan"]["items"]
            if item["itemType"] == "result"
        )

        feedback = self.service.feedback(homework["id"], True)

        self.assertTrue(feedback["saved"])
        with connect_readonly(self.db_path) as connection:
            row = connection.execute(
                "SELECT subject_name FROM learning_records WHERE id = ?",
                (feedback["recordId"],),
            ).fetchone()
        self.assertEqual(row["subject_name"], "测试学科")

    def test_feedback_reports_auto_import_error_after_atomic_record_commit(self) -> None:
        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        confirmed = self.service.confirm_plan(preview["token"])
        homework = next(
            item
            for item in confirmed["currentPlan"]["items"]
            if item["itemType"] == "result"
        )

        original_load_state = self.service._load_state
        load_calls = {"count": 0}

        def fail_refresh_state():
            load_calls["count"] += 1
            if load_calls["count"] == 3:
                raise OSError("post-commit refresh failed")
            return original_load_state()

        with (
            patch(
                "study_app.data.database._auto_import_practice_problems",
                side_effect=RuntimeError("post-commit maintenance failed"),
            ),
            patch.object(
                self.service,
                "_recover_committed_feedback_record",
                side_effect=AssertionError("typed post-commit path must not read back"),
            ) as recovery,
            patch.object(self.service, "_load_state", side_effect=fail_refresh_state),
        ):
            feedback = self.service.feedback(homework["id"], True, "测试学科")

        self.assertTrue(feedback["saved"])
        self.assertEqual(feedback["result"], "correct")
        self.assertTrue(feedback["refreshWarning"])
        recovery.assert_not_called()
        with connect_readonly(self.db_path) as connection:
            records = connection.execute(
                "SELECT id FROM learning_records WHERE note = ?",
                (f"学习计划项完成正确：{homework['itemText']}",),
            ).fetchall()
            item_state = connection.execute(
                "SELECT result FROM study_plan_item_states WHERE item_id = ?",
                (homework["id"],),
            ).fetchone()
        self.assertEqual(len(records), 1)
        self.assertEqual(item_state["result"], "correct")
        with self.assertRaises(ValueError):
            self.service.feedback(homework["id"], True, "测试学科")

    def test_feedback_pre_commit_failure_still_raises_without_result_state(self) -> None:
        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        confirmed = self.service.confirm_plan(preview["token"])
        homework = next(
            item
            for item in confirmed["currentPlan"]["items"]
            if item["itemType"] == "result"
        )

        with patch(
            "study_app.data.database.add_learning_record",
            side_effect=RuntimeError("write rejected before commit"),
        ):
            with self.assertRaisesRegex(RuntimeError, "write rejected before commit"):
                self.service.feedback(homework["id"], True, "测试学科")
        with connect_readonly(self.db_path) as connection:
            record_count = connection.execute(
                "SELECT COUNT(*) FROM learning_records"
            ).fetchone()[0]
            item_state = connection.execute(
                "SELECT result FROM study_plan_item_states WHERE item_id = ?",
                (homework["id"],),
            ).fetchone()
        self.assertEqual(record_count, 0)
        self.assertIsNone(item_state)

    def test_set_checked_reports_refresh_failure_after_progress_commit(self) -> None:
        preview = self.service.preview_plan({"scope": "测试学科", "enhanced": False})
        confirmed = self.service.confirm_plan(preview["token"])
        check = next(
            item
            for item in confirmed["currentPlan"]["items"]
            if item["itemType"] == "check"
        )

        with patch.object(
            self.service,
            "_read_current_budget_plan",
            side_effect=OSError("refresh failed"),
        ):
            result = self.service.set_checked(check["id"], True, "测试学科")

        self.assertTrue(result["updated"])
        self.assertTrue(result["refreshWarning"])
        with connect_readonly(self.db_path) as connection:
            state = connection.execute(
                "SELECT checked FROM study_plan_item_states WHERE item_id = ?",
                (check["id"],),
            ).fetchone()
        self.assertEqual(state["checked"], 1)

    def test_enhanced_preview_redacts_provider_exception_text(self) -> None:
        private_detail = (
            "Bearer sk-private-token https://provider.invalid/v1 "
            "C:\\Users\\person\\private\\settings.json API_KEY=secret"
        )
        with (
            patch("study_app.ai.providers.is_llm_feature_enabled", return_value=True),
            patch(
                "study_app.ai.study_plan_generator.generate_plan_with_llm",
                side_effect=RuntimeError(private_detail),
            ),
        ):
            preview = self.service.preview_plan(
                {"scope": "测试学科", "enhanced": True}
            )

        encoded = json.dumps(preview, ensure_ascii=False)
        self.assertIn("RuntimeError", encoded)
        self.assertNotIn("sk-private-token", encoded)
        self.assertNotIn("provider.invalid", encoded)
        self.assertNotIn("private\\settings.json", encoded)
        self.assertNotIn("API_KEY", encoded)

    def test_custom_subject_practice_assignment_uses_injected_profile_data(self) -> None:
        from study_app.core.practice_bank import generate_practice_assignment

        assignment = generate_practice_assignment(
            "测试学科 / 测试模块 / 测试知识点",
            1,
            db_path=self.db_path,
            model_path=self.model_path,
            records=[],
        )

        self.assertEqual(assignment.difficulty_score, 48)
        self.assertIn("题型基准 50", assignment.difficulty_basis)

    def test_final_review_modes_and_archive_use_injected_database(self) -> None:
        changed = self.service.set_final_review_modes(
            {"states": {"测试学科": True}}
        )
        self.assertEqual(changed["changed"], [{"subject": "测试学科", "phase": "final_review"}])
        from study_app.core.study_phase import get_subject_phase

        self.assertEqual(
            get_subject_phase("测试学科", db_path=self.db_path)["phase"],
            "final_review",
        )
        self.assertEqual(
            self.service.archive("测试学科"),
            {"archived": True, "updated": True, "scope": "测试学科"},
        )

    def test_final_review_mode_reports_archive_failure_after_phase_commit(self) -> None:
        from study_app.core.study_phase import get_subject_phase

        with patch(
            "study_app.quick.plan_service.archive_active_study_plan",
            side_effect=OSError("archive maintenance failed"),
        ):
            result = self.service.set_final_review_modes(
                {"states": {"测试学科": True}}
            )

        self.assertTrue(result["updated"])
        self.assertEqual(result["changed"], [{"subject": "测试学科", "phase": "final_review"}])
        self.assertTrue(result["refreshWarning"])
        self.assertEqual(
            get_subject_phase("测试学科", db_path=self.db_path)["phase"],
            "final_review",
        )

    def test_invalid_final_review_batch_fails_before_any_phase_write(self) -> None:
        from study_app.core.study_phase import get_subject_phase

        with patch.object(self.service, "_write_phase") as write_phase:
            with self.assertRaises(ValueError):
                self.service.set_final_review_modes(
                    {"states": {"测试学科": True, "未知学科": True}}
                )

        write_phase.assert_not_called()
        self.assertEqual(
            get_subject_phase("测试学科", db_path=self.db_path)["phase"],
            "regular",
        )

    def test_real_candidate_builder_and_budget_preview_use_fixture_sources(self) -> None:
        service = PlanService(
            self.db_path,
            self.model_path,
            self.records_path,
            today=self.today,
        )
        state = service._load_state()
        actual = service._candidate_collection(state)
        self.assertEqual(actual, build_plan_candidates(state))
        self.assertNotEqual(actual, CandidateCollection((self.candidate,), ()))

        preview = service.preview_budget(
            {"scope": None, "budget_minutes": 0, "estimate_minutes": {}}
        )
        row_task_ids = {row["taskId"] for row in preview["rows"] if row["taskId"]}
        self.assertEqual(row_task_ids, {item.task_id for item in actual.candidates})

    def test_budget_preview_rejects_model_and_record_input_drift(self) -> None:
        model_preview = self.service.preview_budget(
            {"scope": "测试学科", "budget_minutes": 40, "estimate_minutes": {}}
        )
        changed_model = deepcopy(self.model)
        changed_model["model_name"] = "changed-after-preview"
        self.model_path.write_text(
            json.dumps(changed_model, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(StalePlanPreviewError):
            self.service.confirm_budget(model_preview["token"])

        # Restore the model, then change only the isolated database's record input.
        self.model_path.write_text(json.dumps(self.model, ensure_ascii=False), encoding="utf-8")
        self.service = PlanService(
            self.db_path, self.model_path, self.records_path, today=self.today
        )
        record_preview = self.service.preview_budget(
            {"scope": "测试学科", "budget_minutes": 40, "estimate_minutes": {}}
        )
        raw_record = {
            "date": self.today.isoformat(),
            "subject": "测试学科",
            "module": "测试模块",
            "topic": "测试知识点",
            "activity": "review_exercise",
            "source": "outside_class",
            "score": 50,
            "note": "目录漂移测试记录",
        }
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO learning_records(
                    record_date, subject_name, module_name, topic_name, activity,
                    source, score, duration_minutes, note, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    raw_record["date"], raw_record["subject"], raw_record["module"],
                    raw_record["topic"], raw_record["activity"], raw_record["source"],
                    raw_record["score"], raw_record["note"],
                    json.dumps(raw_record, ensure_ascii=False),
                ),
            )
        with self.assertRaises(StalePlanPreviewError):
            self.service.confirm_budget(record_preview["token"])

    def test_budget_preview_rejects_completed_item_state_drift(self) -> None:
        first = self.service.preview_budget(
            {
                "scope": "测试学科",
                "budget_minutes": 40,
                "estimate_minutes": {self.candidate.task_id: 30},
            }
        )
        self.service.confirm_budget(first["token"])
        before = self.service.load("测试学科")["budgetPlan"]
        self.assertTrue(before["items"])

        pending = self.service.preview_budget(
            {
                "scope": "测试学科",
                "budget_minutes": 40,
                "estimate_minutes": {self.candidate.task_id: 30},
            }
        )
        item = before["items"][0]
        self.service.set_checked(item["id"], not item["checked"], "测试学科")
        with self.assertRaises(StalePlanPreviewError):
            self.service.confirm_budget(pending["token"])

    def test_two_profiles_generate_and_read_provider_phase_without_cross_talk(self) -> None:
        from study_app.ai.providers import LLMSettings, load_llm_settings, save_llm_settings
        from study_app.core.study_phase import get_subject_phase

        profiles = [self.service, self._new_profile_service("second-profile")]
        providers = ("openai", "deepseek")

        def run_profile(service: PlanService, provider: str, final_review: bool):
            save_llm_settings(
                LLMSettings(
                    enabled=False,
                    provider=provider,
                    model=f"{provider}-isolated",
                    custom_base_url="",
                    api_key="",
                    single_call_token_limit=8000,
                    daily_budget_cny=0,
                    allow_upload_images=False,
                    allow_upload_pdfs=False,
                    enabled_features=(),
                ),
                db_path=service.db_path,
            )
            if final_review:
                service.set_final_review_modes({"states": {"测试学科": True}})
            preview = service.preview_plan({"scope": "测试学科", "enhanced": False})
            return (
                load_llm_settings(db_path=service.db_path),
                preview["plan"]["short"][0],
                get_subject_phase("测试学科", db_path=service.db_path)["phase"],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            left = executor.submit(run_profile, profiles[0], providers[0], True)
            right = executor.submit(run_profile, profiles[1], providers[1], False)
            first, second = left.result(), right.result()

        self.assertEqual((first[0].provider, first[0].model), ("openai", "openai-isolated"))
        self.assertEqual((second[0].provider, second[0].model), ("deepseek", "deepseek-isolated"))
        self.assertEqual(first[2], "final_review")
        self.assertEqual(second[2], "regular")
        self.assertIn("期末综合诊断", first[1])
        self.assertIn("今日计划", second[1])


if __name__ == "__main__":
    unittest.main()
