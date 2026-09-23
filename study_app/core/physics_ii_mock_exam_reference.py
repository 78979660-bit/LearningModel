from __future__ import annotations

from typing import Any


REFERENCE_TITLE = "Nanjing University UPII Sample Finals 2003-2020"
REFERENCE_NOTE = (
    "9 套 University Physics II 样卷及答案，共 54 道大题；统一为 6 选 5、每题 20 分，"
    "主要覆盖量子现象、Schrodinger 方程、量子统计、原子物理、核衰变和粒子物理。"
)

MODERN_PHYSICS_KEYWORDS = (
    "量子", "Schrodinger", "薛定谔", "Compton", "光电效应", "de Broglie",
    "原子物理", "核物理", "核衰变", "粒子物理", "费米气体", "玻色气体",
)

PHYSICS_II_ARCHETYPES: tuple[dict[str, Any], ...] = (
    {"template_id": "PHYS-QUANTUM-PHENOMENA", "title": "Compton 散射的波长、能量与反冲角", "topic": "量子现象", "difficulty": 82, "tested_points": ["Compton 散射", "能量动量守恒", "反冲电子", "散射角"]},
    {"template_id": "PHYS-QUANTUM-PHENOMENA", "title": "光电效应的逸出功与遏止电压", "topic": "量子现象", "difficulty": 74, "tested_points": ["光电效应", "逸出功", "遏止电压", "光子能量"]},
    {"template_id": "PHYS-QUANTUM-PHENOMENA", "title": "热中子或粒子的 de Broglie 波长", "topic": "量子现象", "difficulty": 76, "tested_points": ["de Broglie 波长", "热运动", "能量动量关系"]},
    {"template_id": "PHYS-SCHRODINGER", "title": "一维无限深势阱的本征态与边界条件", "topic": "Schrodinger 方程", "difficulty": 80, "tested_points": ["定态 Schrodinger 方程", "边界条件", "能级", "归一化"]},
    {"template_id": "PHYS-SCHRODINGER", "title": "环形边界条件下的能级、波函数与简并", "topic": "Schrodinger 方程", "difficulty": 84, "tested_points": ["周期边界条件", "动量本征态", "能级简并", "正交归一"]},
    {"template_id": "PHYS-SCHRODINGER", "title": "谐振子给定波函数的归一化与能量涨落", "topic": "量子谐振子", "difficulty": 86, "tested_points": ["量子谐振子", "归一化", "本征态判断", "能量涨落"]},
    {"template_id": "PHYS-SCHRODINGER", "title": "势阶问题的反射、透射与隧穿", "topic": "Schrodinger 方程", "difficulty": 86, "tested_points": ["势阶", "波函数匹配", "反射系数", "隧穿"]},
    {"template_id": "PHYS-SCHRODINGER", "title": "非定态叠加态的概率密度与平均位置", "topic": "Schrodinger 方程", "difficulty": 87, "tested_points": ["态叠加", "时间演化", "概率密度", "期望值"]},
    {"template_id": "PHYS-QUANTUM-STATISTICS", "title": "玻色子与费米子的基态填充比较", "topic": "量子统计", "difficulty": 78, "tested_points": ["Pauli 不相容原理", "玻色子", "费米子", "基态能量"]},
    {"template_id": "PHYS-QUANTUM-STATISTICS", "title": "自由费米气体的 Fermi 能与总能", "topic": "量子统计", "difficulty": 86, "tested_points": ["态密度", "Fermi 波数", "Fermi 能", "零温总能"]},
    {"template_id": "PHYS-QUANTUM-STATISTICS", "title": "量子气体经典极限与热 de Broglie 波长", "topic": "量子统计", "difficulty": 88, "tested_points": ["Bose/Fermi 分布", "态密度", "Boltzmann 极限", "简并判据"]},
    {"template_id": "PHYS-QUANTUM-STATISTICS", "title": "光子气体的内能、热容和热力学势", "topic": "量子统计", "difficulty": 88, "tested_points": ["光子气体", "态密度", "Planck 分布", "自由能"]},
    {"template_id": "PHYS-QUANTUM-STATISTICS", "title": "极端相对论费米气体的态密度与 Fermi 能", "topic": "量子统计", "difficulty": 90, "tested_points": ["相对论能谱", "态密度", "Fermi 能", "总能"]},
    {"template_id": "PHYS-ATOMIC", "title": "类氢原子的约化质量、Bohr 半径与能级", "topic": "原子物理", "difficulty": 82, "tested_points": ["约化质量", "Bohr 模型", "半径", "能级"]},
    {"template_id": "PHYS-ATOMIC", "title": "氢原子跃迁中的光子与原子反冲修正", "topic": "原子物理", "difficulty": 86, "tested_points": ["原子能级", "跃迁", "能量动量守恒", "反冲修正"]},
    {"template_id": "PHYS-ATOMIC", "title": "氢原子基态的径向概率与期望半径", "topic": "原子物理", "difficulty": 84, "tested_points": ["球坐标归一化", "径向概率密度", "最概然半径", "平均半径"]},
    {"template_id": "PHYS-NUCLEAR", "title": "恒定产生率与放射性衰变的动态平衡", "topic": "核衰变", "difficulty": 79, "tested_points": ["衰变方程", "产生率", "动态平衡", "半衰期"]},
    {"template_id": "PHYS-NUCLEAR", "title": "放射性样品的核子数、平均寿命与活度", "topic": "核衰变", "difficulty": 76, "tested_points": ["核子数", "平均寿命", "半衰期", "活度"]},
    {"template_id": "PHYS-NUCLEAR", "title": "alpha/beta 衰变的 Q 值与反冲能量", "topic": "核衰变", "difficulty": 84, "tested_points": ["Q 值", "原子质量", "能量动量守恒", "反冲"]},
    {"template_id": "PHYS-NUCLEAR", "title": "半经验质量公式与最稳定核素", "topic": "核物理", "difficulty": 88, "tested_points": ["半经验质量公式", "稳定核素", "对称能", "库仑能"]},
    {"template_id": "PHYS-PARTICLE", "title": "夸克组成、量子数与反应允许性", "topic": "粒子物理", "difficulty": 82, "tested_points": ["夸克模型", "重子数", "奇异数", "守恒律"]},
    {"template_id": "PHYS-PARTICLE", "title": "强、电磁、弱相互作用的过程判别", "topic": "粒子物理", "difficulty": 80, "tested_points": ["基本相互作用", "守恒律", "反应时间尺度", "衰变"]},
    {"template_id": "PHYS-PARTICLE", "title": "宇称或电荷共轭变换与弱相互作用", "topic": "粒子物理", "difficulty": 85, "tested_points": ["宇称", "电荷共轭", "弱相互作用", "对称性破缺"]},
)


def model_has_modern_physics(model: dict[str, Any], subject: str = "大学物理学") -> bool:
    for subject_item in model.get("subjects", []):
        if subject_item.get("name") != subject:
            continue
        text = " ".join(
            [str(module.get("name") or "") for module in subject_item.get("modules", [])]
            + [
                str(topic.get("name") or "")
                for module in subject_item.get("modules", [])
                for topic in module.get("topics", [])
            ]
        )
        return any(keyword.lower() in text.lower() for keyword in MODERN_PHYSICS_KEYWORDS)
    return False


def mock_exam_reference_block() -> str:
    return (
        f"现代物理样卷参考：{REFERENCE_TITLE}。{REFERENCE_NOTE}\n"
        "结构稳定为 6 道大题中选做 5 道，每题 20 分且含 2-4 个递进小问。"
        "只能在考试范围明确包含现代物理时使用，并生成同型变式，禁止直接复制原题。"
    )
