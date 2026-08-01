#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_risk

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_risk phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_risk(workflow) -> Dict[str, Any]:
    """风险预算计算 — 2026 年交易计划组合级别

    基于 `2026年交易计划.md` 的资金配置:
    - 总资金 500 万 = 股票/ETF 300 万 (60%) + 对冲 200 万 (40%)
    - 4 阶段建仓: P1 35% / P2 30% / P3 20% / P4 15%
    - 三层风控: 黄色 6% / 橙色 8% / 红色 12%
    - VaR 预算: 95% < 5%, 99% < 8%
    """
    # 懒初始化（支持单独运行该 phase）
    if not hasattr(self, "rm"):
        try:
            from risk.risk_manager import RiskManager
            workflow.rm = RiskManager(total_capital=workflow.capital)
        except Exception as e:
            logger.warning(f"RiskManager 初始化失败，使用模拟模式: {e}")
            workflow.rm = None
    logger.info("=" * 60)
    logger.info("Phase 3: 风险预算计算 (组合级别 — 500万 4阶段)")
    logger.info("=" * 60)

    # 当前组合净值 (建仓前为现金状态)
    current_equity = workflow.capital
    dd_status = workflow.rm.update_drawdown(current_equity)

    # === 从交易计划读取当日资金配置 ===
    plan_phase = workflow.trade_plan.get("phase", {})
    plan_exec = workflow.trade_plan.get("execution_plan", {})
    day_capital = float(plan_phase.get("day_capital", 0))
    morning_total = float(plan_exec.get("morning_total", 0))
    afternoon_total = float(plan_exec.get("afternoon_total", 0))
    grand_total = float(plan_exec.get("grand_total", 0))

    # === 资金配置明细 ===
    stock_capital = workflow.config.STOCK_CAPITAL
    hedge_capital = workflow.config.HEDGE_CAPITAL

    # === 风险预算计算 (简化版, 实盘应从 RiskManager 获取) ===
    # 单日建仓资金占股票组合的比例
    build_ratio = day_capital / stock_capital if stock_capital > 0 else 0

    # 个股止损 (取中风险 -12% 作为组合止损参考)
    portfolio_stop = workflow.config.STOP_LOSS_RULES["科技股"]

    # VaR 预算 (使用配置上限)
    var_95_limit = 0.05
    var_99_limit = 0.08

    logger.info(f"组合净值: {current_equity:,.0f}")
    logger.info(f"回撤状态: {dd_status} (模式: {workflow.rm.mode})")
    logger.info(f"股票组合资金: {stock_capital:,.0f} (60%)")
    logger.info(f"期权对冲资金: {hedge_capital:,.0f} (40%)")
    logger.info(f"当日建仓资金: {day_capital:,.0f} (占股票组合 {build_ratio:.2%})")
    logger.info(f"  上午批次: {morning_total:,.0f}")
    logger.info(f"  下午批次: {afternoon_total:,.0f}")
    logger.info(f"  合计: {grand_total:,.0f}")
    logger.info(f"组合止损: {portfolio_stop:.2%}, VaR95<{var_95_limit:.0%}, VaR99<{var_99_limit:.0%}")

    risk_status = {
        "equity": current_equity,
        "drawdown_status": dd_status,
        "mode": workflow.rm.mode,
        "position_factor": workflow.rm.position_size_factor,
        "stock_capital": stock_capital,
        "hedge_capital": hedge_capital,
        "day_capital": day_capital,
        "morning_total": morning_total,
        "afternoon_total": afternoon_total,
        "grand_total": grand_total,
        "build_ratio": build_ratio,
        "portfolio_stop": portfolio_stop,
        "var_95_limit": var_95_limit,
        "var_99_limit": var_99_limit,
        "yellow_warning": workflow.config.YELLOW_WARNING,
        "orange_warning": workflow.config.ORANGE_WARNING,
        "red_warning": workflow.config.RED_WARNING,
        "full_stop": workflow.config.FULL_STOP,
    }
    workflow.state["phases"]["risk"] = {"status": "PASS", **risk_status}
    workflow.state["risk_status"] = risk_status

    # === 顶级风险管理: 压力测试情景库 ===
    if workflow.stress_test_engine is not None:
        try:
            positions_list = (workflow._get_portfolio_positions_for_stress_test()
                              if hasattr(self, "_get_portfolio_positions_for_stress_test") else [])
            if positions_list:
                portfolio_value = sum(float(p.get("amount", 0)) for p in positions_list)
                if portfolio_value > 0:
                    stress_results = workflow.stress_test_engine.run_all_scenarios(
                        positions=positions_list,
                        total_portfolio_value=portfolio_value,
                    )
                    stress_summary = workflow.stress_test_engine.summarize(stress_results)
                    workflow.state["phases"]["risk_stress_test"] = {
                        "n_scenarios": stress_summary.get("n_scenarios", 0),
                        "worst_scenario": stress_summary.get("worst_scenario", ""),
                        "worst_return": stress_summary.get("worst_return", 0.0),
                        "worst_pnl": stress_summary.get("worst_pnl", 0.0),
                        "best_return": stress_summary.get("best_return", 0.0),
                        "avg_return": stress_summary.get("avg_return", 0.0),
                        "n_breaches": stress_summary.get("n_breaches", 0),
                        "breach_scenarios": stress_summary.get("breach_scenarios", []),
                        "avg_var_change": stress_summary.get("avg_var_change", 0.0),
                    }
                    worst = workflow.stress_test_engine.get_worst_scenario(stress_results)
                    if worst:
                        logger.info(
                            "[StressTest] %d 场景: 最严重='%s' return=%.2f%% pnl=¥%.0f, breaches=%d",
                            stress_summary.get("n_scenarios", 0),
                            worst.scenario_name,
                            worst.portfolio_return * 100,
                            worst.portfolio_pnl,
                            stress_summary.get("n_breaches", 0),
                        )
                        if worst.is_breach:
                            logger.warning(
                                "[StressTest] ⚠️ 风险突破: %s 收益 %.2f%% 低于阈值 %.0f%%",
                                worst.scenario_name,
                                worst.portfolio_return * 100,
                                workflow.stress_test_engine.risk_threshold * 100,
                            )
        except Exception as exc:
            logger.error("[StressTest] 压力测试失败: %s", exc, exc_info=True)

    # === 顶级风险管理: 风险预算约束优化 (自动 TE 再平衡建议) ===
    if workflow.risk_budget_opt is not None and workflow.barra_decomposer is not None:
        try:
            barra_state = workflow.state.get("phases", {}).get("report_barra")
            if barra_state and barra_state.get("active_risk", 0) > 0.05:
                logger.warning(
                    "[RiskBudget] Barra 显示 TE=%.2f%% 超过 5%% 预算, 生成再平衡建议",
                    barra_state["active_risk"] * 100,
                )
                workflow.state["phases"]["risk_budget_rebalance_needed"] = True
                workflow.state["phases"]["risk_budget_target_te"] = 0.05
                workflow.state["phases"]["risk_budget_current_te"] = barra_state["active_risk"]
        except Exception as exc:
            logger.error("[RiskBudget] 再平衡建议生成失败: %s", exc, exc_info=True)

    return risk_status

    # --------------------------------------------------------
    # Phase 4: 对冲评估 + 自动执行
    # --------------------------------------------------------

