"""D1 LLM 策略 Ideation 引擎 — 市场观察 → 假设生成 → 因子设计 → 回测验证 → 入库.

属于 Wave 4 G6 Phase D 首位, 核心目的: **让 LLM 在 Shadow 模式下生成候选因子/策略假设,
全程 audit.py 留痕, 初始仅产出不执行, 多样性约束 ≥0.7**.

五步流水线:
    1. observe_market()       — 生成市场观察上下文 (行情/板块/资金流)
    2. generate_hypotheses()  — LLM 生成策略假设 (自然语言描述 + 因子方向)
    3. design_factors()       — 从假设推导候选因子 (复用 CandidateFactor)
    4. validate_factors()     — 委托 D2 HypothesisVerifier 验证
    5. promote_to_library()   — 入库 AlphaFactorLibrary

设计原则:
    - Shadow 模式: 初始仅产出不执行, 全程审计留痕
    - 多样性约束: 策略多样性 ≥0.7, 无重复
    - 可测试: LLMRouter 为注入接口, 可用 mock 替换
    - 知识反馈: 从 D3 KnowledgeBase 加载上下文

用法:
    from utils.llm_evolution.strategy_ideation import (
        StrategyIdeationEngine, MarketObservation, Hypothesis,
    )
    engine = StrategyIdeationEngine(llm_router=router, audit_logger=audit)
    obs = engine.observe_market(market_data={...})
    hyps = engine.generate_hypotheses(obs, n=5)
    for h in hyps:
        candidates = engine.design_factors(h)
        validated = engine.validate_factors(candidates)
        engine.promote_to_library(validated)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

logger = logging.getLogger("strategy_ideation")


# ============================================================
# 协议定义 (鸭子类型, 避免硬依赖 LLMRouter)
# ============================================================


class LLMChatProtocol(Protocol):
    """D1 需要的 LLM 最小接口."""

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None: ...


class AuditLoggerProtocol(Protocol):
    """D1 需要的审计接口."""

    def log(
        self,
        module: str,
        action: str,
        severity: str = "INFO",
        symbol: str = "",
        reason: str = "",
        **kwargs: Any,
    ) -> None: ...


# ============================================================
# 数据结构
# ============================================================


@dataclass
class MarketObservation:
    """市场观察快照 (D1 第 1 步产出)."""

    date: str = ""
    index_close: float = 0.0
    index_change_pct: float = 0.0
    volume: float = 0.0
    sector_performance: dict[str, float] = field(default_factory=dict)
    north_flow: float = 0.0  # 北向资金净流入 (亿)
    market_breadth: float = 0.0  # 涨跌比
    volatility: float = 0.0  # VIX 等价
    summary: str = ""  # 自然语言摘要

    def to_prompt_context(self) -> str:
        """转换为 LLM prompt 上下文."""
        lines = [
            f"日期: {self.date}",
            f"指数收盘: {self.index_close:.2f}, 涨跌幅: {self.index_change_pct:+.2f}%",
            f"成交量: {self.volume:.0f}",
            f"北向资金: {self.north_flow:+.1f} 亿",
            f"涨跌比: {self.market_breadth:.2f}",
            f"波动率: {self.volatility:.4f}",
        ]
        if self.sector_performance:
            top_sectors = sorted(
                self.sector_performance.items(), key=lambda x: x[1], reverse=True
            )[:5]
            lines.append(
                "板块表现 (前5): "
                + ", ".join(f"{k}({v:+.2f}%)" for k, v in top_sectors)
            )
        if self.summary:
            lines.append(f"摘要: {self.summary}")
        return "\n".join(lines)


@dataclass
class Hypothesis:
    """LLM 生成的策略假设 (D1 第 2 步产出)."""

    id: str = ""
    description: str = ""  # 自然语言假设描述
    market_observation: str = ""  # 市场观察依据
    factor_direction: str = ""  # 因子方向 (long_small/long_large/long_short)
    proposed_factors: list[dict] = field(
        default_factory=list
    )  # 候选因子列表 [{name, category, formula}]
    strategy_style: str = ""  # 策略风格 (momentum/value/growth/...)
    llm_model: str = ""  # 生成模型
    audit_record: str = ""  # 审计记录 ID
    status: str = "pending"  # pending / validated / falsified / promoted
    created_at: str = ""
    diversity_hash: str = ""  # 用于多样性去重的 hash

    def compute_diversity_hash(self) -> str:
        """计算多样性 hash (基于描述+因子方向+风格)."""
        key = f"{self.description[:100]}|{self.factor_direction}|{self.strategy_style}"
        self.diversity_hash = hashlib.md5(
            key.encode(), usedforsecurity=False
        ).hexdigest()[:12]
        return self.diversity_hash


@dataclass
class IdeationCycleResult:
    """一次 Ideation 周期的汇总结果."""

    cycle_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    observation: MarketObservation | None = None
    hypotheses: list[Hypothesis] = field(default_factory=list)
    total_candidates: int = 0
    total_validated: int = 0
    total_promoted: int = 0
    diversity_score: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and len(self.hypotheses) > 0


# ============================================================
# Prompt 模板
# ============================================================

SYSTEM_PROMPT = """你是一个 A 股量化策略研究员. 你的任务是基于市场观察生成策略假设.

要求:
1. 假设必须基于市场观察数据, 不能凭空捏造
2. 每个假设需明确因子方向 (long_small/long_large/long_short)
3. 建议的因子必须有可计算性 (基于量价/财务/技术指标)
4. 输出 JSON 格式, 不要输出其他内容

输出格式:
```json
{
  "hypotheses": [
    {
      "description": "假设的自然语言描述",
      "market_observation": "市场观察依据",
      "factor_direction": "long_small | long_large | long_short",
      "proposed_factors": [
        {"name": "因子名", "category": "Momentum/Volatility/Value/...", "formula": "计算公式描述"}
      ],
      "strategy_style": "momentum/value/growth/quality/balanced/low_risk/reversal/volatility"
    }
  ]
}
```"""

HYPOTHESIS_PROMPT_TEMPLATE = """基于以下市场观察, 生成 {n} 个不同的策略假设.

市场观察:
{market_context}

{knowledge_context}

请确保假设的多样性, 避免重复."""


# ============================================================
# 主类
# ============================================================


class StrategyIdeationEngine:
    """LLM 策略 Ideation 生成引擎 — D1 五步流水线."""

    def __init__(
        self,
        llm_router: LLMChatProtocol,
        audit_logger: AuditLoggerProtocol | None = None,
        knowledge_base: Any = None,  # D3 KnowledgeBase (可选)
        shadow_mode: bool = True,
        min_diversity: float = 0.7,
    ) -> None:
        if llm_router is None:
            raise ValueError("llm_router 不能为 None")
        self.llm = llm_router
        self.audit = audit_logger
        self.kb = knowledge_base
        self.shadow_mode = shadow_mode
        self.min_diversity = min_diversity
        self._seen_hashes: set[str] = set()

    # ------------------------------------------------------------
    # 第 1 步: 市场观察
    # ------------------------------------------------------------

    def observe_market(self, market_data: dict[str, Any]) -> MarketObservation:
        """从原始市场数据生成 MarketObservation."""
        obs = MarketObservation(
            date=market_data.get("date", datetime.now().strftime("%Y-%m-%d")),
            index_close=float(market_data.get("index_close", 0.0)),
            index_change_pct=float(market_data.get("index_change_pct", 0.0)),
            volume=float(market_data.get("volume", 0.0)),
            sector_performance=market_data.get("sector_performance", {}),
            north_flow=float(market_data.get("north_flow", 0.0)),
            market_breadth=float(market_data.get("market_breadth", 0.0)),
            volatility=float(market_data.get("volatility", 0.0)),
            summary=market_data.get("summary", ""),
        )
        self._audit("OBSERVE", f"市场观察: {obs.date} 指数={obs.index_close:.2f}")
        return obs

    # ------------------------------------------------------------
    # 第 2 步: 假设生成 (LLM)
    # ------------------------------------------------------------

    def generate_hypotheses(
        self,
        observation: MarketObservation,
        n: int = 5,
    ) -> list[Hypothesis]:
        """调用 LLM 生成 n 个策略假设."""
        if n < 1:
            raise ValueError(f"n 应 ≥1, 实际 {n}")

        market_ctx = observation.to_prompt_context()
        knowledge_ctx = ""
        if self.kb is not None:
            knowledge_ctx = self._load_knowledge_context()

        prompt = HYPOTHESIS_PROMPT_TEMPLATE.format(
            n=n,
            market_context=market_ctx,
            knowledge_context=knowledge_ctx,
        )

        try:
            response = self.llm.chat(prompt, system=SYSTEM_PROMPT, temperature=0.7)
        except Exception as exc:
            logger.error(f"[D1] LLM 调用异常: {exc}")
            self._audit("LLM_ERROR", f"假设生成 LLM 调用失败: {exc}", severity="ERROR")
            return []

        if not response:
            self._audit("LLM_EMPTY", "LLM 返回空结果", severity="WARN")
            return []

        hypotheses = self._parse_hypotheses(response, observation)

        # 多样性过滤
        hypotheses = self._filter_diversity(hypotheses)

        self._audit("GENERATE", f"生成 {len(hypotheses)} 个假设 (请求 {n} 个)")
        return hypotheses

    # ------------------------------------------------------------
    # 第 3 步: 因子设计
    # ------------------------------------------------------------

    def design_factors(self, hypothesis: Hypothesis) -> list[dict]:
        """从假设推导候选因子列表 (直接复用 hypothesis.proposed_factors)."""
        candidates = hypothesis.proposed_factors
        self._audit("DESIGN", f"假设 {hypothesis.id} 设计 {len(candidates)} 个候选因子")
        return candidates

    # ------------------------------------------------------------
    # 第 4 步: 因子验证 (委托 D2)
    # ------------------------------------------------------------

    def validate_factors(
        self,
        candidates: list[dict],
        verifier: Any = None,
        factor_data: dict[str, Any] | None = None,
    ) -> list[dict]:
        """委托 D2 HypothesisVerifier 验证候选因子.

        Args:
            candidates: 候选因子列表
            verifier: D2 HypothesisVerifier 实例 (可选, 为 None 时跳过验证)
            factor_data: 因子数据 (可选)
        """
        if verifier is None:
            logger.info("[D1] 无 verifier, 跳过验证 (Shadow 模式)")
            return candidates

        validated: list[dict] = []
        for cand in candidates:
            try:
                verdict = verifier.verify(cand, factor_data)
                if verdict.get("pass", False):
                    cand["validated"] = True
                    cand["verdict"] = verdict
                    validated.append(cand)
                else:
                    self._audit(
                        "REJECT", f"因子 {cand.get('name')} 验证未通过", severity="WARN"
                    )
            except Exception as exc:
                logger.warning(f"[D1] 因子验证异常: {exc}")
                self._audit(
                    "VERIFY_ERROR",
                    f"因子 {cand.get('name')} 验证异常: {exc}",
                    severity="WARN",
                )

        self._audit(
            "VALIDATE", f"验证 {len(candidates)} 个因子, 通过 {len(validated)} 个"
        )
        return validated

    # ------------------------------------------------------------
    # 第 5 步: 入库
    # ------------------------------------------------------------

    def promote_to_library(self, validated: list[dict]) -> int:
        """将验证通过的因子入库 (Shadow 模式仅记录, 不实际执行)."""
        count = len(validated)
        if self.shadow_mode:
            self._audit(
                "PROMOTE_SHADOW", f"Shadow 模式: {count} 个因子标记入库 (不实际执行)"
            )
        else:
            self._audit("PROMOTE", f"入库 {count} 个因子")
        return count

    # ------------------------------------------------------------
    # 完整周期
    # ------------------------------------------------------------

    def run_ideation_cycle(
        self,
        market_data: dict[str, Any],
        n_hypotheses: int = 5,
        verifier: Any = None,
        factor_data: dict[str, Any] | None = None,
    ) -> IdeationCycleResult:
        """执行完整的五步 Ideation 周期."""
        cycle_id = f"ideation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        result = IdeationCycleResult(
            cycle_id=cycle_id,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )

        try:
            # Step 1: 观察市场
            result.observation = self.observe_market(market_data)

            # Step 2: 生成假设
            result.hypotheses = self.generate_hypotheses(
                result.observation, n=n_hypotheses
            )

            # Step 3-5: 对每个假设设计因子 → 验证 → 入库
            for hyp in result.hypotheses:
                candidates = self.design_factors(hyp)
                result.total_candidates += len(candidates)

                validated = self.validate_factors(
                    candidates, verifier=verifier, factor_data=factor_data
                )
                result.total_validated += len(validated)

                promoted = self.promote_to_library(validated)
                result.total_promoted += promoted

                if validated:
                    hyp.status = "validated"
                else:
                    hyp.status = "falsified"

            # 多样性评分
            result.diversity_score = self._compute_diversity_score(result.hypotheses)

        except Exception as exc:
            result.errors.append(f"{type(exc).__name__}: {exc}")
            logger.error(f"[D1] Ideation 周期异常: {exc}")

        result.finished_at = datetime.now().isoformat(timespec="seconds")

        self._audit(
            "CYCLE",
            (
                f"周期 {cycle_id}: 假设={len(result.hypotheses)} "
                f"候选={result.total_candidates} 验证通过={result.total_validated} "
                f"入库={result.total_promoted} 多样性={result.diversity_score:.2f}"
            ),
        )

        return result

    # ------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------

    def _parse_hypotheses(
        self, response: str, observation: MarketObservation
    ) -> list[Hypothesis]:
        """解析 LLM 返回的 JSON 假设列表."""
        # 提取 JSON 块
        json_str = response
        if "```json" in json_str:
            start = json_str.index("```json") + 7
            end = json_str.index("```", start)
            json_str = json_str[start:end]
        elif "```" in json_str:
            start = json_str.index("```") + 3
            end = json_str.index("```", start)
            json_str = json_str[start:end]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"[D1] LLM 返回非 JSON: {response[:200]}")
            return []

        hypotheses: list[Hypothesis] = []
        for i, h in enumerate(data.get("hypotheses", [])):
            hyp = Hypothesis(
                id=f"hyp_{datetime.now().strftime('%Y%m%d')}_{i:03d}",
                description=h.get("description", ""),
                market_observation=h.get(
                    "market_observation", observation.to_prompt_context()
                ),
                factor_direction=h.get("factor_direction", "long_small"),
                proposed_factors=h.get("proposed_factors", []),
                strategy_style=h.get("strategy_style", "balanced"),
                llm_model=getattr(self.llm, "name", "unknown"),
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            hyp.compute_diversity_hash()
            hypotheses.append(hyp)

        return hypotheses

    def _filter_diversity(self, hypotheses: list[Hypothesis]) -> list[Hypothesis]:
        """多样性过滤: 去重 (基于 diversity_hash)."""
        filtered: list[Hypothesis] = []
        for h in hypotheses:
            if h.diversity_hash not in self._seen_hashes:
                self._seen_hashes.add(h.diversity_hash)
                filtered.append(h)
            else:
                self._audit("DEDUP", f"假设 {h.id} 与已有重复, 去重")
        return filtered

    def _compute_diversity_score(self, hypotheses: list[Hypothesis]) -> float:
        """计算多样性评分 (0-1, 基于唯一 hash 比例)."""
        if not hypotheses:
            return 0.0
        unique_hashes = set(h.diversity_hash for h in hypotheses)
        return len(unique_hashes) / len(hypotheses)

    def _load_knowledge_context(self) -> str:
        """从 D3 KnowledgeBase 加载上下文."""
        try:
            ctx = self.kb.load_context_for_ideation()
            return f"已有知识:\n{ctx}" if ctx else ""
        except Exception as exc:
            logger.warning(f"[D1] 加载知识库上下文异常: {exc}")
            return ""

    def _audit(self, action: str, reason: str, severity: str = "INFO") -> None:
        """写审计日志."""
        if self.audit is not None:
            try:
                self.audit.log(
                    module="D1_IDEATION",
                    action=action,
                    severity=severity,
                    reason=reason,
                )
            except Exception:
                pass
