"""
ai_decision.execution_bridge — 决策→执行桥接层
===============================================

将 TradingDecision 转化为 OrderRouter 可消费的执行计划, 经硬风控后路由到 broker。

三层安全防线 (不可绕过):
  L1 — decision_gate 硬风控 (决策层, 在 orchestrator 中已完成)
  L2 — 执行层硬风控 (本模块, 下单前二次校验, 涵盖价格保护/流动性/熔断)
  L3 — broker 层风控 (外部, 券商柜台级风控)

灰度发布:
  shadow → paper → auto_10% → auto_50% → auto_100%
  每阶段有独立回滚触发条件 (PnL 偏离 > 2σ / 连续亏损 / 异常放量)

设计原则:
  - 永远不绕过 L2 风控直接下单
  - shadow/paper 模式 100% 不触达 broker
  - 执行结果回写审计, 全链路可追溯

v8.6 重构: 拆分为 5 个文件 (每个 ≤500 行), 本文件保留核心桥接函数,
其余符号通过 re-export 保持向后兼容 (from ai_decision.execution_bridge import X).
  - grayscale_state.py: 灰度状态机
  - execution_risk.py: L2 执行层硬风控
  - execution_tca.py: TCA 双轨
  - execution_audit.py: 执行审计
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from ai_decision.config import get_config
from ai_decision.decision_gate import RiskContext
from ai_decision.execution_audit import (
    _EXEC_AUDIT_DIR,
    _build_success_audit_record,
    _build_success_return,
    _write_execution_audit,
)
from ai_decision.execution_risk import (
    ExecutionRiskResult,
    _build_grayscale_veto_return,
    _build_l2_veto_return,
    _execution_risk_check,
    _run_l1_checks,
)
from ai_decision.execution_tca import (
    _build_benchmark_from_market_data,
    _build_fills_from_execution,
    _run_tca_post_trade,
    _run_tca_pre_trade,
    _tca_post_trade_enabled,
    _tca_pre_trade_enabled,
    _tca_report_to_dict,
)

# v8.6 拆分: 从子模块 re-export 所有符号, 保持向后兼容 (from ai_decision.execution_bridge import X)
from ai_decision.grayscale_state import (
    _GRAYSCALE_STATE_FILE,
    GrayscaleState,
    advance_grayscale,
    get_grayscale_summary,
)
from ai_decision.models import TradingDecision

# re-export 符号清单 (供 ruff F401 识别为有意 re-export, 同时文档化向后兼容接口)
__all__ = [
    # grayscale_state
    "GrayscaleState",
    "_GRAYSCALE_STATE_FILE",
    "advance_grayscale",
    "get_grayscale_summary",
    # execution_risk
    "ExecutionRiskResult",
    "_build_grayscale_veto_return",
    "_build_l2_veto_return",
    "_execution_risk_check",
    "_run_l1_checks",
    # execution_tca
    "_build_benchmark_from_market_data",
    "_build_fills_from_execution",
    "_run_tca_post_trade",
    "_run_tca_pre_trade",
    "_tca_post_trade_enabled",
    "_tca_pre_trade_enabled",
    "_tca_report_to_dict",
    # execution_audit
    "_EXEC_AUDIT_DIR",
    "_build_success_audit_record",
    "_build_success_return",
    "_write_execution_audit",
    # 本模块核心
    "execute_decision",
    "_generate_execution_plan",
    "_dispatch_execution_mode",
    "_simulate_fill",
]

logger = logging.getLogger("ai_decision.execution_bridge")


# ============================================================
# 执行计划生成
# ============================================================


def _generate_execution_plan(
    decision: TradingDecision,
    portfolio_value: float,
    price: float | None = None,
    max_single_pct: float | None = None,
) -> dict[str, Any]:
    """将 TradingDecision 映射为 OrderRouter.route_order() 可消费的执行计划

    Args:
        decision: 经 decision_gate 放行的决策
        portfolio_value: 组合净值
        price: 当前参考价格 (如可用)
        max_single_pct: 单笔最大资金比例, 默认读配置 2%
    Returns:
        兼容 OrderRouter.route_order(execution_plan, market_state) 的执行计划
    """
    if max_single_pct is None:
        max_single_pct = float(get_config("gate.max_single_pct", 0.02))

    # P0 修复: action 白名单拦截 — hold/veto/review 决策不应生成执行计划
    # 原代码对 action="hold" 或 "veto" 仍生成 BUY 100 股, 配合 auto 模式会触发真实下单
    if decision.action not in ("buy", "sell"):
        raise ValueError(
            f"不应对 action={decision.action!r} 的决策生成执行计划 (仅允许 buy/sell). "
            f"symbol={decision.symbol}, strength={decision.strength}"
        )

    # 价格缺失检测: auto 模式必须 veto, paper/shadow 用默认值占位 (仅模拟, 不触达 broker)
    price_missing = price is None or price <= 0
    if price_missing:
        # paper/shadow 模式允许用占位价格继续生成计划 (不触达 broker)
        # auto 模式由 L2 风控 (price_missing_check) 硬 veto, 防止以默认价灾难性下单
        price = 10.0
        logger.warning(
            "[ExecBridge] %s 价格缺失 (price=%s), auto 模式将被 L2 风控 veto",
            decision.symbol,
            price,
        )
    # 类型窄化: price_missing=False 时 price 非 None 且 > 0; =True 时已赋值 10.0
    # 用 cast 替代 assert 以满足 mypy, 避免 python -O 下断言被剥离 (assert 仅静态语义, 无运行时校验需求)
    from typing import cast

    price = cast(float, price)

    # 仓位计算: 组合净值 * 单笔上限 * 信号强度绝对值 * 置信度
    # P0 修复: 仅当 strength 和 confidence 都有效时才计算仓位, 否则不强制最小仓位
    if abs(decision.strength) < 1e-6 or decision.confidence <= 0:
        allocation = 0.0
    else:
        allocation = (
            portfolio_value
            * max_single_pct
            * abs(decision.strength)
            * decision.confidence
        )
        allocation = max(allocation, portfolio_value * 0.001)  # 最少 0.1% 净值

    raw_qty = max(int(allocation / price), 100)  # A股最小 100 股
    qty = (raw_qty // 100) * 100  # 调整为 100 的整数倍 (A股交易单位)

    side = "SELL" if decision.action == "sell" else "BUY"
    price_type = "LIMIT"  # A股默认限价单

    # 分片信息 (TWAP 模拟, 大盘单笔不拆, 中小盘分 3 片)
    # 简化: 根据信号强度决定分片
    slices = 1 if abs(decision.strength) > 0.8 else (3 if qty > 1000 else 1)

    # P0-1 修复 (2026-09-11): OrderRouter.route_order 要求 execution_plan["slices"]
    # 为"可迭代的 slice dict 列表" (逐片生成订单, 读 slice_id/instrument/direction/size),
    # 而本函数此前产出 int 片数 → auto 模式每笔决策在路由处抛 TypeError 被宽捕获吞掉,
    # 整条 AI 执行链 100% 静默空转 (巡检 P0-1)。
    # 现同步产出 slices 列表 (契约对齐 automated_execution_system 再平衡路径);
    # num_slices 保留片数供灰度缩放读取 (原 int 值迁移到 num_slices, "slices" 键不再承载 int)。
    direction = "buy" if decision.action == "buy" else "sell"
    slice_list = []
    remaining = qty
    for i in range(slices):
        if i == slices - 1:
            size = remaining
        else:
            size = max(qty // slices, 100)
            remaining -= size
        slice_list.append(
            {
                "slice_id": i + 1,
                "size": size,
                "price": price,
                "direction": direction,
                "instrument": decision.symbol,
                "price_type": price_type.lower(),
                "total_slices": slices,
                "slice_index": i + 1,
            }
        )

    # 保留首片 dict 兼容旧读法 (灰度缩放只改 slice_info["size"])
    slice_info = slice_list[0]

    # 根据调整后的 qty 重新计算名义金额
    actual_notional = round(qty * price, 2)
    execution_plan = {
        "symbol": decision.symbol,
        "side": side,
        "qty": qty,
        "price_type": price_type,
        "limit_price": round(price, 2),
        "price_missing": price_missing,  # 价格缺失标记 (L2 风控用于 auto 模式硬 veto)
        "slice_info": slice_info,
        "slices": slice_list,  # P0-1: list[dict] (OrderRouter 契约); 片数见 num_slices
        "num_slices": slices,
        "notional": actual_notional,  # 使用实际成交金额
        "decision_id": f"{decision.symbol}_{decision.timestamp}",
        "ai_confidence": round(decision.confidence, 4),
        "ai_strength": round(decision.strength, 4),
        "verdict_type": decision.verdict_type,
        "generated_at": datetime.now().isoformat(),
    }

    logger.info(
        "生成执行计划: %s %s %d股 @%.2f, 名义金额=%.2f, %d片",
        execution_plan["symbol"],
        execution_plan["side"],
        execution_plan["qty"],
        execution_plan["limit_price"],
        execution_plan["notional"],
        slices,
    )
    return execution_plan


# ============================================================
# 核心桥接函数 - 模式分派
# ============================================================


def _dispatch_execution_mode(
    decision: TradingDecision,
    execution_plan: dict[str, Any],
    mode: str,
    price: float | None,
    order_router: Any,
    broker: Any,
    market_state: str,
    tca_pre_estimate: dict[str, Any] | None,
    tca_error: str,
    risk_result: ExecutionRiskResult,
) -> tuple[dict[str, Any] | None, str, bool, str, bool, str]:
    """模式分派: shadow / paper / auto / unknown

    Returns:
        (execution_result, msg, veto, veto_reason, mode_escalation, mode_escalation_reason)
        veto 为 True 时表示灰度回滚到 0, 需由调用方构建最终返回。
    """
    execution_result: dict[str, Any] | None = None
    msg = ""
    veto = False
    veto_reason = ""
    mode_escalation = False
    mode_escalation_reason = ""

    if mode == "shadow":
        msg = f"[SHADOW] {decision.symbol} {decision.action} 仅记录, 不执行"
        logger.info(msg)

    elif mode == "paper":
        simulated_fill = _simulate_fill(execution_plan, price or 10.0)
        execution_result = simulated_fill
        msg = f"[PAPER] {decision.symbol} {decision.action} 模拟成交 @{simulated_fill.get('avg_price', 0):.2f}"
        logger.info(msg)

    elif mode == "auto":
        gs = GrayscaleState.load()
        should_rb, rb_reason = gs.should_rollback()
        if should_rb:
            new_stage = gs.do_rollback()
            msg = f"[AUTO] 触发回滚 {gs.stage} -> {new_stage}: {rb_reason}"
            logger.warning(msg)
            effective_pct = gs.effective_allocation_pct()
            if effective_pct == 0:
                veto = True
                veto_reason = f"灰度回滚到 {new_stage}, 暂停执行"
                mode_escalation = True
                mode_escalation_reason = (
                    f"灰度回滚至 {new_stage}, 暂停执行: {rb_reason}"
                )
                return (
                    execution_result,
                    msg,
                    veto,
                    veto_reason,
                    mode_escalation,
                    mode_escalation_reason,
                )

        if order_router is None or broker is None:
            msg = "[AUTO] 缺少 OrderRouter/broker, 降级为 paper 执行"
            logger.warning(msg)
            execution_result = _simulate_fill(execution_plan, price or 10.0)
        else:
            effective_pct = gs.effective_allocation_pct()
            if 0 < effective_pct < 1.0:
                original_qty = execution_plan.get("qty", 0)
                scaled_qty = int(original_qty * effective_pct)
                scaled_qty = max((scaled_qty // 100) * 100, 100)
                if scaled_qty != original_qty:
                    original_price = execution_plan.get("limit_price", 0)
                    execution_plan = dict(execution_plan)
                    execution_plan["qty"] = scaled_qty
                    execution_plan["notional"] = round(scaled_qty * original_price, 2)
                    if "slice_info" in execution_plan:
                        # P0-1 修复: slices 现为 list[dict], 片数改读 num_slices
                        num_slices = int(execution_plan.get("num_slices", 1) or 1)
                        new_slice_size = max(scaled_qty // num_slices, 100)
                        execution_plan["slice_info"] = {
                            **execution_plan["slice_info"],
                            "size": new_slice_size,
                        }
                    logger.info(
                        "[GRAYSCALE] %s 阶段缩放: qty %d -> %d (%.0f%%), "
                        "notional %.2f -> %.2f",
                        gs.stage,
                        original_qty,
                        scaled_qty,
                        effective_pct * 100,
                        original_qty * original_price,
                        execution_plan["notional"],
                    )

            try:
                start = time.perf_counter()
                result = order_router.route_order(execution_plan, market_state)
                elapsed = time.perf_counter() - start
                execution_result = {
                    "success": result.get("success", False),
                    "routed_orders": result.get("routed_orders", []),
                    "target_pool": result.get("target_pool", ""),
                    # P0-2 修复 (2026-09-11): 透传路由层 error, 否则
                    # mode_escalation_reason 恒为误导性默认值 "broker 拒单"
                    "error": result.get("error", ""),
                    "elapsed_seconds": round(elapsed, 4),
                }
                if execution_result["success"]:
                    msg = (
                        f"[AUTO] {decision.symbol} {decision.action} "
                        f"已下单, 耗时 {elapsed:.3f}s, "
                        f"路由 {len(execution_result['routed_orders'])} 笔"
                    )
                else:
                    mode_escalation = True
                    mode_escalation_reason = (
                        f"下单失败: {execution_result.get('error', 'broker 拒单')}"
                    )
                    msg = f"[AUTO] {decision.symbol} {decision.action} 下单失败"
                logger.info(msg)
            except (
                TimeoutError,
                ConnectionError,
                OSError,
                ValueError,
                KeyError,
                RuntimeError,
            ) as exc:
                # 下单路径可能抛出的具体异常: 网络超时/连接错误/参数错误/路由失败
                logger.error("下单异常: %s", exc)
                mode_escalation = True
                mode_escalation_reason = f"下单异常: {exc}"
                execution_result = {"success": False, "error": str(exc)}
                msg = f"[AUTO] 下单异常: {exc}"

    else:
        mode_escalation = True
        mode_escalation_reason = f"未知模式 {mode}, 按 shadow 处理"
        msg = f"[{mode}] 未知模式, 按 shadow 处理"

    return (
        execution_result,
        msg,
        veto,
        veto_reason,
        mode_escalation,
        mode_escalation_reason,
    )


# ============================================================
# 核心桥接函数
# ============================================================


def execute_decision(
    decision: TradingDecision,
    portfolio_value: float = 1_000_000.0,
    price: float | None = None,
    market_state: str = "normal",
    order_router: Any = None,
    broker: Any = None,
    force_mode: str | None = None,
    risk_context: RiskContext | None = None,
    # 步骤 2: TCA 双轨参数 (Feature Flag 控制, 默认 None=不启用)
    tca_pre_trade_estimator: Any | None = None,
    tca_post_trade_manager: Any | None = None,
    market_data_for_tca: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将 TradingDecision 转化为执行指令并 (可选) 下单

    这是从 AI 决策到 broker 的唯一桥梁。分四步:
      Step A:  生成执行计划
      Step B:  L2 执行层硬风控 (二次校验, 复用 L1 run_hard_risk)
      Step B+: TCA 执行前预筛 (步骤 2, Feature Flag 控制, 高成本订单升级人工)
      Step C:  按模式分派 (shadow→仅日志, paper→模拟指令, auto→灰度→真实下单)
      Step D:  TCA 事后归因 (步骤 2, 仅执行成功后, 写入审计)

    Args:
        decision: 经 decision_gate 处理后的 TradingDecision
        portfolio_value: 组合净值
        price: 当前参考价格
        market_state: 市场状态 normal/volatile/illiquid/stress/crisis
        order_router: OrderRouter 实例 (auto 模式需要)
        broker: BrokerAPI 实例 (auto 模式需要)
        force_mode: 强制覆盖模式 (用于测试)
        risk_context: L1 风控运行态数据; 传入则 L2 复用 run_hard_risk()
            覆盖黑名单/涨跌停/日内累计检查 (路线图: 不重复造轮子)
        tca_pre_trade_estimator: PreTradeEstimator 实例 (步骤 2);
            None 或 Feature Flag 关闭时不预筛. 预筛否决是软阈值 (escalation)
        tca_post_trade_manager: TCAManager 实例 (步骤 2);
            None 或 Feature Flag 关闭时不归因. 仅执行成功后调用
        market_data_for_tca: TCA 所需市场数据 {adv, volatility, market_cap,
            decision_price, arrival_price, vwap, close_price}; 缺失时降级跳过
    Returns:
        {
            'executed': bool,
            'mode': str,
            'execution_plan': dict,
            'execution_result': dict or None,
            'risk_result': dict,
            'audit_path': str,
            'message': str,
            'veto': bool,                  # 硬风控否决 (L1/L2 直接拦截)
            'veto_reason': str,            # 硬否决原因
            'escalation': bool,            # 软阈值升级 (需人工确认, 含执行异常)
            'escalation_reason': str,      # 升级原因
            'tca_pre_estimate': dict|None, # TCA 执行前预估 (步骤 2)
            'tca_post_report': dict|None,  # TCA 事后归因报告 (步骤 2)
            'tca_error': str,              # TCA 异常信息 (不阻断主路径)
        }
    """
    mode = force_mode or decision.mode

    # ===== 步骤 1: 初始化 escalation 从 decision.escalation 继承 (保留 L1 已设) =====
    escalation: bool = bool(decision.escalation)
    escalation_reason: str = decision.escalation_reason or ""

    # ===== 步骤 2: 初始化 TCA 双轨变量 (Feature Flag 控制, 默认 None=不启用) =====
    tca_pre_estimate: dict[str, Any] | None = None
    tca_post_report: dict[str, Any] | None = None
    tca_error: str = ""

    # ===== Step A: 生成执行计划 =====
    execution_plan = _generate_execution_plan(
        decision,
        portfolio_value,
        price,
        max_single_pct=float(get_config("gate.max_single_pct", 0.02)),
    )

    # ===== Step B: L2 执行层硬风控 (不可绕过) =====
    risk_result = _execution_risk_check(
        execution_plan,
        market_state,
        portfolio_value,
        risk_context=risk_context,
        decision=decision,
        mode=mode,
    )

    if risk_result.veto:
        escalation = True
        escalation_reason = f"L2 风控否决: {risk_result.veto_reason}"
        return _build_l2_veto_return(
            decision, mode, risk_result, escalation, escalation_reason, execution_plan
        )

    # ===== Step B+: TCA 执行前预筛 (步骤 2, Feature Flag 控制) =====
    tca_pre_estimate, pre_escalation, pre_escalation_reason, tca_error = (
        _run_tca_pre_trade(
            decision, execution_plan, market_data_for_tca, tca_pre_trade_estimator
        )
    )
    if pre_escalation:
        escalation = True
        escalation_reason = pre_escalation_reason

    # ===== Step C: 按模式分派 =====
    (
        execution_result,
        msg,
        grayscale_veto,
        veto_reason,
        mode_escalation,
        mode_escalation_reason,
    ) = _dispatch_execution_mode(
        decision,
        execution_plan,
        mode,
        price,
        order_router,
        broker,
        market_state,
        tca_pre_estimate,
        tca_error,
        risk_result,
    )

    if grayscale_veto:
        veto = True
        escalation = True
        escalation_reason = mode_escalation_reason
        return _build_grayscale_veto_return(
            decision,
            mode,
            execution_plan,
            risk_result,
            tca_pre_estimate,
            tca_error,
            veto_reason,
            escalation,
            escalation_reason,
            msg,
        )

    if mode_escalation:
        escalation = True
        escalation_reason = mode_escalation_reason

    # ===== Step D: TCA 事后归因 (步骤 2, 仅执行成功后, Feature Flag 控制) =====
    if execution_result is not None and execution_result.get("success", True):
        tca_post_report, post_tca_error = _run_tca_post_trade(
            tca_post_trade_manager,
            execution_plan,
            execution_result,
            market_data_for_tca,
            decision,
        )
        if post_tca_error:
            tca_error = (
                f"{tca_error}; {post_tca_error}" if tca_error else post_tca_error
            )

    # ===== 写入执行审计 =====
    veto = False
    veto_reason = ""
    record = _build_success_audit_record(
        decision,
        mode,
        execution_plan,
        execution_result,
        risk_result,
        veto,
        veto_reason,
        escalation,
        escalation_reason,
        tca_pre_estimate,
        tca_post_report,
        tca_error,
        msg,
    )
    audit_path = _write_execution_audit(record)

    return _build_success_return(
        decision,
        mode,
        execution_plan,
        execution_result,
        risk_result,
        audit_path,
        msg,
        veto,
        veto_reason,
        escalation,
        escalation_reason,
        tca_pre_estimate,
        tca_post_report,
        tca_error,
    )


def _simulate_fill(plan: dict[str, Any], ref_price: float) -> dict[str, Any]:
    """模拟成交 (paper 模式) — 带 A 股滑点模型"""
    import random

    qty = plan.get("qty", 0)
    # 模拟滑点: 大盘 2bp, 中小盘 5bp (保守取 5bp)
    slippage_bps = random.uniform(2, 5)
    side_mult = 1 if plan.get("side") == "BUY" else -1
    fill_price = ref_price * (1 + side_mult * slippage_bps / 10000)
    return {
        "success": True,
        "filled_size": qty,
        "average_price": round(fill_price, 4),
        "slippage_bps": round(slippage_bps, 2),
        "notional": round(qty * fill_price, 2),
        "timestamp": datetime.now().isoformat(),
        "is_live": False,
    }
