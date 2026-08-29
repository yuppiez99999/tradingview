"""
ETF 跌停+期货流动性枯竭压力测试场景单元测试 (P2-增强, v8.4 2026-07-30)
=========================================================================
验证新增的 2 个流动性风险压力测试场景:

    1. "ETF跌停+期货流动性枯竭 (双重流动性陷阱)"
    2. "期货期权流动性双重枯竭 (对冲瘫痪)"

以及 ShockFactors 新增字段的正确性:
    - etf_limit_down_pct
    - futures_liquidity_dry_up
    - hedge_slippage_bps
    - put_premium_spike

设计原则:
    - 不依赖磁盘文件
    - AAA 模式: Arrange → Act → Assert
    - 验证向后兼容性 (现有 8 个场景不受影响)
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.stress_test_scenario_library import (  # noqa: E402
    ShockFactors,
    StressScenario,
    StressTestEngine,
    _build_default_scenarios,
)


# ============================================================
# 测试夹具
# ============================================================
def _make_etf_heavy_portfolio():
    """构造 ETF 持仓为主的组合 (模拟本系统实际配置)"""
    return [
        {
            "code": "510050.SH",
            "name": "上证50ETF",
            "amount": 320000,
            "sector": "宽基",
            "style": "宽基",
            "type": "ETF",
        },
        {
            "code": "588080.SH",
            "name": "科创50ETF",
            "amount": 42353,
            "sector": "科技",
            "style": "科技",
            "type": "ETF",
        },
        {
            "code": "512880.SH",
            "name": "证券ETF",
            "amount": 145454,
            "sector": "金融",
            "style": "金融",
            "type": "ETF",
        },
        {
            "code": "518880.SH",
            "name": "黄金ETF",
            "amount": 240000,
            "sector": "资源",
            "style": "资源",
            "type": "ETF",
        },
        {
            "code": "512170.SH",
            "name": "医疗ETF",
            "amount": 192000,
            "sector": "医药",
            "style": "医药",
            "type": "ETF",
        },
        {
            "code": "600519.SH",
            "name": "贵州茅台",
            "amount": 100000,
            "sector": "消费",
            "style": "价值",
            "type": "STOCK",
        },
        {
            "code": "IF2509.CFFEX",
            "name": "IF期货空头",
            "amount": 500000,
            "sector": "对冲",
            "style": "对冲",
            "type": "FUTURES",
        },
    ]


# ============================================================
# 测试用例
# ============================================================


class TestLiquidityStressScenarios:
    """流动性风险压力测试场景测试套件"""

    def test_1_new_scenarios_added(self):
        """测试 1: 新增的 2 个流动性场景已添加到默认场景库

        场景总数应从 8 增加到 10
        """
        scenarios = _build_default_scenarios()
        names = [s.name for s in scenarios]

        assert (
            "ETF跌停+期货流动性枯竭 (双重流动性陷阱)" in names
        ), "必须包含 ETF 跌停+期货流动性枯竭场景"
        assert (
            "期货期权流动性双重枯竭 (对冲瘫痪)" in names
        ), "必须包含 期货期权流动性双重枯竭场景"
        assert len(scenarios) == 10, f"场景总数应为 10, 实际 {len(scenarios)}"

    def test_2_new_shock_factors_fields_default_zero(self):
        """测试 2: 新增的 ShockFactors 字段默认值为 0 (向后兼容)"""
        # 构造一个不指定新字段的 ShockFactors
        shocks = ShockFactors(equity_market=-0.20)
        assert shocks.etf_limit_down_pct == 0.0
        assert shocks.futures_liquidity_dry_up == 0.0
        assert shocks.hedge_slippage_bps == 0.0
        assert shocks.put_premium_spike == 0.0

    def test_3_existing_scenarios_backward_compatible(self):
        """测试 3: 现有 8 个场景不受新字段影响 (向后兼容)

        验证前 8 个场景的 etf_limit_down_pct = 0
        """
        scenarios = _build_default_scenarios()
        # 前 8 个是原有场景
        for s in scenarios[:8]:
            assert (
                s.shocks.etf_limit_down_pct == 0.0
            ), f"场景 {s.name} 的 etf_limit_down_pct 应为 0 (向后兼容)"
            assert (
                s.shocks.futures_liquidity_dry_up == 0.0
            ), f"场景 {s.name} 的 futures_liquidity_dry_up 应为 0 (向后兼容)"

    def test_4_etf_limit_down_scenario_values(self):
        """测试 4: ETF跌停场景的关键参数正确"""
        scenarios = _build_default_scenarios()
        scenario = next(s for s in scenarios if "ETF跌停" in s.name)

        assert scenario.severity == "extreme"
        assert (
            scenario.shocks.etf_limit_down_pct == 0.60
        ), f"ETF 跌停比例应为 0.60, 实际 {scenario.shocks.etf_limit_down_pct}"
        assert (
            scenario.shocks.futures_liquidity_dry_up == 0.80
        ), f"期货流动性枯竭应为 0.80, 实际 {scenario.shocks.futures_liquidity_dry_up}"
        assert (
            scenario.shocks.hedge_slippage_bps == 200
        ), f"对冲滑点应为 200bps, 实际 {scenario.shocks.hedge_slippage_bps}"
        assert scenario.shocks.put_premium_spike == 2.5
        assert scenario.shocks.volatility_equity == 5.0
        # ETF 跌停场景大盘跌幅应较大 (>= 20%)
        assert scenario.shocks.equity_market <= -0.20

    def test_5_futures_options_dry_up_scenario_values(self):
        """测试 5: 期货期权流动性双重枯竭场景的关键参数正确"""
        scenarios = _build_default_scenarios()
        scenario = next(s for s in scenarios if "期货期权流动性" in s.name)

        assert scenario.severity == "extreme"
        assert (
            scenario.shocks.futures_liquidity_dry_up == 1.0
        ), "期货流动性应完全枯竭 (1.0)"
        assert scenario.shocks.hedge_slippage_bps == 500, "对冲滑点应为 500bps (5%)"
        assert scenario.shocks.put_premium_spike == 4.0, "Put 权利金应飙升 4 倍"
        assert scenario.shocks.volatility_equity == 6.0

    def test_6_etf_limit_down_impacts_pnl(self):
        """测试 6: ETF 跌停场景应产生额外的流动性损失

        对比: 同样持仓, 有 ETF 跌停 vs 无 ETF 跌停, PnL 应更差
        """
        positions = _make_etf_heavy_portfolio()
        engine = StressTestEngine()
        total_value = 1000000  # 100 万

        # 场景 A: 有 ETF 跌停
        scenario_a = StressScenario(
            name="test_with_etf_limit_down",
            description="test",
            start_date="",
            end_date="",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.20,
                etf_limit_down_pct=0.60,  # 60% ETF 跌停
            ),
        )

        # 场景 B: 无 ETF 跌停 (其他参数相同)
        scenario_b = StressScenario(
            name="test_without_etf_limit_down",
            description="test",
            start_date="",
            end_date="",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.20,
                etf_limit_down_pct=0.0,  # 无跌停
            ),
        )

        result_a = engine.run_scenario(scenario_a, positions, total_value)
        result_b = engine.run_scenario(scenario_b, positions, total_value)

        # 场景 A 的损失应大于场景 B (因为额外有 ETF 跌停损失)
        assert (
            result_a.portfolio_pnl < result_b.portfolio_pnl
        ), f"有 ETF 跌停的场景损失应更大: A={result_a.portfolio_pnl}, B={result_b.portfolio_pnl}"

        # 流动性损失应被记录在 by_factor["liquidity"] 中
        assert "liquidity" in result_a.by_factor, "应有 liquidity 因子"
        assert result_a.by_factor["liquidity"] < 0, "ETF 跌停的流动性损失应为负值"

        # 场景 B 不应有 liquidity 因子 (或为 0)
        assert result_b.by_factor.get("liquidity", 0.0) == 0.0

    def test_7_futures_dry_up_amplifies_var(self):
        """测试 7: 期货流动性枯竭应放大 VaR (持有期延长效应)

        对比: 有期货流动性枯竭 vs 无, VaR_after 应更大
        """
        positions = _make_etf_heavy_portfolio()
        engine = StressTestEngine()
        total_value = 1000000

        # 场景 A: 有期货流动性枯竭
        scenario_a = StressScenario(
            name="test_with_futures_dry_up",
            description="test",
            start_date="",
            end_date="",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.20,
                volatility_equity=3.0,
                futures_liquidity_dry_up=1.0,  # 完全枯竭
            ),
        )

        # 场景 B: 无期货流动性枯竭
        scenario_b = StressScenario(
            name="test_without_futures_dry_up",
            description="test",
            start_date="",
            end_date="",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.20,
                volatility_equity=3.0,
                futures_liquidity_dry_up=0.0,  # 正常
            ),
        )

        result_a = engine.run_scenario(scenario_a, positions, total_value)
        result_b = engine.run_scenario(scenario_b, positions, total_value)

        # 场景 A 的 VaR_after 应大于场景 B (因为持有期延长)
        assert (
            result_a.var_after > result_b.var_after
        ), f"期货流动性枯竭应放大 VaR: A={result_a.var_after}, B={result_b.var_after}"

        # VaR 放大倍数应约为 sqrt(5) ≈ 2.24 (1 + 1.0 * 4 = 5 天)
        var_ratio = (
            result_a.var_after / result_b.var_after if result_b.var_after > 0 else 0
        )
        assert (
            2.0 < var_ratio < 2.5
        ), f"VaR 放大倍数应约为 sqrt(5)≈2.24, 实际 {var_ratio:.2f}"

    def test_8_breach_reason_includes_liquidity_info(self):
        """测试 8: breach_reason 应包含流动性信息"""
        positions = _make_etf_heavy_portfolio()
        engine = StressTestEngine(risk_threshold=-0.05)  # 设置 -5% 阈值, 容易触发
        total_value = 1000000

        scenarios = _build_default_scenarios()
        etf_scenario = next(s for s in scenarios if "ETF跌停" in s.name)

        result = engine.run_scenario(etf_scenario, positions, total_value)

        # 应触发 breach
        assert result.is_breach, "ETF跌停场景应触发 breach"
        # breach_reason 应包含 ETF 跌停信息
        assert (
            "ETF 跌停" in result.breach_reason
            or "etf_limit_down" in result.breach_reason.lower()
        ), f"breach_reason 应包含 ETF 跌停信息, 实际: {result.breach_reason}"
        # breach_reason 应包含期货流动性枯竭信息
        assert (
            "期货流动性" in result.breach_reason
            or "futures_liquidity" in result.breach_reason.lower()
        ), f"breach_reason 应包含期货流动性信息, 实际: {result.breach_reason}"

    def test_9_full_scenario_run_no_error(self):
        """测试 9: 对完整组合运行所有 10 个场景, 无异常

        集成测试: 验证新增场景与现有场景一起运行不会崩溃
        """
        positions = _make_etf_heavy_portfolio()
        engine = StressTestEngine()
        total_value = 1000000

        results = engine.run_all_scenarios(positions, total_value)

        assert len(results) == 10, f"应返回 10 个结果, 实际 {len(results)}"

        # 所有结果都应有合法的 pnl
        for r in results:
            assert isinstance(
                r.portfolio_pnl, (int, float)
            ), f"场景 {r.scenario_name} 的 pnl 应为数字"
            assert isinstance(
                r.portfolio_return, (int, float)
            ), f"场景 {r.scenario_name} 的 return 应为数字"

        # 新增的 2 个场景应比"温和"场景损失更大
        new_scenarios = [
            r
            for r in results
            if "ETF跌停" in r.scenario_name or "期货期权" in r.scenario_name
        ]
        assert len(new_scenarios) == 2
        for r in new_scenarios:
            assert (
                r.portfolio_return < -0.10
            ), f"流动性场景 {r.scenario_name} 损失应 < -10%, 实际 {r.portfolio_return:.2%}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
