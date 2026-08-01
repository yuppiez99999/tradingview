# -*- coding: utf-8 -*-
"""
世界顶级对冲基金视角新增 4 模块综合单元测试
=============================================

覆盖:
    1. 执行算法引擎 (TWAP/VWAP/POV/IS/AC/DARK + 自动选择)
    2. P&L 归因分析引擎 (Alpha/Beta/Style/Sector/Timing + 异常检测)
    3. 数据质量监控引擎 (完整性/缺失值/异常值/一致性/延迟/评分)
    4. 多策略协调器 (失效检测/权重调整/冲突检测/风险预算/现金缓冲)
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.execution_algo_engine import (
    ExecutionAlgoEngine, AlgoType,
)
from utils.pnl_attribution_engine import (
    PnLAttributionEngine, AttributionResult,
)
from utils.data_quality_monitor import (
    DataQualityMonitor,
)
from utils.multi_strategy_coordinator import (
    MultiStrategyCoordinator, CoordinationDecision,
)


# ============================================================
# 1. 执行算法引擎测试
# ============================================================
def test_execution_algo_engine():
    print("\n" + "=" * 60)
    print("测试 1: 执行算法引擎")
    print("=" * 60)

    engine = ExecutionAlgoEngine()

    # TWAP
    plan_twap = engine.plan_order(
        algo=AlgoType.TWAP, symbol="300308", side="buy",
        total_shares=10000, duration_minutes=120, slice_minutes=5,
        current_price=35.50, avg_daily_volume=1_500_000,
    )
    assert plan_twap.algo == "TWAP"
    assert plan_twap.total_shares == 10000
    assert plan_twap.slice_count > 0
    # 累计股数 = 总股数
    total_executed = sum(s.target_shares for s in plan_twap.slices)
    assert total_executed == 10000, f"TWAP 总股数错误: {total_executed}"
    print(f"  ✅ TWAP: {plan_twap.slice_count} 片, 总计 {total_executed} 股, 滑点 {plan_twap.expected_slippage_bps:.2f}bps")

    # VWAP
    plan_vwap = engine.plan_order(
        algo=AlgoType.VWAP, symbol="300308", side="buy",
        total_shares=20000, duration_minutes=240, slice_minutes=10,
        current_price=35.50,
    )
    total_vwap = sum(s.target_shares for s in plan_vwap.slices)
    assert total_vwap == 20000, f"VWAP 总股数错误: {total_vwap}"
    print(f"  ✅ VWAP: {plan_vwap.slice_count} 片, 总计 {total_vwap} 股")

    # POV
    plan_pov = engine.plan_order(
        algo=AlgoType.POV, symbol="600519", side="sell",
        total_shares=1000, duration_minutes=120, slice_minutes=10,
        avg_daily_volume=50000, current_price=1695.0,
    )
    total_pov = sum(s.target_shares for s in plan_pov.slices)
    assert total_pov == 1000, f"POV 总股数错误: {total_pov}"
    assert plan_pov.slices[0].participation_rate > 0
    print(f"  ✅ POV: {plan_pov.slice_count} 片, 参与率 {plan_pov.slices[0].participation_rate:.2%}")

    # IS
    plan_is = engine.plan_order(
        algo=AlgoType.IS, symbol="300308", side="buy",
        total_shares=5000, duration_minutes=60, slice_minutes=5,
        current_price=35.50, volatility=0.25, risk_aversion=2.0,
    )
    total_is = sum(s.target_shares for s in plan_is.slices)
    assert total_is == 5000, f"IS 总股数错误: {total_is}"
    # IS 应该 front-loaded (第一片最大)
    assert plan_is.slices[0].target_shares >= plan_is.slices[-1].target_shares, "IS 应 front-loaded"
    print(f"  ✅ IS: {plan_is.slice_count} 片, front-loaded (首片 {plan_is.slices[0].target_shares} >= 末片 {plan_is.slices[-1].target_shares})")

    # AC (Almgren-Chriss)
    plan_ac = engine.plan_order(
        algo=AlgoType.AC, symbol="600519", side="sell",
        total_shares=5000, duration_minutes=120, slice_minutes=5,
        current_price=1695, volatility=0.25, risk_aversion=1.5,
    )
    total_ac = sum(s.target_shares for s in plan_ac.slices)
    assert total_ac == 5000, f"AC 总股数错误: {total_ac}"
    print(f"  ✅ AC: {plan_ac.slice_count} 片, 总计 {total_ac} 股")

    # DARK (暗池)
    plan_dark = engine.plan_order(
        algo=AlgoType.DARK, symbol="300308", side="buy",
        total_shares=5000, duration_minutes=120, slice_minutes=5,
    )
    assert all(s.target_shares <= 100 for s in plan_dark.slices), "DARK 每片应 <= 100"
    print(f"  ✅ DARK: {plan_dark.slice_count} 片, 每片 <= 100 股 (冰山)")

    # 自动选择
    algo_small = engine.select_algo(1000, 1_000_000, "low")
    assert algo_small == AlgoType.TWAP, "小单应选 TWAP"
    algo_large = engine.select_algo(200000, 1_000_000, "low")
    assert algo_large == AlgoType.POV, "大单应选 POV"
    algo_high = engine.select_algo(10000, 1_000_000, "high")
    assert algo_high == AlgoType.IS, "高紧急应选 IS"
    print("  ✅ 自动选择: 小单→TWAP, 大单→POV, 高紧急→IS")

    # 保存
    path = engine.save_plan(plan_twap)
    assert path.exists(), "执行计划文件应存在"
    print(f"  ✅ 计划保存: {path.name}")

    print("\n  结果: 执行算法引擎全部通过 ✅")


# ============================================================
# 2. P&L 归因分析测试
# ============================================================
def test_pnl_attribution_engine():
    print("\n" + "=" * 60)
    print("测试 2: P&L 归因分析引擎")
    print("=" * 60)

    engine = PnLAttributionEngine(risk_free_rate=0.025)

    # 模拟数据
    positions = [
        {"code": "300308", "name": "中际旭创", "weight": 0.15, "sector": "tech",
         "market_value": 750000,
         "style_exposures": {"momentum": 0.8, "growth": 0.7, "valuation": -0.3}},
        {"code": "600519", "name": "贵州茅台", "weight": 0.10, "sector": "consumer",
         "market_value": 500000,
         "style_exposures": {"earnings_quality": 0.9, "valuation": 0.5}},
        {"code": "601088", "name": "中国神华", "weight": 0.12, "sector": "cyclical",
         "market_value": 600000,
         "style_exposures": {"valuation": 0.8, "earnings_quality": 0.7}},
    ]

    portfolio_returns = [0.005, -0.003, 0.008, 0.002, -0.001, 0.004, 0.006, -0.002, 0.003, 0.001]
    benchmark_returns = [0.003, -0.002, 0.005, 0.001, -0.001, 0.002, 0.004, -0.001, 0.002, 0.001]
    market_returns = [0.002, -0.001, 0.004, 0.001, 0.000, 0.002, 0.003, -0.001, 0.001, 0.001]

    factor_returns = {
        "momentum": [0.001, -0.001, 0.002, 0.001, 0.000, 0.001, 0.002, -0.001, 0.001, 0.000],
        "reversal": [-0.001, 0.001, -0.002, -0.001, 0.000, -0.001, -0.002, 0.001, -0.001, 0.000],
        "earnings_quality": [0.0005, 0.0002, 0.0008, 0.0003, 0.0001, 0.0004, 0.0006, -0.0001, 0.0003, 0.0001],
    }

    sector_returns = {
        "tech": [0.008, -0.005, 0.012, 0.003, -0.002, 0.006, 0.010, -0.003, 0.005, 0.002],
        "consumer": [0.002, 0.001, 0.003, 0.001, 0.000, 0.001, 0.002, 0.000, 0.001, 0.001],
        "cyclical": [0.003, -0.002, 0.005, 0.002, -0.001, 0.003, 0.004, -0.001, 0.002, 0.001],
    }

    result = engine.attribute(
        positions=positions,
        portfolio_returns=portfolio_returns,
        benchmark_returns=benchmark_returns,
        market_returns=market_returns,
        factor_returns=factor_returns,
        sector_returns=sector_returns,
        trading_costs=500.0,
        funding_cost=-50.0,
        hedge_pnl=-200.0,
    )

    # 验证字段
    assert isinstance(result, AttributionResult)
    assert result.total_pnl != 0
    assert result.alpha_pnl != 0
    assert len(result.style_factors) == 7, f"应有 7 个风格因子, 实际 {len(result.style_factors)}"
    assert len(result.sector_factors) > 0
    assert isinstance(result.sharpe_ratio, float)
    assert isinstance(result.information_ratio, float)
    assert isinstance(result.tracking_error, float)

    # 验证分解恒等式: Alpha + Beta + Style + Sector + Timing + Hedge + Cost + Funding = Total
    explained = (
        result.alpha_pnl + result.beta_pnl + result.style_pnl + result.sector_pnl +
        result.timing_pnl + result.hedge_pnl + result.trading_cost + result.funding_cost
    )
    diff = abs(explained - result.total_pnl)
    assert diff < 0.01, f"分解不闭合: explained={explained}, total={result.total_pnl}, diff={diff}"
    print(f"  ✅ 分解闭合: 总 P&L={result.total_pnl:.2f}, 分解={explained:.2f}, 误差 {diff:.4f}")
    print(f"  ✅ Alpha={result.alpha_pnl:.0f}, Beta={result.beta_pnl:.0f}, Style={result.style_pnl:.0f}, Sector={result.sector_pnl:.0f}, Timing={result.timing_pnl:.0f}")
    print(f"  ✅ 风格因子 {len(result.style_factors)} 个, 行业因子 {len(result.sector_factors)} 个")
    print(f"  ✅ Sharpe={result.sharpe_ratio:.2f}, IR={result.information_ratio:.2f}, TE={result.tracking_error:.2%}")
    print(f"  ✅ 异常检测: {len(result.anomalies)} 个")

    # 保存
    path = engine.save_report(result)
    assert path.exists(), "归因报告文件应存在"
    print(f"  ✅ 报告保存: {path.name}")

    print("\n  结果: P&L 归因分析引擎全部通过 ✅")


# ============================================================
# 3. 数据质量监控测试
# ============================================================
def test_data_quality_monitor():
    print("\n" + "=" * 60)
    print("测试 3: 数据质量监控引擎")
    print("=" * 60)

    monitor = DataQualityMonitor(max_latency_minutes=30)

    # 正常数据
    normal_data = {
        "300308": {
            "open": 35.50, "high": 36.20, "low": 35.30, "close": 36.10,
            "volume": 1_500_000, "timestamp": datetime.now().isoformat(),
        },
        "002475": {
            "open": 38.20, "high": 38.80, "low": 38.00, "close": 38.50,
            "volume": 2_200_000, "timestamp": datetime.now().isoformat(),
        },
    }
    report = monitor.check_market_data(normal_data, expected_symbols=["300308", "002475"])
    assert report.passed, f"正常数据应通过: score={report.overall_score}"
    assert report.critical_count == 0
    assert report.error_count == 0
    print(f"  ✅ 正常数据: 综合 {report.overall_score:.1f}/100, critical=0, error=0")

    # 异常数据
    abnormal_data = {
        "300308": {
            "open": 35.50, "high": 36.20, "low": 35.30, "close": 36.10,
            "volume": 1_500_000, "timestamp": datetime.now().isoformat(),
        },
        # 异常 1: high < low
        "000001": {
            "open": 12.50, "high": 12.30, "low": 12.80, "close": 12.60,
            "volume": -100, "timestamp": datetime.now().isoformat(),
        },
        # 异常 2: 缺失 close
        "600036": {
            "open": 38.00, "high": 38.50, "low": 37.80,
            "volume": 800000, "timestamp": datetime.now().isoformat(),
        },
        # 异常 3: 延迟
        "601318": {
            "open": 50.00, "high": 50.50, "low": 49.80, "close": 50.20,
            "volume": 1_200_000, "timestamp": (datetime.now() - timedelta(hours=3)).isoformat(),
        },
    }
    expected = ["300308", "000001", "600036", "601318", "缺失标的1"]
    report2 = monitor.check_market_data(abnormal_data, expected_symbols=expected)

    assert not report2.passed, "异常数据不应通过"
    assert report2.critical_count >= 2, f"应有 critical 问题: {report2.critical_count}"
    assert report2.error_count >= 2, f"应有 error 问题: {report2.error_count}"
    print(f"  ✅ 异常数据: 综合 {report2.overall_score:.1f}/100, critical={report2.critical_count}, error={report2.error_count}, warning={report2.warning_count}")

    # 检查异常类型
    categories = {i.category for i in report2.issues}
    assert "completeness" in categories, "应检测到完整性问题"
    assert "missing" in categories, "应检测到缺失值"
    assert "outlier" in categories, "应检测到异常值"
    assert "latency" in categories, "应检测到延迟"
    print(f"  ✅ 异常类型: {categories}")

    # 保存报告
    path = monitor.save_report(report2)
    assert path.exists(), "数据质量报告文件应存在"
    print(f"  ✅ 报告保存: {path.name}")

    print("\n  结果: 数据质量监控引擎全部通过 ✅")


# ============================================================
# 4. 多策略协调器测试
# ============================================================
def test_multi_strategy_coordinator():
    print("\n" + "=" * 60)
    print("测试 4: 多策略协调器")
    print("=" * 60)

    coord = MultiStrategyCoordinator(total_capital=5_000_000)

    # 验证默认 6 策略
    assert len(coord.strategies) == 6, f"应有 6 个策略, 实际 {len(coord.strategies)}"
    assert "stock_long" in coord.strategies
    assert "quant_neutral" in coord.strategies
    assert "cash_management" in coord.strategies
    print(f"  ✅ 默认策略: {list(coord.strategies.keys())}")

    # 验证资金分配
    total_capital = sum(s.capital for s in coord.strategies.values())
    assert total_capital == 5_000_000, f"总资金应 5000000, 实际 {total_capital}"
    print(f"  ✅ 资金分配: 总计 ¥{total_capital:,}")

    # 模拟协调 (正常情况)
    decision = coord.coordinate(
        target_signals={
            "stock_long": {"300308": {"direction": "long"}},
            "quant_neutral": {"600519": {"direction": "long"}},
        },
        current_positions={
            "300308": {"strategy": "stock_long", "weight": 0.04},  # 正常
            "600519": {"strategy": "stock_long", "weight": 0.03},  # 正常
        },
        strategy_pnl={
            "stock_long": 10000,
            "etf_allocation": 2000,
            "quant_neutral": 5000,
            "macro_hedge": -1000,
            "options_tail": -500,
            "cash_management": 200,
        },
        strategy_correlations={
            "stock_long": 0.60,      # 正常
            "etf_allocation": 0.50,
            "quant_neutral": 0.10,
            "macro_hedge": -0.30,
            "options_tail": -0.20,
            "cash_management": 0.00,
        },
    )

    assert isinstance(decision, CoordinationDecision)
    assert decision.total_capital == 5_000_000
    assert len(decision.strategy_weights) == 6
    assert decision.cash_buffer > 0
    assert decision.risk_budget_used > 0
    print(f"  ✅ 正常协调: 已分配 ¥{decision.total_allocated:,.0f}, 现金缓冲 ¥{decision.cash_buffer:,.0f}")
    print(f"  ✅ 冲突 {len(decision.conflicts)} 个, 风险预算 ¥{decision.risk_budget_used:,.0f} / ¥{decision.risk_budget_limit:,.0f}")

    # 模拟策略失效 (相关性过高)
    coord.coordinate(
        strategy_pnl={"stock_long": -100000},  # 大幅亏损
        strategy_correlations={"stock_long": 0.90},  # 相关性过高
    )
    stock_state = coord.strategies["stock_long"]
    assert stock_state.is_degraded, "stock_long 应失效"
    assert stock_state.correlation_to_portfolio > 0.70
    print(f"  ✅ 策略失效检测: stock_long 失效={stock_state.is_degraded}, 原因={stock_state.degradation_reason}")

    # 模拟冲突检测 (相反信号)
    decision3 = coord.coordinate(
        target_signals={
            "stock_long": {"300308": {"direction": "long"}},
            "quant_neutral": {"300308": {"direction": "short"}},  # 相反
        },
        current_positions={
            "300308": {"strategy": "stock_long", "weight": 0.18},  # 超限 5%
        },
    )
    opposite_conflicts = [c for c in decision3.conflicts if c.conflict_type == "opposite_signal"]
    assert len(opposite_conflicts) > 0, "应检测到相反信号冲突"
    over_pos = [c for c in decision3.conflicts if c.conflict_type == "over_position"]
    assert len(over_pos) > 0, "应检测到持仓超限"
    print(f"  ✅ 冲突检测: 相反信号={len(opposite_conflicts)}, 持仓超限={len(over_pos)}")

    # 保存状态
    path = coord.save_state()
    assert path.exists(), "协调器状态文件应存在"
    print(f"  ✅ 状态保存: {path.name}")

    print("\n  结果: 多策略协调器全部通过 ✅")


# ============================================================
# 主函数
# ============================================================
def main():
    print("\n" + "#" * 60)
    print("# 世界顶级对冲基金视角新增 4 模块综合测试")
    print("#" * 60)

    tests = [
        test_execution_algo_engine,
        test_pnl_attribution_engine,
        test_data_quality_monitor,
        test_multi_strategy_coordinator,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"\n  ❌ {test.__name__} 失败: {e}")
        except Exception as e:
            failed += 1
            print(f"\n  ❌ {test.__name__} 异常: {type(e).__name__}: {e}")

    print("\n" + "#" * 60)
    print(f"# 测试总结: {passed}/{len(tests)} 通过, {failed} 失败")
    print("#" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
