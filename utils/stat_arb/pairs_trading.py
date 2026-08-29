"""配对交易引擎 — 零依赖, 基于 cointegration.py

流程:
1. find_cointegrated_pairs: 全标的两两 Engle-Granger 检验, 筛选协整对
2. PairsTradingEngine.generate_signals: 价差 Z-score 信号生成

信号规则:
    z > +entry_z  做空价差 (卖 A 买 B), signal = -1
    z < -entry_z  做多价差 (买 A 卖 B), signal = +1
    |z| < exit_z  平仓, signal = 0
    其他          持有

参考:
- Gatev, Goetzmann & Rouwenhorst (2006) "Pairs Trading"
- 经典理论覆盖度审计 Top 10 #1
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from utils.stat_arb.cointegration import CointegrationResult, engle_granger_test


@dataclass
class PairSignal:
    """配对交易信号"""

    code_a: str
    code_b: str
    hedge_ratio: float
    intercept: float
    half_life: float | None
    zscore: float
    signal: int  # +1 做多价差 / -1 做空价差 / 0 无信号
    pvalue: float

    def to_dict(self) -> dict:
        return {
            "code_a": self.code_a,
            "code_b": self.code_b,
            "hedge_ratio": round(self.hedge_ratio, 4),
            "intercept": round(self.intercept, 4),
            "half_life": round(self.half_life, 2) if self.half_life else None,
            "zscore": round(self.zscore, 4),
            "signal": self.signal,
            "pvalue": round(self.pvalue, 4),
        }


def find_cointegrated_pairs(
    price_data: dict[str, list[float]],
    significance: float = 0.05,
    min_half_life: int = 1,
    max_half_life: int = 60,
    max_pairs: int = 20,
) -> list[dict]:
    """在全部标的中寻找协整对

    Args:
        price_data: {symbol: [close prices]}
        significance: 协整检验显著性水平
        min_half_life: 最小半衰期 (天)
        max_half_life: 最大半衰期 (天)
        max_pairs: 最多返回对数 (按 p-value 升序)

    Returns:
        [{code_a, code_b, pvalue, hedge_ratio, intercept, half_life}, ...]
    """
    symbols = list(price_data.keys())
    if len(symbols) < 2:
        return []

    candidates: list[dict] = []
    n = len(symbols)
    for i in range(n):
        for j in range(i + 1, n):
            sym_a, sym_b = symbols[i], symbols[j]
            pa = price_data[sym_a]
            pb = price_data[sym_b]
            if len(pa) < 60 or len(pb) < 60:
                continue
            # 对齐长度
            min_len = min(len(pa), len(pb))
            ya = np.asarray(pa[-min_len:], dtype=float)
            xb = np.asarray(pb[-min_len:], dtype=float)

            result: CointegrationResult = engle_granger_test(
                ya, xb, significance=significance
            )
            if not result.is_cointegrated:
                continue

            # 半衰期过滤
            if result.half_life is not None:
                if result.half_life < min_half_life or result.half_life > max_half_life:
                    continue

            candidates.append(
                {
                    "code_a": sym_a,
                    "code_b": sym_b,
                    "pvalue": result.pvalue,
                    "hedge_ratio": result.hedge_ratio,
                    "intercept": result.intercept,
                    "half_life": result.half_life,
                }
            )

    # 按 p-value 升序, 取前 max_pairs
    candidates.sort(key=lambda d: d["pvalue"])
    return candidates[:max_pairs]


class PairsTradingEngine:
    """配对交易信号生成器

    用法:
        engine = PairsTradingEngine(entry_z=2.0, exit_z=0.5)
        pairs = find_cointegrated_pairs(price_data)
        signals = engine.generate_signals(price_data, pairs)
    """

    def __init__(
        self,
        zscore_window: int = 20,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
    ):
        """初始化

        Args:
            zscore_window: 价差 Z-score 滚动窗口 (天)
            entry_z: 开仓 Z-score 阈值
            exit_z: 平仓 Z-score 阈值
        """
        self.zscore_window = zscore_window
        self.entry_z = entry_z
        self.exit_z = exit_z

    def compute_zscore(
        self,
        price_a: list[float],
        price_b: list[float],
        hedge_ratio: float,
        intercept: float,
    ) -> float:
        """计算当前价差 Z-score

        spread = price_a - hedge_ratio * price_b - intercept
        z = (spread - mean(spread_window)) / std(spread_window)

        Args:
            price_a: 标的 A 价格序列
            price_b: 标的 B 价格序列
            hedge_ratio: 对冲比 β
            intercept: 截距 α

        Returns:
            Z-score; 数据不足返回 0
        """
        min_len = min(len(price_a), len(price_b))
        if min_len < self.zscore_window:
            return 0.0
        pa = np.asarray(price_a[-min_len:], dtype=float)
        pb = np.asarray(price_b[-min_len:], dtype=float)
        spread = pa - hedge_ratio * pb - intercept
        window = spread[-self.zscore_window :]
        mu = float(np.mean(window))
        sigma = float(np.std(window, ddof=1))
        if sigma < 1e-12:
            return 0.0
        return float((spread[-1] - mu) / sigma)

    def generate_signals(
        self,
        price_data: dict[str, list[float]],
        pairs: list[dict],
    ) -> list[PairSignal]:
        """对协整对生成交易信号

        Args:
            price_data: {symbol: [close prices]}
            pairs: find_cointegrated_pairs 的输出

        Returns:
            [PairSignal, ...]
        """
        signals: list[PairSignal] = []
        for pair in pairs:
            code_a = pair["code_a"]
            code_b = pair["code_b"]
            if code_a not in price_data or code_b not in price_data:
                continue
            z = self.compute_zscore(
                price_data[code_a],
                price_data[code_b],
                pair["hedge_ratio"],
                pair["intercept"],
            )
            # 信号规则
            if z > self.entry_z:
                sig = -1  # 做空价差
            elif z < -self.entry_z:
                sig = +1  # 做多价差
            elif abs(z) < self.exit_z:
                sig = 0  # 平仓
            else:
                sig = 0  # 持有

            signals.append(
                PairSignal(
                    code_a=code_a,
                    code_b=code_b,
                    hedge_ratio=pair["hedge_ratio"],
                    intercept=pair["intercept"],
                    half_life=pair.get("half_life"),
                    zscore=z,
                    signal=sig,
                    pvalue=pair["pvalue"],
                )
            )
        return signals
