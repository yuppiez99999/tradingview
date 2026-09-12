"""MVSK Regime 检测器单元测试 — P3.

被测模块: utils/mvsk_regime_detector.py
覆盖: 正态/肥尾 regime 判定, 数据不足回退, 1D/2D 输入, 时间线, 参数校验
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.mvsk_regime_detector import (
    Regime,
    RegimeDetector,
    RegimeResult,
)  # noqa: E402


def _normal_returns(n_days: int = 200, n_assets: int = 5, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return rng.randn(n_days, n_assets) * 0.01


def _fat_tail_returns(
    n_days: int = 200, n_assets: int = 5, seed: int = 1
) -> np.ndarray:
    rng = np.random.RandomState(seed)
    R = rng.randn(n_days, n_assets) * 0.01
    # 注入大跌冲击 → 负偏度 + 高峰度
    crash_idx = rng.choice(n_days, size=n_days // 10, replace=False)
    R[crash_idx] -= 0.05
    return R


class TestRegimeDetection:
    def test_normal_data_low_vol(self):
        # 500 日 30 资产等权组合波动率足够稳定, 单次 detect 应回 LOW_VOL_NORMAL
        R = _normal_returns(500, 30, seed=0)
        d = RegimeDetector(window=60)
        r = d.detect(R)
        assert r.regime == Regime.LOW_VOL_NORMAL
        assert r.use_mvsk is False
        assert r.trigger == "normal"

    def test_fat_tail_data_high_vol(self):
        R = _fat_tail_returns(200, 5, seed=1)
        d = RegimeDetector(window=60, kurtosis_threshold=1.0)
        r = d.detect(R)
        # 肥尾数据应触发 fat_tail 或 both
        assert r.use_mvsk is True
        assert r.regime == Regime.HIGH_VOL_FAT_TAIL
        assert r.trigger in ("fat_tail", "both", "high_vol")

    def test_insufficient_data_defaults_low_vol(self):
        R = _normal_returns(10, 3)
        d = RegimeDetector(window=60, min_periods=20)
        r = d.detect(R)
        assert r.regime == Regime.LOW_VOL_NORMAL
        assert r.confidence == 0.5

    def test_1d_series_input(self):
        rp = _normal_returns(200, 1, seed=2)[:, 0]
        d = RegimeDetector(window=60)
        r = d.detect(rp)
        assert isinstance(r, RegimeResult)
        assert r.volatility > 0

    def test_detect_series_alias(self):
        rp = _normal_returns(200, 1, seed=3)[:, 0]
        d = RegimeDetector(window=60)
        r1 = d.detect(rp)
        r2 = d.detect_series(rp)
        assert r1.regime == r2.regime
        assert abs(r1.volatility - r2.volatility) < 1e-10


class TestTimeline:
    def test_timeline_length(self):
        R = _normal_returns(200, 5)
        d = RegimeDetector(window=60)
        tl = d.detect_timeline(R, step=20)
        # range(60, 201, 20) = [60,80,...,200] 共 8 个检测点
        assert len(tl) == 8
        assert all(isinstance(r, RegimeResult) for r in tl)

    def test_timeline_no_fat_tail_for_normal(self):
        # 正态数据滚动波动率有随机性, 某些窗口可能触发 high_vol,
        # 但不应触发 fat_tail (excess_kurtosis < 3.0)
        R = _normal_returns(500, 30, seed=42)
        d = RegimeDetector(window=60)
        tl = d.detect_timeline(R, step=20)
        assert all(r.trigger in ("normal", "high_vol") for r in tl)
        assert all(r.excess_kurtosis < 3.0 for r in tl)


class TestDiagnostics:
    def test_volatility_positive(self):
        R = _normal_returns(200, 5)
        d = RegimeDetector(window=60)
        r = d.detect(R)
        assert r.volatility > 0

    def test_vol_quantile_rank_in_range(self):
        R = _normal_returns(200, 5)
        d = RegimeDetector(window=60)
        r = d.detect(R)
        assert 0.0 <= r.vol_quantile_rank <= 1.0

    def test_confidence_in_range(self):
        R = _fat_tail_returns(200, 5)
        d = RegimeDetector(window=60, kurtosis_threshold=1.0)
        r = d.detect(R)
        assert 0.5 <= r.confidence <= 1.0


class TestParameterValidation:
    def test_window_too_small_raises(self):
        with pytest.raises(ValueError, match="window"):
            RegimeDetector(window=5)

    def test_vol_quantile_out_of_range_raises(self):
        with pytest.raises(ValueError, match="vol_quantile"):
            RegimeDetector(vol_quantile=0.3)
        with pytest.raises(ValueError, match="vol_quantile"):
            RegimeDetector(vol_quantile=1.0)


class TestDataUnavailablePath:
    """SC-28: 数据源失败路径不得静默伪装成 LOW_VOL_NORMAL.

    修复前: 全 NaN / 含 inf 输入 → regime=LOW_VOL_NORMAL, use_mvsk=False,
    trigger="normal", 但 volatility/excess_kurtosis 为 NaN, 零告警 ——
    缺数据与"正常低波 regime"完全不可区分, 会静默翻转 MV↔MVSK 并污染回测.
    """

    def test_all_nan_is_explicitly_unavailable(self):
        R = np.full((150, 3), np.nan)
        d = RegimeDetector(window=60)
        r = d.detect(R)
        assert r.regime == Regime.UNKNOWN
        assert r.data_available is False
        assert r.use_mvsk is False
        assert r.trigger == "data_unavailable"
        assert r.unavailable_reason

    def test_all_nan_outputs_no_nan_diagnostics(self):
        R = np.full((150, 3), np.nan)
        d = RegimeDetector(window=60)
        r = d.detect(R)
        # 诊断字段为 0.0 而非 NaN, 避免 NaN 向下游传播
        for value in (
            r.volatility,
            r.vol_quantile_rank,
            r.excess_kurtosis,
            r.skewness,
            r.confidence,
        ):
            assert np.isfinite(value)

    def test_unavailable_is_distinguishable_from_low_vol(self):
        """核心判据: 数据缺失 ≠ 正常低波 —— 两者必须可区分."""
        d = RegimeDetector(window=60)
        low_vol = d.detect(_normal_returns(500, 30, seed=0))
        no_data = d.detect(np.full((150, 3), np.nan))
        assert low_vol.regime != no_data.regime
        assert low_vol.data_available is True
        assert no_data.data_available is False

    def test_inf_contaminated_is_treated_as_unavailable(self):
        R = _normal_returns(150, 3, seed=5)
        R[100, 0] = np.inf
        R[101, 1] = -np.inf
        # 更极端: 大范围 inf → 有效样本不足
        R[0:120, :] = np.inf
        d = RegimeDetector(window=60)
        r = d.detect(R)
        assert r.data_available is False
        assert r.regime == Regime.UNKNOWN
        assert np.isfinite(r.volatility)

    def test_minority_nan_is_cleaned_and_logged_not_silent(self, caplog):
        """少数 NaN 应剔除后正常判定, 且必须留下告警(不静默)."""
        R = _normal_returns(200, 3, seed=6)
        R[50:55, :] = np.nan  # 15/600 ≈ 2.5% 非有限
        d = RegimeDetector(window=60)
        with caplog.at_level("WARNING", logger="utils.mvsk_regime_detector"):
            r = d.detect(R)
        assert r.data_available is True
        assert r.regime != Regime.UNKNOWN
        assert np.isfinite(r.volatility)
        assert any("非有限值" in rec.message for rec in caplog.records)

    def test_empty_array_unavailable(self):
        d = RegimeDetector(window=60)
        r = d.detect(np.array([]))
        assert r.data_available is False
        assert r.regime == Regime.UNKNOWN

    def test_scalar_input_unavailable(self):
        d = RegimeDetector(window=60)
        r = d.detect(np.array(0.01))
        assert r.data_available is False
        assert r.regime == Regime.UNKNOWN

    def test_zero_asset_columns_unavailable(self):
        d = RegimeDetector(window=60)
        r = d.detect(np.empty((150, 0)))
        assert r.data_available is False
        assert r.regime == Regime.UNKNOWN

    def test_1d_all_nan_unavailable(self):
        d = RegimeDetector(window=60)
        r = d.detect(np.full(150, np.nan))
        assert r.data_available is False
        assert r.regime == Regime.UNKNOWN

    def test_all_zero_returns_is_still_valid_low_vol(self):
        """全零是合法输入(常量收益), 不是数据缺失 —— 保持 LOW_VOL_NORMAL."""
        d = RegimeDetector(window=60)
        r = d.detect(np.zeros((150, 3)))
        assert r.data_available is True
        assert r.regime == Regime.LOW_VOL_NORMAL

    def test_cleaned_series_below_min_periods_falls_back(self):
        """清洗后有效长度低于 min_periods → 走短历史回退 (不硬算矩).

        构造: T=30, 有效 16/30 = 53% (≥ MIN_VALID_RATIO, 故不判 UNKNOWN),
        但 min_periods=20 > 16 → 应走短历史回退而非硬算矩.
        """
        R = _normal_returns(30, 3, seed=9)
        R[:14, :] = np.nan  # 仅剩 16 行有效
        d = RegimeDetector(window=60, min_periods=20)
        r = d.detect(R)
        assert r.data_available is True
        assert r.regime == Regime.LOW_VOL_NORMAL
        assert r.volatility == 0.0

    def test_short_history_fallback_unchanged(self):
        """短历史回退语义保持不变 (向后兼容)."""
        d = RegimeDetector(window=60, min_periods=20)
        r = d.detect(_normal_returns(10, 3))
        assert r.regime == Regime.LOW_VOL_NORMAL
        assert r.data_available is True

    def test_timeline_does_not_silently_absorb_nan_window(self):
        """时间线中数据缺失窗口不得被静默当成正常 regime."""
        R = _normal_returns(200, 5, seed=8)
        R[80:, :] = np.nan  # 后 120/200 全 NaN → 末窗口有效样本占比 0.40 < 0.5
        d = RegimeDetector(window=60)
        tl = d.detect_timeline(R, step=20)
        assert all(isinstance(r, RegimeResult) for r in tl)
        # 末窗口 (后半段全 NaN) 应被判为不可用
        assert tl[-1].data_available is False
        assert tl[-1].regime == Regime.UNKNOWN
