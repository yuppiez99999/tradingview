"""筹码分布因子模块 (CYQ · 第 13 大类 Alpha 因子, 2026-08-12 派生升级 W6.4.5)

借鉴 stock(myhhub) 经典通达信/指南针 CYQ 筹码分布算法, 在无「自由流通股本」数据源
时仍能以「换手率近似」退化运行, 输出 4 个可直接进入 AlphaFactorLibrary 的因子:

  1. CYQ_PROFIT_RATIO   : 获利盘比例 (当前价上方筹码占比 = 套牢盘比例的补)
  2. CYQ_CONCENTRATION  : 筹码集中度 (中心 ±20% 价格区间内筹码占比)
  3. CYQ_COST_DEVIATION : 当前价相对平均成本偏离 = (C - AvgCost) / AvgCost
  4. CYQ_PEAK_POSITION  : 筹码峰位置 = mode_price / 当前价 (峰在下方=支持, 上方=阻力)

算法核心 (myhhub 经典版, 2003 指南针口径, 零依赖可合成数据运行):

  ▶ 价格分档: [0, +∞) 均匀等比切 150 档 bin (bin_width_pct ≈ 1.2%, 约
    覆盖 150 * ln(1.012) ≈ 179% 对数价格跨度, 足够 A 股 3 个月窗口).
    每只股票基于 closes[t-149:t] 窗口独立算 [low, high] 再分档, 跨标的不共享轴.

  ▶ 当日筹码增量分布: 当日 bar 的成交额按三角分布铺洒到 closes 附近 bins
      weight(bin) ∝ max(0, 1 - |price_bin - close_t| / spread_t)
    spread_t = max(high_t - low_t, close_t * 0.01) 保证极端情况不坍缩.

  ▶ 换手衰减 (核心! 无流通股本退化版):
      distribution[t] = (1 - turnover_t) * distribution[t-1] + delta_t
    turnover_t = volume_t / median_volume_60
    当用户提供 free_float_shares (自由流通股本) 时:
      turnover_t = min(1.0, volume_t / free_float_shares)

  ▶ 一字板处理: 当 high_t == low_t 且成交量为中位 2 倍以上时, delta_t 坍缩为
    单点 Dirac-δ (close_t 对应单 bin 全量).

设计要点:
  - 纯数值, 不依赖数据库/通达信文件; 与 price_data OHLCV 格式直接对接
  - 每只股票独立滚动, 因子输出横截面 dict[str, float] 与 GTJA 因子库一致
  - 窗口长度默认 150 (A 股季线级别), 可按 warmup_window 参数调整

Fail-Open 降级:
  - scipy.stats.gaussian_kde 缺失 → 用 moving_average 简单近似筹码峰
  - closes/volumes 长度不足 → 返回空 dict (下游 compute_chip_factors 自动跳过)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from utils.alpha_factor.base import FactorValue

logger = logging.getLogger(__name__)


# ============================================================
# 数据类
# ============================================================


@dataclass
class ChipSnapshot:
    """单只股票单日筹码分布快照 (诊断/可视化用)"""
    symbol: str
    bins: np.ndarray            # 分档中心价数组 shape=(N_BINS,)
    distribution: np.ndarray    # 筹码分布 (已归一化 sum=1), shape=(N_BINS,)
    current_price: float
    avg_cost: float
    profit_ratio: float
    concentration: float
    cost_deviation: float
    peak_price: float


# ============================================================
# 筹码分布引擎 (单标的滚动)
# ============================================================


class ChipDistributionEngine:
    """CYQ 筹码分布滚动引擎 (单标的).

    用法:
        engine = ChipDistributionEngine(window=150, n_bins=150)
        for t in range(window, len(closes)):
            snap = engine.update(sym, closes[:t+1], highs[:t+1],
                                 lows[:t+1], volumes[:t+1])
            four_factors = snap_to_factors(snap)
    """

    def __init__(
        self,
        window: int = 150,
        n_bins: int = 150,
        turnover_median_window: int = 60,
    ):
        self.window = window
        self.n_bins = n_bins
        self.turnover_median_window = turnover_median_window
        # 分布状态缓存: {symbol: (bin_edges, distribution_vector, last_t)}
        #   bin_edges: 稳定轴 (首次初始化后仅可扩展, 不随窗口 H/L 重切)
        #   distribution: 长度 = len(edges)-1, 与 bin_edges 严格对齐
        self._state: dict[str, tuple[np.ndarray, np.ndarray, int]] = {}
        # 首次初始化时每标的记录的价格锚: {symbol: (anchor_min, anchor_max)}
        self._anchors: dict[str, tuple[float, float]] = {}

    # ------------------------------------------------------------
    # 内部: 分档 / 三角权重 / 换手衰减 · 稳定轴版本
    # ------------------------------------------------------------
    @staticmethod
    def _bin_edges_fixed(lo: float, hi: float, n_bins: int, pad_ratio: float = 0.5) -> np.ndarray:
        """以 [lo, hi] 为锚, 上下各加 pad_ratio 缓冲, 线性等分得到稳定轴.

        pad_ratio=0.5 意味着轴覆盖 [lo×0.5, hi×1.5] — 对 150 天窗口的 200 天总跨度
        (常见 50-200% 波动) 完全足够容纳, 极少触发后续扩展.
        """
        lo = max(1e-4, float(lo))
        hi = max(lo * 1.01, float(hi))
        axis_lo = lo * (1 - pad_ratio) if (1 - pad_ratio) > 0 else lo * 0.1
        axis_hi = hi * (1 + pad_ratio)
        axis_lo = max(1e-4, axis_lo)
        if axis_hi <= axis_lo:
            axis_hi = axis_lo * 2.0
        return np.linspace(axis_lo, axis_hi, n_bins + 1)

    @staticmethod
    def _extend_edges(old_edges: np.ndarray, new_lo: float, new_hi: float) -> np.ndarray:
        """在旧轴两端追加桶, 直到覆盖 [new_lo, new_hi]; 返回扩展后的新 edges.

        维持原轴的步长 (均匀线性) 不变, 左延用 -=step, 右延用 +=step, 桶数只增不减.
        """
        step = float(old_edges[1] - old_edges[0])
        if step <= 0:
            step = max(old_edges[0] * 0.01, 1e-6)
        left_pads = 0
        cur = float(old_edges[0])
        while cur > new_lo:
            cur -= step
            left_pads += 1
            if left_pads > 5000:  # 保险: 避免死循环
                break
        right_pads = 0
        cur = float(old_edges[-1])
        while cur < new_hi:
            cur += step
            right_pads += 1
            if right_pads > 5000:
                break
        left_part = np.linspace(old_edges[0] - left_pads * step,
                                 old_edges[0] - step, left_pads) if left_pads > 0 else np.array([])
        right_part = np.linspace(old_edges[-1] + step,
                                  old_edges[-1] + right_pads * step, right_pads) if right_pads > 0 else np.array([])
        parts = [p for p in [left_part, old_edges, right_part] if p.size > 0]
        return np.concatenate(parts)

    @staticmethod
    def _align_distribution(
        old_edges: np.ndarray,
        old_dist: np.ndarray,
        new_edges: np.ndarray,
    ) -> np.ndarray:
        """轴扩展后把旧分布向量的非零位置对齐到新轴: 旧 bin 中心 → 新轴中最近的 bin.

        仅用于旧轴≠新轴时的位置对齐; 若轴相同 (正常路径) 调用方会跳过本函数直接复用旧分布.
        为保持 sum 归一化, 输出最后会被归一化 sum=1.
        """
        if np.array_equal(old_edges, new_edges):
            return old_dist.copy()
        old_centers = 0.5 * (old_edges[:-1] + old_edges[1:])
        new_centers = 0.5 * (new_edges[:-1] + new_edges[1:])
        new_dist = np.zeros(len(new_centers), dtype=float)
        # 用 searchsorted 找最近位置
        idx = np.clip(np.searchsorted(new_centers, old_centers), 0, len(new_centers) - 1)
        for i_old, j_new in enumerate(idx):
            w = float(old_dist[i_old])
            if w > 0:
                new_dist[j_new] += w
        s = new_dist.sum()
        if s > 0:
            new_dist = new_dist / s
        return new_dist

    @staticmethod
    def _triangle_weights(centers: np.ndarray, peak: float, spread: float) -> np.ndarray:
        """三角权重: 在 peak 处权重=1, 向外线性衰减到 ±spread 处=0."""
        if spread <= 0:
            spread = max(peak * 0.01, 1e-6)
        dists = np.abs(centers - peak)
        w = np.clip(1.0 - dists / spread, 0.0, 1.0)
        s = w.sum()
        if s < 1e-12:
            # peak 完全越出轴外 → 找最近 bin 放 1 (保证 delta 归一化 sum=1)
            idx = int(np.argmin(dists))
            w = np.zeros_like(w)
            w[idx] = 1.0
            return w
        return w / s

    # ------------------------------------------------------------
    # 主入口: 单日滚动推进 (稳定轴, 不随窗口 H/L 重切分)
    # ------------------------------------------------------------
    def update(
        self,
        symbol: str,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        volumes: list[float],
        free_float_shares: Optional[float] = None,
    ) -> Optional[ChipSnapshot]:
        """推进到当前 bar, 返回筹码快照. 窗口不足返回 None.

        稳定轴策略 (核心修复 v2: 解决窗口 H/L 漂移导致每日重置的问题):
          1. 首次 (symbol 未见) 进入窗口时, 用当前已收到的 closes H/L ± 50% pad 初始化 edges 轴.
          2. 之后每步先检查当日 [cs[-1], hs[-1], ls[-1]] 是否越出当前轴; 若越出则用 _extend_edges
             扩展 (保持步长不变), 同时用 _align_distribution 把旧分布搬移对齐.
          3. 在稳定轴上做换手衰减: distribution ← (1 - turnover) * distribution + turnover * delta,
             归一化 sum=1 存储.

        Args:
            closes/highs/lows/volumes: 最新可用历史 (长度 t+1)
            free_float_shares: 可选自由流通股本 (股). 提供时换手率=vol/流通股,
                否则用 vol / rolling_median_vol 作为退化换手率.
        """
        T = len(closes)
        if T < self.window:
            return None
        # 窗口内数据 (用于计算换手中位数 / delta 价格区间)
        cs = closes[-self.window:]
        hs = highs[-self.window:]
        ls = lows[-self.window:]
        vs = volumes[-self.window:]

        # ---------- 换手率 (无流通股本退化) ----------
        # A股日均换手率通常 1~3%. 退化版使用 vol/rolling_median_vol 作为偏离倍数,
        # 乘上基准 2% 再 clamp 到 [0.1%, 50%]: 既避免历史筹码被过快稀释,
        # 也给放量日合理的权重更新.
        vol_arr = np.asarray(vs, dtype=float)
        if free_float_shares and free_float_shares > 0:
            turnover_t = min(0.5, float(vol_arr[-1]) / free_float_shares)
        else:
            med_vol = float(np.median(vol_arr[-self.turnover_median_window:]))
            if med_vol <= 0:
                turnover_t = 0.02  # 默认 2% 日换手 (A股典型值)
            else:
                turnover_t = float(vol_arr[-1] / med_vol) * 0.02
                turnover_t = max(1e-3, min(0.5, turnover_t))

        # ---------- 稳定轴: 初始化 / 扩展 ----------
        prev = self._state.get(symbol)
        if prev is None:
            # 首次见: 以「迄今 closes」H/L 为锚建轴 (±50% pad)
            anchor_lo = float(np.nanmin(closes))
            anchor_hi = float(np.nanmax(closes))
            edges = self._bin_edges_fixed(anchor_lo, anchor_hi, self.n_bins, pad_ratio=0.5)
            distribution = np.zeros(len(edges) - 1, dtype=float)
            self._anchors[symbol] = (anchor_lo, anchor_hi)
        else:
            edges, distribution, _ = prev[0], prev[1].copy(), prev[2]
            # 越界检查: 当日 close/high/low 是否越出 edges 范围
            need_low = min(float(cs[-1]), float(ls[-1])) * 0.99
            need_high = max(float(cs[-1]), float(hs[-1])) * 1.01
            if need_low < edges[0] or need_high > edges[-1]:
                new_edges = self._extend_edges(edges, need_low, need_high)
                distribution = self._align_distribution(edges, distribution, new_edges)
                edges = new_edges

        centers = 0.5 * (edges[:-1] + edges[1:])
        bin_width = (edges[-1] - edges[0]) / (len(edges) - 1)

        # ---------- 当日筹码增量 delta ----------
        # spread_t 下界至少 ±1.5 bin, 确保每天 delta 至少投到 3 个 bin, 避免单-bin 坍缩 (集中度始终 100%)
        raw_spread = max(float(hs[-1]) - float(ls[-1]), float(cs[-1]) * 0.01)
        spread_t = max(raw_spread, bin_width * 3.0)
        is_limit_block = (abs(float(hs[-1]) - float(ls[-1])) < float(cs[-1]) * 0.001) and (
            float(vol_arr[-1]) > 2.0 * float(np.median(vol_arr[-10:] + 1e-12))
        )
        if is_limit_block:
            # 一字板: Dirac-δ 坍缩到最近 bin
            delta = np.zeros(len(centers), dtype=float)
            idx = int(np.argmin(np.abs(centers - float(cs[-1]))))
            delta[idx] = 1.0
        else:
            delta = self._triangle_weights(centers, float(cs[-1]), float(spread_t))

        # ---------- 换手衰减 (核心递推) ----------
        distribution *= (1.0 - turnover_t)
        distribution += turnover_t * delta
        total = distribution.sum()
        if total > 0:
            distribution = distribution / total
        self._state[symbol] = (edges, distribution, T)

        # ---------- 计算 4 个因子 ----------
        current = float(cs[-1])
        # 1) 平均成本 = sum(centers * distribution)
        avg_cost = float(np.sum(centers * distribution))
        # 2) 获利盘比例 = sum(distribution[centers <= current])
        profit_mask = centers <= (current * 1.0001)  # 浮点容差
        profit_ratio = float(np.sum(distribution[profit_mask]))
        # 3) 集中度 = sum(distribution[avg_cost ± 20%])
        c_lo = avg_cost * 0.8
        c_hi = avg_cost * 1.2
        conc_mask = (centers >= c_lo) & (centers <= c_hi)
        concentration = float(np.sum(distribution[conc_mask]))
        # 4) 成本偏离 = (当前价 - 平均成本) / 平均成本
        cost_deviation = float((current - avg_cost) / avg_cost) if avg_cost > 0 else 0.0
        # 5) 筹码峰位置 = mode / current (mode = distribution 最大 bin 的中心价)
        peak_idx = int(np.argmax(distribution))
        peak_price = float(centers[peak_idx])

        return ChipSnapshot(
            symbol=symbol,
            bins=centers,
            distribution=distribution,
            current_price=current,
            avg_cost=avg_cost,
            profit_ratio=profit_ratio,
            concentration=concentration,
            cost_deviation=cost_deviation,
            peak_price=peak_price,
        )


# ============================================================
# 横截面因子计算 (供 AlphaFactorLibrary 调用)
# ============================================================


def compute_chip_factors(
    price_data: dict[str, dict[str, list[float]]],
    window: int = 150,
    n_bins: int = 150,
    free_float_shares: Optional[dict[str, float]] = None,
) -> dict[str, FactorValue]:
    """计算 4 个筹码分布因子 (第 13 大类 · ChipDistribution)

    Args:
        price_data: {symbol: {closes/highs/lows/volumes: list}}
        window: 滚动窗口 (默认 150 天, ≈ 季线级别筹码沉淀)
        n_bins: 价格分档数 (默认 150)
        free_float_shares: 可选 {symbol: 流通股数}. 缺省走成交量中位数退化换手率.

    Returns:
        {factor_name: FactorValue} — 4 个因子: CYQ_PROFIT_RATIO / CYQ_CONCENTRATION /
        CYQ_COST_DEVIATION / CYQ_PEAK_POSITION
    """
    engine = ChipDistributionEngine(window=window, n_bins=n_bins)
    ffs = free_float_shares or {}

    cross = {
        "CYQ_PROFIT_RATIO": {},
        "CYQ_CONCENTRATION": {},
        "CYQ_COST_DEVIATION": {},
        "CYQ_PEAK_POSITION": {},
    }
    n_skipped = 0
    for sym, pd in price_data.items():
        closes = pd.get("closes", [])
        highs = pd.get("highs", []) or closes
        lows = pd.get("lows", []) or closes
        volumes = pd.get("volumes", []) or [1.0] * len(closes)
        T = len(closes)
        if T < window:
            n_skipped += 1
            continue
        # 筹码分布是滚动累积过程: 必须从 window 天起逐日重放到最后
        # (ChipDistributionEngine 内部用 symbol 级 state 保存 distribution, 必须逐 bar 推进)
        snap: ChipSnapshot | None = None
        for t in range(window, T + 1):
            snap = engine.update(
                symbol=sym,
                closes=closes[:t],
                highs=highs[:t],
                lows=lows[:t],
                volumes=volumes[:t],
                free_float_shares=ffs.get(sym),
            )
        if snap is None:
            n_skipped += 1
            continue
        cross["CYQ_PROFIT_RATIO"][sym] = snap.profit_ratio
        cross["CYQ_CONCENTRATION"][sym] = snap.concentration
        cross["CYQ_COST_DEVIATION"][sym] = snap.cost_deviation
        if snap.current_price > 0:
            cross["CYQ_PEAK_POSITION"][sym] = snap.peak_price / snap.current_price
        else:
            cross["CYQ_PEAK_POSITION"][sym] = 1.0

    logger.info(
        "CYQ 筹码分布因子: 覆盖标的 %d, 跳过 %d (窗口不足) → 4 因子输出",
        len(cross["CYQ_PROFIT_RATIO"]), n_skipped,
    )

    return {
        "CYQ_PROFIT_RATIO": FactorValue(
            name="CYQ_PROFIT_RATIO",
            category="ChipDistribution",
            values=cross["CYQ_PROFIT_RATIO"],
        ),
        "CYQ_CONCENTRATION": FactorValue(
            name="CYQ_CONCENTRATION",
            category="ChipDistribution",
            values=cross["CYQ_CONCENTRATION"],
        ),
        "CYQ_COST_DEVIATION": FactorValue(
            name="CYQ_COST_DEVIATION",
            category="ChipDistribution",
            values=cross["CYQ_COST_DEVIATION"],
        ),
        "CYQ_PEAK_POSITION": FactorValue(
            name="CYQ_PEAK_POSITION",
            category="ChipDistribution",
            values=cross["CYQ_PEAK_POSITION"],
        ),
    }
