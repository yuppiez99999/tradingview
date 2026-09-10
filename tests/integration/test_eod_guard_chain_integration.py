"""test_eod_guard_chain_integration.py — EOD 七 Guard 链完整协作集成测试

5 条关键链路 #1: pnl_report → 7 Guard 顺序执行 → trade_plan 修改

设计原则:
    - Mock 外部数据源 (akshare / astock_realtime / ExternalDataManager)
    - 保留模块间真实调用 (RiskGuardIntegrator.run_all_guards → 7 个 guard)
    - 验证 Guard 间状态传递 (L3 优先级覆盖 L2, risk_guard 字段叠加)
    - 不写入真实文件 (mock _save_trade_plan / _write_guard_log)
"""

from unittest.mock import MagicMock

import pytest

from utils.risk_guard_integrator import RiskGuardIntegrator


@pytest.fixture
def isolated_integrator(tmp_path, monkeypatch):
    """文件 IO 全隔离的 RiskGuardIntegrator

    - LOGS_DIR / REPORTS_DIR / TRADE_PLANS_DIR 指向 tmp_path
    - _save_trade_plan / _write_guard_log mock 为 no-op
    - 不读取真实 pnl_report / trade_plan
    """
    monkeypatch.setattr("utils.risk.guards.plan_context.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("utils.risk.guards.plan_context.REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(
        "utils.risk.guards.plan_context.TRADE_PLANS_DIR", tmp_path / "trade_plans"
    )

    integrator = RiskGuardIntegrator(report_date="2026-07-21")

    # 禁用文件写入
    monkeypatch.setattr(integrator, "_save_trade_plan", lambda plan, date: None)
    monkeypatch.setattr(integrator, "_write_guard_log", lambda date: None)

    return integrator


@pytest.fixture
def mock_all_external_data(monkeypatch):
    """mock 所有外部数据源, 返回正常状态 (不触发熔断)

    - astock_realtime: 沪深300 -0.5% (L0)
    - akshare: 涨跌停家数 100 (远低于阈值 2000)
    - ExternalDataManager: S&P500 -0.3% (L0)
    """

    # astock_realtime: 沪深300 微跌
    def _normal_quotes(codes):
        return {"510300": {"price": 4.04, "pre_close": 4.06, "change_pct": -0.5}}

    try:
        monkeypatch.setattr("utils.astock_realtime.get_realtime_quotes", _normal_quotes)
    except (AttributeError, ImportError):
        pass

    # akshare: 涨跌停家数 100 (远低于阈值 2000)
    try:
        import pandas as pd

        mock_df = pd.DataFrame({"涨跌幅": [0.5, -0.3, 1.2, -0.8, 0.0]})
        monkeypatch.setattr("akshare.stock_zh_a_spot_em", lambda: mock_df)
        monkeypatch.setattr("akshare.stock_zh_index_spot_em", lambda: mock_df)
    except (AttributeError, ImportError):
        pass

    # ExternalDataManager: S&P500 微跌
    try:
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_external_source",
            lambda self: (-0.003, 0.001, True),
        )
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_cache",
            lambda self: (None, None, False),
        )
    except (AttributeError, ImportError):
        pass


# ============================================================
# 集成测试: 7 Guard 链顺序执行
# ============================================================


class TestEODGuardChainExecution:
    """EOD 七 Guard 链集成测试

    链路顺序 (run_all_guards):
        [1/7] KillSwitch (保证金熔断)
        [2/7] 大盘熔断 (P1-H)
        [3/7] 流动性危机 (P1-J)
        [4/7] 隔夜跳空 (P1-I)
        [5/7] 回撤检查
        [6/7] 波动率控制
        [7/7] 对冲执行 + 认沽保护 + 相关性对冲 + 去重
    """

    @pytest.mark.integration
    def test_all_seven_guards_execute_without_crash(
        self,
        isolated_integrator,
        mock_all_external_data,
        sample_pnl_report_full,
        sample_trade_plan,
        monkeypatch,
    ):
        """集成测试: 7 个 Guard 必须全部执行, 不能因单 Guard 崩溃中断"""
        integrator = isolated_integrator

        # Mock _load_pnl_report / _load_next_trade_plan
        monkeypatch.setattr(
            integrator, "_load_pnl_report", lambda: sample_pnl_report_full
        )
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        # 执行完整链路
        plan = integrator.run_all_guards(next_trade_date="2026-07-22")

        # 必须返回 plan (即使某些 guard 内部失败)
        assert plan is not None
        assert "risk_guard" in plan

    @pytest.mark.integration
    def test_guard_failure_does_not_break_chain(
        self,
        isolated_integrator,
        mock_all_external_data,
        sample_pnl_report_full,
        sample_trade_plan,
        monkeypatch,
    ):
        """单个 Guard 崩溃时, 后续 Guard 必须继续执行

        模拟 KillSwitch 崩溃, 验证后续 6 个 Guard 仍执行
        """
        integrator = isolated_integrator
        monkeypatch.setattr(
            integrator, "_load_pnl_report", lambda: sample_pnl_report_full
        )
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        # 让 guard_kill_switch 抛异常
        def _crash_kill_switch(pnl, plan):
            raise RuntimeError("模拟 KillSwitch 崩溃")

        monkeypatch.setattr(integrator, "guard_kill_switch", _crash_kill_switch)

        # 执行链路 — 不应中断
        plan = integrator.run_all_guards(next_trade_date="2026-07-22")

        # kill_switch_error 应被记录
        assert "kill_switch_error" in plan.get("risk_guard", {})

        # 后续 guard 应继续执行 (大盘熔断 / 流动性危机 / 隔夜跳空 等)
        # 至少有部分 risk_guard 字段被填充
        rg = plan["risk_guard"]
        assert "last_run" in rg, "后续 Guard 应继续执行, last_run 时间戳应被写入"

    @pytest.mark.integration
    def test_l3_priority_overrides_l2_in_chain(
        self,
        isolated_integrator,
        sample_pnl_report_full,
        sample_trade_plan,
        monkeypatch,
    ):
        """L3 (后触发) 应覆盖 L2 (先触发) 的 circuit_level

        模拟: KillSwitch 触发 L2 (WARNING) → 大盘熔断触发 L3 (CRITICAL)
        期望: 最终 circuit_level=CRITICAL (L3 优先)
        """
        integrator = isolated_integrator
        monkeypatch.setattr(
            integrator, "_load_pnl_report", lambda: sample_pnl_report_full
        )
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        # Mock KillSwitch 返回 L2
        mock_ks = MagicMock()
        mock_ks._estimate_margin_from_positions.return_value = 0.78
        mock_ks.check_margin_status.return_value = {
            "level": 2,
            "margin_usage_ratio": 0.78,
            "can_trade": False,
            "can_open": False,
            "action": "L2",
        }
        mock_ks.check_concentration.return_value = {"level": "OK"}
        monkeypatch.setattr("utils.kill_switch.KillSwitch", lambda *a, **kw: mock_ks)

        # Mock 大盘熔断返回 L3
        mock_mcb = MagicMock()
        mock_mcb.check_market_status.return_value = {
            "level": 3,
            "hs300_change_pct": -0.08,
            "actions": ["halt_all_trading"],
            "data_source": "astock_realtime",
            "can_trade": False,
            "can_open": False,
        }

        # apply_to_plan 真实执行 L3 清空逻辑 (必须 return plan, 否则 side_effect 返回 None)
        def _apply_l3(plan, status):
            plan.setdefault("execution_plan", {})
            plan["execution_plan"]["morning_orders"] = []
            plan["execution_plan"]["afternoon_orders"] = []
            plan.setdefault("market_state", {})
            plan["market_state"]["circuit_level"] = "CRITICAL"
            plan["market_state"]["halt_all_trading"] = True
            return plan

        mock_mcb.apply_to_plan.side_effect = _apply_l3
        monkeypatch.setattr(
            "utils.market_circuit_breaker.MarketCircuitBreaker",
            lambda *a, **kw: mock_mcb,
        )

        plan = integrator.run_all_guards(next_trade_date="2026-07-22")

        # 最终 circuit_level 应为 CRITICAL (L3 覆盖 L2)
        assert plan["market_state"]["circuit_level"] == "CRITICAL"
        # 所有订单应被清空 (L3 行为)
        assert plan["execution_plan"]["morning_orders"] == []
        assert plan["execution_plan"]["afternoon_orders"] == []
