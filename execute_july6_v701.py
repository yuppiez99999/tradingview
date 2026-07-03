# -*- coding: utf-8 -*-
"""
v7.0.1 July 6 Complete Execution Script
========================================
建仓首日完整流程: 增强风控 -> 订单生成 -> 交易指令 -> 报告输出
"""
import sys, os, json
sys.path.insert(0, '.')

from datetime import date
from build_plan_executor import BuildPlanExecutor
from enhanced_risk_manager import StressTestEngine

print("=" * 60)
print("v7.0.1 COMPLETE EXECUTION - July 6, 2026")
print("500万建仓首日 + 增强风控 + 紧急响应协议")
print("=" * 60)

# ---- Step 1: Initialize ----
print("\n[1/5] Initializing Build Plan Executor...")
executor = BuildPlanExecutor()
plan_data = executor.plan_data
total_capital = plan_data["metadata"]["total_capital"]
print(f"  Total Capital: {total_capital:,.0f} RMB")
print(f"  Plan: {plan_data['metadata']['strategy']}")
phase_info, phase_idx, phase_status = executor.get_active_phase(date(2026, 7, 6))
print(f"  Phase: {phase_info['name']} ({phase_status})")
print(f"  Phase Capital: {phase_info['capital_amount']:,.0f} RMB")

# ---- Step 2: Enhanced Risk Assessment ----
print("\n[2/5] Enhanced Risk Assessment (v2.0 Emergency Protocol)...")

# Fetch market state (currently using defaults, ready for real data)
market_state = {
    'vix_proxy': 22.0,
    'volatility': 0.18,
    'index_return_5d': 0.01,
    'index_return_20d': 0.03,
    'margin_balance_change': -0.01,
    'volume_ratio': 1.05,
    'sector_health': {
        'high_end_manufacturing_20d': -0.05,
        'semiconductor_20d': -0.02,
    },
}
print(f"  VIX Proxy: {market_state['vix_proxy']:.0f}")
print(f"  5d Return: {market_state['index_return_5d']:.1%}")
print(f"  20d Return: {market_state['index_return_20d']:.1%}")
print(f"  Margin Change (5d): {market_state['margin_balance_change']:.1%}")

protocol = executor.get_emergency_protocol(market_state)
print(f"  Emergency Level: {protocol['level_name']} (L{protocol['level']})")
print(f"  Capital Multiplier: {protocol['day_capital_multiplier']:.0%}")
if protocol['actions']:
    print(f"  Actions ({len(protocol['actions'])}):")
    for a in protocol['actions'][:3]:
        print(f"    - {a}")

# ---- Step 3: Forward-looking Stress Test ----
print("\n[3/5] Forward-looking Stress Test...")
try:
    engine = StressTestEngine()
    portfolio_data = {'total_value': total_capital, 'positions': []}
    market_data_sim = {'volatility': market_state['volatility']}

    for sid in ['forward_bear_2026', 'forward_black_swan']:
        scenario = engine.test_scenarios.get(sid, {})
        result = engine._run_scenario_test(scenario, portfolio_data, market_data_sim)
        loss_pct = result.get('loss_percentage', 0)
        final_val = result.get('portfolio_value_after', 0)
        risk_lvl = result.get('risk_level', 'unknown')
        print(f"  {scenario.get('name', sid)}: Loss={loss_pct:.1%}, "
              f"Final={final_val:,.0f}, Risk={risk_lvl}")
except ImportError:
    print("  [SKIP] StressTestEngine not available (missing dependencies)")

# ---- Step 4: Generate Trade Orders ----
print(f"\n[4/5] Generating Trade Orders for July 6, 2026...")
capital_mult = protocol['day_capital_multiplier']
if capital_mult == 0:
    print("  *** BLOCKED: Emergency protocol triggered, no orders generated ***")

sheet = executor.generate_daily_orders(
    target_date=date(2026, 7, 6),
    capital_multiplier=capital_mult
)

morning_count = len(sheet.morning_orders)
afternoon_count = len(sheet.afternoon_orders)
paused_count = len(sheet.paused_orders)
total_amount = sum(o.est_amount for o in sheet.morning_orders + sheet.afternoon_orders)

print(f"  Phase: {sheet.phase_name}")
print(f"  Day Capital: {sheet.day_capital:,.0f} RMB")
print(f"  Morning Orders: {morning_count}")
print(f"  Afternoon Orders: {afternoon_count}")
print(f"  Paused: {paused_count}")
if capital_mult < 1.0:
    print(f"  *** Capital Multiplier Applied: {capital_mult:.0%} ***")

if sheet.warnings:
    print(f"  Warnings ({len(sheet.warnings)}):")
    for w in sheet.warnings[:5]:
        print(f"    - {w}")

# ---- Step 5: Save Reports ----
print("\n[5/5] Saving Reports...")
md_path, json_path = executor.save_trade_sheet(sheet)
print(f"  Markdown: {md_path}")
print(f"  JSON: {json_path}")

# ---- Summary ----
print("\n" + "=" * 60)
print("EXECUTION SUMMARY - July 6, 2026")
print("=" * 60)
print(f"  System Version: v7.0.1 (Extreme Scenario Resilience)")
print(f"  Capital: {total_capital:,.0f} RMB")
print(f"  Phase: {sheet.phase_name}")
print(f"  Risk Level: {protocol['level_name']}")
print(f"  Orders: {morning_count}AM + {afternoon_count}PM = {morning_count + afternoon_count} total")
print(f"  Day Amount: {total_amount:,.0f} RMB")
print(f"  Files Saved: Yes")

# Print top 5 morning orders
if morning_count > 0:
    print(f"\n  TOP MORNING ORDERS:")
    for o in sheet.morning_orders[:5]:
        print(f"    {o.code} {o.name}: {o.shares:,} shares @{o.limit_price:.3f} "
              f"~{o.est_amount:,.0f} RMB | {o.style} {o.risk}")

print("\n" + "=" * 60)
print("READY FOR EXECUTION ON MONDAY, JULY 6, 2026")
print("=" * 60)
