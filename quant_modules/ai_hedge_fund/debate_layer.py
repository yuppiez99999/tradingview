"""多空对抗辩论层 (Wave 6 W6.2.1 + W6.2.2, TradingAgents 风格)

借鉴 TauricResearch/TradingAgents 的 7 层架构 + 多空对抗辩论机制,
在现有 20 分析师层与 risk_management_agent 之间插入一层辩论节点。

7 层架构映射 (TradingAgents → 28 系统):
    1. Analyst 层       → 复用现有 20 分析师 (warren_buffett / ben_graham / ...)
    2. Bull Researcher  → 新增: 聚合看多证据 + 反驳看空证据
    3. Bear Researcher  → 新增: 聚合看空证据 + 反驳看多证据
    4. Trader 层        → 复用 portfolio_management_agent
    5. Risk Manager 层  → 复用 risk_management_agent
    6. Portfolio 层     → 复用 hedge_analyst (对冲覆盖)
    7. (无)              → 本系统无第 7 层 (TradingAgents 的 "Investment Debate" 已拆分为 2+3)

辩论流程:
    Round 1: Bull/Bear 各自基于分析师信号生成初版论点
    Round 2: Bull/Bear 各自看到对方 Round 1 论点后做反驳, 输出最终立场
    辩论过程记录到 reports/ai_hedge_fund/debates/{date}.json 供审计

关键设计:
    1. 辩论层不改变现有 AgentState 结构, 只在 data["analyst_signals"] 中
       新增 "bull_researcher" / "bear_researcher" 两个虚拟 agent 信号
    2. Bull/Bear 的 confidence 聚合后作为 portfolio_manager 的额外输入
    3. LLM 不可用时降级为规则模式 (基于 bullish/bearish 数量统计)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("ai_hedge_fund.debate")

# 审计留痕目录
_DEBATE_LOG_DIR = os.path.join(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
    "reports",
    "ai_hedge_fund",
    "debates",
)


# ============================================================
# Pydantic 输出模型 (与现有 Agent 信号格式一致)
# ============================================================


class DebateStance(BaseModel):
    """单轮辩论立场"""

    stance: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(description="置信度 0-100")
    key_arguments: list[str] = Field(description="核心论点列表 (3~5 条)")
    rebuttals: list[str] = Field(default_factory=list, description="对对方论点的反驳")
    evidence_summary: str = Field(default="", description="证据链摘要")


class DebateResult(BaseModel):
    """完整两轮辩论结果 (单个 ticker)"""

    ticker: str
    # Round 1
    bull_round1: DebateStance
    bear_round1: DebateStance
    # Round 2 (反驳后最终立场)
    bull_final: DebateStance
    bear_final: DebateStance
    # 裁决
    winner: Literal["bull", "bear", "tie"]
    net_confidence: int = Field(
        description="Bull confidence - Bear confidence, 范围 [-100, 100]"
    )
    final_signal: Literal["bullish", "bearish", "neutral"]
    final_confidence: int = Field(description="最终信号置信度 0-100")
    reasoning: str = Field(description="裁决理由")


# ============================================================
# 辩论过程数据类 (用于审计日志)
# ============================================================


@dataclass
class DebateSession:
    """一次完整辩论会话 (多 ticker)"""

    session_id: str
    timestamp: str
    tickers: list[str]
    debate_results: dict[str, DebateResult] = field(default_factory=dict)
    analyst_signals_snapshot: dict[str, Any] = field(default_factory=dict)
    llm_used: bool = False
    model_name: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "tickers": self.tickers,
            "llm_used": self.llm_used,
            "model_name": self.model_name,
            "errors": self.errors,
            "analyst_signals_snapshot": _make_json_safe(self.analyst_signals_snapshot),
            "debate_results": {
                t: r.model_dump() if hasattr(r, "model_dump") else dict(r)
                for t, r in self.debate_results.items()
            },
        }


# ============================================================
# 安全序列化
# ============================================================


def _make_json_safe(obj: Any) -> Any:
    """把含 pydantic / datetime / numpy 对象的嵌套结构转为 JSON 安全字典"""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return {k: _make_json_safe(v) for k, v in vars(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_make_json_safe(v) for v in obj]
    try:
        return str(obj)
    except (TypeError, ValueError, AttributeError):
        return "<unserializable>"


# ============================================================
# 辩论层核心实现
# ============================================================


class DebateLayer:
    """多空对抗辩论层 (TradingAgents 风格, Wave 6 W6.2.1 + W6.2.2)

    用法 (在 LangGraph workflow 中作为节点函数):
        def bull_researcher_node(state):
            return DebateLayer().run_bull_round(state, round_num=1)

    或一次性运行完整辩论:
        session = DebateLayer().run_full_debate(state)
        state["data"]["analyst_signals"]["bull_researcher"] = session.as_signals()
    """

    def __init__(
        self,
        rounds: int = 2,
        use_llm: bool = True,
        model_name: str = "",
        log_dir: str | None = None,
        enable_audit: bool = True,
        use_rate_limiter: bool = False,
    ):
        self.rounds = max(1, int(rounds))
        self.use_llm = bool(use_llm)
        self.model_name = model_name
        self.log_dir = log_dir or _DEBATE_LOG_DIR
        self.enable_audit = bool(enable_audit)
        self.use_rate_limiter = bool(use_rate_limiter)

        # W6.2.4: 可选接入 RateLimitedLLMCaller (令牌桶限流 + TTL 缓存 + 重试 + 统计)
        self._rate_limited_caller = None
        if self.use_rate_limiter:
            try:
                from quant_modules.ai_hedge_fund.llm_rate_limiter import (
                    get_global_llm_caller,
                )

                self._rate_limited_caller = get_global_llm_caller()
            except ImportError:
                logger.warning(
                    "use_rate_limiter=True 但 llm_rate_limiter 模块不可用, 降级为直接调用"
                )

    # ------------------------------------------------------------
    # 公开主入口: 一次性运行完整两轮辩论
    # ------------------------------------------------------------

    def run_full_debate(
        self,
        tickers: list[str],
        analyst_signals: dict[str, dict[str, Any]],
        state: Any = None,
    ) -> DebateSession:
        """对每个 ticker 执行 Bull vs Bear 两轮辩论

        Args:
            tickers: 股票代码列表
            analyst_signals: 现有分析师信号 {agent_id: {ticker: {signal, confidence, reasoning}}}
            state: AgentState (可选, 用于 LLM 调用提取 model config)

        Returns:
            DebateSession — 含每个 ticker 的 DebateResult + 审计信息
        """
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session = DebateSession(
            session_id=session_id,
            timestamp=datetime.now().isoformat(),
            tickers=list(tickers),
            analyst_signals_snapshot=self._safe_snapshot(analyst_signals),
            llm_used=self.use_llm,
            model_name=self.model_name,
        )

        for ticker in tickers:
            try:
                result = self._debate_single_ticker(ticker, analyst_signals, state)
                session.debate_results[ticker] = result
            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                TimeoutError,
                ImportError,
            ) as exc:
                err_msg = f"{ticker}: {exc!r}"
                session.errors.append(err_msg)
                logger.warning("辩论失败 %s: %r", ticker, exc)
                # 降级: 规则模式生成占位结果
                session.debate_results[ticker] = self._rule_based_debate(
                    ticker,
                    analyst_signals,
                )

        # 审计留痕
        if self.enable_audit:
            self._write_audit_log(session)

        return session

    # ------------------------------------------------------------
    # 单 ticker 辩论
    # ------------------------------------------------------------

    def _debate_single_ticker(
        self,
        ticker: str,
        analyst_signals: dict[str, dict[str, Any]],
        state: Any = None,
    ) -> DebateResult:
        """对单个 ticker 执行完整两轮辩论"""
        # 提取该 ticker 的所有分析师信号
        ticker_signals = self._extract_ticker_signals(ticker, analyst_signals)

        # Round 1: Bull / Bear 各自初版论点
        bull_r1 = self._generate_stance(
            ticker,
            ticker_signals,
            side="bull",
            round_num=1,
            opponent_stance=None,
            state=state,
        )
        bear_r1 = self._generate_stance(
            ticker,
            ticker_signals,
            side="bear",
            round_num=1,
            opponent_stance=None,
            state=state,
        )

        # Round 2: 看到对方 Round 1 后做反驳
        bull_final = self._generate_stance(
            ticker,
            ticker_signals,
            side="bull",
            round_num=2,
            opponent_stance=bear_r1,
            state=state,
        )
        bear_final = self._generate_stance(
            ticker,
            ticker_signals,
            side="bear",
            round_num=2,
            opponent_stance=bull_r1,
            state=state,
        )

        # 裁决
        winner, net_conf, final_signal, final_conf, reasoning = self._adjudicate(
            bull_final,
            bear_final,
            ticker_signals,
        )

        return DebateResult(
            ticker=ticker,
            bull_round1=bull_r1,
            bear_round1=bear_r1,
            bull_final=bull_final,
            bear_final=bear_final,
            winner=winner,
            net_confidence=net_conf,
            final_signal=final_signal,
            final_confidence=final_conf,
            reasoning=reasoning,
        )

    # ------------------------------------------------------------
    # 生成单轮立场 (LLM / 规则双模式)
    # ------------------------------------------------------------

    def _generate_stance(
        self,
        ticker: str,
        ticker_signals: dict[str, dict[str, Any]],
        side: Literal["bull", "bear"],
        round_num: int,
        opponent_stance: DebateStance | None,
        state: Any = None,
    ) -> DebateStance:
        """生成单方单轮立场

        优先使用 LLM (通过 call_llm), 失败降级为规则模式。
        """
        if self.use_llm and self._llm_available():
            try:
                return self._llm_generate_stance(
                    ticker,
                    ticker_signals,
                    side,
                    round_num,
                    opponent_stance,
                    state,
                )
            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                TimeoutError,
                ImportError,
            ) as exc:
                logger.warning(
                    "LLM 辩论失败 %s/%s R%d, 降级规则: %r", ticker, side, round_num, exc
                )

        return self._rule_generate_stance(
            ticker, ticker_signals, side, round_num, opponent_stance
        )

    def _llm_available(self) -> bool:
        """检测 LLM 依赖是否可用"""
        try:
            from langchain_core.prompts import ChatPromptTemplate  # noqa: F401

            from quant_modules.ai_hedge_fund.utils.llm import call_llm  # noqa: F401

            return True
        except ImportError:
            return False

    def _llm_generate_stance(
        self,
        ticker: str,
        ticker_signals: dict[str, dict[str, Any]],
        side: Literal["bull", "bear"],
        round_num: int,
        opponent_stance: DebateStance | None,
        state: Any,
    ) -> DebateStance:
        """LLM 驱动生成立场 (TradingAgents 风格 prompt)"""
        from langchain_core.prompts import ChatPromptTemplate

        from quant_modules.ai_hedge_fund.utils.llm import call_llm

        # 构造分析师信号摘要
        signal_lines = []
        for agent_id, sig in ticker_signals.items():
            s = sig.get("signal", "neutral")
            c = sig.get("confidence", 0)
            r = (sig.get("reasoning") or "")[:120]
            signal_lines.append(f"  - {agent_id}: {s} (conf={c}) {r}")
        signals_text = "\n".join(signal_lines) if signal_lines else "  (无分析师信号)"

        # 对方论点 (Round 2)
        opponent_text = ""
        if opponent_stance:
            opp_args = "; ".join(opponent_stance.key_arguments[:3])
            opponent_text = (
                f"\n对方({('bear' if side=='bull' else 'bull')})论点: {opp_args}"
            )

        side_label = "看多(Bull)" if side == "bull" else "看空(Bear)"

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "你是一位专业的{side_label}研究员, 负责为股票 {ticker} 构建多空辩论中的{side_label}论点。"
                        "基于以下分析师信号, 提炼{side_label}证据链, "
                        "{rebuttal_instr}"
                        "输出结构化 JSON (stance/confidence/key_arguments/rebuttals/evidence_summary)。"
                    ),
                ),
                (
                    "human",
                    (
                        "股票: {ticker}\n"
                        "分析师信号:\n{signals_text}{opponent_text}\n\n"
                        "请生成{side_label}立场 (stance 必须为 '{stance_value}'):"
                    ),
                ),
            ]
        )

        # stance 强制与 side 一致
        stance_filter = "bullish" if side == "bull" else "bearish"

        # Rebuttal 指令 (仅 Round 2)
        if round_num >= 2 and opponent_stance:
            rebuttal_instr = "并针对对方论点做反驳 (rebuttals 字段填入 2~3 条反驳)。"
        else:
            rebuttal_instr = ""

        formatted = prompt.format_messages(
            side_label=side_label,
            ticker=ticker,
            signals_text=signals_text,
            opponent_text=opponent_text,
            stance_value=stance_filter,
            rebuttal_instr=rebuttal_instr,
        )

        agent_name = f"{'bull' if side=='bull' else 'bear'}_researcher"

        def default_fn():
            return self._rule_generate_stance(
                ticker,
                ticker_signals,
                side,
                round_num,
                opponent_stance,
            )

        # W6.2.4: 可选通过 RateLimitedLLMCaller 调用 (令牌桶限流 + TTL 缓存 + 重试 + 统计)
        if self._rate_limited_caller is not None:
            # 构造缓存键 (ticker + side + round + signals 摘要)
            try:
                from quant_modules.ai_hedge_fund.llm_rate_limiter import TTLCache

                cache_key = TTLCache.make_key(
                    ticker, side, round_num, signals_text[:200]
                )
            except ImportError:
                cache_key = None
            try:
                result = self._rate_limited_caller.call(
                    fn=call_llm,
                    args=(formatted, DebateStance),
                    kwargs={
                        "agent_name": agent_name,
                        "state": state,
                        "max_retries": 2,
                        "default_factory": default_fn,
                    },
                    agent_name=agent_name,
                    model_name=self.model_name,
                    cache_key=cache_key,
                    timeout=30.0,
                )
            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                TimeoutError,
            ) as exc:
                logger.warning(
                    "RateLimitedLLMCaller 调用失败 %s/%s R%d, 降级规则: %r",
                    ticker,
                    side,
                    round_num,
                    exc,
                )
                return default_fn()
        else:
            result = call_llm(
                formatted,
                DebateStance,
                agent_name=agent_name,
                state=state,
                max_retries=2,
                default_factory=default_fn,
            )
        # 强制 stance 与 side 一致 (LLM 偶尔不遵守)
        if hasattr(result, "stance") and result.stance != stance_filter:
            object.__setattr__(result, "stance", stance_filter)
        return result

    # ------------------------------------------------------------
    # 规则模式 (LLM 不可用时的降级)
    # ------------------------------------------------------------

    def _rule_generate_stance(
        self,
        ticker: str,
        ticker_signals: dict[str, dict[str, Any]],
        side: Literal["bull", "bear"],
        round_num: int,
        opponent_stance: DebateStance | None,
    ) -> DebateStance:
        """规则模式: 基于 bullish/bearish 数量统计生成立场"""
        bull_count = sum(
            1 for s in ticker_signals.values() if s.get("signal") == "bullish"
        )
        bear_count = sum(
            1 for s in ticker_signals.values() if s.get("signal") == "bearish"
        )
        total = max(1, len(ticker_signals))

        if side == "bull":
            confidence = int(40 + (bull_count / total) * 50)  # 40~90
            target_signals = [
                s for s in ticker_signals.values() if s.get("signal") == "bullish"
            ]
            stance = "bullish"
        else:
            confidence = int(40 + (bear_count / total) * 50)
            target_signals = [
                s for s in ticker_signals.values() if s.get("signal") == "bearish"
            ]
            stance = "bearish"

        # 提取关键论点
        key_args: list[str] = []
        for agent_id, sig in ticker_signals.items():
            if sig.get("signal") == stance:
                r = (sig.get("reasoning") or "")[:100]
                if r:
                    key_args.append(f"[{agent_id}] {r}")
        if not key_args:
            key_args.append(
                f"基于 {bull_count}/{total} 看多 vs {bear_count}/{total} 看空的统计优势"
            )

        # Round 2 反驳
        rebuttals: list[str] = []
        if round_num >= 2 and opponent_stance:
            opp_side = "看空" if side == "bull" else "看多"
            for opp_arg in opponent_stance.key_arguments[:2]:
                rebuttals.append(
                    f"对方{opp_side}论点「{opp_arg[:60]}」忽略了我方 {len(target_signals)} 个支持信号"
                )
            if not rebuttals:
                rebuttals.append(
                    f"对方{opp_side}置信度仅 {opponent_stance.confidence}, 证据不充分"
                )

        return DebateStance(
            stance=stance,
            confidence=min(95, max(10, confidence)),
            key_arguments=key_args[:5],
            rebuttals=rebuttals,
            evidence_summary=f"统计: {bull_count}看多 / {bear_count}看空 / {total - bull_count - bear_count}中性, 共{total}个分析师",  # noqa: E501
        )

    # ------------------------------------------------------------
    # 裁决
    # ------------------------------------------------------------

    def _adjudicate(
        self,
        bull: DebateStance,
        bear: DebateStance,
        ticker_signals: dict[str, dict[str, Any]],
    ) -> tuple[str, int, str, int, str]:
        """裁决: 基于最终置信度差 + 分析师信号一致性"""
        net_conf = bull.confidence - bear.confidence

        # 分析师信号加权 (bloomberg 式: 分析师一致性)
        bull_count = sum(
            1 for s in ticker_signals.values() if s.get("signal") == "bullish"
        )
        bear_count = sum(
            1 for s in ticker_signals.values() if s.get("signal") == "bearish"
        )
        analyst_net = bull_count - bear_count

        # 综合裁决: 辩论置信度差 (权重 0.6) + 分析师一致性 (权重 0.4)
        # analyst_net 归一化到 [-100, 100]
        total = max(1, len(ticker_signals))
        analyst_score = (analyst_net / total) * 100
        composite = 0.6 * net_conf + 0.4 * analyst_score

        if composite > 10:
            winner = "bull"
            final_signal = "bullish"
            final_conf = min(95, bull.confidence)
        elif composite < -10:
            winner = "bear"
            final_signal = "bearish"
            final_conf = min(95, bear.confidence)
        else:
            winner = "tie"
            final_signal = "neutral"
            final_conf = min(50, abs(net_conf) // 2 + 20)

        reasoning = (
            f"辩论裁决: Bull置信度={bull.confidence} vs Bear置信度={bear.confidence}, "
            f"净置信度={net_conf}; 分析师一致性: {bull_count}看多/{bear_count}看空 (net={analyst_net}); "
            f"综合得分={composite:.1f} → 裁决={winner}, 信号={final_signal}"
        )
        return winner, net_conf, final_signal, final_conf, reasoning

    # ------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------

    @staticmethod
    def _extract_ticker_signals(
        ticker: str,
        analyst_signals: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """从 analyst_signals 中提取指定 ticker 的所有分析师信号

        过滤掉 risk_management_agent / portfolio_manager / hedge_analyst 等非分析师信号
        """
        exclude_prefixes = (
            "risk_management",
            "portfolio_manager",
            "hedge_analyst",
            "bull_researcher",
            "bear_researcher",
        )
        out: dict[str, dict[str, Any]] = {}
        for agent_id, sigs in analyst_signals.items():
            if any(agent_id.startswith(p) for p in exclude_prefixes):
                continue
            if ticker in sigs and isinstance(sigs[ticker], dict):
                out[agent_id] = sigs[ticker]
        return out

    @staticmethod
    def _rule_based_debate(
        ticker: str,
        analyst_signals: dict[str, dict[str, Any]],
    ) -> DebateResult:
        """异常降级: 纯规则模式生成占位 DebateResult"""
        ts = DebateLayer._extract_ticker_signals(ticker, analyst_signals)
        bull = DebateLayer()._rule_generate_stance(ticker, ts, "bull", 1, None)
        bear = DebateLayer()._rule_generate_stance(ticker, ts, "bear", 1, None)
        bull2 = DebateLayer()._rule_generate_stance(ticker, ts, "bull", 2, bear)
        bear2 = DebateLayer()._rule_generate_stance(ticker, ts, "bear", 2, bull)
        winner, net, sig, conf, reason = DebateLayer()._adjudicate(bull2, bear2, ts)
        return DebateResult(
            ticker=ticker,
            bull_round1=bull,
            bear_round1=bear,
            bull_final=bull2,
            bear_final=bear2,
            winner=winner,
            net_confidence=net,
            final_signal=sig,
            final_confidence=conf,
            reasoning=reason,
        )

    @staticmethod
    def _safe_snapshot(analyst_signals: dict[str, Any]) -> dict[str, Any]:
        """安全快照 (截断 reasoning, 避免审计日志过大)"""
        out: dict[str, Any] = {}
        for agent_id, sigs in analyst_signals.items():
            if not isinstance(sigs, dict):
                out[agent_id] = _make_json_safe(sigs)
                continue
            out[agent_id] = {}
            for ticker, sig in sigs.items():
                if isinstance(sig, dict):
                    s = dict(sig)
                    if "reasoning" in s and isinstance(s["reasoning"], str):
                        s["reasoning"] = s["reasoning"][:200]
                    out[agent_id][ticker] = s
                else:
                    out[agent_id][ticker] = _make_json_safe(sig)
        return out

    # ------------------------------------------------------------
    # 审计留痕
    # ------------------------------------------------------------

    def _write_audit_log(self, session: DebateSession) -> None:
        """写审计日志到 reports/ai_hedge_fund/debates/{date}_{session_id}.json"""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"{date_str}_{session.session_id}.json"
            filepath = os.path.join(self.log_dir, filename)

            payload = session.to_dict()

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

            logger.info("辩论审计日志已写入: %s", filepath)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("写审计日志失败: %r", exc)

    # ------------------------------------------------------------
    # 辩论结果 → Agent 信号格式转换 (供 portfolio_manager 消费)
    # ------------------------------------------------------------

    @staticmethod
    def session_to_signals(
        session: DebateSession,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """把 DebateSession 转为 analyst_signals 格式

        返回:
            {
                "bull_researcher": {ticker: {signal, confidence, reasoning}},
                "bear_researcher": {ticker: {signal, confidence, reasoning}},
                "debate_verdict":  {ticker: {signal, confidence, reasoning}},  # 裁决结果
            }
        """
        bull_out: dict[str, dict[str, Any]] = {}
        bear_out: dict[str, dict[str, Any]] = {}
        verdict_out: dict[str, dict[str, Any]] = {}

        for ticker, result in session.debate_results.items():
            bull_out[ticker] = {
                "signal": result.bull_final.stance,
                "confidence": result.bull_final.confidence,
                "reasoning": result.bull_final.evidence_summary
                + " | 反驳: "
                + "; ".join(result.bull_final.rebuttals),
            }
            bear_out[ticker] = {
                "signal": result.bear_final.stance,
                "confidence": result.bear_final.confidence,
                "reasoning": result.bear_final.evidence_summary
                + " | 反驳: "
                + "; ".join(result.bear_final.rebuttals),
            }
            verdict_out[ticker] = {
                "signal": result.final_signal,
                "confidence": result.final_confidence,
                "reasoning": result.reasoning,
                "winner": result.winner,
                "net_confidence": result.net_confidence,
            }

        return {
            "bull_researcher": bull_out,
            "bear_researcher": bear_out,
            "debate_verdict": verdict_out,
        }


# ============================================================
# LangGraph 节点函数 (可直接 add_node 使用)
# ============================================================


def debate_node(state: Any) -> Any:
    """LangGraph 辩论节点 — 可直接作为 workflow.add_node("debate_layer", debate_node)

    流程:
        1. 从 state["data"]["analyst_signals"] 提取分析师信号
        2. 对 state["data"]["tickers"] 中每个 ticker 执行两轮辩论
        3. 把 bull_researcher / bear_researcher / debate_verdict 信号注入回 analyst_signals
        4. 审计日志自动写入 reports/ai_hedge_fund/debates/

    插入位置 (在 orchestrator.py create_workflow 中):
        ... 分析师节点 → debate_layer → risk_management_agent → portfolio_manager
    """
    data = state.get("data", {})
    tickers = data.get("tickers", [])
    analyst_signals = data.get("analyst_signals", {})
    metadata = state.get("metadata", {})

    # 从 metadata 提取模型配置
    use_llm = metadata.get("enable_debate_llm", True)
    model_name = metadata.get("model_name", "")
    use_rate_limiter = metadata.get("use_rate_limiter", False)

    debate = DebateLayer(
        rounds=2,
        use_llm=use_llm,
        model_name=model_name,
        enable_audit=True,
        use_rate_limiter=use_rate_limiter,
    )

    session = debate.run_full_debate(tickers, analyst_signals, state=state)

    # 把辩论结果注入 analyst_signals
    debate_signals = DebateLayer.session_to_signals(session)
    analyst_signals.update(debate_signals)
    state["data"]["analyst_signals"] = analyst_signals

    # 在 metadata 中记录辩论摘要 (便于下游 Agent 感知)
    state["metadata"]["debate_summary"] = {
        ticker: {
            "winner": r.winner,
            "final_signal": r.final_signal,
            "final_confidence": r.final_confidence,
            "net_confidence": r.net_confidence,
        }
        for ticker, r in session.debate_results.items()
    }
    state["metadata"]["debate_session_id"] = session.session_id

    # 记录到 messages (与现有 Agent 输出格式一致)
    try:
        from langchain_core.messages import HumanMessage

        summary_text = json.dumps(
            {
                t: s["debate_summary"] if "debate_summary" in s else s
                for t, s in state["metadata"]["debate_summary"].items()
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        state["messages"].append(
            HumanMessage(content=f"[Debate Layer] 辩论完成:\n{summary_text}")
        )
    except (ImportError, TypeError, ValueError, KeyError, AttributeError):
        pass  # messages 不存在时不影响主流程

    return state
