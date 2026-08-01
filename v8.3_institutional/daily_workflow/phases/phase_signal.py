#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_signal

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_signal phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_signal(workflow) -> Dict[str, Any]:
    """信号生成 — 从 trade_plan 加载订单

    读取 `trade_plans/trade_plan_{date}.json` 中的:
    - morning_orders: 上午批次
    - afternoon_orders: 下午批次

    Returns:
        信号字典, 含 action/morning_orders/afternoon_orders/summary 等。
        若交易计划未加载, 返回 {"action": "NO_PLAN"}。
    """
    logger.info("=" * 60)
    logger.info("Phase 5: 信号生成")
    logger.info("=" * 60)

    if not workflow.trade_plan:
        logger.warning("交易计划未加载, 无信号生成")
        workflow.state["phases"]["signal"] = {"status": "PASS", "action": "NO_PLAN"}
        return {"action": "NO_PLAN"}

    # === 从交易计划读取订单 ===
    plan_phase = workflow.trade_plan.get("phase", {})
    plan_exec = workflow.trade_plan.get("execution_plan", {})
    morning_orders = plan_exec.get("morning_orders", [])
    afternoon_orders = plan_exec.get("afternoon_orders", [])

    # === 计算外部报告调整系数 ===
    external_reports = workflow._load_external_reports()
    workflow.state["external_reports"] = external_reports
    if external_reports.get("loaded"):
        logger.info("外部报告加载完成: %d/%d 项",
                    external_reports.get("loaded_count", 0),
                    external_reports.get("total_count", 0))
        logger.info("外部情绪评分: %+.2f", external_reports.get("sentiment_score", 0.0))
        if external_reports.get("risk_events"):
            logger.info("外部风险事件: %d 项", len(external_reports["risk_events"]))
    else:
        logger.warning("外部报告加载失败: %s", external_reports.get("reason", "unknown"))

    external_factor = workflow._calculate_external_factor(external_reports)
    if external_factor != 1.0:
        logger.info("外部报告调整系数: %.2f", external_factor)

    # === 检测市场状态并获取动态融合权重 ===
    market_regime = workflow._detect_market_regime()
    regime_weights = workflow._get_regime_weights(market_regime)
    logger.info("市场状态: %s, 融合权重: Qlib=%.2f iFinD=%.2f External=%.2f",
                market_regime,
                regime_weights.get("qlib", 0.5),
                regime_weights.get("ifind", 0.3),
                regime_weights.get("external", 0.2))

    logger.info(f"阶段: {plan_phase.get('name', 'N/A')} "
                f"(第 {plan_phase.get('day_index', 0)}/{plan_phase.get('duration_days', 0)} 日)")
    logger.info(f"上午批次: {len(morning_orders)} 笔, "
                f"金额 {float(plan_exec.get('morning_total', 0)):,.0f}")
    logger.info(f"下午批次: {len(afternoon_orders)} 笔, "
                f"金额 {float(plan_exec.get('afternoon_total', 0)):,.0f}")
    logger.info(f"单日合计: {float(plan_exec.get('grand_total', 0)):,.0f} "
                f"({plan_exec.get('total_orders', 0)} 笔订单)")

    # === 检查市场状态是否允许建仓 ===
    market_phase = workflow.state.get("phases", {}).get("market", {})
    build_allowed = market_phase.get("build_allowed", True)
    if not build_allowed:
        logger.warning("市场熔断 LEVEL_3+, 暂停建仓")
        workflow.state["phases"]["signal"] = {
            "status": "PASS", "action": "PAUSED",
            "reason": "市场熔断暂停建仓",
        }
        return {"action": "PAUSED", "reason": "市场熔断暂停建仓"}

    # === 检查 iFinD 重大负面新闻熔断 ===
    cb_cfg = workflow.fusion_config.get("ifind_circuit_breaker", {})
    if cb_cfg.get("enabled", True) and workflow.ifind_analyzer is not None:
        planned_symbols = [str(o.get("code", "")) for o in morning_orders + afternoon_orders if o.get("code")]
        name_map = {str(o.get("code", "")): str(o.get("name", "")) for o in morning_orders + afternoon_orders if o.get("code")}
        insight_map = workflow._get_ifind_insights(planned_symbols, name_map)
        cb_dir = cb_cfg.get("direction", "negative")
        cb_min_conf = float(cb_cfg.get("min_confidence", 0.9))
        for _symbol, insight in insight_map.items():
            if insight.direction == cb_dir and float(insight.confidence) >= cb_min_conf:
                logger.warning("iFinD 重大负面新闻熔断: [%s] %s confidence=%.2f reasons=%s",
                               insight.symbol, insight.direction, insight.confidence, insight.reasons)
                workflow.state["phases"]["signal"] = {
                    "status": "PASS", "action": "PAUSED",
                    "reason": f"重大负面新闻暂停建仓: {insight.symbol} {insight.direction} confidence={insight.confidence:.2f}",
                }
                return {"action": "PAUSED", "reason": "重大负面新闻暂停建仓"}

    # === 检查 DEFENSE 模式仓位系数 ===
    position_factor = getattr(self, 'rm', None)
    if position_factor is not None and hasattr(position_factor, 'position_size_factor'):
        position_factor = position_factor.position_size_factor
    else:
        position_factor = 1.0
        logger.debug("RiskManager 未初始化，使用默认仓位系数 1.0")

    # === 应用仓位系数到订单股数 (DEFENSE 模式减仓) ===
    adjusted_morning = workflow._apply_position_factor(morning_orders, position_factor)
    adjusted_afternoon = workflow._apply_position_factor(afternoon_orders, position_factor)

    # === 十五五/康波宏观政策评分（可选增强） ===
    macro_scores = {}
    try:
        from src.macro.macro_policy_scoring import score_macro_policy
        planned_symbols = [str(o.get("code", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")]
        macro_scores = score_macro_policy(planned_symbols)
        logger.info("十五五/康波宏观评分完成: %d 个标的", len(macro_scores))
    except Exception as exc:
        logger.warning("十五五/康波宏观评分跳过: %s", exc)

    # === 计算调整后的金额 ===
    morning_amount = sum(o.get("est_amount", 0) for o in adjusted_morning)
    afternoon_amount = sum(o.get("est_amount", 0) for o in adjusted_afternoon)
    grand_amount = morning_amount + afternoon_amount

    signal = {
        "action": "BUILD_PLAN",
        "phase_name": plan_phase.get("name", ""),
        "phase_number": plan_phase.get("phase_number", 0),
        "day_index": plan_phase.get("day_index", 0),
        "morning_orders": adjusted_morning,
        "afternoon_orders": adjusted_afternoon,
        "morning_window": workflow.config.MORNING_WINDOW,
        "afternoon_window": workflow.config.AFTERNOON_WINDOW,
        "morning_count": len(adjusted_morning),
        "afternoon_count": len(adjusted_afternoon),
        "morning_amount": morning_amount,
        "afternoon_amount": afternoon_amount,
        "grand_amount": grand_amount,
        "total_orders": len(adjusted_morning) + len(adjusted_afternoon),
        "position_factor": position_factor,
    }

    logger.info(f"信号生成完成: {signal['total_orders']} 笔订单, "
                f"总金额 {grand_amount:,.0f}")

    # === v7.5 + Qlib 集成：生成 Qlib 深度学习信号 ===
    qlib_signals = workflow._generate_qlib_signals()
    if qlib_signals:
        signal["qlib_signals"] = qlib_signals
        logger.info(f"Qlib 信号生成完成: {len(qlib_signals)} 个标的")

    # === 获取 iFinD 新闻研判 (带当日缓存) ===
    ifind_insights = {}
    if workflow.ifind_analyzer is not None:
        symbols = [str(o.get("code", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")]
        name_map = {str(o.get("code", "")): str(o.get("name", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")}
        ifind_insights = workflow._get_ifind_insights(symbols, name_map)

    # === 加载 lgb_enhanced 增强模型信号 (第四信号源) ===
    lgb_signals = workflow._load_lgb_enhanced_signals()
    if lgb_signals:
        signal["lgb_enhanced_signals"] = lgb_signals
        logger.info(f"LGB增强信号加载完成: {len(lgb_signals)} 个标的")

    # === 融合 Qlib + iFinD + 外部报告 + LGB增强 信号统一调整订单 ===
    if qlib_signals or ifind_insights or lgb_signals:
        fused_adjustments = workflow._apply_fused_qlib_ifind_adjustments(
            morning_orders=adjusted_morning,
            afternoon_orders=adjusted_afternoon,
            qlib_signals=qlib_signals,
            ifind_insights=ifind_insights,
            external_factor=external_factor,
            regime_weights=regime_weights,
            lgb_signals=lgb_signals,
        )
        if fused_adjustments:
            signal["qlib_adjusted"] = True
            signal["ifind_adjusted"] = True
            signal["morning_orders"] = fused_adjustments.get("morning_orders", adjusted_morning)
            signal["afternoon_orders"] = fused_adjustments.get("afternoon_orders", adjusted_afternoon)
            signal["qlib_skip_count"] = fused_adjustments.get("skip_count", 0)
            signal["qlib_boost_count"] = fused_adjustments.get("boost_count", 0)
            signal["qlib_cut_count"] = fused_adjustments.get("cut_count", 0)
            signal["ifind_skip_count"] = fused_adjustments.get("skip_count", 0)
            signal["ifind_boost_count"] = fused_adjustments.get("boost_count", 0)
            signal["ifind_cut_count"] = fused_adjustments.get("cut_count", 0)
            signal["lgb_boost_count"] = fused_adjustments.get("lgb_boost_count", 0)
            signal["lgb_cut_count"] = fused_adjustments.get("lgb_cut_count", 0)
            logger.info(
                "融合调整完成: 加仓=%d, 减仓=%d, 跳过=%d | LGB: 加仓=%d, 减仓=%d",
                signal["qlib_boost_count"],
                signal["qlib_cut_count"],
                signal["qlib_skip_count"],
                signal["lgb_boost_count"],
                signal["lgb_cut_count"],
            )
            # 记录 LGB 信号应用详情到监控日志 (供阈值优化分析)
            try:
                from utils.lgb_signal_monitor import record_lgb_application
                record_lgb_application(
                    trade_date=datetime.now().strftime("%Y-%m-%d"),
                    orders=signal["morning_orders"] + signal["afternoon_orders"],
                    lgb_signals=lgb_signals or {},
                    boost_count=signal["lgb_boost_count"],
                    cut_count=signal["lgb_cut_count"],
                )
            except Exception:
                logger.debug("LGB 监控记录失败 (非关键)", exc_info=True)
        else:
            signal["qlib_adjusted"] = False
            signal["ifind_adjusted"] = False
    else:
        signal["qlib_adjusted"] = False
        signal["ifind_adjusted"] = False

    # === 十五五/康波宏观政策评分实际调整订单 ===
    if macro_scores:
        macro_adjustments = workflow._apply_macro_policy_adjustments(
            morning_orders=signal.get("morning_orders", adjusted_morning),
            afternoon_orders=signal.get("afternoon_orders", adjusted_afternoon),
            macro_scores=macro_scores,
        )
        if macro_adjustments:
            signal["morning_orders"] = macro_adjustments.get("morning_orders", signal.get("morning_orders", adjusted_morning))
            signal["afternoon_orders"] = macro_adjustments.get("afternoon_orders", signal.get("afternoon_orders", adjusted_afternoon))
            signal["macro_skip_count"] = macro_adjustments.get("skip_count", 0)
            signal["macro_boost_count"] = macro_adjustments.get("boost_count", 0)
            signal["macro_cut_count"] = macro_adjustments.get("cut_count", 0)
            logger.info(
                "宏观调整完成: 加仓=%d, 减仓=%d, 跳过=%d",
                signal["macro_boost_count"],
                signal["macro_cut_count"],
                signal["macro_skip_count"],
            )

    # === Black-Litterman 组合优化 (P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_apply_bl_optimization
    workflow._phase_signal_apply_bl_optimization(signal)

    # === Alpha 因子库 + 动量反转 + Smart Beta (P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_apply_alpha_modules
    workflow._phase_signal_apply_alpha_modules(signal)

    # === 另类数据视角 (新闻情感 + 供应链 + 卫星/搜索/招聘/专利, P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_apply_alt_data_modules
    workflow._phase_signal_apply_alt_data_modules(signal)

    # === 多策略协调器 (冲突检测 + 风险预算审计, P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_apply_strategy_coordination
    workflow._phase_signal_apply_strategy_coordination(signal)

    # === v8.6.4 P0 修复: 计算 target_weights（供 Phase 10 影子账户使用）===
    # 修复 2026-07-26 P0 隐藏 bug: Phase 10 shadow_monitor 读取 signal_phase.target_weights,
    # 但 phase_signal() 之前从未设置该字段, 导致影子账户 NAV 恒为 1.0、fail-fast 永不触发.
    # 此处基于最终调整后的订单计算目标权重, 确保影子账户能真实跟踪组合收益.
    target_weights = {}
    grand_total_safe = float(grand_amount) if grand_amount and grand_amount > 0 else 1.0
    final_morning = signal.get("morning_orders", adjusted_morning)
    final_afternoon = signal.get("afternoon_orders", adjusted_afternoon)
    for order in final_morning + final_afternoon:
        code = str(order.get("code", "")).strip()
        if not code:
            continue
        amount = float(order.get("est_amount", 0) or 0)
        if amount <= 0:
            continue
        side = str(order.get("side", "buy")).lower()
        # 买入类: 正权重; 卖出类: 负权重
        sign = -1.0 if side in ("sell", "close_long", "reduce", "exit", "close") else +1.0
        weight = sign * (amount / grand_total_safe)
        target_weights[code] = target_weights.get(code, 0.0) + weight
    signal["target_weights"] = target_weights

    # === v8.6.4 P0-A: 加载 Pipeline 因子组合信号 (P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_inject_pipeline_signals
    target_weights = workflow._phase_signal_inject_pipeline_signals(signal, target_weights)

    # === v8.6.9: 研究蒸馏信号注入 (第 6 信号源, P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_inject_research_signals
    workflow._phase_signal_inject_research_signals(signal)

    # === v8.7: LGB 增强信号注入 (第 7 信号源, P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_inject_lgb_signals
    workflow._phase_signal_inject_lgb_signals(signal)

    # === v8.6.9: 金融多 Agent Shadow Mode (P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_apply_agent_shadow
    workflow._phase_signal_apply_agent_shadow(signal, target_weights)

    total_exposure = sum(abs(w) for w in target_weights.values())
    logger.info(f"目标权重计算: {len(target_weights)} 标的, 总暴露={total_exposure:.4f} (供 Phase 10 影子账户)")

    workflow.state["phases"]["signal"] = {"status": "PASS", **signal}
    if macro_scores:
        signal["macro_policy"] = {k: {
            "fifteen_five_score": v.fifteen_five_score,
            "kondratiev_score": v.kondratiev_score,
            "combined_score": v.combined_score,
            "fifteen_five_note": v.fifteen_five_note,
            "kondratiev_note": v.kondratiev_note,
        } for k, v in macro_scores.items()}
        workflow.state["phases"]["signal"]["macro_policy"] = signal["macro_policy"]

    # === v8.5: 因子衰减监控 (P0-Q2 抽取为子方法) ===
    # 设计详见 _phase_signal_apply_factor_decay
    workflow._phase_signal_apply_factor_decay(signal)

    return signal


