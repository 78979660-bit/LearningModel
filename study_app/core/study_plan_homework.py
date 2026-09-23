from __future__ import annotations

from study_app.core.dashboard import TodoItem
from study_app.core.practice_bank import generate_practice_assignment


def _lowest_cs_oj_focus() -> str | None:
    try:
        from study_app.core.study_phase import load_learning_model

        model = load_learning_model()
        subject = next(
            item for item in model.get("subjects", [])
            if item.get("name") == "计算机科学"
        )
        module = next(
            item for item in subject.get("modules", [])
            if item.get("name") == "算法设计与OJ训练"
        )
        topics = [
            item for item in module.get("topics", [])
            if isinstance(item, dict) and item.get("name")
        ]
        if not topics:
            return None
        topics.sort(
            key=lambda item: (
                float(item.get("mastery", 0.5) or 0.5),
                -float(item.get("difficulty", 0.0) or 0.0),
            )
        )
        return f"计算机科学 / 算法设计与OJ训练 / {topics[0]['name']}"
    except Exception:
        return None


def _daily_plan(
    focus_topics: list[str],
    todos: list[TodoItem],
    memory_risks: list[dict],
    bkt_alerts: list[dict],
    fallback_subject: str,
    phase_policy: dict | None = None,
    readiness: dict | None = None,
) -> list[str]:
    phase_policy = phase_policy or {}
    final_review = phase_policy.get("phase") == "final_review"
    first = focus_topics[0] if focus_topics else fallback_subject
    if _is_cs_oj_focus(first, fallback_subject):
        return [_oj_daily_plan_text(first)]
    first_homework = _homework_for_topic(first)
    first_standard = _completion_standard_for_topic(first)
    memory_detail = ""
    if memory_risks:
        item = memory_risks[0]
        memory_detail = f"重点压低遗忘风险：当前回忆 {item['recall']:.0%}，目标 {item['target_recall']:.0%}。"
    bkt_detail = ""
    if bkt_alerts:
        item = bkt_alerts[0]
        bkt_detail = f"重点提升题型掌握：P(掌握) {item['mastery_probability']:.0%}，目标 {item['target_mastery']:.0%}。"
    todo_detail = "；".join(f"{item.kind}：{item.title}" for item in todos[:3])
    if not todo_detail:
        todo_detail = f"围绕 {first} 建立新的题目级证据。"
    if final_review:
        gap_text = "、".join((readiness or {}).get("gaps", [])[:3]) or "当前低掌握知识点"
        if "数据结构" in first or fallback_subject == "数据结构与算法基础":
            review_actions = "概念、算法步骤、边界条件和复杂度判断"
            training_style = "跨章节综合、结构比较、过程推演或算法设计题"
        elif "高等数学" in first or fallback_subject in {"高等数学", "微积分"}:
            review_actions = "概念、公式适用条件、积分区域/取向、收敛判别和计算步骤"
            training_style = "跨专题综合、条件判定、区域/方向分析或证明计算题"
        else:
            review_actions = "概念、公式适用条件、边界条件和计算步骤"
            training_style = "跨专题综合、过程推演或应用计算题"
        return [
            (
                f"今日期末综合诊断\n"
                f"诊断自测：主攻 {first}，先完成一组跨章节短测，定位低掌握知识点而非单纯检查是否遗忘。\n"
                f"查漏补缺：只复盘短测中暴露出的{review_actions}；当前整卷覆盖缺口为 {gap_text}。\n"
                f"综合训练：至少 60% 题目采用{training_style}。\n"
                f"当天作业：{first_homework}\n"
                "完成标准：独立完成整组综合诊断题，覆盖至少 3 个知识点；"
                "上传逐题作答、耗时与错因，用于更新知识点掌握度和模拟卷准备度。\n"
                f"复盘记录：逐题记录对应知识点、错误类型、耗时和是否独立完成。\n"
                f"调整依据：下一次计划继续优先最低掌握知识点，并根据覆盖缺口逐步形成整份模拟卷。"
            ),
        ]
    return [
        (
            f"今日计划：回忆与重建\n"
            f"回忆自测：主攻 {first}，先不看资料，用 8-10 分钟写出核心定义、公式、算法流程或解题框架。\n"
            f"查漏补缺：对照资料补齐遗漏点 30-45 分钟，重点标出适用条件、边界条件和常见误判。{memory_detail}\n"
            f"方法整理：把今天最容易错的 2 个判断点写成“如果……则……”形式。\n"
            f"当天作业：{first_homework}\n"
            f"完成标准：{first_standard}\n"
            f"复盘记录：记录回忆是否卡住、补了哪些点、作业正确率和主要错因。\n"
            f"复盘记录：记录回忆是否卡住、作业完成情况；具体正确率与错因通过新增记录上传。\n"
            f"调整依据：明天根据上传记录重新生成计划；若表现稳定则推进，否则继续保留该题型。"
        ),
    ]


def _is_cs_oj_focus(topic: str, fallback_subject: str) -> bool:
    text = f"{fallback_subject} {topic}"
    return fallback_subject == "计算机科学" or ("计算机科学" in text and (
        "算法设计与OJ训练" in text
        or "OJ" in text
        or "LeetCode" in text
        or "leetcode" in text
    ))


def _oj_topic_hint(topic: str) -> str:
    routes = [
        ("滑动窗口", "双指针与滑动窗口"),
        ("双指针", "双指针与滑动窗口"),
        ("哈希", "数组、哈希与前缀结构"),
        ("前缀", "数组、哈希与前缀结构"),
        ("数组", "数组、哈希与前缀结构"),
        ("栈", "栈、队列与单调结构"),
        ("队列", "栈、队列与单调结构"),
        ("单调", "栈、队列与单调结构"),
        ("链表", "链表与指针技巧"),
        ("二叉树", "二叉树、BST与堆"),
        ("BST", "二叉树、BST与堆"),
        ("堆", "堆与优先队列"),
        ("优先队列", "堆与优先队列"),
        ("图", "图搜索、并查集与最短路"),
        ("并查集", "图搜索、并查集与最短路"),
        ("最短路", "图搜索、并查集与最短路"),
        ("回溯", "递归、回溯与分治"),
        ("递归", "递归、回溯与分治"),
        ("分治", "递归、回溯与分治"),
        ("动态规划", "动态规划基础"),
        ("DP", "动态规划基础"),
        ("字符串", "字符串与模式匹配"),
        ("KMP", "字符串与模式匹配"),
    ]
    for keyword, hint in routes:
        if keyword in topic:
            return hint
    return "数组、哈希与前缀结构"


def _oj_seed_lines(topic: str, limit: int = 3) -> list[str]:
    return [line for line, _score in _oj_seed_line_scores(topic, limit)]


def _oj_seed_line_scores(topic: str, limit: int = 3) -> list[tuple[str, float | None]]:
    try:
        from study_app.data.practice_repository import find_practice_problems
        from study_app.data.database import recent_study_plan_texts

        hint = _oj_topic_hint(topic)
        assignment = generate_practice_assignment(f"计算机科学 / 算法设计与OJ训练 / {hint}", 1)
        problems = find_practice_problems(
            template_id="CS-OJ-PRACTICE",
            subject="计算机科学",
            topic=hint,
            difficulty=assignment.difficulty_score,
            limit=max(limit * 8, 24),
        )
        recent_text = "\n".join(recent_study_plan_texts("计算机科学", limit=5))
        fresh_problems = [
            problem for problem in problems
            if str(problem.get("title") or "").strip()
            and str(problem.get("title") or "").strip() not in recent_text
        ]
        repeated_problems = [problem for problem in problems if problem not in fresh_problems]
        problems = (fresh_problems + repeated_problems)[:limit]
        lines = []
        for problem in problems:
            raw = problem.get("raw") or {}
            url = raw.get("url") or ""
            level = raw.get("difficulty_label") or ""
            band = raw.get("difficulty_band") or ""
            score = problem.get("difficulty_score")
            score_text = f"，校准难度 {float(score):.0f}/100" if isinstance(score, (int, float)) else ""
            band_text = f"，{band}" if band else ""
            title = problem.get("title") or ""
            abstract = raw.get("abstract") or ""
            lines.append((f"{title}（官方 {level}{score_text}{band_text}）：{abstract}；{url}", score))
        return lines
    except Exception:
        return []


def _oj_daily_plan_text(topic: str) -> str:
    hint = _oj_topic_hint(topic)
    seeds = _oj_seed_line_scores(topic, limit=3)
    if seeds:
        seed_text = "\n".join(f"  {index}. {line}" for index, (line, _score) in enumerate(seeds, start=1))
    else:
        seed_text = "  1. 从 LeetCode 官方题库中选择与本主题匹配的 2 道原题。"
    assignment = generate_practice_assignment(f"计算机科学 / 算法设计与OJ训练 / {hint}", 1)
    seed_scores = [float(score) for _line, score in seeds if isinstance(score, (int, float))]
    reference_difficulty = round(sum(seed_scores) / len(seed_scores)) if seed_scores else assignment.difficulty_score
    reference_label = (
        "困难" if reference_difficulty >= 75 else "中高" if reference_difficulty >= 62 else
        "中等" if reference_difficulty >= 48 else "基础"
    )
    return (
        "今日计划：OJ 原题训练\n"
        f"训练主题：{hint}。目标不是复述概念，而是完成可提交代码并用官方测试集验证。\n"
        "今日原题：\n"
        f"{seed_text}\n"
        "执行步骤：\n"
        "  1. 先独立读题并写出输入规模、目标复杂度和核心数据结构。\n"
        "  2. 不看题解完成第一版代码，提交到 LeetCode，记录 AC / WA / TLE / RE。\n"
        "  3. 若未 AC，只查看失败用例和自己的调试输出，先定位边界条件或状态维护问题。\n"
        "  4. AC 后用 5 分钟复盘：关键不变量、复杂度、最容易写错的一行。\n"
        f"当天作业：参考难度 {reference_difficulty}/100（{reference_label}，按所选 LeetCode 原题校准分折算）；"
        f"题库模板 CS-OJ-PRACTICE：LeetCode 官方原题索引；选做 2-3 题。\n"
        "完成标准：至少提交 2 道官方原题；记录每题提交结果、失败次数、最终复杂度和主要错因。\n"
        "复盘记录：记录题号、是否 AC、卡住位置、失败用例类型、是否看题解；新增记录上传后再更新掌握度。"
    )


def _homework_for_topic(topic: str) -> str:
    return generate_practice_assignment(topic, 1).to_homework_text()


def _completion_standard_for_topic(topic: str) -> str:
    assignment = generate_practice_assignment(topic, 1)
    prefix = f"参考难度 {assignment.difficulty_score}/100；"
    if any(keyword in topic for keyword in ["AVL", "哈希", "KMP", "复杂度", "算法", "数据结构"]):
        return prefix + "能不看资料写出核心定义/流程，并独立完成至少 1 道基础手算题。"
    if any(keyword in topic for keyword in ["Gauss", "Green", "Stokes", "曲面积分", "曲线积分", "曲面积分", "级数"]):
        return prefix + "能写出公式适用条件、变量/区域/方向含义，并独立完成至少 1 道题。"
    return prefix + "能口述核心概念，并完成一次 10 分钟无资料回忆。"


def _matches_plan_subject(title: str, subject_name: str | None) -> bool:
    return subject_name is None or title.startswith(f"{subject_name} /")
