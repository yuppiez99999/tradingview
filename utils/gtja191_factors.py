# -*- coding: utf-8 -*-
"""
GTJA191 因子库 — Alpha191 / 国泰君安191因子（28-终极量化交易系统7.1 集成版）

当前实现：
- Alpha144：过去 N 个交易日内，下跌日“收益率绝对值/成交额”的平均值。

数据需求：
- close、amount（成交额）
- 至少 lookback + 1 个日频数据点

输出：
- alpha144: float，单位为“收益率/成交额”，反映下跌放量/承接效率。
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


class GTJA191Factors:
    """GTJA191 短周期量价因子计算器"""

    def __init__(self, lookback: int = 20):
        """
        Args:
            lookback: Alpha144 的统计窗口，默认 20 日。
        """
        self.lookback = lookback

    def alpha144(self, df: pd.DataFrame) -> Optional[float]:
        """
        Alpha144:
        SUMIF(ABS(CLOSE/DELAY(CLOSE,1)-1)/AMOUNT, 20, CLOSE<DELAY(CLOSE,1))
        / COUNT(CLOSE<DELAY(CLOSE,1), 20)

        含义：过去 N 个交易日内，下跌日“收益率绝对值/成交额”的平均值。
        高值：下跌放量、单位成交额推动的价格跌幅大
        低值：下跌缩量或承接较好

        Args:
            df: 需包含 close、amount 列，按时间升序。

        Returns:
            float 或 None（数据不足时返回 None）
        """
        if df is None or len(df) < self.lookback + 1:
            return None

        close = df['close'].to_numpy(dtype=float)
        amount = df['amount'].to_numpy(dtype=float)

        ret = np.zeros_like(close)
        ret[1:] = close[1:] / close[:-1] - 1.0

        down_mask = close < np.roll(close, 1)
        down_mask[0] = False

        tail_ret = ret[-self.lookback:]
        tail_amount = amount[-self.lookback:]
        tail_down = down_mask[-self.lookback:]

        if not np.any(tail_down):
            return 0.0

        efficiency = np.where(
            tail_down,
            np.abs(tail_ret) / np.where(tail_amount > 0, tail_amount, np.nan),
            np.nan,
        )
        value = np.nanmean(efficiency)

        return float(value) if np.isfinite(value) else 0.0

    def compute(self, df: pd.DataFrame) -> Dict[str, Optional[float]]:
        """批量计算因子，当前仅实现 Alpha144"""
        return {
            'alpha144': self.alpha144(df),
        }
