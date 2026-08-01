"""测试 risk_guard_integrator pnl_report 加载修复"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "utils"))

# 1. 验证 7-24 报告可读
p = BASE / "每日报告归档" / "2026-07-24" / "daily_pnl_report_2026-07-24.json"
print(f"Report path: {p}")
print(f"Exists: {p.exists()}")
if p.exists():
    import json
    d = json.load(open(p, encoding='utf-8'))
    print(f"Top keys: {list(d.keys())[:10]}")
    pp = d.get('portfolio_pnl', {})
    print(f"portfolio_pnl keys: {list(pp.keys())[:10] if isinstance(pp, dict) else 'N/A'}")
    summary = pp.get('summary', {}) if isinstance(pp, dict) else {}
    print(f"summary: {summary}")
    positions = pp.get('positions', []) or pp.get('details', [])
    print(f"positions count: {len(positions)}")

# 2. 用 risk_guard_integrator 测试加载
print("\n=== 测试 RiskGuardIntegrator._load_pnl_report() ===")
from risk_guard_integrator import RiskGuardIntegrator

rgi = RiskGuardIntegrator(report_date='2026-07-24')
r = rgi._load_pnl_report()
print(f"pnl_report loaded: {r is not None}")
if r:
    summary = rgi._get_pnl_summary(r)
    print(f"summary keys: {list(summary.keys())[:10]}")
    print(f"total_cost: {summary.get('total_cost')}")
    print(f"total_market_value: {summary.get('total_market_value')}")
    print(f"total_pnl: {summary.get('total_pnl')}")
    positions = rgi._extract_positions(r)
    print(f"positions extracted: {len(positions)} 标的")
