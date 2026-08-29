"""
ai_decision.models — 多 AI 辩论共识决策系统的核心数据结构
=========================================================

定义决策流水线中所有跨模块传递的数据契约:

  - DebateTrigger: 辩论触发判定 (多空相反且双方置信度>0.6 才完整辩论)
  - ModelView:     单个模型/角色的结构化多空观点 (Bull/Bear/Judge 等)
  - DebateRecord:  一次完整的 Bull/Bear/Judge 辩论记录
  - DebateDecision:辩论裁决后的方向/强度/置信度
  - TradingDecision:最终可审计的交易决策 (含审计轨迹)
  - DecisionContext:实时 RAG 上下文与五 Agent 信号快照

设计原则 (沿用项目既有约定):
  - Python 3.8.9 兼容 (from __future__ import annotations)
  - 所有数值经 math.isfinite 防御, 边界裁剪 [-1,1]/[0,1]
  - 全部可序列化为 dict 写入审计日志
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ============================================================
# 辩论触发判定
# ============================================================


@dataclass
class DebateTrigger:
    """辩论触发判定

    仅当存在方向相反的多空信号, 且双方置信度均高于阈值时才进入完整辩论.
    常规场景 (~80%) 多空方向一致或置信度不足, 直接走快速聚合, 避免昂贵调用.
    """

    bull_strength: float = 0.0  # 看多强度 [-1, 1]
    bear_strength: float = 0.0  # 看空强度 [-1, 1]
    bull_conf: float = 0.0  # 看多置信度 [0, 1]
    bear_conf: float = 0.0  # 看空置信度 [0, 1]
    debate_threshold: float = 0.6  # 置信度触发阈值

    def should_debate(self) -> bool:
        """方向相反 (一正一负) 且双方置信度均 > 阈值才辩论"""
        opposite = (self.bull_strength > 0 and self.bear_strength < 0) or (
            self.bull_strength < 0 and self.bear_strength > 0
        )
        if not opposite:
            return False
        return (
            self.bull_conf > self.debate_threshold
            and self.bear_conf > self.debate_threshold
        )

    def direction(self) -> str:
        """整体方向 (用于快速聚合路径): bull/bear/neutral"""
        net = self.bull_strength + self.bear_strength
        if net > 0.05:
            return "bull"
        if net < -0.05:
            return "bear"
        return "neutral"


# ============================================================
# 单模型结构化观点
# ============================================================


@dataclass
class ModelView:
    """单个模型/角色的结构化多空观点

    每个角色 (bull/bear/judge 或五 Agent 映射) 输出一份标准化观点,
    由聚合器统一融合.
    """

    role: str = ""  # 角色名 (bull/bear/judge/value/...)
    provider: str = ""  # 实际使用的 Provider (deepseek/glm/mock/...)
    action: str = "hold"  # buy / sell / hold
    strength: float = 0.0  # [-1, 1] 正向看涨, 负向看跌
    confidence: float = 0.0  # [0, 1]
    reasoning: str = ""  # 人类可读理由 (审计)
    key_points: list[str] = field(default_factory=list)  # 关键论据 (用于语义去重)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        if not math.isfinite(self.strength):
            self.strength = 0.0
        if not math.isfinite(self.confidence):
            self.confidence = 0.0
        self.strength = max(-1.0, min(1.0, self.strength))
        self.confidence = max(0.0, min(1.0, self.confidence))
        if self.action not in ("buy", "sell", "hold"):
            self.action = "hold"

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "action": self.action,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "reasoning": self.reasoning,
            "key_points": self.key_points,
            "timestamp": self.timestamp,
        }


# ============================================================
# 辩论记录与裁决
# ============================================================


@dataclass
class DebateRecord:
    """一次完整的 Bull/Bear/Judge 辩论记录

    包含首轮/次轮的多空论证, 以及 Judge 的最终裁决.
    """

    symbol: str = ""
    bull_rounds: list[str] = field(default_factory=list)  # 看多方各轮论证
    bear_rounds: list[str] = field(default_factory=list)  # 看空方各轮论证
    judge_verdict: str = ""  # Judge 裁决文本
    rounds: int = 0  # 实际辩论轮数 (0 表示跳过)
    triggered: bool = False  # 是否触发完整辩论
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bull_rounds": self.bull_rounds,
            "bear_rounds": self.bear_rounds,
            "judge_verdict": self.judge_verdict,
            "rounds": self.rounds,
            "triggered": self.triggered,
            "timestamp": self.timestamp,
        }


@dataclass
class DebateDecision:
    """辩论裁决后的方向/强度/置信度"""

    action: str = "hold"
    strength: float = 0.0
    confidence: float = 0.0
    verdict_type: str = (
        "FAST"  # FAST(快速聚合)/AUTO(自动放行)/HOLD(建议持有)/REVIEW(人工)
    )
    summary: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.strength):
            self.strength = 0.0
        if not math.isfinite(self.confidence):
            self.confidence = 0.0
        self.strength = max(-1.0, min(1.0, self.strength))
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "verdict_type": self.verdict_type,
            "summary": self.summary,
        }


# ============================================================
# 实时上下文快照
# ============================================================


@dataclass
class DecisionContext:
    """实时 RAG 上下文 + 五 Agent 信号快照

    由 rag_context.build_context() 与五 Agent 协调器填充, 供辩论/聚合使用.
    """

    symbol: str = ""
    market_data: dict[str, Any] = field(
        default_factory=dict
    )  # 行情 (close/change_pct/...)
    fundamentals: dict[str, Any] = field(default_factory=dict)  # 基本面 (pe/pb/roe/...)
    news_items: list[str] = field(default_factory=list)  # 新闻事件
    macro_data: dict[str, Any] = field(default_factory=dict)  # 宏观指标
    agent_decisions: list[dict[str, Any]] = field(default_factory=list)  # 五 Agent 输出
    agent_consensus: dict[str, Any] = field(default_factory=dict)  # 加权共识
    as_of: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market_data": self.market_data,
            "fundamentals": self.fundamentals,
            "news_items": self.news_items,
            "macro_data": self.macro_data,
            "agent_decisions": self.agent_decisions,
            "agent_consensus": self.agent_consensus,
            "as_of": self.as_of,
        }


# ============================================================
# 最终交易决策 (可审计)
# ============================================================


@dataclass
class TradingDecision:
    """最终可审计交易决策

    全流程轨迹 (信号 → 辩论/聚合 → 风控门 → 模式) 均记录于此, 供审计.
    """

    symbol: str = ""
    action: str = "hold"  # buy / sell / hold / veto / review
    strength: float = 0.0  # [-1, 1]
    confidence: float = 0.0  # [0, 1]
    mode: str = "shadow"  # shadow / paper / auto
    executed: bool = False  # 是否实际触发下单 (Shadow 永远 False)
    veto: bool = False  # 硬风控/风险 Agent 否决
    veto_reason: str = ""
    verdict_type: str = "FAST"
    debate: dict[str, Any] | None = None
    model_views: list[dict[str, Any]] = field(default_factory=list)
    agent_consensus: dict[str, Any] = field(default_factory=dict)
    risk_checks: dict[str, Any] = field(default_factory=dict)
    escalation: bool = False  # 是否升级人工确认
    escalation_reason: str = ""
    summary: str = ""
    # --- 执行桥接相关字段 ---
    execution_result: dict[str, Any] | None = None  # execute_bridge 返回结果
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        if not math.isfinite(self.strength):
            self.strength = 0.0
        if not math.isfinite(self.confidence):
            self.confidence = 0.0
        self.strength = max(-1.0, min(1.0, self.strength))
        self.confidence = max(0.0, min(1.0, self.confidence))
        if self.action not in ("buy", "sell", "hold", "veto", "review"):
            self.action = "hold"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "symbol": self.symbol,
            "action": self.action,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "mode": self.mode,
            "executed": self.executed,
            "veto": self.veto,
            "veto_reason": self.veto_reason,
            "verdict_type": self.verdict_type,
            "debate": self.debate,
            "model_views": self.model_views,
            "agent_consensus": self.agent_consensus,
            "risk_checks": self.risk_checks,
            "escalation": self.escalation,
            "escalation_reason": self.escalation_reason,
            "summary": self.summary,
            "timestamp": self.timestamp,
        }
        # --- 执行桥接结果 ---
        if self.execution_result is not None:
            d["execution_result"] = self.execution_result
        return d
