"""FeatureStore 配置 — frozen dataclass + fail-closed 加载.

设计范式对齐 utils/risk/cvar.py (frozen dataclass + Calculator + fail-closed).
配置缺失/非法时使用安全默认值 + WARNING 日志, 不抛异常.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("FeatureStore")

_VALID_ONLINE_BACKENDS = {"memory", "redis"}
_VALID_OFFLINE_BACKENDS = {"parquet", "duckdb"}


@dataclass(frozen=True)
class FeatureStoreConfig:
    """FeatureStore 配置 (不可变).

    字段含义:
        online_backend: 在线层后端 ("memory" / "redis")
        offline_backend: 离线层后端 ("parquet" / "duckdb")
        online_ttl_days: 在线层 TTL 天数 (惰性淘汰)
        online_redis_url: Redis 连接 URL (redis 后端时使用)
        offline_duckdb_path: DuckDB 文件路径 (duckdb 后端时使用)
        offline_parquet_dir: Parquet 文件根目录 (parquet 后端时使用)
        batch_size: 批量写入批次大小
        reject_nan: 是否拒绝 NaN/inf 值 (True=跳过, False=允许+告警)
        query_timeout_seconds: 离线查询超时秒数
        feature_flag_name: Feature Flag 名称
    """

    online_backend: str = "memory"
    offline_backend: str = "parquet"
    online_ttl_days: int = 30
    online_redis_url: str = "redis://localhost:6379/0"
    offline_duckdb_path: str = "data/feature_store.duckdb"
    offline_parquet_dir: str = "data/feature_store/"
    batch_size: int = 10000
    reject_nan: bool = False
    query_timeout_seconds: float = 5.0
    feature_flag_name: str = "USE_FEATURE_STORE"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FeatureStoreConfig:
        """从 dict 构造配置, 非法字段降级为默认值 + WARNING (fail-closed)."""
        if data is None:
            logger.warning("[FeatureStore] config is None, using all defaults")
            return cls()
        if not isinstance(data, dict):
            logger.warning("[FeatureStore] config is not dict (%s), using all defaults", type(data).__name__)
            return cls()

        defaults = cls()
        online_backend = data.get("online_backend", defaults.online_backend)
        if online_backend not in _VALID_ONLINE_BACKENDS:
            logger.warning("[FeatureStore] invalid online_backend '%s', using default '%s'", online_backend, defaults.online_backend)
            online_backend = defaults.online_backend

        offline_backend = data.get("offline_backend", defaults.offline_backend)
        if offline_backend not in _VALID_OFFLINE_BACKENDS:
            logger.warning("[FeatureStore] invalid offline_backend '%s', using default '%s'", offline_backend, defaults.offline_backend)
            offline_backend = defaults.offline_backend

        online_ttl_days = data.get("online_ttl_days", defaults.online_ttl_days)
        if not isinstance(online_ttl_days, int) or online_ttl_days <= 0:
            logger.warning("[FeatureStore] invalid online_ttl_days %r, using default %d", online_ttl_days, defaults.online_ttl_days)
            online_ttl_days = defaults.online_ttl_days

        batch_size = data.get("batch_size", defaults.batch_size)
        if not isinstance(batch_size, int) or batch_size <= 0:
            logger.warning("[FeatureStore] invalid batch_size %r, using default %d", batch_size, defaults.batch_size)
            batch_size = defaults.batch_size

        query_timeout_seconds = data.get("query_timeout_seconds", defaults.query_timeout_seconds)
        if not isinstance(query_timeout_seconds, (int, float)) or query_timeout_seconds <= 0:
            logger.warning("[FeatureStore] invalid query_timeout_seconds %r, using default %s", query_timeout_seconds, defaults.query_timeout_seconds)
            query_timeout_seconds = defaults.query_timeout_seconds

        return cls(
            online_backend=online_backend,
            offline_backend=offline_backend,
            online_ttl_days=online_ttl_days,
            online_redis_url=str(data.get("online_redis_url", defaults.online_redis_url)),
            offline_duckdb_path=str(data.get("offline_duckdb_path", defaults.offline_duckdb_path)),
            offline_parquet_dir=str(data.get("offline_parquet_dir", defaults.offline_parquet_dir)),
            batch_size=batch_size,
            reject_nan=bool(data.get("reject_nan", defaults.reject_nan)),
            query_timeout_seconds=float(query_timeout_seconds),
            feature_flag_name=str(data.get("feature_flag_name", defaults.feature_flag_name)),
        )

    @classmethod
    def from_system_config(cls) -> FeatureStoreConfig:
        """从 system_config.json 提取 feature_store 段 (HC-5 四级优先级)."""
        try:
            from utils.config_manager import get_config
            sys_cfg = get_config("system_config", default={})
            if not isinstance(sys_cfg, dict):
                logger.warning("[FeatureStore] system_config is not dict, using all defaults")
                return cls()
            fs_cfg = sys_cfg.get("feature_store")
            if fs_cfg is None:
                logger.warning("[FeatureStore] feature_store section not found in system_config, using all defaults")
                return cls()
            return cls.from_dict(fs_cfg)
        except Exception as e:
            logger.warning("[FeatureStore] failed to load system_config: %s, using all defaults", e)
            return cls()
