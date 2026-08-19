"""Phase 4.8: 现金管理 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1994-L2149

搬移内容:
- phase_cash_management: 主 phase 方法 (逆回购 + 货基 + 应急金 + 保证金追加)
- _load_cash_state: 加载当前现金状态 (仅 phase 内调用)
- _get_current_repo_rate: 获取逆回购利率 (仅 phase 内调用)
- _load_futures_account_state: 加载期货账户状态 (仅 phase 内调用)

模块级依赖:
- V10_STRATEGY_READY / CashManager 通过 get_dw_module() 从 daily_workflow 获取
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 (兼容条件导入) ===
_dw = get_dw_module()
BASE_DIR: Path = getattr(_dw, "BASE_DIR", Path(__file__).resolve().parent.parent) if _dw else Path(__file__).resolve().parent.parent
V10_STRATEGY_READY = getattr(_dw, "V10_STRATEGY_READY", False) if _dw else False

# 类 — 仅当 daily_workflow 模块中已导入时才引入
if _dw is not None and hasattr(_dw, "CashManager"):
    CashManager = _dw.CashManager


def phase_cash_management(ctx: WorkflowContext) -> dict[str, Any]:
    """现金管理 — 逆回购自动下单 + 应急金监控 + 保证金追加检查

    v10.0 投资计划 cash_management (130 万资金, 占总资本 26%):
        - 期货保证金 50 万 (维持率 ≥ 60%)
        - 期权抵押金 10 万
        - 应急保证金 30 万 (2 日内补足)
        - 逆回购 / 货基 40 万 (RCO001, 每日自动)

    自动化:
        - 每日 14:30 评估闲置资金
        - 闲置资金 > 1 万 → 自动下单 RCO001
        - 月末季末高利率期 → 加大投放 20%
        - 应急金动用 → 2 日内补足

    Returns:
        现金管理结果
    """
    logger.info("=" * 60)
    logger.info("Phase 4.8: 现金管理 (逆回购 + 货基 + 应急金)")
    logger.info("=" * 60)

    result: dict[str, Any] = {
        "status": "PASS",
        "action": "skip",
        "total_cash": 0.0,
        "reverse_repo_amount": 0.0,
        "estimated_daily_income": 0.0,
        "repo_order": {},
        "margin_call": {},
        "emergency_replenish": {},
    }

    if not V10_STRATEGY_READY:
        result["status"] = "SKIP"
        result["reason"] = "v10.0 现金管理模块未加载"
        logger.warning("[CashManager] v10.0 模块未加载, 跳过")
        ctx.state["phases"]["cash_management"] = result
        return result

    try:
        # 1. 加载当前现金状态
        total_cash, futures_margin_used, options_collateral_used, emergency_used = _load_cash_state()

        # 2. 获取当前逆回购利率
        repo_rate = _get_current_repo_rate()

        # 3. 执行闲置资金分配
        cm = CashManager()
        cm_result = cm.allocate_idle_cash(
            total_cash=total_cash,
            futures_margin_used=futures_margin_used,
            options_collateral_used=options_collateral_used,
            emergency_used=emergency_used,
            current_repo_rate=repo_rate,
            trade_date=date.today(),
        )

        # 4. 输出摘要
        summary = cm.summary(cm_result)
        logger.info("\n" + summary)

        # 5. 检查应急金补足
        if emergency_used > 0:
            replenish = cm.check_emergency_replenish(emergency_used)
            result["emergency_replenish"] = replenish
            if replenish.get("action") == "replenish_now":
                logger.warning(f"[CashManager] {replenish['reason']}")

        # 6. 检查期货保证金追加
        futures_account_value, futures_margin_used_actual = _load_futures_account_state()
        if futures_account_value > 0:
            margin_check = cm.check_margin_call(futures_account_value, futures_margin_used_actual)
            result["margin_call"] = margin_check
            if margin_check.get("action") != "no_action":
                logger.warning(f"[CashManager] {margin_check['reason']}")

        # 7. 更新结果
        result.update({
            "status": "PASS",
            "action": cm_result.action,
            "total_cash": cm_result.total_cash,
            "reverse_repo_amount": cm_result.reverse_repo,
            "estimated_daily_income": cm_result.estimated_daily_income,
            "estimated_annual_yield": cm_result.estimated_annual_yield,
            "repo_order": cm_result.repo_order,
            "is_month_end": cm_result.is_month_end,
            "is_quarter_end": cm_result.is_quarter_end,
            "futures_margin_ratio": cm_result.futures_margin_ratio,
            "emergency_replenish_needed": cm_result.emergency_replenish_needed,
        })

    except Exception as e:  # fail-safe: 现金管理失败不阻断主流程
        logger.error(f"[CashManager] 现金管理失败: {e}", exc_info=True)
        result["status"] = "ERROR"
        result["reason"] = str(e)

    # === 写入 state ===
    ctx.state["phases"]["cash_management"] = result
    logger.info("-" * 60)
    logger.info("Phase 4.8 完成: 动作=%s, 逆回购=¥%.0f, 日收益=¥%.2f",
                result.get("action", ""),
                result.get("reverse_repo_amount", 0),
                result.get("estimated_daily_income", 0))
    logger.info("=" * 60)
    return result


def _load_cash_state() -> tuple[float, float, float, float]:
    """加载当前现金状态

    Returns:
        (总现金, 期货已用保证金, 期权已用抵押金, 应急金已动用)
    """
    try:
        cash_path = BASE_DIR.parent / "config" / "cash_state.json"
        if cash_path.exists():
            with open(cash_path, encoding="utf-8") as f:
                data = json.load(f)
            return (
                float(data.get("total_cash", 1_300_000)),
                float(data.get("futures_margin_used", 480_000)),
                float(data.get("options_collateral_used", 10_000)),
                float(data.get("emergency_used", 0)),
            )
    except Exception:  # fail-safe: 现金状态加载失败用默认值
        pass
    # 默认值
    return (1_300_000, 480_000, 10_000, 0.0)


def _get_current_repo_rate() -> float:
    """获取当前逆回购利率 (RCO001)"""
    try:
        # 从市场数据获取
        # 简化: 默认 2.5%
        return 0.025
    except Exception:  # fail-safe: 利率获取失败用默认值
        return 0.025


def _load_futures_account_state() -> tuple[float, float]:
    """加载期货账户状态

    Returns:
        (期货账户权益, 已用保证金)
    """
    try:
        futures_path = BASE_DIR.parent / "config" / "futures_account.json"
        if futures_path.exists():
            with open(futures_path, encoding="utf-8") as f:
                data = json.load(f)
            return (
                float(data.get("account_value", 500_000)),
                float(data.get("margin_used", 0)),
            )
    except Exception:  # fail-safe: 期货账户状态加载失败用默认值
        pass
    return (500_000, 0.0)
