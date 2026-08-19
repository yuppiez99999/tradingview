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

from utils.mvsk_regime_detector import Regime, RegimeDetector, RegimeResult  # noqa: E402


def _normal_returns(n_days: int = 200, n_assets: int = 5, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return rng.randn(n_days, n_assets) * 0.01


def _fat_tail_returns(n_days: int = 200, n_assets: int = 5, seed: int = 1) -> np.ndarray:
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
