from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from study_app.core.dashboard import DashboardState, SubjectSummary, TodoItem


class StudyPlanServiceContractTests(unittest.TestCase):
    @staticmethod
    def state() -> DashboardState:
        return DashboardState(
            start=date(2026, 7, 15),
            today=date(2026, 7, 17),
            benchmark=55,
            subjects=(
                SubjectSummary(
                    name="计算机科学",
                    initial_score=60,
                    window_score=60,
                    has_window_records=True,
                    mastery_score=75,
                    covered_mastery_score=70,
                    covered_topic_count=5,
                    total_topic_count=10,
                    latest_record=None,
                    latest_review=None,
                    warnings=(),
                    archived=False,
                ),
            ),
            todos=(),
            memory_risks=(),
            bkt_alerts=(),
        )

    def test_plan_signature_and_refresh_contract(self) -> None:
        from study_app.ai.study_plan_service import (
            current_study_plan_signature,
            should_refresh_plan_for_model,
        )

        state = self.state()
        signature = current_study_plan_signature(state, None)

        self.assertEqual(
            signature,
            "4440b489bdfed75562807e34c96bfa27003dc89ccb2407d4fb81993d5b9ab380",
        )
        saved = {
            "end_date": "2026-07-17",
            "input_signature": signature,
            "plan": {"evidence": ["稳定计划"]},
        }
        self.assertFalse(should_refresh_plan_for_model(saved, state, None))
        self.assertTrue(
            should_refresh_plan_for_model(
                {**saved, "input_signature": "stale"}, state, None
            )
        )
        self.assertTrue(
            should_refresh_plan_for_model(
                {
                    **saved,
                    "plan": {"short": ["window_score=40"]},
                },
                None,
                None,
            )
        )

    def test_single_day_llm_plan_without_legacy_day_prefix_is_valid(self) -> None:
        from study_app.ai.study_plan_generator import parse_and_validate_plan

        plan = {
            "judgement": ["当前判断：计算机科学需要优先巩固。"],
            "goals": ["今日目标：完成一次回忆、练习和复盘闭环。"],
            "short": [
                "回忆自测：先无资料复述核心算法。\n"
                "查漏补缺：核对边界条件。\n"
                "专项练习：完成两道可判定练习。\n"
                "当天作业：参考难度 60/100；题库模板 TEST-CS；题量 2 题。\n"
                "完成标准：至少独立做对 1 题。\n"
                "复盘记录：记录正确率和错因。\n"
                "调整依据：根据结果决定明日重点。"
            ],
            "diagnostic": ["记录每道错题的具体错因。"],
            "record_template": ["记录：完成数量、正确率、错因。"],
            "expected": ["正确完成后相关风险应下降。"],
            "evidence": ["依据当前学习记录生成。"],
        }

        parsed = parse_and_validate_plan(json.dumps(plan, ensure_ascii=False))

        self.assertEqual(parsed["short"], plan["short"])

    def test_plan_signature_changes_when_plan_todos_change(self) -> None:
        from study_app.ai.study_plan_service import current_study_plan_signature

        state = self.state()
        changed = replace(
            state,
            todos=(TodoItem("做题", "计算机科学 / 图", "新增待办", "high", 0.9),),
        )

        self.assertNotEqual(
            current_study_plan_signature(state, None),
            current_study_plan_signature(changed, None),
        )

    def test_local_generation_path_exact(self) -> None:
        from study_app.ai.study_plan_service import generate_study_plan_with_optional_llm

        local_plan = {"evidence": ["local evidence"]}
        with (
            patch(
                "study_app.ai.providers.is_llm_feature_enabled",
                return_value=False,
            ),
            patch(
                "study_app.ai.study_plan_service.generate_study_plan",
                return_value=local_plan,
            ) as generate_local,
            patch("study_app.ai.study_plan_generator.generate_plan_with_llm") as generate_llm,
        ):
            result = generate_study_plan_with_optional_llm(self.state())

        self.assertEqual(result, (local_plan, "local"))
        generate_local.assert_called_once_with(self.state(), None)
        generate_llm.assert_not_called()

    def test_computer_science_always_uses_local_oj_plan(self) -> None:
        from study_app.ai.study_plan_service import generate_study_plan_with_optional_llm

        oj_plan = {
            "short": [
                "今日计划：OJ 原题训练\nLeetCode\n题库模板 CS-OJ-PRACTICE"
            ],
            "evidence": [],
        }
        with (
            patch(
                "study_app.ai.study_plan_service.generate_study_plan",
                return_value=oj_plan,
            ) as generate_local,
            patch(
                "study_app.ai.providers.is_llm_feature_enabled",
            ) as llm_enabled,
            patch(
                "study_app.ai.study_plan_generator.generate_plan_with_llm",
            ) as generate_llm,
        ):
            result = generate_study_plan_with_optional_llm(
                self.state(), "计算机科学"
            )

        self.assertEqual(result, (oj_plan, "本地 OJ 计划已生成。"))
        generate_local.assert_called_once_with(self.state(), "计算机科学")
        llm_enabled.assert_not_called()
        generate_llm.assert_not_called()

    def test_computer_science_theory_plan_is_stale(self) -> None:
        from study_app.ai.study_plan_service import (
            should_refresh_plan_for_model,
            should_upgrade_plan_to_llm,
        )

        old_plan = {
            "plan": {
                "short": [
                    "回忆自测：复述数据结构概念。\n"
                    "查漏补缺：复习教材或笔记。\n"
                    "当天作业：题库模板 GEN-MIXED-PRACTICE。"
                ]
            }
        }
        oj_plan = {
            "plan": {
                "short": [
                    "今日计划：OJ 原题训练\n"
                    "完成 LeetCode 官方原题。\n"
                    "题库模板 CS-OJ-PRACTICE。"
                ]
            }
        }

        self.assertTrue(
            should_refresh_plan_for_model(old_plan, None, "计算机科学")
        )
        self.assertFalse(
            should_refresh_plan_for_model(oj_plan, None, "计算机科学")
        )
        self.assertFalse(should_upgrade_plan_to_llm(oj_plan))

    def test_disabled_llm_does_not_retry_or_relabel_local_generation_error(self) -> None:
        from study_app.ai.study_plan_service import generate_study_plan_with_optional_llm

        local_error = RuntimeError("local model unavailable")
        with (
            patch("study_app.ai.providers.is_llm_feature_enabled", return_value=False),
            patch(
                "study_app.ai.study_plan_service.generate_study_plan",
                side_effect=local_error,
            ) as generate_local,
        ):
            with self.assertRaises(RuntimeError) as captured:
                generate_study_plan_with_optional_llm(self.state())

        self.assertIs(captured.exception, local_error)
        generate_local.assert_called_once()

    def test_dynamic_difficulty_error_is_not_relabelled_as_llm_validation(self) -> None:
        from study_app.ai.study_plan_service import generate_study_plan_with_optional_llm

        difficulty_error = RuntimeError("difficulty model unavailable")
        with (
            patch("study_app.ai.providers.is_llm_feature_enabled", return_value=True),
            patch(
                "study_app.ai.study_plan_service.study_plan_llm_payload",
                return_value={"payload": True},
            ),
            patch(
                "study_app.ai.study_plan_generator.generate_plan_with_llm",
                return_value={"evidence": []},
            ),
            patch(
                "study_app.ai.study_plan_service.apply_dynamic_difficulty_to_plan",
                side_effect=difficulty_error,
            ),
            patch("study_app.ai.study_plan_service.generate_study_plan") as generate_local,
        ):
            with self.assertRaises(RuntimeError) as captured:
                generate_study_plan_with_optional_llm(self.state())

        self.assertIs(captured.exception, difficulty_error)
        generate_local.assert_not_called()

    def test_llm_and_repair_success_paths_preserve_exact_evidence_and_status(self) -> None:
        from study_app.ai.study_plan_service import generate_study_plan_with_optional_llm

        for initial_evidence in ([], ["生成方式：LLM 首次输出未通过本地校验，已自动修复一次并通过校验。"]):
            with self.subTest(initial_evidence=initial_evidence):
                llm_plan = {"short": ["LLM plan"], "evidence": list(initial_evidence)}
                with (
                    patch(
                        "study_app.ai.providers.is_llm_feature_enabled",
                        return_value=True,
                    ),
                    patch(
                        "study_app.ai.study_plan_service.study_plan_llm_payload",
                        return_value={"payload": True},
                    ),
                    patch(
                        "study_app.ai.study_plan_generator.generate_plan_with_llm",
                        return_value=llm_plan,
                    ) as generate_llm,
                    patch(
                        "study_app.ai.study_plan_service.apply_dynamic_difficulty_to_plan",
                        side_effect=lambda plan, _subject, *, as_of_date: plan,
                    ),
                ):
                    plan, status = generate_study_plan_with_optional_llm(self.state())

                self.assertEqual(status, "LLM 增强计划已生成，正在保存。")
                self.assertEqual(
                    plan["evidence"],
                    [
                        *initial_evidence,
                        "生成方式：LLM 增强计划，经本地结构校验后保存。",
                        "BKT证据口径：题目级匹配已启用同义词、记录级兜底和重复证据去重。",
                    ],
                )
                generate_llm.assert_called_once_with({"payload": True})

    def test_llm_exception_falls_back_with_exact_messages(self) -> None:
        from study_app.ai.study_plan_service import generate_study_plan_with_optional_llm

        local_plan = {"evidence": ["local evidence"]}
        with (
            patch(
                "study_app.ai.providers.is_llm_feature_enabled",
                return_value=True,
            ),
            patch(
                "study_app.ai.study_plan_generator.generate_plan_with_llm",
                side_effect=ValueError("第一行\n第二行"),
            ),
            patch(
                "study_app.ai.study_plan_service.study_plan_llm_payload",
                return_value={"payload": True},
            ),
            patch(
                "study_app.ai.study_plan_service.generate_study_plan",
                return_value=local_plan,
            ),
        ):
            plan, status = generate_study_plan_with_optional_llm(self.state())

        self.assertEqual(
            plan["evidence"],
            [
                "local evidence",
                "LLM 增强计划未通过校验，已显示本地计划。原因摘要：第一行 第二行",
            ],
        )
        self.assertEqual(
            status,
            "已显示本地计划；LLM 增强未通过校验：第一行 第二行",
        )

    def test_llm_unavailable_hint_exact_messages_and_exception_default(self) -> None:
        from study_app.ai.study_plan_service import llm_plan_unavailable_hint

        cases = (
            (
                SimpleNamespace(
                    enabled=True,
                    provider="openai",
                    enabled_features=frozenset(),
                    api_key="key",
                ),
                "当前已开启 LLM 增强模式，但设置中尚未勾选“今日计划生成”。因此本次仍显示/生成本地计划。",
            ),
            (
                SimpleNamespace(
                    enabled=True,
                    provider="openai",
                    enabled_features=frozenset({"plan_generation"}),
                    api_key="",
                ),
                "当前已开启“今日计划生成”，但尚未填写 API Key。",
            ),
            (
                SimpleNamespace(
                    enabled=False,
                    provider="local",
                    enabled_features=frozenset(),
                    api_key="",
                ),
                "",
            ),
        )
        for settings, expected in cases:
            with self.subTest(expected=expected):
                with patch(
                    "study_app.ai.study_plan_service.load_llm_settings",
                    return_value=settings,
                ):
                    self.assertEqual(llm_plan_unavailable_hint(), expected)
        with patch(
            "study_app.ai.study_plan_service.load_llm_settings",
            side_effect=RuntimeError("settings unavailable"),
        ):
            self.assertEqual(llm_plan_unavailable_hint(), "")

    def test_facade_reexports_canonical_service_functions_by_identity(self) -> None:
        from study_app.ai import study_plan_service
        from study_app.ui import main_window

        names = (
            "should_upgrade_plan_to_llm",
            "current_study_plan_signature",
            "_plan_text",
            "should_refresh_plan_for_model",
            "llm_plan_unavailable_hint",
            "generate_study_plan_with_optional_llm",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(main_window, name), getattr(study_plan_service, name))


if __name__ == "__main__":
    unittest.main()
