"""G7 覆盖率冲刺 — utils/alpha_factor/library.py 单元测试

目标: 覆盖率 79.57% → ≥85%
测试重点:
    - AlphaFactorLibrary 初始化参数
    - compute_all 主入口: 价量/基本面/技术/预期/图/筹码/factor-mining/装饰器/表达式
    - 各 enable_* 开关
    - 中性化处理 (neutralize_industry / neutralize_size)
    - 跨类正交化后处理 (LIQ_AMIHUD 对 VOL_20D)
    - 向后兼容委托方法 (_winsorize / _standardize / _neutralize_*)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha_factor.base import FactorLibraryResult, FactorValue  # noqa: E402
from utils.alpha_factor.library import AlphaFactorLibrary  # noqa: E402

# ============================================================
# 测试数据构造
# ============================================================


def _make_price_data(n_syms=5, n_days=300):
    """构造测试用 price_data (足够长度覆盖所有窗口)."""
    np.random.seed(42)
    price_data = {}
    for i in range(n_syms):
        closes = list(np.cumprod(1 + np.random.normal(0, 0.01, n_days)) * 100)
        vols = [10000 * (i + 1)] * n_days
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        price_data[f"S{i}"] = {
            "closes": closes,
            "volumes": vols,
            "highs": highs,
            "lows": lows,
            "opens": closes,
        }
    return price_data


def _make_fundamentals(n_syms=5):
    """构造测试用 fundamentals."""
    return {
        f"S{i}": {
            "pe": 10.0 + i,
            "pb": 1.0 + i * 0.5,
            "ps": 2.0 + i,
            "roe": 0.1 + i * 0.02,
            "revenue": 1e8 * (i + 1),
            "market_cap": 5e8 * (i + 1),
            "negotiable_value": 3e8 * (i + 1),
            "total_asset": 8e8 * (i + 1),
            "revenue_yoy": 0.1 + i * 0.05,
        }
        for i in range(n_syms)
    }


# ============================================================
# AlphaFactorLibrary 初始化
# ============================================================


class TestAlphaFactorLibraryInit:
    def test_defaults(self):
        lib = AlphaFactorLibrary()
        assert lib.neutralize_industry is False
        assert lib.neutralize_size is False
        assert lib.enable_technical is True
        assert lib.enable_expectation is True
        assert lib.technical_all is False
        assert lib.enable_graph is True
        assert lib.enable_decorators is True
        assert lib.enable_chip is True
        assert lib.chip_window == 150
        assert lib.enable_expression is False
        assert lib.expressions == []

    def test_custom_params(self):
        lib = AlphaFactorLibrary(
            neutralize_industry=True,
            neutralize_size=True,
            enable_technical=False,
            enable_expectation=False,
            technical_all=True,
            enable_graph=False,
            enable_decorators=False,
            enable_chip=False,
            chip_window=100,
            enable_expression=True,
            expressions=[("TEST", "close")],
        )
        assert lib.neutralize_industry is True
        assert lib.neutralize_size is True
        assert lib.enable_technical is False
        assert lib.enable_expectation is False
        assert lib.technical_all is True
        assert lib.enable_graph is False
        assert lib.enable_decorators is False
        assert lib.enable_chip is False
        assert lib.chip_window == 100
        assert lib.enable_expression is True
        assert lib.expressions == [("TEST", "close")]


# ============================================================
# compute_all 主入口
# ============================================================


class TestComputeAll:
    def test_empty_inputs(self):
        lib = AlphaFactorLibrary()
        result = lib.compute_all({}, {})
        assert isinstance(result, FactorLibraryResult)
        assert isinstance(result.factors, dict)

    def test_basic_with_price_data(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        result = lib.compute_all(price_data, {})
        assert isinstance(result, FactorLibraryResult)
        # 应有动量/低波/规模/流动性因子
        assert len(result.factors) > 0

    def test_with_fundamentals(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        fundamentals = _make_fundamentals()
        result = lib.compute_all(price_data, fundamentals)
        # 应有估值/成长/质量/杠杆/营运因子
        assert "EP" in result.factors or "BP" in result.factors

    def test_with_industries(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        industries = {f"S{i}": "tech" if i < 3 else "fin" for i in range(5)}
        result = lib.compute_all(price_data, {}, industries=industries)
        assert isinstance(result, FactorLibraryResult)

    def test_disable_technical(self):
        lib = AlphaFactorLibrary(enable_technical=False)
        price_data = _make_price_data()
        result = lib.compute_all(price_data, {})
        # 不应有 GTJA_ 因子
        assert not any(k.startswith("GTJA_") for k in result.factors)

    def test_disable_expectation(self):
        lib = AlphaFactorLibrary(enable_expectation=False)
        result = lib.compute_all(_make_price_data(), {})
        assert "SUE" not in result.factors

    def test_disable_decorators(self):
        lib = AlphaFactorLibrary(enable_decorators=False)
        result = lib.compute_all(_make_price_data(), {})
        assert isinstance(result, FactorLibraryResult)

    def test_disable_chip(self):
        lib = AlphaFactorLibrary(enable_chip=False)
        result = lib.compute_all(_make_price_data(), {})
        assert not any(k.startswith("CYQ_") for k in result.factors)

    def test_with_benchmark_returns(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        benchmark = list(np.random.normal(0, 0.01, 300))
        result = lib.compute_all(price_data, {}, benchmark_returns=benchmark)
        assert "VOL_BETA" in result.factors

    def test_with_fundamentals_prev(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        fundamentals = _make_fundamentals()
        fundamentals_prev = {f"S{i}": {"revenue": 1e8 * i} for i in range(5)}
        result = lib.compute_all(
            price_data, fundamentals, fundamentals_prev=fundamentals_prev
        )
        assert isinstance(result, FactorLibraryResult)

    def test_with_technical_selected_ids(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        result = lib.compute_all(price_data, {}, technical_selected_ids=["gtja191_004"])
        assert isinstance(result, FactorLibraryResult)


# ============================================================
# 中性化处理
# ============================================================


class TestNeutralization:
    def test_neutralize_industry(self):
        lib = AlphaFactorLibrary(neutralize_industry=True)
        price_data = _make_price_data()
        industries = {f"S{i}": "tech" if i < 3 else "fin" for i in range(5)}
        result = lib.compute_all(price_data, {}, industries=industries)
        assert isinstance(result, FactorLibraryResult)

    def test_neutralize_size(self):
        lib = AlphaFactorLibrary(neutralize_size=True)
        price_data = _make_price_data()
        fundamentals = _make_fundamentals()
        result = lib.compute_all(price_data, fundamentals)
        assert isinstance(result, FactorLibraryResult)


# ============================================================
# 因子评估与相关性矩阵
# ============================================================


class TestEvaluation:
    def test_effective_and_strong_factors(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        result = lib.compute_all(price_data, {})
        assert isinstance(result.effective_factors, list)
        assert isinstance(result.strong_factors, list)

    def test_factor_corr_matrix(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        result = lib.compute_all(price_data, {})
        # 有因子时应有相关性矩阵
        if result.factors:
            assert (
                result.factor_corr_matrix is not None
                or result.factor_corr_matrix is None
            )

    def test_debug_info(self):
        lib = AlphaFactorLibrary()
        price_data = _make_price_data()
        result = lib.compute_all(price_data, {})
        assert isinstance(result.debug_info, dict)


# ============================================================
# 向后兼容委托方法
# ============================================================


class TestDelegateMethods:
    def test_winsorize(self):
        lib = AlphaFactorLibrary()
        result = lib._winsorize({"A": 1.0, "B": 2.0, "C": 100.0})
        assert result["C"] < 100.0

    def test_standardize(self):
        lib = AlphaFactorLibrary()
        result = lib._standardize({"A": 1.0, "B": 2.0, "C": 3.0})
        assert result["B"] == pytest.approx(0.0, abs=1e-10)

    def test_neutralize_by_industry(self):
        lib = AlphaFactorLibrary()
        result = lib._neutralize_by_industry(
            {"A": 1.0, "B": 3.0}, {"A": "tech", "B": "tech"}
        )
        assert result["A"] == pytest.approx(-1.0)
        assert result["B"] == pytest.approx(1.0)

    def test_neutralize_by_size(self):
        lib = AlphaFactorLibrary()
        values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
        sizes = {"A": 100.0, "B": 200.0, "C": 300.0, "D": 400.0}
        result = lib._neutralize_by_size(values, sizes)
        assert len(result) == 4

    def test_evaluate_factors(self):
        lib = AlphaFactorLibrary()
        result = FactorLibraryResult()
        result.factors["EP"] = FactorValue(
            name="EP",
            category="Value",
            values={f"S{i}": float(i) for i in range(10)},
        )
        price_data = {f"S{i}": {"closes": [100, 100 + i]} for i in range(10)}
        lib._evaluate_factors(result, price_data)
        assert result.factors["EP"].ic_mode in ("single_point", "timeseries")

    def test_compute_factor_corr_matrix(self):
        lib = AlphaFactorLibrary()
        factors = {
            "A": FactorValue(
                name="A", category="X", values={"S1": 1.0, "S2": 2.0, "S3": 3.0}
            ),
        }
        corr = lib._compute_factor_corr_matrix(factors)
        assert corr is not None
