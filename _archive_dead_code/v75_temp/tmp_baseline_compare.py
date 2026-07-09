import json
from pathlib import Path

base_dir = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional")
plan_path = base_dir / "trade_plans/trade_plan_20260705.json"
report_path = base_dir / "../每日报告归档/2026/07/05/v75_daily_workflow_20260705.json"

plan = json.loads(plan_path.read_text(encoding="utf-8"))
report = json.loads(report_path.read_text(encoding="utf-8"))

# baseline orders
baseline = plan["execution_plan"]["morning_orders"] + plan["execution_plan"]["afternoon_orders"]

# qlib adjusted orders
signal = report["phases"]["signal"]
qlib_morning = signal.get("morning_orders", [])
qlib_afternoon = signal.get("afternoon_orders", [])
qlib_orders = qlib_morning + qlib_afternoon

baseline_map = {o["code"]: o for o in baseline}
qlib_map = {o["code"]: o for o in qlib_orders}

all_codes = sorted(set(baseline_map) | set(qlib_map))

print("=" * 90)
print("Baseline vs Qlib 调整对比")
print("=" * 90)
print(
    f"{'代码':<10} {'名称':<10} {'Baseline股数':>12} {'Qlib股数':>10} {'变化':>8} "
    f"{'Baseline金额':>14} {'Qlib金额':>14} {'信号':>8} {'因子':>6}"
)
print("-" * 90)

total_baseline_shares = 0
total_qlib_shares = 0
total_baseline_amount = 0.0
total_qlib_amount = 0.0
skips = []
boosts = []
cuts = []

for code in all_codes:
    b = baseline_map.get(code, {})
    q = qlib_map.get(code, {})
    b_shares = b.get("shares", 0)
    q_shares = q.get("shares", 0)
    b_amount = b.get("est_amount", 0)
    q_amount = q.get("est_amount", 0)
    signal_val = q.get("qlib_signal", "N/A")
    factor = q.get("qlib_factor", "N/A")

    if code not in qlib_map:
        change = "SKIP"
        skips.append(code)
    else:
        change = f"{((q_shares - b_shares) / b_shares * 100):+.1f}%" if b_shares else "NEW"
        if isinstance(factor, (int, float)) and factor >= 1.3:
            boosts.append(code)
        elif isinstance(factor, (int, float)) and factor <= 0.5:
            cuts.append(code)

    name = q.get("name", b.get("name", ""))
    print(
        f"{code:<10} {name:<10} {b_shares:>12} {q_shares:>10} {change:>8} "
        f"{b_amount:>14,.2f} {q_amount:>14,.2f} {str(signal_val):>8} {str(factor):>6}"
    )

    total_baseline_shares += b_shares
    total_qlib_shares += q_shares
    total_baseline_amount += b_amount
    total_qlib_amount += q_amount

print("-" * 90)
print(
    f"{'合计':<21} {total_baseline_shares:>12} {total_qlib_shares:>10} "
    f"{total_qlib_shares-total_baseline_shares:>+8} "
    f"{total_baseline_amount:>14,.2f} {total_qlib_amount:>14,.2f}"
)
print()
print("调整统计:")
print(f"  加仓: {len(boosts)} 笔 -> {boosts}")
print(f"  减仓: {len(cuts)} 笔 -> {cuts}")
print(f"  跳过: {len(skips)} 笔 -> {skips}")
print()

style_map = {
    "601088": "顺周期",
    "600519": "高端制造",
    "000858": "高端制造",
    "002594": "高端制造",
    "300750": "高端制造",
    "002475": "高端制造",
    "600900": "防御",
    "000001": "防御",
    "601318": "防御",
    "000333": "高端制造",
}

baseline_style = {}
qlib_style = {}
for code in all_codes:
    b = baseline_map.get(code, {})
    q = qlib_map.get(code, {})
    style = style_map.get(code, "其他")
    baseline_style[style] = baseline_style.get(style, 0) + b.get("est_amount", 0)
    qlib_style[style] = qlib_style.get(style, 0) + q.get("est_amount", 0)

print("风格暴露对比 (金额):")
print(f"{'风格':<12} {'Baseline':>14} {'Qlib调整':>14} {'变化':>14} {'变化%':>8}")
print("-" * 70)
for style in sorted(set(baseline_style) | set(qlib_style)):
    b = baseline_style.get(style, 0)
    q = qlib_style.get(style, 0)
    chg = q - b
    chg_pct = (chg / b * 100) if b else 0
    print(f"{style:<12} {b:>14,.2f} {q:>14,.2f} {chg:>+14,.2f} {chg_pct:>+7.1f}%")

# 实际成交金额对比
fills = report["phases"]["execute"].get("fills", [])
print()
print("MockBroker 实际成交:")
print(f"  成交笔数: {len(fills)}")
print(f"  成交金额: {sum(f.get('amount', 0) for f in fills):,.2f}")
print(f"   Baseline 成交笔数: {len(baseline)}")
print(f"   Baseline 成交金额估算: {sum(b.get('est_amount', 0) for b in baseline):,.2f}")
