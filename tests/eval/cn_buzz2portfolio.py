"""
CN-Buzz2Portfolio 中国市场基准
==============================

文献依据: #24 CN-Buzz2Portfolio (中国市场) (2026.03, ★★★★★)
论文核心: 日热点新闻 → 宏观/行业配置 → 投资组合构建

Tri-Stage CPA Agent (三阶段中国组合代理)
----------------------------------------
Stage 1: News Collection (新闻收集)
  - 收集 A 股市场日热点新闻
  - 分类: 宏观政策 / 行业动态 / 公司公告 / 市场情绪

Stage 2: Buzz Analysis (舆情分析)
  - 关键词匹配 → 行业映射
  - 情绪评分 (-1 到 +1)
  - 热度评分 (0 到 1)

Stage 3: Portfolio Construction (组合构建)
  - 宏观信号 → 大类资产配置
  - 行业舆情 → 行业权重调整
  - 输出: 目标持仓配置

使用示例
--------
    from tests.eval.cn_buzz2portfolio import CNBuzz2PortfolioBenchmark

    benchmark = CNBuzz2PortfolioBenchmark()
    result = benchmark.run(news_items, current_portfolio)
    print(result.target_weights)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("cn_buzz2portfolio")

# ============================================================
# 新闻分类枚举
# ============================================================

class NewsCategory(str, Enum):
    """新闻类别。"""
    MACRO_POLICY = "macro_policy"        # 宏观政策
    INDUSTRY_DYNAMICS = "industry"       # 行业动态
    COMPANY_ANNOUNCEMENT = "company"     # 公司公告
    MARKET_SENTIMENT = "sentiment"       # 市场情绪
    UNKNOWN = "unknown"


# ============================================================
# 行业映射 (A股主要行业)
# ============================================================

INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "高端制造": ["半导体", "芯片", "集成电路", "高端装备", "数控机床", "工业母机", "航空", "航天"],
    "新能源": ["光伏", "风电", "储能", "锂电", "新能源汽车", "充电桩", "氢能", "钠电"],
    "消费": ["白酒", "食品", "零售", "旅游", "餐饮", "家电", "服装", "免税"],
    "医药": ["创新药", "医疗器械", "疫苗", "中药", "CRO", "CDMO", "生物制药"],
    "金融": ["银行", "保险", "券商", "基金", "信托", "金融科技"],
    "科技": ["人工智能", "AI", "算力", "云计算", "大数据", "5G", "物联网", "信创"],
    "地产": ["房地产", "基建", "建材", "建筑"],
    "资源": ["黄金", "有色", "煤炭", "钢铁", "稀土", "石油", "化工"],
    "公用": ["电力", "水务", "环保", "燃气"],
}

# 宏观政策关键词
MACRO_KEYWORDS: list[str] = [
    "降准", "降息", "MLF", "LPR", "逆回购", "财政", "专项债", "减税",
    "两会", "五年规划", "十五五", "国常会", "发改委", "央行", "证监会",
    "注册制", "退市", "IPO", "再融资",
]


# ============================================================
# 新闻条目
# ============================================================

@dataclass
class NewsItem:
    """单条新闻条目。

    Attributes:
        title: 新闻标题
        content: 新闻内容 (可选)
        source: 来源 (如 "财联社", "新浪财经")
        timestamp: 时间戳
        category: 新闻类别 (自动分类或手动指定)
        industry: 相关行业 (自动映射或手动指定)
        sentiment: 情绪评分 (-1 到 +1, 正=利好)
        heat: 热度评分 (0 到 1)
    """
    title: str
    content: str = ""
    source: str = ""
    timestamp: str = ""
    category: NewsCategory = NewsCategory.UNKNOWN
    industry: str = ""
    sentiment: float = 0.0
    heat: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "timestamp": self.timestamp,
            "category": self.category.value,
            "industry": self.industry,
            "sentiment": self.sentiment,
            "heat": self.heat,
        }


# ============================================================
# 组合配置结果
# ============================================================

@dataclass
class PortfolioConfig:
    """投资组合配置结果。

    Attributes:
        target_weights: 目标行业权重 {行业: 权重}
        macro_signal: 宏观信号 (bullish/neutral/bearish)
        confidence: 配置置信度 (0-1)
        reasoning: 配置理由
    """
    target_weights: dict[str, float] = field(default_factory=dict)
    macro_signal: str = "neutral"
    confidence: float = 0.0
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_weights": dict(self.target_weights),
            "macro_signal": self.macro_signal,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


# ============================================================
# Stage 1: 新闻分类器
# ============================================================

class NewsClassifier:
    """Stage 1: 新闻收集与分类。

    基于关键词匹配将新闻分类为宏观政策/行业动态/公司公告/市场情绪。
    """

    def classify(self, news: NewsItem) -> NewsItem:
        """分类单条新闻。"""
        text = (news.title + " " + news.content).lower()

        if any(kw in text for kw in MACRO_KEYWORDS):
            news.category = NewsCategory.MACRO_POLICY
        elif any(
            kw in text
            for kws in INDUSTRY_KEYWORDS.values()
            for kw in kws
        ):
            news.category = NewsCategory.INDUSTRY_DYNAMICS
        elif any(kw in text for kw in ["公告", "财报", "业绩", "预告", "披露"]):
            news.category = NewsCategory.COMPANY_ANNOUNCEMENT
        elif any(kw in text for kw in ["情绪", "恐慌", "贪婪", "涨停", "跌停", "牛市", "熊市"]):
            news.category = NewsCategory.MARKET_SENTIMENT

        return news

    def classify_batch(self, news_items: list[NewsItem]) -> list[NewsItem]:
        """批量分类新闻。"""
        return [self.classify(n) for n in news_items]


# ============================================================
# Stage 2: 舆情分析器
# ============================================================

class BuzzAnalyzer:
    """Stage 2: 舆情分析。

    关键词匹配 → 行业映射 + 情绪评分 + 热度评分。
    """

    POSITIVE_KEYWORDS = ["利好", "增长", "突破", "创新高", "超预期", "加仓", "买入", "上调", "繁荣"]
    NEGATIVE_KEYWORDS = ["利空", "下降", "下滑", "亏损", "退市", "违规", "处罚", "下调", "危机", "暴跌"]

    def analyze(self, news: NewsItem) -> NewsItem:
        """分析单条新闻的舆情。"""
        text = news.title + " " + news.content

        news.industry = self._map_industry(text)
        news.sentiment = self._score_sentiment(text)
        news.heat = self._score_heat(text, news.source)

        return news

    def analyze_batch(self, news_items: list[NewsItem]) -> list[NewsItem]:
        """批量分析舆情。"""
        return [self.analyze(n) for n in news_items]

    def _map_industry(self, text: str) -> str:
        """将新闻映射到行业。"""
        for industry, keywords in INDUSTRY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return industry
        return ""

    def _score_sentiment(self, text: str) -> float:
        """情绪评分 (-1 到 +1)。"""
        pos_count = sum(1 for kw in self.POSITIVE_KEYWORDS if kw in text)
        neg_count = sum(1 for kw in self.NEGATIVE_KEYWORDS if kw in text)
        total = pos_count + neg_count
        if total == 0:
            return 0.0
        return round((pos_count - neg_count) / total, 2)

    def _score_heat(self, text: str, source: str) -> float:
        """热度评分 (0 到 1)。"""
        heat = min(1.0, len(text) / 200.0)
        if source in ("财联社", "新华财经", "央视财经"):
            heat = min(1.0, heat + 0.2)
        return round(heat, 2)

    def get_industry_buzz(self, news_items: list[NewsItem]) -> dict[str, dict[str, float]]:
        """获取各行业舆情汇总。

        Returns:
            {行业: {avg_sentiment, total_heat, count}}
        """
        industry_news: dict[str, list[NewsItem]] = defaultdict(list)
        for n in news_items:
            if n.industry:
                industry_news[n.industry].append(n)

        result: dict[str, dict[str, float]] = {}
        for industry, items in industry_news.items():
            sentiments = [i.sentiment for i in items]
            heats = [i.heat for i in items]
            result[industry] = {
                "avg_sentiment": round(sum(sentiments) / len(sentiments), 3),
                "total_heat": round(sum(heats), 3),
                "count": len(items),
            }
        return result


# ============================================================
# Stage 3: 组合构建器
# ============================================================

class PortfolioConstructor:
    """Stage 3: 投资组合构建。

    宏观信号 → 大类配置 + 行业舆情 → 行业权重。
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "高端制造": 0.20,
        "新能源": 0.12,
        "消费": 0.15,
        "医药": 0.13,
        "金融": 0.15,
        "科技": 0.10,
        "地产": 0.05,
        "资源": 0.07,
        "公用": 0.03,
    }

    def construct(self, news_items: list[NewsItem],
                  industry_buzz: dict[str, dict[str, float]],
                  current_weights: Optional[dict[str, float]] = None) -> PortfolioConfig:
        """构建投资组合配置。

        Args:
            news_items: 新闻列表
            industry_buzz: 行业舆情汇总
            current_weights: 当前持仓权重 (可选)

        Returns:
            PortfolioConfig 组合配置
        """
        macro_signal = self._assess_macro(news_items)
        weights = dict(self.DEFAULT_WEIGHTS)

        for industry, buzz in industry_buzz.items():
            if industry not in weights:
                weights[industry] = 0.02
            sentiment = buzz["avg_sentiment"]
            heat = min(1.0, buzz["total_heat"] / 5.0)
            adjustment = sentiment * heat * 0.1
            weights[industry] = max(0.0, weights[industry] * (1.0 + adjustment))

        total = sum(weights.values())
        if total > 0:
            weights = {k: round(v / total, 4) for k, v in weights.items()}

        confidence = self._compute_confidence(news_items, industry_buzz)
        reasoning = self._build_reasoning(macro_signal, industry_buzz)

        return PortfolioConfig(
            target_weights=weights,
            macro_signal=macro_signal,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _assess_macro(self, news_items: list[NewsItem]) -> str:
        """评估宏观信号。"""
        macro_news = [n for n in news_items if n.category == NewsCategory.MACRO_POLICY]
        if not macro_news:
            return "neutral"

        avg_sentiment = sum(n.sentiment for n in macro_news) / len(macro_news)
        if avg_sentiment > 0.2:
            return "bullish"
        if avg_sentiment < -0.2:
            return "bearish"
        return "neutral"

    def _compute_confidence(self, news_items: list[NewsItem],
                            industry_buzz: dict[str, dict[str, float]]) -> float:
        """计算配置置信度。"""
        n_news = len(news_items)
        n_industries = len(industry_buzz)
        if n_news == 0:
            return 0.0
        news_factor = min(1.0, n_news / 20.0)
        industry_factor = min(1.0, n_industries / 5.0)
        return round(0.5 * news_factor + 0.5 * industry_factor, 2)

    def _build_reasoning(self, macro_signal: str,
                         industry_buzz: dict[str, dict[str, float]]) -> str:
        """构建配置理由。"""
        reasons: list[str] = [f"宏观信号: {macro_signal}"]
        top_industries = sorted(
            industry_buzz.items(),
            key=lambda x: abs(x[1]["avg_sentiment"]) * x[1]["total_heat"],
            reverse=True,
        )[:3]
        for industry, buzz in top_industries:
            direction = "看多" if buzz["avg_sentiment"] > 0 else "看空"
            reasons.append(f"{industry}{direction}(情绪={buzz['avg_sentiment']:.2f})")
        return "; ".join(reasons)


# ============================================================
# Tri-Stage CPA Agent
# ============================================================

class TriStageCPAAgent:
    """Tri-Stage CPA Agent — 三阶段中国组合代理。

    Stage 1: NewsClassifier → 新闻收集与分类
    Stage 2: BuzzAnalyzer → 舆情分析
    Stage 3: PortfolioConstructor → 组合构建
    """

    def __init__(self) -> None:
        self.classifier = NewsClassifier()
        self.analyzer = BuzzAnalyzer()
        self.constructor = PortfolioConstructor()

    def run(self, news_items: list[NewsItem],
            current_weights: Optional[dict[str, float]] = None) -> PortfolioConfig:
        """运行三阶段 CPA 代理。

        Args:
            news_items: 原始新闻列表
            current_weights: 当前持仓权重 (可选)

        Returns:
            PortfolioConfig 目标组合配置
        """
        if not news_items:
            return PortfolioConfig(reasoning="无新闻输入")

        # Stage 1: 分类
        classified = self.classifier.classify_batch(news_items)
        logger.debug(f"Stage 1 完成: {len(classified)} 条新闻已分类")

        # Stage 2: 舆情分析
        analyzed = self.analyzer.analyze_batch(classified)
        industry_buzz = self.analyzer.get_industry_buzz(analyzed)
        logger.debug(f"Stage 2 完成: {len(industry_buzz)} 个行业舆情已汇总")

        # Stage 3: 组合构建
        config = self.constructor.construct(analyzed, industry_buzz, current_weights)
        logger.debug(f"Stage 3 完成: 宏观={config.macro_signal}, 置信度={config.confidence}")

        return config


# ============================================================
# CN-Buzz2Portfolio 基准
# ============================================================

class CNBuzz2PortfolioBenchmark:
    """CN-Buzz2Portfolio 中国市场基准测试框架。

    使用示例:
        benchmark = CNBuzz2PortfolioBenchmark()
        result = benchmark.run(news_items)
    """

    def __init__(self) -> None:
        self.agent = TriStageCPAAgent()
        self._results: list[dict[str, Any]] = []

    def run(self, news_items: list[NewsItem],
            current_weights: Optional[dict[str, float]] = None) -> PortfolioConfig:
        """运行基准测试。

        Args:
            news_items: 新闻列表
            current_weights: 当前持仓权重 (可选)

        Returns:
            PortfolioConfig 组合配置
        """
        config = self.agent.run(news_items, current_weights)

        self._results.append({
            "n_news": len(news_items),
            "macro_signal": config.macro_signal,
            "confidence": config.confidence,
            "n_industries": len(config.target_weights),
        })

        return config

    def run_batch(self, daily_news: list[list[NewsItem]]) -> list[PortfolioConfig]:
        """批量运行多日基准测试。

        Args:
            daily_news: 每日新闻列表的列表

        Returns:
            每日的组合配置列表
        """
        return [self.run(news) for news in daily_news]

    def get_summary(self) -> dict[str, Any]:
        """获取基准测试摘要。"""
        if not self._results:
            return {"n_runs": 0}

        signal_counts: dict[str, int] = defaultdict(int)
        total_confidence = 0.0
        total_news = 0
        total_industries = 0

        for r in self._results:
            signal_counts[r["macro_signal"]] += 1
            total_confidence += r["confidence"]
            total_news += r["n_news"]
            total_industries += r["n_industries"]

        n = len(self._results)
        return {
            "n_runs": n,
            "avg_confidence": round(total_confidence / n, 3),
            "avg_news_per_run": round(total_news / n, 1),
            "avg_industries_per_run": round(total_industries / n, 1),
            "signal_counts": dict(signal_counts),
        }

    @staticmethod
    def create_news_from_titles(titles: list[str], source: str = "财联社") -> list[NewsItem]:
        """从标题列表创建新闻条目 (便捷方法)。"""
        return [NewsItem(title=t, source=source) for t in titles]


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    """CLI 入口: 演示 CN-Buzz2Portfolio 基准。"""
    print("=" * 60)
    print("CN-Buzz2Portfolio 中国市场基准")
    print("文献: #24 CN-Buzz2Portfolio (2026.03)")
    print("=" * 60)

    benchmark = CNBuzz2PortfolioBenchmark()

    titles = [
        "央行宣布降准0.5个百分点 释放长期资金",
        "半导体行业迎来政策利好 国产替代加速",
        "新能源汽车销量创新高 利好锂电产业链",
        "某上市公司发布退市风险提示",
        "人工智能算力需求爆发 科技板块持续走强",
        "白酒行业进入消费旺季 茅台五粮液领涨",
        "发改委发布十五五规划草案 聚焦高端制造",
    ]
    news = benchmark.create_news_from_titles(titles)

    print(f"\n--- 输入: {len(news)} 条新闻 ---")
    for n in news:
        print(f"  • {n.title}")

    config = benchmark.run(news)

    print("\n--- Tri-Stage CPA Agent 结果 ---")
    print(f"  宏观信号: {config.macro_signal}")
    print(f"  置信度: {config.confidence}")
    print(f"  理由: {config.reasoning}")
    print("\n  目标行业权重:")
    for industry, weight in sorted(config.target_weights.items(), key=lambda x: -x[1]):
        if weight > 0.01:
            print(f"    {industry}: {weight:.2%}")

    summary = benchmark.get_summary()
    print("\n--- 基准摘要 ---")
    print(f"  运行次数: {summary['n_runs']}")
    print(f"  平均置信度: {summary['avg_confidence']}")
    print(f"  信号分布: {summary['signal_counts']}")


if __name__ == "__main__":
    main()
