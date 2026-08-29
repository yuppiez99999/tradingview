"""
ai_decision.backtest_replay_mocks — 历史回放测试 Mock
======================================================

从 backtest_replay.py 拆分 (v8.6.15 重构, AGENTS.md §5.3 文件 ≤800 行约束).

本模块仅含 MockHistoryDataLoader, 用于测试 / CI 合成数据生成:
  - 确定性随机数据 (seed 固定), 满足回放引擎的所有数据需求
  - 不依赖外部数据源, 确保测试可重复

向后兼容: backtest_replay.py 通过 re-export 暴露 MockHistoryDataLoader.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

import numpy as np


class MockHistoryDataLoader:
    """合成历史数据加载器 (测试 / CI 用)

    生成确定性随机数据 (seed 固定), 满足回放引擎的所有数据需求.
    不依赖外部数据源, 确保测试可重复.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        days: int = 60,
        start_date: str = "2025-01-01",
        seed: int = 42,
    ) -> None:
        self._symbols = symbols or ["600519", "000001", "300750"]
        self._seed = seed
        self._rng = random.Random(seed)
        np.random.seed(seed)

        # 生成交易日序列 (跳过周末)
        self._dates: list[str] = []
        d = datetime.strptime(start_date, "%Y-%m-%d")
        for _ in range(days):
            while d.weekday() >= 5:  # 5=周六, 6=周日
                d += timedelta(days=1)
            self._dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

        # 为每个 symbol 生成价格序列 (随机游走)
        self._prices: dict[str, list[float]] = {}
        self._halts: dict[str, set] = {s: set() for s in self._symbols}
        for sym in self._symbols:
            price = 10.0 + self._rng.uniform(0, 90)  # 10~100 元
            prices = [price]
            for _i in range(1, len(self._dates)):
                # 随机游走 + 微小正漂移
                ret = self._rng.gauss(0.001, 0.02)
                price = price * (1 + ret)
                prices.append(round(price, 2))
            self._prices[sym] = prices
            # 随机停牌 (5% 概率)
            for _i, dt in enumerate(self._dates):
                if self._rng.random() < 0.05:
                    self._halts[sym].add(dt)

    def get_trading_dates(self, start: str, end: str) -> list[str]:
        return [d for d in self._dates if start <= d <= end]

    def get_market_data(self, symbol: str, date: str) -> dict[str, Any]:
        if symbol not in self._prices or date not in self._dates:
            return {
                "close": 0.0,
                "change_pct": 0.0,
                "volume": 0.0,
                "is_halted": True,
                "is_limit_up": False,
                "is_limit_down": False,
            }
        idx = self._dates.index(date)
        close = self._prices[symbol][idx]
        prev_close = self._prices[symbol][idx - 1] if idx > 0 else close
        change_pct = (close - prev_close) / prev_close if prev_close > 0 else 0.0
        is_halted = date in self._halts.get(symbol, set())
        # 涨跌停 (|change_pct| > 9.9%)
        is_limit_up = change_pct > 0.099
        is_limit_down = change_pct < -0.099
        return {
            "close": close,
            "change_pct": round(change_pct, 4),
            "volume": float(self._rng.randint(100000, 1000000)),
            "is_halted": is_halted,
            "is_limit_up": is_limit_up,
            "is_limit_down": is_limit_down,
        }

    def get_fundamentals(self, symbol: str, date: str) -> dict[str, Any]:
        # 用披露日而非报告期截止日: disclosure_date <= date 才可见
        return {
            "pe": round(self._rng.uniform(5, 50), 2),
            "pb": round(self._rng.uniform(0.5, 5), 2),
            "roe": round(self._rng.uniform(0.05, 0.30), 4),
            "disclosure_date": date,  # 简化: 当日披露
        }

    def get_forward_return(self, symbol: str, date: str, horizon: int) -> float:
        if symbol not in self._prices or date not in self._dates:
            return float("nan")
        idx = self._dates.index(date)
        if idx + horizon >= len(self._prices[symbol]):
            return float("nan")
        p0 = self._prices[symbol][idx]
        p1 = self._prices[symbol][idx + horizon]
        if p0 <= 0:
            return float("nan")
        return (p1 - p0) / p0

    def get_constituents(self, date: str) -> list[str]:
        # 简化: 成分股固定 (实际应逐日快照防幸存者偏差)
        return list(self._symbols)

    def is_tradable(self, symbol: str, date: str) -> bool:
        md = self.get_market_data(symbol, date)
        if md.get("is_halted", True):
            return False
        if md.get("is_limit_up", False) or md.get("is_limit_down", False):
            return False
        return True


__all__ = ["MockHistoryDataLoader"]
