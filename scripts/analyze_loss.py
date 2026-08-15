"""亏损分析脚本."""
import json
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

# 1. 观察期收益分析
records = [json.loads(l) for l in (PROJ / "reports/shadow/daily_returns.jsonl").read_text(encoding="utf-8").strip().split("\n") if l.strip()]
returns = [r["daily_return"] for r in records]
neg_days = [r for r in records if r["daily_return"] < 0]
pos_days = [r for r in records if r["daily_return"] > 0]

cum = 1.0
for r in records:
    cum *= (1 + r["daily_return"])

p =G = lambda x: f"{x*100:+.4f}%"

print(f"=== 观察期收益分析 ({len(records)} 天) ===")
print(f"累计收益: {p(cum - 1)}")
print(f"盈利天数: {len(pos_days)} ({len(pos_days)/len(records)*100:.0f}%)")
print(f"亏损天数: {len(neg_days)} ({len(neg_days)/len(records)*100:.0f}%)")
print(f"最大单日盈利: {max(r['daily_return'] for r in records)*100:+.4f}% ({max(records, key=lambda r: r['daily_return'])['date']})")
print(f"最大单日亏损: {min(r['daily_return'] for r in records)*100:+.4f}% ({min(records, key=lambda r: r['daily_return'])['date']})")
print(f"平均日收益: {sum(returns)/len(returns)*100:+.4f}%")
print()
print("最近5天:")
for r in records[-5:]:
    print(f"  {r['date']}: {p(r['daily_return'])}")
recent5_cum = 1.0
for r in records[-5:]:
    recent5_cum *= (1 + r["daily_return"])
print(f"最近5天累计: {p(recent5_cum - 1)}")
print()

# 2. 持仓分析
pos = json.loads((PROJ / "config/positions.json").read_text(encoding="utf-8"))
positions = {k: v for k, v in pos["positions"].items() if v["shares"] > 0}
print(f"=== 持仓分析 ({len(positions)} 个标的) ===")

# 按权重排序
sorted_pos = sorted(positions.items(), key=lambda x: x[1]["shares"] * x[1].get("est_price", 0), reverse=True)
total_value = sum(v["shares"] * v.get("est_price", 0) for v in positions.values())
print(f"总市值: ¥{total_value:,.0f}")
print()
for code, v in sorted_pos[:10]:
    mv = v["shares"] * v.get("est_price", 0)
    weight = mv / total_value if total_value > 0 else 0
    print(f"  {code:12s} {v.get('name',''):16s} {v['shares']:>6d}股 @ {v.get('est_price',0):>8.3f} = ¥{mv:>10,.0f} ({weight*100:5.1f}%)")
print()

# 3. 检查是否有风控配置
import os
risk_files = []
for root, dirs, files in os.walk(PROJ / "config"):
    for f in files:
        if any(k in f.lower() for k in ["risk", "stop", "loss", "hedge"]):
            risk_files.append(os.path.join(root, f))
print(f"=== 风控配置文件 ({len(risk_files)} 个) ===")
for f in risk_files[:5]:
    print(f"  {f}")