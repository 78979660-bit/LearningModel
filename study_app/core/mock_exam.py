from __future__ import annotations

from typing import Any

from study_app.core.composite_templates import COMPOSITE_TEMPLATES
from study_app.data.database import DEFAULT_DB_PATH, load_raw_records


def _blueprint_for_subject(subject: str, exam_scope: dict[str, Any] | None) -> list[str]:
    if subject == "\u5316\u5b66\u539f\u7406":
        return [
            "\u6807\u51c6\u671f\u672b\u5377\u56fa\u5b9a\u4e3a 13 \u9898\uff0c\u603b\u5206 100 \u5206\uff0c\u5fc5\u987b\u6309\u8003\u8bd5\u9898\u578b\u56db\u4e2a\u9876\u5c42\u5206\u533a\u7ec4\u5377\u3002",
            "\u4e00\u3001\u5206\u6790\u76f8\u56fe\uff1a2 \u9898\uff0c\u6bcf\u9898 12 \u5206\uff0c\u5171 24 \u5206\uff1b\u91cd\u70b9\u8986\u76d6\u6b65\u51b7\u66f2\u7ebf\u3001\u4e8c\u5143\u76f8\u56fe\u3001\u76f8\u6001/\u81ea\u7531\u5ea6\u5224\u65ad\u548c\u6760\u6746\u89c4\u5219\u3002",
            "\u4e8c\u3001\u63a8\u5bfc\u4e0e\u8bc1\u660e\uff1a4 \u9898\uff0c\u6bcf\u9898 4 \u5206\uff0c\u5171 16 \u5206\uff1b\u91cd\u70b9\u4e3a\u76f8\u5f8b\u63a8\u5bfc\u4e0e\u5e94\u7528\u3001Arrhenius \u516c\u5f0f\u53d8\u5f62\u548c\u901f\u7387\u65b9\u7a0b\u57fa\u7840\u63a8\u5bfc\u3002",
            "\u4e09\u3001\u7b80\u7b54\u9898\uff1a3 \u9898\uff0c3+3+4 \u5206\uff0c\u5171 10 \u5206\uff1b\u4ee5\u6982\u5ff5\u8fa8\u6790\u3001\u56fe\u50cf\u542b\u4e49\u548c\u516c\u5f0f\u9002\u7528\u6761\u4ef6\u4e3a\u4e3b\u3002",
            "\u56db\u3001\u8ba1\u7b97\u9898\uff1a4 \u9898\uff0c16+12+12+10 \u5206\uff0c\u5171 50 \u5206\uff1b\u8986\u76d6\u963f\u4f26\u5c3c\u4e4c\u65af\u516c\u5f0f\u3001\u901f\u7387\u5e38\u6570/\u534a\u8870\u671f\u3001\u76f8\u5f8b\u81ea\u7531\u5ea6\u548c\u6c34-\u9152\u7cbe\u4e8c\u5143\u7cfb\u76f8\u56fe\u5e94\u7528\u3002",
            "\u5fc5\u8003\u91cd\u70b9\uff1a\u4f8b\u9898\u548c\u4e60\u9898\u4e2d\u7684\u6b65\u51b7\u66f2\u7ebf\u3001\u963f\u4f26\u5c3c\u4e4c\u65af\u516c\u5f0f\u3001\u76f8\u5f8b\u63a8\u5bfc\u4e0e\u5e94\u7528\u3001\u6c34-\u9152\u7cbe\u4e8c\u5143\u7cfb\u76f8\u56fe\u5e94\u7528\u3002",
            "\u7981\u8003\uff1a\u8fc7\u6e21\u6001\u3001\u5149\u5316\u5b66\u53cd\u5e94\u3001\u5feb\u901f\u53cd\u5e94\uff1b\u4efb\u4f55\u5377\u578b\u90fd\u4e0d\u5f97\u5f15\u5165\u8fd9\u4e09\u7c7b\u5185\u5bb9\u3002",
        ]
    if subject == "数据结构与算法基础":
        question_types = (exam_scope or {}).get("exam_question_types") or ["选择题", "填空题", "解答题", "算法题"]
        return [
            "题型依据：按《复习（2026春季）》最后一页组织为 " + "、".join(question_types) + " 四类。",
            "标准期末卷必须以这四类作为顶层分区；不得把判断题作为独立大类，判断性质可并入选择题或填空题。",
            "第1章绪论与复杂度：10%",
            "第2至4章线性表、栈队列、数组串与广义表：20%",
            "第5至7章树、集合字典与搜索结构：30%",
            "第8章图：20%",
            "第9章排序：15%",
            "跨章节设计与综合：5%",
        ]
    if subject == "高等数学":
        weights = (exam_scope or {}).get("mock_exam_weights") or {}
        multivar = round(float(weights.get("多元函数积分学", 0.60)) * 100)
        series = round(float(weights.get("级数", 0.40)) * 100)
        return [
            f"多元函数积分学：{multivar}%",
            "内容覆盖：二重积分、三重积分、第一/二型曲线积分、第一/二型曲面积分、Green/Stokes/Gauss 公式。",
            f"级数：{series}%",
            "内容覆盖：数项级数敛散性、一致收敛性、幂级数与函数收敛域、Taylor 级数、逐项求导/积分、Cauchy 根值判别与 D'Alembert 比值判别。",
            "排除：傅里叶级数、场论、Dirichlet/Abel 一致收敛判别法；普通数项级数 Dirichlet/Abel 判别可用。",
        ]
    if subject == "大学物理学":
        weights = (exam_scope or {}).get("mock_exam_weights") or {}
        chapter_weights = (exam_scope or {}).get("chapter_mock_exam_weights") or {}
        relativity = float(weights.get("狭义相对论", 0.2778)) * 100
        thermo = float(weights.get("宏观热力学与相变", 0.5852)) * 100
        microscopic = float(weights.get("理想气体微观模型", 0.1370)) * 100
        chapter_lines = [
            f"{chapter}：约 {float(weight) * 100:.1f}%"
            for chapter, weight in chapter_weights.items()
        ]
        return [
            "真实样卷标尺：南京大学 University Physics I 9 套样卷（54 道大题）。",
            f"三大板块：狭义相对论约 {relativity:.1f}%；宏观热力学与相变约 {thermo:.1f}%；理想气体微观模型约 {microscopic:.1f}%。",
            *chapter_lines,
            "样卷题型风格：每道大题含 2-4 个递进小问；历史样卷的“6选5、每题20分”不作为当前模拟卷题量规则。",
            "章级占比来自样卷统计，单份生成卷允许约 ±3 个百分点浮动，但第9章与第12章应保持为主要板块。",
            "硬性必含题型：至少 1 道第13章 Maxwell 速率分布与 Gamma 函数积分结合题，需显式考查 ∫_0^∞ v^n exp(-a v^2) dv、Gamma 函数或速率矩 <v^r>。",
        ]
    return [
        "基础概念与计算：20%",
        "核心专题：50%",
        "综合应用：30%",
    ]


def mock_exam_readiness(state: Any, subject: str) -> dict[str, Any]:
    from study_app.core.study_phase import (
        exam_scope_label,
        exam_scope_submodule_stats,
        get_subject_phase,
        is_in_exam_scope,
    )

    summary = next((item for item in state.subjects if item.name == subject), None)
    if summary is None:
        return {"score": 0, "label": "暂无数据", "gaps": [], "blueprint": []}

    phase_policy = get_subject_phase(subject)
    exam_scope = phase_policy.get("exam_scope") if isinstance(phase_policy, dict) else None

    bkt = [
        item
        for item in state.bkt_alerts
        if item.get("subject") == subject
        and is_in_exam_scope(subject, item.get("module"), item.get("topic"))
    ]
    low_mastery = sorted(bkt, key=lambda item: float(item.get("mastery_probability", 0)))
    try:
        source_records = getattr(state, "raw_records", ()) or load_raw_records(DEFAULT_DB_PATH)
        records = [
            item
            for item in source_records
            if item.get("subject") == subject
            and is_in_exam_scope(subject, item.get("module"), item.get("topic"))
        ]
    except Exception:
        source_records = []
        records = []

    recent_problem_count = sum(
        int(item.get("problem_count") or len(item.get("problems") or []))
        for item in records[-20:]
    )
    composite_templates = [item for item in COMPOSITE_TEMPLATES if item.subject == subject]

    scope_stats = exam_scope_submodule_stats(subject, records=source_records)
    scoped_topic_count = sum(int(item.get("topic_count") or 0) for item in scope_stats)
    scoped_covered_count = sum(int(item.get("covered_count") or 0) for item in scope_stats)
    coverage = scoped_covered_count / scoped_topic_count if scoped_topic_count else 0.0
    mastery = (
        sum(float(item.get("mastery") or 0.0) * int(item.get("topic_count") or 0) for item in scope_stats)
        / scoped_topic_count
        if scoped_topic_count
        else summary.covered_mastery_score / 100
    )
    evidence = min(1.0, recent_problem_count / 45)
    composite = min(1.0, len(composite_templates) / 4)
    score = round(100 * (0.30 * coverage + 0.35 * mastery + 0.20 * evidence + 0.15 * composite))

    gaps = [
        " / ".join(part for part in (str(item.get("module") or ""), str(item.get("topic") or "")) if part)
        for item in low_mastery[:4]
        if item.get("topic")
    ]
    if len(composite_templates) < 4:
        gaps.append("综合题模板覆盖不足")
    if recent_problem_count < 30:
        gaps.append("近期整卷题量证据不足")

    label = "可生成首份诊断模拟卷" if score >= 65 else "继续积累整卷覆盖证据"
    return {
        "score": score,
        "label": label,
        "gaps": list(dict.fromkeys(gaps))[:6],
        "blueprint": _blueprint_for_subject(subject, exam_scope),
        "recent_problem_count": recent_problem_count,
        "composite_template_count": len(composite_templates),
        "exam_scope": exam_scope_label(subject),
        "scope_submodules": scope_stats,
        "scope_topic_count": scoped_topic_count,
        "scope_covered_count": scoped_covered_count,
        "scope_mastery": round(mastery * 100),
    }
