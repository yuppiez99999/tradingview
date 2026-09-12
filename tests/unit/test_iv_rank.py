"""IVRankProvider 单元测试.

测试 RV-based IV Rank 数据源的三级降级链:
    1. Wind MCP kline (monkeypatch fake) → 滚动 RV 序列 → percentile rank
    2. shadow_state.json daily_return (只读) → 滚动 RV 序列
    3. 历史缓存 history 段 → rank 兜底
    全失败 → None (fail-open)

模式照抄 tests/unit/test_vix_data_source.py (路径 patch + CACHE_PATH 注入).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.datetime_utils import now_bj

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha import iv_rank as iv_rank_module
from utils.alpha.iv_rank import IVRankProvider


# ============================================================
# 工具: 合成 K 线 / 合成 shadow_state
# ============================================================
def _make_kline(days: int = 320, base_price: float = 3.0, daily_sigma: float = 0.01) -> list[dict]:
    """合成日 K 线 (正弦+噪声风格确定性序列, 模拟日波动)."""
    import math
    bars = []
    price = base_price
    d = datetime(2025, 6, 1)
    for i in range(days):
        ret = math.sin(i * 0.7) * daily_sigma
        price *= 1.0 + ret
        bars.append({"date": (d + timedelta(days=i)).strftime("%Y-%m-%d"), "close": round(price, 4)})
    return bars


def _make_shadow_state(n_days: int = 120, daily_sigma: float = 0.01) -> dict:
    """合成 shadow_state.json 结构 (daily_nav 含 daily_return)."""
    import math
    rows = []
    d = datetime(2025, 6, 1)
    for i in range(n_days):
        rows.append({
            "date": (d + timedelta(days=i)).strftime("%Y-%m-%d"),
            "nav": 1.0,
            "daily_return": math.sin(i * 0.7) * daily_sigma,
        })
    return {"current_nav": 1.0, "daily_nav": rows}


@pytest.fixture
def temp_cache_path(tmp_path) -> Path:
    """临时缓存文件路径 (隔离 reports/volatility)."""
    return tmp_path / "iv_rank_cache.json"


@pytest.fixture
def provider(temp_cache_path) -> IVRankProvider:
    """默认配置 provider, 缓存重定向到 tmp_path."""
    return IVRankProvider(cache_path=temp_cache_path)


# ============================================================
# 纯函数: compute_rank
# ============================================================
class TestComputeRank:
    def test_empty_series_raises(self):
        with pytest.raises(ValueError):
            IVRankProvider.compute_rank([], 20.0)

    def test_single_point_raises(self):
        with pytest.raises(ValueError):
            IVRankProvider.compute_rank([20.0], 20.0)

    def test_percentile_basic(self):
        series = [10.0, 20.0, 30.0, 40.0]
        # 小于 25 的占 2/4 = 50
        assert IVRankProvider.compute_rank(series, 25.0) == 50

    def test_percentile_clamp_zero(self):
        series = [10.0, 20.0, 30.0]
        assert IVRankProvider.compute_rank(series, 5.0) == 0

    def test_percentile_clamp_hundred(self):
        series = [10.0, 20.0, 30.0]
        assert IVRankProvider.compute_rank(series, 99.0) == 100

    def test_minmax_basic(self):
        series = [10.0, 20.0, 30.0, 40.0]
        # (25-10)/(40-10) = 50%
        assert IVRankProvider.compute_rank(series, 25.0, method="minmax") == 50

    def test_minmax_no_dispersion_raises(self):
        with pytest.raises(ValueError):
            IVRankProvider.compute_rank([20.0, 20.0, 20.0], 20.0, method="minmax")


# ============================================================
# 主链: Wind kline (monkeypatch fake)
# ============================================================
class TestWindKlineChain:
    def test_fetch_from_wind_kline(self, provider, temp_cache_path):
        """主链可用时返回 rank 且缓存落盘."""
        kline = _make_kline(days=320)
        with patch.object(provider, "_rv_series_from_wind_kline", return_value=provider._rolling_rv(
            [(b["date"], b["close"]) for b in kline]
        )):
            rank = provider.fetch_iv_rank(use_cache=False)
        assert rank is not None
        assert 0 <= rank <= 100
        assert temp_cache_path.exists()

    def test_wind_kline_short_history_returns_none(self, provider):
        """RV 序列 < min_history_days → None."""
        kline = _make_kline(days=30)
        with patch.object(provider, "_rv_series_from_wind_kline", return_value=provider._rolling_rv(
            [(b["date"], b["close"]) for b in kline]
        )):
            rank = provider.fetch_iv_rank(use_cache=False)
        assert rank is None

    def test_low_vol_gives_low_rank(self, temp_cache_path):
        """构造递增波动序列: 尾部高波动 → 高 rank."""
        import math
        closes = []
        price = 3.0
        d = datetime(2025, 1, 1)
        for i in range(300):
            # 前段低波动, 后 60 日高波动 (sigma 选择使 RV 均落在合法区间 [5,150])
            sigma = 0.006 if i < 240 else 0.025
            price *= 1.0 + math.sin(i * 0.9) * sigma
            closes.append(((d + timedelta(days=i)).strftime("%Y-%m-%d"), round(price, 4)))
        p = IVRankProvider(cache_path=temp_cache_path)
        rank = p._rank_from_series(p._rolling_rv(closes))
        assert rank is not None
        assert rank > 70  # 尾部高波动应落入高分位


# ============================================================
# 备链: shadow_state.json 只读
# ============================================================
class TestShadowStateChain:
    def test_fetch_from_shadow_state(self, provider):
        """主链失败时备链 shadow_state 计算 rank."""
        state = _make_shadow_state(n_days=120)
        state_path = _write_state(provider, state)
        with (
            patch.object(provider, "_rv_series_from_wind_kline", return_value=[]),
            patch.object(iv_rank_module, "_SHADOW_STATE_PATH", state_path),
        ):
            rank = provider.fetch_iv_rank(use_cache=False)
        assert rank is not None
        assert 0 <= rank <= 100

    def test_shadow_state_insufficient_data(self, provider, tmp_path):
        """daily_return 不足 rv_window+2 → 备链失败 → None."""
        state_path = tmp_path / "shadow_state.json"
        state_path.write_text(json.dumps({
            "daily_nav": [{"date": "2026-09-01", "daily_return": 0.01}] * 5,
        }), encoding="utf-8")
        with (
            patch.object(provider, "_rv_series_from_wind_kline", return_value=[]),
            patch.object(iv_rank_module, "_SHADOW_STATE_PATH", state_path),
        ):
            assert provider.fetch_iv_rank(use_cache=False) is None

    def test_shadow_state_file_missing(self, provider, tmp_path):
        with (
            patch.object(provider, "_rv_series_from_wind_kline", return_value=[]),
            patch.object(iv_rank_module, "_SHADOW_STATE_PATH", tmp_path / "nonexistent.json"),
        ):
            assert provider.fetch_iv_rank(use_cache=False) is None


def _write_state(provider: IVRankProvider, state: dict) -> Path:
    """把 state dict 写入 provider 缓存同目录的临时文件 (需真实文件供备链读取)."""
    path = Path(provider.CACHE_PATH).parent / "shadow_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return path


# ============================================================
# 三链: 历史缓存兜底
# ============================================================
class TestCacheFallback:
    def test_fallback_to_cache_history(self, provider, temp_cache_path):
        """主备链全失败时, 缓存 history 段 (≥ min_history+1) 推导 rank."""
        import math
        history = []
        d = datetime(2025, 1, 1)
        for i in range(100):
            history.append({
                "date": (d + timedelta(days=i)).strftime("%Y-%m-%d"),
                "vol": round(15.0 + math.sin(i * 0.3) * 5.0, 4),
            })
        temp_cache_path.write_text(json.dumps({
            "rank": 50, "source": "test", "history": history,
            "timestamp": (now_bj() - timedelta(hours=1)).isoformat(),
        }), encoding="utf-8")
        with (
            patch.object(provider, "_rv_series_from_wind_kline", return_value=[]),
            patch.object(iv_rank_module, "_SHADOW_STATE_PATH", Path(provider.CACHE_PATH).parent / "nope.json"),
        ):
            rank = provider.fetch_iv_rank(use_cache=True)
        assert rank is not None
        assert 0 <= rank <= 100

    def test_cache_history_too_short_no_fallback(self, provider, temp_cache_path):
        """history 不足 min_history+1 → 无兜底 → None."""
        temp_cache_path.write_text(json.dumps({
            "history": [{"date": "2026-01-01", "vol": 20.0}] * 10,
            "timestamp": now_bj().isoformat(),
        }), encoding="utf-8")
        with (
            patch.object(provider, "_rv_series_from_wind_kline", return_value=[]),
            patch.object(iv_rank_module, "_SHADOW_STATE_PATH", Path(provider.CACHE_PATH).parent / "nope.json"),
        ):
            assert provider.fetch_iv_rank(use_cache=True) is None

    def test_all_fail_returns_none(self, provider, tmp_path):
        """全链失败且无缓存 → None (fail-open)."""
        with (
            patch.object(provider, "_rv_series_from_wind_kline", return_value=[]),
            patch.object(iv_rank_module, "_SHADOW_STATE_PATH", tmp_path / "nonexistent.json"),
        ):
            assert provider.fetch_iv_rank(use_cache=True) is None


# ============================================================
# 缓存机制: 追加去重 / TTL / current_level
# ============================================================
class TestCacheMechanism:
    def test_history_append_dedup(self, provider, temp_cache_path):
        """history 按 date 去重, 重复追加不膨胀."""
        series = [{"date": f"2026-01-{i:02d}", "vol": 20.0 + i * 0.1} for i in range(1, 21)]
        provider._append_history(series)
        provider._append_history(series)  # 重复追加
        cache = json.loads(temp_cache_path.read_text(encoding="utf-8"))
        assert len(cache["history"]) == 20

    def test_save_and_load_cache(self, provider, temp_cache_path):
        provider._save_cache(42, source="wind_kline_rv")
        cache = provider._load_cache()
        assert cache is not None
        assert cache["rank"] == 42
        assert cache["source"] == "wind_kline_rv"

    def test_cache_dir_auto_creation(self, tmp_path):
        """cache_path 参数注入自动创建目录."""
        cache_path = tmp_path / "nested" / "dir" / "iv_rank_cache.json"
        IVRankProvider(cache_path=cache_path)._save_cache(10, source="test")
        assert cache_path.exists()

    def test_fetch_current_level_from_series(self, provider):
        """fetch_current_level 返回 VIX 量纲的当前波动."""
        kline = _make_kline(days=320, daily_sigma=0.01)
        with patch.object(
            provider, "_rv_series_from_wind_kline",
            return_value=provider._rolling_rv([(b["date"], b["close"]) for b in kline]),
        ):
            level = provider.fetch_current_level(use_cache=False)
        assert level is not None
        assert 5 <= level <= 150

    def test_fetch_current_level_cache_fallback(self, provider, temp_cache_path):
        """序列全失败时从缓存 current_vol 兜底."""
        temp_cache_path.write_text(json.dumps({
            "rank": 50, "current_vol": 22.5,
            "timestamp": now_bj().isoformat(),
        }), encoding="utf-8")
        with (
            patch.object(provider, "_rv_series_from_wind_kline", return_value=[]),
            patch.object(provider, "_rv_series_from_shadow_state", return_value=[]),
        ):
            assert provider.fetch_current_level(use_cache=True) == 22.5

    def test_fetch_rv_history(self, provider):
        """fetch_rv_history 供回测 integration 用."""
        kline = _make_kline(days=320)
        with patch.object(
            provider, "_rv_series_from_wind_kline",
            return_value=provider._rolling_rv([(b["date"], b["close"]) for b in kline]),
        ):
            series = provider.fetch_rv_history()
        assert len(series) > 0
        assert all("date" in s and "vol" in s for s in series)
