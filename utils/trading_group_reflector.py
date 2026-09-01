"""
TradingGroup 自反思机制 (Self-Reflection + Data-Synthesis + Dynamic Stops)
=========================================================================

文献依据: #16 TradingGroup: Self-Reflection + Data-Synthesis (2025.08, ★★★★★)
论文核心: 交易代理通过自反思识别决策错误, 合成训练数据改进模型, 动态调整止盈止损

三大核心组件
------------
1. Self-Reflection (自反思)
   - 对每个决策进行事后评估 (T+1 或 T+N)
   - 识别错误类型: 信号错误 / 时机错误 / 仓位错误 / 风控错误
   - 生成改进建议, 反馈到下一轮决策

2. Data Synthesis (数据合成)
   - 基于历史决策和结果合成训练数据
   - 正样本 (正确决策) + 负样本 (错误决策) + 困难样本 (边界情况)
   - 输出: {features, label, weight, source}

3. Dynamic Stop-Loss/Take-Profit (动态止盈止损)
   - 基于波动率 (ATR) + 趋势强度 + 持仓时间动态调整
   - 止损: ATR 倍数 + 最大回撤限制
   - 止盈: 移动止盈 + 分批止盈

使用示例
--------
    from utils.trading_group_reflector import TradingGroupReflector

    reflector = TradingGroupReflector()
    # 自反思
    record = reflector.reflect(decision, outcome, market_state)
    # 数据合成
    samples = reflector.synthesize_data(history)
    # 动态止盈止损
    stops = reflector.compute_dynamic_stops(position, market_state)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("trading_group_reflector")

# ============================================================
# 错误类型枚举
# ============================================================


class ErrorType(str, Enum):
    """决策错误类型分类。"""

    SIGNAL_ERROR = "signal_error"  # 信号方向错误 (该买却卖)
    TIMING_ERROR = "timing_error"  # 时机错误 (过早或过晚)
    POSITION_ERROR = "position_error"  # 仓位错误 (过大或过小)
    RISK_ERROR = "risk_error"  # 风控错误 (未止损或过度止损)
    NO_ERROR = "no_error"  # 无错误


class ReflectionGrade(str, Enum):
    """自反思评级。"""

    EXCELLENT = "excellent"  # 决策正确且盈利
    GOOD = "good"  # 决策正确但盈利有限
    NEUTRAL = "neutral"  # 决策中性
    POOR = "poor"  # 决策有瑕疵
    BAD = "bad"  # 决策错误


# ============================================================
# 自反思记录
# ============================================================


@dataclass
class ReflectionRecord:
    """单次决策的自反思记录。

    Attributes:
        decision_id: 决策唯一标识
        ticker: 标的代码
        action: 决策动作 (buy/sell/hold)
        entry_price: 入场价格
        exit_price: 出场价格 (T+N 后)
        outcome_return: 实际收益率
        error_type: 错误类型
        grade: 反思评级
        improvement: 改进建议
        market_state: 决策时市场状态快照
        timestamp: 反思时间
    """

    decision_id: str
    ticker: str
    action: str
    entry_price: float
    exit_price: float | None = None
    outcome_return: float | None = None
    error_type: ErrorType = ErrorType.NO_ERROR
    grade: ReflectionGrade = ReflectionGrade.NEUTRAL
    improvement: str = ""
    market_state: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    @property
    def is_correct(self) -> bool:
        """决策是否正确 (无错误)。"""
        return self.error_type == ErrorType.NO_ERROR

    @property
    def is_profitable(self) -> bool:
        """是否盈利。"""
        return self.outcome_return is not None and self.outcome_return > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "ticker": self.ticker,
            "action": self.action,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "outcome_return": self.outcome_return,
            "error_type": self.error_type.value,
            "grade": self.grade.value,
            "improvement": self.improvement,
            "timestamp": self.timestamp,
        }


# ============================================================
# 合成训练样本
# ============================================================


@dataclass
class SyntheticSample:
    """合成的训练样本。

    Attributes:
        features: 特征向量
        label: 标签 (1=正确决策, 0=错误决策)
        weight: 样本权重 (困难样本权重更高)
        source: 来源 (正样本/负样本/困难样本)
        ticker: 标的代码
    """

    features: dict[str, Any]
    label: int  # 0 或 1
    weight: float = 1.0
    source: str = "positive"
    ticker: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": self.features,
            "label": self.label,
            "weight": self.weight,
            "source": self.source,
            "ticker": self.ticker,
        }


# ============================================================
# 动态止盈止损结果
# ============================================================


@dataclass
class DynamicStops:
    """动态止盈止损计算结果。

    Attributes:
        stop_loss: 止损价
        take_profit: 止盈价
        trailing_stop: 移动止损价 (可选)
        position_size: 建议仓位比例 (0-1)
        risk_score: 风险评分 (0-1, 越高越危险)
        reason: 调整理由
    """

    stop_loss: float
    take_profit: float
    trailing_stop: float | None = None
    position_size: float = 1.0
    risk_score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "trailing_stop": self.trailing_stop,
            "position_size": self.position_size,
            "risk_score": self.risk_score,
            "reason": self.reason,
        }


# ============================================================
# 数据合成器
# ============================================================


class DataSynthesizer:
    """从历史决策合成训练数据。

    合成策略:
    - 正样本: 正确决策 (label=1, weight=1.0)
    - 负样本: 错误决策 (label=0, weight=1.0)
    - 困难样本: 边界情况 (label=0/1, weight=2.0)
    """

    def __init__(self, hard_sample_threshold: float = 0.02) -> None:
        """初始化数据合成器。

        Args:
            hard_sample_threshold: 困难样本阈值 (收益率绝对值小于此值为边界情况)
        """
        self.hard_sample_threshold = hard_sample_threshold

    def synthesize(self, records: list[ReflectionRecord]) -> list[SyntheticSample]:
        """从反思记录合成训练样本。

        Args:
            records: 自反思记录列表

        Returns:
            合成的训练样本列表
        """
        samples: list[SyntheticSample] = []

        for record in records:
            if record.outcome_return is None:
                continue

            features = self._extract_features(record)
            ret = record.outcome_return

            if abs(ret) < self.hard_sample_threshold:
                sample = SyntheticSample(
                    features=features,
                    label=1 if record.is_correct else 0,
                    weight=2.0,
                    source="hard",
                    ticker=record.ticker,
                )
            elif record.is_correct:
                sample = SyntheticSample(
                    features=features,
                    label=1,
                    weight=1.0,
                    source="positive",
                    ticker=record.ticker,
                )
            else:
                sample = SyntheticSample(
                    features=features,
                    label=0,
                    weight=1.0,
                    source="negative",
                    ticker=record.ticker,
                )

            samples.append(sample)

        return samples

    def _extract_features(self, record: ReflectionRecord) -> dict[str, Any]:
        """从反思记录提取特征。"""
        features: dict[str, Any] = {
            "action": record.action,
            "entry_price": record.entry_price,
            "outcome_return": record.outcome_return or 0.0,
            "error_type": record.error_type.value,
            "grade": record.grade.value,
        }
        features.update(record.market_state)
        return features

    def get_summary(self, samples: list[SyntheticSample]) -> dict[str, Any]:
        """获取合成样本统计摘要。"""
        source_counts: dict[str, int] = defaultdict(int)
        for s in samples:
            source_counts[s.source] += 1

        return {
            "n_samples": len(samples),
            "n_positive": sum(1 for s in samples if s.label == 1),
            "n_negative": sum(1 for s in samples if s.label == 0),
            "source_counts": dict(source_counts),
            "avg_weight": sum(s.weight for s in samples) / max(len(samples), 1),
        }


# ============================================================
# 动态止盈止损管理器
# ============================================================


class DynamicStopLossManager:
    """基于市场状态动态调整止盈止损。

    调整因子:
    - ATR (平均真实波幅): 波动率越大, 止损越宽
    - 趋势强度: 强趋势止盈更宽, 弱趋势止盈更窄
    - 持仓时间: 时间衰减, 止损逐渐收紧
    - 最大回撤限制: 硬性风控约束
    """

    def __init__(
        self,
        atr_stop_multiplier: float = 2.0,
        atr_profit_multiplier: float = 3.0,
        max_loss_pct: float = 0.05,
        max_position_pct: float = 0.25,
    ) -> None:
        """初始化动态止盈止损管理器。

        Args:
            atr_stop_multiplier: 止损 ATR 倍数
            atr_profit_multiplier: 止盈 ATR 倍数
            max_loss_pct: 最大亏损比例 (硬性约束)
            max_position_pct: 最大仓位比例
        """
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_profit_multiplier = atr_profit_multiplier
        self.max_loss_pct = max_loss_pct
        self.max_position_pct = max_position_pct

    def compute(
        self,
        entry_price: float,
        atr: float,
        trend_strength: float = 0.0,
        holding_days: int = 0,
        action: str = "buy",
        max_holding_days: int = 20,
    ) -> DynamicStops:
        """计算动态止盈止损。

        Args:
            entry_price: 入场价格
            atr: ATR 值 (平均真实波幅)
            trend_strength: 趋势强度 (-1 到 1, 正=上涨趋势)
            holding_days: 已持仓天数
            action: 动作 (buy/sell)
            max_holding_days: 最大持仓天数 (用于时间衰减)

        Returns:
            DynamicStops 止盈止损结果
        """
        if entry_price <= 0:
            raise ValueError("入场价格必须为正")
        if atr < 0:
            raise ValueError("ATR 不能为负")

        atr_pct = atr / entry_price if entry_price > 0 else 0.0

        time_decay = max(0.0, 1.0 - holding_days / max(max_holding_days, 1))
        trend_factor = 1.0 + 0.5 * abs(trend_strength)

        stop_atr = self.atr_stop_multiplier * atr * (0.5 + 0.5 * time_decay)
        profit_atr = self.atr_profit_multiplier * atr * trend_factor

        max_loss_abs = entry_price * self.max_loss_pct
        stop_distance = min(stop_atr, max_loss_abs)

        is_long = action.lower() in ("buy", "long")
        if is_long:
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + profit_atr
            trailing_stop = entry_price + profit_atr * 0.5 - stop_distance
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - profit_atr
            trailing_stop = entry_price - profit_atr * 0.5 + stop_distance

        risk_score = min(1.0, atr_pct / self.max_loss_pct)
        position_size = self.max_position_pct * (1.0 - risk_score * 0.5)

        reasons = []
        if time_decay < 0.5:
            reasons.append("持仓时间衰减收紧止损")
        if abs(trend_strength) > 0.5:
            reasons.append("强趋势放宽止盈")
        if atr_pct > self.max_loss_pct:
            reasons.append("高波动降低仓位")
        if not reasons:
            reasons.append("正常调整")
        reason = "; ".join(reasons)

        return DynamicStops(
            stop_loss=round(stop_loss, 4),
            take_profit=round(take_profit, 4),
            trailing_stop=round(trailing_stop, 4),
            position_size=round(position_size, 4),
            risk_score=round(risk_score, 4),
            reason=reason,
        )


# ============================================================
# TradingGroup 自反思引擎
# ============================================================


class TradingGroupReflector:
    """TradingGroup 自反思引擎 — 整合自反思 + 数据合成 + 动态止盈止损。

    使用示例:
        reflector = TradingGroupReflector()
        record = reflector.reflect(decision, outcome, market_state)
        samples = reflector.synthesize_data(history)
        stops = reflector.compute_dynamic_stops(entry, atr, trend)
    """

    def __init__(
        self,
        profit_threshold: float = 0.01,
        loss_threshold: float = -0.01,
        hard_sample_threshold: float = 0.02,
    ) -> None:
        """初始化自反思引擎。

        Args:
            profit_threshold: 盈利阈值 (超过为正确决策)
            loss_threshold: 亏损阈值 (低于为错误决策)
            hard_sample_threshold: 困难样本阈值
        """
        self.profit_threshold = profit_threshold
        self.loss_threshold = loss_threshold
        self.synthesizer = DataSynthesizer(hard_sample_threshold)
        self.stop_manager = DynamicStopLossManager()
        self._history: list[ReflectionRecord] = []

    def reflect(
        self,
        decision: dict[str, Any],
        outcome: dict[str, Any],
        market_state: dict[str, Any] | None = None,
    ) -> ReflectionRecord:
        """对单次决策进行自反思。

        Args:
            decision: 决策信息 {decision_id, ticker, action, entry_price, ...}
            outcome: 结果信息 {exit_price, return, ...}
            market_state: 决策时市场状态

        Returns:
            ReflectionRecord 自反思记录
        """
        if market_state is None:
            market_state = {}

        decision_id = decision.get("decision_id", "")
        ticker = decision.get("ticker", "")
        action = decision.get("action", "hold")
        entry_price = decision.get("entry_price", 0.0)
        exit_price = outcome.get("exit_price")
        outcome_return = outcome.get("return")

        error_type, grade, improvement = self._evaluate(
            action,
            entry_price,
            exit_price,
            outcome_return,
        )

        record = ReflectionRecord(
            decision_id=decision_id,
            ticker=ticker,
            action=action,
            entry_price=entry_price,
            exit_price=exit_price,
            outcome_return=outcome_return,
            error_type=error_type,
            grade=grade,
            improvement=improvement,
            market_state=dict(market_state),
            timestamp=outcome.get("timestamp", ""),
        )

        self._history.append(record)
        logger.debug(f"自反思完成: {decision_id} → {grade.value}/{error_type.value}")
        return record

    def _evaluate(
        self,
        action: str,
        entry_price: float,
        exit_price: float | None,
        outcome_return: float | None,
    ) -> tuple[ErrorType, ReflectionGrade, str]:
        """评估决策正确性, 返回 (错误类型, 评级, 改进建议)。

        outcome_return 是策略收益率 (正值=盈利, 负值=亏损), 与多空方向无关。
        """
        if outcome_return is None:
            return ErrorType.NO_ERROR, ReflectionGrade.NEUTRAL, "无结果数据, 无法评估"

        if outcome_return >= self.profit_threshold:
            return ErrorType.NO_ERROR, ReflectionGrade.EXCELLENT, "决策正确且盈利"

        if outcome_return <= self.loss_threshold:
            if abs(outcome_return) > 0.05:
                return ErrorType.RISK_ERROR, ReflectionGrade.BAD, "亏损过大, 风控失效"
            return ErrorType.SIGNAL_ERROR, ReflectionGrade.POOR, "方向错误导致亏损"

        return ErrorType.NO_ERROR, ReflectionGrade.NEUTRAL, "收益中性, 无明显错误"

    def synthesize_data(
        self, records: list[ReflectionRecord] | None = None
    ) -> list[SyntheticSample]:
        """合成训练数据。

        Args:
            records: 反思记录列表 (None 时使用内部历史)

        Returns:
            合成的训练样本列表
        """
        if records is None:
            records = self._history
        return self.synthesizer.synthesize(records)

    def compute_dynamic_stops(
        self,
        entry_price: float,
        atr: float,
        trend_strength: float = 0.0,
        holding_days: int = 0,
        action: str = "buy",
    ) -> DynamicStops:
        """计算动态止盈止损。"""
        return self.stop_manager.compute(
            entry_price=entry_price,
            atr=atr,
            trend_strength=trend_strength,
            holding_days=holding_days,
            action=action,
        )

    def get_reflection_summary(self) -> dict[str, Any]:
        """获取自反思历史摘要。"""
        if not self._history:
            return {"n_records": 0}

        grade_counts: dict[str, int] = defaultdict(int)
        error_counts: dict[str, int] = defaultdict(int)
        total_return = 0.0
        n_profitable = 0
        n_with_return = 0

        for record in self._history:
            grade_counts[record.grade.value] += 1
            error_counts[record.error_type.value] += 1
            if record.outcome_return is not None:
                total_return += record.outcome_return
                n_with_return += 1
                if record.outcome_return > 0:
                    n_profitable += 1

        return {
            "n_records": len(self._history),
            "grade_counts": dict(grade_counts),
            "error_counts": dict(error_counts),
            "avg_return": total_return / max(n_with_return, 1),
            "win_rate": n_profitable / max(n_with_return, 1),
        }

    def get_improvement_suggestions(self) -> list[str]:
        """基于历史反思生成改进建议汇总。"""
        if not self._history:
            return []

        error_counts: dict[str, int] = defaultdict(int)
        for record in self._history:
            if record.error_type != ErrorType.NO_ERROR:
                error_counts[record.error_type.value] += 1

        suggestions: list[str] = []
        if error_counts.get("signal_error", 0) > 2:
            suggestions.append("信号错误频发, 建议增强信号验证机制")
        if error_counts.get("timing_error", 0) > 2:
            suggestions.append("时机错误较多, 建议优化入场时机判断")
        if error_counts.get("position_error", 0) > 2:
            suggestions.append("仓位错误较多, 建议调整仓位管理策略")
        if error_counts.get("risk_error", 0) > 1:
            suggestions.append("风控错误出现, 建议加强止损纪律")

        if not suggestions:
            suggestions.append("决策质量良好, 保持当前策略")

        return suggestions


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 TradingGroup 自反思机制。"""
    print("=" * 60)
    print("TradingGroup 自反思机制 (Self-Reflection + Data-Synthesis)")
    print("文献: #16 TradingGroup (2025.08)")
    print("=" * 60)

    reflector = TradingGroupReflector()

    print("\n--- 1. 自反思 ---")
    decisions = [
        (
            {
                "decision_id": "d1",
                "ticker": "000001.SZ",
                "action": "buy",
                "entry_price": 10.0,
            },
            {"exit_price": 10.5, "return": 0.05, "timestamp": "2026-08-23"},
        ),
        (
            {
                "decision_id": "d2",
                "ticker": "600519.SH",
                "action": "buy",
                "entry_price": 1500.0,
            },
            {"exit_price": 1450.0, "return": -0.033, "timestamp": "2026-08-23"},
        ),
        (
            {
                "decision_id": "d3",
                "ticker": "000858.SZ",
                "action": "sell",
                "entry_price": 200.0,
            },
            {"exit_price": 195.0, "return": 0.025, "timestamp": "2026-08-23"},
        ),
    ]
    for decision, outcome in decisions:
        record = reflector.reflect(decision, outcome, {"volatility": 0.02})
        print(
            f"  {record.decision_id}: {record.grade.value}/{record.error_type.value} → {record.improvement}"
        )

    summary = reflector.get_reflection_summary()
    print(
        f"\n  摘要: 胜率={summary['win_rate']:.1%}, 平均收益={summary['avg_return']:.2%}"
    )

    print("\n--- 2. 数据合成 ---")
    samples = reflector.synthesize_data()
    sample_summary = reflector.synthesizer.get_summary(samples)
    print(f"  样本数: {sample_summary['n_samples']}")
    print(
        f"  正样本: {sample_summary['n_positive']}, 负样本: {sample_summary['n_negative']}"
    )
    print(f"  来源: {sample_summary['source_counts']}")

    print("\n--- 3. 动态止盈止损 ---")
    stops = reflector.compute_dynamic_stops(
        entry_price=10.0,
        atr=0.3,
        trend_strength=0.6,
        holding_days=5,
        action="buy",
    )
    print(f"  止损: {stops.stop_loss}, 止盈: {stops.take_profit}")
    print(f"  移动止损: {stops.trailing_stop}, 仓位: {stops.position_size}")
    print(f"  风险评分: {stops.risk_score}, 理由: {stops.reason}")

    print("\n--- 4. 改进建议 ---")
    for suggestion in reflector.get_improvement_suggestions():
        print(f"  • {suggestion}")


if __name__ == "__main__":
    main()
