"""验证 hedge_coordinator.py 的 numpy import 修复 (P0 级 Bug).

原始 Bug:
    hedge_coordinator.py 在 coordinate() 方法中调用了 np.isfinite(portfolio_vol),
    但模块顶部缺少 `import numpy as np`, 导致运行时 NameError.

修复:
    在 imports 区添加 `import numpy as np`.

测试覆盖:
    1. np.isfinite 在 NaN / Inf / 正常数值下的行为正确
    2. coordinate() 在全 NaN returns 下不抛 NameError, 退化为 portfolio_vol=0.0
    3. coordinate() 在正常 returns 下计算 portfolio_vol 并传入 tail_hedger
    4. 模块顶部确实导入了 numpy (静态检查)
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_V83_DIR = _PROJECT_ROOT / "v8.3_institutional"
_V83_SRC_DIR = _V83_DIR / "src"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_V83_DIR))
sys.path.insert(0, str(_V83_SRC_DIR))


# ============================================================
# 静态检查: numpy 必须在模块顶部导入
# ============================================================

def test_numpy_is_imported_at_module_level():
    """模块顶部必须有 `import numpy as np`."""
    from hedging import hedge_coordinator as hc_mod  # noqa: F401
    importlib.reload(hc_mod)
    source = inspect.getsource(hc_mod)
    assert "import numpy" in source, "hedge_coordinator.py 必须导入 numpy"
    assert "np.isfinite" in source, "hedge_coordinator.py 必须使用 np.isfinite"


def test_module_has_np_attribute():
    """模块属性 np 必须可用 (import 成功)."""
    from hedging import hedge_coordinator as hc_mod
    importlib.reload(hc_mod)
    assert hasattr(hc_mod, "np"), "hedge_coordinator 模块缺少 np 属性"
    assert hc_mod.np.isfinite is np.isfinite, "np.isfinite 必须指向真正的 numpy.isfinite"


# ============================================================
# 运行时检查: coordinate() 在异常 returns 下不抛 NameError
# ============================================================

def _make_coordinator_with_mocks():
    """构造一个 HedgeCoordinator, tail/beta/vol/corr 全部 mock 掉."""
    from hedging.hedge_coordinator import HedgeCoordinator
    hc = HedgeCoordinator.__new__(HedgeCoordinator)
    hc.beta_hedger = MagicMock()
    hc.vol_hedger = MagicMock()
    hc.corr_hedger = MagicMock()
    hc.tail_hedger = MagicMock()
    hc.enable_tail_risk = True
    hc.max_total_hedge_normal = 0.40
    hc.max_total_hedge_crisis = 0.90
    hc.max_total_hedge_warning = 0.70
    # tail_hedger.current_regime 属性 (max_total_hedge 依赖)
    type(hc.tail_hedger).current_regime = property(lambda self: "normal")
    # 子模块返回 NO_HEDGE
    hc.beta_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    hc.vol_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    hc.corr_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    hc.beta_hedger.portfolio_beta.return_value = 0.5
    hc.tail_hedger.analyze_market_regime.return_value = "normal"
    hc.tail_hedger.compute_hedge.return_value = {"action": "NO_HEDGE"}
    return hc


class TestNumpyImportFix:
    """P0 Bug 回归: numpy NameError 修复."""

    def test_coordinate_with_all_nan_returns_no_nameerror(self):
        """全 NaN returns 下 coordinate() 必须不抛 NameError."""
        hc = _make_coordinator_with_mocks()
        # 全 NaN DataFrame
        returns = pd.DataFrame({"a": [np.nan, np.nan], "b": [np.nan, np.nan]})
        market_returns = pd.Series([np.nan, np.nan])
        positions = {"a": 100, "b": 200}
        prices = {"a": 10.0, "b": 20.0}

        # 不应抛出 NameError
        try:
            result = hc.coordinate(
                positions=positions,
                prices=prices,
                returns=returns,
                market_returns=market_returns,
                vix=20.0,
                portfolio_value=10000.0,
            )
        except NameError as e:
            pytest.fail(f"coordinate() 抛出 NameError (numpy import 未修复): {e}")

        # 应该正常返回 dict
        assert isinstance(result, dict)
        assert result.get("action") in ("HEDGE", "NO_HEDGE", "SKIP")

    def test_coordinate_with_inf_returns_no_nameerror(self):
        """Inf returns 下 coordinate() 必须不抛 NameError."""
        hc = _make_coordinator_with_mocks()
        returns = pd.DataFrame({"a": [np.inf, -np.inf], "b": [0.01, 0.02]})
        market_returns = pd.Series([0.01, 0.02])
        positions = {"a": 100, "b": 200}
        prices = {"a": 10.0, "b": 20.0}

        try:
            result = hc.coordinate(
                positions=positions,
                prices=prices,
                returns=returns,
                market_returns=market_returns,
                vix=20.0,
                portfolio_value=10000.0,
            )
        except NameError as e:
            pytest.fail(f"coordinate() 抛出 NameError: {e}")
        assert isinstance(result, dict)

    def test_coordinate_with_empty_returns_no_nameerror(self):
        """空 returns 下 coordinate() 必须不抛 NameError."""
        hc = _make_coordinator_with_mocks()
        returns = pd.DataFrame()
        market_returns = pd.Series([], dtype=float)
        positions = {"a": 100}
        prices = {"a": 10.0}

        try:
            result = hc.coordinate(
                positions=positions,
                prices=prices,
                returns=returns,
                market_returns=market_returns,
                vix=20.0,
                portfolio_value=10000.0,
            )
        except NameError as e:
            pytest.fail(f"coordinate() 抛出 NameError: {e}")
        assert isinstance(result, dict)

    def test_portfolio_vol_falls_back_to_zero_on_nan(self):
        """当 returns.std().mean() 为 NaN 时, portfolio_vol 应回退为 0.0.

        这验证 np.isfinite 检查的目的: 防止 NaN 传入 analyze_market_regime.
        """
        hc = _make_coordinator_with_mocks()
        # 构造 std()=NaN 的 returns (单行 std=NaN)
        returns = pd.DataFrame({"a": [0.01], "b": [0.02]})
        market_returns = pd.Series([0.01])
        positions = {"a": 100, "b": 200}
        prices = {"a": 10.0, "b": 20.0}

        hc.coordinate(
            positions=positions,
            prices=prices,
            returns=returns,
            market_returns=market_returns,
            vix=20.0,
            portfolio_value=10000.0,
        )

        # analyze_market_regime 应被调用, portfolio_volatility 参数应为有限值 (0.0)
        assert hc.tail_hedger.analyze_market_regime.called, "tail_hedger.analyze_market_regime 未被调用"
        call_kwargs = hc.tail_hedger.analyze_market_regime.call_args.kwargs
        pv = call_kwargs.get("portfolio_volatility", None)
        assert pv is not None, "portfolio_volatility 参数未传递"
        assert np.isfinite(pv), f"portfolio_volatility={pv} 不是有限值 (np.isfinite 修复失效)"
        assert pv == 0.0, f"NaN 输入应回退为 0.0, 实际为 {pv}"

    def test_portfolio_vol_calculated_on_normal_returns(self):
        """正常 returns 下, portfolio_vol 应为正有限值."""
        hc = _make_coordinator_with_mocks()
        # 60 天正常 returns
        np.random.seed(42)
        returns = pd.DataFrame({
            "a": np.random.normal(0, 0.01, 60),
            "b": np.random.normal(0, 0.015, 60),
        })
        market_returns = pd.Series(np.random.normal(0, 0.01, 60))
        positions = {"a": 100, "b": 200}
        prices = {"a": 10.0, "b": 20.0}

        hc.coordinate(
            positions=positions,
            prices=prices,
            returns=returns,
            market_returns=market_returns,
            vix=20.0,
            portfolio_value=10000.0,
        )

        call_kwargs = hc.tail_hedger.analyze_market_regime.call_args.kwargs
        pv = call_kwargs.get("portfolio_volatility", 0.0)
        assert np.isfinite(pv), f"portfolio_volatility={pv} 不是有限值"
        assert pv > 0.0, f"正常 returns 下 portfolio_volatility 应 > 0, 实际 {pv}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
