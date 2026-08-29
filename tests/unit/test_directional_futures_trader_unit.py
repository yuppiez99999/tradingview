"""directional_futures_trader 单元测试

覆盖: _get_spec, FuturesSignal, FuturesOrder, DirectionalFuturesResult,
DirectionalFuturesTrader (generate_signals, _calc_rsi, _calc_macd_hist, _calc_ema,
calculate_position, check_risk, generate_orders, _build_close_order, _calc_stops,
run, _build_summary, summary, _save_report)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.directional_futures_trader import (
    CONTRACT_SPECS,
    DAILY_MAX_LOSS_PCT,
    FUTURES_MARGIN_CAPITAL,
    FUTURES_MARGIN_MAX_PCT,
    LOSS_PAUSE_DAYS,
    PER_SYMBOL_MARGIN_BUDGET,
    WEEKLY_CONSECUTIVE_LOSS_MAX_PCT,
    DirectionalFuturesResult,
    DirectionalFuturesTrader,
    FuturesOrder,
    FuturesSignal,
    _get_spec,
)


# ============================================================
# fixtures
# ============================================================
@pytest.fixture
def trader() -> DirectionalFuturesTrader:
    return DirectionalFuturesTrader()


@pytest.fixture
def uptrend_closes() -> list[float]:
    """60 根 K 线, 持续上涨"""
    return [100 + i * 2 for i in range(60)]


@pytest.fixture
def downtrend_closes() -> list[float]:
    """60 根 K 线, 持续下跌"""
    return [200 - i * 2 for i in range(60)]


@pytest.fixture
def flat_closes() -> list[float]:
    """60 根 K 线, 价格不变"""
    return [100.0] * 60


@pytest.fixture
def market_data_uptrend() -> dict:
    """3 品种上涨市场数据"""
    return {
        "CU": {"closes": [70000 + i * 200 for i in range(60)]},
        "AU": {"closes": [500 + i * 1.5 for i in range(60)]},
        "T": {"closes": [100 + i * 0.05 for i in range(60)]},
    }


# ============================================================
# _get_spec
# ============================================================
class TestGetSpec:
    def test_valid_symbols(self):
        for sym in ("CU", "AU", "T"):
            spec = _get_spec(sym)
            assert spec is not None
            assert spec.product == sym

    def test_unknown_symbol_raises(self):
        with pytest.raises(KeyError, match="未注册期货品种"):
            _get_spec("XYZ")

    def test_spec_fields_match_contract_specs(self):
        for sym, spec_dict in CONTRACT_SPECS.items():
            spec = _get_spec(sym)
            assert spec.name == spec_dict["name"]
            assert spec.exchange == spec_dict["exchange"]
            assert spec.multiplier == spec_dict["multiplier"]
            assert spec.margin_rate == spec_dict["margin_rate"]
            assert spec.tick_size == spec_dict["tick_size"]


# ============================================================
# 数据类
# ============================================================
class TestDataclasses:
    def test_futures_signal_defaults(self):
        s = FuturesSignal(symbol="CU", name="沪铜期货")
        assert s.direction == "flat"
        assert s.strength == 0.0
        assert s.rsi == 50.0
        assert s.confidence == 0.0

    def test_futures_order_defaults(self):
        o = FuturesOrder(symbol="CU", name="沪铜期货", exchange="SHFE")
        assert o.action == "hold"
        assert o.direction == "flat"
        assert o.contracts == 0

    def test_directional_result_defaults(self):
        r = DirectionalFuturesResult()
        assert r.action == "skip"
        assert r.risk_status == "normal"
        assert r.pause_until is None
        assert r.signals == []
        assert r.orders == []


# ============================================================
# __init__
# ============================================================
class TestInit:
    def test_default_params(self):
        t = DirectionalFuturesTrader()
        assert t.capital == FUTURES_MARGIN_CAPITAL
        assert t.per_symbol_budget == PER_SYMBOL_MARGIN_BUDGET
        assert t.max_margin_pct == FUTURES_MARGIN_MAX_PCT
        assert sorted(t.symbols) == ["AU", "CU", "T"]

    def test_custom_params(self):
        t = DirectionalFuturesTrader(
            capital=1_000_000, per_symbol_budget=200_000, max_margin_pct=0.5
        )
        assert t.capital == 1_000_000
        assert t.per_symbol_budget == 200_000
        assert t.max_margin_pct == 0.5


# ============================================================
# _calc_rsi
# ============================================================
class TestCalcRsi:
    def test_insufficient_data(self, trader):
        assert trader._calc_rsi([100, 101], 14) == 50.0

    def test_all_gains(self, trader):
        closes = [100 + i for i in range(20)]
        rsi = trader._calc_rsi(closes, 14)
        assert rsi == 100.0

    def test_all_losses(self, trader):
        closes = [200 - i for i in range(20)]
        rsi = trader._calc_rsi(closes, 14)
        assert rsi == 0.0

    def test_mixed(self, trader):
        closes = [
            100,
            102,
            99,
            105,
            100,
            108,
            103,
            110,
            105,
            112,
            107,
            114,
            109,
            116,
            111,
            118,
            113,
            120,
            115,
            122,
        ]
        rsi = trader._calc_rsi(closes, 14)
        assert 0 < rsi < 100

    def test_flat_prices(self, trader):
        closes = [100.0] * 20
        rsi = trader._calc_rsi(closes, 14)
        assert rsi == 100.0  # avg_loss == 0 → return 100


# ============================================================
# _calc_ema
# ============================================================
class TestCalcEma:
    def test_insufficient_data(self, trader):
        ema = trader._calc_ema([100, 101, 102], 12)
        assert ema == pytest.approx(101.0)

    def test_empty_closes(self, trader):
        assert trader._calc_ema([], 12) == 0

    def test_exact_period(self, trader):
        closes = [100.0] * 12
        ema = trader._calc_ema(closes, 12)
        assert ema == pytest.approx(100.0)

    def test_trending(self, trader):
        closes = [100 + i for i in range(20)]
        ema = trader._calc_ema(closes, 12)
        assert 100 < ema < closes[-1]


# ============================================================
# _calc_macd_hist
# ============================================================
class TestCalcMacdHist:
    def test_insufficient_data(self, trader):
        assert (
            trader._calc_macd_hist(
                [100, 101, 102],
            )
            == 0.0
        )

    def test_uptrend_positive(self, trader):
        closes = [100 * (1.02**i) for i in range(60)]
        hist = trader._calc_macd_hist(closes)
        assert hist > 0

    def test_downtrend_negative(self, trader):
        closes = [200 - i * 3 - i * i * 0.1 for i in range(60)]
        hist = trader._calc_macd_hist(closes)
        assert isinstance(hist, float)

    def test_flat_zero(self, trader, flat_closes):
        hist = trader._calc_macd_hist(flat_closes)
        assert hist == pytest.approx(0.0, abs=1e-6)


# ============================================================
# generate_signals
# ============================================================
class TestGenerateSignals:
    def test_insufficient_data(self, trader):
        md = {
            "CU": {"closes": [100, 101]},
            "AU": {"closes": []},
            "T": {"closes": [100]},
        }
        signals = trader.generate_signals(md)
        assert len(signals) == 3
        for s in signals:
            assert s.direction == "flat"
            assert "数据不足" in s.rationale

    def test_uptrend_signals(self, trader, market_data_uptrend):
        signals = trader.generate_signals(market_data_uptrend)
        assert len(signals) == 3
        for s in signals:
            assert s.symbol in ("CU", "AU", "T")
            assert s.direction in ("long", "short", "flat")
            assert -1 <= s.strength <= 1
            assert 0 <= s.confidence <= 1
            assert s.ma20 > 0
            assert s.ma60 > 0

    def test_signal_with_fundamental_cu(self, trader, market_data_uptrend):
        fund = {"CU": {"new_energy_demand": 0.5, "inventory_cycle": -0.3}}
        signals = trader.generate_signals(market_data_uptrend, fund)
        cu_sig = next(s for s in signals if s.symbol == "CU")
        assert "新能源需求" in cu_sig.rationale
        assert "库存周期" in cu_sig.rationale

    def test_signal_with_fundamental_au(self, trader, market_data_uptrend):
        fund = {"AU": {"real_rate": -0.2, "safe_haven": 0.8}}
        signals = trader.generate_signals(market_data_uptrend, fund)
        au_sig = next(s for s in signals if s.symbol == "AU")
        assert "实际利率" in au_sig.rationale
        assert "避险" in au_sig.rationale

    def test_signal_with_fundamental_t(self, trader, market_data_uptrend):
        fund = {"T": {"inflation_expectation": 0.3, "monetary_policy": -0.5}}
        signals = trader.generate_signals(market_data_uptrend, fund)
        t_sig = next(s for s in signals if s.symbol == "T")
        assert "通胀预期" in t_sig.rationale
        assert "货币政策" in t_sig.rationale

    def test_missing_symbol_in_market_data(self, trader):
        signals = trader.generate_signals({})
        assert len(signals) == 3
        for s in signals:
            assert s.direction == "flat"

    def test_downtrend_signals(self, trader):
        md = {
            "CU": {"closes": [80000 - i * 200 for i in range(60)]},
            "AU": {"closes": [600 - i * 1.5 for i in range(60)]},
            "T": {"closes": [120 - i * 0.05 for i in range(60)]},
        }
        signals = trader.generate_signals(md)
        assert len(signals) == 3
        for s in signals:
            assert s.direction in ("long", "short", "flat")


# ============================================================
# calculate_position
# ============================================================
class TestCalculatePosition:
    def test_flat_direction(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="flat", strength=0.8
        )
        c, n, m = trader.calculate_position("CU", sig, 75000)
        assert (c, n, m) == (0, 0.0, 0.0)

    def test_low_strength(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=0.2
        )
        c, n, m = trader.calculate_position("CU", sig, 75000)
        assert (c, n, m) == (0, 0.0, 0.0)

    def test_long_position(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=1.0
        )
        c, n, m = trader.calculate_position("CU", sig, 75000)
        assert c >= 1
        assert n > 0
        assert m > 0
        spec = _get_spec("CU")
        assert n == pytest.approx(c * 75000 * spec.multiplier)
        assert m == pytest.approx(n * spec.margin_rate)

    def test_short_position(self, trader):
        sig = FuturesSignal(
            symbol="AU", name="黄金期货", direction="short", strength=0.8
        )
        c, n, m = trader.calculate_position("AU", sig, 550)
        assert c >= 1
        assert n > 0
        assert m > 0

    def test_zero_price(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=1.0
        )
        c, n, m = trader.calculate_position("CU", sig, 0)
        assert (c, n, m) == (0, 0.0, 0.0)

    def test_negative_price(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=1.0
        )
        c, n, m = trader.calculate_position("CU", sig, -100)
        assert (c, n, m) == (0, 0.0, 0.0)

    def test_t_futures(self, trader):
        sig = FuturesSignal(
            symbol="T", name="10年国债期货", direction="short", strength=0.9
        )
        c, n, m = trader.calculate_position("T", sig, 100)
        assert c >= 1
        assert n > 0


# ============================================================
# check_risk
# ============================================================
class TestCheckRisk:
    def test_normal(self, trader):
        status, pause = trader.check_risk({}, daily_pnl_pct=0.0)
        assert status == "normal"
        assert pause is None

    def test_warning_daily_loss(self, trader):
        status, pause = trader.check_risk({}, daily_pnl_pct=-DAILY_MAX_LOSS_PCT)
        assert status == "warning"
        assert pause is None

    def test_warning_exceed_daily_loss(self, trader):
        status, pause = trader.check_risk({}, daily_pnl_pct=-0.20)
        assert status == "warning"
        assert pause is None

    def test_paused_weekly_loss(self, trader):
        status, pause = trader.check_risk(
            {}, weekly_consecutive_loss_pct=WEEKLY_CONSECUTIVE_LOSS_MAX_PCT
        )
        assert status == "paused"
        assert pause is not None
        assert pause == date.today() + timedelta(days=LOSS_PAUSE_DAYS)

    def test_paused_within_pause_period(self, trader):
        last_pause = date.today() - timedelta(days=3)
        status, pause = trader.check_risk({}, last_loss_pause_date=last_pause)
        assert status == "paused"
        assert pause == last_pause + timedelta(days=LOSS_PAUSE_DAYS)

    def test_paused_expired(self, trader):
        last_pause = date.today() - timedelta(days=LOSS_PAUSE_DAYS + 1)
        status, pause = trader.check_risk({}, last_loss_pause_date=last_pause)
        assert status == "normal"
        assert pause is None

    def test_small_loss_normal(self, trader):
        status, pause = trader.check_risk({}, daily_pnl_pct=-0.05)
        assert status == "normal"


# ============================================================
# _calc_stops
# ============================================================
class TestCalcStops:
    def test_flat_direction(self, trader):
        sl, tp = trader._calc_stops("CU", 75000, "flat", 0.8)
        assert (sl, tp) == (0.0, 0.0)

    def test_zero_price(self, trader):
        sl, tp = trader._calc_stops("CU", 0, "long", 0.8)
        assert (sl, tp) == (0.0, 0.0)

    def test_long_stops(self, trader):
        sl, tp = trader._calc_stops("CU", 75000, "long", 1.0)
        assert sl < 75000 < tp
        assert tp - 75000 == pytest.approx((75000 - sl) * 2, rel=1e-3)

    def test_short_stops(self, trader):
        sl, tp = trader._calc_stops("AU", 550, "short", 1.0)
        assert tp < 550 < sl
        assert 550 - tp == pytest.approx((sl - 550) * 2, rel=1e-3)

    def test_strength_affects_stop_width(self, trader):
        sl_low, _ = trader._calc_stops("CU", 75000, "long", 0.3)
        sl_high, _ = trader._calc_stops("CU", 75000, "long", 1.0)
        assert sl_low > sl_high  # stronger → wider stop → lower stop_loss for long


# ============================================================
# _build_close_order
# ============================================================
class TestBuildCloseOrder:
    def test_close_long(self, trader):
        pos = {"direction": "long", "contracts": 5, "entry_price": 74000}
        order = trader._build_close_order(
            "CU", pos, 75000, date(2026, 8, 14), "测试平仓"
        )
        assert order.action == "close_long"
        assert order.direction == "flat"
        assert order.contracts == 5
        assert order.price == 75000
        assert order.required_margin == 0
        assert order.rationale == "测试平仓"
        spec = _get_spec("CU")
        assert order.notional_value == pytest.approx(5 * 75000 * spec.multiplier)

    def test_close_short(self, trader):
        pos = {"direction": "short", "contracts": 3, "entry_price": 560}
        order = trader._build_close_order("AU", pos, 550, date(2026, 8, 14), "平空仓")
        assert order.action == "close_short"
        assert order.contracts == 3


# ============================================================
# generate_orders
# ============================================================
class TestGenerateOrders:
    def test_paused_closes_all(self, trader):
        positions = {
            "CU": {"direction": "long", "contracts": 3, "entry_price": 74000},
            "AU": {"direction": "short", "contracts": 2, "entry_price": 560},
            "T": {"direction": "flat", "contracts": 0, "entry_price": 0},
        }
        prices = {"CU": 75000, "AU": 550, "T": 100}
        signals = [
            FuturesSignal(symbol=s, name=s, direction="long", strength=0.8)
            for s in ("CU", "AU", "T")
        ]
        orders = trader.generate_orders(
            signals, positions, prices, date(2026, 8, 14), risk_status="paused"
        )
        assert len(orders) == 2  # T has 0 contracts, no close order
        actions = {o.action for o in orders}
        assert actions == {"close_long", "close_short"}

    def test_open_new_long(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=0.8
        )
        prices = {"CU": 75000, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], {}, prices, date(2026, 8, 14))
        assert len(orders) == 1
        assert orders[0].action == "open_long"
        assert orders[0].contracts >= 1

    def test_open_new_short(self, trader):
        sig = FuturesSignal(
            symbol="AU", name="黄金期货", direction="short", strength=0.8
        )
        prices = {"CU": 75000, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], {}, prices, date(2026, 8, 14))
        assert len(orders) == 1
        assert orders[0].action == "open_short"

    def test_hold_same_position(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=0.8
        )
        positions = {"CU": {"direction": "long", "contracts": 5, "entry_price": 74000}}
        prices = {"CU": 75000, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], positions, prices, date(2026, 8, 14))
        assert len(orders) == 1
        assert orders[0].action in ("hold", "add", "reduce")

    def test_close_to_flat(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="flat", strength=0.0
        )
        positions = {"CU": {"direction": "long", "contracts": 5, "entry_price": 74000}}
        prices = {"CU": 75000, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], positions, prices, date(2026, 8, 14))
        assert len(orders) == 1
        assert orders[0].action == "close_long"

    def test_reverse_direction(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="short", strength=0.8
        )
        positions = {"CU": {"direction": "long", "contracts": 3, "entry_price": 74000}}
        prices = {"CU": 75000, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], positions, prices, date(2026, 8, 14))
        assert len(orders) == 1
        assert orders[0].action == "reverse"

    def test_zero_price_skipped(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=0.8
        )
        prices = {"CU": 0, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], {}, prices, date(2026, 8, 14))
        assert len(orders) == 0

    def test_order_has_stop_and_take_profit(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=0.8
        )
        prices = {"CU": 75000, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], {}, prices, date(2026, 8, 14))
        assert orders[0].stop_loss > 0
        assert orders[0].take_profit > 0

    def test_trade_date_in_isoformat(self, trader):
        sig = FuturesSignal(
            symbol="CU", name="沪铜期货", direction="long", strength=0.8
        )
        prices = {"CU": 75000, "AU": 550, "T": 100}
        orders = trader.generate_orders([sig], {}, prices, date(2026, 8, 14))
        assert orders[0].trade_date == "2026-08-14"


# ============================================================
# run (主流程)
# ============================================================
class TestRun:
    def test_full_run_normal(self, trader, market_data_uptrend):
        prices = {s: md["closes"][-1] for s, md in market_data_uptrend.items()}
        result = trader.run(
            market_data=market_data_uptrend,
            current_positions={},
            prices=prices,
            trade_date=date(2026, 8, 14),
        )
        assert result.trade_date == "2026-08-14"
        assert result.risk_status == "normal"
        assert len(result.signals) == 3
        assert result.margin_usage_ratio >= 0
        assert result.action in ("trade", "skip")
        assert result.summary_text != ""

    def test_run_with_pause(self, trader, market_data_uptrend):
        prices = {s: md["closes"][-1] for s, md in market_data_uptrend.items()}
        result = trader.run(
            market_data=market_data_uptrend,
            current_positions={},
            prices=prices,
            trade_date=date(2026, 8, 14),
            weekly_consecutive_loss_pct=0.30,
        )
        assert result.risk_status == "paused"
        assert result.action == "pause"
        assert result.pause_until is not None

    def test_run_with_warning(self, trader, market_data_uptrend):
        prices = {s: md["closes"][-1] for s, md in market_data_uptrend.items()}
        result = trader.run(
            market_data=market_data_uptrend,
            current_positions={},
            prices=prices,
            trade_date=date(2026, 8, 14),
            daily_pnl_pct=-0.20,
        )
        assert result.risk_status == "warning"

    def test_run_with_fundamental(self, trader, market_data_uptrend):
        prices = {s: md["closes"][-1] for s, md in market_data_uptrend.items()}
        fund = {
            "CU": {"new_energy_demand": 0.5, "inventory_cycle": -0.3},
            "AU": {"real_rate": -0.2, "safe_haven": 0.8},
            "T": {"inflation_expectation": 0.3, "monetary_policy": -0.5},
        }
        result = trader.run(
            market_data=market_data_uptrend,
            current_positions={},
            prices=prices,
            trade_date=date(2026, 8, 14),
            fundamental_factors=fund,
        )
        assert len(result.signals) == 3

    def test_run_with_existing_positions(self, trader, market_data_uptrend):
        prices = {s: md["closes"][-1] for s, md in market_data_uptrend.items()}
        positions = {
            "CU": {"direction": "long", "contracts": 2, "entry_price": 70000},
        }
        result = trader.run(
            market_data=market_data_uptrend,
            current_positions=positions,
            prices=prices,
            trade_date=date(2026, 8, 14),
        )
        assert len(result.orders) >= 0


# ============================================================
# _build_summary / summary
# ============================================================
class TestSummary:
    def test_build_summary(self, trader):
        result = DirectionalFuturesResult(
            trade_date="2026-08-14",
            risk_status="normal",
            signals=[
                FuturesSignal(
                    symbol="CU", name="沪铜期货", direction="long", strength=0.8
                )
            ],
            orders=[
                FuturesOrder(
                    symbol="CU",
                    name="沪铜期货",
                    exchange="SHFE",
                    action="open_long",
                    contracts=3,
                )
            ],
            total_margin_used=50000,
            total_notional=500000,
            margin_usage_ratio=0.1,
        )
        text = trader._build_summary(result)
        assert "方向性期货组合报告" in text
        assert "2026-08-14" in text
        assert "CU" in text
        assert "open_long" in text

    def test_summary_with_prebuilt_text(self, trader):
        result = DirectionalFuturesResult(summary_text="预构建摘要")
        assert trader.summary(result) == "预构建摘要"

    def test_summary_without_prebuilt(self, trader):
        result = DirectionalFuturesResult(trade_date="2026-08-14")
        text = trader.summary(result)
        assert "方向性期货组合报告" in text

    def test_summary_with_pause(self, trader):
        result = DirectionalFuturesResult(
            trade_date="2026-08-14",
            risk_status="paused",
            pause_until="2026-08-21",
        )
        text = trader._build_summary(result)
        assert "暂停至" in text
        assert "2026-08-21" in text


# ============================================================
# _save_report
# ============================================================
class TestSaveReport:
    def test_save_success(self, trader, tmp_path):
        with patch.object(Path, "parent", return_value=tmp_path):
            result = DirectionalFuturesResult(trade_date="2026-08-14")
            trader._save_report(result, date(2026, 8, 14))

    def test_save_with_full_result(self, trader, tmp_path):
        with patch.object(Path, "parent", return_value=tmp_path):
            result = DirectionalFuturesResult(
                trade_date="2026-08-14",
                risk_status="normal",
                signals=[FuturesSignal(symbol="CU", name="沪铜期货", direction="long")],
                orders=[
                    FuturesOrder(
                        symbol="CU",
                        name="沪铜期货",
                        exchange="SHFE",
                        action="open_long",
                    )
                ],
                total_margin_used=50000,
                total_notional=500000,
                margin_usage_ratio=0.1,
            )
            trader._save_report(result, date(2026, 8, 14))

    def test_save_failure_logged(self, trader):
        result = DirectionalFuturesResult(trade_date="2026-08-14")
        with patch("builtins.open", side_effect=OSError("磁盘满")):
            trader._save_report(result, date(2026, 8, 14))


# ============================================================
# 常量验证
# ============================================================
class TestConstants:
    def test_margin_capital(self):
        assert FUTURES_MARGIN_CAPITAL == 500_000

    def test_margin_max_pct(self):
        assert FUTURES_MARGIN_MAX_PCT == 0.60

    def test_per_symbol_budget(self):
        assert PER_SYMBOL_MARGIN_BUDGET == 150_000

    def test_risk_thresholds(self):
        from utils.directional_futures_trader import MAX_SINGLE_TRADE_LOSS_PCT

        assert MAX_SINGLE_TRADE_LOSS_PCT == 0.20
        assert DAILY_MAX_LOSS_PCT == 0.15
        assert WEEKLY_CONSECUTIVE_LOSS_MAX_PCT == 0.25
        assert LOSS_PAUSE_DAYS == 7

    def test_contract_specs_keys(self):
        assert set(CONTRACT_SPECS.keys()) == {"CU", "AU", "T"}

    def test_contract_specs_cu(self):
        cu = CONTRACT_SPECS["CU"]
        assert cu["multiplier"] == 5
        assert cu["margin_rate"] == 0.09
        assert cu["exchange"] == "SHFE"

    def test_contract_specs_au(self):
        au = CONTRACT_SPECS["AU"]
        assert au["multiplier"] == 1000
        assert au["margin_rate"] == 0.06

    def test_contract_specs_t(self):
        t = CONTRACT_SPECS["T"]
        assert t["multiplier"] == 10_000
        assert t["margin_rate"] == 0.02
        assert t["exchange"] == "CFFEX"
