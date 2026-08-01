# -*- coding: utf-8 -*-
"""
CAGR衰减检测模块 v1.0

检测策略在回测后半段是否出现显著收益衰减。
CAGR衰减是过拟合和因子衰减的最强信号之一。

核心逻辑:
1. 将回测期分为前半段和后半段
2. 分别计算两个时期的CAGR
3. 计算衰减率 = (前半段CAGR - 后半段CAGR) / 前半段CAGR
4. 如果衰减率>40%(即后半段CAGR<前半段的60%),触发强烈警告

使用场景:
- Optuna优化后验证
- 月度/季度策略审查
- 影子账户跟踪

示例:
    from src.validation.cagr_decay import CAGRDetection

    detector = CAGRDetection(
        daily_returns=returns_series,
        risk_free_rate=0.03,
        decay_threshold=0.40  # 40%衰减阈值
    )

    result = detector.detect()
    result.print_summary()

    if result.has_decay:
        print("⚠️ 检测到严重CAGR衰减,策略可能过拟合!")
"""

import numpy as np
import pandas as pd
from typing import Dict
from dataclasses import dataclass


@dataclass
class CAGRDetectionResult:
    """CAGR衰减检测结果"""

    has_decay: bool  # 是否存在显著衰减
    first_half_cagr: float  # 前半段CAGR
    second_half_cagr: float  # 后半段CAGR
    decay_rate: float  # 衰减率(0-1,越大越严重)
    decay_severity: str  # 衰减严重程度:'None', 'Mild', 'Moderate', 'Severe'
    first_half_sharpe: float  # 前半段夏普比率
    second_half_sharpe: float  # 后半段夏普比率
    trading_days: int  # 总交易日数
    warning_message: str  # 警告信息


class CAGRDetection:
    """
    CAGR衰减检测器

    设计原则:
    1. 自动分割回测期为前后两半
    2. 分别计算CAGR、夏普比率、最大回撤
    3. 计算衰减率和严重程度
    4. 提供明确的决策建议
    """

    # 衰减严重程度阈值
    SEVERITY_THRESHOLDS = {
        "None": 0.0,
        "Mild": 0.15,  # 衰减<15%
        "Moderate": 0.30,  # 衰减15%-30%
        "Severe": 0.40,  # 衰减>40%
    }

    def __init__(
        self,
        daily_returns: pd.Series,
        risk_free_rate: float = 0.03,
        decay_threshold: float = 0.40,
        min_trading_days: int = 252,  # 至少1年数据
    ):
        """
        Args:
            daily_returns: 日收益率序列(pd.Series)
            risk_free_rate: 无风险利率(用于夏普比率计算)
            decay_threshold: 衰减阈值(默认40%,即后半段CAGR<前半段60%)
            min_trading_days: 最小交易日数要求
        """
        if len(daily_returns) < min_trading_days:
            raise ValueError(f"数据不足:需要至少{min_trading_days}个交易日,实际只有{len(daily_returns)}个")

        self.daily_returns = daily_returns
        self.risk_free_rate = risk_free_rate
        self.decay_threshold = decay_threshold
        self.min_trading_days = min_trading_days

    def detect(self) -> CAGRDetectionResult:
        """
        执行CAGR衰减检测

        Returns:
            CAGRDetectionResult对象
        """
        n = len(self.daily_returns)
        mid = n // 2

        # 分割数据
        first_half = self.daily_returns.iloc[:mid]
        second_half = self.daily_returns.iloc[mid:]

        # 计算各指标
        first_half_cagr = self._calculate_cagr(first_half)
        second_half_cagr = self._calculate_cagr(second_half)

        first_half_sharpe = self._calculate_sharpe(first_half)
        second_half_sharpe = self._calculate_sharpe(second_half)

        self._calculate_max_drawdown(first_half)
        self._calculate_max_drawdown(second_half)

        # 计算衰减率
        if first_half_cagr > 0:
            decay_rate = (first_half_cagr - second_half_cagr) / first_half_cagr
        else:
            # 前半段CAGR为负,后半段更差则衰减率为正
            decay_rate = max(0, second_half_cagr - first_half_cagr)

        # 判断是否存在显著衰减
        has_decay = decay_rate >= self.decay_threshold

        # 判定严重程度
        severity = self._classify_severity(decay_rate)

        # 生成警告信息
        warning_msg = self._generate_warning(has_decay, decay_rate, severity, first_half_cagr, second_half_cagr)

        return CAGRDetectionResult(
            has_decay=has_decay,
            first_half_cagr=first_half_cagr,
            second_half_cagr=second_half_cagr,
            decay_rate=decay_rate,
            decay_severity=severity,
            first_half_sharpe=first_half_sharpe,
            second_half_sharpe=second_half_sharpe,
            trading_days=n,
            warning_message=warning_msg,
        )

    def _calculate_cagr(self, returns: pd.Series) -> float:
        """
        计算年化收益率(CAGR)

        Formula: CAGR = (Final Value / Initial Value)^(252/trading_days) - 1
        """
        cumulative_return = (1 + returns).prod()
        n_days = len(returns)

        if n_days <= 0 or cumulative_return <= 0:
            return 0.0

        cagr = cumulative_return ** (252 / n_days) - 1
        return cagr

    def _calculate_sharpe(self, returns: pd.Series) -> float:
        """计算年化夏普比率"""
        if returns.std() == 0:
            return 0.0

        sharpe = (returns.mean() - self.risk_free_rate / 252) / returns.std() * np.sqrt(252)
        return sharpe

    def _calculate_max_drawdown(self, returns: pd.Series) -> float:
        """计算最大回撤"""
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max

        return drawdown.min()  # 最负的值即最大回撤

    def _classify_severity(self, decay_rate: float) -> str:
        """
        分类衰减严重程度

        Returns:
            'None', 'Mild', 'Moderate', 'Severe'
        """
        for severity, threshold in sorted(self.SEVERITY_THRESHOLDS.items(), reverse=True):
            if decay_rate >= threshold:
                return severity
        return "None"

    def _generate_warning(
        self, has_decay: bool, decay_rate: float, severity: str, first_half_cagr: float, second_half_cagr: float
    ) -> str:
        """生成警告信息"""
        if not has_decay:
            return "PASS: No significant CAGR decay detected, strategy performance stable"

        warnings = [
            f"CRITICAL: Significant CAGR decay detected! Decay rate: {decay_rate:.1%}",
            f"   First half CAGR: {first_half_cagr:.2%}, Second half CAGR: {second_half_cagr:.2%}",
            f"   Severity: {severity}",
        ]

        if severity == "Severe":
            warnings.append(
                "\n   RECOMMENDATION:"
                "\n   1. Immediately pause strategy live trading"
                "\n   2. Check for look-ahead bias or survivorship bias"
                "\n   3. Retrain model with earlier data"
                "\n   4. Consider strategy retirement"
            )
        elif severity == "Moderate":
            warnings.append(
                "\n   RECOMMENDATION:"
                "\n   1. Increase monitoring frequency (weekly to daily)"
                "\n   2. Review if parameters are near optimal boundaries"
                "\n   3. Prepare backup strategy plans"
            )

        return "\n".join(warnings)

    def print_summary(self):
        """打印检测结果摘要"""
        result = self.detect()

        print("\n" + "=" * 80)
        print("CAGR衰减检测报告")
        print("=" * 80)
        print(f"\n总交易日数: {result.trading_days}")
        print(f"\n前半段CAGR: {result.first_half_cagr:.2%}")
        print(f"后半段CAGR: {result.second_half_cagr:.2%}")
        print(f"\n衰减率: {result.decay_rate:.1%}")
        print(f"严重程度: {result.decay_severity}")
        print(f"\n前半段夏普: {result.first_half_sharpe:.2f}")
        print(f"后半段夏普: {result.second_half_sharpe:.2f}")

        print(f"\n{'=' * 80}")
        print(result.warning_message)
        print("=" * 80 + "\n")

        return result


def quick_cagr_check(returns_series: pd.Series, decay_threshold: float = 0.40) -> CAGRDetectionResult:
    """
    快速CAGR衰减检查(便捷函数)

    Args:
        returns_series: 日收益率序列
        decay_threshold: 衰减阈值

    Returns:
        CAGRDetectionResult对象
    """
    detector = CAGRDetection(returns_series, decay_threshold=decay_threshold)
    return detector.detect()


# ============================================================
# 集成到Optuna Trainer的示例代码
# ============================================================


def integrate_with_optuna(optuna_result: Dict, daily_returns: pd.Series) -> CAGRDetectionResult:
    """
    将CAGR衰减检测集成到Optuna优化流程中

    Args:
        optuna_result: Optuna优化结果字典(包含best_f1_cv等)
        daily_returns: 策略日收益率序列

    Returns:
        CAGRDetectionResult对象
    """
    detector = CAGRDetection(daily_returns)
    result = detector.detect()

    # 打印综合报告
    print("\n" + "=" * 80)
    print("Optuna优化 + CAGR衰减检测综合报告")
    print("=" * 80)
    print(f"\nOptuna最佳CV F1: {optuna_result.get('best_f1_cv', 0):.4f}")
    print(f"CAGR衰减率: {result.decay_rate:.1%}")
    print(f"严重程度: {result.decay_severity}")

    if result.has_decay:
        print(f"\n⚠️ 警告: {result.warning_message}")
    else:
        print("\n✅ 策略通过CAGR衰减检测")

    print("=" * 80 + "\n")

    return result
