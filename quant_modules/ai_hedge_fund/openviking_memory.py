"""OpenViking 适配器 — AI Hedge Fund Agent 长期记忆 (W8.6 集成).

将 volcengine/OpenViking (10_第三方项目/OpenViking) 接入 AI Hedge Fund,
为 20 位分析师 Agent 提供统一上下文数据库 + RAG + 技能记忆.

集成点:
    - quant_modules/ai_hedge_fund/agents/ 20 位分析师
    - quant_modules/ai_hedge_fund/graph/ LangGraph 编排
    - utils/ai_coordinator.py AICoordinator (决策审计)

使用方式:
    from quant_modules.ai_hedge_fund.openviking_memory import (
        OpenVikingMemory, get_openviking_memory,
    )

    memory = get_openviking_memory()
    if memory.is_ready():
        memory.add_context(agent_id="warren_buffett",
                          content="2024Q3 茅台毛利率 91.5%",
                          metadata={"ticker": "600519", "type": "fundamental"})
        context = memory.retrieve(agent_id="warren_buffett",
                                  query="茅台基本面", top_k=5)

降级策略:
    - OpenViking 服务未启动 → is_ready()=False, Agent 回退到无记忆模式
    - SDK 未安装 → 仅记录警告, 不阻断 LangGraph 编排
    - 检索失败 → 返回空列表, Agent 使用当前上下文

依赖路径:
    - 主系统: 28-终极量化交易系统8.4/
    - OpenViking 源码: 10_第三方项目/OpenViking/sdk/python/
    - OpenViking 服务: 默认 http://localhost:8765

集成日期: 2026-08-21 (v8.6, GitHub 周热门项目集成)
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_OPENVIKING_SRC = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "10_第三方项目"
    / "OpenViking"
)


@dataclass
class OpenVikingConfig:
    """OpenViking 适配配置."""

    server_url: str = "http://localhost:8765"
    api_key: str = field(
        default_factory=lambda: os.environ.get("OPENVIKING_API_KEY", "")
    )
    collection_prefix: str = "ai_hedge_fund"  # 集合前缀 (按 agent 隔离)
    embedding_model: str = "bge-m3"  # 中文嵌入模型
    timeout_seconds: int = 30
    use_sdk: bool = True  # True=使用 sdk/python, False=仅 HTTP

    # Agent 记忆命名空间
    agent_namespaces: dict[str, str] = field(
        default_factory=lambda: {
            "warren_buffett": "value_investing",
            "ben_graham": "value_investing",
            "phil_fisher": "growth_investing",
            "cathie_wood": "growth_investing",
            "michael_burry": "contrarian",
            "charlie_munger": "value_investing",
            "peter_lynch": "growth_investing",
            "stanley_druckenmiller": "macro",
            "ray_dalio": "macro",
            "bill_ackman": "activist",
        }
    )


@dataclass
class AgentMemory:
    """单条 Agent 记忆记录."""

    agent_id: str
    content: str
    metadata: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    memory_type: str = "context"  # context / skill / decision / rationale


class OpenVikingMemory:
    """OpenViking 适配器 — Agent 长期记忆 + RAG.

    设计原则:
        - 不可变: add_context 不修改现有记忆, 仅追加
        - 优雅降级: 服务不可用时 Agent 回退到无记忆模式
        - 命名空间隔离: 不同分析师的记忆按 namespace 隔离
    """

    def __init__(self, config: Optional[OpenVikingConfig] = None) -> None:
        self.config = config or OpenVikingConfig()
        self._client: Any = None
        self._init_error: Optional[str] = None
        self._load_client()

    def _load_client(self) -> None:
        """加载 OpenViking Python SDK."""
        if not self.config.use_sdk:
            return
        try:
            sdk_path = _OPENVIKING_SRC / "sdk" / "python"
            if sdk_path.exists():
                str_path = str(sdk_path)
                if str_path not in sys.path:
                    sys.path.insert(0, str_path)
            from openviking_sdk.client import SyncHTTPClient  # type: ignore

            self._client = SyncHTTPClient(
                url=self.config.server_url,
                api_key=self.config.api_key,
                timeout=self.config.timeout_seconds,
            )
            logger.info("✓ OpenViking SDK 已加载 (server=%s)", self.config.server_url)
        except ImportError as e:
            self._init_error = f"OpenViking SDK 未安装: {e}"
            logger.warning(self._init_error)
        except (RuntimeError, OSError, ConnectionError) as e:
            self._init_error = f"OpenViking 连接失败: {e}"
            logger.warning(self._init_error)

    def is_ready(self) -> bool:
        """OpenViking 是否可用."""
        return self._client is not None

    def add_context(
        self,
        agent_id: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
        memory_type: str = "context",
    ) -> bool:
        """为 Agent 添加上下文记忆.

        Args:
            agent_id: 分析师 ID (如 "warren_buffett")
            content: 记忆内容 (文本)
            metadata: 元数据 (ticker / type / source 等)
            memory_type: 记忆类型 (context/skill/decision/rationale)

        Returns:
            True=成功, False=失败 (Agent 应降级到无记忆模式)
        """
        if not self.is_ready():
            return False
        namespace = self.config.agent_namespaces.get(agent_id, "default")
        collection = f"{self.config.collection_prefix}_{namespace}"
        record = AgentMemory(
            agent_id=agent_id,
            content=content,
            metadata=metadata or {},
            memory_type=memory_type,
        )
        try:
            self._client.add_message(
                session_id=collection,
                content=record.content,
                metadata={
                    **record.metadata,
                    "agent_id": agent_id,
                    "memory_type": memory_type,
                    "timestamp": record.timestamp,
                },
            )
            logger.debug("✓ Agent %s 记忆已添加 (type=%s)", agent_id, memory_type)
            return True
        except (RuntimeError, ValueError, ConnectionError, TimeoutError) as e:
            logger.warning("OpenViking 记忆写入失败: %s", e)
            return False

    def retrieve(
        self,
        agent_id: str,
        query: str,
        top_k: int = 5,
        memory_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """检索 Agent 相关记忆 (RAG).

        Args:
            agent_id: 分析师 ID
            query: 检索查询 (如 "茅台基本面")
            top_k: 返回 top_k 条
            memory_type: 限定记忆类型 (None=全部)

        Returns:
            [{"content": str, "metadata": dict, "score": float}]
            失败返回空列表 (Agent 使用当前上下文)
        """
        if not self.is_ready():
            return []
        namespace = self.config.agent_namespaces.get(agent_id, "default")
        collection = f"{self.config.collection_prefix}_{namespace}"
        try:
            filter_dict = {"agent_id": agent_id}
            if memory_type:
                filter_dict["memory_type"] = memory_type
            results = self._client.find(
                session_id=collection,
                query=query,
                top_k=top_k,
                filter=filter_dict,
            )
            return results if isinstance(results, list) else []
        except (RuntimeError, ValueError, ConnectionError, TimeoutError) as e:
            logger.warning("OpenViking 检索失败: %s", e)
            return []

    def add_decision_audit(
        self,
        agent_id: str,
        decision: dict[str, Any],
        rationale: str,
    ) -> bool:
        """记录 Agent 决策审计 (供 AICoordinator 冲突检测).

        Args:
            agent_id: 分析师 ID
            decision: 决策内容 (action/code/weight 等)
            rationale: 决策理由

        Returns:
            True=成功
        """
        return self.add_context(
            agent_id=agent_id,
            content=rationale,
            metadata={"decision": decision},
            memory_type="decision",
        )

    def get_agent_history(
        self,
        agent_id: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """获取 Agent 历史决策 (供 UI 13_🤖_AI决策与ML信号 展示)."""
        return self.retrieve(
            agent_id=agent_id,
            query=f"agent_id:{agent_id}",
            top_k=100,
            memory_type="decision",
        )

    def get_status(self) -> dict[str, Any]:
        """获取 OpenViking 状态 (供 UI 系统概览)."""
        return {
            "client_loaded": self._client is not None,
            "server_url": self.config.server_url,
            "collection_prefix": self.config.collection_prefix,
            "agent_count": len(self.config.agent_namespaces),
            "init_error": self._init_error,
        }


_openviking_instance: Optional[OpenVikingMemory] = None


def get_openviking_memory(
    config: Optional[OpenVikingConfig] = None,
) -> OpenVikingMemory:
    """获取 OpenViking 记忆适配器单例."""
    global _openviking_instance
    if _openviking_instance is None:
        _openviking_instance = OpenVikingMemory(config)
    return _openviking_instance


def is_openviking_available() -> bool:
    """快速检查 OpenViking 是否可用 (供 AI Hedge Fund 启动自检)."""
    return get_openviking_memory().is_ready()


if __name__ == "__main__":
    memory = get_openviking_memory()
