"""Q3 契约: 分母类参数构造期必须校验 > 0.

契约: 优化器风险厌恶/收缩强度/置信度等分母类参数,
      构造期必须显式 if x <= 0: raise ValueError.

对应实现: utils/black_litterman_optimizer.py BlackLittermanOptimizer.__init__
"""

from __future__ import annotations

import pytest

from utils.black_litterman_optimizer import BlackLittermanOptimizer


def test_risk_aversion_zero_rejected() -> None:
    """risk_aversion=0 (将致 w=er/0=inf) → 必须 raise ValueError."""
    with pytest.raises(ValueError, match="risk_aversion"):
        BlackLittermanOptimizer(risk_aversion=0.0)


def test_risk_aversion_negative_rejected() -> None:
    """risk_aversion<0 (无意义) → 必须 raise ValueError."""
    with pytest.raises(ValueError, match="risk_aversion"):
        BlackLittermanOptimizer(risk_aversion=-2.5)


def test_tau_zero_rejected() -> None:
    """tau=0 (观点不确定性缩放, 将致除零) → 必须 raise ValueError."""
    with pytest.raises(ValueError, match="tau"):
        BlackLittermanOptimizer(risk_aversion=2.5, tau=0.0)


def test_tau_out_of_range_rejected() -> None:
    """tau>1 (超出 (0,1] 区间) → 必须 raise ValueError."""
    with pytest.raises(ValueError, match="tau"):
        BlackLittermanOptimizer(risk_aversion=2.5, tau=1.5)


def test_default_confidence_out_of_range_rejected() -> None:
    """default_confidence 不在 [0,1] → 必须 raise ValueError."""
    with pytest.raises(ValueError, match="default_confidence"):
        BlackLittermanOptimizer(risk_aversion=2.5, default_confidence=-0.1)
    with pytest.raises(ValueError, match="default_confidence"):
        BlackLittermanOptimizer(risk_aversion=2.5, default_confidence=1.1)


def test_valid_params_accepted() -> None:
    """合法参数 → 正常构造, 不 raise."""
    opt = BlackLittermanOptimizer(risk_aversion=2.5, tau=0.05, default_confidence=0.5)
    assert opt.delta == 2.5
    assert opt.tau == 0.05
