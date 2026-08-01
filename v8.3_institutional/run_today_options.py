from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v75.today_options")

PLAN_PATH = Path(r"E:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans\trade_plan_20260720.json")
REPORT_DIR = Path(r"E:\各种PY程序\每日报告归档\2026-07-20")


def load_plan() -> dict:
    with PLAN_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_options_broker():
    from sim_broker_integration import SimAccount
    from ths_real_broker import THSRealBroker
    from ths_sim_broker import SimOptionsBroker, THSQuoteProvider

    account = SimAccount(
        account_id="SIM-OPTIONS-TODAY",
        total_capital=1_060_000,
        available_cash=1_060_000,
        positions={},
    )
    quote = THSQuoteProvider()
    broker = THSRealBroker(account=account, quote_provider=quote, mode="sim")
    if broker.connect():
        logger.info("已连接同花顺期货通")
        return broker
    logger.warning("连接失败，回退到本地模拟盘")
    return SimOptionsBroker(account=account, quote_provider=quote)


def main():
    plan = load_plan()
    orders = plan.get("execution_plan", {}).get("options_orders", [])
    if not orders:
        logger.info("2026-07-20 计划中没有期权订单")
        return

    broker = build_options_broker()
    results = {"success": 0, "failed": 0, "total_premium": 0.0, "orders": []}

    for order in orders:
        code = order.get("code", "")
        direction = order.get("direction", "SELL_CALL")
        contracts = order.get("contracts", 0)
        price = order.get("est_premium_per_unit", 0.01)
        if price <= 0:
            price = 0.01

        if contracts <= 0:
            continue

        logger.info("下单: %s %s %s张 @ %s", direction, code, contracts, price)
        try:
            result = broker.place_order(symbol=code, qty=contracts, side=direction, price=price)
            status = result.get("status", "UNKNOWN")
            logger.info("结果: %s %s", status, result)
            if status in ["FILLED", "SUBMITTED"]:
                results["success"] += 1
                results["total_premium"] += result.get("premium", 0)
            else:
                results["failed"] += 1
        except Exception as e:
            results["failed"] += 1
            logger.exception("期权下单失败 %s: %s", code, e)
        time.sleep(0.5)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "options_build_20260720.md"
    report_path.write_text(
        f"# 2026-07-20 期权建仓报告\n\n"
        f"生成时间: {datetime.now().isoformat()}\n\n"
        f"## 执行结果\n\n"
        f"| 指标 | 数值 |\n|------|------|\n"
        f"| 成功订单 | {results['success']} |\n"
        f"| 失败订单 | {results['failed']} |\n"
        f"| 权利金收入 | ¥{results['total_premium']:,.2f} |\n\n"
        f"## 订单明细\n\n"
        + "\n".join(
            [f"- {o.get('code')} {o.get('direction')} {o.get('contracts')}张"
             for o in orders]
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info("期权建仓报告已保存: %s", report_path)


if __name__ == "__main__":
    main()
