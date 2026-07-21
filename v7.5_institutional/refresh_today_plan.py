# -*- coding: utf-8 -*-
"""
重新生成 2026-07-21 今日交易计划，并恢复 Put 期权订单
"""
import json
import subprocess
import os
from pathlib import Path
from datetime import datetime

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional")
PLAN_DIR = BASE / "trade_plans"
PLAN_FILE = PLAN_DIR / "trade_plan_20260721.json"

# 之前添加的 Put 期权订单（重新生成后需要恢复）
PUT_OPTIONS = [
    {"code": "510050", "name": "510050_Put", "direction": "BUY_PUT", "underlying": "510050",
     "contracts": 20, "est_premium_total": 300000, "session": "morning",
     "note": "尾部风险保护: 上证50认沽 OTM_5%"},
    {"code": "588080", "name": "588080_Put", "direction": "BUY_PUT", "underlying": "588080",
     "contracts": 10, "est_premium_total": 120000, "session": "morning",
     "note": "尾部风险保护: 科创50认沽 OTM_5%"},
    {"code": "159915", "name": "159915_Put", "direction": "BUY_PUT", "underlying": "159915",
     "contracts": 10, "est_premium_total": 100000, "session": "morning",
     "note": "尾部风险保护: 创业板认沽 OTM_5%"},
    {"code": "510300", "name": "510300_Put", "direction": "BUY_PUT", "underlying": "510300",
     "contracts": 5, "est_premium_total": 40000, "session": "morning",
     "note": "尾部风险保护: 沪深300认沽 OTM_5%"},
]

def main():
    # 备份原文件
    backup_file = PLAN_DIR / f"trade_plan_20260721.json.bak_{datetime.now():%H%M%S}"
    if PLAN_FILE.exists():
        backup_file.write_text(PLAN_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已备份原文件: {backup_file.name}")
    
    # 调用生成脚本重新生成
    python = r"C:\Program Files\Python38\python.exe"
    generator = BASE / "generate_daily_trade_plan.py"
    
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    result = subprocess.run(
        [python, str(generator), "2026-07-21"],
        capture_output=True,
        text=False,
        env=env,
    )
    
    if result.returncode != 0:
        print(f"生成失败:")
        try:
            print(result.stderr.decode("utf-8", errors="replace"))
        except Exception:
            print(result.stderr)
        return
    
    print("已重新生成今日计划")
    
    # 恢复 Put 期权订单
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        plan = json.load(f)
    
    # 添加 Put 期权
    existing_options = plan.get("execution_plan", {}).get("options_orders", [])
    existing_options.extend(PUT_OPTIONS)
    plan["execution_plan"]["options_orders"] = existing_options
    plan["execution_plan"]["options_orders_count"] = len(existing_options)
    plan["execution_plan"]["options_total_premium"] = sum(
        o.get("est_premium_total", 0) for o in existing_options
    )
    
    # 更新期货目标为 5 手
    hedge_positions = plan.get("hedge_positions", {})
    if "IF_futures" in hedge_positions:
        hedge_positions["IF_futures"]["target_contracts"] = 5
        hedge_positions["IF_futures"]["reason"] = "沪深300对冲系统性风险; 5手覆盖约80%组合Beta; LLM建议增加至5手"
    
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    
    print(f"已恢复 Put 期权: {len(PUT_OPTIONS)} 笔")
    print(f"已更新 IF 期货目标: 5 手")
    
    # 验证新 ETF 是否在计划中
    new_etfs = ["512100", "510500", "588200", "159516"]
    plan_codes = set()
    
    for s in plan.get("execution_plan", {}).get("morning_orders", []):
        plan_codes.add(str(s.get("code", "")))
    for s in plan.get("execution_plan", {}).get("afternoon_orders", []):
        plan_codes.add(str(s.get("code", "")))
    
    print("\n新 ETF 检查:")
    for code in new_etfs:
        if code in plan_codes:
            print(f"  [OK] {code} - 已在计划中")
        else:
            print(f"  [NO] {code} - 未在计划中")
    
    # 统计
    morning_count = len(plan.get("execution_plan", {}).get("morning_orders", []))
    afternoon_count = len(plan.get("execution_plan", {}).get("afternoon_orders", []))
    options_count = plan.get("execution_plan", {}).get("options_orders_count", 0)
    total_amount = plan.get("execution_plan", {}).get("total_amount", 0)
    
    print(f"\n今日计划统计:")
    print(f"  上午订单: {morning_count} 笔")
    print(f"  下午订单: {afternoon_count} 笔")
    print(f"  期权订单: {options_count} 笔")
    print(f"  现货总额: ¥{total_amount:,.0f}")
    print(f"  当日资金: ¥{plan.get('day_capital', 0):,.0f}")

if __name__ == "__main__":
    main()
