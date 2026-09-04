"""OfflineStore 单元测试."""

from __future__ import annotations

import pytest

from utils.feature_store.config import FeatureStoreConfig
from utils.feature_store.offline_store import OfflineStore


@pytest.fixture
def store(tmp_path):
    cfg = FeatureStoreConfig(
        offline_backend="parquet", offline_parquet_dir=str(tmp_path / "fs")
    )
    return OfflineStore(cfg)


class TestOfflineStoreInit:
    def test_default_config(self, tmp_path):
        cfg = FeatureStoreConfig(
            offline_backend="parquet", offline_parquet_dir=str(tmp_path / "fs")
        )
        s = OfflineStore(cfg)
        assert s._root.exists()

    def test_duckdb_fallback(self, tmp_path, monkeypatch):
        cfg = FeatureStoreConfig(
            offline_backend="duckdb",
            offline_duckdb_path=str(tmp_path / "nonexistent" / "db.duckdb"),
        )
        # 原测试依赖 "duckdb 未安装" 才能通过 (ImportError 降级); duckdb 装入 .venv 后
        # connect 永远成功 (mkdir parents=True 会建出 nonexistent 目录) → 断言必挂。
        # 改为环境无关: 已装时强制 connect 抛 duckdb.Error 验证 fail-closed 降级。
        try:
            import duckdb
        except ImportError:
            pass  # duckdb 未装: _init_duckdb 的 ImportError 自然触发降级路径
        else:
            def _raise_connect(*_args, **_kwargs):
                raise duckdb.Error("synthetic connect failure for fallback test")

            monkeypatch.setattr(duckdb, "connect", _raise_connect)
        s = OfflineStore(cfg)
        assert s._duckdb is None


class TestWriteBatch:
    def test_write_and_read(self, store):
        records = [
            {"date": "2026-08-14", "value": 0.85, "rank": 1},
            {"date": "2026-08-15", "value": 0.72, "rank": 3},
        ]
        count = store.write_batch("momentum", records)
        assert count == 2
        df = store.read_range("momentum", "2026-08-01", "2026-08-31")
        assert len(df) == 2

    def test_write_empty(self, store):
        assert store.write_batch("momentum", []) == 0

    def test_write_empty_name(self, store):
        assert store.write_batch("", [{"date": "2026-08-14", "v": 1}]) == 0

    def test_write_reject_nan(self, tmp_path):
        cfg = FeatureStoreConfig(
            offline_backend="parquet",
            offline_parquet_dir=str(tmp_path / "fs"),
            reject_nan=True,
        )
        store = OfflineStore(cfg)
        records = [
            {"date": "2026-08-14", "value": float("nan")},
            {"date": "2026-08-15", "value": 0.72},
        ]
        count = store.write_batch("momentum", records)
        assert count == 1


class TestReadRange:
    def test_read_missing_feature(self, store):
        df = store.read_range("nonexistent", "2026-08-01", "2026-08-31")
        assert df.empty

    def test_read_empty_name(self, store):
        df = store.read_range("", "2026-08-01", "2026-08-31")
        assert df.empty

    def test_read_partial_range(self, store):
        records = [
            {"date": "2026-08-14", "value": 0.85},
            {"date": "2026-08-15", "value": 0.72},
            {"date": "2026-08-16", "value": 0.65},
        ]
        store.write_batch("momentum", records)
        df = store.read_range("momentum", "2026-08-15", "2026-08-16")
        assert len(df) == 2

    def test_read_no_overlap(self, store):
        records = [{"date": "2026-08-14", "value": 0.85}]
        store.write_batch("momentum", records)
        df = store.read_range("momentum", "2026-09-01", "2026-09-30")
        assert df.empty


class TestReadLatest:
    def test_read_latest(self, store):
        records = [
            {"date": "2026-08-14", "value": 0.85},
            {"date": "2026-08-15", "value": 0.72},
        ]
        store.write_batch("momentum", records)
        df = store.read_latest("momentum")
        assert not df.empty

    def test_read_latest_empty(self, store):
        df = store.read_latest("nonexistent")
        assert df.empty


class TestDeleteFeature:
    def test_delete_existing(self, store):
        store.write_batch("momentum", [{"date": "2026-08-14", "v": 1}])
        count = store.delete_feature("momentum")
        assert count >= 1
        assert store.read_range("momentum", "2026-01-01", "2026-12-31").empty

    def test_delete_nonexistent(self, store):
        count = store.delete_feature("nonexistent")
        assert count == 0


class TestListFeatures:
    def test_empty(self, store):
        assert store.list_features() == []

    def test_with_features(self, store):
        store.write_batch("momentum", [{"date": "2026-08-14", "v": 1}])
        store.write_batch("reversal", [{"date": "2026-08-14", "v": 2}])
        features = store.list_features()
        assert "momentum" in features
        assert "reversal" in features
