"""新闻情感分析引擎 v1.0

模仿 Renaissance/JPMorgan NLP 团队的新闻情感分析系统

核心能力:
1. 多源新闻聚合 — 财经媒体/公告/研报/社交媒体
2. 情感打分 — FinBERT 风格的情感分类 (利好/利空/中性)
3. 事件抽取 — 并购/财报/监管/人事/产品 等 8 类事件
4. 影响传播 — 通过供应链/同业关系传播到相关标的
5. 时序衰减 — 旧新闻影响衰减 (半衰期 24h)
6. 异常检测 — 突发新闻/情感突变预警

参考:
- FinBERT (Yang et al. 2020)
- BloombergGPT (Wu et al. 2023)
- Lopez-Lira & Tang (2023) "Can ChatGPT Forecast Stock Price Movements?"

A股适配:
- 数据源: Wind 公告/财经新闻/研报 + iFinD 资讯 + 东方财富/同花顺
- 事件类型: 业绩预告/重大资产重组/股权激励/股东减持/监管问询/董监高变动
- 情感词典: 中文金融情感词典 (扩展 Loughran-McDonald)
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class NewsItem:
    """新闻条目"""
    news_id: str                       # 唯一ID
    title: str                         # 标题
    content: str = ""                  # 正文
    source: str = ""                   # 来源 (Wind/iFinD/Reuters/...)
    publish_time: Optional[datetime] = None
    # 关联标的 (主要)
    symbols: List[str] = field(default_factory=list)
    # 关联行业
    industries: List[str] = field(default_factory=list)
    # 情感分析结果 (由引擎填充)
    sentiment_score: float = 0.0       # [-1, 1] 负向到正向
    sentiment_label: str = "NEUTRAL"   # POSITIVE / NEGATIVE / NEUTRAL
    confidence: float = 0.0            # [0, 1]
    # 事件抽取
    event_type: str = ""               # EARNINGS/M&A/REGULATION/...
    event_entities: List[str] = field(default_factory=list)
    # 影响评估
    impact_score: float = 0.0          # [0, 1] 影响强度
    impact_horizon_hours: int = 24     # 影响时长 (小时)


@dataclass
class SentimentSignal:
    """情感信号"""
    symbol: str
    # 综合情感分
    composite_sentiment: float         # [-1, 1]
    # 新闻数量
    news_count_24h: int
    news_count_7d: int
    # 加权情感 (按时序衰减)
    weighted_sentiment: float
    # 突发新闻标记
    breaking_news: bool
    # 事件类型分布
    event_distribution: Dict[str, int]
    # 影响传播 (来自供应链/同业)
    propagated_sentiment: float
    # 综合信号强度 [-1, 1]
    signal_strength: float
    # 置信度 [0, 1]
    confidence: float
    # 关键新闻 (top 3)
    top_news: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SentimentResult:
    """情感分析结果"""
    signals: Dict[str, SentimentSignal] = field(default_factory=dict)
    # 全局情感 (市场情绪)
    market_sentiment: float = 0.0
    # 异常新闻 (突发/极端情感)
    anomalies: List[NewsItem] = field(default_factory=list)
    # 热门事件
    hot_events: List[Tuple[str, int]] = field(default_factory=list)  # (event_type, count)
    # 元数据
    total_news_processed: int = 0
    processing_time_ms: float = 0.0


# ============================================================
# 中文金融情感词典 (简化版)
# ============================================================

POSITIVE_WORDS = {
    # 业绩
    "增长", "提升", "改善", "盈利", "超预期", "创历史新高", "突破", "大涨",
    "业绩大增", "营收增长", "净利增长", "毛利率提升", "ROE提升",
    # 利好事件
    "收购", "并购", "重组", "注入", "增持", "回购", "股权激励",
    "中标", "签约", "订单", "合作", "战略协议", "独家供应",
    # 监管/政策
    "获批", "通过", "许可", "补贴", "扶持", "鼓励", "减税",
    # 其他
    "突破", "创新高", "龙头", "稀缺", "垄断", "护城河", "技术领先",
}

NEGATIVE_WORDS = {
    # 业绩
    "下降", "下滑", "亏损", "爆雷", "不及预期", "萎缩", "暴跌", "重挫",
    "业绩大降", "营收下滑", "净利下滑", "毛利率下降", "ROE下降",
    # 利空事件
    "减持", "质押", "爆仓", "违约", "诉讼", "仲裁", "处罚", "警示",
    "退市", "ST", "风险警示", "停牌核查",
    # 监管/政策
    "问询", "调查", "立案", "处罚", "限制", "禁止", "加税",
    # 其他
    "破发", "破净", "债务危机", "资金链断裂", "经营困难", "高管辞职",
}

# 事件关键词 (用于事件抽取)
EVENT_KEYWORDS: Dict[str, List[str]] = {
    "EARNINGS": ["业绩预告", "财报", "季报", "年报", "半年报", "营收", "净利", "EPS"],
    "M&A": ["收购", "并购", "重组", "资产注入", "借壳", "合并"],
    "REGULATION": ["问询", "监管", "处罚", "立案", "警示", "停牌"],
    "EQUITY_INCENTIVE": ["股权激励", "员工持股", "限制性股票", "期权"],
    "SHAREHOLDER": ["减持", "增持", "回购", "质押", "解禁"],
    "PRODUCT": ["新品", "发布", "量产", "上市", "突破", "研发"],
    "MANAGEMENT": ["辞职", "聘任", "离任", "变动", "人事"],
    "PARTNERSHIP": ["合作", "战略协议", "签约", "中标", "订单"],
}


# ============================================================
# 新闻情感分析引擎
# ============================================================

class NewsSentimentEngine:
    """新闻情感分析引擎

    用法:
        engine = NewsSentimentEngine()
        engine.add_news(NewsItem(news_id="1", title="某公司业绩大增", symbols=["600519"]))
        result = engine.analyze(symbols=["600519"])
        sig = result.signals["600519"]
        print(sig.composite_sentiment, sig.signal_strength)
    """

    def __init__(
        self,
        # 情感词典 (可自定义)
        positive_words: Optional[set] = None,
        negative_words: Optional[set] = None,
        # 衰减参数
        half_life_hours: float = 24.0,      # 半衰期
        max_history_days: int = 7,         # 最大历史
        # 突发新闻阈值
        breaking_news_threshold: float = 0.7,   # |sentiment| > 此值视为突发
        # 传播权重
        propagate_weight: float = 0.3,          # 供应链/同业传播权重
        # 信号融合
        w_direct: float = 0.7,                  # 直接情感权重
        w_propagated: float = 0.3,              # 传播情感权重
    ):
        self.positive_words = positive_words if positive_words is not None else POSITIVE_WORDS
        self.negative_words = negative_words if negative_words is not None else NEGATIVE_WORDS
        self.half_life_hours = float(half_life_hours)
        self.max_history_days = int(max_history_days)
        self.breaking_threshold = float(breaking_news_threshold)
        self.propagate_weight = float(propagate_weight)
        self.w_direct = float(w_direct)
        self.w_propagated = float(w_propagated)

        # 新闻存储 (按 symbol 索引)
        self.news_store: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))

    # ------------------------------------------------------------
    # 新闻管理
    # ------------------------------------------------------------

    def add_news(self, news: NewsItem) -> None:
        """添加新闻"""
        if not news.publish_time:
            news.publish_time = datetime.now()
        # 单条新闻分析
        self._analyze_single(news)
        # 存储到相关 symbol
        for sym in news.symbols:
            self.news_store[sym].append(news)
        logger.debug(
            "[NewsSentiment] 添加新闻 %s: symbols=%s, sentiment=%.2f, event=%s",
            news.news_id, news.symbols, news.sentiment_score, news.event_type,
        )

    def add_news_batch(self, news_list: List[NewsItem]) -> int:
        """批量添加新闻"""
        count = 0
        for news in news_list:
            try:
                self.add_news(news)
                count += 1
            except Exception as exc:
                logger.warning("[NewsSentiment] 添加新闻失败 %s: %s", news.news_id, exc)
        return count

    # ------------------------------------------------------------
    # 单条新闻分析
    # ------------------------------------------------------------

    def _analyze_single(self, news: NewsItem) -> None:
        """分析单条新闻: 情感打分 + 事件抽取 + 影响评估"""
        text = (news.title + " " + news.content).lower()

        # 1) 情感打分 (基于词典)
        pos_count = sum(1 for w in self.positive_words if w in text)
        neg_count = sum(1 for w in self.negative_words if w in text)
        total = pos_count + neg_count
        if total > 0:
            news.sentiment_score = (pos_count - neg_count) / total
        else:
            news.sentiment_score = 0.0

        # 置信度: 命中词越多置信度越高
        news.confidence = min(total / 5.0, 1.0)

        # 标签
        if news.sentiment_score > 0.2:
            news.sentiment_label = "POSITIVE"
        elif news.sentiment_score < -0.2:
            news.sentiment_label = "NEGATIVE"
        else:
            news.sentiment_label = "NEUTRAL"

        # 2) 事件抽取
        for event_type, keywords in EVENT_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                news.event_type = event_type
                break

        # 3) 影响评估
        # 影响强度 = 置信度 × |情感| × 事件权重
        event_weights = {
            "EARNINGS": 1.2, "M&A": 1.5, "REGULATION": 1.3,
            "EQUITY_INCENTIVE": 0.8, "SHAREHOLDER": 1.0,
            "PRODUCT": 0.9, "MANAGEMENT": 0.7, "PARTNERSHIP": 0.8,
        }
        event_w = event_weights.get(news.event_type, 1.0)
        news.impact_score = min(news.confidence * abs(news.sentiment_score) * event_w, 1.0)

        # 影响时长 (事件类型决定)
        horizon_map = {
            "EARNINGS": 72, "M&A": 168, "REGULATION": 48,
            "SHAREHOLDER": 24, "PRODUCT": 12,
        }
        news.impact_horizon_hours = horizon_map.get(news.event_type, 24)

    # ------------------------------------------------------------
    # 综合分析
    # ------------------------------------------------------------

    def analyze(
        self,
        symbols: List[str],
        supply_chain_map: Optional[Dict[str, List[str]]] = None,
    ) -> SentimentResult:
        """分析多标的综合情感信号

        Args:
            symbols: 待分析标的列表
            supply_chain_map: 供应链关系 {symbol: [related_symbol, ...]}

        Returns:
            SentimentResult
        """
        import time
        start_time = time.time()
        result = SentimentResult()
        result.total_news_processed = sum(len(self.news_store.get(s, [])) for s in symbols)

        cutoff = datetime.now() - timedelta(days=self.max_history_days)
        all_news_count: Dict[str, int] = defaultdict(int)
        all_sentiments: List[float] = []

        for sym in symbols:
            news_list = list(self.news_store.get(sym, []))
            if not news_list:
                continue

            # 过滤过期新闻
            recent_news = [n for n in news_list if n.publish_time and n.publish_time > cutoff]
            if not recent_news:
                continue

            # 24h / 7d 计数
            cutoff_24h = datetime.now() - timedelta(hours=24)
            news_24h = [n for n in recent_news if n.publish_time > cutoff_24h]
            news_count_24h = len(news_24h)
            news_count_7d = len(recent_news)

            # 时序加权情感
            weighted_sentiment = self._calc_weighted_sentiment(recent_news)

            # 综合情感 (简单平均)
            sentiments = [n.sentiment_score for n in recent_news]
            composite = sum(sentiments) / len(sentiments) if sentiments else 0.0
            all_sentiments.append(composite)

            # 突发新闻
            breaking = any(
                abs(n.sentiment_score) > self.breaking_threshold
                for n in news_24h
            )

            # 事件分布
            event_dist: Dict[str, int] = defaultdict(int)
            for n in recent_news:
                if n.event_type:
                    event_dist[n.event_type] += 1
                    all_news_count[n.event_type] += 1

            # 传播情感 (从供应链/同业)
            propagated = 0.0
            if supply_chain_map:
                related = supply_chain_map.get(sym, [])
                related_sentiments = []
                for r in related:
                    if r in symbols:
                        r_news = list(self.news_store.get(r, []))
                        if r_news:
                            r_sentiments = [n.sentiment_score for n in r_news]
                            related_sentiments.append(sum(r_sentiments) / len(r_sentiments))
                if related_sentiments:
                    propagated = sum(related_sentiments) / len(related_sentiments)

            # 综合信号强度
            signal = self.w_direct * composite + self.w_propagated * propagated
            signal = max(-1.0, min(1.0, signal))

            # 置信度
            confidence = min(news_count_24h / 5.0, 1.0)

            # Top 3 新闻
            top_news = sorted(recent_news, key=lambda n: abs(n.sentiment_score), reverse=True)[:3]
            top_news_dicts = [
                {
                    "title": n.title,
                    "sentiment": n.sentiment_score,
                    "event_type": n.event_type,
                    "publish_time": n.publish_time.isoformat() if n.publish_time else "",
                }
                for n in top_news
            ]

            result.signals[sym] = SentimentSignal(
                symbol=sym,
                composite_sentiment=composite,
                news_count_24h=news_count_24h,
                news_count_7d=news_count_7d,
                weighted_sentiment=weighted_sentiment,
                breaking_news=breaking,
                event_distribution=dict(event_dist),
                propagated_sentiment=propagated,
                signal_strength=signal,
                confidence=confidence,
                top_news=top_news_dicts,
            )

        # 全局情感
        result.market_sentiment = sum(all_sentiments) / len(all_sentiments) if all_sentiments else 0.0

        # 异常新闻
        for sym in symbols:
            for n in self.news_store.get(sym, []):
                if abs(n.sentiment_score) > self.breaking_threshold and n.publish_time and n.publish_time > cutoff:
                    if n not in result.anomalies:
                        result.anomalies.append(n)

        # 热门事件
        result.hot_events = sorted(all_news_count.items(), key=lambda x: -x[1])[:5]

        result.processing_time_ms = (time.time() - start_time) * 1000
        return result

    # ------------------------------------------------------------
    # 时序加权
    # ------------------------------------------------------------

    def _calc_weighted_sentiment(self, news_list: List[NewsItem]) -> float:
        """时序加权情感 (半衰期衰减)"""
        now = datetime.now()
        weighted_sum = 0.0
        total_weight = 0.0
        for n in news_list:
            if not n.publish_time:
                continue
            hours_ago = (now - n.publish_time).total_seconds() / 3600
            # 超过影响时长的跳过
            if hours_ago > n.impact_horizon_hours:
                continue
            # 半衰期衰减
            decay = 0.5 ** (hours_ago / self.half_life_hours)
            weight = decay * n.confidence
            weighted_sum += n.sentiment_score * weight
            total_weight += weight
        return weighted_sum / total_weight if total_weight > 0 else 0.0

    # ------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------

    def get_signal(self, symbol: str, supply_chain_map: Optional[Dict[str, List[str]]] = None) -> Optional[SentimentSignal]:
        """获取单标的情感信号"""
        result = self.analyze([symbol], supply_chain_map)
        return result.signals.get(symbol)

    def clear_old_news(self, days: Optional[int] = None) -> int:
        """清理过期新闻"""
        days = days or self.max_history_days
        cutoff = datetime.now() - timedelta(days=days)
        cleared = 0
        for sym in list(self.news_store.keys()):
            original = len(self.news_store[sym])
            self.news_store[sym] = deque(
                (n for n in self.news_store[sym] if n.publish_time and n.publish_time > cutoff),
                maxlen=500,
            )
            cleared += original - len(self.news_store[sym])
        return cleared

    def summarize(self, result: SentimentResult) -> Dict[str, Any]:
        """生成摘要"""
        return {
            "total_signals": len(result.signals),
            "market_sentiment": result.market_sentiment,
            "anomalies_count": len(result.anomalies),
            "hot_events": result.hot_events,
            "total_news_processed": result.total_news_processed,
            "processing_time_ms": result.processing_time_ms,
            "top_positive": sorted(
                [(s, r.signal_strength) for s, r in result.signals.items()],
                key=lambda x: -x[1],
            )[:5],
            "top_negative": sorted(
                [(s, r.signal_strength) for s, r in result.signals.items()],
                key=lambda x: x[1],
            )[:5],
        }
