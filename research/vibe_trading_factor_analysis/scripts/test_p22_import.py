# -*- coding: utf-8 -*-
"""P2.2 质量变化类因子导入与基础功能测试"""
import sys
from pathlib import Path

# 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
    FUNDAMENTALS_DEPENDENT_CATEGORIES,
)


def main():
    # 1. 类别检查
    adapter = VibeTradingFactorAdapter()
    assert "QualityTrend" in adapter.VIBE_TRADING_CATEGORIES, "QualityTrend 类别未注册"
    print("[OK] QualityTrend 类别已注册")
    print("  所有类别:", adapter.VIBE_TRADING_CATEGORIES)

    # 2. 空 history 测试
    factors = adapter._compute_vt_quality_trend_factors(fundamentals={}, fundamentals_history=None)
    assert len(factors) == 0, "空 history 应返回空因子"
    print("[OK] 空 history 返回空因子")

    # 3. 模拟数据测试
    mock_history = {
        "TEST_SH": {
            "quarters": [
                {"year": 2025, "quarter": 2, "roe": 0.15, "gross_margin": 0.35, "debt_to_equity": 0.40, "net_profit": 1000},
                {"year": 2025, "quarter": 1, "roe": 0.13, "gross_margin": 0.33, "debt_to_equity": 0.42, "net_profit": 900},
                {"year": 2024, "quarter": 4, "roe": 0.14, "gross_margin": 0.34, "debt_to_equity": 0.45, "net_profit": 950},
                {"year": 2024, "quarter": 3, "roe": 0.12, "gross_margin": 0.32, "debt_to_equity": 0.47, "net_profit": 880},
                {"year": 2024, "quarter": 2, "roe": 0.10, "gross_margin": 0.30, "debt_to_equity": 0.50, "net_profit": 800},
                {"year": 2024, "quarter": 1, "roe": 0.09, "gross_margin": 0.29, "debt_to_equity": 0.52, "net_profit": 750},
            ],
            "n_valid": 6,
            "data_quality": "real",
        }
    }
    factors = adapter._compute_vt_quality_trend_factors(fundamentals={}, fundamentals_history=mock_history)
    expected = {"VT_QUALTREND_ROE_DELTA", "VT_QUALTREND_MARGIN_EXP", "VT_QUALTREND_DEBT_RED", "VT_QUALTREND_GROWTH_ACCEL"}
    assert set(factors.keys()) == expected, f"因子不匹配: {set(factors.keys())}"
    print(f"[OK] 模拟数据生成 {len(factors)} 个因子")

    for name, fac in factors.items():
        print(f"  [{fac.category}] {name}: values={fac.values}")
        print(f"    formula: {fac.vt_formula}")
        print(f"    description: {fac.description}")

    # 4. 数值正确性校验
    roe_delta = factors["VT_QUALTREND_ROE_DELTA"].values["TEST_SH"]
    # roe[0]=0.15, roe[4]=0.10 -> delta=0.05
    assert abs(roe_delta - 0.05) < 1e-9, f"ROE_DELTA 数值错误: {roe_delta}"
    print(f"[OK] VT_QUALTREND_ROE_DELTA 数值正确: {roe_delta} (预期 0.05)")

    margin_exp = factors["VT_QUALTREND_MARGIN_EXP"].values["TEST_SH"]
    # gm[0]=0.35, gm[4]=0.30 -> delta=0.05
    assert abs(margin_exp - 0.05) < 1e-9, f"MARGIN_EXP 数值错误: {margin_exp}"
    print(f"[OK] VT_QUALTREND_MARGIN_EXP 数值正确: {margin_exp} (预期 0.05)")

    debt_red = factors["VT_QUALTREND_DEBT_RED"].values["TEST_SH"]
    # de[0]=0.40, de[4]=0.50 -> -(0.40-0.50)=0.10
    assert abs(debt_red - 0.10) < 1e-9, f"DEBT_RED 数值错误: {debt_red}"
    print(f"[OK] VT_QUALTREND_DEBT_RED 数值正确: {debt_red} (预期 0.10)")

    growth_accel = factors["VT_QUALTREND_GROWTH_ACCEL"].values["TEST_SH"]
    # growth_cur = (1000 - 800) / 800 = 0.25
    # growth_prev = (900 - 750) / 750 = 0.2
    # accel = 0.25 - 0.2 = 0.05
    expected_accel = (1000 - 800) / 800 - (900 - 750) / 750
    assert abs(growth_accel - expected_accel) < 1e-9, f"GROWTH_ACCEL 数值错误: {growth_accel} vs {expected_accel}"
    print(f"[OK] VT_QUALTREND_GROWTH_ACCEL 数值正确: {growth_accel} (预期 {expected_accel})")

    # 5. PipelineOrchestrator 检查
    print()
    print("FUNDAMENTALS_DEPENDENT_CATEGORIES:", FUNDAMENTALS_DEPENDENT_CATEGORIES)
    assert "QualityTrend" in FUNDAMENTALS_DEPENDENT_CATEGORIES
    print("[OK] QualityTrend 已加入依赖类别")

    orch = PipelineOrchestrator(config={"reports_dir": "reports/test_p22"})
    print("[OK] PipelineOrchestrator 实例化成功")

    # 6. _collect_fundamentals_dependent_factors 检查
    deps = orch._collect_fundamentals_dependent_factors()
    for fname in ["VT_QUALTREND_ROE_DELTA", "VT_QUALTREND_MARGIN_EXP", "VT_QUALTREND_DEBT_RED", "VT_QUALTREND_GROWTH_ACCEL"]:
        assert fname in deps, f"{fname} 未在依赖列表"
    print(f"[OK] 4 个 QualityTrend 因子已加入 _collect_fundamentals_dependent_factors (共 {len(deps)} 个)")

    print()
    print("=" * 60)
    print("P2.2 单元测试全部通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
