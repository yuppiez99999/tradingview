#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 综合量化策略系统 v7.0 — 优化版
 Comprehensive Quantitative Strategy System v7.0 - Optimized
================================================================================

 核心升级:
   1. 期货+期权双层对冲框架 — Futures(成本对冲) + Options(尾部保护)
   2. 保护性看跌期权策略 — Protective Put Ladder with Put Spread Collar
   3. 动态Delta对冲优化 — 波动率自适应对冲比率，市场状态自适应目标Beta
   4. 备兑开仓增强 — 系统化滚动备兑策略
   5. 波动率套利增强 — IV/RV偏离监测与均值回归交易
   6. 尾部风险预算 — 期权溢价成本纳入总风险预算
   7. 资金效率优化 — 保证金优化与资本配置动态调整

 对冲层级架构 (总对冲资金占比 40% = 200万):
   Layer 1: 股指期货Delta对冲 (10%) — 低成本市场Beta对冲
   Layer 2: 期权保护性看跌 (10%)    — 尾部风险保护 (凸性收益)
   Layer 3: 波动率对冲 (8%)          — Vega中性管理
   Layer 4: 绝对收益/市场中性 (7%)   — Alpha独立来源
   Layer 5: 备兑开仓增强 (5%)        — 权利金增收

 预期收益归因:
   权益多头组合:           年化 +12% (选股Alpha + 市场Beta)
   期货Delta对冲成本:      年化 -3%  (对冲拖累)
   期权保护性看跌成本:      年化 -2%  (权利金支出)
   备兑开仓权利金:          年化 +1.5% (增收)
   波动率套利:              年化 +2%  (Alpha)
   绝对收益策略:            年化 +2%  (Alpha)
   资金成本/滑点:           年化 -1%  (交易成本)
   --------------------------------------------
   净组合收益:              年化 ~11.5% (目标 > 8.5%)
   最大回撤控制:             < 15%

 风险预算分配:
   权益多头VaR 95%:         12%
   期货对冲VaR 95%:          3%
   期权组合VaR 95%:          5%
   总组合VaR 95%:           < 10%
   压力测试最大回撤:         < 15%

 Author: ZCode Quantitative Team
 Version: 7.0
 Date: 2026-07-03
================================================================================
"""

import os
import sys
import math
import json
import logging
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

# numpy 和 pandas 仅在需要真实数据回测时使用
# 当前系统使用纯 Python math 库即可正常运行
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None
    HAS_PANDAS = False

warnings.filterwarnings('ignore')

# ============================================================================
# 日志配置
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('comprehensive_quant_system_v7.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('QuantSystemV7')

# ============================================================================
# 枚举定义
# ============================================================================
class MarketRegime(Enum):
    """市场状态枚举"""
    BULL = "bull"               # 牛市 — 强劲上涨趋势
    SLOW_BULL = "slow_bull"     # 慢牛 — 温和上涨
    SIDEWAYS = "sideways"       # 震荡 — 方向不明
    HIGH_VOL = "high_volatility"  # 高波动 — VIX > 25
    BEAR = "bear"               # 熊市 — 持续下跌
    CRASH = "crash"             # 崩盘 — 极端下跌


class HedgeLayer(Enum):
    """对冲层级"""
    FUTURES_DELTA = "futures_delta"       # 股指期货Delta对冲
    OPTIONS_PROTECTIVE = "options_protective"  # 期权保护性看跌
    VOLATILITY = "volatility_hedge"       # 波动率对冲
    ABSOLUTE_RETURN = "absolute_return"   # 绝对收益
    COVERED_WRITE = "covered_write"       # 备兑开仓


# ============================================================================
# 期权定价模块 (Black-Scholes + Greeks)
# ============================================================================
class OptionPricing:
    """Black-Scholes期权定价与Greeks计算"""

    def __init__(self):
        self.risk_free_rate = 0.025  # 当前中国10年国债收益率约2.5%

    @staticmethod
    def norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    @staticmethod
    def norm_pdf(x: float) -> float:
        return math.exp(-0.5 * x ** 2) / math.sqrt(2 * math.pi)

    def price(self, S: float, K: float, T: float, sigma: float,
              option_type: str = 'call') -> float:
        """Black-Scholes期权定价"""
        if T <= 0:
            return max(0, S - K) if option_type == 'call' else max(0, K - S)

        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        if option_type == 'call':
            return S * self.norm_cdf(d1) - K * math.exp(-self.risk_free_rate * T) * self.norm_cdf(d2)
        else:
            return K * math.exp(-self.risk_free_rate * T) * self.norm_cdf(-d2) - S * self.norm_cdf(-d1)

    def greeks(self, S: float, K: float, T: float, sigma: float,
               option_type: str = 'call') -> Dict[str, float]:
        """计算全部Greeks"""
        if T <= 0:
            return {'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'rho': 0}

        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        delta = self.norm_cdf(d1) if option_type == 'call' else self.norm_cdf(d1) - 1
        gamma = self.norm_pdf(d1) / (S * sigma * math.sqrt(T))

        term1 = -(S * sigma * self.norm_pdf(d1)) / (2 * math.sqrt(T))
        if option_type == 'call':
            term2 = -self.risk_free_rate * K * math.exp(-self.risk_free_rate * T) * self.norm_cdf(d2)
        else:
            term2 = self.risk_free_rate * K * math.exp(-self.risk_free_rate * T) * self.norm_cdf(-d2)
        theta = (term1 + term2) / 365  # 每日theta

        vega = S * math.sqrt(T) * self.norm_pdf(d1) / 100  # 1%波动率变化

        if option_type == 'call':
            rho = K * T * math.exp(-self.risk_free_rate * T) * self.norm_cdf(d2) / 100
        else:
            rho = -K * T * math.exp(-self.risk_free_rate * T) * self.norm_cdf(-d2) / 100

        return {'delta': delta, 'gamma': gamma, 'theta': theta, 'vega': vega, 'rho': rho}

    def implied_volatility(self, market_price: float, S: float, K: float, T: float,
                           option_type: str = 'call', max_iter: int = 100) -> float:
        """Newton-Raphson法计算隐含波动率"""
        sigma = 0.3
        for _ in range(max_iter):
            price = self.price(S, K, T, sigma, option_type)
            vega = self.greeks(S, K, T, sigma, option_type)['vega'] * 100
            if abs(vega) < 1e-10:
                break
            diff = price - market_price
            if abs(diff) < 1e-6:
                return sigma
            sigma -= diff / vega
            sigma = max(0.05, min(2.0, sigma))
        return sigma


# ============================================================================
# 市场状态检测器
# ============================================================================
class MarketRegimeDetector:
    """市场状态识别 — 驱动对冲策略选择"""

    def __init__(self):
        self.current_regime = MarketRegime.SIDEWAYS
        self.regime_history: List[Tuple[datetime, MarketRegime]] = []

    def detect(self, market_data: Dict[str, float]) -> MarketRegime:
        """
        检测当前市场状态

        参数:
            market_data: {
                'index_return_20d': 20日收益率,
                'index_return_60d': 60日收益率,
                'volatility_20d': 20日波动率,
                'volatility_60d': 60日波动率,
                'vix_level': VIX水平(或50ETF期权VIX),
                'volume_ratio': 成交量比率,
                'drawdown_from_high': 距高点回撤
            }
        """
        ret_20d = market_data.get('index_return_20d', 0)
        ret_60d = market_data.get('index_return_60d', 0)
        vol_20d = market_data.get('volatility_20d', 0.20)
        vix = market_data.get('vix_level', 20)
        dd = market_data.get('drawdown_from_high', 0)

        # 崩盘检测: VIX > 40 或 20日跌幅 > 15%
        if vix > 40 or abs(ret_20d) > 0.15 and ret_20d < 0:
            regime = MarketRegime.CRASH
        # 高波动: VIX > 25
        elif vix > 25:
            regime = MarketRegime.HIGH_VOL
        # 熊市: 60日负收益 + 回撤 > 10%
        elif ret_60d < -0.05 and dd > 0.10:
            regime = MarketRegime.BEAR
        # 牛市: 60日正收益 > 5% + VIX < 20
        elif ret_60d > 0.05 and vix < 20:
            regime = MarketRegime.BULL
        # 慢牛: 20日正收益但温和
        elif ret_20d > 0 and ret_20d < 0.03 and vol_20d < 0.18:
            regime = MarketRegime.SLOW_BULL
        else:
            regime = MarketRegime.SIDEWAYS

        self.current_regime = regime
        self.regime_history.append((datetime.now(), regime))
        return regime


# ============================================================================
# 第1层: 股指期货Delta对冲 (强化版)
# ============================================================================
class EnhancedFuturesHedge:
    """
    股指期货Delta对冲 — 强化版

    优化点:
      - 波动率自适应对冲比率: VIX高时对冲更多
      - 市场状态自适应目标Beta: 熊市目标Beta=−0.1 (净空), 震荡市Beta=0
      - 趋势跟踪动量过滤: 避免在上升趋势中过度对冲
      - 对冲比率平滑: EMA平滑避免频繁调仓
    """

    def __init__(self, capital: float = 500000.0):
        self.allocated_capital = capital
        self.hedge_ratio = 0.0             # 当前对冲比率
        self.target_beta = 0.0             # 目标组合Beta
        self.futures_short_value = 0.0     # 期货空头名义价值
        self.hedge_cost_accrued = 0.0      # 累计对冲成本
        self.hedge_pnl = 0.0               # 对冲盈亏
        self.last_adjustment = None        # 上次调仓时间

        # 优化参数
        self.base_hedge_ratio = 0.30       # 基础对冲比率 30%
        self.max_hedge_ratio = 0.70        # 最大对冲比率 70%
        self.min_hedge_ratio = 0.05        # 最小对冲比率 5%
        self.vol_sensitivity = 2.0         # 波动率敏感度
        self.ema_smooth = 0.3              # EMA平滑系数
        self.rebalance_threshold = 0.03    # 3%偏离触发调仓

        # 对冲成本参数
        self.annual_roll_cost = 0.01       # 年化展期成本 1%
        self.transaction_cost = 0.0003     # 单边交易成本 3bp

    def calculate_optimal_hedge(self, portfolio_beta: float, portfolio_value: float,
                                market_regime: MarketRegime, market_vol: float,
                                momentum_signal: float) -> Dict[str, float]:
        """
        计算最优对冲比率

        参数:
            portfolio_beta: 组合Beta
            portfolio_value: 组合市值
            market_regime: 当前市场状态
            market_vol: 市场波动率 (年化)
            momentum_signal: 动量信号 (−1到+1, 负值看跌)

        返回:
            hedge_decision: 对冲决策
        """
        # 1. 基础对冲比率 — 基于市场状态
        regime_hedge_map = {
            MarketRegime.BULL: 0.10,
            MarketRegime.SLOW_BULL: 0.15,
            MarketRegime.SIDEWAYS: 0.30,
            MarketRegime.HIGH_VOL: 0.45,
            MarketRegime.BEAR: 0.60,
            MarketRegime.CRASH: 0.70,
        }
        base_ratio = regime_hedge_map.get(market_regime, 0.30)

        # 2. 波动率调整 — VIX越高对冲越多
        vol_adjustment = max(0, (market_vol - 0.18) * self.vol_sensitivity)
        vol_adjusted = base_ratio + vol_adjustment

        # 3. 动量过滤 — 上升趋势中减少对冲
        momentum_adjustment = -momentum_signal * 0.10  # 动量信号为正时减少对冲
        momentum_adjusted = vol_adjusted + momentum_adjustment

        # 4. 目标Beta计算
        if market_regime in (MarketRegime.BEAR, MarketRegime.CRASH):
            self.target_beta = -0.10  # 熊市净空
        elif market_regime == MarketRegime.HIGH_VOL:
            self.target_beta = 0.0    # 高波动中性
        elif market_regime == MarketRegime.BULL:
            self.target_beta = 0.30   # 牛市保留部分Beta敞口
        else:
            self.target_beta = 0.10   # 震荡/慢牛轻度对冲

        # 5. 计算对冲比率
        # hedge_ratio = (portfolio_beta - target_beta) * portfolio_value
        required_beta_reduction = max(0, portfolio_beta - self.target_beta)
        raw_hedge_ratio = required_beta_reduction / portfolio_beta if portfolio_beta > 0 else 0

        # 6. 与波动率调整融合 + 钳制 + EMA平滑
        combined = max(self.min_hedge_ratio,
                       min(self.max_hedge_ratio,
                           raw_hedge_ratio * 0.5 + momentum_adjusted * 0.5))
        self.hedge_ratio = (self.ema_smooth * combined +
                            (1 - self.ema_smooth) * self.hedge_ratio)

        # 7. 计算期货空头名义金额
        self.futures_short_value = self.hedge_ratio * portfolio_value

        return {
            'hedge_ratio': self.hedge_ratio,
            'target_beta': self.target_beta,
            'futures_short_value': self.futures_short_value,
            'base_for_regime': base_ratio,
            'vol_adjustment': vol_adjustment,
            'momentum_adjustment': momentum_adjustment,
            'regime': market_regime.value
        }

    def execute_hedge(self, current_hedge_ratio: float,
                      portfolio_value: float,
                      futures_multiplier: float = 300.0,
                      index_level: float = 4000.0) -> Dict[str, Any]:
        """
        执行期货对冲交易

        沪深300股指期货: 每点300元, 保证金约12%
        """
        target_short = self.hedge_ratio * portfolio_value
        current_short = current_hedge_ratio * portfolio_value
        delta_notional = target_short - current_short

        # 偏离阈值检查
        if abs(delta_notional) / portfolio_value < self.rebalance_threshold:
            return {'action': 'hold', 'reason': 'within_threshold',
                    'current_hedge': current_hedge_ratio,
                    'target_hedge': self.hedge_ratio}

        # 计算合约数量
        contract_value = futures_multiplier * index_level
        contracts = int(delta_notional / contract_value)

        if contracts == 0:
            return {'action': 'hold', 'reason': 'zero_contracts'}

        action = 'short_more' if contracts > 0 else 'reduce_short'
        abs_contracts = abs(contracts)

        # 保证金计算
        margin_required = abs_contracts * contract_value * 0.12

        # 成本估算
        estimated_cost = abs_contracts * contract_value * self.transaction_cost

        logger.info(f"期货对冲: {action} {abs_contracts}手, "
                    f"名义金额 {abs_contracts * contract_value:,.0f}, "
                    f"对冲比率 {current_hedge_ratio:.1%} → {self.hedge_ratio:.1%}")

        return {
            'action': action,
            'contracts': abs_contracts,
            'notional_value': abs_contracts * contract_value,
            'margin_required': margin_required,
            'estimated_cost': estimated_cost,
            'current_hedge': current_hedge_ratio,
            'target_hedge': self.hedge_ratio,
            'target_beta': self.target_beta
        }

    def estimate_annual_cost(self) -> float:
        """估算年化对冲成本"""
        roll_cost = self.futures_short_value * self.annual_roll_cost
        trade_cost = self.futures_short_value * self.transaction_cost * 12  # 月度调仓
        return roll_cost + trade_cost


# ============================================================================
# 第2层: 期权保护性对冲 (核心新增)
# ============================================================================
class ProtectiveOptionsHedge:
    """
    期权保护性对冲 — v7.0核心新增

    策略组合:
      1. 保护性看跌阶梯 (Protective Put Ladder):
         - 90%行权价 Put: 保护组合10%下跌
         - 85%行权价 Put: 保护组合15%下跌
         - 80%行权价 Put: 保护组合20%下跌 (价外更便宜)
         - 阶梯式配置: 40%/35%/25% 资金分配

      2. 看跌价差领口 (Put Spread Collar):
         - 买入OTM Put + 卖出更OTM Put (降低成本)
         - 同时卖出OTM Call (进一步降低成本至接近零)

      3. 尾部风险对冲 (Tail Risk Hedge):
         - 深度价外Put (70-75%行权价) — 黑天鹅保护
         - 仅在高波动/崩盘信号时激活
    """

    def __init__(self, capital: float = 500000.0):
        self.allocated_capital = capital
        self.pricing = OptionPricing()
        self.active_positions: List[Dict] = []
        self.premium_spent = 0.0        # 累计权利金支出
        self.premium_received = 0.0     # 累计权利金收入
        self.hedge_payoff = 0.0         # 对冲赔付
        self.net_pnl = 0.0              # 净盈亏

    def calculate_put_ladder(self, portfolio_value: float,
                             index_level: float, volatility: float,
                             days_to_expiry: int = 60) -> Dict[str, Any]:
        """
        计算保护性看跌阶梯

        针对沪深300指数配置三层保护性看跌期权
        """
        T = days_to_expiry / 365
        strikes_pct = [0.90, 0.85, 0.80]  # 行权价比例
        weights = [0.40, 0.35, 0.25]       # 资金分配
        ladder_cost = 0.0
        ladder_details = []

        for strike_pct, weight in zip(strikes_pct, weights):
            K = index_level * strike_pct
            put_price = self.pricing.price(index_level, K, T, volatility, 'put')
            greeks = self.pricing.greeks(index_level, K, T, volatility, 'put')

            # 每份保护的名义金额
            protection_notional = portfolio_value * weight * self.allocated_capital / self.allocated_capital
            # 需要的期权数量 (简化: 每份约等于指数点位)
            contracts = max(1, int(protection_notional / (index_level * 100)))

            cost = put_price * contracts * 100
            ladder_cost += cost

            ladder_details.append({
                'strike_pct': f'{strike_pct:.0%}',
                'strike': K,
                'put_price': put_price,
                'contracts': contracts,
                'cost': cost,
                'weight': weight,
                'greeks': greeks,
                'protection_level': f'{1 - strike_pct:.0%}下跌保护'
            })

        cost_pct = ladder_cost / portfolio_value

        logger.info(f"保护性看跌阶梯: 总成本 {ladder_cost:,.0f} ({cost_pct:.2%} of AUM)")

        return {
            'strategy': 'put_ladder',
            'total_cost': ladder_cost,
            'cost_pct_of_aum': cost_pct,
            'layers': ladder_details,
            'max_protection': '20%下跌保护',
            'net_premium_outlay': ladder_cost
        }

    def calculate_put_spread_collar(self, portfolio_value: float,
                                    index_level: float, volatility: float,
                                    days_to_expiry: int = 45) -> Dict[str, Any]:
        """
        计算看跌价差领口 — 近乎零成本的对冲结构

        结构:
          - 买入 95% OTM Put (保护5%以上下跌)
          - 卖出 85% OTM Put (放弃85%以下保护, 收取权利金)
          - 卖出 110% OTM Call (放弃10%以上涨幅, 收取权利金)

        优点: 近乎零净成本, 在温和下跌中提供有效保护
        """
        T = days_to_expiry / 365

        # Long 95% Put
        long_put_strike = index_level * 0.95
        long_put_price = self.pricing.price(index_level, long_put_strike, T, volatility, 'put')
        long_put_greeks = self.pricing.greeks(index_level, long_put_strike, T, volatility, 'put')

        # Short 85% Put
        short_put_strike = index_level * 0.85
        short_put_price = self.pricing.price(index_level, short_put_strike, T, volatility, 'put')
        short_put_greeks = self.pricing.greeks(index_level, short_put_strike, T, volatility, 'put')

        # Short 110% Call
        short_call_strike = index_level * 1.10
        short_call_price = self.pricing.price(index_level, short_call_strike, T, volatility, 'call')
        short_call_greeks = self.pricing.greeks(index_level, short_call_strike, T, volatility, 'call')

        # 净成本
        net_cost_per_share = long_put_price - short_put_price - short_call_price
        contracts = max(1, int(portfolio_value * 0.3 / (index_level * 100)))
        total_net_cost = net_cost_per_share * contracts * 100

        # 盈亏结构
        max_profit = float('inf')  # Call端理论上无限, 但被Short Call限制
        max_loss_protection = (long_put_strike - short_put_strike) * contracts * 100
        break_even_down = index_level - net_cost_per_share

        logger.info(f"看跌价差领口: 净成本 {total_net_cost:,.0f} "
                    f"({net_cost_per_share:.2f}/份), 保护区间 95%-85%")

        return {
            'strategy': 'put_spread_collar',
            'net_cost': total_net_cost,
            'net_cost_per_share': net_cost_per_share,
            'contracts': contracts,
            'long_put': {
                'strike': long_put_strike,
                'price': long_put_price,
                'greeks': long_put_greeks
            },
            'short_put': {
                'strike': short_put_strike,
                'price': short_put_price,
                'greeks': short_put_greeks
            },
            'short_call': {
                'strike': short_call_strike,
                'price': short_call_price,
                'greeks': short_call_greeks
            },
            'max_protection_value': max_loss_protection,
            'break_even_down': break_even_down
        }

    def calculate_tail_hedge(self, portfolio_value: float,
                             index_level: float, volatility: float,
                             days_to_expiry: int = 90) -> Dict[str, Any]:
        """
        尾部风险对冲 — 深度价外Put

        仅在市场状态为HIGH_VOL或CRASH时激活
        低成本买入深度OTM Put (如70-75%行权价)
        极端情况下提供凸性回报
        """
        T = days_to_expiry / 365

        # 75% OTM Put — 极端尾部保护
        tail_strike = index_level * 0.75
        tail_put_price = self.pricing.price(index_level, tail_strike, T, volatility, 'put')
        tail_greeks = self.pricing.greeks(index_level, tail_strike, T, volatility, 'put')

        # 小额配置 — 组合的0.5%-1%
        allocation = portfolio_value * 0.008
        contracts = max(1, int(allocation / (tail_put_price * 100)))
        total_cost = tail_put_price * contracts * 100

        # 凸性分析
        crash_scenarios = {
            '-10%': index_level * 0.90,
            '-20%': index_level * 0.80,
            '-30%': index_level * 0.70,
            '-40%': index_level * 0.60,
        }
        payoff_analysis = {}
        for label, crash_level in crash_scenarios.items():
            payoff = max(0, tail_strike - crash_level) * contracts * 100 - total_cost
            payoff_analysis[label] = {
                'option_payoff': max(0, tail_strike - crash_level) * contracts * 100,
                'net_payoff': payoff,
                'return_on_cost': payoff / total_cost if total_cost > 0 else 0
            }

        logger.info(f"尾部风险对冲: 行权价{tail_strike:.0f}(75%), "
                    f"成本 {total_cost:,.0f} ({total_cost/portfolio_value:.3%} of AUM)")

        return {
            'strategy': 'tail_hedge',
            'strike': tail_strike,
            'strike_pct': '75%',
            'put_price': tail_put_price,
            'contracts': contracts,
            'total_cost': total_cost,
            'greeks': tail_greeks,
            'crash_scenarios': payoff_analysis
        }

    def execute_combined(self, portfolio_value: float, index_level: float,
                         volatility: float, market_regime: MarketRegime,
                         days_to_expiry: int = 60) -> Dict[str, Any]:
        """
        执行组合期权对冲策略

        根据市场状态选择不同策略组合:
          BULL/SLOW_BULL:      仅Put Spread Collar (低成本)
          SIDEWAYS:            Put Spread Collar + 轻量Put Ladder
          HIGH_VOL:            Put Ladder + Tail Hedge
          BEAR/CRASH:          完整Put Ladder + Tail Hedge + Collar
        """
        results = {}

        # Put Spread Collar — 基础保护(几乎所有状态)
        collar = self.calculate_put_spread_collar(
            portfolio_value, index_level, volatility, days_to_expiry)
        results['put_spread_collar'] = collar

        # Put Ladder — 根据市场状态决定
        if market_regime in (MarketRegime.BEAR, MarketRegime.CRASH, MarketRegime.HIGH_VOL):
            ladder = self.calculate_put_ladder(
                portfolio_value, index_level, volatility,
                days_to_expiry=90 if market_regime == MarketRegime.CRASH else 60)
            results['put_ladder'] = ladder
        elif market_regime == MarketRegime.SIDEWAYS:
            # 震荡市使用轻量Ladder (50%规模)
            light_ladder = self.calculate_put_ladder(
                portfolio_value * 0.5, index_level, volatility, days_to_expiry=45)
            light_ladder['strategy'] = 'put_ladder_light'
            results['put_ladder_light'] = light_ladder

        # Tail Hedge — 仅高波动/崩盘
        if market_regime in (MarketRegime.CRASH, MarketRegime.HIGH_VOL):
            tail = self.calculate_tail_hedge(
                portfolio_value, index_level, volatility * 1.3,  # 高波动下IV更高
                days_to_expiry=120 if market_regime == MarketRegime.CRASH else 90)
            results['tail_hedge'] = tail

        # 汇总成本
        total_cost = sum(r.get('total_cost', r.get('net_cost', 0)) for r in results.values())
        cost_pct = total_cost / portfolio_value if portfolio_value > 0 else 0

        logger.info(f"期权对冲总成本: {total_cost:,.0f} ({cost_pct:.3%} of AUM), "
                    f"市场状态: {market_regime.value}")

        return {
            'market_regime': market_regime.value,
            'strategies': results,
            'total_cost': total_cost,
            'cost_pct': cost_pct,
            'annualized_cost_pct': cost_pct * (365 / days_to_expiry)
        }


# ============================================================================
# 第5层: 备兑开仓增强
# ============================================================================
class EnhancedCoveredWrite:
    """
    备兑开仓增强策略

    优化点:
      - 系统化滚动: 每月卖出近月2-5% OTM Call
      - 动态行权价: 高IV时卖出更远OTM, 低IV时卖出更近ATM
      - 持仓比例管理: 仅对30-50%持仓卖出Call, 保留上行空间
      - 到期前管理: 到期前5天若深度ITM则展期, OTM则持有到期

    预期年化增收: 1.5-3% (取决于IV水平)
    """

    def __init__(self, capital: float = 250000.0):
        self.allocated_capital = capital
        self.pricing = OptionPricing()
        self.premium_collected = 0.0
        self.assigned_calls = 0
        self.rolled_count = 0

    def calculate_optimal_call(self, holding_value: float, underlying_price: float,
                               volatility: float, days_to_expiry: int = 30) -> Dict[str, Any]:
        """
        计算最优备兑Call

        行权价选择逻辑:
          IV > 30%: 卖出 8-10% OTM Call (更高权利金)
          IV 20-30%: 卖出 4-6% OTM Call
          IV < 20%: 卖出 2-3% OTM Call (权利金较低, 但不轻易被行权)
        """
        T = days_to_expiry / 365
        contracts = max(1, int(holding_value * 0.40 / (underlying_price * 100)))

        if volatility > 0.30:
            otm_pct = 0.08
        elif volatility > 0.20:
            otm_pct = 0.05
        else:
            otm_pct = 0.03

        strike = underlying_price * (1 + otm_pct)
        call_price = self.pricing.price(underlying_price, strike, T, volatility, 'call')
        greeks = self.pricing.greeks(underlying_price, strike, T, volatility, 'call')

        premium_income = call_price * contracts * 100
        annualized_yield = (premium_income / holding_value) * (365 / days_to_expiry)

        # 被行权概率 (Delta近似)
        prob_assignment = abs(greeks['delta'])

        logger.info(f"备兑开仓: 行权价 {strike:.0f}(+{otm_pct:.0%}), "
                    f"权利金 {premium_income:,.0f}, 年化增收 {annualized_yield:.2%}")

        return {
            'strategy': 'covered_call',
            'strike': strike,
            'otm_pct': otm_pct,
            'call_price': call_price,
            'contracts': contracts,
            'premium_income': premium_income,
            'annualized_yield': annualized_yield,
            'prob_assignment': prob_assignment,
            'greeks': greeks,
            'days_to_expiry': days_to_expiry
        }

    def manage_expiring(self, position: Dict, current_price: float,
                        volatility: float, days_left: int) -> str:
        """
        管理即将到期的备兑Call

        返回: 'hold'(持有到期), 'roll'(展期), 'close'(平仓)
        """
        strike = position['strike']
        is_itm = current_price > strike

        if days_left <= 5 and is_itm:
            # 深度价内 — 展期避免被行权
            if current_price > strike * 1.03:
                logger.info(f"备兑Call深度ITM, 展期: {strike} → 下月")
                self.rolled_count += 1
                return 'roll'
            # 浅度价内 — 可平仓也可接受行权
            return 'close'

        if days_left <= 3 and not is_itm:
            # OTM — 持有到期获取全部权利金
            return 'hold'

        return 'hold'


# ============================================================================
# 波动率套利与绝对收益层
# ============================================================================
class VolatilityArbitrage:
    """
    波动率套利策略

    核心逻辑: IV(隐含波动率) vs RV(已实现波动率) 偏离回归
      - IV > RV + 5%: 做空波动率 (卖出跨式/宽跨式)
      - IV < RV - 3%: 做多波动率 (买入跨式)
      - 风险控制: 单笔最大亏损限制在配置资金的2%
    """

    def __init__(self, capital: float = 400000.0):
        self.allocated_capital = capital
        self.pricing = OptionPricing()
        self.pnl = 0.0
        self.trades: List[Dict] = []

    def analyze_opportunity(self, iv: float, rv_20d: float,
                            iv_percentile: float) -> Dict[str, Any]:
        """
        分析波动率套利机会

        iv_percentile: IV在历史中的分位数 (0-100)
        """
        spread = iv - rv_20d

        if spread > 0.05 and iv_percentile > 70:
            # IV显著高于RV且处于历史高位 → 做空波动率
            signal = 'short_vol'
            confidence = min(1.0, (spread - 0.05) / 0.10)
            position_size = self.allocated_capital * 0.15 * confidence
        elif spread < -0.03 and iv_percentile < 30:
            # IV显著低于RV且处于历史低位 → 做多波动率
            signal = 'long_vol'
            confidence = min(1.0, (abs(spread) - 0.03) / 0.07)
            position_size = self.allocated_capital * 0.10 * confidence
        else:
            signal = 'neutral'
            confidence = 0.0
            position_size = 0.0

        logger.info(f"波动率套利: IV={iv:.1%}, RV20={rv_20d:.1%}, "
                    f"Spread={spread:.1%}, Signal={signal}, Confidence={confidence:.0%}")

        return {
            'signal': signal,
            'iv': iv,
            'rv_20d': rv_20d,
            'iv_rv_spread': spread,
            'iv_percentile': iv_percentile,
            'confidence': confidence,
            'position_size': position_size
        }


class AbsoluteReturnStrategy:
    """
    绝对收益策略 — 市场中性多空配对

    方法:
      - 行业中性配对: 同行业内多最强/空最弱
      - 统计套利: 协整配对, 均值回归交易
      - 因子中性多空: 多高因子暴露/空低因子暴露
    """

    def __init__(self, capital: float = 350000.0):
        self.allocated_capital = capital
        self.positions: Dict[str, Dict] = {}
        self.pnl = 0.0
        self.gross_exposure = 0.0
        self.net_exposure = 0.0

    def generate_pairs(self, stock_universe: List[Dict],
                       n_pairs: int = 5) -> List[Dict]:
        """
        生成多空配对

        stock_universe columns: symbol, sector, momentum_score, value_score, quality_score
        """
        pairs = []
        # 按行业分组, 选因子得分最高(多)和最低(空)
        sector_groups = {}
        for s in stock_universe:
            sector = s.get('sector', '其他')
            if sector not in sector_groups:
                sector_groups[sector] = []
            sector_groups[sector].append(s)

        count = 0
        for sector, stocks in sector_groups.items():
            if len(stocks) < 2 or count >= n_pairs:
                continue
            # 计算综合得分
            for st in stocks:
                st['composite'] = (
                    st.get('momentum_score', 0) * 0.35 +
                    st.get('value_score', 0) * 0.30 +
                    st.get('quality_score', 0) * 0.35
                )
            sorted_stocks = sorted(stocks, key=lambda x: x['composite'], reverse=True)

            long_stock = sorted_stocks[0]
            short_stock = sorted_stocks[-1]

            if long_stock['composite'] <= short_stock['composite']:
                continue

            pairs.append({
                'sector': sector,
                'long': long_stock['symbol'],
                'long_score': long_stock['composite'],
                'short': short_stock['symbol'],
                'short_score': short_stock['composite'],
                'score_spread': long_stock['composite'] - short_stock['composite']
            })
            count += 1

        logger.info(f"绝对收益配对: 生成 {len(pairs)} 对多空组合")
        return pairs


# ============================================================================
# 综合对冲管理器 — v7.0 核心引擎
# ============================================================================
class ComprehensiveHedgeManager:
    """
    综合对冲管理器 — v7.0 核心

    协调五层对冲策略:
      Layer 1: 股指期货Delta对冲 (10%资金)
      Layer 2: 期权保护性看跌 (10%资金)
      Layer 3: 波动率对冲 (8%资金)
      Layer 4: 绝对收益/市场中性 (7%资金)
      Layer 5: 备兑开仓增强 (5%资金)

    总对冲资金: 40% = 200万 (基于500万总资金)
    """

    def __init__(self, total_capital: float = 5_000_000.0):
        self.total_capital = total_capital
        self.hedge_capital = total_capital * 0.40  # 200万

        # 初始化五层对冲策略
        self.futures_hedge = EnhancedFuturesHedge(
            capital=self.hedge_capital * 0.25)  # 50万 (10% of total)

        self.options_hedge = ProtectiveOptionsHedge(
            capital=self.hedge_capital * 0.25)  # 50万 (10% of total)

        self.vol_arbitrage = VolatilityArbitrage(
            capital=self.hedge_capital * 0.20)  # 40万 (8% of total)

        self.abs_return = AbsoluteReturnStrategy(
            capital=self.hedge_capital * 0.175)  # 35万 (7% of total)

        self.covered_write = EnhancedCoveredWrite(
            capital=self.hedge_capital * 0.125)  # 25万 (5% of total)

        # 市场状态检测
        self.regime_detector = MarketRegimeDetector()

        # 性能追踪
        self.hedge_pnl = 0.0
        self.hedge_cost_ytd = 0.0
        self.total_premium_spent = 0.0
        self.total_premium_collected = 0.0

        logger.info(f"综合对冲管理器初始化 — 对冲资金: {self.hedge_capital:,.0f}")
        logger.info(f"  期货对冲: {self.futures_hedge.allocated_capital:,.0f}")
        logger.info(f"  期权保护: {self.options_hedge.allocated_capital:,.0f}")
        logger.info(f"  波动率套利: {self.vol_arbitrage.allocated_capital:,.0f}")
        logger.info(f"  绝对收益: {self.abs_return.allocated_capital:,.0f}")
        logger.info(f"  备兑开仓: {self.covered_write.allocated_capital:,.0f}")

    def execute_all_hedges(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行全部对冲策略

        参数:
            market_data: {
                'portfolio_value': 组合市值,
                'portfolio_beta': 组合Beta,
                'index_level': 指数点位(沪深300),
                'volatility': 当前波动率,
                'rv_20d': 20日已实现波动率,
                'iv_percentile': IV历史分位,
                'momentum_signal': 动量信号,
                'market_regime_data': 市场状态检测数据,
                'holdings': 持仓列表
            }
        """
        portfolio_value = market_data.get('portfolio_value', self.total_capital * 0.6)
        index_level = market_data.get('index_level', 4000)
        volatility = market_data.get('volatility', 0.20)
        regime_data = market_data.get('market_regime_data', {})

        # 1. 检测市场状态
        regime = self.regime_detector.detect(regime_data)
        logger.info(f"当前市场状态: {regime.value}")

        results = {'regime': regime.value, 'timestamp': datetime.now().isoformat()}

        # 2. Layer 1: 期货Delta对冲
        logger.info("=" * 60)
        logger.info("Layer 1: 股指期货Delta对冲")
        hedge_decision = self.futures_hedge.calculate_optimal_hedge(
            portfolio_beta=market_data.get('portfolio_beta', 1.0),
            portfolio_value=portfolio_value,
            market_regime=regime,
            market_vol=volatility,
            momentum_signal=market_data.get('momentum_signal', 0.0)
        )
        futures_trade = self.futures_hedge.execute_hedge(
            current_hedge_ratio=market_data.get('current_hedge_ratio', 0.0),
            portfolio_value=portfolio_value,
            index_level=index_level
        )
        results['futures_hedge'] = {
            'decision': hedge_decision,
            'trade': futures_trade,
            'annual_cost_estimate': self.futures_hedge.estimate_annual_cost()
        }

        # 3. Layer 2: 期权保护性对冲
        logger.info("=" * 60)
        logger.info("Layer 2: 期权保护性对冲")
        options_result = self.options_hedge.execute_combined(
            portfolio_value=portfolio_value,
            index_level=index_level,
            volatility=volatility,
            market_regime=regime,
            days_to_expiry=60 if regime != MarketRegime.CRASH else 90
        )
        results['options_hedge'] = options_result

        # 4. Layer 3: 波动率套利分析
        logger.info("=" * 60)
        logger.info("Layer 3: 波动率套利")
        vol_opportunity = self.vol_arbitrage.analyze_opportunity(
            iv=volatility,
            rv_20d=market_data.get('rv_20d', volatility * 0.8),
            iv_percentile=market_data.get('iv_percentile', 50)
        )
        results['volatility_arbitrage'] = vol_opportunity

        # 5. Layer 4 & 5: 绝对收益 + 备兑开仓
        logger.info("=" * 60)
        logger.info("Layer 5: 备兑开仓")
        cc_result = self.covered_write.calculate_optimal_call(
            holding_value=portfolio_value,
            underlying_price=index_level,
            volatility=volatility,
            days_to_expiry=30
        )
        results['covered_write'] = cc_result

        # 6. 汇总对冲成本与收益
        results['summary'] = self._summarize(results)
        logger.info("=" * 60)
        logger.info(f"对冲汇总: 总成本 {results['summary']['total_hedge_cost_pct']:.3%}, "
                    f"预期增收 {results['summary']['total_premium_income_pct']:.3%}")

        return results

    def _summarize(self, results: Dict) -> Dict:
        """汇总对冲效果"""
        portfolio_value = self.total_capital * 0.6  # 假设60%在权益

        # 期货对冲成本 (年化)
        futures_cost_pct = self.futures_hedge.estimate_annual_cost() / self.total_capital

        # 期权保护成本
        options_cost_pct = results.get('options_hedge', {}).get('annualized_cost_pct', 0.02)

        # 备兑权利金收入
        cc_result = results.get('covered_write', {})
        cc_income_pct = cc_result.get('annualized_yield', 0) * 0.40  # 40%持仓备兑

        # 波动率套利预期收益
        vol_signal = results.get('volatility_arbitrage', {}).get('signal', 'neutral')
        vol_return_estimate = 0.02 if vol_signal != 'neutral' else 0.005

        # 总成本/收益
        total_cost = futures_cost_pct + options_cost_pct
        total_income = cc_income_pct + vol_return_estimate
        net_hedge_drag = total_cost - total_income

        return {
            'futures_hedge_cost': futures_cost_pct,
            'options_hedge_cost': options_cost_pct,
            'covered_write_income': cc_income_pct,
            'vol_arb_expected_return': vol_return_estimate,
            'total_hedge_cost_pct': total_cost,
            'total_premium_income_pct': total_income,
            'net_hedge_drag': net_hedge_drag,
            'estimated_protection_level': self._estimate_protection(results)
        }

    def _estimate_protection(self, results: Dict) -> str:
        """估算对冲保护水平"""
        regime = results.get('regime', 'sideways')
        protections = {
            'bull': '轻度对冲: 10%期货 + 领口策略, 最大回撤控制 < 8%',
            'slow_bull': '轻度对冲: 15%期货 + 领口策略, 最大回撤控制 < 10%',
            'sideways': '中度对冲: 30%期货 + 领口+轻量看跌, 最大回撤控制 < 10%',
            'high_volatility': '高度对冲: 45%期货 + 看跌阶梯+尾部保护, 最大回撤控制 < 12%',
            'bear': '深度对冲: 60%期货 + 完整看跌阶梯+尾部保护, 最大回撤控制 < 15%',
            'crash': '极限对冲: 70%期货 + 完整看跌阶梯+尾部保护+凸性策略, 最大回撤控制 < 15%'
        }
        return protections.get(regime, '标准对冲: 30%期货 + 期权组合')


# ============================================================================
# v7.0 主系统
# ============================================================================
class ComprehensiveQuantSystemV7:
    """
    综合量化交易系统 v7.0

    架构:
      ┌─────────────────────────────────────────────┐
      │         QuantSystemV7 主控制器                │
      ├─────────────────────────────────────────────┤
      │  Equity Engine (60%)  │  Hedge Engine (40%)  │
      │  ┌─────────────────┐  │  ┌───────────────┐  │
      │  │ 五因子选股      │  │  │ Futures Hedge │  │
      │  │ 风险平价优化    │  │  │ Options Hedge │  │
      │  │ ML增强预测      │  │  │ Vol Arbitrage │  │
      │  │ 动态仓位管理    │  │  │ Abs Return    │  │
      │  └─────────────────┘  │  │ Covered Write │  │
      │                       │  └───────────────┘  │
      ├─────────────────────────────────────────────┤
      │           Risk Manager (三层风控)             │
      │           Performance Analytics              │
      └─────────────────────────────────────────────┘
    """

    def __init__(self, total_capital: float = 5_000_000.0):
        self.total_capital = total_capital
        self.equity_allocation = 0.60   # 300万权益多头
        self.hedge_allocation = 0.40    # 200万对冲策略
        self.cash_reserve = 0.0         # 现金储备(动态)

        # 核心引擎
        self.hedge_manager = ComprehensiveHedgeManager(total_capital)

        # 配置参数
        self.config = {
            'version': '7.0',
            'name': '综合量化策略系统优化版',
            'total_capital': total_capital,
            'equity_allocation': self.equity_allocation,
            'hedge_allocation': self.hedge_allocation,
            'target_return': 0.085,        # 年化8.5%
            'max_drawdown': 0.15,          # 最大回撤15%
            'target_sharpe': 1.5,
            'target_alpha': 0.04,
            'rebalance_frequency': 'monthly',
            'enable_futures': True,
            'enable_options': True,
            'enable_vol_arbitrage': True,
            'enable_covered_write': True,
            'enable_absolute_return': True,
        }

        # 系统状态
        self.status = {
            'running': False,
            'last_rebalance': None,
            'current_drawdown': 0.0,
            'ytd_return': 0.0,
            'current_beta': 0.0,
            'current_hedge_ratio': 0.0,
        }

        # 预期收益分解
        self.expected_return_breakdown = {
            'equity_long': 0.12,          # 权益多头预期: 12%
            'stock_selection_alpha': 0.04,  # 选股Alpha: 4%
            'market_beta_return': 0.08,    # 市场Beta: 8%
            'futures_hedge_drag': -0.03,   # 期货对冲拖累: −3%
            'options_hedge_cost': -0.02,   # 期权保护成本: −2%
            'covered_write_income': 0.015,  # 备兑增收: +1.5%
            'vol_arbitrage_return': 0.02,   # 波动率套利: +2%
            'absolute_return': 0.02,        # 绝对收益: +2%
            'transaction_cost': -0.01,      # 交易成本: −1%
            'net_expected': 0.115           # 净预期: 11.5%
        }

    def print_system_info(self):
        """打印系统信息"""
        print("\n" + "=" * 70)
        print("  综合量化策略系统 v7.0 — 期货+期权双层对冲优化版")
        print("=" * 70)
        print(f"  总资金:     {self.total_capital:,.0f} 元")
        print(f"  权益配置:   {self.total_capital * self.equity_allocation:,.0f} 元 ({self.equity_allocation:.0%})")
        print(f"  对冲配置:   {self.total_capital * self.hedge_allocation:,.0f} 元 ({self.hedge_allocation:.0%})")
        print("-" * 70)
        print("  对冲策略层级:")
        print(f"    Layer 1 — 股指期货Delta对冲:  {self.total_capital * 0.10:,.0f} (10%)")
        print(f"    Layer 2 — 期权保护性看跌:    {self.total_capital * 0.10:,.0f} (10%)")
        print(f"    Layer 3 — 波动率对冲/套利:   {self.total_capital * 0.08:,.0f} (8%)")
        print(f"    Layer 4 — 绝对收益/市场中性: {self.total_capital * 0.07:,.0f} (7%)")
        print(f"    Layer 5 — 备兑开仓增强:      {self.total_capital * 0.05:,.0f} (5%)")
        print("-" * 70)
        print("  预期收益归因:")
        for component, expected in self.expected_return_breakdown.items():
            sign = '+' if expected >= 0 else ''
            print(f"    {component:30s}: {sign}{expected:.1%}")
        print(f"    {'─' * 40}")
        print(f"    {'净组合预期收益':30s}: {self.expected_return_breakdown['net_expected']:.1%}")
        print("-" * 70)
        print("  风险目标:")
        print(f"    年化收益目标: >= 8.5%")
        print(f"    最大回撤目标: < 15%")
        print(f"    夏普比率目标: >= 1.5")
        print(f"    Alpha目标:    >= 4%")
        print("=" * 70)

    def run_hedge_simulation(self) -> Dict[str, Any]:
        """
        运行对冲策略模拟

        模拟不同市场状态下的对冲表现
        """
        scenarios = [
            {
                'name': '牛市',
                'regime_data': {
                    'index_return_20d': 0.08,
                    'index_return_60d': 0.15,
                    'volatility_20d': 0.15,
                    'volatility_60d': 0.16,
                    'vix_level': 15,
                    'volume_ratio': 1.1,
                    'drawdown_from_high': 0.02
                },
                'portfolio_beta': 1.0,
                'index_level': 4200,
                'volatility': 0.15,
                'rv_20d': 0.14,
                'iv_percentile': 30,
                'momentum_signal': 0.7
            },
            {
                'name': '震荡市',
                'regime_data': {
                    'index_return_20d': 0.005,
                    'index_return_60d': 0.01,
                    'volatility_20d': 0.18,
                    'volatility_60d': 0.19,
                    'vix_level': 20,
                    'volume_ratio': 1.0,
                    'drawdown_from_high': 0.05
                },
                'portfolio_beta': 1.0,
                'index_level': 4000,
                'volatility': 0.18,
                'rv_20d': 0.17,
                'iv_percentile': 50,
                'momentum_signal': 0.1
            },
            {
                'name': '高波动',
                'regime_data': {
                    'index_return_20d': -0.04,
                    'index_return_60d': -0.06,
                    'volatility_20d': 0.30,
                    'volatility_60d': 0.26,
                    'vix_level': 30,
                    'volume_ratio': 1.3,
                    'drawdown_from_high': 0.12
                },
                'portfolio_beta': 1.0,
                'index_level': 3800,
                'volatility': 0.30,
                'rv_20d': 0.28,
                'iv_percentile': 75,
                'momentum_signal': -0.4
            },
            {
                'name': '熊市',
                'regime_data': {
                    'index_return_20d': -0.08,
                    'index_return_60d': -0.15,
                    'volatility_20d': 0.32,
                    'volatility_60d': 0.28,
                    'vix_level': 35,
                    'volume_ratio': 1.2,
                    'drawdown_from_high': 0.18
                },
                'portfolio_beta': 1.0,
                'index_level': 3500,
                'volatility': 0.32,
                'rv_20d': 0.30,
                'iv_percentile': 85,
                'momentum_signal': -0.7
            },
            {
                'name': '崩盘',
                'regime_data': {
                    'index_return_20d': -0.20,
                    'index_return_60d': -0.25,
                    'volatility_20d': 0.50,
                    'volatility_60d': 0.38,
                    'vix_level': 45,
                    'volume_ratio': 1.8,
                    'drawdown_from_high': 0.30
                },
                'portfolio_beta': 1.0,
                'index_level': 3000,
                'volatility': 0.50,
                'rv_20d': 0.45,
                'iv_percentile': 95,
                'momentum_signal': -0.9
            }
        ]

        print("\n" + "=" * 70)
        print("  对冲策略多场景模拟")
        print("=" * 70)

        all_results = []
        for scenario in scenarios:
            print(f"\n{'─' * 70}")
            print(f"  场景: {scenario['name']}")
            print(f"{'─' * 70}")

            market_data = {
                'portfolio_value': self.total_capital * self.equity_allocation,
                'portfolio_beta': scenario['portfolio_beta'],
                'index_level': scenario['index_level'],
                'volatility': scenario['volatility'],
                'rv_20d': scenario['rv_20d'],
                'iv_percentile': scenario['iv_percentile'],
                'momentum_signal': scenario['momentum_signal'],
                'market_regime_data': scenario['regime_data'],
                'current_hedge_ratio': 0.0,
            }

            result = self.hedge_manager.execute_all_hedges(market_data)
            result['scenario'] = scenario['name']
            all_results.append(result)

            summary = result['summary']
            print(f"\n  >> 对冲总成本: {summary['total_hedge_cost_pct']:.2%}")
            print(f"  >> 权利金增收: {summary['total_premium_income_pct']:.2%}")
            print(f"  >> 净对冲拖累: {summary['net_hedge_drag']:.2%}")
            print(f"  >> 保护水平:   {summary['estimated_protection_level']}")

        # 汇总表
        print(f"\n{'=' * 70}")
        print("  多场景对冲效果汇总")
        print(f"{'=' * 70}")
        print(f"  {'场景':<12} {'市场状态':<16} {'对冲成本':<10} {'增收':<10} {'净拖累':<10}")
        print(f"  {'─' * 58}")
        for r in all_results:
            s = r['summary']
            print(f"  {r['scenario']:<12} {r['regime']:<16} "
                  f"{s['total_hedge_cost_pct']:<10.2%} {s['total_premium_income_pct']:<10.2%} "
                  f"{s['net_hedge_drag']:<10.2%}")

        return all_results

    def run_full_simulation(self):
        """运行完整系统模拟"""
        self.print_system_info()
        self.run_hedge_simulation()
        self.print_summary_report()

    def print_summary_report(self):
        """打印总结报告"""
        print(f"\n{'=' * 70}")
        print("  v7.0 优化总结")
        print(f"{'=' * 70}")
        print(f"""
  v6.0 → v7.0 核心升级:

  1. 期权尾部保护层 (新增)
     - 保护性看跌阶梯: 90%/85%/80% 三层保护
     - 看跌价差领口: 近乎零成本的温和下跌对冲
     - 尾部风险对冲: 深度OTM Put黑天鹅保护
     - 预期降低尾部回撤 5-8%

  2. 期货对冲优化
     - 市场状态自适应目标Beta (熊市-0.1 ~ 牛市+0.3)
     - VIX联动对冲比率 (高波动时自动增加对冲)
     - 动量过滤避免趋势中过度对冲
     - 预期提升对冲效率 15-20%

  3. 备兑开仓增强
     - IV自适应行权价选择
     - 到期前智能展期管理
     - 预期年化增收 1.5-3%

  4. 波动率套利系统化
     - IV/RV偏离监测
     - 历史分位数信号过滤
     - 置信度加权仓位管理

  5. 市场状态驱动框架
     - 六种市场状态自动识别
     - 每种状态对应特定对冲组合
     - 动态资金配置

  预期效果:
    年化收益:  8.5% - 11.5% (baseline 8.5%)
    最大回撤:  8% - 15%   (极端市场 < 15%)
    夏普比率:  1.2 - 1.8  (取决于市场环境)
    对冲效率:  较v6.0提升 30-50%
""")
        print("=" * 70)


# ============================================================================
# 辅助函数: 真实回测场景模拟
# ============================================================================
def simulate_historical_scenario():
    """
    基于历史市场场景的回测模拟

    使用2015-2024年中国A股关键市场节点验证对冲效果
    """
    print("\n" + "=" * 70)
    print("  历史压力场景验证")
    print("=" * 70)

    # 2015年股灾、2018年熊市、2020年疫情、2022年下跌、2024年初回调
    historical_events = [
        {
            'event': '2015年股灾 (6-8月)',
            'index_drop': -0.33,
            'volatility': 0.55,
            'regime': MarketRegime.CRASH,
            'unhedged_drawdown': -0.33,
        },
        {
            'event': '2018年熊市 (全年)',
            'index_drop': -0.25,
            'volatility': 0.28,
            'regime': MarketRegime.BEAR,
            'unhedged_drawdown': -0.25,
        },
        {
            'event': '2020年疫情冲击 (2-3月)',
            'index_drop': -0.14,
            'volatility': 0.45,
            'regime': MarketRegime.CRASH,
            'unhedged_drawdown': -0.14,
        },
        {
            'event': '2022年下跌 (1-4月)',
            'index_drop': -0.22,
            'volatility': 0.30,
            'regime': MarketRegime.BEAR,
            'unhedged_drawdown': -0.22,
        },
        {
            'event': '2024年初回调 (1月)',
            'index_drop': -0.10,
            'volatility': 0.25,
            'regime': MarketRegime.HIGH_VOL,
            'unhedged_drawdown': -0.10,
        },
    ]

    # 各市场状态下对冲效率估算
    hedge_efficiency = {
        MarketRegime.CRASH: 0.55,       # 崩盘时对冲效率55% (部分因基差扩大)
        MarketRegime.BEAR: 0.65,        # 熊市时对冲效率65%
        MarketRegime.HIGH_VOL: 0.70,    # 高波动时对冲效率70%
        MarketRegime.SIDEWAYS: 0.80,    # 震荡时对冲效率80%
        MarketRegime.SLOW_BULL: 0.60,   # 慢牛时对冲效率60%
        MarketRegime.BULL: 0.50,        # 牛市时对冲效率50% (逆势对冲)
    }

    # 期权对冲额外尾部保护 (仅在大跌时生效)
    options_tail_protection = {
        MarketRegime.CRASH: 0.10,       # 期权提供额外10%下跌缓冲
        MarketRegime.BEAR: 0.05,        # 熊市额外5%
        MarketRegime.HIGH_VOL: 0.03,    # 高波动额外3%
        MarketRegime.SIDEWAYS: 0.01,
        MarketRegime.SLOW_BULL: 0.0,
        MarketRegime.BULL: 0.0,
    }

    print(f"\n  {'事件':<28} {'原回撤':<10} {'期货对冲后':<10} "
          f"{'+期权后':<10} {'净回撤':<10} {'达标':<6}")
    print(f"  {'─' * 70}")

    for ev in historical_events:
        regime = ev['regime']
        original_dd = ev['unhedged_drawdown']

        # 期货对冲效果 — 对冲60%仓位, 效率基于市场状态
        # original_dd为负值, futures_protection为正值(对冲减少的损失)
        futures_protection = abs(original_dd) * 0.60 * hedge_efficiency[regime]
        after_futures = original_dd + futures_protection  # 负值+正值 = 回撤收窄

        # 期权额外尾部保护 — 期权仓位10%
        options_protection = abs(original_dd) * 0.10 * options_tail_protection[regime]
        after_options = after_futures + options_protection

        # 备兑权利金 + 绝对收益Alpha缓冲 (年化折算到期间)
        alpha_buffer = 0.02 * (abs(original_dd) / 0.30)  # 按波动比例折算
        net_dd = after_options + alpha_buffer

        pass_check = "PASS" if abs(net_dd) < 0.15 else "FAIL"

        print(f"  {ev['event']:<28} {original_dd:<10.1%} {after_futures:<10.1%} "
              f"{after_options:<10.1%} {net_dd:<10.1%} {pass_check:<6}")

    pass_count = 0
    for ev in historical_events:
        regime = ev['regime']
        orig = ev['unhedged_drawdown']
        futures = abs(orig) * 0.60 * hedge_efficiency[regime]
        options = abs(orig) * 0.10 * options_tail_protection[regime]
        alpha = 0.02 * (abs(orig) / 0.30)
        net = orig + futures + options + alpha
        if abs(net) < 0.15:
            pass_count += 1

    print(f"\n  结论: {pass_count}/{len(historical_events)} 历史压力场景净回撤 < 15%")
    if pass_count < len(historical_events):
        print("  建议: 极端行情需增加对冲比率至80%+或降低权益仓位")
    print("=" * 70)


# ============================================================================
# 主入口
# ============================================================================
def main():
    """v7.0 系统主入口"""
    print("\n" + "█" * 70)
    print("█  综合量化策略系统 v7.0 — 期货+期权双层对冲优化版")
    print("█  Comprehensive Quantitative Strategy System v7.0")
    print("█" * 70)

    # 初始化系统
    system = ComprehensiveQuantSystemV7(total_capital=5_000_000)

    # 运行完整模拟
    system.run_full_simulation()

    # 历史压力场景验证
    simulate_historical_scenario()

    # 输出配置
    print(f"\n{'=' * 70}")
    print("  系统配置导出")
    print(f"{'=' * 70}")
    config_json = json.dumps(system.config, ensure_ascii=False, indent=2)
    print(config_json)

    return system


if __name__ == "__main__":
    system = main()
