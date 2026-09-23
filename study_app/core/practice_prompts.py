from __future__ import annotations

import logging

from study_app.core.active_subjects import require_activity_subject
from study_app.core.dashboard import DashboardState
from study_app.core.practice_bank import known_template_ids
from study_app.core.practice_spec import (
    _extract_homework_component_counts,
    _extract_homework_components,
    desired_practice_seed_count,
    extract_homework_exercise_count,
    extract_template_brief,
    infer_practice_template,
    normalize_homework_count,
)
from study_app.core.study_plan_homework import _oj_topic_hint
from study_app.core.study_plan_items import infer_subject_topic_from_plan_line


LOGGER = logging.getLogger(__name__)


def _short_seed_text(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def mock_exam_seed_context(subject: str, mode: str) -> str:
    """Build a compact seed block for full-paper generation.

    Daily practice prompts already use same-template seeds. Mock papers need a
    broader, exam-scope seed map; otherwise the LLM only sees abstract chapter
    weights and tends to reuse a small set of familiar stories.
    """
    from study_app.data.practice_repository import find_practice_problems

    if subject == "大学物理学":
        template_groups: list[tuple[str, list[str], int, float]] = [
            ("第9章 狭义相对论", ["PHYS-REL-EVENTS", "PHYS-REL-LIFETIME", "PHYS-REL-VELOCITY", "PHYS-REL-DOPPLER", "PHYS-REL-DYNAMICS", "PHYS-REL-DECAY"], 6 if mode == "diagnostic" else 4, 78),
            ("第10章 温度与状态方程", ["PHYS-TEMP-EOS"], 2, 74),
            ("第11章 热力学第一定律", ["PHYS-FIRST-LAW"], 2, 76),
            ("第12章 第二定律、熵与热力学函数", ["PHYS-SECOND-LAW-ENGINE", "PHYS-THERMO-ENTROPY", "PHYS-THERMO-POTENTIAL"], 5 if mode != "sprint" else 3, 82),
            ("第13章 理想气体微观模型（必须含 Maxwell-Gamma）", ["PHYS-STATISTICAL"], 3, 86),
            ("第14章 相变", ["PHYS-PHASE-TRANSITION"], 2, 78),
        ]
    elif subject == "数据结构与算法基础":
        template_groups = [
            ("复杂度与绪论", ["ALG-COMPLEXITY"], 2, 72),
            ("树、字典与查找结构", ["DS-BST-OPS", "DS-AVL-ROT", "DS-HASH-ASL"], 5, 78),
        ]
    elif subject == "高等数学":
        template_groups = [
            ("多元函数积分学", ["CALC-TRIPLE-INTEGRAL", "CALC-GAUSS-FLUX", "MIX-CALC-STOKES-CURL"], 6, 78),
            ("级数", ["CALC-SERIES"], 4, 76),
        ]
    elif subject == "化学原理":
        template_groups = [
            ("一、分析相图（2题×12分）：步冷曲线、二元相图、相区/自由度、杠杆规则", ["CHEM-COOLING-CURVE", "CHEM-WATER-ALCOHOL-PHASE", "MIX-CHEM-EQUILIBRIUM-PHASE"], 6, 80),
            ("二、推导与证明（4题×4分）：相律、Arrhenius 变形、速率方程基础推导", ["CHEM-FINAL-SHORT-PROOF", "CHEM-EQUILIBRIUM", "CHEM-KINETICS"], 5, 74),
            ("三、简答题（3题，3+3+4分）：概念辨析、图像含义、公式适用条件", ["CHEM-FINAL-SHORT-PROOF", "CHEM-EQUILIBRIUM", "CHEM-KINETICS"], 4, 68),
            ("四、计算题（4题，16+12+12+10分）：Arrhenius、速率常数/半衰期、相律、二元系相图", ["CHEM-KINETICS", "MIX-CHEM-EQUILIBRIUM-PHASE", "CHEM-WATER-ALCOHOL-PHASE"], 8, 78),
        ]
    else:
        template_groups = [("考试范围题型", [""], 6, 72)]

    lines: list[str] = []
    seen_titles: set[str] = set()
    group_keywords: dict[str, list[str]] = {}
    if subject == "化学原理":
        group_keywords = {
            "一、分析相图（2题×12分）：步冷曲线、二元相图、相区/自由度、杠杆规则": ["相图", "步冷", "冷却", "T-x", "T-w", "杠杆", "共熔", "水-酒精", "水-乙醇", "固液", "气液"],
            "二、推导与证明（4题×4分）：相律、Arrhenius 变形、速率方程基础推导": ["推导", "证明", "相律", "自由度", "Arrhenius", "阿伦尼乌斯", "速率方程", "积分式"],
            "三、简答题（3题，3+3+4分）：概念辨析、图像含义、公式适用条件": ["概念", "含义", "适用", "判断", "自由度", "组分数", "相数", "泡点", "露点", "半衰期"],
            "四、计算题（4题，16+12+12+10分）：Arrhenius、速率常数/半衰期、相律、二元系相图": ["计算", "求", "速率常数", "半衰期", "Arrhenius", "活化能", "蒸气压", "杠杆", "质量", "组成"],
            "必考重点补充：步冷曲线、阿伦尼乌斯公式、相律、水-酒精二元系": ["步冷", "冷却曲线", "阿伦尼乌斯", "Arrhenius", "相律", "水-酒精", "水-乙醇"],
        }
    excluded_seed_keywords = ["过渡态", "光化学", "快速反应", "弛豫法", "闪光光解"] if subject == "化学原理" else []
    if subject == "大学物理学":
        lines.append(
            "- 第13章 理想气体微观模型硬性必含：PHYS-STATISTICAL｜88/100｜Maxwell 速率分布与 Gamma 函数积分：围绕 f(v)=C v^2 exp(-a v^2) 构造递进题，必须显式考查 ∫_0^∞ v^n exp(-a v^2) dv、Gamma((n+1)/2)、归一化或速率矩 <v^r>；可扩展到平均速率、方均根速率或逸出分子束通量加权分布。"
        )
    for group_name, template_ids, group_limit, difficulty in template_groups:
        group_items: list[dict] = []
        per_template_limit = max(1, (group_limit + len(template_ids) - 1) // max(1, len(template_ids)))
        for template_id in template_ids:
            problems = find_practice_problems(
                template_id=template_id or None,
                subject=subject,
                difficulty=difficulty,
                limit=per_template_limit * (6 if group_name in group_keywords else 1),
                prefer_real_sources=True,
            )
            for problem in problems:
                title = str(problem.get("title") or "")
                haystack = " ".join(
                    str(problem.get(key) or "")
                    for key in ("template_id", "title", "statement", "topic_hint", "tags_json", "source_note")
                )
                if excluded_seed_keywords and any(keyword in haystack for keyword in excluded_seed_keywords):
                    continue
                keywords = group_keywords.get(group_name) or []
                if keywords and not any(keyword in haystack for keyword in keywords):
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                group_items.append(problem)
                if len(group_items) >= group_limit:
                    break
            if len(group_items) >= group_limit:
                break
        if not group_items:
            lines.append(f"- {group_name}：暂无可用题库种子，请按考试范围自行生成同型题。")
            continue
        lines.append(f"- {group_name}：")
        for problem in group_items[:group_limit]:
            title = _short_seed_text(problem.get("title"), 48)
            template_id = problem.get("template_id") or ""
            difficulty_score = problem.get("difficulty_score")
            difficulty_text = f"{float(difficulty_score):.0f}/100" if difficulty_score is not None else "未标定"
            statement = _short_seed_text(problem.get("statement"), 110)
            lines.append(f"  · {template_id}｜{difficulty_text}｜{title}：{statement}")
    if not lines:
        return "- 暂无考试范围题库种子；请严格按考试范围和章节比例生成。"
    return "\n".join(lines)


def practice_distribution_guidance(homework: str, exercise_count: int) -> str:
    explicit_components = _extract_homework_component_counts(homework)
    if explicit_components:
        lines = [f"- {component}：建议 {count} 题" for component, count in explicit_components]
        total = sum(count for _component, count in explicit_components)
        if total != exercise_count:
            lines.append(f"- 注意：显式题型配额合计 {total} 题，与解析题量 {exercise_count} 题不一致；请优先核对当前作业规格。")
        lines.append("- 若题型配额与题库模板不一致，优先服从当前作业规格和考试范围。")
        return "\n".join(lines)
    components = _extract_homework_components(homework)
    if not components:
        return "- \u672a\u8bc6\u522b\u5230\u660e\u786e\u590d\u5408\u9898\u578b\uff1b\u8bf7\u6309\u9898\u5e93\u6a21\u677f\u8986\u76d6\u57fa\u7840\u3001\u4e2d\u7b49\u548c\u6613\u9519\u53d8\u5f0f\uff0c\u5e76\u4fdd\u6301\u96be\u5ea6\u63a5\u8fd1\u53c2\u8003\u96be\u5ea6\u3002"
    base = exercise_count // len(components)
    extra = exercise_count % len(components)
    lines = []
    for index, component in enumerate(components):
        count = base + (1 if index < extra else 0)
        lines.append(f"- {component}\uff1a\u5efa\u8bae {count} \u9898")
    lines.append("- \u82e5\u9898\u578b\u914d\u989d\u4e0e\u9898\u5e93\u6a21\u677f\u4e0d\u4e00\u81f4\uff0c\u4f18\u5148\u670d\u4ece\u5f53\u524d\u4f5c\u4e1a\u89c4\u683c\u548c\u8003\u8bd5\u8303\u56f4\u3002")
    return "\n".join(lines)


def build_mock_exam_generation_prompt(state: DashboardState, subject: str, mode: str = "diagnostic") -> str:
    from study_app.core.mock_exam import mock_exam_readiness
    from study_app.core.study_phase import exam_scope_label, get_subject_phase, is_final_review, weighted_topic_priority_states
    from study_app.data.text_integrity import validate_text_integrity

    if not subject:
        raise ValueError("请先选择一个具体学科。")
    require_activity_subject(state, subject, "生成模拟卷")

    mode = mode if mode in {"diagnostic", "standard", "sprint"} else "diagnostic"
    mode_policy = {
        "diagnostic": {
            "name": "诊断卷",
            "difficulty": "正常难度，整体参考难度约 68-74/100。",
            "count": "题量较多，建议 12-15 道小题或等价题量。",
            "focus": "题型不受当前掌握度和遗忘曲线强约束，核心目标是完整覆盖考试范围内主要知识点，用于检测尚未发现的疏漏。",
            "distribution": "覆盖优先，薄弱点只作为排序参考，不应挤占范围内其他关键知识点。",
        },
        "standard": {
            "name": "标准期末卷",
            "difficulty": "平均难度较高，整体参考难度约 76-82/100；必须有难有易，避免全卷同质化。",
            "count": "严格参考考试卷结构、知识点比重和参考题目数。",
            "focus": "按考试范围与蓝图出卷，同时兼顾薄弱点；重点是模拟真实期末卷的结构和节奏。",
            "distribution": "建议基础题约 25%，中等题约 45%，综合/区分度题约 30%。",
        },
        "sprint": {
            "name": "冲刺专题卷",
            "difficulty": "偏难到较难，整体参考难度约 82-90/100。",
            "count": "题型可以较少，建议 4-7 道综合题或专题题。",
            "focus": "用于重难点突破训练，可做成专题卷，优先围绕当前最低掌握、近期错因和高权重考点。",
            "distribution": "不追求完整覆盖，追求高价值薄弱点突破；但仍不得超出考试范围。",
        },
    }[mode]
    if subject == "高等数学" and mode == "standard":
        mode_policy["count"] = "标准期末卷设置 8 到 10 个大题；每道大题的小问数量按题型需要设置，不强行固定。必须按多元函数积分学 60%、级数 40% 配题。"
    if subject == "数据结构与算法基础":
        ds_counts = {
            "diagnostic": "诊断卷按复习 PDF 最后一页的四类题型完整覆盖：选择题、填空题、解答题、算法题；题量可略多，用于覆盖第1至第9章主要题型。",
            "standard": "标准期末卷必须按复习 PDF 最后一页的四类题型组织：选择题、填空题、解答题、算法题；四类均需出现，且作为卷面顶层分区。",
            "sprint": "冲刺专题卷仍以选择题、填空题、解答题、算法题为顶层题型；可减少题量并提高解答题、算法题比例，但不得改成其他题型结构。",
        }
        mode_policy["count"] = ds_counts[mode]
    if subject == "大学物理学":
        physics_counts = {
            "diagnostic": "诊断卷设置 14-16 道必做题，每题可含若干小问；题量用于扩大第9-14章覆盖，不设置选做。",
            "standard": "标准期末卷固定设置 10 道必做大题；每道大题的小问数量按题型需要设置，不设置选做。",
            "sprint": "冲刺专题卷设置 6-8 道必做综合题；每题可含递进小问，不设置选做。",
        }
        mode_policy["count"] = physics_counts[mode]

    phase = get_subject_phase(subject)
    scope_label = exam_scope_label(subject)
    readiness = mock_exam_readiness(state, subject)
    blueprint = readiness.get("blueprint") or []
    gaps = readiness.get("gaps") or []
    weak_items = (
        weighted_topic_priority_states(subject, limit=10, as_of_date=state.today)
        if is_final_review(subject)
        else []
    )
    weak_lines = []
    for item in weak_items[:8]:
        module = str(item.get("module") or "")
        topic = str(item.get("topic") or "")
        mastery = round(float(item.get("mastery_probability") or 0) * 100)
        recall = round(float(item.get("recall") or 0) * 100)
        priority = round(float(item.get("weighted_priority") or item.get("priority") or 0), 3)
        weak_lines.append(f"- {module} / {topic}：掌握度约 {mastery}%，回忆概率约 {recall}%，优先级 {priority}")

    scope_block = f"考试范围：{scope_label}\n" if scope_label else "考试范围：按当前学科已学范围与期末复习设置。\n"
    if not is_final_review(subject):
        scope_block += "注意：该学科尚未启用期末复习模式，请生成诊断型模拟卷，题量可略少，但仍需严格围绕已学内容。\n"
    seed_context = mock_exam_seed_context(subject, mode)

    extra_rules: list[str] = []
    if subject == "高等数学":
        extra_rules.extend(
            [
                "高等数学模拟卷结构必须按整卷比例执行：多元函数积分学约 60%，级数约 40%。",
                "多元函数积分学范围：二重积分、三重积分、第一/第二型曲线积分、第一/第二型曲面积分、Green/Stokes/Gauss 公式。",
                "级数范围：数项级数敛散性、一致收敛性、幂级数和函数收敛域、Taylor 级数、逐项求导/逐项积分、Cauchy 根值判别、D'Alembert 比值判别、普通数项级数 Dirichlet/Abel 判别。",
                "排除：傅里叶级数、场论、Dirichlet/Abel 一致收敛判别法；不要出现数据结构、算法、复杂度等计算机内容。",
            ]
        )
    elif subject == "数据结构与算法基础":
        extra_rules.extend(
            [
                "数据结构模拟卷范围限定为第 1 至第 9 章，覆盖线性结构、树与二叉树、集合与字典、搜索结构、图、排序和算法复杂度。",
                "题型必须严格按照《复习（2026春季）》最后一页：选择题、填空题、解答题、算法题。请把这四类作为卷面顶层分区。",
                "不要把判断题设为独立大类；若需要考查判断性质，请并入选择题或填空题。",
                "解答题可覆盖手算过程、结构构造、查找/排序过程、图算法过程和平均查找长度等；算法题覆盖算法设计、伪代码、正确性说明与复杂度分析。",
                "排除：外排序、B/B+树、红黑树、网络流、后缀数组等范围外内容。",
            ]
        )
    elif subject == "化学原理":
        extra_rules.extend(
            [
                "\u5316\u5b66\u539f\u7406\u6807\u51c6\u671f\u672b\u5377\u5fc5\u987b\u6309\u56fa\u5b9a\u9898\u578b\u7ed3\u6784\u7ec4\u5377\uff1a\u4e00\u3001\u5206\u6790\u76f8\u56fe 2 \u9898\uff0c\u6bcf\u9898 12 \u5206\uff1b\u4e8c\u3001\u63a8\u5bfc\u4e0e\u8bc1\u660e 4 \u9898\uff0c\u6bcf\u9898 4 \u5206\uff1b\u4e09\u3001\u7b80\u7b54\u9898 3 \u9898\uff0c\u5206\u503c 3+3+4\uff1b\u56db\u3001\u8ba1\u7b97\u9898 4 \u9898\uff0c\u5206\u503c 16+12+12+10\u3002",
                "\u8303\u56f4\u9650\u5b9a\u4e3a\u7b2c 12\u300113 \u7ae0\uff1a\u76f8\u5e73\u8861/\u76f8\u56fe\u4e0e\u5316\u5b66\u53cd\u5e94\u52a8\u529b\u5b66\u3002",
                "\u5fc5\u987b\u91cd\u70b9\u8986\u76d6\uff1a\u4f8b\u9898\u548c\u4e60\u9898\u4e2d\u7684\u6b65\u51b7\u66f2\u7ebf\u3001\u963f\u4f26\u5c3c\u4e4c\u65af\u516c\u5f0f\u3001\u76f8\u5f8b\u63a8\u5bfc\u4e0e\u5e94\u7528\u3001\u6c34-\u9152\u7cbe\u4e8c\u5143\u7cfb\u76f8\u56fe\u5e94\u7528\u3002",
                "\u4e25\u683c\u7981\u6b62\u51fa\u73b0\uff1a\u8fc7\u6e21\u6001\u3001\u5149\u5316\u5b66\u53cd\u5e94\u3001\u5feb\u901f\u53cd\u5e94\u3001\u5f1b\u8c6b\u6cd5\u3001\u95ea\u5149\u5149\u89e3\u7b49\u8303\u56f4\u5916\u5185\u5bb9\u3002",
                "\u76f8\u56fe\u5206\u6790\u9898\u4f18\u5148\u53c2\u8003\u6b65\u51b7\u66f2\u7ebf PDF \u79cd\u5b50\uff1aBi-Cd \u4f4e\u5171\u7194\u76f8\u56fe\u3001Sb-Cd \u6b65\u51b7\u66f2\u7ebf\u6570\u636e\u4f5c\u56fe\u3001Sn-Ag \u76f8\u56fe\u533a\u57df\u4e0e\u51b7\u5374\u66f2\u7ebf\u3002",
                "\u540c\u4e00\u9876\u5c42\u9898\u578b\u5185\u4e0d\u5f97\u8fde\u7eed\u51fa\u73b0\u540c\u4e00\u5c0f\u9898\u578b\uff1b\u6807\u51c6\u5377\u4e2d\u7684 13 \u9898\u5e94\u81f3\u5c11\u8986\u76d6 8 \u4e2a\u4e0d\u540c\u5c0f\u9898\u578b\uff0c\u4f8b\u5982\u6b65\u51b7\u66f2\u7ebf\u3001\u6c34-\u9152\u7cbe\u4e8c\u5143\u7cfb\u3001\u76f8\u5f8b\u63a8\u5bfc\u3001\u7ec4\u5206\u6570/\u81ea\u7531\u5ea6\u3001Arrhenius\u3001\u79ef\u5206\u901f\u7387\u65b9\u7a0b\u3001\u534a\u8870\u671f\u3001Clausius-Clapeyron \u6216\u6760\u6746\u89c4\u5219\u3002",
            ]
        )
    elif subject == "大学物理学":
        from study_app.core.physics_mock_exam_reference import mock_exam_reference_block
        from study_app.core.physics_ii_mock_exam_reference import (
            mock_exam_reference_block as modern_reference_block,
            model_has_modern_physics,
        )

        extra_rules.extend(mock_exam_reference_block(mode).splitlines())
        extra_rules.extend(
            [
                "大学物理考试范围严格限定为第 9 至第 14 章。",
                "范围内内容：狭义相对论；温度与气体状态方程；热力学第一定律；热力学第二定律、熵与热力学函数；理想气体微观模型；相变。",
                "排除第 2 至第 8 章的力学、刚体、振动和波动，也排除量子、原子、核与粒子物理。",
                "历史样卷采用6选5，但当前模拟卷不得沿用选做制：诊断卷14-16题、标准卷固定10题、冲刺卷6-8题，所有题均计入卷面。",
                "标准卷10题的推荐章节骨架为：第9章3题、第10章1题、第11章1题、第12章3题、第13章1题、第14章1题；跨章节题可调整归属，但应通过分值配置贴近章级比例。",
                "诊断卷若取15题，可参考第9章4题、第10章1题、第11章2题、第12章5题、第13章2题、第14章1题；冲刺卷按薄弱重难点选题，不强求六章齐全。",
                "所有大学物理模拟卷必须至少包含 1 道 Maxwell 速率分布与 Gamma 函数结合的题目，位置归入第13章理想气体微观模型；题目应显式出现 ∫_0^∞ v^n exp(-a v^2) dv、Gamma 函数或速率矩 <v^r> 的推导/计算。",
                "该 Maxwell-Gamma 题不得只停留在背公式；至少要求完成归一化、平均速率/方均根速率、一般速率矩或逸出分子束通量加权分布中的一个推导。",
            ]
        )
        if "量子" in scope_label and model_has_modern_physics(getattr(state, "model_data", {}) or {}):
            extra_rules.extend(modern_reference_block().splitlines())

    prompt = (
        "[PERSONAL_LEARNING_OS_PDF_WORKFLOW]\n"
        "请根据 Personal Learning OS 的学习数据生成一份期末复习模拟卷，并输出可下载 PDF。\n\n"
        f"学科：{subject}\n"
        f"卷型：{mode_policy['name']}\n"
        f"{scope_block}"
        f"模拟卷准备度：{readiness.get('score', 0)}/100（{readiness.get('label', '')}）\n"
        "卷型策略（优先级高于后续通用出卷要求）：\n"
        f"- 难度：{mode_policy['difficulty']}\n"
        f"- 题量：{mode_policy['count']}\n"
        f"- 目标：{mode_policy['focus']}\n"
        f"- 难度/覆盖分配：{mode_policy['distribution']}\n"
        "模拟卷蓝图：\n"
        + "\n".join(f"- {line}" for line in blueprint)
        + "\n\n覆盖缺口与薄弱点：\n"
        + "\n".join(f"- {line}" for line in (gaps or ["暂无明显覆盖缺口"]))
        + "\n\n当前加权优先级最高的知识点：\n"
        + "\n".join(weak_lines or ["- 暂无可用加权优先级数据，请按考试范围均衡出卷。"])
        + "\n\n考试范围题库种子参考（用于避免题型重复；请生成同型变式，不要直接复制原题）：\n"
        + seed_context
        + "\n\n额外约束：\n"
        + "\n".join(f"- {line}" for line in extra_rules)
        + "\n\n出卷要求：\n"
        "1. 生成一整份模拟卷，而不是零散练习；题目必须全部位于考试范围内。\n"
        "2. 难度必须服从卷型策略：诊断卷正常难度且覆盖优先，标准期末卷平均难度较高且有难有易，冲刺卷偏难且可做专题突破；不要机械堆砌冷门偏题。\n"
        "3. 诊断卷以完整覆盖为先，不要过度追随当前掌握度/遗忘曲线；标准期末卷必须服从考试比重和题数；冲刺专题卷可集中突破重难点。高数整卷比例只用于模拟卷，不用于平时计划练习。\n"
        "4. 每题标注题型、考查点、预估难度和建议用时。\n"
        "5. 题目区与答案区必须分离：题目后不要紧跟答案、关键步骤或常见错因。\n"
        "6. 答案与解析统一放在卷末，题号必须与题目区完全对应。\n"
        "7. 生成前自行检查：范围外内容、题量冲突、答案正确性、符号/公式排版和实际难度。\n\n"
        "PDF 排版要求：\n"
        "1. 请生成并提供一份可下载 PDF，不要只返回聊天文本。\n"
        "2. 首页注明学科、考试范围、建议用时、总分或题量结构。\n"
        "3. 每道题之间保留足够作答空白；计算题、证明题和画图题预留更多空间。\n"
        "4. 使用专业数学/算法/化学符号排版，避免乱码、字符重叠、公式截断。\n"
    )
    validate_text_integrity(prompt, context="模拟卷提示词")
    return prompt


def build_practice_generation_prompt(
    line: str,
    state: DashboardState,
    selected_subject: str | None = None,
) -> str:
    import re

    subject, topic = infer_subject_topic_from_plan_line(line, selected_subject)
    require_activity_subject(state, subject, "生成每日题型或练习")
    homework = line.split("当天作业：", 1)[-1].strip()
    if "当前作业规格：" in homework:
        homework = homework.split("当前作业规格：", 1)[-1].strip()
    difficulty_match = re.search(r"参考难度\s*(\d+(?:\.\d+)?)\s*/\s*100", homework)
    template_match = re.search(r"题库模板\s*(?:TEMPLATE_ID\s*)?[:：=]?\s*([A-Za-z0-9_-]+)", homework)
    raw_template_id = template_match.group(1) if template_match else ""
    inferred_template_id, inferred_template_brief = infer_practice_template(homework)
    template_id = raw_template_id if raw_template_id in known_template_ids() else inferred_template_id
    difficulty = difficulty_match.group(1) if difficulty_match else "60"
    exercise_count_int = extract_homework_exercise_count(homework, 2)
    exercise_count = str(exercise_count_int)
    homework = normalize_homework_count(homework, exercise_count_int)
    desired_seed_count = desired_practice_seed_count(exercise_count_int)
    template_brief = extract_template_brief(homework, template_id)
    if (
        raw_template_id not in known_template_ids()
        or not template_brief
        or template_brief.startswith("，")
        or template_brief.startswith(",")
        or "题量" in template_brief
    ):
        template_brief = inferred_template_brief
    recent_context = recent_practice_context(subject, topic, state)
    seed_context = practice_seed_context(
        template_id,
        topic,
        float(difficulty),
        subject=subject,
        desired_count=desired_seed_count,
        exercise_count=exercise_count_int,
    )
    distribution_context = practice_distribution_guidance(homework, exercise_count_int)
    from study_app.core.study_phase import exam_scope_label, is_final_review, out_of_exam_scope_references

    scope_label = exam_scope_label(subject) if is_final_review(subject) else ""
    scope_violations = out_of_exam_scope_references(subject, f"{line}\n{topic}")
    if scope_violations:
        raise ValueError(
            f"{subject} 已进入期末复习，当前题目超出考试范围 {scope_label}："
            + "、".join(scope_violations)
        )
    scope_requirement = (
        f"考试范围：{scope_label}。所有生成题必须严格位于该范围内，不得引入范围外章节知识点。\n"
        if scope_label
        else ""
    )
    if template_id == "CS-OJ-PRACTICE":
        return build_oj_practice_list(line, state, selected_subject)
    prompt = (
        "[PERSONAL_LEARNING_OS_PDF_WORKFLOW]\n"
        "请根据下列轻量题库模板生成练习题。\n\n"
        f"学科：{subject}\n"
        f"{scope_requirement}"
        f"知识点：{topic}\n"
        f"模板编号：{template_id}\n"
        f"模板说明：{template_brief}\n"
        f"参考难度：{difficulty}/100\n"
        f"题量：{exercise_count}\n"
        f"当前作业规格：{homework}\n\n"
        "最近学习证据与错因：\n"
        f"{recent_context}\n\n"
        "历史样题种子：\n"
        f"{seed_context}\n\n"
        "题型覆盖与配额：\n"
        f"{distribution_context}\n\n"
        "出题要求：\n"
        "1. 题目难度应贴近参考难度，允许上下浮动 5 分。\n"
        "2. 难度标尺同等优先对标用户上传作业、课堂练习、教材题、真实学习记录，以及经过质量校验的 MIT、Princeton 等可信大学官方题目；AI 生成题只可参考题型，不可作为难度标尺。\n"
        "3. 每题给出题面、考查点、预估难度、标准答案或关键步骤。\n"
        "4. 不要直接重复教材原题，可以生成同型变式题。\n"
        "5. 题目应能判断“做对/有误”，并能归纳错因。\n"
        "6. 若最近错因与模板相关，优先围绕该错因生成题目。\n"
        "7. 若历史样题种子少于本次题量的一半，不要机械复制种子；应按“题型覆盖与配额”扩展变式，并保持难度贴近参考难度。\n\n"
        "请现在直接生成题目，不要只返回格式模板，也不要留下空字段。\n"
        "每道题请按以下字段完整填写：\n"
        "题目编号与标题\n"
        "题面：写出可直接作答的完整题目。\n"
        "考查点：列出 2-4 个核心考查点。\n"
        "预估难度：给出 0-100 分，并说明与参考难度的关系。\n"
        "答案/关键步骤：给出标准答案或关键推导步骤。\n"
        "常见错因：写出 1-3 个容易出错的位置。"
    )
    final_prompt = prompt + (
        "\n\nPDF 交付与排版要求：\n"
        "1. 在完成题目设计后，生成并提供一份可下载的 PDF 练习册，不要只返回聊天文本。\n"
        "2. PDF 分为“练习题”和“答案与解析”两部分；练习题部分不得紧跟答案、关键步骤或常见错因，"
        "所有答案、解析和错因统一置于文档末尾。\n"
        "3. 每道题之间保留充足的空白作答间距；计算题、证明题和需要画图的题应预留更多空间。\n"
        "4. 使用专业数学排版，确保公式、上下标、积分号、求和号、矩阵、向量和特殊符号清晰正确，"
        "避免乱码、字符重叠和公式被截断。\n"
        "5. PDF 首页注明学科、知识点、参考难度和题量；答案区题号必须与练习题完全对应。\n"
        "6. 生成 PDF 前检查题面完整性、答案正确性、方向或正负号判断以及实际难度是否贴近参考难度。\n"
    )
    from study_app.data.text_integrity import validate_text_integrity

    validate_text_integrity(final_prompt, context="出题提示词")
    return final_prompt


def build_oj_practice_list(
    line: str,
    state: DashboardState,
    selected_subject: str | None = None,
) -> str:
    import re
    from study_app.data.text_integrity import corruption_reason, validate_text_integrity

    def clean_field(value: object, fallback: str = "") -> str:
        text = str(value or "").strip()
        if not text or corruption_reason(text):
            return fallback
        return text

    subject, topic = infer_subject_topic_from_plan_line(line, selected_subject)
    require_activity_subject(state, subject, "生成 OJ 练习清单")
    homework = line.split("当天作业：", 1)[-1].strip()
    difficulty_match = re.search(r"参考难度\s*(\d+(?:\.\d+)?)\s*/\s*100", homework) or re.search(
        r"(\d+(?:\.\d+)?)\s*/\s*100", homework
    )
    target_difficulty = float(difficulty_match.group(1)) if difficulty_match else None
    exercise_count = extract_homework_exercise_count(homework, 3)
    topic_hint = _oj_topic_hint(topic or line)
    topic_hint = clean_field(topic_hint, "算法设计综合训练")
    try:
        from study_app.data.practice_repository import find_practice_problems

        problems = find_practice_problems(
            template_id="CS-OJ-PRACTICE",
            subject="计算机科学",
            topic=topic_hint,
            difficulty=target_difficulty,
            limit=max(2, min(5, exercise_count)),
        )
    except Exception:
        problems = []

    lines = [
        "[PERSONAL_LEARNING_OS_OJ_LIST]",
        "算法设计与 OJ 训练：系统已按题库与难度分数匹配 LeetCode 官方原题；无需让 LLM 生成题目。",
        f"学科：{clean_field(subject, '计算机科学')}",
        f"训练主题：{topic_hint}",
        f"目标参考难度：{target_difficulty:.0f}/100" if target_difficulty is not None else "目标参考难度：按当前计划自动匹配",
        "",
        "今日 LeetCode 原题清单：",
    ]
    if not problems:
        lines.append("- 题库中暂未匹配到足够的 LeetCode 原题，请先补充该主题题库。")
    for index, problem in enumerate(problems, start=1):
        raw = problem.get("raw") or {}
        number = raw.get("problem_number") or ""
        title = clean_field(problem.get("title") or raw.get("title"), "LeetCode 官方题目")
        level = clean_field(raw.get("difficulty_label"))
        band = clean_field(raw.get("difficulty_band"))
        score = float(problem.get("difficulty_score") or 0)
        url = raw.get("url") or (problem.get("source") or {}).get("url") or ""
        abstract = clean_field(
            raw.get("abstract") or problem.get("statement"),
            "题面摘要略；请打开官方链接查看完整题面。",
        )
        lines.append(
            f"{index}. LeetCode {number}｜{title}｜官方 {level}｜校准难度 {score:.0f}/100"
            f"{'｜' + band if band else ''}\n"
            f"   链接：{url}\n"
            f"   训练点：{abstract}"
        )
    lines.extend(
        [
            "",
            "完成要求：",
            "1. 打开官方链接独立提交，不看题解完成第一版。",
            "2. 记录每题 AC / WA / TLE / RE、失败次数、失败用例类型和最终复杂度。",
            "3. 若未 AC，先用失败用例定位边界条件或状态维护问题，再决定是否看题解。",
            "4. 完成后在学习应用新增记录中上传：题号、是否 AC、错因、是否看题解。",
        ]
    )
    result = "\n".join(lines)
    validate_text_integrity(result, context="OJ练习清单")
    return result


def recent_practice_context(subject: str, topic: str, state: DashboardState) -> str:
    lines: list[str] = []
    topic_keys = [part.strip() for part in topic.replace("/", " ").split() if len(part.strip()) >= 2]
    for item in state.memory_risks:
        item_text = f"{item.get('subject', '')} {item.get('topic', '')}"
        if item.get("subject") == subject and _topic_related(item_text, topic, topic_keys):
            lines.append(
                f"- 遗忘风险：{item.get('topic')}，回忆概率 {float(item.get('recall', 0)):.0%}，优先级 {float(item.get('priority', 0)):.1f}。"
            )
    for item in state.bkt_alerts:
        item_text = f"{item.get('subject', '')} {item.get('topic', '')}"
        if item.get("subject") == subject and _topic_related(item_text, topic, topic_keys):
            lines.append(
                f"- BKT 预警：{item.get('topic')}，掌握概率 {float(item.get('mastery_probability', 0)):.0%}。"
            )
    try:
        source_records = list(getattr(state, "raw_records", ()) or [])
        for record in reversed(source_records[-40:]):
            record_subject = str(record.get("subject") or "")
            record_topic = str(record.get("topic") or "")
            note = str(record.get("note") or "")
            score = record.get("score")
            combined = f"{record_subject} {record_topic} {note}"
            has_error_signal = any(marker in combined for marker in ["错", "有误", "遗忘", "不会", "没掌握"])
            if record_subject == subject and _topic_related(combined, topic, topic_keys) and (has_error_signal or (isinstance(score, (int, float)) and score < 80)):
                summary = note[:90] if note else record_topic
                lines.append(f"- 近期记录：{record.get('date', '')}，{summary}")
            if len(lines) >= 5:
                break
    except Exception:
        pass
    if not lines:
        return "- 暂无明确错因；请按模板生成覆盖基础、中等和易错点的变式题。"
    return "\n".join(lines[:5])


def practice_seed_context(
    template_id: str,
    topic: str,
    target_difficulty: float | None = None,
    subject: str | None = None,
    desired_count: int = 3,
    exercise_count: int = 2,
) -> str:
    try:
        from study_app.data.practice_repository import find_practice_problems
        from study_app.core.composite_templates import composite_components

        generic_topic = template_id == "GEN-MIXED-PRACTICE" and any(
            marker in topic
            for marker in ["概念判定", "基础计算/推导", "混合应用与错因归类"]
        )
        components = composite_components(template_id)
        if components:
            problems = find_practice_problems(
                template_id=template_id,
                subject=subject,
                difficulty=target_difficulty,
                limit=desired_count,
            )
            seen_ids = {problem["id"] for problem in problems}
            for component in components:
                for problem in find_practice_problems(
                    subject=subject,
                    topic=component,
                    difficulty=target_difficulty,
                    limit=desired_count,
                ):
                    if problem["id"] not in seen_ids:
                        problems.append(problem)
                        seen_ids.add(problem["id"])
                    if len(problems) >= desired_count:
                        break
                if len(problems) >= desired_count:
                    break
        else:
            problems = find_practice_problems(
                template_id=None if generic_topic else template_id,
                subject=subject if generic_topic else None,
                topic=None if generic_topic else topic,
                difficulty=target_difficulty,
                limit=desired_count,
            )
        if not problems and not generic_topic:
            problems = find_practice_problems(
                template_id=template_id,
                difficulty=target_difficulty,
                limit=desired_count,
            )
        if problems:
            lines = []
            for problem in problems:
                tags = "、".join(problem.get("tags", []))
                source_data = problem.get("source") or {}
                source = source_data.get("title") or problem.get("source_note") or ""
                source_type = source_data.get("type") or ""
                raw = problem.get("raw") or {}
                if template_id == "CS-OJ-PRACTICE" or source_type == "leetcode_metadata":
                    problem_url = raw.get("url") or source_data.get("url") or ""
                    number = raw.get("problem_number") or ""
                    level = raw.get("difficulty_label") or ""
                    band = raw.get("difficulty_band") or ""
                    score = float(problem.get("difficulty_score") or 0)
                    abstract = raw.get("abstract") or problem.get("statement") or ""
                    lines.append(
                        f"- LeetCode {number}｜{problem['title']}｜官方 {level}｜校准难度 {score:.0f}/100"
                        f"{'｜' + band if band else ''}｜{problem_url}："
                        f"{abstract} 标签：{tags}。说明：使用 LeetCode 官方原题与官方测试集，不生成变式题。"
                    )
                    continue
                gap = (
                    abs(float(problem["difficulty_score"]) - float(target_difficulty))
                    if target_difficulty is not None
                    else 0
                )
                if source_type in {"web_gpt_pdf", "ai_generated", "llm_generated"}:
                    calibration_note = "AI生成来源，仅参考题型，不作为难度对标"
                elif source_type == "seed":
                    calibration_note = (
                        f"题型种子，难度与目标相差 {gap:.0f} 分，可辅助约束题型与难度，"
                        "但不作为唯一难度标尺"
                    )
                elif gap <= 12:
                    calibration_note = f"真实来源，难度与目标相差 {gap:.0f} 分，可作为难度对标"
                elif gap <= 20:
                    calibration_note = f"真实来源，难度与目标相差 {gap:.0f} 分，仅作为次级难度参考"
                else:
                    calibration_note = f"真实来源，但难度与目标相差 {gap:.0f} 分，只参考题型，不作为难度对标"
                lines.append(
                    f"- {problem['title']}（参考难度 {problem['difficulty_score']:.0f}/100）："
                    f"{problem['statement']} 考查点：{tags}。来源：{source}；{calibration_note}"
                )
            if len(problems) < desired_count:
                backlog_error = None
                try:
                    from study_app.data.collection_backlog import register_collection_gap

                    register_collection_gap(
                        subject=subject,
                        template_id=template_id,
                        topic=topic,
                        target_difficulty=target_difficulty,
                        note=(
                            f"\u51fa\u9898\u63d0\u793a\u9700\u8981 {desired_count} \u4e2a\u6837\u9898\u79cd\u5b50\uff0c"
                            f"\u5b9e\u9645\u4ec5 {len(problems)} \u4e2a\uff1b\u672c\u6b21\u9898\u91cf {exercise_count}\u3002"
                        ),
                    )
                except Exception as error:
                    backlog_error = error
                    LOGGER.exception("Failed to register practice collection gap")
                backlog_status = (
                    "已登记到待收集表"
                    if backlog_error is None
                    else "待收集登记失败"
                )
                lines.append(
                    f"- \u79cd\u5b50\u6570\u91cf\u4e0d\u8db3\uff1a\u672c\u6b21\u9898\u91cf {exercise_count} \u9053\uff0c"
                    f"\u5efa\u8bae\u81f3\u5c11\u53c2\u8003 {desired_count} \u4e2a\u6837\u9898\uff0c\u5f53\u524d\u4ec5 {len(problems)} \u4e2a\u3002"
                    f"{backlog_status}\uff1b\u751f\u6210\u65f6\u8bf7\u4f18\u5148\u6309\u9898\u578b\u914d\u989d\u6269\u5c55\u540c\u578b\u53d8\u5f0f\uff0c\u4e0d\u8981\u8d85\u51fa\u8003\u8bd5\u8303\u56f4\u3002"
                )
            return "\n".join(lines)
        from study_app.data.collection_backlog import register_collection_gap

        register_collection_gap(
            subject=subject,
            template_id=template_id,
            topic=topic,
            target_difficulty=target_difficulty,
            note=f"\u672a\u627e\u5230\u6837\u9898\u79cd\u5b50\uff1b\u672c\u6b21\u9898\u91cf {exercise_count}\uff0c\u5efa\u8bae\u81f3\u5c11 {desired_count} \u4e2a\u79cd\u5b50\u3002",
        )
    except Exception:
        pass
    return "- 暂无同模板样题种子；请严格按模板说明生成变式题。"


def _topic_related(text: str, topic: str, topic_keys: list[str]) -> bool:
    if topic and topic in text:
        return True
    return any(key in text for key in topic_keys)
