"""
对 trade_plan_20260728.json 应用 7-Guard 链并检查最终状态
"""
import sys
from pathlib import Path

BASE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "v8.3_institutional"))
sys.path.insert(0, str(BASE / "utils"))

from risk_guard_integrator import RiskGuardIntegrator

integrator = RiskGuardIntegrator(report_date="2026-07-25")
plan = integrator.run_all_guards(next_trade_date="2026-07-28")

print()
print("=" * 72)
print("trade_plan_20260728.json 应用 7-Guard 后状态")
print("=" * 72)

ms = plan.get("market_state", {})
print(f"circuit_level: {ms.get('circuit_level')}")
print(f"build_allowed: {ms.get('build_allowed')}")
print(f"spot_build_allowed: {ms.get('spot_build_allowed')}")

rg = plan.get("risk_guard", {})
print(f"vol_scale: {rg.get('vol_scale')}")
print(f"vol_scale_executed_summary.note: {rg.get('vol_scale_executed_summary', {}).get('note', '')[:80]}")
print(f"overnight_gap.level: {rg.get('overnight_gap', {}).get('level')}")
print(f"liquidity_crisis.data_unavailable: {rg.get('liquidity_crisis', {}).get('data_unavailable')}")

ep = plan.get("execution_plan", {})
print(f"execution_plan.total_orders: {ep.get('total_orders')}")
print(f"execution_plan.day_capital: {ep.get('day_capital')}")
print(f"execution_plan.original_day_capital: {ep.get('original_day_capital')}")
print(f"execution_plan.morning_orders count: {len(ep.get('morning_orders', []))}")
print(f"execution_plan.options_orders count: {len(ep.get('options_orders', []))}")

phase = plan.get("phase", {})
print(f"phase.original_daily_capital: {phase.get('original_daily_capital')}")
print(f"phase.daily_capital: {phase.get('daily_capital')}")
print(f"phase.vol_scale_applied: {phase.get('vol_scale_applied')}")

he = plan.get("hedge_execution", {})
print(f"hedge_execution.execution_status: {he.get('execution_status')}")
print(f"hedge_execution.options_orders count: {len(he.get('options_orders', []))}")

foh = plan.get("futures_options_hedge", {})
print(f"futures_options_hedge.loaded: {foh.get('loaded')}")
print(f"futures_options_hedge.orders_count: {foh.get('orders_count')}")
