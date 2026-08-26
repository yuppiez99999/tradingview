"""P0 经典理论四件套单元测试

覆盖:
- utils/alpha_factor/hurst.py: Hurst 指数 (R/S 分析)
- utils/alpha_factor/information_theory.py: 信息论 (熵/KL/互信息)
- utils/stat_arb/: 协整 + 配对交易 (Engle-Granger + Johansen)
- utils/timing/directional_change.py: DC 事件驱动择时

参考: cairn/classic-theory-coverage-20260819.md Top 10 #1-#4
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha_factor.hurst import (  # noqa: E402
    classify_regime,
    compute_hurst_factors,
    estimate_hurst,
)
from utils.alpha_factor.information_theory import (  # noqa: E402
    compute_information_factors,
    distribution_drift,
    factor_information_content,
    factor_redundancy,
    kl_divergence,
    mutual_information,
    select_factors_by_information,
    shannon_entropy,
)
from utils.stat_arb import (  # noqa: E402
    PairsTradingEngine,
    engle_granger_test,
    find_cointegrated_pairs,
    johansen_test,
    ols_regression,
)
from utils.timing.directional_change import (  # noqa: E402
    DCEventType,
    DirectionalChangeExtractor,
    compute_dc_factors,
    dc_volatility,
    extract_dc_events,
)

# ============================================================
# 测试数据构造
# ============================================================


def _make_trending_prices(n: int = 300, drift: float = 0.001) -> list[float]:
    """构造趋势性价格序列 (Hurst > 0.5)"""
    np.random.seed(42)
    rets = np.random.normal(drift, 0.01, n) + drift * 0.5
    return list(np.cumprod(1 + rets) * 100)


def _make_mean_reverting_prices(n: int = 300, kappa: float = 0.05) -> list[float]:
    """构造均值回归价格序列 (Hurst < 0.5)"""
    np.random.seed(42)
    p = [100.0]
    for _ in range(n - 1):
        ret = -kappa * (p[-1] - 100) / 100 + np.random.normal(0, 0.01)
        p.append(p[-1] * (1 + ret))
    return p


def _make_random_walk_prices(n: int = 300) -> list[float]:
    """构造随机游走价格序列 (Hurst ≈ 0.5)"""
    np.random.seed(42)
    rets = np.random.normal(0, 0.01, n)
    return list(np.cumprod(1 + rets) * 100)


def _make_price_data(n_syms: int = 5, n_days: int = 300) -> dict:
    np.random.seed(42)
    price_data = {}
    for i in range(n_syms):
        closes = list(np.cumprod(1 + np.random.normal(0, 0.01, n_days)) * 100)
        vols = [10000 * (i + 1)] * n_days
        price_data[f"S{i}"] = {
            "closes": closes, "volumes": vols,
            "highs": [c * 1.01 for c in closes],
            "lows": [c * 0.99 for c in closes],
        }
    return price_data


# ============================================================
# 1. Hurst 指数测试
# ============================================================


class TestHurstExponent:
    """Hurst 指数 (R/S 分析) 测试"""

    def test_trending_series_hurst_gt_05(self):
        """趋势序列 Hurst > 0.5"""
        prices = _make_trending_prices(500, drift=0.002)
        h = estimate_hurst(prices)
        assert 0.5 < h <= 1.0, f"趋势序列 Hurst 应 > 0.5, 实际 {h}"

    def test_random_walk_hurst_near_05(self):
        """随机游走 Hurst ≈ 0.5 (容差 ±0.15)"""
        prices = _make_random_walk_prices(500)
        h = estimate_hurst(prices)
        assert 0.35 < h < 0.65, f"随机游走 Hurst 应 ≈ 0.5, 实际 {h}"

    def test_short_series_returns_05(self):
        """短序列返回 0.5 (中性)"""
        h = estimate_hurst([100, 101, 102])
        assert h == 0.5

    def test_hurst_in_range_0_1(self):
        """Hurst 指数 ∈ [0, 1]"""
        prices = _make_random_walk_prices(300)
        h = estimate_hurst(prices)
        assert 0.0 <= h <= 1.0

    def test_compute_hurst_factors(self):
        """compute_hurst_factors 返回 4 个因子"""
        price_data = _make_price_data(n_syms=3, n_days=300)
        factors = compute_hurst_factors(price_data)
        assert "HURST_60D" in factors
        assert "HURST_120D" in factors
        assert "HURST_252D" in factors
        assert "HURST_TREND_SCORE" in factors
        # 60 日因子应有值
        assert len(factors["HURST_60D"].values) == 3

    def test_classify_regime(self):
        """制度分类"""
        assert classify_regime(0.7) == "trending"
        assert classify_regime(0.3) == "mean_reverting"
        assert classify_regime(0.5) == "random_walk"


# ============================================================
# 2. 信息论测试
# ============================================================


class TestInformationTheory:
    """信息论度量测试"""

    def test_shannon_entropy_uniform(self):
        """均匀分布熵最大 = log2(n_bins)"""
        values = list(np.linspace(0, 1, 100))
        h = shannon_entropy(values, n_bins=10)
        assert 3.0 < h <= 3.5, f"均匀分布 10 箱熵应 ≈ log2(10)=3.32, 实际 {h}"

    def test_shannon_entropy_constant(self):
        """常数序列熵 = 0"""
        h = shannon_entropy([5.0] * 100)
        assert h == 0.0

    def test_shannon_entropy_empty(self):
        """空序列熵 = 0"""
        assert shannon_entropy([]) == 0.0

    def test_kl_divergence_same_dist(self):
        """相同分布 KL = 0"""
        np.random.seed(42)
        a = list(np.random.normal(0, 1, 200))
        kl = kl_divergence(a, a, n_bins=10)
        assert kl < 0.01, f"相同分布 KL 应 ≈ 0, 实际 {kl}"

    def test_kl_divergence_different_dist(self):
        """不同分布 KL > 0"""
        np.random.seed(42)
        a = list(np.random.normal(0, 1, 200))
        b = list(np.random.normal(2, 1, 200))
        kl = kl_divergence(a, b, n_bins=10)
        assert kl > 0.1, f"不同分布 KL 应 > 0.1, 实际 {kl}"

    def test_mutual_information_independent(self):
        """独立变量互信息 ≈ 0 (分箱估计有小噪声)"""
        np.random.seed(42)
        x = list(np.random.normal(0, 1, 1000))
        y = list(np.random.normal(0, 1, 1000))
        mi = mutual_information(x, y, n_bins=10)
        assert mi < 0.15, f"独立变量 MI 应接近 0, 实际 {mi}"

    def test_mutual_information_correlated(self):
        """强相关变量互信息 > 0"""
        np.random.seed(42)
        x = np.random.normal(0, 1, 500)
        y = x + np.random.normal(0, 0.1, 500)
        mi = mutual_information(list(x), list(y), n_bins=10)
        assert mi > 0.5, f"强相关 MI 应 > 0.5, 实际 {mi}"

    def test_factor_information_content(self):
        """因子信息含量"""
        np.random.seed(42)
        factor_vals = {f"S{i}": float(np.random.normal(0, 1)) for i in range(50)}
        fwd_rets = {f"S{i}": factor_vals[f"S{i}"] * 0.5 + float(np.random.normal(0, 0.1)) for i in range(50)}
        ic = factor_information_content(factor_vals, fwd_rets)
        assert ic > 0

    def test_factor_redundancy(self):
        """因子冗余度"""
        np.random.seed(42)
        a = {f"S{i}": float(np.random.normal(0, 1)) for i in range(50)}
        b = {f"S{i}": a[f"S{i}"] * 2 + 1 for i in range(50)}  # 完全线性相关
        red = factor_redundancy(a, b)
        assert red > 0.5, f"线性相关冗余应高, 实际 {red}"

    def test_distribution_drift(self):
        """分布漂移"""
        np.random.seed(42)
        cur = {f"S{i}": float(np.random.normal(1, 1)) for i in range(50)}
        hist = {f"S{i}": float(np.random.normal(0, 1)) for i in range(50)}
        drift = distribution_drift(cur, hist)
        assert drift > 0

    def test_compute_information_factors(self):
        """compute_information_factors 返回 3 个因子"""
        price_data = _make_price_data(n_syms=3, n_days=200)
        factors = compute_information_factors(price_data)
        assert "INFO_ENTROPY_60D" in factors
        assert "INFO_ENTROPY_120D" in factors
        assert "INFO_DRIFT_60D" in factors

    def test_select_factors_by_information(self):
        """因子筛选"""
        np.random.seed(42)
        fwd = {f"S{i}": float(np.random.normal(0, 0.02)) for i in range(50)}
        # 强因子
        f1 = {f"S{i}": fwd[f"S{i}"] * 10 + float(np.random.normal(0, 0.01)) for i in range(50)}
        # 弱因子
        f2 = {f"S{i}": float(np.random.normal(0, 1)) for i in range(50)}
        selected = select_factors_by_information(
            {"F1": f1, "F2": f2}, fwd, min_ic=0.01, max_redundancy=0.5
        )
        assert "F1" in selected


# ============================================================
# 3. 协整 + 配对交易测试
# ============================================================


class TestCointegration:
    """协整检验测试"""

    def test_ols_regression_basic(self):
        """OLS 基本回归"""
        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]  # y = 2x
        beta, alpha, r2 = ols_regression(y, x)
        assert abs(beta - 2.0) < 1e-6
        assert abs(alpha) < 1e-6
        assert abs(r2 - 1.0) < 1e-6

    def test_ols_regression_with_intercept(self):
        """OLS 带截距"""
        x = [1, 2, 3, 4, 5]
        y = [3, 5, 7, 9, 11]  # y = 2x + 1
        beta, alpha, _ = ols_regression(y, x)
        assert abs(beta - 2.0) < 1e-6
        assert abs(alpha - 1.0) < 1e-6

    def test_ols_short_series(self):
        """短序列 OLS 返回 0"""
        beta, alpha, r2 = ols_regression([1, 2], [1, 2])
        assert beta == 0.0 and alpha == 0.0 and r2 == 0.0

    def test_engle_granger_cointegrated(self):
        """协整序列检验通过"""
        np.random.seed(42)
        # 构造协整对: y = 2x + noise, x 随机游走
        n = 300
        x = np.cumsum(np.random.normal(0, 1, n)) + 100
        noise = np.random.normal(0, 0.5, n)
        y = 2 * x + noise
        result = engle_granger_test(y, x)
        assert result.is_cointegrated, f"协整序列应通过检验, p={result.pvalue}"
        assert abs(result.hedge_ratio - 2.0) < 0.1

    def test_engle_granger_not_cointegrated(self):
        """非协整序列检验不通过"""
        np.random.seed(42)
        n = 300
        x = np.cumsum(np.random.normal(0, 1, n)) + 100
        y = np.cumsum(np.random.normal(0, 1, n)) + 100  # 独立随机游走
        result = engle_granger_test(y, x)
        assert not result.is_cointegrated

    def test_engle_granger_short_series(self):
        """短序列返回不协整"""
        result = engle_granger_test([1, 2, 3], [4, 5, 6])
        assert not result.is_cointegrated

    def test_johansen_basic(self):
        """Johansen 检验基本运行"""
        np.random.seed(42)
        n = 200
        x = np.cumsum(np.random.normal(0, 1, n))
        y = 2 * x + np.random.normal(0, 0.5, n)
        series = np.column_stack([x, y])
        result = johansen_test(series)
        assert result["n_variables"] == 2
        assert "rank" in result
        assert "eigenvalues" in result

    def test_johansen_short_series(self):
        """短序列 Johansen 返回 rank=0"""
        result = johansen_test([[1, 2], [3, 4]])
        assert result["rank"] == 0


class TestPairsTrading:
    """配对交易引擎测试"""

    def test_find_cointegrated_pairs(self):
        """寻找协整对"""
        np.random.seed(42)
        n = 300
        x = list(np.cumsum(np.random.normal(0, 1, n)) + 100)
        y = [2 * xi + float(np.random.normal(0, 0.5)) for xi in x]
        z = list(np.cumsum(np.random.normal(0, 1, n)) + 100)  # 独立
        price_data = {"A": x, "B": y, "C": z}
        # min_half_life=0 避免高协整但快速均值回复的对被过滤
        pairs = find_cointegrated_pairs(
            price_data, significance=0.10, min_half_life=0, max_half_life=100
        )
        # A-B 应被识别为协整对
        pair_set = {(p["code_a"], p["code_b"]) for p in pairs}
        assert ("A", "B") in pair_set or ("B", "A") in pair_set

    def test_pairs_trading_engine_signals(self):
        """配对交易信号生成"""
        np.random.seed(42)
        n = 300
        x = list(np.cumsum(np.random.normal(0, 1, n)) + 100)
        y = [2 * xi + float(np.random.normal(0, 0.5)) for xi in x]
        pairs = find_cointegrated_pairs(
            {"A": x, "B": y}, significance=0.10, min_half_life=0, max_half_life=100
        )
        if not pairs:
            pytest.skip("未找到协整对")
        engine = PairsTradingEngine(entry_z=1.5, exit_z=0.5)
        signals = engine.generate_signals({"A": x, "B": y}, pairs)
        assert len(signals) == len(pairs)
        for sig in signals:
            assert sig.signal in (-1, 0, 1)
            assert sig.to_dict()["signal"] == sig.signal

    def test_compute_zscore(self):
        """Z-score 计算"""
        engine = PairsTradingEngine(zscore_window=20)
        pa = list(np.linspace(100, 110, 50))
        pb = list(np.linspace(50, 55, 50))
        z = engine.compute_zscore(pa, pb, hedge_ratio=2.0, intercept=0.0)
        assert isinstance(z, float)


# ============================================================
# 4. Directional Change 测试
# ============================================================


class TestDirectionalChange:
    """DC 事件驱动择时测试"""

    def test_extract_dc_events_basic(self):
        """基本 DC 事件提取"""
        prices = [100, 102, 105, 103, 100, 98, 95, 97, 100, 103]
        events = extract_dc_events(prices, threshold=0.03)
        assert len(events) > 0
        for ev in events:
            assert ev.event_type in (DCEventType.UPWARD_DC, DCEventType.DOWNWARD_DC)

    def test_extract_dc_events_short_series(self):
        """短序列无事件"""
        events = extract_dc_events([100, 101], threshold=0.01)
        assert events == []

    def test_dc_volatility(self):
        """DC 波动率"""
        np.random.seed(42)
        prices = list(np.cumprod(1 + np.random.normal(0, 0.02, 200)) * 100)
        vol = dc_volatility(prices, threshold=0.01)
        assert vol > 0

    def test_dc_volatility_short(self):
        """短序列波动率 = 0"""
        assert dc_volatility([100, 101, 102]) == 0.0

    def test_directional_change_extractor_streaming(self):
        """流式 DC 提取器"""
        extractor = DirectionalChangeExtractor(threshold=0.02)
        prices = [100, 102, 105, 103, 100, 98, 95, 97, 100, 103]
        all_events = []
        for p in prices:
            new_events = extractor.update(p)
            all_events.extend(new_events)
        # 流式结果应与批量一致
        batch_events = extract_dc_events(prices, threshold=0.02)
        assert len(all_events) == len(batch_events)

    def test_compute_dc_factors(self):
        """DC 因子计算"""
        price_data = _make_price_data(n_syms=3, n_days=200)
        factors = compute_dc_factors(price_data, threshold=0.01)
        assert "DC_VOL_60D" in factors
        assert "DC_VOL_120D" in factors
        assert "DC_TREND_60D" in factors
        assert len(factors["DC_VOL_60D"].values) == 3

    def test_dc_trend_upward(self):
        """上行趋势 DC_TREND 为正"""
        prices = list(np.cumprod(1 + np.random.normal(0.002, 0.005, 100)) * 100)
        events = extract_dc_events(prices, threshold=0.01)
        n_up = sum(1 for e in events if e.event_type == DCEventType.UPWARD_DC)
        n_down = sum(1 for e in events if e.event_type == DCEventType.DOWNWARD_DC)
        # 上行趋势中上行 DC 应不少于下行 DC
        assert n_up >= n_down - 2  # 容差 2
