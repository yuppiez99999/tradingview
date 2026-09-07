"""T3 单测: CVXPY 凸优化组合权重 (Black-Litterman + Ledoit-Wolf + 成本惩罚).

覆盖:
    1. optimize_weights_cvx 正常求解: 约束满足 (exposure/单票上限/换手/非负);
    2. 强信号与弱信号下的行为差异 (信号驱动性);
    3. 数据不足 / 空输入 fail-open 回退线性混合;
    4. optimize_target_weights flag 分流: 关闭 → linear_blend, 开启 → cvx + 影子报告.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.portfolio_optimizer import PortfolioOptimizer  # noqa: E402

_BASE = {
    "588000": 0.10,
    "300308": 0.08,
    "601088": 0.08,
    "512890": 0.06,
    "511260": 0.08,
}
_SIGNALS = {"588000": 0.8, "300308": 0.5, "601088": -0.3, "512890": 0.0, "511260": -0.6}


def _returns_history(n_days: int = 120, seed: int = 42) -> dict[str, np.ndarray]:
    """构造与信号方向自洽的收益率历史 (高信号 → 高均值收益)."""
    rng = np.random.default_rng(seed)
    means = {
        "588000": 0.0010,
        "300308": 0.0006,
        "601088": -0.0002,
        "512890": 0.0,
        "511260": -0.0005,
    }
    vols = {
        "588000": 0.018,
        "300308": 0.022,
        "601088": 0.015,
        "512890": 0.008,
        "511260": 0.002,
    }
    return {sym: rng.normal(means[sym], vols[sym], n_days) for sym in _BASE}


class TestOptimizeWeightsCvx:
    def test_normal_solve_satisfies_constraints(self):
        opt = PortfolioOptimizer()
        weights, stats = opt.optimize_weights_cvx(_BASE, _SIGNALS, _returns_history(), current_weights=dict(_BASE))
        assert stats["fallback"] is False, f"应正常求解, 实际 {stats}"
        assert stats["solver"] != "none"
        for sym, w in weights.items():
            assert w >= -1e-9, f"{sym} 权重非负违反: {w}"
            assert w <= opt.CVX_DEFAULT_MAX_WEIGHT + 1e-6, f"{sym} 超单票上限: {w}"
        total = sum(weights.values())
        assert total <= 1.0 + 1e-6, f"总暴露超上限: {total}"
        assert stats["turnover"] <= opt.CVX_DEFAULT_TURNOVER_LIMIT + 1e-6

    def test_signal_directional_bias(self):
        """强多头信号标的权重应不低于空头信号标的."""
        opt = PortfolioOptimizer()
        weights, stats = opt.optimize_weights_cvx(_BASE, _SIGNALS, _returns_history(seed=7))
        assert stats["fallback"] is False
        # 588000 信号 +0.8, 511260 信号 -0.6, base 权重相近
        assert weights["588000"] >= weights["511260"]

    def test_insufficient_history_fails_open(self):
        opt = PortfolioOptimizer()
        short = {sym: np.random.default_rng(0).normal(0, 0.01, 10) for sym in _BASE}
        weights, stats = opt.optimize_weights_cvx(_BASE, _SIGNALS, short)
        assert stats["fallback"] is True
        assert "insufficient_history" in stats["reason"]
        # 回退值应与线性混合一致
        assert weights == opt.adjust_target_weights(_BASE, _SIGNALS)

    def test_empty_symbols(self):
        opt = PortfolioOptimizer()
        weights, stats = opt.optimize_weights_cvx({}, {}, {})
        assert weights == {}
        assert stats["fallback"] is True

    def test_turnover_constraint_binds(self):
        """换手约束: 空仓起点下 turnover 不超限."""
        opt = PortfolioOptimizer()
        current = {sym: 0.0 for sym in _BASE}
        weights, stats = opt.optimize_weights_cvx(_BASE, _SIGNALS, _returns_history(), current_weights=current)
        assert stats["fallback"] is False
        assert stats["turnover"] <= opt.CVX_DEFAULT_TURNOVER_LIMIT + 1e-6


class TestOptimizeTargetWeightsFlagRouting:
    def test_flag_off_returns_linear(self, monkeypatch):
        monkeypatch.setattr(
            "utils.infra.feature_flags.FeatureFlags.is_enabled",
            lambda self, name: False,
        )
        opt = PortfolioOptimizer()
        weights, stats = opt.optimize_target_weights(_BASE, _SIGNALS, _returns_history())
        assert stats["path"] == "linear_blend"
        assert weights == opt.adjust_target_weights(_BASE, _SIGNALS)

    def test_flag_on_routes_to_cvx(self, monkeypatch):
        from utils.infra.feature_flags import FeatureFlags

        instance = FeatureFlags.get_instance()
        monkeypatch.setattr(instance, "_overrides", {"USE_CVX_PORTFOLIO_OPTIMIZER": True})
        opt = PortfolioOptimizer()
        weights, stats = opt.optimize_target_weights(_BASE, _SIGNALS, _returns_history())
        if not stats.get("fallback"):
            assert stats["path"] == "cvx_bl_lw"
            assert "588000" in weights
        else:
            assert stats["path"] == "linear_blend_fallback"

    def test_no_returns_history_returns_linear(self, monkeypatch):
        monkeypatch.setattr(
            "utils.infra.feature_flags.FeatureFlags.is_enabled",
            lambda self, name: True,
        )
        opt = PortfolioOptimizer()
        weights, stats = opt.optimize_target_weights(_BASE, _SIGNALS, None)
        assert stats["path"] == "linear_blend"
