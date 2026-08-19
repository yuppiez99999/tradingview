"""回归测试: 第三轮扫描发现的静默数值 bug (F-7 / F-8).

这些 bug 的共同特征: 静态工具(lint/bandit/mypy)全部绿, 但会在运行时
静默产生 inf/NaN 或把陈旧数据误报为满分新鲜数据, 直接污染交易决策。
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from utils.black_litterman_optimizer import BlackLittermanOptimizer
from utils.data.data_layer import STALE_QUALITY_SCORE, DataLayer


# ============================================================
# F-7: Black-Litterman risk_aversion(δ) <= 0 → 静默 NaN 权重
# ============================================================
def test_black_litterman_rejects_nonpositive_delta():
    """构造期必须拒绝 δ<=0, 否则 w = cov_inv @ er / δ 在 numpy 下返回 inf 而非抛异常。"""
    for bad in (0.0, -1.0, -2.5):
        with pytest.raises(ValueError):
            BlackLittermanOptimizer(risk_aversion=bad)


def test_black_litterman_valid_delta_yields_finite_weights():
    """δ>0 时, 无约束解权重必须有限(无 inf/NaN), 否则下游仓位全 NaN。"""
    import numpy as np

    opt = BlackLittermanOptimizer(risk_aversion=2.5)
    n = 3
    cov = np.eye(n)  # 非奇异协方差
    expected_returns = np.array([0.10, 0.05, 0.02])
    w = opt._mean_variance_optimize(
        expected_returns,
        cov,
        risk_free_rate=0.0,
        target_return=None,
        max_weight=None,
        min_weight=None,
    )
    assert w.shape == (n,)
    assert np.all(np.isfinite(w)), f"权重含非有限值(inf/NaN): {w}"


# ============================================================
# F-8: P6 缓存兜底(陈旧数据)必须显式标记 stale, 不得质量分=100
# ============================================================
def test_p6_cache_hit_marks_stale(tmp_path):
    """所有实时源失败时回退到 P6 陈旧缓存: from_cache=True 且 quality_score 降级(非满分)。"""
    from unittest.mock import patch

    class _BadMarketDataProvider:
        def get_historical_data(self, *a, **k):
            raise RuntimeError("网络不可用(测试注入)")

        def get_market_data(self, *a, **k):
            raise RuntimeError("网络不可用(测试注入)")

        def get_external_macro(self, *a, **k):
            raise RuntimeError("网络不可用(测试注入)")

    with patch("utils.data_provider.MarketDataProvider", _BadMarketDataProvider):
        layer = DataLayer(
            feature_flag_check=lambda: True,  # 启用新降级链
            enable_data_gate=False,
            cache_ttl_seconds=999999,
            fallback_log_dir=tmp_path / "log",
            p6_cache_dir=tmp_path / "p6",
            auto_register_providers=True,
        )
        # 预置一份"陈旧"缓存
        layer._p6_cache_store("510300.SH", "get_ohlcv", pd.DataFrame({"close": [1.0]}))

        # from_cache / quality_score 在内部 QueryResult 上, 直接调用 _query_with_fallback
        result = layer._query_with_fallback(
            operation="get_ohlcv",
            symbol="510300.SH",
            query_fn=lambda fn: fn("510300.SH"),
        )
        assert result.from_cache is True, "应标记来自 P6 陈旧缓存"
        assert result.provider_level == "P6"
        assert result.quality_score == STALE_QUALITY_SCORE, (
            f"陈旧缓存质量分不应为 100, 实际={result.quality_score}"
        )


def test_p6_cache_hit_logs_stale_warning(tmp_path, caplog):
    """P6 陈旧缓存命中必须打印 stale 告警(履行模块 docstring 的 'stale 警告' 契约)。"""
    from unittest.mock import patch

    class _BadMarketDataProvider:
        def get_historical_data(self, *a, **k):
            raise RuntimeError("网络不可用(测试注入)")

        def get_market_data(self, *a, **k):
            raise RuntimeError("网络不可用(测试注入)")

        def get_external_macro(self, *a, **k):
            raise RuntimeError("网络不可用(测试注入)")

    with patch("utils.data_provider.MarketDataProvider", _BadMarketDataProvider):
        layer = DataLayer(
            feature_flag_check=lambda: True,
            enable_data_gate=False,
            cache_ttl_seconds=999999,
            fallback_log_dir=tmp_path / "log",
            p6_cache_dir=tmp_path / "p6",
            auto_register_providers=True,
        )
        layer._p6_cache_store("510300.SH", "get_ohlcv", pd.DataFrame({"close": [1.0]}))

        with caplog.at_level(logging.WARNING, logger="data_layer"):
            layer.get_ohlcv("510300.SH")
        assert any("P6 陈旧缓存" in r.message for r in caplog.records), (
            "P6 命中未打印 stale 告警"
        )
