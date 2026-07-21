# -*- coding: utf-8 -*-
"""
修改今日期货计划：5手→2手，并重新执行
"""
import json
from pathlib import Path

PLAN_FILE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans\trade_plan_20260721.json")

def patch_futures():
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        plan = json.load(f)
    
    # 添加 llm_overrides，设置期货为2手
    plan["llm_overrides"] = {
        "futures_if_contracts": 2,
        "reason": "账户权益10.9万不足5手保证金20.6万，临时减仓至2手(需8.2万)；后续资金到位后补至5手"
    }
    
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    
    print(f"已修改期货计划: 5手 -> 2手")
    print(f"原因: {plan['llm_overrides']['reason']}")

if __name__ == "__main__":
    patch_futures()
