"""Phase 5: 信号生成主模块 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py
- phase_signal (L1163-L1809, 647 行) — 主流程
- _fuse_qlib_ifind_factor (L1919-L1945, 静态) — Qlib+iFinD 因子融合
- _apply_fused_qlib_ifind_adjustments (L2046-L2190) — 四源融合调整订单
- _apply_position_factor (L2354-L2381) — DEFENSE 模式仓位系数调整

子模块依赖 (按建议内部再拆分):
- workflow.phases.signal_qlib: Qlib 信号生成与转换
- workflow.phases.signal_ifind: iFinD 研判 + 宏观政策 + 期权快照
- workflow.phases.signal_lgb: LightGBM 增强信号加载与乘数

主流程编排:
1. 加载交易计划 + 外部报告 + 市场状态
2. 市场熔断 + iFinD 负面新闻熔断检查
3. 仓位系数应用 (DEFENSE 模式)
4. Qlib/iFinD/LGB 三源信号生成
5. 四源融合调整订单 (Qlib+iFinD+External+LGB)
6. 宏观政策评分调整
7. Black-Litterman 组合优化 (可选)
8. Alpha 因子库 + 动量反转 + Smart Beta (可选)
9. 另类数据: 新闻情感 + 供应链 + 卫星等 (可选)
10. 多策略协调器冲突检测 (可选)

模块级符号 (动态查找, 兼容 monkeypatch):
- ALPHA_MODULES_READY / ALT_DATA_MODULES_READY / BLView (从 _dw 获取)
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional

from workflow.context import WorkflowContext, get_dw_module
from workflow.phases.signal_ifind import (
    apply_macro_policy_adjustments,
    ifind_signal_to_factor,
)
from workflow.phases.signal_lgb import lgb_confidence_multiplier, load_lgb_enhanced_signals
from workflow.phases.signal_qlib import generate_qlib_signals, qlib_signal_to_factor

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
_dw = get_dw_module()


def fuse_qlib_ifind_factor(
    qlib_factor: float,
    ifind_factor: float,
    *,
    qlib_weight: float = 0.6,
    ifind_weight: float = 0.4,
    clamp_min: float = 0.5,
    clamp_max: float = 1.3,
) -> float:
    """融合 Qlib 和 iFinD 调整系数

    Args:
        qlib_factor: Qlib 信号因子
        ifind_factor: iFinD 新闻因子
        qlib_weight: Qlib 权重 (默认 0.6)
        ifind_weight: iFinD 权重 (默认 0.4)
        clamp_min: 融合因子下限
        clamp_max: 融合因子上限

    Returns:
        融合后的调整系数
    """
    # 如果 iFinD 要求跳过，保留跳过
    if ifind_factor <= 0.0:
        return 0.0
    # 归一化权重
    total_w = qlib_weight + ifind_weight
    if total_w <= 0:
        return 1.0
    w_q = qlib_weight / total_w
    w_i = ifind_weight / total_w
    fused = qlib_factor * w_q + ifind_factor * w_i
    return max(clamp_min, min(clamp_max, fused))


def apply_fused_qlib_ifind_adjustments(
    ctx: WorkflowContext,
    *,
    morning_orders: list[dict[str, Any]],
    afternoon_orders: list[dict[str, Any]],
    qlib_signals: dict[str, float],
    ifind_insights: dict[str, Any],
    external_factor: float = 1.0,
    regime_weights: Optional[dict[str, float]] = None,
    lgb_signals: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """融合 Qlib 信号、iFinD 新闻研判与外部报告，统一调整订单

    Args:
        ctx: WorkflowContext (代理 fusion_config)
        external_factor: 外部报告调整系数 [0.5, 1.3]，默认 1.0
        regime_weights: 市场状态动态权重 {"qlib": x, "ifind": y, "external": z}

    Returns:
        {
            "morning_orders": [...],
            "afternoon_orders": [...],
            "skip_count": int,
            "boost_count": int,
            "cut_count": int,
        }
    """
    if not qlib_signals and not ifind_insights:
        return {}

    # 从配置读取融合参数
    fc = ctx.fusion_config
    qlib_w = float(fc.get("qlib_weight", 0.5))
    ifind_w = float(fc.get("ifind_weight", 0.3))
    external_w = float(fc.get("external_weight", 0.2))
    clamp_min = float(fc.get("fused_factor_min", 0.5))
    clamp_max = float(fc.get("fused_factor_max", 1.3))
    lgb_confidence_gate = bool(fc.get("lgb_confidence_gate", True))

    # 若传入动态权重，覆盖默认值
    if regime_weights:
        qlib_w = float(regime_weights.get("qlib", qlib_w))
        ifind_w = float(regime_weights.get("ifind", ifind_w))
        external_w = float(regime_weights.get("external", external_w))

    def _apply(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        adjusted: list[dict[str, Any]] = []
        for order in orders:
            code = str(order.get("code", ""))
            qlib_signal = qlib_signals.get(code)
            insight = ifind_insights.get(code)

            # 计算 Qlib factor
            qlib_factor = qlib_signal_to_factor(float(qlib_signal)) if qlib_signal is not None else 1.0

            # 计算 iFinD factor
            ifind_factor = 1.0
            if insight:
                ifind_factor = ifind_signal_to_factor(insight.direction, float(insight.confidence))

            # 三因子融合: Qlib + iFinD + 外部报告（权重可动态调整）
            if qlib_signal is not None and insight:
                base_factor = fuse_qlib_ifind_factor(
                    qlib_factor, ifind_factor,
                    qlib_weight=qlib_w, ifind_weight=ifind_w,
                    clamp_min=clamp_min, clamp_max=clamp_max,
                )
                fused_factor = base_factor * (1 - external_w) + external_factor * external_w
                fused_factor = max(clamp_min, min(clamp_max, fused_factor))
            elif qlib_signal is not None:
                fused_factor = qlib_factor * (1 - external_w) + external_factor * external_w
                fused_factor = max(clamp_min, min(clamp_max, fused_factor))
            elif insight:
                fused_factor = ifind_factor * (1 - external_w) + external_factor * external_w
                fused_factor = max(clamp_min, min(clamp_max, fused_factor))
            else:
                adjusted.append(dict(order))
                continue

            # 应用 lgb_enhanced 置信度乘数 (第四信号源, 保守调制)
            lgb_mult = 1.0
            lgb_sig_value = None
            if lgb_signals and lgb_confidence_gate:
                lgb_info = lgb_signals.get(code)
                if lgb_info:
                    lgb_sig_value = lgb_info.get("signal")
                    lgb_mult = lgb_confidence_multiplier(
                        lgb_sig_value, lgb_info.get("quality_flag", "OK"),
                    )
                    if lgb_mult != 1.0:
                        fused_factor = max(clamp_min, min(clamp_max, fused_factor * lgb_mult))

            # 应用 fused_factor
            if fused_factor <= 0.0:
                logger.info("融合信号跳过订单 [%s] qlib_factor=%.2f ifind_factor=%.2f lgb_mult=%.2f",
                            code, qlib_factor, ifind_factor, lgb_mult)
                continue

            new_order = dict(order)
            original_shares = int(order.get("shares", 0))
            float(order.get("est_amount", 0))
            new_shares = max(100, int(original_shares * fused_factor / 100) * 100)
            new_order["shares"] = new_shares
            new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
            new_order["original_shares"] = original_shares
            new_order["qlib_signal"] = round(float(qlib_signal), 4) if qlib_signal is not None else None
            new_order["qlib_factor"] = round(qlib_factor, 2) if qlib_signal is not None else None
            new_order["ifind_direction"] = insight.direction if insight else None
            new_order["ifind_confidence"] = round(float(insight.confidence), 2) if insight else None
            new_order["ifind_factor"] = round(ifind_factor, 2) if insight else None
            new_order["fused_factor"] = round(fused_factor, 2)
            new_order["ifind_reasons"] = insight.reasons[:3] if insight else []
            new_order["lgb_signal"] = round(float(lgb_sig_value), 4) if lgb_sig_value is not None else None
            new_order["lgb_multiplier"] = round(lgb_mult, 2) if lgb_mult != 1.0 else None
            adjusted.append(new_order)

            if fused_factor >= 1.2:
                logger.info("融合加仓 [%s] qlib=%s ifind=%s -> fused=%.2f, %d 股",
                            code,
                            f"{qlib_signal:+.4f}" if qlib_signal is not None else "N/A",
                            f"{insight.direction}/{insight.confidence:.2f}" if insight else "N/A",
                            fused_factor, new_shares)
            elif fused_factor <= 0.5:
                logger.info("融合减仓 [%s] qlib=%s ifind=%s -> fused=%.2f, %d 股",
                            code,
                            f"{qlib_signal:+.4f}" if qlib_signal is not None else "N/A",
                            f"{insight.direction}/{insight.confidence:.2f}" if insight else "N/A",
                            fused_factor, new_shares)
        return adjusted

    new_morning = _apply(morning_orders)
    new_afternoon = _apply(afternoon_orders)

    def _count(orders: list[dict[str, Any]], threshold: float) -> int:
        return sum(1 for o in orders if o.get("fused_factor", 1.0) >= threshold)

    def _count_lgb(orders: list[dict[str, Any]], op: Callable[[float], bool]) -> int:
        return sum(1 for o in orders if o.get("lgb_multiplier") is not None and op(o.get("lgb_multiplier", 1.0)))

    return {
        "morning_orders": new_morning,
        "afternoon_orders": new_afternoon,
        "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
        "boost_count": _count(new_morning, 1.2) + _count(new_afternoon, 1.2),
        "cut_count": _count(new_morning, 0.5) + _count(new_afternoon, 0.5),
        "lgb_boost_count": _count_lgb(new_morning + new_afternoon, lambda x: x > 1.0),
        "lgb_cut_count": _count_lgb(new_morning + new_afternoon, lambda x: x < 1.0),
    }


def apply_position_factor(
    orders: list[dict[str, Any]],
    factor: float,
) -> list[dict[str, Any]]:
    """应用仓位系数到订单列表 (DEFENSE 模式减仓)

    Args:
        orders: 原始订单列表
        factor: 仓位系数 (1.0 = 全仓, 0.5 = 半仓)

    Returns:
        调整后的订单列表 (新建对象, 不修改原订单)
    """
    if factor >= 1.0:
        return list(orders)

    adjusted = []
    for order in orders:
        new_order = dict(order)
        original_shares = int(order.get("shares", 0))
        float(order.get("est_amount", 0))
        # 按 factor 缩减股数, 并对齐到 100 股整数倍
        new_shares = max(100, (int(original_shares * factor) // 100) * 100)
        new_order["shares"] = new_shares
        new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
        new_order["original_shares"] = original_shares
        new_order["position_factor"] = factor
        adjusted.append(new_order)
    return adjusted


def phase_signal(ctx: WorkflowContext) -> dict[str, Any]:
    """信号生成 — 从 trade_plan 加载订单

    读取 `trade_plans/trade_plan_{date}.json` 中的:
    - morning_orders: 上午批次
    - afternoon_orders: 下午批次

    Returns:
        信号字典, 含 action/morning_orders/afternoon_orders/summary 等。
        若交易计划未加载, 返回 {"action": "NO_PLAN"}。
    """
    # 动态查找模块级符号 (兼容 monkeypatch 对 daily_workflow 模块的 patch)
    ALPHA_MODULES_READY = bool(getattr(_dw, "ALPHA_MODULES_READY", False)) if _dw else False
    ALT_DATA_MODULES_READY = bool(getattr(_dw, "ALT_DATA_MODULES_READY", False)) if _dw else False
    BLView = getattr(_dw, "BLView", None) if _dw else None

    logger.info("=" * 60)
    logger.info("Phase 5: 信号生成")
    logger.info("=" * 60)

    if not ctx.trade_plan:
        logger.warning("交易计划未加载, 无信号生成")
        ctx.state["phases"]["signal"] = {"status": "PASS", "action": "NO_PLAN"}
        return {"action": "NO_PLAN"}

    # === 从交易计划读取订单 ===
    plan_phase = ctx.trade_plan.get("phase", {})
    plan_exec = ctx.trade_plan.get("execution_plan", {})
    morning_orders = plan_exec.get("morning_orders", [])
    afternoon_orders = plan_exec.get("afternoon_orders", [])

    # === 计算外部报告调整系数 ===
    external_reports = ctx._load_external_reports()
    ctx.state["external_reports"] = external_reports
    if external_reports.get("loaded"):
        logger.info("外部报告加载完成: %d/%d 项",
                    external_reports.get("loaded_count", 0),
                    external_reports.get("total_count", 0))
        logger.info("外部情绪评分: %+.2f", external_reports.get("sentiment_score", 0.0))
        if external_reports.get("risk_events"):
            logger.info("外部风险事件: %d 项", len(external_reports["risk_events"]))
    else:
        logger.warning("外部报告加载失败: %s", external_reports.get("reason", "unknown"))

    external_factor = ctx._calculate_external_factor(external_reports)
    if external_factor != 1.0:
        logger.info("外部报告调整系数: %.2f", external_factor)

    # === 检测市场状态并获取动态融合权重 ===
    market_regime = ctx._detect_market_regime()
    regime_weights = ctx._get_regime_weights(market_regime)
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
    market_phase = ctx.state.get("phases", {}).get("market", {})
    build_allowed = market_phase.get("build_allowed", True)
    if not build_allowed:
        logger.warning("市场熔断 LEVEL_3+, 暂停建仓")
        ctx.state["phases"]["signal"] = {
            "status": "PASS", "action": "PAUSED",
            "reason": "市场熔断暂停建仓",
        }
        return {"action": "PAUSED", "reason": "市场熔断暂停建仓"}

    # === 检查 iFinD 重大负面新闻熔断 ===
    cb_cfg = ctx.fusion_config.get("ifind_circuit_breaker", {})
    if cb_cfg.get("enabled", True) and ctx.ifind_analyzer is not None:
        planned_symbols = [str(o.get("code", "")) for o in morning_orders + afternoon_orders if o.get("code")]
        name_map = {str(o.get("code", "")): str(o.get("name", "")) for o in morning_orders + afternoon_orders if o.get("code")}
        insight_map = ctx._get_ifind_insights(planned_symbols, name_map)
        cb_dir = cb_cfg.get("direction", "negative")
        cb_min_conf = float(cb_cfg.get("min_confidence", 0.9))
        for _symbol, insight in insight_map.items():
            if insight.direction == cb_dir and float(insight.confidence) >= cb_min_conf:
                logger.warning("iFinD 重大负面新闻熔断: [%s] %s confidence=%.2f reasons=%s",
                               insight.symbol, insight.direction, insight.confidence, insight.reasons)
                ctx.state["phases"]["signal"] = {
                    "status": "PASS", "action": "PAUSED",
                    "reason": f"重大负面新闻暂停建仓: {insight.symbol} {insight.direction} confidence={insight.confidence:.2f}",
                }
                return {"action": "PAUSED", "reason": "重大负面新闻暂停建仓"}

    # === 检查 DEFENSE 模式仓位系数 ===
    position_factor = getattr(ctx, 'rm', None)
    if position_factor is not None and hasattr(position_factor, 'position_size_factor'):
        position_factor = position_factor.position_size_factor
    else:
        position_factor = 1.0
        logger.debug("RiskManager 未初始化，使用默认仓位系数 1.0")

    # === 应用仓位系数到订单股数 (DEFENSE 模式减仓) ===
    adjusted_morning = apply_position_factor(morning_orders, position_factor)
    adjusted_afternoon = apply_position_factor(afternoon_orders, position_factor)

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

    signal: dict[str, Any] = {
        "action": "BUILD_PLAN",
        "phase_name": plan_phase.get("name", ""),
        "phase_number": plan_phase.get("phase_number", 0),
        "day_index": plan_phase.get("day_index", 0),
        "morning_orders": adjusted_morning,
        "afternoon_orders": adjusted_afternoon,
        "morning_window": ctx.config.MORNING_WINDOW,
        "afternoon_window": ctx.config.AFTERNOON_WINDOW,
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
    qlib_signals = generate_qlib_signals(ctx)
    if qlib_signals:
        signal["qlib_signals"] = qlib_signals
        logger.info(f"Qlib 信号生成完成: {len(qlib_signals)} 个标的")

    # === 获取 iFinD 新闻研判 (带当日缓存) ===
    ifind_insights = {}
    if ctx.ifind_analyzer is not None:
        symbols = [str(o.get("code", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")]
        name_map = {str(o.get("code", "")): str(o.get("name", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")}
        ifind_insights = ctx._get_ifind_insights(symbols, name_map)

    # === 加载 lgb_enhanced 增强模型信号 (第四信号源) ===
    lgb_signals = load_lgb_enhanced_signals()
    if lgb_signals:
        signal["lgb_enhanced_signals"] = lgb_signals
        logger.info(f"LGB增强信号加载完成: {len(lgb_signals)} 个标的")

    # === 融合 Qlib + iFinD + 外部报告 + LGB增强 信号统一调整订单 ===
    if qlib_signals or ifind_insights or lgb_signals:
        fused_adjustments = apply_fused_qlib_ifind_adjustments(
            ctx,
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
        macro_adjustments = apply_macro_policy_adjustments(
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

    # === 机构级配置: Black-Litterman 组合优化 ===
    bl_optimizer = getattr(ctx, "bl_optimizer", None)
    if bl_optimizer is not None:
        try:
            positions = (ctx._get_portfolio_positions_for_stress_test()
                         if hasattr(ctx, "_get_portfolio_positions_for_stress_test") else [])
            if positions:
                bl_assets = [p.get("code", "") for p in positions if p.get("code")]
                bl_market_weights = [
                    float(p.get("amount", 0)) for p in positions if p.get("code")
                ]
                total_mv = sum(bl_market_weights)
                if total_mv > 0:
                    bl_market_weights = [w / total_mv for w in bl_market_weights]
                    # 简化协方差: 单位对角矩阵 × 0.04 (4% 日波动)
                    import numpy as _np
                    n_assets = len(bl_assets)
                    bl_cov = _np.eye(n_assets) * 0.04 ** 2
                    # 观点: 从 morning_orders/afternoon_orders 中提取信号
                    # 仅纳入已在持仓中的标的 (避免 BL 维度不匹配)
                    bl_views = []
                    asset_set = set(bl_assets)
                    for order in signal.get("morning_orders", []) + signal.get("afternoon_orders", []):
                        code = str(order.get("code", ""))
                        if code not in asset_set:
                            continue
                        side = str(order.get("side", "BUY")).upper()
                        if side in ("BUY", "OPEN_LONG"):
                            bl_views.append(BLView(
                                type="absolute", assets=[code], weights=[1.0],
                                expected_return=0.02, confidence=0.6,
                            ))
                        elif side in ("SELL", "CLOSE_LONG"):
                            bl_views.append(BLView(
                                type="absolute", assets=[code], weights=[1.0],
                                expected_return=-0.02, confidence=0.5,
                            ))
                    bl_result = bl_optimizer.optimize(
                        assets=bl_assets,
                        market_weights=bl_market_weights,
                        cov_matrix=bl_cov,
                        views=bl_views or None,
                        risk_free_rate=0.03,
                    )
                    signal["bl_optimization"] = {
                        "optimal_weights": bl_result.optimal_weights.tolist(),
                        "weight_change_vs_market": bl_result.weight_change_vs_market.tolist(),
                        "sharpe_ratio": bl_result.sharpe_ratio,
                        "diversification_ratio": bl_result.diversification_ratio,
                        "effective_n": bl_result.effective_n,
                        "expected_portfolio_return": bl_result.expected_portfolio_return,
                        "expected_portfolio_vol": bl_result.expected_portfolio_vol,
                    }
                    logger.info(
                        "[BlackLitterman] 优化完成: Sharpe=%.3f, DivRatio=%.2f, EffN=%.1f",
                        bl_result.sharpe_ratio,
                        bl_result.diversification_ratio,
                        bl_result.effective_n,
                    )
        except Exception as exc:
            logger.error("[BlackLitterman] 优化失败: %s", exc, exc_info=True)

    # === Alpha 生成: Alpha 因子库 + 动量反转引擎 + Smart Beta 优化 ===
    alpha_factor_lib = getattr(ctx, "alpha_factor_lib", None)
    if ALPHA_MODULES_READY and alpha_factor_lib is not None:
        try:
            import numpy as _np_alpha
            positions = (ctx._get_portfolio_positions_for_stress_test()
                         if hasattr(ctx, "_get_portfolio_positions_for_stress_test") else [])
            if positions:
                # 构造简化价格数据 (用持仓成本/市值代理) — 真实场景应从 data_layer 加载
                alpha_symbols = [str(p.get("code", "")) for p in positions if p.get("code")]
                n_alpha = len(alpha_symbols)
                if n_alpha > 0:
                    # 用持仓 amount 作为 market_cap 代理
                    alpha_mcap_dict = {
                        str(p.get("code", "")): max(float(p.get("amount", 1.0)), 1.0)
                        for p in positions if p.get("code")
                    }
                    # 构造合成价格数据 (100日, 用于因子计算)
                    _np_alpha.random.seed(42)
                    alpha_prices = _np_alpha.cumprod(
                        1.0 + _np_alpha.random.randn(100, n_alpha) * 0.02, axis=0
                    ) * 100.0
                    import pandas as _pd_alpha
                    alpha_price_df = _pd_alpha.DataFrame(alpha_prices, columns=alpha_symbols)
                    # compute_all 期望 dict[str, dict[str, list[float]]] 格式, 非 DataFrame
                    alpha_price_dict = {
                        sym: {
                            "closes": alpha_price_df[sym].tolist(),
                            "volumes": [],
                            "highs": [],
                            "lows": [],
                        }
                        for sym in alpha_price_df.columns
                    }
                    # 行业映射简化
                    alpha_industries = {s: "Unknown" for s in alpha_symbols}

                    # 1) Alpha 因子库计算
                    alpha_result = alpha_factor_lib.compute_all(
                        price_data=alpha_price_dict,
                        fundamentals=None,
                        industries=alpha_industries,
                        benchmark_returns=None,
                    )
                    # 实际字段: factors (Dict), effective_factors (List), strong_factors (List)
                    signal["alpha_factors"] = {
                        "total_factors": len(alpha_result.factors),
                        "effective_factors": list(alpha_result.effective_factors),
                        "strong_factors": list(alpha_result.strong_factors),
                        "factor_names": list(alpha_result.factors.keys())[:20],
                    }
                    logger.info(
                        "[AlphaFactorLib] 因子计算完成: 总数=%d, 有效=%d, 强=%d",
                        len(alpha_result.factors),
                        len(alpha_result.effective_factors),
                        len(alpha_result.strong_factors),
                    )

                    # 2) 动量反转信号生成
                    momentum_engine = getattr(ctx, "momentum_engine", None)
                    if momentum_engine is not None:
                        mom_result = momentum_engine.generate_signals(alpha_price_df)
                        # 实际字段: signals (Dict), avg_signal_strength, bullish_count, bearish_count
                        signal["momentum_signals"] = {
                            "total_signals": len(mom_result.signals),
                            "bullish_count": mom_result.bullish_count,
                            "bearish_count": mom_result.bearish_count,
                            "avg_signal_strength": mom_result.avg_signal_strength,
                            "strategy_state": mom_result.strategy_state,
                            "top_long_candidates": mom_result.top_long_candidates[:5],
                            "top_short_candidates": mom_result.top_short_candidates[:5],
                        }
                        logger.info(
                            "[MomentumReversal] 信号生成: 总数=%d, 看多=%d, 看空=%d, 状态=%s",
                            len(mom_result.signals),
                            mom_result.bullish_count,
                            mom_result.bearish_count,
                            mom_result.strategy_state,
                        )

                    # 3) Smart Beta 多因子加权优化
                    smart_beta_engine = getattr(ctx, "smart_beta_engine", None)
                    if smart_beta_engine is not None:
                        # 构造 factor_scores: {symbol: {factor: value}}
                        # 从 alpha_result.factors (Dict[str, FactorValue]) 中提取
                        sb_factor_scores = {}
                        for fname, fvalue_obj in alpha_result.factors.items():
                            # FactorValue.values 是 Dict[str, float] = {symbol: value}
                            values_dict = getattr(fvalue_obj, "values", {}) or {}
                            for sym, val in values_dict.items():
                                sb_factor_scores.setdefault(sym, {})[fname] = float(val)
                        # 仅纳入有因子值的标的
                        sb_symbols = [s for s in alpha_symbols if s in sb_factor_scores]
                        if sb_symbols:
                            # 因子等权
                            first_sym = sb_symbols[0]
                            sb_factor_weights = {k: 1.0 / len(sb_factor_scores[first_sym])
                                                 for k in sb_factor_scores[first_sym]}
                            sb_market_caps = {s: alpha_mcap_dict.get(s, 1.0) for s in sb_symbols}
                            sb_cov = _np_alpha.cov(alpha_prices[:, :len(sb_symbols)].T)
                            sb_result = smart_beta_engine.optimize(
                                symbols=sb_symbols,
                                factor_scores=sb_factor_scores,
                                market_caps=sb_market_caps,
                                factor_weights=sb_factor_weights,
                                cov_matrix=sb_cov,
                            )
                            # 实际字段: smart_beta_weights, weight_concentration, effective_n, sharpe_ratio
                            signal["smart_beta"] = {
                                "weights": sb_result.smart_beta_weights.tolist(),
                                "weight_concentration": float(sb_result.weight_concentration),
                                "effective_n": float(sb_result.effective_n),
                                "sharpe_ratio": float(sb_result.sharpe_ratio),
                                "tracking_error": float(sb_result.tracking_error),
                                "information_ratio": float(sb_result.information_ratio),
                            }
                            logger.info(
                                "[SmartBeta] 优化完成: HHI=%.3f, 有效持仓=%.1f, Sharpe=%.3f, TE=%.4f",
                                sb_result.weight_concentration,
                                sb_result.effective_n,
                                sb_result.sharpe_ratio,
                                sb_result.tracking_error,
                            )
        except Exception as exc:
            logger.error("[AlphaModules] 信号生成失败: %s", exc, exc_info=True)

    # === 另类数据视角: 新闻情感 + 供应链 + 卫星/搜索/招聘/专利 ===
    if ALT_DATA_MODULES_READY:
        try:
            # 收集当前持仓标的列表
            alt_symbols: list[str] = []
            for pos in (ctx._get_portfolio_positions_for_stress_test()
                        if hasattr(ctx, "_get_portfolio_positions_for_stress_test") else []):
                code = str(pos.get("code", ""))
                if code and code not in alt_symbols:
                    alt_symbols.append(code)

            # 1) 新闻情感分析 (若有引擎且添加过新闻)
            news_sentiment_engine = getattr(ctx, "news_sentiment_engine", None)
            if news_sentiment_engine is not None:
                try:
                    # 构建 supply_chain_map (从供应链图引擎)
                    supply_map: dict[str, list[str]] = {}
                    supply_chain_graph = getattr(ctx, "supply_chain_graph", None)
                    if supply_chain_graph is not None:
                        for src, edges in getattr(supply_chain_graph, "adjacency", {}).items():
                            for e in edges:
                                supply_map.setdefault(src, []).append(e.target)

                    ns_result = news_sentiment_engine.analyze(
                        symbols=alt_symbols or [],
                        supply_chain_map=supply_map,
                    )
                    # 实际字段: signals (Dict), market_sentiment, anomalies, hot_events, total_news_processed
                    ns_signals = getattr(ns_result, "signals", {}) or {}
                    ns_positive = sum(1 for s in ns_signals.values() if s.composite_sentiment > 0)
                    ns_negative = sum(1 for s in ns_signals.values() if s.composite_sentiment < 0)
                    ns_neutral = sum(1 for s in ns_signals.values() if s.composite_sentiment == 0)
                    ns_event_counts = {ev: cnt for ev, cnt in (getattr(ns_result, "hot_events", []) or [])}
                    signal["news_sentiment"] = {
                        "avg_sentiment": float(getattr(ns_result, "market_sentiment", 0.0)),
                        "positive_count": int(ns_positive),
                        "negative_count": int(ns_negative),
                        "neutral_count": int(ns_neutral),
                        "event_counts": dict(ns_event_counts),
                        "total_news": int(getattr(ns_result, "total_news_processed", 0)),
                        "top_positive": [
                            {"symbol": s.symbol, "score": float(s.composite_sentiment), "confidence": float(s.confidence)}
                            for s in sorted(ns_signals.values(),
                                            key=lambda x: float(x.composite_sentiment),
                                            reverse=True)
                            if s.composite_sentiment > 0
                        ][:3],
                        "top_negative": [
                            {"symbol": s.symbol, "score": float(s.composite_sentiment), "confidence": float(s.confidence)}
                            for s in sorted(ns_signals.values(),
                                            key=lambda x: float(x.composite_sentiment))
                            if s.composite_sentiment < 0
                        ][:3],
                    }
                    logger.info(
                        "[NewsSentiment] 分析完成: 总新闻=%d, 平均情感=%.3f, 正面=%d, 负面=%d",
                        int(getattr(ns_result, "total_news_processed", 0)),
                        float(getattr(ns_result, "market_sentiment", 0.0)),
                        int(ns_positive),
                        int(ns_negative),
                    )
                except Exception as exc_ns:
                    logger.error("[NewsSentiment] 信号生成失败: %s", exc_ns, exc_info=True)

            # 2) 供应链关系图谱分析
            supply_chain_graph = getattr(ctx, "supply_chain_graph", None)
            if supply_chain_graph is not None:
                try:
                    sc_result = supply_chain_graph.analyze()
                    # 实际字段: nodes (List[str]), edges (List), metrics (Dict[str, NodeMetrics]),
                    # hubs, bottlenecks, risk_contagion, network_density, avg_path_length, num_components
                    sc_metrics = getattr(sc_result, "metrics", {}) or {}
                    sc_risk = getattr(sc_result, "risk_contagion", {}) or {}
                    sc_nodes = getattr(sc_result, "nodes", []) or []
                    sc_edges = getattr(sc_result, "edges", []) or []
                    signal["supply_chain"] = {
                        "total_nodes": int(len(sc_nodes)),
                        "total_edges": int(len(sc_edges)),
                        "top_central": [
                            {"symbol": s, "betweenness": float(getattr(m, "betweenness_centrality", 0.0)),
                             "pagerank": float(getattr(m, "pagerank", 0.0))}
                            for s, m in sorted(sc_metrics.items(),
                                                key=lambda x: float(getattr(x[1], "pagerank", 0.0)),
                                                reverse=True)[:5]
                        ],
                        "hubs": list(getattr(sc_result, "hubs", []))[:5],
                        "bottlenecks": list(getattr(sc_result, "bottlenecks", []))[:5],
                        "risk_contagion": {
                            s: float(v) for s, v in list(sc_risk.items())[:5]
                        },
                        "network_density": float(getattr(sc_result, "network_density", 0.0)),
                        "avg_path_length": float(getattr(sc_result, "avg_path_length", 0.0)),
                    }
                    logger.info(
                        "[SupplyChain] 分析完成: 节点=%d, 边=%d, 中心节点=%d, 网络密度=%.3f",
                        len(sc_nodes),
                        len(sc_edges),
                        len(sc_metrics),
                        float(getattr(sc_result, "network_density", 0.0)),
                    )
                except Exception as exc_sc:
                    logger.error("[SupplyChain] 信号生成失败: %s", exc_sc, exc_info=True)

            # 3) 另类数据综合指标
            alt_data_indicators = getattr(ctx, "alt_data_indicators", None)
            if alt_data_indicators is not None and alt_symbols:
                try:
                    import numpy as _np_alt  # 局部导入, 避免依赖外部 np
                    # 加载演示数据 (实盘接入前)
                    alt_data_indicators.load_demo_data(alt_symbols[:10])
                    ad_result = alt_data_indicators.analyze(alt_symbols[:10])
                    # 实际字段: signals (Dict), market_alt_score, anomalies, total_indicators, coverage_summary
                    ad_signals = getattr(ad_result, "signals", {}) or {}
                    ad_coverage = getattr(ad_result, "coverage_summary", {}) or {}
                    # 平均覆盖率
                    ad_avg_cov = float(_np_alt.mean(list(ad_coverage.values()))) if ad_coverage else 0.0
                    signal["alt_data"] = {
                        "total_symbols": int(len(ad_signals)),
                        "avg_composite_score": float(getattr(ad_result, "market_alt_score", 0.0)),
                        "coverage_rate": float(ad_avg_cov),
                        "top_scores": [
                            {
                                "symbol": s.symbol,
                                "composite_score": float(s.composite_score),
                                "satellite_score": float(getattr(s, "satellite_score", 0.0)),
                                "search_score": float(getattr(s, "search_score", 0.0)),
                                "recruitment_score": float(getattr(s, "recruitment_score", 0.0)),
                                "patent_score": float(getattr(s, "patent_score", 0.0)),
                                "confidence": float(getattr(s, "confidence", 0.0)),
                            }
                            for s in sorted(ad_signals.values(),
                                            key=lambda x: float(x.composite_score),
                                            reverse=True)[:5]
                        ],
                        "anomalies": list(getattr(ad_result, "anomalies", []))[:3],
                    }
                    logger.info(
                        "[AltData] 分析完成: 标的=%d, 平均综合评分=%.3f, 平均覆盖率=%.1f%%",
                        int(len(ad_signals)),
                        float(getattr(ad_result, "market_alt_score", 0.0)),
                        float(ad_avg_cov) * 100,
                    )
                except Exception as exc_ad:
                    logger.error("[AltData] 信号生成失败: %s", exc_ad, exc_info=True)
        except Exception as exc_outer:
            logger.error("[AltDataModules] 信号生成失败: %s", exc_outer, exc_info=True)

    # === 对冲基金视角: 多策略协调器 (冲突检测 + 风险预算审计) ===
    strategy_coordinator = getattr(ctx, "strategy_coordinator", None)
    if strategy_coordinator is not None:
        try:
            # 构造目标信号: 把订单按策略账户归类
            target_signals: dict[str, dict[str, str]] = {
                "stock_long": {},
                "etf_allocation": {},
            }
            for order in signal.get("morning_orders", []) + signal.get("afternoon_orders", []):
                code = str(order.get("code", ""))
                side = str(order.get("side", "BUY")).upper()
                asset_type = str(order.get("type", "STOCK")).upper()
                direction = "BUY" if side in ("BUY", "OPEN_LONG") else "SELL"
                if asset_type == "ETF":
                    target_signals["etf_allocation"][code] = direction
                else:
                    target_signals["stock_long"][code] = direction

            # 期权/期货信号 (从 hedge_plan 与 options_plan)
            hedge_plan = ctx.state.get("phases", {}).get("hedge", {}).get("hedge_plan", {})
            if hedge_plan:
                target_signals["macro_hedge"] = {
                    str(item.get("symbol", "")): "SELL"
                    for item in hedge_plan.get("futures", [])
                    if str(item.get("direction", "")).upper() in ("SHORT", "SELL")
                }
            options_modules = ctx.trade_plan.get("hedge_account", {}).get("modules", []) if ctx.trade_plan else []
            if options_modules:
                target_signals["options_tail"] = {"OPTIONS": "BUY"}

            # 当前持仓 (用于冲突检测) — 转换为 {code: {weight, strategy}} 格式
            current_positions: dict[str, dict[str, Any]] = {}
            portfolio_value = float(getattr(ctx, "capital", 5_000_000))
            for pos in (ctx._get_portfolio_positions_for_stress_test()
                        if hasattr(ctx, "_get_portfolio_positions_for_stress_test") else []):
                code = pos.get("code", "")
                amount = float(pos.get("amount", 0))
                if code and portfolio_value > 0:
                    current_positions[code] = {
                        "weight": amount / portfolio_value,
                        "strategy": "stock_long" if str(pos.get("type", "STOCK")).upper() == "STOCK" else "etf_allocation",
                        "amount": amount,
                    }

            coord_decision = strategy_coordinator.coordinate(
                target_signals=target_signals,
                current_positions=current_positions,
                strategy_pnl={},  # 实盘接入后填充
                strategy_correlations=None,
            )

            signal["strategy_coordination"] = {
                "is_approved": coord_decision.is_approved,
                "total_allocated": coord_decision.total_allocated,
                "cash_buffer": coord_decision.cash_buffer,
                "risk_budget_used": coord_decision.risk_budget_used,
                "risk_budget_limit": coord_decision.risk_budget_limit,
                "conflicts": [
                    {
                        "strategies": c.strategies,
                        "symbol": c.symbol,
                        "conflict_type": c.conflict_type,
                        "severity": c.severity,
                        "message": c.description,
                        "suggested_action": c.suggested_action,
                    } for c in coord_decision.conflicts
                ],
                "adjusted_weights": coord_decision.strategy_weights,
            }

            if not coord_decision.is_approved:
                err_conflicts = [c for c in coord_decision.conflicts if c.severity == "error"]
                logger.warning(
                    "[MultiStrategy] 协调未通过: %d 个 error 级冲突, %d 个 warning",
                    len(err_conflicts),
                    len([c for c in coord_decision.conflicts if c.severity == "warning"]),
                )
                for c in err_conflicts:
                    logger.warning("  - [%s/%s] %s: %s",
                                   ",".join(c.strategies), c.symbol, c.conflict_type, c.description)
            else:
                logger.info(
                    "[MultiStrategy] 协调通过: cash_buffer=%.0f, risk_used=%.0f/%.0f, conflicts=%d",
                    coord_decision.cash_buffer,
                    coord_decision.risk_budget_used,
                    coord_decision.risk_budget_limit,
                    len(coord_decision.conflicts),
                )
        except Exception as exc:
            logger.error("[MultiStrategy] 协调失败: %s", exc, exc_info=True)

    ctx.state["phases"]["signal"] = {"status": "PASS", **signal}
    if macro_scores:
        signal["macro_policy"] = {k: {
            "fifteen_five_score": v.fifteen_five_score,
            "kondratiev_score": v.kondratiev_score,
            "combined_score": v.combined_score,
            "fifteen_five_note": v.fifteen_five_note,
            "kondratiev_note": v.kondratiev_note,
        } for k, v in macro_scores.items()}
        ctx.state["phases"]["signal"]["macro_policy"] = signal["macro_policy"]
    return signal
