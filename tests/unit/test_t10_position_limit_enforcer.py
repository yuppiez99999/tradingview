"""T10 单元测试 — PositionLimitEnforcer 持仓集中度."""

from __future__ import annotations

import pytest

from utils.risk.position_limit_enforcer import (
    OrderImpact,
    PositionLimitEnforcer,
    PositionSnapshot,
)


def _make_snap(equity=10_000_000, positions=None, sectors=None):
    """辅助构造持仓快照."""
    return PositionSnapshot(
        total_equity=equity,
        positions=positions or {"sh600519": (100, 1700.0)},
        sectors=sectors or {"sh600519": "白酒"},
    )


class TestConfigValidation:
    def test_invalid_single_cap(self):
        with pytest.raises(ValueError):
            PositionLimitEnforcer(single_name_cap_pct=1.5)

    def test_invalid_equity_zero_failclose(self):
        enf = PositionLimitEnforcer()
        snap = PositionSnapshot(total_equity=0, positions={})
        impact = OrderImpact("s1", "buy", 100, 10.0)
        r = enf.check_after_trade(snap, impact)
        assert r.rejected
        assert any("EQUITY_ZERO" in s for s in r.reasons)


class TestSingleNameCap:
    def setup_method(self):
        self.enf = PositionLimitEnforcer(single_name_cap_pct=0.15)

    def test_within_cap_pass(self):
        # 已持 100×1700 = 17万, 再加 100×1700 = 34万 / 1000万 = 3.4% < 15%
        snap = _make_snap()
        impact = OrderImpact("sh600519", "buy", 100, 1700.0, sector="白酒")
        r = self.enf.check_after_trade(snap, impact)
        assert r.is_pass
        assert r.projected_single.get("sh600519", 0) <= 0.15

    def test_breach_cap_reject(self):
        # 单票买 1 万 × 1700 = 1700 万 / 1000 万 = 170% >> 15%
        snap = _make_snap()
        impact = OrderImpact("sh600519", "buy", 10_000, 1700.0, sector="白酒")
        r = self.enf.check_after_trade(snap, impact)
        assert r.rejected
        assert any("SINGLE_NAME" in s for s in r.reasons)


class TestSectorCap:
    def test_within_sector_pass(self):
        # 白酒 2 只: 茅台 17万 + 五粮液 100×150=15万 → 32万 / 1000万 = 3.2% < 30%
        enf = PositionLimitEnforcer(sector_cap_pct=0.30)
        snap = _make_snap(
            positions={"sh600519": (100, 1700.0), "sz000858": (100, 1500.0)},
            sectors={"sh600519": "白酒", "sz000858": "白酒"},
        )
        impact = OrderImpact("sz000858", "buy", 100, 1500.0, sector="白酒")
        r = enf.check_after_trade(snap, impact)
        assert r.is_pass

    def test_breach_sector_reject(self):
        # 单板块重仓 400 万 → 40% > 30%
        enf = PositionLimitEnforcer(sector_cap_pct=0.30)
        snap = _make_snap(
            positions={"sh1": (20_000, 100.0)},  # 200 万
            sectors={"sh1": "AI"},
        )
        impact = OrderImpact("sh1", "buy", 20_000, 100.0, sector="AI")  # +200 万 → 40%
        r = enf.check_after_trade(snap, impact)
        assert r.rejected
        assert any("SECTOR" in s for s in r.reasons)

    def test_no_sectors_skips(self):
        enf = PositionLimitEnforcer(sector_cap_pct=0.01)  # 1% 超严阈值
        snap = PositionSnapshot(
            total_equity=10_000_000, positions={"s1": (100_000, 100.0)}
        )  # 1000 万单票, 但没 sectors
        impact = OrderImpact(
            "s1", "buy", 100, 100.0
        )  # 即使全仓也因无 sector 跳过该检查
        r = enf.check_after_trade(snap, impact)
        # 仅 SINGLE_NAME 拦截
        assert r.rejected
        assert any("SINGLE_NAME" in s for s in r.reasons)
        assert not any("SECTOR" in s for s in r.reasons)


class TestNetExposureCap:
    def test_normal_pass(self):
        enf = PositionLimitEnforcer(net_exposure_cap_pct=1.20)
        snap = _make_snap()  # 净多头 17万/1000万 = 1.7%
        impact = OrderImpact("sh600519", "buy", 100, 1700.0)  # +17 万
        r = enf.check_after_trade(snap, impact)
        assert r.is_pass
        assert abs(r.projected_net_exp_pct) <= 1.20

    def test_leverage_too_high_reject(self):
        enf = PositionLimitEnforcer(net_exposure_cap_pct=0.10)  # 10% 极严
        snap = _make_snap()  # 17 万多头 = 1.7%
        impact = OrderImpact("sh2", "buy", 10_000, 1000.0)  # 1000 万 = 100% 净敞口
        r = enf.check_after_trade(snap, impact)
        assert r.rejected
        assert any("NET_EXPOSURE" in s for s in r.reasons)


class TestGrossLeverageCap:
    def test_high_gross_reject(self):
        # 多空合计 3000 万 / 1000 万 = 3x > 2x 阈值
        enf = PositionLimitEnforcer(gross_leverage_cap=2.00)
        PositionSnapshot(
            total_equity=10_000_000,
            positions={
                "long": (10_000, 100.0),  # 多头 100 万
                "short": (-5_000, 200.0),  # 空头 100 万 (市值 -100 万, 绝对值 100 万)
            },
        )
        # 再加 14 万手多 → 1500 万多头 + 100 万空头 = 1600 万 gross? 用个更明显的
        snap2 = PositionSnapshot(
            total_equity=10_000_000,
            positions={"big_long": (150_000, 100.0)},
        )
        impact = OrderImpact(
            "big_long", "buy", 60_000, 100.0
        )  # +600 万 = 2100 万 > 2000 万
        r = enf.check_after_trade(snap2, impact)
        assert r.rejected
        assert any("GROSS_LEVERAGE" in s for s in r.reasons)


class TestModeBlockVsWarn:
    def test_warn_mode_does_not_reject(self):
        enf = PositionLimitEnforcer(single_name_cap_pct=0.001, mode="WARN")
        snap = _make_snap()
        impact = OrderImpact("sh600519", "buy", 100, 1700.0)
        r = enf.check_after_trade(snap, impact)
        assert not r.rejected
        assert r.reasons  # 但仍有告警原因
