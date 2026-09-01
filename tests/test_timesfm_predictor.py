"""TimesFM 预测器单测 — 全部 mock, 不依赖真实 timesfm 安装"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_predictor(enabled=True, available=True, config_overrides=None):
    """构造一个 mock 的 TimesFMPredictor, 跳过真实模型加载。"""
    from utils.timesfm_predictor import TimesFMPredictor

    pred = TimesFMPredictor.__new__(TimesFMPredictor)
    pred.config = {
        "enabled": enabled,
        "model_version": "2.5",
        "checkpoint": "models/timesfm/",
        "context_length": 16384,
        "horizon_default": 5,
        "quantiles": [0.1, 0.5, 0.9],
        "preflight": {"min_ram_gb": 2.0, "min_disk_gb": 1.0, "gpu_optional": True},
        "hybrid": {"alpha": 0.6},
        "device": "cpu",
    }
    if config_overrides:
        pred.config.update(config_overrides)
    pred.enabled = enabled
    pred.available = available
    pred._device = "cpu"
    pred._tfm = MagicMock() if available else None
    return pred


class TestPreflight:
    def test_disabled_bypass(self):
        pred = _make_predictor(enabled=False, available=False)
        assert pred.available is False

    def test_preflight_no_psutil(self):
        from utils.timesfm_predictor import TimesFMPredictor

        pred = TimesFMPredictor.__new__(TimesFMPredictor)
        pred.config = {
            "preflight": {"min_ram_gb": 2.0, "min_disk_gb": 1.0},
            "checkpoint": "models/timesfm/",
        }
        with patch.dict(sys.modules, {"psutil": None}):
            assert pred.preflight_check() is True


class TestForecast:
    def test_unavailable_returns_zeros(self):
        pred = _make_predictor(available=False)
        out = pred.forecast(np.array([1.0, 2.0, 3.0]), horizon=5)
        assert out.available is False
        assert np.all(out.point == 0)
        assert len(out.point) == 5

    @patch.dict(sys.modules, {"timesfm": MagicMock(TimesFM_2p5_200M_torch=type)})
    def test_forecast_success(self):
        pred = _make_predictor(available=True)
        pred._tfm.forecast.return_value = (
            np.array([[0.1, 0.2, 0.3, 0.4, 0.5]]),
            np.array(
                [
                    [
                        [0.05, 0.1, 0.2, 0.3, 0.4],
                        [0.1, 0.2, 0.3, 0.4, 0.5],
                        [0.2, 0.3, 0.4, 0.5, 0.6],
                    ]
                ]
            ),
        )
        out = pred.forecast(np.arange(100, dtype=float), horizon=5)
        assert out.available is True
        assert len(out.point) == 5
        assert out.p10 is not None

    def test_forecast_exception_degrade(self):
        pred = _make_predictor(available=True)
        pred._tfm.forecast.side_effect = RuntimeError("boom")
        out = pred.forecast(np.arange(50, dtype=float), horizon=3)
        assert out.available is False
        assert np.all(out.point == 0)

    def test_forecast_short_series(self):
        pred = _make_predictor(available=True)
        out = pred.forecast(np.array([1.0]), horizon=5)
        assert out.available is False

    def test_forecast_uses_default_horizon(self):
        pred = _make_predictor(available=False)
        out = pred.forecast(np.array([1.0, 2.0]))
        assert len(out.point) == 5

    def test_forecast_immutable(self):
        pred = _make_predictor(available=False)
        series = np.array([1.0, 2.0, 3.0])
        original = series.copy()
        pred.forecast(series, horizon=3)
        assert np.array_equal(series, original)


class TestHybridBlend:
    def test_timesfm_unavailable_returns_lgb(self):
        from utils.timesfm_predictor import ForecastResult

        pred = _make_predictor(available=False)
        result = ForecastResult(point=np.zeros(3), available=False)
        assert pred.hybrid_blend(0.5, result) == 0.5

    def test_blend_default_alpha(self):
        from utils.timesfm_predictor import ForecastResult

        pred = _make_predictor(available=True)
        result = ForecastResult(point=np.array([0.4]), available=True)
        out = pred.hybrid_blend(0.6, result)
        assert out == pytest.approx(0.6 * 0.6 + 0.4 * 0.4)

    def test_blend_custom_alpha(self):
        from utils.timesfm_predictor import ForecastResult

        pred = _make_predictor(available=True)
        result = ForecastResult(point=np.array([0.8]), available=True)
        out = pred.hybrid_blend(0.2, result, alpha=0.5)
        assert out == pytest.approx(0.5 * 0.2 + 0.5 * 0.8)


class TestCovariates:
    @patch.dict(sys.modules, {"timesfm": MagicMock(TimesFM_2p5_200M_torch=type)})
    def test_no_xreg_fallback_to_forecast(self):
        pred = _make_predictor(available=True)
        pred._tfm.forecast.return_value = (np.array([[0.1, 0.2]]), None)
        out = pred.forecast_with_covariates(np.arange(20, dtype=float), horizon=2)
        assert out.available is True

    @patch.dict(sys.modules, {"timesfm": MagicMock(TimesFM_2p5_200M_torch=type)})
    def test_xreg_exception_degrade(self):
        pred = _make_predictor(available=True)
        pred._tfm.forecast.return_value = (np.array([[0.1, 0.2]]), None)
        out = pred.forecast_with_covariates(
            np.arange(20, dtype=float),
            xreg_dynamic=np.arange(20, dtype=float),
            horizon=2,
        )
        assert out.available is True


class TestInit:
    def test_config_missing_bypass(self, tmp_path):
        from utils.timesfm_predictor import TimesFMPredictor

        pred = TimesFMPredictor(config_path=str(tmp_path / "nonexist.yaml"))
        assert pred.enabled is False
        assert pred.available is False
