# -*- coding: utf-8 -*-
"""从交易计划中剔除中国平安/盐湖股份/五粮液并同步下游文件"""
import json
from pathlib import Path

ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
PLAN_FILE = ROOT / "500万建仓计划_20260706.json"
POSITIONS_FILE = ROOT / "config" / "positions.json"
PORTFOLIO_FILE = ROOT / "v7.5_institutional" / "config" / "portfolio.yaml"
TRADE_PLAN_JSON = ROOT / "v7.5_institutional" / "trade_plans" / "trade_plan_20260706.json"
TRADE_PLAN_MD = ROOT / "v7.5_institutional" / "trade_plans" / "trade_plan_20260706.md"
WATCHLIST = ROOT / "v7.5_institutional" / "ifind_watchlist_20260706.csv"
REMOVE_KEYS = {"sz000792", "sh601318", "sz000858"}
REMOVE_CODES = {"000792", "601318", "000858"}

# 1. 500万建仓计划
with open(PLAN_FILE, "r", encoding="utf-8") as f:
    plan = json.load(f)
plan["metadata"]["target_count"] = 25
for key in REMOVE_KEYS:
    plan["target_portfolio"].pop(key, None)
    plan["position_plan"].pop(key, None)
for phase in plan["phase_summary"]:
    phase["asset_count"] = 25
    phase["assets"] = [a for a in phase["assets"] if a["code"] not in REMOVE_CODES]
    phase["total_actual"] = round(sum(a["amount"] for a in phase["assets"]), 2)
with open(PLAN_FILE, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

# 2. positions.json
with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
    positions = json.load(f)
for key in REMOVE_KEYS:
    positions.get("positions", {}).pop(key, None)
with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
    json.dump(positions, f, ensure_ascii=False, indent=2)

# 3. portfolio.yaml
portfolio_text = PORTFOLIO_FILE.read_text(encoding="utf-8")
for code in REMOVE_CODES:
    portfolio_text = "\n".join(line for line in portfolio_text.splitlines() if f"  {code}:" not in line)
PORTFOLIO_FILE.write_text(portfolio_text, encoding="utf-8")

# 4. trade_plan JSON
with open(TRADE_PLAN_JSON, "r", encoding="utf-8") as f:
    trade_plan = json.load(f)
trade_plan["phase"]["asset_count"] = 25
trade_plan["execution_plan"]["morning_orders"] = [o for o in trade_plan["execution_plan"].get("morning_orders", []) if o.get("code") not in REMOVE_CODES]
trade_plan["execution_plan"]["afternoon_orders"] = [o for o in trade_plan["execution_plan"].get("afternoon_orders", []) if o.get("code") not in REMOVE_CODES]
trade_plan["execution_plan"]["total_orders"] = len(trade_plan["execution_plan"]["morning_orders"]) + len(trade_plan["execution_plan"]["afternoon_orders"])
trade_plan["execution_plan"]["morning_total"] = round(sum(o.get("est_amount", 0) for o in trade_plan["execution_plan"]["morning_orders"]), 2)
trade_plan["execution_plan"]["afternoon_total"] = round(sum(o.get("est_amount", 0) for o in trade_plan["execution_plan"]["afternoon_orders"]), 2)
trade_plan["execution_plan"]["grand_total"] = round(trade_plan["execution_plan"]["morning_total"] + trade_plan["execution_plan"]["afternoon_total"], 2)
for item in trade_plan.get("phase_roadmap", []):
    item["asset_count"] = 25
with open(TRADE_PLAN_JSON, "w", encoding="utf-8") as f:
    json.dump(trade_plan, f, ensure_ascii=False, indent=2)

# 5. trade_plan Markdown
lines = TRADE_PLAN_MD.read_text(encoding="utf-8").splitlines()
new_lines = []
for line in lines:
    if "| **建仓标的** |" in line or "| **建仓标的数** |" in line:
        continue
    if "28 只" in line:
        line = line.replace("28 只", "25 只")
    if "56 笔订单" in line:
        line = line.replace("56 笔订单", "50 笔订单")
    if "1,781,556" in line:
        line = line.replace("1,781,556", "1,533,491")
    if "1,802,956" in line:
        line = line.replace("1,802,956", "1,541,813")
    if "| **合计** |" in line and "28 |" in line:
        line = line.replace("28 |", "25 |")
    new_lines.append(line)
TRADE_PLAN_MD.write_text("\n".join(new_lines), encoding="utf-8")

# 6. iFinD 自选股 CSV
rows = WATCHLIST.read_text(encoding="utf-8").strip().splitlines()
new_rows = [rows[0]]
for row in rows[1:]:
    code = row.split(",", 1)[0]
    if code not in REMOVE_CODES:
        new_rows.append(row)
WATCHLIST.write_text("\n".join(new_rows) + "\n", encoding="utf-8")

print("[OK] 已剔除 000792/601318/000858，并同步：")
print(" -", PLAN_FILE)
print(" -", POSITIONS_FILE)
print(" -", PORTFOLIO_FILE)
print(" -", TRADE_PLAN_JSON)
print(" -", TRADE_PLAN_MD)
print(" -", WATCHLIST)
