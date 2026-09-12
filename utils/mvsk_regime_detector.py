"""MVSK Regime 检测器 — P3: 高阶矩优化动态切换

论文发现三: 高阶矩价值是市场窗口依赖的 (regime dependent).
高波动肥尾 regime → MVSK 显著跑赢 MV; 低波动近似正态 regime → 增量有限.

本模块用滚动波动率 + 超额峰度检测 regime, 决定是否启用 MVSK:
    - HIGH_VOL_FAT_TAIL (高波动肥尾): 波动率 > 历史分位 或 超额峰度 > 阈值 → 用 MVSK
    - LOW_VOL_NORMAL (低波动正态): 否则 → 用 MV

Usage:
    from utils.mvsk_regime_detector import RegimeDetector
    detector = RegimeDetector(window=60)
    result = detector.detect(returns_matrix)  # T×N
    if result.use_mvsk:
        # 启用 MVSK 优化
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

logger = logging.getLogger(__name__)

#: 有效样本占比低于该值时, 视为数据源不可用 (fail-closed), 不做 regime 判定.
MIN_VALID_RATIO = 0.5


class Regime(StrEnum):
    HIGH_VOL_FAT_TAIL = "high_vol_fat_tail"
    LOW_VOL_NORMAL = "low_vol_normal"
    UNKNOWN = "unknown"


@dataclass
class RegimeResult:
    regime: Regime
    use_mvsk: bool
    confidence: float
    volatility: float
    vol_quantile_rank: float
    excess_kurtosis: float
    skewness: float
    trigger: str
    #: 数据可用性: False = 输入非有限/有效样本不足, regime 判定不可信.
    #: 调用方据此降级 (不得把 UNKNOWN 当 LOW_VOL_NORMAL 消费).
    data_available: bool = True
    #: 不可用时的原因 (审计用; 可用时为空串).
    unavailable_reason: str = ""


class RegimeDetector:
    """MVSK Regime 检测器.

    Args:
        window: 滚动窗口长度 (交易日, 默认 60)
        vol_quantile: 高波动分位阈值 (默认 0.75)
        kurtosis_threshold: 肥尾峰度阈值 (默认 3.0)
        min_periods: 最小计算周期 (默认 20)
        annualization: 年化因子 (默认 252)
    """

    def __init__(
        self,
        window: int = 60,
        vol_quantile: float = 0.75,
        kurtosis_threshold: float = 3.0,
        min_periods: int = 20,
        annualization: int = 252,
    ):
        if window < 10:
            raise ValueError(f"window 必须 >= 10, 实际 {window}")
        if not (0.5 < vol_quantile < 1.0):
            raise ValueError(f"vol_quantile 必须在 (0.5, 1.0), 实际 {vol_quantile}")
        self.window = int(window)
        self.vol_quantile = float(vol_quantile)
        self.kurtosis_threshold = float(kurtosis_threshold)
        self.min_periods = int(min_periods)
        self.ann = int(annualization)

    def detect(self, returns: np.ndarray) -> RegimeResult:
        """检测当前 regime. 取最后 window 日.

        Args:
            returns: T×N 收益矩阵 或 T 维收益序列.

        Returns:
            RegimeResult
        """
        R = np.asarray(returns, dtype=float)
        if R.ndim == 1:
            rp = R
        elif R.ndim == 0:
            # 标量输入: 无有效时间序列 → 显式不可用
            return self._unavailable_result("输入为标量 (ndim=0), 无时间序列")
        else:
            if R.shape[1] == 0:
                return self._unavailable_result("输入矩阵无资产列 (shape[1]=0)")
            w_eq = np.ones(R.shape[1]) / R.shape[1]
            # 含非有限值的元素会污染等权组合; 先做清洗再由 valid_ratio 判定可用性
            rp = np.where(np.isfinite(R), R, np.nan) @ w_eq

        T = len(rp)

        # 数据源失败路径 (SC-28): 全 NaN / 有效样本不足 → fail-closed,
        # 不得静默返回 LOW_VOL_NORMAL (否则缺数据与"正常低波"不可区分, 会静默
        # 翻转 MV↔MVSK 并污染回测结论).
        finite_mask = np.isfinite(rp)
        n_valid = int(finite_mask.sum())
        valid_ratio = (n_valid / T) if T else 0.0
        if T == 0 or n_valid == 0 or valid_ratio < MIN_VALID_RATIO:
            return self._unavailable_result(
                f"有效样本不足: n_valid={n_valid}/{T} "
                f"(ratio={valid_ratio:.2f} < {MIN_VALID_RATIO}); "
                "疑似数据源失败/存在 NaN-Inf",
                n_valid=n_valid,
                total=T,
            )

        # 清洗后重算: 仅用有限值 (NaN 不参与均值/矩计算)
        if n_valid < T:
            logger.warning(
                "mvsk_regime_detector: 输入含 %d/%d 个非有限值, 已剔除后判定 regime",
                T - n_valid,
                T,
            )
            rp = rp[finite_mask]
            T = len(rp)  # 有效长度决定是否满足 min_periods

        if self.min_periods > T:
            return RegimeResult(
                regime=Regime.LOW_VOL_NORMAL,
                use_mvsk=False,
                confidence=0.5,
                volatility=0.0,
                vol_quantile_rank=0.5,
                excess_kurtosis=0.0,
                skewness=0.0,
                trigger="normal",
            )

        recent = rp[-self.window :] if self.window <= T else rp
        vol, kurt, skew = self._compute_moments(recent)
        vol_rank = self._vol_quantile_rank(rp)

        high_vol = vol_rank >= self.vol_quantile
        fat_tail = kurt > self.kurtosis_threshold
        use_mvsk = high_vol or fat_tail

        if high_vol and fat_tail:
            trigger = "both"
        elif high_vol:
            trigger = "high_vol"
        elif fat_tail:
            trigger = "fat_tail"
        else:
            trigger = "normal"

        conf = self._confidence(vol_rank, kurt, high_vol, fat_tail)
        regime = Regime.HIGH_VOL_FAT_TAIL if use_mvsk else Regime.LOW_VOL_NORMAL
        return RegimeResult(
            regime=regime,
            use_mvsk=use_mvsk,
            confidence=conf,
            volatility=vol,
            vol_quantile_rank=vol_rank,
            excess_kurtosis=kurt,
            skewness=skew,
            trigger=trigger,
        )

    def _unavailable_result(
        self,
        reason: str,
        n_valid: int = 0,
        total: int = 0,
    ) -> RegimeResult:
        """构造"数据不可用"结果 (fail-closed, 显式 + 告警).

        SC-28: 数据源失败路径必须与"正常低波动 regime"可区分 ——
        regime=UNKNOWN / use_mvsk=False / data_available=False / trigger=
        "data_unavailable", 且诊断字段不输出 NaN (避免 NaN 向下游传播).
        """
        logger.warning(
            "mvsk_regime_detector: 数据不可用, 跳过 regime 判定 → UNKNOWN "
            "(n_valid=%d/%d, 原因: %s)",
            n_valid,
            total,
            reason,
        )
        return RegimeResult(
            regime=Regime.UNKNOWN,
            use_mvsk=False,
            confidence=0.0,
            volatility=0.0,
            vol_quantile_rank=0.0,
            excess_kurtosis=0.0,
            skewness=0.0,
            trigger="data_unavailable",
            data_available=False,
            unavailable_reason=reason,
        )

    def detect_series(self, rp: np.ndarray) -> RegimeResult:
        """对组合收益序列 (1D) 检测 regime."""
        return self.detect(rp)

    def detect_timeline(
        self, returns: np.ndarray, step: int = 20
    ) -> list[RegimeResult]:
        """生成 regime 时间线 (每 step 日检测一次, 用于回测)."""
        R = np.asarray(returns, dtype=float)
        if R.ndim == 1:
            rp = R
        else:
            w_eq = np.ones(R.shape[1]) / R.shape[1]
            rp = R @ w_eq

        timeline = []
        for end in range(self.window, len(rp) + 1, step):
            timeline.append(self.detect_series(rp[:end]))
        return timeline

    def _compute_moments(self, rp: np.ndarray) -> tuple[float, float, float]:
        """算年化波动率, 超额峰度, 偏度."""
        m1 = float(rp.mean())
        centered = rp - m1
        m2 = float((centered**2).mean())
        if m2 <= 0:
            return 0.0, 0.0, 0.0
        std = math.sqrt(m2)
        vol = std * math.sqrt(self.ann)
        skew = float((centered**3).mean() / std**3)
        kurt = float((centered**4).mean() / std**4 - 3.0)
        return vol, kurt, skew

    def _vol_quantile_rank(self, rp: np.ndarray) -> float:
        """当前波动率在历史滚动波动率中的分位 rank (0-1)."""
        T = len(rp)
        if self.window + self.min_periods > T:
            return 0.5

        rolling_vols = []
        for end in range(self.window, T + 1):
            w = rp[end - self.window : end]
            m2 = float(((w - w.mean()) ** 2).mean())
            if m2 > 0:
                rolling_vols.append(math.sqrt(m2) * math.sqrt(self.ann))

        if not rolling_vols:
            return 0.5

        current_vol = rolling_vols[-1]
        rank = float(np.mean(np.array(rolling_vols) <= current_vol))
        return rank

    def _confidence(
        self,
        vol_rank: float,
        kurt: float,
        high_vol: bool,
        fat_tail: bool,
    ) -> float:
        """置信度: 距阈值越远越高, 模糊区间 0.5."""
        vol_dist = abs(vol_rank - self.vol_quantile)
        kurt_dist = abs(kurt - self.kurtosis_threshold) / (
            self.kurtosis_threshold + 1.0
        )
        base = 0.5 + 0.5 * (vol_dist + kurt_dist) / 2.0
        return min(1.0, max(0.5, base))
