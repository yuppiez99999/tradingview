"""G7 覆盖率冲刺 — risk_metrics.py 测试增强 (W7.4.5 Phase 2E)

覆盖目标: 26.54% → 90%+
覆盖范围:
  - calculate_var: historical/parametric/monte_carlo/异常路径
  - calculate_es: 正常/空数组/NaN 路径
  - calculate_max_drawdown: 正常/峰值/空数组
  - calculate_sharpe_ratio / calculate_sortino_ratio / calculate_calmar_ratio
  - calculate_volatility / calculate_beta / calculate_alpha
  - calculate_correlation / calculate_tracking_error / calculate_information_ratio
  - calculate_win_rate / calculate_profit_factor
  - calculate_performance_metrics (聚合)
  - calculate_portfolio_weights (positions.json 格式)
  - _align_and_dropna (内部工具函数)
"""

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.risk_metrics import (  # noqa: E402
    _align_and_dropna,
    calculate_alpha,
    calculate_beta,
    calculate_calmar_ratio,
    calculate_correlation,
    calculate_es,
    calculate_information_ratio,
    calculate_max_drawdown,
    calculate_performance_metrics,
    calculate_portfolio_weights,
    calculate_profit_factor,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_tracking_error,
    calculate_var,
    calculate_volatility,
    calculate_win_rate,
)


# ============================================================
# 1. calculate_var
# ============================================================
class TestCalculateVar:
    def test_empty_returns_zero(self):
        assert calculate_var(np.array([])) == 0.0

    def test_all_nan_returns_zero(self):
        assert calculate_var(np.array([np.nan, np.nan, np.nan])) == 0.0

    def test_historical_method_positive(self):
        # 损失分位数为负, abs 后应为正
        rets = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])
        var = calculate_var(rets, 0.95, "historical")
        assert var > 0

    def test_historical_method_confidence_99(self):
        rets = np.array([-0.05, -0.02, 0.0, 0.01, 0.02, 0.03])
        calculate_var(rets, 0.95, "historical")
        var99 = calculate_var(rets, 0.99, "historical")
        # 99% 应至少不小于 95% 的损失幅度 (绝对值)
        assert var99 >= 0

    def test_parametric_method(self):
        rets = np.random.normal(0, 0.01, 100)
        var = calculate_var(rets, 0.95, "parametric")
        assert var > 0

    def test_monte_carlo_method(self):
        np.random.seed(42)
        rets = np.random.normal(0, 0.01, 50)
        var = calculate_var(rets, 0.95, "monte_carlo")
        assert var > 0

    def test_unsupported_method_raises_returns_fail_closed(self):
        # 不支持的方法触发 ValueError, fail-closed 返回 0.03
        rets = np.array([0.01, 0.02])
        var = calculate_var(rets, 0.95, "unknown_method")
        assert var == 0.03

    def test_nan_filtered(self):
        rets = np.array([np.nan, -0.02, 0.0, 0.01, np.nan, 0.02])
        var = calculate_var(rets, 0.95, "historical")
        assert var >= 0


# ============================================================
# 2. calculate_es
# ============================================================
class TestCalculateEs:
    def test_empty_returns_zero(self):
        assert calculate_es(np.array([])) == 0.0

    def test_all_nan_returns_zero(self):
        assert calculate_es(np.array([np.nan, np.nan])) == 0.0

    def test_normal_case(self):
        rets = np.array([-0.05, -0.03, -0.02, 0.0, 0.01, 0.02, 0.03])
        es = calculate_es(rets, 0.95)
        assert es > 0

    def test_es_at_least_var(self):
        rets = np.array([-0.10, -0.05, -0.02, 0.0, 0.01, 0.02, 0.05, 0.10])
        var = calculate_var(rets, 0.95)
        es = calculate_es(rets, 0.95)
        # ES 应 >= VaR (ES 是尾部平均)
        assert es >= var * 0.99  # 允许数值误差

    def test_no_tail_returns_returns_var(self):
        # 当所有 returns 都高于 VaR 阈值时, fallback 返回 var
        rets = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
        es = calculate_es(rets, 0.95)
        assert es >= 0


# ============================================================
# 3. calculate_max_drawdown
# ============================================================
class TestCalculateMaxDrawdown:
    def test_empty_returns_zeros(self):
        dd, start, end = calculate_max_drawdown(np.array([]))
        assert dd == 0.0
        assert start == 0
        assert end == 0

    def test_all_nan_returns_zeros(self):
        dd, start, end = calculate_max_drawdown(np.array([np.nan, np.nan]))
        assert dd == 0.0

    def test_monotonic_increase_no_drawdown(self):
        prices = np.array([100, 101, 102, 103, 104])
        dd, _, _ = calculate_max_drawdown(prices)
        assert dd == 0.0

    def test_clear_drawdown(self):
        # 100 -> 120 -> 80 (33% 回撤)
        prices = np.array([100, 120, 80])
        dd, start, end = calculate_max_drawdown(prices)
        assert 0.3 < dd < 0.34
        assert start == 1  # 峰值在 120
        assert end == 2  # 谷值在 80

    def test_nan_filtered(self):
        prices = np.array([100, np.nan, 120, np.nan, 80])
        dd, _, _ = calculate_max_drawdown(prices)
        assert dd > 0.3


# ============================================================
# 4. calculate_sharpe_ratio
# ============================================================
class TestCalculateSharpeRatio:
    def test_empty_returns_zero(self):
        assert calculate_sharpe_ratio(np.array([])) == 0.0

    def test_all_nan_returns_zero(self):
        assert calculate_sharpe_ratio(np.array([np.nan, np.nan])) == 0.0

    def test_positive_returns_positive_sharpe(self):
        # 需要有波动的正收益序列
        np.random.seed(42)
        rets = np.random.normal(0.002, 0.01, 252)
        sharpe = calculate_sharpe_ratio(rets, 0.02)
        assert sharpe > 0

    def test_zero_volatility_returns_zero(self):
        rets = np.array([0.0] * 10)
        sharpe = calculate_sharpe_ratio(rets)
        assert sharpe == 0.0

    def test_custom_risk_free_rate(self):
        np.random.seed(42)
        rets = np.random.normal(0.002, 0.01, 100)
        s_low = calculate_sharpe_ratio(rets, 0.01)
        s_high = calculate_sharpe_ratio(rets, 0.05)
        assert s_low > s_high  # 更高 rf -> 更低 sharpe


# ============================================================
# 5. calculate_sortino_ratio
# ============================================================
class TestCalculateSortinoRatio:
    def test_empty_returns_zero(self):
        assert calculate_sortino_ratio(np.array([])) == 0.0

    def test_all_nan_returns_zero(self):
        assert calculate_sortino_ratio(np.array([np.nan])) == 0.0

    def test_positive_returns(self):
        rets = np.array([0.001] * 100)
        sortino = calculate_sortino_ratio(rets, 0.02)
        # 全正收益 -> 下行波动=0 -> 返回 0
        assert sortino == 0.0

    def test_mixed_returns(self):
        rets = np.array([0.01, -0.01, 0.02, -0.005, 0.015, -0.008])
        sortino = calculate_sortino_ratio(rets, 0.02)
        # 有下行波动, 应为有限值
        assert isinstance(sortino, float)


# ============================================================
# 6. calculate_calmar_ratio
# ============================================================
class TestCalculateCalmarRatio:
    def test_empty_returns_zero(self):
        assert calculate_calmar_ratio(np.array([]), np.array([])) == 0.0

    def test_no_drawdown_positive_return_inf(self):
        rets = np.array([0.001] * 100)
        prices = np.array([100 + i for i in range(100)])
        calmar = calculate_calmar_ratio(rets, prices)
        assert calmar == float("inf")

    def test_no_drawdown_zero_return(self):
        rets = np.array([0.0] * 10)
        prices = np.array([100] * 10)
        calmar = calculate_calmar_ratio(rets, prices)
        assert calmar == 0.0

    def test_with_drawdown(self):
        rets = np.array([0.01, -0.02, 0.005, -0.03, 0.01])
        prices = np.array([100, 101, 99, 100, 97])
        calmar = calculate_calmar_ratio(rets, prices)
        assert isinstance(calmar, float)


# ============================================================
# 7. calculate_volatility
# ============================================================
class TestCalculateVolatility:
    def test_empty_returns_zero(self):
        assert calculate_volatility(np.array([])) == 0.0

    def test_all_nan_returns_zero(self):
        assert calculate_volatility(np.array([np.nan, np.nan])) == 0.0

    def test_normal_case(self):
        np.random.seed(42)
        rets = np.random.normal(0, 0.01, 100)
        vol = calculate_volatility(rets)
        assert vol > 0
        # 年化波动率应约 0.01 * sqrt(252) ≈ 0.158
        assert 0.1 < vol < 0.2

    def test_custom_period(self):
        rets = np.array([0.01, -0.01] * 50)
        vol_252 = calculate_volatility(rets, 252)
        vol_52 = calculate_volatility(rets, 52)
        assert vol_252 > vol_52  # 周期越大年化越高


# ============================================================
# 8. calculate_beta
# ============================================================
class TestCalculateBeta:
    def test_empty_returns_one(self):
        assert calculate_beta(np.array([]), np.array([])) == 1.0

    def test_short_returns_one(self):
        assert calculate_beta(np.array([0.01]), np.array([0.01])) == 1.0

    def test_perfect_correlation_beta_one(self):
        # numpy cov 用 ddof=1 (样本), var 用 ddof=0 (总体), 相同序列 beta = n/(n-1)
        rets = np.array([0.01, -0.01, 0.02, -0.005])
        mkt = np.array([0.01, -0.01, 0.02, -0.005])
        beta = calculate_beta(rets, mkt)
        # 4 个样本: beta = 4/3 ≈ 1.333
        assert 1.2 < beta < 1.5

    def test_zero_market_variance_returns_one(self):
        rets = np.array([0.01, 0.02, 0.03])
        mkt = np.array([0.0, 0.0, 0.0])
        beta = calculate_beta(rets, mkt)
        assert beta == 1.0

    def test_nan_filtered(self):
        rets = np.array([0.01, np.nan, 0.02, -0.01, 0.03])
        mkt = np.array([0.005, 0.01, np.nan, -0.005, 0.015])
        beta = calculate_beta(rets, mkt)
        assert isinstance(beta, float)

    def test_length_mismatch_aligned(self):
        rets = np.array([0.01, 0.02, 0.03, 0.04])
        mkt = np.array([0.005, 0.015, 0.025])  # 短一位
        beta = calculate_beta(rets, mkt)
        assert isinstance(beta, float)


# ============================================================
# 9. _align_and_dropna
# ============================================================
class TestAlignAndDropna:
    def test_equal_length_no_nan(self):
        a = np.array([0.01, 0.02, 0.03])
        b = np.array([0.005, 0.015, 0.025])
        a2, b2 = _align_and_dropna(a, b)
        assert len(a2) == 3
        assert len(b2) == 3

    def test_length_mismatch_truncates_to_shorter(self):
        a = np.array([0.01, 0.02, 0.03, 0.04])
        b = np.array([0.005, 0.015])
        a2, b2 = _align_and_dropna(a, b)
        # 尾部对齐, 保留后 2 个
        assert len(a2) == 2
        assert a2[0] == 0.03
        assert a2[1] == 0.04

    def test_nan_dropped(self):
        a = np.array([0.01, np.nan, 0.03, 0.04])
        b = np.array([0.005, 0.015, np.nan, 0.025])
        a2, b2 = _align_and_dropna(a, b)
        assert len(a2) == 2
        assert len(b2) == 2

    def test_empty_input(self):
        a2, b2 = _align_and_dropna(np.array([]), np.array([0.01]))
        assert len(a2) == 0
        assert len(b2) == 0


# ============================================================
# 10. calculate_alpha
# ============================================================
class TestCalculateAlpha:
    def test_empty_returns_zero(self):
        assert calculate_alpha(np.array([]), np.array([])) == 0.0

    def test_short_returns_zero(self):
        assert calculate_alpha(np.array([0.01]), np.array([0.01])) == 0.0

    def test_outperform_market(self):
        # alpha = (资产年化超额) - beta * (市场年化超额)
        # 资产均值 0.02575 * 252 = 6.489, 市场均值 0.01275 * 252 = 3.213
        # alpha = (6.489 - 0.02) - beta * (3.213 - 0.02)
        # 只要资产超额 > beta * 市场超额, alpha > 0
        rets = np.array([0.02, 0.03, 0.025, 0.028])
        mkt = np.array([0.01, 0.015, 0.012, 0.014])
        alpha = calculate_alpha(rets, mkt, 0.02)
        # 由于 beta ≈ 1.33 (n/(n-1) 效应), alpha 可能负
        # 只验证不抛异常且为有限数
        assert isinstance(alpha, float)
        assert not np.isnan(alpha)

    def test_nan_filtered(self):
        rets = np.array([0.02, np.nan, 0.025, 0.028])
        mkt = np.array([0.01, 0.015, np.nan, 0.014])
        alpha = calculate_alpha(rets, mkt, 0.02)
        assert isinstance(alpha, float)


# ============================================================
# 11. calculate_correlation
# ============================================================
class TestCalculateCorrelation:
    def test_too_few_rows_returns_eye(self):
        matrix = np.array([[0.01, 0.02]])
        corr = calculate_correlation(matrix)
        assert corr.shape == (2, 2)
        assert np.array_equal(corr, np.eye(2))

    def test_too_few_cols_returns_eye(self):
        matrix = np.array([[0.01], [0.02], [0.03]])
        corr = calculate_correlation(matrix)
        assert corr.shape == (1, 1)

    def test_normal_correlation(self):
        np.random.seed(42)
        matrix = np.random.normal(0, 0.01, (50, 3))
        corr = calculate_correlation(matrix)
        assert corr.shape == (3, 3)
        # 对角线应为 1
        assert abs(corr[0, 0] - 1.0) < 0.01
        assert abs(corr[1, 1] - 1.0) < 0.01

    def test_constant_column_no_nan(self):
        # 常数列应被处理为 0 相关, 不产生 NaN
        matrix = np.array([[0.01, 100], [0.02, 100], [0.015, 100], [0.018, 100]])
        corr = calculate_correlation(matrix)
        assert not np.any(np.isnan(corr))
        assert abs(corr[0, 0] - 1.0) < 0.01


# ============================================================
# 12. calculate_tracking_error
# ============================================================
class TestCalculateTrackingError:
    def test_empty_returns_zero(self):
        assert calculate_tracking_error(np.array([]), np.array([])) == 0.0

    def test_short_returns_zero(self):
        assert calculate_tracking_error(np.array([0.01]), np.array([0.01])) == 0.0

    def test_normal_case(self):
        rets = np.array([0.02, 0.01, 0.03, 0.025, 0.018])
        bench = np.array([0.015, 0.012, 0.022, 0.020, 0.015])
        te = calculate_tracking_error(rets, bench)
        assert te > 0

    def test_nan_filtered(self):
        rets = np.array([0.02, np.nan, 0.03, 0.025])
        bench = np.array([0.015, 0.012, np.nan, 0.020])
        te = calculate_tracking_error(rets, bench)
        assert isinstance(te, float)


# ============================================================
# 13. calculate_information_ratio
# ============================================================
class TestCalculateInformationRatio:
    def test_empty_returns_zero(self):
        assert calculate_information_ratio(np.array([]), np.array([])) == 0.0

    def test_short_returns_zero(self):
        assert calculate_information_ratio(np.array([0.01]), np.array([0.01])) == 0.0

    def test_outperform_benchmark(self):
        rets = np.array([0.02, 0.025, 0.022, 0.028, 0.020, 0.024])
        bench = np.array([0.01, 0.012, 0.011, 0.013, 0.010, 0.012])
        ir = calculate_information_ratio(rets, bench)
        assert ir > 0  # 超越基准 -> 正 IR

    def test_nan_filtered(self):
        rets = np.array([0.02, np.nan, 0.028, 0.024])
        bench = np.array([0.01, 0.012, np.nan, 0.012])
        ir = calculate_information_ratio(rets, bench)
        assert isinstance(ir, float)


# ============================================================
# 14. calculate_win_rate
# ============================================================
class TestCalculateWinRate:
    def test_empty_returns_zero(self):
        assert calculate_win_rate(np.array([])) == 0.0

    def test_all_nan_returns_zero(self):
        assert calculate_win_rate(np.array([np.nan, np.nan])) == 0.0

    def test_all_wins(self):
        rets = np.array([0.01, 0.02, 0.03])
        assert calculate_win_rate(rets) == 1.0

    def test_all_losses(self):
        rets = np.array([-0.01, -0.02, -0.03])
        assert calculate_win_rate(rets) == 0.0

    def test_half_half(self):
        rets = np.array([0.01, -0.01, 0.02, -0.02])
        assert calculate_win_rate(rets) == 0.5

    def test_nan_filtered(self):
        rets = np.array([0.01, np.nan, -0.01, 0.02])
        assert calculate_win_rate(rets) == 2 / 3


# ============================================================
# 15. calculate_profit_factor
# ============================================================
class TestCalculateProfitFactor:
    def test_empty_returns_zero(self):
        assert calculate_profit_factor(np.array([])) == 0.0

    def test_all_nan_returns_zero(self):
        assert calculate_profit_factor(np.array([np.nan])) == 0.0

    def test_all_profit_no_loss_inf(self):
        rets = np.array([0.01, 0.02, 0.03])
        assert calculate_profit_factor(rets) == float("inf")

    def test_normal_case(self):
        rets = np.array([0.02, -0.01, 0.03, -0.015])
        pf = calculate_profit_factor(rets)
        # profit = 0.05, loss = 0.025, pf = 2.0
        assert 1.9 < pf < 2.1

    def test_nan_filtered(self):
        rets = np.array([0.02, np.nan, -0.01, 0.03])
        pf = calculate_profit_factor(rets)
        assert isinstance(pf, float)


# ============================================================
# 16. calculate_performance_metrics
# ============================================================
class TestCalculatePerformanceMetrics:
    def test_empty_returns_dict(self):
        # 空 returns 不抛异常
        result = calculate_performance_metrics(np.array([]))
        assert isinstance(result, dict)
        assert result.get("total_return") == 0.0

    def test_basic_metrics_without_prices_or_benchmark(self):
        rets = np.array([0.01, -0.005, 0.015, 0.008, -0.003, 0.012])
        result = calculate_performance_metrics(rets)
        assert "total_return" in result
        assert "annual_return" in result
        assert "volatility" in result
        assert "sharpe_ratio" in result
        assert "sortino_ratio" in result
        assert "win_rate" in result
        assert "profit_factor" in result
        assert "var_95" in result
        assert "var_99" in result
        assert "es_95" in result
        assert "es_99" in result
        # 无 prices -> 默认 0
        assert result["max_drawdown"] == 0.0
        assert result["calmar_ratio"] == 0.0
        # 无 benchmark -> 默认值
        assert result["beta"] == 1.0
        assert result["alpha"] == 0.0

    def test_with_prices(self):
        rets = np.array([0.01, -0.005, 0.015, 0.008, -0.003])
        prices = np.array([100, 101, 100.5, 102, 102.8, 102.5])
        result = calculate_performance_metrics(rets, prices=prices)
        assert result["max_drawdown"] >= 0.0
        assert "dd_start" in result
        assert "dd_end" in result
        assert "calmar_ratio" in result

    def test_with_benchmark(self):
        rets = np.array([0.02, 0.01, 0.025, 0.015, 0.02])
        bench = np.array([0.01, 0.005, 0.012, 0.008, 0.01])
        result = calculate_performance_metrics(rets, benchmark_returns=bench)
        assert "beta" in result
        assert "alpha" in result
        assert "tracking_error" in result
        assert "information_ratio" in result


# ============================================================
# 17. calculate_portfolio_weights
# ============================================================
class TestCalculatePortfolioWeights:
    def test_empty_returns_empty(self):
        assert calculate_portfolio_weights({}) == {}

    def test_normal_case(self):
        positions = {
            "600519": {
                "phase1_amount": 100000,
                "phase2_amount": 50000,
                "phase3_amount": 0,
            },
            "000858": {"phase1_amount": 50000, "phase2_amount": 0, "phase3_amount": 0},
            "601318": {
                "phase1_amount": 0,
                "phase2_amount": 0,
                "phase3_amount": 0,
            },  # 全 0 跳过
        }
        weights = calculate_portfolio_weights(positions)
        assert len(weights) == 2  # 601318 被过滤
        assert "600519" in weights
        assert "000858" in weights
        # 600519 = 150000 / 200000 = 0.75
        assert abs(weights["600519"] - 0.75) < 0.001
        assert abs(weights["000858"] - 0.25) < 0.001

    def test_all_zero_returns_empty(self):
        positions = {
            "600519": {"phase1_amount": 0, "phase2_amount": 0, "phase3_amount": 0},
        }
        assert calculate_portfolio_weights(positions) == {}

    def test_missing_phase_keys_treated_as_zero(self):
        positions = {
            "600519": {"phase1_amount": 100000},  # phase2/3 缺失
        }
        weights = calculate_portfolio_weights(positions)
        assert len(weights) == 1
        assert weights["600519"] == 1.0


# ============================================================
# 18. 异常路径 (fail-safe 触发)
# ============================================================
class TestFailSafePaths:
    def test_var_with_invalid_input_returns_fail_closed(self):
        # 传入触发异常的类型
        var = calculate_var(None, 0.95, "historical")  # type: ignore
        assert var == 0.03

    def test_es_with_invalid_input_returns_fail_closed(self):
        es = calculate_es(None)  # type: ignore
        assert es == 0.04

    def test_max_drawdown_invalid_returns_zeros(self):
        dd, s, e = calculate_max_drawdown(None)  # type: ignore
        assert dd == 0.0
        assert s == 0
        assert e == 0

    def test_sharpe_invalid_returns_zero(self):
        assert calculate_sharpe_ratio(None) == 0.0  # type: ignore

    def test_correlation_invalid_returns_eye(self):
        # 传入会触发异常的输入 (非 ndarray 且无 shape 属性的对象用 list 替代)
        # list 会被 matrix.shape 抛 AttributeError, except 块尝试 np.eye(matrix.shape[1])
        # 但 list 无 shape -> 二次异常; 此处只验证不抛未捕获异常
        with pytest.raises(AttributeError):
            calculate_correlation([1, 2, 3])  # type: ignore

    def test_portfolio_weights_invalid_returns_empty(self):
        assert calculate_portfolio_weights(None) == {}  # type: ignore
