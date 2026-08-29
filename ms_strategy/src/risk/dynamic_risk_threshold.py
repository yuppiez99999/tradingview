"""
动态风控阈值调整 (Dynamic Risk Threshold Adjustment) — v1.0

核心思想:
风控阈值不是固定的，而应根据市场环境、波动率、流动性、组合状态等因素动态调整。
静态阈值在低波动率市场会过于保守，在高波动率市场会过于激进。

调整维度:
1. 市场波动率 (VIX / ATR / 历史波动率) — 高波动 → 收紧止损
2. 市场流动性 (成交额 / 买卖价差) — 低流动 → 降低单票仓位
3. 组合状态 (近期盈亏 / 回撤深度) — 亏损中 → 降低风险敞口
4. 宏观环境 (利率 / 信用利差 / 风险偏好) — 风险偏好下降 → 提高对冲比例
5. 信号质量 (模型置信度 / 信号强度) — 低置信 → 缩小仓位

调整机制:
- 基准阈值 (Baseline) : 静态配置的默认值
- 乘数因子 (Multiplier): 根据各维度计算的综合调整系数
- 实际阈值 (Actual) = 基准阈值 × 乘数因子

典型应用:
- 止损线: -8% (基准) × 0.8 (高波动) = -6.4% (更紧)
- 单票上限: 15% (基准) × 0.7 (低流动) = 10.5% (更低)
- 对冲比例: 30% (基准) × 1.5 (高风险) = 45% (更高)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("v7.5.dynamic_risk")


# ============================================================
# 枚举与数据结构
# ============================================================

class MarketRegime(Enum):
    """市场状态"""
    LOW_VOL = "low_vol"          # 低波动 (牛市稳定期)
    NORMAL = "normal"            # 正常波动
    HIGH_VOL = "high_vol"        # 高波动 (震荡市)
    EXTREME_VOL = "extreme_vol"  # 极端波动 (危机/暴跌)


class RiskAppetite(Enum):
    """风险偏好等级"""
    AGGRESSIVE = "aggressive"  # 进取
    MODERATE = "moderate"      # 稳健
    CONSERVATIVE = "conservative"  # 保守
    DEFENSIVE = "defensive"    # 防御


@dataclass
class BaselineThresholds:
    """基准风控阈值 (静态配置)"""
    # 单票
    single_stop_loss_pct: float = -0.08       # 单票止损 -8%
    single_take_profit_pct: float = 0.15      # 单票止盈 +15%
    single_position_limit_pct: float = 0.15   # 单票仓位上限 15%

    # 组合
    portfolio_max_drawdown: float = -0.15     # 组合最大回撤 -15%
    portfolio_fast_fuse: float = -0.03        # 组合快熔断 -3%
    portfolio_slow_fuse: float = -0.08        # 组合慢熔断 -8%

    # 风格/板块
    sector_max_pct: float = 0.40              # 单板块上限 40%
    style_deviation_limit: float = 0.10       # 风格偏离 ±10%

    # 对冲
    target_hedge_ratio: float = 0.30          # 目标对冲比例 30%
    hedge_adjust_step: float = 0.05           # 对冲调整步长 5%

    # 交易限制
    daily_trade_limit: float = 0.20           # 日交易额上限 20%
    single_trade_risk_pct: float = 0.015      # 单笔风险预算 1.5%


@dataclass
class MarketEnvironment:
    """市场环境数据 (用于动态调整的输入)"""
    # 波动率
    vix_current: float = 20.0          # VIX 当前值
    vix_ma20: float = 18.0             # VIX 20日均线
    realized_vol_20d: float = 0.15     # 20日已实现波动率 (年化)
    atr_ratio: float = 1.0             # ATR 相对历史均值

    # 流动性
    market_turnover: float = 1.0       # 市场成交额相对系数 (1=正常)
    average_spread_bp: float = 5.0     # 平均买卖价差 (bp)

    # 趋势
    price_trend: float = 0.0           # 价格趋势 (-1 到 +1)
    momentum_score: float = 0.5        # 动量评分 (0-1)

    # 宏观
    risk_on: float = 0.5               # 风险偏好 (0-1)
    credit_spread: float = 1.5         # 信用利差 (%)


@dataclass
class PortfolioState:
    """组合状态 (用于动态调整的输入)"""
    current_drawdown: float = 0.0         # 当前回撤
    pnl_30d_pct: float = 0.0              # 近30日盈亏
    win_rate_30d: float = 0.5             # 近30日胜率
    sharpe_ratio: float = 1.0             # 夏普比率
    position_concentration: float = 0.3   # 持仓集中度 (HHI)
    cash_ratio: float = 0.10              # 现金比例
    current_hedge_ratio: float = 0.30     # 当前对冲比例


@dataclass
class AdjustmentFactors:
    """各维度调整因子"""
    volatility_factor: float = 1.0     # 波动率因子
    liquidity_factor: float = 1.0      # 流动性因子
    trend_factor: float = 1.0          # 趋势因子
    portfolio_factor: float = 1.0      # 组合状态因子
    macro_factor: float = 1.0          # 宏观因子
    signal_factor: float = 1.0         # 信号质量因子

    def combined(self, weights: dict | None = None) -> float:
        """计算综合因子 (加权乘积)

        Args:
            weights: 各维度权重, 默认为均等权重

        Returns:
            综合调整因子 (0.3 ~ 2.0)
        """
        w = weights or {
            "volatility": 0.30,
            "liquidity": 0.15,
            "trend": 0.15,
            "portfolio": 0.25,
            "macro": 0.10,
            "signal": 0.05,
        }
        factors = {
            "volatility": self.volatility_factor,
            "liquidity": self.liquidity_factor,
            "trend": self.trend_factor,
            "portfolio": self.portfolio_factor,
            "macro": self.macro_factor,
            "signal": self.signal_factor,
        }

        # 加权几何平均 (乘法模型)
        log_sum = 0.0
        total_w = 0.0
        for key, weight in w.items():
            if key in factors and factors[key] > 0:
                log_sum += weight * np.log(factors[key])
                total_w += weight

        if total_w == 0:
            return 1.0

        combined = np.exp(log_sum / total_w)
        # 限制在合理范围
        return max(0.3, min(2.0, combined))


@dataclass
class DynamicThresholds:
    """动态调整后的实际阈值"""
    # 单票
    single_stop_loss_pct: float
    single_take_profit_pct: float
    single_position_limit_pct: float

    # 组合
    portfolio_max_drawdown: float
    portfolio_fast_fuse: float
    portfolio_slow_fuse: float

    # 风格/板块
    sector_max_pct: float
    style_deviation_limit: float

    # 对冲
    target_hedge_ratio: float
    hedge_adjust_step: float

    # 交易限制
    daily_trade_limit: float
    single_trade_risk_pct: float

    # 元信息
    adjustment_factor: float = 1.0
    regime: str = "normal"
    risk_appetite: str = "moderate"
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "single_stop_loss_pct": round(self.single_stop_loss_pct, 4),
            "single_take_profit_pct": round(self.single_take_profit_pct, 4),
            "single_position_limit_pct": round(self.single_position_limit_pct, 4),
            "portfolio_max_drawdown": round(self.portfolio_max_drawdown, 4),
            "portfolio_fast_fuse": round(self.portfolio_fast_fuse, 4),
            "portfolio_slow_fuse": round(self.portfolio_slow_fuse, 4),
            "sector_max_pct": round(self.sector_max_pct, 4),
            "style_deviation_limit": round(self.style_deviation_limit, 4),
            "target_hedge_ratio": round(self.target_hedge_ratio, 4),
            "hedge_adjust_step": round(self.hedge_adjust_step, 4),
            "daily_trade_limit": round(self.daily_trade_limit, 4),
            "single_trade_risk_pct": round(self.single_trade_risk_pct, 4),
            "adjustment_factor": round(self.adjustment_factor, 4),
            "regime": self.regime,
            "risk_appetite": self.risk_appetite,
            "last_updated": self.last_updated,
        }


# ============================================================
# 动态风控阈值调整器
# ============================================================

class DynamicRiskThreshold:
    """动态风控阈值调整器

    使用方式:
        adjuster = DynamicRiskThreshold(baseline=BaselineThresholds())
        thresholds = adjuster.calculate(
            market_env=MarketEnvironment(...),
            portfolio_state=PortfolioState(...),
            signal_confidence=0.8,
        )
        logger.info(f"当前止损线: {thresholds.single_stop_loss_pct:.1%}")
    """

    def __init__(self, baseline: BaselineThresholds | None = None):
        self.baseline = baseline or BaselineThresholds()
        self.factors = AdjustmentFactors()
        self.current_thresholds = self._baseline_to_dynamic()
        self.adjustment_history: list[dict[str, Any]] = []

    # ----------------------------------------------------------
    # 主计算入口
    # ----------------------------------------------------------

    def calculate(
        self,
        market_env: MarketEnvironment | None = None,
        portfolio_state: PortfolioState | None = None,
        signal_confidence: float = 0.8,
    ) -> DynamicThresholds:
        """计算动态阈值

        Args:
            market_env: 市场环境数据
            portfolio_state: 组合状态数据
            signal_confidence: 信号置信度 (0-1)

        Returns:
            动态调整后的阈值
        """
        market_env = market_env or MarketEnvironment()
        portfolio_state = portfolio_state or PortfolioState()

        # 1. 计算各维度调整因子
        self.factors = AdjustmentFactors(
            volatility_factor=self._calc_volatility_factor(market_env),
            liquidity_factor=self._calc_liquidity_factor(market_env),
            trend_factor=self._calc_trend_factor(market_env),
            portfolio_factor=self._calc_portfolio_factor(portfolio_state),
            macro_factor=self._calc_macro_factor(market_env),
            signal_factor=self._calc_signal_factor(signal_confidence),
        )

        # 2. 综合因子
        combined = self.factors.combined()

        # 3. 识别市场状态
        regime = self._detect_regime(market_env)
        risk_appetite = self._detect_risk_appetite(combined, portfolio_state)

        # 4. 应用到各阈值
        thresholds = self._apply_adjustment(combined, regime)
        thresholds.regime = regime.value
        thresholds.risk_appetite = risk_appetite.value
        thresholds.adjustment_factor = combined
        thresholds.last_updated = datetime.now().isoformat()

        self.current_thresholds = thresholds

        # 5. 记录历史
        self.adjustment_history.append({
            "timestamp": datetime.now().isoformat(),
            "factor": round(combined, 4),
            "regime": regime.value,
            "risk_appetite": risk_appetite.value,
            "single_stop_loss": round(thresholds.single_stop_loss_pct, 4),
            "position_limit": round(thresholds.single_position_limit_pct, 4),
            "hedge_ratio": round(thresholds.target_hedge_ratio, 4),
        })

        logger.info(
            "动态风控阈值更新: 综合因子=%.3f, 状态=%s, 偏好=%s",
            combined, regime.value, risk_appetite.value,
        )

        return thresholds

    # ----------------------------------------------------------
    # 各维度因子计算
    # ----------------------------------------------------------

    def _calc_volatility_factor(self, env: MarketEnvironment) -> float:
        """波动率因子

        高波动 → 收紧风控 (因子 < 1): 止损更严, 仓位更低, 对冲更高
        低波动 → 放松风控 (因子 > 1): 止损更宽, 仓位更高, 对冲更低
        """
        vix = env.vix_current
        vix_ma = env.vix_ma20 or vix

        # VIX 相对位置
        vix / max(vix_ma, 1)

        # VIX 绝对水平映射
        if vix <= 15:
            base = 1.3
        elif vix <= 20:
            base = 1.1
        elif vix <= 25:
            base = 1.0
        elif vix <= 30:
            base = 0.85
        elif vix <= 40:
            base = 0.65
        else:
            base = 0.45

        # 已实现波动率调整
        vol = env.realized_vol_20d
        if vol > 0.30:
            vol_adj = 0.7
        elif vol > 0.20:
            vol_adj = 0.85
        elif vol > 0.10:
            vol_adj = 1.0
        else:
            vol_adj = 1.15

        factor = base * vol_adj

        logger.debug("波动率因子: VIX=%.1f, 已实现波动=%.1f%%, 因子=%.3f",
                     vix, vol * 100, factor)
        return max(0.3, min(1.8, factor))

    def _calc_liquidity_factor(self, env: MarketEnvironment) -> float:
        """流动性因子

        低流动 → 降低仓位, 收紧止损 (因子 < 1)
        高流动 → 可加大仓位 (因子 > 1)
        """
        turnover = env.market_turnover
        spread = env.average_spread_bp

        # 成交额因子
        if turnover >= 1.5:
            turnover_factor = 1.15
        elif turnover >= 1.0:
            turnover_factor = 1.0
        elif turnover >= 0.6:
            turnover_factor = 0.85
        elif turnover >= 0.3:
            turnover_factor = 0.65
        else:
            turnover_factor = 0.45

        # 价差因子
        if spread <= 3:
            spread_factor = 1.1
        elif spread <= 8:
            spread_factor = 1.0
        elif spread <= 20:
            spread_factor = 0.85
        else:
            spread_factor = 0.7

        factor = turnover_factor * spread_factor
        return max(0.4, min(1.3, factor))

    def _calc_trend_factor(self, env: MarketEnvironment) -> float:
        """趋势因子

        强趋势 → 适当放宽 (顺势)
        震荡/逆趋势 → 收紧
        """
        trend = env.price_trend  # -1 to +1
        momentum = env.momentum_score  # 0-1

        # 强上涨趋势 → 适度放宽止损, 提高止盈
        if trend > 0.5 and momentum > 0.6:
            return 1.15
        if trend > 0.2:
            return 1.05
        if trend < -0.3:
            return 0.75
        if trend < -0.1:
            return 0.9
        # 震荡市
        return 0.95

    def _calc_portfolio_factor(self, state: PortfolioState) -> float:
        """组合状态因子

        盈利且回撤小 → 可承受更大风险 (因子 > 1)
        亏损且回撤深 → 收缩风险 (因子 < 1)
        """
        dd = state.current_drawdown  # 负数表示回撤
        pnl = state.pnl_30d_pct
        sharpe = state.sharpe_ratio

        score = 0.0  # 越高越激进

        # 回撤评分 (40% 权重)
        if dd > -0.03:
            score += 40
        elif dd > -0.08:
            score += 30
        elif dd > -0.12:
            score += 15
        else:
            score += 0

        # 近期盈亏 (30% 权重)
        if pnl > 0.05:
            score += 30
        elif pnl > 0.02:
            score += 25
        elif pnl > -0.02:
            score += 18
        elif pnl > -0.05:
            score += 10
        else:
            score += 0

        # 夏普比率 (30% 权重)
        if sharpe > 2.0:
            score += 30
        elif sharpe > 1.5:
            score += 25
        elif sharpe > 1.0:
            score += 20
        elif sharpe > 0.5:
            score += 12
        else:
            score += 5

        # 分数 → 因子 (50分 = 1.0)
        factor = 0.5 + (score / 100)
        return max(0.4, min(1.5, factor))

    def _calc_macro_factor(self, env: MarketEnvironment) -> float:
        """宏观因子

        风险偏好高 → 因子 > 1
        风险偏好低 → 因子 < 1
        """
        risk_on = env.risk_on  # 0-1
        credit = env.credit_spread

        # 风险偏好
        risk_factor = 0.6 + risk_on * 0.6  # 0.6 ~ 1.2

        # 信用利差 (利差越大越谨慎)
        if credit <= 1.0:
            credit_factor = 1.1
        elif credit <= 2.0:
            credit_factor = 1.0
        elif credit <= 3.5:
            credit_factor = 0.85
        else:
            credit_factor = 0.65

        factor = risk_factor * 0.6 + credit_factor * 0.4
        return max(0.5, min(1.3, factor))

    def _calc_signal_factor(self, confidence: float) -> float:
        """信号质量因子

        高置信 → 因子 > 1
        低置信 → 因子 < 1
        """
        if confidence >= 0.9:
            return 1.15
        if confidence >= 0.75:
            return 1.0
        if confidence >= 0.6:
            return 0.85
        if confidence >= 0.4:
            return 0.7
        return 0.5

    # ----------------------------------------------------------
    # 状态识别
    # ----------------------------------------------------------

    def _detect_regime(self, env: MarketEnvironment) -> MarketRegime:
        """识别市场状态"""
        vix = env.vix_current
        vol = env.realized_vol_20d

        if vix > 40 or vol > 0.40:
            return MarketRegime.EXTREME_VOL
        if vix > 25 or vol > 0.25:
            return MarketRegime.HIGH_VOL
        if vix < 15 and vol < 0.12:
            return MarketRegime.LOW_VOL
        return MarketRegime.NORMAL

    def _detect_risk_appetite(
        self, combined_factor: float, state: PortfolioState
    ) -> RiskAppetite:
        """识别风险偏好等级"""
        dd = state.current_drawdown

        if combined_factor >= 1.2 and dd > -0.05:
            return RiskAppetite.AGGRESSIVE
        if combined_factor >= 0.9 and dd > -0.10:
            return RiskAppetite.MODERATE
        if combined_factor >= 0.6 and dd > -0.15:
            return RiskAppetite.CONSERVATIVE
        return RiskAppetite.DEFENSIVE

    # ----------------------------------------------------------
    # 应用调整
    # ----------------------------------------------------------

    def _apply_adjustment(
        self, factor: float, regime: MarketRegime
    ) -> DynamicThresholds:
        """将调整因子应用到各阈值

        注意: 不同类型的阈值调整方向不同
        - 止损/熔断 (负数): 因子 < 1 → 更严 (数值更大, 如 -8% × 0.8 = -6.4%)
        - 止盈/仓位上限 (正数): 因子 < 1 → 更低
        - 对冲比例: 因子 < 1 → 更高 (反向)
        """
        b = self.baseline

        # 止损类: 乘以因子 (更严 = 更大的数)
        stop_loss = b.single_stop_loss_pct * factor
        max_dd = b.portfolio_max_drawdown * factor
        fast_fuse = b.portfolio_fast_fuse * factor
        slow_fuse = b.portfolio_slow_fuse * factor

        # 止盈类: 乘以因子
        take_profit = b.single_take_profit_pct * factor

        # 仓位限制类: 乘以因子
        pos_limit = b.single_position_limit_pct * factor
        sector_max = b.sector_max_pct * factor
        style_dev = b.style_deviation_limit * factor
        daily_limit = b.daily_trade_limit * factor
        single_risk = b.single_trade_risk_pct * factor

        # 对冲比例: 反向调整 (风险低 → 对冲少; 风险高 → 对冲多)
        # 正常因子1.0 → 正常对冲比例
        # 因子0.5 (高风险) → 对冲比例提高
        hedge_inverse = 1.0 + (1.0 - factor) * 0.8  # 0.5 → 1.4; 1.5 → 0.6
        target_hedge = b.target_hedge_ratio * hedge_inverse
        target_hedge = max(0.0, min(0.80, target_hedge))

        hedge_step = b.hedge_adjust_step * max(0.5, min(1.5, factor))

        return DynamicThresholds(
            single_stop_loss_pct=round(stop_loss, 4),
            single_take_profit_pct=round(take_profit, 4),
            single_position_limit_pct=round(pos_limit, 4),
            portfolio_max_drawdown=round(max_dd, 4),
            portfolio_fast_fuse=round(fast_fuse, 4),
            portfolio_slow_fuse=round(slow_fuse, 4),
            sector_max_pct=round(sector_max, 4),
            style_deviation_limit=round(style_dev, 4),
            target_hedge_ratio=round(target_hedge, 4),
            hedge_adjust_step=round(hedge_step, 4),
            daily_trade_limit=round(daily_limit, 4),
            single_trade_risk_pct=round(single_risk, 4),
        )

    def _baseline_to_dynamic(self) -> DynamicThresholds:
        """基准阈值转动态阈值 (初始值)"""
        b = self.baseline
        return DynamicThresholds(
            single_stop_loss_pct=b.single_stop_loss_pct,
            single_take_profit_pct=b.single_take_profit_pct,
            single_position_limit_pct=b.single_position_limit_pct,
            portfolio_max_drawdown=b.portfolio_max_drawdown,
            portfolio_fast_fuse=b.portfolio_fast_fuse,
            portfolio_slow_fuse=b.portfolio_slow_fuse,
            sector_max_pct=b.sector_max_pct,
            style_deviation_limit=b.style_deviation_limit,
            target_hedge_ratio=b.target_hedge_ratio,
            hedge_adjust_step=b.hedge_adjust_step,
            daily_trade_limit=b.daily_trade_limit,
            single_trade_risk_pct=b.single_trade_risk_pct,
            adjustment_factor=1.0,
            regime="normal",
            risk_appetite="moderate",
            last_updated=datetime.now().isoformat(),
        )

    # ----------------------------------------------------------
    # 便捷查询
    # ----------------------------------------------------------

    def get_thresholds(self) -> DynamicThresholds:
        """获取当前阈值"""
        return self.current_thresholds

    def get_factors(self) -> AdjustmentFactors:
        """获取当前调整因子"""
        return self.factors

    def explain_adjustment(self) -> str:
        """解释当前调整的原因"""
        t = self.current_thresholds
        f = self.factors
        b = self.baseline

        lines = [
            "## 动态风控调整说明",
            "",
            f"- **市场状态**: {t.regime}",
            f"- **风险偏好**: {t.risk_appetite}",
            f"- **综合调整因子**: {t.adjustment_factor:.3f}",
            "",
            "### 各维度因子",
            "",
            "| 维度 | 因子 | 影响 |",
            "|------|------|------|",
            f"| 波动率 | {f.volatility_factor:.3f} | {'收紧' if f.volatility_factor < 1 else '放宽'} |",
            f"| 流动性 | {f.liquidity_factor:.3f} | {'收紧' if f.liquidity_factor < 1 else '放宽'} |",
            f"| 趋势 | {f.trend_factor:.3f} | {'收紧' if f.trend_factor < 1 else '放宽'} |",
            f"| 组合状态 | {f.portfolio_factor:.3f} | {'收紧' if f.portfolio_factor < 1 else '放宽'} |",
            f"| 宏观 | {f.macro_factor:.3f} | {'收紧' if f.macro_factor < 1 else '放宽'} |",
            f"| 信号质量 | {f.signal_factor:.3f} | {'收紧' if f.signal_factor < 1 else '放宽'} |",
            "",
            "### 关键阈值变化",
            "",
            "| 阈值 | 基准 | 调整后 | 变化 |",
            "|------|------|--------|------|",
            f"| 单票止损 | {b.single_stop_loss_pct:.1%} | {t.single_stop_loss_pct:.1%} | "
            f"{'更严' if t.single_stop_loss_pct > b.single_stop_loss_pct else '更宽'} |",
            f"| 单票仓位上限 | {b.single_position_limit_pct:.1%} | {t.single_position_limit_pct:.1%} | "
            f"{'降低' if t.single_position_limit_pct < b.single_position_limit_pct else '提高'} |",
            f"| 组合快熔断 | {b.portfolio_fast_fuse:.1%} | {t.portfolio_fast_fuse:.1%} | "
            f"{'更严' if t.portfolio_fast_fuse > b.portfolio_fast_fuse else '更宽'} |",
            f"| 目标对冲比例 | {b.target_hedge_ratio:.0%} | {t.target_hedge_ratio:.0%} | "
            f"{'提高' if t.target_hedge_ratio > b.target_hedge_ratio else '降低'} |",
            f"| 单笔风险预算 | {b.single_trade_risk_pct:.2%} | {t.single_trade_risk_pct:.2%} | "
            f"{'降低' if t.single_trade_risk_pct < b.single_trade_risk_pct else '提高'} |",
        ]
        return "\n".join(lines)


__all__ = [
    "AdjustmentFactors",
    "BaselineThresholds",
    "DynamicRiskThreshold",
    "DynamicThresholds",
    "MarketEnvironment",
    "MarketRegime",
    "PortfolioState",
    "RiskAppetite",
]
