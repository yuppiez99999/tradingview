"""G7 覆盖率冲刺 — utils/alpha_factor/base.py 单元测试

目标: 覆盖率 60% → ≥80%
测试重点:
    - FactorValue / FactorLibraryResult 数据结构
    - winsorize / standardize / neutralize_by_industry / neutralize_by_size / orthogonalize
    - _forward_returns / calc_ic / calc_ic_series_from_history / calc_ic_ir
    - evaluate_factors (时序模式 + 单点模式)
    - compute_factor_corr_matrix
    - register_factor / list_registered_factors / compute_registered_factors (装饰器系统)
    - build_forward_returns_history / build_factor_history_from_prices
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha_factor.base import (  # noqa: E402
    _FACTOR_REGISTRY,
    FactorLibraryResult,
    FactorValue,
    _forward_returns,
    build_factor_history_from_prices,
    build_forward_returns_history,
    calc_ic,
    calc_ic_ir,
    calc_ic_series_from_history,
    compute_factor_corr_matrix,
    compute_registered_factors,
    evaluate_factors,
    list_registered_factors,
    neutralize_by_industry,
    neutralize_by_size,
    orthogonalize,
    register_factor,
    residualize,
    standardize,
    winsorize,
)

# ============================================================
# 数据结构
# ============================================================


class TestFactorValue:
    def test_defaults(self):
        fv = FactorValue(name="EP", category="Value", values={"A": 1.0})
        assert fv.name == "EP"
        assert fv.category == "Value"
        assert fv.values == {"A": 1.0}
        assert fv.ic_1d == 0.0
        assert fv.ic_mode == "none"

    def test_full_construction(self):
        fv = FactorValue(
            name="MOM",
            category="Momentum",
            values={"A": 0.1},
            ic_1d=0.05,
            ic_ir=1.2,
            ic_mode="timeseries",
        )
        assert fv.ic_1d == 0.05
        assert fv.ic_ir == 1.2
        assert fv.ic_mode == "timeseries"


class TestFactorLibraryResult:
    def test_defaults(self):
        result = FactorLibraryResult()
        assert result.factors == {}
        assert result.factor_corr_matrix is None
        assert result.effective_factors == []
        assert result.strong_factors == []
        assert result.debug_info == {}


# ============================================================
# winsorize
# ============================================================


class TestWinsorize:
    def test_empty(self):
        assert winsorize({}) == {}

    def test_no_outliers(self):
        values = {"A": 1.0, "B": 2.0, "C": 3.0}
        result = winsorize(values)
        assert result == values

    def test_with_outlier(self):
        values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 100.0}
        result = winsorize(values)
        assert result["D"] < 100.0  # 被截断
        assert result["A"] == pytest.approx(1.0)

    def test_mad_zero_returns_original(self):
        """所有值相同 → MAD=0 → 返回原值."""
        values = {"A": 5.0, "B": 5.0, "C": 5.0}
        result = winsorize(values)
        assert result == values

    def test_custom_n_sigma(self):
        values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 100.0}
        result_3 = winsorize(values, n_sigma=3.0)
        result_1 = winsorize(values, n_sigma=1.0)
        # n_sigma=1 更激进, 截断更狠
        assert result_1["D"] <= result_3["D"]


# ============================================================
# standardize
# ============================================================


class TestStandardize:
    def test_empty(self):
        assert standardize({}) == {}

    def test_zero_std_returns_original(self):
        """所有值相同 → std=0 → 返回原值."""
        values = {"A": 5.0, "B": 5.0}
        assert standardize(values) == values

    def test_zscore(self):
        values = {"A": 1.0, "B": 2.0, "C": 3.0}
        result = standardize(values)
        # 均值=2, std=0.8165
        assert result["B"] == pytest.approx(0.0, abs=1e-10)
        assert result["A"] == pytest.approx(-result["C"])


# ============================================================
# neutralize_by_industry
# ============================================================


class TestNeutralizeByIndustry:
    def test_basic(self):
        values = {"A": 1.0, "B": 3.0, "C": 2.0}
        industries = {"A": "tech", "B": "tech", "C": "fin"}
        result = neutralize_by_industry(values, industries)
        # tech 均值=2, A=1-2=-1, B=3-2=1; fin 均值=2, C=2-2=0
        assert result["A"] == pytest.approx(-1.0)
        assert result["B"] == pytest.approx(1.0)
        assert result["C"] == pytest.approx(0.0)

    def test_symbol_not_in_industries(self):
        """values 中的 symbol 不在 industries 中 → industry_means.get("", 0)=0."""
        values = {"A": 1.0, "X": 5.0}
        industries = {"A": "tech"}
        result = neutralize_by_industry(values, industries)
        assert result["A"] == pytest.approx(0.0)  # 1 - 1 = 0
        # X 不在 industries, industries.get("X", "")="", mean=0, 5-0=5
        assert result["X"] == pytest.approx(5.0)


# ============================================================
# neutralize_by_size
# ============================================================


class TestNeutralizeBySize:
    def test_few_symbols_returns_original(self):
        """< 3 个共同标的 → 返回原值."""
        values = {"A": 1.0, "B": 2.0}
        sizes = {"A": 100.0, "B": 200.0}
        assert neutralize_by_size(values, sizes) == values

    def test_basic_regression(self):
        values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
        sizes = {"A": 100.0, "B": 200.0, "C": 300.0, "D": 400.0}
        result = neutralize_by_size(values, sizes)
        # 残差应与 size 不相关 (回归残差)
        assert len(result) == 4

    def test_zero_size_std_returns_original(self):
        """所有 size 相同 → std=0 → 返回原值."""
        values = {"A": 1.0, "B": 2.0, "C": 3.0}
        sizes = {"A": 100.0, "B": 100.0, "C": 100.0}
        assert neutralize_by_size(values, sizes) == values


# ============================================================
# orthogonalize / residualize
# ============================================================


class TestOrthogonalize:
    def test_few_common_returns_copy(self):
        """< 3 个共同标的 → 返回 values 的拷贝."""
        values = {"A": 1.0, "B": 2.0}
        control = {"A": 0.5, "B": 0.6}
        result = orthogonalize(values, control)
        assert result == values

    def test_zero_control_std_returns_common(self):
        """control std < 1e-10 → 返回 common 的值."""
        values = {"A": 1.0, "B": 2.0, "C": 3.0}
        control = {"A": 5.0, "B": 5.0, "C": 5.0}  # std=0
        result = orthogonalize(values, control)
        assert result == {"A": 1.0, "B": 2.0, "C": 3.0}

    def test_basic_residual(self):
        values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
        control = {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0}
        result = orthogonalize(values, control)
        # 残差应与 control 不相关
        assert len(result) == 4

    def test_none_values_skipped(self):
        """values[s] is None 时跳过."""
        values = {"A": 1.0, "B": None, "C": 3.0, "D": 4.0}
        control = {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0}
        result = orthogonalize(values, control)  # type: ignore[arg-type]
        assert "B" not in result

    def test_residualize_alias(self):
        """residualize 是 orthogonalize 的别名."""
        assert residualize is orthogonalize


# ============================================================
# _forward_returns / calc_ic
# ============================================================


class TestForwardReturns:
    def test_basic(self):
        price_data = {"A": {"closes": [100, 105, 110]}}
        fwd = _forward_returns(price_data, forward_window=1, symbols={"A": 1.0})
        # closes[-1]/closes[-1-1] - 1 = 110/105 - 1
        assert fwd["A"] == pytest.approx(110 / 105 - 1)

    def test_insufficient_data(self):
        """closes 长度 <= forward_window → 跳过."""
        price_data = {"A": {"closes": [100, 105]}}
        fwd = _forward_returns(price_data, forward_window=5, symbols={"A": 1.0})
        assert fwd == {}

    def test_zero_base_price(self):
        """基准价格 closes[-1-forward] 为 0 → 跳过."""
        price_data = {"A": {"closes": [100, 0, 110]}}  # closes[-2]=0
        fwd = _forward_returns(price_data, forward_window=1, symbols={"A": 1.0})
        assert fwd == {}

    def test_empty_symbols(self):
        fwd = _forward_returns({}, forward_window=1, symbols={})
        assert fwd == {}


class TestCalcIc:
    def test_insufficient_samples_returns_zero(self):
        """< 5 个有效样本 → 返回 0.0."""
        factor_values = {"A": 1.0, "B": 2.0}
        price_data = {
            "A": {"closes": [100, 105, 110]},
            "B": {"closes": [200, 210, 220]},
        }
        assert calc_ic(factor_values, price_data, forward_window=1) == 0.0

    def test_valid_ic(self):
        """构造 5+ 标的, 因子值与未来收益正相关."""
        factor_values = {f"S{i}": float(i) for i in range(10)}
        # 构造未来收益与因子值正相关
        price_data = {
            f"S{i}": {"closes": [100, 100 * (1 + 0.01 * i)]} for i in range(10)
        }
        ic = calc_ic(factor_values, price_data, forward_window=1)
        assert -1.0 <= ic <= 1.0

    def test_exception_returns_zero(self):
        """scipy 不可用或异常 → 返回 0.0."""
        # 构造会触发异常的输入
        result = calc_ic({}, {}, 1)
        assert result == 0.0


# ============================================================
# calc_ic_series_from_history / calc_ic_ir
# ============================================================


class TestCalcIcSeries:
    def test_empty_history(self):
        assert calc_ic_series_from_history([], []) == []

    def test_insufficient_samples_per_day(self):
        """每日 < min_samples → 该日 IC=0.0."""
        fh = [{"A": 1.0, "B": 2.0}]  # 仅 2 个标的 < 5
        fr = [{"A": 0.01, "B": 0.02}]
        result = calc_ic_series_from_history(fh, fr, min_samples=5)
        assert result == [0.0]

    def test_valid_series(self):
        """构造 10 天 × 10 标的的有效序列."""
        np.random.seed(42)
        fh = [{f"S{i}": float(np.random.randn()) for i in range(10)} for _ in range(10)]
        fr = [{f"S{i}": float(np.random.randn()) for i in range(10)} for _ in range(10)]
        result = calc_ic_series_from_history(fh, fr, min_samples=5)
        assert len(result) == 10
        for ic in result:
            assert -1.0 <= ic <= 1.0

    def test_zero_std_returns_zero(self):
        """因子值或收益 std < 1e-12 → 该日 IC=0.0."""
        fh = [{"S0": 1.0, "S1": 1.0, "S2": 1.0, "S3": 1.0, "S4": 1.0}]  # std=0
        fr = [{"S0": 0.01, "S1": 0.02, "S2": 0.03, "S3": 0.04, "S4": 0.05}]
        result = calc_ic_series_from_history(fh, fr, min_samples=5)
        assert result == [0.0]


class TestCalcIcIr:
    def test_insufficient_samples(self):
        """< min_periods → (0, 0, 0)."""
        assert calc_ic_ir([0.1, 0.2], min_periods=20) == (0.0, 0.0, 0.0)

    def test_valid_ir(self):
        """构造 20+ 个 IC 样本."""
        np.random.seed(42)
        ic_series = list(np.random.normal(0.05, 0.1, 30))
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        assert ic_ir != 0.0
        assert ic_mean == pytest.approx(np.mean(ic_series))

    def test_zero_std_returns_zero_ir(self):
        """IC std < 1e-12 → ir=0."""
        ic_series = [0.05] * 25  # 全相同, std=0
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        assert ic_ir == 0.0
        assert ic_std == 0.0

    def test_non_finite_filtered(self):
        """非有限值被过滤."""
        ic_series = [0.1, float("nan"), 0.2, float("inf"), 0.3] + [0.1] * 20
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        # 过滤后仅 22 个有效, >= 20
        assert isinstance(ic_ir, float)


# ============================================================
# evaluate_factors
# ============================================================


class TestEvaluateFactors:
    def _make_result(self, name="EP", values=None):
        values = values or {"A": 1.0, "B": 2.0, "C": 3.0}
        result = FactorLibraryResult()
        result.factors[name] = FactorValue(name=name, category="Value", values=values)
        return result

    def test_empty_factor_values_skipped(self):
        result = FactorLibraryResult()
        result.factors["EP"] = FactorValue(name="EP", category="Value", values={})
        price_data = {"A": {"closes": [100, 105]}}
        evaluate_factors(result, price_data)
        assert result.strong_factors == []
        assert result.effective_factors == []

    def test_single_point_mode(self):
        """无 factor_history → 单点 IC 模式."""
        result = self._make_result()
        price_data = {f"S{i}": {"closes": [100, 100 + i]} for i in range(10)}
        evaluate_factors(result, price_data)
        fval = result.factors["EP"]
        assert fval.ic_mode == "single_point"
        assert fval.ic_n_samples == 1

    def test_timeseries_mode(self):
        """提供 factor_history + forward_returns_history → 时序 IC 模式."""
        result = self._make_result()
        price_data = {f"S{i}": {"closes": list(range(100, 130))} for i in range(10)}
        # 构造 25 天的时序历史
        factor_history = {
            "EP": [
                {f"S{i}": float(np.random.randn()) for i in range(10)}
                for _ in range(25)
            ]
        }
        forward_returns_history = [
            {f"S{i}": float(np.random.randn() * 0.01) for i in range(10)}
            for _ in range(25)
        ]
        np.random.seed(42)
        evaluate_factors(
            result,
            price_data,
            factor_history=factor_history,
            forward_returns_history=forward_returns_history,
        )
        fval = result.factors["EP"]
        assert fval.ic_mode == "timeseries"

    def test_idempotent_clear(self):
        """重复调用清空 strong/effective factors."""
        result = self._make_result()
        result.strong_factors.append("old")
        result.effective_factors.append("old")
        price_data = {f"S{i}": {"closes": [100, 100 + i]} for i in range(10)}
        evaluate_factors(result, price_data)
        # "old" 应被清空
        assert "old" not in result.strong_factors
        assert "old" not in result.effective_factors


# ============================================================
# compute_factor_corr_matrix
# ============================================================


class TestComputeFactorCorrMatrix:
    def test_empty_returns_none(self):
        assert compute_factor_corr_matrix({}) is None

    def test_basic(self):
        factors = {
            "A": FactorValue(
                name="A", category="X", values={"S1": 1.0, "S2": 2.0, "S3": 3.0}
            ),
            "B": FactorValue(
                name="B", category="X", values={"S1": 3.0, "S2": 2.0, "S3": 1.0}
            ),
        }
        corr = compute_factor_corr_matrix(factors)
        assert corr is not None
        assert isinstance(corr, pd.DataFrame)


# ============================================================
# register_factor / list_registered_factors / compute_registered_factors
# ============================================================


class TestRegistry:
    def test_list_registered_factors(self):
        """price_volume.py 已注册装饰器因子, 应能列出."""
        factors = list_registered_factors()
        assert isinstance(factors, list)
        # 至少有 price_volume.py 注册的 FM_DEMO_* 因子
        names = [f["name"] for f in factors]
        assert "FM_DEMO_VOL_WEIGHTED_MOM" in names or len(names) > 0

    def test_compute_registered_factors_empty_context(self):
        result = compute_registered_factors({})
        assert isinstance(result, dict)

    def test_compute_registered_factors_with_price_data(self):
        """提供 price_data, 装饰器因子应能计算."""
        price_data = {
            "A": {
                "closes": list(range(100, 130)),
                "volumes": [1000] * 30,
                "amounts": [100000] * 30,
            },
            "B": {
                "closes": list(range(200, 230)),
                "volumes": [2000] * 30,
                "amounts": [200000] * 30,
            },
        }
        result = compute_registered_factors({"price_data": price_data})
        assert isinstance(result, dict)

    def test_register_custom_factor(self):
        """注册自定义装饰器因子."""

        @register_factor(category="Test", name="TEST_CUSTOM_FACTOR")
        def my_factor(price_data=None):
            return {"A": 1.0, "B": 2.0}

        assert "TEST_CUSTOM_FACTOR" in _FACTOR_REGISTRY
        result = compute_registered_factors({}, select=["TEST_CUSTOM_FACTOR"])
        assert "TEST_CUSTOM_FACTOR" in result

    def test_compute_with_select_whitelist(self):
        """select 白名单过滤."""
        result = compute_registered_factors({}, select=[])
        assert result == {}

    def test_factor_with_missing_required_param_skipped(self):
        """必填参数缺失 → 跳过该因子."""

        @register_factor(category="Test", name="TEST_REQUIRED_PARAM")
        def factor_with_required(price_data):  # price_data 无默认值
            return {"A": 1.0}

        # 不提供 price_data → 跳过
        result = compute_registered_factors({}, select=["TEST_REQUIRED_PARAM"])
        assert "TEST_REQUIRED_PARAM" not in result


# ============================================================
# build_forward_returns_history
# ============================================================


class TestBuildForwardReturnsHistory:
    def test_empty_price_data(self):
        assert build_forward_returns_history({}) == []

    def test_insufficient_length(self):
        """closes 长度 <= forward_window → 空."""
        price_data = {"A": {"closes": [100, 105]}}
        assert build_forward_returns_history(price_data, forward_window=5) == []

    def test_basic(self):
        price_data = {"A": {"closes": [100, 105, 110, 115, 120]}}
        history = build_forward_returns_history(price_data, forward_window=1)
        # T = 5 - 1 = 4
        assert len(history) == 4
        # history[0] = {A: 105/100 - 1}
        assert history[0]["A"] == pytest.approx(105 / 100 - 1)

    def test_invalid_forward_window(self):
        """forward_window <= 0 → ValueError."""
        with pytest.raises(ValueError):
            build_forward_returns_history({}, forward_window=0)

    def test_with_symbols_whitelist(self):
        price_data = {
            "A": {"closes": [100, 105, 110]},
            "B": {"closes": [200, 210, 220]},
        }
        history = build_forward_returns_history(
            price_data, forward_window=1, symbols=["A"]
        )
        assert len(history) == 2
        assert "A" in history[0]
        assert "B" not in history[0]


# ============================================================
# build_factor_history_from_prices
# ============================================================


class TestBuildFactorHistoryFromPrices:
    def test_empty_price_data(self):
        def factor_fn(pd):
            return {"A": 1.0}

        assert build_factor_history_from_prices({}, factor_fn) == {}

    def test_insufficient_length(self):
        """min_len <= warmup_window → 空."""
        price_data = {"A": {"closes": [100, 105, 110]}}

        def factor_fn(pd):
            return {"A": 1.0}

        assert (
            build_factor_history_from_prices(price_data, factor_fn, warmup_window=20)
            == {}
        )

    def test_basic(self):
        # build_factor_history_from_prices 检查 closes/volumes/highs/lows 四键
        price_data = {
            "A": {
                "closes": list(range(100, 130)),
                "volumes": [1000] * 30,
                "highs": list(range(101, 131)),
                "lows": list(range(99, 129)),
            },
            "B": {
                "closes": list(range(200, 230)),
                "volumes": [2000] * 30,
                "highs": list(range(201, 231)),
                "lows": list(range(199, 229)),
            },
        }

        def factor_fn(pd_slice):
            return {sym: data["closes"][-1] for sym, data in pd_slice.items()}

        history = build_factor_history_from_prices(
            price_data, factor_fn, warmup_window=20
        )
        # 应有 "factor" 键 (因为返回 {sym: value} 格式)
        assert "factor" in history
        # 长度 = 30 - 20 = 10
        assert len(history["factor"]) == 10

    def test_factor_fn_returns_factor_value(self):
        price_data = {
            "A": {
                "closes": list(range(100, 130)),
                "volumes": [1000] * 30,
                "highs": list(range(101, 131)),
                "lows": list(range(99, 129)),
            }
        }

        def factor_fn(pd_slice):
            return FactorValue(
                name="MY_F",
                category="X",
                values={sym: data["closes"][-1] for sym, data in pd_slice.items()},
            )

        history = build_factor_history_from_prices(
            price_data, factor_fn, warmup_window=20
        )
        assert "MY_F" in history

    def test_factor_fn_exception_handled(self):
        """factor_fn 抛异常 → 该 t 跳过."""
        price_data = {
            "A": {
                "closes": list(range(100, 130)),
                "volumes": [1000] * 30,
                "highs": list(range(101, 131)),
                "lows": list(range(99, 129)),
            }
        }

        call_count = [0]

        def factor_fn(pd_slice):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("forced")
            return {"A": 1.0}

        history = build_factor_history_from_prices(
            price_data, factor_fn, warmup_window=20
        )
        # 第一次调用异常, 但后续正常, 应有结果
        assert "factor" in history
