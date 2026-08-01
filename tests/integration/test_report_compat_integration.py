# -*- coding: utf-8 -*-
"""test_report_compat_integration.py — 真实报告 → RiskGuardIntegrator 数据流集成测试

5 条关键链路 #4: daily_pnl_report_*.json → _load_pnl_report → _extract_positions/_summary → guard_kill_switch

设计原则:
    - 使用真实生产报告 (v8.3_institutional/reports/) 作为黄金数据
    - 验证报告字段缺失 (None) 时 Guard 链路的鲁棒性
    - 不修改真实报告文件 (只读)
"""
import pytest
from pathlib import Path

from utils.risk_guard_integrator import RiskGuardIntegrator


@pytest.fixture
def integrator_with_real_reports(tmp_path, monkeypatch):
    """使用真实 REPORTS_DIR, 但隔离 LOGS / TRADE_PLANS"""
    project_root = Path(__file__).resolve().parent.parent.parent
    real_reports = project_root / "v8.3_institutional" / "reports"

    monkeypatch.setattr(
        "utils.risk_guard_integrator.REPORTS_DIR", real_reports
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.LOGS_DIR", tmp_path / "logs"
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.TRADE_PLANS_DIR", tmp_path / "trade_plans"
    )

    integrator = RiskGuardIntegrator(report_date="2026-07-21")
    monkeypatch.setattr(integrator, "_save_trade_plan", lambda plan, date: None)
    monkeypatch.setattr(integrator, "_write_guard_log", lambda date: None)

    return integrator


# ============================================================
# 集成测试: 真实报告数据流
# ============================================================


class TestRealReportDataFlow:
    """真实生产报告 → RiskGuardIntegrator 数据流"""

    @pytest.mark.integration
    def test_load_real_pnl_report_succeeds(self, integrator_with_real_reports):
        """加载真实 pnl_report 必须成功"""
        report = integrator_with_real_reports._load_pnl_report()

        if report is None:
            pytest.skip("真实报告 daily_pnl_report_2026-07-21.json 不存在")

        assert isinstance(report, dict)
        # 真实报告应含 portfolio_pnl 字段
        assert "portfolio_pnl" in report or "summary" in report

    @pytest.mark.integration
    def test_extract_positions_from_real_report(
        self, integrator_with_real_reports, real_pnl_report
    ):
        """从真实报告提取 positions 必须返回非空 list"""
        positions = integrator_with_real_reports._extract_positions(real_pnl_report)

        assert isinstance(positions, list)
        assert len(positions) >= 20, f"真实报告应含 26 标的, 实际: {len(positions)}"

        # 每个 position 应含 code 字段
        for pos in positions[:3]:
            assert isinstance(pos, dict)
            assert "code" in pos or "symbol" in pos

    @pytest.mark.integration
    def test_extract_summary_from_real_report(
        self, integrator_with_real_reports, real_pnl_report
    ):
        """从真实报告提取 summary 必须含关键字段"""
        summary = integrator_with_real_reports._extract_summary(real_pnl_report)

        assert isinstance(summary, dict)
        # 关键字段
        assert "total_cost" in summary
        assert "total_market_value" in summary

    @pytest.mark.integration
    def test_guard_kill_switch_with_real_report(
        self, integrator_with_real_reports, real_pnl_report, sample_trade_plan
    ):
        """guard_kill_switch 在真实报告下必须不抛异常

        即使 margin_used/total_equity 字段为 None 或缺失, 也不能崩溃
        """
        plan = integrator_with_real_reports.guard_kill_switch(
            real_pnl_report, sample_trade_plan.copy()
        )

        # 必须返回 plan (不能崩溃)
        assert plan is not None
        # risk_guard.kill_switch 必须被填充
        assert "kill_switch" in plan.get("risk_guard", {})

    @pytest.mark.integration
    def test_run_all_guards_with_real_report(
        self, integrator_with_real_reports, real_pnl_report, sample_trade_plan, monkeypatch
    ):
        """完整 run_all_guards 在真实报告下必须不中断"""
        monkeypatch.setattr(
            integrator_with_real_reports, "_load_pnl_report",
            lambda: real_pnl_report
        )
        monkeypatch.setattr(
            integrator_with_real_reports, "_load_next_trade_plan",
            lambda date: sample_trade_plan
        )

        # 必须不抛异常完成执行
        plan = integrator_with_real_reports.run_all_guards(
            next_trade_date="2026-07-22"
        )

        assert plan is not None
        assert "risk_guard" in plan
        assert "last_run" in plan["risk_guard"]


# ============================================================
# 集成测试: 报告字段缺失的鲁棒性
# ============================================================


class TestReportFieldMissingRobustness:
    """报告字段缺失 (None / 不存在) 时的鲁棒性"""

    @pytest.mark.integration
    @pytest.mark.bug("P0-D")
    def test_p0d_none_margin_in_real_report_handled(
        self, integrator_with_real_reports, sample_pnl_report_broken_p0d,
        sample_trade_plan, monkeypatch
    ):
        """P0-D: 真实报告 margin_used=None 时不能崩溃

        模拟生产环境报告字段异常, 验证 fallback 路径
        """
        # Mock KillSwitch 的 _estimate_margin_from_positions 返回固定值
        # 注: 不能 patch 类的 __init__ (MagicMock 不允许), 直接 patch 方法本身
        from utils.kill_switch import KillSwitch
        monkeypatch.setattr(
            KillSwitch, "_estimate_margin_from_positions",
            lambda self: 0.40
        )

        plan = integrator_with_real_reports.guard_kill_switch(
            sample_pnl_report_broken_p0d, sample_trade_plan.copy()
        )

        # 必须不抛异常, 且 risk_guard.kill_switch 被填充
        assert plan is not None
        assert "kill_switch" in plan["risk_guard"]

    @pytest.mark.integration
    def test_empty_pnl_report_falls_back_gracefully(
        self, integrator_with_real_reports, sample_trade_plan
    ):
        """空 pnl_report 时, guard_kill_switch 必须降级处理, 不崩溃"""
        empty_report = {"portfolio_pnl": {"summary": {}}}

        plan = integrator_with_real_reports.guard_kill_switch(
            empty_report, sample_trade_plan.copy()
        )

        assert plan is not None
        # 即使数据为空, risk_guard.kill_switch 仍应被填充 (使用保守值)
        assert "kill_switch" in plan["risk_guard"]
