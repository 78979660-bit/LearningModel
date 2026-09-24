from __future__ import annotations

import re
from pathlib import Path
from statistics import median
from dataclasses import dataclass

from study_app.paths import MODEL_PATH

@dataclass(frozen=True)
class PracticeAssignment:
    topic: str
    difficulty_score: int
    difficulty_label: str
    template_id: str
    template_brief: str
    exercise_count: int
    difficulty_basis: str = ""

    def to_homework_text(self) -> str:
        return (
            f"参考难度 {self.difficulty_score}/100（{self.difficulty_label}）；"
            + (f"动态依据：{self.difficulty_basis}；" if self.difficulty_basis else "")
            + f"题库模板 {self.template_id}：{self.template_brief}；"
            + f"生成/选做 {self.exercise_count} 题。"
        )


def generate_practice_assignment(topic: str, day: int = 1) -> PracticeAssignment:
    normalized = normalize_topic(topic)
    difficulty, basis = dynamic_reference_difficulty(normalized, day)
    label = difficulty_label(difficulty)
    subject, _topic_name = split_subject_topic(normalized)
    template_id, brief = template_for_topic(normalized, subject or None)
    final_review_count = 6 if subject and _is_final_review_subject(subject) else None
    return PracticeAssignment(
        topic=topic,
        difficulty_score=difficulty,
        difficulty_label=label,
        template_id=template_id,
        template_brief=brief,
        exercise_count=final_review_count or exercise_count(day),
        difficulty_basis=basis,
    )


def _is_final_review_subject(subject: str) -> bool:
    try:
        from study_app.core.study_phase import is_final_review

        return is_final_review(subject)
    except Exception:
        return False


def normalize_topic(topic: str) -> str:
    return re.sub(r"\s+", " ", topic or "").strip()


def keyword_reference_difficulty(topic: str) -> int:
    base = 52
    data_structure_keywords = [
        "AVL",
        "哈希",
        "散列",
        "KMP",
        "复杂度",
        "二叉搜索树",
        "数据结构",
        "算法",
    ]
    hard_keywords = [
        "Gauss",
        "Green",
        "Stokes",
        "无旋场",
        "势函数",
        "曲面积分",
        "AVL",
        "KMP",
        "复杂度",
        "相图",
        "Arrhenius",
        "一致收敛",
        "逐项求导",
        "逐项积分",
        "Cauchy",
        "D'Alembert",
        "Dirichlet数项",
        "Abel数项",
    ]
    medium_keywords = [
        "三重积分",
        "级数",
        "哈希",
        "二叉搜索树",
        "化学动力学",
        "反应级数",
        "热力学",
        "振动",
    ]
    if any(keyword in topic for keyword in hard_keywords):
        base = 74
    elif any(keyword in topic for keyword in medium_keywords):
        base = 64
    if "级数" in topic:
        base = max(base, 72)
    if any(keyword in topic for keyword in data_structure_keywords):
        base = max(base, 66)
    return max(30, min(92, base))


def reference_difficulty(topic: str, day: int) -> int:
    difficulty, _basis = dynamic_reference_difficulty(topic, day)
    return difficulty


def dynamic_reference_difficulty(topic: str, day: int) -> tuple[int, str]:
    subject, topic_name = split_subject_topic(topic)
    fallback = model_topic_difficulty_standard(subject, topic_name or topic) or keyword_reference_difficulty(topic_name or topic)
    final_review = False
    if subject:
        try:
            from study_app.core.study_phase import is_final_review

            final_review = is_final_review(subject)
        except Exception:
            final_review = False
    # Plans should train slightly above the learner's current demonstrated level.
    stage_adjustment = {-1: -2, 1: -2, 2: 3, 3: 8}.get(day, 0)
    evidence = topic_difficulty_evidence(subject, topic_name or topic)

    if not evidence["has_practice_evidence"]:
        score = fallback + stage_adjustment
        if final_review:
            score = min(72, max(66, score + 2))
        else:
            score = min(68, score)
        mastery = evidence.get("mastery")
        mastery_text = f"，初始掌握度 {mastery:.0%}" if mastery is not None else ""
        basis = f"暂无真实做题证据，使用题型基准 {fallback}{mastery_text}，首轮诊断难度受限"
        if final_review:
            basis += "，期末复习模式不直接跳至高难度"
        return max(30, min(72 if final_review else 68, round(score))), basis

    baseline = evidence["difficulty_median"]
    baseline_source = "已完成练习难度中位数"
    if baseline is None:
        baseline = fallback
        baseline_source = "题型基准"
    mastery = evidence["mastery"]
    accuracy = evidence["accuracy"]
    mastery_adjustment = round((mastery - 0.55) * 16) if mastery is not None else 0
    accuracy_adjustment = round((accuracy - 0.65) * 14) if accuracy is not None else 0
    score = baseline + mastery_adjustment + accuracy_adjustment + stage_adjustment
    if final_review:
        score = max(72, score + 6)
    score = max(40, min(92, round(score)))
    mastery_text = f"知识点掌握 {mastery:.0%}" if mastery is not None else "知识点掌握证据不足"
    accuracy_text = f"近期正确率 {accuracy:.0%}" if accuracy is not None else "近期正确率不足"
    return (
        score,
        f"{baseline_source} {baseline:.0f}，{mastery_text}，{accuracy_text}，计划阶段 {stage_adjustment:+d}"
        + ("，期末复习综合训练上调" if final_review else ""),
    )


def model_topic_difficulty_standard(subject: str, topic_text: str) -> int | None:
    if not subject:
        return None
    try:
        import json

        from learning_bkt import normalize_topic_text

        model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        normalized = normalize_topic_text(topic_text)
        candidates: list[tuple[int, int]] = []
        for subject_item in model.get("subjects", []):
            if subject_item.get("name") != subject:
                continue
            for module in subject_item.get("modules", []):
                for topic in module.get("topics", []):
                    name = str(topic.get("name") or "")
                    normalized_name = normalize_topic_text(name)
                    if normalized_name and normalized_name in normalized:
                        standard = topic.get("difficulty_standard") or {}
                        score = standard.get("score")
                        if not isinstance(score, (int, float)):
                            score = float(topic.get("difficulty", 0.6)) * 100
                        candidates.append((len(normalized_name), round(float(score))))
        return max(candidates)[1] if candidates else None
    except Exception:
        return None


def is_non_calibration_difficulty_source(source: object) -> bool:
    """Return whether an attempt is unsuitable for personal difficulty calibration.

    Completed AI-generated exercises are valid evidence about this learner's
    demonstrated level. They remain excluded from automatic problem-bank import
    in ``database._auto_import_practice_problems``; this predicate only controls
    the personalized reference difficulty used by future plans.
    """
    normalized = str(source or "").strip().lower()
    return normalized in {"planned_reference", "planned_homework"}


def split_subject_topic(text: str) -> tuple[str, str]:
    subjects = [
        "高等数学",
        "微积分",
        "高级程序设计",
        "计算机科学",
        "数据结构与算法基础",
        "大学物理学",
        "化学原理",
        "离散数学",
    ]
    subject = next((item for item in subjects if item in text), "")
    if " / " in text:
        left, right = text.split(" / ", 1)
        if left.strip() in subjects:
            return left.strip(), right.strip()
    return subject, text.replace(subject, "", 1).strip(" /") if subject else text


def topic_difficulty_evidence(subject: str, topic_name: str) -> dict[str, object]:
    try:
        import json

        from learning_bkt import (
            normalize_topic_text,
            problem_correctness_fraction,
            record_correctness_fraction,
            topic_bkt_state,
            topic_matches_text,
        )
        from study_app.data.database import DEFAULT_DB_PATH, load_raw_records

        model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        records = load_raw_records(DEFAULT_DB_PATH)
    except Exception:
        return {
            "has_evidence": False,
            "has_practice_evidence": False,
            "practice_observation_count": 0,
            "objective_difficulty_count": 0,
            "difficulty_median": None,
            "mastery": None,
            "accuracy": None,
        }

    evidence_terms = topic_evidence_terms(topic_name)
    matched_topic = None
    matched_module = ""
    matched_subject = subject
    requested_topic = normalize_topic_text(topic_name)
    # Resolve an explicit modeled topic before considering template-level aliases.
    # Otherwise a request such as "power series" can accidentally bind to the
    # first topic containing the generic word "series".
    for subject_item in model.get("subjects", []):
        if subject and subject_item.get("name") != subject:
            continue
        for module in subject_item.get("modules", []):
            for item in module.get("topics", []):
                if normalize_topic_text(item.get("name", "")) == requested_topic:
                    matched_topic = item
                    matched_module = module.get("name", "")
                    matched_subject = subject_item.get("name", subject)
                    break
            if matched_topic:
                break
        if matched_topic:
            break

    for subject_item in model.get("subjects", []):
        if matched_topic:
            break
        if subject and subject_item.get("name") != subject:
            continue
        for module in subject_item.get("modules", []):
            for item in module.get("topics", []):
                if any(
                    topic_matches_text(item.get("name", ""), term) or topic_matches_text(term, item.get("name", ""))
                    for term in evidence_terms
                ):
                    matched_topic = item
                    matched_module = module.get("name", "")
                    matched_subject = subject_item.get("name", subject)
                    break
            if matched_topic:
                break
        if matched_topic:
            break

    difficulties: list[float] = []
    correctness_values: list[float] = []
    for record in reversed(records):
        if subject and record.get("subject") != subject:
            continue
        record_text = " ".join(str(record.get(key) or "") for key in ["module", "topic", "related_topics", "note"])
        primary_record_text = " ".join(str(record.get(key) or "") for key in ["module", "topic"])
        record_matches = any(topic_matches_text(term, record_text) for term in evidence_terms)
        primary_record_matches = any(topic_matches_text(term, primary_record_text) for term in evidence_terms)
        matching_problems = []
        for problem in record.get("problems", []) or []:
            problem_text = " ".join(
                str(problem.get(key) or "")
                for key in ["title", "statement", "related_topics", "error_cause"]
            )
            if any(topic_matches_text(term, problem_text) for term in evidence_terms):
                matching_problems.append(problem)
        if primary_record_matches and record.get("problems"):
            matching_problems = list(record.get("problems") or [])
        if matching_problems:
            problem_correctness_added = False
            for problem in matching_problems:
                source = str(problem.get("difficulty_source") or "")
                difficulty = problem.get("difficulty_score")
                if isinstance(difficulty, (int, float)) and not is_non_calibration_difficulty_source(source):
                    difficulties.append(float(difficulty))
                correctness = problem_correctness_fraction(problem)
                if correctness is not None:
                    correctness_values.append(float(correctness))
                    problem_correctness_added = True
            if not problem_correctness_added:
                correctness = record_correctness_fraction(record)
                if correctness is not None:
                    correctness_values.append(float(correctness))
        elif record_matches:
            correctness = record_correctness_fraction(record)
            if correctness is not None:
                correctness_values.append(float(correctness))
        if len(correctness_values) >= 8 and len(difficulties) >= 6:
            break

    mastery = None
    if matched_topic:
        policy = model.get("warning_policy", {})
        state = topic_bkt_state(matched_subject, matched_module, matched_topic, records, policy)
        if state and state.get("observation_count", 0):
            mastery = float(state["mastery_probability"])
        else:
            mastery = float(matched_topic.get("mastery", 0) or 0)

    recent_correctness = correctness_values[:8]
    recent_difficulties = difficulties[:8]
    return {
        "has_evidence": bool(recent_correctness or recent_difficulties or mastery is not None),
        "has_practice_evidence": bool(recent_correctness or recent_difficulties),
        "practice_observation_count": len(recent_correctness),
        "objective_difficulty_count": len(recent_difficulties),
        "difficulty_median": median(recent_difficulties) if recent_difficulties else None,
        "mastery": mastery,
        "accuracy": sum(recent_correctness) / len(recent_correctness) if recent_correctness else None,
    }


def topic_evidence_terms(topic_name: str) -> list[str]:
    import re

    from learning_bkt import normalize_topic_text

    raw = str(topic_name or "").strip()
    terms = [raw] if raw else []
    for segment in re.split(r"[、,，;；\n]+", raw):
        segment = segment.strip()
        if not segment:
            continue
        pieces = [segment]
        if "：" in segment:
            pieces.append(segment.split("：", 1)[1].strip())
        if ":" in segment:
            pieces.append(segment.split(":", 1)[1].strip())
        for piece in list(pieces):
            if "/" in piece:
                pieces.append(piece.rsplit("/", 1)[-1].strip())
        for piece in pieces:
            piece = re.sub(
                r"^(高等数学|微积分|高级程序设计|计算机科学|数据结构与算法基础|大学物理学|化学原理|离散数学)\s*/\s*",
                "",
                piece,
            ).strip()
            if piece and len(piece) >= 2:
                terms.append(piece)

    # Template-level fallback. Some plan items only expose the practice template
    # or a broad series description, not the exact modeled topic names. Bind
    # those items back to the series evidence pool so they do not get treated as
    # first-diagnostic/no-evidence tasks after the learner has done series work.
    broad_series_request = normalize_topic_text(raw) in {
        normalize_topic_text("级数"),
        normalize_topic_text("级数综合"),
        normalize_topic_text("级数敛散性、求和/展开、绝对收敛与条件收敛判别"),
        normalize_topic_text("级数敛散性求和与展开专项"),
    }
    if "CALC-SERIES" in raw or broad_series_request:
        terms.extend(
            [
                "级数",
                "级数敛散性",
                "数项级数",
                "正项级数",
                "正项级数与比较判别法",
                "绝对收敛",
                "条件收敛",
                "幂级数",
                "幂级数与收敛半径",
                "函数展开为幂级数",
                "无穷级数与部分和",
                "级数综合计算与证明",
                "一致收敛性",
                "一致收敛性与逐项运算",
                "逐项求导",
                "逐项积分",
                "Cauchy根值判别",
                "D'Alembert比值判别",
                "Cauchy根值判别与D'Alembert比值判别",
                "Dirichlet数项级数判别",
                "Abel数项级数判别",
                "Dirichlet与Abel数项级数判别",
            ]
        )
    if "PHYS-MODELING" in raw or "相对论" in raw:
        terms.extend(
            [
                "相对论",
                "狭义相对论",
                "相对性原理与光速不变",
                "Lorentz 变换",
                "时间膨胀与长度收缩",
                "相对论动力学基础",
                "速度变换",
                "能量动量关系",
            ]
        )
    if "PHYS-MODELING" in raw or "热力学" in raw:
        terms.extend(
            [
                "热力学",
                "热力学第一定律",
                "状态方程",
                "热机循环",
                "Otto 循环",
                "内能",
            ]
        )
    if "CHEM-KINETICS" in raw or "化学动力学" in raw or "反应级数" in raw:
        terms.extend(
            [
                "化学反应动力学",
                "化学反应速率定义",
                "速率测定方法",
                "基元反应与反应机理",
                "质量作用定律",
                "反应级数",
                "Arrhenius",
            ]
        )
    if "CHEM-EQUILIBRIUM" in raw or "相平衡" in raw or "相图" in raw:
        terms.extend(
            [
                "相平衡",
                "二元系相平衡方程",
                "气-液相平衡方程",
                "固-液相平衡方程",
                "一级相变与二级相变",
                "Ehrenfest 方程",
                "相图",
            ]
        )
    if "DS-HASH-ASL" in raw:
        terms.extend(["哈希表", "散列表", "装填因子", "冲突处理", "平均查找长度"])
    if "DS-AVL-ROT" in raw:
        terms.extend(["AVL", "AVL 树旋转与插入删除", "平衡因子", "失衡结点"])
    if "DS-BST-OPS" in raw:
        terms.extend(["二叉搜索树", "BST", "遍历序列", "平均查找长度"])
    if "ALG-COMPLEXITY" in raw:
        terms.extend(["复杂度", "递归复杂度", "递归树", "主定理", "算法复杂度"])
    deduped: list[str] = []
    for term in terms:
        if term and term not in deduped:
            deduped.append(term)
    return deduped


def first_diagnostic_difficulty_cap(subject: str, text: str) -> int | None:
    """Cap plans containing learned topics that still lack real practice evidence."""
    try:
        import json

        from learning_bkt import normalize_topic_text
        from study_app.core.study_phase import is_final_review

        model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        normalized_text = normalize_topic_text(text)
        matched_topics: list[str] = []
        for subject_item in model.get("subjects", []):
            if subject_item.get("name") != subject:
                continue
            for module in subject_item.get("modules", []):
                for topic in module.get("topics", []):
                    name = str(topic.get("name") or "")
                    if name and normalize_topic_text(name) in normalized_text:
                        matched_topics.append(name)
        if not matched_topics:
            return None
        if any(
            not topic_difficulty_evidence(subject, topic_name).get("has_practice_evidence", False)
            for topic_name in matched_topics
        ):
            return 72 if is_final_review(subject) else 68
    except Exception:
        return None
    return None


def difficulty_label(score: int) -> str:
    if score >= 75:
        return "偏难"
    if score >= 55:
        return "中等"
    return "基础"


def template_for_topic(topic: str, subject: str | None = None) -> tuple[str, str]:
    from study_app.core.composite_templates import COMPOSITE_BY_ID, composite_template_for_text
    from study_app.core.discrete_math_taxonomy import (
        DISCRETE_TEMPLATE_INFO,
        classify_discrete_text,
    )
    from study_app.core.ds_course_taxonomy import classify_ds_text

    inferred_subject, _inferred_topic = split_subject_topic(topic)
    subject_filter = (subject or inferred_subject).strip()
    explicit_templates = {
        "CPP-OOP-PRACTICE": "围绕当前理论主题开展开放式 C++ 实践，训练类接口与封装、对象生命周期与资源管理、类协作、继承与多态、测试与调试；具体实现任务随本次实验或作业变化",
        "DS-AVL-ROT": "AVL 插入/删除调整、旋转类型、失衡结点与平衡因子判断",
        "DS-BST-OPS": "二叉搜索树插入、删除、遍历序列、合法性判断与平均查找长度",
        "DS-HASH-ASL": "哈希表构造、冲突处理、装填因子、成功/失败平均查找长度",
        "ALG-KMP-PREFIX": "前缀函数/next 数组手算、失配跳转、字符串匹配适用性判断",
        "ALG-COMPLEXITY": "循环/递归复杂度、最好最坏平均情形、递归树或主定理",
        "CS-CODING-PRACTICE": "计算机知识的代码实现、边界测试、调试定位与复杂度验证",
        "CS-ALGORITHM-DESIGN": "OJ/LeetCode 风格算法题，要求给出可实现思路、代码要点、复杂度与测试用例",
        "CS-OJ-PRACTICE": "OJ/LeetCode 风格算法题，要求给出可实现思路、代码要点、复杂度与测试用例",
        "CALC-GAUSS-FLUX": "第二型曲面积分、补面、方向判断、奇点/零点特殊情况讨论",
        "CALC-TRIPLE-INTEGRAL": "三重积分区域设限、坐标选择、换元与 Jacobian",
        "CALC-SERIES": "级数敛散性、求和/展开、逐项求导/积分、一致收敛、Cauchy/D'Alembert与Dirichlet/Abel数项级数判别",
        "CHEM-KINETICS": "反应级数判断、积分速率方程、半衰期、Arrhenius 公式与活化能",
        "CHEM-EQUILIBRIUM": "化学平衡、相平衡、相图、平衡常数与热力学判据",
        "PHYS-MODELING": "物理建模、守恒律/基本方程选择、公式适用条件与计算",
        "PHYS-RELATIVITY": "狭义相对论时空变换、速度合成、Doppler 效应与相对论动力学",
        "PHYS-REL-EVENTS": "相对性原理、同时性、时空间隔与 Lorentz 事件坐标变换",
        "PHYS-REL-LIFETIME": "时间膨胀、长度收缩、寿命概率和固有量识别",
        "PHYS-REL-VELOCITY": "相对论速度变换、参考系互换和方向符号判断",
        "PHYS-REL-DOPPLER": "相对论 Doppler 效应、红移/蓝移和退行速度反演",
        "PHYS-REL-DYNAMICS": "相对论能量、动量、质能关系与高速近似",
        "PHYS-REL-DECAY": "相对论粒子衰变、能动量守恒和二维运动学",
        "PHYS-THERMO-ENTROPY": "热过程、相变、熵变、熵增原理与自发性判断",
        "PHYS-TEMP-EOS": "温度、气体状态方程、热膨胀系数与等温压缩系数",
        "PHYS-FIRST-LAW": "热量、功、内能、热力学第一定律与理想气体过程",
        "PHYS-SECOND-LAW-ENGINE": "热力学第二定律、Carnot 定理、热机/制冷机和熵判据",
        "PHYS-THERMO-PROCESS": "理想/非理想气体过程、状态方程、功热与响应系数",
        "PHYS-THERMO-POTENTIAL": "热力学势、Maxwell 关系、TdS 方程与偏导恒等式",
        "PHYS-PHASE-TRANSITION": "相图、Clapeyron 方程、一级/二级相变与潜热",
        "PHYS-STATISTICAL": "Maxwell/Boltzmann 分布、统计平均、涨落与分子通量",
        "PHYS-QUANTUM-PHENOMENA": "光电效应、Compton 散射、de Broglie 波与量子现象",
        "PHYS-SCHRODINGER": "Schrodinger 方程、势阱/势阶、量子谐振子与态演化",
        "PHYS-QUANTUM-STATISTICS": "Bose/Fermi 统计、态密度、Fermi 能与经典极限",
        "PHYS-ATOMIC": "Bohr 模型、类氢原子、原子波函数与跃迁反冲",
        "PHYS-NUCLEAR": "核衰变、活度、Q 值、核反冲与半经验质量公式",
        "PHYS-PARTICLE": "夸克模型、守恒律、基本相互作用与离散对称性",
        "GEN-MIXED-PRACTICE": "概念判定、基础计算/推导、混合应用与错因归类",
    }
    explicit_templates.update(
        {template_id: description for template_id, (_module, description) in DISCRETE_TEMPLATE_INFO.items()}
    )
    if subject_filter == "高级程序设计":
        return "CPP-OOP-PRACTICE", explicit_templates["CPP-OOP-PRACTICE"]
    for template_id, description in explicit_templates.items():
        if template_id in topic:
            return template_id, description
    if subject_filter in {"", "大学物理学"} or any(
        keyword in topic
        for keyword in [
            "热力学",
            "状态方程",
            "温度",
            "相变",
            "Clapeyron",
            "Maxwell",
            "统计分布",
            "气体动理论",
        ]
    ):
        physics_routes = (
            (
                "PHYS-PHASE-TRANSITION",
                ("Clapeyron", "相图", "相平衡", "化学势", "相变", "潜热", "三相点"),
            ),
            (
                "PHYS-STATISTICAL",
                ("Boltzmann", "Maxwell 分布", "速率分布", "气体动理论", "微观", "均方根", "最概然"),
            ),
            (
                "PHYS-THERMO-POTENTIAL",
                ("Maxwell", "TdS", "Gibbs", "Helmholtz", "自由能", "热力学势", "偏导", "化学势"),
            ),
            (
                "PHYS-SECOND-LAW-ENGINE",
                ("Carnot", "热机", "制冷机", "COP", "第二定律", "Clausius", "熵增", "最大功"),
            ),
            (
                "PHYS-FIRST-LAW",
                ("第一定律", "热量", "内能", "绝热", "等温", "等压", "等容", "热容", "p-V", "循环"),
            ),
            (
                "PHYS-TEMP-EOS",
                ("温度", "温标", "状态方程", "理想气体", "压缩系数", "膨胀系数", "van der Waals", "Boyle"),
            ),
        )
        for template_id, keywords in physics_routes:
            if any(keyword in topic for keyword in keywords):
                return template_id, explicit_templates[template_id]
    if subject_filter in {"", "大学物理学"} and any(keyword in topic for keyword in ["相对论", "Lorentz", "洛伦兹", "Doppler", "红移", "蓝移", "μ", "介子", "衰变"]):
        relativity_routes = (
            ("PHYS-REL-DECAY", ("衰变", "湮灭", "中子", "Sigma", "Σ", "beta", "能动量守恒", "二体")),
            ("PHYS-REL-DOPPLER", ("Doppler", "多普勒", "红移", "蓝移", "光谱", "频移", "类星体")),
            ("PHYS-REL-VELOCITY", ("速度合成", "速度变换", "相对速度", "参考系互换", "分量")),
            ("PHYS-REL-LIFETIME", ("寿命", "μ", "介子", "长度收缩", "时间膨胀", "固有时间", "固有长度")),
            ("PHYS-REL-DYNAMICS", ("动能", "动量", "质能", "高能", "高速", "超相对论", "近似")),
            ("PHYS-REL-EVENTS", ("同时性", "时空间隔", "事件", "Lorentz", "洛伦兹", "火车")),
        )
        for template_id, keywords in relativity_routes:
            if any(keyword in topic for keyword in keywords):
                return template_id, explicit_templates[template_id]
        return "PHYS-RELATIVITY", explicit_templates["PHYS-RELATIVITY"]
    for template_id, template in COMPOSITE_BY_ID.items():
        if template_id in topic:
            return template_id, template.description

    composite = composite_template_for_text(topic, subject_filter or None)
    if composite:
        return composite.template_id, composite.description
    if subject_filter == "离散数学":
        template_id, _module, _topic = classify_discrete_text(topic)
        return template_id, DISCRETE_TEMPLATE_INFO[template_id][1]
    ds_topic = classify_ds_text(topic) if subject_filter in {"", "数据结构与算法基础"} else None
    if ds_topic:
        return ds_topic.template_id, ds_topic.description
    if subject_filter == "高等数学":
        if any(keyword in topic for keyword in ["Gauss", "曲面积分"]):
            return "CALC-GAUSS-FLUX", "第二型曲面积分、补面、方向判断、奇点/零点特殊情况讨论"
        if "三重积分" in topic:
            return "CALC-TRIPLE-INTEGRAL", "三重积分区域设限、坐标选择、换元与 Jacobian"
        if "级数" in topic:
            return "CALC-SERIES", "级数敛散性、求和/展开、逐项求导/积分、一致收敛、Cauchy/D'Alembert与Dirichlet/Abel数项级数判别"
        return "GEN-MIXED-PRACTICE", "高等数学考试范围内的概念判定、基础计算/推导、混合应用与错因归类"
    if subject_filter in {"", "计算机科学"} and any(
        keyword in topic
        for keyword in [
            "算法设计",
            "OJ",
            "在线评测",
            "LeetCode",
            "leetcode",
            "数组哈希",
            "双指针",
            "滑动窗口",
            "动态规划",
            "回溯",
            "图搜索",
            "BFS",
            "DFS",
            "数据结构选择",
            "图算法",
            "树算法",
            "排序算法",
            "综合算法",
        ]
    ):
        return "CS-OJ-PRACTICE", "OJ/LeetCode 风格算法题，要求给出可实现思路、代码要点、复杂度与测试用例"
    if subject_filter in {"", "计算机科学"} and any(
        keyword in topic for keyword in ["编程实践", "实现训练", "代码实现", "上机", "调试"]
    ):
        return "CS-CODING-PRACTICE", "计算机知识的代码实现、边界测试、调试定位与复杂度验证"
    if "AVL" in topic:
        return "DS-AVL-ROT", "AVL 插入/删除调整、旋转类型、失衡结点与平衡因子判断"
    if "BST" in topic or "二叉搜索树" in topic:
        return "DS-BST-OPS", "二叉搜索树插入、删除、遍历序列、合法性判断与平均查找长度"
    if "哈希" in topic or "散列" in topic:
        return "DS-HASH-ASL", "哈希表构造、冲突处理、装填因子、成功/失败平均查找长度"
    if "KMP" in topic or "字符串" in topic:
        return "ALG-KMP-PREFIX", "前缀函数/next 数组手算、失配跳转、字符串匹配适用性判断"
    if "复杂度" in topic:
        return "ALG-COMPLEXITY", "循环/递归复杂度、最好最坏平均情形、递归树或主定理"
    high_math_like = subject_filter in {"", "高等数学", "微积分"} or any(
        keyword in topic
        for keyword in [
            "Gauss",
            "Green",
            "Stokes",
            "三重积分",
            "二重积分",
            "曲线积分",
            "曲面积分",
            "多元函数积分",
        ]
    )
    if high_math_like and any(keyword in topic for keyword in ["Stokes", "旋度", "曲面选择"]):
        return "MIX-CALC-STOKES-CURL", (
            "Stokes公式、旋度计算、方向判断与曲面选择"
        )
    if high_math_like and any(keyword in topic for keyword in ["Gauss", "通量", "补面", "第二型曲面积分"]):
        return "CALC-GAUSS-FLUX", (
            "第二型曲面积分、补面、方向判断与奇点/零点讨论"
        )
    if high_math_like and any(keyword in topic for keyword in ["三重积分", "Jacobian", "坐标变换"]):
        return "CALC-TRIPLE-INTEGRAL", (
            "三重积分区域设限、坐标选择、换元与 Jacobian"
        )
    if subject_filter in {"", "高等数学", "微积分"} and any(keyword in topic for keyword in ["Gauss", "曲面积分"]):
        return "CALC-GAUSS-FLUX", "第二型曲面积分、补面、方向判断、奇点/零点特殊情况讨论"
    if subject_filter in {"", "高等数学", "微积分"} and "三重积分" in topic:
        return "CALC-TRIPLE-INTEGRAL", "三重积分区域设限、坐标选择、换元与 Jacobian"
    if subject_filter in {"", "高等数学", "微积分"} and "级数" in topic:
        return "CALC-SERIES", "级数敛散性、求和/展开、逐项求导/积分、一致收敛、Cauchy/D'Alembert与Dirichlet/Abel数项级数判别"
    if subject_filter in {"", "化学原理"} and any(keyword in topic for keyword in ["化学动力学", "反应速率", "反应级数", "Arrhenius"]):
        return "CHEM-KINETICS", "反应级数判断、积分速率方程、半衰期、Arrhenius 公式与活化能"
    if subject_filter in {"", "化学原理"} and any(keyword in topic for keyword in ["化学平衡", "相平衡", "相图", "平衡常数", "自由能"]):
        return "CHEM-EQUILIBRIUM", "化学平衡、相平衡、相图、平衡常数与热力学判据"
    if subject_filter in {"", "大学物理学"}:
        if any(keyword in topic for keyword in ["Compton", "光电效应", "de Broglie", "量子现象"]):
            return "PHYS-QUANTUM-PHENOMENA", "光电效应、Compton 散射、de Broglie 波与量子现象"
        if any(keyword in topic for keyword in ["Schrodinger", "薛定谔", "势阱", "势阶", "量子谐振子"]):
            return "PHYS-SCHRODINGER", "Schrodinger 方程、势阱/势阶、量子谐振子与态演化"
        if any(keyword in topic for keyword in ["量子统计", "费米气体", "玻色气体", "Fermi"]):
            return "PHYS-QUANTUM-STATISTICS", "Bose/Fermi 统计、态密度、Fermi 能与经典极限"
        if any(keyword in topic for keyword in ["原子物理", "Bohr", "氢原子"]):
            return "PHYS-ATOMIC", "Bohr 模型、类氢原子、原子波函数与跃迁反冲"
        if any(keyword in topic for keyword in ["核物理", "核衰变", "放射性", "Q 值"]):
            return "PHYS-NUCLEAR", "核衰变、活度、Q 值、核反冲与半经验质量公式"
        if any(keyword in topic for keyword in ["粒子物理", "夸克", "弱相互作用", "强相互作用"]):
            return "PHYS-PARTICLE", "夸克模型、守恒律、基本相互作用与离散对称性"
    if subject_filter in {"", "大学物理学"} and any(keyword in topic for keyword in ["物理", "角动量", "刚体", "振动", "热力学", "相对论"]):
        if "相对论" in topic or "Lorentz" in topic:
            return "PHYS-RELATIVITY", "狭义相对论时空变换、速度合成、Doppler 效应与相对论动力学"
        if any(keyword in topic for keyword in ["Maxwell 关系", "TdS", "自由能", "热力学势"]):
            return "PHYS-THERMO-POTENTIAL", "热力学势、Maxwell 关系、TdS 方程与偏导恒等式"
        if any(keyword in topic for keyword in ["相变", "Clapeyron", "相图"]):
            return "PHYS-PHASE-TRANSITION", "相图、Clapeyron 方程、一级/二级相变与潜热"
        if any(keyword in topic for keyword in ["Maxwell 分布", "Boltzmann", "统计", "气体微观"]):
            return "PHYS-STATISTICAL", "Maxwell/Boltzmann 分布、统计平均、涨落与分子通量"
        if "熵" in topic:
            return "PHYS-THERMO-ENTROPY", "热过程、相变、熵变、熵增原理与自发性判断"
        return "PHYS-MODELING", "物理建模、守恒律/基本方程选择、公式适用条件与计算"
    return "GEN-MIXED-PRACTICE", "概念判定、基础计算/推导、混合应用与错因归类"


def known_template_ids() -> set[str]:
    from study_app.core.composite_templates import composite_template_ids
    from study_app.core.discrete_math_taxonomy import discrete_template_ids
    from study_app.core.ds_course_taxonomy import ds_template_ids

    return {
        "DS-AVL-ROT",
        "DS-BST-OPS",
        "DS-HASH-ASL",
        "ALG-KMP-PREFIX",
        "ALG-COMPLEXITY",
        "CS-CODING-PRACTICE",
        "CS-ALGORITHM-DESIGN",
        "CS-OJ-PRACTICE",
        "CPP-OOP-PRACTICE",
        "CALC-GAUSS-FLUX",
        "CALC-TRIPLE-INTEGRAL",
        "CALC-SERIES",
        "CHEM-KINETICS",
        "CHEM-EQUILIBRIUM",
        "PHYS-MODELING",
        "PHYS-RELATIVITY",
        "PHYS-REL-EVENTS",
        "PHYS-REL-LIFETIME",
        "PHYS-REL-VELOCITY",
        "PHYS-REL-DOPPLER",
        "PHYS-REL-DYNAMICS",
        "PHYS-REL-DECAY",
        "PHYS-THERMO-ENTROPY",
        "PHYS-TEMP-EOS",
        "PHYS-FIRST-LAW",
        "PHYS-SECOND-LAW-ENGINE",
        "PHYS-THERMO-PROCESS",
        "PHYS-THERMO-POTENTIAL",
        "PHYS-PHASE-TRANSITION",
        "PHYS-STATISTICAL",
        "PHYS-QUANTUM-PHENOMENA",
        "PHYS-SCHRODINGER",
        "PHYS-QUANTUM-STATISTICS",
        "PHYS-ATOMIC",
        "PHYS-NUCLEAR",
        "PHYS-PARTICLE",
        "GEN-MIXED-PRACTICE",
    } | composite_template_ids() | ds_template_ids() | discrete_template_ids()


def exercise_count(day: int) -> int:
    if day <= 1:
        return 2
    return 3
