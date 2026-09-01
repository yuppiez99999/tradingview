"""Q2 契约: 除零路径必须守卫.

契约: a/b、np.divide、任何 mean/std/var 分母, 可为 0 时须守卫
      (if denom != 0 / np.where(b != 0, a/b, 0) / max(x, eps)).

对应实现: utils/black_litterman_optimizer.py (confidence 除零守卫 + port_vol 守卫)
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from utils.black_litterman_optimizer import BlackLittermanOptimizer, View

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_view_confidence_zero_does_not_divide_by_zero() -> None:
    """confidence=0 的 view → c=max(0,1e-6)=1e-6, 不除零, 优化结果有限."""
    opt = BlackLittermanOptimizer(risk_aversion=2.5, tau=0.05)
    assets = ["A", "B"]
    cov = np.array([[0.04, 0.01], [0.01, 0.09]])
    market_weights = np.array([0.6, 0.4])
    views = [
        View(
            type="absolute",
            assets=["A"],
            weights=[1.0],
            expected_return=0.10,
            confidence=0.0,
        )
    ]
    result = opt.optimize(assets, market_weights, cov, views, risk_free_rate=0.02)
    assert np.isfinite(result.optimal_weights).all(), f"Q2 违约: confidence=0 权重含 inf/NaN: {result.optimal_weights}"
    assert np.isfinite(result.posterior_returns).all()


def test_division_zero_guard_pattern_present_in_source() -> None:
    """静态契约: black_litterman 源码须含除零守卫模式 (max(.*eps) / if .* > 0 else)."""
    src = _PROJECT_ROOT / (BlackLittermanOptimizer.__module__.replace(".", "/") + ".py")
    if not src.exists():
        pytest.skip(f"源码路径解析失败: {src}")
    text = src.read_text(encoding="utf-8", errors="replace")
    has_max_eps = bool(re.search(r"max\([^)]*1e-6", text))
    has_if_guard = bool(re.search(r"if\s+\w+\s*>\s*0\s+else", text))
    assert has_max_eps or has_if_guard, "Q2 违约: 源码未发现除零守卫模式 (max(eps) / if >0 else)"
