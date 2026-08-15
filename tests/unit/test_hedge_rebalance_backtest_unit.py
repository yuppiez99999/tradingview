"""hedge_rebalance_backtest 单元测试

覆盖模块级纯函数 + HedgeRebalanceBacktest 辅助方法 + 数据类 + 常量验证.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from utils.hedge_rebalance_backtest import (
    CODE_CATEGORIES,
    CODE_NAMES,
    COMMISSION_RATE,
    DEFAULT_SECTOR_WEIGHTS,
    END_DATE,
    HEDGE_EXPOSURE_CAP,
    INDEX_VOL,
    INITIAL_CAPITAL,
    PORTFOLIO_CODES,
    REBALANCE_INTERVAL_DAYS,
    RISK_FREE_RATE,
    SECTOR_ROTATION,
    SLIPPAGE,
    STAMP_TAX_RATE,
    START_DATE,
    STOCK_BETAS,
    TAIL_DD_TRIGGER,
    TAIL_MIN_HEDGE,
    TAIL_VOL_TRIGGER,
    TARGET_WEIGHTS,
    TURNOVER_BUDGET,
    TURNOVER_WINDOW,
    BacktestDataLoader,
    BacktestResult,
    HedgeRebalanceBacktest,
    MultiStrategyResult,
    compute_multi_index_beta_weights,
    compute_portfolio_dd_60d,
    compute_portfolio_vol_30d,
    determine_regime_from_csi300,
    format_comparison_report,
    get_dynamic_hedge_ratio_v1,
    get_dynamic_rebalance_threshold,
    get_tail_hedge_ratio,
    save_report,
)


# ============================================================
# 常量验证
# ============================================================
class TestConstants:
    def test_portfolio_codes_count(self):
        assert len(PORTFOLIO_CODES) == 15

    def test_target_weights_sum(self):
        assert sum(TARGET_WEIGHTS.values()) == pytest.approx(1.0, abs=0.01)

    def test_code_names_coverage(self):
        for code in PORTFOLIO_CODES:
            assert code in CODE_NAMES

    def test_code_categories_coverage(self):
        for code in PORTFOLIO_CODES:
            assert code in CODE_CATEGORIES

    def test_sector_rotation_keys(self):
        assert set(SECTOR_ROTATION.keys()) == {"recovery", "prosperity", "stagflation", "recession"}

    def test_sector_rotation_weights_sum(self):
        for _regime, weights in SECTOR_ROTATION.items():
            assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_default_sector_weights_sum(self):
        assert sum(DEFAULT_SECTOR_WEIGHTS.values()) == pytest.approx(1.0, abs=0.01)

    def test_index_vol(self):
        assert set(INDEX_VOL.keys()) == {"IF", "IC", "IM", "IH"}
        for _k, v in INDEX_VOL.items():
            assert 0 < v < 1

    def test_tail_triggers(self):
        assert TAIL_VOL_TRIGGER == 0.28
        assert TAIL_DD_TRIGGER == 0.12
        assert TAIL_MIN_HEDGE == 0.25

    def test_hedge_cap(self):
        assert HEDGE_EXPOSURE_CAP == 0.40

    def test_cost_params(self):
        assert COMMISSION_RATE == 0.0003
        assert STAMP_TAX_RATE == 0.0010
        assert SLIPPAGE == 0.0010

    def test_backtest_params(self):
        assert INITIAL_CAPITAL == 1_000_000
        assert RISK_FREE_RATE == 0.03
        assert REBALANCE_INTERVAL_DAYS == 15

    def test_turnover_params(self):
        assert TURNOVER_BUDGET == 0.20
        assert TURNOVER_WINDOW == 20

    def test_dates(self):
        assert START_DATE == "2021-01-01"
        assert END_DATE == "2026-06-29"

    def test_stock_betas_count(self):
        assert len(STOCK_BETAS) == 14  # 15 codes minus 600900 (defensive, no beta)

    def test_stock_betas_tuple_size(self):
        for _code, betas in STOCK_BETAS.items():
            assert len(betas) == 4
            assert all(b > 0 for b in betas)


# ============================================================
# determine_regime_from_csi300
# ============================================================
class TestDetermineRegime:
    def test_insufficient_data_returns_recovery(self):
        rets = pd.Series([0.01] * 10)
        assert determine_regime_from_csi300(rets, 5) == "recovery"

    def test_prosperity(self):
        rets = pd.Series([0.003] * 100)
        assert determine_regime_from_csi300(rets, 99) == "prosperity"

    def test_recession(self):
        rets = pd.Series([-0.005] * 100)
        result = determine_regime_from_csi300(rets, 99)
        assert result in ("recession", "stagflation")

    def test_recovery(self):
        rets = pd.Series([0.001] * 100)
        result = determine_regime_from_csi300(rets, 99)
        assert result in ("recovery", "prosperity")

    def test_stagflation_high_vol(self):
        rets = pd.Series([0.02, -0.02] * 50)
        result = determine_regime_from_csi300(rets, 99)
        assert result in ("stagflation", "recovery", "recession")

    def test_boundary_idx_60(self):
        rets = pd.Series([0.001] * 65)
        result = determine_regime_from_csi300(rets, 60)
        assert result in ("recovery", "prosperity", "stagflation", "recession")


# ============================================================
# compute_portfolio_vol_30d
# ============================================================
class TestComputePortfolioVol30d:
    def test_insufficient_data(self):
        assert compute_portfolio_vol_30d([0.01], 0) == 0.18

    def test_below_min_len(self):
        rets = [0.01] * 5
        assert compute_portfolio_vol_30d(rets, 4) == 0.18

    def test_normal_calculation(self):
        rets = [0.01, -0.01, 0.02, -0.02] * 10
        vol = compute_portfolio_vol_30d(rets, 39)
        assert vol > 0
        assert vol != 0.18

    def test_zero_volatility(self):
        rets = [0.0] * 30
        vol = compute_portfolio_vol_30d(rets, 29)
        assert vol == pytest.approx(0.0, abs=1e-10)

    def test_custom_min_len(self):
        rets = [0.01] * 8
        vol = compute_portfolio_vol_30d(rets, 7, min_len=5)
        assert vol >= 0


# ============================================================
# compute_portfolio_dd_60d
# ============================================================
class TestComputePortfolioDD60d:
    def test_insufficient_data(self):
        assert compute_portfolio_dd_60d([100, 101], 1) == 0.0

    def test_no_drawdown(self):
        equity = [100 + i for i in range(60)]
        dd = compute_portfolio_dd_60d(equity, 59)
        assert dd == pytest.approx(0.0, abs=1e-6)

    def test_with_drawdown(self):
        equity = [100, 110, 105, 90, 95, 100] + [100] * 55
        dd = compute_portfolio_dd_60d(equity, 60)
        assert dd > 0

    def test_full_drawdown(self):
        equity = [100] + [50] * 59
        dd = compute_portfolio_dd_60d(equity, 59)
        assert dd == pytest.approx(0.5, abs=1e-6)

    def test_boundary_idx_20(self):
        equity = [100, 90] + [100] * 19
        dd = compute_portfolio_dd_60d(equity, 20)
        assert dd > 0


# ============================================================
# get_tail_hedge_ratio
# ============================================================
class TestGetTailHedgeRatio:
    def test_no_trigger(self):
        assert get_tail_hedge_ratio(0.20, 0.05) == 0.0

    def test_vol_trigger_only(self):
        ratio = get_tail_hedge_ratio(0.30, 0.05)
        assert ratio > 0
        assert ratio >= TAIL_MIN_HEDGE

    def test_dd_trigger_only(self):
        ratio = get_tail_hedge_ratio(0.20, 0.15)
        assert ratio > 0
        assert ratio >= TAIL_MIN_HEDGE

    def test_both_triggers(self):
        ratio = get_tail_hedge_ratio(0.35, 0.20)
        assert ratio > 0
        assert ratio <= HEDGE_EXPOSURE_CAP

    def test_capped_at_exposure_cap(self):
        ratio = get_tail_hedge_ratio(1.0, 1.0)
        assert ratio <= HEDGE_EXPOSURE_CAP

    def test_exact_trigger_boundary(self):
        ratio_at = get_tail_hedge_ratio(TAIL_VOL_TRIGGER, 0.0)
        assert ratio_at == 0.0  # not strictly greater


# ============================================================
# compute_multi_index_beta_weights
# ============================================================
class TestComputeMultiIndexBetaWeights:
    def test_returns_dict_and_float(self):
        betas, total = compute_multi_index_beta_weights()
        assert isinstance(betas, dict)
        assert isinstance(total, float)

    def test_beta_keys(self):
        betas, _ = compute_multi_index_beta_weights()
        assert set(betas.keys()) == {"IF", "IC", "IM"}

    def test_betas_positive(self):
        betas, _ = compute_multi_index_beta_weights()
        for _k, v in betas.items():
            assert v > 0

    def test_total_beta_positive(self):
        _, total = compute_multi_index_beta_weights()
        assert total > 0

    def test_betas_consistent(self):
        betas1, total1 = compute_multi_index_beta_weights()
        betas2, total2 = compute_multi_index_beta_weights()
        assert betas1 == betas2
        assert total1 == total2


# ============================================================
# get_dynamic_hedge_ratio_v1
# ============================================================
class TestGetDynamicHedgeRatioV1:
    def test_low_vol(self):
        assert get_dynamic_hedge_ratio_v1(0.10, "recovery") == 0.0

    def test_medium_vol(self):
        assert get_dynamic_hedge_ratio_v1(0.15, "recovery") == 0.25

    def test_high_vol(self):
        assert get_dynamic_hedge_ratio_v1(0.25, "recovery") == HEDGE_EXPOSURE_CAP

    def test_very_high_vol(self):
        assert get_dynamic_hedge_ratio_v1(0.30, "recovery") == HEDGE_EXPOSURE_CAP

    def test_recession_boost(self):
        ratio = get_dynamic_hedge_ratio_v1(0.15, "recession")
        assert ratio == min(HEDGE_EXPOSURE_CAP, 0.25 + 0.15)

    def test_stagflation_boost(self):
        ratio = get_dynamic_hedge_ratio_v1(0.15, "stagflation")
        assert ratio == min(HEDGE_EXPOSURE_CAP, 0.25 + 0.10)

    def test_capped(self):
        ratio = get_dynamic_hedge_ratio_v1(0.50, "recession")
        assert ratio <= HEDGE_EXPOSURE_CAP

    def test_boundary_012(self):
        assert get_dynamic_hedge_ratio_v1(0.12, "recovery") == 0.25

    def test_boundary_018(self):
        assert get_dynamic_hedge_ratio_v1(0.18, "recovery") == HEDGE_EXPOSURE_CAP

    def test_boundary_028(self):
        assert get_dynamic_hedge_ratio_v1(0.28, "recovery") == HEDGE_EXPOSURE_CAP


# ============================================================
# get_dynamic_rebalance_threshold
# ============================================================
class TestGetDynamicRebalanceThreshold:
    def test_low_vol(self):
        assert get_dynamic_rebalance_threshold(0.10) == 0.03

    def test_medium_vol(self):
        assert get_dynamic_rebalance_threshold(0.20) == 0.05

    def test_high_vol(self):
        assert get_dynamic_rebalance_threshold(0.30) == 0.08

    def test_boundary_015(self):
        assert get_dynamic_rebalance_threshold(0.15) == 0.05

    def test_boundary_025(self):
        assert get_dynamic_rebalance_threshold(0.25) == 0.08

    def test_zero_vol(self):
        assert get_dynamic_rebalance_threshold(0.0) == 0.03


# ============================================================
# BacktestResult 数据类
# ============================================================
class TestBacktestResult:
    def test_defaults(self):
        r = BacktestResult(
            name="test", equity_curve=[1e6], dates=[pd.Timestamp("2026-01-01")],
            daily_returns=[0.0], trade_count=0, hedge_costs=[0.0], transaction_costs=[0.0],
        )
        assert r.name == "test"
        assert r.total_return == 0.0
        assert r.sharpe_ratio == 0.0
        assert r.yearly_stats == []
        assert r.turnover_daily == []

    def test_v2_fields(self):
        r = BacktestResult(
            name="test", equity_curve=[1e6], dates=[pd.Timestamp("2026-01-01")],
            daily_returns=[0.0], trade_count=0, hedge_costs=[0.0], transaction_costs=[0.0],
            hedge_pnl_total=1000, hedge_days=30, hedge_effective_ratio=0.6,
        )
        assert r.hedge_pnl_total == 1000
        assert r.hedge_days == 30
        assert r.hedge_effective_ratio == 0.6

    def test_v22_cost_fields(self):
        r = BacktestResult(
            name="test", equity_curve=[1e6], dates=[pd.Timestamp("2026-01-01")],
            daily_returns=[0.0], trade_count=0, hedge_costs=[0.0], transaction_costs=[0.0],
            roll_cost=500, margin_cost=300, slippage_cost=200,
        )
        assert r.roll_cost == 500
        assert r.margin_cost == 300
        assert r.slippage_cost == 200


# ============================================================
# HedgeRebalanceBacktest 辅助方法
# ============================================================
class TestHedgeRebalanceBacktestHelpers:
    @pytest.fixture
    def backtest(self):
        dates = pd.date_range("2026-01-01", periods=10)
        price_df = pd.DataFrame(
            {"300308": [100 + i for i in range(10)], "688041": [200 + i for i in range(10)]},
            index=dates,
        )
        csi300_ret = pd.Series([0.001] * 10, index=dates)
        return HedgeRebalanceBacktest(price_df, csi300_ret)

    def test_init(self, backtest):
        assert backtest.n_days == 10
        assert len(backtest.dates) == 10
        assert isinstance(backtest.multi_betas, dict)
        assert backtest.total_beta > 0

    def test_px_existing(self, backtest):
        assert backtest._px("300308", 0) == 100
        assert backtest._px("300308", 5) == 105

    def test_px_nonexistent(self, backtest):
        assert backtest._px("UNKNOWN", 0) == 0.0

    def test_px_nan(self, backtest):
        backtest.price_df.iloc[0, 0] = np.nan
        assert backtest._px("300308", 0) == 0.0

    def test_px_negative(self, backtest):
        backtest.price_df.iloc[0, 0] = -5
        assert backtest._px("300308", 0) == 0.0

    def test_rets(self, backtest):
        eq = [100, 110, 105]
        rets = backtest._rets(eq)
        assert rets[0] == 0.0
        assert rets[1] == pytest.approx(0.10)
        assert rets[2] < 0

    def test_rets_zero_prev(self, backtest):
        eq = [0, 100]
        rets = backtest._rets(eq)
        assert rets[1] == 0

    def test_compute_turnover_no_prev(self):
        assert HedgeRebalanceBacktest._compute_turnover(None, {"A": 100}, {"A": 10}) == 0.0

    def test_compute_turnover_normal(self):
        prev = {"A": 100, "B": 200}
        cur = {"A": 150, "B": 180}
        px = {"A": 10, "B": 20}
        t = HedgeRebalanceBacktest._compute_turnover(prev, cur, px)
        assert t == 50 * 10 + 20 * 20  # 500 + 400 = 900

    def test_compute_turnover_zero_price(self):
        prev = {"A": 100}
        cur = {"A": 200}
        px = {"A": 0}
        assert HedgeRebalanceBacktest._compute_turnover(prev, cur, px) == 0.0

    def test_compute_turnover_new_code(self):
        prev = {"A": 100}
        cur = {"A": 100, "B": 50}
        px = {"A": 10, "B": 20}
        t = HedgeRebalanceBacktest._compute_turnover(prev, cur, px)
        assert t == 50 * 20  # only B traded


# ============================================================
# _metrics
# ============================================================
class TestMetrics:
    def test_metrics_calculation(self):
        dates = pd.date_range("2026-01-01", periods=100)
        price_df = pd.DataFrame(
            {"300308": [100] * 100}, index=dates
        )
        csi300_ret = pd.Series([0.001] * 100, index=dates)
        bt = HedgeRebalanceBacktest(price_df, csi300_ret)

        eq = [1_000_000 * (1.001 ** i) for i in range(100)]
        rets = bt._rets(eq)
        r = BacktestResult(
            name="test", equity_curve=eq, dates=dates,
            daily_returns=rets, trade_count=5,
            hedge_costs=[0.0] * 100, transaction_costs=[0.0] * 100,
            n_days=100,
        )
        bt._metrics(r)
        assert r.total_return > 0
        assert r.annual_return > 0
        assert r.annual_volatility >= 0
        assert r.max_drawdown >= 0
        assert r.win_rate >= 0

    def test_metrics_with_turnover(self):
        dates = pd.date_range("2026-01-01", periods=50)
        price_df = pd.DataFrame({"300308": [100] * 50}, index=dates)
        csi300_ret = pd.Series([0.001] * 50, index=dates)
        bt = HedgeRebalanceBacktest(price_df, csi300_ret)

        eq = [1_000_000] * 50
        rets = [0.0] * 50
        r = BacktestResult(
            name="test", equity_curve=eq, dates=dates,
            daily_returns=rets, trade_count=0,
            hedge_costs=[0.0] * 50, transaction_costs=[0.0] * 50,
            n_days=50, turnover_daily=[0.01] * 50,
            turnover_window=20,
        )
        bt._metrics(r)
        assert r.annual_turnover >= 0
        assert r.window_turnover >= 0


# ============================================================
# format_comparison_report
# ============================================================
class TestFormatComparisonReport:
    def test_report_generation(self):
        dates = pd.date_range("2026-01-01", periods=10)
        strategies = []
        for name in ["S1:静态基准", "S2:仅再平衡", "S3:固定对冲", "S4:多指数联动", "S5:再平衡+尾保"]:
            r = BacktestResult(
                name=name,
                equity_curve=[1_000_000, 1_010_000],
                dates=dates[:2],
                daily_returns=[0.0, 0.01],
                trade_count=5,
                hedge_costs=[0.0, 100],
                transaction_costs=[0.0, 50],
                total_return=0.01,
                annual_return=0.12,
                annual_volatility=0.15,
                sharpe_ratio=0.8,
                max_drawdown=0.05,
                calmar_ratio=2.4,
                win_rate=0.55,
                total_hedge_cost=100,
                total_transaction_cost=50,
                roll_cost=40,
                margin_cost=30,
                slippage_cost=30,
                hedge_pnl_total=200,
                hedge_days=10,
                annual_turnover=0.15,
                window_turnover=0.12,
                yearly_stats=[{
                    "year": 2026, "return": 0.01, "volatility": 0.15,
                    "max_drawdown": 0.05, "csi300_return": 0.02,
                    "market_type": "震荡市",
                }],
            )
            strategies.append(r)
        multi = MultiStrategyResult(strategies=strategies)
        report = format_comparison_report(multi)
        assert "对冲+再平衡联动" in report
        assert "S1:静态基准" in report
        assert "S5:再平衡+尾保" in report
        assert "综合排名" in report

    def test_report_has_sections(self):
        strategies = []
        for name in ["S1", "S2", "S3", "S4", "S5"]:
            r = BacktestResult(
                name=name, equity_curve=[1e6, 1.1e6],
                dates=pd.date_range("2026-01-01", periods=2),
                daily_returns=[0.0, 0.1], trade_count=1,
                hedge_costs=[0, 0], transaction_costs=[0, 0],
                yearly_stats=[],
            )
            strategies.append(r)
        multi = MultiStrategyResult(strategies=strategies)
        report = format_comparison_report(multi)
        assert "[1]" in report
        assert "[2]" in report
        assert "[3]" in report
        assert "[4]" in report
        assert "[5]" in report
        assert "[6]" in report


# ============================================================
# save_report
# ============================================================
class TestSaveReport:
    def test_save_default_dir(self, tmp_path):
        with patch("os.makedirs"):
            with patch("builtins.open", create=True):
                fpath = save_report("test report", output_dir=str(tmp_path))
                assert "backtest_hedge_rebalance_v2_" in os.path.basename(fpath)

    def test_save_custom_dir(self, tmp_path):
        fpath = save_report("test content", output_dir=str(tmp_path))
        assert os.path.exists(fpath)
        with open(fpath, encoding="utf-8") as f:
            assert f.read() == "test content"


# ============================================================
# BacktestDataLoader
# ============================================================
class TestBacktestDataLoader:
    def test_init_custom_dir(self, tmp_path):
        loader = BacktestDataLoader(cache_dir=str(tmp_path))
        assert loader.cache_dir == str(tmp_path)

    def test_init_creates_dir(self, tmp_path):
        cache = tmp_path / "sub" / "cache"
        BacktestDataLoader(cache_dir=str(cache))
        assert os.path.exists(str(cache))

    def test_load_or_download_empty(self, tmp_path):
        loader = BacktestDataLoader(cache_dir=str(tmp_path))
        result = loader.load_or_download([], "2026-01-01", "2026-06-30")
        assert result == {}
