"""自动对冲再平衡主协调器 — 10阶段决策闭环。

本组件编排完整的EOD决策流程，协调8个子组件实现自动对冲再平衡。

10阶段EOD决策流程 (design.md §2.1.3.2):
    1. 加载持仓
    2. 风险评估
    3. 市场状态判定
    4. 熔断检查
    5. 工具选择
    6. 成本过滤
    7. 再平衡检查
    8. 联合优化
    9. 目标监控
    10. 策略纠偏 + 熔断检查 + 生成联合计划 + 审计日志

盘中紧急再评估 (design.md §2.1.3.3):
    计算单日跌幅 → 调用 CircuitBreaker.check() → 5秒响应
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.auto_hedge_rebalance.audit_logger import AuditLogger
from utils.auto_hedge_rebalance.circuit_breaker import CircuitBreaker, EmergencyAction
from utils.auto_hedge_rebalance.cost_benefit_filter import (
    CostBenefitFilter,
    PortfolioRisk,
)
from utils.auto_hedge_rebalance.data_fetcher import HedgeToolDataFetcher
from utils.auto_hedge_rebalance.models import (
    AutoHedgePlan,
    CorrectionAction,
    FilterResult,
    MonitorResult,
    StrategyState,
    ToolSelection,
)
from utils.auto_hedge_rebalance.strategy_state_machine import StrategyStateMachine
from utils.auto_hedge_rebalance.target_monitor import TargetMonitor
from utils.auto_hedge_rebalance.tool_selector import HedgeToolSelector, MarketRegime

logger = logging.getLogger(__name__)


# ============================================================================
# AutoHedgeRebalanceEngine
# ============================================================================


class AutoHedgeRebalanceEngine:
    """自动对冲再平衡主协调器。

    编排完整10阶段EOD决策闭环，协调8个子组件实现自动对冲再平衡，
    约束年化收益≥8%且最大回撤<20%。

    Attributes:
        config: 配置字典。
        audit_logger: 审计日志记录器。
        data_fetcher: 对冲工具行情获取器。
        cost_filter: 成本效益过滤器。
        tool_selector: 对冲工具自动选择器。
        state_machine: 策略等级状态机。
        circuit_breaker: 紧急熔断器。
        target_monitor: 目标达成监控器。
    """

    def __init__(
        self,
        base_dir: str = ".",
        config_path: str = "config/auto_hedge_rebalance.yaml",
        integrator: Any | None = None,
        hedge_engine: Any | None = None,
        backtest_engine: Any | None = None,
        target_annual_return: float = 0.08,
        target_max_drawdown: float = 0.20,
    ) -> None:
        """初始化自动对冲再平衡主协调器。

        Args:
            base_dir: 基础目录。
            config_path: 配置文件路径。
            integrator: 对冲再平衡联动引擎 (HedgeRebalanceIntegrator)。
            hedge_engine: 对冲引擎 (HedgeEngine)。
            backtest_engine: 回测引擎。
            target_annual_return: 年化收益目标。
            target_max_drawdown: 最大回撤约束。
        """
        self.base_dir = Path(base_dir)
        self.config = self._load_config(config_path)
        self.integrator = integrator
        self.hedge_engine = hedge_engine
        self.backtest_engine = backtest_engine
        self.target_annual_return = target_annual_return
        self.target_max_drawdown = target_max_drawdown

        # 初始化子组件
        state_file = str(
            self.base_dir
            / self.config.get("state_file", "config/auto_hedge_rebalance_state.json")
        )
        nav_history_file = str(
            self.base_dir
            / self.config.get("nav_history_file", "config/portfolio_nav_history.json")
        )
        audit_db = str(
            self.base_dir
            / self.config.get("audit_db", "data/auto_hedge_rebalance_audit.db")
        )

        self.audit_logger = AuditLogger(db_path=audit_db)
        self.data_fetcher = HedgeToolDataFetcher(config=self.config)
        self.cost_filter = CostBenefitFilter(
            threshold=self.config.get("cost_benefit", {}).get("threshold", 1.5),
        )
        self.tool_selector = HedgeToolSelector(
            hedge_engine=hedge_engine,
            data_fetcher=self.data_fetcher,
            config=self.config,
        )
        self.state_machine = StrategyStateMachine(
            state_path=state_file,
            cooldown_days=self.config.get("cooldown", {}).get("days", 5),
            audit_logger=self.audit_logger,
        )

        cb_cfg = self.config.get("circuit_breaker", {})
        self.circuit_breaker = CircuitBreaker(
            state_path=state_file,
            daily_drop_trigger=cb_cfg.get("daily_drop_trigger", 0.05),
            extreme_drawdown_trigger=cb_cfg.get("extreme_drawdown_trigger", 0.25),
            audit_logger=self.audit_logger,
        )

        rolling_cfg = self.config.get("rolling_window", {})
        self.target_monitor = TargetMonitor(
            nav_history_path=nav_history_file,
            rolling_window=rolling_cfg.get("days", 252),
            target_annual_return=target_annual_return,
            target_max_drawdown=target_max_drawdown,
            min_sample_days=rolling_cfg.get("min_sample_days", 30),
            backtest_engine=backtest_engine,
            config=self.config,
        )

    def _load_config(self, config_path: str) -> dict[str, Any]:
        """加载YAML配置文件。"""
        try:
            import yaml

            full_path = self.base_dir / config_path
            if full_path.exists():
                with open(full_path, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("加载配置失败，使用默认配置: %s", exc)
        return {}

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _determine_market_regime(
        self,
        volatility: float,
        drawdown: float,
    ) -> MarketRegime:
        """判定组合自驱动市场状态。

        Args:
            volatility: 组合年化波动率。
            drawdown: 60日最大回撤。

        Returns:
            市场状态。
        """
        if drawdown > 0.15 or volatility > 0.30:
            return MarketRegime.TAIL_EVENT
        if volatility > 0.22 or drawdown > 0.08:
            return MarketRegime.HIGH
        if volatility > 0.15 or drawdown > 0.03:
            return MarketRegime.MILD
        return MarketRegime.CALM

    def _determine_hedge_ratio(self, regime: MarketRegime) -> float:
        """按市场状态确定对冲比例。

        Returns:
            对冲比例 (0.0-1.0)。
        """
        ratios = {
            MarketRegime.CALM: 0.0,
            MarketRegime.MILD: 0.15,
            MarketRegime.HIGH: 0.25,
            MarketRegime.TAIL_EVENT: 0.40,
        }
        return ratios.get(regime, 0.0)

    def run_eod_decision(
        self,
        portfolio_volatility: float = 0.18,
        portfolio_drawdown_60d: float = 0.0,
        portfolio_nav: float = 1000.0,
        portfolio_beta: float = 1.0,
        equity_exposure: float = 0.8,
    ) -> AutoHedgePlan:
        """执行EOD决策 — 10阶段闭环。

        Args:
            portfolio_volatility: 组合年化波动率。
            portfolio_drawdown_60d: 60日最大回撤。
            portfolio_nav: 组合净值 (万元)。
            portfolio_beta: 组合Beta。
            equity_exposure: 股票敞口比例。

        Returns:
            自动对冲再平衡联合计划 AutoHedgePlan。
        """
        timestamp = self._now_iso()
        degradation_flags: list[str] = []
        audit_event_ids: list[str] = []

        # 阶段1: 风险评估
        try:
            risk = PortfolioRisk(
                volatility=portfolio_volatility,
                max_drawdown_60d=portfolio_drawdown_60d,
                equity_exposure=equity_exposure,
                nav=portfolio_nav,
                beta=portfolio_beta,
            )
        except Exception as exc:
            logger.error("风险评估失败: %s", exc)
            risk = PortfolioRisk(volatility=0.18, max_drawdown_60d=0.0)
            degradation_flags.append(f"风险评估降级: {exc}")

        # 阶段2: 市场状态判定
        regime = self._determine_market_regime(
            portfolio_volatility, portfolio_drawdown_60d
        )

        # 阶段3: 熔断检查
        breaker_status = self.circuit_breaker.get_status()
        if breaker_status.active:
            degradation_flags.append("熔断活跃，拒绝新交易")
            return AutoHedgePlan(
                timestamp=timestamp,
                tool_selection=ToolSelection(reasoning="熔断活跃"),
                filter_result=FilterResult(passed=False, reject_reason="熔断活跃"),
                strategy_state=self.state_machine.get_current_state(),
                breaker_status=breaker_status,
                degradation_flags=degradation_flags,
                audit_event_ids=audit_event_ids,
            )

        # 阶段4: 工具选择
        hedge_ratio = self._determine_hedge_ratio(regime)
        try:
            prices = self.data_fetcher.fetch_futures()
            tool_selection = self.tool_selector.select_tools(
                regime, risk, hedge_ratio, prices
            )
            degradation_flags.extend(tool_selection.fallback_flags)
        except Exception as exc:
            logger.error("工具选择失败: %s", exc)
            tool_selection = ToolSelection(reason=f"工具选择失败: {exc}")
            degradation_flags.append(f"工具选择降级: {exc}")

        # 阶段5: 成本过滤
        filter_result = self.cost_filter.filter(
            tool_selection, risk, prices if "prices" in dir() else {}
        )
        if not filter_result.passed:
            degradation_flags.append(f"成本效益不足: {filter_result.reject_reason}")

        # 阶段6: 联合优化 (复用存量 integrator)
        joint_plan = None
        if self.integrator is not None and filter_result.passed:
            try:
                joint_plan = self.integrator.run_full_workflow()
            except Exception as exc:
                logger.warning("联合优化失败: %s", exc)
                degradation_flags.append(f"联合优化降级: {exc}")

        # 阶段7: 目标监控
        monitor_result = self.target_monitor.monitor(
            self.state_machine.get_current_state().current_level
        )
        if monitor_result.sample_insufficient:
            degradation_flags.append("监控样本不足")

        # 阶段8: 策略纠偏
        if monitor_result.correction_action != CorrectionAction.NONE:
            current_level = self.state_machine.get_current_state().current_level
            # v8.7: 严重纠偏时允许紧急跨级降级 (极端事件快速响应)
            is_emergency = monitor_result.correction_action in (
                CorrectionAction.SEVERE_REVIEW,
                CorrectionAction.DEFENSE_BOOST,
                CorrectionAction.EMERGENCY_LIQUIDATE,
            )
            transition = self.state_machine.transition(
                current_level, monitor_result.correction_action, emergency=is_emergency
            )
            if transition.blocked_reason:
                degradation_flags.append(f"策略纠偏阻断: {transition.blocked_reason}")
            if transition.switch_event is not None:
                audit_event_ids.append(transition.switch_event.event_id)

        # 阶段9: 更新净值历史
        try:
            self.target_monitor.update_nav_history(portfolio_nav)
        except Exception as exc:
            logger.warning("净值更新失败: %s", exc)

        # 阶段10: 生成联合计划
        return AutoHedgePlan(
            timestamp=timestamp,
            joint_plan=joint_plan,
            tool_selection=tool_selection,
            filter_result=filter_result,
            monitor=monitor_result,
            strategy_state=self.state_machine.get_current_state(),
            breaker_status=self.circuit_breaker.get_status(),
            degradation_flags=degradation_flags,
            audit_event_ids=audit_event_ids,
        )

    def run_intraday_check(
        self,
        current_portfolio_value: float,
        previous_portfolio_value: float,
    ) -> EmergencyAction:
        """盘中紧急再评估 — 5秒响应。

        Args:
            current_portfolio_value: 当前组合价值。
            previous_portfolio_value: 上一时刻组合价值。

        Returns:
            紧急保护动作。
        """
        if previous_portfolio_value <= 0:
            return EmergencyAction(action_type="none", description="前值无效")

        daily_drop = (
            previous_portfolio_value - current_portfolio_value
        ) / previous_portfolio_value
        if daily_drop < 0:
            daily_drop = 0  # 上涨不触发

        max_drawdown = self.target_monitor.compute_rolling_max_drawdown()
        self.circuit_breaker.check(daily_drop, max_drawdown)

        if self.circuit_breaker.is_active():
            return self.circuit_breaker.trigger_emergency("circuit_break")

        if daily_drop > self.circuit_breaker.daily_drop_trigger:
            return self.circuit_breaker.trigger_emergency("emergency_reassess")

        return EmergencyAction(action_type="none", description="无需紧急操作")

    def get_monitor_report(self) -> MonitorResult:
        """返回当前目标达成监控报告。"""
        return self.target_monitor.monitor(
            self.state_machine.get_current_state().current_level
        )

    def get_strategy_state(self) -> StrategyState:
        """返回当前策略等级状态。"""
        return self.state_machine.get_current_state()

    def approve_strategy_switch(
        self,
        switch_event_id: str,
        approver: str,
        approved: bool,
        comment: str = "",
    ) -> bool:
        """审批策略切换。"""
        return self.state_machine.approve_switch(switch_event_id, approver, approved)

    def release_circuit_breaker(self, approver: str, comment: str = "") -> bool:
        """解除熔断。"""
        return self.circuit_breaker.release(approver, comment)


__all__ = ["AutoHedgeRebalanceEngine"]
