"""Phase 6: 智能执行 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1468-L2252 (phase_execute, 785 行)
拆分日期: 2026-08-28

self -> ctx 替换说明:
- WorkflowContext.__getattr__ 代理所有未显式定义的属性到 wf 实例
- ctx._execute_order_batch / ctx._execute_sim_mode / ctx._options_market_snapshot
  / ctx._aggregate_order_summary 通过 __getattr__ 返回 wf 的 bound method
- getattr(ctx, ...) / hasattr(ctx, ...) 与 getattr(wf, ...) / hasattr(wf, ...) 语义一致
"""

from __future__ import annotations

import logging
from typing import Any

from workflow.context import get_dw_module

logger = logging.getLogger("v75.daily_workflow")

_dw = get_dw_module()
EXECUTION_MODULES_READY: bool = (
    bool(getattr(_dw, "EXECUTION_MODULES_READY", False)) if _dw else False
)


def phase_execute(ctx, signal: dict[str, Any]) -> list[dict[str, Any]]:
    """智能执行 — 2026 年交易计划订单

    支持两种执行模式:
        - MockBroker (默认): 原有模拟执行
        - SimExecutionEngine (--sim): 股票+期货模拟盘，按交易日+夜盘执行

    Args:
        signal: phase_signal() 返回的信号字典,
                必须包含 morning_orders 和 afternoon_orders。

    Returns:
        成交记录列表, 每条含 symbol/side/qty/price/amount/session/status。
    """
    mode = "模拟盘" if ctx.sim_mode else "MockBroker"
    logger.info("=" * 60)
    logger.info(f"Phase 6: 智能执行 ({mode})")
    logger.info("=" * 60)

    # === 期权策略执行（v9.0 多策略期权覆盖层，优先于现货订单检查） ===
    options_plan = ctx.trade_plan.get("options_execution", {}) if ctx.trade_plan else {}
    options_modules = (
        ctx.trade_plan.get("hedge_account", {}).get("modules", [])
        if ctx.trade_plan
        else []
    )
    options_fills: list[dict[str, Any]] = []
    if options_plan and options_modules:
        try:
            from execution.options_runner import OptionsRunner

            runner = OptionsRunner(
                trade_date=ctx.trade_date,
                hedge_capital=float(
                    ctx.trade_plan.get("hedge_account", {}).get("capital", 1_000_000)
                ),
                margin_usage_max=float(
                    ctx.trade_plan.get("hedge_account", {}).get(
                        "margin_usage_max", 600_000
                    )
                ),
                liquidity_buffer_min=float(
                    ctx.trade_plan.get("hedge_account", {}).get(
                        "liquidity_buffer_min", 400_000
                    )
                ),
            )
            options_fills = runner.run_modules(
                modules=options_modules,
                market_data=ctx._options_market_snapshot(),
                trigger_date=ctx.trade_date,
                event_calendar=options_plan.get("event_calendar", []),
            )
            logger.info("期权策略执行完成: %d 条 fills", len(options_fills))
        except Exception as exc:  # fail-safe
            logger.warning("期权策略执行失败: %s", exc, exc_info=True)
            options_fills = []
    else:
        logger.info("当前交易计划无期权策略模块，跳过期权执行")

    # === 信号校验 ===
    action = signal.get("action", "")
    morning_orders = signal.get("morning_orders", [])
    afternoon_orders = signal.get("afternoon_orders", [])
    has_orders = bool(morning_orders or afternoon_orders)

    # 允许直接从 trade_plan 回退读单，避免 --phase execute 跳过 phase_signal 时空跑
    if not has_orders and ctx.trade_plan:
        plan_exec = ctx.trade_plan.get("execution_plan", {})
        morning_orders = plan_exec.get("morning_orders", []) or []
        afternoon_orders = plan_exec.get("afternoon_orders", []) or []
        has_orders = bool(morning_orders or afternoon_orders)
        if has_orders:
            logger.info(
                "phase_signal 未提供订单，已从 trade_plan 回退加载 %d 笔",
                len(morning_orders) + len(afternoon_orders),
            )

    if action not in ("BUILD_PLAN",) and not has_orders:
        logger.info("信号动作 %s, 无建仓订单, 跳过执行", action)
        ctx.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": [],
            "action": action,
            "options_fills": options_fills,
            "options_count": len(options_fills),
        }
        return []

    if not has_orders:
        logger.info("无订单可执行")
        ctx.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": [],
            "options_fills": options_fills,
            "options_count": len(options_fills),
        }
        return []

    # === 模拟盘模式 ===
    if ctx.sim_mode and ctx.sim_engine is not None:
        sim_fills = ctx._execute_sim_mode(signal, morning_orders, afternoon_orders)
        if not ctx.state["phases"]["execute"].get("options_fills"):
            ctx.state["phases"]["execute"]["options_fills"] = options_fills
            ctx.state["phases"]["execute"]["options_count"] = len(options_fills)
        return sim_fills

    # === DRY-RUN 模式 ===
    if ctx.dry_run:
        logger.info("DRY-RUN 模式, 仅生成指令不执行")

        # === 对冲基金视角: 执行算法引擎 (大单拆单计划) ===
        execution_plans: list[dict[str, Any]] = []
        _split_total = 0
        _split_fail = 0
        if ctx.exec_algo_engine is not None:
            try:
                for order in morning_orders + afternoon_orders:
                    shares = int(order.get("shares", 0))
                    est_price = float(order.get("est_price", 0))
                    code = str(order.get("code", ""))
                    notional = shares * est_price
                    if shares >= 5000 or notional >= 200_000:
                        try:
                            _split_total += 1
                            algo_type = ctx.exec_algo_engine.select_algo(
                                total_shares=shares,
                                avg_daily_volume=shares * 20,
                                urgency="normal",
                                volatility=0.02,
                            )
                            plan = ctx.exec_algo_engine.plan_order(
                                algo=algo_type,
                                symbol=code,
                                side=str(order.get("side", "BUY")).upper(),
                                total_shares=shares,
                                duration_minutes=120,
                                slice_minutes=15,
                                current_price=est_price,
                            )
                            saved_path = ctx.exec_algo_engine.save_plan(plan)
                            execution_plans.append(
                                {
                                    "symbol": code,
                                    "algo": algo_type.value,
                                    "slices": len(plan.slices),
                                    "first_slice_shares": (
                                        plan.slices[0].target_shares
                                        if plan.slices
                                        else 0
                                    ),
                                    "last_slice_shares": (
                                        plan.slices[-1].target_shares
                                        if plan.slices
                                        else 0
                                    ),
                                    "est_total_cost": plan.expected_cost,
                                    "est_slippage_bps": plan.expected_slippage_bps,
                                    "plan_path": str(saved_path),
                                }
                            )
                            logger.info(
                                "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                                code,
                                algo_type.value,
                                len(plan.slices),
                                plan.expected_slippage_bps,
                                plan.expected_cost,
                            )
                        except Exception as exc:  # fail-safe
                            _split_fail += 1
                            logger.error(
                                "[ExecAlgo] %s 拆单失败: %s",
                                code,
                                exc,
                                exc_info=True,
                            )
                    else:
                        # G12 修复 (2026-08-06): 小单也估算冲击成本, 裸市价仅限极小单
                        _small_notional = notional
                        _est_slippage_bps = max(
                            2.0, _small_notional / 1_000_000 * 5.0
                        )  # 简化冲击估算
                        if _est_slippage_bps > 10.0:
                            # 冲击成本 > 10bp 的小单也走 TWAP 拆分
                            try:
                                _split_total += 1
                                _plan = ctx.exec_algo_engine.plan_order(
                                    algo="TWAP",
                                    symbol=code,
                                    side=str(order.get("side", "BUY")).upper(),
                                    total_shares=shares,
                                    duration_minutes=30,
                                    slice_minutes=5,
                                    current_price=est_price,
                                )
                                _saved = ctx.exec_algo_engine.save_plan(_plan)
                                execution_plans.append(
                                    {
                                        "symbol": code,
                                        "algo": "TWAP",
                                        "slices": len(_plan.slices),
                                        "first_slice_shares": (
                                            _plan.slices[0].target_shares
                                            if _plan.slices
                                            else 0
                                        ),
                                        "last_slice_shares": (
                                            _plan.slices[-1].target_shares
                                            if _plan.slices
                                            else 0
                                        ),
                                        "est_total_cost": _plan.expected_cost,
                                        "est_slippage_bps": _plan.expected_slippage_bps,
                                        "plan_path": str(_saved),
                                        "small_order_twap": True,
                                    }
                                )
                                logger.info(
                                    "[ExecAlgo] %s 小单TWAP: %d slices (slippage=%.1fbps)",
                                    code,
                                    len(_plan.slices),
                                    _plan.expected_slippage_bps,
                                )
                            except Exception as exc:  # fail-safe
                                _split_fail += 1
                                logger.error(
                                    "[ExecAlgo] %s 小单TWAP失败: %s",
                                    code,
                                    exc,
                                    exc_info=True,
                                )
                        else:
                            execution_plans.append(
                                {
                                    "symbol": code,
                                    "algo": "MARKET",
                                    "slices": 1,
                                    "first_slice_shares": shares,
                                    "last_slice_shares": shares,
                                    "est_total_cost": _small_notional,
                                    "est_slippage_bps": _est_slippage_bps,
                                    "plan_path": None,
                                    "small_order_market": True,
                                }
                            )
                            logger.debug(
                                "[ExecAlgo] %s 小单市价 (notional=%.0f, slippage=%.1fbps)",
                                code,
                                _small_notional,
                                _est_slippage_bps,
                            )
                if _split_total > 0:
                    _fail_rate = _split_fail / _split_total
                    logger.info(
                        "[ExecAlgo] 拆单统计: 成功 %d/%d, 失败 %d (%.1f%%)",
                        _split_total - _split_fail,
                        _split_total,
                        _split_fail,
                        _fail_rate * 100,
                    )
                    if _fail_rate > 0.5:
                        logger.error(
                            "[ExecAlgo] 拆单失败率 %.1f%% > 50%% 阈值, 执行质量降级",
                            _fail_rate * 100,
                        )
                if execution_plans:
                    logger.info("[ExecAlgo] 共生成 %d 个拆单计划", len(execution_plans))
            except Exception as exc:  # fail-safe
                logger.error("[ExecAlgo] 执行算法引擎失败: %s", exc, exc_info=True)

        dry_orders = []
        for order in morning_orders + afternoon_orders:
            dry_orders.append(
                {
                    "symbol": order.get("code", ""),
                    "name": order.get("name", ""),
                    "session": order.get("session", ""),
                    "side": order.get("side", "BUY"),
                    "qty": int(order.get("shares", 0)),
                    "price": float(order.get("est_price", 0)),
                    "limit_price": float(order.get("limit_price", 0)),
                    "amount": float(order.get("est_amount", 0)),
                    "algo": "LIMIT",
                    "status": "DRY_RUN",
                }
            )
        ctx.state["orders"].extend(dry_orders)
        ctx.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": dry_orders,
            "options_fills": options_fills,
            "options_count": len(options_fills),
            "execution_plans": execution_plans,
            "execution_plans_count": len(execution_plans),
            "split_total": _split_total,
            "split_fail": _split_fail,
            "split_failure_rate": (
                (_split_fail / _split_total) if _split_total > 0 else 0.0
            ),
        }
        return dry_orders

    # === 对冲基金视角: 执行算法引擎 (大单拆单计划) ===
    execution_plans: list[dict[str, Any]] = []
    _split_total = 0
    _split_fail = 0
    if ctx.exec_algo_engine is not None:
        try:
            for order in morning_orders + afternoon_orders:
                shares = int(order.get("shares", 0))
                est_price = float(order.get("est_price", 0))
                code = str(order.get("code", ""))
                # 大单阈值: 单笔金额 > 20万 或股数 > 5000 触发拆单
                notional = shares * est_price
                if shares >= 5000 or notional >= 200_000:
                    try:
                        _split_total += 1
                        algo_type = ctx.exec_algo_engine.select_algo(
                            total_shares=shares,
                            avg_daily_volume=shares * 20,  # 估计 ADV
                            urgency="normal",
                            volatility=0.02,
                        )
                        plan = ctx.exec_algo_engine.plan_order(
                            algo=algo_type,
                            symbol=code,
                            side=str(order.get("side", "BUY")).upper(),
                            total_shares=shares,
                            duration_minutes=120,
                            slice_minutes=15,
                            current_price=est_price,
                        )
                        saved_path = ctx.exec_algo_engine.save_plan(plan)
                        execution_plans.append(
                            {
                                "symbol": code,
                                "algo": algo_type.value,
                                "slices": len(plan.slices),
                                "first_slice_shares": (
                                    plan.slices[0].target_shares if plan.slices else 0
                                ),
                                "last_slice_shares": (
                                    plan.slices[-1].target_shares if plan.slices else 0
                                ),
                                "est_total_cost": plan.expected_cost,
                                "est_slippage_bps": plan.expected_slippage_bps,
                                "plan_path": str(saved_path),
                            }
                        )
                        logger.info(
                            "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                            code,
                            algo_type.value,
                            len(plan.slices),
                            plan.expected_slippage_bps,
                            plan.expected_cost,
                        )
                    except Exception as exc:  # fail-safe
                        _split_fail += 1
                        logger.error(
                            "[ExecAlgo] %s 拆单失败: %s", code, exc, exc_info=True
                        )
                else:
                    # G12 修复 (2026-08-06): 小单也估算冲击成本, 裸市价仅限极小单
                    _small_notional = notional
                    _est_slippage_bps = max(2.0, _small_notional / 1_000_000 * 5.0)
                    if _est_slippage_bps > 10.0:
                        try:
                            _split_total += 1
                            _plan = ctx.exec_algo_engine.plan_order(
                                algo="TWAP",
                                symbol=code,
                                side=str(order.get("side", "BUY")).upper(),
                                total_shares=shares,
                                duration_minutes=30,
                                slice_minutes=5,
                                current_price=est_price,
                            )
                            _saved = ctx.exec_algo_engine.save_plan(_plan)
                            execution_plans.append(
                                {
                                    "symbol": code,
                                    "algo": "TWAP",
                                    "slices": len(_plan.slices),
                                    "first_slice_shares": (
                                        _plan.slices[0].target_shares
                                        if _plan.slices
                                        else 0
                                    ),
                                    "last_slice_shares": (
                                        _plan.slices[-1].target_shares
                                        if _plan.slices
                                        else 0
                                    ),
                                    "est_total_cost": _plan.expected_cost,
                                    "est_slippage_bps": _plan.expected_slippage_bps,
                                    "plan_path": str(_saved),
                                    "small_order_twap": True,
                                }
                            )
                            logger.info(
                                "[ExecAlgo] %s 小单TWAP: %d slices (slippage=%.1fbps)",
                                code,
                                len(_plan.slices),
                                _plan.expected_slippage_bps,
                            )
                        except Exception as exc:  # fail-safe
                            _split_fail += 1
                            logger.error(
                                "[ExecAlgo] %s 小单TWAP失败: %s",
                                code,
                                exc,
                                exc_info=True,
                            )
                    else:
                        execution_plans.append(
                            {
                                "symbol": code,
                                "algo": "MARKET",
                                "slices": 1,
                                "first_slice_shares": shares,
                                "last_slice_shares": shares,
                                "est_total_cost": _small_notional,
                                "est_slippage_bps": _est_slippage_bps,
                                "plan_path": None,
                                "small_order_market": True,
                            }
                        )
                        logger.debug(
                            "[ExecAlgo] %s 小单市价 (notional=%.0f, slippage=%.1fbps)",
                            code,
                            _small_notional,
                            _est_slippage_bps,
                        )
            if _split_total > 0:
                _fail_rate = _split_fail / _split_total
                logger.info(
                    "[ExecAlgo] 拆单统计: 成功 %d/%d, 失败 %d (%.1f%%)",
                    _split_total - _split_fail,
                    _split_total,
                    _split_fail,
                    _fail_rate * 100,
                )
                if _fail_rate > 0.5:
                    logger.error(
                        "[ExecAlgo] 拆单失败率 %.1f%% > 50%% 阈值, 执行质量降级",
                        _fail_rate * 100,
                    )
            if execution_plans:
                logger.info("[ExecAlgo] 共生成 %d 个拆单计划", len(execution_plans))
        except Exception as exc:  # fail-safe
            logger.error("[ExecAlgo] 执行算法引擎失败: %s", exc, exc_info=True)

    # === MockBroker 执行 ===
    try:
        from execution.ntp_sync import NTPSync
        from execution.smart_order_router import MockBroker, SmartOrderRouter

        broker = MockBroker(price_dict=dict(ctx.config.MOCK_PRICES))
        ntp = getattr(ctx, "ntp", None) or NTPSync()
        sor = SmartOrderRouter(broker, ntp)

        all_fills: list[dict[str, Any]] = []

        # === 上午批次执行 ===
        logger.info(f"--- 上午批次 {ctx.config.MORNING_WINDOW} ---")
        morning_fills = ctx._execute_order_batch(
            sor, broker, morning_orders, session="morning"
        )
        all_fills.extend(morning_fills)

        # === 单日回撤检查 (上午批次后) ===
        # 若上午批次亏损 > 3%, 暂停下午批次
        morning_amount = sum(f.get("amount", 0) for f in morning_fills)
        logger.info(
            f"上午批次完成: {len(morning_fills)} 笔成交, 金额 {morning_amount:,.0f}"
        )

        # === 下午批次执行 ===
        logger.info(f"--- 下午批次 {ctx.config.AFTERNOON_WINDOW} ---")
        afternoon_fills = ctx._execute_order_batch(
            sor, broker, afternoon_orders, session="afternoon"
        )
        all_fills.extend(afternoon_fills)

        afternoon_amount = sum(f.get("amount", 0) for f in afternoon_fills)
        logger.info(
            f"下午批次完成: {len(afternoon_fills)} 笔成交, 金额 {afternoon_amount:,.0f}"
        )

        # === 订单级汇总（统一报告与 JSON 口径） ===
        order_summary = ctx._aggregate_order_summary(
            morning_orders + afternoon_orders, all_fills
        )

        # === 汇总 ===
        total_amount = morning_amount + afternoon_amount
        logger.info(
            f"执行完成: {len(order_summary)} 笔订单, " f"总金额 {total_amount:,.0f}"
        )

        ctx.state["orders"].extend(all_fills)

        ctx.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": all_fills,
            "order_summary": order_summary,
            "morning_count": len(morning_fills),
            "afternoon_count": len(afternoon_fills),
            "morning_amount": morning_amount,
            "afternoon_amount": afternoon_amount,
            "total_amount": total_amount,
            "options_fills": options_fills,
            "options_count": len(options_fills),
            "execution_plans": execution_plans,
            "execution_plans_count": len(execution_plans),
            "split_total": _split_total,
            "split_fail": _split_fail,
            "split_failure_rate": (
                (_split_fail / _split_total) if _split_total > 0 else 0.0
            ),
        }

        # === 机构级: TCA 交易后成本分析 ===
        if ctx.tca_manager is not None and all_fills:
            try:
                from utils.tca_engine import BenchmarkPrices, FillRecord

                fills_by_symbol: dict[str, list[FillRecord]] = {}
                benchmarks: dict[str, BenchmarkPrices] = {}
                for fill in all_fills:
                    sym = str(fill.get("symbol", fill.get("code", "")))
                    if not sym:
                        continue
                    fr = FillRecord(
                        symbol=sym,
                        side=str(fill.get("side", "BUY")).upper(),
                        shares=int(fill.get("qty", fill.get("shares", 0))),
                        price=float(fill.get("price", 0)),
                        timestamp=ctx.trade_date,
                    )
                    fills_by_symbol.setdefault(sym, []).append(fr)
                    # 决策价 = 限价, 到达价 = 成交价 (MockBroker)
                    exec_price = float(fill.get("price", 0))
                    benchmarks[sym] = BenchmarkPrices(
                        decision_price=exec_price,
                        arrival_price=exec_price,
                        vwap=exec_price,
                        close_price=exec_price,
                    )
                tca_reports = ctx.tca_manager.analyze_batch(
                    fills_by_symbol=fills_by_symbol,
                    benchmarks=benchmarks,
                )
                tca_summary = ctx.tca_manager.summarize(tca_reports)
                ctx.state["phases"]["execute"]["tca_summary"] = tca_summary
                ctx.state["phases"]["execute"]["tca_reports"] = {
                    sym: {
                        "grade": r.quality_grade,
                        "is_cost_bps": r.is_cost_bps,
                        "vwap_deviation_bps": r.vwap_deviation_bps,
                        "fill_rate": r.fill_rate,
                        "issues": r.issues,
                    }
                    for sym, r in tca_reports.items()
                }
                logger.info(
                    "[TCA] %d 笔成交分析完成: avg IS=%.1fbps, avg VWAP dev=%.1fbps, fill_rate=%.1f%%",
                    tca_summary.get("n_orders", 0),
                    tca_summary.get("avg_is_cost_bps", 0),
                    tca_summary.get("avg_vwap_deviation_bps", 0),
                    tca_summary.get("avg_fill_rate", 0) * 100,
                )
            except Exception as exc:  # fail-safe
                logger.error("[TCA] 分析失败: %s", exc, exc_info=True)

        # === 执行层: 执行算法 + 市场冲击 + 智能路由 ===
        if EXECUTION_MODULES_READY and ctx.execution_algo_engine is not None:
            try:
                import numpy as _np_exec
                import pandas as _pd_exec

                # 为每笔成交生成执行计划与冲击估计
                exec_plans_summary: list[dict[str, Any]] = []
                impact_estimates: list[dict[str, Any]] = []
                routing_decisions: list[dict[str, Any]] = []

                for fill in all_fills:
                    sym = str(fill.get("symbol", fill.get("code", "")))
                    side = str(fill.get("side", "BUY")).upper()
                    # 字段兼容: qty (MockBroker) / filled_shares / shares
                    shares = float(
                        fill.get(
                            "qty", fill.get("filled_shares", fill.get("shares", 0))
                        )
                        or 0
                    )
                    price = float(fill.get("price", 0) or 0)

                    if shares <= 0 or not sym:
                        continue

                    # ADV 代理: 用成交股数 × 10 (假设)
                    adv_proxy = max(shares * 10, 100_000.0)

                    # 1) 市场冲击估计
                    if ctx.market_impact_model is not None:
                        impact_est = ctx.market_impact_model.estimate(
                            symbol=sym,
                            order_shares=shares,
                            adv=adv_proxy,
                            decision_price=price,
                            volatility=0.02,
                            execution_time_days=1.0,
                        )
                        impact_estimates.append(
                            {
                                "symbol": sym,
                                "order_shares": shares,
                                "adv": adv_proxy,
                                "participation_rate": impact_est.participation_rate,
                                "total_impact_bps": impact_est.total_impact_bps,
                                "temporary_impact_bps": impact_est.temporary_impact_bps,
                                "permanent_impact_bps": impact_est.permanent_impact_bps,
                                "expected_exec_price": impact_est.expected_exec_price,
                                "model": impact_est.model_used,
                            }
                        )

                    # 2) 执行算法选择 (自动)
                    if ctx.execution_algo_engine is not None:
                        try:
                            from utils.execution_algorithm_engine import (
                                Order as ExecOrder,
                            )

                            # 构造 ExecOrder (start/end 用今日 9:30-15:00)
                            today = _pd_exec.Timestamp.now().normalize()
                            exec_order = ExecOrder(
                                symbol=sym,
                                side=side,
                                total_shares=shares,
                                start_time=today
                                + _pd_exec.Timedelta(hours=9, minutes=30),
                                end_time=today
                                + _pd_exec.Timedelta(hours=15, minutes=0),
                                benchmark_price=price,
                                urgency="MEDIUM",
                            )
                            # 自动选算法
                            algo_name = ctx.execution_algo_engine.select_algorithm(
                                order=exec_order,
                                adv=adv_proxy,
                                volatility=0.02,
                            )
                            # 生成计划
                            if algo_name == "VWAP":
                                plan = ctx.execution_algo_engine.vwap(exec_order)
                            elif algo_name == "TWAP":
                                plan = ctx.execution_algo_engine.twap(exec_order)
                            elif algo_name == "POV":
                                plan = ctx.execution_algo_engine.pov(
                                    exec_order, expected_market_volume=adv_proxy
                                )
                            elif algo_name == "IS":
                                plan = ctx.execution_algo_engine.is_algo(
                                    exec_order, daily_volatility=0.02
                                )
                            else:
                                plan = ctx.execution_algo_engine.vwap(exec_order)

                            plan_summary = ctx.execution_algo_engine.summarize_plan(
                                plan
                            )
                            plan_summary["selected_by"] = "auto"
                            exec_plans_summary.append(plan_summary)
                        except Exception as ex_inner:  # fail-safe
                            logger.debug(
                                "[ExecAlgo] %s 计划生成失败: %s", sym, ex_inner
                            )

                    # 3) 智能路由决策
                    if ctx.smart_order_router_inst is not None:
                        try:
                            routing = ctx.smart_order_router_inst.route(
                                symbol=sym,
                                side=side,
                                total_shares=shares,
                                order_books=None,  # 无盘口时用场所默认评分
                                strategy="SMART",
                                max_venues=2,
                            )
                            routing_decisions.append(
                                ctx.smart_order_router_inst.summarize_decision(routing)
                            )
                        except Exception as ex_router:  # fail-safe
                            logger.debug(
                                "[SmartRouter] %s 路由失败: %s", sym, ex_router
                            )

                if exec_plans_summary:
                    ctx.state["phases"]["execute"][
                        "execution_plans"
                    ] = exec_plans_summary
                    avg_cost_bps = float(
                        _np_exec.mean(
                            [p.get("expected_cost_bps", 0) for p in exec_plans_summary]
                        )
                    )
                    logger.info(
                        "[ExecAlgo] %d 笔执行计划生成: avg预期成本=%.2fbps, 平均切片数=%.1f",
                        len(exec_plans_summary),
                        avg_cost_bps,
                        float(
                            _np_exec.mean(
                                [p.get("num_slices", 0) for p in exec_plans_summary]
                            )
                        ),
                    )

                if impact_estimates:
                    ctx.state["phases"]["execute"][
                        "impact_estimates"
                    ] = impact_estimates
                    avg_impact_bps = float(
                        _np_exec.mean([e["total_impact_bps"] for e in impact_estimates])
                    )
                    logger.info(
                        "[MarketImpact] %d 笔冲击估计: avg总冲击=%.2fbps, avg参与度=%.4f",
                        len(impact_estimates),
                        avg_impact_bps,
                        float(
                            _np_exec.mean(
                                [e["participation_rate"] for e in impact_estimates]
                            )
                        ),
                    )

                if routing_decisions:
                    ctx.state["phases"]["execute"][
                        "routing_decisions"
                    ] = routing_decisions
                    primary_venues = [
                        r.get("primary_venue", "") for r in routing_decisions
                    ]
                    logger.info(
                        "[SmartRouter] %d 笔路由决策: 主场所分布=%s",
                        len(routing_decisions),
                        dict((v, primary_venues.count(v)) for v in set(primary_venues)),
                    )
            except Exception as exc:  # fail-safe
                logger.error(
                    "[ExecutionModules] 执行层分析失败: %s", exc, exc_info=True
                )

        return all_fills

    except Exception as e:  # fail-safe
        logger.error(f"执行失败: {e}", exc_info=True)
        ctx.state["phases"]["execute"] = {
            "status": "FAIL",
            "error": str(e),
            "options_fills": options_fills,
            "options_count": len(options_fills),
            "execution_plans": execution_plans,
            "execution_plans_count": len(execution_plans),
            "split_total": _split_total,
            "split_fail": _split_fail,
            "split_failure_rate": (
                (_split_fail / _split_total) if _split_total > 0 else 0.0
            ),
        }
        return []
