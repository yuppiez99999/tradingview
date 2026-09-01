"""GitHub 周热门项目集成注册器 (W8.6, 2026-08-21).

统一管理三个适配器的状态查询、启动自检和健康监控:
    - unsloth_adapter       (本地 LLM 训练)
    - switchyard_adapter    (LLM 多模型路由)
    - openviking_memory     (Agent 长期记忆)

集成点:
    - 15_每日工作流/run_daily_morning.py  启动自检
    - institutional_pipeline_runner.py   管道健康检查
    - ui/pages/01_🏠_系统概览.py          UI 状态卡片

使用方式:
    from utils.github_integration_registry import (
        GitHubIntegrationRegistry, get_registry, run_startup_selfcheck,
    )

    registry = get_registry()
    status = registry.get_all_status()          # 全部状态
    report = registry.selfcheck()               # 启动自检报告
    registry.log_status_snapshot()              # 记录状态快照到日志

设计原则:
    - 单一入口: 所有适配器状态查询集中于此
    - 优雅降级: 任何适配器导入失败不影响其他
    - 零侵入: 不修改适配器内部, 仅做聚合
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AdapterStatus:
    """单个适配器状态."""

    name: str
    available: bool
    enabled: bool
    adapter_path: str
    init_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "enabled": self.enabled,
            "adapter_path": self.adapter_path,
            "init_error": self.init_error,
            "extra": self.extra,
        }


@dataclass
class SelfcheckReport:
    """启动自检报告."""

    timestamp: str
    total: int
    available: int
    unavailable: int
    adapters: list[dict[str, Any]]
    overall_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total": self.total,
            "available": self.available,
            "unavailable": self.unavailable,
            "adapters": self.adapters,
            "overall_ok": self.overall_ok,
        }


class GitHubIntegrationRegistry:
    """GitHub 周热门项目集成注册器.

    聚合三个适配器的状态, 提供统一查询和自检入口.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or self._load_config()
        self._adapters: dict[str, Any] = {}
        self._load_adapters()

    @staticmethod
    def _load_config() -> dict[str, Any]:
        """从 system_config.json 加载 github_integration 段."""
        try:
            config_path = Path(__file__).resolve().parent.parent / "system_config.json"
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("github_integration", {})
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.warning("加载 github_integration 配置失败: %s", e)
            return {}

    def _load_adapters(self) -> None:
        """延迟加载三个适配器, 任何失败不影响其他."""
        # unsloth
        try:
            from utils.unsloth_adapter import get_unsloth_adapter

            self._adapters["unsloth"] = get_unsloth_adapter()
        except (ImportError, RuntimeError) as e:
            logger.warning("unsloth 适配器加载失败: %s", e)
            self._adapters["unsloth"] = None

        # switchyard
        try:
            from utils.switchyard_adapter import get_switchyard_adapter

            self._adapters["switchyard"] = get_switchyard_adapter()
        except (ImportError, RuntimeError) as e:
            logger.warning("switchyard 适配器加载失败: %s", e)
            self._adapters["switchyard"] = None

        # openviking
        try:
            from quant_modules.ai_hedge_fund.openviking_memory import (
                get_openviking_memory,
            )

            self._adapters["openviking"] = get_openviking_memory()
        except (ImportError, RuntimeError) as e:
            logger.warning("openviking 适配器加载失败: %s", e)
            self._adapters["openviking"] = None

        # onnxruntime (2026-08-22 新增)
        try:
            from utils.onnxruntime_adapter import get_onnx_adapter

            self._adapters["onnxruntime"] = get_onnx_adapter()
        except (ImportError, RuntimeError) as e:
            logger.warning("onnxruntime 适配器加载失败: %s", e)
            self._adapters["onnxruntime"] = None

        # superpowers (2026-08-22 新增)
        try:
            from utils.superpowers_adapter import get_superpowers_adapter

            self._adapters["superpowers"] = get_superpowers_adapter()
        except (ImportError, RuntimeError) as e:
            logger.warning("superpowers 适配器加载失败: %s", e)
            self._adapters["superpowers"] = None

    def get_adapter_status(self, name: str) -> AdapterStatus:
        """查询单个适配器状态."""
        adapter = self._adapters.get(name)
        cfg = self._config.get(name, {})
        enabled = cfg.get("enabled", False)
        adapter_path = cfg.get("adapter", "")

        if adapter is None:
            return AdapterStatus(
                name=name,
                available=False,
                enabled=enabled,
                adapter_path=adapter_path,
                init_error="适配器加载失败",
            )

        try:
            available = adapter.is_ready()
            extra = adapter.get_status() if hasattr(adapter, "get_status") else {}
            init_error = (
                extra.pop("init_error", None) if isinstance(extra, dict) else None
            )
            return AdapterStatus(
                name=name,
                available=available,
                enabled=enabled,
                adapter_path=adapter_path,
                init_error=init_error,
                extra=extra,
            )
        except (AttributeError, RuntimeError) as e:
            return AdapterStatus(
                name=name,
                available=False,
                enabled=enabled,
                adapter_path=adapter_path,
                init_error=str(e),
            )

    def get_all_status(self) -> dict[str, AdapterStatus]:
        """查询全部适配器状态."""
        return {
            name: self.get_adapter_status(name)
            for name in (
                "unsloth",
                "switchyard",
                "openviking",
                "onnxruntime",
                "superpowers",
            )
        }

    def selfcheck(self) -> SelfcheckReport:
        """启动自检 — 供 run_daily_morning.py 调用.

        Returns:
            SelfcheckReport 含每个适配器状态和总体结论
        """
        statuses = self.get_all_status()
        adapters = [s.to_dict() for s in statuses.values()]
        available_count = sum(1 for s in statuses.values() if s.available)
        unavailable_count = len(statuses) - available_count
        # overall_ok: 配置启用的适配器中, 至少 openviking 可用 (最低要求)
        enabled_statuses = [s for s in statuses.values() if s.enabled]
        overall_ok = any(s.available for s in enabled_statuses)

        report = SelfcheckReport(
            timestamp=datetime.now().isoformat(),
            total=len(statuses),
            available=available_count,
            unavailable=unavailable_count,
            adapters=adapters,
            overall_ok=overall_ok,
        )
        return report

    def log_status_snapshot(self) -> None:
        """记录状态快照到日志 (供每日报告归档)."""
        report = self.selfcheck()
        logger.info(
            "GitHub 集成自检: total=%d, available=%d, unavailable=%d, overall_ok=%s",
            report.total,
            report.available,
            report.unavailable,
            report.overall_ok,
        )
        for adapter in report.adapters:
            status_icon = "✓" if adapter["available"] else "✗"
            logger.info(
                "  %s %s: available=%s, enabled=%s, error=%s",
                status_icon,
                adapter["name"],
                adapter["available"],
                adapter["enabled"],
                adapter.get("init_error"),
            )

    def get_unsloth_adapter(self) -> Any:
        """获取 unsloth 适配器 (供 lgb_enhanced_trainer 调用)."""
        return self._adapters.get("unsloth")

    def get_switchyard_adapter(self) -> Any:
        """获取 switchyard 适配器 (供 glm5_client 调用)."""
        return self._adapters.get("switchyard")

    def get_openviking_memory(self) -> Any:
        """获取 openviking 记忆适配器 (供 ai_hedge_fund 调用)."""
        return self._adapters.get("openviking")


_registry_instance: GitHubIntegrationRegistry | None = None


def get_registry() -> GitHubIntegrationRegistry:
    """获取注册器单例."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = GitHubIntegrationRegistry()
    return _registry_instance


def run_startup_selfcheck() -> SelfcheckReport:
    """启动自检便捷函数 — 供 run_daily_morning.py 一行调用."""
    registry = get_registry()
    report = registry.selfcheck()
    registry.log_status_snapshot()
    return report


if __name__ == "__main__":
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    report = run_startup_selfcheck()
