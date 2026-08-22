"""
ai_decision.backtest_replay_types — 历史回放类型与协议定义
=============================================================

从 backtest_replay.py 拆分 (v8.6.15 重构, AGENTS.md §5.3 文件 ≤800 行约束).

本模块仅含数据结构定义, 无业务逻辑, 无外部依赖 (仅 stdlib + typing):
  - BaselineType: 三基线枚举
  - ReplayConfig: 回放配置 dataclass
  - BaselineResult: 单基线回放结果 dataclass
  - ComparisonReport: 三基线对比报告 dataclass
  - HistoryDataLoader: 历史数据加载协议 (Protocol)

常量 (对齐 fast_backtest / multi_factor_signal):
  - MARGINAL_SHARPE_THRESHOLD: 边际夏普阈值
  - MIN_IC_SAMPLES: IC 最小样本数
  - MIN_TRADING_DAYS: 回测最小交易日数
  - RISK_FREE_RATE: 无风险利率
  - TRADING_DAYS_PER_YEAR: 年交易日数

向后兼容: backtest_replay.py 通过 re-export 暴露所有符号.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

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


# ============================================================
# 枚举
# ============================================================

class BaselineType(StrEnum):
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


__all__ = [
    # 常量
    "MARGINAL_SHARPE_THRESHOLD",
    "MIN_IC_SAMPLES",
    "MIN_TRADING_DAYS",
    "RISK_FREE_RATE",
    "TRADING_DAYS_PER_YEAR",
    # 类型
    "BaselineType",
    "ReplayConfig",
    "BaselineResult",
    "ComparisonReport",
    "HistoryDataLoader",
]
