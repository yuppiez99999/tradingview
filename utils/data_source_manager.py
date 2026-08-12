# -*- coding: utf-8 -*-
"""
统一数据源管理器 — 借鉴 TradingAgents-CN 多层回退模式
支持：优先级回退、连接健康检查、缓存统计、自动降级通知
"""

import time
import sys
from enum import Enum
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field

from .logging_manager import get_logger


class DataSourceStatus(Enum):
    """数据源状态 — 借鉴 TradingAgents-CN 的 available/unavailable 检查"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class CacheStats:
    """缓存统计 — 借鉴 TradingAgents-CN cache_manager 模式"""
    hits: int = 0
    misses: int = 0
    size_bytes: int = 0
    item_count: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': f"{self.hit_rate:.1%}",
            'size_kb': round(self.size_bytes / 1024, 1),
            'items': self.item_count,
        }


@dataclass
class DataSourceInfo:
    """数据源信息"""
    name: str
    priority: int
    status: DataSourceStatus = DataSourceStatus.UNKNOWN
    last_check_time: float = 0
    error_count: int = 0
    success_count: int = 0
    cache: CacheStats = field(default_factory=CacheStats)


class DataSourceRegistry:
    """数据源注册表 — 统一管理所有数据源及其优先级"""

    def __init__(self):
        self._sources: Dict[str, DataSourceInfo] = {}
        self._logger = get_logger('data_source')

    def register(self, name: str, priority: int):
        """注册数据源"""
        self._sources[name] = DataSourceInfo(name=name, priority=priority)
        self._logger.info(f"📋 注册数据源: {name} (优先级: {priority})")

    def mark_healthy(self, name: str):
        if name in self._sources:
            self._sources[name].status = DataSourceStatus.HEALTHY
            self._sources[name].last_check_time = time.time()
            self._sources[name].success_count += 1

    def mark_degraded(self, name: str, reason: str = ""):
        if name in self._sources:
            self._sources[name].status = DataSourceStatus.DEGRADED
            self._sources[name].error_count += 1
            self._logger.warning(f"⚠️ 数据源降级: {name}" + (f" ({reason})" if reason else ""))

    def mark_unavailable(self, name: str, reason: str = ""):
        if name in self._sources:
            self._sources[name].status = DataSourceStatus.UNAVAILABLE
            self._sources[name].error_count += 1
            self._logger.error(f"❌ 数据源不可用: {name}" + (f" ({reason})" if reason else ""))

    def get_available_sources(self) -> List[str]:
        """获取当前可用的数据源列表（按优先级排序）"""
        available = [
            name for name, info in self._sources.items()
            if info.status != DataSourceStatus.UNAVAILABLE
        ]
        available.sort(key=lambda n: self._sources[n].priority, reverse=True)
        return available

    def get_status_report(self) -> str:
        """生成数据源状态报告"""
        lines = ["📊 数据源状态:"]
        for name, info in sorted(self._sources.items(),
                                 key=lambda x: x[1].priority, reverse=True):
            status_icon = {
                DataSourceStatus.HEALTHY: '✅',
                DataSourceStatus.DEGRADED: '⚠️',
                DataSourceStatus.UNAVAILABLE: '❌',
                DataSourceStatus.UNKNOWN: '⚪',
            }[info.status]
            lines.append(
                f"  {status_icon} {name} | 优先级:{info.priority} | "
                f"成功:{info.success_count} | 失败:{info.error_count} | "
                f"缓存命中率:{info.cache.hit_rate:.0%}")
        return "\n".join(lines)


class PriorityDataSourceManager:
    """优先级数据源管理器 — 借鉴 TradingAgents-CN 的统一数据获取与回退模式

    使用示例:
        manager = PriorityDataSourceManager()

        # 注册数据源（优先级高的先尝试）
        manager.register_source('wind', get_wind_data, priority=100)
        manager.register_source('ifind', get_ifind_data, priority=80)
        manager.register_source('sina', get_sina_data, priority=50)
        manager.register_source('akshare', get_akshare_data, priority=30)

        # 获取数据（自动尝试高优先级，失败则回退）
        result = manager.fetch_with_fallback('601088', default={'price': 0})
    """

    def __init__(self, registry: DataSourceRegistry = None):
        self._sources: Dict[str, Callable] = {}  # name -> fetch function
        self._priorities: Dict[str, int] = {}
        self._registry = registry or DataSourceRegistry()
        self._last_successful_source: Optional[str] = None
        self._logger = get_logger('data_source')

    def register_source(self, name: str, fetch_func: Callable, priority: int = 0):
        """注册数据源及其获取函数"""
        self._sources[name] = fetch_func
        self._priorities[name] = priority
        self._registry.register(name, priority)

    def fetch_with_fallback(self, *args, default=None, log_target: str = None,
                            **kwargs) -> Any:
        """按优先级尝试所有数据源，失败自动回退 — 借鉴 TradingAgents-CN 模式

        Args:
            *args: 传递给数据源函数的参数
            default: 所有数据源都失败时的默认返回值
            log_target: 日志中显示的目标名称（如股票代码）
            **kwargs: 传递给数据源函数的参数
        """
        target_str = f" ({log_target})" if log_target else ""
        available = self._registry.get_available_sources()

        if not available:
            self._logger.error(f"❌ 无可用数据源{target_str}，返回默认值")
            return default

        self._logger.info(f"🔍 尝试获取数据{target_str} | 可用数据源: {available}")

        for name in available:
            fetch_func = self._sources.get(name)
            if fetch_func is None:
                continue

            try:
                result = fetch_func(*args, **kwargs)
                # 验证结果有效性
                if result is not None and not (isinstance(result, str) and result.startswith('❌')):
                    self._registry.mark_healthy(name)
                    self._last_successful_source = name
                    self._logger.debug(f"✅ 数据源 {name} 返回成功{target_str}")
                    return result
                else:
                    self._registry.mark_degraded(name, "返回无效数据")
                    self._logger.warning(f"⚠️ 数据源 {name} 返回无效数据{target_str}，尝试下一个")
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
                self._registry.mark_unavailable(name, str(e))
                self._logger.warning(f"⚠️ 数据源 {name} 异常{target_str}: {e}")
                continue

        self._logger.error(f"❌ 所有数据源均失败{target_str}，返回默认值")
        return default

    def get_status(self) -> str:
        """获取数据源管理器状态"""
        return self._registry.get_status_report()

    @property
    def last_successful_source(self) -> Optional[str]:
        return self._last_successful_source


# 全局单例
_data_source_manager: Optional[PriorityDataSourceManager] = None


def get_data_source_manager() -> PriorityDataSourceManager:
    global _data_source_manager
    if _data_source_manager is None:
        _data_source_manager = PriorityDataSourceManager()
    return _data_source_manager