"""
Dynamic Target Calculator for Backtest Validation
=================================================

根据市场状态动态调整目标收益率，防止固定阈值导致的回测操纵。

核心逻辑：
1. 根据市场波动率动态调整目标夏普比率阈值
2. 根据样本量大小调整统计显著性要求
3. 根据策略类型（高频/日频/周频）调整预期范围
"""

import math
from typing import Tuple


class DynamicTargetCalculator:
    """动态目标收益率计算器"""

    def __init__(self, strategy_type: str = "daily", market_volatility: float = 0.20):
        """
        初始化计算器

        Args:
            strategy_type: 策略类型 ('high_frequency', 'daily', 'weekly')
            market_volatility: 当前市场波动率（年化）
        """
        self.strategy_type = strategy_type
        self.market_volatility = market_volatility

        # 基准参数（日频策略，市场波动率20%）
        self._base_sharpe_range = (0.5, 2.0)
        self._base_sample_size = 252  # 一年交易日

        # 根据策略类型调整参数
        if strategy_type == "high_frequency":
            self._base_sharpe_range = (0.3, 1.5)  # 高频策略夏普通常较低
            self._base_sample_size = 504  # 高频数据点更多
        elif strategy_type == "weekly":
            self._base_sharpe_range = (0.8, 2.5)  # 周频策略信号更少但更稳定
            self._base_sample_size = 52  # 一年52周

        # 根据市场波动率调整夏普阈值
        vol_adjustment = 0.20 / max(market_volatility, 0.01)  # 避免除零
        adjusted_min_sharpe = self._base_sharpe_range[0] * vol_adjustment
        adjusted_max_sharpe = self._base_sharpe_range[1] * vol_adjustment

        self._min_acceptable_sharpe = max(adjusted_min_sharpe, 0.1)  # 至少要有0.1
        self._expected_sharpe_range = (adjusted_min_sharpe, adjusted_max_sharpe)

    def calculate_target(self, sample_size: int) -> Tuple[float, float]:
        """
        计算动态目标夏普比率范围

        Args:
            sample_size: 样本数量

        Returns:
            (lower_bound, upper_bound): 动态目标夏普比率范围
        """
        # 样本量调整因子：样本越少，要求越高（避免小样本过拟合）
        sample_adjustment = math.sqrt(sample_size / self._base_sample_size)

        # 调整后的范围
        lower = self._expected_sharpe_range[0] * min(sample_adjustment, 1.5)  # 上限1.5倍
        upper = self._expected_sharpe_range[1] * max(sample_adjustment, 0.5)  # 下限0.5倍

        return (round(lower, 2), round(upper, 2))

    def is_acceptable(self, sharpe_ratio: float, sample_size: int) -> bool:
        """
        判断夏普比率是否在可接受范围内

        Args:
            sharpe_ratio: 策略夏普比率
            sample_size: 样本数量

        Returns:
            True表示在可接受范围内
        """
        lower, upper = self.calculate_target(sample_size)
        return lower <= sharpe_ratio <= upper

    def get_expected_range(self) -> Tuple[float, float]:
        """获取当前市场条件下的预期夏普比率范围"""
        return self._expected_sharpe_range

    def get_min_acceptable_sharpe(self) -> float:
        """获取最低可接受的夏普比率"""
        return self._min_acceptable_sharpe
