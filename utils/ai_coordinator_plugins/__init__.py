"""
AI 协调器插件包 — W.C.3 deepseek-harness 插件化重构

把 ai_coordinator.py 中硬编码 if-else 路由 + 简单多数投票冲突检测
抽象为可插拔插件, 由 PluginRegistry 统一管理.

可用插件:
- RoutingPlugin: 任务路由插件 (按 TaskType 选择模型)
- ConflictDetectionPlugin: 冲突检测插件 (多 AI 系统信号融合)

设计参考: deepseek-harness (dsh) 的插件化思想, 但只借鉴设计不换工具.
向后兼容: 旧 AICoordinator.route() / resolve_conflicts() API 保留,
        内部委托给 PluginRegistry, 由 feature-flag USE_PLUGIN_COORDINATOR 控制走插件还是旧路径.
"""
from __future__ import annotations

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
    pass

try:
    from .registry import PluginRegistry, get_registry
except ImportError:
    pass

try:
    from .routing_plugin import (
        BudgetGuardRoutingPlugin,
        DeepResearchRoutingPlugin,
        DefaultRoutingPlugin,
        IntradayRoutingPlugin,
        MacroAnalysisRoutingPlugin,
    )
except ImportError:
    pass

try:
    from .conflict_detection_plugin import MajorityVotePlugin
except ImportError:
    pass

__all__ = [
    "Plugin",
    "RoutingPlugin",
    "ConflictDetectionPlugin",
    "RoutingContext",
    "ConflictContext",
    "RoutingResult",
    "ConflictResult",
    "PluginRegistry",
    "get_registry",
    "IntradayRoutingPlugin",
    "DeepResearchRoutingPlugin",
    "MacroAnalysisRoutingPlugin",
    "DefaultRoutingPlugin",
    "BudgetGuardRoutingPlugin",
    "MajorityVotePlugin",
]
