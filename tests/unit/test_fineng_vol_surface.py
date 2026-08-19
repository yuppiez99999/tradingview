"""utils.fineng.models.vol_surface 单元测试 — VolSlice / VolSurface / build_vol_surface_from_points"""
from __future__ import annotations

from datetime import date

import pytest

from utils.fineng.models.vol_surface import (
    VolSlice,
    VolSurface,
    build_vol_surface_from_points,
)


def _slice(T: float = 0.25) -> VolSlice:
    # strikes/ivs 按升序; ATM 在中间
    return VolSlice(
        expiry=date(2026, 9, 1), T=T,
        strikes=[3.0, 3.25, 3.5, 3.75, 4.0],
        ivs=[0.18, 0.19, 0.20, 0.21, 0.22],   # 单调升 (put 低 call 高)
        deltas=[0.90, 0.70, 0.50, 0.30, 0.10],
    )


def test_vol_slice_atm_iv():
    """VolSlice: ATM IV 为中间点 (delta=0.5 对应 index 2)"""
    s = _slice()
    assert s.atm_iv == pytest.approx(0.20)


def test_vol_slice_skew():
    """VolSlice: skew = put_25d - call_25d (IV 单调升 → 正 skew)"""
    s = _slice()
    sk = s.skew
    assert sk != 0.0
    # 默认 IV 单调升, skew 为正
    assert sk > 0.0


def test_vol_slice_interpolate():
    """VolSlice: interpolate(strike) 线性插值正确"""
    s = _slice()
    # K=3.35 在 [3.25,3.5] 之间 → 0.19 + 0.4*(0.20-0.19) = 0.194
    assert s.interpolate(3.35) == pytest.approx(0.194, abs=1e-6)
    # 超出范围使用端点
    assert s.interpolate(2.9) == pytest.approx(0.18)
    assert s.interpolate(4.1) == pytest.approx(0.22)


def test_vol_surface_get_iv_interp():
    """VolSurface: get_iv(strike, T) 在期限间插值"""
    vs = VolSurface()
    vs.add_slice(_slice(0.25))
    vs.add_slice(_slice(0.5))
    vs.add_slice(_slice(1.0))
    # K=3.5 (ATM) T=0.75 → 期限制 0.5(0.20) 与 1.0(0.20) 之间 = 0.20
    iv = vs.get_iv(strike=3.5, T=0.75)
    assert iv == pytest.approx(0.20, abs=1e-6)


def test_vol_surface_atm_term_structure():
    """VolSurface: atm_term_structure (property) 返回 (T, iv) 列表"""
    vs = VolSurface()
    vs.add_slice(_slice(0.25))
    vs.add_slice(_slice(0.5))
    vs.add_slice(_slice(1.0))
    ts = vs.atm_term_structure
    assert len(ts) == 3
    assert all(isinstance(p, tuple) and len(p) == 2 for p in ts)
    # 期限制升序
    assert ts[0][0] < ts[-1][0]


def test_vol_surface_skew_term_structure():
    """VolSurface: skew_term_structure (property) 返回 (T, skew) 列表"""
    vs = VolSurface()
    vs.add_slice(_slice(0.25))
    vs.add_slice(_slice(0.5))
    ts = vs.skew_term_structure
    assert len(ts) == 2
    assert ts[0][1] > 0.0


def test_build_vol_surface_from_points():
    """build_vol_surface_from_points: 从 VolPoint 点构建曲面"""
    from datetime import date

    from utils.fineng.models.vol_surface import VolPoint

    points = [
        VolPoint(strike=3.0, expiry=date(2026, 9, 1), iv=0.18),
        VolPoint(strike=3.5, expiry=date(2026, 9, 1), iv=0.20),
        VolPoint(strike=4.0, expiry=date(2026, 9, 1), iv=0.22),
    ]
    vs = build_vol_surface_from_points("510050.SH", points, spot=3.5, r=0.03)
    # ATM (K=3.5, T≈0.07) 应接近 0.20
    assert vs.get_iv(strike=3.5, T=0.07) == pytest.approx(0.20, abs=3e-2)


def test_vol_surface_empty_get_iv():
    """空曲面 get_iv 返回 0.0 (兜底)"""
    vs = VolSurface()
    assert vs.get_iv(strike=3.5, T=0.5) == pytest.approx(0.0)
