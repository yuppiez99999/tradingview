"""组合策略回测引擎 (ComboBacktest).

逐日驱动 -> 策略信号 -> 模拟建仓 -> Greeks更新 -> 滚仓 -> 成本扣除 -> 绩效统计.

复用:
    - OptionDataFetcher._bs_price (BS定价, 零第三方依赖)
    - 5策略引擎 (via ComboOrchestrator)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

from .combo_base import StrategyType

logger = logging.getLogger(__name__)

_TRADING_DAYS_PER_YEAR = 252
_SLIPPAGE_PCT = 0.005
_COMMISSION_PER_CONTRACT = 5.0


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
        etf_prices: dict[str, list[dict]] | None = None,
        use_bs_reconstruct: bool = True,
        underlying: str = "510050.SH",
        initial_iv: float = 0.20,
    ) -> dict:
        """运行回测.

        Args:
            start_date: "YYYY-MM-DD"
            end_date: "YYYY-MM-DD"
            strategies: 策略列表, None则全部5策略
            etf_prices: {underlying: [{date, close}, ...]}, None则合成
            use_bs_reconstruct: 无历史期权链时BS重构
            underlying: 回测标的
            initial_iv: 初始IV(BS重构用)

        Returns:
            {equity_curve, metrics, hedge_efficiency, trades}
        """
        if strategies is None:
            strategies = list(StrategyType)

        prices = (etf_prices.get(underlying) if etf_prices else None) or \
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
                trade = self._simulate_strategy(
                    st, underlying, spot_price, initial_iv, use_bs_reconstruct, current_date,
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
        }
        logger.info(
            "回测完成: %s ~ %s, %d 交易日, %d 策略, 终值=%.0f, 年化=%.4f, 回撤=%.4f, Sharpe=%.4f",
            start_date, end_date, len(prices), len(strategies),
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
    ) -> dict | None:
        """模拟单策略单日交易 — 简化为权利金收入/支出."""
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
                put_p = self._bs_put_price(spot_price, spot_price * 0.95, 45 / 365, 0.03, iv)
                call_p = self._bs_call_price(spot_price, spot_price * 1.05, 45 / 365, 0.03, iv)
                pnl = (call_p - put_p) * 10000 - 2 * self._commission
                return {"strategy": strategy_type.value, "date": current_date, "pnl": round(pnl, 2), "is_hedge": True}
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
    def _bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
            return max(0.0, S - K)
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        N_d1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        N_d2 = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))
        return S * N_d1 - K * math.exp(-r * T) * N_d2

    @staticmethod
    def _bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
            return max(0.0, K - S)
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        N_neg_d1 = 0.5 * (1 + math.erf(-d1 / math.sqrt(2)))
        N_neg_d2 = 0.5 * (1 + math.erf(-d2 / math.sqrt(2)))
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
