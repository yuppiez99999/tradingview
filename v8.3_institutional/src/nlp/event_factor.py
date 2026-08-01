# -*- coding: utf-8 -*-
"""
事件驱动因子生成器
从 YiZhao-FinDataSet 金融语料中提取事件信号, 转化为可量化的交易因子

因子类型:
  1. 舆情情绪因子 (Sentiment Factor)
  2. 事件冲击因子 (Event Impact Factor)
  3. 文本热度因子 (Text Heat Factor)
  4. 行业联动因子 (Industry Correlation Factor)
"""

import os
import sys
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# [V75] yizhao数据桥接
try:
    from ..bridges.yizhao_data import YiZhaoDataLoader, YiZhaoDocument
except ImportError:
    YiZhaoDataLoader = None
    YiZhaoDocument = None
from .sentiment import FinSentimentAnalyzer, get_sentiment_analyzer, get_yizhao_loader


class EventDrivenFactor:
    """事件驱动因子生成器"""

    # 因子权重 (可优化)
    DEFAULT_WEIGHTS = {
        "sentiment": 0.35,  # 舆情情绪
        "event_impact": 0.25,  # 事件冲击
        "text_heat": 0.15,  # 文本热度
        "industry_corr": 0.15,  # 行业联动
        "policy_bias": 0.10,  # 政策倾向
    }

    def __init__(
        self,
        loader: YiZhaoDataLoader = None,
        analyzer: FinSentimentAnalyzer = None,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.loader = loader or get_yizhao_loader()
        self.analyzer = analyzer or get_sentiment_analyzer()
        self.weights = weights or self.DEFAULT_WEIGHTS

        # 因子缓存
        self._factor_cache: Dict[str, Dict] = {}
        self._factor_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=60))

    # ---- 因子1: 舆情情绪因子 ----
    def sentiment_factor(self, code: str) -> float:
        """
        舆情情绪因子
        范围: [-1, 1], 正值表示乐观, 负值表示悲观
        """
        if not self.loader.is_loaded:
            return 0.0

        sentiment = self.analyzer.analyze_code_sentiment(code, top_k=20)
        if "error" in sentiment:
            return 0.0

        score = sentiment.get("sentiment_score", 0.5)
        # 映射 [0,1] → [-1,1]
        factor = (score - 0.5) * 2.0

        # 考虑情绪一致性 (标准差小 = 一致性强)
        std = sentiment.get("sentiment_std", 0.2)
        confidence = max(0, 1 - std * 2)
        factor *= confidence

        return round(factor, 4)

    # ---- 因子2: 事件冲击因子 ----
    def event_impact_factor(self, code: str) -> float:
        """
        事件冲击因子
        正冲击(政策利好/业绩超预期): +值
        负冲击(监管处罚/风险事件): -值
        """
        if not self.loader.is_loaded:
            return 0.0

        results = self.loader.search_by_code(code, top_k=20)
        if not results:
            return 0.0

        impact = 0.0
        for r in results:
            text = r.doc.text[:3000]
            events = self.analyzer.classify_event(text)

            # 各类事件的冲击权重
            event_weights = {
                "政策": 0.6,
                "业绩": 0.5,
                "并购重组": 0.4,
                "技术创新": 0.3,
                "融资": 0.2,
                "市场行情": 0.1,
                "监管": -0.5,
                "风险事件": -0.7,
            }

            sentiment = self.analyzer.analyze_text(text)
            polarity_mult = 1.0 if sentiment["score"] >= 0.5 else -1.0

            for evt in events:
                w = event_weights.get(evt.value, 0.1)
                impact += w * polarity_mult * r.score * 0.05

        # 归一化到 [-1, 1]
        impact = np.clip(impact / max(len(results), 1), -1, 1)
        return round(float(impact), 4)

    # ---- 因子3: 文本热度因子 ----
    def text_heat_factor(self, code: str) -> float:
        """
        文本热度因子
        衡量标的在金融语料中的讨论热度
        热度极高 (>0.8): 可能过热, 需要警惕
        热度极低 (<0.2): 缺乏关注, 可能被低估
        """
        if not self.loader.is_loaded:
            return 0.5  # 中性

        results = self.loader.search_by_code(code, top_k=50)
        if not results:
            return 0.1

        # 热度 = 文档数量 * 平均金融相关性
        doc_count = len(results)
        avg_fin_score = sum(r.doc.fin_int_score for r in results) / doc_count
        avg_score = sum(r.score for r in results) / doc_count

        # 综合热度 (对数压缩避免极端值)
        raw_heat = np.log1p(doc_count) * avg_fin_score / 5.0 * avg_score / 10.0
        heat = np.clip(raw_heat, 0, 1)

        return round(float(heat), 4)

    # ---- 因子4: 行业联动因子 ----
    def industry_correlation_factor(self, code: str) -> float:
        """
        行业联动因子
        检测标的所在行业的整体情绪热度
        """
        if not self.loader.is_loaded:
            return 0.5

        # 获取标的所属行业
        results = self.loader.search_by_code(code, top_k=5)
        if not results:
            return 0.5

        industries = defaultdict(int)
        for r in results:
            inds = self.analyzer.classify_industry(r.doc.text[:2000])
            for ind in inds:
                industries[ind] += 1

        if not industries:
            return 0.5

        # 主要行业
        main_industry = max(industries, key=industries.get)

        # 搜索同行业关键词
        from .sentiment import INDUSTRY_KEYWORDS

        industry_kw = INDUSTRY_KEYWORDS.get(main_industry, [main_industry])
        industry_results = self.loader.search_by_keywords(industry_kw, top_k=30)

        if not industry_results:
            return 0.5

        # 计算行业平均情绪
        scores = []
        for r in industry_results:
            s = self.analyzer.analyze_text(r.doc.text[:2000])
            scores.append(s["score"])

        avg_industry_sentiment = sum(scores) / len(scores)
        return round(avg_industry_sentiment, 4)

    # ---- 因子5: 政策倾向因子 ----
    def policy_bias_factor(self, code: str) -> float:
        """
        政策倾向因子
        评估标的受益于当前政策方向的程度
        """
        keywords = self.loader.config.portfolio_keywords.get(code, [])
        if not keywords or not self.loader.is_loaded:
            return 0.0

        # 搜索政策相关文档
        policy_kw = ["政策", "规划", "国务院", "发改委", "工信部", "支持", "补贴"]
        all_kw = keywords[:3] + policy_kw

        results = self.loader.search_by_keywords(all_kw, top_k=15, min_fin_score=4)
        if not results:
            return 0.0

        positive_count = 0
        for r in results:
            s = self.analyzer.analyze_text(r.doc.text[:2000])
            if s["polarity"] in ("positive", "strong_positive"):
                positive_count += 1

        bias = positive_count / len(results) if results else 0.0
        return round(bias, 4)

    # ---- 综合因子 ----
    def compute_composite_factor(self, code: str) -> Dict:
        """
        计算综合事件驱动因子
        返回: {'composite': float, 'factors': Dict, 'signal': str}
        """
        factors = {
            "sentiment": self.sentiment_factor(code),
            "event_impact": self.event_impact_factor(code),
            "text_heat": self.text_heat_factor(code),
            "industry_corr": self.industry_correlation_factor(code),
            "policy_bias": self.policy_bias_factor(code),
        }

        # 加权合成
        composite = sum(factors[name] * self.weights.get(name, 0.2) for name in factors)

        # 信号生成
        if composite >= 0.3:
            signal = "strong_buy"
        elif composite >= 0.1:
            signal = "buy"
        elif composite <= -0.3:
            signal = "strong_sell"
        elif composite <= -0.1:
            signal = "sell"
        else:
            signal = "hold"

        result = {
            "code": code,
            "composite": round(composite, 4),
            "factors": {k: round(v, 4) for k, v in factors.items()},
            "signal": signal,
            "timestamp": datetime.now().isoformat(),
        }

        # 缓存
        self._factor_cache[code] = result
        self._factor_history[code].append(result)

        return result

    def compute_portfolio_factors(self, codes: Optional[List[str]] = None) -> Dict:
        """计算组合级别因子"""
        if codes is None:
            codes = list(self.loader.config.portfolio_keywords.keys())

        results = {}
        all_composites = []

        for code in codes:
            r = self.compute_composite_factor(code)
            results[code] = r
            all_composites.append(r["composite"])

        avg_composite = np.mean(all_composites) if all_composites else 0
        std_composite = np.std(all_composites) if all_composites else 0

        # 信号分布
        signals = defaultdict(int)
        for r in results.values():
            signals[r["signal"]] += 1

        return {
            "portfolio_composite": round(float(avg_composite), 4),
            "composite_std": round(float(std_composite), 4),
            "signal_distribution": dict(signals),
            "code_factors": results,
            "dominant_signal": max(signals, key=signals.get) if signals else "hold",
        }

    def get_factor_trend(self, code: str, window: int = 10) -> Dict:
        """获取因子趋势 (用于回测)"""
        history = list(self._factor_history.get(code, []))[-window:]
        if not history:
            return {"trend": "flat", "change": 0.0}

        composites = [h["composite"] for h in history]
        change = composites[-1] - composites[0]

        if change > 0.1:
            trend = "improving"
        elif change < -0.1:
            trend = "deteriorating"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "change": round(change, 4),
            "current": composites[-1],
            "window_avg": round(np.mean(composites), 4),
        }


# ============================================================
# 因子回测验证器
# ============================================================
class FactorBacktestValidator:
    """验证事件驱动因子的预测能力"""

    def __init__(self, factor_gen: EventDrivenFactor = None):
        self.factor_gen = factor_gen or EventDrivenFactor()

    def compute_ic(self, factor_values: List[float], future_returns: List[float]) -> Dict:
        """
        计算信息系数 (Information Coefficient)
        IC = corr(factor, future_return)
        """
        if len(factor_values) < 10:
            return {"ic": 0, "error": "数据不足"}

        ic = np.corrcoef(factor_values, future_returns)[0, 1]
        ic = 0 if np.isnan(ic) else ic

        # IC 显著性 (t-test)
        n = len(factor_values)
        t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else 0

        return {
            "ic": round(float(ic), 4),
            "ic_abs": round(abs(float(ic)), 4),
            "t_stat": round(float(t_stat), 4),
            "n_samples": n,
            "significant": abs(float(t_stat)) > 2.0,
        }

    def compute_quantile_returns(
        self, factor_values: List[float], future_returns: List[float], n_quantiles: int = 5
    ) -> Dict:
        """分层回测: 按因子值分5组, 比较各组未来收益"""
        if len(factor_values) < n_quantiles * 3:
            return {"error": "数据不足"}

        arr = np.array(list(zip(factor_values, future_returns)))
        arr = arr[np.argsort(arr[:, 0])]

        chunk_size = len(arr) // n_quantiles
        quantile_returns = []

        for i in range(n_quantiles):
            start = i * chunk_size
            end = start + chunk_size if i < n_quantiles - 1 else len(arr)
            avg_return = arr[start:end, 1].mean()
            quantile_returns.append(round(float(avg_return), 6))

        # 多空收益差 (Q5 - Q1)
        spread = quantile_returns[-1] - quantile_returns[0]

        return {
            "quantile_returns": quantile_returns,
            "top_bottom_spread": round(float(spread), 6),
            "monotonic": all(quantile_returns[i] <= quantile_returns[i + 1] for i in range(n_quantiles - 1))
            or all(quantile_returns[i] >= quantile_returns[i + 1] for i in range(n_quantiles - 1)),
        }


# ============================================================
# 便捷函数
# ============================================================
_global_factor_gen: Optional[EventDrivenFactor] = None


def get_factor_generator() -> EventDrivenFactor:
    global _global_factor_gen
    if _global_factor_gen is None:
        _global_factor_gen = EventDrivenFactor()
    return _global_factor_gen


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    gen = get_factor_generator()

    print("=" * 60)
    print("  事件驱动因子测试")
    print("=" * 60)

    test_codes = ["601088", "300274", "002371", "600276"]

    for code in test_codes:
        result = gen.compute_composite_factor(code)
        print(f"\n  {code}:")
        print(f"    综合因子: {result['composite']:.4f}")
        print(f"    信号: {result['signal']}")
        for name, val in result["factors"].items():
            bar = "█" * int(abs(val) * 20)
            sign = "+" if val > 0 else " "
            print(f"    {name:>15}: {sign}{val:.4f} {bar}")

    print(f"\n{'=' * 60}")
    print("  组合级别因子")
    print("=" * 60)
    portfolio = gen.compute_portfolio_factors()
    print(f"  组合综合因子: {portfolio['portfolio_composite']:.4f}")
    print(f"  因子标准差: {portfolio['composite_std']:.4f}")
    print(f"  主导信号: {portfolio['dominant_signal']}")
    print(f"  信号分布: {portfolio['signal_distribution']}")
