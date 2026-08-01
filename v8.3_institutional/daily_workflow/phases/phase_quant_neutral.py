#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_quant_neutral

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_quant_neutral phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_quant_neutral(workflow) -> Dict[str, Any]:
    """量化市场中性策略 — 7 因子选股 + IC 期货对冲

    v10.0 投资计划 quant_neutral_account (70 万资金, 140 万名义敞口):
        - 做多 25 只因子 top 20% 股票
        - 做空 IC 期货对冲, 目标 beta 0.05
        - 月度调仓, 换手率 150%

    触发条件:
        - 每月最后一个交易日执行 (默认)
        - 或 IC 基差 > 1.5% 时触发减仓评估

    Returns:
        量化中性调仓结果
    """
    logger.info("=" * 60)
    logger.info("Phase 4.7: 量化市场中性策略 (月度调仓 + IC 对冲)")
    logger.info("=" * 60)

    result: Dict[str, Any] = {
        "status": "PASS",
        "action": "skip",
        "reason": "",
        "long_count": 0,
        "long_market_value": 0.0,
        "portfolio_beta": 0.0,
        "net_exposure": 0.0,
        "ic_hedge": {},
        "drawdown_action": "normal",
    }

    if not V10_STRATEGY_READY:
        result["status"] = "SKIP"
        result["reason"] = "v10.0 策略模块未加载"
        logger.warning("[QuantNeutral] v10.0 策略模块未加载, 跳过")
        workflow.state["phases"]["quant_neutral"] = result
        return result

    try:
        # 1. 检查是否调仓日 (每月最后一个交易日)
        today = date.today()
        is_month_end = today.day >= 25  # 简化: 25 日后视为月末窗口
        if not is_month_end:
            result["action"] = "skip"
            result["reason"] = f"非月末调仓窗口 (今日 {today.day} 日 < 25)"
            logger.info(f"[QuantNeutral] {result['reason']}, 跳过月度调仓")
            workflow.state["phases"]["quant_neutral"] = result
            return result

        # 2. 加载候选股票池 (从 v10.0 配置)
        v10_loader = V10ConfigLoader()
        stock_positions = v10_loader.get_stock_positions()

        # 构建候选池 (使用配置中的股票作为简化示例)
        # 实际生产环境应从 Wind/AKShare 获取全市场前 20% 股票
        candidate_universe = []
        for pos in stock_positions:
            candidate_universe.append({
                "code": pos.get("code", ""),
                "name": pos.get("name", ""),
                "returns_20d": 0.05,            # 占位, 实际应从行情接口获取
                "returns_5d": -0.02,
                "volatility_60d": 0.25,
                "avg_turnover_amount": 50_000_000,
                "roe": 0.15,
                "cashflow_ratio": 0.85,
                "revenue_growth": 0.20,
                "profit_growth": 0.18,
                "pe_percentile": 0.45,
                "pb_percentile": 0.30,
                "beta": 1.0,
            })

        # 3. 加载当前持仓
        current_holdings = workflow._load_quant_neutral_holdings()

        # 4. 获取 IC 期货价格 (从 positions.json 或实时行情)
        ic_price = workflow._get_ic_price()

        # 5. 获取 IC 基差
        basis = workflow._get_ic_basis()

        # 6. 获取策略历史回撤
        strategy_dd_pct, history_95pct_dd, consecutive_months = workflow._load_strategy_drawdown_state("quant_neutral")

        # 7. 执行月度调仓
        runner = QuantNeutralRunner()
        qn_result = runner.run_monthly_rebalance(
            candidate_universe=candidate_universe,
            current_holdings=current_holdings,
            current_ic_contracts=workflow._get_current_ic_contracts(),
            ic_price=ic_price,
            basis=basis,
            strategy_drawdown_pct=strategy_dd_pct,
            strategy_history_95pct_drawdown=history_95pct_dd,
            consecutive_overdrawdown_months=consecutive_months,
            trade_date=today,
        )

        # 8. 输出摘要
        summary = runner.summary(qn_result)
        logger.info("\n" + summary)

        # 9. 更新结果
        result.update({
            "status": "PASS",
            "action": qn_result.action,
            "reason": qn_result.reason,
            "long_count": qn_result.long_count,
            "long_market_value": qn_result.long_market_value,
            "portfolio_beta": qn_result.portfolio_beta,
            "target_beta": qn_result.target_beta,
            "net_exposure": qn_result.net_exposure,
            "ic_hedge": qn_result.ic_hedge,
            "drawdown_action": qn_result.drawdown_action,
            "basis_warning": qn_result.basis_warning,
            "turnover_achieved": qn_result.turnover_achieved,
            "candidate_count": qn_result.candidate_count,
            "factors_used": qn_result.factors_used,
        })

        # 10. 紧急风控: 暂停策略 → 触发清仓
        if qn_result.action == "pause":
            logger.warning(f"[QuantNeutral] 策略暂停: {qn_result.reason}")
            result["status"] = "ALERT"

    except Exception as e:
        logger.error(f"[QuantNeutral] 月度调仓失败: {e}", exc_info=True)
        result["status"] = "ERROR"
        result["reason"] = str(e)

    # === 写入 state ===
    workflow.state["phases"]["quant_neutral"] = result
    logger.info("-" * 60)
    logger.info("Phase 4.7 完成: 动作=%s, 做多=%d 只, 净敞口=%.3f",
                result.get("action", ""),
                result.get("long_count", 0),
                result.get("net_exposure", 0))
    logger.info("=" * 60)
    return result


