# -*- coding: utf-8 -*-
"""v8.1 系统自检脚本"""

import sys
import json
from pathlib import Path

sys.path.insert(0, ".")

results = []


def check(name, ok, detail=""):
    results.append({"module": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}: {detail}")


print("=" * 70)
print("v8.1 系统自检 (System Health Check)")
print("=" * 70)

# ------------------------------------------------------------
# 1. Greeks 动态对冲
# ------------------------------------------------------------
print("\n[1/7] Greeks 动态对冲 (greek_hedge_manager)")
try:
    from utils.greek_hedge_manager import GreekHedgeManager

    mgr = GreekHedgeManager(target_delta=0.0, target_gamma=0.0, max_vega=50000.0, max_theta_burn=-5000.0)
    positions = {
        "600519": {
            "shares": 1000,
            "est_price": 1500.0,
            "beta": 1.0,
            "delta": 1.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
        }
    }
    prices = {"600519": 1500.0}
    exp = mgr.calc_portfolio_greeks(positions, prices)
    sig = mgr.rebalance_signal(exp, tolerance=0.05)
    check("GreekHedgeManager", exp.delta > 0 and isinstance(sig, dict), f"Delta={exp.delta:,.0f}, signals={len(sig)}")
except Exception as e:
    check("GreekHedgeManager", False, str(e))

# ------------------------------------------------------------
# 2. 交易成本模型
# ------------------------------------------------------------
print("\n[2/7] 交易成本模型 (transaction_cost_model)")
try:
    from utils.transaction_cost_model import TransactionCostModel

    tcm = TransactionCostModel()
    # 实际签名: estimate_total_cost(notional, adv, volatility, days_delayed, hours_delayed)
    notional = 1000 * 1500.0  # 1000 股 × ¥1500 = ¥1,500,000
    cost = tcm.estimate_total_cost(notional=notional, adv=1e8, volatility=0.02, days_delayed=1.0, hours_delayed=0.0)
    total = cost.get("total", 0) + cost.get("cost_bps", 0)
    check(
        "TransactionCostModel",
        isinstance(cost, dict) and total > 0,
        f"total=¥{cost.get('total', 0):,.2f}, bps={cost.get('cost_bps', 0):.2f}",
    )
except Exception as e:
    check("TransactionCostModel", False, str(e))

# ------------------------------------------------------------
# 3. 智能执行选择 (函数式接口)
# ------------------------------------------------------------
print("\n[3/7] 智能执行选择 (execution_selector)")
try:
    from utils.execution_selector import choose_execution_algorithm

    result = choose_execution_algorithm(
        target_amount=1_500_000.0,
        ref_price=1500.0,
        avg_daily_volume=1e8,
        max_execution_minutes=30,
    )
    if isinstance(result, dict):
        algo = result.get("algorithm") or result.get("selected_algorithm") or "unknown"
        check("ExecutionSelector", bool(algo), f"selected={algo}, keys={list(result.keys())[:5]}")
    else:
        check("ExecutionSelector", True, f"result_type={type(result).__name__}")
except Exception as e:
    check("ExecutionSelector", False, str(e))

# ------------------------------------------------------------
# 4. 风险归因面板
# ------------------------------------------------------------
print("\n[4/7] 风险归因面板 (risk_attribution)")
try:
    from utils.risk_attribution import compute_attribution, attribution_to_dict

    attr = compute_attribution()
    d = attribution_to_dict(attr)
    check(
        "RiskAttribution",
        attr.total_value > 0 and len(attr.by_sector) > 0,
        f"total=¥{attr.total_value:,.0f}, sectors={len(attr.by_sector)}, warnings={len(attr.warnings)}",
    )
except Exception as e:
    check("RiskAttribution", False, str(e))

# ------------------------------------------------------------
# 5. Greeks 监控面板
# ------------------------------------------------------------
print("\n[5/7] Greeks 监控面板 (greek_exposure_dashboard)")
try:
    from utils.greek_exposure_dashboard import compute_dashboard

    d = compute_dashboard()
    snap = d.snapshot
    check("GreekExposureDashboard", len(d.signal_levels) > 0, f"Delta={snap.delta:,.0f}, levels={d.signal_levels}")
except Exception as e:
    check("GreekExposureDashboard", False, str(e))

# ------------------------------------------------------------
# 6. LLM 盘中决策
# ------------------------------------------------------------
print("\n[6/7] LLM 盘中决策 (llm_intraday_decision_engine)")
try:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "llm_intraday_decision_engine", "v8.3_institutional/llm_intraday_decision_engine.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    classes = [c for c in dir(mod) if c.endswith("Engine") or c.endswith("Manager")]
    has_main = hasattr(mod, "main")
    check("IntradayDecisionEngine", len(classes) > 0 or has_main, f"classes={classes}, has_main={has_main}")
except Exception as e:
    check("IntradayDecisionEngine", False, str(e))

# ------------------------------------------------------------
# 7. 年化收益测算
# ------------------------------------------------------------
print("\n[7/7] 年化收益测算 (annual_return_forecast)")
try:
    from research.annual_return_forecast import forecast_annual_return

    f = forecast_annual_return()
    hc = f.get("summary", {}).get("hard_constraints", {})
    scenarios = f.get("scenarios", [])
    conservative = next((s for s in scenarios if s["name"] == "保守情景"), {})
    check(
        "AnnualReturnForecast",
        hc.get("all_constraints_met", False),
        f"保守年化={conservative.get('total_annual_return', 0):+.2%}, 硬约束={hc.get('all_constraints_met')}",
    )
except Exception as e:
    check("AnnualReturnForecast", False, str(e))

# ------------------------------------------------------------
# 数据文件完整性
# ------------------------------------------------------------
print("\n[数据文件完整性]")
data_files = [
    "config/positions.json",
    "v8.3_institutional/trade_plans",
    "v8.3_institutional/reports",
]
for f in data_files:
    p = Path(f)
    ok = p.exists()
    if ok and p.is_dir():
        n = len(list(p.glob("*.json")))
        check(f"数据:{f}", ok, f"{n} JSON 文件")
    else:
        check(f"数据:{f}", ok, "存在" if ok else "缺失")

# ------------------------------------------------------------
# 总结
# ------------------------------------------------------------
print("\n" + "=" * 70)
pass_count = sum(1 for r in results if r["status"] == "PASS")
fail_count = len(results) - pass_count
print(f"总结: {pass_count}/{len(results)} PASS, {fail_count} FAIL")
print("=" * 70)

# 输出 JSON 报告
report_path = Path("v8.3_institutional/reports/system_health_check.json")
report_path.parent.mkdir(parents=True, exist_ok=True)
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "check_time": __import__("datetime").datetime.now().isoformat(),
            "total": len(results),
            "pass": pass_count,
            "fail": fail_count,
            "results": results,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"详细报告已写入: {report_path}")

sys.exit(1 if fail_count else 0)
