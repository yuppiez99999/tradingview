# -*- coding: utf-8 -*-
"""KillSwitch L1 守卫工具.

GLM-5.2 C2(#22) 修复: trades 重建 (_regenerate_trades_from_weights) 后需重新应用
KillSwitch L1 约束 (can_open=False → 过滤 BUY, 仅保留 SELL), 防止 L1 (停止新开仓)
被重建逻辑绕过.

独立模块: 避免被 institutional_pipeline_runner 的重依赖 import 链拖入, 便于单元测试.
"""
import logging
from typing import Any, Dict

logger = logging.getLogger("killswitch_guard")


def apply_killswitch_l1_filter(decision, ks_result: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """重建 trades 后重新应用 KillSwitch L1 约束.

    问题: _regenerate_trades_from_weights 基于 target_weights 重建完整 BUY+SELL trades,
       完全无视 KillSwitch 的 can_open 约束, 覆盖了上层 _step_kill_switch_check 的 BUY 过滤,
       导致 L1 (停止新开仓) 被绕过.

    Args:
        decision: 含 .trades 属性的决策对象 (PortfolioDecision 或 duck-typed)
        ks_result: _step_kill_switch_check 返回结果 (含 can_open 字段)
        result: 运行结果 dict (用于记录过滤统计)

    Returns:
        更新后的 result dict
    """
    try:
        if ks_result is not None and not ks_result.get("can_open", True):
            trades = getattr(decision, "trades", [])
            before = len(trades)
            filtered = [t for t in trades if t.get("side") != "BUY"]
            after = len(filtered)
            decision.trades = filtered
            if before != after:
                logger.warning(
                    "[KillSwitchGuard] L1 生效: 过滤 %d 笔 BUY trades (剩余 %d)",
                    before - after, after,
                )
                result.setdefault("steps", {}).setdefault("killswitch_l1_filter", {
                    "filtered_buy": before - after, "remaining": after,
                })
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
        logger.error("[KillSwitchGuard] L1 过滤异常: %s", e, exc_info=True)
    return result