"""自动化因子研究闭环 (AutoFactorResearch) — 扩展 utils/alpha_factor 的真实闭环.

本模块在 rd_agent_quant.py (LIT-1.1 骨架) 的"假评估"基础上, 把自动化因子研究
落地为**数据驱动的真实闭环**:

    Researcher → Developer → Reviewer → Manager → (ML 组合)

  - Researcher: 规则模板库 + 可选 GLM-5 生成因子表达式 (DSL), fail-open 降级
  - Developer:  用 expression_engine 把表达式编译为真实 FactorValue
  - Reviewer:   用 base.build_* + evaluator 计算**真实** IC/ICIR/换手率/衰减
                 (彻底替换 rd_agent_quant 中硬编码 ic_mean=0.04 的伪评估)
  - Manager:    按真实阈值 accept/reject, 写入 FactorMemory, 维护因子库
  - ML 组合:    接受因子的横截面值 → LightGBM/线性 组合预测 → 报告增量 IC

设计原则 (与项目铁律一致):
  - 零硬依赖: LLM (glm5_client) / ML (lightgbm) / sklearn 缺失时优雅降级
  - 不依赖 QLib: 全程用 {symbol: {"closes": [...]}} 字典 + numpy/pandas
  - 无前视偏差: Reviewer 用滚动 replay, 因子(t) 严格配对 t→t+W 未来收益
  - fail-open: 任何单因子评估异常不影响其他因子与整体流程

用法:
    from utils.alpha_factor.auto_research import AutoFactorResearch
    ar = AutoFactorResearch(use_llm=False)
    report = ar.run_cycle(price_data=price_data)   # price_data 来自 AlphaFactorLibrary 同构
    print(report["accepted"], report["rejected"])
    combo = ar.combine_accepted()                  # 可选 ML 组合阶段

真实数据 (Wind MCP) — 自动拉取并转换 OHLCV:
    ar = AutoFactorResearch()
    report, price_data = ar.run_cycle_on_wind(
        ["600036.SH", "000001.SZ", "588000.SH"], days=300
    )
    # 或命令行: python -m utils.alpha_factor.auto_research --wind 600036.SH,000001.SZ --combine
    # 要求: 环境变量 WIND_API_KEY, 且 tools/wind_mcp_fetcher.py 可用 (失败优雅降级)

参考:
  - rd_agent_quant.py (LIT-1.1 多智能体骨架)
  - utils/alpha_factor/evaluator.py (alphalens 风格评估)
  - utils/alpha_factor/expression_engine.py (DSL 表达式引擎)
  - utils/alpha_factor/factor_memory.py (因子记忆 sqlite)
  - docs/自我进化框架/ARCHITECTURE_自我进化框架.md §6.3
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from utils.alpha_factor.base import (
    FactorValue,
    build_factor_history_from_prices,
    build_forward_returns_history,
)
from utils.alpha_factor.evaluator import build_factor_tear_sheet
from utils.alpha_factor.expression_engine import compute_expression_factors

logger = logging.getLogger(__name__)

# 降级站点可预期异常族 (R10/T6 收窄宽捕获: 可预期异常降级, 非预期异常显式暴露)
_LLM_DEGRADE_ERRS: tuple = (
    ImportError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    OSError,
    RuntimeError,
    TimeoutError,
    ConnectionError,
)
_COMPUTE_DEGRADE_ERRS: tuple = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    OSError,
    RuntimeError,
    ArithmeticError,
)
_ML_DEGRADE_ERRS: tuple = (
    ImportError,
    ValueError,
    TypeError,
    RuntimeError,
    OSError,
    ArithmeticError,
)
_MEMORY_ERRS: tuple = (
    ImportError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    OSError,
    RuntimeError,
    sqlite3.Error,
)
_WIND_IMPORT_ERRS: tuple = (ImportError, AttributeError, OSError)
_WIND_FETCH_ERRS: tuple = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    OSError,
    RuntimeError,
    TimeoutError,
    ConnectionError,
)
_SORT_ERRS: tuple = (TypeError, ValueError)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class FactorCandidate:
    """研究员生成的因子候选 (DSL 表达式)."""

    name: str
    hypothesis: str
    expression: str  # DSL 表达式字符串 (expression_engine 语法)
    category: str = "custom"
    rationale: str = ""
    source: str = "rule"  # rule / llm / library


@dataclass
class ReviewResult:
    """评审员对单个因子的真实评估结果."""

    name: str
    ic_mean: float = 0.0
    ic_ir: float = 0.0
    ic_win_rate: float = 0.0
    turnover: float = 0.0
    half_life_days: float | None = None
    long_short: float = 0.0
    monotonicity: float = 0.0
    passed: bool = False
    reason: str = ""
    # 供 ML 组合阶段复用的时序数据 (已与未来收益对齐)
    factor_history: list[dict[str, float]] = field(default_factory=list)
    forward_returns: list[dict[str, float]] = field(default_factory=list)


@dataclass
class CycleReport:
    """单次研究循环报告."""

    cycle_id: int
    n_proposed: int = 0
    n_implemented: int = 0
    n_accepted: int = 0
    n_rejected: int = 0
    accepted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    reviews: dict[str, ReviewResult] = field(default_factory=dict)


# ============================================================
# 规则模板库 (Researcher 兜底, 不依赖 LLM)
# ============================================================

_RULE_TEMPLATES: list[FactorCandidate] = [
    FactorCandidate(
        name="EXPR_MOM_20D",
        hypothesis="20日动量在趋势市有正 IC",
        expression="rank(close / delay(close, 20) - 1)",
        category="momentum",
        rationale="经典截面动量",
    ),
    FactorCandidate(
        name="EXPR_REV_5D",
        hypothesis="5日反转在震荡市有正 IC",
        expression="rank(-1 * (close / delay(close, 5) - 1))",
        category="reversal",
        rationale="短期反转",
    ),
    FactorCandidate(
        name="EXPR_VOL_PRICE_DIV",
        hypothesis="量价背离预示趋势反转",
        expression="rank(correlation(close, volume, 10))",
        category="volume_price",
        rationale="量价相关系数截面排名",
    ),
    FactorCandidate(
        name="EXPR_VOL_20D",
        hypothesis="低波动异象在 A 股显著",
        expression="rank(-1 * std(close, 20))",
        category="low_vol",
        rationale="20日收益波动越低越好",
    ),
    FactorCandidate(
        name="EXPR_MA_DEV_60D",
        hypothesis="价格偏离 60 日均线有均值回归",
        expression="rank(-1 * (close / mean(close, 60) - 1))",
        category="mean_reversion",
        rationale="相对长期均线偏离度",
    ),
    FactorCandidate(
        name="EXPR_TURNOVER_Z",
        hypothesis="成交量异常放大预示关注",
        expression="rank(zscore(delta(volume, 5)))",
        category="liquidity",
        rationale="5日成交量变化 Z-score",
    ),
]


# ============================================================
# 核心引擎
# ============================================================


class AutoFactorResearch:
    """自动化因子研究闭环.

    Args:
        ic_threshold: 接受所需最小 |IC| (默认 0.03, 国泰君安标准)
        ir_threshold: 接受所需最小 ICIR (默认 0.5)
        max_turnover: 最大容忍换手率 (默认 0.6)
        min_decay: 最小半衰期天数 (默认 2, 防止纯噪声因子)
        max_factors: 因子库上限 (默认 30)
        max_candidates_per_cycle: 每轮最多评估候选数 (默认 8, 控成本)
        use_llm: 是否调用 GLM-5 生成额外候选 (默认 False)
        memory: FactorMemory 实例 (可选, 用于跨会话记忆)
        forward_window: 评审用前瞻收益窗口 (默认 5)
        warmup_window: 滚动 replay 预热窗口 (默认 30)
    """

    def __init__(
        self,
        ic_threshold: float = 0.03,
        ir_threshold: float = 0.5,
        max_turnover: float = 0.6,
        min_decay: float = 2.0,
        max_factors: int = 30,
        max_candidates_per_cycle: int = 8,
        use_llm: bool = False,
        memory: Any = None,
        forward_window: int = 5,
        warmup_window: int = 30,
    ) -> None:
        self.ic_threshold = ic_threshold
        self.ir_threshold = ir_threshold
        self.max_turnover = max_turnover
        self.min_decay = min_decay
        self.max_factors = max_factors
        self.max_candidates_per_cycle = max_candidates_per_cycle
        self.use_llm = use_llm
        self.memory = memory
        self.forward_window = forward_window
        self.warmup_window = warmup_window

        self._cycle_count = 0
        self.accepted: dict[str, FactorValue] = {}
        self.accepted_meta: dict[str, FactorCandidate] = {}
        self._review_cache: dict[str, ReviewResult] = {}
        # 避免 LLM 重复生成相同因子的简单去重集
        self._seen_names: set[str] = set()

    # ------------------------------------------------------------
    # Stage 1: Researcher — 因子假设生成
    # ------------------------------------------------------------

    def propose_candidates(
        self,
        market_state: dict[str, Any] | None = None,
        existing: list[str] | None = None,
    ) -> list[FactorCandidate]:
        """生成候选因子: 规则模板 + 可选 GLM-5 (fail-open)."""
        existing_set = set(existing or [])
        seen = set(existing_set) | self._seen_names

        candidates: list[FactorCandidate] = []
        for tpl in _RULE_TEMPLATES:
            if tpl.name not in seen:
                candidates.append(tpl)
                seen.add(tpl.name)

        if self.use_llm:
            llm_cands = self._llm_propose(market_state)
            for c in llm_cands:
                if c.name not in seen:
                    candidates.append(c)
                    seen.add(c.name)

        # 限制本轮评估数量 (控成本)
        candidates = candidates[: self.max_candidates_per_cycle]
        for c in candidates:
            self._seen_names.add(c.name)
        return candidates

    def _llm_propose(
        self, market_state: dict[str, Any] | None
    ) -> list[FactorCandidate]:
        """调用 GLM-5 生成额外因子表达式 (可选, 异常即降级为空).

        期望 LLM 输出形如 (每行一个, TSV):
            EXPR|<name>|<expression>|<hypothesis>
        """
        try:
            from utils.glm5_client import GLM5Client, quick_chat  # noqa: F401

            client = GLM5Client()
            if not getattr(client, "is_ready", lambda: False)():
                logger.info("[Researcher] GLM-5 未就绪, 跳过 LLM 提案")
                return []
            regime = (market_state or {}).get("regime", "未知")
            prompt = (
                "你是量化因子研究员。请基于当前市场状态提出 2-3 个新的截面 Alpha 因子,"
                "使用如下 DSL 表达式 (支持 rank/zscore/correlation/delay/mean/std/delta/"
                "slope/volume/close 等)。每行严格输出 TSV: "
                "EXPR|<因子名,EXPR_前缀,英文大写>|<表达式>|<假设>\n"
                f"当前市场状态: {regime}。只输出表达式行, 不要解释。"
            )
            text = quick_chat(prompt)
            return self._parse_llm_candidates(text)
        except _LLM_DEGRADE_ERRS as e:  # LLM 不可用 → 降级
            logger.info("[Researcher] GLM-5 提案失败, 降级规则模板: %s", e)
            return []

    @staticmethod
    def _parse_llm_candidates(text: str) -> list[FactorCandidate]:
        """解析 LLM 输出的 TSV 候选行."""
        out: list[FactorCandidate] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("EXPR|"):
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            name = parts[1].strip()
            expr = parts[2].strip()
            hypo = parts[3].strip() if len(parts) > 3 else ""
            if not re.match(r"^[A-Z0-9_]+$", name):
                name = "EXPR_LLM_" + re.sub(r"[^A-Z0-9_]", "", name.upper())[:20]
            out.append(
                FactorCandidate(
                    name=name,
                    hypothesis=hypo or "GLM-5 生成",
                    expression=expr,
                    category="llm",
                    source="llm",
                )
            )
        return out

    # ------------------------------------------------------------
    # Stage 2: Developer — 表达式 → FactorValue
    # ------------------------------------------------------------

    def implement(
        self,
        candidates: list[FactorCandidate],
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, FactorValue]:
        """把候选表达式编译为真实 FactorValue (snapshot 用)."""
        if not candidates:
            return {}
        specs = [(c.name, c.expression) for c in candidates]
        try:
            result = compute_expression_factors(
                price_data, fundamentals=fundamentals, expressions=specs
            )
        except _COMPUTE_DEGRADE_ERRS as e:
            logger.warning("[Developer] 表达式批量编译失败: %s", e)
            return {}
        ok = {k: v for k, v in result.items() if v.values}
        failed = [c.name for c in candidates if c.name not in ok]
        if failed:
            logger.info("[Developer] %d 个表达式编译失败: %s", len(failed), failed)
        return ok

    # ------------------------------------------------------------
    # Stage 3: Reviewer — 真实 IC/ICIR/换手率/衰减
    # ------------------------------------------------------------

    def review(
        self,
        candidates: list[FactorCandidate],
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, ReviewResult]:
        """对候选因子计算真实评估指标 (滚动 replay, 无前视)."""
        reviews: dict[str, ReviewResult] = {}
        n_sym = len(price_data)
        if n_sym < 5:
            # evaluator._rank_ic 要求截面 >=5 只标的, 否则每日 IC 退化为 0
            logger.warning(
                "[Reviewer] 标的数量=%d < 5, 截面 IC 需 >=5 只标的 (evaluator 阈值), "
                "本轮 IC 将退化为 0; 建议扩大股票池到 10-30 只再评估",
                n_sym,
            )
        fwd_hist = build_forward_returns_history(
            price_data, forward_window=self.forward_window
        )
        if not fwd_hist:
            logger.warning("[Reviewer] 未来收益历史为空 (数据不足), 跳过评审")
            return reviews

        for cand in candidates:
            reviews[cand.name] = self._review_one(
                cand, price_data, fundamentals, fwd_hist
            )
        return reviews

    def _review_one(
        self,
        cand: FactorCandidate,
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None,
        fwd_hist: list[dict[str, float]],
    ) -> ReviewResult:
        empty = ReviewResult(name=cand.name)
        expr = cand.expression

        def _fn(slice_pd: dict[str, dict[str, list[float]]]) -> dict[str, FactorValue]:
            # 契约: 返回 {factor_name: FactorValue}
            # (base.build_factor_history_from_prices 会识别 FactorValue 并取 .values)
            try:
                ev = compute_expression_factors(
                    slice_pd,
                    fundamentals=fundamentals,
                    expressions=[(cand.name, expr)],
                )
                fv = ev.get(cand.name)
                return {cand.name: fv} if fv is not None else {}
            except _COMPUTE_DEGRADE_ERRS:
                return {}

        try:
            fhist_map = build_factor_history_from_prices(
                price_data, _fn, warmup_window=self.warmup_window
            )
        except _COMPUTE_DEGRADE_ERRS as e:
            logger.warning("[Reviewer] 构建 %s 因子历史失败: %s", cand.name, e)
            empty.reason = f"history_build_failed: {e}"
            return empty

        fhist = fhist_map.get(cand.name) or []
        if len(fhist) < 20:
            empty.reason = f"insufficient_history: {len(fhist)}<20"
            return empty

        # 严格对齐: fhist[i] 对应时间 warmup+i, 配对 fwd[warmup+i]
        aligned_f: list[dict[str, float]] = []
        aligned_r: list[dict[str, float]] = []
        for i in range(len(fhist)):
            t = self.warmup_window + i
            if t < len(fwd_hist):
                aligned_f.append(fhist[i])
                aligned_r.append(fwd_hist[t])

        if len(aligned_f) < 20:
            empty.reason = "aligned_pairs<20"
            return empty

        try:
            sheet = build_factor_tear_sheet(
                cand.name,
                factor_history=aligned_f,
                forward_returns_history=aligned_r,
                forward_window=self.forward_window,
            )
        except _COMPUTE_DEGRADE_ERRS as e:
            logger.warning("[Reviewer] 构建 %s TearSheet 失败: %s", cand.name, e)
            empty.reason = f"tearsheet_failed: {e}"
            return empty

        half_life = sheet.decay.half_life_days if sheet.decay is not None else None
        res = ReviewResult(
            name=cand.name,
            ic_mean=sheet.ic_mean,
            ic_ir=sheet.ic_ir,
            ic_win_rate=sheet.ic_win_rate,
            turnover=(
                sheet.turnover.avg_daily_turnover if sheet.turnover is not None else 0.0
            ),
            half_life_days=half_life,
            long_short=(
                sheet.quantile.long_short_return if sheet.quantile is not None else 0.0
            ),
            monotonicity=(
                sheet.quantile.monotonicity if sheet.quantile is not None else 0.0
            ),
            factor_history=aligned_f,
            forward_returns=aligned_r,
        )
        res.passed = self._passes(res)
        res.reason = "" if res.passed else self._fail_reason(res)
        return res

    def _passes(self, r: ReviewResult) -> bool:
        if abs(r.ic_mean) < self.ic_threshold:
            return False
        if abs(r.ic_ir) < self.ir_threshold:
            return False
        if r.turnover > self.max_turnover:
            return False
        if r.half_life_days is not None and r.half_life_days != float("inf"):
            if r.half_life_days < self.min_decay:
                return False
        return True

    def _fail_reason(self, r: ReviewResult) -> str:
        reasons = []
        if abs(r.ic_mean) < self.ic_threshold:
            reasons.append(f"IC {r.ic_mean:.3f}<{self.ic_threshold}")
        if abs(r.ic_ir) < self.ir_threshold:
            reasons.append(f"IR {r.ic_ir:.3f}<{self.ir_threshold}")
        if r.turnover > self.max_turnover:
            reasons.append(f"TO {r.turnover:.2f}>{self.max_turnover}")
        if (
            r.half_life_days is not None
            and r.half_life_days != float("inf")
            and r.half_life_days < self.min_decay
        ):
            reasons.append(f"half_life {r.half_life_days:.1f}d<{self.min_decay}")
        return "; ".join(reasons)

    # ------------------------------------------------------------
    # Stage 4: Manager — accept/reject + 记忆
    # ------------------------------------------------------------

    def manage(
        self,
        candidates: list[FactorCandidate],
        implemented: dict[str, FactorValue],
        reviews: dict[str, ReviewResult],
    ) -> CycleReport:
        """根据评审结果接受/拒绝因子, 维护因子库与记忆."""
        self._cycle_count += 1
        report = CycleReport(cycle_id=self._cycle_count)
        report.n_proposed = len(candidates)
        report.n_implemented = len(implemented)

        name_to_cand = {c.name: c for c in candidates}
        for name, rev in reviews.items():
            report.reviews[name] = rev
            if not rev.passed:
                report.rejected.append(name)
                self._record_memory(name_to_cand.get(name), rev, "ineffective")
                continue
            if len(self.accepted) >= self.max_factors:
                logger.info(
                    "[Manager] 因子库已满 (%d), %s 暂不接纳", self.max_factors, name
                )
                report.rejected.append(name)
                continue
            fv = implemented.get(name)
            if fv is None:
                report.rejected.append(name)
                continue
            self.accepted[name] = fv
            self._review_cache[name] = rev
            self.accepted_meta[name] = name_to_cand.get(name)
            report.accepted.append(name)
            self._record_memory(name_to_cand.get(name), rev, "effective")

        report.n_accepted = len(report.accepted)
        report.n_rejected = len(report.rejected)
        return report

    def _record_memory(
        self, cand: FactorCandidate | None, rev: ReviewResult, conclusion: str
    ) -> None:
        if self.memory is None:
            return
        try:
            from utils.alpha_factor.factor_memory import FactorExperiment

            self.memory.record_experiment(
                FactorExperiment(
                    experiment_id=f"auto_{rev.name}_{self._cycle_count}",
                    factor_name=rev.name,
                    factor_category=(cand.category if cand else "custom"),
                    params={"expression": cand.expression if cand else ""},
                    metrics={
                        "ic_mean": rev.ic_mean,
                        "ic_ir": rev.ic_ir,
                        "turnover": rev.turnover,
                        "half_life_days": (
                            rev.half_life_days
                            if rev.half_life_days is not None
                            else -1.0
                        ),
                        "long_short": rev.long_short,
                    },
                    conclusion=conclusion,
                    evidence={"source": "auto_research"},
                    tags=["auto_research", f"cycle_{self._cycle_count}"],
                )
            )
        except _MEMORY_ERRS as e:
            logger.debug("[Manager] 记忆写入失败: %s", e)

    def get_accepted_specs(self, with_stats: bool = True) -> list[dict]:
        """导出已接受因子的可持久化规格 (名称/表达式/类别/评审指标).

        用于接 AlphaFactorLibrary 持久化: 存表达式定义而非计算值,
        使因子可在任意新 Universe 上被重新求值 (第 16 大类表达式因子机制).

        Returns:
            [{name, expression, category, hypothesis, rationale, source,
              ic_mean, ic_ir, turnover, half_life_days, long_short}, ...]
        """
        specs: list[dict] = []
        for name, cand in self.accepted_meta.items():
            if cand is None:
                continue
            spec = {
                "name": name,
                "expression": cand.expression,
                "category": cand.category,
                "hypothesis": cand.hypothesis,
                "rationale": cand.rationale,
                "source": cand.source,
            }
            if with_stats:
                rev = self._review_cache.get(name)
                if rev is not None:
                    spec.update(
                        {
                            "ic_mean": rev.ic_mean,
                            "ic_ir": rev.ic_ir,
                            "turnover": rev.turnover,
                            "half_life_days": rev.half_life_days,
                            "long_short": rev.long_short,
                        }
                    )
            specs.append(spec)
        return specs

    # ------------------------------------------------------------
    # 全循环
    # ------------------------------------------------------------

    def run_cycle(
        self,
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None = None,
        market_state: dict[str, Any] | None = None,
    ) -> CycleReport:
        """运行一次完整研究循环: 提案 → 实现 → 评审 → 管理."""
        logger.info("[AutoResearch] 启动第 %d 轮研究循环", self._cycle_count + 1)
        candidates = self.propose_candidates(
            market_state, existing=list(self.accepted.keys())
        )
        if not candidates:
            logger.info("[AutoResearch] 无新候选因子")
            return CycleReport(cycle_id=self._cycle_count + 1, n_proposed=0)
        implemented = self.implement(candidates, price_data, fundamentals)
        reviews = self.review(candidates, price_data, fundamentals)
        report = self.manage(candidates, implemented, reviews)
        logger.info(
            "[AutoResearch] 轮次完成: 提案 %d / 实现 %d / 接受 %d / 拒绝 %d",
            report.n_proposed,
            report.n_implemented,
            report.n_accepted,
            report.n_rejected,
        )
        return report

    # ------------------------------------------------------------
    # 真实数据接入: Wind MCP
    # ------------------------------------------------------------

    def run_cycle_on_wind(
        self,
        symbols: list[str],
        days: int = 300,
        adjust: int = 1,
        market_state: dict[str, Any] | None = None,
        is_fund_fn: Any | None = None,
    ) -> tuple:
        """用 Wind MCP 真实行情跑一次完整研究循环 (闭环替换 synthetic 数据).

        Returns:
            (CycleReport, price_data) — price_data 为 Wind 拉取的
            {symbol: {"closes":..., ...}} 字典, 供 inspect / combine_accepted 复用。
            若 Wind 未返回任何有效数据, 返回 (空 CycleReport, {}) 且不崩溃 (fail-open)。
        """
        price_data = fetch_price_data_via_wind(
            symbols, days=days, adjust=adjust, is_fund_fn=is_fund_fn
        )
        if not price_data:
            logger.error(
                "[AutoResearch] Wind 未返回任何有效价格数据, 跳过研究循环 "
                "(检查 WIND_API_KEY / 网络 / 代码格式如 600036.SH)"
            )
            return CycleReport(cycle_id=self._cycle_count + 1, n_proposed=0), {}
        report = self.run_cycle(price_data, market_state=market_state)
        return report, price_data

    # ------------------------------------------------------------
    # ML 组合阶段 (ml_enhanced) — 接受因子横截面组合
    # ------------------------------------------------------------

    def combine_accepted(
        self,
        method: str = "auto",
    ) -> dict[str, Any]:
        """把已接受因子组合成复合 Alpha, 报告增量 IC.

        Args:
            method: "auto" (优先 LightGBM, 退化线性, 再退化 IC 加权) / "ic_weight" / "mean"

        Returns:
            {status, combined_ic, best_single_ic, n_factors, method, note}
        """
        names = list(self.accepted.keys())
        valid = [n for n in names if n in self._review_cache]
        if len(valid) < 2:
            return {
                "status": "skip",
                "reason": f"接受因子不足 2 ({len(valid)})",
                "combined_ic": 0.0,
                "best_single_ic": 0.0,
                "n_factors": len(valid),
            }

        # 组装面板: 每个样本 = (时间 t, 标的 s), 特征 = 各因子在 (t,s) 的截面值,
        # 目标 = s 在 t 的未来收益 (所有因子共用同一 forward_returns 序列).
        n = min(len(self._review_cache[nm].factor_history) for nm in valid)
        if n < 20:
            return {"status": "skip", "reason": "时序不足", "n_factors": len(valid)}

        feature_rows: list[list[float]] = []
        target_rows: list[float] = []
        for t in range(n):
            fdict0 = self._review_cache[valid[0]].factor_history[t]
            rdict0 = self._review_cache[valid[0]].forward_returns[t]
            for s in fdict0:
                if s not in rdict0 or not np.isfinite(rdict0[s]):
                    continue
                row: list[float] = []
                ok = True
                for nm in valid:
                    fv = self._review_cache[nm].factor_history[t].get(s)
                    if fv is None or not np.isfinite(fv):
                        ok = False
                        break
                    row.append(float(fv))
                if not ok:
                    continue
                feature_rows.append(row)
                target_rows.append(float(rdict0[s]))

        if len(feature_rows) < 20:
            return {"status": "skip", "reason": "有效样本不足", "n_factors": len(valid)}

        X = np.array(feature_rows, dtype=float)
        y = np.array(target_rows, dtype=float)

        # 用 rank-IC (Spearman) 评估预测力 (与目标未来收益)
        pred = self._fit_predict(X, y, method)
        combined_ic = self._rank_ic(pred, y)
        best_single = max(
            (abs(self._review_cache[nm].ic_mean) for nm in valid), default=0.0
        )
        used_method = self._last_method
        return {
            "status": "ok",
            "combined_ic": round(float(combined_ic), 4),
            "best_single_ic": round(float(best_single), 4),
            "incremental_ic": round(float(combined_ic - best_single), 4),
            "n_factors": len(valid),
            "n_samples": len(X),
            "method": used_method,
            "factor_names": valid,
        }

    _last_method: str = "none"

    def _fit_predict(self, X: np.ndarray, y: np.ndarray, method: str) -> np.ndarray:
        # 目标方向: 用 y 的符号做监督, 预测"下期收益方向强度"
        if method in ("auto", "ic_weight"):
            try:
                return self._predict_lightgbm(X, y)
            except _ML_DEGRADE_ERRS:
                pass
        if method in ("auto", "linear"):
            try:
                return self._predict_linear(X, y)
            except _ML_DEGRADE_ERRS:
                pass
        return self._predict_ic_weight(X, y)

    def _predict_lightgbm(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        import lightgbm as lgb  # type: ignore

        model = lgb.LGBMRegressor(
            n_estimators=80,
            max_depth=3,
            learning_rate=0.05,
            random_state=42,
            min_child_samples=5,
            verbose=-1,
        )
        model.fit(X, y)
        self._last_method = "lightgbm"
        return np.asarray(model.predict(X), dtype=float)

    def _predict_linear(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        from sklearn.linear_model import LinearRegression  # type: ignore

        model = LinearRegression()
        model.fit(X, y)
        self._last_method = "linear"
        return np.asarray(model.predict(X), dtype=float)

    def _predict_ic_weight(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        # IC 加权: 每个因子列自己对 y 的 rank-IC 作为权重, 加权求和
        weights = np.zeros(X.shape[1])
        for j in range(X.shape[1]):
            weights[j] = self._rank_ic(X[:, j], y)
        wsum = np.sum(np.abs(weights))
        if wsum < 1e-12:
            self._last_method = "mean"
            return X.mean(axis=1)
        weights = weights / wsum
        self._last_method = "ic_weight"
        return X.dot(weights)

    @staticmethod
    def _rank_ic(a: np.ndarray, b: np.ndarray) -> float:
        a = np.asarray(a, dtype=float)
        b = np.asarray(b, dtype=float)
        if np.std(a) < 1e-12 or np.std(b) < 1e-12:
            return 0.0
        try:
            from scipy.stats import spearmanr

            corr, _ = spearmanr(a, b)
            return float(corr) if not np.isnan(corr) else 0.0
        except ImportError:
            ra = a.argsort().argsort()
            rb = b.argsort().argsort()
            ra = ra - ra.mean()
            rb = rb - rb.mean()
            den = np.sqrt((ra**2).sum() * (rb**2).sum())
            return float((ra * rb).sum() / den) if den > 0 else 0.0

    # ------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        return {
            "cycles_run": self._cycle_count,
            "accepted": list(self.accepted.keys()),
            "n_accepted": len(self.accepted),
            "max_factors": self.max_factors,
            "use_llm": self.use_llm,
            "thresholds": {
                "ic": self.ic_threshold,
                "ir": self.ir_threshold,
                "max_turnover": self.max_turnover,
                "min_decay": self.min_decay,
            },
        }


# ============================================================
# Wind MCP 真实数据接入 (零硬依赖: 失败时优雅降级, 不抛异常)
# ============================================================

# OHLCV 字段别名 (兼容 Wind MCP 可能返回的中文 / 英文列名)
_OHLC_ALIASES: dict[str, list[str]] = {
    "open": ["开盘价", "开盘", "open", "open_price", "O"],
    "high": ["最高价", "最高", "high", "high_price", "H"],
    "low": ["最低价", "最低", "low", "low_price", "L"],
    "close": ["收盘价", "收盘", "close", "close_price", "C"],
    "volume": ["成交量", "volume", "vol", "VOL", "VOLUME", "成交额", "amount"],
    "date": ["时间", "日期", "date", "datetime", "trade_date", "DATE"],
}


def _normalize_key(k: str) -> str:
    return "".join(ch for ch in str(k).lower() if ch.isalnum())


def _infer_field_map(keys: list[str]) -> dict[str, str]:
    """从 Wind 返回的记录键名推断 {标准字段: 实际键} 映射 (容错中文/英文)."""
    norm_to_orig = {_normalize_key(k): k for k in keys}
    field_map: dict[str, str] = {}
    for std, aliases in _OHLC_ALIASES.items():
        for alias in aliases:
            nal = _normalize_key(alias)
            if nal in norm_to_orig:  # 精确匹配
                field_map[std] = norm_to_orig[nal]
                break
            hit = next(
                (orig for nk, orig in norm_to_orig.items() if nal in nk or nk in nal),
                None,
            )
            if hit:  # 包含匹配 (如 "成交量" 命中 "成交量(手)")
                field_map[std] = hit
                break
    return field_map


def _records_to_price_data(
    records: list[dict], symbol: str
) -> dict[str, list[float]] | None:
    """把 Wind K 线记录列表转为 auto_research 的 price_data 格式.

    返回 {closes, opens, highs, lows, volumes}; 缺失的 O/H/L 用 close 兜底。
    """
    if not records or not isinstance(records[0], dict):
        return None
    field_map = _infer_field_map(list(records[0].keys()))
    if "close" not in field_map:
        return None  # 没有收盘价无法继续

    # 按日期升序排列 (若可推断), 保证时序回测正确
    date_key = field_map.get("date")
    if date_key:
        try:
            records = sorted(records, key=lambda r: str(r.get(date_key, "")))
        except _SORT_ERRS:
            pass

    closes, opens, highs, lows, volumes = [], [], [], [], []
    for r in records:
        try:
            c = float(r.get(field_map["close"]))
        except (TypeError, ValueError):
            continue
        closes.append(c)
        try:
            o = float(r[field_map["open"]]) if "open" in field_map else c
        except (TypeError, ValueError, KeyError):
            o = c
        opens.append(o)
        try:
            h = float(r[field_map["high"]]) if "high" in field_map else c
        except (TypeError, ValueError, KeyError):
            h = c
        highs.append(h)
        try:
            lo = float(r[field_map["low"]]) if "low" in field_map else c
        except (TypeError, ValueError, KeyError):
            lo = c
        lows.append(lo)
        try:
            v = float(r[field_map["volume"]]) if "volume" in field_map else 0.0
        except (TypeError, ValueError, KeyError):
            v = 0.0
        volumes.append(v)

    if len(closes) < 2:
        return None
    return {
        "closes": closes,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
    }


def fetch_price_data_via_wind(
    symbols: list[str],
    days: int = 300,
    adjust: int = 1,
    is_fund_fn: Any | None = None,
    max_symbols: int = 80,
) -> dict[str, dict[str, list[float]]]:
    """通过 Wind MCP 拉取真实日线 OHLCV, 转换为因子研究用 price_data 格式.

    Args:
        symbols: Wind 代码列表, 如 ["600036.SH", "000001.SZ", "588000.SH"]
        days: 回溯交易日数 (Wind MCP 实际会多拉一点再截取)
        adjust: 复权类型 (1=前复权, ETF/份额折算必须复权);
                见 tools.wind_mcp_fetcher.KLINE_ADJUST_*
        is_fund_fn: 可选 callable(windcode)->bool 强制指定 ETF/基金;
                    默认按首字符 1/5 判定
        max_symbols: 单次最多拉取的标的数量 (防止打爆 API 配额)

    Returns:
        {symbol: {"closes": [...], "opens": [...], "highs": [...],
                  "lows": [...], "volumes": [...]}}
        拉取失败/无数据的标的会被跳过 (fail-open, 不抛异常)。
    """
    try:
        from tools.wind_mcp_fetcher import KLINE_ADJUST_QFQ, wind_get_kline
    except _WIND_IMPORT_ERRS as e:  # 工具缺失 → 优雅降级
        logger.error("[Data] Wind MCP fetcher 不可用: %s", e)
        return {}

    if adjust is None:
        adjust = KLINE_ADJUST_QFQ

    out: dict[str, dict[str, list[float]]] = {}
    for sym in list(symbols)[:max_symbols]:
        sym = sym.strip()
        if not sym:
            continue
        try:
            is_fund = is_fund_fn(sym) if is_fund_fn else sym[:1] in ("1", "5")
            recs = wind_get_kline(sym, days=days, is_fund=is_fund, adjust=adjust)
        except _WIND_FETCH_ERRS as e:
            logger.warning("[Data] Wind 拉取 %s 失败: %s", sym, e)
            continue
        if not recs:
            logger.warning("[Data] Wind 无 %s 数据", sym)
            continue
        converted = _records_to_price_data(recs, sym)
        if not converted:
            logger.warning("[Data] %s K线解析失败 (缺少收盘价字段?)", sym)
            continue
        out[sym] = converted
        logger.info("[Data] %s 拉取成功: %d 根K线", sym, len(converted["closes"]))
    if not out:
        logger.error("[Data] Wind 未返回任何有效价格数据 (检查 WIND_API_KEY/网络)")
    return out


# ============================================================
# 自检 (synthetic 数据, 无需 QLib / 网络)
# ============================================================


def _synthetic_price_data(
    n_symbols: int = 30, n_days: int = 260, seed: int = 42
) -> dict[str, dict[str, list[float]]]:
    """生成合成 OHLCV 价格数据 (含真实截面动量结构, 使因子有可测 IC).

    每只标的分配一个持久动量信号 m[s]~N(0,1), 日收益 = 0.0005 + 0.01*m[s] + 噪声。
    因此过去 20 日收益 (动量因子) 与未来收益都由 m[s] 驱动, 动量因子具有显著正 IC,
    足以让真实评估器区分"有效/无效"因子, 证明 R&D 闭环是数据驱动的而非硬编码。
    """
    rng = np.random.default_rng(seed)
    ms = rng.normal(0.0, 1.0, size=n_symbols)
    price_data: dict[str, dict[str, list[float]]] = {}
    for s in range(n_symbols):
        closes = [float(rng.uniform(8, 60))]
        vols = [float(rng.uniform(1e6, 5e6))]
        m = ms[s]
        for _ in range(1, n_days):
            ret = 0.0005 + 0.01 * m + rng.normal(0, 0.008)
            closes.append(max(0.5, closes[-1] * (1 + ret)))
            vols.append(float(rng.uniform(1e6, 5e6) * (1 + abs(ret) * 5)))
        highs = [c * (1 + abs(rng.normal(0, 0.01))) for c in closes]
        lows = [c * (1 - abs(rng.normal(0, 0.01))) for c in closes]
        opens = [closes[i - 1] if i > 0 else closes[0] for i in range(n_days)]
        price_data[f"SYM{s:03d}"] = {
            "closes": closes,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "volumes": vols,
        }
    return price_data


def quick_check() -> dict[str, Any]:
    """自检接口 — 供 run_quick_check / 命令行调用."""
    # 抑制表达式引擎逐切片日志刷屏
    logging.getLogger("alpha_factor.expression_engine").setLevel(logging.WARNING)

    price_data = _synthetic_price_data()
    ar = AutoFactorResearch(use_llm=False)
    report = ar.run_cycle(price_data)
    combo = ar.combine_accepted()

    ic_values = [r.ic_mean for r in report.reviews.values()]
    # rd_agent_quant 旧骨架硬编码 ic_mean=0.04; 真实闭环必须不再是这个常量
    all_old_hardcode = bool(ic_values) and all(abs(v - 0.04) < 1e-9 for v in ic_values)

    return {
        "available": True,
        "cycles_run": ar._cycle_count,
        "n_proposed": report.n_proposed,
        "n_implemented": report.n_implemented,
        "n_accepted": report.n_accepted,
        "n_rejected": report.n_rejected,
        "ic_values": [round(v, 4) for v in ic_values],
        "reviews": {
            name: {
                "ic": round(r.ic_mean, 4),
                "ir": round(r.ic_ir, 3),
                "to": round(r.turnover, 3),
                "passed": r.passed,
                "reason": r.reason,
            }
            for name, r in report.reviews.items()
        },
        "is_old_hardcode": all_old_hardcode,
        "ml_combo": combo,
        "status": ar.get_status(),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AutoFactorResearch 驱动")
    parser.add_argument(
        "--wind",
        type=str,
        default=None,
        help="逗号分隔的 Wind 代码 (如 600036.SH,000001.SZ,588000.SH), "
        "用 Wind MCP 真实行情跑研究闭环",
    )
    parser.add_argument("--days", type=int, default=300, help="Wind 回溯交易日数")
    parser.add_argument("--use-llm", action="store_true", help="启用 GLM-5 生成因子")
    parser.add_argument("--combine", action="store_true", help="跑完后做 ML 组合阶段")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.wind:
        syms = [s.strip() for s in args.wind.split(",") if s.strip()]
        ar = AutoFactorResearch(use_llm=args.use_llm)
        report, price_data = ar.run_cycle_on_wind(syms, days=args.days)
        print("=" * 60)
        print(f"AutoFactorResearch (Wind MCP 真实数据) — {len(price_data)} 只标的")
        print("=" * 60)
        if not price_data:
            print("Wind 未返回有效数据 (检查 WIND_API_KEY / 网络 / 代码格式)")
        else:
            print(
                f"提案={report.n_proposed} 实现={report.n_implemented} "
                f"接受={report.n_accepted} 拒绝={report.n_rejected}"
            )
            for name, r in report.reviews.items():
                print(
                    f"  {name:<20} IC={r.ic_mean:>7.4f} IR={r.ic_ir:>6.3f} "
                    f"TO={r.turnover:>5.3f} {'通过' if r.passed else '拒绝'} "
                    f"{('('+r.reason+')') if r.reason else ''}"
                )
            if args.combine and ar.accepted:
                print(f"ML 组合: {ar.combine_accepted()}")
    else:
        chk = quick_check()
        print("=" * 60)
        print("AutoFactorResearch 自检")
        print("=" * 60)
        print(
            f"提案={chk['n_proposed']} 实现={chk['n_implemented']} "
            f"接受={chk['n_accepted']} 拒绝={chk['n_rejected']}"
        )
        print("各因子评审: ")
        for name, r in chk["reviews"].items():
            print(
                f"  {name:<20} IC={r['ic']:>7.4f} IR={r['ir']:>6.3f} "
                f"TO={r['to']:>5.3f} {'通过' if r['passed'] else '拒绝'} "
                f"{('('+r['reason']+')') if r['reason'] else ''}"
            )
        print(f"ML 组合: {chk['ml_combo']}")
        assert not chk[
            "is_old_hardcode"
        ], "Reviewer IC 仍是 rd_agent_quant 的硬编码 0.04!"
        assert chk["n_implemented"] > 0, "Developer 未能实现任何因子!"
        print("自检通过 ✓ (真实数据驱动, 非硬编码)")
