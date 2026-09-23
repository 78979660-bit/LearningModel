from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompositeTemplate:
    template_id: str
    subject: str
    title: str
    description: str
    components: tuple[str, ...]
    keyword_groups: tuple[tuple[str, ...], ...]


COMPOSITE_TEMPLATES = (
    CompositeTemplate(
        "MIX-CALC-TRIPLE-DIVERGENCE",
        "高等数学",
        "三重积分与散度综合",
        "三重积分区域设限、坐标变换、散度计算与 Gauss 公式综合应用",
        ("三重积分", "散度与Gauss公式"),
        (
            ("三重积分", "triple integral", "体积分", "volume integral", "3d solid", "region g", "柱坐标", "球坐标"),
            ("散度", "divergence", "Gauss", "高斯公式", "通量"),
        ),
    ),
    CompositeTemplate(
        "MIX-CALC-STOKES-CURL",
        "高等数学",
        "Stokes公式与旋度综合",
        "旋度计算、方向判断、曲面选择与 Stokes 公式综合应用",
        ("Stokes公式", "旋度"),
        (
            ("Stokes", "斯托克斯", "曲线积分"),
            ("旋度", "curl", "rot"),
        ),
    ),
    CompositeTemplate(
        "MIX-CALC-SERIES-UNIFORM",
        "高等数学",
        "级数与一致收敛综合",
        "数项级数判别、函数项级数一致收敛及连续性或逐项运算证明",
        ("级数敛散性", "一致收敛"),
        (
            ("级数", "series", "收敛", "convergence"),
            ("一致收敛", "uniform convergence", "Weierstrass", "M判别"),
        ),
    ),
    CompositeTemplate(
        "MIX-DS-COMPLEXITY-HASH",
        "数据结构与算法基础",
        "复杂度与哈希表综合",
        "冲突处理、装填因子、平均查找长度与复杂度分析综合",
        ("第1章：算法复杂度分析", "第6章：字典与哈希表"),
        (
            ("复杂度", "complexity", "Big-O", "摊还"),
            ("哈希", "散列", "hash", "probing"),
        ),
    ),
    CompositeTemplate(
        "MIX-DS-BST-AVL",
        "数据结构与算法基础",
        "二叉搜索树与AVL综合",
        "二叉搜索树操作、平衡因子、旋转与插入删除调整综合",
        ("第7章：二叉搜索树", "第7章：AVL树"),
        (
            ("二叉搜索树", "BST", "binary search tree"),
            ("AVL", "平衡因子", "旋转"),
        ),
    ),
    CompositeTemplate(
        "MIX-DS-SORT-COMPLEXITY",
        "数据结构与算法基础",
        "排序算法与复杂度综合",
        "排序过程推演、稳定性、时间空间复杂度与算法选择综合",
        ("第9章：排序算法", "第1章：复杂度分析"),
        (
            ("排序", "快排", "堆排序", "归并排序", "sorting", "quicksort", "heapsort", "mergesort"),
            ("复杂度", "complexity", "稳定性", "最好", "最坏", "平均"),
        ),
    ),
    CompositeTemplate(
        "MIX-DS-SEARCH-STRUCTURES",
        "数据结构与算法基础",
        "查找结构综合",
        "哈希表、二叉搜索树与 AVL 树的构造、查找性能和适用场景综合",
        ("第6章：哈希表", "第7章：二叉搜索树与AVL树"),
        (
            ("哈希", "散列", "hash", "二叉搜索树", "BST", "AVL"),
            ("查找", "搜索", "search", "平均查找长度", "性能"),
        ),
    ),
    CompositeTemplate(
        "MIX-DS-FINAL-COMPREHENSIVE",
        "数据结构与算法基础",
        "数据结构期末综合",
        "覆盖第1至第9章的概念、过程推演、算法设计与跨章节综合训练",
        ("第1章绪论", "第2至4章线性结构", "第5至7章树与搜索", "第8章图", "第9章排序"),
        (
            ("复杂度", "树", "查找", "排序", "算法"),
            ("综合", "期末", "模拟", "跨章节", "设计"),
        ),
    ),
    CompositeTemplate(
        "MIX-PHYS-THERMO-CYCLE",
        "大学物理学",
        "热力学过程与循环综合",
        "状态方程、热力学第一定律、过程量与热机循环综合",
        ("热力学过程", "热机循环"),
        (
            ("热力学第一定律", "内能", "状态方程", "thermodynamic"),
            ("循环", "热机", "Carnot", "Otto", "Joule"),
        ),
    ),
    CompositeTemplate(
        "MIX-CHEM-EQUILIBRIUM-PHASE",
        "化学原理",
        "化学平衡与相平衡综合",
        "化学势、平衡条件、相图与相平衡计算综合",
        ("化学平衡", "相平衡"),
        (
            ("化学平衡", "平衡常数", "chemical equilibrium"),
            ("相平衡", "相图", "phase equilibrium", "phase diagram"),
        ),
    ),
)

COMPOSITE_BY_ID = {item.template_id: item for item in COMPOSITE_TEMPLATES}


def composite_template_for_text(text: str, subject: str | None = None) -> CompositeTemplate | None:
    normalized = (text or "").lower()
    subject_filter = (subject or "").strip()
    matches = [
        template
        for template in COMPOSITE_TEMPLATES
        if (not subject_filter or template.subject == subject_filter)
        if all(any(keyword.lower() in normalized for keyword in group) for group in template.keyword_groups)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: sum(len(group) for group in item.keyword_groups))


def composite_template_ids() -> set[str]:
    return set(COMPOSITE_BY_ID)


def composite_components(template_id: str) -> tuple[str, ...]:
    template = COMPOSITE_BY_ID.get(template_id)
    return template.components if template else ()
