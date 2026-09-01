"""quant_modules.data_layer — 数据连接器管理器

DataConnectorManager 仅负责按优先级注册并选择"最高优先级连接器实例",
真正的 P0-P6 数据降级链实现在 utils/data/data_layer.py (DataLayer + _query_with_fallback)。

注意: 此处为轻量管理器, 不包含逐级失败回退逻辑; 需要完整降级请使用 utils/data/data_layer.py。

用法:
    manager = DataConnectorManager()
    manager.register(name, connector_instance, priority=0)
    active = manager.get_active_connector()
    label = manager.get_data_source_label()
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DataConnectorManager:
    """数据连接器管理器 — 按优先级管理多个数据源连接器。

    连接器按 priority 排序 (数值越小优先级越高), get_active_connector() 返回最高优先级可用连接器。
    """

    def __init__(self) -> None:
        self._connectors: list[dict[str, Any]] = []  # [{name, priority, instance, ...}]
        self._active: dict[str, Any] | None = None

    def register(self, name: str, connector: Any, priority: int = 100) -> None:
        """注册数据源连接器。

        Args:
            name: 连接器名称 (如 'Wind MCP' / '通达信')
            connector: 连接器实例
            priority: 优先级 (数值越小越高, 默认 100)
        """
        entry = {"name": name, "priority": priority, "instance": connector}
        self._connectors.append(entry)
        # 按优先级排序
        self._connectors.sort(key=lambda x: x["priority"])
        # 更新活跃连接器
        if self._active is None or priority < self._active["priority"]:
            self._active = entry
        logger.debug("连接器注册: %s (优先级 %d)", name, priority)

    def get_active_connector(self) -> Any | None:
        """获取最高优先级的可用连接器实例, 无可用返回 None。"""
        if self._active is None:
            return None
        return self._active["instance"]

    def get_data_source_label(self) -> str:
        """获取当前数据源标签字符串 (如 'Wind MCP'), 无可用返回 'Unknown'。"""
        if self._active is None:
            return "Unknown"
        return self._active["name"]

    def list_connectors(self) -> list[dict[str, Any]]:
        """列出所有已注册连接器 (按优先级排序)。"""
        return [
            {"name": c["name"], "priority": c["priority"]} for c in self._connectors
        ]

    def get_connector(self, name: str) -> Any | None:
        """按名称获取连接器实例, 不存在返回 None。"""
        for c in self._connectors:
            if c["name"] == name:
                return c["instance"]
        return None


__all__ = ["DataConnectorManager"]
