from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from study_app.data.database import DEFAULT_DB_PATH, get_setting, load_raw_records, set_setting


DEFAULT_PHASE = "regular"
FINAL_REVIEW_PHASE = "final_review"
ARCHIVED_PHASE = "archived"
SETTING_PREFIX = "subject_study_phase:"


class ScopeValidationError(RuntimeError):
    """Raised when exam-scope validation cannot complete safely."""


DEFAULT_FINAL_REVIEW_EXAM_SCOPES = {
    "计算机科学": {
        "label": "综合能力训练；数据结构期末理论复习封存为参考数据，算法设计以 OJ/LeetCode 实战为主",
        "modules": ["编程实践与实现训练", "算法设计与OJ训练"],
        "included_topics": [
            "算法题代码实现",
            "数据结构操作代码实现",
            "边界测试与调试定位",
            "复杂度实测与优化",
            "LeetCode与课程实验",
            "算法设计与OJ训练",
            "LeetCode题库训练",
            "数组与哈希表OJ题",
            "双指针与滑动窗口",
            "栈与队列OJ题",
            "二叉树与图搜索",
            "堆与优先队列",
            "动态规划入门",
            "回溯与搜索",
        ],
        "included_keywords": [
            "编程实践",
            "实现训练",
            "代码实现",
            "上机",
            "调试",
            "边界测试",
            "LeetCode",
            "leetcode",
            "复杂度实测",
            "算法设计",
            "OJ",
            "在线评测",
            "LeetCode题库",
            "数组哈希",
            "双指针",
            "滑动窗口",
            "动态规划",
            "回溯",
            "图搜索",
        ],
        "excluded_topics": [],
        "excluded_keywords": [],
    },
    "高等数学": {
        "label": "多元函数积分学与级数（不含傅里叶级数、场论、Dirichlet/Abel一致收敛判别法）",
        "modules": ["多元函数积分学", "级数", "综合应用与题型"],
        "included_submodules": [
            "重积分",
            "曲线积分",
            "曲面积分",
            "三大积分公式",
            "数项级数与判敛",
            "幂级数与函数展开",
            "函数项级数与逐项运算",
            "积分综合",
            "级数综合",
        ],
        "excluded_submodules": [
            "向量场与场论",
            "傅里叶级数",
        ],
        "included_topics": [
            "二重积分",
            "三重积分",
            "第一型曲线积分",
            "第二型曲线积分",
            "第一型曲面积分",
            "第二型曲面积分",
            "Green公式",
            "Stokes公式",
            "Gauss公式",
            "无穷级数与部分和",
            "几何级数",
            "级数收敛的基本性质",
            "正项级数与比较判别法",
            "交错级数与绝对/条件收敛",
            "一致收敛性",
            "幂级数与收敛半径",
            "函数展开为幂级数",
            "Taylor级数",
            "Cauchy根值判别法",
            "D'Alembert比值判别法",
            "Dirichlet数项级数判别法",
            "Abel数项级数判别法",
            "逐项求导与逐项积分",
        ],
        "included_keywords": [
            "二重积分",
            "三重积分",
            "第一型曲线积分",
            "第二型曲线积分",
            "第一型曲面积分",
            "第二型曲面积分",
            "曲线积分",
            "曲面积分",
            "Green",
            "Stokes",
            "Gauss",
            "数项级数",
            "一致收敛",
            "幂级数",
            "收敛域",
            "Taylor",
            "Maclaurin",
            "函数展开为幂级数",
            "Cauchy",
            "D'Alembert",
            "比值判别",
            "根值判别",
            "Dirichlet数项",
            "Abel数项",
            "逐项求导",
            "逐项积分",
        ],
        "excluded_topics": [
            "傅里叶级数",
            "场论",
            "Dirichlet一致收敛判别法",
            "Abel一致收敛判别法",
            "无源场与无旋场",
            "梯度、散度、旋度的综合理解",
        ],
        "excluded_keywords": [
            "傅里叶级数",
            "傅里叶",
            "场论",
            "Dirichlet一致收敛",
            "Abel一致收敛",
            "Dirichlet 一致收敛",
            "Abel 一致收敛",
            "无源场",
            "无旋场",
            "梯度",
            "散度",
            "旋度",
            "向量场",
            "势函数",
            "数据结构",
            "算法设计",
            "算法题",
            "复杂度",
        ],
        "mock_exam_weights": {
            "多元函数积分学": 0.60,
            "级数": 0.40,
        },
    },
    "数据结构与算法基础": {
        "label": "第1至第9章：线性结构、树/图、搜索、内排序与算法复杂度",
        "chapter_ranges": [[1, 9]],
        "modules": [
            "第1章：数据结构概论",
            "第2章：线性表",
            "第3章：栈和队列",
            "第4章：数组、串与广义表",
            "第5章：树与二叉树",
            "第6章：集合与字典",
            "第7章：搜索结构",
            "第8章：图",
            "第9章：排序",
        ],
        "included_topics": [
            "算法性能分析与度量",
            "空间复杂度",
            "时间复杂度",
            "线性表顺序存储与链式存储",
            "顺序表查找、插入、删除",
            "单链表、静态链表、循环链表、双向链表",
            "栈的顺序存储与链式存储",
            "后缀表达式求值",
            "递归与栈",
            "循环队列与链式队列",
            "优先级队列与堆",
            "数组与特殊矩阵",
            "稀疏矩阵",
            "字符串模式匹配",
            "广义表长度、深度、表头、表尾",
            "二叉树性质与存储",
            "二叉树遍历与线索二叉树",
            "树、森林与二叉树转换",
            "堆的向下调整、向上调整、插入、删除",
            "霍夫曼树、WPL与霍夫曼编码",
            "并查集与等价类",
            "散列函数与除留余数法",
            "线性探查与开散列",
            "散列表成功/失败平均查找长度",
            "顺序搜索与折半搜索",
            "二叉搜索树搜索、插入、删除",
            "AVL树旋转与平衡调整",
            "图的基本概念、度、连通与强连通",
            "邻接矩阵与邻接表",
            "DFS与BFS",
            "最小生成树Kruskal与Prim",
            "最短路径Dijkstra与Floyd",
            "AOV网拓扑排序",
            "AOE网关键活动与关键路径",
            "内排序基本概念与稳定性",
            "插入排序、折半插入排序、希尔排序",
            "起泡排序与快速排序",
            "直接选择排序、锦标赛排序、堆排序",
            "二路归并排序迭代实现",
            "排序性能分析",
        ],
        "included_keywords": [
            "ACN",
            "AMN",
            "AVL",
            "AOE",
            "AOV",
            "BFS",
            "DFS",
            "Dijkstra",
            "Floyd",
            "Kruskal",
            "Prim",
            "WPL",
            "并查集",
            "二叉搜索树",
            "二叉树",
            "优先级队列",
            "关键路径",
            "哈希",
            "图",
            "堆",
            "广义表",
            "归并排序",
            "循环队列",
            "快排",
            "快速排序",
            "折半搜索",
            "散列表",
            "时间复杂度",
            "栈",
            "森林",
            "模式匹配",
            "线性探查",
            "线性表",
            "线索二叉树",
            "邻接矩阵",
            "邻接表",
            "队列",
            "霍夫曼",
        ],
        "excluded_topics": [
            "外排序",
            "外部排序",
            "B树",
            "B+树",
            "红黑树",
            "最优二叉搜索树",
            "网络流",
            "高级字符串算法",
        ],
        "excluded_keywords": [
            "外排序",
            "外部排序",
            "B树",
            "B+树",
            "红黑树",
            "最优二叉搜索树",
            "网络流",
            "后缀数组",
            "后缀自动机",
        ],
        "exam_question_types": [
            "选择题",
            "填空题",
            "解答题",
            "算法题",
        ],
    },
    "大学物理学": {
        "label": "第9至第14章：狭义相对论、热力学、气体微观模型与相变",
        "chapter_ranges": [[9, 14]],
        "modules": [
            "第9章：狭义相对论",
            "第10章：温度与气体状态方程",
            "第11章：热力学第一定律",
            "第12章：热力学第二定律与热力学函数",
            "第13章：理想气体的微观模型",
            "第14章：相变",
        ],
        "excluded_keywords": [
            "质点运动学",
            "质点动力学",
            "万有引力",
            "Kepler",
            "多体系统",
            "刚体",
            "振动",
            "波动",
            "量子",
            "Schrodinger",
            "薛定谔",
            "Compton",
            "光电效应",
            "de Broglie",
            "原子物理",
            "核物理",
            "核衰变",
            "粒子物理",
            "夸克",
            "费米气体",
            "玻色气体",
        ],
        "mock_exam_weights": {
            "狭义相对论": 0.2778,
            "宏观热力学与相变": 0.5852,
            "理想气体微观模型": 0.1370,
        },
        "must_include_mock_exam_topics": [
            "Maxwell 速率分布与 Gamma 函数积分",
        ],
        "chapter_mock_exam_weights": {
            "第9章：狭义相对论": 0.2778,
            "第10章：温度与气体状态方程": 0.0667,
            "第11章：热力学第一定律": 0.1259,
            "第12章：热力学第二定律与热力学函数": 0.3130,
            "第13章：理想气体的微观模型": 0.1370,
            "第14章：相变": 0.0796,
        },
    },
    "化学原理": {
        "label": "第12、13章：相平衡/相图与化学动力学（不含过渡态、光化学反应、快速反应）",
        "chapters": [12, 13],
        "modules": ["第12章：相平衡与相变专题", "第13章：化学反应动力学"],
        "included_topics": [
            "相图分析",
            "步冷曲线",
            "冷却曲线",
            "二元固液相图",
            "二元气液相图",
            "水-酒精二元系相图",
            "相律推导与应用",
            "组分数、相数与自由度",
            "杠杆规则",
            "Clapeyron方程",
            "Clausius-Clapeyron方程",
            "反应速率定义",
            "反应级数",
            "积分速率方程",
            "半衰期",
            "Arrhenius公式",
            "阿伦尼乌斯公式",
            "活化能",
            "催化反应速率",
        ],
        "included_keywords": [
            "相图",
            "步冷曲线",
            "冷却曲线",
            "低共熔",
            "共熔",
            "固液相图",
            "气液相图",
            "T-x",
            "T-w",
            "水-酒精",
            "水-乙醇",
            "乙醇-水",
            "相律",
            "自由度",
            "组分数",
            "相数",
            "杠杆规则",
            "Clapeyron",
            "Clausius",
            "速率方程",
            "反应级数",
            "半衰期",
            "Arrhenius",
            "阿伦尼乌斯",
            "活化能",
            "速率常数",
        ],
        "excluded_topics": [
            "过渡态理论",
            "过渡态",
            "光化学反应",
            "光化学动力学",
            "快速反应",
            "快反应",
        ],
        "excluded_keywords": [
            "过渡态",
            "活化熵",
            "光化学",
            "量子产率",
            "快速反应",
            "快反应",
            "弛豫法",
            "闪光光解",
            "碰撞理论与过渡态",
        ],
        "exam_question_types": [
            {"name": "分析相图", "count": 2, "scores": [12, 12], "total": 24},
            {"name": "推导与证明", "count": 4, "scores": [4, 4, 4, 4], "total": 16},
            {"name": "简答题", "count": 3, "scores": [3, 3, 4], "total": 10},
            {"name": "计算题", "count": 4, "scores": [16, 12, 12, 10], "total": 50},
        ],
        "mock_exam_weights": {
            "分析相图": 0.24,
            "推导与证明": 0.16,
            "简答题": 0.10,
            "计算题": 0.50,
        },
        "must_focus": [
            "例题和习题中的步冷曲线",
            "阿伦尼乌斯公式",
            "相律推导与应用",
            "水-酒精二元系相图应用",
        ],
    },
}


FINAL_REVIEW_POLICY = {
    "phase": FINAL_REVIEW_PHASE,
    "label": "期末复习",
    "priority_weights": {
        "mastery_deficit": 0.60,
        "recent_error": 0.25,
        "forgetting_risk": 0.15,
        "recent_coverage_gap": 0.24,
        "recent_review_saturation": 0.22,
    },
    "comprehensive_practice_ratio": 0.60,
    "mock_exam_target_coverage": 0.85,
    "description": "以掌握度缺口为主排序，同时提高综合题和整卷覆盖准备度。",
}

REGULAR_POLICY = {
    "phase": DEFAULT_PHASE,
    "label": "常规学习",
    "priority_weights": {
        "mastery_deficit": 0.25,
        "recent_error": 0.15,
        "forgetting_risk": 0.60,
        "recent_coverage_gap": 0.12,
        "recent_review_saturation": 0.10,
    },
    "comprehensive_practice_ratio": 0.25,
    "description": "以遗忘风险为主排序，同时兼顾掌握度缺口和近期错误。",
}

ARCHIVED_POLICY = {
    "phase": ARCHIVED_PHASE,
    "label": "已封存",
    "active_learning": False,
    "allow_mastery_updates": False,
    "allow_daily_plan": False,
    "allow_practice_generation": False,
    "enable_warnings": False,
    "description": "考试已结束；历史数据只读保留，不再参与学习状态更新、计划、出题或预警。",
}

def phase_setting_key(subject: str) -> str:
    return f"{SETTING_PREFIX}{subject}"


@lru_cache(maxsize=64)
def _cached_phase_setting(subject: str, db_path: str) -> dict[str, Any] | None:
    raw = get_setting(phase_setting_key(subject), None, db_path=db_path)
    return raw if isinstance(raw, dict) else None


def _clear_phase_cache() -> None:
    _cached_phase_setting.cache_clear()


@lru_cache(maxsize=4)
def _load_learning_model_cached(path_str: str, mtime_ns: int) -> dict[str, Any]:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load_learning_model(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        from study_app.paths import MODEL_PATH

        file_path = MODEL_PATH
    else:
        file_path = Path(path)
    resolved = str(file_path.resolve())
    mtime_ns = file_path.stat().st_mtime_ns if file_path.exists() else 0
    return _load_learning_model_cached(resolved, mtime_ns)


def get_subject_phase(
    subject: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    raw = _cached_phase_setting(subject, str(Path(db_path).resolve()))
    if not isinstance(raw, dict):
        return {**REGULAR_POLICY, "subject": subject, "exam_scope": None}
    if raw.get("phase") == FINAL_REVIEW_PHASE:
        exam_scope = raw.get("exam_scope") or DEFAULT_FINAL_REVIEW_EXAM_SCOPES.get(subject)
        return {**raw, **FINAL_REVIEW_POLICY, "subject": subject, "exam_scope": exam_scope}
    if raw.get("phase") == ARCHIVED_PHASE:
        return {**raw, **ARCHIVED_POLICY, "subject": subject, "exam_scope": raw.get("exam_scope")}
    return {**raw, **REGULAR_POLICY, "subject": subject, "exam_scope": raw.get("exam_scope")}


def set_subject_phase(subject: str, phase: str) -> dict[str, Any]:
    existing = get_setting(phase_setting_key(subject), {})
    if phase == FINAL_REVIEW_PHASE:
        exam_scope = (
            existing.get("exam_scope")
            if isinstance(existing, dict) and existing.get("exam_scope")
            else DEFAULT_FINAL_REVIEW_EXAM_SCOPES.get(subject)
        )
        policy = {**FINAL_REVIEW_POLICY, "subject": subject, "exam_scope": exam_scope}
    elif phase == ARCHIVED_PHASE:
        exam_scope = existing.get("exam_scope") if isinstance(existing, dict) else None
        policy = {
            **ARCHIVED_POLICY,
            "subject": subject,
            "exam_scope": exam_scope,
            "archived_at": date.today().isoformat(),
            "archive_reason": "考试结束",
            "reference_category": subject,
        }
    else:
        exam_scope = existing.get("exam_scope") if isinstance(existing, dict) else None
        policy = {**REGULAR_POLICY, "subject": subject, "exam_scope": exam_scope}
    set_setting(phase_setting_key(subject), policy)
    _clear_phase_cache()
    return policy


def set_subject_exam_scope(subject: str, exam_scope: dict[str, Any] | None) -> dict[str, Any]:
    policy = get_subject_phase(subject)
    policy["exam_scope"] = exam_scope
    set_setting(phase_setting_key(subject), policy)
    _clear_phase_cache()
    return policy


def exam_scope_label(subject: str | None) -> str:
    if not subject:
        return ""
    scope = get_subject_phase(subject).get("exam_scope")
    return str(scope.get("label") or "") if isinstance(scope, dict) else ""


def _extract_chapter_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    for value in re.findall(r"第\s*(\d+)\s*章", text):
        try:
            numbers.append(int(value))
        except ValueError:
            continue
    return numbers


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


NEGATED_SCOPE_MARKERS = (
    "不考",
    "不含",
    "排除",
    "不涉及",
    "除外",
    "不复习",
    "不纳入",
    "不包括",
    "不要求",
    "禁止",
    "无需复习",
    "不用复习",
    "不需要复习",
)


def _has_positive_reference(text: str, keyword: str) -> bool:
    if not text or not keyword:
        return False
    start = 0
    hard_separators = ("。", "；", "\n", "\r", ".", ";", "!", "?", "！", "？")
    soft_separators = ("，", ",", "、", "：", ":")
    positive_markers = (
        "复习",
        "学习",
        "练习",
        "训练",
        "安排",
        "完成",
        "巩固",
        "诊断",
        "回顾",
        "整理",
        "掌握",
        "提升",
    )
    while True:
        index = text.find(keyword, start)
        if index < 0:
            return False
        left = text[:index]
        right = text[index + len(keyword):]
        left_positions = [left.rfind(sep) for sep in hard_separators]
        clause_start = max(left_positions) if left_positions else -1
        right_positions = [right.find(sep) for sep in hard_separators if right.find(sep) >= 0]
        clause_end = index + len(keyword) + (min(right_positions) if right_positions else len(right))
        sentence = text[clause_start + 1:clause_end]
        prefix = sentence.split(keyword, 1)[0]
        last_negation = max((prefix.rfind(marker) for marker in NEGATED_SCOPE_MARKERS), default=-1)
        if last_negation < 0:
            return True
        last_soft = max((prefix.rfind(sep) for sep in soft_separators), default=-1)
        tail = prefix[last_soft + 1:] if last_soft > last_negation else ""
        if tail and any(marker in tail for marker in positive_markers):
            return True
        start = index + len(keyword)


def _topic_submodule(subject: str, module: str, topic: str) -> str:
    if not topic:
        return ""
    try:
        from study_app.core.curriculum_hierarchy import find_topic_submodule

        return find_topic_submodule(load_learning_model(), subject, module, topic)
    except Exception:
        return ""


def is_in_exam_scope(
    subject: str | None,
    module: str | None,
    topic: str | None = None,
    submodule: str | None = None,
    *,
    phase: dict[str, Any] | None = None,
    infer_submodule: bool = True,
) -> bool:
    if not subject:
        return True
    if phase is None:
        if not is_final_review(subject):
            return True
        policy = get_subject_phase(subject)
    else:
        policy = phase
    if policy.get("phase") != FINAL_REVIEW_PHASE:
        return True
    scope = policy.get("exam_scope")
    if not isinstance(scope, dict) or not scope:
        return True

    module_name = str(module or "").strip()
    topic_name = str(topic or "").strip()
    submodule_name = str(submodule or "").strip()
    if not submodule_name and infer_submodule:
        submodule_name = _topic_submodule(subject, module_name, topic_name)
    combined = f"{module_name} {topic_name}".strip()

    excluded_topics = [str(item).strip() for item in scope.get("excluded_topics", []) if str(item).strip()]
    excluded_keywords = [str(item).strip() for item in scope.get("excluded_keywords", []) if str(item).strip()]
    excluded_submodules = [str(item).strip() for item in scope.get("excluded_submodules", []) if str(item).strip()]
    if topic_name and topic_name in excluded_topics:
        return False
    if submodule_name and submodule_name in excluded_submodules:
        return False
    if combined and _contains_any(combined, excluded_keywords):
        return False

    modules = [str(item).strip() for item in scope.get("modules", []) if str(item).strip()]
    included_submodules = [str(item).strip() for item in scope.get("included_submodules", []) if str(item).strip()]
    included_topics = [str(item).strip() for item in scope.get("included_topics", []) if str(item).strip()]
    included_keywords = [str(item).strip() for item in scope.get("included_keywords", []) if str(item).strip()]

    if topic_name and topic_name in included_topics:
        return True
    if submodule_name and submodule_name in included_submodules:
        return True
    if combined and _contains_any(combined, included_keywords):
        return True
    if modules and module_name in modules and not (included_submodules or excluded_submodules):
        return True

    allowed = {int(value) for value in scope.get("chapters", [])}
    for start, end in scope.get("chapter_ranges", []):
        allowed.update(range(int(start), int(end) + 1))
    chapter_numbers = _extract_chapter_numbers(combined)
    return bool(chapter_numbers and any(number in allowed for number in chapter_numbers))


def out_of_exam_scope_references(subject: str | None, text: str) -> list[str]:
    if not subject or not is_final_review(subject) or not text:
        return []
    scope = get_subject_phase(subject).get("exam_scope")
    if not isinstance(scope, dict) or not scope:
        return []

    violations: list[str] = []
    excluded_topics = [str(item).strip() for item in scope.get("excluded_topics", []) if str(item).strip()]
    excluded_keywords = [str(item).strip() for item in scope.get("excluded_keywords", []) if str(item).strip()]
    for topic in excluded_topics:
        if _has_positive_reference(text, topic):
            violations.append(topic)
    for keyword in excluded_keywords:
        if _has_positive_reference(text, keyword):
            violations.append(keyword)

    allowed_chapters = {int(value) for value in scope.get("chapters", [])}
    for start, end in scope.get("chapter_ranges", []):
        allowed_chapters.update(range(int(start), int(end) + 1))
    if allowed_chapters:
        for number in _extract_chapter_numbers(text):
            if number not in allowed_chapters:
                violations.append(f"第{number}章")

    try:
        model = load_learning_model()
        subject_item = next(
            (item for item in model.get("subjects", []) if item.get("name") == subject),
            None,
        )
        if subject_item is None:
            return list(dict.fromkeys(violations))
        for module in subject_item.get("modules", []):
            module_name = str(module.get("name") or "")
            module_topics = list(module.get("topics", []) or [])
            module_has_in_scope_content = any(
                is_in_exam_scope(subject, module_name, str(topic.get("name") or ""))
                for topic in module_topics
            )
            if not module_has_in_scope_content:
                if module_name and _has_positive_reference(text, module_name):
                    violations.append(module_name)
            for topic in module_topics:
                topic_name = str(topic.get("name") or "")
                if (
                    topic_name
                    and not is_in_exam_scope(subject, module_name, topic_name)
                    and _has_positive_reference(text, topic_name)
                ):
                    violations.append(topic_name)
    except Exception as error:
        raise ScopeValidationError(
            f"无法完成 {subject} 的考试范围模型校验：{error}"
        ) from error

    return list(dict.fromkeys(violations))


def exam_scope_submodule_stats(
    subject: str,
    *,
    model: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Summarize exam coverage at submodule granularity.

    Main-module mastery remains the user-facing curriculum summary. This view is
    specifically for exam-scope filtering, readiness, and plan prioritization.
    """
    from learning_bkt import bkt_topic_states
    from study_app.core.curriculum_hierarchy import iter_submodules

    model = model or load_learning_model()
    records = records if records is not None else load_raw_records(DEFAULT_DB_PATH)
    state_lookup = {
        (str(item.get("module") or ""), str(item.get("topic") or "")): item
        for item in bkt_topic_states(model, records)
        if item.get("subject") == subject
    }
    learned_statuses = {
        "learning",
        "current",
        "in_progress",
        "learned",
        "learned_needs_review",
        "reviewing",
        "mastered",
    }
    result: list[dict[str, Any]] = []
    for subject_item in model.get("subjects", []):
        if subject_item.get("name") != subject:
            continue
        for module in subject_item.get("modules", []):
            module_name = str(module.get("name") or "")
            for submodule in iter_submodules(module):
                submodule_name = str(submodule.get("name") or "")
                if str(submodule.get("role") or "content") == "assessment":
                    continue
                topics = [
                    topic
                    for topic in submodule.get("topics_data", [])
                    if is_in_exam_scope(subject, module_name, str(topic.get("name") or ""), submodule_name)
                ]
                if not topics:
                    continue
                mastery_values = []
                observed = 0
                covered = 0
                for topic in topics:
                    topic_name = str(topic.get("name") or "")
                    state = state_lookup.get((module_name, topic_name))
                    if state and int(state.get("observation_count") or 0) > 0:
                        mastery_values.append(float(state.get("mastery_probability", 0.0) or 0.0))
                        observed += 1
                    else:
                        mastery_values.append(float(topic.get("mastery", 0.0) or 0.0))
                    if str(topic.get("status") or module.get("status") or "") in learned_statuses:
                        covered += 1
                result.append(
                    {
                        "module": module_name,
                        "submodule": submodule_name,
                        "topic_count": len(topics),
                        "covered_count": covered,
                        "observed_count": observed,
                        "mastery": sum(mastery_values) / len(mastery_values),
                        "topics": [str(topic.get("name") or "") for topic in topics],
                    }
                )
        break
    return result


def is_final_review(subject: str | None) -> bool:
    return bool(subject) and get_subject_phase(subject).get("phase") == FINAL_REVIEW_PHASE


def is_archived(subject: str | None) -> bool:
    return bool(subject) and get_subject_phase(subject).get("phase") == ARCHIVED_PHASE


def _record_date_key(record: dict[str, Any]) -> str:
    return str(record.get("date") or record.get("record_date") or "")


def _record_on_or_before(record: dict[str, Any], as_of_date: date) -> bool:
    try:
        return date.fromisoformat(_record_date_key(record)) <= as_of_date
    except (TypeError, ValueError):
        return False


def _record_subject(record: dict[str, Any]) -> str:
    return str(record.get("subject") or record.get("subject_name") or "")


def _record_learning_signal(record: dict[str, Any]) -> bool:
    activity = str(record.get("activity") or "")
    source = str(record.get("source") or "")
    if source in {"classroom", "class", "outside_class", "self_study", "ai_generated_pdf", "planned_homework"}:
        return True
    return activity in {
        "class",
        "class_learning",
        "class_exercise",
        "practice",
        "exercise",
        "exercise_review",
        "review",
        "review_exercise",
        "self_test",
        "plan_completion",
    }


def _record_text(record: dict[str, Any]) -> str:
    parts = [
        str(record.get(key) or "")
        for key in ("module", "module_name", "topic", "topic_name", "chapter", "note")
    ]
    related = record.get("related_topics") or []
    if isinstance(related, list):
        parts.extend(str(item) for item in related)
    for problem in record.get("problems", []) or []:
        parts.extend(
            str(problem.get(key) or "")
            for key in ("title", "statement", "error_cause")
        )
        problem_related = problem.get("related_topics") or []
        if isinstance(problem_related, list):
            parts.extend(str(item) for item in problem_related)
    return " ".join(parts)


def recent_review_profile(
    subject: str,
    records: list[dict[str, Any]],
    limit: int = 5,
    *,
    as_of_date: date,
) -> dict[str, Any]:
    """Summarize where the latest practice/class evidence has concentrated."""

    from learning_bkt import topic_matches_text

    recent = []
    for record in records:
        if _record_subject(record) != subject or not _record_learning_signal(record):
            continue
        if _record_on_or_before(record, as_of_date):
            recent.append(record)
    recent.sort(key=lambda item: (_record_date_key(item), int(item.get("id") or 0)), reverse=True)
    recent = recent[:limit]
    module_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    texts: list[str] = []
    for record in recent:
        module = str(record.get("module") or record.get("module_name") or "").strip()
        topic = str(record.get("topic") or record.get("topic_name") or "").strip()
        text = _record_text(record)
        texts.append(text)
        if module:
            module_counts[module] = module_counts.get(module, 0) + 1
        if topic:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    def count_for(module_name: str, topic_name: str) -> tuple[int, int]:
        module_count = module_counts.get(module_name, 0)
        topic_count = topic_counts.get(topic_name, 0)
        for text in texts:
            if module_name and module_name not in module_counts and topic_matches_text(module_name, text):
                module_count += 1
            if topic_name and topic_matches_text(topic_name, text):
                topic_count += 1
        return module_count, topic_count

    return {
        "limit": limit,
        "records": recent,
        "total": len(recent),
        "module_counts": module_counts,
        "topic_counts": topic_counts,
        "count_for": count_for,
    }


def recent_review_balance_signals(
    module_name: str,
    topic_name: str,
    profile: dict[str, Any],
) -> dict[str, float]:
    total = int(profile.get("total") or 0)
    if total <= 0:
        return {
            "recent_module_count": 0,
            "recent_topic_count": 0,
            "recent_module_share": 0.0,
            "recent_topic_share": 0.0,
            "recent_coverage_gap": 0.0,
            "recent_review_saturation": 0.0,
        }
    module_count, topic_count = profile["count_for"](module_name, topic_name)
    module_share = max(0.0, min(1.0, module_count / total))
    topic_share = max(0.0, min(1.0, topic_count / total))
    coverage_gap = 1.0 if module_count == 0 else max(0.0, 0.40 - module_share) / 0.40
    module_saturation = max(0.0, module_share - 0.55) / 0.45
    topic_saturation = max(0.0, topic_share - 0.35) / 0.65
    saturation = max(0.0, min(1.0, 0.75 * module_saturation + 0.25 * topic_saturation))
    if total < 3:
        coverage_gap *= total / 3
        saturation *= total / 3
    return {
        "recent_module_count": float(module_count),
        "recent_topic_count": float(topic_count),
        "recent_module_share": module_share,
        "recent_topic_share": topic_share,
        "recent_coverage_gap": coverage_gap,
        "recent_review_saturation": saturation,
    }


def weighted_topic_priority_states(
    subject: str,
    limit: int = 8,
    *,
    model: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
    states: list[dict[str, Any]] | None = None,
    as_of_date: date,
    computation_context: Any | None = None,
    phase: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    phase = phase or get_subject_phase(subject)
    if phase.get("phase") == ARCHIVED_PHASE:
        return []
    try:
        from learning_bkt import bkt_topic_states
        from learning_memory import topic_memory_state
        from study_app.core.dashboard import is_covered_learning_item

        model = model or load_learning_model()
        records = records if records is not None else load_raw_records(DEFAULT_DB_PATH)
        records = [record for record in records if _record_on_or_before(record, as_of_date)]
        states = states if states is not None else (
            computation_context.bkt_topic_states()
            if computation_context is not None
            else bkt_topic_states(model, records, as_of_date=as_of_date)
        )
    except Exception:
        return []

    covered_statuses = {
        "learned",
        "learning",
        "current",
        "in_progress",
        "reviewing",
        "learned_needs_review",
        "mastered",
    }
    topic_meta: dict[tuple[str, str, str], tuple[dict[str, Any], str]] = {}
    for subject_item in model.get("subjects", []):
        for module in subject_item.get("modules", []):
            module_status = str(module.get("status") or "")
            for topic in module.get("topics", []):
                topic_meta[
                    (subject_item.get("name", ""), module.get("name", ""), topic.get("name", ""))
                ] = (topic, str(topic.get("status") or module_status))

    weights = phase.get("priority_weights", {})
    review_profile = recent_review_profile(
        subject,
        records,
        limit=int(phase.get("recent_review_window", 5) or 5),
        as_of_date=as_of_date,
    )
    candidates: list[dict[str, Any]] = []
    for raw in states:
        if raw.get("subject") != subject:
            continue
        item = dict(raw)
        if not is_in_exam_scope(
            subject,
            item.get("module"),
            item.get("topic"),
            phase=phase,
        ):
            continue
        key = (str(item.get("subject") or ""), str(item.get("module") or ""), str(item.get("topic") or ""))
        topic, status = topic_meta.get(key, ({}, ""))
        covered = status in covered_statuses or is_covered_learning_item(
            subject,
            str(item.get("module") or ""),
            topic,
            status,
            records,
        )
        if not covered:
            continue

        memory = (
            computation_context.memory_state(
                subject, str(item.get("module") or ""), topic
            )
            if computation_context is not None
            else topic_memory_state(
                subject,
                str(item.get("module") or ""),
                topic,
                records,
                as_of_date,
                model.get("warning_policy", {}),
            )
        )
        observations = int(item.get("observation_count") or 0)
        mastery = float(item.get("mastery_probability", 0))
        target_mastery = float(item.get("target_mastery", 0.72))
        mastery_deficit = max(0.0, target_mastery - mastery) / max(target_mastery, 0.01)
        target_recall = float(memory.get("target_recall", 0.78))
        recall = float(memory.get("recall", 0))
        forgetting_risk = max(0.0, target_recall - recall) / max(target_recall, 0.01)
        last_observation = item.get("last_observation") or {}
        recent_error = (
            1.0 - float(last_observation.get("correctness", 0))
            if observations > 0 and last_observation.get("correctness") is not None
            else 0.0
        )
        balance = recent_review_balance_signals(
            str(item.get("module") or ""),
            str(item.get("topic") or ""),
            review_profile,
        )
        recent_coverage_gap = float(balance["recent_coverage_gap"])
        recent_review_saturation = float(balance["recent_review_saturation"])
        score = (
            float(weights.get("mastery_deficit", 0)) * mastery_deficit
            + float(weights.get("recent_error", 0)) * recent_error
            + float(weights.get("forgetting_risk", 0)) * forgetting_risk
            + float(weights.get("recent_coverage_gap", 0)) * recent_coverage_gap
            - float(weights.get("recent_review_saturation", 0)) * recent_review_saturation
        )
        item.update(
            {
                "priority": score,
                "weighted_priority": score,
                "mastery_deficit": mastery_deficit,
                "forgetting_risk": forgetting_risk,
                "recent_error": recent_error,
                "recent_coverage_gap": recent_coverage_gap,
                "recent_review_saturation": recent_review_saturation,
                "recent_module_count": int(balance["recent_module_count"]),
                "recent_topic_count": int(balance["recent_topic_count"]),
                "recent_window_size": int(review_profile.get("total") or 0),
                "recall": recall,
                "target_recall": target_recall,
                "phase": phase.get("phase", DEFAULT_PHASE),
                "phase_label": phase.get("label", "常规学习"),
                "priority_weights": weights,
            }
        )
        if observations == 0:
            item["priority_reason"] = "暂无题目级证据，按初始掌握度参与加权排序。"
        elif mastery < target_mastery:
            item["priority_reason"] = "已有证据显示掌握度低于目标。"
        else:
            item["priority_reason"] = "掌握度已达标，按遗忘和近期错误安排维护。"
        if recent_coverage_gap >= 0.8:
            item["priority_reason"] += "；近期复习覆盖不足，优先补齐。"
        elif recent_review_saturation >= 0.4:
            item["priority_reason"] += "；近期复习较集中，优先级已适度降温。"
        candidates.append(item)

    candidates.sort(key=lambda current: float(current.get("weighted_priority", 0)), reverse=True)
    return candidates[:limit]


def final_review_topic_states(
    subject: str,
    limit: int = 8,
    *,
    as_of_date: date,
) -> list[dict[str, Any]]:
    return weighted_topic_priority_states(subject, limit, as_of_date=as_of_date)

