"""
FinanceAgentOrchestrator — 金融多 Agent 协调器 (Shadow Mode)
==============================================================

设计借鉴:
  - awesome-llm-apps/ai_hedge_fund 的多 Agent 投票架构
  - 5 个专家 Agent: ValueAgent / MomentumAgent / SentimentAgent / RiskAgent / MacroAgent
  - RiskAgent 拥有 veto 权 (一票否决)
  - 加权投票: 基于各 Agent 置信度的动态权重

重要约束 (Shadow Mode):
  - 仅 Shadow Mode 运行, 不进入 SignalFusionEngine 主信号路径
  - 输出与 signal_fusion.fuse() 结果对比, 记录差异到审计日志
  - 30 天 OOS 验证后, 评估是否升级为正式信号源
  - 不修改任何全局状态, 不直接触发交易

工作流:
  1. orchestrate(symbol, context) → 调用所有 Agent, 加权聚合
  2. shadow_compare(fusion_result, agent_result) → 对比差异
  3. save_audit_log() → 持久化到 data/agent_orchestrator_audit/

集成日期: 2026-07-26
集成批次: GitHub 周榜热门项目深度集成 (第二批)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj
from utils.logger import get_logger

logger = get_logger("finance_agent_orchestrator")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class AgentConsensus:
    """多 Agent 协调器的最终共识决策

    Attributes:
        symbol: 标的代码
        action: 最终动作 (buy / sell / hold / veto)
        strength: 加权强度 [-1, 1]
        confidence: 加权置信度 [0, 1]
        veto: 是否被 veto
        veto_reason: 否决理由
        agent_decisions: 所有 Agent 的决策列表
        weighted_vote_detail: 加权投票明细
        timestamp: ISO 时间戳
    """

    symbol: str
    action: str = "hold"
    strength: float = 0.0
    confidence: float = 0.0
    veto: bool = False
    veto_reason: str = ""
    agent_decisions: list[dict[str, Any]] = field(default_factory=list)
    weighted_vote_detail: dict[str, float] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: now_bj().isoformat())

    def __post_init__(self) -> None:
        """防御性 NaN 检查"""
        if not math.isfinite(self.strength):
            self.strength = 0.0
        if not math.isfinite(self.confidence):
            self.confidence = 0.0
        self.strength = max(-1.0, min(1.0, self.strength))
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "veto": self.veto,
            "veto_reason": self.veto_reason,
            "agent_decisions": self.agent_decisions,
            "weighted_vote_detail": self.weighted_vote_detail,
            "timestamp": self.timestamp,
        }


@dataclass
class ShadowDiff:
    """Shadow Mode 对比结果

    Attributes:
        symbol: 标的
        fusion_strength: SignalFusion 的 strength
        agent_strength: Agent 共识的 strength
        diff: 强度差 (agent - fusion)
        direction_match: 方向是否一致 (同号)
        action_match: 动作是否一致
        timestamp: ISO 时间戳
    """

    symbol: str
    fusion_strength: float = 0.0
    agent_strength: float = 0.0
    diff: float = 0.0
    direction_match: bool = True
    action_match: bool = True
    timestamp: str = field(default_factory=lambda: now_bj().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "fusion_strength": round(self.fusion_strength, 4),
            "agent_strength": round(self.agent_strength, 4),
            "diff": round(self.diff, 4),
            "direction_match": self.direction_match,
            "action_match": self.action_match,
            "timestamp": self.timestamp,
        }


# ============================================================
# 主协调器
# ============================================================


class FinanceAgentOrchestrator:
    """金融多 Agent 协调器 (Shadow Mode)

    借鉴 awesome-llm-apps/ai_hedge_fund 多 Agent 投票模式.

    重要: 仅 Shadow Mode 运行, 不参与实盘决策.
    - 输出与 signal_fusion.fuse() 结果对比, 记录差异到审计日志
    - 30 天 OOS 验证后, 评估是否升级为正式信号源
    """

    # Agent 默认权重 (基于历史置信度, 可调)
    DEFAULT_WEIGHTS: dict[str, float] = {
        "value": 0.22,  # 估值: 长期逻辑
        "momentum": 0.22,  # 动量: 中期信号
        "sentiment": 0.13,  # 情绪: 短期事件
        "risk": 0.22,  # 风险: veto + 风险评分
        "macro": 0.10,  # 宏观: 环境因素
        "weather": 0.11,  # v8.6.13: 气象因子 (7因子体系)
    }

    def __init__(
        self,
        agents: list[Any] | None = None,
        weights: dict[str, float] | None = None,
        audit_log_dir: Path | None = None,
    ):
        """
        Args:
            agents: 已实例化的 Agent 列表 (None 则使用默认 5 个)
            weights: Agent 权重 (None 则使用 DEFAULT_WEIGHTS)
            audit_log_dir: 审计日志目录 (None 则 data/agent_orchestrator_audit/)
        """
        # 初始化 Agent
        if agents is None:
            agents = self._init_default_agents()
        self.agents = agents
        self.agent_map: dict[str, Any] = {a.name: a for a in self.agents}

        # 权重
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        # 归一化
        total_w = sum(self.weights.values())
        if total_w > 0:
            self.weights = {k: v / total_w for k, v in self.weights.items()}

        # 审计日志目录
        self.audit_log_dir = audit_log_dir or Path("data/agent_orchestrator_audit")
        try:
            self.audit_log_dir.mkdir(parents=True, exist_ok=True)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("审计日志目录创建失败: %s", e)

        logger.info(
            "FinanceAgentOrchestrator 初始化完成: agents=%s, weights=%s",
            list(self.agent_map.keys()),
            {k: round(v, 3) for k, v in self.weights.items()},
        )

        # v8.4.1: TradingAgents 桥接客户端 (懒加载)
        # 通过 HTTP 调用 28_bridge.py 微服务 (Python 3.10+), 获取多 Agent 决策
        # 降级链: 微服务 → 本地 orchestrate → 中性决策
        self._tradingagents_bridge: Any | None = None

    # ----------------------------------------------------------
    # v8.4.1: TradingAgents 多 Agent 决策引擎 (外部集成)
    # ----------------------------------------------------------

    def orchestrate_tradingagents(
        self,
        symbol: str,
        date: str | None = None,
        analysts: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentConsensus:
        """调用 TradingAgents 多 Agent 决策系统.

        通过 HTTP 桥接微服务调用 langgraph 多 Agent 框架 (Python 3.10+),
        获取 market/news/fundamentals/social 分析师协作决策.

        降级链:
            1. TradingAgents 微服务 (多 Agent 推理)
            2. 本地 orchestrate() (5 Agent 投票)
            3. 中性决策 (HOLD)

        Args:
            symbol: 标的代码 (如 "600519.SH" / "AAPL")
            date: 分析日期 "YYYY-MM-DD", 默认今天
            analysts: 指定分析师列表, 默认全部
            context: 上下文 (保留参数, 供未来扩展)

        Returns:
            AgentConsensus 共识决策 (source 字段标注决策来源)
        """
        # 懒加载桥接客户端
        if self._tradingagents_bridge is None:
            try:
                from utils.tradingagents_bridge import TradingAgentsBridge

                self._tradingagents_bridge = TradingAgentsBridge()
            except ImportError as e:
                logger.warning("TradingAgentsBridge 不可用: %s, 降级到本地", e)
                return self.orchestrate(symbol, context or {})

        # 调用微服务
        result = self._tradingagents_bridge.analyze(symbol, date, analysts)

        # 将桥接结果转换为 AgentConsensus 格式
        action = result.get("action", "HOLD").lower()
        confidence = float(result.get("confidence", 0.0))
        source = result.get("source", "unknown")
        reasoning = result.get("reasoning", "")

        # action 映射: BUY→buy, SELL→sell, HOLD→hold
        if action not in ("buy", "sell", "hold"):
            action = "hold"

        # strength 映射: buy→+confidence, sell→-confidence, hold→0
        if action == "buy":
            strength = confidence
        elif action == "sell":
            strength = -confidence
        else:
            strength = 0.0

        consensus = AgentConsensus(
            symbol=symbol,
            action=action,
            strength=strength,
            confidence=confidence,
            veto=False,
            veto_reason="",
            agent_decisions=[
                {
                    "agent": "tradingagents",
                    "source": source,
                    "action": action,
                    "confidence": confidence,
                    "reasoning": reasoning,
                }
            ],
            weighted_vote_detail={"tradingagents": strength},
        )

        logger.info(
            "TradingAgents 决策: %s → %s (strength=%.3f, conf=%.3f, source=%s)",
            symbol,
            action,
            strength,
            confidence,
            source,
        )
        return consensus

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------

    def orchestrate(self, symbol: str, context: dict[str, Any]) -> AgentConsensus:
        """协调所有 Agent 分析单个标的

        Args:
            symbol: 标的代码
            context: 共享上下文 (kline / fundamentals / news_items / macro_data / position_weight / beta)

        Returns:
            AgentConsensus 共识决策
        """
        all_decisions: list[dict[str, Any]] = []
        veto_triggered = False
        veto_reason = ""

        # 并行调用 (顺序实现, 因为 Agent 间无依赖)
        for agent in self.agents:
            try:
                if not agent.is_available(context):
                    logger.debug(
                        "Agent %s 对 %s 不可用, 跳过",
                        agent.name,
                        symbol,
                    )
                    continue
                decision = agent.analyze(symbol, context)
                all_decisions.append(decision.to_dict())

                # 检查 veto
                if decision.action == "veto":
                    veto_triggered = True
                    veto_reason = decision.veto_reason or f"{agent.name} 触发 veto"
                    logger.warning(
                        "[Shadow] %s/%s 触发 VETO: %s",
                        agent.name,
                        symbol,
                        veto_reason,
                    )
                    # 不 break, 继续收集其他 Agent 决策 (用于审计)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # P2 模块 fail-safe, 待后续精确化
                logger.warning(
                    "Agent %s 分析 %s 异常: %s",
                    agent.name,
                    symbol,
                    e,
                )
                # 异常不影响其他 Agent
                all_decisions.append(
                    {
                        "agent_name": agent.name,
                        "symbol": symbol,
                        "action": "hold",
                        "strength": 0.0,
                        "confidence": 0.0,
                        "reasoning": f"Agent 异常: {e}",
                        "error": True,
                    }
                )

        # veto 优先
        if veto_triggered:
            return AgentConsensus(
                symbol=symbol,
                action="veto",
                strength=-1.0,
                confidence=0.95,
                veto=True,
                veto_reason=veto_reason,
                agent_decisions=all_decisions,
                weighted_vote_detail={"veto": 1.0},
            )

        # 加权投票
        weighted_strength, weighted_confidence, vote_detail = self._weighted_vote(
            all_decisions
        )

        # 决策动作
        if weighted_strength > 0.3:
            action = "buy"
        elif weighted_strength < -0.3:
            action = "sell"
        else:
            action = "hold"

        return AgentConsensus(
            symbol=symbol,
            action=action,
            strength=weighted_strength,
            confidence=weighted_confidence,
            veto=False,
            agent_decisions=all_decisions,
            weighted_vote_detail=vote_detail,
        )

    # ----------------------------------------------------------
    # Shadow Mode 对比
    # ----------------------------------------------------------

    def shadow_compare(
        self,
        fusion_result: Any,
        agent_result: AgentConsensus,
    ) -> ShadowDiff:
        """对比 SignalFusion 结果与 Agent 共识 (Shadow Mode)

        Args:
            fusion_result: SignalFusionEngine.fuse() 返回的 FusionSignal (单标的)
                          或一个 dict (含 strength 字段)
            agent_result: AgentConsensus 共识决策

        Returns:
            ShadowDiff 对比结果
        """
        # 提取 fusion strength
        if hasattr(fusion_result, "strength"):
            fusion_strength = float(fusion_result.strength)
        elif isinstance(fusion_result, dict):
            fusion_strength = float(fusion_result.get("strength", 0.0))
        else:
            fusion_strength = 0.0

        if not math.isfinite(fusion_strength):
            fusion_strength = 0.0

        agent_strength = agent_result.strength
        diff = agent_strength - fusion_strength

        # 方向一致性
        direction_match = (
            (fusion_strength > 0 and agent_strength > 0)
            or (fusion_strength < 0 and agent_strength < 0)
            or (abs(fusion_strength) < 0.05 and abs(agent_strength) < 0.05)
        )

        # 动作一致性
        fusion_action = self._strength_to_action(fusion_strength)
        agent_action = agent_result.action
        action_match = fusion_action == agent_action

        return ShadowDiff(
            symbol=agent_result.symbol,
            fusion_strength=fusion_strength,
            agent_strength=agent_strength,
            diff=diff,
            direction_match=direction_match,
            action_match=action_match,
        )

    # ----------------------------------------------------------
    # 审计日志持久化
    # ----------------------------------------------------------

    def save_audit_log(
        self,
        consensus: AgentConsensus,
        diff: ShadowDiff | None = None,
        trade_date: str | None = None,
    ) -> Path | None:
        """持久化审计日志 (jsonl 格式, 每行一个决策)

        Args:
            consensus: Agent 共识
            diff: (可选) Shadow 对比结果
            trade_date: 交易日 (用于文件名)

        Returns:
            写入的文件路径, 失败返回 None
        """
        try:
            trade_date = trade_date or now_bj().strftime("%Y%m%d")
            log_file = self.audit_log_dir / f"shadow_diffs_{trade_date}.jsonl"
            entry = {
                "trade_date": trade_date,
                "consensus": consensus.to_dict(),
                "diff": diff.to_dict() if diff else None,
            }
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return log_file
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("审计日志写入失败: %s", e)
            return None

    def load_audit_log(self, trade_date: str) -> list[dict[str, Any]]:
        """加载指定日期的审计日志

        Args:
            trade_date: 交易日 (YYYYMMDD)

        Returns:
            日志条目列表
        """
        log_file = self.audit_log_dir / f"shadow_diffs_{trade_date}.jsonl"
        if not log_file.exists():
            return []
        entries = []
        try:
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("审计日志读取失败: %s", e)
        return entries

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _weighted_vote(self, decisions: list[dict[str, Any]]) -> tuple:
        """加权投票

        Returns:
            (weighted_strength, weighted_confidence, vote_detail)
        """
        if not decisions:
            return 0.0, 0.0, {}

        # 过滤异常决策
        valid = [d for d in decisions if not d.get("error")]
        if not valid:
            return 0.0, 0.0, {}

        # 计算每个 Agent 的加权贡献
        weighted_strength = 0.0
        weighted_confidence = 0.0
        total_weight_used = 0.0
        vote_detail: dict[str, float] = {}

        for d in valid:
            agent_name = d.get("agent_name", "")
            strength = float(d.get("strength", 0.0))
            confidence = float(d.get("confidence", 0.0))
            # 加权 = Agent 权重 × Agent 置信度 (置信度低的 Agent 贡献小)
            w = self.weights.get(agent_name, 0.0) * confidence
            if w > 0:
                weighted_strength += w * strength
                weighted_confidence += w
                total_weight_used += w
                vote_detail[agent_name] = round(w, 4)

        if total_weight_used > 0:
            weighted_strength = weighted_strength / total_weight_used
            weighted_confidence = weighted_confidence / total_weight_used
        else:
            weighted_strength = 0.0
            weighted_confidence = 0.0

        # NaN 防御
        if not math.isfinite(weighted_strength):
            weighted_strength = 0.0
        if not math.isfinite(weighted_confidence):
            weighted_confidence = 0.0

        return weighted_strength, weighted_confidence, vote_detail

    @staticmethod
    def _strength_to_action(strength: float) -> str:
        """强度转动作 (与 SignalFusion 一致)"""
        if strength > 0.3:
            return "buy"
        if strength < -0.3:
            return "sell"
        return "hold"

    @staticmethod
    def _init_default_agents() -> list[Any]:
        """初始化默认 6 个 Agent (v8.6.13 新增 WeatherAgent)"""
        try:
            from utils.finance_agents import (
                MacroAgent,
                MomentumAgent,
                RiskAgent,
                SentimentAgent,
                ValueAgent,
            )

            agents: list[Any] = [
                ValueAgent(),
                MomentumAgent(),
                SentimentAgent(),
                RiskAgent(),
                MacroAgent(),
            ]

            # v8.6.13: 气象因子 Agent (懒加载, 不影响系统启动)
            try:
                from utils.finance_agents.weather_agent import WeatherAgent

                agents.append(WeatherAgent())
                logger.info("WeatherAgent 初始化成功")
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as weather_err:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("WeatherAgent 初始化失败 (降级跳过): %s", weather_err)

            return agents
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error("默认 Agent 初始化失败: %s", e)
            return []


# ============================================================
# 便捷入口
# ============================================================

_default_orchestrator: FinanceAgentOrchestrator | None = None


def get_default_orchestrator() -> FinanceAgentOrchestrator:
    """获取默认单例 (惰性初始化)"""
    global _default_orchestrator
    if _default_orchestrator is None:
        _default_orchestrator = FinanceAgentOrchestrator()
    return _default_orchestrator
