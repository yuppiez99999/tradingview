#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速系统检查：验证 v7.5 关键配置与代码一致性
"""

import json
import os
import sys
import py_compile
from pathlib import Path

BASE_DIR = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
V75_DIR = BASE_DIR / "v7.5_institutional"

checks = []

def check(name, fn):
    try:
        ok, detail = fn()
        checks.append((name, ok, detail))
    except Exception as e:
        checks.append((name, False, f"ERROR: {e}"))

# 1. portfolio.yaml
def check_portfolio():
    p = V75_DIR / "config" / "portfolio.yaml"
    if not p.exists():
        return False, "文件不存在"
    text = p.read_text(encoding="utf-8")
    ok = "equity_allocation: 3_000_000" in text and "hedge_allocation: 2_000_000" in text
    return ok, "equity=300万, hedge=200万" if ok else "资金分配不符合 60/40"

check("portfolio.yaml 资金分配", check_portfolio)

# 2. trade_plan
def check_trade_plan():
    p = V75_DIR / "trade_plans" / "trade_plan_20260706.json"
    if not p.exists():
        return False, "文件不存在"
    data = json.loads(p.read_text(encoding="utf-8"))
    ok = (data.get("capital") == 5_000_000 and
          data.get("stock_etf_capital") == 3_000_000 and
          data.get("hedge_capital") == 2_000_000)
    phase = data.get("phase", {})
    detail = f"capital={data.get('capital')}, equity={data.get('stock_etf_capital')}, hedge={data.get('hedge_capital')}, phase={phase.get('capital_ratio')}"
    return ok, detail

check("trade_plan_20260706.json 资金", check_trade_plan)

# 3. build plan
def check_build_plan():
    p = BASE_DIR / "500万建仓计划_20260706.json"
    if not p.exists():
        return False, "文件不存在"
    data = json.loads(p.read_text(encoding="utf-8"))
    meta = data.get("metadata", {})
    ok = meta.get("stock_etf_capital") == 3_000_000 and meta.get("hedge_capital") == 2_000_000
    detail = f"stock={meta.get('stock_etf_capital')}, hedge={meta.get('hedge_capital')}"
    return ok, detail

check("500万建仓计划_20260706.json 资金", check_build_plan)

# 4. positions.json
def check_positions():
    p = BASE_DIR / "config" / "positions.json"
    if not p.exists():
        return False, "文件不存在"
    data = json.loads(p.read_text(encoding="utf-8"))
    positions = data.get("positions", data if isinstance(data, list) else [])
    return True, f"持仓数量={len(positions)}"

check("config/positions.json", check_positions)

# 5. macro module
def check_macro_module():
    p = V75_DIR / "src" / "macro" / "macro_policy_scoring.py"
    if not p.exists():
        return False, "文件不存在"
    # 尝试导入关键函数
    sys.path.insert(0, str(V75_DIR / "src" / "macro"))
    import macro_policy_scoring as m
    has_score = hasattr(m, "score_macro_policy")
    has_factor = hasattr(m, "macro_score_to_factor")
    ok = has_score and has_factor
    return ok, f"score_macro_policy={has_score}, macro_score_to_factor={has_factor}"

check("macro_policy_scoring.py", check_macro_module)

# 6. daily_workflow syntax
def check_workflow_syntax():
    p = V75_DIR / "daily_workflow.py"
    if not p.exists():
        return False, "文件不存在"
    py_compile.compile(str(p), doraise=True)
    return True, "语法检查通过"

check("daily_workflow.py 语法", check_workflow_syntax)

# 7. smart_order_router option support
def check_mock_broker_option():
    p = V75_DIR / "src" / "execution" / "smart_order_router.py"
    if not p.exists():
        return False, "文件不存在"
    text = p.read_text(encoding="utf-8")
    ok = "option_type" in text and "strike" in text
    return ok, "option_type/strike 已支持" if ok else "缺少期权字段"

check("MockBroker 期权支持", check_mock_broker_option)

# 8. 残留棉花常量
def check_cotton_leftover():
    p = V75_DIR / "daily_workflow.py"
    text = p.read_text(encoding="utf-8")
    leftovers = [tok for tok in ["COTTON_CODE", "COTTON_PUT_CODE", "CF2609", "棉花期货", "棉花期权"] if tok in text]
    ok = len(leftovers) == 0
    detail = "无残留" if ok else f"残留: {leftovers}"
    return ok, detail

check("daily_workflow.py 棉花常量清理", check_cotton_leftover)

# 输出结果
print("=" * 60)
print("v7.5 系统检查结果")
print("=" * 60)
all_ok = True
for name, ok, detail in checks:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"[{status}] {name}: {detail}")

print("=" * 60)
print(f"总体: {'PASS' if all_ok else 'FAIL'}")
print("=" * 60)

sys.exit(0 if all_ok else 1)
