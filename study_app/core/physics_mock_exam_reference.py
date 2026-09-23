from __future__ import annotations

from typing import Any


REFERENCE_TITLE = "Nanjing University UPI Sample Finals 2006-2019"
REFERENCE_NOTE = (
    "9 套 University Physics I 样卷及答案，共 54 道大题；统一为 6 选 5、每题 20 分，"
    "主要覆盖狭义相对论、宏观热力学、相变与熵、热力学势/Maxwell 关系、气体动理论与统计分布。"
)

EXAM_STRUCTURE = {
    "paper_count": 9,
    "problem_count": 54,
    "selection_rule": "6 选 5",
    "points_per_problem": 20,
    "typical_subparts": "每道大题 2-4 个递进小问",
    "stable_distribution": {
        "狭义相对论": "1-2 道",
        "宏观热力学、熵与热力学势": "3-4 道",
        "气体动理论或统计物理": "1 道",
    },
    "chapter_score_analysis": {
        "method": "对 9 套卷的 54 道大题按小问与主要推导步骤拆分跨章节权重",
        "sample_offered_points": 1080,
        "weights": {
            "第9章：狭义相对论": 0.2778,
            "第10章：温度与气体状态方程": 0.0667,
            "第11章：热力学第一定律": 0.1259,
            "第12章：热力学第二定律与热力学函数": 0.3130,
            "第13章：理想气体的微观模型": 0.1370,
            "第14章：相变": 0.0796,
        },
    },
}

PHYSICS_FINAL_ARCHETYPES: tuple[dict[str, Any], ...] = (
    {"template_id": "PHYS-RELATIVITY", "title": "同时性、时空间隔与事件变换", "topic": "Lorentz 变换", "difficulty": 80, "tested_points": ["Lorentz 变换", "同时性的相对性", "时空间隔", "事件坐标"]},
    {"template_id": "PHYS-RELATIVITY", "title": "相对论速度合成与参考系互换", "topic": "相对论动力学基础", "difficulty": 80, "tested_points": ["速度变换", "参考系", "方向与符号", "互易性"]},
    {"template_id": "PHYS-RELATIVITY", "title": "运动斜杆的长度与倾角变换", "topic": "时间膨胀与长度收缩", "difficulty": 84, "tested_points": ["长度收缩", "横纵分量", "倾角变换", "固有长度"]},
    {"template_id": "PHYS-RELATIVITY", "title": "粒子湮灭与双光子能动量守恒", "topic": "相对论动力学基础", "difficulty": 88, "tested_points": ["四动量", "能量守恒", "动量守恒", "发射角"]},
    {"template_id": "PHYS-RELATIVITY", "title": "相对论 Doppler 频移", "topic": "相对论动力学基础", "difficulty": 76, "tested_points": ["相对论 Doppler", "红移蓝移", "频率变换"]},
    {"template_id": "PHYS-RELATIVITY", "title": "运动带电体的电荷密度变换", "topic": "时间膨胀与长度收缩", "difficulty": 75, "tested_points": ["电荷不变量", "体积收缩", "电荷密度", "参考系"]},
    {"template_id": "PHYS-THERMO-ENTROPY", "title": "有限热容系统接触后的平衡温度与熵增", "topic": "熵与熵增原理", "difficulty": 79, "tested_points": ["能量守恒", "平衡温度", "熵变", "熵增证明"]},
    {"template_id": "PHYS-THERMO-ENTROPY", "title": "自由膨胀与绝热压缩的复合过程", "topic": "热力学第一定律", "difficulty": 85, "tested_points": ["自由膨胀", "绝热过程", "比热比", "总熵变"]},
    {"template_id": "PHYS-THERMO-ENTROPY", "title": "跨温区相变的可逆路径与熵变", "topic": "熵与熵增原理", "difficulty": 82, "tested_points": ["相变潜热", "可逆替代路径", "分段积分", "熵变"]},
    {"template_id": "PHYS-THERMO-ENTROPY", "title": "过冷液体凝固的系统总熵与自发性", "topic": "熵与熵增原理", "difficulty": 84, "tested_points": ["不可逆过程", "环境熵变", "总熵判据", "自发性"]},
    {"template_id": "PHYS-THERMO-PROCESS", "title": "多方过程的功、热量、熵与过程热容", "topic": "理想气体过程", "difficulty": 82, "tested_points": ["多方过程", "功", "熵变", "过程热容"]},
    {"template_id": "PHYS-THERMO-PROCESS", "title": "非理想气体的膨胀系数与压缩系数", "topic": "膨胀系数与压缩系数", "difficulty": 78, "tested_points": ["状态方程", "偏导数", "膨胀系数", "等温压缩系数"]},
    {"template_id": "PHYS-THERMO-POTENTIAL", "title": "Van der Waals 气体等温过程的自由能与内能", "topic": "Helmholtz 与 Gibbs 自由能", "difficulty": 86, "tested_points": ["Van der Waals 方程", "Helmholtz 自由能", "内能变化", "热力学恒等式"]},
    {"template_id": "PHYS-THERMO-POTENTIAL", "title": "由基本方程推导 Maxwell 关系与 TdS 方程", "topic": "Maxwell 关系与 TdS 方程", "difficulty": 88, "tested_points": ["Legendre 变换", "Maxwell 关系", "TdS 方程", "偏导变换"]},
    {"template_id": "PHYS-THERMO-POTENTIAL", "title": "由热力学信息反推状态方程", "topic": "Maxwell 关系与 TdS 方程", "difficulty": 88, "tested_points": ["全微分", "可积条件", "状态方程", "热容约束"]},
    {"template_id": "PHYS-THERMO-POTENTIAL", "title": "橡皮带拉伸中的广义功与温度效应", "topic": "Maxwell 关系与 TdS 方程", "difficulty": 87, "tested_points": ["广义力", "基本热力学方程", "Maxwell 关系", "绝热温升"]},
    {"template_id": "PHYS-THERMO-POTENTIAL", "title": "非理想气体 Carnot 循环与绝热方程", "topic": "热力学第二定律与 Carnot 定理", "difficulty": 89, "tested_points": ["Carnot 循环", "非理想气体", "绝热方程", "效率"]},
    {"template_id": "PHYS-PHASE-TRANSITION", "title": "三相点、Clapeyron 方程与潜热", "topic": "相图与 Clapeyron 方程", "difficulty": 82, "tested_points": ["三相点", "Clapeyron 方程", "升华潜热", "汽化潜热"]},
    {"template_id": "PHYS-PHASE-TRANSITION", "title": "二级相变的热容与熵连续性", "topic": "一级相变与二级相变", "difficulty": 85, "tested_points": ["二级相变", "热容", "熵连续", "低温极限"]},
    {"template_id": "PHYS-STATISTICAL", "title": "Maxwell 分布的归一化、矩与涨落", "topic": "理想气体微观模型", "difficulty": 85, "tested_points": ["Maxwell 分布", "归一化", "统计平均", "涨落"]},
    {"template_id": "PHYS-STATISTICAL", "title": "Maxwell 速率分布与 Gamma 函数积分", "topic": "理想气体微观模型", "difficulty": 88, "tested_points": ["Maxwell 速率分布", "Gamma 函数", "速率矩", "归一化"]},
    {"template_id": "PHYS-STATISTICAL", "title": "逸出分子束的速度分布与通量", "topic": "宏观量与微观量联系", "difficulty": 86, "tested_points": ["分子束", "通量加权", "速度矩", "涨落"]},
    {"template_id": "PHYS-STATISTICAL", "title": "谐振子相空间分布与能量均分", "topic": "理想气体微观模型", "difficulty": 86, "tested_points": ["Boltzmann 分布", "相空间", "能量均分", "位置涨落"]},
)


def mock_exam_reference_block(mode: str) -> str:
    adaptation = {
        "diagnostic": "生成 14-16 道必做题，扩大覆盖面并检测概念链和建模步骤。",
        "standard": "固定生成 10 道必做大题，每题可含递进小问，并在基础、推导、综合之间形成梯度。",
        "sprint": "生成 6-8 道必做综合题，优先突破热力学势、Maxwell 关系、相对论能动量和统计分布。",
    }.get(mode, "按当前卷型调整题量与难度。")
    return (
        f"真实样卷参考：{REFERENCE_TITLE}。{REFERENCE_NOTE}\n"
        "样卷的章节分布和每道大题 2-4 个递进小问可作参考，但样卷的“6选5、每题20分”"
        "不得作为当前卷的题量或选做规则。\n"
        f"当前卷适配要求：{adaptation} 不得直接复制样卷原题，应生成同型变式。"
    )
