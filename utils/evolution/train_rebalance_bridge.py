"""训练→进化→再平衡串联桥接 (ER-1.2, Wave 7-ERL Sprint 1)

把训练器 ER-1.1 提供的 ``post_train_callback`` 钩子连到:
    训练完成 → EvolutionOrchestratorV2.run_cycle() → run_daily_rebalance()

Feature Flag 控制:
    - USE_EVOLUTION_ORCHESTRATOR: 控制进化循环 (G1)
    - USE_EOD_REBALANCE:         控制再平衡 (G1 联动)

设计原则 (与 cairn/evolution-rebalance-loop.md §13.4 一致):
    1. 优雅降级: 回调异常不阻断训练主流程 (fail-safe)
    2. Feature Flag 控制: flag 关闭时返回 disabled, 行为不变
    3. 乘子约束: weight_adjustments 仍限 [0.5, 2.0] (由 EvolutionOrchestratorV2 内部保证)
    4. 向后兼容: callback=None 时训练行为不变 (由 invoke_post_train_callback 保证)
    5. 审计优先: 每次串联触发写入 JSONL 审计日志

用法:
    from utils.evolution.train_rebalance_bridge import make_train_evolution_rebalance_callback
    from autolearn_trainer import invoke_post_train_callback

    callback = make_train_evolution_rebalance_callback(
        positions_provider=lambda: {...},
        prices_provider=lambda: {...},
    )
    invoke_post_train_callback(callback, train_result)

依赖注记 (2026-08-27, ROADMAP ER-1.2):
    自动重训触发源与 B3 USE_AUTO_RETRAIN (冻结中) 强相关;
    B3 未启用前先以显式训练事件触发 (post_train_callback), 不因 B3 冻结而阻塞.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("train_rebalance_bridge")

# ============================================================
# 路径
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVOLUTION_REPORT_DIR = PROJECT_ROOT / "reports" / "evolution"
BRIDGE_AUDIT_LOG = EVOLUTION_REPORT_DIR / "train_rebalance_bridge.jsonl"

# Feature Flag 名称 (与 utils/evolution/orchestrator.py + feature_flags.yaml 对齐)
FLAG_EVOLUTION = "USE_EVOLUTION_ORCHESTRATOR"
FLAG_REBALANCE = "USE_EOD_REBALANCE"

# 乘子约束 (与 hedge_rebalance_integrator._load_evolution_factor_weights 一致)
WEIGHT_MULTIPLIER_MIN = 0.5
WEIGHT_MULTIPLIER_MAX = 2.0


# ============================================================
# 审计日志
# ============================================================
def _append_audit(record: dict[str, Any]) -> None:
    """追加审计记录到 JSONL (fail-open, 写入失败仅告警)."""
    try:
        EVOLUTION_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        with BRIDGE_AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except (OSError, ValueError, TypeError) as e:
        logger.warning("审计日志写入失败 (fail-open): %s", e)


def _now_iso() -> str:
    """当前时间 ISO 格式."""
    return datetime.now().isoformat(timespec="seconds")


# ============================================================
# Feature Flag 检查
# ============================================================
def _is_flag_enabled(name: str) -> bool:
    """检查 Feature Flag (fail-safe, 异常返回 False)."""
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled(name))
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        ImportError,
    ) as e:
        logger.warning("Feature Flag 检查失败 (%s), 默认禁用: %s", name, e)
        return False


# ============================================================
# 进化循环
# ============================================================
def _run_evolution_cycle() -> dict[str, Any]:
    """运行一次 EvolutionOrchestratorV2.run_cycle().

    Returns:
        CycleResult.to_dict() 或降级字典.
    """
    try:
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        orchestrator = EvolutionOrchestratorV2()
        if not orchestrator.enabled:
            return {"status": "disabled", "reason": f"{FLAG_EVOLUTION}=false"}
        cycle_result = orchestrator.run_cycle()
        result_dict = (
            cycle_result.to_dict()
            if hasattr(cycle_result, "to_dict")
            else {"status": "ok"}
        )
        logger.info("[Bridge] 进化循环完成: status=%s", result_dict.get("status"))
        return result_dict
    except (
        ImportError,
        ValueError,
        TypeError,
        OSError,
        AttributeError,
        RuntimeError,
    ) as e:
        logger.warning("[Bridge] 进化循环失败 (fail-safe 降级): %s", e)
        return {"status": "degraded", "error": str(e)}


# ============================================================
# 再平衡
# ============================================================
def _run_rebalance(
    positions: dict[str, dict] | None,
    prices: dict[str, float] | None,
    trade_date: str | None,
    current_drawdown: float = 0.0,
) -> dict[str, Any]:
    """运行 ETF 期权对冲再平衡.

    Args:
        positions: 持仓字典 {code: {weight, ...}}; None 时跳过再平衡
        prices: 价格字典 {code: price}; None 时跳过再平衡
        trade_date: 交易日期; None 时用今日
        current_drawdown: 当前回撤

    Returns:
        plan.to_dict() 或降级字典.
    """
    if positions is None or prices is None:
        return {
            "status": "skipped",
            "reason": "positions/prices 不可用 (降级跳过再平衡)",
        }

    try:
        from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

        rebalancer = ETFOptionHedgeRebalancer()
        plan = rebalancer.run_daily_rebalance(
            positions=positions,
            prices=prices,
            trade_date=trade_date or datetime.now().strftime("%Y-%m-%d"),
            current_drawdown=float(current_drawdown),
        )
        plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else {"status": "ok"}
        logger.info("[Bridge] 再平衡完成: date=%s", trade_date)
        return plan_dict
    except (
        ImportError,
        ValueError,
        TypeError,
        OSError,
        AttributeError,
        RuntimeError,
    ) as e:
        logger.warning("[Bridge] 再平衡失败 (fail-safe 降级): %s", e)
        return {"status": "degraded", "error": str(e)}


# ============================================================
# 乘子约束 (与 hedge_rebalance_integrator 对齐)
# ============================================================
def _clamp_weight_adjustments(
    adjustments: dict[str, Any] | None,
) -> dict[str, float]:
    """限制 weight_adjustments 乘子在 [0.5, 2.0] 之间.

    与 hedge_rebalance_integrator._load_evolution_factor_weights 逻辑一致,
    防止单次进化调整极端值导致再平衡风暴.

    Args:
        adjustments: {code: multiplier} 字典, 值可能为任意类型 (非数值会被跳过)
    """
    if not adjustments:
        return {}
    clamped: dict[str, float] = {}
    for code, mult in adjustments.items():
        try:
            val = float(mult)
        except (ValueError, TypeError) as e:
            logger.warning("weight_adjustments[%s]=%r 非数值, 跳过: %s", code, mult, e)
            continue
        if val < WEIGHT_MULTIPLIER_MIN:
            val = WEIGHT_MULTIPLIER_MIN
        elif val > WEIGHT_MULTIPLIER_MAX:
            val = WEIGHT_MULTIPLIER_MAX
        clamped[str(code)] = val
    return clamped


# ============================================================
# 串联桥接主逻辑
# ============================================================
def _run_train_evolution_rebalance(
    train_result: dict[str, Any],
    positions_provider: Callable[[], dict[str, dict] | None] | None,
    prices_provider: Callable[[], dict[str, float] | None] | None,
) -> dict[str, Any]:
    """训练→进化→再平衡串联核心逻辑.

    Step 1: 进化循环 (受 USE_EVOLUTION_ORCHESTRATOR 控制)
    Step 2: 再平衡 (受 USE_EOD_REBALANCE 控制, 需 positions/prices)

    Returns:
        串联结果摘要 (含 evolution_result + rebalance_result + weight_adjustments).
    """
    bridge_result: dict[str, Any] = {
        "bridge": "train_evolution_rebalance",
        "train_status": train_result.get("status", "unknown"),
        "timestamp": _now_iso(),
        "evolution": None,
        "rebalance": None,
        "weight_adjustments": {},
    }

    # Step 1: 进化循环
    if _is_flag_enabled(FLAG_EVOLUTION):
        evolution_result = _run_evolution_cycle()
        bridge_result["evolution"] = evolution_result
        # 提取并约束 weight_adjustments (进化→再平衡权重乘子)
        raw_adjustments = evolution_result.get("weight_adjustments", {})
        bridge_result["weight_adjustments"] = _clamp_weight_adjustments(raw_adjustments)
    else:
        bridge_result["evolution"] = {
            "status": "disabled",
            "reason": f"{FLAG_EVOLUTION}=false",
        }

    # Step 2: 再平衡 (需要 positions/prices 上下文)
    if _is_flag_enabled(FLAG_REBALANCE):
        positions = positions_provider() if positions_provider is not None else None
        prices = prices_provider() if prices_provider is not None else None
        trade_date = train_result.get("trade_date") or datetime.now().strftime(
            "%Y-%m-%d"
        )
        current_drawdown = float(train_result.get("current_drawdown", 0.0))
        rebalance_result = _run_rebalance(
            positions, prices, trade_date, current_drawdown
        )
        bridge_result["rebalance"] = rebalance_result
    else:
        bridge_result["rebalance"] = {
            "status": "disabled",
            "reason": f"{FLAG_REBALANCE}=false",
        }

    return bridge_result


# ============================================================
# 公共 API: 回调工厂
# ============================================================
def make_train_evolution_rebalance_callback(
    *,
    positions_provider: Callable[[], dict[str, dict] | None] | None = None,
    prices_provider: Callable[[], dict[str, float] | None] | None = None,
    enable_audit: bool = True,
) -> Callable[[dict[str, Any]], None]:
    """创建训练→进化→再平衡串联回调 (ER-1.2).

    返回的回调函数可作为训练器 ``post_train_callback`` 参数传入:
        训练完成 → 回调 → EvolutionOrchestratorV2.run_cycle() → run_daily_rebalance()

    Args:
        positions_provider: 持仓提供者 (无参 callable, 返回 {code: {weight,...}});
            None 时再平衡降级跳过 (仅运行进化循环)
        prices_provider: 价格提供者 (无参 callable, 返回 {code: price});
            None 时再平衡降级跳过
        enable_audit: 是否写审计日志 (默认 True)

    Returns:
        post_train_callback 回调函数, 接收训练结果 dict, 无返回值.

    设计:
        - fail-safe: 任何异常仅 logger.warning, 不阻断训练主流程
          (与 autolearn_trainer.invoke_post_train_callback 二级防护叠加)
        - Feature Flag 关闭时返回 disabled, 行为不变
        - B3 冻结期: 由显式训练事件触发 (post_train_callback), 不依赖 USE_AUTO_RETRAIN
    """
    # 捕获上下文快照 (不可变, 避免闭包漂移)
    captured_positions_provider = positions_provider
    captured_prices_provider = prices_provider
    captured_enable_audit = bool(enable_audit)

    def callback(train_result: dict[str, Any]) -> None:
        """训练→进化→再平衡串联回调."""
        try:
            bridge_result = _run_train_evolution_rebalance(
                train_result=train_result,
                positions_provider=captured_positions_provider,
                prices_provider=captured_prices_provider,
            )
            if captured_enable_audit:
                _append_audit(bridge_result)
            logger.info(
                "[Bridge] 串联完成: evolution=%s rebalance=%s",
                bridge_result.get("evolution", {}).get("status", "n/a"),
                bridge_result.get("rebalance", {}).get("status", "n/a"),
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            # fail-safe: 串联异常不阻断训练主流程
            logger.warning("[Bridge] 训练→进化→再平衡串联失败 (fail-safe 降级): %s", e)
            if captured_enable_audit:
                _append_audit(
                    {
                        "bridge": "train_evolution_rebalance",
                        "status": "degraded",
                        "error": str(e),
                        "timestamp": _now_iso(),
                    }
                )

    return callback
