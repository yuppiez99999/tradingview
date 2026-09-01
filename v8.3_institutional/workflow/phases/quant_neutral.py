"""Phase 4.7: 量化市场中性策略 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1238-L1444 (phase_quant_neutral + 5 个私有方法)

搬移内容:
- phase_quant_neutral: 7 因子选股 + IC 期货对冲 (月度调仓)
- _load_quant_neutral_holdings: 加载量化中性策略的多头持仓
- _get_ic_price: 获取 IC 期货价格
- _get_ic_basis: 获取 IC 基差
- _get_current_ic_contracts: 获取当前 IC 空头合约数
- _load_strategy_drawdown_state: 加载策略回撤状态

模块级依赖 (动态查找, 兼容 daily_workflow 作为 __main__/模块导入):
- V10_STRATEGY_READY, V10ConfigLoader, QuantNeutralRunner
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
_dw = get_dw_module()
BASE_DIR: Path = (
    getattr(_dw, "BASE_DIR", Path(__file__).resolve().parent.parent)
    if _dw
    else Path(__file__).resolve().parent.parent
)


# ============================================================
# 私有辅助方法 (从 daily_workflow.py 搬移, 零行为变更)
# ============================================================


def _load_quant_neutral_holdings() -> list[dict]:
    """加载量化中性策略的当前多头持仓"""
    try:
        positions_path = BASE_DIR.parent / "config" / "quant_neutral_positions.json"
        if positions_path.exists():
            with open(positions_path, encoding="utf-8") as f:
                return json.load(f).get("long_positions", [])
    except Exception:
        pass
    return []


def _get_ic_price() -> float:
    """获取 IC 期货价格"""
    try:
        positions_path = BASE_DIR.parent / "config" / "positions.json"
        if positions_path.exists():
            with open(positions_path, encoding="utf-8") as f:
                data = json.load(f)
            # 从 positions.json 中查找 IC 期货价格
            for pos in data.get("positions", []):
                if pos.get("code") == "IC" or pos.get("symbol", "").startswith("IC"):
                    return float(pos.get("price", 5500.0))
    except Exception:
        pass
    return 5500.0  # 默认 IC 价格


def _get_ic_basis() -> float | None:
    """获取 IC 基差 (正=贴水, 负=升水)"""
    try:
        # 从市场数据中获取 IC 基差
        # 简化: 默认无基差警告
        return 0.0
    except Exception:  # fail-safe
        return None


def _get_current_ic_contracts() -> int:
    """获取当前持有的 IC 空头合约数"""
    try:
        positions_path = BASE_DIR.parent / "config" / "quant_neutral_positions.json"
        if positions_path.exists():
            with open(positions_path, encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("ic_short_contracts", 0))
    except Exception:
        pass
    return 0


def _load_strategy_drawdown_state(strategy_name: str) -> tuple[float, float, int]:
    """加载策略回撤状态

    Returns:
        (当前回撤百分比, 历史 95% 分位回撤, 连续回撤超限月数)
    """
    try:
        state_path = BASE_DIR.parent / "config" / f"{strategy_name}_state.json"
        if state_path.exists():
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
            return (
                float(data.get("current_drawdown_pct", 0.0)),
                float(data.get("history_95pct_drawdown", 0.05)),
                int(data.get("consecutive_overdrawdown_months", 0)),
            )
    except Exception:
        pass
    return (0.0, 0.05, 0)


# ============================================================
# phase_quant_neutral: 主 phase 方法 (从 daily_workflow.py L1238-L1377 原样搬移)
# ============================================================


def phase_quant_neutral(ctx: WorkflowContext) -> dict[str, Any]:
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
    # 动态查找模块级符号 (兼容 monkeypatch 对 daily_workflow 模块的 patch)
    V10_STRATEGY_READY = (
        bool(getattr(_dw, "V10_STRATEGY_READY", False)) if _dw else False
    )
    V10ConfigLoader = getattr(_dw, "V10ConfigLoader", None) if _dw else None
    QuantNeutralRunner = getattr(_dw, "QuantNeutralRunner", None) if _dw else None

    logger.info("=" * 60)
    logger.info("Phase 4.7: 量化市场中性策略 (月度调仓 + IC 对冲)")
    logger.info("=" * 60)

    result: dict[str, Any] = {
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
        ctx.state["phases"]["quant_neutral"] = result
        return result

    try:
        # 1. 检查是否调仓日 (每月最后一个交易日)
        today = date.today()
        is_month_end = today.day >= 25  # 简化: 25 日后视为月末窗口
        if not is_month_end:
            result["action"] = "skip"
            result["reason"] = f"非月末调仓窗口 (今日 {today.day} 日 < 25)"
            logger.info(f"[QuantNeutral] {result['reason']}, 跳过月度调仓")
            ctx.state["phases"]["quant_neutral"] = result
            return result

        # 2. 加载候选股票池 (从 v10.0 配置)
        if V10ConfigLoader is None:
            raise ImportError("V10ConfigLoader 未加载")
        v10_loader = V10ConfigLoader()
        stock_positions = v10_loader.get_stock_positions()

        # 构建候选池 (使用配置中的股票作为简化示例)
        # 实际生产环境应从 Wind/AKShare 获取全市场前 20% 股票
        candidate_universe = []
        for pos in stock_positions:
            candidate_universe.append(
                {
                    "code": pos.get("code", ""),
                    "name": pos.get("name", ""),
                    "returns_20d": 0.05,  # 占位, 实际应从行情接口获取
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
                }
            )

        # 3. 加载当前持仓
        current_holdings = _load_quant_neutral_holdings()

        # 4. 获取 IC 期货价格 (从 positions.json 或实时行情)
        ic_price = _get_ic_price()

        # 5. 获取 IC 基差
        basis = _get_ic_basis()

        # 6. 获取策略历史回撤
        strategy_dd_pct, history_95pct_dd, consecutive_months = (
            _load_strategy_drawdown_state("quant_neutral")
        )

        # 7. 执行月度调仓
        if QuantNeutralRunner is None:
            raise ImportError("QuantNeutralRunner 未加载")
        runner = QuantNeutralRunner()
        qn_result = runner.run_monthly_rebalance(
            candidate_universe=candidate_universe,
            current_holdings=current_holdings,
            current_ic_contracts=_get_current_ic_contracts(),
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
        result.update(
            {
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
            }
        )

        # 10. 紧急风控: 暂停策略 → 触发清仓
        if qn_result.action == "pause":
            logger.warning(f"[QuantNeutral] 策略暂停: {qn_result.reason}")
            result["status"] = "ALERT"

    except Exception as e:  # fail-safe
        logger.error(f"[QuantNeutral] 月度调仓失败: {e}", exc_info=True)
        result["status"] = "ERROR"
        result["reason"] = str(e)

    # === 写入 state ===
    ctx.state["phases"]["quant_neutral"] = result
    logger.info("-" * 60)
    logger.info(
        "Phase 4.7 完成: 动作=%s, 做多=%d 只, 净敞口=%.3f",
        result.get("action", ""),
        result.get("long_count", 0),
        result.get("net_exposure", 0),
    )
    logger.info("=" * 60)
    return result
