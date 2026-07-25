# -*- coding: utf-8 -*-
"""CapacityAnalyzer - E1 容量分析（CIO 视角 v1.0）

估算因子可承载的美元容量。锁定参数（DECISION v1.0）：
  - 参与率上限：5% ADV
  - 容量阈值：>= 组合价值 2%
  - 换手率惩罚：1 - min(turnover, 0.5)
  - 公式：capacity_usd = ADV_median * participation_cap(5%) * (1 - turnover_penalty)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("capacity_analyzer")


# ============== CIO 锁定参数（DECISION v1.0）==============
PARTICIPATION_CAP = 0.05          # 5% ADV 参与率上限
CAPACITY_RATIO_THRESHOLD = 0.02   # 容量 >= 组合价值 2%
TURNOVER_PENALTY_CAP = 0.5       # 换手率惩罚上限


@dataclass
class CapacityResult:
    """容量分析结果"""
    factor_name: str
    capacity_usd: float = 0.0
    capacity_ratio: float = 0.0          # 容量 / 组合价值
    participation_ratio: float = 0.0     # 实际参与率
    adv_median_usd: float = 0.0
    turnover_penalty: float = 0.0
    n_universe: int = 0
    pass_capacity: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CapacityAnalyzer:
    """容量分析器（E1）

    用法：
        >>> analyzer = CapacityAnalyzer()
        >>> result = analyzer.analyze(
        ...     factor_values={"S001": 0.5, "S002": -0.3, ...},
        ...     adv_data={"S001": 1e7, "S002": 5e6, ...},  # 美元 ADV
        ...     turnover=0.35,
        ...     portfolio_value=1e8,
        ...     factor_name="VT_MOM_ILLIQUID_60D",
        ... )
        >>> if result.pass_capacity:
        ...     # 容量达标
        ...     pass
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        c = config or {}
        self.participation_cap = float(c.get("participation_cap", PARTICIPATION_CAP))
        self.capacity_ratio_threshold = float(
            c.get("capacity_ratio_threshold", CAPACITY_RATIO_THRESHOLD)
        )
        self.turnover_penalty_cap = float(c.get("turnover_penalty_cap", TURNOVER_PENALTY_CAP))
        logger.info(
            "[CapacityAnalyzer] 初始化 | participation=%.0f%% capacity_ratio>=%.1f%%",
            self.participation_cap * 100, self.capacity_ratio_threshold * 100,
        )

    def analyze(
        self,
        factor_values: Dict[str, float],
        adv_data: Dict[str, float],
        turnover: float,
        portfolio_value: float,
        factor_name: str = "candidate",
    ) -> CapacityResult:
        """分析因子容量

        Args:
            factor_values: 因子值 {symbol: factor_value}
            adv_data: 每个标的的美元 ADV {symbol: adv_usd}
            turnover: 因子年换手率（0-1）
            portfolio_value: 组合总价值（美元）
            factor_name: 因子名称

        Returns:
            CapacityResult
        """
        r = CapacityResult(factor_name=factor_name)

        # 数据校验
        if portfolio_value <= 0:
            r.reason = "portfolio_value <= 0"
            return r

        common = [s for s in factor_values if s in adv_data and adv_data[s] > 0]
        if not common:
            r.reason = "无共同标的（factor_values 与 adv_data 不匹配）"
            return r

        r.n_universe = len(common)

        # ADV 中位数（美元）
        advs = np.array([adv_data[s] for s in common], dtype=float)
        r.adv_median_usd = float(np.median(advs))

        # 换手率惩罚：1 - min(turnover, 0.5)
        turnover = max(0.0, min(1.0, float(turnover)))
        r.turnover_penalty = min(turnover, self.turnover_penalty_cap)
        turnover_factor = 1.0 - r.turnover_penalty

        # 单标的参与率：5% ADV
        r.participation_ratio = self.participation_cap

        # 单标的容量：ADV * 5% * (1 - turnover_penalty)
        per_symbol_capacity = r.adv_median_usd * self.participation_cap * turnover_factor

        # 因子总容量：单标的容量 * 标的数（保守估计，假设均匀分布）
        r.capacity_usd = per_symbol_capacity * r.n_universe

        # 容量 / 组合价值
        r.capacity_ratio = float(r.capacity_usd / portfolio_value)

        # 准入判定
        if r.capacity_ratio >= self.capacity_ratio_threshold:
            r.pass_capacity = True
            r.reason = "pass"
        else:
            r.reason = (
                f"capacity_ratio={r.capacity_ratio:.4f} < {self.capacity_ratio_threshold}"
                f"（容量 {r.capacity_usd/1e6:.2f}M < 组合 {portfolio_value/1e6:.2f}M × {self.capacity_ratio_threshold:.0%}）"
            )

        logger.info(
            "[CapacityAnalyzer] %s | adv_med=%.2fM turnover_pen=%.2f cap=%.2fM ratio=%.4f %s",
            factor_name, r.adv_median_usd / 1e6, r.turnover_penalty,
            r.capacity_usd / 1e6, r.capacity_ratio,
            "✓" if r.pass_capacity else "✗",
        )
        return r


def quick_analyze(
    factor_values: Dict[str, float],
    adv_data: Dict[str, float],
    turnover: float,
    portfolio_value: float,
    factor_name: str = "candidate",
) -> CapacityResult:
    """快速容量分析（使用默认配置）"""
    analyzer = CapacityAnalyzer()
    return analyzer.analyze(factor_values, adv_data, turnover, portfolio_value, factor_name)
