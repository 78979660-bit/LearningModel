import unittest
from study_app.core.practice_bank import template_for_topic, known_template_ids, split_subject_topic
from study_app.core.active_subjects import subject_reference_violations

class ProbabilityStatisticsRoutingTests(unittest.TestCase):
    def test_statistics_topics_never_fall_into_physics_or_calculus(self):
        cases={'极大似然估计':'PROB-CH07','正态分布':'PROB-CH02','二维正态分布':'PROB-CH03','协方差与相关系数':'PROB-CH04','中心极限定理':'PROB-CH05','统计量与抽样分布':'PROB-CH06','假设检验':'PROB-CH08','双因素方差分析':'PROB-CH09','一元线性回归模型':'PROB-CH10','R统计软件':'PROB-CH11'}
        for topic,expected in cases.items():
            with self.subTest(topic=topic):
                self.assertEqual(template_for_topic(topic,'概率论与数理统计')[0],expected)
                self.assertIn(expected,known_template_ids())

    def test_plan_topics_include_subject_context(self):
        subject,topic=split_subject_topic('概率论与数理统计 / 极大似然估计')
        self.assertEqual(subject,'概率论与数理统计')
        self.assertEqual(topic,'极大似然估计')
        self.assertEqual(template_for_topic('概率论与数理统计 / 极大似然估计')[0],'PROB-CH07')

    def test_aliases_and_scope_guard(self):
        self.assertEqual(template_for_topic('正态分布','概率统计')[0],'PROB-CH02')
        self.assertEqual(subject_reference_violations('概率统计练习',[]),('概率论与数理统计（概率统计）',))
        self.assertEqual(subject_reference_violations('概率统计练习',['概率论与数理统计']),())

    def test_chapter_context_takes_priority_over_overlapping_topic(self):
        self.assertEqual(template_for_topic('方差分析/单因素方差分析的F检验与参数估计','概率论与数理统计')[0],'PROB-CH09')

    def test_existing_subject_routing_is_preserved(self):
        self.assertEqual(template_for_topic('曲面积分','高等数学')[0],'CALC-GAUSS-FLUX')
        self.assertEqual(template_for_topic('类与对象','高级程序设计')[0],'CPP-OOP-PRACTICE')
        self.assertEqual(template_for_topic('相对论时间膨胀','大学物理学')[0],'PHYS-REL-LIFETIME')

if __name__=='__main__': unittest.main()
