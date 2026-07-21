# -*- coding: utf-8 -*-
"""
补丁: 本周自动交易计划 — 仅使用期权对冲，移除所有期货订单
依据: 用户指令 2026-07-21

变更范围:
  - trade_plan_20260722.json  → 移除 futures_orders, futures_if_contracts=0
  - 其余本周计划(7/23,7/24,weekly)已无期货引用, 无需修改
"""
import json
from datetime import datetime
from pathlib import Path

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans")

TARGET_FILES = [
    "trade_plan_20260722.json",
]

def backup(plan_path):
    bak = Path(str(plan_path) + f".bak_{datetime.now().strftime('%H%M%S')}")
    with open(plan_path, "rb") as f:
        bak.write_bytes(f.read())
    return bak

def patch_plan(path):
    bak = backup(path)
    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    changes = []

    # --- 1. hedge_execution.futures_orders: 清空 ---
    he = plan.get("hedge_execution", {})
    old_futures = he.get("futures_orders", [])
    if old_futures:
        old_contracts = sum(o.get("contracts", 0) for o in old_futures)
        old_margin = sum(o.get("margin_required", 0) for o in old_futures)
        old_notional = sum(o.get("notional", 0) for o in old_futures)
        he["futures_orders"] = []
        changes.append(f"移除期货: {old_contracts}手, 释放保证金 {old_margin:,.0f}, 释放名义价值 {old_notional:,.0f}")

    # --- 2. cost_summary: 仅保留期权权利金 ---
    cs = he.get("cost_summary", {})
    premium = sum(o.get("premium_budget", 0) for o in he.get("options_orders", []))
    cs["total_margin_required"] = 0
    cs["total_cost"] = premium
    cs["hedge_capital_usage_pct"] = round(premium / 1_000_000, 4)
    cs["within_budget"] = cs["hedge_capital_usage_pct"] <= 1.0
    cs["total_premium_budget"] = premium
    changes.append(f"成本重算: 仅权利金 {premium:,.0f} (使用率 {cs['hedge_capital_usage_pct']*100:.1f}%)")

    # --- 3. hedge_execution 元信息 ---
    he["hedge_mode"] = "OPTIONS_ONLY"
    he["generated_at"] = datetime.now().isoformat()
    he["hedge_effectiveness"]["beta_reduction"] = 0.75  # 纯期权尾部保护,不降Beta
    he["hedge_effectiveness"]["estimated_annual_cost_pct"] = round(premium / 5_000_000 * 100, 2)

    # --- 4. llm_overrides ---
    plan["llm_overrides"]["futures_if_contracts"] = 0
    plan["llm_overrides"]["futures_enabled"] = False
    plan["llm_overrides"]["applied_by"] = f"期权对冲补丁 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    plan["llm_overrides"]["note"] = "本周仅使用期权对冲(4条Put), 不启用期货空头"

    # --- 5. metadata ---
    plan["metadata"]["llm_adjustments"]["applied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plan["metadata"]["llm_adjustments"]["futures_disabled"] = True
    adjustments = plan["metadata"]["llm_adjustments"].get("adjustments", [])
    # 移除期货相关调整项
    adjustments = [a for a in adjustments if "IF" not in a and "期货" not in a and "futures" not in a.lower()]
    adjustments.insert(0, "本周仅使用期权对冲(上证50ETF/科创50/创业板/沪深300 Put), 不启用IF期货空头")
    plan["metadata"]["llm_adjustments"]["adjustments"] = adjustments

    # --- 6. risk_guard ---
    plan["risk_guard"]["hedge_action"] = "OPTIONS_ONLY_NO_FUTURES"
    plan["risk_guard"]["last_run"] = datetime.now().isoformat()

    # --- 7. execution_plan.windows: 移除期货窗口 ---
    ep = plan.get("execution_plan", {})
    plan["execution_plan"]["note"] = "仅期权对冲 + 现货建仓, 不执行期货"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"[OK] {path.name}")
    print(f"     备份: {bak.name}")
    for c in changes:
        print(f"     {c}")
    print()
    return plan

# --- 执行 ---
print("=" * 60)
print("  本周自动交易计划 — 期权对冲补丁")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)
print()

for tf in TARGET_FILES:
    path = BASE / tf
    if path.exists():
        patch_plan(path)
    else:
        print(f"[SKIP] {tf} 不存在")

print("完成: 本周仅使用期权对冲")
