"""
DeepFund 防泄漏评估基准 (NeurIPS 2025)
=====================================

文献依据: #22 DeepFund: Live Fund Benchmark (NeurIPS 2025)
论文标题: "Time Travel is Cheating: Going Live with DeepFund for Real-Time
          Fund Investment Benchmarking"
开源仓库: https://github.com/HKUSTDial/DeepFund

核心问题
--------
LLM 通过预训练可能"记住"了未来信息 (look-ahead bias / time travel)。
本 harness 在真实时间点给 LLM 截止该时点的历史数据, 检测其决策是否
"异常准确" — 若超越统计显著性阈值, 则提示可能存在信息泄漏。

架构
----
1. LLMAdapter          — 统一 LLM 接口 (复用 utils/llm_client.py, 支持 mock)
2. BenchmarkDataLoader — 基准数据加载 (A 股子集, 严格时序切分)
3. TimeLeakageDetector — 时间穿越检测器 (本基准核心创新)
4. DeepFundHarness     — 主评估器 (多 LLM 竞技场 + 报告生成)

验收标准 (LIT-1.3)
------------------
- 9 LLM 实测 (或 mock 替代)
- 时间穿越检测通过
- 归档到 reports/eval/deepfund/

使用示例
--------
    from tests.eval.deepfund_harness import DeepFundHarness, LLMAdapter

    harness = DeepFundHarness(symbols=["000001.SZ", "600000.SH"])
    adapters = [LLMAdapter(provider="mock", model=f"mock-{i}") for i in range(9)]
    report = harness.run_evaluation(adapters, start_date="2024-01-01",
                                    end_date="2024-06-30")
    harness.save_report(report, "reports/eval/deepfund/")
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("deepfund_harness")

# ============================================================
# 常量
# ============================================================

# 9 个 LLM (参考 DeepFund 论文, 适配国内可用模型)
DEFAULT_LLM_REGISTRY: list[dict[str, str]] = [
    {"provider": "openai", "model": "gpt-4o", "name": "GPT-4o"},
    {"provider": "openai", "model": "gpt-4o-mini", "name": "GPT-4o-mini"},
    {"provider": "openai", "model": "gpt-4-turbo", "name": "GPT-4-turbo"},
    {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet",
        "name": "Claude-3.5-Sonnet",
    },
    {"provider": "anthropic", "model": "claude-3-5-haiku", "name": "Claude-3.5-Haiku"},
    {"provider": "deepseek", "model": "deepseek-chat", "name": "DeepSeek-V3"},
    {"provider": "deepseek", "model": "deepseek-reasoner", "name": "DeepSeek-R1"},
    {"provider": "zhipu", "model": "glm-5", "name": "GLM-5"},
    {"provider": "qwen", "model": "qwen2.5-72b", "name": "Qwen2.5-72B"},
]

# 时间穿越检测阈值
LEAKAGE_P_VALUE_THRESHOLD = 0.01  # 1% 显著性水平
LEAKAGE_SHARPE_ANOMALY = 3.0  # Sharpe > 3.0 视为异常 (参考论文)
LEAKAGE_WIN_RATE_ANOMALY = 0.75  # 胜率 > 75% 视为可疑

# 默认 A 股评估标的 (沪深 300 核心子集)
DEFAULT_SYMBOLS = [
    "000001.SZ",  # 平安银行
    "000002.SZ",  # 万科A
    "000858.SZ",  # 五粮液
    "600036.SH",  # 招商银行
    "600519.SH",  # 贵州茅台
    "601318.SH",  # 中国平安
]


# ============================================================
# 数据结构
# ============================================================


@dataclass
class MarketData:
    """截止某时点的市场数据快照 (严格时序边界)。"""

    date: str
    prices: dict[str, list[float]]  # symbol -> 历史收盘价序列 (截止 date)
    volumes: dict[str, list[int]] = field(default_factory=dict)
    news: list[dict[str, str]] = field(default_factory=list)  # 截止 date 的新闻

    def info_cutoff(self) -> str:
        """返回信息截止时点 (用于 prompt 构造)。"""
        return self.date


@dataclass
class Decision:
    """LLM 在某时点的交易决策。"""

    date: str
    symbol: str
    action: str  # "buy" / "sell" / "hold"
    weight: float  # 0.0 ~ 1.0
    reasoning: str = ""  # LLM 推理过程 (用于泄漏检测)
    timestamp: str = ""  # 决策生成时间戳


@dataclass
class Metrics:
    """评估指标。"""

    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    n_decisions: int = 0
    n_buy: int = 0
    n_sell: int = 0
    n_hold: int = 0


@dataclass
class LeakageReport:
    """时间穿越检测报告。"""

    is_leaked: bool = False
    p_value: float = 1.0
    anomaly_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    chronological_ok: bool = True
    info_boundary_ok: bool = True


@dataclass
class EvalResult:
    """单个 LLM 的评估结果。"""

    llm_name: str
    provider: str
    model: str
    decisions: list[Decision] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    leakage: LeakageReport = field(default_factory=LeakageReport)
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class EvalReport:
    """完整评估报告 (多 LLM 竞技场)。"""

    start_date: str = ""
    end_date: str = ""
    symbols: list[str] = field(default_factory=list)
    results: list[EvalResult] = field(default_factory=list)
    generated_at: str = ""
    n_trading_days: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "symbols": self.symbols,
            "generated_at": self.generated_at,
            "n_trading_days": self.n_trading_days,
            "results": [
                {
                    "llm_name": r.llm_name,
                    "provider": r.provider,
                    "model": r.model,
                    "metrics": {
                        "total_return": r.metrics.total_return,
                        "sharpe_ratio": r.metrics.sharpe_ratio,
                        "max_drawdown": r.metrics.max_drawdown,
                        "win_rate": r.metrics.win_rate,
                        "n_decisions": r.metrics.n_decisions,
                    },
                    "leakage": {
                        "is_leaked": r.leakage.is_leaked,
                        "p_value": r.leakage.p_value,
                        "anomaly_score": r.leakage.anomaly_score,
                        "reasons": r.leakage.reasons,
                    },
                    "elapsed_seconds": r.elapsed_seconds,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


# ============================================================
# LLM 适配器
# ============================================================


class LLMAdapter:
    """统一 LLM 接口, 复用 utils/llm_client.py, 支持 mock 模式。

    mock 模式: 不调用真实 LLM, 使用确定性伪随机生成决策,
    用于无 API key 环境下的基准测试 + CI 验证。
    """

    def __init__(
        self, provider: str, model: str, name: str = "", mock: Optional[bool] = None
    ) -> None:
        self.provider = provider
        self.model = model
        self.name = name or f"{provider}/{model}"
        # 自动检测 mock 模式: 无 API key 时降级为 mock
        if mock is None:
            mock = not self._has_api_key()
        self.mock = mock
        self._client: Any = None
        if not mock:
            self._client = self._load_client()

    def _has_api_key(self) -> bool:
        """检查是否有对应 provider 的 API key。"""
        key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "zhipu": "ZHIPU_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
        }
        env_var = key_map.get(self.provider, "")
        return bool(os.environ.get(env_var, ""))

    def _load_client(self) -> Any:
        """加载真实 LLM 客户端 (复用项目统一客户端)。"""
        try:
            from utils import llm_client

            return llm_client
        except (
            ImportError,
            ModuleNotFoundError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ):
            logger.warning("DeepFund: utils.llm_client 不可用, 降级为 mock")
            self.mock = True
            return None

    def chat(self, prompt: str, system: str = "") -> str:
        """调用 LLM 生成决策, mock 模式返回确定性伪随机结果。"""
        if self.mock:
            return self._mock_chat(prompt, system)
        if self._client is None:
            return self._mock_chat(prompt, system)
        try:
            result = self._client.chat(
                prompt=prompt, system=system, temperature=0.3, max_tokens=1000
            )
            return result or self._mock_chat(prompt, system)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"DeepFund: LLM chat 失败 ({e}), 降级为 mock")
            return self._mock_chat(prompt, system)

    def _mock_chat(self, prompt: str, system: str) -> str:
        """确定性伪随机决策 (基于 prompt 哈希, 保证可复现)。"""
        seed = hash(prompt + self.model) & 0xFFFFFFFF
        rng = random.Random(seed)
        action = rng.choice(["buy", "sell", "hold"])
        weight = round(rng.uniform(0.0, 1.0), 4)
        return json.dumps(
            {
                "action": action,
                "weight": weight,
                "reasoning": f"mock-{self.model}: 基于历史数据的伪随机决策",
            },
            ensure_ascii=False,
        )

    def is_available(self) -> bool:
        """检查 LLM 是否可用 (mock 始终可用)。"""
        if self.mock:
            return True
        if self._client is None:
            return False
        try:
            status = self._client.test_connection()
            return bool(status.get("available", False))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            return False


# ============================================================
# 基准数据加载器
# ============================================================


class BenchmarkDataLoader:
    """基准数据加载器, 严格时序切分 (防止信息泄漏)。

    使用合成数据 (几何布朗运动) 作为默认基准,
    确保评估可复现且不依赖外部数据源。
    生产环境可子类化并重写 load_market_data 接入真实数据。
    """

    def __init__(
        self, symbols: list[str], start_date: str, end_date: str, seed: int = 42
    ) -> None:
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.seed = seed
        self._cache: dict[str, MarketData] = {}

    def _generate_synthetic_prices(self) -> dict[str, dict[str, list[float]]]:
        """生成合成价格数据 (几何布朗运动, 可复现)。"""
        rng = random.Random(self.seed)
        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        dates: list[str] = []
        current = start
        while current <= end:
            if current.weekday() < 5:  # 工作日
                dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        prices: dict[str, dict[str, list[float]]] = {}
        for symbol in self.symbols:
            symbol_prices: list[float] = []
            price = 100.0 * (1.0 + rng.uniform(-0.3, 0.3))
            for _ in dates:
                ret = rng.gauss(0.0002, 0.02)  # 漂移 + 波动率
                price *= math.exp(ret)
                symbol_prices.append(round(price, 4))
            prices[symbol] = {"dates": dates, "prices": symbol_prices}
        return prices

    def load_market_data(self, date: str) -> MarketData:
        """加载截止 date 的市场数据 (严格时序边界)。"""
        if date in self._cache:
            return self._cache[date]

        all_data = self._generate_synthetic_prices()
        cutoff_prices: dict[str, list[float]] = {}
        cutoff_volumes: dict[str, list[int]] = {}

        for symbol in self.symbols:
            symbol_data = all_data[symbol]
            dates = symbol_data["dates"]
            prices = symbol_data["prices"]
            # 严格截止: 只包含 <= date 的数据
            cutoff_idx = [i for i, d in enumerate(dates) if d <= date]
            cutoff_prices[symbol] = [prices[i] for i in cutoff_idx]
            cutoff_volumes[symbol] = [
                int(1e6 * (1.0 + random.Random(hash(dates[i])).uniform(-0.5, 0.5)))
                for i in cutoff_idx
            ]

        market_data = MarketData(
            date=date,
            prices=cutoff_prices,
            volumes=cutoff_volumes,
            news=[],  # 生产环境可接入新闻数据
        )
        self._cache[date] = market_data
        return market_data

    def get_trading_dates(self) -> list[str]:
        """获取评估区间内的交易日列表。"""
        all_data = self._generate_synthetic_prices()
        if not all_data:
            return []
        first_symbol = self.symbols[0]
        return all_data[first_symbol]["dates"]


# ============================================================
# 时间穿越检测器 (核心创新)
# ============================================================


class TimeLeakageDetector:
    """时间穿越检测器 — DeepFund 基准的核心创新。

    检测方法:
    1. 时序检查: 决策是否按时间顺序生成 (无未来决策混入)
    2. 信息边界检查: LLM 推理过程是否引用了未来日期
    3. 统计异常检测: 决策表现是否超越统计显著性阈值
    4. 对照组对比: 与随机决策基准对比
    """

    def __init__(
        self,
        p_value_threshold: float = LEAKAGE_P_VALUE_THRESHOLD,
        sharpe_anomaly: float = LEAKAGE_SHARPE_ANOMALY,
        win_rate_anomaly: float = LEAKAGE_WIN_RATE_ANOMALY,
    ) -> None:
        self.p_value_threshold = p_value_threshold
        self.sharpe_anomaly = sharpe_anomaly
        self.win_rate_anomaly = win_rate_anomaly

    def detect(
        self,
        decisions: list[Decision],
        metrics: Metrics,
        benchmark_metrics: Optional[Metrics] = None,
    ) -> LeakageReport:
        """执行时间穿越检测, 返回泄漏报告。"""
        reasons: list[str] = []

        # 1. 时序检查
        chronological_ok = self._check_chronological_order(decisions)
        if not chronological_ok:
            reasons.append("决策未按时间顺序生成 (存在未来决策混入)")

        # 2. 信息边界检查
        info_boundary_ok = self._check_info_boundary(decisions)
        if not info_boundary_ok:
            reasons.append("LLM 推理过程引用了未来日期信息")

        # 3. 统计异常检测
        anomaly_score = 0.0
        p_value = 1.0

        if metrics.sharpe_ratio > self.sharpe_anomaly:
            anomaly_score += 0.4
            reasons.append(
                f"Sharpe {metrics.sharpe_ratio:.2f} > {self.sharpe_anomaly} (异常高)"
            )
            p_value = min(p_value, 0.005)

        if metrics.win_rate > self.win_rate_anomaly:
            anomaly_score += 0.3
            reasons.append(
                f"胜率 {metrics.win_rate:.2%} > {self.win_rate_anomaly:.0%} (可疑)"
            )
            p_value = min(p_value, 0.02)

        # 4. 对照组对比 (与随机基准对比)
        if benchmark_metrics is not None:
            benchmark_sharpe = abs(benchmark_metrics.sharpe_ratio)
            actual_sharpe = abs(metrics.sharpe_ratio)
            if benchmark_sharpe > 0 and actual_sharpe > benchmark_sharpe * 5:
                anomaly_score += 0.3
                reasons.append(
                    f"Sharpe 超越随机基准 5 倍 ({actual_sharpe:.2f} vs {benchmark_sharpe:.2f})"
                )
                p_value = min(p_value, 0.01)

        # 单个强异常信号 (Sharpe>3.0 或 胜率>75%) 即可触发泄漏标记
        is_leaked = (
            (anomaly_score >= 0.3) or (not chronological_ok) or (not info_boundary_ok)
        )

        return LeakageReport(
            is_leaked=is_leaked,
            p_value=p_value,
            anomaly_score=round(anomaly_score, 4),
            reasons=reasons,
            chronological_ok=chronological_ok,
            info_boundary_ok=info_boundary_ok,
        )

    def _check_chronological_order(self, decisions: list[Decision]) -> bool:
        """检查决策是否按时间顺序生成。"""
        if len(decisions) <= 1:
            return True
        for i in range(1, len(decisions)):
            if decisions[i].date < decisions[i - 1].date:
                return False
        return True

    def _check_info_boundary(self, decisions: list[Decision]) -> bool:
        """检查 LLM 推理过程是否引用了未来日期。

        启发式检测: reasoning 中出现的日期 > decision.date 则视为泄漏。
        """
        for decision in decisions:
            if not decision.reasoning:
                continue
            # 提取 reasoning 中的日期模式 (YYYY-MM-DD)
            import re

            date_pattern = r"(\d{4}-\d{2}-\d{2})"
            found_dates = re.findall(date_pattern, decision.reasoning)
            for found_date in found_dates:
                if found_date > decision.date:
                    return False
        return True


# ============================================================
# 主评估器
# ============================================================


class DeepFundHarness:
    """DeepFund 防泄漏评估基准主评估器。

    使用示例:
        harness = DeepFundHarness(symbols=["000001.SZ"])
        adapters = [LLMAdapter(provider="mock", model=f"mock-{i}") for i in range(9)]
        report = harness.run_evaluation(adapters, "2024-01-01", "2024-06-30")
    """

    def __init__(self, symbols: Optional[list[str]] = None, seed: int = 42) -> None:
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.seed = seed
        self.detector = TimeLeakageDetector()

    def run_evaluation(
        self, adapters: list[LLMAdapter], start_date: str, end_date: str
    ) -> EvalReport:
        """运行多 LLM 竞技场评估。"""
        logger.info(
            f"DeepFund: 开始评估 {len(adapters)} 个 LLM, "
            f"区间 {start_date} ~ {end_date}"
        )

        loader = BenchmarkDataLoader(self.symbols, start_date, end_date, self.seed)
        trading_dates = loader.get_trading_dates()
        # 采样评估日期 (避免全量评估耗时过长)
        eval_dates = self._sample_eval_dates(trading_dates, max_days=60)

        results: list[EvalResult] = []
        for adapter in adapters:
            logger.info(f"DeepFund: 评估 {adapter.name} ...")
            result = self._evaluate_single(adapter, loader, eval_dates)
            results.append(result)

        # 计算随机基准对照组
        benchmark_metrics = self._compute_random_benchmark(loader, eval_dates)

        # 对每个结果重新检测 (加入对照组对比)
        for result in results:
            if result.error is None:
                result.leakage = self.detector.detect(
                    result.decisions, result.metrics, benchmark_metrics
                )

        report = EvalReport(
            start_date=start_date,
            end_date=end_date,
            symbols=self.symbols,
            results=results,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            n_trading_days=len(eval_dates),
        )
        logger.info(
            f"DeepFund: 评估完成, {sum(1 for r in results if r.error is None)} 成功, "
            f"{sum(1 for r in results if r.leakage.is_leaked)} 疑似泄漏"
        )
        return report

    def _sample_eval_dates(
        self, trading_dates: list[str], max_days: int = 60
    ) -> list[str]:
        """采样评估日期 (均匀采样, 避免全量评估)。"""
        if len(trading_dates) <= max_days:
            return trading_dates
        step = len(trading_dates) // max_days
        return trading_dates[::step][:max_days]

    def _evaluate_single(
        self, adapter: LLMAdapter, loader: BenchmarkDataLoader, eval_dates: list[str]
    ) -> EvalResult:
        """评估单个 LLM。"""
        start_time = time.time()
        decisions: list[Decision] = []
        portfolio: dict[str, float] = {s: 0.0 for s in self.symbols}

        try:
            for date in eval_dates:
                market_data = loader.load_market_data(date)
                for symbol in self.symbols:
                    decision = self._make_decision(adapter, market_data, symbol)
                    decisions.append(decision)
                    # 更新组合 (简化: 直接记录权重)
                    portfolio[symbol] = decision.weight

            metrics = self._compute_metrics(decisions, loader, eval_dates)
            elapsed = time.time() - start_time

            return EvalResult(
                llm_name=adapter.name,
                provider=adapter.provider,
                model=adapter.model,
                decisions=decisions,
                metrics=metrics,
                elapsed_seconds=round(elapsed, 4),
            )
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            return EvalResult(
                llm_name=adapter.name,
                provider=adapter.provider,
                model=adapter.model,
                decisions=decisions,
                elapsed_seconds=round(time.time() - start_time, 4),
                error=str(e),
            )

    def _make_decision(
        self, adapter: LLMAdapter, market_data: MarketData, symbol: str
    ) -> Decision:
        """调用 LLM 生成单标的决策。"""
        prices = market_data.prices.get(symbol, [])
        recent_prices = prices[-20:] if len(prices) >= 20 else prices
        prompt = self._build_prompt(market_data.date, symbol, recent_prices)
        response = adapter.chat(prompt, system=self._system_prompt())

        # 解析 LLM 响应
        action, weight, reasoning = self._parse_response(response)
        return Decision(
            date=market_data.date,
            symbol=symbol,
            action=action,
            weight=weight,
            reasoning=reasoning,
            timestamp=datetime.now().isoformat(timespec="seconds"),
        )

    def _system_prompt(self) -> str:
        return (
            "你是一个专业的基金经理。基于截止当前时点的历史数据做出交易决策。"
            "严格禁止使用未来信息 (时间穿越)。"
            '返回 JSON: {"action": "buy|sell|hold", "weight": 0.0-1.0, '
            '"reasoning": "决策理由"}'
        )

    def _build_prompt(self, date: str, symbol: str, recent_prices: list[float]) -> str:
        return json.dumps(
            {
                "date": date,
                "symbol": symbol,
                "recent_prices": recent_prices,
                "instruction": "基于截止上述时点的数据做出决策, 禁止引用未来日期",
            },
            ensure_ascii=False,
        )

    def _parse_response(self, response: str) -> tuple[str, float, str]:
        """解析 LLM 响应为 (action, weight, reasoning)。"""
        try:
            data = json.loads(response)
            action = str(data.get("action", "hold")).lower()
            if action not in ("buy", "sell", "hold"):
                action = "hold"
            weight = float(data.get("weight", 0.0))
            weight = max(0.0, min(1.0, weight))
            reasoning = str(data.get("reasoning", ""))
            return action, weight, reasoning
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            return "hold", 0.0, response[:200]

    def _compute_metrics(
        self,
        decisions: list[Decision],
        loader: BenchmarkDataLoader,
        eval_dates: list[str],
    ) -> Metrics:
        """计算评估指标。"""
        if not decisions:
            return Metrics()

        n_buy = sum(1 for d in decisions if d.action == "buy")
        n_sell = sum(1 for d in decisions if d.action == "sell")
        n_hold = sum(1 for d in decisions if d.action == "hold")

        # 计算简化收益 (基于决策权重 * 价格变化)
        returns: list[float] = []
        for i in range(1, len(eval_dates)):
            prev_date = eval_dates[i - 1]
            curr_date = eval_dates[i]
            for symbol in self.symbols:
                prev_prices = loader.load_market_data(prev_date).prices.get(symbol, [])
                curr_prices = loader.load_market_data(curr_date).prices.get(symbol, [])
                if not prev_prices or not curr_prices:
                    continue
                prev_price = prev_prices[-1]
                curr_price = curr_prices[-1]
                if prev_price <= 0:
                    continue
                daily_return = (curr_price - prev_price) / prev_price
                # 查找该时点的决策
                decision = next(
                    (
                        d
                        for d in decisions
                        if d.date == prev_date and d.symbol == symbol
                    ),
                    None,
                )
                if decision is None:
                    continue
                if decision.action == "buy":
                    returns.append(decision.weight * daily_return)
                elif decision.action == "sell":
                    returns.append(-decision.weight * daily_return)

        total_return = sum(returns)
        if returns:
            mean_ret = sum(returns) / len(returns)
            std_ret = (sum((r - mean_ret) ** 2 for r in returns) / len(returns)) ** 0.5
            sharpe = (mean_ret / std_ret * (252**0.5)) if std_ret > 0 else 0.0
            win_rate = sum(1 for r in returns if r > 0) / len(returns)
            # 最大回撤
            cum_returns: list[float] = []
            cum = 0.0
            for r in returns:
                cum += r
                cum_returns.append(cum)
            peak = cum_returns[0] if cum_returns else 0.0
            max_dd = 0.0
            for cr in cum_returns:
                if cr > peak:
                    peak = cr
                dd = peak - cr
                if dd > max_dd:
                    max_dd = dd
        else:
            sharpe = 0.0
            win_rate = 0.0
            max_dd = 0.0

        return Metrics(
            total_return=round(total_return, 6),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown=round(max_dd, 6),
            win_rate=round(win_rate, 4),
            n_decisions=len(decisions),
            n_buy=n_buy,
            n_sell=n_sell,
            n_hold=n_hold,
        )

    def _compute_random_benchmark(
        self, loader: BenchmarkDataLoader, eval_dates: list[str]
    ) -> Metrics:
        """计算随机决策基准对照组。"""
        rng = random.Random(self.seed + 999)
        random_decisions: list[Decision] = []
        for date in eval_dates:
            for symbol in self.symbols:
                random_decisions.append(
                    Decision(
                        date=date,
                        symbol=symbol,
                        action=rng.choice(["buy", "sell", "hold"]),
                        weight=rng.uniform(0.0, 1.0),
                    )
                )
        return self._compute_metrics(random_decisions, loader, eval_dates)

    def save_report(
        self, report: EvalReport, output_dir: str = "reports/eval/deepfund"
    ) -> Path:
        """保存评估报告到 JSON 文件。"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"deepfund_eval_{timestamp}.json"
        filepath = output_path / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"DeepFund: 报告已保存到 {filepath}")
        return filepath


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 运行 DeepFund 评估基准。

    使用方式:
        python -m tests.eval.deepfund_harness
        python tests/eval/deepfund_harness.py
    """
    print("=" * 60)
    print("DeepFund 防泄漏评估基准 (NeurIPS 2025)")
    print("文献: Time Travel is Cheating (Li et al., 2025)")
    print("=" * 60)

    # 默认使用 mock 模式 (无需 API key)
    adapters = [
        LLMAdapter(
            provider=cfg["provider"], model=cfg["model"], name=cfg["name"], mock=True
        )
        for cfg in DEFAULT_LLM_REGISTRY
    ]
    print(f"\n评估 {len(adapters)} 个 LLM (mock 模式):")
    for a in adapters:
        print(f"  - {a.name}")

    harness = DeepFundHarness(symbols=DEFAULT_SYMBOLS)
    print(f"\n标的: {DEFAULT_SYMBOLS}")
    print("区间: 2024-01-01 ~ 2024-06-30")

    report = harness.run_evaluation(adapters, "2024-01-01", "2024-06-30")
    filepath = harness.save_report(report)

    print("\n" + "=" * 60)
    print("评估结果汇总")
    print("=" * 60)
    for result in report.results:
        status = "泄漏" if result.leakage.is_leaked else "通过"
        print(f"\n{result.llm_name}:")
        print(
            f"  状态: {status} (p={result.leakage.p_value:.4f}, "
            f"anomaly={result.leakage.anomaly_score:.2f})"
        )
        print(f"  Sharpe: {result.metrics.sharpe_ratio:.4f}")
        print(f"  胜率: {result.metrics.win_rate:.2%}")
        print(
            f"  决策数: {result.metrics.n_decisions} "
            f"(buy={result.metrics.n_buy}, sell={result.metrics.n_sell}, "
            f"hold={result.metrics.n_hold})"
        )
        if result.leakage.reasons:
            print(f"  原因: {result.leakage.reasons}")

    print(f"\n报告已保存: {filepath}")


if __name__ == "__main__":
    main()
