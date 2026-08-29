"""
机构级模块单元测试
- Black-Litterman 组合优化器
- TCA 交易后成本分析引擎
- Barra 风险因子暴露分解
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from utils.barra_risk_decomposer import BarraRiskDecomposer  # noqa: E402
from utils.black_litterman_optimizer import BlackLittermanOptimizer  # noqa: E402
from utils.black_litterman_optimizer import View as BLView  # noqa: E402
from utils.tca_engine import BenchmarkPrices, FillRecord, TCAManager  # noqa: E402


def test_black_litterman_optimizer():
    """测试 1: Black-Litterman 组合优化器"""
    print("\n" + "=" * 60)
    print("测试 1: Black-Litterman 组合优化器")
    print("=" * 60)

    optimizer = BlackLittermanOptimizer(risk_aversion=2.5, tau=0.05)

    # 3 标的 + 协方差矩阵
    assets = ["600519", "000858", "601318"]
    market_weights = [0.5, 0.3, 0.2]
    np.random.seed(42)
    returns = np.random.randn(100, 3) * 0.02
    cov = np.cov(returns.T)

    # 测试 1a: 无观点
    result_a = optimizer.optimize(
        assets=assets,
        market_weights=market_weights,
        cov_matrix=cov,
        views=None,
        risk_free_rate=0.03,
    )
    print(
        f"  无观点: w_BL={result_a.optimal_weights}, Sharpe={result_a.sharpe_ratio:.3f}"
    )
    assert len(result_a.optimal_weights) == 3
    assert abs(result_a.optimal_weights.sum() - 1.0) < 1e-6, "权重应归一化"
    assert result_a.effective_n > 0

    # 测试 1b: 有观点 (绝对观点)
    views = [
        BLView(
            type="absolute",
            assets=["600519"],
            weights=[1.0],
            expected_return=0.15,
            confidence=0.7,
        ),
        BLView(
            type="relative",
            assets=["000858", "601318"],
            weights=[1.0, -1.0],
            expected_return=0.03,
            confidence=0.6,
        ),
    ]
    result_b = optimizer.optimize(
        assets=assets,
        market_weights=market_weights,
        cov_matrix=cov,
        views=views,
        risk_free_rate=0.03,
    )
    print(
        f"  有观点: w_BL={result_b.optimal_weights}, Sharpe={result_b.sharpe_ratio:.3f}"
    )
    print(f"  隐含收益 Π={result_b.implied_equilibrium_returns}")
    print(f"  后验收益 E[R]={result_b.posterior_returns}")
    assert len(result_b.posterior_returns) == 3
    # 后验收益应与隐含收益不同 (因为加了观点)
    assert not np.allclose(
        result_b.posterior_returns, result_b.implied_equilibrium_returns
    )

    # 测试 1c: 保存结果
    save_path = PROJECT_ROOT / "tests" / "test_bl_result.json"
    optimizer.save_result(result_b, save_path)
    assert save_path.exists()
    save_path.unlink()

    print("\n✓ 测试 1 通过")
    return True


def test_tca_engine():
    """测试 2: TCA 交易后成本分析引擎"""
    print("\n" + "=" * 60)
    print("测试 2: TCA 交易后成本分析引擎")
    print("=" * 60)

    tca = TCAManager()

    # 构造成交记录: 600519 买入 10000 股, 多笔成交
    fills = [
        FillRecord(
            symbol="600519",
            side="BUY",
            shares=3000,
            price=1500.0,
            timestamp="2026-07-14T09:35",
        ),
        FillRecord(
            symbol="600519",
            side="BUY",
            shares=4000,
            price=1502.0,
            timestamp="2026-07-14T10:00",
        ),
        FillRecord(
            symbol="600519",
            side="BUY",
            shares=3000,
            price=1505.0,
            timestamp="2026-07-14T10:30",
        ),
    ]
    benchmark = BenchmarkPrices(
        decision_price=1498.0,  # 决策价
        arrival_price=1500.0,  # 到达价
        vwap=1503.0,  # 区间 VWAP
        close_price=1508.0,
    )

    report = tca.analyze(
        fills=fills,
        benchmark=benchmark,
        order_shares=10000,
        interval_volume=200000,
    )
    print(f"  {report.symbol} ({report.side}):")
    print(f"    avg_exec_price = {report.avg_exec_price:.2f}")
    print(f"    IS cost = {report.is_cost_bps:.1f} bps")
    print(f"    VWAP dev = {report.vwap_deviation_bps:.1f} bps")
    print(f"    market impact = {report.market_impact_bps:.1f} bps")
    print(f"    timing cost = {report.timing_cost_bps:.1f} bps")
    print(f"    fill_rate = {report.fill_rate:.1%}")
    print(f"    participation = {report.participation_rate:.1%}")
    print(f"    grade = {report.quality_grade}")
    print(f"    issues = {report.issues}")

    # 验证
    assert report.symbol == "600519"
    assert report.side == "BUY"
    assert report.total_shares == 10000
    assert 1495 <= report.avg_exec_price <= 1510
    # IS 应为正 (买入价高于决策价)
    assert report.is_cost_bps > 0
    assert 0 <= report.fill_rate <= 1.0
    assert report.quality_grade in ("A+", "A", "B", "C", "D", "F")

    # 测试批量
    fills_by_symbol = {
        "600519": fills,
        "000858": [
            FillRecord(
                symbol="000858",
                side="SELL",
                shares=5000,
                price=200.0,
                timestamp="2026-07-14T09:40",
            ),
        ],
    }
    benchmarks = {
        "600519": benchmark,
        "000858": BenchmarkPrices(
            decision_price=205.0,
            arrival_price=202.0,
            vwap=201.0,
            close_price=198.0,
        ),
    }
    reports = tca.analyze_batch(fills_by_symbol, benchmarks)
    summary = tca.summarize(reports)
    print(
        f"\n  批量汇总: {summary['n_orders']} 笔, avg IS={summary['avg_is_cost_bps']:.1f}bps"
    )
    assert summary["n_orders"] == 2
    assert "grade_distribution" in summary

    print("\n✓ 测试 2 通过")
    return True


def test_barra_decomposer():
    """测试 3: Barra 风险因子暴露分解"""
    print("\n" + "=" * 60)
    print("测试 3: Barra 风险因子暴露分解")
    print("=" * 60)

    decomposer = BarraRiskDecomposer()

    # 3 标的 + 因子暴露
    symbols = ["600519", "000858", "601318"]
    weights = [0.5, 0.3, 0.2]
    benchmark_weights = [0.4, 0.35, 0.25]
    factor_exposures = {
        "600519": {
            "Size": 1.5,
            "Beta": 0.8,
            "Momentum": 0.3,
            "ResidualVolatility": -0.2,
            "NonLinearSize": 0.1,
            "BookToPrice": -0.5,
            "Liquidity": 0.4,
            "EarningsYield": 0.6,
            "Growth": 0.2,
            "Leverage": -0.3,
        },
        "000858": {
            "Size": 1.2,
            "Beta": 1.1,
            "Momentum": -0.1,
            "ResidualVolatility": 0.3,
            "NonLinearSize": 0.2,
            "BookToPrice": -0.3,
            "Liquidity": 0.5,
            "EarningsYield": 0.4,
            "Growth": 0.3,
            "Leverage": -0.1,
        },
        "601318": {
            "Size": 1.8,
            "Beta": 0.9,
            "Momentum": 0.5,
            "ResidualVolatility": -0.1,
            "NonLinearSize": 0.0,
            "BookToPrice": 0.2,
            "Liquidity": 0.6,
            "EarningsYield": 0.3,
            "Growth": 0.1,
            "Leverage": -0.5,
        },
    }
    factor_returns = {"Momentum": 0.001, "BookToPrice": 0.0005}
    factor_cov = np.eye(10) * 0.01**2
    specific_risks = {"600519": 0.02, "000858": 0.025, "601318": 0.018}
    industries = {"600519": "食品饮料", "000858": "食品饮料", "601318": "非银金融"}

    result = decomposer.decompose(
        symbols=symbols,
        weights=weights,
        benchmark_weights=benchmark_weights,
        factor_exposures=factor_exposures,
        factor_returns=factor_returns,
        factor_cov_matrix=factor_cov,
        stock_specific_risks=specific_risks,
        industries=industries,
        risk_budget=0.05,
    )
    print(f"  主动风险 (TE): {result.active_risk:.2%}")
    print(f"  因子风险: {result.factor_risk:.2%} ({result.factor_risk_pct:.1%})")
    print(f"  个股风险: {result.specific_risk:.2%}")
    print(f"  主动收益: {result.active_return:.4f}")
    print(f"  IR: {result.information_ratio:.3f}")
    print(f"  风险预算利用: {result.risk_budget_utilization:.1%}")
    print(f"  集中因子: {result.concentrated_factors}")
    print(f"  缺失因子: {result.missing_factors}")
    print(f"  行业暴露: {result.industry_exposures}")
    print("\n  10 个风格因子暴露:")
    for fe in result.style_factor_exposures:
        print(
            f"    {fe.factor_name:25s}: exposure={fe.exposure:+.4f}, "
            f"return_contrib={fe.contribution_to_active_return:+.4f}"
        )

    # 验证
    assert len(result.style_factor_exposures) == 10
    assert result.active_risk > 0
    assert result.factor_risk > 0
    assert result.specific_risk > 0
    assert 0 <= result.factor_risk_pct <= 1
    assert 0 <= result.risk_budget_utilization <= 2  # 可能 > 1 表示超预算
    assert "食品饮料" in result.industry_exposures

    # 测试从持仓自动估算
    positions = [
        {"code": "600519", "amount": 100000, "sector": "食品饮料"},
        {"code": "000858", "amount": 60000, "sector": "食品饮料"},
        {"code": "601318", "amount": 40000, "sector": "非银金融"},
    ]
    result_b = decomposer.decompose_from_positions(positions=positions)
    print(
        f"\n  从持仓自动估算: TE={result_b.active_risk:.2%}, IR={result_b.information_ratio:.3f}"
    )
    assert len(result_b.style_factor_exposures) == 10

    print("\n✓ 测试 3 通过")
    return True


if __name__ == "__main__":
    tests = [
        test_black_litterman_optimizer,
        test_tca_engine,
        test_barra_decomposer,
    ]
    results = []
    for t in tests:
        try:
            t()
            results.append((t.__name__, "PASS", ""))
        except Exception as e:
            import traceback

            traceback.print_exc()
            results.append((t.__name__, "FAIL", str(e)[:200]))

    print("\n" + "=" * 60)
    print("最终结果")
    print("=" * 60)
    for name, status, err in results:
        print(f"{status} {name} {err}")
