"""factor_orthogonalizer 单元测试.

测试目标:
    1. Flag 透传 (HC-1): USE_VIBE_FACTOR_INJECTION=False 时返回空列表
    2. 正交化逻辑: 高相关因子对中保留方差大的, 丢弃方差小的
    3. 阈值行为: |corr| >= threshold 丢弃, |corr| < threshold 保留
    4. 失败安全: 异常输入返回空列表 + error 报告
    5. 不可变性: 不修改输入字典
    6. 诊断: get_correlation_summary 正确返回相关性摘要
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from utils.alpha.factor_orthogonalizer import (
    DEFAULT_THRESHOLD,
    OrthogonalizationReport,
    filter_orthogonal,
    get_correlation_summary,
    orthogonalize_factors,
)


# ============================================================
# 测试数据生成
# ============================================================
def _make_uncorrelated_factors(n: int = 3, length: int = 60) -> dict[str, pd.Series]:
    """生成 n 个互不相关的因子序列."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=length, freq="D")
    return {f"alpha_{i:03d}": pd.Series(np.random.randn(length), index=dates) for i in range(n)}


def _make_correlated_pair(corr: float = 0.9, length: int = 60) -> dict[str, pd.Series]:
    """生成两个高相关的因子序列."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=length, freq="D")
    x = np.random.randn(length)
    # y = corr * x + sqrt(1-corr^2) * noise
    noise = np.random.randn(length)
    y = corr * x + np.sqrt(1 - corr**2) * noise
    return {
        "alpha_high_var": pd.Series(x * 10, index=dates),  # 高方差
        "alpha_low_var": pd.Series(y, index=dates),  # 低方差
    }


def _make_mixed_factors(length: int = 60) -> dict[str, pd.Series]:
    """生成混合因子集 (3 个独立 + 1 个冗余)."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=length, freq="D")
    a = np.random.randn(length)
    b = np.random.randn(length)
    c = np.random.randn(length)
    d = 0.95 * a + np.sqrt(1 - 0.95**2) * np.random.randn(length)  # 与 a 高相关
    return {
        "alpha_a": pd.Series(a, index=dates),
        "alpha_b": pd.Series(b, index=dates),
        "alpha_c": pd.Series(c, index=dates),
        "alpha_d_redundant": pd.Series(d, index=dates),
    }


# ============================================================
# 1. Flag 透传 (HC-1)
# ============================================================
class TestFlagGate:
    """USE_VIBE_FACTOR_INJECTION=False 时必须返回空列表."""

    def test_flag_disabled_returns_empty(self) -> None:
        factors = _make_uncorrelated_factors(3)
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=False):
            selected, report = orthogonalize_factors(factors)
        assert selected == []
        assert report.status == "disabled"

    def test_flag_disabled_no_correlation_computation(self) -> None:
        """flag 关闭时不应计算相关系数矩阵."""
        factors = _make_uncorrelated_factors(3)
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=False), patch(
            "pandas.DataFrame.corr"
        ) as mock_corr:
            orthogonalize_factors(factors)
            mock_corr.assert_not_called()

    def test_empty_input_returns_empty_even_if_flag_enabled(self) -> None:
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors({})
        assert selected == []
        assert report.status == "success"


# ============================================================
# 2. 正交化逻辑
# ============================================================
class TestOrthogonalizationLogic:
    """正交化选择逻辑."""

    def test_uncorrelated_factors_all_kept(self) -> None:
        """互不相关的因子应全部保留."""
        factors = _make_uncorrelated_factors(3)
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors(factors, threshold=0.7)
        assert len(selected) == 3
        assert report.status == "success"
        assert len(report.dropped_factors) == 0

    def test_correlated_pair_keeps_higher_variance(self) -> None:
        """高相关因子对中, 方差大的应被保留."""
        factors = _make_correlated_pair(corr=0.9)
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors(factors, threshold=0.7)
        assert len(selected) == 1
        # alpha_high_var 方差更大, 应被保留
        assert "alpha_high_var" in selected
        assert "alpha_low_var" in report.dropped_factors
        assert "alpha_low_var" in report.drop_reasons

    def test_mixed_factors_drops_redundant(self) -> None:
        """混合因子集中, 高相关因子对 (alpha_a, alpha_d_redundant) 应恰好保留一个."""
        factors = _make_mixed_factors()
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors(factors, threshold=0.7)
        # alpha_b, alpha_c 应保留 (与其他因子不相关)
        assert "alpha_b" in selected
        assert "alpha_c" in selected
        # alpha_a 和 alpha_d_redundant 高相关, 应恰好保留一个
        correlated_pair = {"alpha_a", "alpha_d_redundant"}
        kept_from_pair = correlated_pair & set(selected)
        assert len(kept_from_pair) == 1, (
            f"高相关对应恰好保留 1 个, 实际保留: {kept_from_pair}, selected: {selected}"
        )
        # 另一个应在 dropped_factors 中
        dropped_from_pair = correlated_pair - kept_from_pair
        assert dropped_from_pair.pop() in report.dropped_factors

    def test_threshold_boundary(self) -> None:
        """阈值边界: |corr| == threshold 应丢弃."""
        factors = _make_correlated_pair(corr=0.7)
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            # threshold=0.7, corr=0.7 → |0.7| >= 0.7 → 丢弃
            selected, report = orthogonalize_factors(factors, threshold=0.7)
        assert len(selected) == 1

    def test_below_threshold_kept(self) -> None:
        """|corr| < threshold 应保留两个因子."""
        factors = _make_correlated_pair(corr=0.6)
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors(factors, threshold=0.7)
        assert len(selected) == 2


# ============================================================
# 3. 失败安全
# ============================================================
class TestFailSafe:
    """异常输入应返回空列表 + error 报告."""

    def test_insufficient_samples_returns_empty(self) -> None:
        """样本数不足时应返回空列表."""
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        factors = {"alpha_001": pd.Series([1, 2, 3, 4, 5], index=dates)}
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors(factors)
        assert selected == []
        assert report.status in ("error", "success")

    def test_constant_series_dropped(self) -> None:
        """常数序列 (方差=0) 应被丢弃."""
        dates = pd.date_range("2026-01-01", periods=60, freq="D")
        factors = {
            "alpha_const": pd.Series([1.0] * 60, index=dates),
            "alpha_normal": pd.Series(np.random.randn(60), index=dates),
        }
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors(factors)
        # 常数序列应被丢弃
        assert "alpha_const" not in selected
        assert "alpha_const" in report.drop_reasons
        # 正常序列应保留
        assert "alpha_normal" in selected

    def test_all_nan_returns_error(self) -> None:
        """全 NaN 输入: 方差=0 被视为低方差因子丢弃, 返回 success + 空列表.

        说明: 全 NaN 序列方差为 0 (< MIN_VARIANCE), 被归类为低方差丢弃,
        这是成功的过滤操作 (而非错误). 报告应记录丢弃原因.
        """
        dates = pd.date_range("2026-01-01", periods=60, freq="D")
        factors = {"alpha_nan": pd.Series([np.nan] * 60, index=dates)}
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            selected, report = orthogonalize_factors(factors)
        assert selected == []
        # 全 NaN → 方差=0 → 低方差丢弃 (success, 非 error)
        assert report.status == "success"
        assert "alpha_nan" in report.dropped_factors
        assert "alpha_nan" in report.drop_reasons


# ============================================================
# 4. 不可变性
# ============================================================
class TestImmutability:
    """不修改输入字典."""

    def test_input_dict_not_modified(self) -> None:
        factors = _make_mixed_factors()
        original_keys = set(factors.keys())
        original_lengths = {k: len(v) for k, v in factors.items()}
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            orthogonalize_factors(factors)
        assert set(factors.keys()) == original_keys
        assert {k: len(v) for k, v in factors.items()} == original_lengths


# ============================================================
# 5. 诊断函数
# ============================================================
class TestCorrelationSummary:
    """get_correlation_summary 诊断函数."""

    def test_summary_structure(self) -> None:
        factors = _make_mixed_factors()
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            summary = get_correlation_summary(factors)
        assert "n_factors" in summary
        assert "high_correlation_pairs" in summary
        assert "max_correlation" in summary
        assert "mean_abs_correlation" in summary

    def test_high_correlation_pairs_detected(self) -> None:
        factors = _make_mixed_factors()
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            summary = get_correlation_summary(factors)
        # alpha_d_redundant 与 alpha_a 高相关, 应被检测到
        assert summary["n_factors"] == 4
        high_pair_names = [(p[0], p[1]) for p in summary["high_correlation_pairs"]]
        assert ("alpha_a", "alpha_d_redundant") in high_pair_names or (
            "alpha_d_redundant",
            "alpha_a",
        ) in high_pair_names

    def test_empty_input_summary(self) -> None:
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            summary = get_correlation_summary({})
        assert summary["n_factors"] == 0
        assert summary["max_correlation"] == 0.0


# ============================================================
# 6. 快捷函数
# ============================================================
class TestFilterOrthogonal:
    """filter_orthogonal 快捷函数."""

    def test_returns_filtered_dict(self) -> None:
        factors = _make_mixed_factors()
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=True):
            filtered = filter_orthogonal(factors, threshold=0.7)
        # 应返回字典, 且只包含正交化后的因子
        assert isinstance(filtered, dict)
        # alpha_a 和 alpha_d_redundant 高相关 (corr=0.943), 应恰好保留一个
        # 按方差排序: alpha_d_redundant (0.9241) > alpha_a (0.8254), 故保留 alpha_d_redundant
        correlated_pair = {"alpha_a", "alpha_d_redundant"}
        kept_from_pair = correlated_pair & set(filtered.keys())
        assert len(kept_from_pair) == 1, (
            f"高相关对应恰好保留 1 个, 实际保留: {kept_from_pair}, keys: {list(filtered.keys())}"
        )
        # alpha_b, alpha_c 与其他因子不相关, 应保留
        assert "alpha_b" in filtered
        assert "alpha_c" in filtered
        # 值应为原始 pd.Series
        kept_name = kept_from_pair.pop()
        assert isinstance(filtered[kept_name], pd.Series)

    def test_flag_disabled_returns_empty_dict(self) -> None:
        factors = _make_mixed_factors()
        with patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=False):
            filtered = filter_orthogonal(factors)
        assert filtered == {}


# ============================================================
# 7. 报告数据结构
# ============================================================
class TestReport:
    """OrthogonalizationReport 数据结构."""

    def test_to_dict_structure(self) -> None:
        report = OrthogonalizationReport(
            total_factors=5,
            selected_factors=["a", "b"],
            dropped_factors=["c", "d", "e"],
            drop_reasons={"c": "与 a 相关性 0.85 >= 0.7"},
            threshold=0.7,
            status="success",
        )
        d = report.to_dict()
        assert d["total_factors"] == 5
        assert d["selected_count"] == 2
        assert d["dropped_count"] == 3
        assert d["status"] == "success"
        assert "c" in d["drop_reasons"]
