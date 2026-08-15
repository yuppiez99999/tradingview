"""
多策略协调器 (Multi-Strategy Coordinator) v1.0
================================================

世界顶级对冲基金标准多策略架构 — Citadel/Point72/Millennium 同级:

    1. 策略间风险隔离 (Risk Isolation)
       - 每个策略独立风险预算
       - 策略间相关性监控
       - 总风险预算约束

    2. 策略权重动态调整 (Dynamic Weighting)
       - 基于近期表现调整权重
       - 限制单策略最大权重
       - 衰减式权重调整 (避免突变)

    3. 策略冲突检测 (Conflict Detection)
       - 同标的相反信号检测
       - 总持仓超限检测
       - 现金分配冲突

    4. 策略表现追踪 (Performance Tracking)
       - 各策略独立 P&L
       - Sharpe/IR/最大回撤
       - 策略失效预警

    5. 资金分配 (Capital Allocation)
       - Kelly 准则 + 风险平价
       - 最低/最高资金限制
       - 现金缓冲管理

支持的 6 大策略 (v10.0):
    - stock_long:        选股多头 (180 万)
    - etf_allocation:    ETF 配置 (50 万)
    - macro_hedge:       宏观对冲 (期货方向性 50 万)
    - quant_neutral:     量化中性 (70 万)
    - options_tail:      期权尾部 (20 万)
    - cash_management:   现金管理 (130 万)

用法:
    from utils.multi_strategy_coordinator import MultiStrategyCoordinator
    coord = MultiStrategyCoordinator()
    coord.register_strategy("stock_long", capital=1_800_000, max_weight=0.40)
    coord.register_strategy("quant_neutral", capital=700_000, max_weight=0.20)
    decision = coord.coordinate(target_signals={...}, current_positions={...})
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("strategy_coord")

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "config"


@dataclass
class StrategyState:
    """策略状态"""

    name: str
    capital: float = 0.0
    max_weight: float = 0.40
    min_weight: float = 0.05
    current_weight: float = 0.0
    current_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    correlation_to_portfolio: float = 0.0
    is_active: bool = True
    last_rebalance: str = ""
    # 风险指标
    var_95: float = 0.0
    beta_to_market: float = 0.0
    # 失效预警
    is_degraded: bool = False
    degradation_reason: str = ""


@dataclass
class StrategyConflict:
    """策略冲突"""

    conflict_type: str  # opposite_signal / over_position / cash_conflict / risk_budget_exceeded
    strategies: list[str]
    symbol: str = ""
    description: str = ""
    severity: str = "warning"  # info / warning / error
    suggested_action: str = ""


@dataclass
class CoordinationDecision:
    """协调决策"""

    decision_date: str = ""
    total_capital: float = 0.0
    total_allocated: float = 0.0
    cash_buffer: float = 0.0
    strategy_weights: dict[str, float] = field(default_factory=dict)
    strategy_capital: dict[str, float] = field(default_factory=dict)
    conflicts: list[StrategyConflict] = field(default_factory=list)
    risk_budget_used: float = 0.0
    risk_budget_limit: float = 0.0
    is_approved: bool = True
    summary: str = ""


class MultiStrategyCoordinator:
    """多策略协调器。

    管理股票多头 / ETF 配置 / 宏观对冲 / 量化中性 / 期权尾部 / 现金管理
    六类策略的资金分配、权重调整、冲突检测与风险预算控制。
    """

    # v10.0 默认策略配置
    DEFAULT_STRATEGIES = {
        "stock_long": {
            "capital": 1_800_000,
            "max_weight": 0.40,
            "min_weight": 0.10,
        },
        "etf_allocation": {
            "capital": 500_000,
            "max_weight": 0.15,
            "min_weight": 0.05,
        },
        "macro_hedge": {
            "capital": 500_000,
            "max_weight": 0.15,
            "min_weight": 0.05,
        },
        "quant_neutral": {
            "capital": 700_000,
            "max_weight": 0.20,
            "min_weight": 0.05,
        },
        "options_tail": {
            "capital": 200_000,
            "max_weight": 0.06,
            "min_weight": 0.02,
        },
        "cash_management": {
            "capital": 1_300_000,
            "max_weight": 0.35,
            "min_weight": 0.10,
        },
    }

    # 风险预算
    TOTAL_RISK_BUDGET = 0.15  # 总最大回撤 15%
    PER_STRATEGY_RISK = 0.05  # 单策略最大回撤 5%
    CORRELATION_THRESHOLD = 0.70  # 相关性告警阈值

    def __init__(self, total_capital: float = 5_000_000):
        """初始化多策略协调器并加载默认策略配置。

        Args:
            total_capital: 总资金规模，默认 500 万
        """
        self.total_capital = total_capital
        self.strategies: dict[str, StrategyState] = {}
        self._initialize_default_strategies()

    def _initialize_default_strategies(self) -> None:
        """初始化默认策略"""
        for name, cfg in self.DEFAULT_STRATEGIES.items():
            self.strategies[name] = StrategyState(
                name=name,
                capital=cfg["capital"],
                max_weight=cfg["max_weight"],
                min_weight=cfg["min_weight"],
                current_weight=cfg["capital"] / self.total_capital,
            )

    # ------------------------------------------------------------
    # 策略注册
    # ------------------------------------------------------------
    def register_strategy(
        self,
        name: str,
        capital: float,
        max_weight: float = 0.40,
        min_weight: float = 0.05,
    ) -> None:
        """注册新策略到协调器。

        Args:
            name: 策略名称
            capital: 分配资金额度
            max_weight: 最大权重上限，默认 0.40
            min_weight: 最小权重下限，默认 0.05
        """
        self.strategies[name] = StrategyState(
            name=name,
            capital=capital,
            max_weight=max_weight,
            min_weight=min_weight,
            current_weight=capital / self.total_capital,
        )
        logger.info(f"[StrategyCoord] 注册策略 {name}: 资金 {capital:,.0f}, 权重 {capital / self.total_capital:.1%}")

    # ------------------------------------------------------------
    # 主入口: 协调策略
    # ------------------------------------------------------------
    def coordinate(
        self,
        target_signals: dict[str, dict[str, Any]] | None = None,
        current_positions: dict[str, dict[str, Any]] | None = None,
        strategy_pnl: dict[str, float] | None = None,
        strategy_correlations: dict[str, float] | None = None,
    ) -> CoordinationDecision:
        """协调多策略。

        依次执行策略表现更新、失效检测、权重调整、冲突检测、风险预算
        与现金缓冲检查，最终汇总为协调决策。

        Args:
            target_signals: {strategy_name: {symbol: signal}}
            current_positions: {symbol: {strategy, weight, direction}}
            strategy_pnl: {strategy_name: pnl}
            strategy_correlations: {strategy_name: correlation_to_portfolio}

        Returns:
            CoordinationDecision: 含策略权重/资金分配/冲突/风险预算/是否通过等字段
        """
        decision = CoordinationDecision(
            decision_date=datetime.now().strftime("%Y-%m-%d"),
            total_capital=self.total_capital,
        )

        # 1. 更新策略表现
        if strategy_pnl:
            self._update_strategy_pnl(strategy_pnl)

        if strategy_correlations:
            self._update_correlations(strategy_correlations)

        # 2. 检测策略失效
        self._check_strategy_degradation(decision)

        # 3. 动态调整权重
        self._adjust_weights(decision)

        # 4. 冲突检测
        if target_signals and current_positions:
            self._detect_conflicts(target_signals, current_positions, decision)

        # 5. 风险预算检查
        self._check_risk_budget(decision)

        # 6. 现金缓冲检查
        self._check_cash_buffer(decision)

        # 7. 生成摘要
        decision.summary = self._build_summary(decision)
        # 严重冲突 (error 级别) 或 现金缓冲不足 时拒绝
        min_cash = self.total_capital * 0.10
        decision.is_approved = (
            len([c for c in decision.conflicts if c.severity == "error"]) == 0
            and decision.risk_budget_used <= decision.risk_budget_limit
            and decision.cash_buffer >= min_cash
        )

        logger.info(
            "[StrategyCoord] %s | 总资金 %.0f | 已分配 %.0f | 现金缓冲 %.0f | 冲突 %d | %s",
            decision.decision_date,
            decision.total_capital,
            decision.total_allocated,
            decision.cash_buffer,
            len(decision.conflicts),
            "通过" if decision.is_approved else "未通过",
        )

        return decision

    # ------------------------------------------------------------
    # 更新策略 P&L
    # ------------------------------------------------------------
    def _update_strategy_pnl(self, pnl: dict[str, float]) -> None:
        """更新策略 P&L"""
        for name, p in pnl.items():
            if name in self.strategies:
                s = self.strategies[name]
                s.current_pnl = p
                s.cumulative_pnl += p

    def _update_correlations(self, correlations: dict[str, float]) -> None:
        """更新策略相关性"""
        for name, corr in correlations.items():
            if name in self.strategies:
                self.strategies[name].correlation_to_portfolio = corr

    # ------------------------------------------------------------
    # 策略失效检测
    # ------------------------------------------------------------
    def _check_strategy_degradation(self, decision: CoordinationDecision) -> None:
        """检测策略失效

        失效条件:
            1. 连续亏损超过 5% 资金
            2. 最大回撤超过 5%
            3. 与组合相关性 > 0.9 (失去分散价值)
            4. Sharpe < -0.5
        """
        for name, s in self.strategies.items():
            reasons = []

            # 连续亏损
            if s.cumulative_pnl < -s.capital * self.PER_STRATEGY_RISK:
                reasons.append(f"累计亏损 {s.cumulative_pnl:,.0f} 超过 {self.PER_STRATEGY_RISK:.0%} 资金")

            # 最大回撤
            if s.max_drawdown > self.PER_STRATEGY_RISK:
                reasons.append(f"最大回撤 {s.max_drawdown:.1%} 超过 {self.PER_STRATEGY_RISK:.0%}")

            # 相关性过高
            if s.correlation_to_portfolio > self.CORRELATION_THRESHOLD:
                reasons.append(f"与组合相关性 {s.correlation_to_portfolio:.2f} > {self.CORRELATION_THRESHOLD}")

            # Sharpe 过低
            if s.sharpe_ratio < -0.5:
                reasons.append(f"Sharpe {s.sharpe_ratio:.2f} < -0.5")

            if reasons:
                s.is_degraded = True
                s.degradation_reason = "; ".join(reasons)
                decision.conflicts.append(
                    StrategyConflict(
                        conflict_type="strategy_degraded",
                        strategies=[name],
                        description=f"策略 {name} 失效: {s.degradation_reason}",
                        severity="warning",
                        suggested_action=f"降低 {name} 权重或暂停策略",
                    )
                )
            else:
                s.is_degraded = False
                s.degradation_reason = ""

    # ------------------------------------------------------------
    # 动态权重调整
    # ------------------------------------------------------------
    def _adjust_weights(self, decision: CoordinationDecision) -> None:
        """动态调整策略权重

        规则:
            1. 失效策略权重降至 min_weight
            2. 表现好的策略权重上调 (但不超过 max_weight)
            3. 权重变化不超过 20% (避免突变)
            4. 总权重 = 100%
        """
        total_target_weight = 0.0

        for name, s in self.strategies.items():
            if not s.is_active:
                target_weight = 0.0
            elif s.is_degraded:
                # 失效策略: 降至 min_weight
                target_weight = s.min_weight
            else:
                # 正常策略: 基于表现微调
                base_weight = s.capital / self.total_capital
                if s.sharpe_ratio > 1.0:
                    # Sharpe > 1: 上调 10%
                    target_weight = base_weight * 1.10
                elif s.sharpe_ratio < 0:
                    # Sharpe < 0: 下调 10%
                    target_weight = base_weight * 0.90
                else:
                    target_weight = base_weight

                # 限制在 [min_weight, max_weight]
                target_weight = max(s.min_weight, min(s.max_weight, target_weight))

            # 限制权重变化幅度 (避免突变)
            max_change = 0.20
            weight_change = (target_weight - s.current_weight) / max(s.current_weight, 0.01)
            if abs(weight_change) > max_change:
                if weight_change > 0:
                    target_weight = s.current_weight * (1 + max_change)
                else:
                    target_weight = s.current_weight * (1 - max_change)

            decision.strategy_weights[name] = round(target_weight, 4)
            decision.strategy_capital[name] = round(target_weight * self.total_capital, 0)
            total_target_weight += target_weight

        # 归一化 (确保总权重 = 1.0)
        if total_target_weight > 0:
            scale = 1.0 / total_target_weight
            for name in decision.strategy_weights:
                decision.strategy_weights[name] = round(decision.strategy_weights[name] * scale, 4)
                decision.strategy_capital[name] = round(decision.strategy_capital[name] * scale, 0)

        decision.total_allocated = sum(
            cap for name, cap in decision.strategy_capital.items() if name != "cash_management"
        )
        # 现金缓冲 = cash_management 策略资金 + 未分配资金
        decision.cash_buffer = decision.strategy_capital.get("cash_management", 0) + (
            self.total_capital - sum(decision.strategy_capital.values())
        )

    # ------------------------------------------------------------
    # 冲突检测
    # ------------------------------------------------------------
    def _detect_conflicts(
        self,
        target_signals: dict[str, dict[str, Any]],
        current_positions: dict[str, dict[str, Any]],
        decision: CoordinationDecision,
    ) -> None:
        """检测策略冲突

        1. 同标的相反信号
        2. 总持仓超限
        3. 现金冲突
        """
        # 收集每个标的的信号方向
        symbol_signals: dict[str, list[tuple[str, str]]] = {}
        for strategy, signals in target_signals.items():
            for symbol, signal in signals.items():
                direction = ""
                if isinstance(signal, dict):
                    direction = signal.get("direction", signal.get("action", ""))  # type: ignore[index]
                elif isinstance(signal, str):
                    direction = signal
                if direction:
                    if symbol not in symbol_signals:
                        symbol_signals[symbol] = []
                    symbol_signals[symbol].append((strategy, direction))

        # 检测相反信号
        for symbol, sigs in symbol_signals.items():
            if len(sigs) < 2:
                continue
            has_long = any("long" in d or "buy" in d for _, d in sigs)
            has_short = any("short" in d or "sell" in d for _, d in sigs)
            if has_long and has_short:
                strategies_involved = [s for s, _ in sigs]
                decision.conflicts.append(
                    StrategyConflict(
                        conflict_type="opposite_signal",
                        strategies=strategies_involved,
                        symbol=symbol,
                        description=f"标的 {symbol} 存在相反信号: {sigs}",
                        severity="warning",
                        suggested_action="按优先级取较高置信度信号, 或对冲处理",
                    )
                )

        # 检测总持仓超限
        total_weight = sum(p.get("weight", 0) for p in current_positions.values())
        if total_weight > 1.0:
            decision.conflicts.append(
                StrategyConflict(
                    conflict_type="over_position",
                    strategies=list(self.strategies.keys()),
                    description=f"总持仓权重 {total_weight:.1%} 超过 100%",
                    severity="error",
                    suggested_action="立即减仓至 100% 以内",
                )
            )

        # 单标的持仓超限 (>5%)
        for symbol, pos in current_positions.items():
            weight = pos.get("weight", 0)
            if weight > 0.05:
                strategies_in_symbol = [pos.get("strategy", "unknown")]
                decision.conflicts.append(
                    StrategyConflict(
                        conflict_type="over_position",
                        strategies=strategies_in_symbol,
                        symbol=symbol,
                        description=f"标的 {symbol} 持仓 {weight:.1%} 超过 5% 上限",
                        severity="warning",
                        suggested_action=f"减仓 {symbol} 至 5% 以内",
                    )
                )

    # ------------------------------------------------------------
    # 风险预算检查
    # ------------------------------------------------------------
    def _check_risk_budget(self, decision: CoordinationDecision) -> None:
        """检查风险预算"""
        decision.risk_budget_limit = self.TOTAL_RISK_BUDGET * self.total_capital

        # 简化: 风险预算 = Σ (策略权重 × 策略 VaR)
        total_risk = 0.0
        for name, s in self.strategies.items():
            weight = decision.strategy_weights.get(name, s.current_weight)
            # 简化: 假设 VaR_95 = 3% × weight × capital
            strategy_risk = (
                abs(s.var_95) * weight * self.total_capital if s.var_95 else 0.03 * weight * self.total_capital
            )
            total_risk += strategy_risk

        decision.risk_budget_used = total_risk

        if total_risk > decision.risk_budget_limit:
            decision.conflicts.append(
                StrategyConflict(
                    conflict_type="risk_budget_exceeded",
                    strategies=list(self.strategies.keys()),
                    description=f"总风险预算 {total_risk:,.0f} 超过限额 {decision.risk_budget_limit:,.0f}",
                    severity="error",
                    suggested_action="降低高风险策略权重",
                )
            )

    # ------------------------------------------------------------
    # 现金缓冲检查
    # ------------------------------------------------------------
    def _check_cash_buffer(self, decision: CoordinationDecision) -> None:
        """检查现金缓冲"""
        min_buffer = self.total_capital * 0.10  # 最低 10% 现金
        if decision.cash_buffer < min_buffer:
            decision.conflicts.append(
                StrategyConflict(
                    conflict_type="cash_conflict",
                    strategies=["cash_management"],
                    description=f"现金缓冲 {decision.cash_buffer:,.0f} 低于最低 {min_buffer:,.0f}",
                    severity="warning",
                    suggested_action="增加现金管理策略权重",
                )
            )

    # ------------------------------------------------------------
    # 摘要生成
    # ------------------------------------------------------------
    def _build_summary(self, decision: CoordinationDecision) -> str:
        """生成协调摘要"""
        lines = [
            f"多策略协调摘要 ({decision.decision_date})",
            "=" * 60,
            f"总资金: ¥{decision.total_capital:,.0f}",
            f"已分配: ¥{decision.total_allocated:,.0f}",
            f"现金缓冲: ¥{decision.cash_buffer:,.0f}",
            f"风险预算: ¥{decision.risk_budget_used:,.0f} / ¥{decision.risk_budget_limit:,.0f}",
            "",
            "策略权重:",
        ]

        for name, weight in decision.strategy_weights.items():
            s = self.strategies.get(name)
            if s:
                status = "❌ 失效" if s.is_degraded else ("✅ 正常" if s.is_active else "⏸ 暂停")
                lines.append(
                    f"  {name:<20} 权重 {weight:.1%} (¥{decision.strategy_capital[name]:>10,.0f}) "
                    f"Sharpe {s.sharpe_ratio:>5.2f} 相关性 {s.correlation_to_portfolio:>5.2f} {status}"
                )
                if s.is_degraded:
                    lines.append(f"    └ 原因: {s.degradation_reason}")

        if decision.conflicts:
            lines.append("")
            lines.append(f"冲突检测 ({len(decision.conflicts)}):")
            for c in decision.conflicts:
                icon = {"info": "ℹ", "warning": "⚠", "error": "❌"}.get(c.severity, "?")
                lines.append(f"  {icon} [{c.conflict_type}] {c.description}")
                if c.suggested_action:
                    lines.append(f"    └ 建议: {c.suggested_action}")

        status = "✅ 通过" if decision.is_approved else "❌ 未通过"
        lines.append("")
        lines.append(f"结论: {status}")

        return "\n".join(lines)

    # ------------------------------------------------------------
    # 保存状态
    # ------------------------------------------------------------
    def save_state(self) -> Path:
        """保存协调器状态到 JSON 文件。

        Returns:
            Path: 状态文件路径 (config/strategy_coordinator_state.json)
        """
        path = STATE_DIR / "strategy_coordinator_state.json"
        state = {
            "total_capital": self.total_capital,
            "strategies": {name: asdict(s) for name, s in self.strategies.items()},
            "saved_at": datetime.now().isoformat(),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"策略协调器状态已保存: {path}")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"保存策略协调器状态失败: {e}")
        return path


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="多策略协调器")
    parser.add_argument("--simulate", action="store_true", help="使用模拟数据测试")
    args = parser.parse_args()

    coord = MultiStrategyCoordinator(total_capital=5_000_000)

    if args.simulate:
        # 模拟策略 P&L
        pnl = {
            "stock_long": 15000,
            "etf_allocation": 3000,
            "macro_hedge": -5000,
            "quant_neutral": 2000,
            "options_tail": -1000,
            "cash_management": 500,
        }

        # 模拟相关性
        correlations = {
            "stock_long": 0.85,
            "etf_allocation": 0.65,
            "macro_hedge": -0.30,
            "quant_neutral": 0.05,
            "options_tail": -0.20,
            "cash_management": 0.00,
        }

        # 模拟信号
        target_signals = {
            "stock_long": {"300308": {"direction": "long", "weight": 0.15}},
            "quant_neutral": {"300308": {"direction": "short", "weight": 0.05}},  # 冲突
        }

        # 模拟持仓
        current_positions = {
            "300308": {"strategy": "stock_long", "weight": 0.18},  # 超限
            "600519": {"strategy": "stock_long", "weight": 0.10},
        }

        decision = coord.coordinate(
            target_signals=target_signals,
            current_positions=current_positions,
            strategy_pnl=pnl,  # type: ignore
            strategy_correlations=correlations,
        )

        logger.info(decision.summary)
        coord.save_state()
