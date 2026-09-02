"""S12 影子账户运行器单测: 策略口径 / 幂等 / 再平衡成本 / 无前视.

对照物: data/etf_option_backtest/run_etf_option_backtest.py run_s12_defensive_rp
注意: update() 是补漏式 — 记录面板内 start 之后所有未记录交易日,
测试用短面板控制记录天数.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_s12_shadow", _PROJECT_ROOT / "scripts" / "run_s12_shadow.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_s12_shadow"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runner():
    return _load_runner()


@pytest.fixture(scope="module")
def config():
    return json.loads(
        (_PROJECT_ROOT / "config" / "s12_shadow_config.json").read_text(encoding="utf-8")
    )


def _make_panel(days: int = 130, seed: int = 7) -> pd.DataFrame:
    """3 ETF 模拟面板: 差异化波动率 (国债低/黄金中/红利高)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-01-05", periods=days)
    vols = {"518880": 0.010, "511260": 0.002, "512890": 0.014}
    data = {}
    for c, v in vols.items():
        rets = rng.normal(0.0002, v, days)
        data[c] = 100 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


def _state(start: pd.Timestamp) -> dict:
    return {
        "account_id": "T", "strategy_id": "S12_DEFENSIVE_RP",
        "initial_capital": 2_000_000.0,
        "start_date": start.strftime("%Y-%m-%d"),
        "recorded_dates": [], "trading_day_count": 0,
        "weights": {}, "nav": 1.0, "daily_nav": [], "trade_log": [],
        "fail_fast_triggered": False,
    }


class TestInverseVolWeights:
    def test_no_lookahead(self, runner):
        """asof 日权重只能用 < asof 的数据: 注入未来数据不影响权重."""
        panel = _make_panel()
        asof = panel.index[-1]
        w1 = runner.inverse_vol_weights(panel, list(panel.columns), asof, 60)
        panel2 = panel.copy()
        panel2.loc[asof:] = panel2.loc[asof:] * 1.5
        w2 = runner.inverse_vol_weights(panel2, list(panel2.columns), asof, 60)
        assert w1 == pytest.approx(w2, abs=1e-12)

    def test_low_vol_gets_more_weight(self, runner):
        """低波动资产 (国债) 权重应显著高于高波动资产."""
        panel = _make_panel()
        w = runner.inverse_vol_weights(panel, list(panel.columns), panel.index[-1], 60)
        assert w["511260"] > w["518880"] > 0
        assert w["512890"] < w["518880"]
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-9)

    def test_insufficient_history_equal_weight(self, runner):
        """历史不足 2 条 → 均权回退."""
        panel = _make_panel(2)
        w = runner.inverse_vol_weights(panel, list(panel.columns), panel.index[-1], 60)
        assert w == pytest.approx({c: 1 / 3 for c in panel.columns})


class TestUpdate:
    def test_first_day_nav_one_equal_weight(self, runner, config, monkeypatch):
        """首日建仓: NAV=1.0, 等权, trade_log 记 init (面板只留 1 个待记日)."""
        panel = _make_panel(6)
        state = _state(panel.index[5])
        monkeypatch.setattr(runner, "fetch_prices", lambda c: (panel, "test"))
        out = runner.update(state, config)
        assert out["recorded_dates"] == [panel.index[5].strftime("%Y-%m-%d")]
        assert out["nav"] == 1.0
        assert out["weights"] == pytest.approx({c: 1 / 3 for c in config["universe"]})
        assert out["trade_log"][0]["action"] == "init"
        assert out["trading_day_count"] == 1

    def test_idempotent(self, runner, config, monkeypatch):
        """重复运行同一天: 不重复记录 (幂等)."""
        panel = _make_panel(6)
        state = _state(panel.index[5])
        monkeypatch.setattr(runner, "fetch_prices", lambda c: (panel, "test"))
        out1 = runner.update(state, config)
        out2 = runner.update(out1, config)
        assert out2["recorded_dates"] == out1["recorded_dates"]
        assert out2["nav"] == out1["nav"]
        assert out2["trading_day_count"] == out1["trading_day_count"]
        assert len(out2["trade_log"]) == len(out1["trade_log"])

    def test_catchup_records_all_pending(self, runner, config, monkeypatch):
        """补漏: 一次运行补齐 start 之后全部待记交易日."""
        panel = _make_panel(10)
        state = _state(panel.index[0])
        monkeypatch.setattr(runner, "fetch_prices", lambda c: (panel, "test"))
        out = runner.update(state, config)
        assert out["trading_day_count"] == 10
        assert len(out["recorded_dates"]) == 10

    def test_daily_return_matches_manual(self, runner, config, monkeypatch):
        """第 2 日 NAV = 1 + Σ w·ret (手算对照)."""
        panel = _make_panel(7)
        d0, d1 = panel.index[5], panel.index[6]
        state = _state(d0)
        monkeypatch.setattr(runner, "fetch_prices", lambda c: (panel, "test"))
        out = runner.update(state, config)
        exp = sum(
            (1 / 3) * (panel.loc[d1, c] / panel.loc[d0, c] - 1)
            for c in config["universe"]
        )
        assert out["nav"] == pytest.approx(1.0 + exp, rel=1e-12)
        assert out["daily_nav"][-1]["daily_return"] == pytest.approx(exp, rel=1e-12)

    def test_rebalance_at_day22_with_cost(self, runner, config, monkeypatch):
        """第 22 交易日 (回测 i%21==0 口径) 再平衡: 权重切换 + 成本扣除."""
        panel = _make_panel(40)
        state = _state(panel.index[0])
        monkeypatch.setattr(runner, "fetch_prices", lambda c: (panel, "test"))
        out = runner.update(state, config)
        rebal = [t for t in out["trade_log"] if t["action"] == "rebalance"]
        assert len(rebal) == 1  # k=22 触发 ((k-1)%21==0)
        assert out["trading_day_count"] == 40
        assert rebal[0]["old_weights"] == pytest.approx(
            {c: 1 / 3 for c in config["universe"]}, abs=1e-6
        )
        assert rebal[0]["new_weights"]["511260"] > rebal[0]["new_weights"]["512890"]
        assert rebal[0]["turnover"] > 0
        assert rebal[0]["cost"] == pytest.approx(
            rebal[0]["turnover"]
            * (config["transaction_cost"] + config["slippage"]), rel=1e-9
        )

    def test_nav_matches_backtest_engine(self, runner, config, monkeypatch):
        """与回测引擎逐日对齐: 同一价格序列下 NAV 轨迹一致 (核心口径验证)."""
        spec = importlib.util.spec_from_file_location(
            "etf_ref_backtest",
            _PROJECT_ROOT / "data" / "etf_option_backtest" / "run_etf_option_backtest.py",
        )
        bt = importlib.util.module_from_spec(spec)
        sys.modules["etf_ref_backtest"] = bt
        spec.loader.exec_module(bt)

        panel = _make_panel(60)
        eq_bt, _tc = bt.run_s12_defensive_rp(panel, {})
        state = _state(panel.index[0])
        monkeypatch.setattr(runner, "fetch_prices", lambda c: (panel, "test"))
        out = runner.update(state, config)
        # 影子日 k=1 ↔ 回测 day0 (同 NAV=1.0), 逐日对齐
        navs = [e["nav"] for e in out["daily_nav"]]
        bt_norm = np.asarray(eq_bt, dtype=float) / float(eq_bt[0])
        assert len(navs) == len(bt_norm) == 60
        np.testing.assert_allclose(navs, bt_norm, rtol=1e-10)
