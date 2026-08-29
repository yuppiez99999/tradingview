"""
VaR 回测模块 (VaR Backtester)
==============================

实现 Basel Committee 标准的 VaR 模型回测框架:

1. Kupiec POF (Proportion of Failures) 检验 -- 无条件覆盖检验
   - H0: 实际例外率 = 预期例外率 (模型正确校准)
   - 统计量 LR_POF ~ chi2(1)
   - 公式: LR = -2*ln[(1-p)^(N-x) * p^x]
             + 2*ln[(1 - x/N)^(N-x) * (x/N)^x]

2. Christoffersen 独立性检验 -- 条件覆盖检验
   - H0: 例外事件相互独立 (无聚类效应)
   - 构建 2x2 转移矩阵 (00/01/10/11)
   - 统计量 LR_ind ~ chi2(1)

3. Basel 交通灯机制 (Traffic Light)
   - 绿区: 250天内 0-4 次例外
   - 黄区: 5-9 次例外
   - 红区: 10+ 次例外

用法:
    import numpy as np
    from utils.var_backtest import VaRBacktester

    # 模拟 VaR 估计和实际收益
    var_estimates = np.full(250, 0.02)   # 每日 VaR = 2%
    actual_returns = np.random.randn(250) * 0.01  # 实际收益

    bt = VaRBacktester()
    result = bt.backtest(var_estimates, actual_returns, confidence=0.99)
    logger.info(result.summary_report)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 尝试导入 scipy (有则用, 无则用内置近似)
# ------------------------------------------------------------------
try:
    from scipy.stats import chi2 as _scipy_chi2

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def _chi2_sf(x: float, df: int = 1) -> float:
    """计算卡方分布的上侧概率 P(X > x)。

    优先使用 scipy; 若不可用, 对 df=1 使用 erfc 精确近似。
    """
    if x <= 0:
        return 1.0
    if _HAS_SCIPY:
        return float(_scipy_chi2.sf(x, df))
    # df=1 时: chi2_sf(x, 1) = erfc(sqrt(x/2))
    if df == 1:
        return float(math.erfc(math.sqrt(x / 2.0)))
    # 对 df != 1 的粗略近似 (Wilson-Hilferty 变换)
    z = ((x / df) ** (1.0 / 3.0) - (1 - 2.0 / (9 * df))) / math.sqrt(2.0 / (9 * df))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------
@dataclass
class VarBacktestResult:
    """VaR 回测结果。"""

    exceptions_count: int  # 例外次数
    total_observations: int  # 总观测数
    expected_exceptions: float  # 预期例外数
    exception_rate: float  # 实际例外率
    expected_rate: float  # 预期例外率
    kupiec_pof_statistic: float  # Kupiec POF 统计量
    kupiec_p_value: float  # Kupiec POF p 值
    christoffersen_statistic: float  # Christoffersen 统计量
    christoffersen_p_value: float  # Christoffersen p 值
    traffic_light: str  # GREEN / YELLOW / RED
    is_model_valid: bool  # 模型是否有效
    confidence: float  # 置信水平
    window: int  # 回测窗口
    transition_matrix: list | None = None  # 2x2 转移矩阵
    summary_report: str = ""

    def to_dict(self) -> dict:
        return {
            "exceptions_count": self.exceptions_count,
            "total_observations": self.total_observations,
            "expected_exceptions": round(self.expected_exceptions, 2),
            "exception_rate": round(self.exception_rate, 6),
            "expected_rate": round(self.expected_rate, 6),
            "kupiec_pof_statistic": round(self.kupiec_pof_statistic, 6),
            "kupiec_p_value": round(self.kupiec_p_value, 6),
            "christoffersen_statistic": round(self.christoffersen_statistic, 6),
            "christoffersen_p_value": round(self.christoffersen_p_value, 6),
            "traffic_light": self.traffic_light,
            "is_model_valid": self.is_model_valid,
            "confidence": self.confidence,
            "window": self.window,
            "transition_matrix": self.transition_matrix,
            "summary_report": self.summary_report,
        }


# ------------------------------------------------------------------
# 交通灯颜色枚举
# ------------------------------------------------------------------
TRAFFIC_LIGHT_GREEN = "GREEN"
TRAFFIC_LIGHT_YELLOW = "YELLOW"
TRAFFIC_LIGHT_RED = "RED"


# ------------------------------------------------------------------
# VaR 回测器
# ------------------------------------------------------------------
class VaRBacktester:
    """VaR 模型回测器。

    实现 Kupiec POF 检验、Christoffersen 独立性检验和 Basel 交通灯机制。

    约定:
        - var_estimates[i] 为第 i 期的 VaR 估计 (正数, 表示最大预期损失)
        - actual_returns[i] 为第 i 期的实际收益率 (正=盈利, 负=亏损)
        - 例外 (exception) 发生当 actual_return < -var_estimate
          (即实际损失超过 VaR 预期)
    """

    # Basel 交通灯阈值 (基于 250 天窗口)
    GREEN_ZONE_MAX = 4  # 0-4: 绿区
    YELLOW_ZONE_MAX = 9  # 5-9: 黄区; 10+: 红区

    # 统计检验显著性水平
    SIGNIFICANCE_LEVEL = 0.05

    # ================================================================
    # 主接口
    # ================================================================

    def backtest(
        self,
        var_estimates: np.ndarray,
        actual_returns: np.ndarray,
        confidence: float = 0.99,
        window: int = 250,
    ) -> VarBacktestResult:
        """执行 VaR 回测。

        Args:
            var_estimates: VaR 估计值数组 (正数, 表示损失阈值)
            actual_returns: 实际收益率数组 (正=盈利, 负=亏损)
            confidence: 置信水平 (如 0.99 表示 99% VaR)
            window: 回测窗口 (取最近 window 个观测; 若不足则用全部)

        Returns:
            VarBacktestResult: 回测结果
        """
        var_estimates = np.asarray(var_estimates, dtype=np.float64)
        actual_returns = np.asarray(actual_returns, dtype=np.float64)

        if var_estimates.shape[0] != actual_returns.shape[0]:
            raise ValueError(
                f"var_estimates 长度 ({var_estimates.shape[0]}) "
                f"与 actual_returns 长度 ({actual_returns.shape[0]}) 不一致"
            )

        n_total = var_estimates.shape[0]
        if n_total == 0:
            raise ValueError("输入数组为空")

        # 取最近 window 个观测
        if n_total > window:
            var_used = var_estimates[-window:]
            ret_used = actual_returns[-window:]
            n = window
        else:
            var_used = var_estimates
            ret_used = actual_returns
            n = n_total

        # 确保 VaR 为正数 (取绝对值, 处理负数表示法)
        var_positive = np.abs(var_used)

        # 计算例外 (实际损失超过 VaR)
        exceptions = ret_used < -var_positive
        x = int(np.sum(exceptions))  # 例外次数

        # 预期例外概率和数量
        p = 1.0 - float(confidence)  # 预期例外概率
        expected_exceptions = n * p

        # Kupiec POF 检验
        kupiec_stat, kupiec_pval = self._kupiec_pof_test(n=n, x=x, p=p)

        # Christoffersen 独立性检验
        christ_stat, christ_pval, trans_matrix = self._christoffersen_test(exceptions)

        # Basel 交通灯
        traffic = self._traffic_light(x)

        # 模型有效性: 交通灯非红区 AND Kupiec 检验不显著
        is_valid = (
            traffic != TRAFFIC_LIGHT_RED and kupiec_pval > self.SIGNIFICANCE_LEVEL
        )

        # 生成报告
        report = self._generate_report(
            n=n,
            x=x,
            p=p,
            expected=expected_exceptions,
            confidence=confidence,
            kupiec_stat=kupiec_stat,
            kupiec_pval=kupiec_pval,
            christ_stat=christ_stat,
            christ_pval=christ_pval,
            traffic=traffic,
            is_valid=is_valid,
            trans_matrix=trans_matrix,
        )

        return VarBacktestResult(
            exceptions_count=x,
            total_observations=n,
            expected_exceptions=expected_exceptions,
            exception_rate=x / n if n > 0 else 0.0,
            expected_rate=p,
            kupiec_pof_statistic=kupiec_stat,
            kupiec_p_value=kupiec_pval,
            christoffersen_statistic=christ_stat,
            christoffersen_p_value=christ_pval,
            traffic_light=traffic,
            is_model_valid=is_valid,
            confidence=confidence,
            window=window,
            transition_matrix=trans_matrix,
            summary_report=report,
        )

    # ================================================================
    # Kupiec POF 检验
    # ================================================================

    def _kupiec_pof_test(
        self,
        n: int,
        x: int,
        p: float,
    ) -> tuple[float, float]:
        """Kupiec POF (Proportion of Failures) 检验。

        检验 H0: 实际例外率 = 预期例外率 p

        公式:
            LR = -2*ln[(1-p)^(N-x) * p^x]
              + 2*ln[(1 - x/N)^(N-x) * (x/N)^x]

        其中:
            N = 观测数
            x = 例外数
            p = 预期例外概率 (1 - confidence)

        统计量 LR ~ chi2(1) under H0.

        Args:
            n: 观测数 N
            x: 例外数
            p: 预期例外概率

        Returns:
            (LR 统计量, p 值)
        """
        if n == 0:
            return 0.0, 1.0

        # 边界处理
        if x == 0:
            # 无例外: LR = -2*[(N)*ln(1-p)] + 2*[N*ln(1)]
            # = -2*N*ln(1-p) + 0
            # = -2*N*ln(1-p)
            try:
                lr = -2.0 * n * math.log(1.0 - p)
            except (ValueError, OverflowError):
                lr = float("inf")
            pval = _chi2_sf(lr, df=1)
            return float(lr), float(pval)

        if x == n:
            # 全部例外: LR = -2*[0] + 2*[N*ln(1)]
            # = -2*[N*ln(p)] + 0
            try:
                lr = -2.0 * n * math.log(p)
            except (ValueError, OverflowError):
                lr = float("inf")
            pval = _chi2_sf(lr, df=1)
            return float(lr), float(pval)

        # 一般情况
        pi_hat = x / n  # 实际例外率

        # 对数似然 under H0 (使用预期概率 p)
        try:
            ll_h0 = (n - x) * math.log(1.0 - p) + x * math.log(p)
        except (ValueError, OverflowError):
            ll_h0 = float("-inf")

        # 对数似然 under H1 (使用实际例外率)
        try:
            ll_h1 = (n - x) * math.log(1.0 - pi_hat) + x * math.log(pi_hat)
        except (ValueError, OverflowError):
            ll_h1 = float("-inf")

        # LR = -2 * (ll_h0 - ll_h1)
        #    = -2*ln[L(p)] + 2*ln[L(pi_hat)]
        lr = -2.0 * (ll_h0 - ll_h1)

        # 数值修正
        if not math.isfinite(lr) or lr < 0:
            lr = max(lr, 0.0)

        pval = _chi2_sf(lr, df=1)
        return float(lr), float(pval)

    # ================================================================
    # Christoffersen 独立性检验
    # ================================================================

    def _christoffersen_test(
        self,
        exceptions: np.ndarray,
    ) -> tuple[float, float, list | None]:
        """Christoffersen 独立性检验。

        检验 H0: 例外事件相互独立 (无聚类效应)

        构建 2x2 转移矩阵:
            n_00 = P(无例外 -> 无例外) 的次数
            n_01 = P(无例外 -> 例外)   的次数
            n_10 = P(例外 -> 无例外)   的次数
            n_11 = P(例外 -> 例外)     的次数

        统计量:
            LR_ind = -2 * ln[L(pi) / L(pi_01, pi_11)]

        其中:
            pi = (n_01 + n_11) / (n_00 + n_01 + n_10 + n_11)
            pi_01 = n_01 / (n_00 + n_01)
            pi_11 = n_11 / (n_10 + n_11)

        统计量 ~ chi2(1) under H0.

        Args:
            exceptions: 布尔数组, True = 例外

        Returns:
            (LR 统计量, p 值, 转移矩阵 [[n00, n01], [n10, n11]])
        """
        n = len(exceptions)
        if n < 2:
            return 0.0, 1.0, None

        # 构建转移矩阵
        # exc[i] = 当前状态, exc[i+1] = 下一状态
        exc_curr = exceptions[:-1]
        exc_next = exceptions[1:]

        n00 = int(np.sum((~exc_curr) & (~exc_next)))  # 0->0
        n01 = int(np.sum((~exc_curr) & exc_next))  # 0->1
        n10 = int(np.sum(exc_curr & (~exc_next)))  # 1->0
        n11 = int(np.sum(exc_curr & exc_next))  # 1->1

        trans_matrix = [[n00, n01], [n10, n11]]
        total_trans = n00 + n01 + n10 + n11

        if total_trans == 0:
            return 0.0, 1.0, trans_matrix

        # 无条件例外概率
        total_exceptions = n01 + n11
        pi = total_exceptions / total_trans

        # 条件概率
        row0_sum = n00 + n01  # 从 "无例外" 开始的转移数
        row1_sum = n10 + n11  # 从 "例外" 开始的转移数

        pi_01 = n01 / row0_sum if row0_sum > 0 else 0.0
        pi_11 = n11 / row1_sum if row1_sum > 0 else 0.0

        # 边界情况: 如果没有例外, 无法拒绝独立性
        if total_exceptions == 0:
            return 0.0, 1.0, trans_matrix

        # 对数似然 under H0 (独立性: 使用无条件概率 pi)
        # L(pi) = (1-pi)^(n00+n10) * pi^(n01+n11)
        try:
            ll_h0 = (n00 + n10) * math.log(1.0 - pi) + (n01 + n11) * math.log(pi)
        except (ValueError, OverflowError):
            ll_h0 = float("-inf")

        # 对数似然 under H1 (条件概率)
        # L(pi_01, pi_11) = (1-pi_01)^n00 * pi_01^n01
        #                   * (1-pi_11)^n10 * pi_11^n11
        try:
            terms = []
            if n00 > 0:
                terms.append(n00 * math.log(1.0 - pi_01))
            if n01 > 0:
                terms.append(n01 * math.log(pi_01))
            if n10 > 0:
                terms.append(n10 * math.log(1.0 - pi_11))
            if n11 > 0:
                terms.append(n11 * math.log(pi_11))
            ll_h1 = sum(terms)
        except (ValueError, OverflowError):
            ll_h1 = float("-inf")

        # LR_ind = -2 * ln[L(pi) / L(pi_01, pi_11)]
        #        = -2 * (ll_h0 - ll_h1)
        lr = -2.0 * (ll_h0 - ll_h1)

        if not math.isfinite(lr) or lr < 0:
            lr = max(lr, 0.0)

        pval = _chi2_sf(lr, df=1)
        return float(lr), float(pval), trans_matrix

    # ================================================================
    # Basel 交通灯机制
    # ================================================================

    def _traffic_light(self, exceptions: int) -> str:
        """Basel 交通灯机制 (基于 250 天窗口)。

        绿区: 0-4 次例外 -- 模型有效
        黄区: 5-9 次例外 -- 模型需关注, 可能需重新校准
        红区: 10+ 次例外 -- 模型无效, 必须立即修正

        Args:
            exceptions: 例外次数

        Returns:
            "GREEN" / "YELLOW" / "RED"
        """
        if exceptions <= self.GREEN_ZONE_MAX:
            return TRAFFIC_LIGHT_GREEN
        if exceptions <= self.YELLOW_ZONE_MAX:
            return TRAFFIC_LIGHT_YELLOW
        return TRAFFIC_LIGHT_RED

    # ================================================================
    # 报告生成
    # ================================================================

    def _generate_report(
        self,
        n: int,
        x: int,
        p: float,
        expected: float,
        confidence: float,
        kupiec_stat: float,
        kupiec_pval: float,
        christ_stat: float,
        christ_pval: float,
        traffic: str,
        is_valid: bool,
        trans_matrix: list | None,
    ) -> str:
        """生成可视化友好的文本报告。"""
        lines: list = []

        lines.append("=" * 66)
        lines.append("           VaR 回测报告 (Basel Committee 标准)")
        lines.append("=" * 66)
        lines.append("")

        # -- 基本参数 --
        lines.append("【基本参数】")
        lines.append(f"  置信水平:       {confidence:.2%}")
        lines.append(f"  回测窗口:       {n} 天")
        lines.append(f"  预期例外概率:   {p:.4%}")
        lines.append(f"  预期例外数:     {expected:.1f} 次")
        lines.append("")

        # -- 例外统计 --
        lines.append("【例外统计】")
        lines.append(f"  实际例外数:     {x} 次")
        lines.append(f"  实际例外率:     {x / n:.4%}" if n > 0 else "  N/A")
        lines.append(
            f"  预期 vs 实际:   {expected:.1f} vs {x} "
            f"({'偏高' if x > expected else '偏低' if x < expected else '一致'})"
        )
        lines.append("")

        # -- Kupiec POF 检验 --
        kupiec_pass = kupiec_pval > self.SIGNIFICANCE_LEVEL
        lines.append("【Kupiec POF 检验 (无条件覆盖)】")
        lines.append(f"  LR 统计量:       {kupiec_stat:.4f}")
        lines.append(f"  p 值:           {kupiec_pval:.4f}")
        lines.append(f"  显著性水平:     {self.SIGNIFICANCE_LEVEL}")
        lines.append(
            f"  结论:           {'通过' if kupiec_pass else '拒绝'} (H0: 实际例外率 = 预期例外率)"
        )
        lines.append("")

        # -- Christoffersen 独立性检验 --
        christ_pass = christ_pval > self.SIGNIFICANCE_LEVEL
        lines.append("【Christoffersen 独立性检验 (条件覆盖)】")
        lines.append(f"  LR 统计量:       {christ_stat:.4f}")
        lines.append(f"  p 值:           {christ_pval:.4f}")
        if trans_matrix is not None:
            n00, n01 = trans_matrix[0]
            n10, n11 = trans_matrix[1]
            lines.append("  转移矩阵:")
            lines.append(f"    无例外 -> 无例外:  n00 = {n00}")
            lines.append(f"    无例外 -> 例外:    n01 = {n01}")
            lines.append(f"    例外   -> 无例外:  n10 = {n10}")
            lines.append(f"    例外   -> 例外:    n11 = {n11}")
            if n11 > 0:
                lines.append(f"  *** 检测到例外聚类 (n11={n11}) ***")
        lines.append(
            f"  结论:           {'通过' if christ_pass else '拒绝'} (H0: 例外事件相互独立)"
        )
        lines.append("")

        # -- Basel 交通灯 --
        lines.append("【Basel 交通灯机制】")
        if traffic == TRAFFIC_LIGHT_GREEN:
            lines.append("  区域:           绿区 (GREEN)")
            lines.append("  例外范围:       0-4 次 (250天内)")
            lines.append("  说明:           模型表现良好, 无需额外行动")
        elif traffic == TRAFFIC_LIGHT_YELLOW:
            lines.append("  区域:           黄区 (YELLOW)")
            lines.append("  例外范围:       5-9 次 (250天内)")
            lines.append("  说明:           模型需关注, 建议检查校准")
        else:
            lines.append("  区域:           红区 (RED)")
            lines.append("  例外范围:       10+ 次 (250天内)")
            lines.append("  说明:           模型无效, 必须立即修正!")
        lines.append("")

        # -- 综合结论 --
        lines.append("-" * 66)
        if is_valid:
            lines.append("  *** 综合结论: 模型有效 (VALID) ***")
            if traffic == TRAFFIC_LIGHT_YELLOW:
                lines.append("  注意: 处于黄区, 建议加强监控和重新校准")
            lines.append("  - Kupiec 检验: 通过 (例外率与预期一致)")
            lines.append("  - 交通灯: 非红区")
        else:
            lines.append("  *** 综合结论: 模型无效 (INVALID) ***")
            if traffic == TRAFFIC_LIGHT_RED:
                lines.append("  原因: 处于红区 (例外过多)")
            elif not kupiec_pass:
                lines.append("  原因: Kupiec 检验被拒绝 (例外率显著偏离预期)")
            if not christ_pass:
                lines.append("  原因: Christoffersen 检验被拒绝 (例外存在聚类)")
        lines.append("=" * 66)

        return "\n".join(lines)


# ------------------------------------------------------------------
# 模块级便捷函数
# ------------------------------------------------------------------


def backtest(
    var_estimates: np.ndarray,
    actual_returns: np.ndarray,
    confidence: float = 0.99,
    window: int = 250,
) -> VarBacktestResult:
    """便捷入口: 创建 VaRBacktester 并执行回测。

    Args:
        var_estimates: VaR 估计值数组 (正数, 表示损失阈值)
        actual_returns: 实际收益率数组 (正=盈利, 负=亏损)
        confidence: 置信水平 (如 0.99 表示 99% VaR)
        window: 回测窗口

    Returns:
        VarBacktestResult: 回测结果
    """
    return VaRBacktester().backtest(
        var_estimates=var_estimates,
        actual_returns=actual_returns,
        confidence=confidence,
        window=window,
    )


# ------------------------------------------------------------------
# CLI 入口
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="VaR 回测 -- Kupiec POF + Christoffersen + Basel 交通灯"
    )
    parser.add_argument(
        "--var-file",
        type=str,
        help="VaR 估计值 CSV 文件路径 (一列数值)",
    )
    parser.add_argument(
        "--returns-file",
        type=str,
        help="实际收益率 CSV 文件路径 (一列数值)",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.99,
        help="VaR 置信水平 (默认 0.99)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=250,
        help="回测窗口 (默认 250)",
    )
    args = parser.parse_args()

    # 从 CSV 加载数据或使用模拟数据
    if args.var_file and args.returns_file:
        var_data = np.loadtxt(args.var_file)
        ret_data = np.loadtxt(args.returns_file)
    else:
        logger.info("未指定文件, 使用模拟数据演示...")
        np.random.seed(42)
        n_days = 300
        # 模拟: 真实波动率 1%, VaR 设为 2.33% (99% 置信度正态分布)
        var_data = np.full(n_days, 0.0233)
        ret_data = np.random.randn(n_days) * 0.01

    result = backtest(
        var_estimates=var_data,
        actual_returns=ret_data,
        confidence=args.confidence,
        window=args.window,
    )

    logger.info(result.summary_report)
    logger.info("完整 JSON 输出:")
    logger.info(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
