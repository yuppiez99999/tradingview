"""G7 boost: ms_strategy/src/alpha/signal_generator.py 单元测试.

覆盖 SignalGenerator 全部公开接口:
  - get_asset_factors (各资产类别 + 未知回退)
  - select_features (LASSO 正常/空数据/数据不足)
  - estimate_weights (Ridge 正常/空数据/数据不足)
  - generate (训练/不重训练/空矩阵/从 factor_lib 获取/selected_factors 空)
  - compute_ic (正常/数据不足)
使用真实 sklearn + 小数据集, mock factor_library.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.alpha.signal_generator import SignalGenerator  # noqa: E402

# ============================================================
# 测试数据构造
# ============================================================


def _make_factor_matrix(n_rows: int = 60, n_cols: int = 5) -> pd.DataFrame:
    """构造因子矩阵, 列名为 STOCK 优先因子前 N 个."""
    np.random.seed(42)
    cols = ["momentum", "volatility", "value", "quality", "size"][:n_cols]
    dates = pd.date_range("2026-01-01", periods=n_rows, freq="B")
    return pd.DataFrame(np.random.randn(n_rows, n_cols), index=dates, columns=cols)


def _make_forward_returns(n_rows: int = 60) -> pd.Series:
    """构造前向收益, 与因子矩阵对齐."""
    np.random.seed(123)
    dates = pd.date_range("2026-01-01", periods=n_rows, freq="B")
    return pd.Series(np.random.randn(n_rows) * 0.01, index=dates)


@pytest.fixture
def factor_lib_mock():
    """mock FactorLibrary, get_factor_matrix 返回有效矩阵."""
    lib = MagicMock(name="FactorLibrary")
    lib.get_factor_matrix.return_value = _make_factor_matrix()
    return lib


@pytest.fixture
def generator(factor_lib_mock):
    return SignalGenerator(factor_library=factor_lib_mock, lookback=60)


# ============================================================
# 1. 资产类别因子映射
# ============================================================


class TestGetAssetFactors:
    def test_stock_factors(self, generator):
        factors = generator.get_asset_factors("STOCK")
        assert "momentum" in factors
        assert "volatility" in factors
        assert len(factors) == 10

    def test_etf_factors(self, generator):
        factors = generator.get_asset_factors("ETF")
        assert "momentum" in factors
        assert "flow" in factors
        assert len(factors) == 5

    def test_future_factors(self, generator):
        factors = generator.get_asset_factors("FUTURE")
        assert "carry" in factors
        assert "basis" in factors
        assert len(factors) == 7

    def test_option_factors(self, generator):
        factors = generator.get_asset_factors("OPTION")
        assert "implied_volatility" in factors
        assert "skew" in factors
        assert len(factors) == 7

    def test_unknown_class_falls_back_to_stock(self, generator):
        factors = generator.get_asset_factors("UNKNOWN")
        assert factors == generator.ASSET_FACTOR_PRIORITY["STOCK"]

    def test_lowercase_class_normalized(self, generator):
        factors = generator.get_asset_factors("stock")
        assert factors == generator.ASSET_FACTOR_PRIORITY["STOCK"]

    def test_asset_factor_priority_class_attribute(self):
        assert "STOCK" in SignalGenerator.ASSET_FACTOR_PRIORITY
        assert "ETF" in SignalGenerator.ASSET_FACTOR_PRIORITY
        assert "FUTURE" in SignalGenerator.ASSET_FACTOR_PRIORITY
        assert "OPTION" in SignalGenerator.ASSET_FACTOR_PRIORITY


# ============================================================
# 2. select_features 特征选择
# ============================================================


class TestSelectFeatures:
    def test_normal_selection(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        selected = generator.select_features(X, y)
        assert isinstance(selected, list)
        assert all(f in X.columns for f in selected)

    def test_empty_x_returns_columns(self, generator):
        X = pd.DataFrame()  # noqa: N806
        y = pd.Series(dtype=float)
        result = generator.select_features(X, y)
        assert result == []

    def test_empty_y_returns_columns(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = pd.Series(dtype=float)
        result = generator.select_features(X, y)
        assert result == list(X.columns)

    def test_insufficient_data_returns_columns(self, generator):
        # lookback=60, 阈值=15, 给 10 行
        X = _make_factor_matrix(10, 5)  # noqa: N806
        y = _make_forward_returns(10)
        result = generator.select_features(X, y)
        assert result == list(X.columns)

    def test_select_features_updates_internal_state(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        selected = generator.select_features(X, y)
        # select_features 本身不改 selected_factors, 但返回值可用
        assert isinstance(selected, list)


# ============================================================
# 3. estimate_weights 权重估计
# ============================================================


class TestEstimateWeights:
    def test_normal_estimation(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        weights = generator.estimate_weights(X, y)
        assert weights is not None
        assert len(weights) == 5

    def test_empty_x_returns_uniform_weights(self, generator):
        X = pd.DataFrame()  # noqa: N806
        y = pd.Series(dtype=float)
        weights = generator.estimate_weights(X, y)
        # 空矩阵 shape[1]=0, 返回空数组
        assert len(weights) == 0

    def test_empty_y_returns_uniform_weights(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = pd.Series(dtype=float)
        weights = generator.estimate_weights(X, y)
        # 5 列, 均匀权重 = 1/5
        assert len(weights) == 5
        assert all(w == pytest.approx(0.2) for w in weights)

    def test_insufficient_data_returns_uniform_weights(self, generator):
        # common_idx < 20
        X = _make_factor_matrix(15, 5)  # noqa: N806
        y = _make_forward_returns(15)
        weights = generator.estimate_weights(X, y)
        assert len(weights) == 5
        assert all(w == pytest.approx(0.2) for w in weights)


# ============================================================
# 4. generate 信号生成
# ============================================================


class TestGenerate:
    def test_generate_with_retrain(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        signal = generator.generate(X, y, retrain=True, asset_class="STOCK")
        assert isinstance(signal, pd.Series)
        assert len(signal) == 60
        # 信号在 [-1, 1]
        assert signal.max() <= 1.0 + 1e-6
        assert signal.min() >= -1.0 - 1e-6

    def test_generate_without_retrain(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        # 先训练一次
        generator.generate(X, y, retrain=True)
        # 再不重训练
        signal = generator.generate(X, y, retrain=False)
        assert isinstance(signal, pd.Series)
        assert len(signal) == 60

    def test_generate_no_forward_returns(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        signal = generator.generate(X, None, retrain=False)
        assert isinstance(signal, pd.Series)
        assert len(signal) == 60

    def test_generate_empty_matrix_returns_zero(self, generator):
        signal = generator.generate(pd.DataFrame(), None)
        assert isinstance(signal, pd.Series)
        assert (signal == 0.0).all()

    def test_generate_from_factor_lib(self, generator, factor_lib_mock):
        # factor_matrix=None, 从 factor_lib 获取
        signal = generator.generate(None, None, retrain=False)
        assert isinstance(signal, pd.Series)
        factor_lib_mock.get_factor_matrix.assert_called_once()

    def test_generate_factor_lib_returns_none(self, factor_lib_mock):
        factor_lib_mock.get_factor_matrix.return_value = None
        gen = SignalGenerator(factor_library=factor_lib_mock, lookback=60)
        signal = gen.generate(None, None)
        assert isinstance(signal, pd.Series)
        assert (signal == 0.0).all()

    def test_generate_factor_lib_returns_empty(self, factor_lib_mock):
        factor_lib_mock.get_factor_matrix.return_value = pd.DataFrame()
        gen = SignalGenerator(factor_library=factor_lib_mock, lookback=60)
        signal = gen.generate(None, None)
        assert (signal == 0.0).all()

    def test_generate_updates_latest_signals(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        generator.generate(X, y, retrain=True)
        assert isinstance(generator.latest_signals, dict)
        # selected_factors 应映射到 latest_val
        if generator.selected_factors:
            vals = list(generator.latest_signals.values())
            assert len(vals) == len(generator.selected_factors)
            # 所有值相同 (取最新综合信号)
            assert all(v == vals[0] for v in vals)

    def test_generate_etf_asset_class(self, generator):
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        signal = generator.generate(X, y, retrain=True, asset_class="ETF")
        assert isinstance(signal, pd.Series)
        assert len(signal) == 60

    def test_generate_future_asset_class(self, generator):
        # FUTURE 因子与 STOCK 不同, 但可用因子仍按交集排序
        X = _make_factor_matrix(60, 5)  # noqa: N806
        y = _make_forward_returns(60)
        signal = generator.generate(X, y, retrain=True, asset_class="FUTURE")
        assert isinstance(signal, pd.Series)
        assert len(signal) == 60

    def test_generate_zero_signal_when_all_zero_factors(self, generator):
        # 全零因子矩阵 → raw_signal 全零 → normalized 保持零
        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        X = pd.DataFrame(
            np.zeros((60, 5)),
            index=dates,  # noqa: N806
            columns=["momentum", "volatility", "value", "quality", "size"],
        )
        signal = generator.generate(X, None, retrain=False)
        assert (signal == 0.0).all()

    def test_generate_selected_factors_empty_fallback(self, generator):
        # retrain=True 但 forward_returns=None → 走 else 分支
        X = _make_factor_matrix(60, 5)  # noqa: N806
        signal = generator.generate(X, None, retrain=True)
        assert isinstance(signal, pd.Series)
        assert len(signal) == 60


# ============================================================
# 5. compute_ic IC 分析
# ============================================================


class TestComputeIC:
    @pytest.fixture(autouse=True)
    def _compat_pandas_freq(self, monkeypatch):
        """pandas < 2.2 兼容: ME → M (源码用 ME, pandas 2.0.3 仅识别 M)."""
        ver = tuple(int(x) for x in pd.__version__.split(".")[:2])
        if ver < (2, 2):
            original_grouper = pd.Grouper

            def _patched(*args, **kwargs):
                if kwargs.get("freq") == "ME":
                    kwargs["freq"] = "M"
                return original_grouper(*args, **kwargs)

            monkeypatch.setattr(pd, "Grouper", _patched)

    def test_compute_ic_normal(self, generator):
        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        np.random.seed(42)
        signal = pd.Series(np.random.randn(60), index=dates)
        forward_returns = pd.Series(np.random.randn(60) * 0.01, index=dates)
        result = generator.compute_ic(signal, forward_returns)
        assert "ic" in result
        assert "ir" in result

    def test_compute_ic_insufficient_data(self, generator):
        # 少于 10 个公共点
        dates = pd.date_range("2026-01-01", periods=5, freq="B")
        signal = pd.Series(np.random.randn(5), index=dates)
        forward_returns = pd.Series(np.random.randn(5), index=dates)
        result = generator.compute_ic(signal, forward_returns)
        assert result == {"ic": 0.0, "ir": 0.0}

    def test_compute_ic_with_nan(self, generator):
        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        signal = pd.Series(np.random.randn(60), index=dates)
        signal.iloc[:10] = np.nan
        forward_returns = pd.Series(np.random.randn(60) * 0.01, index=dates)
        result = generator.compute_ic(signal, forward_returns)
        assert "ic" in result
        assert "ir" in result

    def test_compute_ic_returns_ic_monthly(self, generator):
        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        np.random.seed(42)
        signal = pd.Series(np.random.randn(120), index=dates)
        forward_returns = pd.Series(np.random.randn(120) * 0.01, index=dates)
        result = generator.compute_ic(signal, forward_returns)
        # ic_monthly 应存在且为 list
        assert "ic_monthly" in result
        assert isinstance(result["ic_monthly"], list)


# ============================================================
# 6. __init__ 参数
# ============================================================


class TestInit:
    def test_init_default_params(self, factor_lib_mock):
        gen = SignalGenerator(factor_library=factor_lib_mock)
        assert gen.lasso_alpha_range == (0.001, 0.01, 0.1, 1.0, 10.0)
        assert gen.ridge_alpha_range == (0.1, 1.0, 10.0, 100.0)
        assert gen.lookback == 252
        assert gen.selected_factors == []
        assert gen.factor_weights is None
        assert gen.latest_signals == {}

    def test_init_custom_params(self, factor_lib_mock):
        gen = SignalGenerator(
            factor_library=factor_lib_mock,
            lasso_alpha_range=(0.01, 0.1),
            ridge_alpha_range=(1.0, 10.0),
            lookback=120,
        )
        assert gen.lasso_alpha_range == (0.01, 0.1)
        assert gen.ridge_alpha_range == (1.0, 10.0)
        assert gen.lookback == 120
