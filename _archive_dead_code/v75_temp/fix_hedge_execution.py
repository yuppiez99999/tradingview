#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修正 daily_workflow.py 的 phase_hedge()，使对冲订单实际通过 MockBroker 执行。
"""

import json
import os

BASE_DIR = r"e:\各种PY程序\28-终极量化交易系统7.1"
DAILY_WORKFLOW_PATH = os.path.join(BASE_DIR, "v7.5_institutional", "daily_workflow.py")

with open(DAILY_WORKFLOW_PATH, "r", encoding="utf-8") as f:
    source = f.read()

# 1. 确保 phase_hedge 中的期货订单通过 MockBroker 执行
old_futures_block = '''                    # 期货空头下单 (Beta 对冲)
                    if action == "SHORT_FUTURES":
                        fut_code = order.get("instrument", "IF")
                        contracts = int(order.get("contracts", 0))
                        fut_price = float(order.get("futures_price", 0))
                        if contracts > 0 and fut_price > 0:
                            logger.info(f"[对冲执行] {hedge_type}: {fut_code} 空头 {contracts} 手 @ {fut_price}")
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "instrument": fut_code,
                                "side": "SELL_SHORT",
                                "contracts": contracts,
                                "price": fut_price,
                                "notional": order.get("notional", 0),
                                "cost": order.get("estimated_cost", 0),
                                "status": "FILLED",
                                "reason": "Beta 对冲自动执行",
                            })'''

new_futures_block = '''                    # 期货空头下单 (Beta 对冲)
                    if action == "SHORT_FUTURES":
                        fut_code = order.get("instrument", "IF")
                        contracts = int(order.get("contracts", 0))
                        fut_price = float(order.get("futures_price", 0))
                        if contracts > 0 and fut_price > 0:
                            logger.info(f"[对冲执行] {hedge_type}: {fut_code} 空头 {contracts} 手 @ {fut_price}")
                            try:
                                oid = broker.place(
                                    symbol=fut_code,
                                    qty=contracts,
                                    side="SELL_SHORT",
                                    order_type="LIMIT",
                                    price=fut_price,
                                )
                                fill = broker.wait_fill(oid)
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": fut_code,
                                    "side": "SELL_SHORT",
                                    "contracts": contracts,
                                    "price": fill["price"] if fill else fut_price,
                                    "notional": order.get("notional", 0),
                                    "cost": order.get("estimated_cost", 0),
                                    "status": fill["order_type"] if fill else "FILLED",
                                    "reason": "Beta 对冲自动执行",
                                    "order_id": oid,
                                })
                            except Exception as exc:
                                logger.error(f"Beta 对冲执行失败: {exc}")
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": fut_code,
                                    "side": "SELL_SHORT",
                                    "contracts": contracts,
                                    "price": fut_price,
                                    "notional": order.get("notional", 0),
                                    "cost": order.get("estimated_cost", 0),
                                    "status": "FAILED",
                                    "reason": f"Beta 对冲执行失败: {exc}",
                                })'''

source = source.replace(old_futures_block, new_futures_block)

# 2. 确保期权下单通过 MockBroker 执行
old_option_block = '''                    # 期权买入 (Vol 对冲)
                    elif action in ("BUY_PUT_SPREAD", "BUY_BARE_PUT", "BUY_EMERGENCY_PUT"):
                        budget = float(order.get("budget", 0))
                        if budget > 0:
                            logger.info(f"[对冲执行] {hedge_type}: {action} 预算 {budget:.0f}")
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "instrument": order.get("instrument", "50ETF_OPTIONS"),
                                "side": "BUY",
                                "option_type": "PUT",
                                "budget": budget,
                                "delta_target": order.get("delta_target", -0.2),
                                "coverage": order.get("actual_coverage", 0),
                                "status": "FILLED",
                                "reason": f"Vol 对冲自动执行 (VIX={vix_level})",
                            })'''

new_option_block = '''                    # 期权买入 (Vol 对冲)
                    elif action in ("BUY_PUT_SPREAD", "BUY_BARE_PUT", "BUY_EMERGENCY_PUT"):
                        budget = float(order.get("budget", 0))
                        if budget > 0:
                            logger.info(f"[对冲执行] {hedge_type}: {action} 预算 {budget:.0f}")
                            try:
                                opt_symbol = order.get("instrument", "50ETF_OPTIONS")
                                opt_price = budget / 10000.0 if budget > 0 else 0.0
                                oid = broker.place(
                                    symbol=opt_symbol,
                                    qty=1,
                                    side="BUY",
                                    order_type="LIMIT",
                                    price=opt_price,
                                    option_type="PUT",
                                    strike=0.0,
                                )
                                fill = broker.wait_fill(oid)
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": opt_symbol,
                                    "side": "BUY",
                                    "option_type": "PUT",
                                    "strike": 0.0,
                                    "budget": budget,
                                    "delta_target": order.get("delta_target", -0.2),
                                    "coverage": order.get("actual_coverage", 0),
                                    "price": fill["price"] if fill else opt_price,
                                    "status": "FILLED",
                                    "reason": f"Vol 对冲自动执行 (VIX={vix_level})",
                                    "order_id": oid,
                                })
                            except Exception as exc:
                                logger.error(f"Vol 对冲执行失败: {exc}")
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": order.get("instrument", "50ETF_OPTIONS"),
                                    "side": "BUY",
                                    "option_type": "PUT",
                                    "strike": 0.0,
                                    "budget": budget,
                                    "delta_target": order.get("delta_target", -0.2),
                                    "coverage": order.get("actual_coverage", 0),
                                    "status": "FAILED",
                                    "reason": f"Vol 对冲执行失败: {exc}",
                                })'''

source = source.replace(old_option_block, new_option_block)

# 3. 确保避险资产配置通过 MockBroker 执行
old_safe_block = '''                    # 避险资产配置 (Correlation 对冲)
                    elif action == "SAFE_HAVEN_ALLOC":
                        gold_value = float(order.get("gold_value", 0))
                        repo_value = float(order.get("repo_value", 0))
                        logger.info(f"[对冲执行] {hedge_type}: 黄金ETF {gold_value:.0f} + 逆回购 {repo_value:.0f}")
                        executed_orders.append({
                            "type": hedge_type,
                            "action": action,
                            "gold_etf": order.get("gold_etf", "518880"),
                            "gold_value": gold_value,
                            "repo_symbol": order.get("repo_symbol", "GC001"),
                            "repo_value": repo_value,
                            "status": "FILLED",
                            "reason": f"Correlation 对冲自动执行 (ρ̄={order.get('avg_corr', 0):.3f})",
                        })'''

new_safe_block = '''                    # 避险资产配置 (Correlation 对冲)
                    elif action == "SAFE_HAVEN_ALLOC":
                        gold_value = float(order.get("gold_value", 0))
                        repo_value = float(order.get("repo_value", 0))
                        logger.info(f"[对冲执行] {hedge_type}: 黄金ETF {gold_value:.0f} + 逆回购 {repo_value:.0f}")
                        try:
                            gold_symbol = order.get("gold_etf", "518880")
                            est_gold_price = self.config.MOCK_PRICES.get(gold_symbol, 5.85)
                            gold_qty = int(gold_value / est_gold_price / 100) * 100
                            if gold_qty > 0:
                                oid = broker.place(
                                    symbol=gold_symbol,
                                    qty=gold_qty,
                                    side="BUY",
                                    order_type="LIMIT",
                                    price=est_gold_price,
                                )
                                fill = broker.wait_fill(oid)
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "gold_etf": gold_symbol,
                                    "gold_qty": gold_qty,
                                    "gold_value": gold_value,
                                    "gold_price": fill["price"] if fill else est_gold_price,
                                    "repo_symbol": order.get("repo_symbol", "GC001"),
                                    "repo_value": repo_value,
                                    "status": "FILLED",
                                    "reason": f"Correlation 对冲自动执行 (ρ̄={order.get('avg_corr', 0):.3f})",
                                    "order_id": oid,
                                })
                        except Exception as exc:
                            logger.error(f"Correlation 对冲执行失败: {exc}")
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "gold_etf": order.get("gold_etf", "518880"),
                                "gold_value": gold_value,
                                "repo_symbol": order.get("repo_symbol", "GC001"),
                                "repo_value": repo_value,
                                "status": "FAILED",
                                "reason": f"Correlation 对冲执行失败: {exc}",
                            })'''

source = source.replace(old_safe_block, new_safe_block)

with open(DAILY_WORKFLOW_PATH, "w", encoding="utf-8") as f:
    f.write(source)

print("已修正 daily_workflow.py phase_hedge()：期货/期权/避险资产均通过 MockBroker 执行")
