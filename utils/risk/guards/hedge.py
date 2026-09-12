"""风控守卫 Guard 3/4 — 对冲执行与认沽保护 mixin

审计 item 11 (2026-09-10) 拆自 ``utils/risk_guard_integrator.py``: **零行为变更**,
仅搬移方法体与所属分隔注释, 逻辑/字段/落盘路径/日志文本逐字节保持原样。

契约: 路径与 logger 一律经 ``plan_context.XXX`` 属性式访问 (属性式访问使测试的
``monkeypatch.setattr(plan_context, ...)`` 生效); 重量级引擎依赖保持方法体内惰性 import。
"""

from __future__ import annotations

from typing import Any

from utils.risk.guards.plan_context import PlanContextMixin


class HedgeGuardMixin(PlanContextMixin):
    # ============================================================
    # Guard 3: 对冲执行
    # ============================================================
    def guard_hedge_execution(self, pnl_report: dict[str, Any], plan: dict[str, Any], next_date: str) -> dict[str, Any]:
        """对冲引擎 - 计算并写入对冲订单。

        调用 HedgeExecutionEngine 生成期货/期权对冲订单，并按预算校验
        设置 execution_status (PENDING/CANCELLED_OVER_BUDGET)。

        Args:
            pnl_report: 当日盈亏报告字典
            plan: 次日交易计划字典
            next_date: 下一交易日 (YYYY-MM-DD)

        Returns:
            修改后的交易计划 (写入 hedge_execution 字段)
        """
        try:
            from utils.hedge_execution_engine import HedgeExecutionEngine
        except ImportError:
            self._log("[对冲] HedgeExecutionEngine 导入失败，跳过")
            return plan

        engine = HedgeExecutionEngine()

        try:
            hedge_result = engine.generate_hedge_orders()
            if hedge_result and isinstance(hedge_result, dict):
                futures_orders = hedge_result.get("futures_orders", [])
                options_orders = hedge_result.get("options_orders", [])
                total_orders = len(futures_orders) + len(options_orders)

                # v8.6.8 P0-01 FIX (2026-07-26): 同步 execution_status / execution_notes 字段
                # 原代码 plan['hedge_execution'] = hedge_result 仅写入原始结果, 缺少
                # execution_status 和 execution_notes, 导致下游执行器无法识别订单状态
                # 现在与 write_to_trade_plan() 保持一致, 在 in-memory plan 中也添加这些字段
                cost_summary = hedge_result.get("cost_summary", {})
                within_budget = bool(cost_summary.get("within_budget", True))
                if within_budget:
                    execution_status = "PENDING"
                    order_status = "PENDING"
                    execution_notes = [
                        "期货: 09:45-10:30 完成IF空头开仓 (若存在)",
                        "期权: 09:30-10:00 完成认沽期权买入",
                        "确认: 盘后核实对冲比例是否达标",
                    ]
                else:
                    execution_status = "CANCELLED"
                    order_status = "CANCELLED_OVER_BUDGET"
                    execution_notes = [
                        f"[P0-01] 预算超支, 全部对冲订单已拦截: "
                        f"total_cost=¥{cost_summary.get('total_cost', 0):,.0f} > "
                        f"threshold=¥{cost_summary.get('budget_threshold', 0):,.0f}",
                        "下游执行器 (daily_workflow/SOR) 必须跳过 CANCELLED_OVER_BUDGET 订单",
                        "需调整 hedge_positions 配置 (减少 contracts 或 premium_budget) 后重新生成",
                    ]
                    # 超预算时改写订单 status
                    for o in futures_orders:
                        o["status"] = order_status
                    for o in options_orders:
                        o["status"] = order_status

                # 写入交易计划 (含 execution_status / execution_notes, 与 write_to_trade_plan 一致)
                hedge_result["execution_status"] = execution_status
                # v8.6.8 P0-07 FIX (2026-07-26): execution_notes 根据 futures_orders 动态生成
                # 原代码硬编码 "期货: 09:45-10:30 完成IF空头开仓 (若存在)"
                # 但 OPTIONS_ONLY 模式下 futures_orders=[], 仍提及 IF 期货会误导执行器
                # 修复: 根据 futures_orders 是否为空动态生成 notes
                if not futures_orders:
                    execution_notes = [
                        "[P0-07] hedge_mode=OPTIONS_ONLY, 无期货订单",
                        "期权: 09:30-10:00 完成认沽期权买入",
                        "确认: 盘后核实对冲比例是否达标",
                    ]
                else:
                    execution_notes = [
                        "期货: 09:45-10:30 完成IF空头开仓",
                        "期权: 09:30-10:00 完成认沽期权买入",
                        "确认: 盘后核实对冲比例是否达标",
                    ]
                hedge_result["execution_notes"] = execution_notes
                plan["hedge_execution"] = hedge_result

                # v8.6.8 P0-08 FIX (2026-07-26): 同步 futures_options_hedge 与 hedge_execution 一致
                # 原始 bug: futures_options_hedge 字段从 hedge_execution_orders_{date}.json 加载
                # (可能不存在或过时), 而 hedge_execution 由 hedge_execution_engine 实时生成
                # 两者漂移会导致: futures_options_hedge.orders=[] 但 hedge_execution.options_orders 有 4 个订单
                # 下游执行器读 futures_options_hedge 会漏掉对冲订单
                # 修复: 用 hedge_execution 的内容同步 futures_options_hedge
                plan["futures_options_hedge"] = {
                    "hedge_mode": hedge_result.get("cost_summary", {}).get("hedge_mode", "OPTIONS_ONLY"),
                    "loaded": True,
                    "portfolio_beta": hedge_result.get("portfolio_status", {}).get("portfolio_beta_before", 0.0),
                    "hedge_pct": (
                        0.0
                        if not futures_orders
                        else (futures_orders[0].get("rationale", {}).get("beta_to_hedge", 0) if futures_orders else 0.0)
                    ),
                    "execution_timing": {
                        "options_window": "09:30-10:00",
                        "futures_window": "10:30-11:00" if futures_orders else None,
                        "reserve_ratio": 0.175,
                        "reserve_amount": hedge_result.get("cost_summary", {}).get("buffer_for_roll", 0),
                    },
                    "orders": futures_orders + options_orders,
                    "orders_count": total_orders,
                    "total_premium": hedge_result.get("cost_summary", {}).get("total_premium_budget", 0),
                    "total_notional": sum(o.get("notional", 0) for o in futures_orders),
                    "total_safe_haven": 0,
                    "budget_check": {
                        "total_cost": hedge_result.get("cost_summary", {}).get("total_cost", 0),
                        "total_premium": hedge_result.get("cost_summary", {}).get("total_premium_budget", 0),
                        "futures_margin": hedge_result.get("cost_summary", {}).get("total_margin_required", 0),
                        "safe_haven": 0,
                        "reserve_amount": hedge_result.get("cost_summary", {}).get("buffer_for_roll", 0),
                        "remaining": (
                            hedge_capital_value - hedge_result.get("cost_summary", {}).get("total_cost", 0)
                            if (hedge_capital_value := hedge_result.get("portfolio_status", {}).get("hedge_capital", 0))
                            > 0
                            else 0
                        ),
                        "within_budget": within_budget,
                    },
                    "notes": [
                        f"对冲模式: {hedge_result.get('cost_summary', {}).get('hedge_mode', 'OPTIONS_ONLY')}",
                        f"执行状态: {execution_status}",
                        f"订单数: {total_orders} (期货 {len(futures_orders)} + 期权 {len(options_orders)})",
                        f"预算使用: {hedge_result.get('cost_summary', {}).get('hedge_capital_usage_pct', 0) * 100:.1f}%",  # noqa: E501
                    ],
                    "execution_status": execution_status,
                }

                plan.setdefault("risk_guard", {})["hedge_action"] = (
                    f"GENERATED_{total_orders}_ORDERS"
                    if within_budget
                    else f"CANCELLED_{total_orders}_ORDERS_OVER_BUDGET"
                )

                # 同时写入独立的对冲执行文件 (v8.6.8 P0-01: 现已写入 v8.3 路径)
                engine.write_to_trade_plan(hedge_result, next_date)

                self._log(
                    f"[对冲] 已生成 {total_orders} 条对冲订单 "
                    f"(期货{len(futures_orders)}+期权{len(options_orders)}), "
                    f"status={execution_status}"
                )

                # 输出期货订单详情
                for o in futures_orders:
                    contracts = o.get("contracts", 0)
                    est_price = o.get("est_price", 0)
                    self._log(f"  → IF期货 空头 {contracts}手 @ {est_price:.2f}")

                # 输出组合Beta变化
                ps = hedge_result.get("portfolio_status", {})
                self._log(f"  → Beta: {ps.get('portfolio_beta_before', '?')} → {ps.get('target_beta_after', '?')}")
            else:
                plan.setdefault("risk_guard", {})["hedge_action"] = "NO_CHANGE_NEEDED"
                self._log("[对冲] 当前对冲比例正常，无需调整")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[对冲] 执行引擎异常: {e}")
            plan.setdefault("risk_guard", {})["hedge_action"] = f"ERROR: {e}"

        return plan

    # ============================================================
    # Guard 4: 认沽保护
    # ============================================================
    def guard_protective_put(self, pnl_report: dict[str, Any], plan: dict[str, Any], next_date: str) -> dict[str, Any]:
        """认沽保护 - 检查并生成保护性认沽订单。

        组合市值低于 100 万时跳过；否则调用 ProtectivePutEngine 按回撤
        级别生成认沽订单并写入计划。

        Args:
            pnl_report: 当日盈亏报告字典
            plan: 次日交易计划字典
            next_date: 下一交易日 (YYYY-MM-DD)

        Returns:
            修改后的交易计划 (写入 put_protection_orders / risk_guard.put_action)
        """
        try:
            from utils.protective_put_engine import ProtectivePutEngine
        except ImportError:
            self._log("[认沽] ProtectivePutEngine 导入失败，跳过")
            return plan

        ppe = ProtectivePutEngine()

        try:
            # 检查是否需要建立/滚仓 (v7.7修正: 使用 portfolio_pnl.summary)
            portfolio_value = self._get_pnl_summary(pnl_report).get("total_market_value", 0)

            if portfolio_value < 1_000_000:
                self._log(f"[认沽] 组合市值 {portfolio_value:,.0f} < 100万，暂不启动保护")
                plan.setdefault("risk_guard", {})["put_action"] = "BELOW_THRESHOLD"
                return plan

            # v7.7修正: generate_put_orders() 返回 Dict {should_execute, orders, ...}
            result = ppe.generate_put_orders(drawdown_level=plan.get("risk_guard", {}).get("drawdown_level", 0))
            put_orders = result.get("orders", [])

            if result.get("should_execute") and put_orders:
                plan.setdefault("put_protection_orders", [])
                plan["put_protection_orders"] = put_orders
                plan.setdefault("risk_guard", {})["put_action"] = f"GENERATED_{len(put_orders)}_PUTS"

                total_premium = result.get("total_premium_est", 0)
                self._log(f"[认沽] 已生成 {len(put_orders)} 条认沽订单, 总权利金 ¥{total_premium:,.0f}")
            else:
                plan.setdefault("risk_guard", {})["put_action"] = "EXISTING_PROTECTION_OK"
                self._log(f"[认沽] {result.get('reason', '无需新建/滚仓')}")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[认沽] 引擎异常: {e}")
            plan.setdefault("risk_guard", {})["put_action"] = f"ERROR: {e}"

        return plan
    # ============================================================
    # v7.7: 对冲引擎 & 认沽保护引擎去重
    # ============================================================
    def _deduplicate_put_orders(self, plan: dict[str, Any]) -> None:
        """去重: 避免 HedgeExecutionEngine 与 ProtectivePutEngine 对同一底层重复生成 PUT 订单

        策略:
          - ProtectivePutEngine 是 PUT 订单的权威来源 (含真实权利金估算、预算控制)
          - HedgeExecutionEngine 的 options_orders 如与 put_protection_orders 重复底层，则剔除
          - HedgeExecutionEngine 的 futures_orders 不受影响
        """
        put_protection_orders = plan.get("put_protection_orders", [])
        hedge_execution = plan.get("hedge_execution", {})
        options_orders = hedge_execution.get("options_orders", [])

        if not put_protection_orders or not options_orders:
            self._log("[去重] 无需去重 (单侧无Put订单)")
            return

        # 提取认沽保护引擎已覆盖的底层
        protected_codes = set()
        for o in put_protection_orders:
            code = self._extract_underlying_code(o.get("underlying", "") or o.get("instrument", ""))
            if code:
                protected_codes.add(code)

        if not protected_codes:
            self._log("[去重] 无法识别受保护底层，跳过")
            return

        # 检查对冲引擎的期权订单
        removed = []
        kept_options = []
        for o in options_orders:
            o_code = self._extract_underlying_code(o.get("instrument", "") or o.get("underlying", ""))
            if o_code and o_code in protected_codes:
                removed.append(
                    {
                        "instrument": o.get("instrument", "?"),
                        "code": o_code,
                        "order_id": o.get("order_id", "?"),
                    }
                )
            else:
                kept_options.append(o)

        if removed:
            plan["hedge_execution"]["options_orders"] = kept_options
            # 更新订单总数
            futures = hedge_execution.get("futures_orders", [])
            plan["hedge_execution"]["total_orders"] = len(futures) + len(kept_options)
            plan["risk_guard"][
                "hedge_action"
            ] = f"GENERATED_{len(futures) + len(kept_options)}_ORDERS(dedup_removed_{len(removed)})"
            plan.setdefault("risk_guard", {})["put_hedge_dedup"] = {
                "removed_count": len(removed),
                "removed": [r["code"] for r in removed],
                "reason": "ProtectivePutEngine 已覆盖, 避免超额对冲",
            }

            self._log(f"[去重] 剔除 {len(removed)} 笔重复对冲PUT: {', '.join(r['code'] for r in removed)}")
            self._log(f"[去重] 对冲引擎保留 {len(kept_options)} 笔独立PUT + {len(futures)} 笔期货")
        else:
            self._log("[去重] 无重复底层，所有订单保留")
