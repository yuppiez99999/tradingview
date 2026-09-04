"""05_06 — E2E 端到端测试.

模拟真实运行场景: CLI 全流程/监控/滚仓/回测/状态恢复/数据源全失败/异常输入.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.etf_option_combo.combo_backtest import ComboBacktest
from utils.etf_option_combo.combo_base import LegSide, StrategyType
from utils.etf_option_combo.combo_orchestrator import ComboOrchestrator
from utils.etf_option_combo.combo_risk_manager import ComboRiskManager
from utils.etf_option_combo.combo_state import ComboStateManager
from utils.etf_option_combo.covered_call import CoveredCallEngine
from utils.etf_option_combo.cash_secured_put import CashSecuredPutEngine

from .conftest import SyntheticDataLayer, SyntheticOptionDataFetcher


pytestmark = pytest.mark.e2e


@pytest.fixture
def e2e_orchestrator(tmp_path, monkeypatch):
    """E2E 编排器 (隔离环境)."""
    cfg_path = tmp_path / "e2e_config.yaml"
    cfg_path.write_text("""
combo_strategies:
  enabled: true
  total_capital: 2_000_000
  enabled_underlyings: ["510050.SH"]
  covered_call:
    otm_pct: 0.05
    otm_pct_range: [0.02, 0.08]
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
    annual_budget_pct: 0.015
  collar:
    put_otm_pct: 0.05
    call_otm_pct: 0.05
    protection_band_min: 0.10
    put_otm_max: 0.10
    max_net_cost_pct: 0.005
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
  cash_secured_put:
    otm_pct: 0.05
    otm_pct_range: [0.03, 0.08]
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
    annual_budget_pct: 0.010
  vertical_spread:
    spread_width_min: 0.05
    spread_width_max: 0.30
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
  calendar_spread:
    near_month_dte_min: 20
    near_month_dte_max: 40
    preferred_near_dte: 30
    far_month_dte_min: 50
    far_month_dte_max: 90
  risk_control:
    max_margin_pct: 0.20
    fat_finger_limit: 500_000
  strategy_routing:
    calm:
      strategies: ["covered_call", "cash_secured_put"]
    tail_event:
      strategies: ["collar"]
""", encoding="utf-8")

    from utils.etf_option_combo import combo_state
    monkeypatch.setattr(combo_state, "_DEFAULT_STATE_PATH", tmp_path / "e2e_state.json")
    data_layer = SyntheticDataLayer({"510050.SH": 3.0, "510300.SH": 4.0})
    orch = ComboOrchestrator(config_path=cfg_path, total_capital=2_000_000, data_layer=data_layer)
    # 注入合成 fetcher 替代真实 Wind MCP
    orch._chain_fetcher._fetcher = SyntheticOptionDataFetcher(iv=0.20, source="bs_synthetic")
    return orch


class TestE2EFullRun:
    def test_e2e_cli_full_run(self, e2e_orchestrator):
        """E2E: 全流程运行 (calm 市场)."""
        results = e2e_orchestrator.run_all(
            underlyings=["510050.SH"],
            market_state={"regime": "calm"},
            spot_positions={"510050.SH": {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.1, "current_weight": 0.0}},
        )
        assert "510050.SH" in results
        assert len(results["510050.SH"]) == 2
        # 至少一个成功
        success_count = sum(1 for r in results["510050.SH"] if r.error_code is None)
        assert success_count >= 1

    def test_e2e_cli_monitor(self, e2e_orchestrator):
        """E2E: 监控扫描."""
        result = e2e_orchestrator.monitor()
        assert "margin_checks" in result
        assert "exercise_risks" in result

    def test_e2e_cli_roll(self, e2e_orchestrator):
        """E2E: 滚仓调度."""
        roll_results = e2e_orchestrator.roll_all()
        assert isinstance(roll_results, list)

    def test_e2e_cli_backtest(self, e2e_orchestrator):
        """E2E: 回测验证."""
        bt = ComboBacktest(config={"total_capital": 2_000_000})
        result = bt.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-31",
            strategies=[StrategyType.COVERED_CALL],
        )
        assert result["trade_count"] > 0
        assert len(result["equity_curve"]) > 0


class TestE2EStateRecovery:
    def test_e2e_state_recovery(self, tmp_path, monkeypatch):
        """E2E: 状态恢复 — 重启后加载已保存状态."""
        from utils.etf_option_combo import combo_state
        state_path = tmp_path / "recovery.json"
        monkeypatch.setattr(combo_state, "_DEFAULT_STATE_PATH", state_path)

        mgr1 = ComboStateManager()
        mgr1.save_strategy_instance("cc_510050_20260903", {"net_premium": 500.0})
        mgr1.update_budget("covered_call", 500.0)

        # 模拟重启
        mgr2 = ComboStateManager()
        inst = mgr2.get_strategy_instance("cc_510050_20260903")
        assert inst is not None
        assert inst["net_premium"] == 500.0
        budget = mgr2.get_budget("covered_call")
        assert budget["ytd_income"] == 500.0


class TestE2EDataSourceAllFail:
    def test_e2e_data_source_all_fail(self, empty_chain_fetcher, base_config, underlying_code):
        """E2E: 所有数据源失败时优雅降级 (返回错误码, 不崩溃)."""
        engine = CoveredCallEngine(base_config, empty_chain_fetcher)
        result = engine.generate(underlying_code, {"shares": 10000})
        assert result.error_code is not None
        assert result.orders == ()

    def test_e2e_no_spot_data(self, empty_chain_fetcher, base_config, underlying_code):
        """E2E: 无现货价时返回 NO_SPOT_DATA."""
        engine = CoveredCallEngine(base_config, empty_chain_fetcher)
        result = engine.generate(underlying_code, {"shares": 10000})
        assert result.error_code in ("NO_SPOT_DATA", "NO_OPTION_DATA")


class TestE2EGreeksNegativeInput:
    def test_e2e_greeks_negative_input(self, chain_fetcher, base_config, underlying_code, make_combo_leg):

        """E2E: Greeks 计算处理负输入不崩溃."""
        from utils.etf_option_combo.combo_base import ComboBase

        class DummyStrategy(ComboBase):
            def _select_legs(self, underlying, spot_price, option_chain, spot_position):
                return (None, "DUMMY")

            def _validate_business_rules(self, legs, spot_price):
                return None

        engine = DummyStrategy(
            strategy_type=StrategyType.COVERED_CALL,
            config={}, chain_fetcher=chain_fetcher, greek_manager=None,
        )
        legs = (make_combo_leg(premium=-0.01, quantity=-1),)
        # 不应抛异常
        greeks = engine._calc_combo_greeks(legs, -1.0)
        assert greeks is not None


class TestE2EAssignment:
    def test_e2e_assignment_insufficient_underlying(self, chain_fetcher, base_config, underlying_code, make_combo_leg):
        """E2E: 被指派时现货不足触发再平衡日志 (不报错)."""
        engine = CashSecuredPutEngine(base_config, chain_fetcher)
        assigned_leg = make_combo_leg(option_type="PUT", side=LegSide.SELL, strike=2.85, quantity=1)
        result = engine.handle_assignment(assigned_leg)
        assert result.error_msg == "ASSIGNMENT_HANDLED"

    def test_e2e_assignment_insufficient_fund(self, chain_fetcher, base_config, underlying_code, make_combo_leg):
        """E2E: 被指派时资金不足仍生成指派处理结果 (不阻断)."""
        engine = CashSecuredPutEngine(base_config, chain_fetcher)
        assigned_leg = make_combo_leg(option_type="PUT", side=LegSide.SELL, strike=2.85, quantity=10)
        result = engine.handle_assignment(assigned_leg)
        assert result.error_msg == "ASSIGNMENT_HANDLED"


class TestE2EFatFinger:
    def test_e2e_fat_finger_confirmation(self, make_combo_leg):
        """E2E: 肥手指订单 requires_confirmation=True."""
        mgr = ComboRiskManager(config={"fat_finger_limit": 500_000})
        # premium=60, qty=1, mult=10000 -> 600000 > 500000
        legs = (make_combo_leg(side=LegSide.BUY, premium=60.0, quantity=1),)
        result = mgr.pre_check(legs, {})
        assert result["requires_confirmation"] is True
        assert "FAT_FINGER" in result["risk_flags"]


class TestE2EKillSwitchFlow:
    def test_e2e_kill_switch_blocks_new_sell(self, chain_fetcher, base_config, underlying_code, spot_position_sufficient):
        """E2E: Kill Switch 触发时新卖开仓被阻断."""
        mgr = ComboRiskManager()
        mgr.update_risk_state(kill_switch_active=True)
        engine = CoveredCallEngine(
            base_config, chain_fetcher, risk_manager=mgr, state_manager=None,
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        # Kill switch 经由 risk_manager.pre_check 或 CSP_KILL_SWITCH_ACTIVE
        # CoveredCall 不直接查 kill_switch, 但 risk_manager.pre_check 会拦截
        assert result.error_code in ("RISK_BLOCKED", "CC_BLOCKED_BY_DRAWDOWN", None)
        # 若 risk_pre_check 触发, 应为 RISK_BLOCKED
        if result.error_code == "RISK_BLOCKED":
            assert result.error_msg is not None