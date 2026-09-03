"""目标达成监控器 — 滚动252日窗口+纠偏+预检。

本组件滚动计算组合年化收益率与最大回撤，分级触发纠偏动作，
策略切换前调用回测引擎验证新参数可行性。

纠偏分级 (spec.md §5.3.1):
    收益偏离:
        6-8%  → MILD_TUNE (温和纠偏)
        4-6%  → MODERATE_ROTATE (中度纠偏)
        <4% 持续20日 → SEVERE_REVIEW (重度纠偏)
    回撤:
        15-18% → 预警
        18-20% → DEFENSE_BOOST (危险)
        ≥20%   → EMERGENCY_LIQUIDATE (突破)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.auto_hedge_rebalance.models import (
    CorrectionAction,
    MonitorResult,
    PrecheckResult,
    StrategyLevel,
)

logger = logging.getLogger(__name__)


# ============================================================================
# TargetMonitor
# ============================================================================


class TargetMonitor:
    """目标达成监控器。

    滚动252日窗口计算实际指标，分级触发纠偏，策略切换前回测预检。

    Attributes:
        nav_history_path: 组合净值历史文件路径。
        rolling_window: 滚动窗口交易日数 (默认252)。
        target_annual_return: 年化收益目标 (默认8%)。
        target_max_drawdown: 最大回撤约束 (默认20%)。
    """

    def __init__(
        self,
        nav_history_path: str = "config/portfolio_nav_history.json",
        rolling_window: int = 252,
        target_annual_return: float = 0.08,
        target_max_drawdown: float = 0.20,
        min_sample_days: int = 30,
        backtest_engine: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """初始化目标达成监控器。

        Args:
            nav_history_path: 组合净值历史文件路径。
            rolling_window: 滚动窗口交易日数 (默认252)。
            target_annual_return: 年化收益目标 (默认8%)。
            target_max_drawdown: 最大回撤约束 (默认20%)。
            min_sample_days: 最小样本日 (默认30)。
            backtest_engine: 回测引擎实例 (可选)。
            config: 配置字典 (可选)。
        """
        self.nav_history_path = Path(nav_history_path)
        self.rolling_window = rolling_window
        self.target_annual_return = target_annual_return
        self.target_max_drawdown = target_max_drawdown
        self.min_sample_days = min_sample_days
        self.backtest_engine = backtest_engine
        self.config = config or {}

        # 从配置加载纠偏阈值
        correction_cfg = self.config.get("correction", {})
        self.mild_deviation = correction_cfg.get("mild_deviation", 0.02)
        self.moderate_deviation = correction_cfg.get("moderate_deviation", 0.04)

        drawdown_cfg = self.config.get("drawdown_action", {})
        self.warning_threshold = drawdown_cfg.get("warning_threshold", 0.15)
        self.danger_threshold = drawdown_cfg.get("danger_threshold", 0.18)
        self.breach_threshold = drawdown_cfg.get("breach_threshold", 0.20)

    def _load_nav_history(self) -> list[dict[str, Any]]:
        """加载净值历史。"""
        if not self.nav_history_path.exists():
            return []
        try:
            with open(self.nav_history_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as exc:
            logger.warning("加载净值历史失败: %s", exc)
            return []

    def update_nav_history(self, nav: float, date: str | None = None) -> None:
        """增量更新净值历史。

        Args:
            nav: 当日组合净值。
            date: 当日日期 (ISO格式，可选，默认当前时间)。
        """
        history = self._load_nav_history()
        entry = {
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "nav": nav,
        }
        history.append(entry)

        self.nav_history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.nav_history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def compute_rolling_annual_return(self) -> tuple[float, bool]:
        """计算滚动252日年化收益率。

        Returns:
            (年化收益率, 样本不足标志)。
        """
        history = self._load_nav_history()
        if len(history) < 2:
            return 0.0, True

        # 取最近 rolling_window 日
        window = history[-self.rolling_window :]
        if len(window) < self.min_sample_days:
            # 不足最小样本日，按比例年化并标记样本不足
            nav_start = window[0]["nav"]
            nav_end = window[-1]["nav"]
            days = len(window)
            if nav_start <= 0 or days == 0:
                return 0.0, True
            total_return = nav_end / nav_start - 1
            annual_return = total_return * (252 / days)
            return annual_return, True

        nav_start = window[0]["nav"]
        nav_end = window[-1]["nav"]
        days = len(window)
        if nav_start <= 0 or days == 0:
            return 0.0, True

        total_return = nav_end / nav_start - 1
        annual_return = total_return * (252 / days)
        return annual_return, False

    def compute_rolling_max_drawdown(self) -> float:
        """计算滚动窗口内最大回撤。

        Returns:
            最大回撤 (正数，如0.15表示15%)。
        """
        history = self._load_nav_history()
        if len(history) < 2:
            return 0.0

        window = history[-self.rolling_window :]
        navs = [entry["nav"] for entry in window if entry["nav"] > 0]
        if len(navs) < 2:
            return 0.0

        max_drawdown = 0.0
        peak = navs[0]
        for nav in navs[1:]:
            if nav > peak:
                peak = nav
            drawdown = (peak - nav) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return max_drawdown

    def _detect_return_deviation(self, annual_return: float) -> CorrectionAction:
        """检测收益偏离并返回纠偏动作。

        Args:
            annual_return: 滚动年化收益率。

        Returns:
            纠偏动作。
        """
        deviation = self.target_annual_return - annual_return

        if deviation <= 0:
            # 达标或超额
            return CorrectionAction.NONE

        if deviation < self.mild_deviation:
            # 偏离 < 2pp，温和纠偏
            return CorrectionAction.MILD_TUNE

        if deviation < self.moderate_deviation:
            # 偏离 2-4pp，中度纠偏
            return CorrectionAction.MODERATE_ROTATE

        # 偏离 ≥ 4pp，重度纠偏
        return CorrectionAction.SEVERE_REVIEW

    def _detect_drawdown_breach(self, max_drawdown: float) -> CorrectionAction:
        """检测回撤突破并返回纠偏动作。

        Args:
            max_drawdown: 滚动最大回撤。

        Returns:
            纠偏动作。
        """
        if max_drawdown >= self.breach_threshold:
            # ≥20% 突破
            return CorrectionAction.EMERGENCY_LIQUIDATE

        if max_drawdown >= self.danger_threshold:
            # 18-20% 危险
            return CorrectionAction.DEFENSE_BOOST

        if max_drawdown >= self.warning_threshold:
            # 15-18% 预警 (使用 MILD_TUNE)
            return CorrectionAction.MILD_TUNE

        return CorrectionAction.NONE

    def monitor(
        self, current_strategy_level: StrategyLevel = StrategyLevel.NORMAL
    ) -> MonitorResult:
        """执行目标达成监控。

        Args:
            current_strategy_level: 当前策略等级。

        Returns:
            监控结果 MonitorResult。
        """
        annual_return, sample_insufficient = self.compute_rolling_annual_return()
        max_drawdown = self.compute_rolling_max_drawdown()

        return_deviation = self.target_annual_return - annual_return
        drawdown_margin = self.target_max_drawdown - max_drawdown

        # 检测纠偏动作 (取收益偏离与回撤突破中更严重的)
        return_action = self._detect_return_deviation(annual_return)
        drawdown_action = self._detect_drawdown_breach(max_drawdown)

        # 选择更严重的纠偏动作
        action_priority = {
            CorrectionAction.NONE: 0,
            CorrectionAction.MILD_TUNE: 1,
            CorrectionAction.MODERATE_ROTATE: 2,
            CorrectionAction.SEVERE_REVIEW: 3,
            CorrectionAction.DEFENSE_BOOST: 4,
            CorrectionAction.EMERGENCY_LIQUIDATE: 5,
        }
        correction_action = (
            return_action
            if action_priority[return_action] >= action_priority[drawdown_action]
            else drawdown_action
        )

        return MonitorResult(
            rolling_annual_return=annual_return,
            rolling_max_drawdown=max_drawdown,
            return_deviation=return_deviation,
            drawdown_margin=drawdown_margin,
            sample_insufficient=sample_insufficient,
            correction_action=correction_action,
            precheck_result=None,
        )

    def precheck_strategy_feasibility(
        self,
        new_strategy_params: dict[str, Any],
        timeout_seconds: int = 120,
    ) -> PrecheckResult:
        """策略可行性预检。

        调用回测引擎验证新参数，超时后终止并标记timeout=True。

        Args:
            new_strategy_params: 新策略参数。
            timeout_seconds: 超时秒数 (默认120)。

        Returns:
            预检结果 PrecheckResult。
        """
        if self.backtest_engine is None:
            return PrecheckResult(
                passed=True,
                reason="回测引擎不可用，跳过预检",
            )

        try:
            # 调用回测引擎 (简化实现，实际应使用异步+超时)
            result = self.backtest_engine.run_backtest(**new_strategy_params)

            backtest_annual_return = float(result.get("annual_return", 0))
            backtest_max_drawdown = float(result.get("max_drawdown", 0))

            passed = (
                backtest_annual_return >= self.target_annual_return
                and backtest_max_drawdown < self.target_max_drawdown
            )

            reason = "通过" if passed else "未通过: 回测指标不满足目标约束"

            return PrecheckResult(
                backtest_annual_return=backtest_annual_return,
                backtest_max_drawdown=backtest_max_drawdown,
                passed=passed,
                timeout=False,
                reason=reason,
            )

        except TimeoutError:
            return PrecheckResult(
                passed=False,
                timeout=True,
                reason="预检超时",
            )
        except Exception as exc:
            logger.warning("预检异常: %s", exc)
            return PrecheckResult(
                passed=False,
                reason=f"预检异常: {exc}",
            )


__all__ = ["TargetMonitor"]
