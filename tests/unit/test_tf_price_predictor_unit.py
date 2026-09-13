"""tf_price_predictor 单元测试 — 价格预测模块."""

from __future__ import annotations

import numpy as np

from utils.tf_price_predictor import (
    PredictionResult,
    PricePredictor,
    StatisticalForecaster,
    TensorflowLSTMPredictor,
    TimesFMForecaster,
)


class TestPredictionResult:
    def test_defaults(self):
        r = PredictionResult(symbol="A", horizon=5, current_price=100, target_price=105)
        assert r.symbol == "A"
        assert r.horizon == 5
        assert r.direction == "NEUTRAL"
        assert r.confidence == 0.0
        assert r.method == "unknown"
        assert r.quantiles == {}

    def test_to_dict(self):
        r = PredictionResult(
            symbol="A",
            horizon=1,
            current_price=10,
            target_price=11,
            direction="UP",
            confidence=0.8,
        )
        d = r.to_dict()
        assert d["symbol"] == "A"
        assert d["direction"] == "UP"
        assert d["confidence"] == 0.8


class TestTimesFMForecaster:
    def test_not_available(self):
        f = TimesFMForecaster()
        assert f.available is False

    def test_forecast_returns_none(self):
        f = TimesFMForecaster()
        prices = np.array([1, 2, 3, 4, 5], dtype=np.float32)
        assert f.forecast(prices, horizon=3) is None


class TestTensorflowLSTMPredictor:
    def test_not_available(self):
        p = TensorflowLSTMPredictor()
        assert p.available is False

    def test_train_and_predict_returns_none(self):
        p = TensorflowLSTMPredictor()
        prices = np.random.randn(100)
        assert p.train_and_predict(prices, horizon=5) is None

    def test_prepare_data(self):
        p = TensorflowLSTMPredictor(sequence_length=10)
        prices = np.random.randn(50)
        X, y, mean, std = p._prepare_data(prices, horizon=5)
        assert X.shape[1] == 10
        assert y.shape[1] == 5
        # P0-M3: 归一化统计量来自训练段 (前 80%), 非全序列
        fit = prices[: int(len(prices) * 0.8)]
        assert abs(mean - fit.mean()) < 1e-9
        assert abs(std - fit.std()) < 1e-9

    def test_prepare_data_zero_std(self):
        p = TensorflowLSTMPredictor(sequence_length=5)
        prices = np.array([5.0] * 20)
        X, y, _mean, std = p._prepare_data(prices, horizon=3)
        assert X.shape == (13, 5)
        assert y.shape == (13, 3)
        assert std == 1  # 零方差保护


class TestStatisticalForecaster:
    def test_available(self):
        f = StatisticalForecaster()
        assert f.available is True

    def test_ma_momentum_forecast(self):
        f = StatisticalForecaster()
        prices = np.array(
            [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], dtype=np.float64
        )
        forecast, quantiles = f._ma_momentum_forecast(prices, horizon=3)
        assert len(forecast) == 3
        assert "q10" in quantiles
        assert "q50" in quantiles
        assert "q90" in quantiles

    def test_forecast(self):
        f = StatisticalForecaster()
        prices = np.random.randn(60) + 100
        forecast, quantiles = f.forecast(prices, horizon=5)
        assert len(forecast) == 5

    def test_forecast_short_prices(self):
        f = StatisticalForecaster()
        prices = np.array([10, 11, 12], dtype=np.float64)
        forecast, quantiles = f.forecast(prices, horizon=2)
        assert len(forecast) == 2

    def test_quantile_spread(self):
        f = StatisticalForecaster()
        prices = np.array(
            [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], dtype=np.float64
        )
        forecast, quantiles = f._ma_momentum_forecast(prices, horizon=3)
        for i in range(3):
            assert quantiles["q10"][i] < quantiles["q50"][i] < quantiles["q90"][i]


class TestPricePredictor:
    def test_init(self):
        p = PricePredictor()
        assert p.statistical.available is True

    def test_predict_fallback_short_data(self):
        p = PricePredictor()
        prices = np.array([10, 20])
        result = p.predict("A", prices, horizon=5)
        assert result.method == "fallback"
        assert result.direction == "NEUTRAL"

    def test_predict_normal(self):
        p = PricePredictor()
        prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, 60))
        result = p.predict("TEST", prices, horizon=5)
        assert result.symbol == "TEST"
        assert result.horizon == 5
        assert result.method in ("ma_momentum", "arima", "timesfm", "tensorflow_lstm")
        assert result.direction in ("UP", "DOWN", "NEUTRAL")
        assert -1 <= result.signal_strength <= 1

    def test_predict_with_current_price(self):
        p = PricePredictor()
        prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, 60))
        result = p.predict("A", prices, horizon=1, current_price=105)
        assert result.current_price == 105

    def test_batch_predict(self):
        p = PricePredictor()
        prices1 = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, 60))
        prices2 = 50 * np.cumprod(1 + np.random.normal(0.001, 0.02, 60))
        results = p.batch_predict({"A": prices1, "B": prices2}, horizon=3)
        assert "A" in results
        assert "B" in results
        assert results["A"].horizon == 3

    def test_get_signal_for_fusion(self):
        p = PricePredictor()
        prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, 60))
        signal = p.get_signal_for_fusion("A", prices, horizon=5)
        assert -1 <= signal <= 1
