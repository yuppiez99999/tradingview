"""
隐含波动率曲面深度对冲 — 单元测试
==================================

文献: #38 IV Surface Deep Hedging 2025.04
"""

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.iv_surface_deep_hedge import (
    HedgeTool,
    HedgeToolType,
    IVSurfaceDeepHedgeEngine,
    MultiToolHedger,
    SecondOrderGreeks,
    VolatilitySignal,
    VRPCalculator,
)

# ============================================================
# 枚举测试
# ============================================================

class TestEnums:
    def test_hedge_tool_types(self):
        assert len(HedgeToolType) == 3

    def test_volatility_signals(self):
        assert len(VolatilitySignal) == 3


# ============================================================
# VRP 测试
# ============================================================

class TestVarianceRiskPremium:
    def test_short_vol(self):
        vrp = VRPCalculator.compute(0.25, 0.15)
        assert vrp.vrp > 0
        assert vrp.signal == VolatilitySignal.SHORT_VOL

    def test_long_vol(self):
        vrp = VRPCalculator.compute(0.15, 0.25)
        assert vrp.vrp < 0
        assert vrp.signal == VolatilitySignal.LONG_VOL

    def test_neutral(self):
        vrp = VRPCalculator.compute(0.20, 0.20)
        assert vrp.vrp == 0.0
        assert vrp.signal == VolatilitySignal.NEUTRAL

    def test_series(self):
        ivs = np.array([0.25, 0.15, 0.20])
        rvs = np.array([0.15, 0.25, 0.20])
        results = VRPCalculator.compute_series(ivs, rvs)
        assert len(results) == 3

    def test_rolling(self):
        ivs = np.random.default_rng(42).standard_normal(100) * 0.05 + 0.2
        rvs = np.random.default_rng(43).standard_normal(100) * 0.05 + 0.18
        vrp = VRPCalculator.rolling_vrp(ivs, rvs, window=20)
        assert len(vrp) == 100


# ============================================================
# 二阶希腊字母测试
# ============================================================

class TestSecondOrderGreeks:
    def test_vanna(self):
        v = SecondOrderGreeks.vanna(100, 100, 30 / 365, 0.2)
        assert isinstance(v, float)

    def test_volga(self):
        v = SecondOrderGreeks.volga(100, 100, 30 / 365, 0.2)
        assert isinstance(v, float)

    def test_all_vol_greeks(self):
        g = SecondOrderGreeks.all_vol_greeks(100, 100, 30 / 365, 0.2)
        assert "vega" in g
        assert "vanna" in g
        assert "volga" in g

    def test_zero_maturity(self):
        g = SecondOrderGreeks.all_vol_greeks(100, 100, 0, 0.2)
        assert g["vega"] == 0.0


# ============================================================
# 多工具对冲器测试
# ============================================================

class TestMultiToolHedger:
    def test_no_tools(self):
        hedger = MultiToolHedger()
        result = hedger.hedge(100, 10, 50, [])
        assert result.residual_vega == 100

    def test_single_tool(self):
        hedger = MultiToolHedger()
        tool = HedgeTool(tool_type=HedgeToolType.OPTION, vega=10)
        result = hedger.hedge(100, 0, 0, [tool])
        assert abs(result.residual_vega) < 1e-6

    def test_three_tools(self):
        hedger = MultiToolHedger()
        tools = [
            HedgeTool(tool_type=HedgeToolType.OPTION, vega=10, vanna=1, volga=2),
            HedgeTool(tool_type=HedgeToolType.OPTION, vega=5, vanna=2, volga=1),
            HedgeTool(tool_type=HedgeToolType.OPTION, vega=8, vanna=3, volga=4),
        ]
        result = hedger.hedge(100, 10, 50, tools)
        assert abs(result.residual_vega) < 1e-6
        assert abs(result.residual_vanna) < 1e-6
        assert abs(result.residual_volga) < 1e-6


# ============================================================
# 引擎测试
# ============================================================

class TestIVSurfaceDeepHedgeEngine:
    def test_hedge(self):
        engine = IVSurfaceDeepHedgeEngine()
        tools = [
            HedgeTool(tool_type=HedgeToolType.OPTION, vega=10, vanna=1, volga=2),
            HedgeTool(tool_type=HedgeToolType.OPTION, vega=5, vanna=2, volga=1),
            HedgeTool(tool_type=HedgeToolType.OPTION, vega=8, vanna=3, volga=4),
        ]
        result = engine.hedge(100, 10, 50, tools, implied_vol=0.25, realized_vol=0.15)
        assert result.vrp_signal == VolatilitySignal.SHORT_VOL

    def test_build_tools(self):
        engine = IVSurfaceDeepHedgeEngine()
        tools = engine.build_option_tools(
            spot=100, strikes=[95, 100, 105],
            maturities=[30 / 365] * 3, ivs=[0.22, 0.20, 0.18],
        )
        assert len(tools) == 3
        assert all(t.vega > 0 for t in tools)

    def test_stats(self):
        engine = IVSurfaceDeepHedgeEngine()
        engine.hedge(100, 10, 50, [], implied_vol=0.2, realized_vol=0.2)
        assert engine.get_stats()["total"] == 1


# ============================================================
# 端到端测试
# ============================================================

class TestEndToEnd:
    def test_full_pipeline(self):
        engine = IVSurfaceDeepHedgeEngine()
        tools = engine.build_option_tools(
            spot=100, strikes=[90, 95, 100, 105, 110],
            maturities=[30 / 365] * 5, ivs=[0.25, 0.22, 0.20, 0.19, 0.18],
        )
        result = engine.hedge(
            portfolio_vega=100, portfolio_vanna=10, portfolio_volga=50,
            tools=tools, implied_vol=0.22, realized_vol=0.15,
        )
        assert len(result.hedge_quantities) == 5
        assert result.vrp_signal == VolatilitySignal.SHORT_VOL
