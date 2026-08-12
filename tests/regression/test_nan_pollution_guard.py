"""回归测试: 相关系数/协方差/IC 的 NaN 污染防护 (F-4 / F-5 / F-6).

背景
----
量化系统中大量使用 np.corrcoef / spearmanr。当因子或收益序列为**常数**
(零方差, 如停牌/恒定价/未更新数据) 时, 这些函数会返回 NaN 且**不会抛异常**,
导致 NaN 沿风险监控、仓位计算、策略 IC 指标路径静默传播, 腐蚀决策。

本测试锁定三处已修复的泄漏点, 确保常数列输入不再产生 NaN。
"""

import math

import numpy as np
import pytest

from utils.risk_metrics import calculate_correlation
from utils.ledoit_wolf_covariance import LedoitWolfCovariance
from utils.infra.core import StrategyRegistry


def _matrix_with_constant_column(t: int = 30, n: int = 4) -> np.ndarray:
    """构造含一个常数列(零方差)的收益率矩阵: 第 0 列恒为 0.0。"""
    rng = np.random.default_rng(42)
    m = rng.normal(0, 0.01, size=(t, n))
    m[:, 0] = 0.0  # 常数/零方差资产
    return m


# ----------------------------------------------------------------------
# F-5: risk_metrics.calculate_correlation 常数列不产 NaN
# ----------------------------------------------------------------------
def test_calculate_correlation_no_nan_with_constant_column():
    m = _matrix_with_constant_column()
    corr = calculate_correlation(m)
    assert corr.shape == (m.shape[1], m.shape[1])
    assert np.all(np.isfinite(corr)), "相关系数矩阵含 NaN/Inf (F-5 回归)"
    # 对角线恒为 1
    assert np.allclose(np.diag(corr), 1.0)


# ----------------------------------------------------------------------
# F-6: Ledoit-Wolf 收缩估计常数列 avg_correlation 有限
# ----------------------------------------------------------------------
def test_ledoit_wolf_avg_correlation_finite_with_constant_column():
    m = _matrix_with_constant_column()
    result = LedoitWolfCovariance().fit(m)
    assert np.isfinite(result.avg_correlation), "avg_correlation 为 NaN (F-6 回归)"
    assert np.all(np.isfinite(result.cov_shrunk)), "cov_shrunk 含 NaN (F-6 回归)"
    assert 0.0 <= result.shrinkage_intensity <= 1.0


# ----------------------------------------------------------------------
# F-4: 策略注册表 update_alpha_metrics 常数序列 IC 不为 NaN
# ----------------------------------------------------------------------
def test_update_alpha_metrics_constant_series_no_nan():
    reg = StrategyRegistry.get_instance()

    class _Dummy:
        pass

    name = "_nan_guard_test_strategy"
    reg.register(name, _Dummy, overwrite=True)
    try:
        # 常数因子 + 常数收益 → corrcoef 返回 NaN, 必须被防护为 0.0
        constant_factor = [1.0, 1.0, 1.0, 1.0]
        constant_ret = [0.0, 0.0, 0.0, 0.0]
        reg.update_alpha_metrics(name, constant_factor, constant_ret)
        perf = reg.get_performance(name)
        assert math.isfinite(perf.ic_mean), "ic_mean 为 NaN (F-4 回归)"
        assert math.isfinite(perf.ic_ir), "ic_ir 为 NaN (F-4 回归)"
        assert perf.ic_mean == 0.0
    finally:
        reg.unregister(name)


# ----------------------------------------------------------------------
# 端到端: 正常序列仍给出有效 IC, 不被防护误伤
# ----------------------------------------------------------------------
def test_update_alpha_metrics_normal_series_still_works():
    reg = StrategyRegistry.get_instance()

    class _Dummy:
        pass

    name = "_nan_guard_test_strategy_normal"
    reg.register(name, _Dummy, overwrite=True)
    try:
        factor = [0.1, 0.2, 0.3, 0.4, 0.5]
        ret = [0.01, 0.02, 0.015, 0.03, 0.025]
        reg.update_alpha_metrics(name, factor, ret)
        perf = reg.get_performance(name)
        assert math.isfinite(perf.ic_mean)
        assert perf.ic_mean != 0.0  # 确有相关, 不应被误判为 0
    finally:
        reg.unregister(name)
