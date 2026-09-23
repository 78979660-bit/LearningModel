from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, ensure_seeded_database
from study_app.data.practice_repository import upsert_practice_source


SUBJECT = "化学原理"
SOURCE_TITLE = "化学原理第12-13章上传题目种子 v1"
SOURCE_NOTE = (
    "根据用户于 2026-06-24 上传的化学原理习题截图整理；"
    "作为第12章相平衡/相图和第13章化学动力学的题型、难度与考点种子，"
    "生成新题时应做同型变式，不直接复制原题。"
)


TEMPLATES: tuple[tuple[str, str, str], ...] = (
    ("CHEM-EQUILIBRIUM", "化学平衡与相平衡", "组分数、自由度、相律、相图、Clapeyron 方程、蒸气压与相平衡计算"),
    ("CHEM-KINETICS", "化学动力学", "反应级数、积分速率方程、半衰期、Arrhenius 公式、催化、复合反应与反应机理"),
    ("MIX-CHEM-EQUILIBRIUM-PHASE", "化学平衡与相平衡综合", "化学势、相律、二元/三元相图、蒸馏、共熔、相变热力学综合"),
)


def item(
    template_id: str,
    title: str,
    topic: str,
    difficulty: int,
    points: list[str],
    statement: str,
    image_refs: list[str],
) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "title": title,
        "topic": topic,
        "difficulty": difficulty,
        "tested_points": points,
        "statement": statement,
        "image_refs": image_refs,
    }


EQUILIBRIUM_PROBLEMS: tuple[dict[str, Any], ...] = (
    item("CHEM-EQUILIBRIUM", "多反应气固系统的组分数与自由度", "相律与组分数", 70, ["相律", "组分数", "自由度", "独立反应数"], "给出含 C(s)、CO(g)、H2O(g)、CO2(g)、H2(g) 的平衡系统，要求判断独立组分数 K 和自由度 f，并说明相数与独立反应约束。", ["image1"]),
    item("CHEM-EQUILIBRIUM", "HI 平衡体系在不同初始投料下的组分数", "化学平衡与组分数", 66, ["组分数", "初始约束", "反应平衡", "自由度"], "对 H2(g)+I2(g)<=>2HI(g) 平衡体系，分别讨论只投 HI、等量 H2/I2、任意量 H2/I2/HI 时的组分数与约束差异。", ["image1"]),
    item("CHEM-EQUILIBRIUM", "多相体系相数、组分数与自由度判定组题", "相律与相平衡", 76, ["相数", "组分数", "自由度", "分配平衡"], "给出 Ca(OH)2/CaO 平衡、I2 在水与 CCl4 中分配、NH4HCO3 分解、水盐溶液和水蒸气平衡等体系，逐一判断相数、组分数和自由度。", ["image1"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "冰熔化压力与水蒸气压的 Clapeyron 计算", "Clapeyron 方程", 78, ["Clapeyron 方程", "相变焓", "摩尔体积差", "相边界移动"], "利用冰和水的密度及熔化热估算在 -0.5 C 使冰熔化所需的最小压力，并由水的汽化热估算不同温度或压力下的蒸气压/沸点。", ["image1"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "由蒸气压方程求高原沸点与相变焓", "蒸气压与相变焓", 74, ["Clausius-Clapeyron", "蒸气压方程", "沸点", "相变焓"], "给出 ln(p/Pa)=A-B/T 或含 lnT 项的蒸气压关系，要求计算高原水的沸点、Hg 在指定温度的汽化焓，或由不同温度蒸气压数据求正常沸点。", ["image1", "image2"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "UF6 三相点由固液蒸气压曲线求解", "三相点", 78, ["三相点", "固液气平衡", "蒸气压曲线", "相图"], "给出 UF6 固态和液态饱和蒸气压随温度的函数，求三相点温度和压力。", ["image2"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "理想溶液的组成、蒸气压与沸点升高", "气液平衡", 76, ["Raoult 定律", "理想溶液", "蒸气压", "沸点"], "CCl4 与二氯乙烷混合为理想溶液，给出两组分蒸气压和混合质量，要求计算液相组成、总蒸气压和沸点。", ["image2"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "二元气液 T-x-y 数据作图与蒸馏分析", "二元相图与蒸馏", 84, ["T-x-y 图", "杠杆规则", "简单蒸馏", "相组成"], "给出甲苯-正辛烷或 HNO3-H2O 的液相/气相组成与沸点数据，要求绘制 T-x-y 图，判断冷却或蒸馏过程中的相组成、相量比例与馏出物组成。", ["image2", "image3"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "部分互溶液体的 T-w 相图与临界溶解温度", "液液相平衡", 82, ["部分互溶", "最高/最低会溶温度", "液液相图", "杠杆规则"], "根据甲苯胺-甘油体系不同质量分数下浑浊出现与消失温度数据，绘制 T-w 图，找出最高/最低会溶温度，并求给定温度下两液相组成。", ["image3"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "分层液体质量分配与共轭相组成", "液液相平衡", 72, ["共轭相", "质量分数", "物料衡算", "杠杆规则"], "水和酚混合分为两层，给出酚层/水层组成和总投料，计算两层质量。", ["image3"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "水蒸气蒸馏测定有机物蒸气压与摩尔质量", "水蒸气蒸馏", 80, ["水蒸气蒸馏", "分压", "摩尔质量", "质量分数"], "水蒸气蒸馏有机物时，给出馏出物中水的质量分数、水蒸气压和温度，求有机物饱和蒸气压与摩尔质量。", ["image3"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "合金二元固液相图与冷却曲线", "二元固液相图", 86, ["二元合金相图", "共晶/化合物", "冷却曲线", "杠杆规则"], "根据 Sb-Cd 或 Sn-Ag 系列组成熔点/凝固温度数据绘制相图，标注区域相态和自由度，并计算给定组成冷却到指定温度时各相质量。", ["image3", "image4"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "NaCl-H2O 低共熔与不相合熔点相图", "盐水体系相图", 84, ["低共熔", "不相合熔点", "水合盐", "相图草绘"], "根据 NaCl-H2O 的低共熔点、水合盐析出和转熔反应信息，草绘相图并说明各区域相态。", ["image3"]),
    item("MIX-CHEM-EQUILIBRIUM-PHASE", "三元盐水体系变温结晶分离路线", "三元相图", 88, ["三元相图", "变温结晶", "工艺路线", "相区路径"], "利用 H2O-KNO3-NaNO3 三元相图，设计通过变温结晶分离 KNO3 与 NaNO3 的工艺路线。", ["image4"]),
)


KINETICS_PROBLEMS: tuple[dict[str, Any], ...] = (
    item("CHEM-KINETICS", "定温定容总反应速率与分反应速率关系", "反应速率定义", 62, ["反应速率表示", "化学计量数", "定容体系"], "给出复杂化学反应方程，要求写出各物种浓度变化速率与反应速率之间的关系。", ["image5"]),
    item("CHEM-KINETICS", "一级反应由浓度或压力数据求速率常数", "一级反应积分式", 70, ["一级反应", "积分速率方程", "半衰期", "压力数据"], "对气相或溶液中一级分解反应，给出起始浓度/压力和若干时刻数据，求速率常数与半衰期。", ["image5", "image6", "image7"]),
    item("CHEM-KINETICS", "图解法判断反应级数与速率常数", "积分法判断级数", 78, ["积分法", "图解法", "反应级数", "速率常数"], "给出压力、浓度或电导随时间变化的数据，要求通过作图或线性化判断一级/二级反应并求速率常数。", ["image5", "image6"]),
    item("CHEM-KINETICS", "表观一级反应与真实二级速率常数", "准一级反应", 74, ["表观速率常数", "催化剂浓度", "准一级", "二级反应"], "反应速率正比于反应物浓度和催化剂浓度，催化剂浓度不变时表现为一级，要求由表观一级常数求真实二级常数，并预测改变催化剂浓度后的表观常数。", ["image6"]),
    item("CHEM-KINETICS", "二级反应给定速率常数求转化时间", "二级反应积分式", 76, ["二级反应", "非等初浓度", "积分方程", "转化率"], "给出二级反应速率常数和两个反应物初浓度，求某一反应物反应掉指定百分比所需时间。", ["image6"]),
    item("CHEM-KINETICS", "初始速率法求速率方程", "初始速率法", 68, ["初始速率法", "反应级数", "速率方程", "浓度指数"], "给出 A+B->P 在不同初始浓度下的初速率数据，求速率方程 r=k c_A^alpha c_B^beta。", ["image7"]),
    item("CHEM-KINETICS", "Arrhenius 双温度数据求活化能与速率提升", "Arrhenius 公式", 76, ["Arrhenius", "活化能", "温度效应", "速率常数比"], "给出两个温度下的速率常数或一个反应的活化能，要求计算温度改变时速率常数增加倍数或求活化能。", ["image7", "image8"]),
    item("CHEM-KINETICS", "半衰期随初始压力变化判断反应级数", "半衰期法", 74, ["半衰期", "反应级数", "压力数据", "动力学判别"], "给出不同初始压力下的半衰期数据，利用半衰期与初始浓度关系判断反应级数，并进一步求速率常数。", ["image7", "image8"]),
    item("CHEM-KINETICS", "碰撞理论与过渡态理论计算指前因子", "反应速率理论", 82, ["碰撞理论", "过渡态理论", "指前因子", "活化熵"], "给出反应活化能、分子直径或活化熵数据，分别用简单碰撞理论和过渡态理论计算指前因子并比较结果。", ["image8"]),
    item("CHEM-KINETICS", "对峙反应达到平衡的时间与浓度", "可逆一级反应", 80, ["对峙反应", "正逆速率常数", "平衡组成", "积分式"], "对 A<=>B 可逆一级反应，给出正逆速率常数和初始纯 A，求达到等浓度所需时间以及指定时刻 A/B 浓度。", ["image8"]),
    item("CHEM-KINETICS", "平行反应选择性与温度控制", "平行反应", 84, ["平行反应", "选择性", "Arrhenius", "活化能比较"], "给出 A 并行生成 B、C 的频率因子和活化能，判断要使目标反应速率大于副反应时最低或最高控制温度。", ["image9"]),
    item("CHEM-KINETICS", "连续一级反应的最佳停留时间与最大产率", "连串反应", 84, ["连串反应", "中间产物最大值", "选择性", "最佳反应时间"], "对 A->B->C 连续一级反应，给出 k1、k2，求目标中间产物 B 的最佳反应时间和最大产率。", ["image9"]),
    item("CHEM-KINETICS", "稳态近似和平衡态近似推导速率方程", "反应机理", 88, ["稳态近似", "平衡态近似", "机理判别", "速率方程推导"], "给出 NO 氧化或卤素自由基反应机理，分别用稳态近似和平衡态近似推导速率方程，并与经验速率式比较判断机理是否可能。", ["image9", "image10", "image11"]),
    item("CHEM-KINETICS", "链反应机理由稳态近似导出级数和活化能", "链反应机理", 90, ["链引发", "链传递", "链终止", "阿伦尼乌斯活化能"], "给出乙烷热分解或氯仿光氯化的链反应机理，要求在给定近似条件下证明总反应级数，并导出速率方程和表观活化能表达式。", ["image9", "image11"]),
    item("CHEM-KINETICS", "催化反应速率、选择性与酸催化常数", "催化动力学", 78, ["催化剂", "选择性", "酸催化", "速率常数"], "给出甲醇催化氧化、HI 催化分解、碘化反应或蔗糖变旋等数据，计算催化反应选择性、催化/非催化速率常数比或酸催化常数。", ["image10"]),
    item("CHEM-KINETICS", "酶催化反应最大速率与 Michaelis 常数", "酶催化动力学", 80, ["酶催化", "Michaelis-Menten", "最大速率", "Km"], "给出底物浓度与反应速率数据，用作图法或线性化方法求酶催化反应最大速率和 Km。", ["image11"]),
    item("CHEM-KINETICS", "光化学反应量子产率与可能机理", "光化学动力学", 82, ["量子产率", "光化学", "吸收光能", "反应机理"], "给出光照波长、吸收光能和反应物分解量，求量子产率，并根据量子产率推测可能反应机理。", ["image11"]),
)


PROBLEMS = EQUILIBRIUM_PROBLEMS + KINETICS_PROBLEMS


def import_chemistry_exam_archetypes(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, int]:
    path = ensure_seeded_database(db_path)
    with connect(path) as connection:
        source_id = upsert_practice_source(connection, "uploaded_homework", SOURCE_TITLE, "", SOURCE_NOTE)
        for template_id, title, description in TEMPLATES:
            connection.execute(
                """
                INSERT INTO practice_templates(
                    template_id, subject_hint, topic_hint, title, description,
                    generation_rules_json, source_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_id) DO UPDATE SET
                    subject_hint=excluded.subject_hint,
                    topic_hint=excluded.topic_hint,
                    title=excluded.title,
                    description=excluded.description,
                    generation_rules_json=excluded.generation_rules_json,
                    source_json=excluded.source_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    template_id,
                    SUBJECT,
                    title,
                    template_id,
                    description,
                    dumps({"style": "chemistry_uploaded_exam_archetype", "exam_chapters": [12, 13]}),
                    dumps({"kind": "built_in_chemistry_exam_archetype_v1"}),
                ),
            )
        for problem in PROBLEMS:
            connection.execute(
                """
                INSERT INTO practice_problems(
                    template_id, title, statement, answer_outline,
                    common_errors_json, difficulty_score, difficulty_source,
                    subject_hint, topic_hint, tags_json, source_id, source_note,
                    raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_id, title) DO UPDATE SET
                    statement=excluded.statement,
                    answer_outline=excluded.answer_outline,
                    common_errors_json=excluded.common_errors_json,
                    difficulty_score=excluded.difficulty_score,
                    difficulty_source=excluded.difficulty_source,
                    subject_hint=excluded.subject_hint,
                    topic_hint=excluded.topic_hint,
                    tags_json=excluded.tags_json,
                    source_id=excluded.source_id,
                    source_note=excluded.source_note,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    problem["template_id"],
                    problem["title"],
                    problem["statement"],
                    "上传题型种子用于约束题型、考点和难度；生成新题时应重新构造数据并独立校验答案。",
                    dumps(
                        [
                            "相数、组分数或反应级数判断混淆",
                            "单位换算、温度换算或对数线性化错误",
                            "忽略平衡约束、物料衡算或近似条件",
                        ]
                    ),
                    problem["difficulty"],
                    "uploaded_chemistry_seed",
                    SUBJECT,
                    problem["topic"],
                    dumps(problem["tested_points"] + problem["image_refs"] + [problem["template_id"]]),
                    source_id,
                    SOURCE_NOTE,
                    dumps(problem),
                ),
            )
    return {
        "templates": len(TEMPLATES),
        "problems": len(PROBLEMS),
        "equilibrium_phase": len(EQUILIBRIUM_PROBLEMS),
        "kinetics": len(KINETICS_PROBLEMS),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    print(json.dumps(import_chemistry_exam_archetypes(), ensure_ascii=False, indent=2))
