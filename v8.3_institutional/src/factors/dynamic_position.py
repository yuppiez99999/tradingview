# -*- coding: utf-8 -*-
"""动态仓位管理器 — 基于波动率与回撤自适应调仓 (v5.2 新增)

核心逻辑：
1. 计算组合已实现波动率（近20日收益率标准差 × √252）
2. 波动率越高，仓位越低（反比关系）
3. 回撤越大，仓位越低（线性惩罚）
4. 结合原有三级风控，输出最终仓位比例
"""
from __future__ import annotations

import time
import math
import numpy as np
from typing import Optional, List, Dict
from copy import deepcopy


class DynamicPositionManager:
    """动态仓位管理器 — 基于波动率与回撤自适应调仓

    参数:
    - target_vol: 目标年化波动率（默认15%）
    - max_vol: 最大容忍波动率（默认25%）
    - vol_lookback: 波动率计算回溯期（默认20日）
    - base_position: 基准仓位（默认0.95，即95%）
    - min_position: 最低仓位（默认0.3，即30%）
    """

    def __init__(
        self,
        target_vol: float = 0.15,
        max_vol: float = 0.25,
        vol_lookback: int = 20,
        base_position: float = 0.95,
        min_position: float = 0.3,
    ):
        self.target_vol = target_vol
        self.max_vol = max_vol
        self.vol_lookback = vol_lookback
        self.base_position = base_position
        self.min_position = min_position
        self._position_history: List[Dict] = []

    def calculate_volatility(self, returns_series) -> float:
        """计算年化已实现波动率

        Args:
            returns_series: pandas Series of daily returns or numpy array

        Returns:
            年化波动率百分比 (e.g. 18.5)
        """
        if returns_series is None or len(returns_series) < 5:
            return self.target_vol * 100  # 数据不足，返回目标波动率

        # 兼容 numpy.ndarray 和 pandas.Series
        if hasattr(returns_series, 'tail'):
            recent = returns_series.tail(self.vol_lookback)
        else:
            recent = returns_series[-self.vol_lookback:]

        daily_std = np.std(recent, ddof=1)
        if np.isnan(daily_std) or daily_std == 0:
            return self.target_vol * 100

        annualized_vol = daily_std * math.sqrt(252) * 100
        return round(annualized_vol, 2)

    def calculate_drawdown(self, equity_curve) -> float:
        """计算当前回撤

        Args:
            equity_curve: pandas Series of equity values or numpy array

        Returns:
            当前回撤百分比 (e.g. -8.5)
        """
        if equity_curve is None or len(equity_curve) < 2:
            return 0.0

        # 兼容 numpy.ndarray 和 pandas.Series
        if hasattr(equity_curve, 'cummax'):
            peak = equity_curve.cummax()
            current = equity_curve.iloc[-1]
            peak_val = peak.iloc[-1]
        else:
            arr = np.asarray(equity_curve, dtype=float)
            peak_arr = np.maximum.accumulate(arr)
            current = arr[-1]
            peak_val = peak_arr[-1]

        if peak_val <= 0:
            return 0.0

        drawdown = (current - peak_val) / peak_val * 100
        return round(float(drawdown), 2)

    def compute_position_size(
        self,
        returns_series,
        equity_curve,
        current_drawdown: Optional[float] = None,
    ) -> Dict:
        """计算推荐仓位比例

        Args:
            returns_series: pandas Series of daily returns
            equity_curve: pandas Series of equity values
            current_drawdown: 可选的当前回撤（如已计算则直接使用）

        Returns:
            dict: {position_ratio, vol_pct, vol_factor, dd_factor, drawdown_pct, adjustment_reason}
        """
        # Step 1: 计算波动率
        vol = self.calculate_volatility(returns_series)

        # Step 2: 波动率调整因子
        if vol <= self.target_vol * 100:
            vol_factor = 1.0
        elif vol >= self.max_vol * 100:
            vol_factor = 0.5
        else:
            vol_factor = 1.0 - 0.5 * (vol - self.target_vol * 100) / (self.max_vol * 100 - self.target_vol * 100)

        # Step 3: 回撤调整因子
        if current_drawdown is not None:
            drawdown = current_drawdown
        else:
            drawdown = self.calculate_drawdown(equity_curve)

        dd_abs = abs(drawdown)
        if dd_abs < 5.0:
            dd_factor = 1.0
        elif dd_abs >= 15.0:
            dd_factor = 0.3
        else:
            dd_factor = 1.0 - 0.7 * (dd_abs - 5.0) / 10.0

        # Step 4: 最终仓位
        final_position = max(
            self.min_position,
            self.base_position * vol_factor * dd_factor
        )
        final_position = round(final_position, 4)

        # Step 5: 记录历史
        record = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'position_ratio': final_position,
            'vol_pct': vol,
            'vol_factor': round(vol_factor, 4),
            'dd_factor': round(dd_factor, 4),
            'drawdown_pct': round(drawdown, 2),
            'adjustment_reason': self._get_reason(vol_factor, dd_factor),
        }
        self._position_history.append(record)

        return record

    def _get_reason(self, vol_factor: float, dd_factor: float) -> str:
        """生成仓位调整原因说明"""
        if vol_factor < 1.0 and dd_factor < 1.0:
            return "波动率偏高+回撤扩大，双重减仓"
        elif vol_factor < 1.0:
            return "波动率偏高，减仓控制风险"
        elif dd_factor < 1.0:
            return "回撤扩大，降低仓位保护本金"
        else:
            return "市场平稳，维持基准仓位"

    def get_risk_level(self, vol: float, drawdown: float) -> str:
        """获取风险等级

        Args:
            vol: 年化波动率百分比
            drawdown: 回撤百分比

        Returns:
            '低' / '中' / '高'
        """
        if vol > 20.0 or drawdown < -10.0:
            return '高'
        elif vol > 15.0 or drawdown < -5.0:
            return '中'
        else:
            return '低'

    def get_position_history(self) -> List[Dict]:
        """获取仓位调整历史记录"""
        return deepcopy(self._position_history)

    def summary(self, returns_series, equity_curve) -> str:
        """生成仓位管理摘要报告"""
        result = self.compute_position_size(returns_series, equity_curve)
        risk = self.get_risk_level(result['vol_pct'], result['drawdown_pct'])

        lines = [
            "=" * 60,
            "  动态仓位管理报告",
            "=" * 60,
            f"  年化波动率:  {result['vol_pct']:.2f}%",
            f"  当前回撤:    {result['drawdown_pct']:.2f}%",
            f"  波动率因子:  {result['vol_factor']:.4f}",
            f"  回撤因子:    {result['dd_factor']:.4f}",
            f"  推荐仓位:    {result['position_ratio']*100:.1f}%",
            f"  风险等级:    {risk}",
            f"  调整原因:    {result['adjustment_reason']}",
            "=" * 60,
        ]
        return '\n'.join(lines)
