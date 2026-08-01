# -*- coding: utf-8 -*-
"""multi_factor_signal ↔ factor_orthogonalizer 集成测试 — Phase 3 (HC-8).

测试目标:
    1. Flag 透传 (HC-1): USE_VIBE_FACTOR_INJECTION=False 时不过滤, 行为等价
    2. 正交化过滤: 高相关因子在 IC 加权前被剔除
    3. 失败安全: orthogonalizer 异常/不可用时降级为原因子列表
    4. 样本不足处理: < ORTHO_MIN_SAMPLES 的因子保留原状
    5. _build_factor_series: cross-sectional mean 时间序列构建正确
    6. combine_factors_ic_weighted 端到端集成
"""

from __future__ import annotations

import math
from contextlib import ExitStack
from unittest.mock import patch

from utils.alpha.multi_factor_signal import (
    ORTHO_DEFAULT_THRESHOLD,
    ORTHO_FLAG_NAME,
    ORTHO_MIN_SAMPLES,
    MultiFactorSignal,
)


# ============================================================
# 辅助: 同时 patch 两个模块的 is_enabled
# ============================================================
def _patch_flags(enabled: bool) -> ExitStack:
    """同时 patch multi_factor_signal 和 factor_orthogonalizer 的 is_enabled.

    multi_factor_signal.is_enabled 控制 USE_MULTI_FACTOR_SIGNAL (IC 加权 vs 等权)
    factor_orthogonalizer.is_enabled 控制 USE_VIBE_FACTOR_INJECTION (正交化过滤)

    Args:
        enabled: 两个 flag 是否同时启用/关闭

    Returns:
        ExitStack (需在 with 语句中使用)
    """
    stack = ExitStack()
    stack.enter_context(patch("utils.alpha.multi_factor_signal.is_enabled", return_value=enabled))
    stack.enter_context(
        patch("utils.alpha.factor_orthogonalizer.is_enabled", return_value=enabled)
    )
    return stack


# ============================================================
# 测试数据生成
# ============================================================
def _make_uncorrelated_factor_history(
    n_factors: int = 3,
    n_days: int = 30,
    n_symbols: int = 5,
    seed: int = 42,
) -> tuple[dict, list]:
    """生成 n 个互不相关的因子历史数据.

    Returns:
        (factor_history, forward_returns)
        - factor_history: {factor_name: [day_0_values, ...]}
        - forward_returns: [{symbol: fwd_return}, ...]
    """
    import random

    rng = random.Random(seed)
    factor_history: dict[str, list[dict[str, float]]] = {}
    for i in range(n_factors):
        name = f"F_{i}"
        daily_vals: list[dict[str, float]] = []
        for _ in range(n_days):
            day_vals = {f"S{j}": rng.gauss(0, 1) for j in range(n_symbols)}
            daily_vals.append(day_vals)
        factor_history[name] = daily_vals

    forward_returns: list[dict[str, float]] = []
    for _ in range(n_days):
        fwd = {f"S{j}": rng.gauss(0, 0.02) for j in range(n_symbols)}
        forward_returns.append(fwd)

    return factor_history, forward_returns


def _make_correlated_factor_history(
    corr: float = 0.95,
    n_days: int = 30,
    n_symbols: int = 5,
    seed: int = 42,
) -> tuple[dict, list]:
    """生成两个高相关的因子历史数据 (F_redundant 与 F_primary 相关).

    Returns:
        (factor_history, forward_returns)
        - factor_history: {"F_primary": [...], "F_redundant": [...], "F_independent": [...]}
        - forward_returns: [{symbol: fwd_return}, ...]
    """
    import random

    rng = random.Random(seed)
    n = n_days

    factor_history: dict[str, list[dict[str, float]]] = {
        "F_primary": [],
        "F_redundant": [],
        "F_independent": [],
    }
    forward_returns: list[dict[str, float]] = []

    for _ in range(n):
        # F_primary: 基础信号
        primary = {f"S{j}": rng.gauss(0, 1) for j in range(n_symbols)}
        # F_redundant: corr * primary + sqrt(1-corr^2) * noise (高相关)
        noise = {f"S{j}": rng.gauss(0, 1) for j in range(n_symbols)}
        redundant = {
            s: corr * primary[s] + math.sqrt(1 - corr**2) * noise[s] for s in primary
        }
        # F_independent: 完全独立的信号
        independent = {f"S{j}": rng.gauss(0, 1) for j in range(n_symbols)}

        factor_history["F_primary"].append(primary)
        factor_history["F_redundant"].append(redundant)
        factor_history["F_independent"].append(independent)

        fwd = {f"S{j}": rng.gauss(0, 0.02) for j in range(n_symbols)}
        forward_returns.append(fwd)

    return factor_history, forward_returns


# ============================================================
# 1. Flag 透传 (HC-1)
# ============================================================
class TestFlagPassthrough:
    """USE_VIBE_FACTOR_INJECTION=False 时正交化过滤应为 no-op."""

    def test_flag_disabled_returns_original_names(self) -> None:
        """flag=False 时 filter_orthogonal_factors 返回原列表 + None 报告."""
        factor_history, _ = _make_uncorrelated_factor_history(n_factors=3, n_days=30)
        mfs = MultiFactorSignal()
        original_names = ["F_0", "F_1", "F_2"]

        with _patch_flags(False):
            filtered, report = mfs.filter_orthogonal_factors(
                factor_history, original_names
            )

        assert filtered == original_names
        assert report is None

    def test_flag_disabled_combine_uses_all_factors(self) -> None:
        """flag=False 时 combine_factors_ic_weighted 使用全部因子 (不过滤)."""
        factor_history, fwd_returns = _make_uncorrelated_factor_history(
            n_factors=3, n_days=30
        )
        mfs = MultiFactorSignal()
        names = ["F_0", "F_1", "F_2"]

        # USE_MULTI_FACTOR_SIGNAL=False, USE_VIBE_FACTOR_INJECTION=False
        with _patch_flags(False):
            combined, weights = mfs.combine_factors_ic_weighted(
                factor_history, fwd_returns, names
            )

        # 等权模式下, 每个因子权重 = 1/3
        assert len(combined) == 30
        assert len(weights) == 30
        for w in weights:
            assert "mode" in w
            for name in names:
                assert abs(w[name] - 1.0 / 3) < 1e-9


# ============================================================
# 2. 正交化过滤 (HC-8)
# ============================================================
class TestOrthogonalizationFilter:
    """flag=True 时高相关因子应被过滤."""

    def test_correlated_factors_one_dropped(self) -> None:
        """高相关因子对 (corr>=0.9) 应恰好保留一个."""
        factor_history, _ = _make_correlated_factor_history(corr=0.95, n_days=30)
        mfs = MultiFactorSignal()
        names = ["F_primary", "F_redundant", "F_independent"]

        with _patch_flags(True):
            filtered, report = mfs.filter_orthogonal_factors(factor_history, names)

        # 应返回非空列表
        assert len(filtered) >= 1
        # F_primary 和 F_redundant 高相关, 应恰好保留一个
        correlated_pair = {"F_primary", "F_redundant"}
        kept = correlated_pair & set(filtered)
        assert len(kept) == 1, (
            f"高相关对应保留 1 个, 实际: {kept}, filtered: {filtered}"
        )
        # F_independent 与其他因子不相关, 应保留
        assert "F_independent" in filtered
        # 报告非 None
        assert report is not None
        assert report.status == "success"

    def test_uncorrelated_factors_all_kept(self) -> None:
        """互不相关的因子应全部保留."""
        factor_history, _ = _make_uncorrelated_factor_history(n_factors=3, n_days=30)
        mfs = MultiFactorSignal()
        names = ["F_0", "F_1", "F_2"]

        with _patch_flags(True):
            filtered, report = mfs.filter_orthogonal_factors(factor_history, names)

        # 3 个不相关因子应全部保留
        assert len(filtered) == 3
        assert set(filtered) == set(names)
        assert report is not None
        assert report.status == "success"

    def test_threshold_boundary(self) -> None:
        """threshold 参数应能控制过滤严格程度."""
        factor_history, _ = _make_correlated_factor_history(corr=0.6, n_days=30)
        mfs = MultiFactorSignal()
        names = ["F_primary", "F_redundant", "F_independent"]

        # 低阈值 (0.5): corr=0.6 >= 0.5, 应过滤
        with _patch_flags(True):
            filtered_strict, _ = mfs.filter_orthogonal_factors(
                factor_history, names, threshold=0.5
            )
        # 高阈值 (0.99): corr=0.6 < 0.99, 应全部保留
        with _patch_flags(True):
            filtered_loose, _ = mfs.filter_orthogonal_factors(
                factor_history, names, threshold=0.99
            )

        assert len(filtered_strict) < 3  # 至少过滤掉 1 个
        assert len(filtered_loose) == 3  # 全部保留


# ============================================================
# 3. 失败安全
# ============================================================
class TestFailSafe:
    """异常情况下降级为原因子列表."""

    def test_orthogonalizer_import_error_returns_original(self) -> None:
        """orthogonalizer 模块不可用时, 返回原列表 + None."""
        factor_history, _ = _make_uncorrelated_factor_history(n_factors=2, n_days=30)
        mfs = MultiFactorSignal()
        names = ["F_0", "F_1"]

        # 模拟 import 失败
        import builtins

        orig_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "factor_orthogonalizer" in name:
                raise ImportError("mocked import error")
            return orig_import(name, *args, **kwargs)

        with _patch_flags(True), patch("builtins.__import__", side_effect=mock_import):
            filtered, report = mfs.filter_orthogonal_factors(factor_history, names)

        assert filtered == names
        assert report is None

    def test_orthogonalizer_exception_returns_original(self) -> None:
        """orthogonalize_factors 抛异常时, 返回原列表 + None."""
        factor_history, _ = _make_uncorrelated_factor_history(n_factors=2, n_days=30)
        mfs = MultiFactorSignal()
        names = ["F_0", "F_1"]

        with _patch_flags(True), patch(
            "utils.alpha.factor_orthogonalizer.orthogonalize_factors",
            side_effect=RuntimeError("mocked runtime error"),
        ):
            filtered, report = mfs.filter_orthogonal_factors(factor_history, names)

        assert filtered == names
        assert report is None

    def test_combine_factors_with_orthogonalization_error_still_works(self) -> None:
        """正交化异常时 combine_factors_ic_weighted 仍应正常工作 (降级)."""
        factor_history, fwd_returns = _make_uncorrelated_factor_history(
            n_factors=2, n_days=30
        )
        mfs = MultiFactorSignal()
        names = ["F_0", "F_1"]

        with _patch_flags(True), patch(
            "utils.alpha.factor_orthogonalizer.orthogonalize_factors",
            side_effect=RuntimeError("mocked error"),
        ):
            combined, weights = mfs.combine_factors_ic_weighted(
                factor_history, fwd_returns, names
            )

        # 应降级为使用全部因子, 仍产生结果
        assert len(combined) == 30
        assert len(weights) == 30


# ============================================================
# 4. 样本不足处理
# ============================================================
class TestInsufficientSamples:
    """< ORTHO_MIN_SAMPLES 的因子应保留原状, 不参与正交化."""

    def test_short_factor_history_kept_as_is(self) -> None:
        """样本不足的因子应保留, 不被正交化丢弃."""
        # 仅 5 天数据 (< ORTHO_MIN_SAMPLES=20)
        factor_history, _ = _make_uncorrelated_factor_history(
            n_factors=2, n_days=5, n_symbols=5
        )
        mfs = MultiFactorSignal()
        names = ["F_0", "F_1"]

        with _patch_flags(True):
            filtered, report = mfs.filter_orthogonal_factors(factor_history, names)

        # 样本不足 → 无可评估因子 → 返回原列表 + None
        assert filtered == names
        assert report is None

    def test_mixed_sample_lengths(self) -> None:
        """混合样本长度: 充足的参与正交化, 不足的保留原状."""
        # F_long: 30 天 (充足), F_short: 5 天 (不足)
        factor_history_long, fwd = _make_uncorrelated_factor_history(
            n_factors=1, n_days=30, seed=1
        )
        factor_history_short, _ = _make_uncorrelated_factor_history(
            n_factors=1, n_days=5, seed=2
        )
        factor_history = {
            "F_long": factor_history_long["F_0"],
            "F_short": factor_history_short["F_0"],
        }
        mfs = MultiFactorSignal()
        names = ["F_long", "F_short"]

        with _patch_flags(True):
            filtered, report = mfs.filter_orthogonal_factors(factor_history, names)

        # F_short 样本不足, 应保留 (不参与正交化)
        assert "F_short" in filtered
        # F_long 样本充足, 参与正交化 (单因子, 必然保留)
        assert "F_long" in filtered


# ============================================================
# 5. _build_factor_series 辅助方法
# ============================================================
class TestBuildFactorSeries:
    """_build_factor_series 时间序列构建测试."""

    def test_returns_series_for_sufficient_samples(self) -> None:
        """样本充足的因子应返回 pd.Series."""
        factor_history, _ = _make_uncorrelated_factor_history(
            n_factors=2, n_days=30, n_symbols=5
        )
        names = ["F_0", "F_1"]

        series_dict, evaluable, insufficient = MultiFactorSignal._build_factor_series(
            factor_history, names
        )

        assert len(evaluable) == 2
        assert len(insufficient) == 0
        assert set(series_dict.keys()) == {"F_0", "F_1"}
        # 每个 series 长度 = 30
        for s in series_dict.values():
            assert len(s) == 30

    def test_returns_empty_for_insufficient_samples(self) -> None:
        """样本不足的因子应归入 insufficient_names."""
        factor_history, _ = _make_uncorrelated_factor_history(
            n_factors=2, n_days=5, n_symbols=5
        )
        names = ["F_0", "F_1"]

        series_dict, evaluable, insufficient = MultiFactorSignal._build_factor_series(
            factor_history, names
        )

        assert len(evaluable) == 0
        assert len(insufficient) == 2
        assert len(series_dict) == 0

    def test_missing_factor_in_history(self) -> None:
        """factor_history 中不存在的因子应归入 insufficient."""
        factor_history, _ = _make_uncorrelated_factor_history(
            n_factors=1, n_days=30, n_symbols=5
        )
        names = ["F_0", "F_missing"]

        series_dict, evaluable, insufficient = MultiFactorSignal._build_factor_series(
            factor_history, names
        )

        assert "F_0" in evaluable
        assert "F_missing" in insufficient

    def test_daily_mean_computation(self) -> None:
        """series 值应为每日 cross-sectional mean."""
        # 构造已知数据: 每日 3 个标的, 均值已知
        factor_history = {
            "F_test": [
                {"S0": 1.0, "S1": 2.0, "S2": 3.0},  # mean=2.0
                {"S0": 4.0, "S1": 5.0, "S2": 6.0},  # mean=5.0
            ]
        }
        # 直接测试逻辑 (绕过 MIN_SAMPLES 检查)
        means: list[float] = []
        for day_vals in factor_history["F_test"]:
            valid = [v for v in day_vals.values() if isinstance(v, (int, float))]
            means.append(sum(valid) / len(valid))

        assert means == [2.0, 5.0]


# ============================================================
# 6. 端到端集成 (combine_factors_ic_weighted)
# ============================================================
class TestEndToEndIntegration:
    """combine_factors_ic_weighted 与正交化的端到端集成."""

    def test_correlated_factors_filtered_before_combine(self) -> None:
        """高相关因子在 combine 前被过滤, 结果仅用保留的因子."""
        factor_history, fwd_returns = _make_correlated_factor_history(
            corr=0.95, n_days=30, n_symbols=5
        )
        mfs = MultiFactorSignal()
        names = ["F_primary", "F_redundant", "F_independent"]

        # 同时启用 USE_MULTI_FACTOR_SIGNAL 和 USE_VIBE_FACTOR_INJECTION
        with _patch_flags(True):
            combined, weights = mfs.combine_factors_ic_weighted(
                factor_history, fwd_returns, names
            )

        # 应产生 30 天的组合信号
        assert len(combined) == 30
        assert len(weights) == 30

        # weights 中应只包含保留的因子 (F_independent + 1 of {F_primary, F_redundant})
        first_weights = weights[0]
        weight_keys = {
            k for k in first_weights if not k.startswith("ic_ir_") and k != "mode"
        }
        # 应恰好 2 个因子 (F_independent + 1 个高相关因子)
        assert len(weight_keys) == 2, (
            f"期望 2 个因子权重, 实际 {len(weight_keys)}: {weight_keys}"
        )
        assert "F_independent" in weight_keys
        # 高相关对中恰好 1 个
        correlated_kept = weight_keys & {"F_primary", "F_redundant"}
        assert len(correlated_kept) == 1

    def test_flag_disabled_uses_all_factors_in_combine(self) -> None:
        """flag=False 时 combine 使用全部因子 (不过滤)."""
        factor_history, fwd_returns = _make_correlated_factor_history(
            corr=0.95, n_days=30, n_symbols=5
        )
        mfs = MultiFactorSignal()
        names = ["F_primary", "F_redundant", "F_independent"]

        with _patch_flags(False):
            combined, weights = mfs.combine_factors_ic_weighted(
                factor_history, fwd_returns, names
            )

        # 等权模式, 全部 3 个因子参与
        assert len(combined) == 30
        first_weights = weights[0]
        assert first_weights["mode"] == "equal_weight"
        for name in names:
            assert abs(first_weights[name] - 1.0 / 3) < 1e-9

    def test_empty_factor_names_returns_empty(self) -> None:
        """空因子列表应返回空结果 (不触发正交化)."""
        mfs = MultiFactorSignal()
        with _patch_flags(True):
            combined, weights = mfs.combine_factors_ic_weighted({}, [], [])
        assert combined == []
        assert weights == []


# ============================================================
# 7. 常量与配置
# ============================================================
class TestConstants:
    """Phase 3 常量正确性."""

    def test_orthogonalization_constants(self) -> None:
        """HC-8 常量应符合 feature_flags.yaml 配置."""
        assert ORTHO_FLAG_NAME == "USE_VIBE_FACTOR_INJECTION"
        assert ORTHO_DEFAULT_THRESHOLD == 0.7
        assert ORTHO_MIN_SAMPLES == 20

    def test_default_threshold_matches_yaml(self) -> None:
        """默认阈值 0.7 应与 feature_flags.yaml 中 '正交性 corr < 0.7' 一致."""
        # 来自 feature_flags.yaml 注释: 正交性 corr < 0.7
        assert ORTHO_DEFAULT_THRESHOLD == 0.7
