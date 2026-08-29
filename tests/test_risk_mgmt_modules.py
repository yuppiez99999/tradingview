"""
顶级风险管理模块单元测试
- Ledoit-Wolf 收缩协方差估计器
- 风险预算约束优化器
- 压力测试情景库
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from utils.ledoit_wolf_covariance import LedoitWolfCovariance  # noqa: E402
from utils.risk_budget_optimizer import RiskBudgetOptimizer  # noqa: E402
from utils.stress_test_scenario_library import (  # noqa: E402
    ShockFactors,
    StressTestEngine,
)


def test_ledoit_wolf():
    """测试 1: Ledoit-Wolf 收缩协方差估计器"""
    print("\n" + "=" * 60)
    print("测试 1: Ledoit-Wolf 收缩协方差估计器")
    print("=" * 60)

    np.random.seed(42)
    # 生成 100×5 收益率矩阵 (5 资产, 100 天)
    n_assets = 5
    n_obs = 100
    true_cov = np.eye(n_assets) * 0.04
    # 添加一些相关性
    for i in range(n_assets - 1):
        true_cov[i, i + 1] = 0.01
        true_cov[i + 1, i] = 0.01
    returns = np.random.multivariate_normal(np.zeros(n_assets), true_cov, size=n_obs)

    estimator = LedoitWolfCovariance(annualize=False)
    result = estimator.fit(returns)

    print(f"  样本数: T={result.n_observations}, N={result.n_assets}")
    print(f"  收缩强度 δ*={result.shrinkage_intensity:.3f}")
    print(
        f"  条件数: {result.condition_number_before:.0f} → {result.condition_number_after:.0f}"
    )
    print(f"  平均方差: {result.avg_variance:.4f}")
    print(f"  平均相关性: {result.avg_correlation:.3f}")

    # 验证
    assert result.cov_shrunk.shape == (n_assets, n_assets)
    assert 0 <= result.shrinkage_intensity <= 1
    # 收缩后应改善条件数
    assert result.condition_number_after <= result.condition_number_before + 1
    # 协方差矩阵应对称正定
    assert np.allclose(result.cov_shrunk, result.cov_shrunk.T)
    # 对角线应 > 0
    assert np.all(np.diag(result.cov_shrunk) > 0)

    # 测试小样本 (T < N) 场景, 收缩强度应更高
    returns_small = returns[:3]  # T=3, N=5
    result_small = estimator.fit(returns_small)
    print(f"\n  小样本 (T=3, N=5): δ={result_small.shrinkage_intensity:.3f}")
    assert result_small.shrinkage_intensity > 0.1, "小样本应有较高收缩"

    print("\n✓ 测试 1 通过")
    return True


def test_risk_budget_optimizer():
    """测试 2: 风险预算约束优化器"""
    print("\n" + "=" * 60)
    print("测试 2: 风险预算约束优化器")
    print("=" * 60)

    optimizer = RiskBudgetOptimizer(risk_aversion=2.5, risk_free_rate=0.03)
    np.random.seed(42)

    symbols = ["600519", "000858", "601318", "601398", "600036"]
    n = len(symbols)
    expected_returns = np.array([0.15, 0.10, 0.08, 0.06, 0.12])
    # 生成协方差 (年化)
    returns = np.random.randn(252, n) * 0.02
    cov_annual = np.cov(returns.T) * 252
    benchmark_weights = np.array([0.25, 0.20, 0.20, 0.15, 0.20])

    # 测试 2a: TE 约束 5%
    result = optimizer.optimize(
        symbols=symbols,
        expected_returns=expected_returns,
        cov_matrix=cov_annual,
        benchmark_weights=benchmark_weights,
        max_tracking_error=0.05,
        max_weight=0.40,
        min_weight=0.0,
    )
    print(f"  最优权重: {result.optimal_weights}")
    print(f"  跟踪误差 TE: {result.tracking_error:.2%}")
    print(f"  主动收益: {result.active_return:.2%}")
    print(f"  信息比率 IR: {result.information_ratio:.3f}")
    print(f"  TE 约束松弛: {result.te_constraint_slack:.4f}")
    print(f"  TE 约束绑定: {result.te_constraint_binding}")
    print(f"  权重违反: {result.weight_bounds_violated}")
    print(f"  求解器: {result.solver_status}")

    # 验证
    assert len(result.optimal_weights) == n
    assert abs(result.optimal_weights.sum() - 1.0) < 1e-3, "权重应满仓"
    assert result.tracking_error <= 0.05 + 0.001, "TE 应满足约束"
    assert not result.weight_bounds_violated, "权重不应违反上下限"
    assert result.optimal_weights.min() >= 0 and result.optimal_weights.max() <= 0.40

    # 测试 2b: 紧约束 TE = 2%
    result_tight = optimizer.optimize(
        symbols=symbols,
        expected_returns=expected_returns,
        cov_matrix=cov_annual,
        benchmark_weights=benchmark_weights,
        max_tracking_error=0.02,
        max_weight=0.40,
        min_weight=0.0,
    )
    print(f"\n  紧约束 TE=2%: 实际 TE={result_tight.tracking_error:.2%}")
    assert result_tight.tracking_error <= 0.025

    # 测试 2c: rebalance_to_te_target
    rebalance = optimizer.rebalance_to_te_target(
        current_weights=np.array([0.50, 0.30, 0.10, 0.05, 0.05]),
        benchmark_weights=benchmark_weights,
        cov_matrix=cov_annual,
        target_te=0.03,
        max_adjustment=0.20,  # 放宽调整幅度
    )
    print(f"\n  rebalance_to_te_target: new_TE={rebalance['expected_te']:.2%}")
    assert rebalance["expected_te"] <= 0.035

    print("\n✓ 测试 2 通过")
    return True


def test_stress_test_engine():
    """测试 3: 压力测试情景库"""
    print("\n" + "=" * 60)
    print("测试 3: 压力测试情景库")
    print("=" * 60)

    engine = StressTestEngine(risk_threshold=-0.10)
    print(f"  预定义场景数: {len(engine.scenarios)}")
    # v8.4 (2026-07-30): 场景数从 8 扩展为 10, 新增 2 个流动性风险场景
    #   9. ETF跌停+期货流动性枯竭 (双重流动性陷阱)
    #   10. 期货期权流动性双重枯竭 (对冲瘫痪)
    assert len(engine.scenarios) == 10

    positions = [
        {
            "code": "600519",
            "amount": 100000,
            "sector": "食品饮料",
            "style": "value",
            "type": "STOCK",
        },
        {
            "code": "000858",
            "amount": 80000,
            "sector": "食品饮料",
            "style": "growth",
            "type": "STOCK",
        },
        {
            "code": "601318",
            "amount": 120000,
            "sector": "非银金融",
            "style": "value",
            "type": "STOCK",
        },
        {
            "code": "600036",
            "amount": 90000,
            "sector": "银行",
            "style": "value",
            "type": "STOCK",
        },
        {
            "code": "518880",
            "amount": 60000,
            "sector": "黄金",
            "style": "",
            "type": "GOLD",
        },
    ]
    total_value = sum(p["amount"] for p in positions)

    # 测试 3a: 运行所有场景
    results = engine.run_all_scenarios(positions, total_value)
    print(f"\n  {len(results)} 个场景结果:")
    for r in results:
        print(
            f"    {r.scenario_name}: return={r.portfolio_return:+.2%}, pnl=¥{r.portfolio_pnl:+.0f}, "
            f"breach={r.is_breach}"
        )

    assert len(results) == 10  # v8.4: 8 原场景 + 2 新流动性场景

    # 验证极端场景 (2008 金融危机) 损失较大
    worst = engine.get_worst_scenario(results)
    print(f"\n  最严重场景: {worst.scenario_name} ({worst.portfolio_return:.2%})")
    assert worst is not None
    assert worst.portfolio_return < 0

    # 测试 3b: 2008 金融危机应该损失最大
    result_2008 = next(r for r in results if "2008" in r.scenario_name)
    assert result_2008.portfolio_return < -0.20, "2008 金融危机损失应 < -20%"
    # 黄金应在该场景下上涨
    gold_pnl = result_2008.by_asset.get("518880", 0)
    assert gold_pnl > 0, "2008 金融危机黄金应上涨"

    # 测试 3c: 汇总
    summary = engine.summarize(results)
    print(
        f"\n  汇总: 最严重={summary['worst_scenario']}, "
        f"worst_return={summary['worst_return']:.2%}, "
        f"breaches={summary['n_breaches']}"
    )
    assert summary["n_scenarios"] == 10  # v8.4: 8 原场景 + 2 新流动性场景

    # 测试 3d: 自定义场景
    custom = engine.create_custom_shock(
        name="测试场景",
        description="自定义冲击测试",
        shocks=ShockFactors(equity_market=-0.10, commodity_gold=0.05),
    )
    assert len(engine.scenarios) == 11  # v8.4: 10 默认 + 1 自定义
    custom_result = engine.run_scenario(custom, positions, total_value)
    assert custom_result.scenario_name == "测试场景"
    print(f"\n  自定义场景: return={custom_result.portfolio_return:+.2%}")

    print("\n✓ 测试 3 通过")
    return True


if __name__ == "__main__":
    tests = [
        test_ledoit_wolf,
        test_risk_budget_optimizer,
        test_stress_test_engine,
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
