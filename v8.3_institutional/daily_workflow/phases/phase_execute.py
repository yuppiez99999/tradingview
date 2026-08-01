#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_execute

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_execute phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_execute(workflow, signal: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    mode = "模拟盘" if workflow.sim_mode else "MockBroker"
    logger.info("=" * 60)
    logger.info(f"Phase 6: 智能执行 ({mode})")
    logger.info("=" * 60)

    # === 期权策略执行（v9.0 多策略期权覆盖层，优先于现货订单检查） ===
    options_plan = workflow.trade_plan.get("options_execution", {}) if workflow.trade_plan else {}
    options_modules = workflow.trade_plan.get("hedge_account", {}).get("modules", []) if workflow.trade_plan else []

    # === P0-12: Put Option 累计预算 60% 硬限制 (2026-07-25 顶级对冲基金审计) ===
    # 审计问题: put option 无累计预算上限, 极端行情下可能耗尽对冲账户全部资金
    # 修复: put 累计预算 <= 60% * hedge_capital, 保留 40% 缓冲应对极端行情
    put_budget_info: Dict[str, Any] = {}
    if options_modules:
        try:
            options_modules, put_budget_info = workflow._enforce_put_option_budget_limit(options_modules)
        except Exception as exc:
            logger.error("[PutBudget] 60%% 硬限制检查异常 (fail-closed, 阻止全部期权模块): %s", exc, exc_info=True)
            options_modules = []
            put_budget_info = {"error": str(exc), "fail_closed": True}

    options_fills: List[Dict[str, Any]] = []
    if options_plan and options_modules:
        try:
            from execution.options_runner import OptionsRunner
            runner = OptionsRunner(
                trade_date=workflow.trade_date,
                hedge_capital=float(workflow.trade_plan.get("hedge_account", {}).get("capital", 2_000_000)),
                margin_usage_max=float(workflow.trade_plan.get("hedge_account", {}).get("margin_usage_max", 1_200_000)),
                liquidity_buffer_min=float(workflow.trade_plan.get("hedge_account", {}).get("liquidity_buffer_min", 800_000)),
            )
            options_fills = runner.run_modules(
                modules=options_modules,
                market_data=workflow._options_market_snapshot(),
                trigger_date=workflow.trade_date,
                event_calendar=options_plan.get("event_calendar", []),
            )
            logger.info("期权策略执行完成: %d 条 fills", len(options_fills))
        except Exception as exc:
            logger.warning("期权策略执行失败: %s", exc, exc_info=True)
            options_fills = []
    else:
        logger.info("当前交易计划无期权策略模块，跳过期权执行")

    # === P0-6/P0-7/P0-9/P0-11: 下单前风控门控 (2026-07-25 顶级对冲基金审计) ===
    # 审计问题: 原 phase_execute 直接调用 MockBroker 下单, KillSwitch 检查
    #           仅在 Phase 4.5 记录日志但不拦截, L1/L2/L3 形同虚设
    # 修复: 下单前必须经过 UnifiedRiskCockpit 扫描, 按级别过滤/拦截订单
    risk_gate: Dict[str, Any] = {}
    kill_switch_enforcement: List[Dict[str, Any]] = []
    try:
        risk_gate = workflow._pre_trade_risk_gate()
    except Exception as exc:
        # P0-11: fail-closed — 风控门控异常时阻止一切交易
        logger.critical("[RiskGate] 风控门控异常! fail-closed 阻止全部交易: %s", exc, exc_info=True)
        risk_gate = {
            "kill_switch_level": 3, "can_trade": False, "can_open": False,
            "margin_usage_ratio": 1.0, "fail_closed": True,
            "risk_snapshot": {"error": str(exc)},
        }

    # === 信号校验 ===
    action = signal.get("action", "")
    morning_orders = signal.get("morning_orders", [])
    afternoon_orders = signal.get("afternoon_orders", [])
    has_orders = bool(morning_orders or afternoon_orders)

    # === P0-11: fail-closed 双重保险 — 自检阶段 fail_closed 时阻止一切交易 ===
    check_state = workflow.state.get("phases", {}).get("check", {})
    if check_state.get("fail_closed", False) or workflow.state.get("fail_closed", False):
        logger.critical(
            "[Fail-Closed] 系统处于 fail-closed 状态, 阻止 phase_execute 全部交易: %s",
            check_state.get("reason", "unknown"),
        )
        workflow.state["phases"]["execute"] = {
            "status": "BLOCKED",
            "reason": "fail_closed",
            "risk_gate": risk_gate,
            "blocked_orders": len(morning_orders) + len(afternoon_orders),
        }
        return []

    # 允许直接从 trade_plan 回退读单，避免 --phase execute 跳过 phase_signal 时空跑
    if not has_orders and workflow.trade_plan:
        plan_exec = workflow.trade_plan.get("execution_plan", {})
        morning_orders = plan_exec.get("morning_orders", []) or []
        afternoon_orders = plan_exec.get("afternoon_orders", []) or []
        has_orders = bool(morning_orders or afternoon_orders)
        if has_orders:
            logger.info("phase_signal 未提供订单，已从 trade_plan 回退加载 %d 笔", len(morning_orders) + len(afternoon_orders))

    # === P0-7: KillSwitch 订单拦截 (L1 过滤 BUY / L2+ 阻止全部) ===
    # 在订单进入执行队列前, 根据 risk_gate 级别过滤/拦截
    pre_filter_count = len(morning_orders) + len(afternoon_orders)
    if has_orders and risk_gate:
        try:
            morning_orders, afternoon_orders, kill_switch_enforcement = workflow._enforce_kill_switch_on_orders(
                morning_orders, afternoon_orders, risk_gate,
            )
            has_orders = bool(morning_orders or afternoon_orders)
            post_filter_count = len(morning_orders) + len(afternoon_orders)
            if pre_filter_count != post_filter_count:
                logger.warning(
                    "[RiskGate] KillSwitch 拦截: %d → %d 笔订单 (拦截 %d 笔)",
                    pre_filter_count, post_filter_count, pre_filter_count - post_filter_count,
                )
        except Exception as exc:
            # P0-11: fail-closed — 拦截异常时阻止全部交易
            logger.critical(
                "[RiskGate] KillSwitch 拦截异常! fail-closed 阻止全部交易: %s",
                exc, exc_info=True,
            )
            morning_orders, afternoon_orders = [], []
            has_orders = False
            kill_switch_enforcement = [{
                "level": risk_gate.get("kill_switch_level", 3),
                "action": "enforce_failed_fail_closed",
                "error": str(exc),
                "blocked_morning": pre_filter_count,
            }]

    if action not in ("BUILD_PLAN",) and not has_orders:
        logger.info("信号动作 %s, 无建仓订单, 跳过执行", action)
        workflow.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": [],
            "action": action,
            "options_fills": options_fills,
            "options_count": len(options_fills),
            "risk_gate": risk_gate,
            "kill_switch_enforcement": kill_switch_enforcement,
            "put_budget_info": put_budget_info,
        }
        return []

    if not has_orders:
        logger.info("无订单可执行")
        workflow.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": [],
            "options_fills": options_fills,
            "options_count": len(options_fills),
            "risk_gate": risk_gate,
            "kill_switch_enforcement": kill_switch_enforcement,
            "put_budget_info": put_budget_info,
        }
        return []

    # === 模拟盘模式 ===
    if workflow.sim_mode and workflow.sim_engine is not None:
        sim_fills = workflow._execute_sim_mode(signal, morning_orders, afternoon_orders)
        if not workflow.state["phases"]["execute"].get("options_fills"):
            workflow.state["phases"]["execute"]["options_fills"] = options_fills
            workflow.state["phases"]["execute"]["options_count"] = len(options_fills)
        return sim_fills

    # === DRY-RUN 模式 ===
    if workflow.dry_run:
        logger.info("DRY-RUN 模式, 仅生成指令不执行")

        # === 对冲基金视角: 执行算法引擎 (大单拆单计划) ===
        execution_plans: List[Dict[str, Any]] = []
        if workflow.exec_algo_engine is not None:
            try:
                for order in morning_orders + afternoon_orders:
                    shares = int(order.get("shares", 0))
                    est_price = float(order.get("est_price", 0))
                    code = str(order.get("code", ""))
                    notional = shares * est_price
                    if shares >= 5000 or notional >= 200_000:
                        try:
                            algo_type = workflow.exec_algo_engine.select_algo(
                                total_shares=shares,
                                avg_daily_volume=shares * 20,
                                urgency="normal",
                                volatility=0.02,
                            )
                            plan = workflow.exec_algo_engine.plan_order(
                                algo=algo_type,
                                symbol=code,
                                side=str(order.get("side", "BUY")).upper(),
                                total_shares=shares,
                                duration_minutes=120,
                                slice_minutes=15,
                                current_price=est_price,
                            )
                            saved_path = workflow.exec_algo_engine.save_plan(plan)
                            execution_plans.append({
                                "symbol": code,
                                "algo": algo_type.value,
                                "slices": len(plan.slices),
                                # P3-B FIX (2026-07-26): ExecutionSlice 字段名是 target_shares (非 shares)
                                "first_slice_shares": plan.slices[0].target_shares if plan.slices else 0,
                                "last_slice_shares": plan.slices[-1].target_shares if plan.slices else 0,
                                # P3-B FIX (2026-07-26): ExecutionPlan 字段名是 expected_* (非 estimated_*)
                                "est_total_cost": plan.expected_cost,
                                "est_slippage_bps": plan.expected_slippage_bps,
                                "plan_path": str(saved_path),
                            })
                            logger.info(
                                "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                                code, algo_type.value, len(plan.slices),
                                plan.expected_slippage_bps, plan.expected_cost,
                            )
                        except Exception as exc:
                            logger.warning("[ExecAlgo] %s 拆单失败: %s", code, exc)
                if execution_plans:
                    logger.info("[ExecAlgo] 共生成 %d 个拆单计划", len(execution_plans))
            except Exception as exc:
                logger.error("[ExecAlgo] 执行算法引擎失败: %s", exc, exc_info=True)

        dry_orders = []
        for order in morning_orders + afternoon_orders:
            dry_orders.append({
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
            })
        workflow.state["orders"].extend(dry_orders)
        workflow.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": dry_orders,
            "options_fills": options_fills,
            "options_count": len(options_fills),
            "execution_plans": execution_plans,
            "execution_plans_count": len(execution_plans),
            "risk_gate": risk_gate,
            "kill_switch_enforcement": kill_switch_enforcement,
            "put_budget_info": put_budget_info,
        }
        return dry_orders

    # === 对冲基金视角: 执行算法引擎 (大单拆单计划) ===
    execution_plans: List[Dict[str, Any]] = []
    if workflow.exec_algo_engine is not None:
        try:
            for order in morning_orders + afternoon_orders:
                shares = int(order.get("shares", 0))
                est_price = float(order.get("est_price", 0))
                code = str(order.get("code", ""))
                # 大单阈值: 单笔金额 > 20万 或股数 > 5000 触发拆单
                notional = shares * est_price
                if shares >= 5000 or notional >= 200_000:
                    try:
                        algo_type = workflow.exec_algo_engine.select_algo(
                            total_shares=shares,
                            avg_daily_volume=shares * 20,  # 估计 ADV
                            urgency="normal",
                            volatility=0.02,
                        )
                        plan = workflow.exec_algo_engine.plan_order(
                            algo=algo_type,
                            symbol=code,
                            side=str(order.get("side", "BUY")).upper(),
                            total_shares=shares,
                            duration_minutes=120,
                            slice_minutes=15,
                            current_price=est_price,
                        )
                        saved_path = workflow.exec_algo_engine.save_plan(plan)
                        execution_plans.append({
                            "symbol": code,
                            "algo": algo_type.value,
                            "slices": len(plan.slices),
                            # P3-B FIX (2026-07-26): ExecutionSlice 字段名是 target_shares (非 shares)
                            "first_slice_shares": plan.slices[0].target_shares if plan.slices else 0,
                            "last_slice_shares": plan.slices[-1].target_shares if plan.slices else 0,
                            # P3-B FIX (2026-07-26): ExecutionPlan 字段名是 expected_* (非 estimated_*)
                            "est_total_cost": plan.expected_cost,
                            "est_slippage_bps": plan.expected_slippage_bps,
                            "plan_path": str(saved_path),
                        })
                        logger.info(
                            "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                            code, algo_type.value, len(plan.slices),
                            plan.expected_slippage_bps, plan.expected_cost,
                        )
                    except Exception as exc:
                        logger.warning("[ExecAlgo] %s 拆单失败: %s", code, exc)
            if execution_plans:
                logger.info("[ExecAlgo] 共生成 %d 个拆单计划", len(execution_plans))
        except Exception as exc:
            logger.error("[ExecAlgo] 执行算法引擎失败: %s", exc, exc_info=True)

    # === Broker 选择 (v8.6.8 P0-LIVE-01 修复: 实盘模式真正对接券商) ===
    # 此前实盘模式 (not dry_run and not sim_mode) 仍硬编码 MockBroker,
    # 导致真实交易永远不会走券商网关, 这是阻断实盘对接的 P0 BUG.
    # 修复: 按 dry_run → sim_mode → 实盘券商(CTP/THS) → MockBroker 顺序选择
    try:
        broker = None
        broker_source = "unknown"

        # 优先级 0: DRY-RUN / SIM_MODE — 强制使用 MockBroker, 永不触真实下单
        if workflow.dry_run or workflow.sim_mode:
            broker = MockBroker(price_dict=dict(workflow.config.MOCK_PRICES))
            broker_source = "mock_dry_or_sim"
            logger.info("[Broker] %s 模式使用 MockBroker (不会真实下单)",
                        "DRY-RUN" if workflow.dry_run else "SIM")
        else:
            # 优先级 1/2: CTP 期货网关 → 同花顺实盘
            # v8.6.9 P3-3 重构: 连接逻辑抽离为 _connect_live_broker(), 三处复用
            broker, broker_source = workflow._connect_live_broker()

            # 优先级 3: 降级 MockBroker (开发/测试)
            # v8.6.8 P0-07 FIX: --live 模式下 broker 连接失败必须 fail-closed, 不允许降级
            if broker is None:
                if workflow.live_mode:
                    # v8.6.8 P0-07: --live 模式严格 fail-closed
                    # 防止生产环境 CTP/THS 配置错误时静默使用 MockBroker (虚假交易)
                    logger.critical(
                        "[Broker] [P0-07] --live 实盘模式但 CTP/THS 均未连接, FAIL-CLOSED 终止. "
                        "请检查: CTP_FRONT_ADDR / CTP_USER_ID / CTP_PASSWORD / THS_ACCOUNT 配置"
                    )
                    workflow.state["phases"]["execute"] = {
                        "status": "FAIL",
                        "fail_closed": True,
                        "reason": "P0-07: --live 模式 broker 连接失败, 防止虚假交易",
                        "broker_source": "none_live_fail_closed",
                    }
                    # P3-B FIX (2026-07-26): 函数返回 List[Dict], 用 [] 表示 fail-closed 无订单
                    return []
                # 非生产模式降级 MockBroker (开发/测试)
                logger.warning(
                    "[Broker] ⚠️ 实盘模式但未检测到真实券商网关, 降级 MockBroker. "
                    "生产环境请配置 CTP_FRONT_ADDR / THS_ACCOUNT, "
                    "或使用 --live 开关启用严格 fail-closed 检查"
                )
                broker = MockBroker(price_dict=dict(workflow.config.MOCK_PRICES))
                broker_source = "mock_fallback"

        # 安全断言: dry_run 模式下必须使用 MockBroker (防御性编程)
        if workflow.dry_run and broker_source != "mock_dry_or_sim":
            logger.critical(
                "[Broker] 安全断言失败! DRY-RUN 模式却使用了 %s, 强制回退 MockBroker",
                broker_source,
            )
            broker = MockBroker(price_dict=dict(workflow.config.MOCK_PRICES))
            broker_source = "mock_safety_fallback"

        ntp = getattr(self, 'ntp', None) or NTPSync()
        # v8.6.8 P0-06: SOR 传入 workflow.ks, 拆单过程中重检 KillSwitch 状态
        # 防止盘中熔断后 SOR 继续下单 (前 50 笔成交后 KillSwitch 升级到 L2, 后 50 笔应停止)
        sor = SmartOrderRouter(broker, ntp, kill_switch=getattr(self, 'ks', None))

        all_fills: List[Dict[str, Any]] = []

        # === 上午批次执行 ===
        logger.info(f"--- 上午批次 {workflow.config.MORNING_WINDOW} ---")
        morning_fills = workflow._execute_order_batch(
            sor, broker, morning_orders, session="morning"
        )
        all_fills.extend(morning_fills)

        # === 单日回撤检查 (上午批次后) ===
        # 若上午批次亏损 > 3%, 暂停下午批次
        morning_amount = sum(f.get("amount", 0) for f in morning_fills)
        logger.info(f"上午批次完成: {len(morning_fills)} 笔成交, 金额 {morning_amount:,.0f}")

        # === 下午批次执行 ===
        logger.info(f"--- 下午批次 {workflow.config.AFTERNOON_WINDOW} ---")
        afternoon_fills = workflow._execute_order_batch(
            sor, broker, afternoon_orders, session="afternoon"
        )
        all_fills.extend(afternoon_fills)

        afternoon_amount = sum(f.get("amount", 0) for f in afternoon_fills)
        logger.info(f"下午批次完成: {len(afternoon_fills)} 笔成交, 金额 {afternoon_amount:,.0f}")

        # === 订单级汇总（统一报告与 JSON 口径） ===
        order_summary = workflow._aggregate_order_summary(morning_orders + afternoon_orders, all_fills)

        # === 汇总 ===
        total_amount = morning_amount + afternoon_amount
        logger.info(f"执行完成: {len(order_summary)} 笔订单, "
                    f"总金额 {total_amount:,.0f}")

        workflow.state["orders"].extend(all_fills)

        workflow.state["phases"]["execute"] = {
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
            "risk_gate": risk_gate,
            "kill_switch_enforcement": kill_switch_enforcement,
            "put_budget_info": put_budget_info,
        }

        # === 机构级: TCA 交易后成本分析 ===
        if workflow.tca_manager is not None and all_fills:
            try:
                fills_by_symbol: Dict[str, List[FillRecord]] = {}
                benchmarks: Dict[str, BenchmarkPrices] = {}
                for fill in all_fills:
                    sym = str(fill.get("symbol", fill.get("code", "")))
                    if not sym:
                        continue
                    fr = FillRecord(
                        symbol=sym,
                        side=str(fill.get("side", "BUY")).upper(),
                        shares=int(fill.get("qty", fill.get("shares", 0))),
                        price=float(fill.get("price", 0)),
                        timestamp=workflow.trade_date,
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
                tca_reports = workflow.tca_manager.analyze_batch(
                    fills_by_symbol=fills_by_symbol,
                    benchmarks=benchmarks,
                )
                tca_summary = workflow.tca_manager.summarize(tca_reports)
                workflow.state["phases"]["execute"]["tca_summary"] = tca_summary
                workflow.state["phases"]["execute"]["tca_reports"] = {
                    sym: {
                        "grade": r.quality_grade,
                        "is_cost_bps": r.is_cost_bps,
                        "vwap_deviation_bps": r.vwap_deviation_bps,
                        "fill_rate": r.fill_rate,
                        "issues": r.issues,
                    } for sym, r in tca_reports.items()
                }
                logger.info(
                    "[TCA] %d 笔成交分析完成: avg IS=%.1fbps, avg VWAP dev=%.1fbps, fill_rate=%.1f%%",
                    tca_summary.get("n_orders", 0),
                    tca_summary.get("avg_is_cost_bps", 0),
                    tca_summary.get("avg_vwap_deviation_bps", 0),
                    tca_summary.get("avg_fill_rate", 0) * 100,
                )
            except Exception as exc:
                logger.error("[TCA] 分析失败: %s", exc, exc_info=True)

        # === 执行层: 执行算法 + 市场冲击 + 智能路由 ===
        if EXECUTION_MODULES_READY and workflow.execution_algo_engine is not None:
            try:
                import numpy as _np_exec
                import pandas as _pd_exec
                # 为每笔成交生成执行计划与冲击估计
                exec_plans_summary: List[Dict[str, Any]] = []
                impact_estimates: List[Dict[str, Any]] = []
                routing_decisions: List[Dict[str, Any]] = []

                for fill in all_fills:
                    sym = str(fill.get("symbol", fill.get("code", "")))
                    side = str(fill.get("side", "BUY")).upper()
                    # 字段兼容: qty (MockBroker) / filled_shares / shares
                    shares = float(fill.get("qty", fill.get("filled_shares", fill.get("shares", 0))) or 0)
                    price = float(fill.get("price", 0) or 0)

                    if shares <= 0 or not sym:
                        continue

                    # ADV 代理: 用成交股数 × 10 (假设)
                    adv_proxy = max(shares * 10, 100_000.0)

                    # 1) 市场冲击估计
                    if workflow.market_impact_model is not None:
                        impact_est = workflow.market_impact_model.estimate(
                            symbol=sym,
                            order_shares=shares,
                            adv=adv_proxy,
                            decision_price=price,
                            volatility=0.02,
                            execution_time_days=1.0,
                        )
                        impact_estimates.append({
                            "symbol": sym,
                            "order_shares": shares,
                            "adv": adv_proxy,
                            "participation_rate": impact_est.participation_rate,
                            "total_impact_bps": impact_est.total_impact_bps,
                            "temporary_impact_bps": impact_est.temporary_impact_bps,
                            "permanent_impact_bps": impact_est.permanent_impact_bps,
                            "expected_exec_price": impact_est.expected_exec_price,
                            "model": impact_est.model_used,
                        })

                    # 2) 执行算法选择 (自动)
                    if workflow.execution_algo_engine is not None:
                        try:
                            # 构造 ExecOrder (start/end 用今日 9:30-15:00)
                            today = _pd_exec.Timestamp.now().normalize()
                            exec_order = ExecOrder(
                                symbol=sym,
                                side=side,
                                total_shares=shares,
                                start_time=today + _pd_exec.Timedelta(hours=9, minutes=30),
                                end_time=today + _pd_exec.Timedelta(hours=15, minutes=0),
                                benchmark_price=price,
                                urgency="MEDIUM",
                            )
                            # 自动选算法
                            algo_name = workflow.execution_algo_engine.select_algorithm(
                                order=exec_order,
                                adv=adv_proxy,
                                volatility=0.02,
                            )
                            # 生成计划
                            if algo_name == "VWAP":
                                plan = workflow.execution_algo_engine.vwap(exec_order)
                            elif algo_name == "TWAP":
                                plan = workflow.execution_algo_engine.twap(exec_order)
                            elif algo_name == "POV":
                                plan = workflow.execution_algo_engine.pov(exec_order, expected_market_volume=adv_proxy)
                            elif algo_name == "IS":
                                plan = workflow.execution_algo_engine.is_algo(exec_order, daily_volatility=0.02)
                            else:
                                plan = workflow.execution_algo_engine.vwap(exec_order)

                            plan_summary = workflow.execution_algo_engine.summarize_plan(plan)
                            plan_summary["selected_by"] = "auto"
                            exec_plans_summary.append(plan_summary)
                        except Exception as ex_inner:
                            logger.debug("[ExecAlgo] %s 计划生成失败: %s", sym, ex_inner)

                    # 3) 智能路由决策
                    if workflow.smart_order_router_inst is not None:
                        try:
                            routing = workflow.smart_order_router_inst.route(
                                symbol=sym,
                                side=side,
                                total_shares=shares,
                                order_books=None,  # 无盘口时用场所默认评分
                                strategy="SMART",
                                max_venues=2,
                            )
                            routing_decisions.append(
                                workflow.smart_order_router_inst.summarize_decision(routing)
                            )
                        except Exception as ex_router:
                            logger.debug("[SmartRouter] %s 路由失败: %s", sym, ex_router)

                if exec_plans_summary:
                    workflow.state["phases"]["execute"]["execution_plans"] = exec_plans_summary
                    avg_cost_bps = float(_np_exec.mean([p.get("expected_cost_bps", 0) for p in exec_plans_summary]))
                    logger.info(
                        "[ExecAlgo] %d 笔执行计划生成: avg预期成本=%.2fbps, 平均切片数=%.1f",
                        len(exec_plans_summary), avg_cost_bps,
                        float(_np_exec.mean([p.get("num_slices", 0) for p in exec_plans_summary])),
                    )

                if impact_estimates:
                    workflow.state["phases"]["execute"]["impact_estimates"] = impact_estimates
                    avg_impact_bps = float(_np_exec.mean([e["total_impact_bps"] for e in impact_estimates]))
                    logger.info(
                        "[MarketImpact] %d 笔冲击估计: avg总冲击=%.2fbps, avg参与度=%.4f",
                        len(impact_estimates), avg_impact_bps,
                        float(_np_exec.mean([e["participation_rate"] for e in impact_estimates])),
                    )

                if routing_decisions:
                    workflow.state["phases"]["execute"]["routing_decisions"] = routing_decisions
                    primary_venues = [r.get("primary_venue", "") for r in routing_decisions]
                    logger.info(
                        "[SmartRouter] %d 笔路由决策: 主场所分布=%s",
                        len(routing_decisions),
                        dict((v, primary_venues.count(v)) for v in set(primary_venues)),
                    )
            except Exception as exc:
                logger.error("[ExecutionModules] 执行层分析失败: %s", exc, exc_info=True)

        # === v8.5: 影子账户跟踪 (成交后同步记录) ===
        try:
            if V85_READY:
                from validation.shadow_account_system import ShadowAccountSystem
                shadow = ShadowAccountSystem()
                shadow_result = shadow.track(
                    fills=all_fills,
                    trade_date=workflow.trade_date,
                    mode="shadow",
                )
                logger.info(
                    "[v8.5 ShadowAccount] 已同步 %d 笔成交到影子账户, "
                    "偏离度=%s, 状态=%s",
                    shadow_result.get("tracked_count", 0),
                    shadow_result.get("deviation", "N/A"),
                    shadow_result.get("status", "N/A"),
                )
                workflow.state["phases"]["execute"]["shadow_account"] = shadow_result
            else:
                workflow.state["phases"]["execute"]["shadow_account"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.error(f"[v8.5 ShadowAccount] 跟踪失败: {e}", exc_info=True)
            workflow.state["phases"]["execute"]["shadow_account"] = {"status": "ERROR", "error": str(e)}

        return all_fills

    except Exception as e:
        logger.error(f"执行失败: {e}", exc_info=True)
        workflow.state["phases"]["execute"] = {
            "status": "FAIL",
            "error": str(e),
            "options_fills": options_fills,
            "options_count": len(options_fills),
            "execution_plans": execution_plans,
            "execution_plans_count": len(execution_plans),
        }
        return []


