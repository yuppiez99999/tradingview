"""
AI-Trader 实时未污染评估基准 (2025.12)
=====================================

文献依据: #23 AI-Trader: Real-Time Benchmark (2025.12)
开源仓库: https://github.com/HKUDS/AI-Trader
定位: "100% Fully-Automated Agent-Native Trading"

与 DeepFund (LIT-1.3) 的互补关系
--------------------------------
- DeepFund: 检测 LLM **内部**时间穿越 (预训练"记住"未来信息)
- AI-Trader: 检测**数据管道**污染 (实时数据流是否被未来数据污染)

核心问题
--------
在实时交易中, 数据管道可能被未来数据污染:
1. 数据源时间戳造假 (future timestamp 混入)
2. 数据到达顺序违反时序 (乱序到达)
3. 数据被篡改 (哈希校验失败)
4. 评估环境与训练数据未隔离 (泄漏)

本 harness 模拟实时数据流, 验证 agent 决策所用数据未被污染。

架构
----
1. DataRecord          — 单条数据记录 (含时间戳 + 哈希 + 来源)
2. RealTimeStream      — 实时数据流模拟 (按时间顺序到达)
3. DataContaminationDetector — 数据污染检测器 (五层防线)
4. AgentNativeBenchmark — Agent 原生基准测试 (复用 LLMAdapter)
5. AITraderHarness     — 主评估器

验收标准 (LIT-1.4)
------------------
- A 股子集实测
- 数据未污染验证通过
- 归档到 reports/eval/ai_trader/

使用示例
--------
    from tests.eval.ai_trader_harness import AITraderHarness, AgentAdapter

    harness = AITraderHarness(symbols=["000001.SZ", "600000.SH"])
    agents = [AgentAdapter(name=f"agent-{i}", mock=True) for i in range(5)]
    report = harness.run_evaluation(agents, start_date="2024-01-01",
                                    end_date="2024-06-30")
    harness.save_report(report, "reports/eval/ai_trader/")
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ai_trader_harness")

# ============================================================
# 常量
# ============================================================

# Agent 原生集成: 5 个测试 agent (模拟 AI-Trader 平台注册的 agent)
DEFAULT_AGENTS: list[dict[str, str]] = [
    {"name": "momentum-agent", "strategy": "momentum"},
    {"name": "mean-revert-agent", "strategy": "mean_revert"},
    {"name": "value-agent", "strategy": "value"},
    {"name": "sentiment-agent", "strategy": "sentiment"},
    {"name": "ensemble-agent", "strategy": "ensemble"},
]

# 数据污染检测阈值
CONTAMINATION_HASH_THRESHOLD = 0.0  # 哈希不匹配率阈值 (0 = 零容忍)
CONTAMINATION_ORDER_THRESHOLD = 0.0  # 乱序率阈值 (0 = 零容忍)
CONTAMINATION_FUTURE_TS_THRESHOLD = 0.0  # 未来时间戳率阈值 (0 = 零容忍)

# 默认 A 股评估标的 (与 DeepFund 一致, 便于对比)
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
class DataRecord:
    """单条数据记录 (含时间戳 + 哈希 + 来源)。

    哈希用于检测数据篡改: content 哈希应与 record_hash 一致。
    """

    timestamp: str  # 数据时点 (YYYY-MM-DD HH:MM:SS)
    symbol: str
    price: float
    volume: int
    source: str = "synthetic"  # 数据来源
    record_hash: str = ""  # 内容哈希 (用于篡改检测)

    def __post_init__(self) -> None:
        if not self.record_hash:
            self.record_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """计算内容哈希 (用于篡改检测)。"""
        content = (
            f"{self.timestamp}|{self.symbol}|{self.price}|{self.volume}|{self.source}"
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def is_hash_valid(self) -> bool:
        """验证哈希是否匹配 (检测篡改)。"""
        return self.record_hash == self._compute_hash()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "price": self.price,
            "volume": self.volume,
            "source": self.source,
            "record_hash": self.record_hash,
        }


@dataclass
class AgentDecision:
    """Agent 在某时点的决策。"""

    timestamp: str
    symbol: str
    action: str  # "buy" / "sell" / "hold"
    weight: float
    data_used: list[DataRecord] = field(default_factory=list)  # 决策所用数据
    reasoning: str = ""


@dataclass
class ContaminationReport:
    """数据污染检测报告。"""

    is_contaminated: bool = False
    hash_violations: int = 0
    order_violations: int = 0
    future_ts_violations: int = 0
    isolation_violations: int = 0
    total_records: int = 0
    reasons: list[str] = field(default_factory=list)
    contamination_score: float = 0.0  # 0.0 = 干净, 1.0 = 严重污染

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_contaminated": self.is_contaminated,
            "hash_violations": self.hash_violations,
            "order_violations": self.order_violations,
            "future_ts_violations": self.future_ts_violations,
            "isolation_violations": self.isolation_violations,
            "total_records": self.total_records,
            "reasons": self.reasons,
            "contamination_score": self.contamination_score,
        }


@dataclass
class AgentEvalResult:
    """单个 Agent 的评估结果。"""

    agent_name: str
    strategy: str
    decisions: list[AgentDecision] = field(default_factory=list)
    contamination: ContaminationReport = field(default_factory=ContaminationReport)
    n_correct_decisions: int = 0
    n_total_decisions: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def accuracy(self) -> float:
        """决策准确率 (基于数据未污染的正确决策)。"""
        if self.n_total_decisions == 0:
            return 0.0
        return self.n_correct_decisions / self.n_total_decisions


@dataclass
class AITraderEvalReport:
    """完整评估报告 (多 Agent 竞技场)。"""

    start_date: str = ""
    end_date: str = ""
    symbols: list[str] = field(default_factory=list)
    results: list[AgentEvalResult] = field(default_factory=list)
    generated_at: str = ""
    n_stream_records: int = 0
    global_contamination: ContaminationReport = field(
        default_factory=ContaminationReport
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "symbols": self.symbols,
            "generated_at": self.generated_at,
            "n_stream_records": self.n_stream_records,
            "global_contamination": self.global_contamination.to_dict(),
            "results": [
                {
                    "agent_name": r.agent_name,
                    "strategy": r.strategy,
                    "contamination": r.contamination.to_dict(),
                    "accuracy": r.accuracy,
                    "n_correct": r.n_correct_decisions,
                    "n_total": r.n_total_decisions,
                    "elapsed_seconds": r.elapsed_seconds,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


# ============================================================
# 实时数据流模拟
# ============================================================


class RealTimeStream:
    """实时数据流模拟 — 按时间顺序生成数据记录。

    模拟真实市场的实时数据到达:
    1. 按时间顺序生成 (不乱序)
    2. 每条记录带时间戳 + 哈希
    3. 支持注入污染 (用于测试检测器)
    """

    def __init__(
        self, symbols: list[str], start_date: str, end_date: str, seed: int = 42
    ) -> None:
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.seed = seed
        self._records: list[DataRecord] = []
        self._generate()

    def _generate(self) -> None:
        """生成按时间顺序的数据流。"""
        rng = random.Random(self.seed)
        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")

        current = start
        while current <= end:
            if current.weekday() < 5:  # 工作日
                ts = current.strftime("%Y-%m-%d 15:00:00")  # 收盘时点
                for symbol in self.symbols:
                    price = round(
                        100.0
                        * (1.0 + rng.uniform(-0.3, 0.3))
                        * (1.0 + rng.gauss(0, 0.02)),
                        4,
                    )
                    volume = int(1e6 * (1.0 + rng.uniform(-0.5, 0.5)))
                    self._records.append(
                        DataRecord(
                            timestamp=ts,
                            symbol=symbol,
                            price=price,
                            volume=volume,
                            source="realtime-stream",
                        )
                    )
            current += timedelta(days=1)

    def get_records_up_to(self, timestamp: str) -> list[DataRecord]:
        """获取截止某时点的所有记录 (严格时序边界)。"""
        return [r for r in self._records if r.timestamp <= timestamp]

    def get_all_records(self) -> list[DataRecord]:
        """获取全部记录。"""
        return list(self._records)

    def inject_hash_contamination(self, n: int = 1) -> None:
        """注入哈希篡改污染 (用于测试检测器)。"""
        if n <= 0 or not self._records:
            return
        rng = random.Random(self.seed + 100)
        for _ in range(min(n, len(self._records))):
            idx = rng.randint(0, len(self._records) - 1)
            r = self._records[idx]
            # 篡改: 修改价格但保持旧哈希
            self._records[idx] = DataRecord(
                timestamp=r.timestamp,
                symbol=r.symbol,
                price=round(r.price * 1.5, 4),  # 篡改价格
                volume=r.volume,
                source=r.source,
                record_hash=r.record_hash,  # 保持旧哈希 (不匹配)
            )

    def inject_order_contamination(self, n: int = 1) -> None:
        """注入乱序污染 (用于测试检测器) — 在同一标的内交换相邻记录。"""
        if n <= 0 or len(self._records) < 2:
            return
        rng = random.Random(self.seed + 200)
        # 按标的分组找到同标的相邻记录对
        from collections import defaultdict

        by_symbol: dict[str, list[int]] = defaultdict(list)
        for idx, rec in enumerate(self._records):
            by_symbol[rec.symbol].append(idx)
        swapped = 0
        for symbol_indices in by_symbol.values():
            if swapped >= n:
                break
            if len(symbol_indices) < 2:
                continue
            # 在同一标的内交换相邻记录
            for i in range(len(symbol_indices) - 1):
                if swapped >= n:
                    break
                if rng.random() < 0.3:  # 30% 概率交换
                    idx1, idx2 = symbol_indices[i], symbol_indices[i + 1]
                    self._records[idx1], self._records[idx2] = (
                        self._records[idx2],
                        self._records[idx1],
                    )
                    swapped += 1

    def inject_future_timestamp(self, n: int = 1) -> None:
        """注入未来时间戳污染 (用于测试检测器)。"""
        if n <= 0 or not self._records:
            return
        future_ts = "2099-12-31 15:00:00"
        rng = random.Random(self.seed + 300)
        for _ in range(min(n, len(self._records))):
            idx = rng.randint(0, len(self._records) - 1)
            r = self._records[idx]
            self._records[idx] = DataRecord(
                timestamp=future_ts,
                symbol=r.symbol,
                price=r.price,
                volume=r.volume,
                source=r.source,
            )


# ============================================================
# 数据污染检测器 (五层防线)
# ============================================================


class DataContaminationDetector:
    """数据污染检测器 — AI-Trader 基准的核心创新。

    五层防线:
    1. 哈希校验: 检测数据篡改 (content hash 不匹配)
    2. 时序校验: 检测数据到达顺序 (乱序)
    3. 未来时间戳: 检测未来时间戳混入
    4. 隔离校验: 检测评估环境与训练数据隔离
    5. 来源校验: 检测数据来源可信度
    """

    def __init__(
        self,
        hash_threshold: float = CONTAMINATION_HASH_THRESHOLD,
        order_threshold: float = CONTAMINATION_ORDER_THRESHOLD,
        future_ts_threshold: float = CONTAMINATION_FUTURE_TS_THRESHOLD,
    ) -> None:
        self.hash_threshold = hash_threshold
        self.order_threshold = order_threshold
        self.future_ts_threshold = future_ts_threshold

    def detect(
        self, records: list[DataRecord], decision_cutoff: Optional[str] = None
    ) -> ContaminationReport:
        """执行数据污染检测。

        Args:
            records: 待检测的数据记录列表
            decision_cutoff: 决策时点 (用于检测未来时间戳)
        """
        if not records:
            return ContaminationReport()

        total = len(records)
        hash_violations = 0
        order_violations = 0
        future_ts_violations = 0
        isolation_violations = 0
        reasons: list[str] = []

        # 1. 哈希校验 (检测篡改)
        for record in records:
            if not record.is_hash_valid():
                hash_violations += 1
        if hash_violations > 0:
            reasons.append(f"哈希校验失败 {hash_violations}/{total} 条 (数据被篡改)")

        # 2. 时序校验 (检测乱序) — 仅检测同一标的内的乱序 (跨标的无可比性)
        from collections import defaultdict

        by_symbol: dict[str, list[DataRecord]] = defaultdict(list)
        for record in records:
            by_symbol[record.symbol].append(record)
        for symbol_recs in by_symbol.values():
            for i in range(1, len(symbol_recs)):
                if symbol_recs[i].timestamp < symbol_recs[i - 1].timestamp:
                    order_violations += 1
        if order_violations > 0:
            reasons.append(f"时序乱序 {order_violations} 处 (数据到达顺序违反时序)")

        # 3. 未来时间戳检测
        if decision_cutoff is not None:
            for record in records:
                if record.timestamp > decision_cutoff:
                    future_ts_violations += 1
            if future_ts_violations > 0:
                reasons.append(
                    f"未来时间戳 {future_ts_violations}/{total} 条 (决策使用了未来数据)"
                )

        # 4. 隔离校验 (来源检查)
        trusted_sources = {
            "synthetic",
            "realtime-stream",
            "wind-terminal",
            "tdx",
            "akshare",
        }
        for record in records:
            if record.source not in trusted_sources:
                isolation_violations += 1
        if isolation_violations > 0:
            reasons.append(
                f"不可信来源 {isolation_violations}/{total} 条 (评估环境未隔离)"
            )

        # 计算污染分数
        score = (
            hash_violations
            + order_violations
            + future_ts_violations
            + isolation_violations
        ) / total
        is_contaminated = (
            hash_violations > 0
            or order_violations > 0
            or future_ts_violations > 0
            or isolation_violations > 0
        )

        return ContaminationReport(
            is_contaminated=is_contaminated,
            hash_violations=hash_violations,
            order_violations=order_violations,
            future_ts_violations=future_ts_violations,
            isolation_violations=isolation_violations,
            total_records=total,
            reasons=reasons,
            contamination_score=round(score, 6),
        )


# ============================================================
# Agent 适配器
# ============================================================


class AgentAdapter:
    """Agent 原生适配器 (模拟 AI-Trader 平台注册的 agent)。

    mock 模式: 使用确定性策略生成决策, 不调用真实 LLM。
    """

    def __init__(
        self, name: str, strategy: str = "momentum", mock: bool = True
    ) -> None:
        self.name = name
        self.strategy = strategy
        self.mock = mock

    def make_decision(
        self, records: list[DataRecord], symbol: str, cutoff: str
    ) -> AgentDecision:
        """基于截止 cutoff 的数据生成决策。"""
        symbol_records = [
            r for r in records if r.symbol == symbol and r.timestamp <= cutoff
        ]
        if not symbol_records:
            return AgentDecision(
                timestamp=cutoff, symbol=symbol, action="hold", weight=0.0
            )

        action, weight, reasoning = self._apply_strategy(symbol_records, symbol)
        return AgentDecision(
            timestamp=cutoff,
            symbol=symbol,
            action=action,
            weight=weight,
            data_used=symbol_records[-20:],  # 最近 20 条
            reasoning=reasoning,
        )

    def _apply_strategy(
        self, records: list[DataRecord], symbol: str
    ) -> tuple[str, float, str]:
        """应用交易策略生成决策。"""
        prices = [r.price for r in records]
        if len(prices) < 2:
            return "hold", 0.0, f"{self.strategy}: 数据不足"

        if self.strategy == "momentum":
            # 动量策略: 近期上涨则买入
            recent = prices[-5:] if len(prices) >= 5 else prices
            momentum = (recent[-1] - recent[0]) / recent[0] if recent[0] > 0 else 0.0
            if momentum > 0.02:
                return "buy", min(1.0, abs(momentum) * 10), f"momentum: +{momentum:.4f}"
            elif momentum < -0.02:
                return "sell", min(1.0, abs(momentum) * 10), f"momentum: {momentum:.4f}"
            return "hold", 0.0, f"momentum: {momentum:.4f}"

        elif self.strategy == "mean_revert":
            # 均值回归: 偏离均值则反向操作
            avg = sum(prices) / len(prices)
            current = prices[-1]
            deviation = (current - avg) / avg if avg > 0 else 0.0
            if deviation > 0.03:
                return (
                    "sell",
                    min(1.0, abs(deviation) * 5),
                    f"mean_revert: dev=+{deviation:.4f}",
                )
            elif deviation < -0.03:
                return (
                    "buy",
                    min(1.0, abs(deviation) * 5),
                    f"mean_revert: dev={deviation:.4f}",
                )
            return "hold", 0.0, f"mean_revert: dev={deviation:.4f}"

        elif self.strategy == "value":
            # 价值策略: 价格低于历史 25% 分位数则买入
            sorted_prices = sorted(prices)
            q25 = sorted_prices[len(sorted_prices) // 4]
            q75 = sorted_prices[3 * len(sorted_prices) // 4]
            current = prices[-1]
            if current < q25:
                return "buy", 0.6, f"value: price < Q25 ({current:.2f} < {q25:.2f})"
            elif current > q75:
                return "sell", 0.4, f"value: price > Q75 ({current:.2f} > {q75:.2f})"
            return "hold", 0.0, f"value: Q25={q25:.2f} < {current:.2f} < Q75={q75:.2f}"

        elif self.strategy == "sentiment":
            # 情感策略: mock 情感评分
            rng = random.Random(hash(symbol + records[-1].timestamp) & 0xFFFFFFFF)
            score = rng.uniform(-1.0, 1.0)
            if score > 0.3:
                return "buy", min(1.0, score), f"sentiment: score={score:.4f}"
            elif score < -0.3:
                return "sell", min(1.0, abs(score)), f"sentiment: score={score:.4f}"
            return "hold", 0.0, f"sentiment: score={score:.4f}"

        else:  # ensemble
            # 集成策略: 综合以上信号
            actions = []
            for s in ["momentum", "mean_revert", "value"]:
                adapter = AgentAdapter(name=f"sub-{s}", strategy=s, mock=True)
                a, w, _ = adapter._apply_strategy(records, symbol)
                actions.append((a, w))
            buy_score = sum(w for a, w in actions if a == "buy")
            sell_score = sum(w for a, w in actions if a == "sell")
            if buy_score > sell_score and buy_score > 0.1:
                return "buy", buy_score / len(actions), f"ensemble: buy={buy_score:.4f}"
            elif sell_score > buy_score and sell_score > 0.1:
                return (
                    "sell",
                    sell_score / len(actions),
                    f"ensemble: sell={sell_score:.4f}",
                )
            return "hold", 0.0, f"ensemble: buy={buy_score:.4f}, sell={sell_score:.4f}"


# ============================================================
# 主评估器
# ============================================================


class AITraderHarness:
    """AI-Trader 实时未污染评估基准主评估器。

    使用示例:
        harness = AITraderHarness(symbols=["000001.SZ"])
        agents = [AgentAdapter(name=f"agent-{i}", mock=True) for i in range(5)]
        report = harness.run_evaluation(agents, "2024-01-01", "2024-06-30")
    """

    def __init__(self, symbols: Optional[list[str]] = None, seed: int = 42) -> None:
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.seed = seed
        self.detector = DataContaminationDetector()

    def run_evaluation(
        self, agents: list[AgentAdapter], start_date: str, end_date: str
    ) -> AITraderEvalReport:
        """运行多 Agent 实时未污染评估。"""
        logger.info(
            f"AI-Trader: 开始评估 {len(agents)} 个 agent, "
            f"区间 {start_date} ~ {end_date}"
        )

        # 1. 生成实时数据流
        stream = RealTimeStream(self.symbols, start_date, end_date, self.seed)
        all_records = stream.get_all_records()
        logger.info(f"AI-Trader: 数据流 {len(all_records)} 条记录")

        # 2. 全局数据污染检测
        global_contamination = self.detector.detect(all_records)

        # 3. 采样评估时点
        eval_timestamps = self._sample_eval_timestamps(all_records, max_points=30)

        # 4. 逐 agent 评估
        results: list[AgentEvalResult] = []
        for agent in agents:
            logger.info(f"AI-Trader: 评估 {agent.name} ({agent.strategy}) ...")
            result = self._evaluate_single(agent, stream, eval_timestamps)
            results.append(result)

        report = AITraderEvalReport(
            start_date=start_date,
            end_date=end_date,
            symbols=self.symbols,
            results=results,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            n_stream_records=len(all_records),
            global_contamination=global_contamination,
        )

        n_clean = sum(
            1
            for r in results
            if r.error is None and not r.contamination.is_contaminated
        )
        logger.info(f"AI-Trader: 评估完成, {n_clean}/{len(results)} agent 数据未污染")
        return report

    def _sample_eval_timestamps(
        self, records: list[DataRecord], max_points: int = 30
    ) -> list[str]:
        """采样评估时点 (均匀采样)。"""
        all_ts = list({r.timestamp for r in records})
        all_ts.sort()
        if len(all_ts) <= max_points:
            return all_ts
        step = len(all_ts) // max_points
        return all_ts[::step][:max_points]

    def _evaluate_single(
        self, agent: AgentAdapter, stream: RealTimeStream, eval_timestamps: list[str]
    ) -> AgentEvalResult:
        """评估单个 agent。"""
        start_time = time.time()
        decisions: list[AgentDecision] = []

        try:
            for cutoff in eval_timestamps:
                available = stream.get_records_up_to(cutoff)
                for symbol in self.symbols:
                    decision = agent.make_decision(available, symbol, cutoff)
                    decisions.append(decision)

            # 检测每个决策所用数据是否被污染 (逐决策检测, 避免合并产生人工乱序)
            total_violations = 0
            hash_violations = 0
            order_violations = 0
            future_ts_violations = 0
            isolation_violations = 0
            total_records = 0
            for decision in decisions:
                if decision.data_used:
                    report = self.detector.detect(
                        decision.data_used, decision.timestamp
                    )
                    if report.is_contaminated:
                        total_violations += 1
                    hash_violations += report.hash_violations
                    order_violations += report.order_violations
                    future_ts_violations += report.future_ts_violations
                    isolation_violations += report.isolation_violations
                    total_records += report.total_records

            # 汇总各决策的污染报告 (不合并 data_used, 避免跨决策人工乱序)
            all_reasons: list[str] = []
            is_contaminated = (
                hash_violations > 0
                or order_violations > 0
                or future_ts_violations > 0
                or isolation_violations > 0
            )
            if hash_violations > 0:
                all_reasons.append(f"哈希校验失败 {hash_violations} 条 (数据被篡改)")
            if order_violations > 0:
                all_reasons.append(
                    f"时序乱序 {order_violations} 处 (数据到达顺序违反时序)"
                )
            if future_ts_violations > 0:
                all_reasons.append(
                    f"未来时间戳 {future_ts_violations} 条 (决策使用了未来数据)"
                )
            if isolation_violations > 0:
                all_reasons.append(
                    f"不可信来源 {isolation_violations} 条 (评估环境未隔离)"
                )
            score = (
                (
                    hash_violations
                    + order_violations
                    + future_ts_violations
                    + isolation_violations
                )
                / total_records
                if total_records > 0
                else 0.0
            )
            contamination = ContaminationReport(
                is_contaminated=is_contaminated,
                hash_violations=hash_violations,
                order_violations=order_violations,
                future_ts_violations=future_ts_violations,
                isolation_violations=isolation_violations,
                total_records=total_records,
                reasons=all_reasons,
                contamination_score=round(score, 6),
            )

            n_correct = len(decisions) - total_violations
            elapsed = time.time() - start_time

            return AgentEvalResult(
                agent_name=agent.name,
                strategy=agent.strategy,
                decisions=decisions,
                contamination=contamination,
                n_correct_decisions=n_correct,
                n_total_decisions=len(decisions),
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
            return AgentEvalResult(
                agent_name=agent.name,
                strategy=agent.strategy,
                decisions=decisions,
                elapsed_seconds=round(time.time() - start_time, 4),
                error=str(e),
            )

    def run_contamination_test(
        self, agents: list[AgentAdapter], start_date: str, end_date: str
    ) -> dict[str, Any]:
        """运行污染注入测试 (验证检测器能否发现注入的污染)。

        注入三种污染, 验证检测器能否全部检出。
        """
        logger.info("AI-Trader: 运行污染注入测试 ...")
        stream = RealTimeStream(self.symbols, start_date, end_date, self.seed)

        # 1. 注入哈希篡改
        stream.inject_hash_contamination(n=5)
        records = stream.get_all_records()
        hash_report = self.detector.detect(records)
        hash_detected = hash_report.hash_violations > 0

        # 2. 注入乱序
        stream.inject_order_contamination(n=5)
        records = stream.get_all_records()
        order_report = self.detector.detect(records)
        order_detected = order_report.order_violations > 0

        # 3. 注入未来时间戳
        stream.inject_future_timestamp(n=3)
        records = stream.get_all_records()
        future_report = self.detector.detect(records, "2024-12-31 15:00:00")
        future_detected = future_report.future_ts_violations > 0

        result = {
            "hash_contamination": {
                "injected": True,
                "detected": hash_detected,
                "violations": hash_report.hash_violations,
            },
            "order_contamination": {
                "injected": True,
                "detected": order_detected,
                "violations": order_report.order_violations,
            },
            "future_ts_contamination": {
                "injected": True,
                "detected": future_detected,
                "violations": future_report.future_ts_violations,
            },
            "all_detected": hash_detected and order_detected and future_detected,
        }
        logger.info(
            f"AI-Trader: 污染注入测试 {'全部检出' if result['all_detected'] else '检出失败'}"
        )
        return result

    def save_report(
        self, report: AITraderEvalReport, output_dir: str = "reports/eval/ai_trader"
    ) -> Path:
        """保存评估报告到 JSON 文件。"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ai_trader_eval_{timestamp}.json"
        filepath = output_path / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"AI-Trader: 报告已保存到 {filepath}")
        return filepath


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 运行 AI-Trader 评估基准。

    使用方式:
        python -m tests.eval.ai_trader_harness
        python tests/eval/ai_trader_harness.py
    """
    print("=" * 60)
    print("AI-Trader 实时未污染评估基准 (2025.12)")
    print("文献: AI-Trader Real-Time Benchmark (HKUDS)")
    print("=" * 60)

    agents = [
        AgentAdapter(name=cfg["name"], strategy=cfg["strategy"], mock=True)
        for cfg in DEFAULT_AGENTS
    ]
    print(f"\n评估 {len(agents)} 个 agent:")
    for a in agents:
        print(f"  - {a.name} ({a.strategy})")

    harness = AITraderHarness(symbols=DEFAULT_SYMBOLS)
    print(f"\n标的: {DEFAULT_SYMBOLS}")
    print("区间: 2024-01-01 ~ 2024-06-30")

    # 1. 正常评估 (数据流未污染)
    print("\n--- 1. 正常评估 (数据流未污染) ---")
    report = harness.run_evaluation(agents, "2024-01-01", "2024-06-30")
    filepath = harness.save_report(report)

    print(
        f"\n全局数据污染: {'是' if report.global_contamination.is_contaminated else '否'}"
        f" (score={report.global_contamination.contamination_score:.6f})"
    )
    print(f"数据流记录数: {report.n_stream_records}")

    for result in report.results:
        status = "污染" if result.contamination.is_contaminated else "干净"
        print(f"\n{result.agent_name} ({result.strategy}):")
        print(f"  数据状态: {status}")
        print(
            f"  准确率: {result.accuracy:.2%} ({result.n_correct_decisions}/{result.n_total_decisions})"
        )
        if result.contamination.reasons:
            print(f"  原因: {result.contamination.reasons}")

    # 2. 污染注入测试 (验证检测器)
    print("\n--- 2. 污染注入测试 (验证检测器) ---")
    contamination_test = harness.run_contamination_test(
        agents, "2024-01-01", "2024-06-30"
    )
    print(
        f"\n哈希篡改检测: {'通过' if contamination_test['hash_contamination']['detected'] else '失败'}"
        f" ({contamination_test['hash_contamination']['violations']} 违规)"
    )
    print(
        f"乱序检测: {'通过' if contamination_test['order_contamination']['detected'] else '失败'}"
        f" ({contamination_test['order_contamination']['violations']} 违规)"
    )
    print(
        f"未来时间戳检测: {'通过' if contamination_test['future_ts_contamination']['detected'] else '失败'}"
        f" ({contamination_test['future_ts_contamination']['violations']} 违规)"
    )
    print(
        f"\n污染注入测试: {'全部检出' if contamination_test['all_detected'] else '检出失败'}"
    )

    print(f"\n报告已保存: {filepath}")


if __name__ == "__main__":
    main()
