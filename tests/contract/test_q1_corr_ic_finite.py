"""Q1 契约: 相关系数/IC 必须 np.isfinite 守卫.

契约: np.corrcoef/spearmanr 对常数序列返回 NaN 且不报错;
      项目 IC 计算函数必须先判方差/守卫 NaN, 结果非有限按 0.

对应实现: utils/alpha_factor/base.py calc_ic / calc_ic_series_from_history
"""

from __future__ import annotations

import numpy as np

from utils.alpha_factor.base import calc_ic, calc_ic_series_from_history


def test_calc_ic_constant_factor_returns_finite_zero() -> None:
    """常数因子值序列 (无区分度) → IC 必须返回有限值 0.0, 不传播 NaN."""
    factor_values = {f"S{i}": 1.0 for i in range(10)}
    price_data = {
        f"S{i}": {"closes": [10.0, 10.0 + i * 0.1, 10.0 + i * 0.2]}
        for i in range(10)
    }
    ic = calc_ic(factor_values, price_data, forward_window=1)
    assert np.isfinite(ic), f"Q1 违约: 常数因子 IC={ic} 非有限"
    assert ic == 0.0, f"Q1 违约: 常数因子 IC={ic} 应为 0.0"


def test_calc_ic_insufficient_samples_returns_finite_zero() -> None:
    """样本不足 (<5) → IC 必须返回有限值 0.0, 不传播 NaN."""
    factor_values = {f"S{i}": float(i) for i in range(3)}
    price_data = {
        f"S{i}": {"closes": [10.0, 10.0 + i, 10.0 + 2 * i]} for i in range(3)
    }
    ic = calc_ic(factor_values, price_data, forward_window=1)
    assert np.isfinite(ic), f"Q1 违约: 样本不足 IC={ic} 非有限"
    assert ic == 0.0


def test_calc_ic_normal_case_returns_finite() -> None:
    """正常有区分度序列 → IC 必须有限 (|IC| <= 1)."""
    factor_values = {f"S{i}": float(i) for i in range(10)}
    price_data = {
        f"S{i}": {"closes": [10.0, 10.0 + i * 0.5, 10.0 + i]} for i in range(10)
    }
    ic = calc_ic(factor_values, price_data, forward_window=1)
    assert np.isfinite(ic), f"Q1 违约: 正常 IC={ic} 非有限"
    assert -1.0 <= ic <= 1.0


def test_calc_ic_series_constant_returns_all_finite_zero() -> None:
    """时序 IC 序列: 常数序列每日 → 全 0.0, 无 NaN."""
    factor_history = [{f"S{i}": 1.0 for i in range(10)}] * 5
    ret_history = [{f"S{i}": float(i) * 0.01 for i in range(10)}] * 5
    series = calc_ic_series_from_history(factor_history, ret_history)
    assert all(np.isfinite(v) for v in series), "Q1 违约: IC 序列含 NaN"
    assert all(v == 0.0 for v in series), f"Q1 违约: 常数序列 IC 应全 0, 实际 {series}"
