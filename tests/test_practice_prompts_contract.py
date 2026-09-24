from __future__ import annotations

import hashlib
import inspect
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from study_app.core.dashboard import DashboardState, SubjectSummary


EXPECTED_PRACTICE_PROMPT = """[PERSONAL_LEARNING_OS_PDF_WORKFLOW]
请根据下列轻量题库模板生成练习题。

学科：高等数学
知识点：概念判定、基础计算/推导、混合应用与错因归类
模板编号：CALC-SERIES
模板说明：概念判定、基础计算/推导、混合应用与错因归类
参考难度：64/100
题量：3
当前作业规格：参考难度 64/100；题库模板 CALC-SERIES；题量 3 题。

最近学习证据与错因：
- 暂无明确错因；请按模板生成覆盖基础、中等和易错点的变式题。

历史样题种子：
- 正项级数判敛变式（参考难度 63/100）：判断给定参数级数的敛散性并说明判别依据。 考查点：比值判别、边界条件。来源：固定教材样题；真实来源，难度与目标相差 1 分，可作为难度对标
- 幂级数收敛域（参考难度 67/100）：求幂级数的收敛半径、收敛区间并检查端点。 考查点：幂级数、端点。来源：固定课堂练习；真实来源，难度与目标相差 3 分，可作为难度对标
- Taylor 展开（参考难度 65/100）：利用 Taylor 展开计算极限并给出余项阶数。 考查点：Taylor、余项。来源：固定作业；真实来源，难度与目标相差 1 分，可作为难度对标

题型覆盖与配额：
- 未识别到明确复合题型；请按题库模板覆盖基础、中等和易错变式，并保持难度接近参考难度。

出题要求：
1. 题目难度应贴近参考难度，允许上下浮动 5 分。
2. 难度标尺同等优先对标用户上传作业、课堂练习、教材题、真实学习记录，以及经过质量校验的 MIT、Princeton 等可信大学官方题目；AI 生成题只可参考题型，不可作为难度标尺。
3. 每题给出题面、考查点、预估难度、标准答案或关键步骤。
4. 不要直接重复教材原题，可以生成同型变式题。
5. 题目应能判断“做对/有误”，并能归纳错因。
6. 若最近错因与模板相关，优先围绕该错因生成题目。
7. 若历史样题种子少于本次题量的一半，不要机械复制种子；应按“题型覆盖与配额”扩展变式，并保持难度贴近参考难度。

请现在直接生成题目，不要只返回格式模板，也不要留下空字段。
每道题请按以下字段完整填写：
题目编号与标题
题面：写出可直接作答的完整题目。
考查点：列出 2-4 个核心考查点。
预估难度：给出 0-100 分，并说明与参考难度的关系。
答案/关键步骤：给出标准答案或关键推导步骤。
常见错因：写出 1-3 个容易出错的位置。

PDF 交付与排版要求：
1. 在完成题目设计后，生成并提供一份可下载的 PDF 练习册，不要只返回聊天文本。
2. PDF 分为“练习题”和“答案与解析”两部分；练习题部分不得紧跟答案、关键步骤或常见错因，所有答案、解析和错因统一置于文档末尾。
3. 每道题之间保留充足的空白作答间距；计算题、证明题和需要画图的题应预留更多空间。
4. 使用专业数学排版，确保公式、上下标、积分号、求和号、矩阵、向量和特殊符号清晰正确，避免乱码、字符重叠和公式被截断。
5. PDF 首页注明学科、知识点、参考难度和题量；答案区题号必须与练习题完全对应。
6. 生成 PDF 前检查题面完整性、答案正确性、方向或正负号判断以及实际难度是否贴近参考难度。
"""

EXPECTED_OJ_LIST = """[PERSONAL_LEARNING_OS_OJ_LIST]
算法设计与 OJ 训练：系统已按题库与难度分数匹配 LeetCode 官方原题；无需让 LLM 生成题目。
学科：计算机科学
训练主题：数组、哈希与前缀结构
目标参考难度：72/100

今日 LeetCode 原题清单：
1. LeetCode 999｜Shortest Path Fixture｜官方 Medium｜校准难度 72/100｜中高
   链接：https://leetcode.example/fixture
   训练点：在带权图中求受约束的最短路径。

完成要求：
1. 打开官方链接独立提交，不看题解完成第一版。
2. 记录每题 AC / WA / TLE / RE、失败次数、失败用例类型和最终复杂度。
3. 若未 AC，先用失败用例定位边界条件或状态维护问题，再决定是否看题解。
4. 完成后在学习应用新增记录中上传：题号、是否 AC、错因、是否看题解。"""

EXPECTED_MOCK_PROMPT = """[PERSONAL_LEARNING_OS_PDF_WORKFLOW]
请根据 Personal Learning OS 的学习数据生成一份期末复习模拟卷，并输出可下载 PDF。

学科：微积分
卷型：诊断卷
考试范围：按当前学科已学范围与期末复习设置。
注意：该学科尚未启用期末复习模式，请生成诊断型模拟卷，题量可略少，但仍需严格围绕已学内容。
模拟卷准备度：73/100（可生成）
卷型策略（优先级高于后续通用出卷要求）：
- 难度：正常难度，整体参考难度约 68-74/100。
- 题量：题量较多，建议 12-15 道小题或等价题量。
- 目标：题型不受当前掌握度和遗忘曲线强约束，核心目标是完整覆盖考试范围内主要知识点，用于检测尚未发现的疏漏。
- 难度/覆盖分配：覆盖优先，薄弱点只作为排序参考，不应挤占范围内其他关键知识点。
模拟卷蓝图：
- 基础概念 40%
- 综合应用 60%

覆盖缺口与薄弱点：
- 参数题覆盖不足

当前加权优先级最高的知识点：
- 暂无可用加权优先级数据，请按考试范围均衡出卷。

考试范围题库种子参考（用于避免题型重复；请生成同型变式，不要直接复制原题）：
- 考试范围题型：
  · FIXTURE-MOCK｜70/100｜覆盖性诊断样题：固定种子用于覆盖考试范围中的基础概念与综合应用。

额外约束：


出卷要求：
1. 生成一整份模拟卷，而不是零散练习；题目必须全部位于考试范围内。
2. 难度必须服从卷型策略：诊断卷正常难度且覆盖优先，标准期末卷平均难度较高且有难有易，冲刺卷偏难且可做专题突破；不要机械堆砌冷门偏题。
3. 诊断卷以完整覆盖为先，不要过度追随当前掌握度/遗忘曲线；标准期末卷必须服从考试比重和题数；冲刺专题卷可集中突破重难点。高数整卷比例只用于模拟卷，不用于平时计划练习。
4. 每题标注题型、考查点、预估难度和建议用时。
5. 题目区与答案区必须分离：题目后不要紧跟答案、关键步骤或常见错因。
6. 答案与解析统一放在卷末，题号必须与题目区完全对应。
7. 生成前自行检查：范围外内容、题量冲突、答案正确性、符号/公式排版和实际难度。

PDF 排版要求：
1. 请生成并提供一份可下载 PDF，不要只返回聊天文本。
2. 首页注明学科、考试范围、建议用时、总分或题量结构。
3. 每道题之间保留足够作答空白；计算题、证明题和画图题预留更多空间。
4. 使用专业数学/算法/化学符号排版，避免乱码、字符重叠、公式截断。
"""


class PracticePromptsContractTests(unittest.TestCase):
    @staticmethod
    def state_for(*names: str) -> DashboardState:
        subjects = tuple(
            SubjectSummary(
                name=name,
                initial_score=60,
                window_score=62,
                has_window_records=True,
                mastery_score=71,
                covered_mastery_score=68,
                covered_topic_count=4,
                total_topic_count=8,
                latest_record=None,
                latest_review=None,
                warnings=(),
                archived=False,
            )
            for name in names
        )
        return DashboardState(
            start=date(2026, 7, 15),
            today=date(2026, 7, 17),
            benchmark=55,
            subjects=subjects,
            todos=(),
            memory_risks=(
                {
                    "subject": "高等数学",
                    "topic": "级数判敛",
                    "recall": 0.42,
                    "priority": 1.7,
                },
            ),
            bkt_alerts=(
                {
                    "subject": "高等数学",
                    "topic": "级数判敛",
                    "mastery_probability": 0.51,
                },
            ),
            raw_records=(
                {
                    "date": "2026-07-16",
                    "subject": "高等数学",
                    "topic": "级数判敛",
                    "note": "比值判别方向有误",
                    "score": 62,
                },
            ),
        )

    @staticmethod
    def practice_problems() -> list[dict]:
        return [
            {
                "id": 101,
                "title": "正项级数判敛变式",
                "difficulty_score": 63.0,
                "statement": "判断给定参数级数的敛散性并说明判别依据。",
                "tags": ["比值判别", "边界条件"],
                "source": {"title": "固定教材样题", "type": "textbook"},
                "raw": {},
            },
            {
                "id": 102,
                "title": "幂级数收敛域",
                "difficulty_score": 67.0,
                "statement": "求幂级数的收敛半径、收敛区间并检查端点。",
                "tags": ["幂级数", "端点"],
                "source": {"title": "固定课堂练习", "type": "course"},
                "raw": {},
            },
            {
                "id": 103,
                "title": "Taylor 展开",
                "difficulty_score": 65.0,
                "statement": "利用 Taylor 展开计算极限并给出余项阶数。",
                "tags": ["Taylor", "余项"],
                "source": {"title": "固定作业", "type": "homework"},
                "raw": {},
            },
        ]

    def test_partial_seed_backlog_failure_never_claims_registered(self) -> None:
        from study_app.core.practice_prompts import practice_seed_context

        with (
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=self.practice_problems()[:1],
            ),
            patch(
                "study_app.data.collection_backlog.register_collection_gap",
                side_effect=OSError("backlog locked"),
            ),
            self.assertLogs("study_app.core.practice_prompts", level="ERROR") as captured,
        ):
            context = practice_seed_context(
                "CALC-SERIES",
                "级数判敛",
                target_difficulty=64,
                subject="高等数学",
                desired_count=3,
                exercise_count=3,
            )

        self.assertNotIn("已登记到待收集表", context)
        self.assertIn("待收集登记失败", context)
        self.assertNotIn("backlog locked", context)
        self.assertIn("backlog locked", "\n".join(captured.output))

    def test_missing_records_and_failed_records_have_distinct_context(self) -> None:
        from study_app.core.practice_prompts import recent_practice_context

        state = SimpleNamespace(memory_risks=(), bkt_alerts=(), raw_records=())
        self.assertIn("暂无明确错因", recent_practice_context("高等数学", "级数", state))

        state.raw_records = (None,)
        with self.assertLogs("study_app.core.practice_prompts", level="ERROR") as captured:
            context = recent_practice_context("高等数学", "级数", state)
        self.assertIn("近期记录读取失败", context)
        self.assertNotIn("暂无明确错因", context)
        self.assertIn("AttributeError", "\n".join(captured.output))

    def test_missing_seeds_and_failed_query_have_distinct_context(self) -> None:
        from study_app.core.practice_prompts import practice_seed_context

        with (
            patch("study_app.data.practice_repository.find_practice_problems", side_effect=OSError("bank locked")),
            self.assertLogs("study_app.core.practice_prompts", level="ERROR") as captured,
        ):
            context = practice_seed_context("CALC-SERIES", "级数", subject="高等数学")
        self.assertIn("样题种子读取或处理失败", context)
        self.assertNotIn("暂无同模板样题种子", context)
        self.assertIn("bank locked", "\n".join(captured.output))

        with (
            patch("study_app.data.practice_repository.find_practice_problems", return_value=[]),
            patch("study_app.data.collection_backlog.register_collection_gap", side_effect=OSError("backlog locked")),
            self.assertLogs("study_app.core.practice_prompts", level="ERROR"),
        ):
            context = practice_seed_context("CALC-SERIES", "级数", subject="高等数学")
        self.assertIn("暂无同模板样题种子", context)
        self.assertIn("待收集登记失败", context)

    def test_oj_query_failure_is_not_described_as_empty_bank(self) -> None:
        from study_app.core.practice_prompts import build_oj_practice_list

        line = "计算机科学 / 图搜索\n当天作业：参考难度 72/100；题库模板 CS-OJ-PRACTICE；题量 1 题。"
        with (
            patch("study_app.data.practice_repository.find_practice_problems", side_effect=OSError("bank locked")),
            self.assertLogs("study_app.core.practice_prompts", level="ERROR") as captured,
        ):
            context = build_oj_practice_list(line, self.state_for("计算机科学"), "计算机科学")
        self.assertIn("题库读取失败", context)
        self.assertNotIn("暂未匹配到足够", context)
        self.assertIn("bank locked", "\n".join(captured.output))

    def assert_golden(
        self,
        actual: str,
        expected: str,
        chars: int,
        utf8_bytes: int,
        sha256: str,
    ) -> None:
        encoded = actual.encode("utf-8")
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), chars)
        self.assertEqual(len(encoded), utf8_bytes)
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), sha256)

    def test_complete_practice_prompt_text_and_digest(self) -> None:
        from study_app.core.practice_prompts import build_practice_generation_prompt

        line = "高等数学 / 级数判敛\n当天作业：参考难度 64/100；题库模板 CALC-SERIES；题量 3 题。"
        with (
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=self.practice_problems(),
            ),
            patch("study_app.data.collection_backlog.register_collection_gap") as backlog,
            patch("study_app.core.study_phase.is_final_review", return_value=False),
            patch("study_app.core.study_phase.exam_scope_label", return_value=""),
            patch("study_app.core.study_phase.out_of_exam_scope_references", return_value=[]),
            patch("study_app.data.text_integrity.validate_text_integrity"),
        ):
            actual = build_practice_generation_prompt(
                line, self.state_for("高等数学"), "高等数学"
            )

        backlog.assert_not_called()
        self.assert_golden(
            actual,
            EXPECTED_PRACTICE_PROMPT,
            1361,
            3507,
            "dbfb2952f22d47d572017828abdc0a8721e2dee4b2004aec01c33ca6e2a10ad1",
        )

    def test_complete_oj_list_text_and_digest(self) -> None:
        from study_app.core.practice_prompts import build_oj_practice_list

        problem = {
            "id": 201,
            "title": "Shortest Path Fixture",
            "difficulty_score": 72.0,
            "statement": "fixture fallback",
            "tags": ["graph"],
            "source": {
                "title": "LeetCode",
                "type": "leetcode_metadata",
                "url": "https://leetcode.example/fixture",
            },
            "raw": {
                "problem_number": "999",
                "title": "Shortest Path Fixture",
                "difficulty_label": "Medium",
                "difficulty_band": "中高",
                "url": "https://leetcode.example/fixture",
                "abstract": "在带权图中求受约束的最短路径。",
            },
        }
        line = "计算机科学 / 图搜索\n当天作业：参考难度 72/100；题库模板 CS-OJ-PRACTICE；题量 1 题。"
        with (
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=[problem],
            ),
            patch("study_app.data.text_integrity.validate_text_integrity"),
        ):
            actual = build_oj_practice_list(
                line, self.state_for("计算机科学"), "计算机科学"
            )

        self.assert_golden(
            actual,
            EXPECTED_OJ_LIST,
            422,
            856,
            "e4082ecf9f8a45ca7adf288a4f61f8ef84c708661157f97e9871fae13a59cc91",
        )

    def test_complete_mock_prompt_text_and_digest(self) -> None:
        from study_app.core.practice_prompts import build_mock_exam_generation_prompt

        seed = {
            "id": 301,
            "template_id": "FIXTURE-MOCK",
            "title": "覆盖性诊断样题",
            "difficulty_score": 70.0,
            "statement": "固定种子用于覆盖考试范围中的基础概念与综合应用。",
            "topic_hint": "综合",
            "tags_json": "[]",
            "source_note": "固定来源",
        }
        readiness = {
            "score": 73,
            "label": "可生成",
            "blueprint": ["基础概念 40%", "综合应用 60%"],
            "gaps": ["参数题覆盖不足"],
        }
        with (
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=[seed],
            ),
            patch("study_app.core.mock_exam.mock_exam_readiness", return_value=readiness),
            patch("study_app.core.study_phase.get_subject_phase", return_value=None),
            patch("study_app.core.study_phase.exam_scope_label", return_value=""),
            patch("study_app.core.study_phase.is_final_review", return_value=False),
            patch(
                "study_app.core.study_phase.weighted_topic_priority_states",
                return_value=[],
            ),
            patch("study_app.data.text_integrity.validate_text_integrity"),
        ):
            actual = build_mock_exam_generation_prompt(
                self.state_for("微积分"), "微积分", "diagnostic"
            )

        self.assert_golden(
            actual,
            EXPECTED_MOCK_PROMPT,
            1048,
            2701,
            "e330461fa33f8db6c24f12bf3df75bf997711f2d494df03d3fd783e2a70c7340",
        )

    def test_seed_backlog_registration_contract(self) -> None:
        from study_app.core.practice_prompts import practice_seed_context

        kwargs = {
            "template_id": "CALC-SERIES",
            "topic": "级数",
            "target_difficulty": 64.0,
            "subject": "高等数学",
            "desired_count": 3,
            "exercise_count": 4,
        }
        with (
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=self.practice_problems(),
            ),
            patch("study_app.data.collection_backlog.register_collection_gap") as backlog,
        ):
            practice_seed_context(**kwargs)
        backlog.assert_not_called()

        with (
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=self.practice_problems()[:1],
            ),
            patch("study_app.data.collection_backlog.register_collection_gap") as backlog,
        ):
            practice_seed_context(**kwargs)
        backlog.assert_called_once_with(
            subject="高等数学",
            template_id="CALC-SERIES",
            topic="级数",
            target_difficulty=64.0,
            note="出题提示需要 3 个样题种子，实际仅 1 个；本次题量 4。",
        )

        with (
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=[],
            ),
            patch("study_app.data.collection_backlog.register_collection_gap") as backlog,
        ):
            practice_seed_context(**kwargs)
        backlog.assert_called_once_with(
            subject="高等数学",
            template_id="CALC-SERIES",
            topic="级数",
            target_difficulty=64.0,
            note="未找到样题种子；本次题量 4，建议至少 3 个种子。",
        )

    def test_facade_identity_and_patch_boundary(self) -> None:
        from study_app.core import practice_prompts
        from study_app.ui import main_window

        names = (
            "_short_seed_text",
            "mock_exam_seed_context",
            "practice_distribution_guidance",
            "build_mock_exam_generation_prompt",
            "build_practice_generation_prompt",
            "build_oj_practice_list",
            "recent_practice_context",
            "practice_seed_context",
            "_topic_related",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(main_window, name), getattr(practice_prompts, name))

        line = "高等数学 / 级数判敛\n当天作业：参考难度 64/100；题库模板 CALC-SERIES；题量 3 题。"
        with (
            patch(
                "study_app.ui.main_window.recent_practice_context",
                return_value="facade evidence",
            ) as facade_context,
            patch(
                "study_app.core.practice_prompts.recent_practice_context",
                return_value="canonical evidence",
            ) as canonical_context,
            patch(
                "study_app.data.practice_repository.find_practice_problems",
                return_value=self.practice_problems(),
            ),
            patch("study_app.core.study_phase.is_final_review", return_value=False),
            patch("study_app.core.study_phase.exam_scope_label", return_value=""),
            patch("study_app.core.study_phase.out_of_exam_scope_references", return_value=[]),
            patch("study_app.data.text_integrity.validate_text_integrity"),
        ):
            result = main_window.build_practice_generation_prompt(
                line, self.state_for("高等数学"), "高等数学"
            )

        self.assertIn("canonical evidence", result)
        self.assertNotIn("facade evidence", result)
        facade_context.assert_not_called()
        canonical_context.assert_called_once()

    def test_canonical_module_has_no_ui_or_pyside_dependency(self) -> None:
        from study_app.core import practice_prompts

        source = inspect.getsource(practice_prompts)
        self.assertNotIn("study_app.ui", source)
        self.assertNotIn("PySide", source)


if __name__ == "__main__":
    unittest.main()
