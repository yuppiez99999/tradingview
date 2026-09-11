"""模拟执行簇 —— 自 ``daily_workflow.DailyWorkflow`` 迁出 (2026-09-11)。

包含模拟盘模式执行 (股票日盘 + 期货日盘/夜盘)、批次执行、订单规范化、
夜盘订单抽取、订单批量执行与订单汇总。

为什么落点不是 ``v8.3_institutional/daily_workflow/`` 目录
----------------------------------------------------------
若创建带 ``__init__.py`` 的 ``daily_workflow/`` 包, 它会**抢先**于同名模块
``daily_workflow.py`` 被 import, 直接弄坏既有
``from daily_workflow import DailyWorkflow`` (4 个测试文件 + v87 release gate
都依赖该路径), 故落在 ``workflow_mixins/``。

拆解口径
--------
本文件只装**真实现**; 编排层 (``__init__`` / ``phase_*`` 委托 shim /
``_build_context`` / ``run`` / ``main``) 留在宿主。迁出方法只引用标准库与
typing 名字, 故无需宿主属性式间接层。logger 名字必须与宿主一致
(``v75.daily_workflow``), 否则日志分区会漂移。

``SmartOrderRouter`` / ``MockBroker`` / ``AlgoType`` 属 V75 **可选导入**
(宿主 Try 块内, 失败时置 ``V75_READY=False`` 降级), 故注解走 ``TYPE_CHECKING``,
运行时在 ``_execute_order_batch`` 内局部导入 —— 若改成模块级导入, 可选模块
缺失时会把宿主的整体导入炸掉, 破坏 graceful degradation。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # AlgoType 只在运行时用 (见 _execute_order_batch 内局部导入), 不在此导入
    from execution.smart_order_router import MockBroker, SmartOrderRouter

logger = logging.getLogger("v75.daily_workflow")


class SimExecutionMixin:
    """模拟执行实现 (由 DailyWorkflow 继承使用)。"""

    def _execute_sim_mode(
        self,
        signal: dict[str, Any],
        morning_orders: list[dict[str, Any]],
        afternoon_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """模拟盘模式执行：股票日盘 + 期货（日盘+夜盘）"""
        calendar = self.sim_engine.calendar
        if not calendar.is_trading_day():
            logger.info("非交易日，跳过模拟盘执行")
            self.state["phases"]["execute"] = {
                "status": "PASS",
                "fills": [],
                "action": "SKIP_NON_TRADING_DAY",
            }
            return []

        all_fills: list[dict[str, Any]] = []

        # === 上午批次（股票+期货日盘） ===
        logger.info("--- 模拟盘上午批次 (股票日盘 + 期货日盘) ---")
        morning_fills = self._execute_sim_batch(morning_orders, session="day")
        all_fills.extend(morning_fills)

        # === 下午批次（股票+期货日盘） ===
        logger.info("--- 模拟盘下午批次 (股票日盘 + 期货日盘) ---")
        afternoon_fills = self._execute_sim_batch(afternoon_orders, session="day")
        all_fills.extend(afternoon_fills)

        # === 期货夜盘批次（如有夜盘品种） ===
        futures_night_orders = self._extract_futures_night_orders(
            morning_orders + afternoon_orders
        )
        if futures_night_orders and calendar.is_trading_day():
            logger.info("--- 模拟盘夜盘批次 (期货夜盘) ---")
            night_fills = self._execute_sim_batch(futures_night_orders, session="night")
            all_fills.extend(night_fills)

        # === 更新持仓 ===
        if self.position_sync:
            try:
                self.position_sync.update_positions_from_fills(all_fills)
            except Exception as exc:  # fail-safe
                logger.warning("更新持仓失败: %s", exc)

        # === 日末持仓快照 ===
        if self.position_sync:
            try:
                snapshot_path = self.position_sync.save_daily_snapshot(self.trade_date)
                self.state.setdefault("phases", {}).setdefault("execute", {})[
                    "sim_snapshot"
                ] = str(snapshot_path)
            except Exception as exc:  # fail-safe
                logger.warning("保存日末持仓快照失败: %s", exc)

        # === 订单级汇总 ===
        all_orders = morning_orders + afternoon_orders
        order_summary = self._aggregate_order_summary(all_orders, all_fills)

        morning_amount = sum(f.get("amount", 0) for f in morning_fills)
        afternoon_amount = sum(f.get("amount", 0) for f in afternoon_fills)
        total_amount = morning_amount + afternoon_amount

        self.state["orders"].extend(all_fills)
        # 期权希腊字母暴露
        greek_exposure = {}
        try:
            greek_exposure = (
                self.sim_engine.get_greek_exposure()
                if hasattr(self.sim_engine, "get_greek_exposure")
                else {}
            )
            if greek_exposure:
                logger.info(
                    "[模拟盘] 期权希腊字母暴露: Delta=%.2f Gamma=%.2f Theta=%.2f Vega=%.2f",
                    greek_exposure.get("delta", 0),
                    greek_exposure.get("gamma", 0),
                    greek_exposure.get("theta", 0),
                    greek_exposure.get("vega", 0),
                )
        except Exception:
            pass

        self.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": all_fills,
            "order_summary": order_summary,
            "morning_count": len(morning_fills),
            "afternoon_count": len(afternoon_fills),
            "morning_amount": morning_amount,
            "afternoon_amount": afternoon_amount,
            "total_amount": total_amount,
            "sim_mode": True,
            "greek_exposure": greek_exposure,
        }
        return all_fills

    def _execute_sim_batch(
        self, orders: list[dict[str, Any]], session: str
    ) -> list[dict[str, Any]]:
        """执行一批模拟盘订单（按股票/期货/期权拆分）"""
        if not orders:
            return []

        stock_orders = []
        futures_orders = []
        options_orders = []
        for order in orders:
            symbol = str(order.get("code", order.get("symbol", "")))
            market = self.sim_engine.router._detect_market(symbol)
            if market == "stock":
                stock_orders.append(self._normalize_sim_order(order, session=session))
            elif market == "options":
                options_orders.append(self._normalize_sim_order(order, session=session))
            else:
                futures_orders.append(self._normalize_sim_order(order, session=session))

        fills: list[dict[str, Any]] = []
        if stock_orders:
            logger.info("[模拟盘] 股票订单 %d 笔 @ %s", len(stock_orders), session)
            fills.extend(
                self.sim_engine.execute_stock_orders(stock_orders, session=session)
            )
        if futures_orders:
            logger.info(
                "[模拟盘] 期货订单 %d 笔 @ %s (同花顺期货通)",
                len(futures_orders),
                session,
            )
            fills.extend(
                self.sim_engine.execute_futures_orders(futures_orders, session=session)
            )
        if options_orders:
            logger.info("[模拟盘] 期权订单 %d 笔 @ %s", len(options_orders), session)
            fills.extend(
                self.sim_engine.execute_options_orders(options_orders, session=session)
            )
        return fills

    def _normalize_sim_order(
        self, order: dict[str, Any], session: str
    ) -> dict[str, Any]:
        """将交易计划订单规范化为模拟盘订单"""
        symbol = str(order.get("code", order.get("symbol", "")))
        side = str(order.get("side", "BUY"))
        qty = int(order.get("shares", order.get("qty", 0)))
        price = float(order.get("est_price", order.get("price", 0)))
        return {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": price,
            "order_type": "LIMIT",
            "session": session,
        }

    def _extract_futures_night_orders(
        self, orders: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """从订单中提取支持夜盘的期货订单"""
        night_codes = set(
            self.sim_engine.router.futures_broker._night_session_info.keys()
        )
        result = []
        for order in orders:
            symbol = str(order.get("code", order.get("symbol", "")))
            code = symbol[:2] if len(symbol) >= 2 else symbol
            if code in night_codes:
                result.append(order)
        return result

    def _execute_order_batch(
        self,
        sor: SmartOrderRouter,
        broker: MockBroker,
        orders: list[dict[str, Any]],
        session: str,
    ) -> list[dict[str, Any]]:
        """执行一批订单 (上午或下午)

        Args:
            sor: SmartOrderRouter 实例
            broker: MockBroker 实例
            orders: 订单列表 (按 priority 排序)
            session: "morning" 或 "afternoon"

        Returns:
            成交记录列表
        """
        # 运行时局部导入: AlgoType 属 V75 可选导入, 缺失时不得炸掉模块导入
        from execution.algo_engine import AlgoType

        fills: list[dict[str, Any]] = []
        for order in orders:
            symbol = order.get("code", "")
            name = order.get("name", "")
            qty = int(order.get("shares", 0))
            side = order.get("side", "BUY")
            est_price = float(order.get("est_price", 0))
            limit_price = float(order.get("limit_price", 0))
            risk = order.get("risk", "中")
            style = order.get("style", "")

            if qty <= 0 or est_price <= 0:
                logger.warning(f"跳过无效订单: {symbol} qty={qty} price={est_price}")
                continue

            # 确保 MockBroker 使用订单 est_price，避免键前缀不匹配导致默认 10.0
            broker.prices[symbol] = est_price
            decision_price = est_price

            try:
                # 使用 ICEBERG 拆单执行 (每片 100 股)
                # decision_price 使用 est_price, MockBroker 会以 est_price ± 0.05% 成交
                fill_objs = sor.execute(
                    symbol=symbol,
                    target_qty=qty,
                    side=side,
                    decision_price=decision_price,
                    algo=AlgoType.ICEBERG,
                )

                for f in fill_objs:
                    slip_pct = (
                        getattr(f, "slippage", 0.0) / decision_price
                        if decision_price > 0
                        else 0.0
                    )
                    fill_status = "FILLED" if slip_pct < 0.005 else "SLIPPAGE_BREAK"
                    fill_dict = {
                        "symbol": f.symbol,
                        "name": name,
                        "session": session,
                        "side": f.side,
                        "qty": f.fill_qty,
                        "price": f.fill_price,
                        "amount": f.fill_qty * f.fill_price,
                        "limit_price": limit_price,
                        "est_price": est_price,
                        "decision_price": decision_price,
                        "risk": risk,
                        "style": style,
                        "slippage_bps": getattr(f, "slippage_bps", 0),
                        "slippage_pct": slip_pct,
                        "status": fill_status,
                    }
                    fills.append(fill_dict)

                logger.info(
                    f"  [{session}] {symbol} {name}: {side} {qty}股 @ {est_price} → "
                    f"{len(fill_objs)} 笔成交, 金额 {sum(f.fill_qty*f.fill_price for f in fill_objs):,.0f}"
                )

            except Exception as e:  # fail-safe
                logger.error(f"  [{session}] {symbol} {name} 执行失败: {e}")
                fills.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "session": session,
                        "side": side,
                        "qty": qty,
                        "price": 0,
                        "amount": 0,
                        "status": "FAILED",
                        "error": str(e),
                    }
                )

        return fills

    def _aggregate_order_summary(
        self, orders: list[dict[str, Any]], fills: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """按订单汇总成交明细，统一报告与状态 JSON 的执行口径"""
        # 建立 symbol -> session 映射，订单本身可能不含 session
        symbol_session_map: dict[str, str] = {}
        for f in fills:
            sym = f.get("symbol", "")
            if sym and sym not in symbol_session_map:
                symbol_session_map[sym] = f.get("session", "")

        order_map: dict[str, dict[str, Any]] = {}
        for order in orders:
            symbol = order.get("code", "")
            if not symbol:
                continue
            session = order.get("session", symbol_session_map.get(symbol, ""))
            key = f"{symbol}|{session}"
            order_map[key] = {
                "symbol": symbol,
                "name": order.get("name", ""),
                "session": session,
                "side": order.get("side", "BUY"),
                "qty": int(order.get("shares", 0)),
                "est_price": float(order.get("est_price", 0)),
                "limit_price": float(order.get("limit_price", 0)),
                "risk": order.get("risk", "中"),
                "style": order.get("style", ""),
                "filled_qty": 0,
                "filled_amount": 0.0,
                "avg_price": 0.0,
                "max_slippage_pct": 0.0,
                "status": "PENDING",
                "error": "",
            }

        for f in fills:
            symbol = f.get("symbol", "")
            session = f.get("session", "")
            key = f"{symbol}|{session}"
            record = order_map.get(key)
            if not record:
                continue
            record["filled_qty"] += int(f.get("qty", 0))
            record["filled_amount"] += float(f.get("amount", 0))
            record["max_slippage_pct"] = max(
                record["max_slippage_pct"], float(f.get("slippage_pct", 0.0))
            )
            if f.get("status") == "FAILED":
                record["status"] = "FAILED"
                record["error"] = f.get("error", "")
            elif f.get("status") == "SLIPPAGE_BREAK" and record["status"] not in (
                "FAILED",
                "SLIPPAGE_BREAK",
            ):
                record["status"] = "SLIPPAGE_BREAK"
            elif record["status"] not in ("FAILED", "SLIPPAGE_BREAK"):
                record["status"] = "FILLED"

        for record in order_map.values():
            if record["filled_qty"] > 0:
                record["avg_price"] = (
                    record["filled_amount"] / record["filled_qty"]
                    if record["filled_qty"]
                    else 0.0
                )
            if record["status"] == "FAILED" and not record["error"]:
                record["error"] = "no_fill"

        return list(order_map.values())
