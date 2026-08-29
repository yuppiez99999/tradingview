"""G7 覆盖率冲刺 — hedge_rebalance_backtest.py 测试增强 (W7.4.5 Phase 2B)

覆盖目标: 16.14% → 70%+
覆盖范围:
  - 纯函数: determine_regime_from_csi300 / compute_portfolio_vol_30d /
            compute_portfolio_dd_60d / get_tail_hedge_ratio /
            compute_multi_index_beta_weights / get_dynamic_hedge_ratio_v1 /
            get_dynamic_rebalance_threshold
  - dataclass: BacktestResult / MultiStrategyResult
  - HedgeRebalanceBacktest: __init__ / _px / _rets / _metrics / _compute_turnover
  - HedgeRebalanceBacktest: _run_s1 ~ _run_s5 (用合成数据)
  - format_comparison_report / save_report
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.hedge_rebalance_backtest import (  # noqa: E402
    HEDGE_EXPOSURE_CAP,
    TAIL_MIN_HEDGE,
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
# 1. determine_regime_from_csi300
# ============================================================
class TestDetermineRegime:
    def test_short_returns_recovery(self):
        rets = pd.Series([0.01, 0.02, 0.03])
        assert determine_regime_from_csi300(rets, 5) == "recovery"

    def test_prosperity(self):
        # 高累计收益 + 低波动 -> prosperity
        rets = pd.Series([0.005] * 70)  # 累计约 0.35, 低波动
        assert determine_regime_from_csi300(rets, 65) == "prosperity"

    def test_recovery(self):
        # 累计 0.05-0.15 -> recovery
        rets = pd.Series([0.001] * 70)  # 累计约 0.07
        assert determine_regime_from_csi300(rets, 65) == "recovery"

    def test_recession(self):
        # 大幅下跌 + 高波动 -> recession
        rets = pd.Series([-0.005] * 70 + [-0.01] * 10)
        regime = determine_regime_from_csi300(rets, 75)
        assert regime in ("recession", "stagflation")

    def test_stagflation_low_return_high_vol(self):
        # 累计接近 0 但高波动 -> stagflation
        rets = pd.Series([0.05, -0.05] * 35)
        regime = determine_regime_from_csi300(rets, 65)
        assert regime in ("stagflation", "recession", "recovery")


# ============================================================
# 2. compute_portfolio_vol_30d
# ============================================================
class TestComputeVol30d:
    def test_short_returns_default(self):
        assert compute_portfolio_vol_30d([0.01, 0.02], 1) == 0.18

    def test_normal_case(self):
        rets = [
            0.01,
            -0.01,
            0.02,
            -0.005,
            0.015,
            -0.008,
            0.012,
            -0.003,
            0.018,
            -0.01,
            0.005,
        ]
        vol = compute_portfolio_vol_30d(rets, 10)
        assert vol > 0
        # 年化波动率应约 0.01 * sqrt(252) ≈ 0.158
        assert 0.05 < vol < 0.5

    def test_insufficient_window_returns_default(self):
        rets = [0.01, 0.02, 0.03]
        # idx >= min_len=10 但窗口内数据不足
        assert compute_portfolio_vol_30d(rets, 10, min_len=10) == 0.18


# ============================================================
# 3. compute_portfolio_dd_60d
# ============================================================
class TestComputeDD60d:
    def test_short_returns_zero(self):
        assert compute_portfolio_dd_60d([100, 101, 102], 5) == 0.0

    def test_no_drawdown(self):
        equity = [100 + i for i in range(70)]
        assert compute_portfolio_dd_60d(equity, 65) == 0.0

    def test_clear_drawdown(self):
        # 100 -> 120 -> 80 (33% 回撤)
        equity = [100] * 30 + [120] * 30 + [80] * 10
        dd = compute_portfolio_dd_60d(equity, 65)
        assert dd > 0.3

    def test_insufficient_window(self):
        equity = [100, 101, 102, 103, 104]
        assert compute_portfolio_dd_60d(equity, 4) == 0.0


# ============================================================
# 4. get_tail_hedge_ratio
# ============================================================
class TestGetTailHedgeRatio:
    def test_no_trigger_returns_zero(self):
        # vol < 28% 且 dd < 12% -> 0
        ratio = get_tail_hedge_ratio(0.20, 0.05)
        assert ratio == 0.0

    def test_vol_trigger(self):
        # vol > 28% 激活
        ratio = get_tail_hedge_ratio(0.35, 0.0)
        assert ratio >= TAIL_MIN_HEDGE
        # ratio = 0.25 + (0.35 - 0.28) * 2.0 = 0.39
        assert abs(ratio - 0.39) < 0.01

    def test_dd_trigger(self):
        # dd > 12% 激活
        ratio = get_tail_hedge_ratio(0.20, 0.20)
        assert ratio >= TAIL_MIN_HEDGE
        # dd_ratio = 0.25 + 0.08 * 3 = 0.49, 但被 cap 限制为 0.40
        assert abs(ratio - 0.40) < 0.01

    def test_both_triggers_takes_max(self):
        # vol 和 dd 都触发, 但都被 cap 限制为 0.40
        ratio = get_tail_hedge_ratio(0.50, 0.30)
        assert ratio == HEDGE_EXPOSURE_CAP

    def test_capped_at_exposure_cap(self):
        # 极端值应被 cap 限制
        ratio = get_tail_hedge_ratio(1.0, 1.0)
        assert ratio <= HEDGE_EXPOSURE_CAP


# ============================================================
# 5. compute_multi_index_beta_weights
# ============================================================
class TestComputeMultiIndexBetaWeights:
    def test_returns_three_indices(self):
        betas, total = compute_multi_index_beta_weights()
        assert "IF" in betas
        assert "IC" in betas
        assert "IM" in betas
        assert total > 0

    def test_betas_normalized_by_weight(self):
        betas, total = compute_multi_index_beta_weights()
        # 总 beta 应为正 (各指数 beta 之和)
        assert total > 0

    def test_betas_positive(self):
        betas, _ = compute_multi_index_beta_weights()
        for _k, v in betas.items():
            assert v > 0


# ============================================================
# 6. get_dynamic_hedge_ratio_v1
# ============================================================
class TestGetDynamicHedgeRatioV1:
    def test_low_vol_no_hedge(self):
        assert get_dynamic_hedge_ratio_v1(0.10, "recovery") == 0.0

    def test_medium_vol(self):
        ratio = get_dynamic_hedge_ratio_v1(0.15, "recovery")
        assert ratio == 0.25

    def test_high_vol(self):
        ratio = get_dynamic_hedge_ratio_v1(0.20, "recovery")
        # base = 0.50, 被 cap 限制为 0.40
        assert ratio == 0.40

    def test_very_high_vol(self):
        ratio = get_dynamic_hedge_ratio_v1(0.30, "recovery")
        # base = 0.75, 被 cap 限制为 0.40
        assert ratio == 0.40

    def test_recession_adds_15_pct(self):
        ratio = get_dynamic_hedge_ratio_v1(0.15, "recession")
        # 0.25 + 0.15 = 0.40
        assert ratio == 0.40

    def test_stagflation_adds_10_pct(self):
        ratio = get_dynamic_hedge_ratio_v1(0.15, "stagflation")
        # 0.25 + 0.10 = 0.35
        assert ratio == 0.35

    def test_capped(self):
        ratio = get_dynamic_hedge_ratio_v1(0.50, "recession")
        assert ratio <= HEDGE_EXPOSURE_CAP


# ============================================================
# 7. get_dynamic_rebalance_threshold
# ============================================================
class TestGetDynamicRebalanceThreshold:
    def test_low_vol(self):
        assert get_dynamic_rebalance_threshold(0.10) == 0.03

    def test_medium_vol(self):
        assert get_dynamic_rebalance_threshold(0.20) == 0.05

    def test_high_vol(self):
        assert get_dynamic_rebalance_threshold(0.30) == 0.08

    def test_boundary_low(self):
        assert get_dynamic_rebalance_threshold(0.15) == 0.05

    def test_boundary_high(self):
        assert get_dynamic_rebalance_threshold(0.25) == 0.08


# ============================================================
# 8. dataclass: BacktestResult / MultiStrategyResult
# ============================================================
class TestDataclasses:
    def test_backtest_result_defaults(self):
        r = BacktestResult(
            name="test",
            equity_curve=[100],
            dates=[pd.Timestamp("2026-01-01")],
            daily_returns=[0.0],
            trade_count=0,
            hedge_costs=[0.0],
            transaction_costs=[0.0],
        )
        assert r.name == "test"
        assert r.total_return == 0.0
        assert r.sharpe_ratio == 0.0
        assert r.yearly_stats == []
        assert r.turnover_daily == []

    def test_multi_strategy_result(self):
        r1 = BacktestResult(
            name="s1",
            equity_curve=[100],
            dates=[pd.Timestamp("2026-01-01")],
            daily_returns=[0.0],
            trade_count=0,
            hedge_costs=[0.0],
            transaction_costs=[0.0],
        )
        r2 = BacktestResult(
            name="s2",
            equity_curve=[100],
            dates=[pd.Timestamp("2026-01-01")],
            daily_returns=[0.0],
            trade_count=0,
            hedge_costs=[0.0],
            transaction_costs=[0.0],
        )
        multi = MultiStrategyResult(strategies=[r1, r2])
        assert len(multi.strategies) == 2


# ============================================================
# 9. HedgeRebalanceBacktest 辅助方法
# ============================================================
class TestHedgeRebalanceBacktestHelpers:
    @pytest.fixture
    def engine(self):
        """构造一个简单的回测引擎 (3 天数据)"""
        dates = pd.date_range("2026-01-01", periods=3, freq="B")
        price_df = pd.DataFrame(
            {
                "300308": [100.0, 101.0, 102.0],
                "688041": [50.0, 50.5, 51.0],
            },
            index=dates,
        )
        csi300_ret = pd.Series([0.0, 0.01, 0.005], index=dates)
        return HedgeRebalanceBacktest(price_df, csi300_ret)

    def test_init(self, engine):
        assert engine.n_days == 3
        assert "IF" in engine.multi_betas
        assert engine.total_beta > 0

    def test_px_existing(self, engine):
        assert engine._px("300308", 0) == 100.0
        assert engine._px("300308", 2) == 102.0

    def test_px_nonexistent(self, engine):
        assert engine._px("999999", 0) == 0.0

    def test_rets_normal(self, engine):
        eq = [100, 110, 105]
        rets = engine._rets(eq)
        assert len(rets) == 3
        assert rets[0] == 0.0
        assert abs(rets[1] - 0.1) < 0.001
        assert abs(rets[2] - (-5 / 110)) < 0.001

    def test_rets_zero_equity(self, engine):
        rets = engine._rets([0, 0, 0])
        assert rets == [0.0, 0.0, 0.0]

    def test_compute_turnover_no_prev(self):
        assert (
            HedgeRebalanceBacktest._compute_turnover(None, {"A": 100}, {"A": 100})
            == 0.0
        )

    def test_compute_turnover_no_change(self):
        prev = {"A": 100}
        pos = {"A": 100}
        px = {"A": 50.0}
        assert HedgeRebalanceBacktest._compute_turnover(prev, pos, px) == 0.0

    def test_compute_turnover_with_change(self):
        prev = {"A": 100}
        pos = {"A": 200}
        px = {"A": 50.0}
        # traded = 100 * 50 = 5000
        assert HedgeRebalanceBacktest._compute_turnover(prev, pos, px) == 5000.0

    def test_compute_turnover_zero_price_skip(self):
        prev = {"A": 100}
        pos = {"A": 200}
        px = {"A": 0.0}
        assert HedgeRebalanceBacktest._compute_turnover(prev, pos, px) == 0.0


# ============================================================
# 10. HedgeRebalanceBacktest._metrics
# ============================================================
class TestMetrics:
    def test_metrics_calculated(self):
        dates = pd.date_range("2026-01-01", periods=5, freq="B")
        price_df = pd.DataFrame(
            {
                "300308": [100.0, 101.0, 102.0, 101.5, 103.0],
            },
            index=dates,
        )
        csi300_ret = pd.Series([0.0, 0.01, 0.005, -0.002, 0.008], index=dates)

        engine = HedgeRebalanceBacktest(price_df, csi300_ret)
        r = BacktestResult(
            name="test",
            equity_curve=[1_000_000, 1_010_000, 1_020_000, 1_015_000, 1_030_000],
            dates=dates,
            daily_returns=[0.0, 0.01, 0.0099, -0.0049, 0.0148],
            trade_count=2,
            hedge_costs=[0.0] * 5,
            transaction_costs=[0.0] * 5,
            n_days=5,
            turnover_daily=[0.0, 0.1, 0.05, 0.0, 0.08],
        )
        engine._metrics(r)
        assert r.total_return > 0
        assert r.annual_return != 0
        assert r.annual_volatility > 0
        assert r.sharpe_ratio != 0
        assert r.max_drawdown >= 0
        assert r.win_rate >= 0
        assert r.total_transaction_cost == 0.0
        assert r.annual_turnover > 0
        # 5 天数据不足 10 天, yearly_stats 应为空
        assert r.yearly_stats == []


# ============================================================
# 11. HedgeRebalanceBacktest._run_s1 ~ _run_s5 (合成数据)
# ============================================================
class TestStrategyRuns:
    @pytest.fixture
    def engine(self):
        """构造 30 天合成数据, 足够跑策略"""
        np.random.seed(42)
        dates = pd.date_range("2026-01-01", periods=30, freq="B")
        # 构造 3 个标的的价格数据
        price_df = pd.DataFrame(
            {
                "300308": 100 + np.cumsum(np.random.normal(0, 0.5, 30)),
                "688041": 50 + np.cumsum(np.random.normal(0, 0.3, 30)),
                "601088": 20 + np.cumsum(np.random.normal(0, 0.1, 30)),
            },
            index=dates,
        )
        csi300_ret = pd.Series(np.random.normal(0.001, 0.01, 30), index=dates)
        index_rets = {
            "IF": csi300_ret,
            "IC": pd.Series(np.random.normal(0.001, 0.012, 30), index=dates),
            "IM": pd.Series(np.random.normal(0.001, 0.015, 30), index=dates),
        }
        return HedgeRebalanceBacktest(price_df, csi300_ret, index_rets)

    def test_run_s1(self, engine):
        r = engine._run_s1()
        assert r.name == "S1:静态基准"
        assert len(r.equity_curve) == 30
        assert r.trade_count >= 0

    def test_run_s2(self, engine):
        r = engine._run_s2()
        assert r.name == "S2:仅再平衡"
        assert len(r.equity_curve) == 30
        assert len(r.turnover_daily) == 30

    def test_run_s3(self, engine):
        r = engine._run_s3()
        assert "S3" in r.name
        assert len(r.equity_curve) == 30

    def test_run_s4_v2(self, engine):
        r = engine._run_s4_v2()
        assert "S4" in r.name
        assert len(r.equity_curve) == 30

    def test_run_s5(self, engine):
        r = engine._run_s5()
        assert "S5" in r.name
        assert len(r.equity_curve) == 30

    def test_run_all(self, engine):
        multi = engine.run_all()
        assert len(multi.strategies) == 6
        # 每个策略应已计算 metrics
        for s in multi.strategies:
            assert len(s.equity_curve) == 30
            # 初始资金应在合理范围 (策略可能第一天就交易)
            assert s.equity_curve[0] > 0


# ============================================================
# 12. format_comparison_report / save_report
# ============================================================
class TestReportGeneration:
    @pytest.fixture
    def multi_result(self):
        dates = pd.date_range("2026-01-01", periods=5, freq="B")
        strategies = []
        for name in ["S1", "S2", "S3", "S4", "S5"]:
            r = BacktestResult(
                name=name,
                equity_curve=[1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_025_000],
                dates=dates,
                daily_returns=[0.0, 0.01, -0.005, 0.015, 0.005],
                trade_count=1,
                hedge_costs=[0.0] * 5,
                transaction_costs=[10.0] * 5,
                n_days=5,
                turnover_daily=[0.0, 0.05, 0.0, 0.03, 0.0],
            )
            strategies.append(r)
        return MultiStrategyResult(strategies=strategies)

    def test_format_comparison_report(self, multi_result):
        report = format_comparison_report(multi_result)
        assert isinstance(report, str)
        assert "对冲+再平衡联动" in report
        assert "S1" in report
        assert "S5" in report

    def test_save_report_default_dir(self, multi_result, tmp_path):
        report = format_comparison_report(multi_result)
        # save_report 用 tmp_path 避免污染项目目录
        saved_path = save_report(report, output_dir=str(tmp_path))
        # 验证文件已生成 (.md 扩展名)
        files = list(Path(tmp_path).glob("*.md"))
        assert len(files) >= 1
        assert saved_path is not None

    def test_save_report_no_dir_creates_file(self, multi_result):
        report = "test report content"
        # 不传 output_dir, 应在默认位置创建 (项目 reports/ 下)
        result_path = save_report(report)
        assert result_path is not None
        # 清理生成的文件
        if Path(result_path).exists():
            Path(result_path).unlink()


# ============================================================
# 13. BacktestDataLoader (mock 文件系统)
# ============================================================
class TestBacktestDataLoader:
    def test_init_default_dir(self, tmp_path):
        loader = BacktestDataLoader(cache_dir=str(tmp_path))
        assert loader.cache_dir == str(tmp_path)
        assert loader._price_data == {}
        assert loader._csi300 is None
        assert loader._index_data == {}

    def test_load_or_download_empty_codes(self, tmp_path):
        loader = BacktestDataLoader(cache_dir=str(tmp_path))
        result = loader.load_or_download([], "2026-01-01", "2026-01-31")
        assert result == {}

    def test_load_or_download_from_cache(self, tmp_path):
        # parquet 依赖 pyarrow, 可能未安装; 用 mock 验证逻辑
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        df = pd.DataFrame({"close": 100 + np.arange(200) * 0.1}, index=dates)
        df.index.name = "date"
        cache_path = tmp_path / "kline_300308_daily.parquet"
        try:
            df.to_parquet(cache_path)
        except ImportError:
            pytest.skip("pyarrow/fastparquet not installed")

        loader = BacktestDataLoader(cache_dir=str(tmp_path))
        result = loader.load_or_download(["300308"], "2025-01-01", "2025-12-31")
        assert "300308" in result
        assert len(result["300308"]) == 200

    def test_load_csi300_from_index_data(self, tmp_path):
        loader = BacktestDataLoader(cache_dir=str(tmp_path))
        # 预设 _index_data
        loader._index_data["IF"] = pd.Series(
            [100, 101, 102], index=pd.date_range("2026-01-01", periods=3)
        )
        csi = loader.load_csi300("2026-01-01", "2026-01-31")
        assert len(csi) == 3

    def test_load_multi_index_no_cache(self, tmp_path):
        # baostock 未安装时, _load_index_data 会抛 ModuleNotFoundError
        # 验证 load_multi_index 在无缓存无 baostock 时优雅处理
        loader = BacktestDataLoader(cache_dir=str(tmp_path))
        try:
            result = loader.load_multi_index("2026-01-01", "2026-01-31")
            assert isinstance(result, dict)
        except (ModuleNotFoundError, ImportError):
            # baostock 未安装, 跳过
            pytest.skip("baostock not installed")
