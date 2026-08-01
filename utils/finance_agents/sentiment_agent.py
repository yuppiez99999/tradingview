"""
SentimentAgent — 舆情分析 Agent (复用 AIReportAgent)
=====================================================

设计原则:
  - 不重新实现情感分析, 而是复用 utils.ai_report_agent.AIReportAgent
  - 通过 to_agent_decision() 适配方法, 将 SentimentResult 转为 AgentDecision
  - LLM 不可用时自动降级到规则引擎 (AIReportAgent 已实现)

决策逻辑:
  1. 调用 AIReportAgent.analyze_news_sentiment(news_items)
  2. 聚合多新闻的 sentiment score → 平均强度
  3. 强负面 (score < -0.6) → 看空
  4. 强正面 (score > +0.6) → 看多
  5. 重大负面关键词 (CRITICAL_NEGATIVE_WORDS) → action=veto

集成日期: 2026-07-26
"""

from __future__ import annotations

import math
from typing import Any

from utils.finance_agents.base_agent import AgentDecision, BaseAgent
from utils.logger import get_logger

logger = get_logger("finance_agents.sentiment")


class SentimentAgent(BaseAgent):
    """舆情分析 Agent (复用 AIReportAgent)"""

    def __init__(
        self,
        name: str = "sentiment",
        report_agent: Any | None = None,
        use_llm: bool = False,
    ):
        """
        Args:
            name: Agent 名称
            report_agent: 已初始化的 AIReportAgent 实例 (None 则惰性创建)
            use_llm: 是否启用 LLM 情感分析 (默认 False, Shadow Mode 用规则引擎兜底,
                     避免 LLM 调用拖慢 Phase 7. OOS 验证后可改 True)
        """
        super().__init__(name=name)
        self._report_agent = report_agent
        self._init_tried = False
        # v8.6.9 Shadow Mode 设计: 默认 use_llm=False, 避免阻塞 daily_workflow
        # 理由: 1) Shadow Mode 不影响实盘, 2) LLM 调用 22s+ 拖慢 Phase 7,
        #       3) 规则引擎兜底已能识别重大负面 (CRITICAL_NEGATIVE_WORDS)
        self._use_llm = use_llm

    def _get_report_agent(self):
        """惰性初始化 AIReportAgent (避免循环导入)"""
        if self._report_agent is not None:
            return self._report_agent
        if self._init_tried:
            return None
        self._init_tried = True
        try:
            from utils.ai_report_agent import AIReportAgent

            self._report_agent = AIReportAgent()
            logger.info("SentimentAgent: 已复用 AIReportAgent")
            return self._report_agent
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("SentimentAgent: AIReportAgent 初始化失败: %s", e)
            return None

    def is_available(self, context: dict[str, Any]) -> bool:
        """需要 news_items 数据"""
        news = self._safe_get(context, "news_items")
        return isinstance(news, list) and len(news) > 0

    def analyze(self, symbol: str, context: dict[str, Any]) -> AgentDecision:
        """舆情分析主入口"""
        news_items: list[dict] = self._safe_get(context, "news_items", default=[]) or []
        # 过滤出与该 symbol 相关的新闻
        related_news = [
            n
            for n in news_items
            if isinstance(n, dict)
            and (n.get("symbol") == symbol or symbol.split(".")[0] in (n.get("title", "") + n.get("content", "")))
        ]
        # 若无相关新闻, 用全部新闻做市场情绪
        analyzed_news = related_news if related_news else news_items

        if not analyzed_news:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                confidence=0.0,
                reasoning="无新闻数据, 无法分析舆情",
            )

        agent = self._get_report_agent()
        if agent is None:
            # 降级: 简单关键词匹配
            return self._fallback_keyword_sentiment(symbol, analyzed_news)

        # v8.6.9 Shadow Mode: 默认 use_llm=False, 直接走规则引擎
        # 避免 LLM 调用 22s+ 拖慢 Phase 7
        if not self._use_llm:
            return self._fallback_keyword_sentiment(symbol, analyzed_news)

        try:
            sentiments = agent.analyze_news_sentiment(analyzed_news, use_llm=True)
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("SentimentAgent: AIReportAgent.analyze_news_sentiment 异常: %s", e)
            return self._fallback_keyword_sentiment(symbol, analyzed_news)

        if not sentiments:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                confidence=0.0,
                reasoning="情感分析返回空结果",
            )

        # 聚合多新闻的 score
        scores = [s.score for s in sentiments if math.isfinite(s.score)]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        # 检查重大负面 (触发 veto)
        critical_hits = []
        for item in analyzed_news:
            text = item.get("title", "") + " " + item.get("content", "")
            for word in agent.CRITICAL_NEGATIVE_WORDS:
                if word in text:
                    critical_hits.append(word)
                    break

        metrics: dict[str, Any] = {
            "news_count": len(analyzed_news),
            "avg_score": round(avg_score, 4),
            "critical_hits": critical_hits,
        }

        if critical_hits:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="veto",
                strength=-1.0,
                confidence=0.95,
                reasoning=f"重大负面关键词: {critical_hits}",
                key_metrics=metrics,
                veto_reason=f"舆情检测到重大负面: {critical_hits}",
            )

        # 普通情绪强度
        if avg_score > 0.6:
            action = "buy"
            strength = min(1.0, avg_score)
        elif avg_score < -0.6:
            action = "sell"
            strength = max(-1.0, avg_score)
        else:
            action = "hold"
            strength = avg_score * 0.5  # 中性区间强度减半

        confidence = min(0.9, 0.3 + 0.15 * len(analyzed_news))

        return AgentDecision(
            agent_name=self.name,
            symbol=symbol,
            action=action,
            strength=strength,
            confidence=confidence,
            reasoning=f"舆情均分 {avg_score:.2f} (基于 {len(analyzed_news)} 条新闻)",
            key_metrics=metrics,
        )

    # ----------------------------------------------------------
    # 降级: 关键词匹配
    # ----------------------------------------------------------

    def _fallback_keyword_sentiment(self, symbol: str, news_items: list[dict]) -> AgentDecision:
        """规则引擎兜底 (LLM 不可用时)"""
        # 复用 AIReportAgent 的关键词词典
        try:
            from utils.ai_report_agent import AIReportAgent

            pos_words = AIReportAgent.POSITIVE_WORDS
            neg_words = AIReportAgent.NEGATIVE_WORDS
            crit_words = AIReportAgent.CRITICAL_NEGATIVE_WORDS
        except Exception:  # P2 模块 fail-safe, 待后续精确化
            pos_words = ["利好", "增长", "上涨", "突破"]
            neg_words = ["利空", "下降", "下跌", "风险"]
            crit_words = ["立案调查", "退市", "财务造假"]

        pos_count = 0
        neg_count = 0
        critical_hits = []
        for item in news_items:
            text = item.get("title", "") + " " + item.get("content", "")
            for w in pos_words:
                if w in text:
                    pos_count += 1
            for w in neg_words:
                if w in text:
                    neg_count += 1
            for w in crit_words:
                if w in text:
                    critical_hits.append(w)
                    break

        if critical_hits:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="veto",
                strength=-1.0,
                confidence=0.9,
                reasoning=f"[降级] 重大负面: {critical_hits}",
                key_metrics={"pos_count": pos_count, "neg_count": neg_count, "critical_hits": critical_hits},
                veto_reason=f"重大负面关键词: {critical_hits}",
            )

        score = (pos_count - neg_count) / max(1, pos_count + neg_count)
        if score > 0.3:
            action = "buy"
        elif score < -0.3:
            action = "sell"
        else:
            action = "hold"

        return AgentDecision(
            agent_name=self.name,
            symbol=symbol,
            action=action,
            strength=max(-1.0, min(1.0, score)),
            confidence=0.5,
            reasoning=f"[降级] 利好 {pos_count} / 利空 {neg_count}",
            key_metrics={"pos_count": pos_count, "neg_count": neg_count},
        )
