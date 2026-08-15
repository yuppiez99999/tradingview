"""OnlineStore 单元测试."""

from __future__ import annotations

import time

import pytest

from utils.feature_store.config import FeatureStoreConfig
from utils.feature_store.online_store import OnlineStore


@pytest.fixture
def store():
    return OnlineStore(FeatureStoreConfig(online_backend="memory", online_ttl_days=1))


@pytest.fixture
def short_ttl_store():
    return OnlineStore(FeatureStoreConfig(online_backend="memory", online_ttl_days=1))


class TestOnlineStoreInit:
    def test_default_config(self):
        s = OnlineStore()
        assert s._config.online_backend == "memory"
        assert s._ttl_seconds > 0

    def test_custom_config(self):
        cfg = FeatureStoreConfig(online_backend="memory", online_ttl_days=7)
        s = OnlineStore(cfg)
        assert s._ttl_seconds == 7 * 86400

    def test_redis_fallback_to_memory(self):
        cfg = FeatureStoreConfig(online_backend="redis", online_redis_url="redis://invalid:9999/0")
        s = OnlineStore(cfg)
        assert s._redis is None


class TestPutGet:
    def test_put_and_get(self, store):
        store.put("momentum", "2026-08-14", {"value": 0.85})
        result = store.get("momentum", "2026-08-14")
        assert result == {"value": 0.85}

    def test_get_missing(self, store):
        assert store.get("nonexistent", "2026-08-14") is None

    def test_get_missing_date(self, store):
        store.put("momentum", "2026-08-14", {"value": 0.85})
        assert store.get("momentum", "2026-08-15") is None

    def test_put_empty_name(self, store):
        assert store.put("", "2026-08-14", {"v": 1}) is False

    def test_put_empty_date(self, store):
        assert store.put("momentum", "", {"v": 1}) is False

    def test_overwrite(self, store):
        store.put("momentum", "2026-08-14", {"value": 0.85})
        store.put("momentum", "2026-08-14", {"value": 0.92})
        assert store.get("momentum", "2026-08-14") == {"value": 0.92}

    def test_multiple_features(self, store):
        store.put("momentum", "2026-08-14", {"v": 1})
        store.put("reversal", "2026-08-14", {"v": 2})
        assert store.get("momentum", "2026-08-14") == {"v": 1}
        assert store.get("reversal", "2026-08-14") == {"v": 2}


class TestTTL:
    def test_not_expired(self, store):
        store.put("momentum", "2026-08-14", {"v": 1})
        assert store.get("momentum", "2026-08-14") is not None

    def test_expired(self):
        cfg = FeatureStoreConfig(online_backend="memory", online_ttl_days=1)
        store = OnlineStore(cfg)
        store.put("momentum", "2026-08-14", {"v": 1})
        with store._lock:
            for fm in store._memory.values():
                for k in fm:
                    fm[k] = (fm[k][0], time.monotonic() - 1)
        assert store.get("momentum", "2026-08-14") is None

    def test_cleanup_expired(self):
        cfg = FeatureStoreConfig(online_backend="memory", online_ttl_days=1)
        store = OnlineStore(cfg)
        store.put("momentum", "2026-08-14", {"v": 1})
        store.put("momentum", "2026-08-15", {"v": 2})
        with store._lock:
            for fm in store._memory.values():
                keys = list(fm.keys())
                fm[keys[0]] = (fm[keys[0]][0], time.monotonic() - 1)
        count = store.cleanup_expired()
        assert count == 1
        assert store.size() == 1


class TestDelete:
    def test_delete_existing(self, store):
        store.put("momentum", "2026-08-14", {"v": 1})
        assert store.delete("momentum", "2026-08-14") is True
        assert store.get("momentum", "2026-08-14") is None

    def test_delete_nonexistent(self, store):
        assert store.delete("nonexistent", "2026-08-14") is False


class TestClear:
    def test_clear_empty(self, store):
        assert store.clear() == 0

    def test_clear_with_data(self, store):
        store.put("a", "2026-08-14", {"v": 1})
        store.put("b", "2026-08-14", {"v": 2})
        count = store.clear()
        assert count == 2
        assert store.size() == 0


class TestSize:
    def test_empty(self, store):
        assert store.size() == 0

    def test_with_data(self, store):
        store.put("a", "2026-08-14", {"v": 1})
        store.put("a", "2026-08-15", {"v": 2})
        store.put("b", "2026-08-14", {"v": 3})
        assert store.size() == 3


class TestRejectNan:
    def test_reject_nan(self):
        cfg = FeatureStoreConfig(online_backend="memory", reject_nan=True)
        store = OnlineStore(cfg)
        assert store.put("momentum", "2026-08-14", {"v": float("nan")}) is False
        assert store.get("momentum", "2026-08-14") is None

    def test_reject_inf(self):
        cfg = FeatureStoreConfig(online_backend="memory", reject_nan=True)
        store = OnlineStore(cfg)
        assert store.put("momentum", "2026-08-14", {"v": float("inf")}) is False

    def test_allow_nan(self, store):
        store.put("momentum", "2026-08-14", {"v": float("nan")})
        result = store.get("momentum", "2026-08-14")
        assert result is not None
