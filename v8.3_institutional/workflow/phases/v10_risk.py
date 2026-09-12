"""Phase 4.6: v10.0 风控 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1769-L2026

搬移内容:
- phase_v10_risk: 主 phase 方法 (回撤控制 + VaR 监控 + 压力测试 + 配置加载)
- _load_returns_history: 加载历史收益率序列 (仅 phase_v10_risk 内调用)
- _get_portfolio_positions_for_stress_test: 获取压力测试持仓 (跨 phase 调用, daily_workflow.py 保留转发)

模块级依赖:
- V10_RISK_READY / V10ConfigLoader / DrawdownController / VaRMonitor / StressTestRunner
  均通过 get_dw_module() 从 daily_workflow 获取 (兼容条件导入)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj
from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 (兼容条件导入) ===
_dw = get_dw_module()
BASE_DIR: Path = (
    getattr(_dw, "BASE_DIR", Path(__file__).resolve().parent.parent)
    if _dw
    else Path(__file__).resolve().parent.parent
)
V10_RISK_READY = getattr(_dw, "V10_RISK_READY", False) if _dw else False

# 类 — 仅当 daily_workflow 模块中已导入时才引入 (与原 daily_workflow.py 中 NameError 语义一致)
if _dw is not None and hasattr(_dw, "V10ConfigLoader"):
    V10ConfigLoader = _dw.V10ConfigLoader
if _dw is not None and hasattr(_dw, "DrawdownController"):
    DrawdownController = _dw.DrawdownController
if _dw is not None and hasattr(_dw, "VaRMonitor"):
    VaRMonitor = _dw.VaRMonitor
if _dw is not None and hasattr(_dw, "StressTestRunner"):
    StressTestRunner = _dw.StressTestRunner


def phase_v10_risk(ctx: WorkflowContext) -> dict[str, Any]:
    """v10.0 风控阶段 — 回撤控制 + VaR 监控 + 压力测试 + 配置加载"""
    logger.info("=" * 60)
    logger.info("Phase 4.6: v10.0 风控 (回撤控制 + VaR 监控 + 压力测试)")
    logger.info("=" * 60)

    result: dict[str, Any] = {
        "status": "PASS",
        "modules_loaded": V10_RISK_READY,
        "drawdown": {},
        "var": {},
        "stress_test": {},
        "v10_config": {},
    }

    if not V10_RISK_READY:
        logger.warning("v10.0 风控模块未加载, 跳过本阶段")
        result["status"] = "SKIP"
        result["reason"] = "modules_not_loaded"
        ctx.state["phases"]["v10_risk"] = result
        return result

    # === 1. 加载 v10.0 配置 ===
    try:
        v10_loader = V10ConfigLoader()
        phase = v10_loader.get_current_phase()
        allocation = v10_loader.get_allocation()
        daily_build_limit = v10_loader.get_daily_build_limit()
        logger.info(
            "[V10Config] 当前阶段: %s - %s, 每日建仓限额: ¥%.0f",
            phase.get("phase_key", "N/A"),
            phase.get("name", "N/A"),
            daily_build_limit,
        )
        result["v10_config"] = {
            "phase_key": phase.get("phase_key"),
            "phase_name": phase.get("name"),
            "daily_build_limit": daily_build_limit,
            "allocation": allocation,
        }
    except Exception as e:  # fail-safe: 配置加载失败不阻断
        logger.error(f"[V10Config] 加载失败: {e}", exc_info=True)
        result["v10_config"] = {"status": "ERROR", "error": str(e)}

    # === 2. 回撤控制 ===
    try:
        dc = DrawdownController()
        # 使用当前组合净值 (从 state 读取或用默认值)
        portfolio_value = float(ctx.state.get("portfolio_value", ctx.capital))
        peak_value = float(ctx.state.get("peak_value", ctx.capital))
        dd_result = dc.check_drawdown(peak_value, portfolio_value)
        dd_level = dd_result.get("level", 0)
        logger.info(
            "[Drawdown] 回撤级别: L%d (%s), 回撤: %.2f%%",
            dd_level,
            dd_result.get("level_name", "正常"),
            abs(dd_result.get("drawdown_pct", 0)) * 100,
        )
        result["drawdown"] = {
            "level": dd_level,
            "level_name": dd_result.get("level_name"),
            "drawdown_pct": dd_result.get("drawdown_pct"),
            "build_allowed": dd_result.get("build_allowed"),
            "spot_reduce_pct": dd_result.get("spot_reduce_pct"),
            "hedge_ratio_target": dd_result.get("hedge_ratio_target"),
            "actions": dd_result.get("actions", []),
        }
        if dd_level >= 2:
            logger.warning(
                "[Drawdown] L%d 触发! 禁止新开仓, 建议减仓 %.0f%%",
                dd_level,
                dd_result.get("spot_reduce_pct", 0) * 100,
            )
    except Exception as e:  # fail-safe: 回撤检查失败不阻断
        logger.error(f"[Drawdown] 检查失败: {e}", exc_info=True)
        result["drawdown"] = {"status": "ERROR", "error": str(e)}

    # === 3. VaR 监控 ===
    try:
        vm = VaRMonitor()
        # 尝试从 returns_history.json 加载历史收益率
        returns_history = _load_returns_history()
        portfolio_value = float(ctx.state.get("portfolio_value", ctx.capital))
        if returns_history and len(returns_history) >= 30:
            var_result = vm.calculate_var(returns_history, portfolio_value)
            logger.info(
                "[VaR] 95%%=%.2f%% (限 %.2f%%), 99%%=%.2f%% (限 %.2f%%), 超限=%s",
                var_result.get("var_95_pct", 0) * 100,
                vm.VAR_95_LIMIT_PCT * 100,
                var_result.get("var_99_pct", 0) * 100,
                vm.VAR_99_LIMIT_PCT * 100,
                var_result.get("any_breach", False),
            )
            result["var"] = {
                "var_95_pct": var_result.get("var_95_pct"),
                "var_99_pct": var_result.get("var_99_pct"),
                "var_95_breach": var_result.get("var_95_breach"),
                "var_99_breach": var_result.get("var_99_breach"),
                "any_breach": var_result.get("any_breach"),
                "actions": var_result.get("actions", []),
            }
        else:
            logger.info(
                "[VaR] 历史数据不足 (%d 日), 跳过 VaR 计算",
                len(returns_history) if returns_history else 0,
            )
            result["var"] = {"status": "SKIP", "reason": "insufficient_data"}
    except Exception as e:  # fail-safe: VaR 监控失败不阻断
        logger.error(f"[VaR] 监控失败: {e}", exc_info=True)
        result["var"] = {"status": "ERROR", "error": str(e)}

    # === 4. 压力测试 (季度执行, 其他时间跳过) ===
    try:
        today = now_bj()
        # 季度末 (3/6/9/12月最后一周) 执行
        is_quarter_end = today.month in (3, 6, 9, 12) and today.day >= 25
        if is_quarter_end:
            logger.info("[StressTest] 季度末, 执行压力测试")
            runner = StressTestRunner()
            positions = _get_portfolio_positions_for_stress_test()
            portfolio_value = float(ctx.state.get("portfolio_value", ctx.capital))
            stress_result = runner.run_all_scenarios(positions, portfolio_value)
            logger.info(
                "[StressTest] 结果: %s, 最差场景: %s (%.1f%%)",
                "全部通过" if stress_result.get("all_pass") else "有超限",
                stress_result.get("worst_scenario"),
                stress_result.get("worst_dd", 0) * 100,
            )
            result["stress_test"] = {
                "executed": True,
                "all_pass": stress_result.get("all_pass"),
                "worst_scenario": stress_result.get("worst_scenario"),
                "worst_dd": stress_result.get("worst_dd"),
                "report_path": stress_result.get("report_path"),
            }
        else:
            logger.info("[StressTest] 非季度末, 跳过压力测试")
            result["stress_test"] = {"executed": False, "reason": "not_quarter_end"}
    except Exception as e:  # fail-safe: 压力测试失败不阻断
        logger.error(f"[StressTest] 执行失败: {e}", exc_info=True)
        result["stress_test"] = {"status": "ERROR", "error": str(e)}

    # === 5. 十五五阶段季度评估 (PhaseManager 季度末触发) ===
    try:
        if ctx.phase_manager is not None:
            sim_date = (
                datetime.strptime(ctx.trade_date, "%Y-%m-%d").date()
                if ctx.trade_date
                else None
            )
            is_pm_quarter_end = ctx.phase_manager.is_quarter_end(sim_date)
            if is_pm_quarter_end:
                logger.info("[PhaseManager] 季度末, 触发十五五季度评估")
                positions_for_review = _get_portfolio_positions_for_stress_test()
                portfolio_value = float(ctx.state.get("portfolio_value", ctx.capital))
                review = ctx.phase_manager.trigger_quarterly_review(
                    positions=positions_for_review,
                    portfolio_value=portfolio_value,
                    today=sim_date,
                )
                logger.info(
                    "[PhaseManager] 季度评估完成: Q%s, 压测=%s, 调仓=%s, 动作数=%d",
                    review.quarter,
                    review.stress_test_triggered,
                    review.rebalance_needed,
                    len(review.actions),
                )
                for action in review.actions:
                    logger.info(f"  - {action}")
                result["quarterly_review"] = {
                    "executed": True,
                    "quarter": review.quarter,
                    "is_quarter_end": review.is_quarter_end,
                    "stress_test_triggered": review.stress_test_triggered,
                    "stress_test_result": review.stress_test_result,
                    "rebalance_needed": review.rebalance_needed,
                    "actions": review.actions,
                    "strategy_effectiveness": review.strategy_effectiveness,
                }
                # 2030 清仓年: 输出清仓动作
                if (
                    ctx.current_phase_info
                    and ctx.current_phase_info.is_liquidation_year
                ):
                    liq_actions = ctx.phase_manager.get_liquidation_actions(sim_date)
                    if liq_actions:
                        logger.warning(
                            "[PhaseManager] 2030 清仓 %s - %s",
                            ctx.current_phase_info.current_quarter,
                            liq_actions.get("name", ""),
                        )
                        result["liquidation_actions"] = liq_actions
            else:
                logger.info("[PhaseManager] 非季度末, 季度评估跳过")
                result["quarterly_review"] = {
                    "executed": False,
                    "reason": "not_quarter_end",
                }
        else:
            result["quarterly_review"] = {
                "status": "SKIP",
                "reason": "phase_manager_not_loaded",
            }
    except Exception as e:  # fail-safe: 季度评估失败不阻断
        logger.error(f"[PhaseManager] 季度评估失败: {e}", exc_info=True)
        result["quarterly_review"] = {"status": "ERROR", "error": str(e)}

    # === 写入 state ===
    ctx.state["phases"]["v10_risk"] = result
    logger.info("-" * 60)
    logger.info(
        "Phase 4.6 完成: 回撤=L%d, VaR超限=%s, 压测=%s, 季度评估=%s",
        result["drawdown"].get("level", 0),
        result["var"].get("any_breach", False),
        "执行" if result["stress_test"].get("executed") else "跳过",
        "执行" if result.get("quarterly_review", {}).get("executed") else "跳过",
    )
    logger.info("=" * 60)
    return result


def _load_returns_history() -> list[float]:
    """加载历史收益率序列 (用于 VaR 计算)"""
    try:
        returns_path = BASE_DIR.parent / "config" / "returns_history.json"
        if returns_path.exists():
            with open(returns_path, encoding="utf-8") as f:
                data = json.load(f)
            # 支持多种格式: {"returns": [...]} 或 {"daily_returns": [...]} 或 [...]
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                for key in ("returns", "daily_returns", "portfolio_returns"):
                    if key in data:
                        return data[key]
        return []
    except Exception:  # fail-safe: 历史数据加载失败返回空
        return []


def _get_portfolio_positions_for_stress_test() -> list[dict]:
    """获取用于压力测试的持仓列表"""
    positions: list[dict] = []
    try:
        v10_loader = V10ConfigLoader()
        for pos in v10_loader.get_stock_positions():
            positions.append(
                {
                    "code": pos.get("code"),
                    "name": pos.get("name"),
                    "amount": pos.get("amount", 0),
                    "strategy": "stock_long",
                    "style": pos.get("style", ""),
                }
            )
        for pos in v10_loader.get_etf_positions():
            positions.append(
                {
                    "code": pos.get("code"),
                    "name": pos.get("name"),
                    "amount": pos.get("amount", 0),
                    "strategy": "etf",
                    "style": pos.get("style", ""),
                }
            )
        # 期货和期权账户
        futures_cfg = v10_loader.get_futures_config()
        if futures_cfg:
            positions.append(
                {
                    "code": "futures",
                    "name": "期货账户",
                    "amount": futures_cfg.get("margin_capital", 0),
                    "strategy": "futures_hedge",
                }
            )
        options_cfg = v10_loader.get_options_config()
        if options_cfg:
            positions.append(
                {
                    "code": "options",
                    "name": "期权账户",
                    "amount": options_cfg.get("capital", 0),
                    "strategy": "options_tail",
                }
            )
        cash_cfg = v10_loader.get_cash_config()
        if cash_cfg:
            positions.append(
                {
                    "code": "cash",
                    "name": "现金管理",
                    "amount": cash_cfg.get("capital", 0),
                    "strategy": "cash",
                }
            )
    except Exception as e:  # fail-safe: 持仓获取失败返回空
        logger.warning(f"获取压力测试持仓失败: {e}")
    return positions
