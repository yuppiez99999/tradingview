"""组合策略回测引擎 (ComboBacktest).

逐日驱动 -> 策略信号 -> 模拟建仓 -> Greeks更新 -> 滚仓 -> 成本扣除 -> 绩效统计.

复用:
    - OptionDataFetcher._bs_price (BS定价, 零第三方依赖)
    - 5策略引擎 (via ComboOrchestrator)
"""

from __future__ import annotations

import copy
import logging
import math
from datetime import datetime, timedelta

from .combo_base import StrategyType
from .iv_adaptive import load_iv_adaptive_config, resolve_adaptive_params

logger = logging.getLogger(__name__)

_TRADING_DAYS_PER_YEAR = 252
_SLIPPAGE_PCT = 0.005
_COMMISSION_PER_CONTRACT = 5.0

# Collar 静态参数基线 — 镜像 CollarEngine 默认值 (put/call 5% OTM, DTE 窗口
# [30,60] 中点 45d, 与旧硬编码 0.95/1.05/45d 等价); adaptive 模式经
# resolve_adaptive_params 在此基线上做 tier 覆盖 (回测与生产共用同一解析)
_COLLAR_STATIC_PARAMS: dict = {
    "put_otm_pct": 0.05,
    "call_otm_pct": 0.05,
    "dte_min": 30,
    "dte_max": 60,
}


class ComboBacktest:
    """组合策略回测 — 逐日驱动验证策略绩效."""

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._initial_capital = self._config.get("total_capital", 2_000_000)
        self._slippage = self._config.get("slippage_pct", _SLIPPAGE_PCT)
        self._commission = self._config.get("commission_per_contract", _COMMISSION_PER_CONTRACT)

    def run_backtest(
        self,
        start_date: str,
        end_date: str,
        strategies: list[StrategyType] | None = None,
        etf_prices: dict[str, list[dict]] | list[dict] | None = None,
        use_bs_reconstruct: bool = True,
        underlying: str = "510050.SH",
        initial_iv: float = 0.20,
        iv_series: dict[str, int] | None = None,
        param_mode: str = "static",
        iv_adaptive_cfg: dict | None = None,
    ) -> dict:
        """运行回测.

        Args:
            start_date: "YYYY-MM-DD"
            end_date: "YYYY-MM-DD"
            strategies: 策略列表, None则全部5策略
            etf_prices: {underlying: [{date, close}, ...]} 或直接 [{date, close}, ...], None则合成
            use_bs_reconstruct: 无历史期权链时BS重构
            underlying: 回测标的
            initial_iv: 初始IV(BS重构用)
            iv_series: {date: iv_rank} 日度 IV Rank 序列 (仅 COLLAR 消费)
            param_mode: "static" 静态参数(默认, 旧行为) | "adaptive" IV Rank 分档动态参数
            iv_adaptive_cfg: iv_adaptive 配置段 (tiers), adaptive 模式且
                无 rank/未命中档时按 fail-open 回静态

        Returns:
            {equity_curve, metrics, hedge_efficiency, trades, param_mode}
        """
        if strategies is None:
            strategies = list(StrategyType)

        if isinstance(etf_prices, dict):
            prices = etf_prices.get(underlying) or \
                self._generate_synthetic_prices(start_date, end_date, underlying)
        else:
            prices = etf_prices or \
                self._generate_synthetic_prices(start_date, end_date, underlying)
        if not prices:
            return {"equity_curve": [], "metrics": {}, "hedge_efficiency": 0.0, "trades": []}

        equity = self._initial_capital
        equity_curve: list[dict] = []
        trades: list[dict] = []
        peak_equity = equity
        max_drawdown = 0.0
        daily_returns: list[float] = []
        hedge_pnl = 0.0
        spot_pnl = 0.0

        for i, bar in enumerate(prices):
            current_date = bar.get("date", str(i))
            spot_price = bar.get("close", 3.0)
            prev_equity = equity

            for st in strategies:
                collar_resolved = None
                if st == StrategyType.COLLAR and param_mode == "adaptive":
                    collar_resolved = resolve_adaptive_params(
                        (iv_series or {}).get(current_date),
                        iv_adaptive_cfg,
                        _COLLAR_STATIC_PARAMS,
                    )
                trade = self._simulate_strategy(
                    st, underlying, spot_price, initial_iv, use_bs_reconstruct, current_date,
                    collar_resolved=collar_resolved,
                )
                if trade is not None:
                    trades.append(trade)
                    equity += trade["pnl"]
                    if trade.get("is_hedge", False):
                        hedge_pnl += trade["pnl"]
                    else:
                        spot_pnl += trade["pnl"]

            if prev_equity > 0:
                daily_ret = (equity - prev_equity) / prev_equity
                daily_returns.append(daily_ret)

            peak_equity = max(peak_equity, equity)
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            max_drawdown = max(max_drawdown, dd)

            equity_curve.append({"date": current_date, "equity": round(equity, 2), "drawdown": round(dd, 4)})

        metrics = self._calc_metrics(equity, daily_returns, max_drawdown, len(prices))
        hedge_efficiency = self._calc_hedge_efficiency(hedge_pnl, spot_pnl, max_drawdown)

        result = {
            "equity_curve": equity_curve,
            "metrics": metrics,
            "hedge_efficiency": round(hedge_efficiency, 4),
            "trades": trades,
            "trade_count": len(trades),
            "param_mode": param_mode,
        }
        logger.info(
            "回测完成: %s ~ %s, %d 交易日, %d 策略, param_mode=%s, 终值=%.0f, 年化=%.4f, 回撤=%.4f, Sharpe=%.4f",
            start_date, end_date, len(prices), len(strategies), param_mode,
            equity, metrics["annual_return"], metrics["max_drawdown"], metrics["sharpe"],
        )
        return result

    def _simulate_strategy(
        self,
        strategy_type: StrategyType,
        underlying: str,
        spot_price: float,
        iv: float,
        use_bs: bool,
        current_date: str,
        collar_resolved: dict | None = None,
    ) -> dict | None:
        """模拟单策略单日交易 — 简化为权利金收入/支出.

        collar_resolved: run_backtest 解析的分档结果
        {"params": {...}, "tier": str|None, "iv_rank": int|None};
        None / tier=None 时按 _COLLAR_STATIC_PARAMS 静态模拟 (与旧行为逐分一致)。
        """
        try:
            if strategy_type == StrategyType.COVERED_CALL:
                premium = self._bs_call_price(spot_price, spot_price * 1.05, 45 / 365, 0.03, iv)
                pnl = premium * 10000 - self._commission
                return {"strategy": strategy_type.value, "date": current_date, "pnl": round(pnl, 2), "is_hedge": False}
            elif strategy_type == StrategyType.CASH_SECURED_PUT:
                premium = self._bs_put_price(spot_price, spot_price * 0.95, 45 / 365, 0.03, iv)
                pnl = premium * 10000 - self._commission
                return {"strategy": strategy_type.value, "date": current_date, "pnl": round(pnl, 2), "is_hedge": False}
            elif strategy_type == StrategyType.COLLAR:
                params = (collar_resolved or {}).get("params") or _COLLAR_STATIC_PARAMS
                put_otm_pct = params.get("put_otm_pct", 0.05)
                call_otm_pct = params.get("call_otm_pct", 0.05)
                dte = (params.get("dte_min", 30) + params.get("dte_max", 60)) / 2.0
                put_p = self._bs_put_price(spot_price, spot_price * (1 - put_otm_pct), dte / 365, 0.03, iv)
                call_p = self._bs_call_price(spot_price, spot_price * (1 + call_otm_pct), dte / 365, 0.03, iv)
                net_cost = (put_p - call_p) * 10000 + 2 * self._commission
                trade = {
                    "strategy": strategy_type.value,
                    "date": current_date,
                    "pnl": round(-net_cost, 2),
                    "is_hedge": True,
                    "net_cost": round(net_cost, 2),
                }
                if collar_resolved and collar_resolved.get("tier") is not None:
                    trade["tier"] = collar_resolved["tier"]
                    trade["iv_rank"] = collar_resolved.get("iv_rank")
                return trade
            elif strategy_type == StrategyType.VERTICAL_SPREAD:
                call_low = self._bs_call_price(spot_price, spot_price * 1.03, 45 / 365, 0.03, iv)
                call_high = self._bs_call_price(spot_price, spot_price * 1.08, 45 / 365, 0.03, iv)
                pnl = (call_low - call_high) * 10000 - 2 * self._commission
                return {"strategy": strategy_type.value, "date": current_date, "pnl": round(pnl, 2), "is_hedge": True}
            elif strategy_type == StrategyType.CALENDAR_SPREAD:
                near_p = self._bs_call_price(spot_price, spot_price, 30 / 365, 0.03, iv)
                far_p = self._bs_call_price(spot_price, spot_price, 60 / 365, 0.03, iv)
                pnl = (near_p - far_p) * 10000 - 2 * self._commission
                return {"strategy": strategy_type.value, "date": current_date, "pnl": round(pnl, 2), "is_hedge": True}
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
            logger.debug("策略 %s 模拟异常: %s", strategy_type.value, e)
        return None

    @staticmethod
    def _bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:  # noqa: N803 — BS 惯例大写 S/K/T
        if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
            return max(0.0, S - K)
        sqrt_T = math.sqrt(T)  # noqa: N806
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        N_d1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))  # noqa: N806
        N_d2 = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))  # noqa: N806
        return S * N_d1 - K * math.exp(-r * T) * N_d2

    @staticmethod
    def _bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:  # noqa: N803 — BS 惯例大写 S/K/T
        if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
            return max(0.0, K - S)
        sqrt_T = math.sqrt(T)  # noqa: N806
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        N_neg_d1 = 0.5 * (1 + math.erf(-d1 / math.sqrt(2)))  # noqa: N806
        N_neg_d2 = 0.5 * (1 + math.erf(-d2 / math.sqrt(2)))  # noqa: N806
        return K * math.exp(-r * T) * N_neg_d2 - S * N_neg_d1

    def _generate_synthetic_prices(self, start_date: str, end_date: str, underlying: str) -> list[dict]:
        """生成合成价格序列(离线回测用)."""
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
        prices: list[dict] = []
        current = start
        spot = 3.0
        while current <= end:
            if current.weekday() < 5:
                spot *= 1.0 + (math.sin(current.toordinal() / 10) * 0.005 + 0.0001)
                prices.append({"date": current.strftime("%Y-%m-%d"), "close": round(spot, 4)})
            current += timedelta(days=1)
        return prices

    def _calc_metrics(self, final_equity: float, daily_returns: list[float], max_dd: float, n_days: int) -> dict:
        """计算绩效指标."""
        total_return = (final_equity - self._initial_capital) / self._initial_capital
        years = n_days / _TRADING_DAYS_PER_YEAR if n_days > 0 else 1.0
        annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 and total_return > -1 else 0.0

        if daily_returns:
            mean_ret = sum(daily_returns) / len(daily_returns)
            var_ret = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
            std_ret = math.sqrt(var_ret) if var_ret > 0 else 0.0
            sharpe = (mean_ret / std_ret) * math.sqrt(_TRADING_DAYS_PER_YEAR) if std_ret > 0 else 0.0
        else:
            sharpe = 0.0

        return {
            "total_return": round(total_return, 4),
            "annual_return": round(annual_return, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe": round(sharpe, 4),
            "final_equity": round(final_equity, 2),
            "n_trading_days": n_days,
        }

    @staticmethod
    def _calc_hedge_efficiency(hedge_pnl: float, spot_pnl: float, max_dd: float) -> float:
        """计算对冲效率 — 对冲收益占比 × (1 - 回撤)."""
        total_pnl = hedge_pnl + spot_pnl
        if abs(total_pnl) < 1e-6:
            return 0.0
        hedge_ratio = hedge_pnl / total_pnl if total_pnl > 0 else 0.0
        dd_penalty = max(0.0, 1.0 - max_dd)
        return max(0.0, min(1.0, hedge_ratio * dd_penalty))

    # ============================================================
    # IV Rank 自适应对比 (static vs adaptive) — enabled 决策材料
    # ============================================================

    def run_comparison(
        self,
        start_date: str,
        end_date: str,
        iv_series: dict[str, int] | None = None,
        iv_adaptive_cfg: dict | None = None,
        underlying: str = "510050.SH",
        initial_iv: float = 0.20,
        etf_prices: dict[str, list[dict]] | list[dict] | None = None,
    ) -> dict:
        """Collar 策略 static vs adaptive 对比 — 仅产出决策材料, 不断言 adaptive 必优.

        两次回测共用同一价格序列; adaptive 侧按 iv_series 逐日分档解析参数。
        缺省处理 (均可显式传入覆盖):
            - iv_series=None: 确定性正弦 rank 序列 (周期~252d, 平滑换挡覆盖
              low/mid/high 三档, 避免日间 tier 抖动)
            - iv_adaptive_cfg=None: 从 config/etf_option_combo.yaml 只读加载
              iv_adaptive 段并强制 enabled=true (对比即评估, 不受生产开关影响)

        Returns:
            {static, adaptive, delta, avg_net_cost, iv_series}
            delta = adaptive - static (均净成本为负表示 adaptive 更省)
        """
        if etf_prices is None:
            prices = self._generate_synthetic_prices(start_date, end_date, underlying)
        elif isinstance(etf_prices, dict):
            prices = etf_prices.get(underlying) or \
                self._generate_synthetic_prices(start_date, end_date, underlying)
        else:
            prices = etf_prices or \
                self._generate_synthetic_prices(start_date, end_date, underlying)
        if iv_series is None:
            iv_series = {
                bar.get("date", str(i)): int(round(50 + 50 * math.sin(i / 40.0)))
                for i, bar in enumerate(prices)
            }
        if iv_adaptive_cfg is None:
            iv_adaptive_cfg = self._load_iv_adaptive_cfg_for_comparison()

        static_res = self.run_backtest(
            start_date, end_date,
            strategies=[StrategyType.COLLAR], etf_prices=prices,
            underlying=underlying, initial_iv=initial_iv, param_mode="static",
        )
        adaptive_res = self.run_backtest(
            start_date, end_date,
            strategies=[StrategyType.COLLAR], etf_prices=prices,
            underlying=underlying, initial_iv=initial_iv,
            iv_series=iv_series, param_mode="adaptive",
            iv_adaptive_cfg=iv_adaptive_cfg,
        )
        s_cost = self._avg_collar_net_cost(static_res)
        a_cost = self._avg_collar_net_cost(adaptive_res)
        delta = {
            "max_drawdown": round(
                adaptive_res["metrics"]["max_drawdown"] - static_res["metrics"]["max_drawdown"], 4),
            "sharpe": round(
                adaptive_res["metrics"]["sharpe"] - static_res["metrics"]["sharpe"], 4),
            "hedge_efficiency": round(
                adaptive_res["hedge_efficiency"] - static_res["hedge_efficiency"], 4),
            "avg_net_cost": (
                round(a_cost - s_cost, 2)
                if s_cost is not None and a_cost is not None else None
            ),
        }
        logger.info(
            "对比完成: Δ回撤=%+.4f ΔSharpe=%+.4f Δ对冲效率=%+.4f Δ均净成本=%s (负=adaptive更省)",
            delta["max_drawdown"], delta["sharpe"], delta["hedge_efficiency"],
            "N/A" if delta["avg_net_cost"] is None else f"{delta['avg_net_cost']:+.2f}",
        )
        return {
            "static": static_res,
            "adaptive": adaptive_res,
            "delta": delta,
            "avg_net_cost": {"static": s_cost, "adaptive": a_cost},
            "iv_series": iv_series,
        }

    @staticmethod
    def _load_iv_adaptive_cfg_for_comparison() -> dict:
        """加载 iv_adaptive 配置用于对比 — 深拷贝并强制 enabled=true.

        强制启用仅作用于本次对比的内存副本, 绝不改写生产配置文件;
        配置缺失时返回 {} (adaptive 侧 fail-open 回静态, delta=0)。
        """
        cfg = load_iv_adaptive_config()
        if not cfg.get("tiers"):
            logger.warning("iv_adaptive 配置缺失或无 tiers, adaptive 侧回退静态参数 (delta=0)")
            return {}
        forced = copy.deepcopy(cfg)
        forced["enabled"] = True
        return forced

    @staticmethod
    def _avg_collar_net_cost(result: dict) -> float | None:
        """collar 交易的平均净成本 (买 put 付权利金 - 卖 call 收权利金 + 佣金)."""
        costs: list[float] = [
            t["net_cost"] for t in result.get("trades", [])
            if t.get("strategy") == "collar" and "net_cost" in t
        ]
        if not costs:
            return None
        return sum(costs) / len(costs)
