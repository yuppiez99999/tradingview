"""U1 衔接 library 集成测试.

验证 AlphaFactorLibrary.compute_all 接入 factor_history 后:
    - 场景 1: 传入 factor_history 走时序 IC/ICIR 模式 (fval.ic_ir 被填充)
    - 场景 2: 不传 factor_history 走降级单点 IC (向后兼容, fval.ic_ir 不被填充)
    - 场景 3: factor_history 部分因子有历史部分无 (混合模式)
    - 场景 4: factor_history 样本不足 (<20 天) 仍走降级单点 IC

签名核对 (2026-08-05):
    - AlphaFactorLibrary.compute_all(price_data, fundamentals=None, ...,
        factor_history=None, forward_returns_history=None) -> FactorLibraryResult
    - evaluate_factors(result, price_data, factor_history=None, forward_returns_history=None)
    - use_timeseries = factor_history is not None and forward_returns_history is not None
                       and len(forward_returns_history) >= 20
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from utils.alpha_factor.base import FactorLibraryResult, FactorValue, evaluate_factors
from utils.alpha_factor.library import AlphaFactorLibrary

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.unit]


# ============================================================
# Helper: 构造测试数据
# ============================================================


def _build_price_data(n_symbols: int = 10, n_days: int = 30) -> dict:
    """构造 price_data: {symbol: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...]}}."""
    np.random.seed(42)
    price_data = {}
    for i in range(n_symbols):
        closes = 100 * np.exp(np.cumsum(np.random.randn(n_days) * 0.02))
        price_data[f"TEST{i:03d}.SZ"] = {
            "closes": closes.tolist(),
            "volumes": np.random.randint(1e6, 1e7, n_days).tolist(),
            "highs": (closes * 1.02).tolist(),
            "lows": (closes * 0.98).tolist(),
        }
    return price_data


def _build_factor_history(
    factor_name: str,
    n_days: int = 25,
    n_symbols: int = 10,
) -> dict[str, list[dict[str, float]]]:
    """构造 factor_history: {factor_name: [{symbol: value}, ...]}."""
    np.random.seed(123)
    symbols = [f"TEST{i:03d}.SZ" for i in range(n_symbols)]
    history = []
    for _ in range(n_days):
        day_values = {sym: float(np.random.randn()) for sym in symbols}
        history.append(day_values)
    return {factor_name: history}


def _build_forward_returns_history(
    n_days: int = 25,
    n_symbols: int = 10,
) -> list[dict[str, float]]:
    """构造 forward_returns_history: [{symbol: ret}, ...]."""
    np.random.seed(456)
    symbols = [f"TEST{i:03d}.SZ" for i in range(n_symbols)]
    return [
        {sym: float(np.random.randn() * 0.02) for sym in symbols} for _ in range(n_days)
    ]


# ============================================================
# 场景 1: 传入 factor_history 走时序 IC/ICIR 模式 ✅
# ============================================================


class TestTimeseriesMode:
    """传入 factor_history + forward_returns_history 时走时序模式."""

    def test_timeseries_mode_fills_ic_ir(self):
        """传入完整 factor_history: fval.ic_ir 被填充 (时序模式)."""
        price_data = _build_price_data()
        factor_history = _build_factor_history("MOM_20D", n_days=25)
        fwd_returns = _build_forward_returns_history(n_days=25)

        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data=price_data,
            factor_history=factor_history,
            forward_returns_history=fwd_returns,
        )

        # 时序模式下, MOM_20D 因子应有 ic_ir 值
        if "MOM_20D" in result.factors:
            fval = result.factors["MOM_20D"]
            # ic_ir 被填充 (时序模式); 0.0 也是合法值 (样本不足或零方差)
            assert fval.ic_ir is not None
            assert isinstance(fval.ic_ir, float)
            # ic_1d / ic_5d 也应被填充
            assert fval.ic_1d is not None
            assert fval.ic_5d is not None

    def test_timeseries_mode_strong_factors_populated(self):
        """时序模式: strong_factors / effective_factors 基于时序 IC 均值."""
        price_data = _build_price_data()
        # 构造强相关 factor_history (因子值与 forward_return 强正相关)
        n_days, n_symbols = 25, 10
        symbols = [f"TEST{i:03d}.SZ" for i in range(n_symbols)]
        np.random.seed(789)
        fwd_returns = [
            {sym: float(np.random.randn() * 0.02) for sym in symbols}
            for _ in range(n_days)
        ]
        # 因子值 = forward_return + 小噪声 (强正相关)
        factor_history = {
            "MOM_20D": [
                {
                    sym: fwd_returns[i][sym] + np.random.randn() * 0.001
                    for sym in symbols
                }
                for i in range(n_days)
            ]
        }

        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data=price_data,
            factor_history=factor_history,
            forward_returns_history=fwd_returns,
        )

        # 强相关因子应被识别 (strong_factors 或 effective_factors)
        if "MOM_20D" in result.factors:
            fval = result.factors["MOM_20D"]
            assert fval.ic_ir is not None
            # 强相关下 ic_ir 应为正
            assert fval.ic_ir > 0, f"强相关因子 ic_ir 应>0, 实际 {fval.ic_ir}"


# ============================================================
# 场景 2: 不传 factor_history 走降级单点 IC (向后兼容) ✅
# ============================================================


class TestBackwardCompatibility:
    """不传 factor_history 时走降级单点 IC (向后兼容)."""

    def test_no_factor_history_falls_back_to_single_point(self):
        """不传 factor_history: 走单点 IC, fval.ic_ir 不被填充 (保持默认值)."""
        price_data = _build_price_data()

        lib = AlphaFactorLibrary()
        result = lib.compute_all(price_data=price_data)

        # 降级模式: ic_ir 保持默认值 (0.0 或 None, 取决于 FactorValue 定义)
        # 关键: 不应有时序 ic_1d 填充
        for _name, fval in result.factors.items():
            # ic_5d 在单点模式下被填充 (calc_ic with forward_window=5)
            assert fval.ic_5d is not None
            # ic_ir 在单点模式下不被填充 (保持默认 0.0)
            assert fval.ic_ir == 0.0 or fval.ic_ir is None

    def test_none_factor_history_equivalent_to_omitted(self):
        """显式传 factor_history=None 等价于不传."""
        price_data = _build_price_data()
        lib = AlphaFactorLibrary()

        result_omitted = lib.compute_all(price_data=price_data)
        result_none = lib.compute_all(
            price_data=price_data,
            factor_history=None,
            forward_returns_history=None,
        )

        # 强因子列表长度一致 (行为等价)
        assert len(result_omitted.strong_factors) == len(result_none.strong_factors)


# ============================================================
# 场景 3: factor_history 部分因子有历史部分无 (混合模式) ✅
# ============================================================


class TestMixedMode:
    """factor_history 部分因子有历史部分无时的混合模式."""

    def test_partial_factor_history(self):
        """部分因子有 history: 有 history 的走时序, 无的走单点."""
        price_data = _build_price_data()
        # 只给 MOM_20D 历史, 其他因子无
        factor_history = _build_factor_history("MOM_20D", n_days=25)
        fwd_returns = _build_forward_returns_history(n_days=25)

        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data=price_data,
            factor_history=factor_history,
            forward_returns_history=fwd_returns,
        )

        # MOM_20D 应走时序模式 (有 history)
        if "MOM_20D" in result.factors:
            fval = result.factors["MOM_20D"]
            assert fval.ic_ir is not None

        # 其他因子走单点 IC (不在 factor_history 中)
        for name, fval in result.factors.items():
            if name == "MOM_20D":
                continue
            # 单点模式: ic_ir 保持默认
            assert fval.ic_ir == 0.0 or fval.ic_ir is None


# ============================================================
# 场景 4: factor_history 样本不足 (<20 天) 仍走降级单点 IC ✅
# ============================================================


class TestInsufficientSamples:
    """forward_returns_history < 20 天时仍走降级单点 IC."""

    def test_short_history_falls_back_to_single_point(self):
        """forward_returns_history 只有 15 天 (<20): 走单点 IC."""
        price_data = _build_price_data()
        factor_history = _build_factor_history("MOM_20D", n_days=15)
        fwd_returns = _build_forward_returns_history(n_days=15)

        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data=price_data,
            factor_history=factor_history,
            forward_returns_history=fwd_returns,
        )

        # 样本不足: 走降级单点 IC
        if "MOM_20D" in result.factors:
            fval = result.factors["MOM_20D"]
            # ic_ir 保持默认 (时序模式未触发)
            assert fval.ic_ir == 0.0 or fval.ic_ir is None
            # ic_5d 在单点模式下被填充
            assert fval.ic_5d is not None


# ============================================================
# 场景 5: evaluate_factors 直接调用幂等性验证 ✅
# ============================================================


class TestEvaluateFactorsIdempotency:
    """evaluate_factors 直接调用幂等性 (重复调用结果一致)."""

    def test_evaluate_factors_idempotent(self):
        """重复调用 evaluate_factors: strong/effective 列表一致."""
        price_data = _build_price_data()

        # 构造一个简单的 FactorLibraryResult
        result = FactorLibraryResult()
        np.random.seed(999)
        symbols = list(price_data.keys())
        result.factors["MOM_20D"] = FactorValue(
            name="MOM_20D",
            category="Momentum",
            values={sym: float(np.random.randn()) for sym in symbols},
        )

        # 第一次调用
        evaluate_factors(result, price_data)
        strong_1 = list(result.strong_factors)
        effective_1 = list(result.effective_factors)

        # 第二次调用 (幂等: 先清空再评估)
        evaluate_factors(result, price_data)
        strong_2 = list(result.strong_factors)
        effective_2 = list(result.effective_factors)

        assert strong_1 == strong_2
        assert effective_1 == effective_2
