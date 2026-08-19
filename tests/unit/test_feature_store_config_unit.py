"""feature_store config 单元测试 — fail-closed 配置加载全分支覆盖

被测模块: utils/feature_store/config.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.feature_store.config import FeatureStoreConfig  # noqa: E402


class TestDefaults:
    def test_defaults(self):
        c = FeatureStoreConfig()
        assert c.online_backend == "memory"
        assert c.offline_backend == "parquet"
        assert c.online_ttl_days == 30
        assert c.online_redis_url == "redis://localhost:6379/0"
        assert c.offline_duckdb_path == "data/feature_store.duckdb"
        assert c.offline_parquet_dir == "data/feature_store/"
        assert c.batch_size == 10000
        assert c.reject_nan is False
        assert c.query_timeout_seconds == 5.0
        assert c.feature_flag_name == "USE_FEATURE_STORE"

    def test_frozen_cannot_mutate(self):
        c = FeatureStoreConfig()
        with pytest.raises((AttributeError, TypeError)):
            c.online_backend = "redis"

    def test_frozen_cannot_mutate_int(self):
        c = FeatureStoreConfig()
        with pytest.raises((AttributeError, TypeError)):
            c.batch_size = 999


class TestFromDictNoneAndNonDict:
    def test_none_returns_defaults(self):
        c = FeatureStoreConfig.from_dict(None)
        assert c == FeatureStoreConfig()

    def test_string_returns_defaults(self):
        c = FeatureStoreConfig.from_dict("not a dict")
        assert c == FeatureStoreConfig()

    def test_list_returns_defaults(self):
        c = FeatureStoreConfig.from_dict([1, 2, 3])
        assert c == FeatureStoreConfig()

    def test_int_returns_defaults(self):
        c = FeatureStoreConfig.from_dict(42)
        assert c == FeatureStoreConfig()


class TestFromDictValid:
    def test_full_valid_config(self):
        data = {
            "online_backend": "redis",
            "offline_backend": "duckdb",
            "online_ttl_days": 60,
            "online_redis_url": "redis://x:6380/1",
            "offline_duckdb_path": "/tmp/x.duckdb",
            "offline_parquet_dir": "/tmp/pq/",
            "batch_size": 5000,
            "reject_nan": True,
            "query_timeout_seconds": 10.0,
            "feature_flag_name": "FF_X",
        }
        c = FeatureStoreConfig.from_dict(data)
        assert c.online_backend == "redis"
        assert c.offline_backend == "duckdb"
        assert c.online_ttl_days == 60
        assert c.online_redis_url == "redis://x:6380/1"
        assert c.offline_duckdb_path == "/tmp/x.duckdb"
        assert c.offline_parquet_dir == "/tmp/pq/"
        assert c.batch_size == 5000
        assert c.reject_nan is True
        assert c.query_timeout_seconds == 10.0
        assert c.feature_flag_name == "FF_X"

    def test_partial_config(self):
        c = FeatureStoreConfig.from_dict({"batch_size": 2000})
        assert c.batch_size == 2000
        assert c.online_backend == "memory"
        assert c.offline_backend == "parquet"

    def test_empty_dict(self):
        c = FeatureStoreConfig.from_dict({})
        assert c == FeatureStoreConfig()

    def test_query_timeout_int_accepted(self):
        c = FeatureStoreConfig.from_dict({"query_timeout_seconds": 3})
        assert c.query_timeout_seconds == 3.0
        assert isinstance(c.query_timeout_seconds, float)


class TestFromDictInvalidFallbacks:
    def test_invalid_online_backend(self):
        c = FeatureStoreConfig.from_dict({"online_backend": "mysql"})
        assert c.online_backend == "memory"

    def test_invalid_offline_backend(self):
        c = FeatureStoreConfig.from_dict({"offline_backend": "csv"})
        assert c.offline_backend == "parquet"

    def test_online_ttl_non_int(self):
        c = FeatureStoreConfig.from_dict({"online_ttl_days": "30"})
        assert c.online_ttl_days == 30

    def test_online_ttl_zero(self):
        c = FeatureStoreConfig.from_dict({"online_ttl_days": 0})
        assert c.online_ttl_days == 30

    def test_online_ttl_negative(self):
        c = FeatureStoreConfig.from_dict({"online_ttl_days": -5})
        assert c.online_ttl_days == 30

    def test_online_ttl_float(self):
        c = FeatureStoreConfig.from_dict({"online_ttl_days": 30.5})
        assert c.online_ttl_days == 30

    def test_batch_size_non_int(self):
        c = FeatureStoreConfig.from_dict({"batch_size": "100"})
        assert c.batch_size == 10000

    def test_batch_size_zero(self):
        c = FeatureStoreConfig.from_dict({"batch_size": 0})
        assert c.batch_size == 10000

    def test_batch_size_negative(self):
        c = FeatureStoreConfig.from_dict({"batch_size": -1})
        assert c.batch_size == 10000

    def test_batch_size_float(self):
        c = FeatureStoreConfig.from_dict({"batch_size": 100.0})
        assert c.batch_size == 10000

    def test_query_timeout_non_number(self):
        c = FeatureStoreConfig.from_dict({"query_timeout_seconds": "5"})
        assert c.query_timeout_seconds == 5.0

    def test_query_timeout_zero(self):
        c = FeatureStoreConfig.from_dict({"query_timeout_seconds": 0})
        assert c.query_timeout_seconds == 5.0

    def test_query_timeout_negative(self):
        c = FeatureStoreConfig.from_dict({"query_timeout_seconds": -1.0})
        assert c.query_timeout_seconds == 5.0

    def test_all_invalid_simultaneously(self):
        c = FeatureStoreConfig.from_dict({
            "online_backend": "xxx",
            "offline_backend": "yyy",
            "online_ttl_days": -1,
            "batch_size": 0,
            "query_timeout_seconds": "bad",
        })
        assert c == FeatureStoreConfig()


class TestFromDictTypeCoercion:
    def test_reject_nan_truthy(self):
        c = FeatureStoreConfig.from_dict({"reject_nan": 1})
        assert c.reject_nan is True

    def test_reject_nan_falsy(self):
        c = FeatureStoreConfig.from_dict({"reject_nan": 0})
        assert c.reject_nan is False

    def test_string_fields_coerced(self):
        c = FeatureStoreConfig.from_dict({
            "online_redis_url": 12345,
            "offline_duckdb_path": 67890,
            "offline_parquet_dir": None,
            "feature_flag_name": 42,
        })
        assert c.online_redis_url == "12345"
        assert c.offline_duckdb_path == "67890"
        assert c.offline_parquet_dir == "None"
        assert c.feature_flag_name == "42"


class TestFromSystemConfig:
    @patch("utils.config_manager.get_config")
    def test_valid_section(self, mock_get):
        mock_get.return_value = {
            "feature_store": {"batch_size": 500, "online_backend": "redis"},
        }
        c = FeatureStoreConfig.from_system_config()
        assert c.batch_size == 500
        assert c.online_backend == "redis"

    @patch("utils.config_manager.get_config")
    def test_none_config(self, mock_get):
        mock_get.return_value = None
        c = FeatureStoreConfig.from_system_config()
        assert c == FeatureStoreConfig()

    @patch("utils.config_manager.get_config")
    def test_non_dict_config(self, mock_get):
        mock_get.return_value = "not dict"
        c = FeatureStoreConfig.from_system_config()
        assert c == FeatureStoreConfig()

    @patch("utils.config_manager.get_config")
    def test_missing_feature_store_section(self, mock_get):
        mock_get.return_value = {"other": {}}
        c = FeatureStoreConfig.from_system_config()
        assert c == FeatureStoreConfig()

    @patch("utils.config_manager.get_config")
    def test_feature_store_section_is_none(self, mock_get):
        mock_get.return_value = {"feature_store": None}
        c = FeatureStoreConfig.from_system_config()
        assert c == FeatureStoreConfig()

    @patch("utils.config_manager.get_config")
    def test_exception_returns_defaults(self, mock_get):
        mock_get.side_effect = RuntimeError("boom")
        c = FeatureStoreConfig.from_system_config()
        assert c == FeatureStoreConfig()

    @patch("utils.config_manager.get_config")
    def test_invalid_field_in_section_falls_back(self, mock_get):
        mock_get.return_value = {
            "feature_store": {"online_backend": "bad", "batch_size": -1},
        }
        c = FeatureStoreConfig.from_system_config()
        assert c.online_backend == "memory"
        assert c.batch_size == 10000
