"""
单元测试: utils/data_source_manager.py
覆盖 DataSourceStatus / CacheStats / DataSourceInfo / DataSourceRegistry / PriorityDataSourceManager /
get_data_source_manager
"""

from __future__ import annotations

import pytest

from utils import data_source_manager as dsm
from utils.data_source_manager import (
    CacheStats,
    DataSourceInfo,
    DataSourceRegistry,
    DataSourceStatus,
    PriorityDataSourceManager,
    get_data_source_manager,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    dsm._data_source_manager = None
    yield
    dsm._data_source_manager = None


class TestDataSourceStatus:
    def test_enum_values(self):
        assert DataSourceStatus.HEALTHY.value == "healthy"
        assert DataSourceStatus.DEGRADED.value == "degraded"
        assert DataSourceStatus.UNAVAILABLE.value == "unavailable"
        assert DataSourceStatus.UNKNOWN.value == "unknown"


class TestCacheStats:
    def test_defaults(self):
        cs = CacheStats()
        assert cs.hits == 0
        assert cs.misses == 0
        assert cs.hit_rate == 0.0

    def test_hit_rate(self):
        cs = CacheStats(hits=8, misses=2)
        assert cs.hit_rate == 0.8

    def test_hit_rate_all_misses(self):
        cs = CacheStats(hits=0, misses=5)
        assert cs.hit_rate == 0.0

    def test_to_dict(self):
        cs = CacheStats(hits=7, misses=3, size_bytes=2048, item_count=10)
        d = cs.to_dict()
        assert d["hits"] == 7
        assert d["misses"] == 3
        assert d["hit_rate"] == "70.0%"
        assert d["size_kb"] == 2.0
        assert d["items"] == 10


class TestDataSourceInfo:
    def test_defaults(self):
        info = DataSourceInfo(name="test", priority=10)
        assert info.status == DataSourceStatus.UNKNOWN
        assert info.error_count == 0
        assert info.success_count == 0

    def test_custom_values(self):
        info = DataSourceInfo(
            name="wind",
            priority=100,
            status=DataSourceStatus.HEALTHY,
            success_count=5,
        )
        assert info.name == "wind"
        assert info.priority == 100
        assert info.status == DataSourceStatus.HEALTHY
        assert info.success_count == 5


class TestDataSourceRegistry:
    def test_register(self):
        reg = DataSourceRegistry()
        reg.register("wind", 100)
        assert "wind" in reg._sources
        assert reg._sources["wind"].priority == 100

    def test_mark_healthy(self):
        reg = DataSourceRegistry()
        reg.register("wind", 100)
        reg.mark_healthy("wind")
        assert reg._sources["wind"].status == DataSourceStatus.HEALTHY
        assert reg._sources["wind"].success_count == 1

    def test_mark_degraded(self):
        reg = DataSourceRegistry()
        reg.register("wind", 100)
        reg.mark_degraded("wind", "timeout")
        assert reg._sources["wind"].status == DataSourceStatus.DEGRADED
        assert reg._sources["wind"].error_count == 1

    def test_mark_unavailable(self):
        reg = DataSourceRegistry()
        reg.register("wind", 100)
        reg.mark_unavailable("wind", "connection refused")
        assert reg._sources["wind"].status == DataSourceStatus.UNAVAILABLE
        assert reg._sources["wind"].error_count == 1

    def test_mark_nonexistent_source(self):
        reg = DataSourceRegistry()
        reg.mark_healthy("nonexistent")
        reg.mark_degraded("nonexistent")
        reg.mark_unavailable("nonexistent")

    def test_get_available_sources_sorted(self):
        reg = DataSourceRegistry()
        reg.register("low", 10)
        reg.register("high", 100)
        reg.register("mid", 50)
        available = reg.get_available_sources()
        assert available == ["high", "mid", "low"]

    def test_get_available_excludes_unavailable(self):
        reg = DataSourceRegistry()
        reg.register("wind", 100)
        reg.register("akshare", 30)
        reg.mark_unavailable("wind")
        available = reg.get_available_sources()
        assert "wind" not in available
        assert "akshare" in available

    def test_get_status_report(self):
        reg = DataSourceRegistry()
        reg.register("wind", 100)
        reg.mark_healthy("wind")
        report = reg.get_status_report()
        assert "wind" in report
        assert "✅" in report


class TestPriorityDataSourceManager:
    def test_register_source(self):
        mgr = PriorityDataSourceManager()
        mgr.register_source("wind", lambda: None, priority=100)
        assert "wind" in mgr._sources
        assert mgr._priorities["wind"] == 100

    def test_fetch_success(self):
        mgr = PriorityDataSourceManager()
        mgr.register_source("wind", lambda x: {"price": x}, priority=100)
        result = mgr.fetch_with_fallback("600519", default={})
        assert result == {"price": "600519"}
        assert mgr.last_successful_source == "wind"

    def test_fetch_fallback(self):
        mgr = PriorityDataSourceManager()
        mgr.register_source("wind", lambda x: None, priority=100)
        mgr.register_source("akshare", lambda x: {"price": 10}, priority=30)
        result = mgr.fetch_with_fallback("600519", default={})
        assert result == {"price": 10}
        assert mgr.last_successful_source == "akshare"

    def test_fetch_all_fail_returns_default(self):
        mgr = PriorityDataSourceManager()
        mgr.register_source("wind", lambda x: None, priority=100)
        mgr.register_source("akshare", lambda x: None, priority=30)
        result = mgr.fetch_with_fallback("600519", default={"fallback": True})
        assert result == {"fallback": True}

    def test_fetch_no_sources(self):
        mgr = PriorityDataSourceManager()
        result = mgr.fetch_with_fallback("600519", default={"empty": True})
        assert result == {"empty": True}

    def test_fetch_exception_fallback(self):
        def failing(x):
            raise RuntimeError("connection error")

        mgr = PriorityDataSourceManager()
        mgr.register_source("wind", failing, priority=100)
        mgr.register_source("akshare", lambda x: {"ok": True}, priority=30)
        result = mgr.fetch_with_fallback("600519", default={})
        assert result == {"ok": True}

    def test_fetch_invalid_string_fallback(self):
        mgr = PriorityDataSourceManager()
        mgr.register_source("wind", lambda x: "❌ error", priority=100)
        mgr.register_source("akshare", lambda x: {"ok": True}, priority=30)
        result = mgr.fetch_with_fallback("600519", default={})
        assert result == {"ok": True}

    def test_get_status(self):
        mgr = PriorityDataSourceManager()
        mgr.register_source("wind", lambda: None, priority=100)
        status = mgr.get_status()
        assert "wind" in status

    def test_last_successful_source_property(self):
        mgr = PriorityDataSourceManager()
        assert mgr.last_successful_source is None
        mgr.register_source("wind", lambda: {"ok": True}, priority=100)
        mgr.fetch_with_fallback(default={})
        assert mgr.last_successful_source == "wind"


class TestGetDataSourceManager:
    def test_singleton(self):
        m1 = get_data_source_manager()
        m2 = get_data_source_manager()
        assert m1 is m2
