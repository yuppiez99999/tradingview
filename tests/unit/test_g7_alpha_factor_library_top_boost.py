"""G7 覆盖率冲刺 — utils/alpha_factor_library.py (顶层 shim) 单元测试.

目标: 覆盖率从 12.66% → ≥70%

测试范围:
    1. 导出符号可用性 (AlphaFactorLibrary / winsorize / DEFAULT_GTJA / list_available_factors 等)
    2. _build_demo_data: 返回结构 / 字段完整性
    3. _print_summary: 无 corr_matrix / 有 corr_matrix / 高相关对 / 多于 3 个因子
    4. main: 完整演示流程 (小数据 mock)

运行:
    python -m pytest tests/unit/test_g7_alpha_factor_library_top_boost.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import utils.alpha_factor_library as lib_mod  # noqa: E402
from utils.alpha_factor_library import (  # noqa: E402
    DEFAULT_GTJA,
    DEFAULT_GTJA_30,
    AlphaFactorLibrary,
    FactorLibraryResult,
    FactorValue,
    calc_ic,
    compute_factor_corr_matrix,
    evaluate_factors,
    list_available_factors,
    neutralize_by_industry,
    neutralize_by_size,
    orthogonalize,
    standardize,
    winsorize,
)

# ============================================================
# 1. 导出符号可用性
# ============================================================


class TestExports:
    def test_alpha_factor_library_callable(self) -> None:
        assert callable(AlphaFactorLibrary)

    def test_preprocess_funcs_callable(self) -> None:
        assert callable(winsorize)
        assert callable(standardize)
        assert callable(neutralize_by_industry)
        assert callable(neutralize_by_size)
        assert callable(orthogonalize)
        assert callable(calc_ic)
        assert callable(evaluate_factors)
        assert callable(compute_factor_corr_matrix)

    def test_default_gtja_nonempty(self) -> None:
        assert isinstance(DEFAULT_GTJA, list)
        assert len(DEFAULT_GTJA) > 0

    def test_default_gtja_30_alias(self) -> None:
        # DEFAULT_GTJA_30 是 DEFAULT_GTJA 的向后兼容别名
        assert DEFAULT_GTJA_30 == DEFAULT_GTJA

    def test_list_available_factors_callable(self) -> None:
        result = list_available_factors()
        assert isinstance(result, list)


# ============================================================
# 2. _build_demo_data
# ============================================================


class TestBuildDemoData:
    def test_return_structure(self) -> None:
        price_data, fundamentals, industries, benchmark_returns = (
            lib_mod._build_demo_data(n_symbols=4, n_days=50)
        )
        assert len(price_data) == 4
        assert len(fundamentals) == 4
        assert len(industries) == 4
        assert len(benchmark_returns) == 50

    def test_price_data_fields(self) -> None:
        price_data, _, _, _ = lib_mod._build_demo_data(n_symbols=2, n_days=50)
        for _sym, data in price_data.items():
            assert "closes" in data
            assert "volumes" in data
            assert "highs" in data
            assert "lows" in data
            assert "opens" in data
            assert len(data["closes"]) == 50
            assert "tail_volume_ratio" in data
            assert "open_big_buy_ratio" in data

    def test_fundamentals_fields(self) -> None:
        _, fundamentals, _, _ = lib_mod._build_demo_data(n_symbols=2, n_days=50)
        for _sym, f in fundamentals.items():
            # 估值字段
            assert "pe" in f and "pb" in f
            # 规模
            assert "market_cap" in f
            # 成长
            assert "revenue_yoy" in f
            # 质量
            assert "roe" in f
            # 杠杆
            assert "debt_to_equity" in f
            # 营运
            assert "asset_turnover" in f
            # 预期
            assert "sue" in f

    def test_industries_pool(self) -> None:
        _, _, industries, _ = lib_mod._build_demo_data(n_symbols=8, n_days=50)
        valid_industries = {"Manufacturing", "Finance", "Tech", "Resource"}
        for _sym, ind in industries.items():
            assert ind in valid_industries

    def test_deterministic_with_seed(self) -> None:
        # seed=42 固定 → 两次调用结果一致
        r1 = lib_mod._build_demo_data(n_symbols=2, n_days=30)
        r2 = lib_mod._build_demo_data(n_symbols=2, n_days=30)
        assert r1[0].keys() == r2[0].keys()
        sym = next(iter(r1[0]))
        assert r1[0][sym]["closes"][0] == pytest.approx(r2[0][sym]["closes"][0])


# ============================================================
# 3. _print_summary
# ============================================================


class TestPrintSummary:
    def _make_result(
        self,
        with_corr_matrix: bool = False,
        n_factors_per_cat: int = 2,
    ) -> FactorLibraryResult:
        """构造测试用 FactorLibraryResult。"""
        factors: dict[str, FactorValue] = {}
        for cat in ["Value", "Momentum", "Quality"]:
            for i in range(n_factors_per_cat):
                name = f"{cat}_{i}"
                factors[name] = FactorValue(
                    name=name,
                    category=cat,
                    values={"A": 1.0, "B": 2.0},
                    ic_5d=0.04 + i * 0.01,
                )
        result = FactorLibraryResult(
            factors=factors,
            effective_factors=[n for n in factors if abs(factors[n].ic_5d) > 0.03],
            strong_factors=[n for n in factors if abs(factors[n].ic_5d) > 0.05],
        )
        if with_corr_matrix:
            names = list(factors.keys())
            n = len(names)
            # 构造高相关矩阵 (对角 1.0, off-diag 0.8)
            mat = np.full((n, n), 0.8)
            np.fill_diagonal(mat, 1.0)
            result.factor_corr_matrix = pd.DataFrame(mat, index=names, columns=names)
        return result

    def test_no_corr_matrix(self) -> None:
        result = self._make_result(with_corr_matrix=False)
        # 不抛异常即可 (logger.info 调用)
        lib_mod._print_summary(result)

    def test_with_corr_matrix_high_corr(self) -> None:
        result = self._make_result(with_corr_matrix=True)
        # 有高相关对 (0.8 > 0.7) → 走 high_corr_pairs 分支
        lib_mod._print_summary(result)

    def test_more_than_three_per_category(self) -> None:
        # 每类 5 个因子 → 走 "... (N more)" 分支
        result = self._make_result(with_corr_matrix=False, n_factors_per_cat=5)
        lib_mod._print_summary(result)

    def test_empty_result(self) -> None:
        result = FactorLibraryResult()
        lib_mod._print_summary(result)


# ============================================================
# 4. main (演示入口)
# ============================================================


class TestMain:
    def test_main_runs_full_pipeline(self) -> None:
        # 用小数据 mock _build_demo_data 加速, 验证 main 不抛异常
        small_data = lib_mod._build_demo_data(n_symbols=4, n_days=60)
        with patch.object(lib_mod, "_build_demo_data", return_value=small_data):
            lib_mod.main()

    def test_main_with_minimal_data(self) -> None:
        # 极小数据 (2 只标的 30 天)
        small_data = lib_mod._build_demo_data(n_symbols=2, n_days=30)
        with patch.object(lib_mod, "_build_demo_data", return_value=small_data):
            lib_mod.main()


# ============================================================
# 5. AlphaFactorLibrary 实例化 (导出类可用性)
# ============================================================


class TestAlphaFactorLibraryInstantiation:
    def test_default_init(self) -> None:
        lib = AlphaFactorLibrary()
        assert lib.neutralize_industry is False
        assert lib.enable_technical is True

    def test_custom_init(self) -> None:
        lib = AlphaFactorLibrary(
            neutralize_industry=True,
            neutralize_size=True,
            enable_technical=False,
            enable_expectation=False,
        )
        assert lib.neutralize_industry is True
        assert lib.neutralize_size is True
        assert lib.enable_technical is False
        assert lib.enable_expectation is False
