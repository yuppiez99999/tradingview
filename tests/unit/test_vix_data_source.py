"""
VixDataSource 单元测试
=======================

测试 VIX 数据源模块的三级降级链:
    1. shadow_state.json → realized_vol → VIX proxy
    2. Wind MCP 510050 K 线 → 波动率 → VIX proxy
    3. 缓存兜底

测试用例:
    - test_fetch_from_shadow_state_rv_success: shadow_state.json 有效数据
    - test_fetch_from_shadow_state_rv_insufficient_data: 数据不足时返回 None
    - test_fetch_from_shadow_state_rv_file_missing: 文件不存在时返回 None
    - test_cache_save_and_load: 缓存写入和读取
    - test_cache_expired: 缓存过期返回 None
    - test_cache_invalid_format: 缓存格式错误返回 None
    - test_fetch_vix_full_chain: 完整降级链测试
    - test_fetch_vix_all_fail: 全失败返回 None
    - test_cache_dir_auto_creation: 自动创建缓存目录
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

from utils.alpha.vix_data_source import VixDataSource


# ============================================================
# 测试 fixtures
# ============================================================
@pytest.fixture
def temp_state_file(tmp_path) -> Path:
    """创建临时 shadow_state.json 文件."""
    state_data = {
        "current_nav": 0.996477,
        "daily_nav": [
            {"date": "2026-07-27", "nav": 1.003251, "daily_return": 0.003251},
            {"date": "2026-07-28", "nav": 0.997283, "daily_return": -0.005949},
            {"date": "2026-07-29", "nav": 0.997897, "daily_return": 0.000616},
            {"date": "2026-07-30", "nav": 0.996218, "daily_return": -0.001683},
            {"date": "2026-07-31", "nav": 1.003485, "daily_return": 0.007295},
            {"date": "2026-08-03", "nav": 0.997079, "daily_return": -0.006384},
            {"date": "2026-08-04", "nav": 0.996477, "daily_return": -0.000603},
        ],
    }
    state_path = tmp_path / "shadow_state.json"
    state_path.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
    return state_path


@pytest.fixture
def temp_cache_dir(tmp_path) -> Path:
    """创建临时缓存目录."""
    cache_dir = tmp_path / "volatility"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


# ============================================================
# 测试用例: shadow_state RV 数据源
# ============================================================
class TestShadowStateRv:
    """测试从 shadow_state.json 计算 RV."""

    def test_fetch_from_shadow_state_rv_success(self, temp_state_file, temp_cache_dir):
        """正常读取 shadow_state.json, 计算 RV 并转换为 VIX proxy."""
        ds = VixDataSource()
        # 重定向路径
        ds.CACHE_PATH = temp_cache_dir / "vix_cache.json"
        with patch("utils.alpha.vix_data_source._SHADOW_STATE_PATH", temp_state_file):
            vix = ds._fetch_from_shadow_state_rv()

        # 7 天 daily_return, 计算年化波动率 * 100
        # daily_return 标准差约 0.0049, 年化约 7.8%, VIX proxy 约 7.8
        assert vix is not None
        assert 5 <= vix <= 50  # 合理区间

    def test_fetch_from_shadow_state_rv_insufficient_data(
        self, tmp_path, temp_cache_dir
    ):
        """daily_nav 数据不足 2 条时返回 None."""
        state_data = {
            "daily_nav": [{"date": "2026-08-04", "nav": 1.0, "daily_return": None}]
        }
        state_path = tmp_path / "shadow_state.json"
        state_path.write_text(json.dumps(state_data), encoding="utf-8")

        ds = VixDataSource()
        ds.CACHE_PATH = temp_cache_dir / "vix_cache.json"
        with patch("utils.alpha.vix_data_source._SHADOW_STATE_PATH", state_path):
            vix = ds._fetch_from_shadow_state_rv()

        assert vix is None

    def test_fetch_from_shadow_state_rv_file_missing(self, tmp_path, temp_cache_dir):
        """文件不存在时返回 None."""
        ds = VixDataSource()
        ds.CACHE_PATH = temp_cache_dir / "vix_cache.json"
        with patch(
            "utils.alpha.vix_data_source._SHADOW_STATE_PATH",
            tmp_path / "nonexistent.json",
        ):
            vix = ds._fetch_from_shadow_state_rv()

        assert vix is None

    def test_fetch_from_shadow_state_rv_empty_returns(self, tmp_path, temp_cache_dir):
        """daily_return 全为 None 时返回 None."""
        state_data = {
            "daily_nav": [
                {"date": "2026-07-27", "nav": 1.0, "daily_return": None},
                {"date": "2026-07-28", "nav": 1.0, "daily_return": None},
            ]
        }
        state_path = tmp_path / "shadow_state.json"
        state_path.write_text(json.dumps(state_data), encoding="utf-8")

        ds = VixDataSource()
        ds.CACHE_PATH = temp_cache_dir / "vix_cache.json"
        with patch("utils.alpha.vix_data_source._SHADOW_STATE_PATH", state_path):
            vix = ds._fetch_from_shadow_state_rv()

        assert vix is None


# ============================================================
# 测试用例: 缓存机制
# ============================================================
class TestCacheMechanism:
    """测试 VIX 缓存读写."""

    def test_cache_save_and_load(self, temp_cache_dir):
        """缓存写入后能正确读取."""
        ds = VixDataSource()
        cache_path = temp_cache_dir / "vix_cache.json"
        ds.CACHE_PATH = cache_path

        # 写入缓存
        ds._save_cache(25.3, source="shadow_state_rv")
        assert cache_path.exists()

        # 读取缓存
        cached = ds._load_cache()
        assert cached is not None
        assert cached["vix"] == 25.3
        assert cached["source"] == "shadow_state_rv"

    def test_cache_expired(self, temp_cache_dir):
        """缓存过期后返回 None."""
        ds = VixDataSource()
        cache_path = temp_cache_dir / "vix_cache.json"
        ds.CACHE_PATH = cache_path

        # 写入过期缓存 (10 分钟前)
        expired_time = (now_bj() - timedelta(minutes=10)).isoformat()
        cache_data = {
            "vix": 25.3,
            "source": "shadow_state_rv",
            "timestamp": expired_time,
            "ttl": 300,  # 5 分钟
        }
        cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

        cached = ds._load_cache()
        assert cached is None

    def test_cache_invalid_format(self, temp_cache_dir):
        """缓存格式错误时返回 None."""
        ds = VixDataSource()
        cache_path = temp_cache_dir / "vix_cache.json"
        ds.CACHE_PATH = cache_path

        # 写入无效 JSON
        cache_path.write_text("invalid json content", encoding="utf-8")
        cached = ds._load_cache()
        assert cached is None

        # 写入缺少 vix 字段的缓存
        cache_path.write_text(
            json.dumps({"source": "test", "timestamp": now_bj().isoformat()}),
            encoding="utf-8",
        )
        cached = ds._load_cache()
        assert cached is None

    def test_cache_missing_file(self, temp_cache_dir):
        """缓存文件不存在时返回 None."""
        ds = VixDataSource()
        ds.CACHE_PATH = temp_cache_dir / "nonexistent.json"

        cached = ds._load_cache()
        assert cached is None


# ============================================================
# 测试用例: 完整降级链
# ============================================================
class TestFetchVixFullChain:
    """测试 fetch_vix 完整降级链."""

    def test_fetch_vix_from_shadow_state(self, temp_state_file, temp_cache_dir):
        """主数据源可用时返回 shadow_state RV."""
        ds = VixDataSource()
        ds.CACHE_PATH = temp_cache_dir / "vix_cache.json"
        with patch("utils.alpha.vix_data_source._SHADOW_STATE_PATH", temp_state_file):
            vix = ds.fetch_vix(use_cache=False)

        assert vix is not None
        assert 5 <= vix <= 50
        # 验证缓存已写入
        assert ds.CACHE_PATH.exists()

    def test_fetch_vix_all_fail_no_cache(self, tmp_path, temp_cache_dir):
        """全失败且无缓存时返回 None."""
        ds = VixDataSource()
        ds.CACHE_PATH = temp_cache_dir / "vix_cache.json"
        # shadow_state 不存在 + Wind K 线失败 + 无缓存
        with (
            patch(
                "utils.alpha.vix_data_source._SHADOW_STATE_PATH",
                tmp_path / "nonexistent.json",
            ),
            patch.object(ds, "_fetch_from_wind_kline", return_value=None),
        ):
            vix = ds.fetch_vix(use_cache=False)

        assert vix is None

    def test_fetch_vix_fallback_to_cache(self, temp_state_file, temp_cache_dir):
        """主备数据源失败时回退到缓存."""
        ds = VixDataSource()
        cache_path = temp_cache_dir / "vix_cache.json"
        ds.CACHE_PATH = cache_path

        # 预写入有效缓存
        ds._save_cache(28.5, source="shadow_state_rv")

        # shadow_state 不可用 + Wind 失败 → 应该回退到缓存
        with (
            patch(
                "utils.alpha.vix_data_source._SHADOW_STATE_PATH",
                temp_cache_dir / "nonexistent.json",
            ),
            patch.object(ds, "_fetch_from_wind_kline", return_value=None),
        ):
            vix = ds.fetch_vix(use_cache=True)

        assert vix == 28.5

    def test_fetch_vix_skip_cache_when_disabled(self, temp_state_file, temp_cache_dir):
        """use_cache=False 时跳过缓存读取, 但仍尝试主备数据源."""
        ds = VixDataSource()
        cache_path = temp_cache_dir / "vix_cache.json"
        ds.CACHE_PATH = cache_path

        # 预写入过期缓存
        expired_time = (now_bj() - timedelta(minutes=10)).isoformat()
        cache_path.write_text(
            json.dumps(
                {
                    "vix": 99.9,  # 不应该返回这个值
                    "source": "test",
                    "timestamp": expired_time,
                    "ttl": 300,
                }
            ),
            encoding="utf-8",
        )

        # shadow_state 可用 → 应该返回 shadow_state RV, 不是缓存
        with patch("utils.alpha.vix_data_source._SHADOW_STATE_PATH", temp_state_file):
            vix = ds.fetch_vix(use_cache=False)

        assert vix is not None
        assert vix != 99.9  # 不是缓存值


# ============================================================
# 测试用例: 缓存目录自动创建
# ============================================================
class TestCacheDirCreation:
    """测试缓存目录自动创建."""

    def test_cache_dir_auto_creation(self, tmp_path):
        """VixDataSource 初始化时自动创建缓存目录."""
        cache_dir = tmp_path / "volatility"
        cache_path = cache_dir / "vix_cache.json"

        # 使用 cache_path 参数传入自定义路径
        ds = VixDataSource(cache_path=cache_path)
        # 目录可能尚未创建 (lazy creation), 写入缓存时才会创建
        # 先验证 _save_cache 能自动创建目录
        ds._save_cache(20.0, source="test")
        assert cache_dir.exists()
        assert cache_path.exists()
