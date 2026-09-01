import json
import os

# 使用当前项目路径，而非硬编码其他项目目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, "config", "positions.json"), encoding="utf-8") as f:
    positions = json.load(f)

with open(os.path.join(BASE_DIR, "500万建仓计划_20260706.json"), encoding="utf-8") as f:
    build_plan = json.load(f)

total_capital = positions["meta"]["total_capital"]
stock_etf_capital = positions["meta"]["stock_etf_capital"]
hedge_capital = positions["meta"]["hedge_capital"]

current_market_value = 0
current_cost_value = 0
target_total = 0

style_stats = {}
sector_stats = {}

for _code, pos in positions["positions"].items():
    shares = pos.get("shares", 0)
    avg_cost = pos.get("avg_cost", 0)
    est_price = (
        pos.get("est_price", avg_cost) if pos.get("est_price", 0) > 0 else avg_cost
    )
    target_amount = pos.get("amount", 0)
    style = pos.get("style", "其他")
    sector = pos.get("sector", "其他")

    current_cost_value += shares * avg_cost
    current_market_value += shares * est_price
    target_total += target_amount

    if style not in style_stats:
        style_stats[style] = {"current_value": 0, "target_amount": 0, "count": 0}
    style_stats[style]["current_value"] += shares * est_price
    style_stats[style]["target_amount"] += target_amount
    style_stats[style]["count"] += 1

    if sector not in sector_stats:
        sector_stats[sector] = {"current_value": 0, "target_amount": 0}
    sector_stats[sector]["current_value"] += shares * est_price
    sector_stats[sector]["target_amount"] += target_amount

print("=" * 80)
print("当前建仓计划完成情况分析")
print("=" * 80)
print(f"\n总资金: ¥{total_capital:,.0f}")
print(f"股票ETF资金: ¥{stock_etf_capital:,.0f}")
print(f"对冲资金: ¥{hedge_capital:,.0f}")
print(f"\n当前持仓市值: ¥{current_market_value:,.0f}")
print(f"当前持仓成本: ¥{current_cost_value:,.0f}")
print(f"目标总金额: ¥{target_total:,.0f}")
print(f"\n完成率: {current_market_value / target_total * 100:.1f}%")
print(f"可用资金: ¥{stock_etf_capital - current_cost_value:,.0f}")

print("\n" + "=" * 80)
print("风格分布分析")
print("=" * 80)
print(
    f"{'风格':<10} {'当前市值':<15} {'目标金额':<15} {'实际占比':<10} {'目标占比':<10} {'偏差':<10}"
)
print("-" * 80)
for style, stats in sorted(
    style_stats.items(), key=lambda x: x[1]["target_amount"], reverse=True
):
    current_pct = (
        stats["current_value"] / current_market_value * 100
        if current_market_value > 0
        else 0
    )
    target_pct = stats["target_amount"] / target_total * 100 if target_total > 0 else 0
    deviation = current_pct - target_pct
    print(
        f"{style:<10} ¥{stats['current_value']:>12,.0f} ¥{stats['target_amount']:>12,.0f} {current_pct:>8.1f}% {target_pct:>8.1f}% {deviation:>+8.1f}%"  # noqa: E501
    )

print("\n" + "=" * 80)
print("各标的建仓进度")
print("=" * 80)
print(
    f"{'代码':<12} {'名称':<16} {'持仓':<10} {'当前市值':<12} {'目标金额':<12} {'完成率':<8} {'风格':<8}"
)
print("-" * 80)
sorted_positions = sorted(
    positions["positions"].items(),
    key=lambda x: x[1].get("target_weight", 0),
    reverse=True,
)
for code, pos in sorted_positions:
    shares = pos.get("shares", 0)
    est_price = (
        pos.get("est_price", pos.get("avg_cost", 0))
        if pos.get("est_price", 0) > 0
        else pos.get("avg_cost", 0)
    )
    current_value = shares * est_price
    target_amount = pos.get("amount", 0)
    completion = current_value / target_amount * 100 if target_amount > 0 else 0
    print(
        f"{code:<12} {pos['name'][:16]:<16} {shares:<10} ¥{current_value:>10,.0f} ¥{target_amount:>10,.0f} {completion:>6.1f}% {pos.get('style', '其他'):<8}"  # noqa: E501
    )

print("\n" + "=" * 80)
print("风险提示")
print("=" * 80)
for style, stats in style_stats.items():
    current_pct = (
        stats["current_value"] / current_market_value * 100
        if current_market_value > 0
        else 0
    )
    target_pct = stats["target_amount"] / target_total * 100 if target_total > 0 else 0
    if abs(current_pct - target_pct) > 10:
        if current_pct > target_pct:
            print(
                f"⚠️ {style}超配: 实际{current_pct:.1f}% vs 目标{target_pct:.1f}%, 超配{current_pct-target_pct:.1f}个百分点"  # noqa: E501
            )
        else:
            print(
                f"⚠️ {style}低配: 实际{current_pct:.1f}% vs 目标{target_pct:.1f}%, 低配{target_pct-current_pct:.1f}个百分点"  # noqa: E501
            )

for _code, pos in positions["positions"].items():
    target_amount = pos.get("amount", 0)
    if target_amount > 0:
        shares = pos.get("shares", 0)
        est_price = (
            pos.get("est_price", pos.get("avg_cost", 0))
            if pos.get("est_price", 0) > 0
            else pos.get("avg_cost", 0)
        )
        current_value = shares * est_price
        completion = current_value / target_amount * 100
        if completion < 10 and target_amount > 10000:
            print(f"⚠️ {pos['name']}建仓严重滞后: 仅完成{completion:.1f}%")

print("\n" + "=" * 80)
print("建议调整方向")
print("=" * 80)
print("1. 防御资产(长江电力+国债ETF)超配约24个百分点，建议逐步降低")
print("2. 科技股低配约4个百分点，建议增加配置")
print("3. 创业板ETF目标权重为0，需确认是否从配置中移除")
print("4. 对冲资金尚未使用，建议尽快部署期权/期货对冲")
print("5. 中际旭创持仓为0，建议尽快建仓")
