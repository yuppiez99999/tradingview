# -*- coding: utf-8 -*-
"""
v7.5 风控管理器 —— 三级回撤 + 风险预算 + 对冲联动

整合 RiskBudgeter (风险预算) 与 AutoHedger (三联对冲)，
作为整个 v7.5 系统的风险中枢。
"""

import numpy as np
import pandas as pd
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from .risk_budgeter import RiskBudgeter

logger = logging.getLogger('v7.5.risk_manager')


class RiskManager:
    """v7.5 风控管理器：三级回撤 + Kelly + RP + 对冲联动"""

    def __init__(self,
                 total_capital: float = 5_000_000,
                 target_return: float = 0.08,
                 max_dd: float = 0.15,
                 single_trade_risk: float = 0.015,
                 kelly_tau2: float = 0.01,
                 rf: float = 0.02,
                 mu_market: float = 0.08):
        self.capital = total_capital
        self.target = target_return
        self.max_dd = max_dd
        self.single_risk = single_trade_risk

        # 风险预算器
        self.budgeter = RiskBudgeter(
            total_capital=total_capital,
            target_return=target_return,
            max_dd=max_dd,
            single_trade_risk=single_trade_risk,
            kelly_tau2=kelly_tau2,
            rf=rf,
            mu_market=mu_market,
        )

        # 对冲协调器（延迟引用避免循环导入）
        self.hedger = None

        # 事件日志
        self.event_log = deque(maxlen=500)

        logger.info(f"RiskManager v7.5 初始化完成, 资本: {total_capital:,.0f}")

    def set_hedger(self, hedger):
        """注入对冲协调器"""
        self.hedger = hedger

    # ============================================================
    # 回撤更新
    # ============================================================

    def update_drawdown(self, equity: float,
                        ts: Optional[datetime] = None) -> str:
        """更新回撤，返回当前模式"""
        return self.budgeter.update_drawdown(equity, ts)

    @property
    def mode(self) -> str:
        return self.budgeter.mode

    @property
    def position_multiplier(self) -> float:
        return self.budgeter.position_multiplier

    # 兼容别名：测试用 position_size_factor
    @property
    def position_size_factor(self) -> float:
        return self.budgeter.position_multiplier

    @property
    def circuit_break_until(self):
        return self.budgeter.circuit_break_until

    # ============================================================
    # Kelly / Risk Parity (委托给 budgeter)
    # ============================================================

    def kelly_weight(self,
                     symbol: str,
                     mu_hist: float,
                     sigma: float,
                     beta: float = 1.0,
                     n: int = 252) -> float:
        """单标的 Kelly 仓位 (兼容旧 API: 接受 symbol 首参)"""
        return self.budgeter.kelly_weight(mu_hist, sigma, beta, n)

    def risk_parity_weights(self, returns: pd.DataFrame) -> np.ndarray:
        """Risk Parity 权重"""
        return self.budgeter.risk_parity_weights(returns)

    # ============================================================
    # 仓位计算
    # ============================================================

    def size_position(self,
                      mu_hist: float,
                      sigma: float,
                      beta: float,
                      n: int = 252) -> float:
        """计算单标的 Kelly 仓位权重"""
        return self.budgeter.kelly_weight(mu_hist, sigma, beta, n)

    def size_portfolio(self,
                       symbols: List[str],
                       mu_hist: Dict[str, float],
                       sigma: Dict[str, float],
                       beta: Dict[str, float],
                       returns: pd.DataFrame,
                       kelly_weight: float = 0.6) -> Dict[str, float]:
        """综合 Kelly + RP 计算全组合权重"""
        return self.budgeter.size_positions(
            symbols, mu_hist, sigma, beta, returns,
            kelly_weight=kelly_weight,
            rp_weight=1.0 - kelly_weight
        )

    # ============================================================
    # 自动对冲触发
    # ============================================================

    def auto_hedge(self,
                   portfolio_beta: float,
                   vix_level: float,
                   avg_correlation: float = 0.5,
                   portfolio_value: Optional[float] = None) -> List[Dict]:
        """三联对冲触发：Beta + VIX + Correlation"""
        actions = []
        V = portfolio_value or self.capital

        # --- Beta 对冲 ---
        if portfolio_beta > 0.7:
            target_beta = 0.3
            hedge_ratio = (portfolio_beta - target_beta) / portfolio_beta
            actions.append({
                'type': 'BETA_HEDGE',
                'instrument': 'IF_futures',
                'ratio': round(hedge_ratio, 4),
                'target_beta': target_beta,
                'urgency': 'high' if portfolio_beta > 1.0 else 'medium',
            })

        # --- 波动率对冲 ---
        if vix_level > 30:
            if vix_level <= 40:
                actions.append({
                    'type': 'PUT_SPREAD',
                    'budget': V * 0.003,
                    'vix_level': vix_level,
                })
            elif vix_level <= 60:
                actions.append({
                    'type': 'BARE_PUT',
                    'budget': V * 0.005,
                    'delta_target': -0.2,
                    'vix_level': vix_level,
                })
            else:
                coverage = max(0.015, 1 - (vix_level - 40) / 50)
                actions.append({
                    'type': 'EMERGENCY_PUT',
                    'budget': V * 0.008,
                    'coverage': round(coverage, 4),
                    'vix_level': vix_level,
                })

        # --- 相关性对冲 ---
        if avg_correlation > 0.85:
            w_gold = 0.10 * min(1.0, (avg_correlation - 0.85) / 0.15)
            w_repo = max(0, 0.30 - w_gold)
            actions.append({
                'type': 'SAFE_HAVEN',
                'gold_etf_weight': round(w_gold, 4),
                'reverse_repo_weight': round(w_repo, 4),
            })

        return actions

    def execute_hedge_actions(self, actions: List[Dict]) -> List[Dict]:
        """执行对冲动作（委托给 Hedger）"""
        if self.hedger is None:
            logger.warning("对冲协调器未注入，跳过对冲执行")
            return []
        results = []
        for action in actions:
            try:
                result = self.hedger.execute(action)
                results.append(result)
                logger.info(f"对冲执行: {action['type']} -> {result.get('status', 'unknown')}")
            except Exception as e:
                logger.error(f"对冲执行失败 [{action['type']}]: {e}")
                results.append({'type': action['type'], 'status': 'failed', 'error': str(e)})
        return results

    # ============================================================
    # 风控全周期
    # ============================================================

    def run_risk_cycle(self,
                       equity: float,
                       portfolio_beta: float,
                       vix_level: float,
                       avg_correlation: float,
                       positions: Dict[str, float],
                       returns: pd.DataFrame,
                       ts: Optional[datetime] = None) -> Dict:
        """执行完整风控周期"""

        # 1. 回撤更新
        mode = self.update_drawdown(equity, ts)

        # 2. 风险预算检查
        budget = self.budgeter.check_budget(positions, returns)

        # 3. 对冲触发
        hedge_actions = []
        if mode not in ("CIRCUIT_BREAKER", "CIRCUIT_BREAK_ACTIVE"):
            hedge_actions = self.auto_hedge(
                portfolio_beta, vix_level, avg_correlation, equity
            )

        # 4. 生成决策
        decision = {
            'mode': mode,
            'current_dd': self.budgeter.current_dd,
            'position_multiplier': self.position_multiplier,
            'allow_new_positions': self.budgeter.allow_new_positions,
            'portfolio_vol': budget.get('portfolio_vol', 0),
            'hedge_actions': hedge_actions,
            'timestamp': (ts or datetime.now()).isoformat(),
        }

        # 5. 执行对冲
        if hedge_actions and mode not in ("CIRCUIT_BREAKER", "CIRCUIT_BREAK_ACTIVE"):
            hedge_results = self.execute_hedge_actions(hedge_actions)
            decision['hedge_results'] = hedge_results

        self.event_log.append(decision)
        return decision

    def get_status(self) -> Dict:
        return {
            **self.budgeter.get_status(),
            'event_count': len(self.event_log),
        }


# 向后兼容别名
RiskBudgeter = RiskBudgeter
