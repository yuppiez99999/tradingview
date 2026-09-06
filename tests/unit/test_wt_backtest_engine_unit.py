"""wt_backtest_engine 单元测试 — WonderTrader 风格回测引擎

覆盖:
- BacktestEngine: 初始化/重置/手续费/滑点/买入/卖出/权益/回测/报告
- ETFSignalStrategy: 信号生成
- BacktestDataLoader: 合成数据/历史加载
- run_etf_signal_backtest / compare_strategies 便捷函数
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from utils.wt_backtest_engine import (
    BacktestDataLoader,
    BacktestEngine,
    ETFSignalStrategy,
    compare_strategies,
    run_etf_signal_backtest,
)

# ============================================================
# BacktestEngine
# ============================================================


class TestBacktestEngine:
    """BacktestEngine 回测引擎测试"""

    def test_init_defaults(self):
        e = BacktestEngine()
        assert e.initial_capital == 1_000_000.0
        assert e.commission_rate == 0.0003
        assert e.slippage_rate == 0.001
        assert e.min_commission == 5.0
        assert e.cash == 1_000_000.0
        assert e.positions == {}
        assert e.trades == []

    def test_init_custom(self):
        e = BacktestEngine(
            initial_capital=500_000, commission_rate=0.0005, slippage_rate=0.002
        )
        assert e.initial_capital == 500_000
        assert e.commission_rate == 0.0005
        assert e.slippage_rate == 0.002

    def test_reset(self):
        e = BacktestEngine()
        e.cash = 100
        e.positions = {"x": {}}
        e.trades = [{}]
        e.reset()
        assert e.cash == 1_000_000.0
        assert e.positions == {}
        assert e.trades == []

    def test_calculate_commission(self):
        e = BacktestEngine(commission_rate=0.0003, min_commission=5.0)
        assert e.calculate_commission(100_000) == pytest.approx(30.0)
        assert e.calculate_commission(100) == 5.0  # min_commission

    def test_calculate_slippage_buy(self):
        e = BacktestEngine(slippage_rate=0.001)
        assert e.calculate_slippage(100, 10, "BUY") == 100.1

    def test_calculate_slippage_sell(self):
        e = BacktestEngine(slippage_rate=0.001)
        assert e.calculate_slippage(100, 10, "SELL") == 99.9

    # ---- P1-2: 冲击成本与流动性 (ADV) 挂钩 ----

    def test_init_impact_defaults(self):
        """P1-2: 未注入 ADV 时冲击层为空, 行为与旧版一致。"""
        e = BacktestEngine()
        assert e.liquidity_adv == {}
        assert e.impact_sr_coefficient == 0.5
        assert e.impact_daily_volatility == 0.02

    def test_impact_zero_without_adv(self):
        """P1-2: 标的未注入 ADV → 成交价 = 基础滑点价 (零冲击, 向后兼容)。"""
        e = BacktestEngine(slippage_rate=0.001)
        # 直接方法: 无 ADV 时返回原价
        assert e.calculate_impact_price("A", 100.0, 1_000, "BUY") == 100.0
        # buy/sell 路径: 成交价 = 基础滑点价
        assert e.buy("A", 100.0, 1_000) is True
        assert e.trades[-1]["execution_price"] == pytest.approx(100.1)

    def test_impact_price_buy_above_slippage(self):
        """P1-2: BUY 冲击价高于基础滑点价, 金额随参与度放大。"""
        e = BacktestEngine(
            slippage_rate=0.001, liquidity_adv={"A": 10_000_000.0}
        )
        # participation = 100*1000/1e7 = 0.01 -> impact_rate = 0.02*0.5*sqrt(0.01)
        # impact = 100 * 0.001; 总价 = 基础滑点价 + impact
        assert e.buy("A", 100.0, 1_000) is True
        impact_price = e.calculate_impact_price("A", 100.0, 1_000, "BUY")
        assert impact_price > 100.0
        assert e.trades[-1]["execution_price"] == pytest.approx(
            100.1 + (impact_price - 100.0), rel=1e-9
        )

    def test_impact_grows_with_order_size(self):
        """P1-2: 同一标的 ADV 下, 订单越大冲击成本越高 (不再固定脱钩)。"""
        e = BacktestEngine(liquidity_adv={"A": 10_000_000.0})
        small = e.calculate_impact_price("A", 100.0, 1_000, "BUY")
        large = e.calculate_impact_price("A", 100.0, 50_000, "BUY")
        assert large - 100.0 > small - 100.0

    def test_impact_sell_symmetric(self):
        """P1-2: SELL 冲击对称下浮。"""
        e = BacktestEngine(liquidity_adv={"A": 10_000_000.0})
        buy_px = e.calculate_impact_price("A", 100.0, 1_000, "BUY")
        sell_px = e.calculate_impact_price("A", 100.0, 1_000, "SELL")
        assert (sell_px - 100.0) == pytest.approx(-(buy_px - 100.0), rel=1e-9)

    def test_impact_capped_at_full_adv(self):
        """P1-2: 参与度 > 100% 时按 100% 封顶, sqrt 不失真。"""
        e = BacktestEngine(liquidity_adv={"A": 1_000.0})
        # notional 100*50000 = 5e6 >> ADV 1000 -> participation cap = 1.0
        px = e.calculate_impact_price("A", 100.0, 50_000, "BUY")
        cap_impact = 100.0 * 0.02 * 0.5 * 1.0
        assert (px - 100.0) == pytest.approx(cap_impact, rel=1e-9)

    # ---- P1-4 基准对比: Alpha/Beta/信息比率/超额收益 ----

    @staticmethod
    def _run_two_day_no_trade():
        """空仓 2 交易日回测, 组合日收益恒 0 (便于精确对照基准指标)。"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 105}},
        ]
        return e.run(data, lambda d, p: [])

    def test_benchmark_fields_none_by_default(self):
        """P1-4: 不传基准 → 基准字段 None, 向后兼容。"""
        r = self._run_two_day_no_trade()
        assert r["benchmark_total_return"] is None
        assert r["benchmark_annualized_return"] is None
        assert r["excess_total_return"] is None
        assert r["alpha_annual"] is None
        assert r["beta"] is None
        assert r["information_ratio"] is None
        assert r["benchmark_note"] is None

    def test_benchmark_total_return_geometric(self):
        """P1-4: 基准累计收益 = 几何连乘 - 1, 超额 = 组合 - 基准。"""
        bench = [0.01, 0.02]
        bench_total = (1.01) * (1.02) - 1.0
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 105}},
        ]
        r = e.run(data, lambda d, p: [], benchmark_returns=bench)
        assert r["benchmark_total_return"] == pytest.approx(bench_total)
        # 组合 0 收益 → 超额 = -bench_total
        assert r["excess_total_return"] == pytest.approx(-bench_total, rel=1e-9)
        assert r["benchmark_note"] is None

    def test_benchmark_length_mismatch(self):
        """P1-4: 基准长度 ≠ 样本 → note 说明并跳过。"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 105}},
        ]
        r = e.run(data, lambda d, p: [], benchmark_returns=[0.01])
        assert r["benchmark_total_return"] is None
        assert r["benchmark_note"] is not None
        assert "长度" in r["benchmark_note"]

    def test_alpha_beta_zero_cash_path(self):
        """P1-4: 组合恒 0 收益时, beta=0, alpha≈-rf 年化, IR 为负。

        组合日收益恒 0 → 与基准无关 (cov=0) → beta=0;
        alpha_daily = mean(组合超额) = -rf_d → 年化 ≈ -rf。
        """
        rf_d = (1.0 + 0.02) ** (1.0 / 252.0) - 1.0
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 105}},
        ]
        r = e.run(data, lambda d, p: [], benchmark_returns=[0.01, 0.02])
        assert r["beta"] == pytest.approx(0.0, abs=1e-9)
        assert r["alpha_annual"] == pytest.approx(-rf_d * 252.0, rel=1e-6)
        assert r["information_ratio"] is not None
        assert r["information_ratio"] < 0.0  # 组合跑输正收益基准

    def test_benchmark_single_obs_no_regression(self):
        """P1-4: 单日样本仅累计/超额可用, 不抛除零。"""
        e = BacktestEngine()
        data = [{"date": "2026-08-01", "prices": {"A": 100}}]
        r = e.run(data, lambda d, p: [], benchmark_returns=[0.0])
        assert r["benchmark_total_return"] == pytest.approx(0.0)
        assert r["alpha_annual"] is None
        assert r["beta"] is None
        assert r["information_ratio"] is None
        assert "样本仅 1" in (r["benchmark_note"] or "")

    # ---- 数值正确性: 解析解对照 (确定性权益路径) ----

    @staticmethod
    def _seed_deterministic_path():
        """注入确定权益路径: 初始 10 万 → 11万(+10%) → 9.9万(-10%) → 10.89万(+10%)。

        raw_returns = [0.1, -0.1, 0.1] (样本期 3 日)。
        """
        e = BacktestEngine(initial_capital=100_000.0, risk_free_rate=0.0)
        e.equity_curve = [
            {"date": "2026-08-01", "equity": 110_000.0},
            {"date": "2026-08-02", "equity": 99_000.0},
            {"date": "2026-08-03", "equity": 108_900.0},
        ]
        e.daily_pnl = [{"date": d} for d in ("2026-08-01", "2026-08-02", "2026-08-03")]
        return e

    def test_analytic_total_and_annualized_return(self):
        """解析: 总收益 (108900/100000-1)=8.9%; 短窗(<63日) 年化=累计。"""
        e = self._seed_deterministic_path()
        r = e.generate_report()
        assert r["total_return"] == pytest.approx(0.089, rel=1e-12)
        # 3 日 < 63 → P1-6 不做年化外推
        assert r["annualized_return"] == pytest.approx(0.089, rel=1e-12)
        assert r["annualized_note"] is not None
        assert "样本仅 3" in r["annualized_note"]

    def test_analytic_sharpe(self):
        """解析: 样本均值 0.1/3, 样本std(3-1)=sqrt(0.04/3), Sharpe = mean/std*sqrt252。"""
        import math

        e = self._seed_deterministic_path()
        r = e.generate_report()
        mean = 0.1 / 3.0
        std = math.sqrt(
            ((0.1 - mean) ** 2 + (-0.1 - mean) ** 2 + (0.1 - mean) ** 2) / 2.0
        )
        expect_sharpe = mean / std * math.sqrt(252)
        assert r["avg_daily_return"] == pytest.approx(mean, rel=1e-12)
        assert r["std_daily_return"] == pytest.approx(std, rel=1e-12)
        assert r["sharpe_ratio"] == pytest.approx(expect_sharpe, rel=1e-9)

    def test_analytic_max_drawdown(self):
        """解析: 峰值 11 万 → 9.9 万回撤 = 2/11 ≈ 18.18%。"""
        e = self._seed_deterministic_path()
        r = e.generate_report()
        assert r["max_drawdown"] == pytest.approx(
            (110_000.0 - 99_000.0) / 110_000.0, rel=1e-9
        )

    def test_analytic_win_rate_with_costs(self):
        """解析: 含费胜率 = 盈利卖出 / 全部卖出。

        两笔卖出: +20% 净盈利、-20% 净亏损 → win_rate = 0.5
        (sell 成交价与净回款均含费用, 用 realized_pnl 判盈)。
        """
        e = BacktestEngine(initial_capital=1_000_000.0)
        e.buy("A", 100.0, 1000)
        e.sell("A", 120.0, 1000)
        e.buy("B", 100.0, 1000)
        e.sell("B", 80.0, 1000)
        e.record_daily_pnl("2026-08-01")
        r = e.generate_report()
        assert r["sell_trades"] == 2
        assert r["win_rate"] == 0.5
        # 含费判盈逐笔核对: A 卖出净盈、B 卖出净亏 (费用不反转方向)
        pnls = [t["realized_pnl"] for t in e.trades if t["action"] == "SELL"]
        assert pnls[0] > 0.0 and pnls[1] < 0.0

    def test_buy_success(self):
        e = BacktestEngine()
        assert e.buy("600519", 100, 100) is True
        assert "600519" in e.positions
        assert e.positions["600519"]["qty"] == 100
        assert e.cash < 1_000_000
        assert len(e.trades) == 1

    def test_buy_insufficient_cash(self):
        e = BacktestEngine(initial_capital=100)
        assert e.buy("600519", 100, 100) is False
        assert "600519" not in e.positions

    def test_buy_updates_avg_cost(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.buy("A", 110, 100)
        pos = e.positions["A"]
        assert pos["qty"] == 200
        assert 100 < pos["avg_cost"] < 110

    def test_sell_success(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        assert e.sell("A", 110, 50) is True
        assert e.positions["A"]["qty"] == 50

    def test_sell_all_removes_position(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        assert e.sell("A", 110, 100) is True
        assert "A" not in e.positions

    def test_sell_no_position(self):
        e = BacktestEngine()
        assert e.sell("A", 100, 10) is False

    def test_sell_insufficient_qty(self):
        e = BacktestEngine()
        e.buy("A", 100, 50)
        assert e.sell("A", 100, 100) is False

    def test_update_prices(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.update_prices({"A": 110})
        assert e.positions["A"]["current_price"] == 110

    def test_get_total_equity_empty(self):
        e = BacktestEngine()
        assert e.get_total_equity() == 1_000_000.0

    def test_get_total_equity_with_positions(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.update_prices({"A": 110})
        # cash + 100*110
        assert e.get_total_equity() == e.cash + 100 * 110

    def test_record_daily_pnl_first(self):
        e = BacktestEngine()
        e.record_daily_pnl("2026-08-01")
        assert len(e.equity_curve) == 1
        assert len(e.daily_pnl) == 1
        assert e.daily_pnl[0]["daily_return"] == 0.0

    def test_record_daily_pnl_subsequent(self):
        e = BacktestEngine()
        e.record_daily_pnl("2026-08-01")
        e.buy("A", 100, 100)
        e.record_daily_pnl("2026-08-02")
        assert len(e.daily_pnl) == 2

    def test_record_daily_pnl_none_date(self):
        e = BacktestEngine()
        e.record_daily_pnl(None)
        assert e.daily_pnl[0]["date"] == ""

    def test_is_suspended_explicit(self):
        e = BacktestEngine()
        day_data = {"suspended": {"A": True}}
        assert e._is_suspended(day_data, "A") is True

    def test_is_suspended_not_suspended(self):
        e = BacktestEngine()
        day_data = {"suspended": {"A": False}, "prices": {"A": 100}}
        assert e._is_suspended(day_data, "A") is False

    def test_is_suspended_zero_price(self):
        e = BacktestEngine()
        day_data = {"prices": {"A": 0}}
        assert e._is_suspended(day_data, "A") is True

    def test_is_suspended_no_data(self):
        e = BacktestEngine()
        assert e._is_suspended({}, "A") is True

    def test_run_empty_data(self):
        e = BacktestEngine()
        result = e.run([], lambda d, p: [])
        assert result["status"] == "error"

    def test_run_simple(self):
        e = BacktestEngine()

        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 110}},
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01" and "A" not in positions:
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            return []

        result = e.run(data, strategy)
        assert result["status"] == "success"
        assert result["backtest_days"] == 2

    def test_run_with_limit_up(self):
        """涨停不可买——P0-3 延迟成交下, 约束在执行日(次日)开盘撮合时校验"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {
                "date": "2026-08-02",
                "prices": {"A": 110},
                "limit_up_prices": {"A": 110},
            },
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01" and "A" not in positions:
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            return []

        result = e.run(data, strategy)
        assert result["total_trades"] == 0

    def test_run_with_limit_down(self):
        """跌停不可卖——P0-3 延迟成交: T 收盘 SELL 信号在 T+1 跌停日撮合被拦"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 105}},
            {
                "date": "2026-08-03",
                "prices": {"A": 90},
                "limit_down_prices": {"A": 90},
            },
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01":
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            elif day_data["date"] == "2026-08-02" and "A" in positions:
                return [{"code": "A", "action": "SELL", "qty": 100, "price": 105}]
            return []

        result = e.run(data, strategy)
        assert result["buy_trades"] == 1
        assert result["sell_trades"] == 0

    def test_run_with_suspended(self):
        """停牌不可交易——P0-3 延迟成交: 停牌执行日拒单, 复牌日撮合成功"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {
                "date": "2026-08-02",
                "prices": {"A": 110},
                "suspended": {"A": True},
            },
            {"date": "2026-08-03", "prices": {"A": 120}},
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01":
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            return []

        result = e.run(data, strategy)
        assert result["total_trades"] == 1
        # 成交日应为复牌日 2026-08-03, 而非停牌日/信号日
        assert result["trades"][0]["date"] == "2026-08-03"

    def test_generate_report_no_data(self):
        e = BacktestEngine()
        assert e.generate_report()["status"] == "error"

    def test_generate_report_with_data(self):
        e = BacktestEngine()
        data = [{"date": "2026-08-01", "prices": {"A": 100}}]
        e.run(data, lambda d, p: [])
        report = e.generate_report()
        assert report["status"] == "success"
        for field in [
            "initial_capital",
            "final_equity",
            "total_return",
            "sharpe_ratio",
            "max_drawdown",
            "win_rate",
        ]:
            assert field in report

    # ---- P0-1 胜率数值口径: 基于 realized_pnl, 而非净回款恒为正 ----
    def _buy_and_sell(self, sell_price):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.sell("A", sell_price, 100)
        e.record_daily_pnl("2026-08-01")
        return e

    def test_win_rate_zero_when_loss(self):
        """亏损卖出(90<买入100) → win_rate 必须为 0 (旧口径净回款恒正→100%)"""
        e = self._buy_and_sell(90)
        assert e.trades[-1]["action"] == "SELL"
        assert e.trades[-1]["realized_pnl"] < 0
        r = e.generate_report()
        assert r["sell_trades"] == 1
        assert r["win_rate"] == 0.0

    def test_win_rate_one_when_profit(self):
        """盈利卖出(120>100) → win_rate 为 1"""
        e = self._buy_and_sell(120)
        assert e.trades[-1]["realized_pnl"] > 0
        r = e.generate_report()
        assert r["win_rate"] == 1.0

    def test_win_rate_mixed(self):
        """一盈一亏 → win_rate == 0.5"""
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.sell("A", 120, 100)
        e.buy("B", 100, 100)
        e.sell("B", 90, 100)
        e.record_daily_pnl("2026-08-01")
        r = e.generate_report()
        assert r["sell_trades"] == 2
        assert r["win_rate"] == 0.5

    # ---- P0-2 Sharpe 数值口径: 零收益日保留在样本中 ----
    def test_sharpe_positive_with_profit_run(self):
        """延迟成交+持仓升值 → sharpe_ratio > 0 (日收益样本含含费零/负日)"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 100}},
            {"date": "2026-08-03", "prices": {"A": 110}},
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01":
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            return []

        r = e.run(data, strategy)
        assert r["status"] == "success"
        assert r["std_daily_return"] > 0, "样本标准差应>0 (含0收益日也要计入波动样本)"
        assert r["sharpe_ratio"] > 0

    # ---- P1-1 T+1: 同一执行日先 BUY 后 SELL 同一 code 被拒 ----
    def test_t_plus_1_blocks_same_day_sell(self):
        """同日先买入后卖出同一标的 → 卖出被 T+1 拦截"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 105}},
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01":
                # 同日同 code 两个信号: BUY 先成交, SELL 应被 T+1 拒绝
                return [
                    {"code": "A", "action": "BUY", "qty": 100, "price": 100},
                    {"code": "A", "action": "SELL", "qty": 100, "price": 100},
                ]
            return []

        r = e.run(data, strategy)
        assert r["buy_trades"] == 1
        assert r["sell_trades"] == 0, "T+1: 当日买入不可当日卖出"

    # ---- P1-3 印花税/过户费: 股票卖出征收, ETF/基金免征 ----
    def test_stamp_tax_applied_to_stock_sell_only(self):
        e = BacktestEngine()
        assert e.buy("600519", 100, 100)
        assert e.sell("600519", 110, 100)
        sell_trade = e.trades[-1]
        assert sell_trade["stamp_tax"] > 0  # 卖出印花税
        assert sell_trade["transfer_fee"] > 0  # 股票双边过户费
        # 过户费/印花税都按比例远小于成交额
        assert sell_trade["stamp_tax"] < 11000 * 0.001

    def test_no_stamp_tax_for_etf(self):
        """ETF(5 开头)买卖免征印花税与过户费"""
        e = BacktestEngine()
        assert e.buy("510050", 3.0, 10000)
        assert e.sell("510050", 3.2, 10000)
        sell_trade = e.trades[-1]
        assert sell_trade["stamp_tax"] == 0.0
        assert sell_trade["transfer_fee"] == 0.0


# ============================================================
# ETFSignalStrategy
# ============================================================


class TestETFSignalStrategy:
    """ETFSignalStrategy 策略测试"""

    def test_init_defaults(self):
        s = ETFSignalStrategy()
        assert s.signal_thresholds["strong_buy"] == "强加仓"
        assert s.signal_thresholds["strong_sell"] == "强减仓"
        assert s.max_position_pct == 0.3

    def test_init_custom(self):
        s = ETFSignalStrategy(max_position_pct=0.5)
        assert s.max_position_pct == 0.5

    def test_generate_signals_empty(self):
        s = ETFSignalStrategy()
        day_data = {"etf_signals": {}, "prices": {}, "equity": 1_000_000}
        assert s.generate_signals(day_data, {}) == []

    def test_generate_signals_strong_buy(self):
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "强加仓", "inflow": 100}},
            "prices": {"A": 10},
            "equity": 1_000_000,
        }
        signals = s.generate_signals(day_data, {})
        assert len(signals) >= 1
        assert signals[0]["action"] == "BUY"
        assert signals[0]["code"] == "A"

    def test_generate_signals_strong_sell(self):
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "强减仓", "inflow": -100}},
            "prices": {"A": 10},
            "equity": 1_000_000,
        }
        positions = {"A": {"qty": 1000, "avg_cost": 10, "current_price": 10}}
        signals = s.generate_signals(day_data, positions)
        assert len(signals) >= 1
        assert signals[0]["action"] == "SELL"

    def test_generate_signals_zero_price(self):
        """价格为 0 → 跳过"""
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "强加仓", "inflow": 100}},
            "prices": {"A": 0},
            "equity": 1_000_000,
        }
        assert s.generate_signals(day_data, {}) == []

    def test_generate_signals_no_signal(self):
        """无信号 → 不交易"""
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "中性", "inflow": 0}},
            "prices": {"A": 10},
            "equity": 1_000_000,
        }
        assert s.generate_signals(day_data, {}) == []


# ============================================================
# BacktestDataLoader
# ============================================================


class TestBacktestDataLoader:
    """BacktestDataLoader 数据加载器测试"""

    def test_generate_synthetic_data(self):
        data = BacktestDataLoader.generate_synthetic_data(
            "2026-08-03", "2026-08-07", ["A", "B"]
        )
        assert len(data) >= 3  # 8/3-8/7 有 5 个工作日
        for day in data:
            assert "date" in day
            assert "prices" in day
            assert "etf_signals" in day
            assert "A" in day["prices"]

    def test_generate_synthetic_data_weekend_skip(self):
        """周末跳过"""
        data = BacktestDataLoader.generate_synthetic_data(
            "2026-08-01", "2026-08-02", ["A"]
        )
        # 8/1=周六, 8/2=周日 → 0 个工作日
        assert len(data) == 0

    def test_load_from_positions_history_empty(self):
        with tempfile.TemporaryDirectory() as d:
            data = BacktestDataLoader.load_from_positions_history(d)
        assert data == []

    def test_load_from_positions_history_with_file(self):
        with tempfile.TemporaryDirectory() as d:
            pos_file = os.path.join(d, "positions_2026-08-01.json")
            with open(pos_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "positions": {
                            "A": {
                                "etf_flow_signal": "强加仓",
                                "etf_inflow": 100,
                                "avg_cost": 10,
                            }
                        }
                    },
                    f,
                )
            data = BacktestDataLoader.load_from_positions_history(d)
        assert len(data) == 1
        assert data[0]["date"] == "2026-08-01"
        assert "A" in data[0]["prices"]

    def test_load_from_positions_history_with_tickers_filter(self):
        with tempfile.TemporaryDirectory() as d:
            pos_file = os.path.join(d, "positions_2026-08-01.json")
            with open(pos_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "positions": {
                            "A": {"avg_cost": 10},
                            "B": {"avg_cost": 20},
                        }
                    },
                    f,
                )
            data = BacktestDataLoader.load_from_positions_history(d, tickers=["A"])
        assert len(data) == 1
        assert "A" in data[0]["prices"]
        assert "B" not in data[0]["prices"]


# ============================================================
# 便捷函数
# ============================================================


class TestConvenienceFunctions:
    """run_etf_signal_backtest / compare_strategies 测试"""

    def test_run_etf_signal_backtest(self):
        data = BacktestDataLoader.generate_synthetic_data(
            "2026-08-03", "2026-08-07", ["A"]
        )
        result = run_etf_signal_backtest(data, initial_capital=500_000)
        assert result["status"] == "success"
        assert result["initial_capital"] == 500_000

    def test_run_etf_signal_backtest_empty(self):
        result = run_etf_signal_backtest([])
        assert result["status"] == "error"

    def test_compare_strategies(self):
        data = [{"date": "2026-08-03", "prices": {"A": 10}, "etf_signals": {}}]

        def buy_strategy(day_data, positions):
            if "A" not in positions:
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 10}]
            return []

        def hold_strategy(day_data, positions):
            return []

        results = compare_strategies(data, {"buy": buy_strategy, "hold": hold_strategy})
        assert "buy" in results
        assert "hold" in results
        assert results["buy"]["status"] == "success"
        assert results["hold"]["status"] == "success"
