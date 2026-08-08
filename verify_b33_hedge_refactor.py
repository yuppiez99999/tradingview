"""B3.3 HedgeEngine 抽取验证脚本

验证项:
1. HedgeEngine 实例化
2. assess_portfolio_risk 委托到 portfolio_risk_assessor
3. determine_hedge_signal_strength 委托到 hedge_strategy_executor
4. compute_optimal_hedge_ratio 委托
5. generate_hedge_plan 委托
6. run_historical_stress_tests 委托
7. _compute_weighted_beta / _compute_mrc / _compute_portfolio_vol_cov 委托
"""

import os
import sys
from pathlib import Path

# 确保 v8.3_institutional 在 path 中
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402
setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

from src.data.futures_prices import (  # noqa: E402
    DEFAULT_FUTURES_PRICES,
    get_live_futures_prices,
)
from src.hedging.hedge_engine_v59 import (  # noqa: E402
    INDEX_FUTURES_SPECS,
    HedgeEngine,
    HedgeRecommendation,
    HedgeSignalStrength,
    HedgeType,
    PortfolioRisk,
)
from src.hedging.hedge_strategy_executor import (  # noqa: E402
    HedgeRecommendation as ExecutorHedgeRecommendation,
)
from src.hedging.hedge_strategy_executor import (  # noqa: E402
    HedgeSignalStrength as ExecutorHedgeSignalStrength,
)
from src.hedging.hedge_strategy_executor import (  # noqa: E402
    HedgeType as ExecutorHedgeType,
)
from src.hedging.hedge_strategy_executor import (  # noqa: E402
    compute_optimal_hedge_ratio,
    determine_hedge_signal_strength,
    generate_futures_hedge,
    generate_hedge_plan,
    generate_hedge_reason,
    generate_options_hedge,
)
from src.risk.portfolio_risk_assessor import (  # noqa: E402
    DEFAULT_BETAS,
    FALLBACK_PRICE_MAP,
    HISTORICAL_STRESS_SCENARIOS,
    SECTOR_MAP,
    assess_portfolio_risk,
    compute_expected_shortfall,
    compute_mrc,
    compute_portfolio_vol_cov,
    compute_weighted_beta,
    run_historical_stress_tests,
)
from src.risk.portfolio_risk_assessor import (  # noqa: E402
    PortfolioRisk as AssessorPortfolioRisk,
)


def banner(msg: str) -> None:
    print(f"\n{'='*60}\n{msg}\n{'='*60}")


def test_instantiation():
    banner("[1] HedgeEngine 实例化")
    engine = HedgeEngine(portfolio_value=1_000_000)
    assert engine.portfolio_value == 1_000_000
    assert hasattr(engine, "assess_portfolio_risk")
    assert hasattr(engine, "determine_hedge_signal_strength")
    assert hasattr(engine, "compute_optimal_hedge_ratio")
    assert hasattr(engine, "generate_hedge_plan")
    assert hasattr(engine, "run_historical_stress_tests")
    print(f"  ✓ 实例化成功 portfolio_value={engine.portfolio_value}")


def test_assess_portfolio_risk():
    banner("[2] assess_portfolio_risk 委托 (B3.3)")
    engine = HedgeEngine(portfolio_value=1_000_000)
    positions = {
        "300308.SZ": {"shares": 1000, "name": "中际旭创"},
        "600276.SH": {"shares": 2000, "name": "恒瑞医药"},
        "601088.SH": {"shares": 3000, "name": "中国神华"},
    }
    prices = {"300308.SZ": 105.0, "600276.SH": 48.0, "601088.SH": 38.0}
    risk = engine.assess_portfolio_risk(positions=positions, prices=prices, cash=10000.0)

    assert isinstance(risk, PortfolioRisk)
    assert isinstance(risk, AssessorPortfolioRisk)  # 同一类
    assert risk.total_value > 0
    assert risk.stock_exposure > 0
    # Beta 应该计算出来
    assert risk.beta_csi300 != 0 or risk.beta_csi500 != 0
    print(f"  ✓ total_value={risk.total_value:.2f}")
    print(f"  ✓ stock_exposure={risk.stock_exposure:.2f}")
    print(f"  ✓ beta_csi300={risk.beta_csi300:.4f}")
    print(f"  ✓ beta_csi500={risk.beta_csi500:.4f}")


def test_determine_hedge_signal_strength():
    banner("[3] determine_hedge_signal_strength 委托")
    engine = HedgeEngine(portfolio_value=1_000_000)
    risk = PortfolioRisk(
        total_value=1_000_000,
        stock_exposure=900_000,
        beta_csi300=1.10,
        volatility_30d=0.30,  # 高波动率触发
        var_95_daily=15000,
    )
    strength, urgency = engine.determine_hedge_signal_strength(
        risk=risk,
        portfolio_volatility=0.30,
        portfolio_drawdown_60d=0.15,
    )
    assert isinstance(strength, HedgeSignalStrength)
    assert isinstance(strength, ExecutorHedgeSignalStrength)
    assert urgency >= 0.0
    print(f"  ✓ strength={strength.name} urgency={urgency:.4f}")


def test_compute_optimal_hedge_ratio():
    banner("[4] compute_optimal_hedge_ratio 委托")
    engine = HedgeEngine(portfolio_value=1_000_000)
    risk = PortfolioRisk(
        total_value=1_000_000,
        stock_exposure=900_000,
        beta_csi300=1.20,
        volatility_30d=0.32,
    )
    # compute_optimal_hedge_ratio 需要 hedge_strength 参数
    strength = HedgeSignalStrength.MODERATE
    ratio = engine.compute_optimal_hedge_ratio(risk=risk, hedge_strength=strength)
    assert isinstance(ratio, float)
    assert 0.0 <= ratio <= 1.0
    print(f"  ✓ hedge_ratio={ratio:.4f} (strength={strength.name})")


def test_generate_hedge_plan():
    banner("[5] generate_hedge_plan 委托 (完整对冲方案)")
    engine = HedgeEngine(portfolio_value=1_000_000)
    positions = {
        "300308.SZ": {"shares": 1000, "name": "中际旭创"},
        "600276.SH": {"shares": 2000, "name": "恒瑞医药"},
    }
    prices = {"300308.SZ": 105.0, "600276.SH": 48.0}
    risk = engine.assess_portfolio_risk(positions=positions, prices=prices)
    futures_prices = {"IF": 3900.0, "IC": 5500.0, "IM": 6500.0, "IH": 2600.0}

    plan = engine.generate_hedge_plan(
        risk=risk,
        futures_prices=futures_prices,
        portfolio_volatility=0.30,
        portfolio_drawdown_60d=0.15,
        positions=positions,
        prices=prices,
    )
    assert isinstance(plan, HedgeRecommendation)
    assert isinstance(plan, ExecutorHedgeRecommendation)
    assert plan.timestamp != "" or plan.hedge_type != HedgeType.NONE
    print(f"  ✓ hedge_type={plan.hedge_type.name}")
    print(f"  ✓ strength={plan.strength.name}")
    print(f"  ✓ hedge_ratio={plan.hedge_ratio:.4f}")
    print(f"  ✓ futures_contracts={plan.futures_contracts}")
    print(f"  ✓ reasoning={plan.reasoning[:80]}..." if plan.reasoning else "  ✓ (no reasoning)")


def test_run_historical_stress_tests():
    banner("[6] run_historical_stress_tests 委托")
    engine = HedgeEngine(portfolio_value=1_000_000)
    positions = {
        "300308.SZ": {"shares": 1000, "name": "中际旭创"},
        "600276.SH": {"shares": 2000, "name": "恒瑞医药"},
    }
    prices = {"300308.SZ": 105.0, "600276.SH": 48.0}
    stress = engine.run_historical_stress_tests(positions=positions, prices=prices)
    assert isinstance(stress, dict)
    assert len(stress) > 0
    # 验证至少包含 6 个情景
    assert len(stress) >= 6, f"期望 >=6 情景, 实际 {len(stress)}"
    for name, result in stress.items():
        assert "estimated_loss" in result or "loss" in result or "portfolio_loss" in result, f"情景 {name} 缺少 loss 字段: {result}"
    print(f"  ✓ 情景数: {len(stress)}")
    for name, result in list(stress.items())[:3]:
        print(f"    - {name}: {result}")


def test_compute_helpers():
    banner("[7] 辅助计算函数委托 (_compute_weighted_beta / _compute_mrc / _compute_portfolio_vol_cov)")
    engine = HedgeEngine(portfolio_value=1_000_000)
    # _compute_weighted_beta
    weights = {"300308": 0.4, "600276": 0.3, "601088": 0.3}
    beta_csi300 = engine._compute_weighted_beta(weights, "CSI300")
    assert beta_csi300 > 0
    print(f"  ✓ _compute_weighted_beta(CSI300)={beta_csi300:.4f}")

    # _estimate_default_price
    price = engine._estimate_default_price("300308")
    assert price > 0
    assert price == FALLBACK_PRICE_MAP.get("300308", 50.0)
    print(f"  ✓ _estimate_default_price('300308')={price}")

    # _compute_portfolio_vol_cov
    historical_returns = {
        "300308": [0.01, -0.02, 0.015, 0.005, -0.01] * 10,
        "600276": [0.005, -0.01, 0.008, 0.002, -0.005] * 10,
        "601088": [0.003, -0.005, 0.004, 0.001, -0.003] * 10,
    }
    vol = engine._compute_portfolio_vol_cov(weights, historical_returns, ["300308", "600276", "601088"])
    assert vol >= 0
    print(f"  ✓ _compute_portfolio_vol_cov={vol:.6f}")

    # _compute_expected_shortfall
    es = engine._compute_expected_shortfall(
        weights, historical_returns, ["300308", "600276", "601088"], 1_000_000, 0.95
    )
    assert es >= 0
    print(f"  ✓ _compute_expected_shortfall={es:.4f}")

    # _compute_mrc
    mrc = engine._compute_mrc(
        weights, historical_returns, ["300308", "600276", "601088"], vol
    )
    assert isinstance(mrc, dict)
    print(f"  ✓ _compute_mrc keys={list(mrc.keys())}")


def test_data_futures_prices_module():
    banner("[8] data/futures_prices.py 模块可用")
    assert isinstance(DEFAULT_FUTURES_PRICES, dict)
    assert "IF" in DEFAULT_FUTURES_PRICES
    assert "IC" in DEFAULT_FUTURES_PRICES
    assert "IM" in DEFAULT_FUTURES_PRICES
    assert "IH" in DEFAULT_FUTURES_PRICES
    print(f"  ✓ DEFAULT_FUTURES_PRICES: {DEFAULT_FUTURES_PRICES}")

    # 不实际联网，仅验证函数可调用
    assert callable(get_live_futures_prices)
    print("  ✓ get_live_futures_prices 可调用")


def test_index_futures_specs_preserved():
    banner("[9] INDEX_FUTURES_SPECS 常量保留")
    assert "IF" in INDEX_FUTURES_SPECS
    assert "IC" in INDEX_FUTURES_SPECS
    assert "IM" in INDEX_FUTURES_SPECS
    assert "IH" in INDEX_FUTURES_SPECS
    assert INDEX_FUTURES_SPECS["IF"]["multiplier"] == 300
    assert INDEX_FUTURES_SPECS["IC"]["multiplier"] == 200
    print(f"  ✓ IF multiplier={INDEX_FUTURES_SPECS['IF']['multiplier']}")
    print(f"  ✓ IC multiplier={INDEX_FUTURES_SPECS['IC']['multiplier']}")


def main():
    print("=" * 60)
    print("B3.3 HedgeEngine 抽取验证 — 开始")
    print("=" * 60)
    try:
        test_instantiation()
        test_assess_portfolio_risk()
        test_determine_hedge_signal_strength()
        test_compute_optimal_hedge_ratio()
        test_generate_hedge_plan()
        test_run_historical_stress_tests()
        test_compute_helpers()
        test_data_futures_prices_module()
        test_index_futures_specs_preserved()
        print("\n" + "=" * 60)
        print("✅ 全部验证通过 — B3.3 抽取功能完整")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ 验证失败 (AssertionError): {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ 验证异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
