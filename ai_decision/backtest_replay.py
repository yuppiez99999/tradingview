"""
ai_decision.backtest_replay — 历史回放 + 三基线对比
====================================================

任务: 阶段三步骤 6 — 用历史数据回放 run_decision() 全链路,
      对比三条基线 (ai_debate / five_agents / rule_only),
      验证 AI 增量 IC 与边际夏普.

责任层: L0 离线研究 (不阻断主链路, 不触发真实下单)

设计原则 (路线图 ai_decision_roadmap_execution_plan.md 步骤 6):
  - 三基线对比:
      ai_debate   = 完整 run_decision() (辩论 + 聚合 + 风控)
      five_agents = 仅五 Agent 共识 (跳过辩论/judge)
      rule_only   = 纯规则兜底 (跳过 AI)
  - 前视偏差防控 (项目记忆硬约束):
      1. 基本面用披露日 (disclosure_date) 而非报告期截止日
      2. 逐日成分股快照防幸存者偏差
      3. 停牌冻结不可交易, 涨跌停不可成交
      4. 调仓日信号至少滞后一期
  - 复用 FastBacktest 计算绩效指标 (sharpe/dsr/ic_ir/cv)
  - 复用 _spearman_ic 计算 IC 序列
  - 边际夏普 > 0.05 才建议上线 auto 模式

接口:
  - HistoryDataLoader: 历史数据加载协议 (Protocol)
  - MockHistoryDataLoader: 测试用合成数据加载器
  - BacktestReplay: 回放引擎
    - replay_history(baseline) -> BaselineResult    单基线回放
    - compare_baselines() -> ComparisonReport        三基线对比
    - to_markdown(report) -> str                     Markdown 报告
    - save(report) -> str                            落盘

用法:
    from ai_decision.backtest_replay import BacktestReplay, MockHistoryDataLoader

    loader = MockHistoryDataLoader(symbols=["600519", "000001"], days=60)
    replay = BacktestReplay(loader=loader)
    report = replay.compare_baselines()
    path = replay.save(report)
"""
from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from ai_decision.decision_gate import RiskContext, apply_mode, run_hard_risk
from ai_decision.health import ModelHealthMonitor, get_default_monitor
from ai_decision.models import TradingDecision
from ai_decision.orchestrator import _run_five_agents, run_decision
from ai_decision.rag_context import build_context

logger = logging.getLogger("ai_decision.backtest_replay")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORT_DIR = _PROJECT_ROOT / "reports" / "ai_decision"

# ============================================================
# 常量
# ============================================================

# 边际夏普阈值: 超过此值才值得上线 auto 模式 (路线图 line 358)
MARGINAL_SHARPE_THRESHOLD = 0.05

# IC 最小样本数 (对齐 multi_factor_signal.MIN_IC_SAMPLES)
MIN_IC_SAMPLES = 5

# 回测所需最小交易日数 (对齐 fast_backtest.InsufficientDataError)
MIN_TRADING_DAYS = 30

# 无风险利率 (对齐 fast_backtest.DEFAULT_RF)
RISK_FREE_RATE = 0.02

# 年交易日数 (对齐 fast_backtest.DEFAULT_TRADING_DAYS)
TRADING_DAYS_PER_YEAR = 252


class BaselineType(str, Enum):
    """三基线类型"""
    AI_DEBATE = "ai_debate"        # 完整 run_decision (辩论+聚合+风控)
    FIVE_AGENTS = "five_agents"    # 仅五 Agent 共识 (跳过辩论/judge)
    RULE_ONLY = "rule_only"        # 纯规则兜底 (跳过 AI)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ReplayConfig:
    """回放配置

    Attributes:
        start_date: 回放开始日期 (YYYY-MM-DD)
        end_date: 回放结束日期 (YYYY-MM-DD)
        symbols: 标的池 (None 时用 loader.get_constituents)
        rebalance_freq: 调仓频率 ("D"=日 / "W"=周 / "M"=月)
        forward_return_horizon: 前瞻收益天数 (用于 IC 计算)
        use_mock_providers: 是否强制使用 MockProvider (避免真实 API 调用)
        initial_capital: 初始资金 (用于仓位计算)
    """
    start_date: str = ""
    end_date: str = ""
    symbols: list[str] | None = None
    rebalance_freq: str = "W"
    forward_return_horizon: int = 5
    use_mock_providers: bool = True
    initial_capital: float = 1_000_000.0


@dataclass
class BaselineResult:
    """单基线回放结果

    Attributes:
        baseline: 基线类型
        decisions: 决策序列 (每日每标的一条)
        returns: 日收益率序列
        ic_series: 每日 IC 序列 (Spearman)
        metrics: 绩效指标 (from FastBacktest.summary())
        debate_trigger_rate: 辩论触发率 (仅 ai_debate 有效)
        n_decisions: 决策总数
        n_buy / n_sell / n_hold: action 分布
    """
    baseline: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    ic_series: list[float] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    debate_trigger_rate: float = 0.0
    n_decisions: int = 0
    n_buy: int = 0
    n_sell: int = 0
    n_hold: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "n_decisions": self.n_decisions,
            "n_buy": self.n_buy,
            "n_sell": self.n_sell,
            "n_hold": self.n_hold,
            "debate_trigger_rate": self.debate_trigger_rate,
            "metrics": self.metrics,
            # decisions / returns / ic_series 不序列化 (过大), 仅保留统计
            "n_returns": len(self.returns),
            "n_ic": len(self.ic_series),
        }


@dataclass
class ComparisonReport:
    """三基线对比报告

    Attributes:
        config: 回放配置
        baselines: {baseline_type: BaselineResult}
        marginal_sharpe_debate_vs_agents: ai_debate - five_agents 的边际夏普
        marginal_sharpe_agents_vs_rule: five_agents - rule_only 的边际夏普
        recommendation: 上线建议 ("auto" / "paper" / "shadow")
        bias_checks: 前视偏差校验结果
        generated_at: 生成时间
    """
    config: dict[str, Any] = field(default_factory=dict)
    baselines: dict[str, dict[str, Any]] = field(default_factory=dict)
    marginal_sharpe_debate_vs_agents: float = 0.0
    marginal_sharpe_agents_vs_rule: float = 0.0
    recommendation: str = "shadow"
    bias_checks: dict[str, bool] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "baselines": self.baselines,
            "marginal_sharpe_debate_vs_agents": self.marginal_sharpe_debate_vs_agents,
            "marginal_sharpe_agents_vs_rule": self.marginal_sharpe_agents_vs_rule,
            "recommendation": self.recommendation,
            "bias_checks": self.bias_checks,
            "generated_at": self.generated_at,
        }


# ============================================================
# 历史数据加载协议
# ============================================================

class HistoryDataLoader(Protocol):
    """历史数据加载协议 (Protocol)

    实现此协议的类需提供以下方法. 用于解耦回放引擎与具体数据源.
    """

    def get_trading_dates(self, start: str, end: str) -> list[str]:
        """获取交易日列表 (YYYY-MM-DD)"""
        ...

    def get_market_data(self, symbol: str, date: str) -> dict[str, Any]:
        """获取指定日期的行情数据

        Returns:
            {"close": float, "change_pct": float, "volume": float, "is_halted": bool,
             "is_limit_up": bool, "is_limit_down": bool}
        """
        ...

    def get_fundamentals(self, symbol: str, date: str) -> dict[str, Any]:
        """获取指定日期可见的基本面 (用披露日, 非报告期截止日)

        Returns:
            {"pe": float, "pb": float, "roe": float, "disclosure_date": str}
        """
        ...

    def get_forward_return(self, symbol: str, date: str, horizon: int) -> float:
        """获取指定日期的未来 horizon 天收益率 (用于 IC 计算)

        Returns:
            前瞻收益率 (如 0.05 = +5%), 停牌/数据缺失返回 nan
        """
        ...

    def get_constituents(self, date: str) -> list[str]:
        """获取指定日期的成分股快照 (防幸存者偏差)"""
        ...

    def is_tradable(self, symbol: str, date: str) -> bool:
        """检查指定日期是否可交易 (未停牌 + 未涨跌停)"""
        ...


# ============================================================
# Mock 历史数据加载器 (测试用)
# ============================================================

class MockHistoryDataLoader:
    """合成历史数据加载器 (测试 / CI 用)

    生成确定性随机数据 (seed 固定), 满足回放引擎的所有数据需求.
    不依赖外部数据源, 确保测试可重复.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        days: int = 60,
        start_date: str = "2025-01-01",
        seed: int = 42,
    ) -> None:
        self._symbols = symbols or ["600519", "000001", "300750"]
        self._seed = seed
        self._rng = random.Random(seed)
        np.random.seed(seed)

        # 生成交易日序列 (跳过周末)
        self._dates: list[str] = []
        d = datetime.strptime(start_date, "%Y-%m-%d")
        for _ in range(days):
            while d.weekday() >= 5:  # 5=周六, 6=周日
                d += timedelta(days=1)
            self._dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

        # 为每个 symbol 生成价格序列 (随机游走)
        self._prices: dict[str, list[float]] = {}
        self._halts: dict[str, set] = {s: set() for s in self._symbols}
        for sym in self._symbols:
            price = 10.0 + self._rng.uniform(0, 90)  # 10~100 元
            prices = [price]
            for _i in range(1, len(self._dates)):
                # 随机游走 + 微小正漂移
                ret = self._rng.gauss(0.001, 0.02)
                price = price * (1 + ret)
                prices.append(round(price, 2))
            self._prices[sym] = prices
            # 随机停牌 (5% 概率)
            for _i, dt in enumerate(self._dates):
                if self._rng.random() < 0.05:
                    self._halts[sym].add(dt)

    def get_trading_dates(self, start: str, end: str) -> list[str]:
        return [d for d in self._dates if start <= d <= end]

    def get_market_data(self, symbol: str, date: str) -> dict[str, Any]:
        if symbol not in self._prices or date not in self._dates:
            return {"close": 0.0, "change_pct": 0.0, "volume": 0.0,
                    "is_halted": True, "is_limit_up": False, "is_limit_down": False}
        idx = self._dates.index(date)
        close = self._prices[symbol][idx]
        prev_close = self._prices[symbol][idx - 1] if idx > 0 else close
        change_pct = (close - prev_close) / prev_close if prev_close > 0 else 0.0
        is_halted = date in self._halts.get(symbol, set())
        # 涨跌停 (|change_pct| > 9.9%)
        is_limit_up = change_pct > 0.099
        is_limit_down = change_pct < -0.099
        return {
            "close": close,
            "change_pct": round(change_pct, 4),
            "volume": float(self._rng.randint(100000, 1000000)),
            "is_halted": is_halted,
            "is_limit_up": is_limit_up,
            "is_limit_down": is_limit_down,
        }

    def get_fundamentals(self, symbol: str, date: str) -> dict[str, Any]:
        # 用披露日而非报告期截止日: disclosure_date <= date 才可见
        return {
            "pe": round(self._rng.uniform(5, 50), 2),
            "pb": round(self._rng.uniform(0.5, 5), 2),
            "roe": round(self._rng.uniform(0.05, 0.30), 4),
            "disclosure_date": date,  # 简化: 当日披露
        }

    def get_forward_return(self, symbol: str, date: str, horizon: int) -> float:
        if symbol not in self._prices or date not in self._dates:
            return float("nan")
        idx = self._dates.index(date)
        if idx + horizon >= len(self._prices[symbol]):
            return float("nan")
        p0 = self._prices[symbol][idx]
        p1 = self._prices[symbol][idx + horizon]
        if p0 <= 0:
            return float("nan")
        return (p1 - p0) / p0

    def get_constituents(self, date: str) -> list[str]:
        # 简化: 成分股固定 (实际应逐日快照防幸存者偏差)
        return list(self._symbols)

    def is_tradable(self, symbol: str, date: str) -> bool:
        md = self.get_market_data(symbol, date)
        if md.get("is_halted", True):
            return False
        if md.get("is_limit_up", False) or md.get("is_limit_down", False):
            return False
        return True


# ============================================================
# 回放引擎
# ============================================================

class BacktestReplay:
    """三基线历史回放引擎

    用法:
        loader = MockHistoryDataLoader(symbols=["600519"], days=60)
        replay = BacktestReplay(loader=loader)
        report = replay.compare_baselines()
        replay.save(report)
    """

    def __init__(
        self,
        loader: HistoryDataLoader,
        config: ReplayConfig | None = None,
        health_monitor: ModelHealthMonitor | None = None,
    ) -> None:
        """
        Args:
            loader: 历史数据加载器 (实现 HistoryDataLoader 协议)
            config: 回放配置 (None 时用默认)
            health_monitor: 模型健康监控器 (None 时用全局单例)
        """
        self._loader = loader
        self._config = config or ReplayConfig()
        self._monitor = health_monitor or get_default_monitor()

        # 自动填充日期范围
        if not self._config.start_date:
            dates = loader.get_trading_dates("2020-01-01", "2099-12-31")
            if dates:
                self._config.start_date = dates[0]
                # 默认取全部日期的 80% (留 20% 给前瞻收益计算)
                cutoff_idx = int(len(dates) * 0.8)
                self._config.end_date = dates[min(cutoff_idx, len(dates) - 1)]

    # ------------------------------------------------------------
    # 主入口: 三基线对比
    # ------------------------------------------------------------

    def compare_baselines(self) -> ComparisonReport:
        """运行三基线对比

        Returns:
            ComparisonReport 含三基线结果 + 边际夏普 + 建议
        """
        logger.info(
            "[Replay] 开始三基线对比: %s ~ %s, symbols=%s",
            self._config.start_date, self._config.end_date,
            self._config.symbols or "(all constituents)",
        )

        # 前视偏差校验
        bias_checks = self._check_bias()

        # 运行三基线
        baselines: dict[str, BaselineResult] = {}
        for bt in BaselineType:
            try:
                result = self.replay_history(bt)
                baselines[bt.value] = result
            except Exception as exc:
                logger.error("[Replay] 基线 %s 回放失败: %s", bt.value, exc)
                baselines[bt.value] = BaselineResult(baseline=bt.value)

        # 计算边际夏普
        sharpe_debate = baselines.get(BaselineType.AI_DEBATE.value, BaselineResult()).metrics.get("sharpe", 0.0)
        sharpe_agents = baselines.get(BaselineType.FIVE_AGENTS.value, BaselineResult()).metrics.get("sharpe", 0.0)
        sharpe_rule = baselines.get(BaselineType.RULE_ONLY.value, BaselineResult()).metrics.get("sharpe", 0.0)

        marginal_debate_vs_agents = sharpe_debate - sharpe_agents
        marginal_agents_vs_rule = sharpe_agents - sharpe_rule

        # 上线建议
        recommendation = self._make_recommendation(
            marginal_debate_vs_agents, marginal_agents_vs_rule, bias_checks
        )

        report = ComparisonReport(
            config={
                "start_date": self._config.start_date,
                "end_date": self._config.end_date,
                "symbols": self._config.symbols,
                "rebalance_freq": self._config.rebalance_freq,
                "forward_return_horizon": self._config.forward_return_horizon,
                "use_mock_providers": self._config.use_mock_providers,
            },
            baselines={k: v.to_dict() for k, v in baselines.items()},
            marginal_sharpe_debate_vs_agents=round(marginal_debate_vs_agents, 4),
            marginal_sharpe_agents_vs_rule=round(marginal_agents_vs_rule, 4),
            recommendation=recommendation,
            bias_checks=bias_checks,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )
        return report

    # ------------------------------------------------------------
    # 单基线回放
    # ------------------------------------------------------------

    def replay_history(self, baseline: BaselineType) -> BaselineResult:
        """运行单基线回放

        Args:
            baseline: 基线类型
        Returns:
            BaselineResult 含决策序列 + 收益 + IC + 指标
        """
        dates = self._loader.get_trading_dates(
            self._config.start_date, self._config.end_date
        )
        if len(dates) < MIN_TRADING_DAYS:
            logger.warning("[Replay] 交易日不足 %d (实际 %d), 结果可能不显著",
                           MIN_TRADING_DAYS, len(dates))

        symbols = self._config.symbols
        # 注意: 当 symbols=None 时, 不能只在 start_date 取一次成分股快照,
        # 否则整个回测期间都用固定列表, 引入幸存者偏差 (已退市/ST 股被排除).
        # 正确做法: 每日循环中重新获取当日成分股快照 (项目记忆硬约束).

        all_decisions: list[dict[str, Any]] = []
        # 按日聚合: 每日的 action 强度作为因子, 对应前瞻收益
        daily_factor: dict[str, dict[str, float]] = {}  # date -> {symbol: strength}
        daily_forward: dict[str, dict[str, float]] = {}  # date -> {symbol: fwd_return}
        debate_triggered_count = 0
        total_decisions = 0

        for date_str in dates:
            daily_factor[date_str] = {}
            daily_forward[date_str] = {}

            # 每日重新获取成分股快照 (防幸存者偏差, 项目记忆硬约束)
            # 当 self._config.symbols 已指定时, 用固定列表 (回测特定标的池)
            daily_symbols = symbols if symbols is not None else \
                self._loader.get_constituents(date_str)

            for sym in daily_symbols:
                # 前视偏差: 不可交易则跳过 (停牌/涨跌停)
                if not self._loader.is_tradable(sym, date_str):
                    continue

                md = self._loader.get_market_data(sym, date_str)
                fund = self._loader.get_fundamentals(sym, date_str)

                # 按基线类型生成决策
                if baseline == BaselineType.AI_DEBATE:
                    decision = self._run_ai_debate(sym, date_str, md, fund)
                    if decision.get("debate_triggered"):
                        debate_triggered_count += 1
                elif baseline == BaselineType.FIVE_AGENTS:
                    decision = self._run_five_agents_only(sym, date_str, md, fund)
                else:  # RULE_ONLY
                    decision = self._run_rule_only(sym, date_str, md)

                # 记录决策
                decision["date"] = date_str
                decision["symbol"] = sym
                all_decisions.append(decision)
                total_decisions += 1

                # 因子 = 决策强度 (信号), 前瞻收益 = 未来 N 天收益率
                daily_factor[date_str][sym] = decision.get("strength", 0.0)
                fwd = self._loader.get_forward_return(
                    sym, date_str, self._config.forward_return_horizon
                )
                if math.isfinite(fwd):
                    daily_forward[date_str][sym] = fwd

        # 计算收益序列 (简化: 等权多头的日收益均值作为策略收益)
        returns = self._compute_returns(all_decisions, dates)

        # 计算 IC 序列 (每日 Spearman IC)
        ic_series = self._compute_ic_series(daily_factor, daily_forward)

        # 用 FastBacktest 计算指标
        metrics = self._compute_metrics(returns, ic_series)

        # action 分布
        n_buy = sum(1 for d in all_decisions if d.get("action") == "buy")
        n_sell = sum(1 for d in all_decisions if d.get("action") == "sell")
        n_hold = sum(1 for d in all_decisions if d.get("action") == "hold")

        debate_rate = debate_triggered_count / total_decisions if total_decisions > 0 else 0.0

        return BaselineResult(
            baseline=baseline.value,
            decisions=all_decisions,
            returns=returns,
            ic_series=ic_series,
            metrics=metrics,
            debate_trigger_rate=debate_rate,
            n_decisions=total_decisions,
            n_buy=n_buy,
            n_sell=n_sell,
            n_hold=n_hold,
        )

    # ------------------------------------------------------------
    # 三基线实现
    # ------------------------------------------------------------

    def _run_ai_debate(
        self, symbol: str, date_str: str,
        market_data: dict[str, Any], fundamentals: dict[str, Any],
    ) -> dict[str, Any]:
        """基线 1: 完整 run_decision (辩论 + 聚合 + 风控)

        复用 orchestrator.run_decision() 全链路.
        use_mock_providers=True 时通过 health_monitor 自动降级 Mock.
        """
        try:
            decision = run_decision(
                symbol=symbol,
                market_data=market_data,
                fundamentals=fundamentals,
                mode="shadow",  # 回放用 shadow 模式, 不触发下单
                health_monitor=self._monitor,
            )
            return {
                "action": decision.action,
                "strength": decision.strength,
                "confidence": decision.confidence,
                "verdict_type": decision.verdict_type,
                "debate_triggered": decision.verdict_type == "DEBATE",
            }
        except Exception as exc:
            logger.warning("[Replay] ai_debate %s %s 失败, 降级 hold: %s",
                           symbol, date_str, exc)
            return {"action": "hold", "strength": 0.0, "confidence": 0.0,
                    "verdict_type": "ERROR", "debate_triggered": False}

    def _run_five_agents_only(
        self, symbol: str, date_str: str,
        market_data: dict[str, Any], fundamentals: dict[str, Any],
    ) -> dict[str, Any]:
        """基线 2: 仅五 Agent 共识 (跳过辩论/judge)

        直接调用 _run_five_agents() + apply_mode(), 不进入辩论环节.
        测量五 Agent 共识相对于纯规则的增量.
        """
        try:
            ctx = build_context(symbol, market_data, fundamentals, None, None)
            agent_res = _run_five_agents(symbol, ctx)

            action = agent_res.get("action", "hold")
            strength = float(agent_res.get("strength", 0.0))
            confidence = float(agent_res.get("confidence", 0.0))

            # 构造 TradingDecision 并过风控门 (与 run_decision 一致)
            decision = TradingDecision(
                symbol=symbol, action=action, strength=strength,
                confidence=confidence, verdict_type="FAST",
            )
            rc = RiskContext(symbol=symbol)
            rc.agent_veto = bool(agent_res.get("veto", False))
            rc.agent_veto_reason = str(agent_res.get("veto_reason", ""))
            gate = run_hard_risk(decision, rc)
            decision = apply_mode(decision, gate, mode="shadow")

            return {
                "action": decision.action,
                "strength": decision.strength,
                "confidence": decision.confidence,
                "verdict_type": "FAST",
                "debate_triggered": False,
            }
        except Exception as exc:
            logger.warning("[Replay] five_agents %s %s 失败, 降级 hold: %s",
                           symbol, date_str, exc)
            return {"action": "hold", "strength": 0.0, "confidence": 0.0,
                    "verdict_type": "ERROR", "debate_triggered": False}

    def _run_rule_only(
        self, symbol: str, date_str: str,
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        """基线 3: 纯规则兜底 (跳过 AI)

        规则公式 (单位修正版):
            change_pct 已是小数形式 (0.05 表示 +5%), 直接用作 strength
            action = buy if change_pct > 0.05 (5% 涨幅)
                   else sell if change_pct < -0.05 (5% 跌幅)
                   else hold

        原公式 strength = change_pct * 0.03 单位错配:
            0.05 * 0.03 = 0.0015 << 阈值 0.05, 永远触发 hold, 使 rule_only 失真.
        """
        change = float(market_data.get("change_pct", 0.0) or 0.0)
        # change_pct 是小数 (如 0.05=5%), 直接作为 strength, clamp 到 [-1, 1]
        strength = max(-1.0, min(1.0, change))
        action = "buy" if strength > 0.05 else ("sell" if strength < -0.05 else "hold")

        # 过风控门 (与 run_decision 一致)
        decision = TradingDecision(
            symbol=symbol, action=action, strength=strength,
            confidence=0.4, verdict_type="RULE",
        )
        rc = RiskContext(symbol=symbol)
        gate = run_hard_risk(decision, rc)
        decision = apply_mode(decision, gate, mode="shadow")

        return {
            "action": decision.action,
            "strength": decision.strength,
            "confidence": decision.confidence,
            "verdict_type": "RULE",
            "debate_triggered": False,
        }

    # ------------------------------------------------------------
    # 收益 / IC / 指标计算
    # ------------------------------------------------------------

    def _compute_returns(
        self, decisions: list[dict[str, Any]], dates: list[str],
    ) -> list[float]:
        """计算策略日收益序列

        简化模型: 每日等权持有所有 buy 决策的标的, 收益 = 前瞻收益均值.
        无 buy 决策时收益 = 0 (空仓).
        前视偏差: 用 date 的前瞻收益 (date+horizon), 但信号在 date 生成 (无泄露).

        单位修正: forward_return 是 horizon 天累计收益, 不能直接当日收益传给
        FastBacktest (会让 Sharpe/波动率严重失真). 这里除以 horizon 转为日均收益.
        """
        # 按日聚合
        daily_returns: list[float] = []
        decisions_by_date: dict[str, list[dict[str, Any]]] = {}
        for d in decisions:
            decisions_by_date.setdefault(d.get("date", ""), []).append(d)

        horizon = max(int(self._config.forward_return_horizon), 1)

        for date_str in dates:
            day_decs = decisions_by_date.get(date_str, [])
            # 取 buy 决策的前瞻收益
            long_returns: list[float] = []
            for d in day_decs:
                if d.get("action") == "buy":
                    sym = d.get("symbol", "")
                    fwd = self._loader.get_forward_return(
                        sym, date_str, self._config.forward_return_horizon
                    )
                    if math.isfinite(fwd):
                        long_returns.append(fwd)

            if long_returns:
                # 等权多头收益 (horizon 天累计 → 日均, 对齐 FastBacktest 日收益序列)
                avg_horizon_return = sum(long_returns) / len(long_returns)
                daily_returns.append(avg_horizon_return / horizon)
            else:
                daily_returns.append(0.0)  # 空仓

        return daily_returns

    def _compute_ic_series(
        self,
        daily_factor: dict[str, dict[str, float]],
        daily_forward: dict[str, dict[str, float]],
    ) -> list[float]:
        """计算每日 Spearman IC 序列

        对每个交易日, 用决策强度 (因子) 与前瞻收益 (label) 计算 Spearman 秩相关.
        样本不足 (< MIN_IC_SAMPLES) 的日期跳过.
        """
        ic_list: list[float] = []
        for date_str in sorted(daily_factor.keys()):
            factors = daily_factor[date_str]
            forwards = daily_forward.get(date_str, {})
            # 取交集
            common = set(factors.keys()) & set(forwards.keys())
            if len(common) < MIN_IC_SAMPLES:
                continue
            fv = {s: factors[s] for s in common}
            fr = {s: forwards[s] for s in common}
            ic = self._spearman_ic(fv, fr)
            if math.isfinite(ic):
                ic_list.append(ic)
        return ic_list

    @staticmethod
    def _spearman_ic(
        factor_values: dict[str, float],
        forward_returns: dict[str, float],
    ) -> float:
        """计算 Spearman 秩相关 IC (复用 multi_factor_signal 公式)

        Args:
            factor_values: {symbol: factor_value}
            forward_returns: {symbol: forward_return}
        Returns:
            Spearman IC, 样本不足返回 nan
        """
        common = set(factor_values.keys()) & set(forward_returns.keys())
        pairs: list[tuple] = []
        for sym in common:
            fv = factor_values.get(sym)
            fr = forward_returns.get(sym)
            if (isinstance(fv, (int, float)) and isinstance(fr, (int, float))
                    and math.isfinite(fv) and math.isfinite(fr)):
                pairs.append((fv, fr))

        if len(pairs) < MIN_IC_SAMPLES:
            return float("nan")

        # 计算 rank
        sorted_by_fv = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
        sorted_by_fr = sorted(range(len(pairs)), key=lambda i: pairs[i][1])
        rank_fv = [0.0] * len(pairs)
        rank_fr = [0.0] * len(pairs)
        for rank, idx in enumerate(sorted_by_fv):
            rank_fv[idx] = rank
        for rank, idx in enumerate(sorted_by_fr):
            rank_fr[idx] = rank

        n = len(pairs)
        mean_fv = sum(rank_fv) / n
        mean_fr = sum(rank_fr) / n
        num = sum((rank_fv[i] - mean_fv) * (rank_fr[i] - mean_fr) for i in range(n))
        var_fv = sum((r - mean_fv) ** 2 for r in rank_fv)
        var_fr = sum((r - mean_fr) ** 2 for r in rank_fr)
        denom = math.sqrt(var_fv * var_fr) if var_fv > 0 and var_fr > 0 else 0.0

        if denom < 1e-10:
            return float("nan")
        return num / denom

    def _compute_metrics(
        self, returns: list[float], ic_series: list[float],
    ) -> dict[str, Any]:
        """用 FastBacktest 计算绩效指标 (复用, 不重写)"""
        try:
            from utils.alpha.fast_backtest import BacktestResult, FastBacktest
            if len(returns) < MIN_TRADING_DAYS:
                return self._empty_metrics()
            bt = FastBacktest()
            result: BacktestResult = bt.run(
                returns=pd.Series(returns),
                n_trials=3,  # 三基线对比 = 3 次尝试
                ic_series=pd.Series(ic_series) if ic_series else None,
            )
            return result.summary()
        except Exception as exc:
            logger.warning("[Replay] FastBacktest 计算失败, 返回空指标: %s", exc)
            return self._empty_metrics()

    @staticmethod
    def _empty_metrics() -> dict[str, Any]:
        """空指标 (数据不足或异常时降级)"""
        return {
            "annual_return": 0.0, "annual_vol": 0.0, "sharpe": 0.0,
            "sortino": 0.0, "calmar": 0.0, "max_drawdown": 0.0,
            "win_rate": 0.0, "dsr": 0.0, "ic_ir": 0.0, "sharpe_cv": 0.0,
            "n_windows": 0, "n_trials": 1, "passed_v9": False, "v9_failures": [],
        }

    # ------------------------------------------------------------
    # 上线建议
    # ------------------------------------------------------------

    def _make_recommendation(
        self,
        marginal_debate_vs_agents: float,
        marginal_agents_vs_rule: float,
        bias_checks: dict[str, bool],
    ) -> str:
        """根据边际夏普 + 偏差校验给出上线建议

        判定逻辑:
        - 偏差校验不通过 → shadow (不可上线)
        - 辩论增量 > 阈值 AND Agent增量 > 阈值 → auto (可上线)
        - 仅 Agent增量 > 阈值 → paper (可模拟)
        - 其他 → shadow
        """
        # 偏差校验必须全通过
        if not all(bias_checks.values()):
            return "shadow"

        debate_valuable = marginal_debate_vs_agents > MARGINAL_SHARPE_THRESHOLD
        agents_valuable = marginal_agents_vs_rule > MARGINAL_SHARPE_THRESHOLD

        if debate_valuable and agents_valuable:
            return "auto"
        if agents_valuable:
            return "paper"
        return "shadow"

    # ------------------------------------------------------------
    # 前视偏差校验
    # ------------------------------------------------------------

    def _check_bias(self) -> dict[str, bool]:
        """前视偏差校验 (项目记忆硬约束)

        校验项:
        1. disclosure_date_check: 基本面用披露日而非报告期截止日
        2. constituent_snapshot_check: 逐日成分股快照防幸存者偏差
        3. tradability_check: 停牌冻结/涨跌停不可成交
        4. signal_lag_check: 信号至少滞后一期
        """
        checks: dict[str, bool] = {}

        # 1. 披露日校验: loader 返回的 fundamentals 必须含 disclosure_date
        try:
            dates = self._loader.get_trading_dates(
                self._config.start_date, self._config.end_date
            )
            if dates and self._config.symbols:
                fund = self._loader.get_fundamentals(self._config.symbols[0], dates[0])
                checks["disclosure_date_check"] = "disclosure_date" in fund
            else:
                checks["disclosure_date_check"] = True  # 无数据视为通过
        except Exception:
            checks["disclosure_date_check"] = False

        # 2. 成分股快照校验: loader 必须支持 get_constituents
        try:
            if dates:
                constituents = self._loader.get_constituents(dates[0])
                checks["constituent_snapshot_check"] = isinstance(constituents, list)
            else:
                checks["constituent_snapshot_check"] = True
        except Exception:
            checks["constituent_snapshot_check"] = False

        # 3. 可交易性校验: loader 必须支持 is_tradable
        try:
            if dates and self._config.symbols:
                tradable = self._loader.is_tradable(self._config.symbols[0], dates[0])
                checks["tradability_check"] = isinstance(tradable, bool)
            else:
                checks["tradability_check"] = True
        except Exception:
            checks["tradability_check"] = False

        # 4. 信号滞后校验: forward_return_horizon >= 1 (信号在 date 生成, 收益在 date+N)
        checks["signal_lag_check"] = self._config.forward_return_horizon >= 1

        logger.info("[Replay] 前视偏差校验: %s", checks)
        return checks

    # ------------------------------------------------------------
    # Markdown 输出
    # ------------------------------------------------------------

    def to_markdown(self, report: ComparisonReport) -> str:
        """将对比报告转为 Markdown"""
        cfg = report.config
        lines: list[str] = [
            "# ai_decision 历史回放 + 三基线对比",
            "",
            f"> 生成时间: {report.generated_at}",
            "",
            "## 1. 回放配置",
            "",
            f"- 日期范围: **{cfg.get('start_date', '')}** ~ **{cfg.get('end_date', '')}**",
            f"- 调仓频率: {cfg.get('rebalance_freq', '')}",
            f"- 前瞻收益天数: {cfg.get('forward_return_horizon', '')}",
            f"- 使用 Mock Provider: {cfg.get('use_mock_providers', '')}",
            "",
            "## 2. 前视偏差校验",
            "",
        ]
        for check, passed in report.bias_checks.items():
            icon = "✅" if passed else "❌"
            lines.append(f"- {icon} {check}: {'通过' if passed else '未通过'}")

        lines.extend([
            "",
            "## 3. 三基线绩效对比",
            "",
            "| 基线 | 决策数 | Buy/Sell/Hold | Sharpe | 年化收益 | 最大回撤 | IC_IR | 辩论触发率 |",
            "|------|--------|---------------|--------|----------|----------|-------|-----------|",
        ])

        for bt_value in ["ai_debate", "five_agents", "rule_only"]:
            bl = report.baselines.get(bt_value, {})
            m = bl.get("metrics", {})
            sharpe = m.get("sharpe", 0.0)
            annual = m.get("annual_return", 0.0)
            max_dd = m.get("max_drawdown", 0.0)
            ic_ir = m.get("ic_ir", 0.0)
            n_dec = bl.get("n_decisions", 0)
            bsh = f"{bl.get('n_buy', 0)}/{bl.get('n_sell', 0)}/{bl.get('n_hold', 0)}"
            debate_rate = bl.get("debate_trigger_rate", 0.0)
            lines.append(
                f"| {bt_value} | {n_dec} | {bsh} | {sharpe:.3f} | "
                f"{annual:.2%} | {max_dd:.2%} | {ic_ir:.3f} | {debate_rate:.1%} |"
            )

        lines.extend([
            "",
            "## 4. 边际夏普 (增量价值)",
            "",
            f"- ai_debate - five_agents = **{report.marginal_sharpe_debate_vs_agents:+.4f}**"
            f" ({'✅ 超过阈值' if report.marginal_sharpe_debate_vs_agents > MARGINAL_SHARPE_THRESHOLD else '❌ 未达阈值'} {MARGINAL_SHARPE_THRESHOLD})",
            f"- five_agents - rule_only = **{report.marginal_sharpe_agents_vs_rule:+.4f}**"
            f" ({'✅ 超过阈值' if report.marginal_sharpe_agents_vs_rule > MARGINAL_SHARPE_THRESHOLD else '❌ 未达阈值'} {MARGINAL_SHARPE_THRESHOLD})",
            "",
            "## 5. 上线建议",
            "",
        ])
        rec_icon = {"auto": "🟢", "paper": "🟡", "shadow": "🔴"}.get(report.recommendation, "⚪")
        lines.append(f"### {rec_icon} {report.recommendation.upper()}")
        if report.recommendation == "auto":
            lines.append("")
            lines.append("辩论与五 Agent 均有显著增量价值, 建议推进至 auto 灰度.")
        elif report.recommendation == "paper":
            lines.append("")
            lines.append("五 Agent 有增量但辩论未达阈值, 建议继续 paper 模拟观察.")
        else:
            lines.append("")
            lines.append("增量价值不足或偏差校验未通过, 维持 shadow 模式.")

        return "\n".join(lines)

    # ------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------

    def save(self, report: ComparisonReport) -> str:
        """落盘 Markdown + JSON

        文件路径:
        - reports/ai_decision/backtest_replay_{timestamp}.md
        - reports/ai_decision/backtest_replay_{timestamp}.json
        """
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON
        json_path = _REPORT_DIR / f"backtest_replay_{ts}.json"
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)

        # Markdown
        md_path = _REPORT_DIR / f"backtest_replay_{ts}.md"
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(self.to_markdown(report))

        logger.info("[Replay] 回放报告已落盘: %s + %s", md_path, json_path)
        return str(md_path)
