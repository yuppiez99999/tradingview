"""300万计划 净收益 5% 下限脆弱性量化.

量化 "毛收益 5.86% - 期权 overlay 年成本" 在不同成本档位下的净年化,
并定位 5% 下限的击穿点.

关键结论 (写入 300万计划实测附录 / v9.3 参数表):
    - 净收益 = 毛收益(5.86%) - 期权年成本
    - 5% 下限的**成本击穿临界点 = 0.86%/年** (5.86 - 0.86 = 5.00)
    - 即: 只要任一年期权净成本 > 0.86%, 净收益即跌破 5% 下限
    - 而计划 §三 预算上限 1.2% / 铁律一临时上浮 1.5% 均 > 0.86%,
      故危机年份 (预算被实质使用) 净收益必然 < 5% — 下限并非硬约束.
"""

from __future__ import annotations

import csv
from pathlib import Path

# 来自方略 §二 配置加权毛收益 与 §0/§三 的预算口径
GROSS_RETURN = 0.0586  # 5.86%
NET_FLOOR = 0.05  # 5% 净值下限

# 期权 overlay 年成本情景 (占净值比例)
COST_SCENARIOS = [0.003, 0.005, 0.008, 0.010, 0.012, 0.015]

OUT_CSV = Path(__file__).resolve().parent / "net_return_sensitivity.csv"


def main() -> None:
    rows = []
    print(f"{'期权年成本':>10} | {'净年化':>8} | {'距5%下限':>10} | 击穿5%下限?")
    print("-" * 48)
    for cost in COST_SCENARIOS:
        net = GROSS_RETURN - cost
        vs_floor = net - NET_FLOOR
        breach = net < NET_FLOOR
        rows.append(
            {
                "option_cost_pct": round(cost * 100, 2),
                "net_return_pct": round(net * 100, 2),
                "vs_floor_pct": round(vs_floor * 100, 2),
                "breaches_floor": "是" if breach else "否",
            }
        )
        print(
            f"{cost*100:>9.1f}% | {net*100:>7.2f}% | {vs_floor*100:>9.2f}pp | {'是' if breach else '否'}"
        )

    # 击穿临界点: 净收益 = 5% 时的成本
    breakeven_cost = GROSS_RETURN - NET_FLOOR
    print("-" * 48)
    print(f"5% 下限的成本击穿临界点 = {breakeven_cost*100:.2f}%/年")
    print(
        "含义: 期权年成本 > 此值, 净收益即跌破 5% 下限;"
        " 计划预算上限 1.2% / 临时 1.5% 均越过该点。"
    )

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["option_cost_pct", "net_return_pct", "vs_floor_pct", "breaches_floor"],
        )
        w.writeheader()
        w.writerows(rows)
    print(f"\n已写出: {OUT_CSV}")


if __name__ == "__main__":
    main()
