"""feature_store 单元测试 — 特征缓存层全覆盖.

被测模块: utils/data/feature_store.py
覆盖目标: >=95%
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.data.feature_store import FeatureStore  # noqa: E402

# ============================================================
# make_key
# ============================================================


class TestMakeKey:
    def test_basic_namespace(self):
        assert FeatureStore.make_key("alpha") == "alpha"

    def test_single_part(self):
        assert FeatureStore.make_key("alpha", symbol="600519") == "alpha::symbol=600519"

    def test_multiple_parts_sorted(self):
        key = FeatureStore.make_key("alpha", symbol="600519", date="2026-08-08")
        assert key == "alpha::date=2026-08-08_symbol=600519"

    def test_empty_namespace(self):
        key = FeatureStore.make_key("", symbol="600519")
        assert key == "::symbol=600519"

    def test_no_parts(self):
        assert FeatureStore.make_key("ns") == "ns"


# ============================================================
# get / put
# ============================================================


class TestGetPut:
    def test_put_then_get_memory(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path, ttl_seconds=3600)
        store.put("k1", {"val": 42})
        assert store.get("k1") == {"val": 42}

    def test_get_missing_returns_none(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path)
        assert store.get("nonexistent") is None

    def test_put_scalar(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path)
        store.put("scalar", 3.14)
        assert store.get("scalar") == 3.14

    def test_put_list(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path)
        store.put("list", [1, 2, 3])
        assert store.get("list") == [1, 2, 3]

    def test_file_fallback(self, tmp_path):
        store1 = FeatureStore(cache_dir=tmp_path, ttl_seconds=3600)
        store1.put("file_key", {"data": "test"})
        store2 = FeatureStore(cache_dir=tmp_path, ttl_seconds=3600)
        assert store2.get("file_key") == {"data": "test"}

    def test_non_serializable_stays_in_memory(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path)
        obj = object()
        store.put("obj", obj)
        assert store.get("obj") is obj


# ============================================================
# TTL
# ============================================================


class TestTTL:
    def test_expired_memory(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path, ttl_seconds=0)
        store.put("k", "v")
        time.sleep(0.01)
        assert store.get("k") is None

    def test_expired_file(self, tmp_path):
        store1 = FeatureStore(cache_dir=tmp_path, ttl_seconds=0)
        store1.put("k", "v")
        time.sleep(0.01)
        store2 = FeatureStore(cache_dir=tmp_path, ttl_seconds=0)
        assert store2.get("k") is None

    def test_long_ttl_survives(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path, ttl_seconds=999999)
        store.put("k", "v")
        assert store.get("k") == "v"


# ============================================================
# get_or_compute
# ============================================================


class TestGetOrCompute:
    def test_compute_on_miss(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path)
        calls = []
        result = store.get_or_compute("k", lambda: (calls.append(1), 99)[1])
        assert result == 99
        assert len(calls) == 1

    def test_cached_no_recompute(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path, ttl_seconds=3600)
        calls = []

        def fn():
            return (calls.append(1), 99)[1]

        store.get_or_compute("k", fn)
        store.get_or_compute("k", fn)
        assert len(calls) == 1

    def test_file_cache_hit(self, tmp_path):
        store1 = FeatureStore(cache_dir=tmp_path, ttl_seconds=3600)
        store1.get_or_compute("k", lambda: 42)
        store2 = FeatureStore(cache_dir=tmp_path, ttl_seconds=3600)
        calls = []
        result = store2.get_or_compute("k", lambda: (calls.append(1), 0)[1])
        assert result == 42
        assert len(calls) == 0


# ============================================================
# clear_expired
# ============================================================


class TestClearExpired:
    def test_clear_all(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path, ttl_seconds=0)
        store.put("k1", 1)
        store.put("k2", 2)
        time.sleep(0.01)
        n = store.clear_expired()
        assert n == 2

    def test_clear_none(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path, ttl_seconds=999999)
        store.put("k1", 1)
        n = store.clear_expired()
        assert n == 0

    def test_partial_clear(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path, ttl_seconds=500)
        store.put("k1", 1)
        store._mem["k1"]["ts"] = time.time() - 1000
        store.put("k2", 2)
        n = store.clear_expired()
        assert n == 1


# ============================================================
# _path
# ============================================================


class TestPath:
    def test_safe_path(self, tmp_path):
        store = FeatureStore(cache_dir=tmp_path)
        p = store._path("ns::k=v/x\\y:z")
        assert "/" not in p.name
        assert "\\" not in p.name
        assert ":" not in p.name
        assert p.suffix == ".json"
