"""G7 覆盖率冲刺 — utils/alpha_factor/price_volume.py 单元测试

目标: 覆盖率 78.92% → ≥85%
测试重点:
    - compute_momentum_factors: 多窗口动量/12-1M/反转/成交量加权/上涨下跌/行业调整
    - compute_volatility_factors: 多窗口波动率/Beta/下行/特质/偏度
    - compute_size_factors: 对数市值/流通比率/营收/资产/中盘溢酬/非线性/立方
    - compute_liquidity_factors: 换手率/Amihud/价差/深度/RSVP/零收益/量Z-score
    - compute_factor_mining_factors: FM_RET_1D/FM_MOM_5D/FM_MOM_20D/FM_IDIO_VOL/FM_AMIHUD_AMT/FM_CIRC_MCAP
    - 装饰器注册因子 (FM_DEMO_*)
    - 窗口不足跳过 /- NaN/Inf 过滤
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha_factor.price_volume import (  # noqa: E402
    _fm_demo_roe_smoothed,
    _fm_demo_volume_weighted_momentum,
    _fm_demo_zero_trade_days,
    compute_factor_mining_factors,
    compute_liquidity_factors,
    compute_momentum_factors,
    compute_size_factors,
    compute_volatility_factors,
)

# ============================================================
# 测试数据构造
# ============================================================

def _make_price_data(n_syms=5, n_days=300):
    np.random.seed(42)
    price_data = {}
    for i in range(n_syms):
        closes = list(np.cumprod(1 + np.random.normal(0, 0.01, n_days)) * 100)
        vols = [10000 * (i + 1)] * n_days
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        price_data[f"S{i}"] = {
            "closes": closes, "volumes": vols,
            "highs": highs, "lows": lows,
            "opens": closes, "amounts": [c * v for c, v in zip(closes, vols, strict=True)],
        }
    return price_data


# ============================================================
# compute_momentum_factors
# ============================================================

class TestMomentumFactors:
    def test_empty_price_data(self):
        result = compute_momentum_factors({})
        assert len(result) == 10  # 10 个动量因子
        for fval in result.values():
            assert fval.category == "Momentum"

    def test_basic_with_data(self):
        price_data = _make_price_data()
        result = compute_momentum_factors(price_data)
        assert "MOM_20D" in result
        assert "MOM_60D" in result
        assert "MOM_120D" in result
        assert "MOM_252D" in result
        assert "MOM_12_1M" in result
        assert "MOM_REVERSAL_5D" in result
        assert "MOM_REVERSAL_20D" in result
        assert "MOM_VOLUME_ADJ" in result
        assert "MOM_UP_DOWN" in result
        assert "MOM_INDUSTRY_ADJ" in result

    def test_mom_20d_calculation(self):
        """MOM_20D = closes[-1]/closes[-20] - 1."""
        closes = [100.0] * 19 + [110.0]  # 20 个点, 最后一个 110
        price_data = {"A": {"closes": closes, "volumes": [1000] * 20}}
        result = compute_momentum_factors(price_data)
        # len(closes)=20, 不满足 > 20, 跳过
        assert result["MOM_20D"].values == {}

    def test_mom_20d_sufficient_data(self):
        closes = [100.0] * 20 + [110.0]  # 21 个点
        price_data = {"A": {"closes": closes, "volumes": [1000] * 21}}
        result = compute_momentum_factors(price_data)
        assert "A" in result["MOM_20D"].values
        assert result["MOM_20D"].values["A"] == pytest.approx(110 / 100 - 1)

    def test_window_insufficient(self):
        """数据不足时跳过."""
        price_data = {"A": {"closes": [100, 105, 110], "volumes": [1000, 2000, 3000]}}
        result = compute_momentum_factors(price_data)
        # 所有窗口都 > 3, 全部跳过
        assert result["MOM_20D"].values == {}

    def test_with_industries(self):
        price_data = _make_price_data()
        industries = {f"S{i}": "tech" if i < 3 else "fin" for i in range(5)}
        result = compute_momentum_factors(price_data, industries=industries)
        assert "MOM_INDUSTRY_ADJ" in result

    def test_mom_reversal_5d(self):
        """MOM_REVERSAL_5D = -(closes[-1]/closes[-5] - 1)."""
        closes = [100.0] * 5 + [110.0]  # 6 个点
        price_data = {"A": {"closes": closes, "volumes": [1000] * 6}}
        result = compute_momentum_factors(price_data)
        assert "A" in result["MOM_REVERSAL_5D"].values
        assert result["MOM_REVERSAL_5D"].values["A"] == pytest.approx(-(110 / 100 - 1))


# ============================================================
# compute_volatility_factors
# ============================================================

class TestVolatilityFactors:
    def test_empty_price_data(self):
        result = compute_volatility_factors({})
        assert len(result) == 8
        for fval in result.values():
            assert fval.category == "LowVolatility"

    def test_basic_with_data(self):
        price_data = _make_price_data()
        result = compute_volatility_factors(price_data)
        assert "VOL_20D" in result
        assert "VOL_120D" in result
        assert "VOL_60D" in result
        assert "VOL_252D" in result

        assert "VOL_BETA" in result
        assert "VOL_DOWNSIDE" in result
        assert "VOL_IDIO" in result
        assert "VOL_SKEW" in result

    def test_with_benchmark_returns(self):
        price_data = _make_price_data()
        benchmark = list(np.random.normal(0, 0.01, 300))
        result = compute_volatility_factors(price_data, benchmark_returns=benchmark)
        assert "VOL_BETA" in result
        assert "VOL_IDIO" in result

    def test_vol_20d_negative_sign(self):
        """VOL_20D 反向 (低波 = 高分, 取负)."""
        price_data = _make_price_data()
        result = compute_volatility_factors(price_data)
        for val in result["VOL_20D"].values.values():
            assert val < 0  # 反向

    def test_window_insufficient(self):
        price_data = {"A": {"closes": [100, 105, 110]}}
        result = compute_volatility_factors(price_data)
        assert result["VOL_20D"].values == {}


# ============================================================
# compute_size_factors
# ============================================================

class TestSizeFactors:
    def test_empty_fundamentals(self):
        result = compute_size_factors({})
        assert len(result) == 7
        for fval in result.values():
            assert fval.category == "Size"

    def test_basic_with_data(self):
        fundamentals = {
            f"S{i}": {
                "market_cap": 5e8 * (i + 1),
                "negotiable_value": 3e8 * (i + 1),
                "revenue": 1e8 * (i + 1),
                "total_asset": 8e8 * (i + 1),
            }
            for i in range(5)
        }
        result = compute_size_factors(fundamentals)
        assert "SIZE_LOG_MCAP" in result
        assert "SIZE_LOG_NS" in result
        assert "SIZE_LOG_REV" in result
        assert "SIZE_LOG_ASSETS" in result
        assert "SIZE_SMALL_LARGE_RATIO" in result
        assert "SIZE_NON_LINEAR" in result
        assert "SIZE_CUBIC" in result

    def test_size_log_mcap_negative_sign(self):
        """SIZE_LOG_MCAP 反向 (小盘 = 高分, 取负)."""
        fundamentals = {
            "A": {"market_cap": 1e10},
            "B": {"market_cap": 1e8},
        }
        result = compute_size_factors(fundamentals)
        # B (小盘) 应有更高值 (更接近 0 或更小负数)
        assert result["SIZE_LOG_MCAP"].values["B"] > result["SIZE_LOG_MCAP"].values["A"]

    def test_zero_market_cap_skipped(self):
        fundamentals = {"A": {"market_cap": 0}}
        result = compute_size_factors(fundamentals)
        assert result["SIZE_LOG_MCAP"].values == {}

    def test_negotiable_ratio_out_of_range_skipped(self):
        """流通比率 > 1.0 → 跳过."""
        fundamentals = {"A": {"negotiable_value": 200, "market_cap": 100}}  # ratio=2 > 1
        result = compute_size_factors(fundamentals)
        assert result["SIZE_LOG_NS"].values == {}


# ============================================================
# compute_liquidity_factors
#5 ============================================================

class TestLiquidityFactors:
    def test_empty_price_data(self):
        result = compute_liquidity_factors({})
        assert len(result) == 8
        for fval in result.values():
            assert fval.category == "Liquidity"

    def test_basic_with_data(self):
        price_data = _make_price_data()
        result = compute_liquidity_factors(price_data)
        assert "LIQ_TURNOVER_20D" in result
        assert "LIQ_TURNOVER_60D" in result
        assert "LIQ_AMIHUD" in result
        assert "LIQ_SPREAD" in result
        assert "LIQ_DEPTH" in result
        assert "LIQ_RSVP" in result
        assert "LIQ_ZERO_RET_DAYS" in result
        assert "LIQ_VOLUME_ZSCORE" in result

    def test_liq_turnover_negative_sign(self):
        """LIQ_TURNOVER_20D 反向 (低流动性 = 高分, 取负)."""
        price_data = _make_price_data()
        result = compute_liquidity_factors(price_data)
        for val in result["LIQ_TURNOVER_20D"].values.values():
            assert val < 0

    def test_window_insufficient(self):
        price_data = {"A": {"closes": [100, 105], "volumes": [1000, 2000]}}
        result = compute_liquidity_factors(price_data)
        assert result["LIQ_TURNOVER_20D"].values == {}


# ============================================================
# compute_factor_mining_factors
# ============================================================

class TestFactorMiningFactors:
    def test_empty_inputs(self):
        result = compute_factor_mining_factors({}, {})
        assert len(result) == 6  # FM_RET_1D/FM_MOM_5D/FM_MOM_20D/FM_IDIO_VOL/FM_AMIHUD_AMT/FM_CIRC_MCAP
        for fval in result.values():
            assert fval.values == {}

    def test_fm_ret_1d(self):
        """FM_RET_1D = closes[-1]/closes[-2] - 1 (正向)."""
        price_data = {"A": {"closes": [100, 110], "volumes": [1000, 2000]}}
        result = compute_factor_mining_factors(price_data, {})
        assert result["FM_RET_1D"].values["A"] == pytest.approx(110 / 100 - 1)

    def test_fm_mom_5d(self):
        closes = [100.0] * 5 + [110.0]
        price_data = {"A": {"closes": closes, "volumes": [1000] * 6}}
        result = compute_factor_mining_factors(price_data, {})
        assert result["FM_MOM_5D"].values["A"] == pytest.approx(110 / 100 - 1)

    def test_fm_mom_20d(self):
        closes = [100.0] * 20 + [110.0]
        price_data = {"A": {"closes": closes, "volumes": [1000] * 21}}
        result = compute_factor_mining_factors(price_data, {})
        assert result["FM_MOM_20D"].values["A"] == pytest.approx(110 / 100 - 1)

    def test_fm_idio_vol(self):
        """FM_IDIO_VOL: 对市场等权收益回归取残差波动."""
        price_data = _make_price_data(n_syms=5, n_days=70)
        result = compute_factor_mining_factors(price_data, {})
        assert "FM_IDIO_VOL" in result
        # 应有值 (5 个标的, 70 天)
        assert len(result["FM_IDIO_VOL"].values) > 0

    def test_fm_amihud_amt(self):
        """FM_AMIHUD_AMT: 成交额版 Amihud."""
        price_data = _make_price_data(n_syms=3, n_days=30)
        result = compute_factor_mining_factors(price_data, {})
        assert "FM_AMIHUD_AMT" in result

    def test_fm_amihud_amt_with_explicit_amounts(self):
        """提供显式 amounts 字段."""
        closes = list(range(100, 130))
        vols = [1000] * 30
        amounts = [c * v * 2 for c, v in zip(closes, vols, strict=True)]  # 显式 amounts
        price_data = {"A": {"closes": closes, "volumes": vols, "amounts": amounts}}
        result = compute_factor_mining_factors(price_data, {})
        assert "FM_AMIHUD_AMT" in result

    def test_fm_circ_mcap(self):
        """FM_CIRC_MCAP: log(流通市值)."""

        fundamentals = {"A": {"negotiable_value": 1e8}, "B": {"negotiable_value": 1e10}}
        result = compute_factor_mining_factors({}, fundamentals)
        # A (小盘) 应有更高值 (反向)
        assert result["FM_CIRC_MCAP"].values["A"] > result["FM_CIRC_MCAP"].values["B"]

    def test_fm_circ_mcap_zero_skipped(self):
        fundamentals = {"A": {"negotiable_value": 0}}
        result = compute_factor_mining_factors({}, fundamentals)
        assert result["FM_CIRC_MCAP"].values == {}

    def test_with_benchmark_returns(self):
        price_data = _make_price_data(n_syms=3, n_days=70)
        benchmark = list(np.random.normal(0, 0.01, 70))
        result = compute_factor_mining_factors(price_data, {}, benchmark_returns=benchmark)
        assert isinstance(result, dict)


# ============================================================
# 装饰器注册因子 (FM_DEMO_*)
# ============================================================

class TestDecoratorFactors:
    def test_fm_demo_vol_weighted_mom_registered(self):
        """FM_DEMO_VOL_WEIGHTED_MOM 应已注册."""
        from utils.alpha_factor.base import _FACTOR_REGISTRY
        assert "FM_DEMO_VOL_WEIGHTED_MOM" in _FACTOR_REGISTRY

    def test_fm_demo_zero_trade_days_registered(self):
        from utils.alpha_factor.base import _FACTOR_REGISTRY
        assert "FM_DEMO_ZERO_TRADE_DAYS" in _FACTOR_REGISTRY

    def test_fm_demo_roe_smoothed_registered(self):
        from utils.alpha_factor.base import _FACTOR_REGISTRY
        assert "FM_DEMO_ROE_SMOOTHED" in _FACTOR_REGISTRY

    # ---------- _fm_demo_volume_weighted_momentum 实调用 ----------

    def test_vol_weighted_mom_basic(self):
        """放量上涨日权重更高, 加权动量应介于 min(ret) 与 max(ret) 之间."""
        closes = [100, 101, 102, 103, 104, 105, 110]
        amounts = [1000, 1000, 1000, 1000, 1000, 1000, 50000]
        price_data = {"A": {"closes": closes, "amounts": amounts}}
        out = _fm_demo_volume_weighted_momentum(price_data, window=5)
        assert "A" in out
        rets = [closes[i] / closes[i - 1] - 1 for i in range(2, 7)]
        assert min(rets) - 1e-9 <= out["A"] <= max(rets) + 1e-9

    def test_vol_weighted_mom_window_insufficient(self):
        """closes 长度 <= window 时跳过."""
        price_data = {"A": {"closes": [100, 101, 102], "amounts": [1000, 1000, 1000]}}
        out = _fm_demo_volume_weighted_momentum(price_data, window=5)
        assert out == {}

    def test_vol_weighted_mom_amounts_too_short(self):
        """amounts 长度 < closes - 1 时跳过."""
        closes = [100, 101, 102, 103, 104, 105, 106]
        amounts = [1000, 1000]  # 长度不足
        price_data = {"A": {"closes": closes, "amounts": amounts}}
        out = _fm_demo_volume_weighted_momentum(price_data, window=5)
        assert out == {}

    def test_vol_weighted_mom_uses_volumes_fallback(self):
        """无 amounts 时回退到 volumes."""
        closes = [100, 101, 102, 103, 104, 105, 106]
        volumes = [1000] * 7
        price_data = {"A": {"closes": closes, "volumes": volumes}}
        out = _fm_demo_volume_weighted_momentum(price_data, window=5)
        assert "A" in out

    def test_vol_weighted_mom_zero_amount_sum_skipped(self):
        """amount 全 0 时 sum<=0 跳过."""
        closes = [100, 101, 102, 103, 104, 105, 106]
        amounts = [0.0] * 7
        price_data = {"A": {"closes": closes, "amounts": amounts}}
        out = _fm_demo_volume_weighted_momentum(price_data, window=5)
        assert out == {}

    def test_vol_weighted_mom_nan_amount_skipped(self):
        """amount 含 NaN 跳过."""
        closes = [100, 101, 102, 103, 104, 105, 106]
        amounts = [1000, 1000, 1000, 1000, 1000, float("nan"), 1000]
        price_data = {"A": {"closes": closes, "amounts": amounts}}
        out = _fm_demo_volume_weighted_momentum(price_data, window=5)
        assert out == {}

    def test_vol_weighted_mom_multiple_symbols(self):
        """多标的混合: 一个有效一个无效."""
        good = {"closes": [100, 101, 102, 103, 104, 105, 106], "amounts": [1000] * 7}
        bad = {"closes": [100, 101], "amounts": [1000, 1000]}
        price_data = {"A": good, "B": bad}
        out = _fm_demo_volume_weighted_momentum(price_data, window=5)
        assert "A" in out and "B" not in out

    # ---------- _fm_demo_zero_trade_days 实调用 ----------

    def test_zero_trade_days_basic(self):
        """有零成交量日时返回负占比."""
        volumes = [1000] * 15 + [0] * 5  # 5 个零成交日
        price_data = {"A": {"volumes": volumes}}
        out = _fm_demo_zero_trade_days(price_data, window=20)
        assert "A" in out
        assert out["A"] == pytest.approx(-5 / 20)

    def test_zero_trade_days_all_nonzero(self):
        """全非零时返回 0."""
        volumes = [1000] * 20
        price_data = {"A": {"volumes": volumes}}
        out = _fm_demo_zero_trade_days(price_data, window=20)
        assert out["A"] == pytest.approx(0.0)

    def test_zero_trade_days_window_insufficient(self):
        """volumes 长度 < window 跳过."""
        price_data = {"A": {"volumes": [1000, 0, 1000]}}
        out = _fm_demo_zero_trade_days(price_data, window=20)
        assert out == {}

    def test_zero_trade_days_empty_volumes(self):
        price_data = {"A": {}}
        out = _fm_demo_zero_trade_days(price_data, window=20)
        assert out == {}

    # ---------- _fm_demo_roe_smoothed 实调用 ----------

    def test_roe_smoothed_empty_fundamentals(self):
        """fundamentals 为空时返回 {}."""
        assert _fm_demo_roe_smoothed(fundamentals=None) == {}
        assert _fm_demo_roe_smoothed(fundamentals={}) == {}

    def test_roe_smoothed_no_industries(self):
        """无 industries 时走 winsorize + zscore 分支 (723-729)."""
        fundamentals = {
            "A": {"roe": 0.15},
            "B": {"roe": 0.10},
            "C": {"roe": 0.20},
        }
        out = _fm_demo_roe_smoothed(fundamentals=fundamentals, industries=None)
        assert len(out) == 3
        # zscore 均值应接近 0
        mean_val = sum(out.values()) / len(out)
        assert abs(mean_val) < 1e-6

    def test_roe_smoothed_with_industries(self):
        """有 industries 时走 winsorize + neutralize_by_industry 分支 (714-722)."""
        fundamentals = {
            "A": {"roe": 0.15},
            "B": {"roe": 0.10},
            "C": {"roe": 0.20},
            "D": {"roe": 0.12},
        }
        industries = {"A": "Tech", "B": "Tech", "C": "Finance", "D": "Finance"}
        out = _fm_demo_roe_smoothed(fundamentals=fundamentals, industries=industries)
        assert len(out) == 4

    def test_roe_smoothed_roe_none_skipped(self):
        """roe 为 None 的标的跳过."""
        fundamentals = {"A": {"roe": 0.15}, "B": {"roe": None}, "C": {}}
        out = _fm_demo_roe_smoothed(fundamentals=fundamentals, industries=None)
        assert "A" in out and "B" not in out and "C" not in out

    def test_roe_smoothed_roe_invalid_skipped(self):
        """roe 不可转 float 时跳过 (709-710)."""
        fundamentals = {"A": {"roe": 0.15}, "B": {"roe": "invalid"}}
        out = _fm_demo_roe_smoothed(fundamentals=fundamentals, industries=None)
        assert "A" in out and "B" not in out

    def test_roe_smoothed_all_invalid_returns_empty(self):
        """全部无效时 raw 为空, 返回 {} (711-712)."""
        fundamentals = {"A": {"roe": None}, "B": {}}
        out = _fm_demo_roe_smoothed(fundamentals=fundamentals, industries=None)
        assert out == {}

    def test_roe_smoothed_with_industries_and_outlier(self):
        """有 industries 且含极端值, winsorize 应裁剪 (714-722 完整路径)."""
        fundamentals = {
            "A": {"roe": 0.15},
            "B": {"roe": 0.12},
            "C": {"roe": 0.18},
            "D": {"roe": 5.0},  # 极端值
        }
        industries = {"A": "Tech", "B": "Tech", "C": "Finance", "D": "Finance"}
        out = _fm_demo_roe_smoothed(fundamentals=fundamentals, industries=industries)
        assert len(out) == 4
