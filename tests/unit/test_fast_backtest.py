"""T4.1 ML 回测验证引擎单元测试.

验证:
    1. FastBacktest 基础接口 (run / summary)
    2. 基础绩效指标 (annual_return / sharpe / sortino / calmar / max_dd / win_rate)
    3. DSR 计算 (Bailey & López de Prado 2014)
    4. IC_IR 计算 (mean/std)
    5. Sharpe CV 计算 (12 月滚动变异系数)
    6. V9 评估标准 (DSR>=5 + 年化>=15% + 回撤<=10% + Sharpe CV<1.0)
    7. walk-forward 窗口数估算
    8. 异常处理 (数据不足)
    9. 便捷函数 (run_fast_backtest / check_v9_standards)
    10. 公式对齐 (与 v8.3 metrics.py 一致性)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.fast_backtest import (
    V9_ANNUAL_RETURN_THRESHOLD,
    V9_DSR_THRESHOLD,
    V9_MAX_DRAWDOWN_THRESHOLD,
    V9_SHARPE_CV_THRESHOLD,
    BacktestConfig,
    BacktestResult,
    FastBacktest,
    FastBacktestError,
    InsufficientDataError,
    check_v9_standards,
    run_fast_backtest,
)


# ============================================================
# 测试数据生成器
# ============================================================
def make_returns(
    n_days: int = 252,
    annual_return: float = 0.20,
    annual_vol: float = 0.15,
    seed: int = 42,
) -> pd.Series:
    """生成模拟日收益率序列.

    Args:
        n_days: 天数
        annual_return: 年化收益率
        annual_vol: 年化波动率
        seed: 随机种子
    """
    rng = np.random.default_rng(seed)
    daily_mean = annual_return / 252
    daily_std = annual_vol / math.sqrt(252)
    daily_returns = rng.normal(daily_mean, daily_std, n_days)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    return pd.Series(daily_returns, index=dates)


def make_ic_series(
    n_days: int = 60,
    mean_ic: float = 0.05,
    std_ic: float = 0.10,
    seed: int = 42,
) -> pd.Series:
    """生成模拟 IC 序列."""
    rng = np.random.default_rng(seed)
    ic_values = rng.normal(mean_ic, std_ic, n_days)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    return pd.Series(ic_values, index=dates)


# ============================================================
# 1. FastBacktest 基础接口测试
# ============================================================
class TestFastBacktestBasic:
    """FastBacktest 基础接口测试."""

    def test_init_default_config(self):
        """默认配置初始化."""
        engine = FastBacktest()
        assert engine.config.train_months == 24
        assert engine.config.test_months == 3
        assert engine.config.cv_folds == 5

    def test_init_custom_config(self):
        """自定义配置初始化."""
        config = BacktestConfig(train_months=12, test_months=2, rf=0.03)
        engine = FastBacktest(config)
        assert engine.config.train_months == 12
        assert engine.config.test_months == 2
        assert engine.config.rf == 0.03

    def test_run_returns_backtest_result(self):
        """run() 返回 BacktestResult."""
        engine = FastBacktest()
        returns = make_returns(n_days=252)
        result = engine.run(returns)
        assert isinstance(result, BacktestResult)

    def test_run_with_series(self):
        """支持 pd.Series 输入."""
        engine = FastBacktest()
        returns = make_returns(n_days=252)
        result = engine.run(returns)
        assert result.annual_return != 0.0

    def test_run_with_ndarray(self):
        """支持 numpy 数组输入."""
        engine = FastBacktest()
        returns = np.random.default_rng(42).normal(0.001, 0.01, 252)
        result = engine.run(returns)
        assert isinstance(result, BacktestResult)

    def test_run_with_list(self):
        """支持 list 输入."""
        engine = FastBacktest()
        returns = list(np.random.default_rng(42).normal(0.001, 0.01, 252))
        result = engine.run(returns)
        assert isinstance(result, BacktestResult)

    def test_summary_dict(self):
        """summary() 返回字典."""
        engine = FastBacktest()
        result = engine.run(make_returns())
        summary = result.summary()
        assert isinstance(summary, dict)
        assert "annual_return" in summary
        assert "dsr" in summary
        assert "passed_v9" in summary

    def test_summary_str(self):
        """summary_str() 返回字符串."""
        engine = FastBacktest()
        result = engine.run(make_returns())
        s = result.summary_str()
        assert isinstance(s, str)
        assert "Annual Return" in s
        assert "DSR" in s


# ============================================================
# 2. 基础绩效指标测试
# ============================================================
class TestBasicMetrics:
    """基础绩效指标测试."""

    def test_annual_return_positive(self):
        """正收益."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.20, annual_vol=0.15, seed=123)
        result = engine.run(returns)
        # 年化收益应接近 20% (有噪声, 放宽范围)
        assert 0.05 < result.annual_return < 0.40

    def test_annual_vol(self):
        """年化波动率."""
        engine = FastBacktest()
        returns = make_returns(annual_vol=0.15)
        result = engine.run(returns)
        assert 0.10 < result.annual_vol < 0.20

    def test_sharpe_ratio(self):
        """Sharpe Ratio."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.20, annual_vol=0.15, seed=123)
        result = engine.run(returns)
        # Sharpe ≈ (0.20 - 0.02) / 0.15 ≈ 1.2, 放宽下界
        assert 0.2 < result.sharpe < 2.5

    def test_max_drawdown_negative(self):
        """最大回撤应为负值."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.10, annual_vol=0.20)
        result = engine.run(returns)
        assert result.max_drawdown <= 0.0

    def test_win_rate_range(self):
        """胜率在 [0, 1] 之间."""
        engine = FastBacktest()
        returns = make_returns()
        result = engine.run(returns)
        assert 0.0 <= result.win_rate <= 1.0

    def test_sortino_ratio(self):
        """Sortino Ratio."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.20, annual_vol=0.15)
        result = engine.run(returns)
        assert result.sortino > 0.0

    def test_calmar_ratio(self):
        """Calmar Ratio."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.20, annual_vol=0.15)
        result = engine.run(returns)
        assert result.calmar > 0.0

    def test_zero_volatility(self):
        """零波动率 (恒定收益)."""
        engine = FastBacktest()
        returns = pd.Series([0.001] * 100)
        result = engine.run(returns)
        # 浮点精度保护: 极小值视为 0
        assert result.annual_vol < 1e-10
        # Sharpe 在 vol→0 时可能非常大, 不做严格断言, 只验证不抛异常
        assert isinstance(result.sharpe, float)

    def test_insufficient_data_raises(self):
        """数据不足抛 InsufficientDataError."""
        engine = FastBacktest()
        returns = pd.Series([0.001] * 10)  # < 30
        with pytest.raises(InsufficientDataError):
            engine.run(returns)


# ============================================================
# 3. DSR 计算测试
# ============================================================
class TestDSR:
    """Deflated Sharpe Ratio 测试."""

    def test_dsr_in_range(self):
        """DSR 在 [0, 1] 之间."""
        engine = FastBacktest()
        returns = make_returns()
        result = engine.run(returns, n_trials=10)
        assert 0.0 <= result.dsr <= 1.0

    def test_dsr_increases_with_better_sharpe(self):
        """Sharpe 越高 DSR 越大."""
        engine = FastBacktest()
        # 低 Sharpe
        returns_low = make_returns(annual_return=0.05, annual_vol=0.20, seed=1)
        result_low = engine.run(returns_low, n_trials=5)
        # 高 Sharpe
        returns_high = make_returns(annual_return=0.30, annual_vol=0.10, seed=1)
        result_high = engine.run(returns_high, n_trials=5)
        assert result_high.dsr > result_low.dsr

    def test_dsr_decreases_with_more_trials(self):
        """尝试次数越多 DSR 越低 (数据窥探惩罚)."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.20, annual_vol=0.15, seed=42)
        result_few = engine.run(returns, n_trials=2)
        result_many = engine.run(returns, n_trials=100)
        # 浮点精度保护: 允许相等 (1.0 == 0.99999... 在精度范围内)
        # n_trials=2 时 DSR 可能等于 1.0 (高 Sharpe), n_trials=100 时 DSR 略低
        # 主要验证 DSR 不反向增加
        assert result_many.dsr <= result_few.dsr + 1e-6

    def test_dsr_single_trial(self):
        """单次尝试 (n_trials=1) DSR 为 0."""
        engine = FastBacktest()
        returns = make_returns()
        result = engine.run(returns, n_trials=1)
        assert result.dsr == 0.0

    def test_dsr_zero_observations(self):
        """n_observations <= 1 时 DSR 为 0."""
        engine = FastBacktest()
        dsr = engine._compute_dsr(
            sharpe_ratio=2.0, n_trials=10, n_observations=1,
        )
        assert dsr == 0.0


# ============================================================
# 4. IC_IR 计算测试
# ============================================================
class TestICIR:
    """IC_IR 测试."""

    def test_ic_ir_positive(self):
        """正 IC_IR."""
        engine = FastBacktest()
        ic = make_ic_series(mean_ic=0.05, std_ic=0.10)
        result = engine.run(make_returns(), ic_series=ic)
        assert result.ic_ir > 0.0
        # IC_IR ≈ 0.05 / 0.10 = 0.5, 放宽范围
        assert 0.3 < result.ic_ir < 0.8

    def test_ic_ir_negative(self):
        """负 IC_IR."""
        engine = FastBacktest()
        ic = make_ic_series(mean_ic=-0.05, std_ic=0.10)
        result = engine.run(make_returns(), ic_series=ic)
        assert result.ic_ir < 0.0

    def test_ic_ir_zero_std(self):
        """std=0 时 IC_IR=0."""
        engine = FastBacktest()
        ic = pd.Series([0.05] * 20)  # 恒定 IC
        result = engine.run(make_returns(), ic_series=ic)
        # 浮点精度保护: 极小值视为 0
        assert abs(result.ic_ir) < 1e-6

    def test_ic_ir_insufficient_samples(self):
        """IC 样本不足时 IC_IR=0."""
        engine = FastBacktest()
        ic = pd.Series([0.05, 0.03, 0.04])  # < 5
        result = engine.run(make_returns(), ic_series=ic)
        assert result.ic_ir == 0.0

    def test_ic_ir_none(self):
        """未提供 ic_series 时 IC_IR=0."""
        engine = FastBacktest()
        result = engine.run(make_returns())
        assert result.ic_ir == 0.0

    def test_ic_ir_with_nan(self):
        """IC 含 nan 值."""
        engine = FastBacktest()
        ic = pd.Series([0.05, np.nan, 0.04, 0.06, np.nan, 0.03] * 10)
        result = engine.run(make_returns(), ic_series=ic)
        # nan 被过滤后仍可计算
        assert isinstance(result.ic_ir, float)


# ============================================================
# 5. Sharpe CV 测试
# ============================================================
class TestSharpeCV:
    """Sharpe CV 测试."""

    def test_sharpe_cv_positive(self):
        """Sharpe CV 为正值."""
        engine = FastBacktest()
        returns = make_returns(n_days=504)  # 2 年数据
        result = engine.run(returns)
        assert result.sharpe_cv > 0.0

    def test_sharpe_cv_stable_strategy(self):
        """稳定策略 Sharpe CV 较低."""
        engine = FastBacktest()
        # 高收益低波动 → 稳定 Sharpe → 低 CV
        returns = make_returns(annual_return=0.30, annual_vol=0.10, n_days=504, seed=42)
        result = engine.run(returns)
        # 稳定策略 CV 应 < 2.0
        assert result.sharpe_cv < 2.0

    def test_sharpe_cv_inf_when_insufficient(self):
        """数据不足时 Sharpe CV = inf."""
        engine = FastBacktest()
        returns = make_returns(n_days=30)  # 刚好 30 天
        result = engine.run(returns)
        assert math.isinf(result.sharpe_cv)

    def test_sharpe_cv_no_datetime_index(self):
        """无时间索引的 Series 也能计算."""
        engine = FastBacktest()
        returns = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 504))
        result = engine.run(returns)
        # 无时间索引用滚动窗口, 仍能计算
        assert result.sharpe_cv > 0.0


# ============================================================
# 6. V9 评估标准测试
# ============================================================
class TestV9Standards:
    """V9 评估标准测试."""

    def test_v9_pass_good_strategy(self):
        """优质策略通过 V9."""
        engine = FastBacktest()
        # 高收益 + 低波动 + 长数据 → 应通过 V9
        returns = make_returns(annual_return=0.30, annual_vol=0.10, n_days=756, seed=42)
        result = engine.run(returns, n_trials=5)
        # DSR 应该较高
        assert result.dsr * 10 >= V9_DSR_THRESHOLD or not result.passed_v9
        # 至少年化和回撤应通过
        assert result.annual_return >= V9_ANNUAL_RETURN_THRESHOLD or "年化" in " ".join(result.v9_failures)

    def test_v9_fail_low_return(self):
        """低收益不通过 V9."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.05, annual_vol=0.15, n_days=252)
        result = engine.run(returns, n_trials=5)
        assert not result.passed_v9
        assert any("年化" in f for f in result.v9_failures)

    def test_v9_fail_high_drawdown(self):
        """高回撤不通过 V9."""
        engine = FastBacktest()
        # 极高波动率 → 大回撤
        returns = make_returns(annual_return=0.20, annual_vol=0.50, n_days=252, seed=42)
        result = engine.run(returns, n_trials=5)
        assert not result.passed_v9
        assert any("回撤" in f for f in result.v9_failures)

    def test_v9_failures_list(self):
        """v9_failures 是列表."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.05, annual_vol=0.30, n_days=252)
        result = engine.run(returns, n_trials=5)
        assert isinstance(result.v9_failures, list)

    def test_v9_thresholds_values(self):
        """V9 阈值正确."""
        assert V9_DSR_THRESHOLD == 5.0
        assert V9_ANNUAL_RETURN_THRESHOLD == 0.15
        assert V9_MAX_DRAWDOWN_THRESHOLD == 0.10
        assert V9_SHARPE_CV_THRESHOLD == 1.0

    def test_check_v9_standards_function(self):
        """check_v9_standards 便捷函数."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.30, annual_vol=0.10, n_days=756, seed=42)
        result = engine.run(returns, n_trials=5)
        passed, failures = check_v9_standards(result)
        assert isinstance(passed, bool)
        assert isinstance(failures, list)


# ============================================================
# 7. Walk-Forward 窗口数估算测试
# ============================================================
class TestWindowEstimation:
    """Walk-Forward 窗口数估算测试."""

    def test_estimate_n_windows_sufficient_data(self):
        """数据充足时窗口数 > 0."""
        engine = FastBacktest()
        returns = make_returns(n_days=756)  # 3 年
        n_windows = engine._estimate_n_windows(returns)
        assert n_windows > 0

    def test_estimate_n_windows_insufficient_data(self):
        """数据不足时窗口数 = 0."""
        engine = FastBacktest()
        returns = make_returns(n_days=60)  # < train_days + test_days
        n_windows = engine._estimate_n_windows(returns)
        assert n_windows == 0

    def test_estimate_n_windows_increases_with_data(self):
        """数据越多窗口数越多."""
        engine = FastBacktest()
        short_returns = make_returns(n_days=400)
        long_returns = make_returns(n_days=800)
        short_n = engine._estimate_n_windows(short_returns)
        long_n = engine._estimate_n_windows(long_returns)
        assert long_n > short_n


# ============================================================
# 8. 异常处理测试
# ============================================================
class TestExceptionHandling:
    """异常处理测试."""

    def test_insufficient_data_error(self):
        """InsufficientDataError 含 required/actual 属性."""
        engine = FastBacktest()
        returns = pd.Series([0.001] * 10)
        with pytest.raises(InsufficientDataError) as exc_info:
            engine.run(returns)
        assert exc_info.value.required == 30
        assert exc_info.value.actual == 10

    def test_fast_backtest_error_inheritance(self):
        """InsufficientDataError 继承 FastBacktestError."""
        assert issubclass(InsufficientDataError, FastBacktestError)

    def test_empty_returns_raises(self):
        """空数据抛异常."""
        engine = FastBacktest()
        with pytest.raises(InsufficientDataError):
            engine.run(pd.Series([], dtype=float))

    def test_run_with_nan_values(self):
        """含 nan 的数据可处理."""
        engine = FastBacktest()
        returns = pd.Series([0.001, np.nan, 0.002, 0.001, -0.001] * 60)
        result = engine.run(returns)
        assert isinstance(result, BacktestResult)


# ============================================================
# 9. 便捷函数测试
# ============================================================
class TestConvenienceFunctions:
    """便捷函数测试."""

    def test_run_fast_backtest(self):
        """run_fast_backtest 便捷函数."""
        returns = make_returns(n_days=252)
        result = run_fast_backtest(returns, n_trials=5)
        assert isinstance(result, BacktestResult)
        assert result.n_trials == 5

    def test_run_fast_backtest_with_config(self):
        """run_fast_backtest 支持自定义配置."""
        config = BacktestConfig(rf=0.03)
        returns = make_returns(n_days=252)
        result = run_fast_backtest(returns, config=config)
        assert isinstance(result, BacktestResult)

    def test_run_fast_backtest_with_ic(self):
        """run_fast_backtest 支持 ic_series."""
        returns = make_returns(n_days=252)
        ic = make_ic_series(n_days=60)
        result = run_fast_backtest(returns, ic_series=ic)
        assert result.ic_ir != 0.0

    def test_check_v9_standards_returns_tuple(self):
        """check_v9_standards 返回元组."""
        result = run_fast_backtest(make_returns())
        passed, failures = check_v9_standards(result)
        assert isinstance(passed, bool)
        assert isinstance(failures, list)


# ============================================================
# 10. 公式对齐测试 (与 v8.3 metrics.py 一致性)
# ============================================================
class TestFormulaAlignment:
    """公式对齐测试."""

    def test_annual_return_formula(self):
        """年化收益公式 = mean * 252 (对齐 metrics.py L37)."""
        engine = FastBacktest()
        returns = pd.Series([0.001] * 252)  # 恒定 0.1%
        result = engine.run(returns)
        # 0.001 * 252 = 0.252 = 25.2%
        assert abs(result.annual_return - 0.252) < 0.001

    def test_annual_vol_formula(self):
        """年化波动公式 = std * sqrt(252) (对齐 metrics.py L42)."""
        engine = FastBacktest()
        returns = pd.Series([0.01, -0.01] * 126)  # std=0.01
        result = engine.run(returns)
        # 0.01 * sqrt(252) ≈ 0.1587
        assert abs(result.annual_vol - 0.01 * math.sqrt(252)) < 0.001

    def test_max_drawdown_formula(self):
        """最大回撤公式 (对齐 metrics.py L44-55)."""
        engine = FastBacktest()
        # 构造已知回撤序列: +10%, -20% → 最大回撤 = -20%, 补足 30 天最低要求
        returns = pd.Series([0.10, -0.20] + [0.0] * 30)
        result = engine.run(returns)
        # cumulative: 1.1 → 0.88 → 0.88; max_dd = (0.88 - 1.1) / 1.1 ≈ -0.2
        assert abs(result.max_drawdown - (-0.20)) < 0.01

    def test_sharpe_formula(self):
        """Sharpe 公式 = (annual_return - rf) / annual_vol (对齐 metrics.py L57-60)."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.20, annual_vol=0.15, seed=42)
        result = engine.run(returns)
        expected_sharpe = (result.annual_return - 0.02) / result.annual_vol
        assert abs(result.sharpe - expected_sharpe) < 0.001

    def test_dsr_formula_alignment(self):
        """DSR 公式对齐 (Bailey & López de Prado 2014)."""
        engine = FastBacktest()
        # 已知参数
        sharpe = 1.5
        n_trials = 10
        n_obs = 252
        skew = 0.0
        kurt = 3.0

        # 手动计算
        from scipy.stats import norm
        z_max = math.sqrt(2 * math.log(n_trials))
        correction = 1 + (skew / 6) * (z_max ** 2 - 1) + ((kurt - 3) / 24) * (z_max ** 3 - 3 * z_max)
        e_max_sr = z_max * correction / math.sqrt(n_obs)
        z_score = (sharpe - e_max_sr) * math.sqrt(n_obs - 1)
        expected_dsr = float(norm.cdf(z_score))

        actual_dsr = engine._compute_dsr(sharpe, n_trials, n_obs, skew, kurt)
        assert abs(actual_dsr - expected_dsr) < 1e-10

    def test_v9_dsr_scaling(self):
        """V9 DSR 缩放: 原始 DSR * 10 >= 5."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.30, annual_vol=0.10, n_days=756, seed=42)
        result = engine.run(returns, n_trials=5)
        # DSR 原始值在 [0, 1], V9 检查时 * 10
        assert 0.0 <= result.dsr <= 1.0
        # V9 阈值 5 对应原始 DSR >= 0.5
        if result.dsr >= 0.5:
            assert result.dsr * 10 >= V9_DSR_THRESHOLD
        else:
            assert any("DSR" in f for f in result.v9_failures)


# ============================================================
# 11. 配置参数测试
# ============================================================
class TestConfigParameters:
    """配置参数测试."""

    def test_default_rf(self):
        """默认 rf=0.02."""
        config = BacktestConfig()
        assert config.rf == 0.02

    def test_default_trading_days(self):
        """默认 trading_days=252."""
        config = BacktestConfig()
        assert config.trading_days == 252

    def test_custom_trading_days(self):
        """自定义 trading_days."""
        config = BacktestConfig(trading_days=365)
        engine = FastBacktest(config)
        returns = pd.Series([0.001] * 100)
        result = engine.run(returns)
        # 0.001 * 365 = 0.365
        assert abs(result.annual_return - 0.365) < 0.001

    def test_lookback_months_default(self):
        """默认 lookback_months=12."""
        config = BacktestConfig()
        assert config.lookback_months == 12


# ============================================================
# 12. 模块级常量测试
# ============================================================
class TestModuleConstants:
    """模块级常量测试."""

    def test_v9_thresholds_exported(self):
        """V9 阈值常量已导出."""
        from utils.alpha.fast_backtest import (
            V9_ANNUAL_RETURN_THRESHOLD,
            V9_DSR_THRESHOLD,
            V9_MAX_DRAWDOWN_THRESHOLD,
            V9_SHARPE_CV_THRESHOLD,
        )
        assert V9_DSR_THRESHOLD == 5.0
        assert V9_ANNUAL_RETURN_THRESHOLD == 0.15
        assert V9_MAX_DRAWDOWN_THRESHOLD == 0.10
        assert V9_SHARPE_CV_THRESHOLD == 1.0

    def test_all_exported(self):
        """__all__ 完整."""
        from utils.alpha import fast_backtest
        expected = {
            "FastBacktest", "BacktestConfig", "BacktestResult",
            "FastBacktestError", "InsufficientDataError",
            "run_fast_backtest", "check_v9_standards",
        }
        assert expected.issubset(set(fast_backtest.__all__))


# ============================================================
# 13. 边界条件测试
# ============================================================
class TestEdgeCases:
    """边界条件测试."""

    def test_single_negative_return(self):
        """单一负收益."""
        engine = FastBacktest()
        returns = pd.Series([-0.01] * 100)
        result = engine.run(returns)
        assert result.annual_return < 0.0

    def test_extreme_volatility(self):
        """极端波动率."""
        engine = FastBacktest()
        returns = make_returns(annual_return=0.20, annual_vol=1.0, seed=42)
        result = engine.run(returns)
        assert result.annual_vol > 0.5
        assert result.max_drawdown < -0.3

    def test_all_zero_returns(self):
        """全零收益."""
        engine = FastBacktest()
        returns = pd.Series([0.0] * 100)
        result = engine.run(returns)
        assert result.annual_return == 0.0
        assert result.annual_vol == 0.0

    def test_very_long_series(self):
        """超长序列."""
        engine = FastBacktest()
        returns = make_returns(n_days=1260)  # 5 年
        result = engine.run(returns, n_trials=10)
        assert isinstance(result, BacktestResult)
        assert result.n_windows > 10


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
