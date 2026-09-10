"""test_pnl_report_compat_unit.py — 盈亏报告数据结构兼容性单元测试

覆盖场景:
    - 完整格式 (daily_pnl_report_YYYY-MM-DD.json): portfolio_pnl.details / portfolio_pnl.summary
    - 简化格式 (daily_pnl_report_YYYYMMDD.json): 顶层 positions / summary
    - 字段缺失/为 None 的鲁棒性 (P0-D 触发条件)

设计原则:
    - 使用 RiskGuardIntegrator._extract_positions / _extract_summary / _get_pnl_summary
      这些方法是 EOD Guard 链路的数据入口, 必须兼容多种报告格式
    - 不依赖真实报告文件, 全部使用 fixture 构造样本
"""

import pytest

from utils.risk_guard_integrator import RiskGuardIntegrator


@pytest.fixture
def integrator(tmp_path, monkeypatch):
    """隔离文件 IO 的 RiskGuardIntegrator"""
    monkeypatch.setattr("utils.risk.guards.plan_context.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("utils.risk.guards.plan_context.REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(
        "utils.risk.guards.plan_context.TRADE_PLANS_DIR", tmp_path / "trade_plans"
    )
    return RiskGuardIntegrator(report_date="2026-07-21")


# ============================================================
# _extract_positions: 三种数据位置兼容性
# ============================================================


class TestExtractPositionsCompatibility:
    """_extract_positions 兼容三种 positions 数据位置"""

    @pytest.mark.unit
    def test_full_format_with_details_list(self, integrator, sample_pnl_report_full):
        """完整格式 1: portfolio_pnl.details (list, 当前生产格式)"""
        positions = integrator._extract_positions(sample_pnl_report_full)

        assert isinstance(positions, list)
        assert len(positions) == 3
        assert positions[0]["code"] == "588080.SH"
        assert positions[1]["code"] == "512880.SH"
        assert positions[2]["code"] == "510050.SH"

    @pytest.mark.unit
    def test_simplified_format_top_level_positions(
        self, integrator, sample_pnl_report_simplified
    ):
        """简化格式: 顶层 positions (list)"""
        positions = integrator._extract_positions(sample_pnl_report_simplified)

        assert isinstance(positions, list)
        assert len(positions) == 2
        assert positions[0]["code"] == "588080.SH"

    @pytest.mark.unit
    def test_full_format_with_positions_dict(self, integrator):
        """完整格式 2: portfolio_pnl.positions (dict) → 转 list"""
        report = {
            "portfolio_pnl": {
                "positions": {
                    "588080.SH": {"code": "588080.SH", "pnl": 20000},
                    "512880.SH": {"code": "512880.SH", "pnl": 15000},
                }
            }
        }
        positions = integrator._extract_positions(report)

        assert isinstance(positions, list)
        assert len(positions) == 2

    @pytest.mark.unit
    def test_empty_report_returns_empty_list(self, integrator):
        """空报告返回空 list, 不抛异常"""
        positions = integrator._extract_positions({})
        assert positions == []

    @pytest.mark.unit
    def test_broken_report_p0d_returns_empty_list(
        self, integrator, sample_pnl_report_broken_p0d
    ):
        """P0-D bug 样本 (margin_used=None) → positions 为空, 但不抛异常"""
        positions = integrator._extract_positions(sample_pnl_report_broken_p0d)
        assert isinstance(positions, list)


# ============================================================
# _extract_summary / _get_pnl_summary: 两种 summary 位置兼容性
# ============================================================


class TestExtractSummaryCompatibility:
    """_extract_summary / _get_pnl_summary 兼容两种 summary 位置"""

    @pytest.mark.unit
    def test_full_format_summary(self, integrator, sample_pnl_report_full):
        """完整格式: portfolio_pnl.summary"""
        summary = integrator._extract_summary(sample_pnl_report_full)

        assert summary["total_cost"] == 1_000_000
        assert summary["margin_used"] == 600_000
        assert summary["total_equity"] == 1_500_000

    @pytest.mark.unit
    def test_simplified_format_summary(self, integrator, sample_pnl_report_simplified):
        """简化格式: 顶层 summary"""
        summary = integrator._extract_summary(sample_pnl_report_simplified)

        assert summary["total_cost"] == 800_000
        assert summary["total_market_value"] == 840_000

    @pytest.mark.unit
    def test_get_pnl_summary_alias(self, integrator, sample_pnl_report_full):
        """_get_pnl_summary 与 _extract_summary 行为一致 (兼容方法)"""
        s1 = integrator._get_pnl_summary(sample_pnl_report_full)
        s2 = integrator._extract_summary(sample_pnl_report_full)

        assert s1 == s2

    @pytest.mark.unit
    def test_empty_report_summary_returns_empty_dict(self, integrator):
        """空报告返回空 dict, 不抛异常"""
        assert integrator._extract_summary({}) == {}
        assert integrator._get_pnl_summary({}) == {}


# ============================================================
# P0-D 触发条件: margin_used/total_equity 为 None
# ============================================================


class TestP0DNoneFieldsRobustness:
    """P0-D 触发条件: margin_used/total_equity 为 None

    这些字段为 None 是 P0-D bug 的根本原因, 必须保证 _extract_summary
    能正确返回 None (而非 0 或抛异常), 由调用方 (guard_kill_switch) 处理 fallback
    """

    @pytest.mark.unit
    @pytest.mark.bug("P0-D")
    def test_p0d_none_margin_used_preserved_in_summary(
        self, integrator, sample_pnl_report_broken_p0d
    ):
        """P0-D: margin_used=None 必须被原样保留 (不能默认为 0)

        原代码 pnl_summary.get('margin_used', 0) 在字段存在但值为 None 时返回 None,
        导致 None/None 抛 TypeError. _extract_summary 不做转换, 只确保返回 dict.
        """
        summary = integrator._extract_summary(sample_pnl_report_broken_p0d)

        # summary 应能正常返回 (不抛异常)
        assert isinstance(summary, dict)
        # margin_used=None 必须保留 (调用方判断 None 后 fallback)
        assert summary.get("margin_used") is None
        assert summary.get("total_equity") is None

    @pytest.mark.unit
    def test_real_pnl_report_structure(self, integrator, real_pnl_report):
        """真实生产报告 (26 标的完整格式) 必须能被正确解析

        黄金数据: v8.3_institutional/reports/daily_pnl_report_2026-07-21.json
        """
        positions = integrator._extract_positions(real_pnl_report)
        summary = integrator._extract_summary(real_pnl_report)

        # 真实报告应至少有 20 个标的
        assert isinstance(positions, list)
        assert len(positions) >= 20, f"真实报告应含 26 标的, 实际: {len(positions)}"

        # summary 必须含 total_cost / total_market_value
        assert "total_cost" in summary
        assert "total_market_value" in summary
