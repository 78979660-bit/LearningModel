from __future__ import annotations

SUBJECT_NAME = '概率论与数理统计'
SUBJECT_ALIASES = ('概率论与数理统计', '概率统计', '概率论', '数理统计', 'Probability and Statistics')
CHAPTER_TEMPLATES = [('PROB-CH01', '随机事件与概率', ('随机事件及其运算', '事件概率的定义与性质', '古典概型', '几何概率', '条件概率与乘法公式', '全概率公式与贝叶斯公式', '事件独立性与可靠性', '独立重复试验')), ('PROB-CH02', '随机变量及其概率分布', ('随机变量与分布函数', '离散型随机变量与分布律', '二项、泊松与几何分布', '连续型随机变量与密度函数', '均匀、指数与正态分布', '随机变量函数的分布')), ('PROB-CH03', '二维随机变量及其分布', ('联合分布函数', '二维离散型随机变量', '边缘分布与独立性', '二维连续型随机变量与联合密度', '边缘密度与独立性', '二维均匀与正态分布', '条件分布', '二维随机变量函数的分布')), ('PROB-CH04', '随机变量的数字特征', ('数学期望与随机变量函数的期望', '数学期望的性质', '方差的定义与性质', '切比雪夫不等式', '协方差与相关系数', '矩与协方差阵')), ('PROB-CH05', '极限理论', ('大数定律', '中心极限定理', '二项与泊松分布的正态近似')), ('PROB-CH06', '统计量与抽样分布', ('总体与样本', '统计量与抽样分布', '卡方、t与F分布', '上分位点', '正态总体样本均值与样本方差的分布')), ('PROB-CH07', '参数估计', ('矩估计', '极大似然估计', '无偏性、均方误差与一致性', '枢轴变量法与区间估计', '正态总体均值与方差的置信区间', '两个正态总体均值差与方差比的置信区间', '大样本区间估计')), ('PROB-CH08', '假设检验', ('原假设、备择假设与拒绝域', '两类错误与p值', '正态总体均值检验', '两个正态总体均值差与配对检验', '正态总体方差检验', '拟合优度检验', '列联表与独立性检验')), ('PROB-CH09', '方差分析', ('单因素试验与平方和分解', '单因素方差分析的F检验与参数估计', '双因素等重复试验', '双因素无重复试验')), ('PROB-CH10', '回归分析', ('一元线性回归模型', '最小二乘与极大似然估计', '线性回归的显著性检验', '回归系数与均值的置信区间', '点预测与预测区间', '线性化与多元线性回归')), ('PROB-CH11', '统计软件介绍', ('SAS统计软件', 'SPSS统计软件', 'R统计软件', '其他统计软件与Excel统计功能'))]
PROBABILITY_TEMPLATE_INFO = {key: (name, '围绕'+name+'开展教材习题训练') for key,name,_topics in CHAPTER_TEMPLATES}

def probability_template_for_topic(text: str) -> tuple[str, str]:
    module = text.split('/', 1)[0].strip()
    for key, name, _topics in CHAPTER_TEMPLATES:
        if key in text or module == name:
            return key, PROBABILITY_TEMPLATE_INFO[key][1]
    for key,name,_topics in CHAPTER_TEMPLATES:
        if key in text or name in text:
            return key, PROBABILITY_TEMPLATE_INFO[key][1]
    for key,_name,topics in CHAPTER_TEMPLATES:
        if any(topic in text for topic in topics):
            return key, PROBABILITY_TEMPLATE_INFO[key][1]
    routes = (
        ('PROB-CH08', ('假设检验','p值','拒绝域','显著性水平','拟合优度','列联表')),
        ('PROB-CH07', ('估计','置信区间','似然','无偏','均方误差')),
        ('PROB-CH09', ('方差分析','双因素','单因素')),
        ('PROB-CH10', ('回归','预测区间','最小二乘')),
        ('PROB-CH06', ('抽样','总体','样本','卡方','F分布','t分布','分位点')),
        ('PROB-CH05', ('大数','中心极限','正态近似')),
        ('PROB-CH04', ('期望','协方差','相关系数','方差','切比雪夫','矩')),
        ('PROB-CH03', ('二维','联合','边缘','条件分布')),
        ('PROB-CH02', ('随机变量','分布','密度','二项','泊松','正态','指数','均匀','几何分布')),
        ('PROB-CH11', ('SAS','SPSS','R统计','统计软件','Excel')),
    )
    for key,words in routes:
        if any(word in text for word in words):
            return key, PROBABILITY_TEMPLATE_INFO[key][1]
    return 'PROB-CH01', PROBABILITY_TEMPLATE_INFO['PROB-CH01'][1]
