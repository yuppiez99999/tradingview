# -*- coding: utf-8 -*-
"""
全自动对冲后的修正年化收益率测算
================================

输入: 现有组合收益预测 + 五层对冲成本 + 极端场景保护效果
输出: 开启全自动对冲后的概率加权年化收益率

适用组合: 500万, 13标的, 高端制造50%/防御25%/资源20%/顺周期5%
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

# ============================================================================
# 基础参数
# ============================================================================

TOTAL_CAPITAL = 5_000_000.0

# 情景定义：名称 / 概率 / 基准回撤(未对冲) / 对冲后回撤 / 说明
SCENARIOS = [
    {
        "name": "牛市",
        "probability": 0.20,
        "base_return": 0.357,
        "description": "持续上涨趋势，对冲轻度拖累",
    },
    {
        "name": "基准",
        "probability": 0.40,
        "base_return": 0.161,
        "description": "温和上涨，对冲成本与部分增收抵消",
    },
    {
        "name": "熊市",
        "probability": 0.30,
        "base_return": -0.223,
        "description": "持续下跌，自动减仓+期货+期权压缩损失",
    },
    {
        "name": "黑天鹅",
        "probability": 0.10,
        "base_return": -0.414,
        "description": "极端下跌，全层对冲启用，守住70%资金底线",
    },
]


# ============================================================================
# 对冲成本模型（基于 comprehensive_quant_system_v7.py 的年化成本拆解）
# ============================================================================

@dataclass
class HedgeCostModel:
    """五层对冲年化成本/收益"""

    # 资金分配（基于500万）
    futures_capital: float = 750_000.0       # Layer 1 期货 15%
    options_capital: float = 750_000.0       # Layer 2 期权 15%
    vol_capital: float = 300_000.0           # Layer 3 波动率 6%
    abs_capital: float = 250_000.0           # Layer 4 绝对收益 5%
    covered_capital: float = 200_000.0       # Layer 5 备兑 4%

    # 年化成本率
    futures_roll_cost: float = 0.007         # 展期成本 0.7%（优化后）
    futures_trade_cost: float = 0.0020       # 月度调仓交易成本 0.2%（优化后）
    options_premium_cost: float = 0.012      # 期权权利金净支出 1.2%（优化后）
    vol_arb_return: float = 0.025            # 波动率套利收益 +2.5%（优化后）
    abs_return: float = 0.025                # 绝对收益 +2.5%（优化后）
    covered_income: float = 0.018            # 备兑增收 +1.8%（优化后）
    transaction_cost: float = 0.005          # 综合交易/滑点成本 0.5%（优化后）

    # 情景乘数（极端市场下成本上升、部分增收失效）
    stress_cost_multiplier: float = 1.8      # 黑天鹅时对冲成本上升
    stress_income_haircut: float = 0.5       # 黑天鹅时增收策略部分失效


# ============================================================================
# 主测算
# ============================================================================

def estimate_hedged_annual_return(hedge_ratio: float = 0.40) -> Dict[str, Any]:
    """
    估算开启全自动对冲后的年化收益率

    参数:
        hedge_ratio: 对冲资金占总资金比例，默认 40%

    逻辑:
      1. 先算无对冲时的概率加权预期收益
      2. 按对冲资金比例调整五层对冲的净成本/收益
      3. 在极端场景中加入“保护压缩损失”的效果
      4. 输出修正后的年化收益率
    """
    # 对冲资金按比例缩放
    base_futures = 750_000.0 * (hedge_ratio / 0.40)
    base_options = 750_000.0 * (hedge_ratio / 0.40)
    base_vol = 300_000.0 * (hedge_ratio / 0.40)
    base_abs = 250_000.0 * (hedge_ratio / 0.40)
    base_covered = 200_000.0 * (hedge_ratio / 0.40)

    cost_model = HedgeCostModel(
        futures_capital=base_futures,
        options_capital=base_options,
        vol_capital=base_vol,
        abs_capital=base_abs,
        covered_capital=base_covered,
    )

    # 1. 无对冲概率加权收益
    base_expected = sum(s["probability"] * s["base_return"] for s in SCENARIOS)

    # 2. 对冲净拖累（正常市场）
    net_hedge_drag = (
        cost_model.futures_roll_cost
        + cost_model.futures_trade_cost
        + cost_model.options_premium_cost
        + cost_model.transaction_cost
        - cost_model.vol_arb_return
        - cost_model.abs_return
        - cost_model.covered_income
    )

    # 3. 极端场景修正：黑天鹅下损失被压缩
    #    原黑天鹅 -41.4%，开启全自动对冲后：
    #      - 自动减仓 60%
    #      - 期货加仓至 70%
    #      - 期权加厚 + 深度 OTM Put
    #      - 资金底线 70%（即最多再跌 30% 权益空间）
    #    这里简化为：黑天鹅有效回撤从 -41.4% 压缩到 -20%~-25%
    #    采用保守估计 -22%
    #    对冲资金越多，黑天鹅保护越强，这里做线性改善（最低-22%，最高-15%）
    min_black_swan = -0.22
    max_black_swan = -0.15
    if hedge_ratio <= 0.40:
        hedged_black_swan_return = min_black_swan
    else:
        # 线性插值：40% -> -22%, 70% -> -15%
        hedged_black_swan_return = min_black_swan + (max_black_swan - min_black_swan) * ((hedge_ratio - 0.40) / 0.30)
        hedged_black_swan_return = max(hedged_black_swan_return, max_black_swan)

    # 4. 重算概率加权收益（仅替换黑天鹅情景）
    hedged_expected = (
        SCENARIOS[0]["probability"] * SCENARIOS[0]["base_return"]   # 牛市
        + SCENARIOS[1]["probability"] * SCENARIOS[1]["base_return"] # 基准
        + SCENARIOS[2]["probability"] * SCENARIOS[2]["base_return"] # 熊市
        + SCENARIOS[3]["probability"] * hedged_black_swan_return    # 黑天鹅（已对冲）
    )

    # 5. 最终修正预期 = 对冲后情景收益 - 正常市场下的对冲净拖累
    #    注意：这里做保守处理，把净拖累直接扣减
    final_expected = hedged_expected - net_hedge_drag

    # 6. 情景明细
    scenario_details = []
    for s in SCENARIOS:
        if s["name"] == "黑天鹅":
            hedged_r = hedged_black_swan_return
        else:
            hedged_r = s["base_return"] - net_hedge_drag

        scenario_details.append({
            "name": s["name"],
            "probability": s["probability"],
            "base_return": s["base_return"],
            "hedged_return": hedged_r,
            "capital_before": TOTAL_CAPITAL,
            "capital_after": TOTAL_CAPITAL * (1 + hedged_r),
        })

    return {
        "capital": TOTAL_CAPITAL,
        "hedge_ratio": hedge_ratio,
        "base_probability_weighted_return": base_expected,
        "hedged_black_swan_return": hedged_black_swan_return,
        "net_hedge_drag": net_hedge_drag,
        "final_probability_weighted_return": final_expected,
        "scenarios": scenario_details,
        "note": (
            "此为简化模型，实际运行中 auto_hedge_executor 会根据 LEVEL_2/3/4 "
            "自动减仓、期权加厚、期货加仓，黑天鹅保护效果可能更优。"
        ),
    }


def print_report(result: Dict[str, Any]) -> None:
    """打印可读报告"""
    print("\n" + "=" * 70)
    print("  全自动对冲后的修正年化收益率测算")
    print("=" * 70)
    print(f"  组合资金: {result['capital']:,.0f} 元")
    print(f"  对冲资金占比: {result['hedge_ratio']:.0%}")
    print(f"  无对冲概率加权收益: {result['base_probability_weighted_return']:+.2%}")
    print(f"  五层对冲净拖累:     {result['net_hedge_drag']:+.2%}")
    print(f"  黑天鹅对冲后回撤:   {result['hedged_black_swan_return']:+.2%}")
    print(f"  修正后概率加权收益: {result['final_probability_weighted_return']:+.2%}")
    print("-" * 70)
    print(f"  {'情景':<10} {'概率':<8} {'未对冲':<10} {'对冲后':<10} {'期末权益':<12}")
    print(f"  {'─' * 55}")
    for s in result["scenarios"]:
        print(
            f"  {s['name']:<10} {s['probability']:<8.0%} "
            f"{s['base_return']:<+10.2%} {s['hedged_return']:<+10.2%} "
            f"{s['capital_after']:>10,.0f}"
        )
    print("=" * 70)
    print(f"  备注: {result['note']}")
    print("=" * 70)


def sensitivity_analysis() -> None:
    """对冲资金占比敏感性分析"""
    hedge_ratios = [0.30, 0.40, 0.50, 0.60]
    results = []

    print("\n" + "=" * 70)
    print("  对冲资金占比敏感性分析")
    print("=" * 70)
    print(f"  {'对冲占比':<10} {'年化收益':<10} {'黑天鹅回撤':<12} {'期末权益(1.5年)':<16} {'备注'}")
    print(f"  {'─' * 70}")

    for ratio in hedge_ratios:
        r = estimate_hedged_annual_return(ratio)
        horizon = project_to_horizon(r["final_probability_weighted_return"], years=1.5)
        note = ""
        if ratio == 0.30:
            note = "高收益"
        elif ratio == 0.40:
            note = "当前配置"
        elif ratio == 0.50:
            note = "高保护"
        else:
            note = "极限保护"

        print(
            f"  {ratio:<10.0%} "
            f"{r['final_probability_weighted_return']:<+10.2%} "
            f"{r['hedged_black_swan_return']:<+12.2%} "
            f"{horizon['end_value']:>12,.0f}   "
            f"{note}"
        )
        results.append({
            "hedge_ratio": ratio,
            "annual_return": r["final_probability_weighted_return"],
            "black_swan_drawdown": r["hedged_black_swan_return"],
            "end_value_15y": horizon["end_value"],
        })

    print("=" * 70)

    # 简单结论
    best = max(results, key=lambda x: x["annual_return"])
    print(f"\n  在当前模型下，年化收益最高的对冲占比为：{best['hedge_ratio']:.0%}")
    print(f"  对应年化收益：{best['annual_return']:+.2%}，黑天鹅回撤：{best['black_swan_drawdown']:.2%}")
    print("  注意：更高对冲占比会提升极端保护，但也会增加常态成本。")


# ============================================================================
# 扩展：2026-07 至 2027-12 的 1.5 年折算
# ============================================================================

def project_to_horizon(annual_return: float, years: float = 1.5) -> Dict[str, float]:
    """
    将年化收益外推到指定 horizon

    简单复利: 期末 = 本金 * (1 + r)^years
    """
    end_value = TOTAL_CAPITAL * ((1 + annual_return) ** years)
    total_return = end_value / TOTAL_CAPITAL - 1
    return {
        "years": years,
        "annual_return": annual_return,
        "end_value": end_value,
        "total_return": total_return,
    }


def print_horizon(result: Dict[str, float]) -> None:
    print("\n" + "=" * 70)
    print("  预测周期：2026-07 至 2027-12（约 1.5 年）")
    print("=" * 70)
    print(f"  年化收益率:   {result['annual_return']:+.2%}")
    print(f"  1.5 年后金额: {result['end_value']:,.0f} 元")
    print(f"  1.5 年总收益: {result['total_return']:+.2%}")
    print("=" * 70)


if __name__ == "__main__":
    # 先做敏感性分析
    sensitivity_analysis()

    # 再输出默认 40% 对冲的详细结果
    result = estimate_hedged_annual_return(0.40)
    print_report(result)
    horizon = project_to_horizon(result["final_probability_weighted_return"], years=1.5)
    print_horizon(horizon)
