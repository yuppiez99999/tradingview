# -*- coding: utf-8 -*-
"""
v7.5 本周自动交易计划执行器
============================

从周计划文件读取本周交易计划，在每个交易日自动执行:
    1. 加载当日交易计划 (trade_plan_YYYYMMDD.json)
    2. 通过同花顺期货通模拟盘执行股票/期货/期权订单
    3. 生成每日交易执行报告
    4. 更新周计划执行进度

用法:
    python weekly_trade_executor.py                    # 执行今日计划(全天)
    python weekly_trade_executor.py --session morning  # 上午批次
    python weekly_trade_executor.py --session afternoon # 下午批次
    python weekly_trade_executor.py --date 2026-07-21  # 指定日期
    python weekly_trade_executor.py --dry-run          # 干跑模式
    python weekly_trade_executor.py --week             # 查看本周计划概览
"""
from __future__ import annotations

import os
import sys
import json
import logging
import argparse
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"weekly_trade_{datetime.now():%Y%m%d}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("v75.weekly_trade")

ACCOUNT_SNAPSHOT_FILE = LOG_DIR / "sim_account_snapshot.json"

# ============================================================
# 导入 LLM 盘中决策引擎（午盘/夜盘前自动刷新决策）
# ============================================================
LLM_INTRADAY_READY = False
try:
    sys.path.insert(0, str(BASE_DIR))
    from llm_intraday_decision_engine import run_intraday_decision as _run_intraday_decision
    LLM_INTRADAY_READY = True
    logger.info("LLM 盘中决策引擎加载成功（午盘/夜盘前将自动刷新）")
except Exception as _e:
    logger.warning(f"LLM 盘中决策引擎加载失败，使用静态计划: {_e}")


class WeeklyTradeExecutor:
    """本周自动交易计划执行器"""

    def __init__(self, trade_date: Optional[str] = None, dry_run: bool = False, session: str = "all"):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.dry_run = dry_run
        self.session = session.lower()
        self.plan_dir = BASE_DIR / "trade_plans"
        self.report_dir = BASE_DIR.parent.parent / "每日报告归档"
        self.report_dir.mkdir(parents=True, exist_ok=True)

        self.weekly_plan: Dict[str, Any] = {}
        self.daily_plan: Dict[str, Any] = {}
        self.execution_results: Dict[str, Any] = {}

        self.stock_account = None
        self.futures_account = None
        self.options_account = None

        if self.session == "night":
            self.trade_date = self._get_next_trading_day(self.trade_date)

    def _get_next_trading_day(self, date_str: str) -> str:
        """获取下一个交易日（处理T+1规则：夜盘属于下一个交易日）"""
        today = datetime.strptime(date_str, "%Y-%m-%d").date()
        next_day = today + timedelta(days=1)

        HOLIDAYS_2026 = {
            date(2026, 1, 1),
            date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
            date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23),
            date(2026, 4, 6), date(2026, 4, 7),
            date(2026, 5, 4), date(2026, 5, 5),
            date(2026, 6, 19), date(2026, 6, 22),
            date(2026, 9, 25),
            date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
            date(2026, 10, 8),
        }

        while True:
            if next_day.weekday() >= 5 or next_day in HOLIDAYS_2026:
                next_day += timedelta(days=1)
            else:
                break

        return next_day.strftime("%Y-%m-%d")

    def _load_account_snapshot(self) -> Dict[str, Any]:
        """加载账户快照"""
        if ACCOUNT_SNAPSHOT_FILE.exists():
            try:
                with open(ACCOUNT_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载账户快照失败: {e}")
        return {}

    def _save_account_snapshot(self, snapshot: Dict[str, Any]):
        """保存账户快照"""
        try:
            with open(ACCOUNT_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            logger.info(f"账户快照已保存: {ACCOUNT_SNAPSHOT_FILE}")
        except Exception as e:
            logger.error(f"保存账户快照失败: {e}")

    def load_weekly_plan(self) -> bool:
        """加载本周交易计划"""
        today = datetime.strptime(self.trade_date, "%Y-%m-%d")
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=4)

        week_start_str = week_start.strftime("%Y%m%d")
        week_end_str = week_end.strftime("%Y%m%d")

        plan_file = self.plan_dir / f"weekly_plan_{week_start_str}_{week_end_str}.json"
        if plan_file.exists():
            try:
                with open(plan_file, "r", encoding="utf-8") as f:
                    self.weekly_plan = json.load(f)
                logger.info(f"已加载周计划: {plan_file.name}")
                return True
            except Exception as e:
                logger.error(f"加载周计划失败: {e}")
                return False

        logger.warning(f"未找到周计划文件: {plan_file.name}")
        return False

    def load_daily_plan(self) -> bool:
        """加载当日交易计划"""
        date_compact = self.trade_date.replace("-", "")
        candidates = [
            self.plan_dir / f"trade_plan_{date_compact}.json",
            self.plan_dir / f"trade_plan_{self.trade_date}.json",
        ]
        for path in candidates:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self.daily_plan = json.load(f)
                    logger.info(f"已加载当日计划: {path.name}")
                    return True
                except Exception as e:
                    logger.error(f"加载当日计划失败: {e}")
                    return False

        logger.warning(f"未找到当日计划文件")
        return False

    def _refresh_llm_decisions(self) -> None:
        """午盘/夜盘前刷新 LLM 盘中决策

        调用 llm_intraday_decision_engine.run_intraday_decision() 生成最新决策，
        然后重新加载交易计划文件以获取更新后的决策。
        - 常规场景：qwen2.5:7b (~22秒)
        - 复杂场景（止损/回撤/强加仓）：deepseek-r1:14b (~1-3分钟，深度思考）
        """
        logger.info(f"[LLM] 刷新盘中决策 (session={self.session})...")
        try:
            result = _run_intraday_decision(self.trade_date, mode="mock")
            deep_mode = result.get("deep_mode", False)
            decision_count = result.get("decision_count", 0)
            llm_model = result.get("llm_model", "unknown")
            if deep_mode:
                logger.info(f"[LLM] 触发深度思考: {result.get('deep_reason', '')}")
                logger.info(f"[LLM] 模型={llm_model}, 决策数={decision_count} (深度模式)")
            else:
                logger.info(f"[LLM] 模型={llm_model}, 决策数={decision_count} (快速模式)")

            # 重新加载计划文件以获取最新 LLM 决策
            if self.load_daily_plan():
                llm_decs = self.daily_plan.get("llm_intraday_decisions", {})
                if isinstance(llm_decs, dict):
                    stored_count = llm_decs.get("count", 0)
                    stored_mode = llm_decs.get("mode", "")
                    logger.info(f"[LLM] 计划已刷新: {stored_count} 条决策, 模式={stored_mode}")
        except Exception as e:
            logger.warning(f"[LLM] 刷新盘中决策失败，使用已有计划: {e}")

    def is_trading_day(self) -> bool:
        """判断是否为交易日"""
        today = datetime.strptime(self.trade_date, "%Y-%m-%d").date()
        if today.weekday() >= 5:
            logger.info(f"{self.trade_date} 是周末，跳过交易")
            return False

        HOLIDAYS_2026 = {
            date(2026, 1, 1),
            date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
            date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23),
            date(2026, 4, 6), date(2026, 4, 7),
            date(2026, 5, 4), date(2026, 5, 5),
            date(2026, 6, 19), date(2026, 6, 22),
            date(2026, 9, 25),
            date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
            date(2026, 10, 8),
        }
        if today in HOLIDAYS_2026:
            logger.info(f"{self.trade_date} 是节假日，跳过交易")
            return False

        return True

    def _get_session_orders(self) -> Dict[str, List[Dict]]:
        """根据 session 参数获取对应批次的订单"""
        exec_plan = self.daily_plan.get("execution_plan", {})
        morning_orders = exec_plan.get("morning_orders", [])
        afternoon_orders = exec_plan.get("afternoon_orders", [])
        options_orders = exec_plan.get("options_orders", [])

        if self.session == "morning":
            return {"stock": morning_orders, "options": options_orders}
        elif self.session == "afternoon":
            return {"stock": afternoon_orders, "options": []}
        elif self.session == "night":
            return {"stock": [], "options": []}
        else:
            return {"stock": morning_orders + afternoon_orders, "options": options_orders}

    def _extract_futures_hedge_orders(self) -> List[Dict]:
        """从 daily_plan 中提取期货对冲订单（去重）"""
        orders = {}

        llm_overrides = self.daily_plan.get("llm_overrides", {})
        futures_contracts = llm_overrides.get("futures_if_contracts", 0)

        if futures_contracts > 0:
            key = f"IF_SELL_OPEN"
            orders[key] = {
                "symbol": "IF2609",
                "qty": futures_contracts,
                "side": "SELL_OPEN",
                "price": 0,
                "instrument": "沪深300股指期货",
                "note": f"LLM建议: 增加期货对冲合约数量至{futures_contracts}手IF",
            }

        hedge_config = self.daily_plan.get("hedge_config", {})
        layers = hedge_config.get("layers", {})
        layer1 = layers.get("layer1_futures", {})
        if layer1.get("action") == "SHORT_FUTURES" and layer1.get("capital", 0) > 0:
            instrument = layer1.get("instrument", "")
            if "IF" in instrument:
                key = f"IF_SELL_OPEN"
                if key in orders:
                    orders[key]["note"] += f" | {instrument}, 资金{layer1.get('capital', 0):,}"
                else:
                    orders[key] = {
                        "symbol": "IF2609",
                        "qty": 5,
                        "side": "SELL_OPEN",
                        "price": 0,
                        "instrument": instrument,
                        "note": f"对冲配置: {instrument}, 资金{layer1.get('capital', 0):,}",
                    }

        return list(orders.values())

    def execute_stock_orders(self, orders: List[Dict]) -> Dict[str, Any]:
        """执行股票订单"""
        results = {"success": 0, "failed": 0, "total_amount": 0, "orders": []}

        if not orders:
            return results

        try:
            from sim_broker_integration import SimStockBroker, SimAccount
            from ths_sim_broker import THSQuoteProvider

            snapshot = self._load_account_snapshot()
            stock_snapshot = snapshot.get("stock", {})

            self.stock_account = SimAccount(
                account_id="SIM-STOCK-WEEKLY",
                total_capital=stock_snapshot.get("total_capital", 3_000_000),
                available_cash=stock_snapshot.get("available_cash", 3_000_000),
                positions=stock_snapshot.get("positions", {}),
            )
            ths_quote = THSQuoteProvider()
            stock_broker = SimStockBroker(account=self.stock_account, price_provider=None)

            for order in orders:
                code = order.get("code", "")
                name = order.get("name", "")
                shares = order.get("shares", 0)
                price = order.get("limit_price", order.get("est_price", 0))
                side = order.get("side", "BUY")

                if shares <= 0 or price <= 0:
                    logger.warning(f"跳过无效订单: {code} {shares}股 @ {price}")
                    results["failed"] += 1
                    continue

                if self.dry_run:
                    logger.info(f"[DRY-RUN] {side} {code}({name}) {shares}股 @ {price:.2f}")
                    results["success"] += 1
                    results["total_amount"] += shares * price
                    results["orders"].append({
                        "code": code, "name": name, "shares": shares,
                        "price": price, "side": side, "status": "DRY_RUN",
                        "amount": shares * price,
                    })
                else:
                    try:
                        result = stock_broker.place_order(
                            symbol=code, qty=shares, side=side, price=price
                        )
                        status = result.get("status", "UNKNOWN")
                        if status == "FILLED":
                            results["success"] += 1
                            results["total_amount"] += result.get("amount", 0)
                            logger.info(f"成交: {side} {code}({name}) {shares}股 @ {price:.2f}")
                        else:
                            results["failed"] += 1
                            logger.warning(f"未成交: {code} {side} {shares}股")

                        results["orders"].append({
                            "code": code, "name": name, "shares": shares,
                            "price": price, "side": side, "status": status,
                            "amount": result.get("amount", 0),
                        })
                    except Exception as e:
                        results["failed"] += 1
                        logger.error(f"下单失败 {code}: {e}")

            if not self.dry_run:
                self._save_account_snapshot({
                    "stock": {
                        "total_capital": self.stock_account.total_capital,
                        "available_cash": self.stock_account.available_cash,
                        "positions": self.stock_account.positions,
                    },
                    "futures": {},
                    "options": {},
                    "last_update": datetime.now().isoformat(),
                })

        except ImportError as e:
            logger.warning(f"模拟盘模块不可用，降级为干跑模式: {e}")
            for order in orders:
                code = order.get("code", "")
                name = order.get("name", "")
                shares = order.get("shares", 0)
                price = order.get("limit_price", order.get("est_price", 0))
                side = order.get("side", "BUY")
                logger.info(f"[SIM-MODE] {side} {code}({name}) {shares}股 @ {price:.2f}")
                results["success"] += 1
                results["total_amount"] += shares * price

        return results

    def execute_futures_orders(self, orders: List[Dict]) -> Dict[str, Any]:
        """执行期货订单"""
        results = {"success": 0, "failed": 0, "total_amount": 0, "orders": []}

        if not orders:
            return results

        try:
            from sim_broker_integration import SimAccount
            from ths_sim_broker import THSQuoteProvider, THSSimFuturesBroker
            from ths_real_broker import THSRealBroker

            snapshot = self._load_account_snapshot()
            futures_snapshot = snapshot.get("futures", {})

            self.futures_account = SimAccount(
                account_id="SIM-FUTURES-WEEKLY",
                total_capital=futures_snapshot.get("total_capital", 1_060_000),
                available_cash=futures_snapshot.get("available_cash", 1_060_000),
                positions=futures_snapshot.get("positions", {}),
            )
            ths_quote = THSQuoteProvider()

            futures_broker = THSRealBroker(account=self.futures_account, quote_provider=ths_quote, mode="sim")
            if not futures_broker.connect():
                logger.warning("无法连接同花顺期货通，回退到本地模拟盘")
                futures_broker = THSSimFuturesBroker(account=self.futures_account, quote_provider=ths_quote)

            for order in orders:
                symbol = order.get("symbol", "")
                qty = order.get("qty", 0)
                side = order.get("side", "BUY_OPEN")
                price = order.get("price", 0)
                note = order.get("note", "")

                if qty <= 0:
                    continue

                if price <= 0:
                    price = futures_broker._get_futures_price(symbol)

                if self.dry_run:
                    logger.info(f"[DRY-RUN] 期货 {side} {symbol} {qty}手 @ {price:.2f} | {note}")
                    results["success"] += 1
                else:
                    try:
                        result = futures_broker.place_order(symbol, qty, side, price)
                        status = result.get("status", "UNKNOWN")
                        if status in ["FILLED", "SUBMITTED"]:
                            results["success"] += 1
                            results["total_amount"] += result.get("amount", 0)
                            logger.info(f"期货下单成功: {side} {symbol} {qty}手 | {note}")
                        else:
                            results["failed"] += 1
                    except Exception as e:
                        results["failed"] += 1
                        logger.error(f"期货下单失败 {symbol}: {e}")

            if not self.dry_run:
                self._save_account_snapshot({
                    "stock": {},
                    "futures": {
                        "total_capital": self.futures_account.total_capital,
                        "available_cash": self.futures_account.available_cash,
                        "positions": self.futures_account.positions,
                    },
                    "options": {},
                    "last_update": datetime.now().isoformat(),
                })

        except ImportError as e:
            logger.warning(f"期货模拟盘模块不可用: {e}")

        return results

    def execute_options_orders(self, orders: List[Dict]) -> Dict[str, Any]:
        """执行期权订单"""
        results = {"success": 0, "failed": 0, "total_premium": 0, "orders": []}

        if not orders:
            return results

        try:
            from sim_broker_integration import SimAccount
            from ths_sim_broker import THSQuoteProvider, SimOptionsBroker
            from ths_real_broker import THSRealBroker

            snapshot = self._load_account_snapshot()
            options_snapshot = snapshot.get("options", {})

            self.options_account = SimAccount(
                account_id="SIM-OPTIONS-WEEKLY",
                total_capital=options_snapshot.get("total_capital", 1_060_000),
                available_cash=options_snapshot.get("available_cash", 1_060_000),
                positions=options_snapshot.get("positions", {}),
            )
            ths_quote = THSQuoteProvider()

            options_broker = THSRealBroker(account=self.options_account, quote_provider=ths_quote, mode="sim")
            if not options_broker.connect():
                logger.warning("无法连接同花顺期货通，回退到本地模拟盘")
                options_broker = SimOptionsBroker(account=self.options_account, quote_provider=ths_quote)

            for order in orders:
                code = order.get("code", "")
                direction = order.get("direction", "SELL_CALL")
                contracts = order.get("contracts", 0)
                premium = order.get("est_premium_total", 0)

                if contracts <= 0:
                    continue

                if self.dry_run:
                    logger.info(f"[DRY-RUN] 期权 {direction} {code} {contracts}张, 预估权利金: {premium:.2f}")
                    results["success"] += 1
                    results["total_premium"] += premium
                else:
                    try:
                        price = order.get("price", 0)
                        if price <= 0:
                            price = 0.01
                        result = options_broker.place_order(
                            symbol=code, qty=contracts, side=direction, price=price
                        )
                        status = result.get("status", "UNKNOWN")
                        if status in ["FILLED", "SUBMITTED"]:
                            results["success"] += 1
                            results["total_premium"] += result.get("premium", 0)
                            logger.info(f"期权下单成功: {direction} {code} {contracts}张")
                        else:
                            results["failed"] += 1
                    except Exception as e:
                        results["failed"] += 1
                        logger.error(f"期权下单失败 {code}: {e}")

            if not self.dry_run:
                self._save_account_snapshot({
                    "stock": {},
                    "futures": {},
                    "options": {
                        "total_capital": self.options_account.total_capital,
                        "available_cash": self.options_account.available_cash,
                        "positions": self.options_account.positions,
                    },
                    "last_update": datetime.now().isoformat(),
                })

        except ImportError as e:
            logger.warning(f"期权模拟盘模块不可用: {e}")

        return results

    def generate_report(self) -> str:
        """生成当日交易执行报告"""
        session_desc = {"morning": "上午批次", "afternoon": "下午批次", "all": "全天"}.get(self.session, "全天")

        report = f"""# 本周自动交易计划执行报告 — {self.trade_date} ({session_desc})

## 一、执行概览

| 项目 | 数值 |
|------|------|
| 交易日期 | {self.trade_date} |
| 执行模式 | {'干跑模式' if self.dry_run else '实盘模式'} |
| 执行批次 | {session_desc} |
| 周计划 | {self.weekly_plan.get('phase', 'N/A')} |
| 当前阶段 | {self.daily_plan.get('phase', {}).get('name', 'N/A')} |

## 二、股票交易执行

"""

        stock_results = self.execution_results.get("stock", {})
        report += f"""| 指标 | 数值 |
|------|------|
| 成功订单 | {stock_results.get('success', 0)} |
| 失败订单 | {stock_results.get('failed', 0)} |
| 成交金额 | ¥{stock_results.get('total_amount', 0):,.2f} |

### 订单明细

| 优先级 | 代码 | 名称 | 方向 | 数量 | 价格 | 金额 | 状态 |
|--------|------|------|------|------|------|------|------|
"""
        for idx, order in enumerate(stock_results.get("orders", [])):
            report += f"| {idx+1} | {order.get('code', '')} | {order.get('name', '')} | {order.get('side', '')} | {order.get('shares', 0)} | {order.get('price', 0):.2f} | ¥{order.get('amount', 0):,.2f} | {order.get('status', '')} |\n"

        report += "\n## 三、期货对冲执行\n\n"
        futures_results = self.execution_results.get("futures", {})
        report += f"""| 指标 | 数值 |
|------|------|
| 成功订单 | {futures_results.get('success', 0)} |
| 失败订单 | {futures_results.get('failed', 0)} |
| 成交金额 | ¥{futures_results.get('total_amount', 0):,.2f} |

"""
        futures_orders = self.execution_results.get("futures_orders", [])
        if futures_orders:
            report += """### 期货订单明细

| 合约 | 方向 | 手数 | 价格 | 备注 |
|------|------|------|------|------|
"""
            for order in futures_orders:
                report += f"| {order.get('symbol', '')} | {order.get('side', '')} | {order.get('qty', 0)} | {order.get('price', 0):.2f} | {order.get('note', '')} |\n"

        report += "\n## 四、期权执行\n\n"
        options_results = self.execution_results.get("options", {})
        report += f"""| 指标 | 数值 |
|------|------|
| 成功订单 | {options_results.get('success', 0)} |
| 失败订单 | {options_results.get('failed', 0)} |
| 权利金收入 | ¥{options_results.get('total_premium', 0):,.2f} |

## 五、风控状态

"""
        risk_controls = self.daily_plan.get("risk_controls", {})
        report += f"""| 风控指标 | 阈值 |
|----------|------|
| 黄色预警 | {risk_controls.get('yellow_warning', 0):.0%} |
| 橙色预警 | {risk_controls.get('orange_warning', 0):.0%} |
| 红色止损 | {risk_controls.get('red_stop', 0):.0%} |

## 六、周计划进度

"""
        progress_pct = self.weekly_plan.get("progress_pct", 0)
        report += f"""| 指标 | 数值 |
|------|------|
| 本周阶段 | {self.weekly_plan.get('phase', 'N/A')} |
| 阶段进度 | {progress_pct:.1f}% |
| 已用天数 | {self.weekly_plan.get('days_elapsed', 0)}/{self.weekly_plan.get('total_days', 0)} |
| 剩余天数 | {self.weekly_plan.get('days_remaining', 0)} |

## 七、关键提示

"""
        for note in self.weekly_plan.get("key_notes", []):
            report += f"- {note}\n"

        report += f"\n---\n*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
        return report

    def save_report(self, report: str) -> str:
        """保存报告到归档目录"""
        date_folder = self.report_dir / self.trade_date
        date_folder.mkdir(exist_ok=True)
        session_suffix = f"_{self.session}" if self.session != "all" else ""
        report_file = date_folder / f"weekly_trade_execution_{self.trade_date}{session_suffix}.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"报告已保存: {report_file}")
        return str(report_file)

    def run(self) -> bool:
        """执行本周交易计划"""
        logger.info("=" * 60)
        logger.info(f"本周自动交易计划执行器 @ {self.trade_date} (session={self.session})")
        logger.info("=" * 60)

        if not self.is_trading_day():
            return True

        if not self.load_weekly_plan():
            logger.warning("未加载周计划，继续执行当日计划")

        if not self.load_daily_plan():
            logger.error("未找到当日计划，退出")
            return False

        # 午盘(14:00)和夜盘(21:00)前自动刷新 LLM 盘中决策
        # 早盘(9:30)使用 7:05 daily_workflow 预计算的决策（足够新）
        if self.session in ("afternoon", "night") and LLM_INTRADAY_READY:
            self._refresh_llm_decisions()

        session_orders = self._get_session_orders()
        stock_orders = session_orders.get("stock", [])
        options_orders = session_orders.get("options", [])
        futures_orders = self._extract_futures_hedge_orders()

        logger.info(f"当日计划: 股票订单 {len(stock_orders)} 笔, 期权 {len(options_orders)} 笔, 期货对冲 {len(futures_orders)} 笔")

        self.execution_results["stock"] = self.execute_stock_orders(stock_orders)
        self.execution_results["futures"] = self.execute_futures_orders(futures_orders)
        self.execution_results["futures_orders"] = futures_orders
        self.execution_results["options"] = self.execute_options_orders(options_orders)

        report = self.generate_report()
        self.save_report(report)

        stock_results = self.execution_results["stock"]
        futures_results = self.execution_results["futures"]
        logger.info(f"执行完成: 股票 {stock_results['success']}成功/{stock_results['failed']}失败, 金额 ¥{stock_results['total_amount']:,.2f}")
        logger.info(f"执行完成: 期货 {futures_results['success']}成功/{futures_results['failed']}失败")
        logger.info("=" * 60)

        return True

    def show_weekly_overview(self):
        """显示本周计划概览"""
        if not self.load_weekly_plan():
            print("无法加载本周计划")
            return

        print("\n" + "=" * 60)
        print("本周自动交易计划概览")
        print("=" * 60)
        print(f"周区间: {self.weekly_plan.get('week_start')} ~ {self.weekly_plan.get('week_end')}")
        print(f"阶段: {self.weekly_plan.get('phase')}")
        print(f"进度: {self.weekly_plan.get('progress_pct', 0):.1f}% ({self.weekly_plan.get('days_elapsed', 0)}/{self.weekly_plan.get('total_days', 0)}天)")
        print(f"剩余天数: {self.weekly_plan.get('days_remaining', 0)}天")
        print(f"每日建仓: {self.weekly_plan.get('daily_capital', 0):,}元")
        print(f"本周目标: {self.weekly_plan.get('weekly_capital_target', 0):,}元")

        print("\n每日计划:")
        for day in self.weekly_plan.get("daily_plans", []):
            print(f"  {day.get('date')} ({day.get('weekday')}): {day.get('file')}")

        print("\n关键提示:")
        for note in self.weekly_plan.get("key_notes", []):
            print(f"  • {note}")

        print("\n本周目标:")
        targets = self.weekly_plan.get("targets_this_week", {})
        for k, v in targets.items():
            print(f"  • {k}: {v}")


def main():
    parser = argparse.ArgumentParser(description="本周自动交易计划执行器")
    parser.add_argument("--date", type=str, help="指定交易日期 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式")
    parser.add_argument("--week", action="store_true", help="查看本周计划概览")
    parser.add_argument("--session", type=str, default="all", 
                        choices=["morning", "afternoon", "night", "all"],
                        help="执行批次: morning/afternoon/night/all (默认all, 夜盘仅期货)")
    args = parser.parse_args()

    executor = WeeklyTradeExecutor(trade_date=args.date, dry_run=args.dry_run, session=args.session)

    if args.week:
        executor.show_weekly_overview()
        return

    executor.run()


if __name__ == "__main__":
    main()
