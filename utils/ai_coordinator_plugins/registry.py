"""
PluginRegistry — 插件注册中心

职责:
1. 动态注册/卸载插件
2. 按插件 priority 降序排列
3. 遍历找到第一个 can_handle=True 的插件执行 handle()
4. 支持从 YAML 配置批量加载插件
5. 全局单例 get_registry()

线程安全: 注册/卸载加锁, 查询/执行无锁 (假设插件列表在启动时确定).
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from typing import Any

try:
    from .base import (
        ConflictContext,
        ConflictDetectionPlugin,
        ConflictResult,
        Plugin,
        RoutingContext,
        RoutingPlugin,
        RoutingResult,
    )
except ImportError:
    Plugin = object  # type: ignore

logger = logging.getLogger("ai_coordinator_plugins")


class PluginRegistryError(Exception):
    """PluginRegistry 操作异常"""


class PluginRegistry:
    """插件注册中心 — 统一管理 RoutingPlugin / ConflictDetectionPlugin

    Usage:
        registry = PluginRegistry()
        registry.register(IntradayRoutingPlugin())
        result = registry.resolve_routing(RoutingContext(...))
    """

    def __init__(self) -> None:
        self._routing_plugins: list[RoutingPlugin] = []
        self._conflict_plugins: list[ConflictDetectionPlugin] = []
        self._lock = threading.RLock()
        self._plugin_names: dict[str, Plugin] = {}

    # ── 注册 / 卸载 ──

    def register(self, plugin: Plugin) -> None:
        """注册插件 (按 priority 降序插入)

        Args:
            plugin: Plugin 实例 (RoutingPlugin 或 ConflictDetectionPlugin)

        Raises:
            PluginRegistryError: 类型不支持 / 重名
        """
        with self._lock:
            name = plugin.name
            if name in self._plugin_names:
                raise PluginRegistryError(f"插件已存在: {name}")
            self._plugin_names[name] = plugin

            if isinstance(plugin, RoutingPlugin):
                self._routing_plugins.append(plugin)
                self._routing_plugins.sort(key=lambda p: p.priority, reverse=True)
            elif isinstance(plugin, ConflictDetectionPlugin):
                self._conflict_plugins.append(plugin)
                self._conflict_plugins.sort(key=lambda p: p.priority, reverse=True)
            else:
                raise PluginRegistryError(
                    f"不支持的插件类型: {type(plugin).__name__} (需 RoutingPlugin 或 ConflictDetectionPlugin)"
                )
            logger.debug("插件已注册: %s (priority=%d)", name, plugin.priority)

    def unregister(self, name: str) -> Plugin | None:
        """按名称卸载插件

        Returns:
            被卸载的插件, 不存在返回 None
        """
        with self._lock:
            plugin = self._plugin_names.pop(name, None)
            if plugin is None:
                return None
            if isinstance(plugin, RoutingPlugin):
                self._routing_plugins = [
                    p for p in self._routing_plugins if p.name != name
                ]
            elif isinstance(plugin, ConflictDetectionPlugin):
                self._conflict_plugins = [
                    p for p in self._conflict_plugins if p.name != name
                ]
            logger.debug("插件已卸载: %s", name)
            return plugin

    def clear(self) -> None:
        """清空所有插件"""
        with self._lock:
            self._routing_plugins.clear()
            self._conflict_plugins.clear()
            self._plugin_names.clear()

    # ── 查询 ──

    def list_routing_plugins(self) -> list[dict[str, Any]]:
        """列出所有路由插件 (按 priority 降序)"""
        return [
            {"name": p.name, "priority": p.priority, "type": type(p).__name__}
            for p in self._routing_plugins
        ]

    def list_conflict_plugins(self) -> list[dict[str, Any]]:
        """列出所有冲突检测插件 (按 priority 降序)"""
        return [
            {"name": p.name, "priority": p.priority, "type": type(p).__name__}
            for p in self._conflict_plugins
        ]

    def get_plugin(self, name: str) -> Plugin | None:
        """按名称获取插件"""
        return self._plugin_names.get(name)

    def __len__(self) -> int:
        return len(self._plugin_names)

    # ── 执行 ──

    def resolve_routing(self, context: RoutingContext) -> RoutingResult | None:
        """遍历路由插件, 返回第一个 can_handle=True 的 handle() 结果

        Returns:
            RoutingResult 或 None (无插件可处理)
        """
        for plugin in self._routing_plugins:
            try:
                if plugin.can_handle(context):
                    result = plugin.handle(context)
                    result.plugin_name = plugin.name
                    return result
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
                logger.warning("路由插件 %s 执行失败: %s", plugin.name, e)
                continue
        return None

    def resolve_conflict(self, context: ConflictContext) -> ConflictResult | None:
        """遍历冲突检测插件, 返回第一个 can_handle=True 的 handle() 结果

        Returns:
            ConflictResult 或 None (无插件可处理)
        """
        for plugin in self._conflict_plugins:
            try:
                if plugin.can_handle(context):
                    result = plugin.handle(context)
                    result.plugin_name = plugin.name
                    return result
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
                logger.warning("冲突检测插件 %s 执行失败: %s", plugin.name, e)
                continue
        return None

    # ── 从 YAML 配置加载 ──

    def load_from_config(self, config_path: str | None = None) -> int:
        """从 YAML 配置批量加载插件

        配置格式:
            routing_plugins:
              - module: "utils.ai_coordinator_plugins.routing_plugin"
                class: "IntradayRoutingPlugin"
                enabled: true
            conflict_plugins:
              - module: "utils.ai_coordinator_plugins.conflict_detection_plugin"
                class: "MajorityVotePlugin"
                enabled: true

        Args:
            config_path: YAML 路径, None 时用默认 configs/ai_coordinator_plugins.yaml

        Returns:
            成功加载的插件数
        """
        if config_path is None:
            base_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            config_path = os.path.join(
                base_dir, "configs", "ai_coordinator_plugins.yaml"
            )

        try:
            import yaml  # type: ignore
        except ImportError:
            logger.warning("PyYAML 未安装, 跳过插件配置加载")
            return 0

        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.debug("插件配置不存在: %s", config_path)
            return 0
        except (ValueError, OSError, RuntimeError) as e:
            logger.warning("插件配置加载失败 %s: %s", config_path, e)
            return 0

        count = 0
        for section in ("routing_plugins", "conflict_plugins"):
            for entry in cfg.get(section, []) or []:
                if not entry.get("enabled", True):
                    continue
                module_name = entry.get("module")
                class_name = entry.get("class")
                if not module_name or not class_name:
                    continue
                try:
                    module = importlib.import_module(module_name)
                    cls: type[Plugin] = getattr(module, class_name)
                    plugin = cls()
                    self.register(plugin)
                    count += 1
                except (
                    ImportError,
                    AttributeError,
                    PluginRegistryError,
                    TypeError,
                    ValueError,
                ) as e:
                    logger.warning("插件加载失败 %s.%s: %s", module_name, class_name, e)
        logger.info("从 %s 加载 %d 个插件", config_path, count)
        return count


# ── 全局单例 ──

_registry: PluginRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> PluginRegistry:
    """获取全局 PluginRegistry 单例"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = PluginRegistry()
    return _registry


def reset_registry() -> None:
    """重置全局单例 (仅测试用)"""
    global _registry
    with _registry_lock:
        _registry = None
