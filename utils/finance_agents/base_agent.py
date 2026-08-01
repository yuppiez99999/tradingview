# -*- coding: utf-8 -*-
"""
BaseAgent — 金融多 Agent 基类 (Shadow Mode)
============================================

设计借鉴:
  - awesome-llm-apps/ai_hedge_fund 的多 Agent 投票架构
  - 每个专家 Agent 独立分析, 输出标准化的 AgentDecision
  - RiskAgent 拥有 veto 权 (极端情况下可一票否决)

约束:
  - Python 3.8.9 兼容 (from __future__ import annotations)
  - 所有 Agent 不直接修改交易决策, 仅输出决策建议
  - 协调器 (FinanceAgentOrchestrator) 负责聚合, Agent 本身不互相通信

集成日期: 2026-07-26
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from utils.logger import get_logger

logger = get_logger("finance_agents")


# ============================================================
# 数据结构 (AgentDecision)
# ============================================================


@dataclass
class AgentDecision:
    """单个 Agent 的标准化决策输出

    所有 Agent 必须返回此结构, 由 FinanceAgentOrchestrator 聚合.

    Attributes:
        agent_name: Agent 名称 (value/momentum/sentiment/risk/macro)
        symbol: 标的代码 (如 "600276.SH")
        action: 决策动作 (buy / sell / hold / veto)
        strength: 信号强度 [-1, 1], 正值看涨, 负值看跌
        confidence: 置信度 [0, 1]
        reasoning: 决策理由 (人类可读, 用于审计)
        key_metrics: 关键指标快照 (如 {"pe": 15.2, "pb": 2.1})
        veto_reason: 若 action=veto, 必须填否决理由
        timestamp: ISO 格式时间戳
    """

    agent_name: str
    symbol: str
    action: str = "hold"  # buy / sell / hold / veto
    strength: float = 0.0  # [-1, 1]
    confidence: float = 0.0  # [0, 1]
    reasoning: str = ""
    key_metrics: Dict[str, Any] = field(default_factory=dict)
    veto_reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        """构造后防御性 NaN 检查 + 边界裁剪"""
        # NaN / Inf 归零 (与 signal_fusion.py 一致的防御策略)
        if not math.isfinite(self.strength):
            logger.warning(
                "[AgentDecision] %s/%s strength=%s 非有限值, 归零",
                self.agent_name,
                self.symbol,
                self.strength,
            )
            self.strength = 0.0
        if not math.isfinite(self.confidence):
            logger.warning(
                "[AgentDecision] %s/%s confidence=%s 非有限值, 归零",
                self.agent_name,
                self.symbol,
                self.confidence,
            )
            self.confidence = 0.0
        # 边界裁剪
        self.strength = max(-1.0, min(1.0, self.strength))
        self.confidence = max(0.0, min(1.0, self.confidence))
        # action 规范化
        if self.action not in ("buy", "sell", "hold", "veto"):
            logger.warning(
                "[AgentDecision] %s/%s action=%s 不合法, 降级为 hold",
                self.agent_name,
                self.symbol,
                self.action,
            )
            self.action = "hold"
        # veto 必须有理由
        if self.action == "veto" and not self.veto_reason:
            self.veto_reason = f"{self.agent_name} 触发否决 (未提供具体理由)"

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict (用于审计日志)"""
        return {
            "agent_name": self.agent_name,
            "symbol": self.symbol,
            "action": self.action,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "reasoning": self.reasoning,
            "key_metrics": self.key_metrics,
            "veto_reason": self.veto_reason,
            "timestamp": self.timestamp,
        }


# ============================================================
# BaseAgent 抽象基类
# ============================================================


class BaseAgent(ABC):
    """所有金融专家 Agent 的抽象基类

    子类必须实现 analyze() 方法, 返回 AgentDecision.
    子类可选实现 is_available() (默认 True) 和 name() (默认类名).

    设计原则:
      - 单一职责: 每个 Agent 只负责一个维度 (估值/动量/情绪/风险/宏观)
      - 无副作用: analyze() 不修改任何全局状态, 不写入磁盘
      - 优雅降级: 数据缺失时返回 hold + confidence=0, 不抛异常
      - 可审计: 所有决策可序列化为 dict, 写入审计日志
    """

    def __init__(self, name: Optional[str] = None):
        self._name = name or self.__class__.__name__

    @property
    def name(self) -> str:
        """Agent 名称 (用于日志和审计)"""
        return self._name

    def is_available(self, context: Dict[str, Any]) -> bool:
        """检查 Agent 是否可用 (基于上下文)

        子类可重写, 默认始终可用.
        """
        return True

    @abstractmethod
    def analyze(self, symbol: str, context: Dict[str, Any]) -> AgentDecision:
        """分析单个标的, 返回决策

        Args:
            symbol: 标的代码 (如 "600276.SH")
            context: 共享上下文 (行情/持仓/新闻/宏观数据等)

        Returns:
            AgentDecision 标准化决策
        """
        raise NotImplementedError

    # ----------------------------------------------------------
    # 工具方法 (子类可用)
    # ----------------------------------------------------------

    @staticmethod
    def _safe_get(context: Dict[str, Any], *keys: str, default: Any = None) -> Any:
        """安全嵌套取值 (按嵌套层级取值)

        Example:
            BaseAgent._safe_get(context, "market_data", "close", default=0.0)
            # 等价于 context["market_data"]["close"]

        Note:
            本方法是嵌套取值, 不是 fallback. 若需要 "先 pe 再 pe_ttm" 的 fallback,
            请用 _safe_get_fallback().
        """
        cur: Any = context
        for k in keys:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k, default)
            if cur is None:
                return default
        return cur

    @staticmethod
    def _safe_get_fallback(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
        """安全 fallback 取值 (按顺序尝试多个 key, 返回第一个非 None 的值)

        Example:
            BaseAgent._safe_get_fallback(fund, "pe", "pe_ttm", default=0.0)
            # 等价于 fund["pe"] if "pe" in fund else fund["pe_ttm"] else 0.0
        """
        if not isinstance(d, dict):
            return default
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return default

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """安全转 float, 处理 None/NaN/Inf/字符串"""
        if value is None:
            return default
        try:
            f = float(value)
            if not math.isfinite(f):
                return default
            return f
        except (TypeError, ValueError):
            return default
