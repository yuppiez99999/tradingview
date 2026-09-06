"""Phase 2 — ComboBacktest IV Rank 自适应参数化与对比测试.

unit 级 (默认套件运行, 无 integration mark):
    - 静态回归: 参数化改造后 COLLAR 模拟与旧硬编码 (0.95/1.05/45d) 逐分一致
    - etf_prices list 输入 (修复存量 AttributeError)
    - adaptive 分档参数生效 + tier/iv_rank 落盘 + 字段继承
    - fail-open: 无 rank / 配置未启用 / rank 越界 → 与 static 逐分一致
    - run_comparison: 结构 / delta 口径 / 确定性 / 三档全覆盖
"""

from __future__ import annotations

import pytest

from utils.etf_option_combo.combo_backtest import ComboBacktest
from utils.etf_option_combo.combo_base import StrategyType

_S = 3.0
_COMMISSION_X2 = 10.0  # collar 双腿 × 5 元/张 (默认配置)
_BARS_4D = [
    {"date": "2026-01-05", "close": 3.00},
    {"date": "2026-01-06", "close": 3.02},
    {"date": "2026-01-07", "close": 2.98},
    {"date": "2026-01-08", "close": 3.01},
]


@pytest.fixture
def bt() -> ComboBacktest:
    return ComboBacktest(config={"total_capital": 2_000_000})


def _high_cfg() -> dict:
    return {
        "enabled": True,
        "tiers": [
            {"name": "high", "max_rank": 101, "params": {
                "put_otm_pct": 0.07, "call_otm_pct": 0.03,
                "dte_min": 45, "dte_max": 90,
            }},
        ],
    }


class TestStaticRegression:
    def test_static_collar_matches_legacy_formula(self, bt):
        """静态模式与旧硬编码 0.95/1.05/45d 逐分一致 (参数化零回归)."""
        res = bt.run_backtest(
            "2026-01-05", "2026-01-05",
            strategies=[StrategyType.COLLAR],
            etf_prices=[{"date": "2026-01-05", "close": _S}],
            initial_iv=0.20,
        )
        put_p = ComboBacktest._bs_put_price(_S, _S * 0.95, 45 / 365, 0.03, 0.20)
        call_p = ComboBacktest._bs_call_price(_S, _S * 1.05, 45 / 365, 0.03, 0.20)
        net_cost = (put_p - call_p) * 10000 + _COMMISSION_X2
        trade = res["trades"][0]
        assert trade["strategy"] == "collar"
        assert trade["net_cost"] == round(net_cost, 2)
        assert trade["pnl"] == round(-net_cost, 2)
        assert "tier" not in trade
        assert res["param_mode"] == "static"

    def test_etf_prices_list_input(self, bt):
        """etf_prices 直接传 list 不再 AttributeError (存量 bug 修复)."""
        res = bt.run_backtest(
            "2026-01-05", "2026-01-08",
            strategies=[StrategyType.COLLAR],
            etf_prices=_BARS_4D,
        )
        assert len(res["equity_curve"]) == 4

    def test_etf_prices_dict_input(self, bt):
        """dict 形式 {underlying: bars} 仍然可用."""
        res = bt.run_backtest(
            "2026-01-05", "2026-01-08",
            strategies=[StrategyType.COLLAR],
            etf_prices={"510050.SH": _BARS_4D},
        )
        assert len(res["equity_curve"]) == 4


class TestAdaptiveParams:
    def test_high_tier_params_effective(self, bt):
        """rank=85 → high 档: put 7% OTM / call 3% OTM / dte 窗口中点 67.5, tier 落盘."""
        res = bt.run_backtest(
            "2026-01-05", "2026-01-05",
            strategies=[StrategyType.COLLAR],
            etf_prices=[{"date": "2026-01-05", "close": _S}],
            initial_iv=0.20,
            iv_series={"2026-01-05": 85},
            param_mode="adaptive",
            iv_adaptive_cfg=_high_cfg(),
        )
        dte = (45 + 90) / 2.0
        put_p = ComboBacktest._bs_put_price(_S, _S * 0.93, dte / 365, 0.03, 0.20)
        call_p = ComboBacktest._bs_call_price(_S, _S * 1.03, dte / 365, 0.03, 0.20)
        net_cost = (put_p - call_p) * 10000 + _COMMISSION_X2
        trade = res["trades"][0]
        assert trade["tier"] == "high"
        assert trade["iv_rank"] == 85
        assert trade["net_cost"] == round(net_cost, 2)
        assert res["param_mode"] == "adaptive"

    def test_tier_field_inheritance(self, bt):
        """tier 仅覆盖 put/call otm, 未给 dte → 继承静态窗口 [30,60] → dte 45."""
        cfg = {"enabled": True, "tiers": [
            {"name": "low", "max_rank": 30, "params": {"put_otm_pct": 0.03, "call_otm_pct": 0.07}},
        ]}
        res = bt.run_backtest(
            "2026-01-05", "2026-01-05",
            strategies=[StrategyType.COLLAR],
            etf_prices=[{"date": "2026-01-05", "close": _S}],
            initial_iv=0.20,
            iv_series={"2026-01-05": 10},
            param_mode="adaptive",
            iv_adaptive_cfg=cfg,
        )
        put_p = ComboBacktest._bs_put_price(_S, _S * 0.97, 45 / 365, 0.03, 0.20)
        call_p = ComboBacktest._bs_call_price(_S, _S * 1.07, 45 / 365, 0.03, 0.20)
        trade = res["trades"][0]
        assert trade["tier"] == "low"
        assert trade["iv_rank"] == 10
        assert trade["net_cost"] == round((put_p - call_p) * 10000 + _COMMISSION_X2, 2)

    def test_fail_open_no_rank(self, bt):
        """adaptive 但当日无 rank → fail-open 回静态, 交易与 static 逐分一致."""
        static = bt.run_backtest(
            "2026-01-05", "2026-01-08",
            strategies=[StrategyType.COLLAR], etf_prices=_BARS_4D, initial_iv=0.20,
        )
        adaptive = bt.run_backtest(
            "2026-01-05", "2026-01-08",
            strategies=[StrategyType.COLLAR], etf_prices=_BARS_4D, initial_iv=0.20,
            iv_series={}, param_mode="adaptive", iv_adaptive_cfg=_high_cfg(),
        )
        assert adaptive["trades"] == static["trades"]
        assert adaptive["equity_curve"] == static["equity_curve"]

    def test_fail_open_cfg_disabled(self, bt):
        """iv_adaptive_cfg.enabled=False → tier 解析旁路, 与 static 一致."""
        cfg = {"enabled": False, "tiers": _high_cfg()["tiers"]}
        adaptive = bt.run_backtest(
            "2026-01-05", "2026-01-08",
            strategies=[StrategyType.COLLAR], etf_prices=_BARS_4D, initial_iv=0.20,
            iv_series={b["date"]: 85 for b in _BARS_4D},
            param_mode="adaptive", iv_adaptive_cfg=cfg,
        )
        for trade in adaptive["trades"]:
            assert "tier" not in trade

    def test_fail_open_rank_out_of_range(self, bt):
        """rank 越界 (150) → 告警回静态, 不产生 tier."""
        adaptive = bt.run_backtest(
            "2026-01-05", "2026-01-05",
            strategies=[StrategyType.COLLAR],
            etf_prices=[{"date": "2026-01-05", "close": _S}], initial_iv=0.20,
            iv_series={"2026-01-05": 150},
            param_mode="adaptive", iv_adaptive_cfg=_high_cfg(),
        )
        assert "tier" not in adaptive["trades"][0]


class TestRunComparison:
    def test_comparison_structure(self, bt):
        """对比结果结构完整, 两侧 param_mode 正确."""
        c = bt.run_comparison(
            "2026-01-05", "2026-01-08",
            iv_series={b["date"]: 85 for b in _BARS_4D},
            iv_adaptive_cfg=_high_cfg(),
            etf_prices=_BARS_4D,
        )
        assert {"static", "adaptive", "delta", "avg_net_cost", "iv_series"} <= set(c)
        assert c["static"]["param_mode"] == "static"
        assert c["adaptive"]["param_mode"] == "adaptive"
        assert set(c["delta"]) == {"max_drawdown", "sharpe", "hedge_efficiency", "avg_net_cost"}
        assert c["avg_net_cost"]["static"] is not None
        assert c["avg_net_cost"]["adaptive"] is not None
        # 两侧共用同一价格序列
        assert [p["date"] for p in c["static"]["equity_curve"]] == \
            [p["date"] for p in c["adaptive"]["equity_curve"]]

    def test_comparison_delta_zero_when_cfg_empty(self, bt):
        """配置为空 dict → adaptive 侧 fail-open, delta 全 0 (口径自洽)."""
        c = bt.run_comparison(
            "2026-01-05", "2026-01-08",
            iv_series={b["date"]: 85 for b in _BARS_4D},
            iv_adaptive_cfg={},
            etf_prices=_BARS_4D,
        )
        assert c["delta"] == {
            "max_drawdown": 0.0, "sharpe": 0.0,
            "hedge_efficiency": 0.0, "avg_net_cost": 0.0,
        }

    def test_comparison_deterministic(self, bt):
        """缺省 iv_series/cfg (合成价格+正弦 rank+yaml tiers) 结果可复现."""
        c1 = bt.run_comparison("2026-01-01", "2026-06-30")
        c2 = bt.run_comparison("2026-01-01", "2026-06-30")
        assert c1["delta"] == c2["delta"]
        assert c1["iv_series"] == c2["iv_series"]

    def test_comparison_default_covers_all_tiers(self, bt):
        """默认正弦 rank 序列全年覆盖 low/mid/high 三档 (yaml tiers)."""
        c = bt.run_comparison("2026-01-01", "2026-12-31")
        tiers = {t.get("tier") for t in c["adaptive"]["trades"]}
        assert {"low", "mid", "high"} <= tiers
