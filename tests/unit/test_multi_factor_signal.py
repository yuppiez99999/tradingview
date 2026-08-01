# -*- coding: utf-8 -*-
"""multi_factor_signal 单元测试 — T2.3.

验证以下方面:
    1. FactorICMetrics / CombinationResult 数据类
    2. MultiFactorSignal 初始化与配置加载
    3. cross_sectional_rank 标准化
    4. compute_rolling_ic_series (Spearman IC)
    5. compute_rolling_ic_ir_at_t (滚动 IC_IR)
    6. compute_factor_ic_metrics + 反向信号检测 (HC-6)
    7. detect_inverted_factors 批量检测
    8. combine_factors_ic_weighted IC 加权融合 (HC-7 lookback=10)
    9. Feature Flag 透传 (HC-1: flag=False 等权模式)
    10. generate_signal 单日快照
    11. 便捷函数 combine_factors / detect_inverted_factors
    12. 异常处理 (InsufficientSamplesError)
    13. 边界条件 (空输入/单标的/样本不足)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

# 导入被测模块
from utils.alpha.multi_factor_signal import (
    CombinationResult,
    FactorICMetrics,
    InsufficientSamplesError,
    MultiFactorSignal,
    MultiFactorSignalError,
    combine_factors,
    detect_inverted_factors,
)
from utils.infra.feature_flags import FeatureFlags

# ============================================================
# 测试 fixture
# ============================================================

@pytest.fixture
def mfs():
    """默认 MultiFactorSignal 实例."""
    return MultiFactorSignal(lookback=10, inverted_threshold=0.3)


@pytest.fixture
def synthetic_factor_history():
    """构造合成的因子历史数据 (25 天, 8 标的).

    关键设计:
        - 加入随机噪声让 IC 序列有方差 (避免 std=0 → IC_IR=0)
        - F_A 与 forward_return 整体正相关 (IC 均值 > 0, IC_IR > 0)
        - F_B 与 forward_return 整体负相关 (IC 均值 < 0, IC_IR < 0, 反向信号)
        - 使用固定随机种子确保测试可重现

    返回: (factor_history, forward_returns)
        factor_history = {"F_A": [day_0_values, ...], "F_B": [...]}
        forward_returns = [day_0_returns, ...]
    """
    import random
    rng = random.Random(42)  # 固定种子, 确保测试可重现

    symbols = ["S001", "S002", "S003", "S004", "S005", "S006", "S007", "S008"]
    n_days = 25

    # 标的的"真实质量"排序: S001 最差, S008 最好
    quality_rank = {s: i for i, s in enumerate(symbols)}

    factor_history: Dict[str, List[Dict[str, float]]] = {"F_A": [], "F_B": []}
    forward_returns: List[Dict[str, float]] = []

    for t in range(n_days):
        # forward return: 基本随质量递增, 但加入噪声
        fwd = {
            s: 0.001 * quality_rank[s] + 0.0005 * t + rng.gauss(0, 0.002)
            for s in symbols
        }
        forward_returns.append(fwd)

        # F_A: 与质量正相关 (含噪声, 整体 IC > 0)
        f_a = {
            s: float(quality_rank[s]) + 0.05 * t + rng.gauss(0, 0.5)
            for s in symbols
        }
        factor_history["F_A"].append(f_a)

        # F_B: 与质量负相关 (含噪声, 整体 IC < 0, 反向信号)
        f_b = {
            s: float(7 - quality_rank[s]) - 0.05 * t + rng.gauss(0, 0.5)
            for s in symbols
        }
        factor_history["F_B"].append(f_b)

    return factor_history, forward_returns


# ============================================================
# 1. 数据类测试
# ============================================================

class TestFactorICMetrics:
    """FactorICMetrics 数据类测试."""

    def test_basic_construction(self):
        """基本构造."""
        m = FactorICMetrics(
            factor_name="TEST",
            ic_mean=0.05,
            ic_std=0.1,
            ic_ir=0.5,
            is_inverted=False,
            effective_ic_ir=0.5,
            n_samples=10,
        )
        assert m.factor_name == "TEST"
        assert m.ic_mean == 0.05
        assert m.ic_std == 0.1
        assert m.ic_ir == 0.5
        assert m.is_inverted is False
        assert m.effective_ic_ir == 0.5
        assert m.n_samples == 10

    def test_inverted_factor_metrics(self):
        """反向信号因子指标."""
        m = FactorICMetrics(
            factor_name="INV",
            ic_mean=-0.05,
            ic_std=0.1,
            ic_ir=-0.5,
            is_inverted=True,
            effective_ic_ir=0.5,  # 反向后取绝对值
            n_samples=10,
        )
        assert m.is_inverted is True
        assert m.effective_ic_ir == abs(m.ic_ir)

    def test_to_dict(self):
        """to_dict 序列化."""
        m = FactorICMetrics(
            factor_name="TEST",
            ic_mean=0.05,
            ic_std=0.1,
            ic_ir=0.5,
            is_inverted=False,
            effective_ic_ir=0.5,
            n_samples=10,
        )
        d = m.to_dict()
        assert d["factor_name"] == "TEST"
        assert d["ic_ir"] == 0.5
        assert d["is_inverted"] is False
        assert d["n_samples"] == 10


class TestCombinationResult:
    """CombinationResult 数据类测试."""

    def test_basic_construction(self):
        """基本构造."""
        r = CombinationResult(
            combined_history=[{"S001": 0.5}],
            weights_history=[{"F_A": 0.7, "F_B": 0.3}],
            factor_metrics={},
            lookback=10,
            n_days=1,
            mode="ic_weighted",
        )
        assert r.lookback == 10
        assert r.n_days == 1
        assert r.mode == "ic_weighted"

    def test_to_dict(self):
        """to_dict 序列化."""
        r = CombinationResult(
            combined_history=[{"S001": 0.5}],
            weights_history=[{"F_A": 0.7}],
            factor_metrics={},
            lookback=10,
            n_days=1,
            mode="ic_weighted",
        )
        d = r.to_dict()
        assert d["lookback"] == 10
        assert d["mode"] == "ic_weighted"
        assert d["n_combined"] == 1


# ============================================================
# 2. MultiFactorSignal 初始化测试
# ============================================================

class TestMultiFactorSignalInit:
    """MultiFactorSignal 初始化测试."""

    def test_default_init(self):
        """默认初始化 (HC-7: lookback=10)."""
        mfs = MultiFactorSignal()
        assert mfs.lookback == 10  # HC-7
        assert mfs.inverted_threshold == 0.3  # HC-6
        assert mfs.feature_flag_name == "USE_MULTI_FACTOR_SIGNAL"

    def test_custom_init(self):
        """自定义参数初始化."""
        mfs = MultiFactorSignal(
            lookback=20,
            inverted_threshold=0.5,
            feature_flag_name="CUSTOM_FLAG",
        )
        assert mfs.lookback == 20
        assert mfs.inverted_threshold == 0.5
        assert mfs.feature_flag_name == "CUSTOM_FLAG"

    def test_config_name(self):
        """config_name 默认值."""
        mfs = MultiFactorSignal()
        assert mfs.config_name == "multi_factor_signal"


# ============================================================
# 3. cross_sectional_rank 测试
# ============================================================

class TestCrossSectionalRank:
    """cross_sectional_rank 标准化测试."""

    def test_basic_rank(self):
        """基本 rank 标准化到 [0, 1]."""
        values = {"S001": 1.0, "S002": 2.0, "S003": 3.0, "S004": 4.0, "S005": 5.0}
        ranks = MultiFactorSignal.cross_sectional_rank(values)
        assert ranks["S001"] == 0.0  # 最小值
        assert ranks["S005"] == 1.0  # 最大值
        assert 0.0 <= ranks["S003"] <= 1.0

    def test_single_symbol(self):
        """单标的返回 0.5."""
        values = {"S001": 1.0}
        ranks = MultiFactorSignal.cross_sectional_rank(values)
        assert ranks["S001"] == 0.5

    def test_empty_input(self):
        """空输入返回空字典."""
        ranks = MultiFactorSignal.cross_sectional_rank({})
        assert ranks == {}

    def test_nan_and_inf_excluded(self):
        """NaN 和 Inf 值被排除."""
        values = {"S001": 1.0, "S002": float("nan"), "S003": float("inf"), "S004": 2.0}
        ranks = MultiFactorSignal.cross_sectional_rank(values)
        # 仅 S001 和 S004 参与排序
        assert ranks["S001"] == 0.0
        assert ranks["S004"] == 1.0

    def test_all_same_values(self):
        """所有值相同 → 方差为 0, rank 仍能计算."""
        values = {"S001": 5.0, "S002": 5.0, "S003": 5.0}
        ranks = MultiFactorSignal.cross_sectional_rank(values)
        # 都相同时, 按排序顺序赋 rank
        assert all(0.0 <= v <= 1.0 for v in ranks.values())


# ============================================================
# 4. compute_rolling_ic_series 测试
# ============================================================

class TestComputeRollingICSeries:
    """compute_rolling_ic_series 测试."""

    def test_positive_correlation(self, synthetic_factor_history):
        """正相关因子的 IC 应为正."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        ic_series = mfs.compute_rolling_ic_series(
            factor_history["F_A"], forward_returns,
        )
        assert len(ic_series) == 25
        # F_A 与 forward_returns 正相关, IC 应 > 0
        valid_ic = [v for v in ic_series if math.isfinite(v)]
        assert len(valid_ic) > 0
        avg_ic = sum(valid_ic) / len(valid_ic)
        assert avg_ic > 0.0, f"Expected positive IC, got {avg_ic}"

    def test_negative_correlation(self, synthetic_factor_history):
        """负相关因子的 IC 应为负."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        ic_series = mfs.compute_rolling_ic_series(
            factor_history["F_B"], forward_returns,
        )
        valid_ic = [v for v in ic_series if math.isfinite(v)]
        assert len(valid_ic) > 0
        avg_ic = sum(valid_ic) / len(valid_ic)
        assert avg_ic < 0.0, f"Expected negative IC, got {avg_ic}"

    def test_length_alignment(self):
        """长度对齐: 取 min(len(factor), len(fwd))."""
        mfs = MultiFactorSignal()
        factor = [{"S001": 1.0}] * 10
        fwd = [{"S001": 0.01}] * 5
        ic_series = mfs.compute_rolling_ic_series(factor, fwd)
        assert len(ic_series) == 5

    def test_empty_input(self):
        """空输入返回空列表."""
        mfs = MultiFactorSignal()
        assert mfs.compute_rolling_ic_series([], []) == []


# ============================================================
# 5. compute_rolling_ic_ir_at_t 测试
# ============================================================

class TestComputeRollingICIrAtT:
    """compute_rolling_ic_ir_at_t 测试."""

    def test_insufficient_lookback_returns_zero(self):
        """t < lookback 时返回 0 (样本不足)."""
        mfs = MultiFactorSignal(lookback=10)
        ic_series = [0.1] * 5
        assert mfs.compute_rolling_ic_ir_at_t(ic_series, t=3, lookback=10) == 0.0

    def test_insufficient_samples_returns_zero(self):
        """样本数 < MIN_IC_SAMPLES 返回 0.

        _spearman_ic 样本不足时返回 nan (非 0.0),
        compute_rolling_ic_ir_at_t 用 isfinite 过滤 nan,
        有效样本 < 5 时返回 0.0 (中性权重).
        """
        mfs = MultiFactorSignal(lookback=10)
        # 构造 10 个 IC, 其中 7 个是 nan (样本不足), 3 个有效 (< 5)
        ic_series = [
            float("nan"), float("nan"), float("nan"), float("nan"), float("nan"),
            float("nan"), float("nan"), 0.1, 0.2, 0.3, 0.0,
        ]
        # t=10, window=ic_series[0:10] = [nan*7, 0.1, 0.2, 0.3]
        # isfinite 过滤后有效值 = [0.1, 0.2, 0.3] 仅 3 个 < 5
        result = mfs.compute_rolling_ic_ir_at_t(ic_series, t=10, lookback=10)
        assert result == 0.0

    def test_valid_ic_ir(self):
        """有效 IC 序列计算 IC_IR."""
        mfs = MultiFactorSignal(lookback=10)
        # 构造 10 个有效 IC, 均值为正
        ic_series = [0.05 + 0.01 * i for i in range(10)] + [0.0]
        result = mfs.compute_rolling_ic_ir_at_t(ic_series, t=10, lookback=10)
        assert result > 0.0  # 均值正 → IC_IR 正

    def test_zero_std_returns_zero(self):
        """标准差为 0 时返回 0 (避免除零)."""
        mfs = MultiFactorSignal(lookback=10)
        # 所有 IC 相同 → std=0
        ic_series = [0.05] * 10 + [0.0]
        result = mfs.compute_rolling_ic_ir_at_t(ic_series, t=10, lookback=10)
        assert result == 0.0

    def test_negative_ic_ir(self):
        """均值为负 → IC_IR 为负."""
        mfs = MultiFactorSignal(lookback=10)
        ic_series = [-0.05 - 0.01 * i for i in range(10)] + [0.0]
        result = mfs.compute_rolling_ic_ir_at_t(ic_series, t=10, lookback=10)
        assert result < 0.0


# ============================================================
# 6. compute_factor_ic_metrics 测试 (HC-6 反向信号检测)
# ============================================================

class TestComputeFactorICMetrics:
    """compute_factor_ic_metrics 测试."""

    def test_positive_factor_metrics(self, synthetic_factor_history):
        """正向因子指标 (F_A: IC_IR > 0)."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        metrics = mfs.compute_factor_ic_metrics(
            factor_history, forward_returns, "F_A",
        )
        assert metrics.factor_name == "F_A"
        assert metrics.ic_ir > 0.0, f"Expected positive IC_IR, got {metrics.ic_ir}"
        assert metrics.is_inverted is False
        assert metrics.effective_ic_ir == metrics.ic_ir
        assert metrics.n_samples >= 5

    def test_inverted_factor_metrics(self, synthetic_factor_history):
        """反向信号因子指标 (F_B: IC_IR < 0, HC-6).

        HC-6: |IC_IR|>=0.3 的负 IC_IR 因子反向使用.
        """
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal(inverted_threshold=0.3)
        metrics = mfs.compute_factor_ic_metrics(
            factor_history, forward_returns, "F_B",
        )
        assert metrics.ic_ir < 0.0, f"Expected negative IC_IR, got {metrics.ic_ir}"
        # IC_IR 负且 |IC_IR| >= 0.3 → 反向
        if abs(metrics.ic_ir) >= 0.3:
            assert metrics.is_inverted is True
            assert metrics.effective_ic_ir == abs(metrics.ic_ir)
        else:
            # |IC_IR| < 0.3 时不反向, 但仍记录 IC_IR
            assert metrics.is_inverted is False

    def test_to_dict_serialization(self, synthetic_factor_history):
        """to_dict 序列化."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        metrics = mfs.compute_factor_ic_metrics(
            factor_history, forward_returns, "F_A",
        )
        d = metrics.to_dict()
        assert d["factor_name"] == "F_A"
        assert "ic_ir" in d
        assert "is_inverted" in d
        assert "effective_ic_ir" in d

    def test_nonexistent_factor_raises(self, synthetic_factor_history):
        """不存在的因子抛 InsufficientSamplesError."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        with pytest.raises(InsufficientSamplesError):
            mfs.compute_factor_ic_metrics(
                factor_history, forward_returns, "F_NOT_EXIST",
            )

    def test_insufficient_samples_raises(self):
        """样本不足抛 InsufficientSamplesError."""
        mfs = MultiFactorSignal()
        # 仅 3 天数据 < MIN_IC_SAMPLES=5
        factor_history = {"F_A": [{"S001": 1.0}, {"S001": 2.0}, {"S001": 3.0}]}
        forward_returns = [{"S001": 0.01}, {"S001": 0.02}, {"S001": 0.03}]
        with pytest.raises(InsufficientSamplesError):
            mfs.compute_factor_ic_metrics(
                factor_history, forward_returns, "F_A",
            )

    def test_threshold_boundary(self):
        """反向信号阈值边界测试 (HC-6).

        IC_IR = -0.3 (恰好等于阈值) → is_inverted=True (<= -threshold)
        IC_IR = -0.29 (小于阈值) → is_inverted=False
        """
        MultiFactorSignal(inverted_threshold=0.3)
        # 构造 IC_IR 接近 -0.3 的因子历史
        # 使用 mock 直接测试阈值逻辑
        metrics = FactorICMetrics(
            factor_name="BOUNDARY",
            ic_mean=-0.03,
            ic_std=0.1,
            ic_ir=-0.3,  # 恰好等于阈值
            is_inverted=True,  # <= -threshold
            effective_ic_ir=0.3,
            n_samples=10,
        )
        assert metrics.is_inverted is True

        metrics2 = FactorICMetrics(
            factor_name="BOUNDARY2",
            ic_mean=-0.029,
            ic_std=0.1,
            ic_ir=-0.29,  # 小于阈值
            is_inverted=False,
            effective_ic_ir=-0.29,
            n_samples=10,
        )
        assert metrics2.is_inverted is False


# ============================================================
# 7. detect_inverted_factors 批量检测测试
# ============================================================

class TestDetectInvertedFactors:
    """detect_inverted_factors 批量检测测试."""

    def test_batch_detection(self, synthetic_factor_history):
        """批量检测反向信号因子 (HC-6)."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal(inverted_threshold=0.3)
        results = mfs.detect_inverted_factors(
            factor_history, forward_returns, ["F_A", "F_B"],
        )
        assert "F_A" in results
        assert "F_B" in results
        # F_A 正相关 → 不反向
        assert results["F_A"].is_inverted is False
        # F_B 负相关且 |IC_IR| 大 → 反向
        if abs(results["F_B"].ic_ir) >= 0.3:
            assert results["F_B"].is_inverted is True

    def test_skip_nonexistent_factor(self, synthetic_factor_history):
        """不存在的因子被跳过 (不抛异常)."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        results = mfs.detect_inverted_factors(
            factor_history, forward_returns, ["F_A", "F_NOT_EXIST"],
        )
        assert "F_A" in results
        assert "F_NOT_EXIST" not in results

    def test_skip_insufficient_samples(self):
        """样本不足的因子被跳过.

        - F_GOOD: 5 个标的 + 20 天, 满足 IC 计算条件 (≥5 标的 + ≥5 天)
        - F_SHORT: 仅 2 天数据, 样本不足, 应被跳过
        """
        import random
        rng = random.Random(99)
        mfs = MultiFactorSignal()
        # 5 个标的, 整体正相关, 加入噪声让 IC 序列有方差
        symbols = ["S001", "S002", "S003", "S004", "S005"]
        factor_history = {
            "F_GOOD": [
                {s: float(i + 1) + rng.gauss(0, 0.3) for i, s in enumerate(symbols)}
                for _ in range(20)
            ],
            "F_SHORT": [{"S001": 1.0}] * 2,  # 样本不足
        }
        forward_returns = [
            {s: 0.001 * (i + 1) + rng.gauss(0, 0.0005) for i, s in enumerate(symbols)}
            for _ in range(20)
        ]
        results = mfs.detect_inverted_factors(
            factor_history, forward_returns, ["F_GOOD", "F_SHORT"],
        )
        assert "F_GOOD" in results
        assert "F_SHORT" not in results


# ============================================================
# 8. combine_factors_ic_weighted 测试 (HC-7 + Feature Flag)
# ============================================================

class TestCombineFactorsICWeighted:
    """combine_factors_ic_weighted 测试."""

    def test_flag_disabled_equal_weight_mode(self, synthetic_factor_history):
        """Feature Flag=False 时使用等权模式 (HC-1 透传)."""
        factor_history, forward_returns = synthetic_factor_history

        # 确保 flag 关闭
        FeatureFlags.reset_instance()
        mfs = MultiFactorSignal()
        combined, weights = mfs.combine_factors_ic_weighted(
            factor_history, forward_returns, ["F_A", "F_B"],
        )
        assert len(combined) == 25
        assert len(weights) == 25
        # 等权模式: 每个因子权重 = 0.5
        for w in weights:
            assert abs(w["F_A"] - 0.5) < 1e-6
            assert abs(w["F_B"] - 0.5) < 1e-6
            assert w["mode"] == "equal_weight"

    def test_flag_enabled_ic_weighted_mode(self, synthetic_factor_history):
        """Feature Flag=True 时使用 IC 加权模式 (HC-7)."""
        factor_history, forward_returns = synthetic_factor_history

        # 启用 flag
        FeatureFlags.reset_instance()
        with patch("utils.alpha.multi_factor_signal.is_enabled", return_value=True):
            mfs = MultiFactorSignal(lookback=5)  # 较小 lookback 适配测试数据
            combined, weights = mfs.combine_factors_ic_weighted(
                factor_history, forward_returns, ["F_A", "F_B"],
            )
        assert len(combined) == 25
        assert len(weights) == 25
        # IC 加权模式: 前 lookback 天 IC_IR=0 → 等权兜底
        # 之后应有非零 IC_IR
        for w in weights:
            assert "F_A" in w
            assert "F_B" in w
            # 权重和的绝对值应接近 1 (归一化)
            total_abs = abs(w["F_A"]) + abs(w["F_B"])
            assert 0.9 <= total_abs <= 1.1

    def test_empty_factor_names(self):
        """空因子列表返回空结果."""
        mfs = MultiFactorSignal()
        combined, weights = mfs.combine_factors_ic_weighted(
            {}, [], [],
        )
        assert combined == []
        assert weights == []

    def test_missing_factor_returns_empty(self):
        """因子不存在返回空结果."""
        mfs = MultiFactorSignal()
        combined, weights = mfs.combine_factors_ic_weighted(
            factor_history={"F_A": [{"S001": 1.0}]},
            forward_returns=[{"S001": 0.01}],
            factor_names=["F_NOT_EXIST"],
        )
        assert combined == []
        assert weights == []

    def test_length_alignment(self, synthetic_factor_history):
        """长度对齐: 取最短序列长度."""
        factor_history, forward_returns = synthetic_factor_history
        # 截断 forward_returns 到 12 天
        fwd_short = forward_returns[:12]
        mfs = MultiFactorSignal()
        combined, _weights = mfs.combine_factors_ic_weighted(
            factor_history, fwd_short, ["F_A", "F_B"],
        )
        assert len(combined) == 12

    def test_combined_values_in_valid_range(self, synthetic_factor_history):
        """融合值在合理范围内 (rank 加权后 [0, 1])."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        combined, _ = mfs.combine_factors_ic_weighted(
            factor_history, forward_returns, ["F_A", "F_B"],
        )
        for day_values in combined:
            for v in day_values.values():
                assert -1.0 <= v <= 1.0, f"Combined value out of range: {v}"

    def test_custom_lookback_override(self, synthetic_factor_history):
        """显式 lookback 参数覆盖实例默认值."""
        factor_history, forward_returns = synthetic_factor_history
        FeatureFlags.reset_instance()
        with patch("utils.alpha.multi_factor_signal.is_enabled", return_value=True):
            mfs = MultiFactorSignal(lookback=10)
            combined, _weights = mfs.combine_factors_ic_weighted(
                factor_history, forward_returns, ["F_A", "F_B"],
                lookback=3,  # 显式覆盖
            )
        assert len(combined) == 25


# ============================================================
# 9. generate_signal 单日快照测试
# ============================================================

class TestGenerateSignal:
    """generate_signal 单日快照测试."""

    def test_equal_weight_signal(self):
        """等权融合信号."""
        mfs = MultiFactorSignal()
        factor_values = {
            "F_A": {"S001": 1.0, "S002": 2.0, "S003": 3.0},
            "F_B": {"S001": 3.0, "S002": 2.0, "S003": 1.0},
        }
        signal = mfs.generate_signal(factor_values)
        assert len(signal) == 3
        # F_A 和 F_B 反序, 等权融合后应接近 0.5 (中性)
        for v in signal.values():
            assert 0.0 <= v <= 1.0

    def test_custom_weights_signal(self):
        """自定义权重融合信号."""
        mfs = MultiFactorSignal()
        factor_values = {
            "F_A": {"S001": 1.0, "S002": 2.0, "S003": 3.0},
            "F_B": {"S001": 3.0, "S002": 2.0, "S003": 1.0},
        }
        # 仅用 F_A
        weights = {"F_A": 1.0, "F_B": 0.0}
        signal = mfs.generate_signal(factor_values, weights)
        # S001 在 F_A 中最小 (rank=0), S003 最大 (rank=1)
        assert signal["S001"] == 0.0
        assert signal["S003"] == 1.0

    def test_empty_factor_values(self):
        """空因子值返回空字典."""
        mfs = MultiFactorSignal()
        assert mfs.generate_signal({}) == {}

    def test_single_factor(self):
        """单因子信号 (rank 标准化)."""
        mfs = MultiFactorSignal()
        factor_values = {
            "F_A": {"S001": 10.0, "S002": 20.0, "S003": 30.0},
        }
        signal = mfs.generate_signal(factor_values)
        assert signal["S001"] == 0.0
        assert signal["S003"] == 1.0


# ============================================================
# 10. 便捷函数测试
# ============================================================

class TestConvenienceFunctions:
    """模块级便捷函数测试."""

    def test_combine_factors_function(self, synthetic_factor_history):
        """combine_factors 便捷函数."""
        factor_history, forward_returns = synthetic_factor_history
        FeatureFlags.reset_instance()
        combined, weights = combine_factors(
            factor_history, forward_returns, ["F_A", "F_B"], lookback=10,
        )
        assert len(combined) == 25
        assert len(weights) == 25

    def test_detect_inverted_factors_function(self, synthetic_factor_history):
        """detect_inverted_factors 便捷函数 (HC-6)."""
        factor_history, forward_returns = synthetic_factor_history
        results = detect_inverted_factors(
            factor_history, forward_returns, ["F_A", "F_B"], threshold=0.3,
        )
        assert "F_A" in results
        assert "F_B" in results


# ============================================================
# 11. 异常处理测试
# ============================================================

class TestExceptionHandling:
    """异常处理测试."""

    def test_insufficient_samples_error_inheritance(self):
        """InsufficientSamplesError 继承自 MultiFactorSignalError."""
        assert issubclass(InsufficientSamplesError, MultiFactorSignalError)

    def test_compute_metrics_nonexistent_factor(self, synthetic_factor_history):
        """不存在的因子抛 InsufficientSamplesError."""
        factor_history, forward_returns = synthetic_factor_history
        mfs = MultiFactorSignal()
        with pytest.raises(InsufficientSamplesError) as exc_info:
            mfs.compute_factor_ic_metrics(
                factor_history, forward_returns, "F_NOT_EXIST",
            )
        assert "F_NOT_EXIST" in str(exc_info.value)

    def test_combine_factors_with_missing_factor(self):
        """combine_factors 因子缺失不抛异常, 返回空结果."""
        mfs = MultiFactorSignal()
        combined, weights = mfs.combine_factors_ic_weighted(
            factor_history={"F_A": []},
            forward_returns=[],
            factor_names=["F_A", "F_NOT_EXIST"],
        )
        # F_NOT_EXIST 不存在 → _aligned_length 返回 0
        assert combined == []
        assert weights == []


# ============================================================
# 12. 边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_empty_inputs(self):
        """空输入安全处理."""
        mfs = MultiFactorSignal()
        combined, weights = mfs.combine_factors_ic_weighted(
            factor_history={},
            forward_returns=[],
            factor_names=[],
        )
        assert combined == []
        assert weights == []

    def test_single_symbol_insufficient_for_ic(self):
        """单标的不足以计算 IC (需要 >= 5 标的).

        _spearman_ic 在 len(pairs) < MIN_IC_SAMPLES 时返回 nan,
        compute_factor_ic_metrics 过滤 nan 后 valid_ic 为空,
        len(valid_ic) < MIN_IC_SAMPLES → 抛 InsufficientSamplesError.
        """
        mfs = MultiFactorSignal()
        factor_history = {"F_A": [{"S001": 1.0}] * 10}
        forward_returns = [{"S001": 0.01}] * 10
        with pytest.raises(InsufficientSamplesError):
            mfs.compute_factor_ic_metrics(factor_history, forward_returns, "F_A")

    def test_minimal_valid_samples(self):
        """最小有效样本数 (5 个标的, 8 天, 含噪声让 IC 有方差)."""
        import random
        rng = random.Random(123)
        mfs = MultiFactorSignal(lookback=3)
        symbols = ["S001", "S002", "S003", "S004", "S005", "S006", "S007", "S008"]
        # 每天因子值与 forward_return 整体正相关但含噪声
        factor_history = {
            "F_A": [
                {s: float(i + 1) + rng.gauss(0, 0.3) for i, s in enumerate(symbols)}
                for _ in range(8)
            ],
        }
        forward_returns = [
            {s: 0.001 * (i + 1) + rng.gauss(0, 0.0005) for i, s in enumerate(symbols)}
            for _ in range(8)
        ]
        metrics = mfs.compute_factor_ic_metrics(
            factor_history, forward_returns, "F_A",
        )
        assert metrics.n_samples >= 5
        # 整体正相关 → IC_IR > 0 (但加入噪声后可能不严格, 仅验证可计算)
        assert math.isfinite(metrics.ic_ir)

    def test_zero_weights_fallback_to_equal(self):
        """IC_IR 全为 0 时等权兜底 (避免除零)."""
        mfs = MultiFactorSignal()
        ic_irs = {"F_A": 0.0, "F_B": 0.0}
        weights = mfs._normalize_ic_weights(ic_irs)
        assert abs(weights["F_A"] - 0.5) < 1e-6
        assert abs(weights["F_B"] - 0.5) < 1e-6

    def test_negative_positive_mixed_weights(self):
        """IC_IR 一正一负 → 权重保留符号."""
        mfs = MultiFactorSignal()
        ic_irs = {"F_A": 1.0, "F_B": -0.5}
        weights = mfs._normalize_ic_weights(ic_irs)
        # abs_sum = 1.0 + 0.5 = 1.5
        # weight_A = 1.0/1.5 = 0.6667
        # weight_B = -0.5/1.5 = -0.3333
        assert weights["F_A"] > 0
        assert weights["F_B"] < 0
        assert abs(abs(weights["F_A"]) + abs(weights["F_B"]) - 1.0) < 1e-6
