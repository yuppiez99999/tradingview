# -*- coding: utf-8 -*-
"""临时脚本:对 generate_daily_trade_plan.py 应用 4 处编辑"""
import sys

p = r'e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\generate_daily_trade_plan.py'
with open(p, 'r', encoding='utf-8') as f:
    content = f.read()

# Edit 1: 第 333 行注释
old1 = '# 交易日数: 30 (7/13 周一 ~ 8/21 周五, 跳过周末)\n# 每日建仓: 150,000 元 (现货)\n# 总建仓金额: 3,000,000 元 (60%)'
new1 = '# 交易日数: 30 (7/13 周一 ~ 8/21 周五, 跳过周末)\n# 每日建仓: 200,000 元 (现货)\n# 总建仓金额: 3,000,000 元 (60%)'
assert old1 in content, 'Edit 1 old string not found'
content = content.replace(old1, new1, 1)
print('Edit 1 applied: 注释 150,000 -> 200,000')

# Edit 2: 第 428 行 docstring
old2 = '        - 每日建仓资金 = daily_capital (固定 15 万现货)'
new2 = '        - 每日建仓资金 = daily_capital (固定 20 万现货)'
assert old2 in content, 'Edit 2 old string not found'
content = content.replace(old2, new2, 1)
print('Edit 2 applied: docstring 15 万 -> 20 万')

# Edit 3: 第 649 行 day_capital 计算 bug
old3 = '"daily_capital": phase.get("daily_capital", round(phase["phase_capital"] / phase["duration_days"], 2)),\n            "day_capital": round(phase["phase_capital"] / phase["duration_days"], 2),'
new3 = '"daily_capital": phase.get("daily_capital", round(phase["phase_capital"] / phase["duration_days"], 2)),\n            "day_capital": phase.get("daily_capital", round(phase["phase_capital"] / phase["duration_days"], 2)),'
assert old3 in content, 'Edit 3 old string not found'
content = content.replace(old3, new3, 1)
print('Edit 3 applied: day_capital 改用 daily_capital')

# Edit 4: 插入 ProtectivePutEngine 调用块(在 Gamma 引擎之前)
old4 = '    # === 3. Gamma引擎 — 尾部危机监控状态 ==='
new4 = '''    # === 2.5 ProtectivePut 引擎 — 尾部保护 Put 部署 ===
    try:
        from utils.protective_put_engine import ProtectivePutEngine
        ppe = ProtectivePutEngine()
        put_result = ppe.generate_put_orders()
        put_orders = put_result.get("orders", []) if isinstance(put_result, dict) else []
        if put_orders:
            # 补齐 direction 字段, 与 Theta options_orders 格式对齐
            for po in put_orders:
                po.setdefault("direction", "BUY_PUT")
                po.setdefault("session", "morning")
            overlays.setdefault("options_orders", []).extend(put_orders)
            overlays["protective_put"] = {
                "available": True,
                "deployed": True,
                "should_execute": put_result.get("should_execute", True),
                "orders_count": len(put_orders),
                "total_budget": put_result.get("total_premium_est", 0),
                "annual_budget_remaining": put_result.get("annual_budget_remaining", 0),
                "targets": [po.get("underlying") for po in put_orders],
                "reason": put_result.get("reason", ""),
            }
        else:
            overlays["protective_put"] = {
                "available": True,
                "deployed": False,
                "should_execute": put_result.get("should_execute", False) if isinstance(put_result, dict) else False,
                "reason": put_result.get("reason", "无需部署") if isinstance(put_result, dict) else "无订单",
            }
    except Exception as e:
        overlays["protective_put"] = {"available": False, "error": str(e), "deployed": False}

    # === 3. Gamma引擎 — 尾部危机监控状态 ==='''
assert old4 in content, 'Edit 4 old string not found'
content = content.replace(old4, new4, 1)
print('Edit 4 applied: ProtectivePutEngine 调用块已插入')

with open(p, 'w', encoding='utf-8') as f:
    f.write(content)
print('\n所有 4 处编辑已写入文件')
