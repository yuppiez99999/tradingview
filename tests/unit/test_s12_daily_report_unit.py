"""每日状态报告生成器单测: 指标计算 / 报告渲染 / 异常标记."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


def _load_gen():
    spec = importlib.util.spec_from_file_location(
        "generate_daily_status_report",
        _PROJECT_ROOT / "scripts" / "generate_daily_status_report.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generate_daily_status_report"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gen():
    return _load_gen()


@pytest.fixture(scope="module")
def config():
    return json.loads(
        (_PROJECT_ROOT / "config" / "s12_shadow_config.json").read_text(encoding="utf-8")
    )


def _state(n_days: int = 0) -> dict:
    """构造 state: n_days 个交易日, 收益交替 ±1%."""
    navs, nav = [], 1.0
    for i in range(n_days):
        ret = 0.01 if i % 2 == 0 else -0.01
        nav *= 1 + ret
        navs.append({"date": f"d{i}", "nav": nav, "daily_return": ret,
                     "capital": 2_000_000 * nav})
    return {
        "account_id": "T", "strategy_id": "S12_DEFENSIVE_RP",
        "recorded_dates": [f"d{i}" for i in range(n_days)],
        "trading_day_count": n_days, "weights": {"518880": 0.33, "511260": 0.34, "512890": 0.33},
        "nav": navs[-1]["nav"] if navs else 1.0, "daily_nav": navs,
        "trade_log": [], "fail_fast_triggered": False, "price_source": "wind_mcp",
    }


class TestComputeMetrics:
    def test_empty(self, gen):
        m = gen.compute_metrics([])
        assert m["n_days"] == 0
        assert m["nav"] == 1.0
        assert m["sharpe"] is None and m["annualized"] is None

    def test_basic(self, gen):
        m = gen.compute_metrics(_state(20)["daily_nav"])
        assert m["n_days"] == 20
        assert m["sharpe"] is not None  # ≥10 日
        assert m["annualized"] is not None  # ≥2 日
        # 交替 ±1%: 峰值回撤必现, 且 ≤2%
        assert 0 < m["max_drawdown"] <= 0.02

    def test_drawdown_deep(self, gen):
        navs = [{"nav": 1.0, "daily_return": 0.0},
                {"nav": 1.10, "daily_return": 0.10},
                {"nav": 0.88, "daily_return": -0.2}]
        m = gen.compute_metrics(navs)
        assert m["max_drawdown"] == pytest.approx(0.2, abs=1e-9)  # (1.10-0.88)/1.10


class TestNextRebalance:
    def test_day5(self, gen):
        assert gen.next_rebalance_days(5) == 17  # k=22 再平衡

    def test_day22_same_day(self, gen):
        assert gen.next_rebalance_days(22) == 21  # 当日已再平衡, 下次 k=43

    def test_day21(self, gen):
        assert gen.next_rebalance_days(21) == 1  # 明日 k=22


class TestBuildReport:
    def test_normal_recorded(self, gen, config):
        state = _state(15)
        state["recorded_dates"][-1] = "2026-09-10"
        state["trade_log"] = [
            {"date": "2026-09-10", "action": "rebalance",
             "old_weights": {}, "new_weights": {"511260": 0.5},
             "turnover": 0.12, "cost": 0.000156}
        ]
        rpt = gen.build_report(state, config, {"LastTaskResult": 0}, "2026-09-10")
        assert "# 每日运行状态报告 — 2026-09-10" in rpt
        assert "15 / 30" in rpt
        assert "✅ 已记录" in rpt
        assert "无异常" in rpt
        assert "rebalance" in rpt

    def test_not_recorded_warns(self, gen, config):
        rpt = gen.build_report(_state(3), config, {"LastTaskResult": 0}, "2026-09-10")
        assert "❌ 未记录" in rpt
        assert "⚠️ 当日未记录" in rpt
        assert "无异常" not in rpt

    def test_degraded_source_and_task_fail(self, gen, config):
        state = _state(3)
        state["price_source"] = "akshare_sina_unadjusted"
        rpt = gen.build_report(state, config, {"LastTaskResult": 1}, "2026-09-10")
        assert "数据源降级至 sina" in rpt
        assert "退出码 1" in rpt

    def test_fail_fast(self, gen, config):
        state = _state(3)
        state["fail_fast_triggered"] = True
        rpt = gen.build_report(state, config, {}, "2026-09-10")
        assert "fail-fast 已触发" in rpt

    def test_window_full_flag(self, gen, config):
        rpt = gen.build_report(_state(30), config, {}, "d29")
        assert "30 交易日窗口已满" in rpt

    def test_empty_state_no_crash(self, gen, config):
        rpt = gen.build_report(_state(0), config, {}, "2026-09-10")
        assert "0 / 30" in rpt
        assert "N/A" in rpt
