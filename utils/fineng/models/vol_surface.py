"""
波动率曲面模型 (Volatility Surface)

基于期权市场数据构建波动率曲面, 支持:
    - 线性插值 (Delta-Moneyness 网格)
    - SVI 参数化 (Stochastic Volatility Inspired)
    - 任意行权价/期限的 IV 查询

设计:
    保持轻量, 不引入 scipy/numpy 依赖。
    高级插值使用纯 math 实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class VolPoint:
    """波动率数据点"""
    strike: float
    expiry: date
    iv: float                # 隐含波动率
    delta: float | None = None   # 对应 Delta (可选)
    volume: float = 0.0      # 成交量


@dataclass
class VolSlice:
    """特定期限的波动率切片"""
    expiry: date
    T: float                 # 剩余年限
    strikes: list[float] = field(default_factory=list)
    ivs: list[float] = field(default_factory=list)
    deltas: list[float] = field(default_factory=list)

    @property
    def atm_iv(self) -> float:
        """ATM 波动率 (最接近平值的 IV)"""
        if not self.ivs:
            return 0.0
        if self.deltas:
            # 找最接近 0.5 delta 的
            best_idx = min(range(len(self.deltas)), key=lambda i: abs(abs(self.deltas[i]) - 0.5))
            return self.ivs[best_idx]
        return self.ivs[len(self.ivs) // 2]

    @property
    def skew(self) -> float:
        """Put Skew: 25Δ Put IV - 25Δ Call IV"""
        put_25d = self._get_iv_by_delta(-0.25)
        call_25d = self._get_iv_by_delta(0.25)
        if put_25d is None or call_25d is None:
            return 0.0
        return put_25d - call_25d

    def interpolate(self, strike: float) -> float:
        """线性插值获取特定期权价 IV"""
        if not self.strikes:
            return 0.0

        # 边界
        if strike <= self.strikes[0]:
            return self.ivs[0]
        if strike >= self.strikes[-1]:
            return self.ivs[-1]

        # 二分查找
        for i in range(len(self.strikes) - 1):
            if self.strikes[i] <= strike <= self.strikes[i + 1]:
                t = (strike - self.strikes[i]) / (self.strikes[i + 1] - self.strikes[i])
                return self.ivs[i] + t * (self.ivs[i + 1] - self.ivs[i])

        return 0.0

    def _get_iv_by_delta(self, target_delta: float) -> float | None:
        """按 Delta 获取 IV"""
        if not self.deltas or not self.ivs:
            return None
        best_idx = min(range(len(self.deltas)), key=lambda i: abs(self.deltas[i] - target_delta))
        return self.ivs[best_idx]


class VolSurface:
    """波动率曲面

    组织多个期限的波动率切片, 支持跨期限/行权价的 IV 查询。
    """

    def __init__(self, underlying: str = ""):
        self.underlying = underlying
        self.slices: list[VolSlice] = []

    def add_slice(self, sl: VolSlice) -> None:
        """添加一个期限的波动率切片"""
        self.slices.append(sl)
        self.slices.sort(key=lambda s: s.T)

    def get_iv(self, strike: float, T: float) -> float:
        """获取指定行权价和期限的隐含波动率

        先沿期限维度插值, 再沿行权价维度插值。
        """
        if not self.slices:
            return 0.0

        # 边界: T 超出范围
        if T <= self.slices[0].T:
            return self.slices[0].interpolate(strike)
        if T >= self.slices[-1].T:
            return self.slices[-1].interpolate(strike)

        # 在相邻两个 slice 间线性插值
        for i in range(len(self.slices) - 1):
            if self.slices[i].T <= T <= self.slices[i + 1].T:
                t = (T - self.slices[i].T) / (self.slices[i + 1].T - self.slices[i].T)
                iv_lower = self.slices[i].interpolate(strike)
                iv_upper = self.slices[i + 1].interpolate(strike)
                return iv_lower + t * (iv_upper - iv_lower)

        return 0.0

    @property
    def atm_term_structure(self) -> list[tuple[float, float]]:
        """ATM 波动率期限结构 [(T, IV), ...]"""
        return [(sl.T, sl.atm_iv) for sl in self.slices if sl.atm_iv > 0]

    @property
    def skew_term_structure(self) -> list[tuple[float, float]]:
        """Skew 期限结构 [(T, skew), ...]"""
        return [(sl.T, sl.skew) for sl in self.slices]


def build_vol_surface_from_points(
    underlying: str,
    points: list[VolPoint],
    spot: float,
    r: float = 0.02,
) -> VolSurface:
    """从原始 IV 数据点构建波动率曲面

    自动按到期日分组, 计算 Delta 和期限。
    """
    from collections import defaultdict

    surface = VolSurface(underlying)

    # 按到期日分组
    groups: dict[date, list[VolPoint]] = defaultdict(list)
    for pt in points:
        groups[pt.expiry].append(pt)

    today = date.today()
    for expiry, pts in groups.items():
        T = max((expiry - today).days, 1) / 365.0
        pts_sorted = sorted(pts, key=lambda p: p.strike)

        sl = VolSlice(
            expiry=expiry,
            T=T,
            strikes=[p.strike for p in pts_sorted],
            ivs=[p.iv for p in pts_sorted],
            deltas=[p.delta if p.delta is not None else _approx_delta(spot, p.strike, T, r, p.iv)
                    for p in pts_sorted],
        )
        surface.add_slice(sl)

    return surface


def _approx_delta(spot: float, strike: float, T: float, r: float, iv: float) -> float:
    """用 BS 近似计算 Delta (用于分组)"""
    from utils.fineng.pricing.black_scholes import bs_delta
    return bs_delta(spot, strike, T, r, iv, strike > spot)
