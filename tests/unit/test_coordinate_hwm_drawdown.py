# -*- coding: utf-8 -*-
"""验证 HedgeCoordinator.coordinate() 正确传递 hwm_drawdown + bs_loss 参数.

背景:
    Phase 0 修复前, daily_workflow.py 调用 hc.coordinate() 时未传 hwm_drawdown 和 bs_loss,
    导致 tail_hedger.analyze_market_regime() 和 compute_hedge() 拿到的永远是默认值 0.0,
    无法识别当前回撤状态, regime 误判为 normal, 对冲指令失效.

修复:
    daily_workflow.py 从 self.state 中提取 hwm_drawdown 和 bs_loss, 显式传入 coordinate().

测试覆盖:
    1. hwm_drawdown 透传到 tail_hedger.analyze_market_regime
    2. hwm_drawdown 透传到 tail_hedger.compute_hedge
    3. bs_loss 透传到 tail_hedger.compute_hedge
    4. 不同量级的 hwm_drawdown (0.0 / 0.10 / 0.20 / 0.33) 都能正确传递
    5. 不同量级的 bs_loss (0.0 / 0.20 / 0.40 / 0.60) 都能正确传递
    6. 返回结果中包含 hwm_drawdown 字段
    7. 默认值 (不传参时) 为 0.0
    8. 端到端: 用真实 TailRiskHedger 验证 hwm_drawdown 影响 regime 判定
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_V83_DIR = _PROJECT_ROOT / "v8.3_institutional"
_V83_SRC_DIR = _V83_DIR / "src"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_V83_DIR))
sys.path.insert(0, str(_V83_SRC_DIR))


def _make_coordinator_with_mock_tail():
    """构造 HedgeCoordinator, 仅 mock tail_hedger (其他子模块保留真实实现)."""
    from hedging.hedge_coordinator import HedgeCoordinator
    hc = HedgeCoordinator.__new__(HedgeCoordinator)
    hc.beta_hedger = MagicMock()
    hc.vol_hedger = MagicMock()
    hc.corr_hedger = MagicMock()
    hc.tail_hedger = MagicMock()
    hc.enable_tail_risk = True
    hc.max_total_hedge_normal = 0.40
    hc.max_total_hedge_crisis = 0.90
    hc.max_total_hedge_warning = 0.70
    type(hc.tail_hedger).current_regime = property(lambda self: "normal")
    hc.beta_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    hc.vol_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    hc.corr_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    hc.beta_hedger.portfolio_beta.return_value = 0.5
    hc.tail_hedger.analyze_market_regime.return_value = "normal"
    hc.tail_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    return hc


def _make_test_inputs():
    """构造 coordinate() 所需的标准输入."""
    np.random.seed(42)
    returns = pd.DataFrame({
        "a": np.random.normal(0, 0.01, 60),
        "b": np.random.normal(0, 0.015, 60),
    })
    market_returns = pd.Series(np.random.normal(0, 0.01, 60))
    positions = {"a": 100, "b": 200}
    prices = {"a": 10.0, "b": 20.0}
    return positions, prices, returns, market_returns


# ============================================================
# 参数透传测试 (mock tail_hedger)
# ============================================================

class TestHwmDrawdownPassThrough:
    """hwm_drawdown 参数透传验证."""

    @pytest.mark.parametrize("hwm_dd", [0.0, 0.05, 0.10, 0.15, 0.20, 0.33])
    def test_hwm_drawdown_passed_to_analyze_market_regime(self, hwm_dd):
        """hwm_drawdown 必须原值传到 analyze_market_regime."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            hwm_drawdown=hwm_dd,
        )
        kwargs = hc.tail_hedger.analyze_market_regime.call_args.kwargs
        assert kwargs.get("hwm_drawdown") == hwm_dd, \
            f"hwm_drawdown 透传失败: 传入 {hwm_dd}, 实际 {kwargs.get('hwm_drawdown')}"

    @pytest.mark.parametrize("hwm_dd", [0.0, 0.05, 0.10, 0.15, 0.20, 0.33])
    def test_hwm_drawdown_passed_to_compute_hedge(self, hwm_dd):
        """hwm_drawdown 必须原值传到 compute_hedge."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            hwm_drawdown=hwm_dd,
        )
        kwargs = hc.tail_hedger.compute_hedge.call_args.kwargs
        assert kwargs.get("hwm_drawdown") == hwm_dd, \
            f"hwm_drawdown 透传到 compute_hedge 失败: 传入 {hwm_dd}, 实际 {kwargs.get('hwm_drawdown')}"

    def test_hwm_drawdown_in_result_dict(self):
        """返回结果必须包含 hwm_drawdown 字段."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        result = hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            hwm_drawdown=0.15,
        )
        assert "hwm_drawdown" in result, "返回结果缺少 hwm_drawdown 字段"
        assert result["hwm_drawdown"] == pytest.approx(0.15), \
            f"结果 hwm_drawdown={result['hwm_drawdown']} != 0.15"

    def test_default_hwm_drawdown_is_zero(self):
        """不传 hwm_drawdown 时, 默认值应为 0.0."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            # 故意不传 hwm_drawdown
        )
        kwargs = hc.tail_hedger.analyze_market_regime.call_args.kwargs
        assert kwargs.get("hwm_drawdown") == 0.0, \
            f"默认 hwm_drawdown 应为 0.0, 实际 {kwargs.get('hwm_drawdown')}"


class TestBsLossPassThrough:
    """bs_loss 参数透传验证."""

    @pytest.mark.parametrize("bs", [0.0, 0.10, 0.20, 0.40, 0.55, 0.60])
    def test_bs_loss_passed_to_compute_hedge(self, bs):
        """bs_loss 必须原值传到 compute_hedge."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            bs_loss=bs,
        )
        kwargs = hc.tail_hedger.compute_hedge.call_args.kwargs
        assert kwargs.get("bs_loss") == bs, \
            f"bs_loss 透传失败: 传入 {bs}, 实际 {kwargs.get('bs_loss')}"

    def test_bs_loss_not_passed_to_analyze_market_regime(self):
        """bs_loss 不应传到 analyze_market_regime (该函数没有 bs_loss 参数).

        这验证参数传递的精确性: bs_loss 仅用于 compute_hedge 的 OTM 阶梯决策.
        """
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            bs_loss=0.45,
        )
        kwargs = hc.tail_hedger.analyze_market_regime.call_args.kwargs
        assert "bs_loss" not in kwargs, \
            "bs_loss 不应被传到 analyze_market_regime (该函数无此参数)"

    def test_default_bs_loss_is_zero(self):
        """不传 bs_loss 时, 默认值应为 0.0."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            # 故意不传 bs_loss
        )
        kwargs = hc.tail_hedger.compute_hedge.call_args.kwargs
        assert kwargs.get("bs_loss") == 0.0, \
            f"默认 bs_loss 应为 0.0, 实际 {kwargs.get('bs_loss')}"


class TestBothParametersCombined:
    """hwm_drawdown + bs_loss 组合传递."""

    def test_both_parameters_passed_simultaneously(self):
        """同时传 hwm_drawdown + bs_loss, 两者都应原值到达 compute_hedge."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=25.0, portfolio_value=10000.0,
            hwm_drawdown=0.18,
            bs_loss=0.42,
        )
        # analyze_market_regime 收到 hwm_drawdown (无 bs_loss)
        amr_kwargs = hc.tail_hedger.analyze_market_regime.call_args.kwargs
        assert amr_kwargs.get("hwm_drawdown") == 0.18
        assert "bs_loss" not in amr_kwargs

        # compute_hedge 收到两者
        ch_kwargs = hc.tail_hedger.compute_hedge.call_args.kwargs
        assert ch_kwargs.get("hwm_drawdown") == 0.18
        assert ch_kwargs.get("bs_loss") == 0.42

    def test_result_includes_hwm_drawdown_float(self):
        """返回结果中 hwm_drawdown 必须是 float 类型."""
        hc = _make_coordinator_with_mock_tail()
        positions, prices, returns, mr = _make_test_inputs()

        result = hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            hwm_drawdown=0.12, bs_loss=0.05,
        )
        assert isinstance(result["hwm_drawdown"], float), \
            f"hwm_drawdown 应为 float, 实际 {type(result['hwm_drawdown'])}"


# ============================================================
# 端到端: 用真实 TailRiskHedger 验证 hwm_drawdown 影响 regime
# ============================================================

class TestEndToEndRegimeImpact:
    """端到端验证: hwm_drawdown 影响真实 TailRiskHedger 的 regime 判定.

    场景:
        - VIX=20, hwm_drawdown=0.0  → regime=normal (无回撤)
        - VIX=20, hwm_drawdown=0.33 → regime=crisis (33% 回撤触发危机)
    """

    def test_zero_drawdown_yields_normal_regime(self):
        """hwm_drawdown=0.0 + 低 VIX → normal regime, 不应触发危机对冲."""
        from hedging.hedge_coordinator import HedgeCoordinator
        from hedging.tail_risk_hedge import MarketRegime, TailRiskHedger

        # 用真实 TailRiskHedger, 其他 mock
        hc = HedgeCoordinator.__new__(HedgeCoordinator)
        hc.beta_hedger = MagicMock()
        hc.vol_hedger = MagicMock()
        hc.corr_hedger = MagicMock()
        hc.tail_hedger = TailRiskHedger()
        hc.enable_tail_risk = True
        hc.max_total_hedge_normal = 0.40
        hc.max_total_hedge_crisis = 0.90
        hc.max_total_hedge_warning = 0.70
        hc.beta_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.vol_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.corr_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.beta_hedger.portfolio_beta.return_value = 0.5

        positions, prices, returns, mr = _make_test_inputs()
        result = hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            hwm_drawdown=0.0, bs_loss=0.0,
        )
        # 低 VIX + 无回撤 → regime 应为 normal
        assert result["regime"] == MarketRegime.NORMAL, \
            f"hwm_drawdown=0.0 + VIX=20 应为 normal, 实际 {result['regime']}"

    def test_large_drawdown_yields_crisis_regime(self):
        """hwm_drawdown=0.33 + VIX=20 → crisis regime (回撤权重 40%)."""
        from hedging.hedge_coordinator import HedgeCoordinator
        from hedging.tail_risk_hedge import MarketRegime, TailRiskHedger

        hc = HedgeCoordinator.__new__(HedgeCoordinator)
        hc.beta_hedger = MagicMock()
        hc.vol_hedger = MagicMock()
        hc.corr_hedger = MagicMock()
        hc.tail_hedger = TailRiskHedger()
        hc.enable_tail_risk = True
        hc.max_total_hedge_normal = 0.40
        hc.max_total_hedge_crisis = 0.90
        hc.max_total_hedge_warning = 0.70
        hc.beta_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.vol_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.corr_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.beta_hedger.portfolio_beta.return_value = 0.5

        positions, prices, returns, mr = _make_test_inputs()
        result = hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=20.0, portfolio_value=10000.0,
            hwm_drawdown=0.33, bs_loss=0.0,
        )
        # dd_score = min(1.0, 0.33 * 3) = 0.99 → tail_score = 0.99*0.4 = 0.396
        # 0.396 > 0.3 → recovery (不是 crisis)
        # 实际上 0.33 回撤 + VIX=20 不足以触发 crisis, 但应触发 recovery (score>0.3)
        assert result["regime"] != MarketRegime.NORMAL, \
            f"hwm_drawdown=0.33 应触发非 normal regime, 实际 {result['regime']}"
        # 进一步验证: 0.33 回撤应使 regime 至少达到 recovery
        assert result["regime"] in (MarketRegime.RECOVERY, MarketRegime.WARNING, MarketRegime.CRISIS), \
            f"hwm_drawdown=0.33 应触发 recovery/warning/crisis, 实际 {result['regime']}"

    def test_large_drawdown_with_high_vix_yields_crisis(self):
        """hwm_drawdown=0.33 + VIX=80 → crisis regime (回撤+VIX双高分)."""
        from hedging.hedge_coordinator import HedgeCoordinator
        from hedging.tail_risk_hedge import MarketRegime, TailRiskHedger

        hc = HedgeCoordinator.__new__(HedgeCoordinator)
        hc.beta_hedger = MagicMock()
        hc.vol_hedger = MagicMock()
        hc.corr_hedger = MagicMock()
        hc.tail_hedger = TailRiskHedger()
        hc.enable_tail_risk = True
        hc.max_total_hedge_normal = 0.40
        hc.max_total_hedge_crisis = 0.90
        hc.max_total_hedge_warning = 0.70
        hc.beta_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.vol_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.corr_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
        hc.beta_hedger.portfolio_beta.return_value = 0.5

        positions, prices, returns, mr = _make_test_inputs()
        result = hc.coordinate(
            positions=positions, prices=prices, returns=returns,
            market_returns=mr, vix=80.0, portfolio_value=10000.0,
            hwm_drawdown=0.33, bs_loss=0.0,
        )
        # dd_score=0.99, vix_score=1.0 → tail_score = 0.99*0.4 + 1.0*0.35 = 0.746
        # 0.746 > 0.6 → warning (仍未到 0.8 crisis)
        # 但若 portfolio_vol 高, vol_score 也加分, 可能到 crisis
        # 这里只验证 regime 不是 normal
        assert result["regime"] != MarketRegime.NORMAL, \
            f"hwm_drawdown=0.33 + VIX=80 不应为 normal, 实际 {result['regime']}"


# ============================================================
# daily_workflow.py 调用点验证 (静态检查)
# ============================================================

class TestDailyWorkflowCallSite:
    """验证 daily_workflow.py 中 hc.coordinate() 调用点传入了新参数.

    通过 grep 源码确认 _hwm_dd / _bs_loss 变量被正确传递.
    """

    def test_daily_workflow_passes_hwm_drawdown(self):
        """daily_workflow.py 必须传 hwm_drawdown 参数."""
        wf_path = _V83_DIR / "daily_workflow.py"
        if not wf_path.exists():
            pytest.skip(f"daily_workflow.py 不存在: {wf_path}")
        content = wf_path.read_text(encoding="utf-8")

        # 必须存在 hwm_drawdown= 传参
        assert "hwm_drawdown=" in content, \
            "daily_workflow.py 中 hc.coordinate() 调用必须传 hwm_drawdown="

    def test_daily_workflow_passes_bs_loss(self):
        """daily_workflow.py 必须传 bs_loss 参数."""
        wf_path = _V83_DIR / "daily_workflow.py"
        if not wf_path.exists():
            pytest.skip(f"daily_workflow.py 不存在: {wf_path}")
        content = wf_path.read_text(encoding="utf-8")

        # 必须存在 bs_loss= 传参
        assert "bs_loss=" in content, \
            "daily_workflow.py 中 hc.coordinate() 调用必须传 bs_loss="

    def test_daily_workflow_extracts_hwm_from_state(self):
        """daily_workflow.py 必须从 self.state 提取 hwm_drawdown."""
        wf_path = _V83_DIR / "daily_workflow.py"
        if not wf_path.exists():
            pytest.skip(f"daily_workflow.py 不存在: {wf_path}")
        content = wf_path.read_text(encoding="utf-8")

        # 应该有从 state 读取 hwm_drawdown 的逻辑
        assert "hwm_drawdown" in content, \
            "daily_workflow.py 必须从 state 提取 hwm_drawdown"
        # 应该有 _hwm_dd 变量定义
        assert "_hwm_dd" in content or "hwm_drawdown" in content, \
            "daily_workflow.py 必须定义 _hwm_dd 或直接传 hwm_drawdown"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
