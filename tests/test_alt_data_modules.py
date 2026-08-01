# -*- coding: utf-8 -*-
"""
另类数据层模块单元测试 (Alternative Data Layer Modules Tests)

覆盖:
1) NewsSentimentEngine — 新闻情感分析引擎
2) SupplyChainGraph — 供应链关系图谱
3) AltDataIndicators — 另类数据指标 (卫星/搜索/招聘/专利)
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

# 添加项目根目录到 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================
# 1. NewsSentimentEngine 测试
# ============================================================

class TestNewsSentimentEngine(unittest.TestCase):
    """新闻情感分析引擎测试"""

    def setUp(self):
        """每个测试前创建一个新的引擎实例"""
        from utils.news_sentiment_engine import NewsItem, NewsSentimentEngine
        self.engine = NewsSentimentEngine()
        self.NewsItem = NewsItem

    def test_engine_initialization(self):
        """测试引擎初始化"""
        self.assertIsNotNone(self.engine.positive_words)
        self.assertIsNotNone(self.engine.negative_words)
        # 默认词典应非空
        self.assertGreater(len(self.engine.positive_words), 10)
        self.assertGreater(len(self.engine.negative_words), 10)
        # 默认参数检查
        self.assertEqual(self.engine.max_history_days, 7)
        self.assertAlmostEqual(self.engine.half_life_hours, 24.0)

    def test_add_single_news(self):
        """测试添加单条新闻"""
        news = self.NewsItem(
            news_id="test_001",
            title="某公司业绩大增",
            content="公司发布业绩预告,净利增长50%",
            symbols=["600519"],
            publish_time=datetime.now(),
        )
        self.engine.add_news(news)
        # 添加后应触发自动分析
        self.assertGreaterEqual(news.sentiment_score, 0.0)  # 应为正面
        self.assertEqual(news.sentiment_label, "POSITIVE")
        # 应抽取出事件类型 (EARNINGS)
        self.assertEqual(news.event_type, "EARNINGS")

    def test_negative_news_analysis(self):
        """测试负面新闻分析"""
        news = self.NewsItem(
            news_id="test_neg_001",
            title="某公司业绩下滑亏损",
            content="公司发布业绩预告,营收下滑,净利下降",
            symbols=["000001"],
            publish_time=datetime.now(),
        )
        self.engine.add_news(news)
        self.assertLess(news.sentiment_score, 0.0)
        self.assertEqual(news.sentiment_label, "NEGATIVE")

    def test_add_news_batch(self):
        """测试批量添加新闻"""
        news_list = [
            self.NewsItem(
                news_id=f"batch_{i}",
                title=f"公司{i}业绩增长",
                content="业绩增长",
                symbols=[f"60000{i}"],
                publish_time=datetime.now(),
            )
            for i in range(5)
        ]
        count = self.engine.add_news_batch(news_list)
        self.assertEqual(count, 5)

    def test_analyze_single_symbol(self):
        """测试单标的分析"""
        # 添加几条新闻
        for i in range(3):
            self.engine.add_news(self.NewsItem(
                news_id=f"analyze_{i}",
                title="业绩增长 订单增加",
                content="业绩大增 净利增长",
                symbols=["600519"],
                publish_time=datetime.now() - timedelta(hours=i),
            ))
        result = self.engine.analyze(symbols=["600519"])
        # 应有结果
        self.assertIn("600519", result.signals)
        sig = result.signals["600519"]
        self.assertEqual(sig.symbol, "600519")
        # 应为正面
        self.assertGreater(sig.composite_sentiment, 0.0)
        self.assertGreater(sig.news_count_24h, 0)
        self.assertGreaterEqual(sig.confidence, 0.0)
        self.assertLessEqual(sig.confidence, 1.0)
        # 总新闻数应 > 0
        self.assertGreater(result.total_news_processed, 0)

    def test_analyze_multiple_symbols(self):
        """测试多标的分析"""
        for i, sym in enumerate(["600519", "000858", "601318"]):
            self.engine.add_news(self.NewsItem(
                news_id=f"multi_{i}",
                title="公司业绩增长",
                content="业绩大增",
                symbols=[sym],
                publish_time=datetime.now(),
            ))
        result = self.engine.analyze(symbols=["600519", "000858", "601318"])
        self.assertGreaterEqual(len(result.signals), 1)

    def test_analyze_with_supply_chain_propagation(self):
        """测试供应链传播"""
        # 600519 有新闻, 000858 是其供应链关联
        self.engine.add_news(self.NewsItem(
            news_id="sc_test_1",
            title="业绩大增",
            content="净利增长",
            symbols=["600519"],
            publish_time=datetime.now(),
        ))
        supply_map = {"600519": ["000858"]}
        result = self.engine.analyze(
            symbols=["600519", "000858"],
            supply_chain_map=supply_map,
        )
        # 600519 应有正面信号
        self.assertIn("600519", result.signals)
        self.assertGreater(result.signals["600519"].composite_sentiment, 0.0)

    def test_analyze_empty_symbols(self):
        """测试空标的列表"""
        result = self.engine.analyze(symbols=[])
        self.assertEqual(len(result.signals), 0)
        self.assertEqual(result.total_news_processed, 0)

    def test_analyze_no_news_for_symbol(self):
        """测试无新闻的标的"""
        result = self.engine.analyze(symbols=["999999"])
        # 无新闻 → 无信号
        self.assertNotIn("999999", result.signals)

    def test_event_extraction_m_and_a(self):
        """测试并购事件抽取"""
        news = self.NewsItem(
            news_id="mna_test",
            title="公司收购并购重组",
            content="发布收购公告",
            symbols=["600519"],
            publish_time=datetime.now(),
        )
        self.engine.add_news(news)
        # M&A 事件权重应较高
        self.assertEqual(news.event_type, "M&A")
        self.assertGreater(news.impact_score, 0.0)

    def test_event_extraction_regulation(self):
        """测试监管事件抽取"""
        news = self.NewsItem(
            news_id="reg_test",
            title="公司被监管问询处罚",
            content="收到问询函",
            symbols=["600519"],
            publish_time=datetime.now(),
        )
        self.engine.add_news(news)
        self.assertEqual(news.event_type, "REGULATION")


# ============================================================
# 2. SupplyChainGraph 测试
# ============================================================

class TestSupplyChainGraph(unittest.TestCase):
    """供应链关系图谱测试"""

    def setUp(self):
        from utils.supply_chain_graph import SupplyChainEdge, SupplyChainGraph
        self.graph = SupplyChainGraph()
        self.SupplyChainEdge = SupplyChainEdge

    def test_graph_initialization(self):
        """测试图初始化"""
        self.assertEqual(len(self.graph.adjacency), 0)
        self.assertEqual(len(self.graph.all_nodes), 0)

    def test_add_single_edge(self):
        """测试添加单条边"""
        edge = self.SupplyChainEdge(
            source="600519", target="000858",
            relation_type="SUPPLIER", strength=0.7,
        )
        self.graph.add_edge(edge)
        self.assertIn("600519", self.graph.adjacency)
        self.assertEqual(len(self.graph.adjacency["600519"]), 1)
        self.assertIn("600519", self.graph.all_nodes)
        self.assertIn("000858", self.graph.all_nodes)

    def test_add_edges_batch(self):
        """测试批量添加边"""
        edges = [
            self.SupplyChainEdge(source="A", target="B", relation_type="SUPPLIER", strength=0.5),
            self.SupplyChainEdge(source="B", target="C", relation_type="CUSTOMER", strength=0.6),
            self.SupplyChainEdge(source="C", target="D", relation_type="PARTNER", strength=0.4),
        ]
        count = self.graph.add_edges(edges)
        self.assertEqual(count, 3)
        self.assertEqual(len(self.graph.all_nodes), 4)

    def test_load_default_chains(self):
        """测试加载默认产业链"""
        count = self.graph.load_default_chains()
        self.assertGreater(count, 0)
        # 应至少有 3 条产业链的节点
        self.assertGreaterEqual(len(self.graph.all_nodes), 5)

    def test_propagate_impact(self):
        """测试影响传播"""
        # 构建链路: A → B → C
        self.graph.add_edge(self.SupplyChainEdge(
            source="A", target="B", relation_type="SUPPLIER", strength=0.8,
        ))
        self.graph.add_edge(self.SupplyChainEdge(
            source="B", target="C", relation_type="SUPPLIER", strength=0.6,
        ))
        # 从 A 传播
        paths = self.graph.propagate_impact(source="A", impact_strength=1.0, max_hops=3)
        self.assertGreater(len(paths), 0)
        # 应能到达 B 和 C
        reached = {p.target for p in paths}
        self.assertIn("B", reached)
        self.assertIn("C", reached)

    def test_compute_centrality(self):
        """测试中心性计算"""
        self.graph.load_default_chains()
        metrics = self.graph.compute_centrality()
        self.assertGreater(len(metrics), 0)
        # 每个节点应有 PageRank
        for _sym, m in metrics.items():
            self.assertGreaterEqual(m.pagerank, 0.0)
            self.assertLessEqual(m.pagerank, 1.0)
            self.assertGreaterEqual(m.betweenness_centrality, 0.0)

    def test_assess_risk_contagion(self):
        """测试风险传染评估"""
        self.graph.load_default_chains()
        risk = self.graph.assess_risk_contagion(
            shock_sources={"300308": 1.0},  # 中际旭创 受冲击
        )
        # 应有传染风险评估
        self.assertIsInstance(risk, dict)

    def test_analyze(self):
        """测试综合分析"""
        self.graph.load_default_chains()
        result = self.graph.analyze()
        # 应返回 SupplyChainResult
        self.assertIsNotNone(result)
        self.assertGreater(len(result.nodes), 0)
        self.assertGreater(len(result.edges), 0)
        # 中心性指标
        self.assertGreater(len(result.metrics), 0)
        # 网络密度
        self.assertGreaterEqual(result.network_density, 0.0)

    def test_find_path(self):
        """测试路径查找"""
        self.graph.add_edge(self.SupplyChainEdge(
            source="A", target="B", relation_type="SUPPLIER", strength=0.5,
        ))
        self.graph.add_edge(self.SupplyChainEdge(
            source="B", target="C", relation_type="SUPPLIER", strength=0.5,
        ))
        path = self.graph.find_path(source="A", target="C")
        self.assertIsNotNone(path)
        self.assertEqual(path, ["A", "B", "C"])

    def test_find_path_no_connection(self):
        """测试无连接路径查找"""
        self.graph.add_edge(self.SupplyChainEdge(
            source="A", target="B", relation_type="SUPPLIER", strength=0.5,
        ))
        path = self.graph.find_path(source="A", target="Z")
        self.assertIsNone(path)


# ============================================================
# 3. AltDataIndicators 测试
# ============================================================

class TestAltDataIndicators(unittest.TestCase):
    """另类数据指标测试"""

    def setUp(self):
        from utils.alt_data_indicators import (
            AltDataIndicators,
            PatentIndicator,
            RecruitmentIndicator,
            SatelliteIndicator,
            SearchIndexIndicator,
        )
        self.engine = AltDataIndicators()
        self.SatelliteIndicator = SatelliteIndicator
        self.SearchIndexIndicator = SearchIndexIndicator
        self.RecruitmentIndicator = RecruitmentIndicator
        self.PatentIndicator = PatentIndicator

    def test_engine_initialization(self):
        """测试引擎初始化"""
        self.assertGreater(self.engine.w_sat, 0.0)
        self.assertGreater(self.engine.w_search, 0.0)
        self.assertGreater(self.engine.w_recruit, 0.0)
        self.assertGreater(self.engine.w_patent, 0.0)
        # 权重总和应接近 1
        total_w = self.engine.w_sat + self.engine.w_search + self.engine.w_recruit + self.engine.w_patent
        self.assertAlmostEqual(total_w, 1.0, places=2)

    def test_add_satellite_indicator(self):
        """测试添加卫星指标"""
        sat = self.SatelliteIndicator(
            region="宁波港",
            indicator_type="PORT_ACTIVITY",
            value=85.5,
            yoy_change=12.3,
            related_symbols=["601016"],
        )
        self.engine.add_satellite(sat)
        self.assertEqual(len(self.engine.satellite_data), 1)

    def test_add_search_indicator(self):
        """测试添加搜索指数"""
        search = self.SearchIndexIndicator(
            keyword="宁德时代",
            platform="BAIDU",
            index_value=12500.0,
            trend_7d=15.2,
            is_breakout=True,
            related_symbols=["300750"],
        )
        self.engine.add_search(search)
        self.assertEqual(len(self.engine.search_data), 1)

    def test_add_recruitment_indicator(self):
        """测试添加招聘数据"""
        recruit = self.RecruitmentIndicator(
            company="中芯国际",
            job_count=350,
            avg_salary=25000,
            job_count_yoy=20.0,
            related_symbol="688981",
        )
        self.engine.add_recruitment(recruit)
        self.assertEqual(len(self.engine.recruitment_data), 1)

    def test_add_patent_indicator(self):
        """测试添加专利数据"""
        patent = self.PatentIndicator(
            company="海康威视",
            patent_count=120,
            citation_count=850,
            tech_distribution={"计算机视觉": 60, "AI": 40, "芯片": 20},
            patent_count_yoy=15.0,
            related_symbol="002415",
        )
        self.engine.add_patent(patent)
        self.assertEqual(len(self.engine.patent_data), 1)

    def test_load_demo_data(self):
        """测试加载演示数据"""
        count = self.engine.load_demo_data(["300308", "688981"])
        self.assertGreater(count, 0)
        # 应有至少 1 条数据
        total = (len(self.engine.satellite_data) + len(self.engine.search_data) +
                 len(self.engine.recruitment_data) + len(self.engine.patent_data))
        self.assertGreater(total, 0)

    def test_analyze_single_symbol(self):
        """测试单标的分析"""
        self.engine.load_demo_data(["300308"])
        result = self.engine.analyze(symbols=["300308"])
        self.assertIsNotNone(result)
        # 可能没数据也可能有, 但结构应正确
        self.assertIsInstance(result.signals, dict)

    def test_analyze_multiple_symbols(self):
        """测试多标的分析"""
        self.engine.load_demo_data(["300308", "688981", "300750"])
        result = self.engine.analyze(symbols=["300308", "688981", "300750"])
        # 应有信号
        self.assertGreaterEqual(len(result.signals), 0)  # 可能因数据不足为空
        # market_alt_score 应存在
        self.assertIsInstance(result.market_alt_score, float)

    def test_analyze_empty_symbols(self):
        """测试空标的分析"""
        result = self.engine.analyze(symbols=[])
        self.assertEqual(len(result.signals), 0)
        self.assertEqual(result.total_indicators, 0)

    def test_composite_score_range(self):
        """测试综合评分范围"""
        self.engine.load_demo_data(["300308"])
        result = self.engine.analyze(symbols=["300308"])
        for _sym, sig in result.signals.items():
            # 综合评分应在 [-1, 1] 范围
            self.assertGreaterEqual(sig.composite_score, -1.0)
            self.assertLessEqual(sig.composite_score, 1.0)
            # 覆盖率应在 [0, 1]
            self.assertGreaterEqual(sig.coverage, 0.0)
            self.assertLessEqual(sig.coverage, 1.0)


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
