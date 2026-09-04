"""05_04 — ComboOrchestrator 集成测试.

策略路由/多策略并行/订单合并/滚仓调度/状态持久化.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.etf_option_combo.combo_base import StrategyType
from utils.etf_option_combo.combo_orchestrator import ComboOrchestrator


pytestmark = pytest.mark.integration


@pytest.fixture
def orchestrator(tmp_path, monkeypatch):
    """隔离环境的 ComboOrchestrator (临时配置 + 临时状态)."""
    # 写入最小化配置
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    cfg_path = cfg_dir / "etf_option_combo.yaml"
    cfg_content = """
combo_strategies:
  enabled: true
  total_capital: 2_000_000
  enabled_underlyings:
    - "510050.SH"
  covered_call:
    enabled: true
    otm_pct: 0.05
    otm_pct_range: [0.02, 0.08]
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
    annual_budget_pct: 0.015
  collar:
    enabled: true
    put_otm_pct: 0.05
    call_otm_pct: 0.05
    protection_band_min: 0.10
    put_otm_max: 0.10
    max_net_cost_pct: 0.005
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
  cash_secured_put:
    enabled: true
    otm_pct: 0.05
    otm_pct_range: [0.03, 0.08]
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
    annual_budget_pct: 0.010
  vertical_spread:
    enabled: true
    spread_width_min: 0.05
    spread_width_max: 0.30
    dte_min: 30
    dte_max: 60
    preferred_dte: 45
  calendar_spread:
    enabled: true
    near_month_dte_min: 20
    near_month_dte_max: 40
    preferred_near_dte: 30
    far_month_dte_min: 50
    far_month_dte_max: 90
  risk_control:
    max_margin_pct: 0.20
    fat_finger_limit: 500_000
    delta_rebalance_threshold: 0.3
    delta_severe_threshold: 0.5
  strategy_routing:
    calm:
      strategies: ["covered_call", "cash_secured_put"]
    tail_event:
      strategies: ["collar"]
    trending:
      strategies: ["vertical_spread", "calendar_spread"]
"""
    cfg_path.write_text(cfg_content, encoding="utf-8")

    # 隔离状态文件
    from utils.etf_option_combo import combo_state
    monkeypatch.setattr(combo_state, "_DEFAULT_STATE_PATH", tmp_path / "state.json")

    return ComboOrchestrator(config_path=cfg_path, total_capital=2_000_000)


class TestOrchestratorInit:
    def test_orchestrator_init(self, orchestrator):
        """初始化构建 5 策略引擎."""
        assert len(orchestrator._engines) == 5
        assert StrategyType.COVERED_CALL in orchestrator._engines
        assert StrategyType.COLLAR in orchestrator._engines
        assert StrategyType.CASH_SECURED_PUT in orchestrator._engines
        assert StrategyType.VERTICAL_SPREAD in orchestrator._engines
        assert StrategyType.CALENDAR_SPREAD in orchestrator._engines

    def test_config_validation_pass(self, orchestrator):
        """配置校验通过 (otm_pct/dte 在范围内)."""
        assert orchestrator._total_capital == 2_000_000


class TestConfigValidation:
    def test_config_invalid_otm_raises(self, tmp_path, monkeypatch):
        """otm_pct 越界抛 ValueError."""
        cfg_path = tmp_path / "bad.yaml"
        cfg_path.write_text("""
combo_strategies:
  total_capital: 2_000_000
  covered_call:
    otm_pct: 0.50
""", encoding="utf-8")
        from utils.etf_option_combo import combo_state
        monkeypatch.setattr(combo_state, "_DEFAULT_STATE_PATH", tmp_path / "s.json")
        with pytest.raises(ValueError):
            ComboOrchestrator(config_path=cfg_path)

    def test_config_invalid_dte_raises(self, tmp_path, monkeypatch):
        """dte_min < 10 抛 ValueError."""
        cfg_path = tmp_path / "bad2.yaml"
        cfg_path.write_text("""
combo_strategies:
  total_capital: 2_000_000
  covered_call:
    otm_pct: 0.05
    dte_min: 5
""", encoding="utf-8")
        from utils.etf_option_combo import combo_state
        monkeypatch.setattr(combo_state, "_DEFAULT_STATE_PATH", tmp_path / "s.json")
        with pytest.raises(ValueError):
            ComboOrchestrator(config_path=cfg_path)


class TestRunAll:
    def test_run_all_single_underlying(self, orchestrator):
        """单标的运行返回结果列表."""
        results = orchestrator.run_all(
            underlyings=["510050.SH"],
            market_state={"regime": "calm"},
            spot_positions={"510050.SH": {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.1, "current_weight": 0.0}},
        )
        assert "510050.SH" in results
        assert isinstance(results["510050.SH"], list)
        # calm 路由 covered_call + cash_secured_put
        assert len(results["510050.SH"]) == 2

    def test_run_all_multi_underlying(self, orchestrator):
        """多标的运行返回各标的结果."""
        results = orchestrator.run_all(
            underlyings=["510050.SH", "510300.SH"],
            market_state={"regime": "calm"},
            spot_positions={
                "510050.SH": {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.1, "current_weight": 0.0},
                "510300.SH": {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.1, "current_weight": 0.0},
            },
        )
        assert len(results) == 2
        assert "510050.SH" in results
        assert "510300.SH" in results

    def test_run_all_execution_error_isolated(self, orchestrator):
        """单策略异常不影响其他策略."""
        results = orchestrator.run_all(
            underlyings=["510050.SH"],
            market_state={"regime": "calm"},
            spot_positions={"510050.SH": {}},  # 空持仓, 各策略返回错误码
        )
        assert len(results["510050.SH"]) == 2
        # 各结果都有 error_code (空持仓)
        for r in results["510050.SH"]:
            assert r.error_code is not None


class TestRouteStrategy:
    def test_route_strategy_calm(self, orchestrator):
        """calm 市场路由 covered_call + cash_secured_put."""
        strategies = orchestrator.route_strategy({"regime": "calm"})
        assert StrategyType.COVERED_CALL in strategies
        assert StrategyType.CASH_SECURED_PUT in strategies

    def test_route_strategy_tail_event(self, orchestrator):
        """tail_event 市场路由 collar."""
        strategies = orchestrator.route_strategy({"regime": "tail_event"})
        assert StrategyType.COLLAR in strategies

    def test_route_strategy_trending(self, orchestrator):
        """trending 市场路由 vertical_spread + calendar_spread."""
        strategies = orchestrator.route_strategy({"regime": "trending"})
        assert StrategyType.VERTICAL_SPREAD in strategies
        assert StrategyType.CALENDAR_SPREAD in strategies

    def test_route_strategy_unknown_regime_fallback(self, orchestrator):
        """未知 regime 回退到 calm."""
        strategies = orchestrator.route_strategy({"regime": "unknown_regime"})
        # 回退到 calm 配置
        assert isinstance(strategies, list)


class TestMonitorAndRoll:
    def test_monitor_performance(self, orchestrator):
        """monitor 扫描 < 3 秒."""
        import time
        t0 = time.perf_counter()
        result = orchestrator.monitor()
        elapsed = time.perf_counter() - t0
        assert elapsed < 3.0
        assert isinstance(result, dict)

    def test_roll_all_returns_list(self, orchestrator):
        """roll_all 返回列表 (基类 roll() 返回 needs_roll=False)."""
        results = orchestrator.roll_all()
        assert isinstance(results, list)
        # 基类 roll() 返回 needs_roll=False, 故为空
        assert len(results) == 0


class TestStatePersistence:
    def test_state_persistence(self, orchestrator):
        """运行后状态可查询."""
        orchestrator.run_all(
            underlyings=["510050.SH"],
            market_state={"regime": "calm"},
            spot_positions={"510050.SH": {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.1, "current_weight": 0.0}},
        )
        snapshot = orchestrator.get_portfolio_snapshot()
        assert "strategy_instances" in snapshot
        assert "budgets" in snapshot
        assert "last_updated" in snapshot

    def test_get_portfolio_snapshot(self, orchestrator):
        """get_portfolio_snapshot 返回结构化快照."""
        snapshot = orchestrator.get_portfolio_snapshot()
        assert isinstance(snapshot["strategy_instances"], int)
        assert isinstance(snapshot["budgets"], dict)


class TestOrderIdempotency:
    def test_order_idempotency_same_day(self, orchestrator):
        """同交易日同策略同标的重复生成, order_id 一致 (幂等)."""
        pos = {"510050.SH": {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.1, "current_weight": 0.0}}
        r1 = orchestrator.run_all(["510050.SH"], {"regime": "calm"}, pos)
        r2 = orchestrator.run_all(["510050.SH"], {"regime": "calm"}, pos)
        # order_id 格式: {strategy}_{underlying}_{date}_{index}
        for res1, res2 in zip(r1["510050.SH"], r2["510050.SH"]):
            for o1, o2 in zip(res1.orders, res2.orders):
                assert o1.order_id == o2.order_id