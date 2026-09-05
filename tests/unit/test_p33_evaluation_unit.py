"""P3.3 评估脚本单测: 分位数 / 滚动窗口年化 / 四项验收判定."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


def _load_eval():
    spec = importlib.util.spec_from_file_location(
        "run_p33_evaluation", _PROJECT_ROOT / "scripts" / "run_p33_evaluation.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_p33_evaluation"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ev():
    return _load_eval()


class TestPercentile:
    def test_basic(self, ev):
        vals = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
        assert ev.percentile(vals, 0.0) == 1.0
        assert ev.percentile(vals, 0.5) == 3.0
        assert ev.percentile(vals, 1.0) == 5.0

    def test_interpolation(self, ev):
        vals = sorted([1.0, 2.0, 3.0, 4.0])
        assert ev.percentile(vals, 0.25) == pytest.approx(1.75)
        assert ev.percentile(vals, 0.5) == pytest.approx(2.5)

    def test_empty(self, ev):
        import math
        assert math.isnan(ev.percentile([], 0.5))


class TestRollingAnnualized:
    def test_flat(self, ev):
        """全平 NAV → 年化 0."""
        eq = [1.0] * 40
        wins = ev.rolling_window_annualized(eq, 30)
        assert len(wins) == 11
        assert all(w == pytest.approx(0.0) for w in wins)

    def test_steady_growth(self, ev):
        """日涨 0.1% → 每窗口年化 ≈ 0.001*252 = 25.2% (复利略低)."""
        eq = [1.0]
        for _ in range(35):
            eq.append(eq[-1] * 1.001)
        wins = ev.rolling_window_annualized(eq, 30)
        assert len(wins) == 7  # 36 个 NAV 点 → 7 个窗口
        # 窗口含 30 点 = 29 次增长: 年化 = 1.001^29^(252/30) - 1 ≈ 27.6%
        assert all(0.24 < w < 0.28 for w in wins)

    def test_window_count(self, ev):
        eq = [float(i) + 1.0 for i in range(50)]
        assert len(ev.rolling_window_annualized(eq, 30)) == 21


class TestEvaluateAcceptance:
    def test_all_pass(self, ev):
        checks, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert ok
        assert all(c["pass"] for c in checks.values())

    def test_negative_return_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=-0.01, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_drawdown_limit(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.16,  # > 15%
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_excess_rebalance_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=3,  # > 2
            max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_extreme_single_turnover_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.8,  # > 50%
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_outside_dist_band_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.50,  # 远超 P95=25%
            dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_none_ann_fails_dist_check(self, ev):
        checks, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=None, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok
        assert not checks["backtest_consistent"]["pass"]


# ============================================================
# R-5 一致性四项 (2026-09-05): NAV 对账 / 成本偏差 / 节奏 / 数据源
# ============================================================

def _make_nav(n=5, ret=0.001, initial=2_000_000.0, corrupt_idx=None):
    """构造自洽 daily_nav; corrupt_idx 指定日破坏 NAV 链路."""
    out = [{"date": "2026-09-01", "nav": 1.0,
            "daily_return": 0.0, "capital": initial,
            "recorded_at": "2026-09-02T16:30:00"}]
    for i in range(1, n):
        nav = out[-1]["nav"] * (1 + ret)
        if i == corrupt_idx:
            nav *= 1.01  # 注入 1% 断链
        out.append({"date": f"2026-09-{1+i:02d}", "nav": nav,
                    "daily_return": ret, "capital": nav * initial,
                    "recorded_at": "2026-09-02T16:30:00"})
    return out


class TestEvaluateConsistency:
    def test_all_consistent_pass(self, ev):
        nav = _make_nav(6)
        checks, ok = ev.evaluate_consistency(nav, [], 2_000_000.0)
        assert ok
        assert checks["nav_reconciliation"]["pass"]
        assert checks["cost_deviation"]["pass"]      # 无再平衡 N/A
        assert checks["rebalance_cadence"]["pass"]   # <2 次 N/A
        assert checks["source_stability"]["pass"]

    def test_nav_chain_broken_fails(self, ev):
        """修复前: 无 NAV 对账检查, 链路断裂不可见 (R-5 增量的核心价值)."""
        nav = _make_nav(6, corrupt_idx=3)
        checks, ok = ev.evaluate_consistency(nav, [], 2_000_000.0)
        assert not checks["nav_reconciliation"]["pass"]
        assert not ok
        assert "2026-09-04" in checks["nav_reconciliation"]["detail"]

    def test_capital_mismatch_fails(self, ev):
        nav = _make_nav(4)
        nav[2]["capital"] = nav[2]["capital"] * 1.05  # capital 与 NAV 脱钩
        checks, ok = ev.evaluate_consistency(nav, [], 2_000_000.0)
        assert not checks["nav_reconciliation"]["pass"]
        assert not ok

    def test_cost_within_band_pass(self, ev):
        nav = _make_nav(3)
        # 单次再平衡 (避免触发节奏判定): 0.0011 / 0.0013 ≈ 0.85 ∈ [0.5, 2.0]
        trades = [{"date": "2026-09-02", "action": "rebalance", "cost": 0.0011}]
        checks, ok = ev.evaluate_consistency(nav, trades, 2_000_000.0)
        assert checks["cost_deviation"]["pass"]
        assert ok
        assert "0.1300" in checks["cost_deviation"]["detail"]  # 0.0013 → 0.1300%

    def test_cost_spike_fails(self, ev):
        """实际单次成本 0.004 = 回测口径 3 倍 → FAIL (修复前: 成本仅统计总额不可见)."""
        nav = _make_nav(3)
        trades = [{"date": "2026-09-02", "action": "rebalance", "cost": 0.004}]
        checks, ok = ev.evaluate_consistency(nav, trades, 2_000_000.0)
        assert not checks["cost_deviation"]["pass"]
        assert not ok

    def test_cadence_in_tolerance_pass(self, ev):
        """间隔 21±5 交易日 PASS (21 日规则口径)."""
        nav, trades = [], []
        nav.append({"date": "2026-09-01", "nav": 1.0, "daily_return": 0.0, "capital": 2_000_000.0})
        # 21 交易日间隔两次再平衡
        for i in range(1, 45):
            nav.append({"date": f"2026-{10 + (i // 28)}-{1 + i % 28:02d}",
                        "nav": 1.0, "daily_return": 0.0, "capital": 2_000_000.0})
        d0, d1 = nav[1]["date"], nav[22]["date"]
        trades = [
            {"date": d0, "action": "rebalance", "cost": 0.0013},
            {"date": d1, "action": "rebalance", "cost": 0.0013},
        ]
        checks, ok = ev.evaluate_consistency(nav, trades, 2_000_000.0)
        assert checks["rebalance_cadence"]["pass"], checks["rebalance_cadence"]["detail"]
        assert ok

    def test_cadence_drift_fails(self, ev):
        """间隔远超 21±5 → FAIL (该再平衡而未再平衡)."""
        nav = [{"date": f"2026-09-{1+i:02d}", "nav": 1.0, "daily_return": 0.0, "capital": 2_000_000.0}
               for i in range(10)]
        trades = [
            {"date": nav[0]["date"], "action": "rebalance", "cost": 0.0013},
            {"date": nav[9]["date"], "action": "rebalance", "cost": 0.0013},  # 间隔 9 日
        ]
        checks, ok = ev.evaluate_consistency(nav, trades, 2_000_000.0)
        assert not checks["rebalance_cadence"]["pass"]
        assert not ok

    def test_source_artifact_return_fails(self, ev):
        """单日 |收益| > 3% → 疑似数据源切换伪影 FAIL (修复前: 仅看回撤不可见单日跳变)."""
        nav = _make_nav(5, corrupt_idx=None)
        nav[3]["daily_return"] = 0.05  # 5% 单日跳变
        # 保持 NAV 链路与跳变自洽, 隔离验证 source_stability 维度
        for i in range(3, 5):
            prev = nav[i - 1]["nav"]
            r = nav[i]["daily_return"] if i == 3 else 0.001
            nav[i]["nav"] = prev * (1 + r)
            nav[i]["capital"] = nav[i]["nav"] * 2_000_000.0
        checks, ok = ev.evaluate_consistency(nav, [], 2_000_000.0)
        assert not checks["source_stability"]["pass"]
        assert not ok

    def test_initial_capital_zero_still_runs(self, ev):
        """防御: initial_capital 缺失 (0.0) 时不崩溃, capital 检查跳过."""
        nav = [{"date": "2026-09-02", "nav": 1.0, "daily_return": 0.0}]  # 无 capital 键
        checks, ok = ev.evaluate_consistency(nav, [], 0.0)
        assert checks["nav_reconciliation"]["pass"]
        assert ok
