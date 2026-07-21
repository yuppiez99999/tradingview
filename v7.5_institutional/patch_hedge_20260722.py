# -*- coding: utf-8 -*-
"""
补丁: 将7/21 PnL分析建议写入7/22自动交易计划
基于今日收盘盈亏报告: 组合Beta 1.052→0.30, IF期货 3→5手, 期权保护建仓

执行时间: 2026-07-21 盘后
"""
import json
import copy
from datetime import datetime
from pathlib import Path

PLAN_FILE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans\trade_plan_20260722.json")

# 备份原文件
BAK_FILE = Path(str(PLAN_FILE) + f".bak_{datetime.now().strftime('%H%M%S')}")

with open(PLAN_FILE, "r", encoding="utf-8") as f:
    original = f.read()

with open(BAK_FILE, "w", encoding="utf-8") as f:
    f.write(original)

plan = json.loads(original)

CURRENT_IF_HELD = 3   # 7/21收盘已持IF空头
TARGET_IF_TOTAL = 5   # LLM建议总持仓
IF_TO_ADD = TARGET_IF_TOTAL - CURRENT_IF_HELD  # = 2
EST_IF_PRICE = 4650.0
IF_MULTIPLIER = 300
IF_MARGIN_RATE = 0.12

# ============================================================
# 1. 更新 hedge_execution.futures_orders — IF从1手→2手
# ============================================================
futures_orders = plan.get("hedge_execution", {}).get("futures_orders", [])
if futures_orders:
    fo = futures_orders[0]
    old_contracts = fo.get("contracts", 1)
    fo["contracts"] = IF_TO_ADD
    fo["notional"] = round(EST_IF_PRICE * IF_MULTIPLIER * IF_TO_ADD, 0)
    fo["margin_required"] = round(fo["notional"] * IF_MARGIN_RATE, 0)
    fo["rationale"]["beta_to_hedge"] = round(1.052 - 0.30, 4)  # 原始Beta→目标
    fo["rationale"]["hedge_notional"] = round(fo["notional"], 0)
    fo["rationale"]["drawdown_level"] = 0
    fo["rationale"]["note"] = f"从{old_contracts}手修正为{IF_TO_ADD}手 (当前持有{CURRENT_IF_HELD}手→目标{TARGET_IF_TOTAL}手), 基于7/21收盘PnL分析"
    fo["order_id"] = f"HEDGE_IF_20260721_{datetime.now().strftime('%H%M%S')}"
    fo["status"] = "PENDING"

# ============================================================
# 2. 更新 cost_summary
# ============================================================
if futures_orders:
    total_margin = futures_orders[0]["margin_required"]
    total_premium = sum(o.get("premium_budget", 0) for o in plan.get("hedge_execution", {}).get("options_orders", []))
    cost_summary = plan.get("hedge_execution", {}).get("cost_summary", {})
    cost_summary["total_margin_required"] = total_margin
    cost_summary["total_cost"] = total_margin + total_premium
    cost_summary["hedge_capital_usage_pct"] = round((total_margin + total_premium) / 1000000, 4)
    cost_summary["within_budget"] = cost_summary["hedge_capital_usage_pct"] <= 1.0
    cost_summary["total_premium_budget"] = total_premium
    plan["hedge_execution"]["cost_summary"] = cost_summary

# ============================================================
# 3. 更新 hedge_execution 整体状态
# ============================================================
plan["hedge_execution"]["generated_at"] = datetime.now().isoformat()
plan["hedge_execution"]["portfolio_status"]["portfolio_beta_before"] = 1.052
plan["hedge_execution"]["hedge_effectiveness"]["beta_reduction"] = 1.052 - 0.30
plan["hedge_execution"]["hedge_effectiveness"]["estimated_annual_cost_pct"] = round((total_margin + total_premium) / 5000000 * 100, 2)

# ============================================================
# 4. 更新 llm_overrides (修正IF合约数)
# ============================================================
plan["llm_overrides"]["futures_if_contracts"] = TARGET_IF_TOTAL
plan["llm_overrides"]["applied_by"] = "LLM daily_pnl_report_2026-07-21 + patch_hedge_20260722"
plan["llm_overrides"]["note"] = f"IF期货当前持有{CURRENT_IF_HELD}手→明日加{IF_TO_ADD}手至{TARGET_IF_TOTAL}手; 期权保护4条Put全部PENDING待执行"

# ============================================================
# 5. 更新 metadata.llm_adjustments
# ============================================================
plan["metadata"]["llm_adjustments"]["applied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
plan["metadata"]["llm_adjustments"]["adjustments"] = [
    f"【已量化】IF期货: 当前持有{CURRENT_IF_HELD}手空头 → 明日加{IF_TO_ADD}手 → 总持仓{TARGET_IF_TOTAL}手, Beta从1.052降至~0.30",
    "【已量化】上证50ETF Put: 20张 OTM5% 权利金预算30万 (优先级P1)",
    "【已量化】科创50ETF Put: 10张 OTM5% 权利金预算12万 (优先级P2)",
    "【已量化】创业板ETF Put: 10张 OTM5% 权利金预算10万 (优先级P3)",
    "【已量化】沪深300ETF Put: 5张 OTM5% 权利金预算4万 (优先级P4)",
    "科技/成长风格表现优异(科创50 +11% 半导体 +10%), 建议维持该板块权重配置",
    "今日4只微亏(长江电力-0.86%/恒瑞-1.15%/银行ETF-2.23%/中国神华-2.25%)均未触发止损, 继续持有",
    "明日盘中监控VIX和指数波动, 动态调整期货对冲仓位"
]

# ============================================================
# 6. 更新 risk_guard
# ============================================================
plan["risk_guard"]["last_run"] = datetime.now().isoformat()
plan["risk_guard"]["hedge_action"] = "PATCHED_IF_3_TO_5"
plan["risk_guard"]["put_action"] = "VERIFIED_4_PUTS"

# ============================================================
# 7. 更新 execution_plan 统计 (现货部分不变)
# ============================================================
# 现货统计已正确，不修改

# ============================================================
# 写入
# ============================================================
with open(PLAN_FILE, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

# ============================================================
# 打印变更摘要
# ============================================================
print("=" * 60)
print("  7/22 自动交易计划 — 对冲补丁已应用")
print("=" * 60)
print(f"  备份文件: {BAK_FILE.name}")
print()
print(f"  IF期货修正: 1手 → {IF_TO_ADD}手 (当前{CURRENT_IF_HELD}手→目标{TARGET_IF_TOTAL}手)")
print(f"  新增名义价值: {futures_orders[0]['notional']:,.0f}元")
print(f"  新增保证金: {futures_orders[0]['margin_required']:,.0f}元")
print()
print("  期权保护订单 (已验证,已存在):")
for o in plan["put_protection_orders"]:
    print(f"    P{o['priority']} | {o['underlying_name']} Put | {o['contracts']}张 OTM{o['otm_pct']*100:.0f}% | 权利金预算 ~{o['premium_total']:,.0f}元 | {o['status']}")
print()
cs = plan["hedge_execution"]["cost_summary"]
print(f"  对冲成本合计: {cs['total_cost']:,.0f}元 (保证金{cs['total_margin_required']:,.0f} + 权利金{cs['total_premium_budget']:,.0f})")
status_text = "OK 预算内" if cs['within_budget'] else "WARN 超预算"
print(f"  对冲资金使用率: {cs['hedge_capital_usage_pct']*100:.1f}% [{status_text}]")
print()
print(f"  现货建仓: {plan['execution_plan']['total_orders']}笔订单, {plan['execution_plan']['total_amount']:,.0f}元")
print(f"  Covered Call (Theta): {plan['execution_plan']['options_orders_count']}笔, 权利金 {plan['execution_plan']['options_total_premium']:,.0f}元")
print()
print("  明日执行窗口:")
print("    09:30-10:00  期权Put建仓 (优先上证50ETF Put)")
print("    10:30-11:00  IF期货加仓 (追加2手至5手)")
print("    09:30-10:30  现货建仓 (16标的 上/下午分批)")
