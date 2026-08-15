"""FeatureStore Registry — 因子注册表 + 元数据管理 + 状态流转.

设计原则:
    - (name, version) 唯一键
    - 状态流转: active ↔ inactive, active/inactive → deprecated, deprecated → active 拒绝
    - JSON 文件原子写入 (临时文件 + os.replace)
    - fail-closed: 后端不可用时返回空 + WARNING, 不抛异常
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from utils.feature_store.config import FeatureStoreConfig

logger = logging.getLogger("FeatureStore")

_VALID_STORAGE_TIERS = {"online", "offline", "both"}
_VALID_CALC_FREQUENCIES = {"daily", "weekly", "monthly", "realtime"}
_VALID_STATUSES = {"active", "inactive", "deprecated"}


@dataclass(frozen=True)
class FactorMeta:
    """因子元数据 (不可变)."""

    name: str
    version: str
    category: str = ""
    dependencies: tuple[str, ...] = ()
    calc_frequency: str = "daily"
    storage_tier: str = "both"
    created_at: str = ""
    updated_at: str = ""
    status: str = "active"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "version": self.version, "category": self.category,
            "dependencies": list(self.dependencies), "calc_frequency": self.calc_frequency,
            "storage_tier": self.storage_tier, "created_at": self.created_at,
            "updated_at": self.updated_at, "status": self.status, "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FactorMeta:
        deps = data.get("dependencies", [])
        if isinstance(deps, list):
            deps = tuple(deps)
        elif not isinstance(deps, tuple):
            deps = ()
        return cls(
            name=str(data.get("name", "")), version=str(data.get("version", "")),
            category=str(data.get("category", "")), dependencies=deps,
            calc_frequency=str(data.get("calc_frequency", "daily")),
            storage_tier=str(data.get("storage_tier", "both")),
            created_at=str(data.get("created_at", "")), updated_at=str(data.get("updated_at", "")),
            status=str(data.get("status", "active")), description=str(data.get("description", "")),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Registry:
    """因子注册表 — JSON 后端 + 内存缓存."""

    def __init__(self, config: FeatureStoreConfig) -> None:
        self._config = config
        self._cache: dict[tuple[str, str], FactorMeta] = {}
        parquet_dir = config.offline_parquet_dir.rstrip("/\\")
        self._json_path = os.path.join(os.path.dirname(parquet_dir), "feature_registry.json")
        if not self._json_path:
            self._json_path = "data/feature_registry.json"
        self._load()

    def _load(self) -> None:
        try:
            if not os.path.exists(self._json_path):
                self._cache = {}
                return
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("[FeatureStore] registry JSON is not dict, empty cache")
                self._cache = {}
                return
            self._cache = {}
            for key_str, meta_dict in data.items():
                if not isinstance(meta_dict, dict):
                    continue
                meta = FactorMeta.from_dict(meta_dict)
                parts = key_str.split("\x00", 1)
                if len(parts) == 2:
                    self._cache[(parts[0], parts[1])] = meta
                else:
                    self._cache[(meta.name, meta.version)] = meta
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[FeatureStore] registry backend unavailable: %s", e)
            self._cache = {}

    def _save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self._json_path) or ".", exist_ok=True)
            data = {}
            for (name, version), meta in self._cache.items():
                data[f"{name}\x00{version}"] = meta.to_dict()
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
            return True
        except (OSError, ValueError, TypeError) as e:
            logger.warning("[FeatureStore] registry save failed: %s", e)
            tmp_path = self._json_path + ".tmp"
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return False

    def register(self, meta: FactorMeta) -> tuple[bool, str]:
        if meta.storage_tier not in _VALID_STORAGE_TIERS:
            return (False, f"invalid storage_tier '{meta.storage_tier}'")
        if meta.calc_frequency not in _VALID_CALC_FREQUENCIES:
            return (False, f"invalid calc_frequency '{meta.calc_frequency}'")
        key = (meta.name, meta.version)
        if key in self._cache:
            return (False, "duplicate registration")
        now = _utc_now_iso()
        new_meta = FactorMeta(
            name=meta.name, version=meta.version, category=meta.category,
            dependencies=meta.dependencies, calc_frequency=meta.calc_frequency,
            storage_tier=meta.storage_tier, created_at=meta.created_at or now,
            updated_at=now, status=meta.status if meta.status in _VALID_STATUSES else "active",
            description=meta.description,
        )
        self._cache[key] = new_meta
        if not self._save():
            del self._cache[key]
            return (False, "registry backend unavailable")
        return (True, "registered")

    def get(self, name: str, version: str | None = None) -> FactorMeta | None:
        if version is not None:
            return self._cache.get((name, version))
        candidates = [(v, m) for (n, v), m in self._cache.items() if n == name]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[-1][1]

    def list_factors(self, status: str | None = None, category: str | None = None) -> list[FactorMeta]:
        result = []
        for meta in self._cache.values():
            if status is not None and meta.status != status:
                continue
            if category is not None and meta.category != category:
                continue
            result.append(meta)
        return result

    def is_registered(self, name: str, version: str | None = None) -> bool:
        meta = self.get(name, version)
        return meta is not None and meta.status == "active"

    def _update_status(self, name: str, version: str | None, new_status: str, forbidden_from: set[str]) -> tuple[bool, str]:
        meta = self.get(name, version)
        if meta is None:
            return (False, "factor not found")
        if meta.status == new_status:
            return (False, f"already {new_status}")
        if meta.status in forbidden_from:
            return (False, f"cannot {new_status} from {meta.status}")
        key = (meta.name, meta.version)
        updated_meta = FactorMeta(
            name=meta.name, version=meta.version, category=meta.category,
            dependencies=meta.dependencies, calc_frequency=meta.calc_frequency,
            storage_tier=meta.storage_tier, created_at=meta.created_at,
            updated_at=_utc_now_iso(), status=new_status, description=meta.description,
        )
        old_meta = self._cache[key]
        self._cache[key] = updated_meta
        if not self._save():
            self._cache[key] = old_meta
            return (False, "registry backend unavailable")
        return (True, new_status)

    def deactivate(self, name: str, version: str | None = None) -> tuple[bool, str]:
        return self._update_status(name, version, "inactive", {"inactive", "deprecated"})

    def activate(self, name: str, version: str | None = None) -> tuple[bool, str]:
        return self._update_status(name, version, "active", {"deprecated"})

    def deprecate(self, name: str, version: str | None = None) -> tuple[bool, str]:
        return self._update_status(name, version, "deprecated", set())
