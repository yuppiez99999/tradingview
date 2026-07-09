# -*- coding: utf-8 -*-
"""
多因子选股模型 — 五维因子评估框架

来源：整合自 E:\各种PY程序\11_量化策略\five_factor_model.py + event_driven_factor.py

五维因子体系：
1. 价值因子 (Value) — 估值水平、股息率
2. 质量因子 (Quality) — 盈利能力、财务健康
3. 动量因子 (Momentum) — 价格趋势、相对强度
4. 增长因子 (Growth) — 收入/利润增长
5. 安全因子 (Safety) — 风险控制、稳定性

+ 事件驱动因子（来自事件驱动模块）：
6. 舆情情绪因子 (Sentiment)
7. 事件冲击因子 (Event Impact)

使用方式：
  from utils.factor_model import FactorModel
  
  model = FactorModel()
  scores = model.evaluate(klines_data)
  signal = model.generate_signal(scores)
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging

try:
    from utils.gtja191_factors import GTJA191Factors
    _HAS_GTJA191 = True
except ImportError:
    GTJA191Factors = None
    _HAS_GTJA191 = False

logger = logging.getLogger('factor_model')


@dataclass
class FactorResult:
    """单只标的因子评估结果"""
    code: str
    composite: float              # 综合得分 [-1, 1]
    factors: Dict[str, float]     # 各因子得分
    signal: str                   # strong_buy/buy/hold/sell/strong_sell
    rank: int = 0                 # 排名


class FactorModel:
    """五维因子选股模型"""

    # 因子权重
    DEFAULT_WEIGHTS = {
        'value': 0.18,
        'quality': 0.18,
        'momentum': 0.17,
        'growth': 0.14,
        'safety': 0.13,
        'sentiment': 0.04,      # 可选：事件驱动
        'event_impact': 0.04,   # 可选：事件驱动
        'technical_alpha': 0.12,  # GTJA191 Alpha144 等短周期量价因子
    }

    # 信号阈值
    SIGNAL_THRESHOLDS = {
        'strong_buy': 0.30,
        'buy': 0.10,
        'hold_lower': -0.10,
        'sell': -0.30,
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None,
                 lookback: int = 252):
        """
        Args:
            weights: 因子权重，默认使用 DEFAULT_WEIGHTS
            lookback: 回看窗口（交易日），默认 252（约1年）
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.lookback = lookback

    # ============================================================
    # 因子1: 价值因子
    # ============================================================
    def value_factor(self, df: pd.DataFrame,
                     pe: Optional[float] = None,
                     pb: Optional[float] = None,
                     dividend_yield: Optional[float] = None) -> float:
        """
        价值因子：低估值 + 高股息 = 高分。

        得分范围 [-1, 1]，正值表示低估（有吸引力）。
        """
        score = 0.0

        # PE 维度
        if pe is not None and pe > 0:
            # PE < 15 高分，PE > 50 低分
            pe_score = max(-1, min(1, (15 - pe) / 30))
            score += pe_score * 0.5

        # PB 维度
        if pb is not None and pb > 0:
            pb_score = max(-1, min(1, (2 - pb) / 2))
            score += pb_score * 0.3

        # 股息率维度
        if dividend_yield is not None and dividend_yield >= 0:
            dy_score = max(-1, min(1, (dividend_yield - 0.01) / 0.04))
            score += dy_score * 0.2

        return round(score, 4)

    # ============================================================
    # 因子2: 质量因子
    # ============================================================
    def quality_factor(self, df: pd.DataFrame,
                       roe: Optional[float] = None,
                       debt_ratio: Optional[float] = None,
                       profit_margin: Optional[float] = None) -> float:
        """
        质量因子：高ROE + 低负债 + 高利润率 = 高分。
        """
        score = 0.0

        if roe is not None:
            roe_score = max(-1, min(1, (roe - 0.08) / 0.12))
            score += roe_score * 0.4

        if debt_ratio is not None:
            debt_score = max(-1, min(1, (0.5 - debt_ratio) / 0.5))
            score += debt_score * 0.3

        if profit_margin is not None:
            margin_score = max(-1, min(1, (profit_margin - 0.05) / 0.15))
            score += margin_score * 0.3

        return round(score, 4)

    # ============================================================
    # 因子3: 动量因子
    # ============================================================
    def momentum_factor(self, df: pd.DataFrame) -> float:
        """
        动量因子：基于多周期收益率。

        正值表示正向动量。
        """
        if df.empty or 'close' not in df.columns:
            return 0.0

        prices = df['close'].values
        if len(prices) < 60:
            return 0.0

        # 多周期动量
        mom_1m = (prices[-1] / prices[-min(20, len(prices))] - 1) if len(prices) >= 20 else 0
        mom_3m = (prices[-1] / prices[-min(60, len(prices))] - 1) if len(prices) >= 60 else 0
        mom_6m = (prices[-1] / prices[-min(120, len(prices))] - 1) if len(prices) >= 120 else 0

        score = (mom_1m * 0.3 + mom_3m * 0.4 + mom_6m * 0.3)
        score = max(-1, min(1, score * 5))  # 归一化

        return round(score, 4)

    # ============================================================
    # 因子4: 增长因子
    # ============================================================
    def growth_factor(self, revenue_growth: Optional[float] = None,
                      earnings_growth: Optional[float] = None) -> float:
        """
        增长因子：高收入/盈利增长 = 高分。
        """
        score = 0.0

        if revenue_growth is not None:
            rev_score = max(-1, min(1, (revenue_growth - 0.05) / 0.20))
            score += rev_score * 0.5

        if earnings_growth is not None:
            earn_score = max(-1, min(1, (earnings_growth - 0.05) / 0.25))
            score += earn_score * 0.5

        return round(score, 4)

    # ============================================================
    # 因子5: 安全因子
    # ============================================================
    def safety_factor(self, df: pd.DataFrame) -> float:
        """
        安全因子：低波动 + 低回撤 + 高Sharpe = 高分。
        """
        if df.empty or 'close' not in df.columns:
            return 0.0

        prices = df['close'].values
        if len(prices) < 20:
            return 0.0

        # 年化波动率
        returns = np.diff(prices) / prices[:-1]
        vol = np.std(returns) * np.sqrt(252)
        vol_score = max(-1, min(1, (0.30 - vol) / 0.30))

        # 最大回撤
        peak = np.maximum.accumulate(prices)
        max_dd = np.max((peak - prices) / peak)
        dd_score = max(-1, min(1, (0.30 - max_dd) / 0.30))

        # Sharpe（简化）
        mean_ret = np.mean(returns) * 252
        sharpe = mean_ret / max(vol, 0.001)
        sharpe_score = max(-1, min(1, (sharpe - 0) / 1.5))

        score = vol_score * 0.4 + dd_score * 0.3 + sharpe_score * 0.3
        return round(score, 4)

    # ============================================================
    # 因子6: 技术Alpha因子（GTJA191 Alpha144）
    # ============================================================
    def technical_alpha_factor(self, df: pd.DataFrame) -> float:
        """
        技术Alpha因子：基于 GTJA191 Alpha144。

        统计过去 20 个交易日内，下跌日“收益率绝对值/成交额”的平均值。
        低效率通常意味着下跌缩量或承接较好，映射为正向 Alpha；
        高效率则映射为负向 Alpha。

        Args:
            df: 需包含 close、amount 列，按时间升序。

        Returns:
            float，范围 [-1, 1]
        """
        if not _HAS_GTJA191 or df is None or df.empty:
            return 0.0

        if 'close' not in df.columns or 'amount' not in df.columns:
            return 0.0

        try:
            factors = GTJA191Factors(lookback=20)
            value = factors.alpha144(df)
            if value is None:
                return 0.0

            # 原始值越小越好；这里将其翻转映射到 [-1, 1]
            # 经验阈值做截断，避免极端值主导
            score = max(-1.0, min(1.0, 1.0 - float(value) * 1e8))
            return round(float(score), 4)
        except Exception as exc:
            logger.warning(f"technical_alpha_factor 计算失败: {exc}")
            return 0.0

    # ============================================================
    # 综合评估
    # ============================================================
    def evaluate(self, klines: Dict[str, pd.DataFrame],
                 fundamentals: Optional[Dict[str, Dict]] = None,
                 event_factors: Optional[Dict[str, Dict]] = None) -> Dict[str, FactorResult]:
        """
        对所有标的进行五维因子评估。

        Args:
            klines: {code: DataFrame}，需包含 'close' 列
            fundamentals: {code: {pe, pb, roe, ...}}
            event_factors: {code: {sentiment, event_impact}}

        Returns:
            {code: FactorResult}
        """
        results = {}

        for code, df in klines.items():
            if df.empty or len(df) < 20:
                continue

            fund = (fundamentals or {}).get(code, {})
            events = (event_factors or {}).get(code, {})

            factors = {
                'value': self.value_factor(
                    df, pe=fund.get('pe'), pb=fund.get('pb'),
                    dividend_yield=fund.get('dividend_yield')
                ),
                'quality': self.quality_factor(
                    df, roe=fund.get('roe'),
                    debt_ratio=fund.get('debt_ratio'),
                    profit_margin=fund.get('profit_margin')
                ),
                'momentum': self.momentum_factor(df),
                'growth': self.growth_factor(
                    revenue_growth=fund.get('revenue_growth'),
                    earnings_growth=fund.get('earnings_growth')
                ),
                'safety': self.safety_factor(df),
                'technical_alpha': self.technical_alpha_factor(df),
            }

            # 合并事件驱动因子（如果提供）
            if 'sentiment' in events:
                factors['sentiment'] = events['sentiment']
            if 'event_impact' in events:
                factors['event_impact'] = events['event_impact']

            # 加权综合
            composite = sum(
                factors.get(name, 0) * self.weights.get(name, 0)
                for name in self.weights
            )

            # 信号
            signal = self._to_signal(composite)

            results[code] = FactorResult(
                code=code,
                composite=round(composite, 4),
                factors={k: round(v, 4) for k, v in factors.items()},
                signal=signal,
            )

        # 排名
        sorted_codes = sorted(results.keys(),
                              key=lambda c: results[c].composite, reverse=True)
        for rank, code in enumerate(sorted_codes, 1):
            results[code].rank = rank

        return results

    def generate_signal(self, results: Dict[str, FactorResult]) -> Dict:
        """
        根据因子评估结果生成组合级别信号。

        Returns:
            {
                'avg_composite': float,
                'signal': str,
                'distribution': {signal: count},
                'top_3': [code, ...],
                'bottom_3': [code, ...],
            }
        """
        if not results:
            return {'signal': 'hold', 'avg_composite': 0.0, 'note': '无数据'}

        composites = [r.composite for r in results.values()]
        avg = np.mean(composites)

        # 信号分布
        dist = {}
        for r in results.values():
            dist[r.signal] = dist.get(r.signal, 0) + 1

        # Top/Bottom
        sorted_r = sorted(results.values(), key=lambda x: x.composite, reverse=True)
        top_3 = [r.code for r in sorted_r[:3]]
        bottom_3 = [r.code for r in sorted_r[-3:]]

        return {
            'avg_composite': round(avg, 4),
            'signal': self._to_signal(avg),
            'distribution': dist,
            'top_3': top_3,
            'bottom_3': bottom_3,
        }

    def _to_signal(self, composite: float) -> str:
        """因子得分 → 交易信号"""
        if composite >= self.SIGNAL_THRESHOLDS['strong_buy']:
            return 'strong_buy'
        elif composite >= self.SIGNAL_THRESHOLDS['buy']:
            return 'buy'
        elif composite >= self.SIGNAL_THRESHOLDS['hold_lower']:
            return 'hold'
        elif composite >= self.SIGNAL_THRESHOLDS['sell']:
            return 'sell'
        else:
            return 'strong_sell'

    def compute_factor_correlation(self, results: Dict[str, FactorResult]) -> pd.DataFrame:
        """
        计算因子间相关性矩阵（用于评估因子独立性）。
        """
        factor_names = list(self.weights.keys())
        data = {}
        for name in factor_names:
            data[name] = [r.factors.get(name, 0) for r in results.values()]

        df = pd.DataFrame(data)
        return df.corr()
