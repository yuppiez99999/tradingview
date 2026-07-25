# -*- coding: utf-8 -*-
"""
v7.5 相关性对冲引擎 —— 平均相关系数监控 + 避险资产配置

数学基础:
    平均非对角相关:
        ρ̄_t = (2 / (N(N-1))) Σ_{i<j} ρ_{ij,t}

    触发条件:
        ρ̄_t > 0.85  且  较 60 日均值上升 > 0.15

    避险权重:
        w_gold = 0.10 × min(1, (ρ̄_t - 0.85) / 0.15)
        w_repo = max(0, 0.30 - w_gold)
"""
from __future__ import annotations

import logging
from typing import Dict

import numpy as np
import pandas as pd

logger = logging.getLogger("v75.hedging.corr")


class CorrelationHedger:
    """相关性对冲器: 检测相关性趋同并配置避险资产"""

    def __init__(self,
                 corr_trigger: float = 0.85,
                 corr_window: int = 30,
                 corr_baseline_window: int = 60,
                 corr_jump_threshold: float = 0.15,
                 max_gold_weight: float = 0.10,
                 max_repo_weight: float = 0.30,
                 gold_etf: str = "518880",
                 repo_symbol: str = "GC001"):
        """
        Args:
            corr_trigger: 触发阈值
            corr_window: 滚动相关系数窗口
            corr_baseline_window: 基线均值窗口
            corr_jump_threshold: 较基线上升阈值
            max_gold_weight: 黄金 ETF 最大权重
            max_repo_weight: 逆回购最大权重
            gold_etf: 黄金 ETF 代码
            repo_symbol: 国债逆回购代码
        """
        self.corr_trigger = float(corr_trigger)
        self.corr_window = int(corr_window)
        self.corr_baseline_window = int(corr_baseline_window)
        self.corr_jump = float(corr_jump_threshold)
        self.max_gold = float(max_gold_weight)
        self.max_repo = float(max_repo_weight)
        self.gold_etf = gold_etf
        self.repo_symbol = repo_symbol

    def average_correlation(self, returns: pd.DataFrame) -> float:
        """计算平均非对角相关系数

        Args:
            returns: T×N 收益率 DataFrame

        Returns:
            平均相关系数 ρ̄
        """
        if returns.empty or returns.shape[1] < 2:
            return 0.0

        recent = returns.tail(self.corr_window)
        if len(recent) < 10:
            return 0.0

        corr_mat = recent.corr().values
        n = corr_mat.shape[0]
        if n < 2:
            return 0.0

        # 提取上三角 (排除对角线)
        upper_mask = np.triu(np.ones_like(corr_mat, dtype=bool), k=1)
        upper_corr = corr_mat[upper_mask]
        if len(upper_corr) == 0:
            return 0.0

        # 处理 NaN
        upper_corr = upper_corr[~np.isnan(upper_corr)]
        if len(upper_corr) == 0:
            return 0.0

        return float(np.mean(np.abs(upper_corr)))

    def baseline_correlation(self, returns: pd.DataFrame) -> float:
        """计算基线平均相关 (60 日均值)"""
        if returns.empty or returns.shape[1] < 2:
            return 0.0
        baseline = returns.tail(self.corr_baseline_window)
        if len(baseline) < 20:
            return self.average_correlation(returns)
        return self.average_correlation(baseline)

    def compute_hedge(self,
                      returns: pd.DataFrame,
                      portfolio_value: float) -> Dict[str, object]:
        """计算相关性对冲指令

        Args:
            returns: 历史收益率 DataFrame
            portfolio_value: 组合市值

        Returns:
            对冲指令字典
        """
        avg_corr = self.average_correlation(returns)
        baseline = self.baseline_correlation(returns)
        jump = avg_corr - baseline

        # 触发条件：
        #   1) 正常跳跃触发: avg_corr > 阈值 且 jump > 跳跃阈值
        #   2) 极端高相关强制触发: avg_corr > 0.95 (相关性趋同已到极致，
        #      无论是否跳跃都需配置避险资产)
        triggered = ((avg_corr > self.corr_trigger
                      and jump > self.corr_jump)
                     or avg_corr > 0.95)

        if not triggered:
            return {"action": "NO_HEDGE",
                    "reason": (f"ρ̄={avg_corr:.3f}, 基线={baseline:.3f}, "
                               f"跳跃={jump:.3f} 未达触发条件"),
                    "avg_corr": avg_corr,
                    "baseline_corr": baseline,
                    "jump": jump}

        # 避险权重
        w_gold = self.max_gold * min(1.0,
                                      (avg_corr - self.corr_trigger) / 0.15)
        w_repo = max(0.0, self.max_repo - w_gold)

        gold_value = w_gold * portfolio_value
        repo_value = w_repo * portfolio_value

        result = {
            "action": "SAFE_HAVEN_ALLOC",
            "avg_corr": float(avg_corr),
            "baseline_corr": float(baseline),
            "jump": float(jump),
            "gold_etf": self.gold_etf,
            "gold_weight": float(w_gold),
            "gold_value": float(gold_value),
            "repo_symbol": self.repo_symbol,
            "repo_weight": float(w_repo),
            "repo_value": float(repo_value),
            "portfolio_value": float(portfolio_value),
        }

        logger.warning("相关性对冲触发: ρ̄=%.3f (基线 %.3f, 跳跃 +%.3f), "
                       "黄金 ETF %.2f%%, 逆回购 %.2f%%",
                       avg_corr, baseline, jump, w_gold * 100, w_repo * 100)
        return result
