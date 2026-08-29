"""
v7.5 Beta 对冲引擎 —— EWMA Beta + 期货空头对冲

数学基础:
    EWMA Beta:
        σ_{iM,t} = λ·σ_{iM,t-1} + (1-λ)·r_{i,t}·r_{M,t}
        β_t = σ_{iM,t} / σ_{M,t}^2

    对冲手数:
        N_hedge = ⌊(β_port - β_target)·V_port / (β_fut·Multiplier·P_fut)⌉

    成本阈值:
        仅当对冲成本/组合市值 < 0.3% 时执行期货对冲
        否则降级至反向 ETF 或期权 Put Spread
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger("v75.hedging.beta")

try:
    from data_sources.akshare_futures import fetch_futures_realtime
    HAS_AKSHARE_FUTURES = True
except ImportError:
    # 导入降级: data_sources.akshare_futures 缺失时关闭期货空头对冲功能
    fetch_futures_realtime = None  # type: ignore[assignment]
    HAS_AKSHARE_FUTURES = False


class BetaHedger:
    """Beta 对冲器: 通过股指期货空头将组合 Beta 降至目标"""

    def __init__(self,
                 beta_target: float = 0.3,
                 beta_trigger: float = 0.7,
                 ewma_lambda: float = 0.94,
                 window: int = 60,
                 cost_max: float = 0.003,
                 futures_config: dict | None = None):
        """
        Args:
            beta_target: 目标组合 Beta
            beta_trigger: 触发对冲的 Beta 阈值
            ewma_lambda: EWMA 衰减因子
            window: 滚动窗口
            cost_max: 对冲成本上限 (组合市值占比)
            futures_config: {symbol: {multiplier, beta, price}}
        """
        self.beta_target = float(beta_target)
        self.beta_trigger = float(beta_trigger)
        self.lam = float(ewma_lambda)
        self.window = int(window)
        self.cost_max = float(cost_max)
        # 默认期货合约配置
        self.futures = futures_config or {
            "IF": {"multiplier": 300, "beta": 1.0, "price": 3800.0},
            "IC": {"multiplier": 200, "beta": 1.2, "price": 5500.0},
            "IM": {"multiplier": 200, "beta": 1.1, "price": 5800.0},
        }

    def ewma_beta(self, asset_returns: pd.Series,
                  market_returns: pd.Series) -> float:
        """计算 EWMA Beta

        Args:
            asset_returns: 资产收益率序列
            market_returns: 市场收益率序列

        Returns:
            EWMA Beta
        """
        if len(asset_returns) == 0 or len(market_returns) == 0:
            return 0.0
        df = pd.DataFrame({"a": asset_returns, "m": market_returns}).dropna()
        if len(df) < 10:
            return 0.0
        df = df.tail(self.window)

        # EWMA 协方差与方差
        lam = self.lam
        cov_am = 0.0
        var_m = 0.0
        for i, (a, m) in enumerate(zip(df["a"], df["m"], strict=False)):
            w = (1 - lam) * (lam ** (len(df) - i - 1))
            cov_am += w * a * m
            var_m += w * m * m
        # 归一化: EWMA 权重和 = (1-lam) * Σ lam^k = 1 - lam^n  (RiskMetrics 1996)
        # P0-6 FIX: 原代码 (1-lam)^n / (1-lam) 数学错误, 导致 beta 估算系统性偏差
        s = (1.0 - lam ** len(df)) if lam < 1.0 else 1.0
        cov_am /= max(s, 1e-12)
        var_m /= max(s, 1e-12)

        return float(cov_am / var_m) if var_m > 0 else 0.0

    def portfolio_beta(self,
                       positions: dict[str, float],
                       prices: dict[str, float],
                       returns: pd.DataFrame,
                       market_returns: pd.Series) -> float:
        """计算组合加权 Beta

        Args:
            positions: {symbol: quantity}
            prices: {symbol: current_price}
            returns: 历史收益率 DataFrame
            market_returns: 市场收益率

        Returns:
            组合 Beta
        """
        market_value = {s: positions.get(s, 0) * prices.get(s, 0)
                        for s in positions if s in prices and s in returns.columns}
        total = sum(market_value.values())
        if total <= 0:
            return 0.0

        beta_port = 0.0
        for symbol, value in market_value.items():
            w = value / total
            b = self.ewma_beta(returns[symbol], market_returns)
            beta_port += w * b
        return float(beta_port)

    def compute_hedge(self,
                      portfolio_beta: float,
                      portfolio_value: float,
                      preferred_futures: str = "IF") -> dict[str, object]:
        """计算对冲指令

        Args:
            portfolio_beta: 当前组合 Beta
            portfolio_value: 组合市值 (RMB)
            preferred_futures: 首选期货合约 (IF/IC/IM)

        Returns:
            对冲指令字典
        """
        if portfolio_beta <= self.beta_trigger:
            return {"action": "NO_HEDGE",
                    "reason": f"Beta {portfolio_beta:.3f} ≤ 触发阈值 {self.beta_trigger}",
                    "current_beta": portfolio_beta}

        # 计算需要对冲的 Beta 部分
        excess_beta = portfolio_beta - self.beta_target
        value_to_hedge = excess_beta * portfolio_value

        # 期货选择 + 动态价格
        fut = self.futures.get(preferred_futures, self.futures["IF"])
        live_price = self._resolve_futures_price(preferred_futures, fut)
        notional_per_contract = fut["multiplier"] * live_price
        beta_adjusted_notional = notional_per_contract * fut["beta"]

        if beta_adjusted_notional <= 0:
            return {"action": "ERROR", "reason": "期货合约参数异常"}

        # 对冲手数 (四舍五入)
        n_contracts = int(round(value_to_hedge / beta_adjusted_notional))
        if n_contracts <= 0:
            return {"action": "NO_HEDGE",
                    "reason": "计算对冲手数 ≤ 0",
                    "current_beta": portfolio_beta}

        # 估算对冲成本 (手续费 + 滑点)
        commission_rate = 0.000023   # 万 0.23
        slippage_rate = 0.0001       # 万 1
        cost = n_contracts * notional_per_contract * (commission_rate + slippage_rate)
        cost_ratio = cost / portfolio_value if portfolio_value > 0 else 1.0

        result = {
            "action": "SHORT_FUTURES",
            "instrument": preferred_futures,
            "contracts": int(n_contracts),
            "direction": "SELL",
            "multiplier": fut["multiplier"],
            "futures_price": float(live_price),
            "futures_beta": fut["beta"],
            "notional": float(n_contracts * notional_per_contract),
            "estimated_cost": float(cost),
            "cost_ratio": float(cost_ratio),
            "current_beta": float(portfolio_beta),
            "target_beta": float(self.beta_target),
            "beta_reduced": float(excess_beta),
        }

        # 成本阈值检查
        if cost_ratio > self.cost_max:
            result["action"] = "DOWNGRADE_TO_PUT_SPREAD"
            result["reason"] = (f"对冲成本 {cost_ratio:.4f} > 阈值 {self.cost_max:.4f}, "
                                f"降级至期权 Put Spread")
            logger.warning("Beta 对冲降级: %s", result["reason"])

        logger.info("Beta 对冲: Beta %.3f → %.3f, %s %d 手 @ %.2f, 成本 %.0f (%.4f)",
                    portfolio_beta, self.beta_target,
                    preferred_futures, n_contracts, live_price, cost, cost_ratio)
        return result

    def _resolve_futures_price(self, preferred_futures: str, fut: dict) -> float:
        """获取期货价格：优先 AKShare 实时，否则回退到配置价格"""
        symbol = preferred_futures
        if HAS_AKSHARE_FUTURES:
            try:
                rt = fetch_futures_realtime(symbol)
                if rt and rt.get("index_price"):
                    logger.debug("AKShare 期货实时价格 %s = %s", symbol, rt["index_price"])
                    return float(rt["index_price"])
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("AKShare 期货价格获取失败 %s: %s", symbol, e)
        # HG-1: AKShare 实时价格不可用时回退到硬编码配置价 (IF=3800/IC=5500/IM=5800)。
        # 按 memory B1/Q4 要求: 硬编码/陈旧数据必须显式标记降级 (OFFLINE_ONLY + stale),
        # 否则下游会误把陈旧价当实时价算对冲手数/成本。
        price = fut.get("price")
        if price is None:
            return 0.0
        logger.warning(
            "[OFFLINE_ONLY] %s 期货实时价不可用, 使用硬编码配置价 %.2f (非实时, 对冲手数可能失真)",
            preferred_futures, float(price),
        )
        return float(price)


def pick_futures_contract(portfolio_beta: float,
                          portfolio_value: float,
                          futures_config: dict) -> str:
    """根据组合特征选择最合适的期货合约

    Args:
        portfolio_beta: 组合 Beta
        portfolio_value: 组合市值
        futures_config: 期货配置

    Returns:
        推荐的期货代码
    """
    # 简单启发式: 大盘股用 IF, 中盘用 IC, 小盘用 IM
    # 实盘应根据持仓成分股的中位数市值判断
    if portfolio_beta > 1.1:
        return "IF"   # 大盘股, 高 Beta
    if portfolio_beta > 0.9:
        return "IC"   # 中盘股
    return "IM"   # 小盘股
