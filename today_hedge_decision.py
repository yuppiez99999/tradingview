# -*- coding: utf-8 -*-
"""
今日开仓对冲决策 - 优化版
基于组合结构的智能评估 + v7.5 对冲引擎
"""

import sys
import json
import pandas as pd
from collections import defaultdict

sys.path.insert(0, r"e:\各种PY程序\28-终极量化交易系统7.1")
sys.path.insert(0, r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\src")

from hedging.hedge_coordinator import HedgeCoordinator

# ============================================================
# 1. 读取持仓
# ============================================================
positions_path = r"e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json"
plan_path = r"e:\各种PY程序\28-终极量化交易系统7.1\500万建仓计划_20260706.json"

with open(positions_path, "r", encoding="utf-8") as f:
    positions_data = json.load(f)["positions"]

with open(plan_path, "r", encoding="utf-8") as f:
    plan_data = json.load(f)

positions = {}
prices = {}
style_map = {}
risk_map = {}
for _key, item in positions_data.items():
    code = item.get("code")
    qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
    price = item.get("est_price", 0.0)
    if code and qty:
        positions[code] = float(qty)
        prices[code] = float(price)
        style_map[code] = item.get("style", "其他")
        risk_map[code] = item.get("risk", "中")

# ============================================================
# 2. 组合结构分析
# ============================================================
portfolio_value = 5_000_000.0
day_capital = 1_722_410.0
deployed_ratio = day_capital / portfolio_value

# 按风格统计
style_amounts = defaultdict(float)
style_counts = defaultdict(int)
for code, qty in positions.items():
    amt = qty * prices.get(code, 0.0)
    style = style_map.get(code, "其他")
    style_amounts[style] += amt
    style_counts[style] += 1

# 按风险统计
risk_amounts = defaultdict(float)
for code, qty in positions.items():
    amt = qty * prices.get(code, 0.0)
    risk = risk_map.get(code, "中")
    risk_amounts[risk] += amt

# 集中度分析
sorted_positions = sorted(positions.items(), key=lambda x: x[1] * prices.get(x[0], 0.0), reverse=True)
top5 = sorted_positions[:5]
top5_amount = sum(qty * prices.get(code, 0.0) for code, qty in top5)

# B-4.2: 风格Beta代理统一到 utils/risk/style_beta.py (消除 DRY 违规)
# 注: 本脚本引用 7.1 旧路径, 用 try/except 兼容 8.4 统一模块导入
try:
    from utils.risk.style_beta import STYLE_BETA_PROXY as style_beta_proxy
except ImportError:
    # 回退: 7.1 旧路径环境下使用硬编码 (与 utils/risk/style_beta.py 保持同步)
    style_beta_proxy = {
        "宽基": 0.95, "高端制造": 1.15, "科技": 1.20, "制造": 1.05,
        "新能源": 1.10, "医药": 0.85, "化工": 1.00, "银行": 0.75,
        "防御": 0.60, "顺周期": 1.10, "避险": -0.10, "红利": 0.70, "成长": 1.25,
    }

portfolio_beta_est = 0.0
for code, qty in positions.items():
    amt = qty * prices.get(code, 0.0)
    style = style_map.get(code, "其他")
    beta = style_beta_proxy.get(style, 1.0)
    portfolio_beta_est += (amt / day_capital) * beta if day_capital > 0 else 0.0

# ============================================================
# 3. 运行 v7.5 对冲引擎 (使用空数据作为 baseline)
# ============================================================
returns = pd.DataFrame()
market_returns = pd.Series(dtype=float)
vix = 25.0
hwm_drawdown = 0.03
bs_loss = 0.0

coordinator = HedgeCoordinator(enable_tail_risk=True)
plan = coordinator.coordinate(
    positions=positions,
    prices=prices,
    returns=returns,
    market_returns=market_returns,
    vix=vix,
    portfolio_value=portfolio_value,
    hwm_drawdown=hwm_drawdown,
    bs_loss=bs_loss,
)

# ============================================================
# 4. 优化判断逻辑
# ============================================================
print("=" * 60)
print("今日开仓对冲决策 - 优化版")
print("=" * 60)
print("日期: 2026-07-06")
print("阶段: 第一阶段-底仓建立")
print(f"组合总市值: {portfolio_value:,.0f}")
print(f"今日建仓金额: {day_capital:,.0f}")
print(f"建仓进度: {deployed_ratio * 100:.1f}%")
print()

print("-" * 60)
print("一、组合结构分析")
print("-" * 60)
print("按风格分布:")
for style in [
    "宽基",
    "高端制造",
    "科技",
    "制造",
    "新能源",
    "医药",
    "化工",
    "银行",
    "防御",
    "顺周期",
    "避险",
    "红利",
    "成长",
]:
    if style in style_amounts:
        amt = style_amounts[style]
        pct = amt / day_capital * 100 if day_capital > 0 else 0
        print(f"  {style:8s}: {amt:>10,.0f} 元 ({pct:5.1f}%)")
print()
print(f"估算组合Beta: {portfolio_beta_est:.2f}")
print(f"Top5集中度: {top5_amount / day_capital * 100:.1f}%")
for i, (code, qty) in enumerate(top5, 1):
    name = style_map.get(code, code)
    amt = qty * prices.get(code, 0.0)
    print(f"  {i}. {name:12s} {amt:>10,.0f} 元")

print()
print("按风险分布:")
for risk in ["低", "中", "中高", "高"]:
    if risk in risk_amounts:
        amt = risk_amounts[risk]
        pct = amt / day_capital * 100 if day_capital > 0 else 0
        print(f"  {risk:4s}: {amt:>10,.0f} 元 ({pct:5.1f}%)")

print()
print("-" * 60)
print("二、v7.5 对冲引擎原始输出")
print("-" * 60)
print(f"动作: {plan.get('action')}")
print(f"市场状态: {plan.get('regime')}")
print(f"组合Beta: {plan.get('portfolio_beta')}")
print(f"总对冲比例: {float(plan.get('total_hedge_pct', 0.0) or 0.0) * 100:.2f}%")
print(f"总成本比例: {float(plan.get('total_cost_pct', 0.0) or 0.0) * 100:.4f}%")
print(f"最大对冲上限: {float(plan.get('max_hedge_limit', 0.0) or 0.0) * 100:.2f}%")
print("分项:")
for k in ["beta_hedge", "vol_hedge", "corr_hedge", "tail_hedge"]:
    print(f"  {k}: {plan.get('summary', {}).get(k, 'N/A')}")

print()
print("-" * 60)
print("三、优化判断")
print("-" * 60)

# 优化判断逻辑
beta_over = portfolio_beta_est > 0.7
tech_heavy = style_amounts.get("科技", 0) / day_capital > 0.20 if day_capital > 0 else False
mfg_heavy = style_amounts.get("制造", 0) / day_capital > 0.30 if day_capital > 0 else False
high_risk_ratio = risk_amounts.get("高", 0) / day_capital if day_capital > 0 else 0
defense_weak = (
    (style_amounts.get("防御", 0) + style_amounts.get("避险", 0)) / day_capital < 0.15 if day_capital > 0 else True
)
single_large = top5_amount / day_capital > 0.40 if day_capital > 0 else False

# 剩余待建仓比例
future_ratio = 1.0 - deployed_ratio

if beta_over or tech_heavy or mfg_heavy or high_risk_ratio > 0.30 or single_large:
    print("结论: 建议开启/准备对冲")
    print("理由:")
    if beta_over:
        print(f"  - 估算组合Beta {portfolio_beta_est:.2f} > 0.7 触发阈值")
    if tech_heavy:
        tech_pct = style_amounts.get("科技", 0) / day_capital * 100
        print(f"  - 科技风格占比 {tech_pct:.1f}% > 20%，进攻性过强")
    if mfg_heavy:
        mfg_pct = style_amounts.get("制造", 0) / day_capital * 100
        print(f"  - 制造风格占比 {mfg_pct:.1f}% > 30%，周期敞口过大")
    if high_risk_ratio > 0.30:
        print(f"  - 高风险标的占比 {high_risk_ratio * 100:.1f}% > 30%，波动风险偏高")
    if single_large:
        print(f"  - Top5集中度 {top5_amount / day_capital * 100:.1f}% > 40%，个股集中度风险")
    if defense_weak:
        print("  - 防御/避险资产占比偏低，组合缺乏下跌保护")
else:
    print("结论: 暂不开启额外对冲")
    print("理由:")
    print(f"  - 估算组合Beta {portfolio_beta_est:.2f} <= 0.7")
    print(f"  - 科技+制造合计 {(style_amounts.get('科技', 0) + style_amounts.get('制造', 0)) / day_capital * 100:.1f}%")
    print(f"  - 高风险占比 {high_risk_ratio * 100:.1f}%")
    print("  - 当前仅第一阶段22.5%底仓，暴露可控")

print()
print("-" * 60)
print("四、对冲建议 (分阶段)")
print("-" * 60)
print(f"今日开仓: {day_capital:,.0f} 元")
print(f"后续待建仓: {portfolio_value * future_ratio:,.0f} 元")
print()
print("如果决定对冲，建议分三步走：")
print()
print("1. 今日建仓后观察窗口")
print("   - 不急于立即开对冲仓位")
print("   - 观察尾盘涨跌幅、波动率变化")
print("   - 若尾盘跳水 > 1.5%，转为立即保护")
print()
print("2. 明日开盘前确认")
print("   - 根据隔夜 A50/外围市场判断")
print("   - 若外围走弱，启动 Beta 对冲")
print("   - 若 VIX > 30，启动波动率对冲")
print()
print("3. 第二阶段前锁定")
print("   - 第二阶段(7/20)前完成对冲框架搭建")
print("   - 预留 100 万对冲资金中的 20-30%")
print("   - 优先使用：")
print("     * Beta对冲: IF/IC 股指期货空头")
print("     * 尾部保护: 黄金ETF(518880)已持有")
print("     * 期权保护: 沪深300ETF期权 Put Spread")

print()
print("-" * 60)
print("五、关键触发条件")
print("-" * 60)
print("Beta对冲触发: 组合Beta > 0.7")
print("波动率对冲触发: VIX > 30")
print("相关性对冲触发: 持仓平均相关 > 0.85 且跳升 > 0.15")
print("尾部风险触发: 回撤 > 5% 或 VIX > 25")
print()
print("当前状态:")
print(f"  估算Beta: {portfolio_beta_est:.2f} {'(已触发)' if portfolio_beta_est > 0.7 else '(未触发)'}")
print(f"  VIX假设: {vix:.1f} {'(已触发)' if vix > 30 else '(未触发)'}")
print(f"  当前回撤: {hwm_drawdown * 100:.1f}%")
print(f"  市场状态: {plan.get('regime')}")

print()
print("-" * 60)
print("六、下一步动作")
print("-" * 60)
if portfolio_beta_est > 0.7 or high_risk_ratio > 0.30:
    print("[建议] 启动 Beta 对冲评估")
    print("  - 计算实际持仓 Beta (需要历史收益率)")
    print("  - 确定 IF/IC/IM 期货空头手数")
    print("  - 估算对冲成本，若 > 0.3% 则降级为期权保护")
else:
    print("[建议] 暂缓对冲，继续观察")
    print("  - 明日开盘前重新评估")
    print("  - 关注外围市场 + A50 走势")
    print("  - 若 VIX 抬升或回撤扩大，立即启动")

print()
print("=" * 60)
print("备注: 本结果为基于持仓结构的结构化评估")
print("      真实 Beta/相关性需历史收益率数据支持")
print("=" * 60)
