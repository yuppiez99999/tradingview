#!/usr/bin/env python3
"""特征存储层 (Feature Store) — 数据管道物理分层之"特征层" (G9 架构增强).

对应顶级量化系统架构铁律: 数据管道第一公民, 分层为
  接入层(多源交叉校验) -> 清洗层(异常/缺失/复权) -> 特征层(因子计算缓存) -> 服务层(毫秒查询).

本模块提供:
  - 因子/特征计算结果的内存 + 文件缓存, 避免重复计算 (同一交易日同标的重复拉取).
  - TTL 控制 (默认当日有效), 盘后自动失效.
  - 线程安全 (生产环境多策略并行取特征).

用法:
  store = FeatureStore(cache_dir="reports/feature_cache")
  key = store.make_key("alpha_factor", symbol="600519", date="2026-08-08")
  features = store.get_or_compute(key, compute_fn=lambda: heavy_compute())
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path("reports/feature_cache")


class FeatureStore:
    """轻量特征缓存层 (内存 + JSON 文件落地)."""

    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE_DIR, ttl_seconds: int = 86400):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds
        self._mem: dict[str, Any] = {}
        self._lock = threading.Lock()

    @staticmethod
    def make_key(namespace: str, **parts: str) -> str:
        """构造缓存键, 例如 make_key('alpha', symbol='600519', date='2026-08-08')."""
        sorted_parts = "_".join(f"{k}={v}" for k, v in sorted(parts.items()))
        return f"{namespace}::{sorted_parts}" if sorted_parts else namespace

    def get(self, key: str) -> Any | None:
        """命中且未过期返回缓存, 否则 None."""
        with self._lock:
            if key in self._mem:
                item = self._mem[key]
                if time.time() - item["ts"] < self.ttl:
                    return item["value"]
                self._mem.pop(key, None)
        # 文件层回退
        path = self._path(key)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if time.time() - raw.get("ts", 0) < self.ttl:
                    return raw.get("value")
            except (OSError, ValueError):
                pass
        return None

    def put(self, key: str, value: Any) -> None:
        """写入内存 + 文件层."""
        with self._lock:
            self._mem[key] = {"ts": time.time(), "value": value}
        path = self._path(key)
        try:
            path.write_text(
                json.dumps({"ts": time.time(), "value": value}, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError):
            # 不可序列化时仅留内存层
            pass

    def get_or_compute(self, key: str, compute_fn: Callable[[], Any]) -> Any:
        """命中则返回缓存, 未命中则计算并写入."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute_fn()
        self.put(key, value)
        return value

    def clear_expired(self) -> int:
        """清理过期内存条目, 返回清理数量."""
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._mem.items() if now - v["ts"] >= self.ttl]
            for k in expired:
                self._mem.pop(k, None)
        return len(expired)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.cache_dir / f"{safe}.json"
