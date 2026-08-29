"""
Plugin 抽象接口 — W.C.3 deepseek-harness 插件化重构基类

定义两类插件契约:
1. RoutingPlugin: 任务路由 (TaskType + Priority + 预算状态 -> 模型名)
2. ConflictDetectionPlugin: 冲突检测 (多 AI 系统决策 -> 解决后的决策)

所有插件通过 can_handle() 判断是否处理当前上下文, PluginRegistry 按 priority
降序遍历, 第一个 can_handle=True 的插件执行 handle().
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

# 复用 ai_coordinator 的枚举 (避免循环导入, 用字符串约定 + 运行时校验)
# TaskType / Priority 在 ai_coordinator.py 中定义, 这里用 Any 接收


@dataclass
class RoutingContext:
    """路由上下文 — 传给 RoutingPlugin.can_handle / handle"""

    task_type: Any
    priority: Any
    budget_ratio: float
    token_used_today: int = 0
    daily_token_budget: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingResult:
    """路由结果"""

    model: str
    plugin_name: str = ""
    reason: str = ""


@dataclass
class ConflictContext:
    """冲突检测上下文 — 传给 ConflictDetectionPlugin.can_handle / handle"""

    decisions_by_source: dict[str, dict[str, str]]
    source_weights: dict[str, float] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConflictResult:
    """冲突检测结果 — 与旧 resolve_conflicts 返回结构一致"""

    resolved: dict[str, dict[str, Any]] = field(default_factory=dict)
    plugin_name: str = ""


class Plugin(abc.ABC):
    """插件抽象基类

    子类必须实现:
        - name: 插件唯一标识 (str)
        - priority: 排序优先级 (int, 越大越优先)
        - can_handle(context) -> bool: 是否处理该上下文
        - handle(context) -> Any: 执行处理, 返回结果
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """插件唯一标识"""

    @property
    @abc.abstractmethod
    def priority(self) -> int:
        """优先级 (越大越优先, PluginRegistry 按降序排列)"""

    @abc.abstractmethod
    def can_handle(self, context: Any) -> bool:
        """判断本插件是否处理该上下文"""

    @abc.abstractmethod
    def handle(self, context: Any) -> Any:
        """执行处理, 返回结果"""

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(name={self.name!r}, priority={self.priority})"
        )


class RoutingPlugin(Plugin):
    """路由插件抽象基类 — 处理 RoutingContext, 返回 RoutingResult"""

    @abc.abstractmethod
    def can_handle(self, context: RoutingContext) -> bool:
        """判断是否处理该路由上下文"""

    @abc.abstractmethod
    def handle(self, context: RoutingContext) -> RoutingResult:
        """执行路由, 返回模型选择结果"""


class ConflictDetectionPlugin(Plugin):
    """冲突检测插件抽象基类 — 处理 ConflictContext, 返回 ConflictResult"""

    @abc.abstractmethod
    def can_handle(self, context: ConflictContext) -> bool:
        """判断是否处理该冲突检测上下文"""

    @abc.abstractmethod
    def handle(self, context: ConflictContext) -> ConflictResult:
        """执行冲突检测, 返回解决后的决策"""
