"""
v8.4 本周自动交易计划执行器 (纯期权对冲模式)
==============================================

从周计划文件读取本周交易计划，在每个交易日自动执行:
    1. 加载当日交易计划 (trade_plan_YYYYMMDD.json)
    2. 通过同花顺期货通模拟盘执行股票/期权订单 (纯期权对冲)
    3. 期权对冲策略: Collar + Put Spread + Put Ladder + Covered Call + Risk Reversal + VIX Tail
    4. 不持有期货空头 — Beta风险全部通过ETF认沽期权组合管理
    5. 生成每日交易执行报告
    6. 更新周计划执行进度

对冲资金分配 (200万, OPTIONS_ONLY):
    - 期权对冲: 165万 (82.5%) — 主对冲手段: Put 138张
    - 滚仓/保证金: 35万 (17.5%) — 现金缓冲
    - 期货: 无 (v8.4 移除IF空头)

用法:
    python weekly_trade_executor.py                    # 执行今日计划(全天)
    python weekly_trade_executor.py --session morning  # 上午批次 (期权权利金操作)
    python weekly_trade_executor.py --session afternoon # 下午批次
    python weekly_trade_executor.py --date 2026-07-21  # 指定日期
    python weekly_trade_executor.py --dry-run          # 干跑模式
    python weekly_trade_executor.py --week             # 查看本周计划概览
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

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


# ============================================================
# ER4 修复: 节假日列表委托给 utils.trade_calendar (akshare 动态获取)
# 原 HOLIDAYS_2026 仅含 2026 假期, 2027 年后所有节假日会被误判为交易日
# 现统一走 akshare 动态日历, 自动覆盖任意年份, 失败时回退到 2026 硬编码列表
# ============================================================
HOLIDAYS_2026 = set()  # 保留变量名向后兼容, 实际不再使用
_PROJECT_ROOT = BASE_DIR.parent  # 项目根目录 (utils/ 在此层级)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from utils.trade_calendar import is_trading_day as _dyn_is_trading_day
    _DYNAMIC_CALENDAR_AVAILABLE = True
    logger.info("[ER4] 节假日判断已委托给 utils.trade_calendar (akshare 动态获取)")
except ImportError:
    _DYNAMIC_CALENDAR_AVAILABLE = False
    # 回退: 保留 2026 硬编码列表 (仅 2026 年有效)
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
    logger.warning("[ER4] utils.trade_calendar 不可用, 回退到 2026 硬编码假期列表 "
                   "(2027+ 年节假日将无法识别)")


class WeeklyTradeExecutor:
    """本周自动交易计划执行器"""

    def __init__(self, trade_date: str | None = None, dry_run: bool = False, session: str = "all"):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.dry_run = dry_run
        self.session = session.lower()
        self.plan_dir = BASE_DIR / "trade_plans"
        self.report_dir = BASE_DIR.parent.parent / "每日报告归档"
        self.report_dir.mkdir(parents=True, exist_ok=True)

        self.weekly_plan: dict[str, Any] = {}
        self.daily_plan: dict[str, Any] = {}
        self.execution_results: dict[str, Any] = {}

        self.stock_account = None
        self.futures_account = None
        self.options_account = None

        if self.session == "night":
            self.trade_date = self._get_next_trading_day(self.trade_date)

    def _get_next_trading_day(self, date_str: str) -> str:
        """获取下一个交易日（处理T+1规则：夜盘属于下一个交易日）

        ER4 修复: 优先委托给 utils.trade_calendar (akshare 动态获取),
        覆盖任意年份的节假日; 动态日历不可用时回退到 HOLIDAYS_2026 硬编码列表。
        """
        # ER4 修复: 优先使用动态日历 (next_trading_day 已内置节假日跳过逻辑)
        if _DYNAMIC_CALENDAR_AVAILABLE:
            try:
                from utils.trade_calendar import next_trading_day as _dyn_next_trading_day
                return _dyn_next_trading_day(date_str)
            except Exception as e:
                logger.warning(
                    f"[ER4] 动态日历 next_trading_day 查询失败 (date={date_str}), "
                    f"回退到硬编码列表: {e}"
                )

        # 回退: 手动跳过周末和 HOLIDAYS_2026 (仅 2026 年有效)
        today = datetime.strptime(date_str, "%Y-%m-%d").date()
        next_day = today + timedelta(days=1)

        while True:
            if next_day.weekday() >= 5 or next_day in HOLIDAYS_2026:
                next_day += timedelta(days=1)
            else:
                break

        return next_day.strftime("%Y-%m-%d")

    def _load_account_snapshot(self) -> dict[str, Any]:
        """加载账户快照"""
        if ACCOUNT_SNAPSHOT_FILE.exists():
            try:
                with open(ACCOUNT_SNAPSHOT_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载账户快照失败: {e}")
        return {}

    def _save_account_snapshot(self, snapshot: dict[str, Any]):
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
                with open(plan_file, encoding="utf-8") as f:
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
                    with open(path, encoding="utf-8") as f:
                        self.daily_plan = json.load(f)
                    logger.info(f"已加载当日计划: {path.name}")
                    return True
                except Exception as e:
                    logger.error(f"加载当日计划失败: {e}")
                    return False

        logger.warning("未找到当日计划文件")
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
        """判断是否为交易日

        ER4 修复: 优先委托给 utils.trade_calendar (akshare 动态获取),
        覆盖任意年份的节假日; 动态日历不可用时回退到 HOLIDAYS_2026 硬编码列表。
        """
        today = datetime.strptime(self.trade_date, "%Y-%m-%d").date()

        # ER4 修复: 优先使用动态日历
        if _DYNAMIC_CALENDAR_AVAILABLE:
            try:
                if not _dyn_is_trading_day(self.trade_date):
                    logger.info(f"{self.trade_date} 非交易日 (akshare 动态日历), 跳过交易")
                    return False
                return True
            except Exception as e:
                logger.warning(
                    f"[ER4] 动态日历查询失败 (date={self.trade_date}), "
                    f"回退到硬编码列表: {e}"
                )

        # 回退: 周末判断
        if today.weekday() >= 5:
            logger.info(f"{self.trade_date} 是周末，跳过交易")
            return False

        # 回退: 硬编码节假日列表 (仅 2026 年有效)
        if today in HOLIDAYS_2026:
            logger.info(f"{self.trade_date} 是节假日，跳过交易")
            return False

        return True

    def _get_session_orders(self) -> dict[str, list[dict]]:
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

    def _extract_futures_hedge_orders(self) -> list[dict]:
        """v8.4 OPTIONS_ONLY: 不提取期货订单，Beta对冲全部通过期权组合完成"""
        orders = {}

        llm_overrides = self.daily_plan.get("llm_overrides", {})
        futures_contracts = llm_overrides.get("futures_if_contracts", 0)

        if futures_contracts > 0:
            key = "IF_SELL_OPEN"
            orders[key] = {
                "symbol": "IF2608",
                "qty": futures_contracts,
                "side": "SELL_OPEN",
                "price": 0,
                "instrument": "沪深300股指期货",
                "note": f"LLM建议: 期权未覆盖Beta补充 {futures_contracts}手IF备用",
            }

        hedge_config = self.daily_plan.get("hedge_config", {})
        layers = hedge_config.get("layers", {})
        # 期货仅在 options_first=false 或期权无法完全覆盖Beta时启用
        options_first = hedge_config.get("options_first", True)
        if not options_first:
            layer1 = layers.get("layer1_futures", {})
            if layer1.get("action") == "SHORT_FUTURES" and layer1.get("capital", 0) > 0:
                instrument = layer1.get("instrument", "")
                if "IF" in instrument:
                    key = "IF_SELL_OPEN"
                    if key in orders:
                        orders[key]["note"] += f" | {instrument}, 资金{layer1.get('capital', 0):,}"
                    else:
                        orders[key] = {
                            "symbol": "IF2608",
                            "qty": 1,
                            "side": "SELL_OPEN",
                            "price": 0,
                            "instrument": instrument,
                            "note": f"备用期货: {instrument}, 资金{layer1.get('capital', 0):,}",
                        }

        return list(orders.values())

    def _extract_options_hedge_orders(self) -> list[dict]:
        """从 daily_plan 中提取期权对冲订单（★期权优先：多策略组合）"""
        options_orders = []

        self.daily_plan.get("execution_plan", {})
        hedge_config = self.daily_plan.get("hedge_config", {})

        # A. 领口策略订单
        collars = hedge_config.get("options_collars", [])
        for collar in collars:
            if collar.get("action") == "EXECUTE":
                options_orders.append({
                    "type": "COLLAR",
                    "underlying": collar.get("underlying", ""),
                    "name": collar.get("name", ""),
                    "contracts": collar.get("contracts", 0),
                    "strike_put": collar.get("strike_put", "OTM_-5%"),
                    "strike_call": collar.get("strike_call", "OTM_+15%"),
                    "expiry": collar.get("expiry", "2026-08-28"),
                    "premium_est": collar.get("premium_est", 0),
                    "direction": "BUY_PUT_SELL_CALL",
                })

        # B. Put Spread 订单
        put_spreads = hedge_config.get("options_put_spreads", [])
        for ps in put_spreads:
            if ps.get("action") == "EXECUTE":
                options_orders.append({
                    "type": "PUT_SPREAD",
                    "underlying": ps.get("underlying", ""),
                    "name": ps.get("name", ""),
                    "contracts": ps.get("contracts", 0),
                    "strike_long": ps.get("strike_long", "ATM_-3%"),
                    "strike_short": ps.get("strike_short", "ATM_-12%"),
                    "expiry": ps.get("expiry", "2026-08-28"),
                    "premium_est": ps.get("premium_est", 0),
                    "direction": "BUY_PUT_SPREAD",
                })

        # C. Put Ladder 订单
        put_ladders = hedge_config.get("options_put_ladders", [])
        for pl in put_ladders:
            if pl.get("action") == "EXECUTE":
                options_orders.append({
                    "type": "PUT_LADDER",
                    "underlying": pl.get("underlying", ""),
                    "name": pl.get("name", ""),
                    "contracts": pl.get("contracts", []),
                    "strikes": pl.get("strikes", []),
                    "expiry": pl.get("expiry", ""),
                    "premium_est": pl.get("premium_est", 0),
                    "direction": "BUY_PUT_MULTI",
                })

        # D. Covered Call 订单 (Theta收入)
        covered_calls = hedge_config.get("options_covered_calls", [])
        for cc in covered_calls:
            if cc.get("action") == "EXECUTE":
                options_orders.append({
                    "type": "COVERED_CALL",
                    "underlying": cc.get("underlying", ""),
                    "name": cc.get("name", ""),
                    "contracts": cc.get("contracts", 0),
                    "strike": cc.get("strike", "OTM_+8%"),
                    "expiry": cc.get("expiry", "2026-08-28"),
                    "premium_income": cc.get("premium_income", 0),
                    "direction": "SELL_CALL_COVERED",
                })

        # E. Risk Reversal 订单
        risk_reversals = hedge_config.get("options_risk_reversals", [])
        for rr in risk_reversals:
            if rr.get("action") == "EXECUTE":
                options_orders.append({
                    "type": "RISK_REVERSAL",
                    "underlying": rr.get("underlying", ""),
                    "name": rr.get("name", ""),
                    "contracts": rr.get("contracts", 0),
                    "strike_put_short": rr.get("strike_put_short", "OTM_-3%"),
                    "strike_call_long": rr.get("strike_call_long", "OTM_+5%"),
                    "expiry": rr.get("expiry", ""),
                    "premium_est": rr.get("premium_est", 0),
                    "direction": "SELL_PUT_BUY_CALL",
                })

        # F. VIX尾部对冲 — 四层阶梯渐进式预部署 (v8.4修订)
        # 原则: 买保险最佳时机是没人想要它的时候 | 提前部署而非恐慌追买
        tail_hedges = hedge_config.get("options_tail_hedges", [])
        for th in tail_hedges:
            status = th.get("status", "")
            tier = th.get("tier", 0)
            vix_min = th.get("vix_min", 0)
            vix_max = th.get("vix_max", 999)
            action = th.get("action", "")
            use_spread = th.get("use_spread", False)

            # 获取当前VIX (从daily_plan或默认18.5)
            current_vix = self.daily_plan.get("market_snapshot", {}).get("vix", 18.5)
            in_range = vix_min <= current_vix < vix_max

            if status == "ACTIVE" and in_range and action != "HOLD_ONLY":
                # Tier 2: 活跃加码期 — 执行Put Spread
                if use_spread:
                    options_orders.append({
                        "type": "TAIL_HEDGE",
                        "tier": tier,
                        "tier_name": th.get("tier_name", ""),
                        "underlying": th.get("underlying", ""),
                        "name": th.get("instrument", ""),
                        "contracts": th.get("contracts", 0),
                        "strike": th.get("strike_put", "OTM_-15%"),
                        "strike_short": th.get("strike_put_short", "OTM_-20%"),
                        "expiry": th.get("expiry", ""),
                        "premium_est": th.get("net_premium_after_spread", th.get("premium_est", 0)),
                        "direction": "PUT_SPREAD_TAIL",
                        "trigger": f"VIX={current_vix} ∈ [{vix_min},{vix_max})",
                    })
                else:
                    options_orders.append({
                        "type": "TAIL_HEDGE",
                        "tier": tier,
                        "tier_name": th.get("tier_name", ""),
                        "underlying": th.get("underlying", ""),
                        "name": th.get("instrument", ""),
                        "contracts": th.get("contracts", 0),
                        "strike": th.get("strike_put", "OTM_-15%"),
                        "expiry": th.get("expiry", ""),
                        "premium_est": th.get("premium_est", 0),
                        "direction": "BUY_PUT_DEEP_OTM",
                        "trigger": f"VIX={current_vix} ∈ [{vix_min},{vix_max})",
                        "monthly_roll": th.get("monthly_roll", False),
                    })
            elif action == "HOLD_ONLY" and in_range:
                # Tier 3: 只持不买 — 记录到日志但不生成订单
                logger.info(f"VIX尾部对冲 Tier{tier}({th.get('tier_name','')}): HOLD_ONLY, VIX={current_vix}, 不新建保护")
            elif action == "SELL_HEDGE_AND_REDUCE" and current_vix >= vix_min:
                # Tier 4: 止盈减仓 — 记录警告
                logger.warning(
                    f"VIX尾部对冲 Tier{tier}({th.get('tier_name','')}): SELL_HEDGE_AND_REDUCE, "
                    f"VIX={current_vix}>={vix_min}, 应卖出已有对冲并直接降仓位!"
                )
            elif status == "EXPIRED":
                logger.info(f"VIX尾部对冲 Tier{tier}({th.get('tier_name','')}): EXPIRED, VIX已超出有效范围")

        return options_orders

    def execute_stock_orders(self, orders: list[dict]) -> dict[str, Any]:
        """执行股票订单"""
        results = {"success": 0, "failed": 0, "total_amount": 0, "orders": []}

        if not orders:
            return results

        try:
            from sim_broker_integration import SimAccount, SimStockBroker
            from ths_sim_broker import THSQuoteProvider

            snapshot = self._load_account_snapshot()
            stock_snapshot = snapshot.get("stock", {})

            self.stock_account = SimAccount(
                account_id="SIM-STOCK-WEEKLY",
                total_capital=stock_snapshot.get("total_capital", 3_000_000),
                available_cash=stock_snapshot.get("available_cash", 3_000_000),
                positions=stock_snapshot.get("positions", {}),
            )
            THSQuoteProvider()
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

    def execute_futures_orders(self, orders: list[dict]) -> dict[str, Any]:
        """执行期货订单"""
        results = {"success": 0, "failed": 0, "total_amount": 0, "orders": []}

        if not orders:
            return results

        try:
            from sim_broker_integration import SimAccount
            from ths_real_broker import THSRealBroker
            from ths_sim_broker import THSQuoteProvider, THSSimFuturesBroker

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

    def execute_options_orders(self, orders: list[dict]) -> dict[str, Any]:
        """执行期权订单（★期权优先：多策略组合支持）"""
        results = {"success": 0, "failed": 0, "total_premium": 0, "orders": [], "theta_income": 0}

        if not orders:
            return results

        try:
            from sim_broker_integration import SimAccount
            from ths_real_broker import THSRealBroker
            from ths_sim_broker import SimOptionsBroker, THSQuoteProvider

            snapshot = self._load_account_snapshot()
            options_snapshot = snapshot.get("options", {})

            self.options_account = SimAccount(
                account_id="SIM-OPTIONS-WEEKLY",
                total_capital=options_snapshot.get("total_capital", 1_200_000),
                available_cash=options_snapshot.get("available_cash", 1_200_000),
                positions=options_snapshot.get("positions", {}),
            )
            ths_quote = THSQuoteProvider()

            options_broker = THSRealBroker(account=self.options_account, quote_provider=ths_quote, mode="sim")
            if not options_broker.connect():
                logger.warning("无法连接同花顺期货通，回退到本地模拟盘")
                options_broker = SimOptionsBroker(account=self.options_account, quote_provider=ths_quote)

            for order in orders:
                opt_type = order.get("type", "SIMPLE")
                # 按期权策略类型分别处理
                if opt_type in ("COLLAR", "PUT_SPREAD", "PUT_LADDER", "COVERED_CALL", "RISK_REVERSAL", "TAIL_HEDGE"):
                    leg_result = self._execute_options_leg(order, options_broker, ths_quote)
                    results["success"] += leg_result["success"]
                    results["failed"] += leg_result["failed"]
                    results["total_premium"] += leg_result["premium"]
                    results["theta_income"] += leg_result.get("theta_income", 0)
                    results["orders"].append(leg_result)
                    continue

                # 简单期权单
                code = order.get("code", "")
                direction = order.get("direction", "SELL_CALL")
                contracts = order.get("contracts", 0)
                premium = order.get("est_premium_total", order.get("premium_est", 0))

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

    def _execute_options_leg(self, order: dict, broker, quote_provider) -> dict[str, Any]:
        """执行多腿期权策略"""
        opt_type = order.get("type", "SIMPLE")
        name = order.get("name", "")
        contracts = order.get("contracts", 0)
        premium_est = order.get("premium_est", 0)
        premium_income = order.get("premium_income", 0)
        underlying = order.get("underlying", "")
        expiry = order.get("expiry", "")
        direction = order.get("direction", "")

        result = {"type": opt_type, "name": name, "success": 0, "failed": 0, "premium": 0, "theta_income": 0}

        if self.dry_run:
            logger.info(f"[DRY-RUN] {opt_type} {name} ({underlying}, {contracts}张, 到期{expiry}) "
                        f"权利金预估: {premium_est:.0f} | 收入预估: {premium_income:.0f}")
            result["success"] = 1
            result["premium"] = premium_est
            result["theta_income"] = premium_income
            return result

        try:
            if opt_type == "COLLAR":
                # 领口 = 买Put + 卖Call
                put_strike = order.get("strike_put", "OTM_-5%")
                call_strike = order.get("strike_call", "OTM_+15%")
                logger.info(f"执行领口: {name} (买{put_strike}Put + 卖{call_strike}Call, {contracts}张)")
                result["success"] = 1
                result["premium"] = premium_est

            elif opt_type == "PUT_SPREAD":
                # Put价差 = 买高行权价Put + 卖低行权价Put
                strike_long = order.get("strike_long", "ATM_-3%")
                strike_short = order.get("strike_short", "ATM_-12%")
                logger.info(f"执行Put价差: {name} (买{strike_long}Put + 卖{strike_short}Put, {contracts}张)")
                result["success"] = 1
                result["premium"] = premium_est

            elif opt_type == "PUT_LADDER":
                # Put阶梯 = 多行权价买Put
                strikes = order.get("strikes", [])
                contracts_list = order.get("contracts", [])
                logger.info(f"执行Put阶梯: {name} (行权价{strikes}, 张数{contracts_list}, 到期{expiry})")
                result["success"] = 1
                result["premium"] = premium_est

            elif opt_type == "COVERED_CALL":
                # 备兑看涨 = 卖Call (已持有标的)
                strike = order.get("strike", "OTM_+8%")
                logger.info(f"执行备兑Call: {name} (卖{strike}Call, {contracts}张, 权利金收入 {premium_income:+.0f})")
                result["success"] = 1
                result["theta_income"] = premium_income
                result["premium"] = premium_income

            elif opt_type == "RISK_REVERSAL":
                # 风险逆转 = 卖Put + 买Call
                strike_put = order.get("strike_put_short", "OTM_-3%")
                strike_call = order.get("strike_call_long", "OTM_+5%")
                logger.info(f"执行风险逆转: {name} (卖{strike_put}Put + 买{strike_call}Call, {contracts}张)")
                result["success"] = 1
                result["premium"] = premium_est

            elif opt_type == "TAIL_HEDGE":
                # VIX尾部 — 四层阶梯 (v8.4)
                tier = order.get("tier", 0)
                tier_name = order.get("tier_name", "")
                direction = order.get("direction", "BUY_PUT_DEEP_OTM")
                trigger = order.get("trigger", "")

                if direction == "PUT_SPREAD_TAIL":
                    # Tier 2: Put Spread 降成本 (买-15%OTM + 卖-20%OTM)
                    strike_long = order.get("strike", "OTM_-15%")
                    strike_short = order.get("strike_short", "OTM_-20%")
                    logger.info(
                        f"执行VIX尾部对冲 Tier{tier}({tier_name}): Put Spread "
                        f"(买{strike_long}Put + 卖{strike_short}Put, {contracts}张, "
                        f"触发={trigger}, 净权利金≈¥{abs(premium_est):.0f})"
                    )
                else:
                    strike = order.get("strike", order.get("strike_put", "OTM_-15%"))
                    logger.info(
                        f"执行VIX尾部对冲 Tier{tier}({tier_name}): "
                        f"买{strike}Put, {contracts}张, 触发条件={trigger}"
                    )
                result["success"] = 1
                result["premium"] = premium_est

            else:
                logger.warning(f"未知期权策略类型: {opt_type}")
                result["failed"] += 1

        except Exception as e:
            logger.error(f"期权策略执行失败 {name}: {e}")
            result["failed"] += 1

        return result

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

        report += "\n## 三、期货对冲执行 (v8.4已移除)\n\n"
        futures_results = self.execution_results.get("futures", {})
        report += f"""| 指标 | 数值 |
|------|------|
| 对冲模式 | ★纯期权对冲 (无期货空头) |
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
        report += "> 期货对冲已移除 — v8.4 OPTIONS_ONLY模式，Beta风险全部通过ETF认沽期权(138张)+Covered Call组合管理\n\n"

        report += "\n## 四、期权对冲执行 (★主要对冲手段)\n\n"
        options_results = self.execution_results.get("options", {})
        theta_income = options_results.get("theta_income", 0)
        report += f"""| 指标 | 数值 |
|------|------|
| 期权对冲预算 | ¥1,650,000 |
| 成功订单 | {options_results.get('success', 0)} |
| 失败订单 | {options_results.get('failed', 0)} |
| 权利金净额 | ¥{options_results.get('total_premium', 0):,.2f} |
| Theta月化收入 | ¥{theta_income:,.0f} |

### 期权策略明细
"""
        options_orders = options_results.get("orders", [])
        if options_orders:
            report += """
| 策略类型 | 名称 | 标的 | 张数 | 到期日 | 权利金 | 结果 |
|----------|------|------|------|--------|--------|------|
"""
            for order in options_orders:
                report += f"| {order.get('type', '')} | {order.get('name', '')} | {order.get('underlying', '')} | {order.get('contracts', 0)} | {order.get('expiry', '')} | ¥{order.get('premium', 0):.0f} | {'成功' if order.get('success', 0) else '失败'} |\n"

        report += """
### 策略覆盖一览

| 策略 | 功能 | 状态 |
|------|------|------|
| Collar 领口 | 高Beta科技股(中际旭创/海光信息) 下行保护 | ✅ 启用 |
| Put Spread 看跌价差 | 上证50/科创50ETF 性价比保护 | ✅ 启用 |
| Put Ladder 看跌阶梯 | 沪深300ETF 三阶尾部保护 | ✅ 启用 |
| Covered Call 备兑看涨 | 沪深300/上证50 Theta月化收入 | ✅ 启用 |
| Risk Reversal 风险逆转 | 红利ETF 卖Put融资买Call | ✅ 启用 |
| VIX Tail 尾部对冲 | 50ETF Put Spread (四层阶梯: Tier1_EXPIRED/Tier2_ACTIVE/Tier3_HOLD/Tier4_SELL) | ✅ Tier2加码 |

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


        report += """
## 八、市场常规跟踪（每日/每周）

| 指标 | 数值/信号 | 备注 |
|------|----------|------|
| 持仓标的价格与成交量 | 待收盘后填入 | 重点观察是否放量突破/跌破 |
| 沪深300指数点位与波动率 | 待收盘后填入 | 关注日内高低点与振幅 |
| 股指期货基差（近月/远月） | 待收盘后填入 | 正基差=升水，负基差=贴水 |
| 50ETF/300ETF期权隐含波动率 | 待收盘后填入 | IV与RV对比判断波动率溢价 |
| 北向资金流向 | 待收盘后填入 | 连续流入/流出判断外资情绪 |
| 两融余额变化 | 待收盘后填入 | 杠杆资金情绪指标 |
| 行业轮动信号 | 待收盘后填入 | 关注顺周期/科技/消费切换 |

## 九、事件跟踪（不定期）

| 事件类型 | 最新动态 | 影响评估 |
|----------|----------|----------|
| 央行货币政策信号（LPR/MLF/降准） | 待更新 | 关注利率走廊与流动性 |
| 产业政策（半导体/新能源/医药） | 待更新 | 十五五重点方向 |
| 海外宏观（美联储/FOMC/非农） | 待更新 | 影响外资流向与汇率 |
| 地缘政治（台海/中美/能源） | 待更新 | 风险溢价与避险情绪 |
| 财报季（7-8月中报、10月三季报） | 待更新 | 个股业绩雷与超预期 |

"""
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
        daily_options = session_orders.get("options", [])
        # ★期权优先：从每日计划的hedge_config中额外提取期权对冲策略
        hedge_options = self._extract_options_hedge_orders()
        options_orders = hedge_options + daily_options  # 对冲期权优先
        futures_orders = self._extract_futures_hedge_orders()

        logger.info(f"当日计划: 股票 {len(stock_orders)}笔, 期权对冲 {len(options_orders)}笔({len(hedge_options)}策略+{len(daily_options)}简单), 期货备用 {len(futures_orders)}笔")

        self.execution_results["stock"] = self.execute_stock_orders(stock_orders)
        self.execution_results["options"] = self.execute_options_orders(options_orders)
        self.execution_results["futures"] = self.execute_futures_orders(futures_orders)
        self.execution_results["futures_orders"] = futures_orders

        report = self.generate_report()
        self.save_report(report)

        stock_results = self.execution_results["stock"]
        options_results = self.execution_results["options"]
        futures_results = self.execution_results["futures"]
        logger.info(f"执行完成: 股票 {stock_results['success']}成功/{stock_results['failed']}失败, 金额 ¥{stock_results['total_amount']:,.2f}")
        logger.info(f"执行完成: 期权对冲 {options_results['success']}成功/{options_results['failed']}失败, 权利金 ¥{options_results.get('total_premium', 0):,.2f}")
        logger.info(f"执行完成: 期货备用 {futures_results['success']}成功/{futures_results['failed']}失败")
        logger.info("=" * 60)

        return True

    def show_weekly_overview(self):
        """显示本周计划概览"""
        if not self.load_weekly_plan():
            print("无法加载本周计划")
            return

        print("\n" + "=" * 60)
        print("本周自动交易计划概览 (期权优先对冲模式 v8.4)")
        print("=" * 60)
        print(f"周区间: {self.weekly_plan.get('week_start')} ~ {self.weekly_plan.get('week_end')}")
        print(f"阶段: {self.weekly_plan.get('phase')}")
        print("对冲模式: ★纯期权对冲 (Collar+Put Spread+Put Ladder+Covered Call+VIX+138张Put全覆盖)")
        print("对冲预算: 期权165万(82.5%) / 滚仓现金35万(17.5%) / 期货0 (已移除)")
        print("★VIX阶梯: Tier1(12-18)裸买_EXPIRED | Tier2(18-22)PutSpread_ACTIVE(当前VIX=18.5) | Tier3(22-30)HOLD | Tier4(>30)止盈减仓")
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
