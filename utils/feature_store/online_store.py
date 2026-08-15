"""FeatureStore 在线层 — 低延迟查询 (<10ms) + TTL 惰性淘汰.

后端:
    - memory: 进程内 dict + TTL (默认, 零依赖)
    - redis:  Redis KV (可选, 跨进程共享)

设计原则:
    - fail-closed: 后端不可用时返回 None + WARNING, 不抛异常
    - TTL 简化: 存储时附带过期时间戳, 查询时惰性检查
    - 线程安全: memory 后端用 threading.Lock 保护 dict 操作
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from utils.feature_store.config import FeatureStoreConfig

logger = logging.getLogger("FeatureStore")


class OnlineStore:
    """在线特征存储 — 低延迟查询.

    用法:
        store = OnlineStore(config)
        store.put("momentum_v1", "2026-08-14", {"value": 0.85})
        val = store.get("momentum_v1", "2026-08-14")  # <10ms
    """

    def __init__(self, config: FeatureStoreConfig | None = None):
        self._config = config or FeatureStoreConfig()
        self._ttl_seconds = self._config.online_ttl_days * 86400
        self._lock = threading.Lock()
        self._memory: dict[str, dict[str, tuple[Any, float]]] = {}
        self._redis = None
        if self._config.online_backend == "redis":
            self._init_redis()

    def _init_redis(self) -> None:
        try:
            import redis
            self._redis = redis.Redis.from_url(
                self._config.online_redis_url,
                decode_responses=True,
                socket_timeout=self._config.query_timeout_seconds,
            )
            self._redis.ping()
            logger.info("[FeatureStore] OnlineStore redis connected: %s", self._config.online_redis_url)
        except Exception as e:
            logger.warning("[FeatureStore] OnlineStore redis init failed (%s), falling back to memory", e)
            self._redis = None

    def _make_key(self, feature_name: str, date_key: str) -> str:
        return f"fs:{feature_name}:{date_key}"

    def put(self, feature_name: str, date_key: str, value: dict[str, Any]) -> bool:
        """写入特征值. 返回 True 成功, False 失败."""
        if not feature_name or not date_key:
            return False
        if self._config.reject_nan:
            if not self._validate_no_nan(value):
                logger.warning("[FeatureStore] OnlineStore put rejected (NaN/inf): %s/%s", feature_name, date_key)
                return False
        if self._redis is not None:
            return self._put_redis(feature_name, date_key, value)
        return self._put_memory(feature_name, date_key, value)

    def _put_memory(self, feature_name: str, date_key: str, value: dict[str, Any]) -> bool:
        expiry = time.monotonic() + self._ttl_seconds
        with self._lock:
            if feature_name not in self._memory:
                self._memory[feature_name] = {}
            self._memory[feature_name][date_key] = (value, expiry)
        return True

    def _put_redis(self, feature_name: str, date_key: str, value: dict[str, Any]) -> bool:
        import json
        try:
            key = self._make_key(feature_name, date_key)
            self._redis.setex(key, int(self._ttl_seconds), json.dumps(value, default=str))
            return True
        except Exception as e:
            logger.warning("[FeatureStore] OnlineStore redis put failed (%s), falling back to memory", e)
            return self._put_memory(feature_name, date_key, value)

    def get(self, feature_name: str, date_key: str) -> dict[str, Any] | None:
        """查询特征值. 返回 None 表示未找到/已过期."""
        if not feature_name or not date_key:
            return None
        if self._redis is not None:
            val = self._get_redis(feature_name, date_key)
            if val is not None:
                return val
        return self._get_memory(feature_name, date_key)

    def _get_memory(self, feature_name: str, date_key: str) -> dict[str, Any] | None:
        with self._lock:
            feature_map = self._memory.get(feature_name)
            if feature_map is None:
                return None
            entry = feature_map.get(date_key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del feature_map[date_key]
                if not feature_map:
                    del self._memory[feature_name]
                return None
            return value

    def _get_redis(self, feature_name: str, date_key: str) -> dict[str, Any] | None:
        import json
        try:
            key = self._make_key(feature_name, date_key)
            raw = self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("[FeatureStore] OnlineStore redis get failed (%s), falling back to memory", e)
            return None

    def delete(self, feature_name: str, date_key: str) -> bool:
        """删除单个特征值."""
        if self._redis is not None:
            try:
                self._redis.delete(self._make_key(feature_name, date_key))
            except Exception as e:
                logger.warning("[FeatureStore] OnlineStore redis delete failed: %s", e)
        with self._lock:
            feature_map = self._memory.get(feature_name)
            if feature_map and date_key in feature_map:
                del feature_map[date_key]
                if not feature_map:
                    del self._memory[feature_name]
                return True
        return False

    def clear(self) -> int:
        """清空所有缓存. 返回清除的条目数."""
        count = 0
        if self._redis is not None:
            try:
                keys = self._redis.keys("fs:*")
                if keys:
                    count += self._redis.delete(*keys)
            except Exception as e:
                logger.warning("[FeatureStore] OnlineStore redis clear failed: %s", e)
        with self._lock:
            for feature_map in self._memory.values():
                count += len(feature_map)
            self._memory.clear()
        return count

    def cleanup_expired(self) -> int:
        """惰性清理所有过期条目. 返回清理数量."""
        count = 0
        now = time.monotonic()
        with self._lock:
            empty_features = []
            for feature_name, feature_map in self._memory.items():
                expired_keys = [k for k, (_, exp) in feature_map.items() if now > exp]
                for k in expired_keys:
                    del feature_map[k]
                    count += 1
                if not feature_map:
                    empty_features.append(feature_name)
            for name in empty_features:
                del self._memory[name]
        return count

    def size(self) -> int:
        """返回当前缓存条目数 (近似, 不含惰性淘汰)."""
        if self._redis is not None:
            try:
                return len(self._redis.keys("fs:*"))
            except Exception:
                pass
        with self._lock:
            return sum(len(fm) for fm in self._memory.values())

    @staticmethod
    def _validate_no_nan(value: dict[str, Any]) -> bool:
        import math
        for v in value.values():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return False
        return True
