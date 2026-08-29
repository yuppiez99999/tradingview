"""
KTD-Fin 记忆控制评估基准
========================

文献依据: #25 KTD-Fin: Memory-Controlled Benchmark (2026.05, ★★★★)
论文核心: 数据侧掩码 + Barra 风险因子归因 + 记忆泄漏检测

核心问题
--------
LLM 在金融决策中可能"记忆"训练数据中的未来信息, 导致评估泄漏。
KTD-Fin 通过数据侧掩码 + 归因分析检测这种记忆泄漏。

三大核心组件
------------
1. DataMasker (数据侧掩码)
   - 对未来时间窗口的数据进行掩码
   - 支持多种掩码策略: 零值/均值/噪声/丢弃
   - 确保评估时 LLM 无法访问未来信息

2. BarraAttributor (Barra 风险因子归因)
   - 将组合收益归因到 Barra 风险因子
   - 检测异常因子暴露 (可能暗示记忆泄漏)
   - 因子: Market/Size/Value/Momentum/Volatility/Beta

3. MemoryLeakDetector (记忆泄漏检测)
   - 对比掩码前后的决策差异
   - 检测异常收益 (Sharpe > 3 或胜率 > 75%)
   - 输出泄漏评估报告

使用示例
--------
    from tests.eval.ktd_fin import KTDFinBenchmark

    benchmark = KTDFinBenchmark()
    result = benchmark.evaluate(agent, market_data, mask_date="2025-06-01")
    print(result.is_leaked)
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

logger = logging.getLogger("ktd_fin")

# ============================================================
# 掩码策略枚举
# ============================================================


class MaskStrategy(str, Enum):
    """数据掩码策略。"""

    ZERO = "zero"  # 零值掩码
    MEAN = "mean"  # 均值掩码
    NOISE = "noise"  # 随机噪声掩码
    DROP = "drop"  # 丢弃掩码


class LeakSeverity(str, Enum):
    """泄漏严重程度。"""

    NONE = "none"  # 无泄漏
    LOW = "low"  # 低风险
    MEDIUM = "medium"  # 中等风险
    HIGH = "high"  # 高风险
    CRITICAL = "critical"  # 严重泄漏


# ============================================================
# Barra 风险因子
# ============================================================

BARRA_FACTORS: list[str] = [
    "market",  # 市场因子
    "size",  # 规模因子
    "value",  # 价值因子
    "momentum",  # 动量因子
    "volatility",  # 波动率因子
    "beta",  # Beta 因子
]


# ============================================================
# 数据条目
# ============================================================


@dataclass
class MarketDataPoint:
    """单条市场数据。

    Attributes:
        date: 日期 (YYYY-MM-DD)
        ticker: 标的代码
        price: 收盘价
        volume: 成交量
        return_pct: 日收益率
        is_future: 是否为未来数据 (需要掩码)
    """

    date: str
    ticker: str
    price: float = 0.0
    volume: float = 0.0
    return_pct: float = 0.0
    is_future: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "ticker": self.ticker,
            "price": self.price,
            "volume": self.volume,
            "return_pct": self.return_pct,
            "is_future": self.is_future,
        }


# ============================================================
# 归因结果
# ============================================================


@dataclass
class AttributionResult:
    """Barra 风险因子归因结果。

    Attributes:
        factor_exposures: 各因子暴露 {因子: 暴露值}
        factor_returns: 各因子收益 {因子: 收益}
        specific_return: 特质收益 (无法被因子解释的部分)
        total_return: 总收益
        r_squared: 拟合优度
    """

    factor_exposures: dict[str, float] = field(default_factory=dict)
    factor_returns: dict[str, float] = field(default_factory=dict)
    specific_return: float = 0.0
    total_return: float = 0.0
    r_squared: float = 0.0

    @property
    def explained_return(self) -> float:
        """因子解释的收益。"""
        return sum(
            self.factor_exposures.get(f, 0.0) * self.factor_returns.get(f, 0.0)
            for f in BARRA_FACTORS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_exposures": dict(self.factor_exposures),
            "factor_returns": dict(self.factor_returns),
            "specific_return": self.specific_return,
            "total_return": self.total_return,
            "r_squared": self.r_squared,
            "explained_return": self.explained_return,
        }


# ============================================================
# 泄漏评估结果
# ============================================================


@dataclass
class LeakAssessment:
    """记忆泄漏评估结果。

    Attributes:
        is_leaked: 是否检测到泄漏
        severity: 泄漏严重程度
        sharpe_ratio: 策略 Sharpe 比率
        win_rate: 胜率
        decision_diff: 掩码前后决策差异
        suspicious_factors: 异常因子列表
        reason: 评估理由
    """

    is_leaked: bool = False
    severity: LeakSeverity = LeakSeverity.NONE
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    decision_diff: float = 0.0
    suspicious_factors: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_leaked": self.is_leaked,
            "severity": self.severity.value,
            "sharpe_ratio": self.sharpe_ratio,
            "win_rate": self.win_rate,
            "decision_diff": self.decision_diff,
            "suspicious_factors": list(self.suspicious_factors),
            "reason": self.reason,
        }


# ============================================================
# Agent 协议 (接口)
# ============================================================


class AgentProtocol(Protocol):
    """评估代理协议。"""

    def predict(self, data: list[MarketDataPoint]) -> dict[str, Any]:
        """基于市场数据生成预测。"""
        ...


# ============================================================
# Stage 1: 数据掩码器
# ============================================================


class DataMasker:
    """数据侧掩码器 — 对未来时间窗口的数据进行掩码。

    确保评估时 LLM 无法访问未来信息。
    """

    def __init__(self, strategy: MaskStrategy = MaskStrategy.MEAN) -> None:
        self.strategy = strategy

    def mask(self, data: list[MarketDataPoint]) -> list[MarketDataPoint]:
        """掩码未来数据。

        Args:
            data: 市场数据列表

        Returns:
            掩码后的数据列表 (未来数据被替换)
        """
        masked = []
        for point in data:
            if not point.is_future:
                masked.append(point)
                continue

            masked_point = MarketDataPoint(
                date=point.date,
                ticker=point.ticker,
                is_future=True,
            )

            if self.strategy == MaskStrategy.ZERO:
                masked_point.price = 0.0
                masked_point.volume = 0.0
                masked_point.return_pct = 0.0
            elif self.strategy == MaskStrategy.MEAN:
                historical = [
                    p for p in data if not p.is_future and p.ticker == point.ticker
                ]
                if historical:
                    masked_point.price = sum(p.price for p in historical) / len(
                        historical
                    )
                    masked_point.volume = sum(p.volume for p in historical) / len(
                        historical
                    )
                    masked_point.return_pct = sum(
                        p.return_pct for p in historical
                    ) / len(historical)
            elif self.strategy == MaskStrategy.NOISE:
                historical = [
                    p for p in data if not p.is_future and p.ticker == point.ticker
                ]
                if historical:
                    mean_ret = sum(p.return_pct for p in historical) / len(historical)
                    masked_point.price = point.price * (1.0 + mean_ret)
                    masked_point.return_pct = mean_ret
            elif self.strategy == MaskStrategy.DROP:
                continue

            masked.append(masked_point)

        return masked

    def get_mask_summary(self, data: list[MarketDataPoint]) -> dict[str, Any]:
        """获取掩码摘要。"""
        total = len(data)
        future = sum(1 for p in data if p.is_future)
        return {
            "total_points": total,
            "masked_points": future,
            "mask_ratio": round(future / max(total, 1), 3),
            "strategy": self.strategy.value,
        }


# ============================================================
# Stage 2: Barra 风险因子归因
# ============================================================


class BarraAttributor:
    """Barra 风险因子归因分析。

    将组合收益归因到 6 个 Barra 风险因子, 检测异常暴露。
    """

    def attribute(
        self, returns: list[float], factor_exposures: dict[str, list[float]]
    ) -> AttributionResult:
        """归因分析。

        Args:
            returns: 组合收益序列
            factor_exposures: 各因子的暴露序列 {因子: [暴露值]}

        Returns:
            AttributionResult 归因结果
        """
        if not returns:
            return AttributionResult()

        total_return = sum(returns) / len(returns)
        factor_returns: dict[str, float] = {}

        for factor in BARRA_FACTORS:
            exposures = factor_exposures.get(factor, [])
            if exposures and len(exposures) == len(returns):
                factor_returns[factor] = self._estimate_factor_return(
                    returns, exposures
                )
            else:
                factor_returns[factor] = 0.0

        avg_exposures: dict[str, float] = {}
        for factor in BARRA_FACTORS:
            exposures = factor_exposures.get(factor, [])
            avg_exposures[factor] = (
                sum(exposures) / len(exposures) if exposures else 0.0
            )

        explained = sum(avg_exposures[f] * factor_returns[f] for f in BARRA_FACTORS)
        specific = total_return - explained

        r_squared = self._compute_r_squared(returns, factor_exposures, factor_returns)

        return AttributionResult(
            factor_exposures=avg_exposures,
            factor_returns=factor_returns,
            specific_return=round(specific, 6),
            total_return=round(total_return, 6),
            r_squared=round(r_squared, 4),
        )

    def _estimate_factor_return(
        self, returns: list[float], exposures: list[float]
    ) -> float:
        """估计因子收益 (简化 OLS)。"""
        n = len(returns)
        if n == 0:
            return 0.0
        sum_x = sum(exposures)
        sum_y = sum(returns)
        sum_xy = sum(e * r for e, r in zip(exposures, returns, strict=False))
        sum_x2 = sum(e * e for e in exposures)
        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-10:
            return 0.0
        return round((n * sum_xy - sum_x * sum_y) / denom, 6)

    def _compute_r_squared(
        self,
        returns: list[float],
        factor_exposures: dict[str, list[float]],
        factor_returns: dict[str, float],
    ) -> float:
        """计算拟合优度 R²。"""
        if not returns:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        ss_total = sum((r - mean_ret) ** 2 for r in returns)
        if ss_total < 1e-10:
            return 0.0

        ss_res = 0.0
        for i, ret in enumerate(returns):
            predicted = sum(
                factor_exposures.get(f, [])[i] * factor_returns.get(f, 0.0)
                for f in BARRA_FACTORS
                if i < len(factor_exposures.get(f, []))
            )
            ss_res += (ret - predicted) ** 2

        return max(0.0, 1.0 - ss_res / ss_total)

    def detect_anomalies(self, attribution: AttributionResult) -> list[str]:
        """检测异常因子暴露。"""
        anomalies: list[str] = []
        for factor, exposure in attribution.factor_exposures.items():
            if abs(exposure) > 2.0:
                anomalies.append(f"{factor}_overexposed")
            if abs(attribution.factor_returns.get(factor, 0.0)) > 0.05:
                anomalies.append(f"{factor}_abnormal_return")
        return anomalies


# ============================================================
# Stage 3: 记忆泄漏检测器
# ============================================================


class MemoryLeakDetector:
    """记忆泄漏检测器。

    对比掩码前后的决策差异, 检测异常收益。
    """

    SHARPE_THRESHOLD = 3.0
    WIN_RATE_THRESHOLD = 0.75

    def detect(
        self,
        original_result: dict[str, Any],
        masked_result: dict[str, Any],
        attribution: Optional[AttributionResult] = None,
    ) -> LeakAssessment:
        """检测记忆泄漏。

        Args:
            original_result: 原始 (未掩码) 决策结果
            masked_result: 掩码后决策结果
            attribution: Barra 归因结果 (可选)

        Returns:
            LeakAssessment 泄漏评估
        """
        sharpe = masked_result.get("sharpe_ratio", 0.0)
        win_rate = masked_result.get("win_rate", 0.0)
        original_sharpe = original_result.get("sharpe_ratio", 0.0)

        decision_diff = abs(sharpe - original_sharpe)

        suspicious_factors: list[str] = []
        if attribution is not None:
            suspicious_factors = self._check_attribution_anomalies(attribution)

        is_leaked, severity, reason = self._assess(
            sharpe,
            win_rate,
            decision_diff,
            suspicious_factors,
        )

        return LeakAssessment(
            is_leaked=is_leaked,
            severity=severity,
            sharpe_ratio=round(sharpe, 3),
            win_rate=round(win_rate, 3),
            decision_diff=round(decision_diff, 3),
            suspicious_factors=suspicious_factors,
            reason=reason,
        )

    def _check_attribution_anomalies(self, attribution: AttributionResult) -> list[str]:
        """检查归因异常。"""
        anomalies: list[str] = []
        for factor, exposure in attribution.factor_exposures.items():
            if abs(exposure) > 2.0:
                anomalies.append(factor)
        if attribution.r_squared > 0.95:
            anomalies.append("overfitting")
        return anomalies

    def _assess(
        self,
        sharpe: float,
        win_rate: float,
        decision_diff: float,
        suspicious_factors: list[str],
    ) -> tuple[bool, LeakSeverity, str]:
        """评估泄漏严重程度。"""
        reasons: list[str] = []

        if sharpe > self.SHARPE_THRESHOLD:
            reasons.append(f"Sharpe={sharpe:.1f}异常高")
        if win_rate > self.WIN_RATE_THRESHOLD:
            reasons.append(f"胜率={win_rate:.1%}异常高")
        if decision_diff > 2.0:
            reasons.append(f"掩码前后差异={decision_diff:.1f}过大")
        if suspicious_factors:
            reasons.append(f"异常因子: {suspicious_factors}")

        if not reasons:
            return False, LeakSeverity.NONE, "未检测到记忆泄漏"

        if sharpe > self.SHARPE_THRESHOLD * 2 or len(suspicious_factors) >= 3:
            return True, LeakSeverity.CRITICAL, "; ".join(reasons)
        if sharpe > self.SHARPE_THRESHOLD * 1.5 or len(suspicious_factors) >= 2:
            return True, LeakSeverity.HIGH, "; ".join(reasons)
        if sharpe > self.SHARPE_THRESHOLD or win_rate > self.WIN_RATE_THRESHOLD:
            return True, LeakSeverity.MEDIUM, "; ".join(reasons)
        return True, LeakSeverity.LOW, "; ".join(reasons)


# ============================================================
# KTD-Fin 基准
# ============================================================


class KTDFinBenchmark:
    """KTD-Fin 记忆控制评估基准。

    使用示例:
        benchmark = KTDFinBenchmark()
        result = benchmark.evaluate(agent, market_data)
    """

    def __init__(self, mask_strategy: MaskStrategy = MaskStrategy.MEAN) -> None:
        self.masker = DataMasker(mask_strategy)
        self.attributor = BarraAttributor()
        self.detector = MemoryLeakDetector()
        self._results: list[LeakAssessment] = []

    def evaluate(
        self,
        agent: AgentProtocol,
        data: list[MarketDataPoint],
        factor_exposures: Optional[dict[str, list[float]]] = None,
    ) -> LeakAssessment:
        """评估代理是否存在记忆泄漏。

        Args:
            agent: 评估代理 (实现 AgentProtocol)
            data: 市场数据列表 (含未来数据标记)
            factor_exposures: 因子暴露数据 (可选)

        Returns:
            LeakAssessment 泄漏评估
        """
        original_result = self._run_agent(agent, data)

        masked_data = self.masker.mask(data)
        masked_result = self._run_agent(agent, masked_data)

        attribution = AttributionResult()
        if factor_exposures and "returns" in masked_result:
            attribution = self.attributor.attribute(
                masked_result["returns"],
                factor_exposures,
            )

        assessment = self.detector.detect(original_result, masked_result, attribution)
        self._results.append(assessment)

        logger.debug(
            f"评估完成: leaked={assessment.is_leaked}, severity={assessment.severity.value}"
        )
        return assessment

    def _run_agent(
        self, agent: AgentProtocol, data: list[MarketDataPoint]
    ) -> dict[str, Any]:
        """运行代理并计算评估指标。"""
        try:
            prediction = agent.predict(data)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"代理执行失败: {e}")
            return {"sharpe_ratio": 0.0, "win_rate": 0.0, "returns": []}

        returns = prediction.get("returns", [])
        sharpe = self._compute_sharpe(returns)
        win_rate = self._compute_win_rate(returns)

        return {
            "sharpe_ratio": sharpe,
            "win_rate": win_rate,
            "returns": returns,
            "prediction": prediction,
        }

    @staticmethod
    def _compute_sharpe(returns: list[float]) -> float:
        """计算 Sharpe 比率。"""
        if len(returns) < 2:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std < 1e-10:
            return 0.0
        return round(mean_ret / std * math.sqrt(252), 3)

    @staticmethod
    def _compute_win_rate(returns: list[float]) -> float:
        """计算胜率。"""
        if not returns:
            return 0.0
        wins = sum(1 for r in returns if r > 0)
        return round(wins / len(returns), 3)

    def get_summary(self) -> dict[str, Any]:
        """获取评估摘要。"""
        if not self._results:
            return {"n_evaluations": 0}

        severity_counts: dict[str, int] = defaultdict(int)
        n_leaked = 0
        total_sharpe = 0.0
        total_win_rate = 0.0

        for r in self._results:
            severity_counts[r.severity.value] += 1
            if r.is_leaked:
                n_leaked += 1
            total_sharpe += r.sharpe_ratio
            total_win_rate += r.win_rate

        n = len(self._results)
        return {
            "n_evaluations": n,
            "n_leaked": n_leaked,
            "leak_rate": round(n_leaked / n, 3),
            "avg_sharpe": round(total_sharpe / n, 3),
            "avg_win_rate": round(total_win_rate / n, 3),
            "severity_counts": dict(severity_counts),
        }


# ============================================================
# Mock Agent (用于测试)
# ============================================================


class MockAgent:
    """Mock 代理 (用于测试)。"""

    def __init__(self, sharpe: float = 1.0, win_rate: float = 0.55) -> None:
        self.sharpe = sharpe
        self.win_rate = win_rate

    def predict(self, data: list[MarketDataPoint]) -> dict[str, Any]:
        """生成 mock 预测。"""
        n = len(data)
        if n == 0:
            return {"returns": []}

        import random

        rng = random.Random(42)
        returns = [rng.gauss(0.001, 0.02) for _ in range(min(n, 100))]

        target_sharpe = self.sharpe
        if target_sharpe > 0:
            mean_ret = sum(returns) / len(returns)
            std = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / len(returns))
            if std > 0:
                target_mean = target_sharpe * std / math.sqrt(252)
                returns = [r - mean_ret + target_mean for r in returns]

        return {"returns": returns, "action": "hold"}


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 KTD-Fin 记忆控制评估。"""
    print("=" * 60)
    print("KTD-Fin 记忆控制评估基准")
    print("文献: #25 KTD-Fin (2026.05)")
    print("=" * 60)

    benchmark = KTDFinBenchmark(mask_strategy=MaskStrategy.MEAN)

    data = []
    for i in range(100):
        data.append(
            MarketDataPoint(
                date=f"2025-01-{i+1:02d}" if i < 31 else f"2025-02-{i-30:02d}",
                ticker="000300.SH",
                price=4000.0 + i * 5,
                volume=1e8,
                return_pct=0.001,
                is_future=i >= 50,
            )
        )

    print(
        f"\n--- 数据: {len(data)} 条 ({sum(1 for d in data if d.is_future)} 条未来) ---"
    )
    mask_summary = benchmark.masker.get_mask_summary(data)
    print(f"  掩码: {mask_summary}")

    agent = MockAgent(sharpe=1.5, win_rate=0.55)
    print("\n--- 评估 MockAgent (Sharpe=1.5) ---")
    result = benchmark.evaluate(agent, data)
    print(f"  泄漏: {result.is_leaked}")
    print(f"  严重: {result.severity.value}")
    print(f"  Sharpe: {result.sharpe_ratio}")
    print(f"  胜率: {result.win_rate}")
    print(f"  理由: {result.reason}")

    summary = benchmark.get_summary()
    print("\n--- 摘要 ---")
    print(f"  评估次数: {summary['n_evaluations']}")
    print(f"  泄漏率: {summary['leak_rate']}")


if __name__ == "__main__":
    main()
