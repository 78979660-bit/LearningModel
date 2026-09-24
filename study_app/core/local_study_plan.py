from __future__ import annotations

import logging
from datetime import date

from study_app.core.active_subjects import (
    dashboard_activity_subject_names as activity_subject_names,
    dashboard_activity_subjects as activity_subjects,
    dashboard_plan_subject_scope_label as plan_subject_scope_label,
    require_activity_subject,
)
from study_app.core.dashboard import DashboardState
from study_app.core.practice_bank import first_diagnostic_difficulty_cap, generate_practice_assignment
from study_app.core.study_plan_homework import _daily_plan, _lowest_cs_oj_focus, _matches_plan_subject

LOGGER = logging.getLogger(__name__)

def _balanced_homework_topic(
    subject: str,
    current_line: str,
    *,
    as_of_date: date,
) -> str | None:
    if not subject:
        return None
    try:
        from study_app.core.study_phase import is_final_review, weighted_topic_priority_states
    except Exception:
        LOGGER.exception("Final-review topic balancing is unavailable for %s", subject)
        return None
    if not is_final_review(subject):
        return None

    text = current_line or ""
    series_terms = (
        "\u7ea7\u6570",
        "\u4e00\u81f4\u6536\u655b",
        "\u5e42\u7ea7\u6570",
        "CALC-SERIES",
        "MIX-CALC-SERIES-UNIFORM",
    )
    if not any(term in text for term in series_terms):
        return None

    try:
        states = weighted_topic_priority_states(
            subject,
            limit=16,
            as_of_date=as_of_date,
        )
    except Exception:
        LOGGER.exception("Weighted topic priorities could not be read for %s", subject)
        return None
    if not states:
        return None

    def is_series_item(item: dict) -> bool:
        raw = f"{item.get('module') or ''} {item.get('topic') or ''}"
        return any(term in raw for term in series_terms[:3])

    series_saturation = max(
        (float(item.get("recent_review_saturation") or 0) for item in states if is_series_item(item)),
        default=0.0,
    )
    series_recent_count = max(
        (int(item.get("recent_module_count") or 0) for item in states if is_series_item(item)),
        default=0,
    )
    if series_saturation < 0.35 and series_recent_count < 3:
        return None

    for item in states:
        if is_series_item(item):
            continue
        if float(item.get("recent_review_saturation") or 0) >= 0.40:
            continue
        module = str(item.get("module") or "").strip()
        topic = str(item.get("topic") or "").strip()
        if not topic:
            continue
        return " / ".join(part for part in (subject, module, topic) if part)

    return None


def apply_dynamic_difficulty_to_plan(
    plan: dict[str, list[str]],
    selected_subject: str | None = None,
    *,
    as_of_date: date,
) -> dict[str, list[str]]:
    import re
    from statistics import median
    from study_app.core.study_phase import is_final_review

    updated = {key: list(value) if isinstance(value, list) else value for key, value in plan.items()}
    if selected_subject == "计算机科学":
        return updated
    short_lines = []
    subjects = [
        "高等数学",
        "微积分",
        "高级程序设计",
        "计算机科学",
        "数据结构与算法基础",
        "大学物理学",
        "化学原理",
    ]
    mixed_topic_keywords = [
        "编程实践",
        "实现训练",
        "代码实现",
        "调试",
        "LeetCode",
        "AVL",
        "哈希表",
        "散列表",
        "复杂度",
        "递归",
        "二叉搜索树",
        "KMP",
        "图结构",
        "DFS",
        "BFS",
        "最短路径",
        "最小生成树",
        "Gauss",
        "Green",
        "Stokes",
        "曲线积分",
        "曲面积分",
        "三重积分",
        "级数",
        "相对论",
        "热力学",
        "相平衡",
        "化学动力学",
    ]
    for index, line in enumerate(plan.get("short", []), start=1):
        subject = selected_subject or next((item for item in subjects if item in line), "")
        template_topic_match = re.search(
            r"题库模板[:：]\s*([A-Z0-9_-]+)\s*[:：]\s*([^；。\n]+)",
            line,
        )
        topic_match = re.search(
            r"(?:主攻|围绕|复习|专项练习：围绕)\s*([^，。；\n]+(?:\s*/\s*[^，。；\n]+)?)",
            line,
        )
        if template_topic_match:
            topic = f"{template_topic_match.group(1).strip()}: {template_topic_match.group(2).strip()}"
        else:
            topic = topic_match.group(1).strip() if topic_match else line
        if subject and subject not in topic:
            topic = f"{subject} / {topic}"
        balanced_topic = _balanced_homework_topic(
            subject,
            line,
            as_of_date=as_of_date,
        )
        assignment_topic = balanced_topic or topic
        assignment = generate_practice_assignment(assignment_topic, 1)
        difficulty = assignment.difficulty_score
        mixed_difficulty = None
        component_topics = sorted(
            {keyword for keyword in mixed_topic_keywords if keyword in line},
            key=line.find,
        )
        if component_topics:
            component_scores = [
                generate_practice_assignment(
                    f"{subject} / {component}" if subject else component,
                    day=1,
                ).difficulty_score
                for component in component_topics
            ]
            if len(component_scores) == 1:
                difficulty = component_scores[0]
            else:
                combination_bonus = 2
                difficulty = min(92, round(median(component_scores) + combination_bonus))
        if is_final_review(subject):
            difficulty = max(72, min(92, difficulty + 4))
            if mixed_difficulty is not None:
                mixed_difficulty = max(76, min(92, mixed_difficulty + 4))
        diagnostic_cap = first_diagnostic_difficulty_cap(subject, line) if subject else None
        if diagnostic_cap is not None:
            difficulty = min(difficulty, diagnostic_cap)
            if mixed_difficulty is not None:
                mixed_difficulty = min(mixed_difficulty, diagnostic_cap)
        replacement_index = 0

        def replace_difficulty(_match):
            nonlocal replacement_index
            replacement_index += 1
            target = mixed_difficulty if mixed_difficulty is not None and replacement_index > 1 else difficulty
            return f"参考难度 {target}/100"

        replaced = re.sub(r"参考难度\s*\d+(?:\.\d+)?\s*/\s*100", replace_difficulty, line)
        if "当天作业：" in replaced:
            homework_text = assignment.to_homework_text()
            if difficulty != assignment.difficulty_score:
                homework_text = re.sub(
                    r"参考难度\s*\d+(?:\.\d+)?\s*/\s*100",
                    f"参考难度 {difficulty}/100",
                    homework_text,
                    count=1,
                )
            replaced = re.sub(
                r"当天作业：.*?(?=\n(?:完成标准|复盘记录|调整依据|$)|$)",
                f"当天作业：{homework_text}",
                replaced,
                flags=re.S,
            )
        short_lines.append(replaced)
    updated["short"] = short_lines
    return updated


def study_plan_llm_payload(state: DashboardState, subject_name: str | None = None) -> dict[str, object]:
    from study_app.core.mock_exam import mock_exam_readiness
    from study_app.core.study_phase import get_subject_phase, is_in_exam_scope, load_learning_model

    active_names = set(activity_subject_names(state))
    allowed_subjects = [
        name for name in activity_subject_names(state)
        if subject_name is None or name == subject_name
    ]

    subjects = [
        {
            "name": subject.name,
            "initial_score": subject.initial_score,
            "window_score": subject.window_score,
            "mastery_score": subject.mastery_score,
            "covered_mastery_score": subject.covered_mastery_score,
            "covered_topic_count": subject.covered_topic_count,
            "total_topic_count": subject.total_topic_count,
            "latest_record": subject.latest_record.isoformat() if subject.latest_record else None,
            "latest_review": subject.latest_review.isoformat() if subject.latest_review else None,
            "warnings": list(subject.warnings),
        }
        for subject in activity_subjects(state)
        if subject_name is None or subject.name == subject_name
    ]
    todos = [
        {
            "kind": item.kind,
            "title": item.title,
            "detail": item.detail,
            "level": item.level,
            "priority": item.priority,
        }
        for item in state.todos
        if _matches_plan_subject(item.title, subject_name)
        and any(_matches_plan_subject(item.title, name) for name in active_names)
    ]
    memory_risks = [
        shrink_risk_item(item)
        for item in state.memory_risks
        if (
            item.get("subject") in active_names
            and (
                subject_name is None
                or (
                    item.get("subject") == subject_name
                    and is_in_exam_scope(subject_name, item.get("module"), item.get("topic"))
                )
            )
        )
    ][:8]
    bkt_alerts = [
        shrink_risk_item(item)
        for item in state.bkt_alerts
        if (
            item.get("subject") in active_names
            and (
                subject_name is None
                or (
                    item.get("subject") == subject_name
                    and is_in_exam_scope(subject_name, item.get("module"), item.get("topic"))
                )
            )
        )
    ][:8]
    phase_policy = get_subject_phase(subject_name) if subject_name else None
    readiness = (
        mock_exam_readiness(state, subject_name)
        if subject_name and phase_policy and phase_policy.get("phase") == "final_review"
        else None
    )
    weighted_topics = [
        shrink_risk_item(item)
        for item in getattr(state, "weighted_priorities", ())
        if subject_name and item.get("subject") == subject_name
    ][:5]
    return {
        "subject_scope": plan_subject_scope_label(subject_name),
        "allowed_subjects": allowed_subjects,
        "study_phase": phase_policy,
        "mock_exam_readiness": readiness,
        "weighted_topic_priorities": weighted_topics,
        "window": {
            "start": state.start.isoformat(),
            "today": state.today.isoformat(),
            "benchmark": state.benchmark,
        },
        "subjects": subjects,
        "todos": todos[:4],
        "memory_risks": memory_risks[:4],
        "bkt_alerts": bkt_alerts[:4],
        "low_subjects": [
            subject for subject in state.low_subjects
            if subject in active_names and (subject_name is None or subject == subject_name)
        ],
        "stale_subjects": [
            subject.name for subject in state.stale_subjects
            if subject.name in active_names and (subject_name is None or subject.name == subject_name)
        ],
    }


def shrink_risk_item(item: dict) -> dict[str, object]:
    keys = [
        "subject",
        "module",
        "topic",
        "level",
        "priority",
        "recall",
        "target_recall",
        "days_since",
        "mastery_probability",
        "target_mastery",
        "observation_count",
        "final_review_reason",
        "priority_reason",
        "weighted_priority",
        "mastery_deficit",
        "forgetting_risk",
        "recent_error",
        "recent_coverage_gap",
        "recent_review_saturation",
        "recent_module_count",
        "recent_topic_count",
        "recent_window_size",
        "phase_label",
        "priority_weights",
    ]
    return {key: item.get(key) for key in keys if key in item}


def generate_study_plan(state: DashboardState, subject_name: str | None = None) -> dict[str, list[str]]:
    from study_app.core.mock_exam import mock_exam_readiness
    from study_app.core.study_phase import get_subject_phase, is_in_exam_scope

    active_names = set(activity_subject_names(state))
    if subject_name:
        require_activity_subject(state, subject_name, "生成每日学习计划")

    phase_policy = get_subject_phase(subject_name) if subject_name else {}
    final_review = phase_policy.get("phase") == "final_review"
    readiness = mock_exam_readiness(state, subject_name) if final_review and subject_name else None
    todos = [
        item for item in state.todos
        if _matches_plan_subject(item.title, subject_name)
        and any(_matches_plan_subject(item.title, name) for name in active_names)
    ]
    subjects = [
        subject for subject in activity_subjects(state)
        if subject_name is None or subject.name == subject_name
    ]
    priority_subjects = []
    for subject in subjects:
        if subject.window_score is not None and subject.window_score < state.benchmark:
            priority_subjects.append(subject.name)
        elif subject.covered_topic_count and subject.covered_mastery_score < state.benchmark:
            priority_subjects.append(subject.name)
        elif subject.warnings:
            priority_subjects.append(subject.name)
    priority_subjects = list(dict.fromkeys(priority_subjects))[:3]

    memory_risks = [
        item for item in state.memory_risks
        if item.get("subject") in active_names
        and (
            subject_name is None
            or (
                item.get("subject") == subject_name
                and is_in_exam_scope(subject_name, item.get("module"), item.get("topic"))
            )
        )
    ]
    bkt_alerts = [
        item for item in state.bkt_alerts
        if item.get("subject") in active_names
        and (
            subject_name is None
            or (
                item.get("subject") == subject_name
                and is_in_exam_scope(subject_name, item.get("module"), item.get("topic"))
            )
        )
    ]
    if subject_name == "计算机科学":
        bkt_alerts = [
            item for item in bkt_alerts
            if "算法设计与OJ训练" in str(item.get("module") or "")
        ]
    mastery_candidates = [
        item for item in getattr(state, "weighted_priorities", ())
        if subject_name and item.get("subject") == subject_name
    ] if subject_name else bkt_alerts
    if subject_name == "计算机科学":
        mastery_candidates = [
            item for item in mastery_candidates
            if "算法设计与OJ训练" in str(item.get("module") or "")
        ]
    fallback_subject = subject_name or "当前记录较少的学科"
    memory_topics = [
        " / ".join(str(item.get(key) or "") for key in ("subject", "module", "topic") if item.get(key))
        for item in memory_risks[:5]
    ]
    bkt_topics = [
        " / ".join(str(item.get(key) or "") for key in ("subject", "module", "topic") if item.get(key))
        for item in bkt_alerts[:5]
    ]
    todo_topics = [item.title for item in todos[:5]]
    if subject_name:
        mastery_topics = [
            " / ".join(str(item.get(key) or "") for key in ("subject", "module", "topic") if item.get(key))
            for item in mastery_candidates[:5]
        ]
        if subject_name == "计算机科学":
            oj_candidates = [
                item for item in mastery_candidates
                if "算法设计与OJ训练" in str(item.get("module") or "")
            ]
            if oj_candidates:
                mastery_topics = [
                    " / ".join(
                        str(item.get(key) or "")
                        for key in ("subject", "module", "topic")
                        if item.get(key)
                    )
                    for item in oj_candidates[:5]
                ]
        focus_topics = list(dict.fromkeys(mastery_topics + todo_topics + memory_topics))[:6]
        if subject_name == "计算机科学":
            oj_focus = _lowest_cs_oj_focus()
            if oj_focus:
                focus_topics = [oj_focus] + [
                    item for item in focus_topics
                    if "算法设计与OJ训练" in item and item != oj_focus
                ]
                focus_topics = list(dict.fromkeys(focus_topics))[:6]
        low_mastery_names = [
            " / ".join(
                str(item.get(key) or "")
                for key in ("module", "topic")
                if item.get(key)
            )
            for item in mastery_candidates
            if item.get("topic")
        ][:3]
        if final_review and low_mastery_names:
            dominant_reason = str(mastery_candidates[0].get("priority_reason") or "")
            diagnostic_title = (
                "数据结构期末综合诊断"
                if subject_name == "数据结构与算法基础"
                else f"{subject_name}期末综合诊断"
            )
            comprehensive = (
                f"{subject_name} / {diagnostic_title}："
                + "、".join(low_mastery_names)
                + f"；{dominant_reason or '跨章节综合'}"
            )
            focus_topics.insert(0, comprehensive)
    else:
        focus_topics = list(dict.fromkeys(todo_topics + memory_topics + bkt_topics))[:6]
    primary_focus = focus_topics[0] if focus_topics else fallback_subject
    focus_subjects = "、".join(priority_subjects) if priority_subjects else fallback_subject
    memory_focus = "；".join(memory_topics) if memory_topics else f"{fallback_subject}中最近新学或久未复习的知识点"
    bkt_focus = "；".join(bkt_topics) if bkt_topics else f"{fallback_subject}中近期做题暴露出的不稳定题型"

    judgement = []
    if final_review:
        judgement.append(
            f"{subject_name} 已进入期末复习阶段：当前使用加权优先级排序，"
            "掌握度缺口权重较高，遗忘风险仍作为辅助信号。"
        )
        scope_label = str((phase_policy.get("exam_scope") or {}).get("label") or "")
        if scope_label:
            judgement.append(f"考试范围限定为 {scope_label}；计划、练习与模拟卷准备度均不使用范围外知识点。")
    elif priority_subjects:
        judgement.append(f"当前优先对象是 {focus_subjects}：存在低于标杆、久未课外复习或窗口证据不足。")
    else:
        judgement.append(f"当前范围为 {plan_subject_scope_label(subject_name)}，没有强低分信号，计划重点放在巩固和防遗忘。")
    if final_review and readiness:
        covered_lines = [
            f"{subject_name} 考试范围掌握度 {readiness.get('scope_mastery', 0)}/100"
            f"（覆盖 {readiness.get('scope_covered_count', 0)}/{readiness.get('scope_topic_count', 0)} 个范围内知识点）"
            for _subject in subjects
        ]
    else:
        covered_lines = [
            f"{subject.name} 已学掌握度 {subject.covered_mastery_score}/100"
            + (
                f"（覆盖 {subject.covered_topic_count}/{subject.total_topic_count} 个知识点）"
                if subject.total_topic_count
                else ""
            )
            + f"，整门课总掌握度 {subject.mastery_score}/100"
            for subject in subjects
        ]
    if covered_lines:
        judgement.append("已学范围口径：" + "；".join(covered_lines[:4]) + "。")
    model_warning = ""
    if subject_name:
        try:
            from study_app.core.study_phase import load_learning_model

            subject_model = next(
                (
                    item for item in load_learning_model().get("subjects", [])
                    if item.get("name") == subject_name
                ),
                None,
            )
            module_lines = [
                f"{module.get('name')} {float(module.get('mastery', 0)):.0%}"
                for module in (subject_model or {}).get("modules", [])
            ]
            if module_lines:
                judgement.append("主模块掌握度：" + "；".join(module_lines) + "。")
        except FileNotFoundError:
            model_warning = "学科模型文件不存在，主模块掌握度暂不可用。"
            LOGGER.info("Subject model file is absent for %s", subject_name)
        except Exception:
            model_warning = "学科模型读取失败，主模块掌握度暂不可用。"
            LOGGER.exception("Subject model details could not be read for %s", subject_name)
    if memory_risks:
        top = memory_risks[0]
        judgement.append(
            f"{'遗忘曲线辅助参考' if final_review else '遗忘曲线最高优先级'}：{top['subject']} / {top['topic']}，"
            f"当前回忆概率约 {top['recall']:.0%}，目标 {top['target_recall']:.0%}。"
        )
    if mastery_candidates:
        top = mastery_candidates[0]
        reason = str(top.get("priority_reason") or "")
        judgement.append(
            (
                f"{top.get('phase_label', '当前模式')}加权优先项：{top['subject']} / {top['topic']}，"
                f"综合优先级 {float(top.get('weighted_priority', top.get('priority', 0))):.2f}；{reason}；"
                + (
                    f"已有证据下掌握概率约 {top['mastery_probability']:.0%}，目标 {top['target_mastery']:.0%}。"
                    if int(top.get("observation_count") or 0) > 0
                    else f"目前没有题目级证据，按初始掌握概率约 {top['mastery_probability']:.0%} 参与排序。"
                )
                if final_review
                else f"BKT 低掌握题型：{top['subject']} / {top['topic']}，P(掌握) 约 {top['mastery_probability']:.0%}，目标 {top['target_mastery']:.0%}。"
            )
        )
    if not memory_risks and not mastery_candidates:
        judgement.append("当前模型缺少足够的题目级证据，计划会更偏向建立记录闭环。")

    goals = [
        f"今天围绕 {primary_focus} 完成一个“诊断低掌握点 -> 综合练习 -> 错因复盘”的闭环。"
        if final_review
        else f"今天围绕 {primary_focus} 完成一个“回忆复习 -> 针对练习 -> 错因复盘”的闭环。",
        "每次学习后至少记录：做题数量、正确率、错因、是否独立完成。",
        "综合题与跨章节题占今日练习的比例不低于 60%，为整份模拟卷训练积累覆盖证据。"
        if final_review
        else "优先处理能同时降低遗忘风险和 BKT 预警的知识点，而不是平均铺开所有内容。",
        "明天根据新记录重新生成计划，对仍低掌握或尚未诊断的项目继续保留。"
        if final_review
        else "明天根据新记录重新生成计划，对仍高风险的项目继续保留。"
    ]

    short = _daily_plan(
        focus_topics,
        todos,
        memory_risks,
        bkt_alerts,
        fallback_subject,
        phase_policy=phase_policy,
        readiness=readiness,
    )
    if subject_name == "数据结构与算法基础":
        diagnostic_error_types = "概念遗忘、题面建模失败、算法适用条件误判、过程推演错误、边界条件遗漏、时间管理问题"
    elif subject_name in {"高等数学", "微积分"}:
        diagnostic_error_types = "概念遗忘、题面建模失败、公式适用条件误判、区域/取向判断错误、判别法选择错误、计算错误"
    else:
        diagnostic_error_types = "概念遗忘、题面建模失败、公式适用条件误判、计算错误、边界条件遗漏、时间管理问题"
    diagnostic = [
        "每道错题记录：题目 / 是否独立完成 / 卡住位置 / 错因类型 / 下次复习触发条件。",
        f"错因类型建议：{diagnostic_error_types}。",
        "如果同一错因连续出现 2 次，把它升级为下一轮计划的主攻点。"
    ]
    record_template = [
        "记录：复习【知识点】，完成【题目/数量】，正确率【x%】，错因是【...】，是否独立完成【是/否】。",
        "记录：做了【题号范围】，错了【题号】，其余全对；主要问题是【...】。",
        "记录：完成第 N 天计划，【达成/未达成】完成标准，下一步需要补【...】。"
    ]
    expected = [
        "若今天完成计划，相关知识点的遗忘风险应下降，复习优先级会后移。",
        "若题目级正确率较高，BKT 掌握概率会上升；若仍低，会自动保留为明日重点。",
        "三天窗口分数继续作为近期表现指标，但不再决定计划必须持续三天。"
    ]

    if subject_name == "计算机科学":
        judgement = [
            "计算机科学当前只启用编程实践计划：系统按算法设计知识点、个人掌握度和校准难度匹配 LeetCode 官方原题。",
            "数据结构笔试、概念背诵、教材查漏和手算推演不进入本板块今日计划；它们仍保留在已封存的理论学习数据中。",
        ]
        goals = [
            f"围绕 {primary_focus} 完成可提交、可运行、可由官方测试集判定的代码练习。",
            "至少完成 2 道系统匹配的 LeetCode 官方原题，并记录每次提交的 AC / WA / TLE / RE 状态。",
            "每题保留目标复杂度、关键不变量、失败用例与修正方式，作为后续难度和掌握度更新证据。",
        ]
        diagnostic = [
            "每道未通过题记录：题号 / 提交状态 / 失败用例 / 卡住位置 / 是否查看题解 / 最终复杂度。",
            "错因类型建议：算法选择错误、状态或不变量错误、边界遗漏、复杂度超限、运行时错误、代码实现错误。",
            "只有实际提交与测试结果参与掌握度更新；计划勾选只记录执行进度。",
        ]
        record_template = [
            "记录：完成 LeetCode【题号/标题】，提交结果【AC/WA/TLE/RE】，失败【x】次，主要错因【...】，最终复杂度【...】，是否查看题解【是/否】。",
            "记录：完成算法设计今日计划，共做【x】题，其中【题号】未通过；失败用例或边界问题是【...】。",
        ]
        expected = [
            "系统根据实际提交结果、题目校准难度和错因更新对应算法知识点掌握度。",
            "连续稳定 AC 后提高后续匹配难度；出现 WA / TLE / RE 时优先补同知识点的针对性原题。",
        ]

    evidence = [
        f"计划范围：{plan_subject_scope_label(subject_name)}。",
        f"三天窗口：{state.start.isoformat()} 至 {state.today.isoformat()}，周期标杆 {state.benchmark:.0f} 分。",
        f"当前范围内待办 {len(todos)} 项，遗忘风险 {len(memory_risks)} 项，BKT 预警 {len(bkt_alerts)} 项。",
        "掌握度口径：计划优先使用已学范围掌握度，整门课总掌握度仅作背景。",
        "BKT证据口径：题目级匹配已启用同义词、记录级兜底和重复证据去重。",
    ]
    if model_warning:
        evidence.append(model_warning)
    if subject_name:
        weights = phase_policy.get("priority_weights", {})
        evidence.append(
            f"{phase_policy.get('label', '当前模式')}加权排序："
            f"掌握度缺口 {float(weights.get('mastery_deficit', 0)):.0%}，"
            f"近期错误 {float(weights.get('recent_error', 0)):.0%}，"
            f"遗忘风险 {float(weights.get('forgetting_risk', 0)):.0%}；"
            "无题目级证据时使用初始掌握度。"
        )
        scope_label = str((phase_policy.get("exam_scope") or {}).get("label") or "")
        if scope_label:
            evidence.append(f"期末考试范围：{scope_label}；范围外知识点已从计划候选中排除。")
    if final_review and readiness:
        weights = phase_policy.get("priority_weights", {})
        evidence.extend(
            [
                f"综合练习目标占比：{float(phase_policy.get('comprehensive_practice_ratio', 0.6)):.0%}。",
                f"模拟卷准备度：{readiness['score']}/100（{readiness['label']}）。",
                "模拟卷覆盖缺口：" + "、".join(readiness["gaps"] or ["暂无明显缺口"]),
            ]
        )
        scope_submodules = readiness.get("scope_submodules") or []
        if scope_submodules:
            evidence.append(
                "考试范围分模块掌握度："
                + "；".join(
                    f"{item.get('submodule')} {float(item.get('mastery', 0)):.0%}"
                    for item in scope_submodules
                )
                + "。"
            )
    for subject in subjects:
        if subject.total_topic_count:
            evidence.append(
                f"{subject.name}：已学掌握度 {subject.covered_mastery_score}/100，"
                f"覆盖 {subject.covered_topic_count}/{subject.total_topic_count} 个知识点；"
                f"整门课总掌握度 {subject.mastery_score}/100。"
            )
    low_subjects = [
        subject for subject in state.low_subjects
        if subject in active_names and (subject_name is None or subject == subject_name)
    ]
    stale_subjects = [
        item.name for item in state.stale_subjects
        if item.name in active_names and (subject_name is None or item.name == subject_name)
    ]
    if low_subjects:
        evidence.append("低于标杆学科：" + "、".join(low_subjects))
    if stale_subjects:
        evidence.append("课外复习间隔较长：" + "、".join(stale_subjects))
    return {
        "judgement": judgement,
        "goals": goals,
        "short": short,
        "diagnostic": diagnostic,
        "record_template": record_template,
        "expected": expected,
        "evidence": evidence,
    }
