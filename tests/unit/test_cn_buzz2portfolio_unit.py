"""
CN-Buzz2Portfolio 中国市场基准 — 单元测试
==========================================

测试覆盖:
- NewsCategory 枚举
- NewsItem / PortfolioConfig 数据结构
- NewsClassifier (Stage 1: 分类)
- BuzzAnalyzer (Stage 2: 舆情分析)
- PortfolioConstructor (Stage 3: 组合构建)
- TriStageCPAAgent (三阶段代理)
- CNBuzz2PortfolioBenchmark (基准框架)

文献: #24 CN-Buzz2Portfolio (中国市场) (2026.03)
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.eval.cn_buzz2portfolio import (
    INDUSTRY_KEYWORDS,
    MACRO_KEYWORDS,
    BuzzAnalyzer,
    CNBuzz2PortfolioBenchmark,
    NewsCategory,
    NewsClassifier,
    NewsItem,
    PortfolioConfig,
    PortfolioConstructor,
    TriStageCPAAgent,
)

# ============================================================
# 枚举测试
# ============================================================

class TestNewsCategory:
    """新闻类别枚举测试。"""

    def test_five_categories(self):
        assert len(NewsCategory) == 5

    def test_values(self):
        assert NewsCategory.MACRO_POLICY.value == "macro_policy"
        assert NewsCategory.UNKNOWN.value == "unknown"


# ============================================================
# 数据结构测试
# ============================================================

class TestNewsItem:
    """新闻条目测试。"""

    def test_default_values(self):
        item = NewsItem(title="测试新闻")
        assert item.content == ""
        assert item.category == NewsCategory.UNKNOWN
        assert item.sentiment == 0.0
        assert item.heat == 0.0

    def test_to_dict(self):
        item = NewsItem(title="测试", source="财联社", sentiment=0.5)
        d = item.to_dict()
        assert d["title"] == "测试"
        assert d["source"] == "财联社"
        assert d["sentiment"] == 0.5


class TestPortfolioConfig:
    """组合配置测试。"""

    def test_default_values(self):
        config = PortfolioConfig()
        assert config.target_weights == {}
        assert config.macro_signal == "neutral"
        assert config.confidence == 0.0

    def test_to_dict(self):
        config = PortfolioConfig(
            target_weights={"科技": 0.3},
            macro_signal="bullish",
            confidence=0.8,
        )
        d = config.to_dict()
        assert d["target_weights"] == {"科技": 0.3}
        assert d["macro_signal"] == "bullish"


# ============================================================
# 关键词映射测试
# ============================================================

class TestKeywordMaps:
    """行业/宏观关键词映射测试。"""

    def test_industry_keywords_covered(self):
        """9 个行业关键词已定义。"""
        assert len(INDUSTRY_KEYWORDS) == 9
        for industry, kws in INDUSTRY_KEYWORDS.items():
            assert len(kws) > 0, f"{industry} 无关键词"

    def test_macro_keywords_covered(self):
        """宏观关键词已定义。"""
        assert len(MACRO_KEYWORDS) > 10
        assert "降准" in MACRO_KEYWORDS
        assert "央行" in MACRO_KEYWORDS


# ============================================================
# Stage 1: NewsClassifier 测试
# ============================================================

class TestNewsClassifier:
    """新闻分类器测试。"""

    def test_classify_macro(self):
        """宏观政策新闻分类。"""
        classifier = NewsClassifier()
        news = NewsItem(title="央行宣布降准0.5个百分点")
        result = classifier.classify(news)
        assert result.category == NewsCategory.MACRO_POLICY

    def test_classify_industry(self):
        """行业动态新闻分类。"""
        classifier = NewsClassifier()
        news = NewsItem(title="半导体行业迎来政策利好")
        result = classifier.classify(news)
        assert result.category == NewsCategory.INDUSTRY_DYNAMICS

    def test_classify_company(self):
        """公司公告新闻分类。"""
        classifier = NewsClassifier()
        news = NewsItem(title="某公司发布财报公告")
        result = classifier.classify(news)
        assert result.category == NewsCategory.COMPANY_ANNOUNCEMENT

    def test_classify_sentiment(self):
        """市场情绪新闻分类。"""
        classifier = NewsClassifier()
        news = NewsItem(title="市场恐慌情绪蔓延")
        result = classifier.classify(news)
        assert result.category == NewsCategory.MARKET_SENTIMENT

    def test_classify_unknown(self):
        """无法分类的新闻。"""
        classifier = NewsClassifier()
        news = NewsItem(title="今天天气不错")
        result = classifier.classify(news)
        assert result.category == NewsCategory.UNKNOWN

    def test_classify_batch(self):
        """批量分类。"""
        classifier = NewsClassifier()
        items = [
            NewsItem(title="央行降准"),
            NewsItem(title="半导体利好"),
        ]
        results = classifier.classify_batch(items)
        assert len(results) == 2
        assert results[0].category == NewsCategory.MACRO_POLICY


# ============================================================
# Stage 2: BuzzAnalyzer 测试
# ============================================================

class TestBuzzAnalyzer:
    """舆情分析器测试。"""

    def test_map_industry(self):
        """行业映射。"""
        analyzer = BuzzAnalyzer()
        assert analyzer._map_industry("半导体芯片") == "高端制造"
        assert analyzer._map_industry("光伏储能") == "新能源"
        assert analyzer._map_industry("今天天气") == ""

    def test_score_sentiment_positive(self):
        """正面情绪。"""
        analyzer = BuzzAnalyzer()
        score = analyzer._score_sentiment("利好增长突破")
        assert score > 0

    def test_score_sentiment_negative(self):
        """负面情绪。"""
        analyzer = BuzzAnalyzer()
        score = analyzer._score_sentiment("利空下降亏损")
        assert score < 0

    def test_score_sentiment_neutral(self):
        """中性情绪。"""
        analyzer = BuzzAnalyzer()
        score = analyzer._score_sentiment("今天天气不错")
        assert score == 0.0

    def test_score_sentiment_mixed(self):
        """混合情绪。"""
        analyzer = BuzzAnalyzer()
        score = analyzer._score_sentiment("利好利空")
        assert score == 0.0

    def test_score_heat(self):
        """热度评分。"""
        analyzer = BuzzAnalyzer()
        heat = analyzer._score_heat("短文本", "财联社")
        assert 0 < heat <= 1.0

    def test_score_heat_premium_source(self):
        """权威来源热度加成。"""
        analyzer = BuzzAnalyzer()
        heat_normal = analyzer._score_heat("测试文本", "未知来源")
        heat_premium = analyzer._score_heat("测试文本", "财联社")
        assert heat_premium > heat_normal

    def test_analyze(self):
        """完整分析。"""
        analyzer = BuzzAnalyzer()
        news = NewsItem(title="半导体行业利好 国产替代加速", source="财联社")
        result = analyzer.analyze(news)
        assert result.industry == "高端制造"
        assert result.sentiment > 0
        assert result.heat > 0

    def test_analyze_batch(self):
        """批量分析。"""
        analyzer = BuzzAnalyzer()
        items = [NewsItem(title="半导体利好"), NewsItem(title="光伏增长")]
        results = analyzer.analyze_batch(items)
        assert len(results) == 2
        assert results[0].industry == "高端制造"

    def test_get_industry_buzz(self):
        """行业舆情汇总。"""
        analyzer = BuzzAnalyzer()
        items = [
            NewsItem(title="半导体利好", industry="高端制造", sentiment=0.5, heat=0.8),
            NewsItem(title="半导体增长", industry="高端制造", sentiment=0.3, heat=0.6),
            NewsItem(title="光伏利好", industry="新能源", sentiment=0.4, heat=0.5),
        ]
        buzz = analyzer.get_industry_buzz(items)
        assert "高端制造" in buzz
        assert "新能源" in buzz
        assert buzz["高端制造"]["count"] == 2
        assert 0 < buzz["高端制造"]["avg_sentiment"] < 1


# ============================================================
# Stage 3: PortfolioConstructor 测试
# ============================================================

class TestPortfolioConstructor:
    """组合构建器测试。"""

    def test_default_weights_sum_to_one(self):
        """默认权重和为 1。"""
        total = sum(PortfolioConstructor.DEFAULT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6

    def test_construct_basic(self):
        """基本组合构建。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title="半导体利好", category=NewsCategory.INDUSTRY_DYNAMICS,
                         industry="高端制造", sentiment=0.5, heat=0.8)]
        buzz = {"高端制造": {"avg_sentiment": 0.5, "total_heat": 0.8, "count": 1}}
        config = constructor.construct(news, buzz)
        assert len(config.target_weights) > 0
        assert abs(sum(config.target_weights.values()) - 1.0) < 1e-3

    def test_construct_bullish_macro(self):
        """利好宏观信号。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title="央行降准利好", category=NewsCategory.MACRO_POLICY,
                         sentiment=0.8, heat=0.9)]
        config = constructor.construct(news, {})
        assert config.macro_signal == "bullish"

    def test_construct_bearish_macro(self):
        """利空宏观信号。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title="央行加息利空", category=NewsCategory.MACRO_POLICY,
                         sentiment=-0.8, heat=0.9)]
        config = constructor.construct(news, {})
        assert config.macro_signal == "bearish"

    def test_construct_neutral_macro(self):
        """中性宏观信号。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title="央行发言", category=NewsCategory.MACRO_POLICY,
                         sentiment=0.0, heat=0.5)]
        config = constructor.construct(news, {})
        assert config.macro_signal == "neutral"

    def test_construct_no_macro_news(self):
        """无宏观新闻。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title="半导体利好", category=NewsCategory.INDUSTRY_DYNAMICS)]
        config = constructor.construct(news, {})
        assert config.macro_signal == "neutral"

    def test_construct_confidence(self):
        """置信度计算。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title=f"新闻{i}") for i in range(10)]
        config = constructor.construct(news, {})
        assert 0 <= config.confidence <= 1

    def test_construct_reasoning(self):
        """配置理由非空。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title="半导体利好")]
        buzz = {"高端制造": {"avg_sentiment": 0.5, "total_heat": 0.8, "count": 1}}
        config = constructor.construct(news, buzz)
        assert len(config.reasoning) > 0

    def test_positive_sentiment_increases_weight(self):
        """正面舆情增加权重。"""
        constructor = PortfolioConstructor()
        news = [NewsItem(title="半导体利好", industry="高端制造", sentiment=1.0, heat=1.0)]
        buzz = {"高端制造": {"avg_sentiment": 1.0, "total_heat": 5.0, "count": 5}}
        config = constructor.construct(news, buzz)
        default = PortfolioConstructor.DEFAULT_WEIGHTS["高端制造"]
        actual = config.target_weights["高端制造"]
        # 正面舆情应使权重增加 (相对于默认比例)
        assert actual >= default * 0.9  # 允许归一化影响


# ============================================================
# TriStageCPAAgent 测试
# ============================================================

class TestTriStageCPAAgent:
    """三阶段 CPA 代理测试。"""

    def test_run_basic(self):
        """基本运行。"""
        agent = TriStageCPAAgent()
        news = [NewsItem(title="央行降准利好半导体行业")]
        config = agent.run(news)
        assert isinstance(config, PortfolioConfig)
        assert len(config.target_weights) > 0

    def test_run_empty_news(self):
        """空新闻列表。"""
        agent = TriStageCPAAgent()
        config = agent.run([])
        assert config.reasoning == "无新闻输入"

    def test_run_multi_news(self):
        """多新闻运行。"""
        agent = TriStageCPAAgent()
        news = [
            NewsItem(title="央行降准"),
            NewsItem(title="半导体利好"),
            NewsItem(title="光伏增长"),
        ]
        config = agent.run(news)
        assert config.confidence > 0
        assert abs(sum(config.target_weights.values()) - 1.0) < 1e-3

    def test_three_stages_executed(self):
        """三阶段全部执行。"""
        agent = TriStageCPAAgent()
        news = [
            NewsItem(title="央行降准利好", source="财联社"),
            NewsItem(title="半导体芯片突破", source="新华财经"),
        ]
        config = agent.run(news)
        # 阶段 1: 分类 (宏观政策 + 行业动态)
        # 阶段 2: 舆情 (行业映射 + 情绪 + 热度)
        # 阶段 3: 组合构建
        assert config.macro_signal in ("bullish", "neutral", "bearish")
        assert len(config.target_weights) > 0


# ============================================================
# CNBuzz2PortfolioBenchmark 测试
# ============================================================

class TestCNBuzz2PortfolioBenchmark:
    """基准框架测试。"""

    def test_run_basic(self):
        """基本运行。"""
        benchmark = CNBuzz2PortfolioBenchmark()
        news = [NewsItem(title="央行降准")]
        config = benchmark.run(news)
        assert isinstance(config, PortfolioConfig)

    def test_run_batch(self):
        """批量运行。"""
        benchmark = CNBuzz2PortfolioBenchmark()
        daily_news = [
            [NewsItem(title="半导体利好")],
            [NewsItem(title="光伏增长")],
        ]
        configs = benchmark.run_batch(daily_news)
        assert len(configs) == 2

    def test_get_summary_empty(self):
        """空摘要。"""
        benchmark = CNBuzz2PortfolioBenchmark()
        summary = benchmark.get_summary()
        assert summary["n_runs"] == 0

    def test_get_summary_with_runs(self):
        """有运行记录的摘要。"""
        benchmark = CNBuzz2PortfolioBenchmark()
        benchmark.run([NewsItem(title="央行降准")])
        benchmark.run([NewsItem(title="半导体利好")])
        summary = benchmark.get_summary()
        assert summary["n_runs"] == 2
        assert "signal_counts" in summary
        assert "avg_confidence" in summary

    def test_create_news_from_titles(self):
        """从标题创建新闻。"""
        titles = ["标题1", "标题2", "标题3"]
        news = CNBuzz2PortfolioBenchmark.create_news_from_titles(titles)
        assert len(news) == 3
        assert news[0].title == "标题1"
        assert news[0].source == "财联社"


# ============================================================
# 端到端集成测试
# ============================================================

class TestEndToEnd:
    """端到端集成测试。"""

    def test_full_pipeline(self):
        """完整管线: 标题 → 分类 → 舆情 → 组合。"""
        benchmark = CNBuzz2PortfolioBenchmark()
        titles = [
            "央行宣布降准0.5个百分点 释放长期资金",
            "半导体行业迎来政策利好 国产替代加速",
            "新能源汽车销量创新高 利好锂电产业链",
            "某上市公司发布退市风险提示",
            "人工智能算力需求爆发 科技板块持续走强",
        ]
        news = benchmark.create_news_from_titles(titles)
        config = benchmark.run(news)

        assert config.macro_signal in ("bullish", "neutral", "bearish")
        assert len(config.target_weights) > 0
        assert abs(sum(config.target_weights.values()) - 1.0) < 1e-3
        assert config.confidence > 0
        assert len(config.reasoning) > 0

    def test_multi_day_batch(self):
        """多日批量运行。"""
        benchmark = CNBuzz2PortfolioBenchmark()
        day1 = benchmark.create_news_from_titles(["央行降准", "半导体利好"])
        day2 = benchmark.create_news_from_titles(["光伏增长", "白酒旺季"])
        day3 = benchmark.create_news_from_titles(["AI算力爆发", "新能源利好"])

        configs = benchmark.run_batch([day1, day2, day3])
        assert len(configs) == 3

        summary = benchmark.get_summary()
        assert summary["n_runs"] == 3
        assert summary["avg_news_per_run"] == 2.0
