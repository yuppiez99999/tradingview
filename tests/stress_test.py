"""
组合压力测试脚本
验证2026-2030年期间年化收益>=8%，回撤<15%
"""

import numpy as np
import pandas as pd
import yaml


def load_positions():
    # B1.7: 委托给 utils.positions_loader 统一入口 (P2: 用默认路径, 不传相对路径)
    from utils.positions_loader import load_positions as _load

    return _load()


def load_portfolio():
    with open("configs/portfolio.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def simulate_market_scenario(start_date, end_date, scenario="moderate"):
    dates = pd.date_range(start_date, end_date, freq="B")
    n_days = len(dates)

    scenarios = {
        "moderate": {
            "mean_daily": 0.0005,
            "vol_daily": 0.012,
            "crash_prob": 0.002,
            "crash_size": -0.05,
        },
        "bull": {
            "mean_daily": 0.0008,
            "vol_daily": 0.010,
            "crash_prob": 0.001,
            "crash_size": -0.04,
        },
        "bear": {
            "mean_daily": -0.0001,
            "vol_daily": 0.015,
            "crash_prob": 0.003,
            "crash_size": -0.06,
        },
    }

    params = scenarios[scenario]
    returns = np.random.normal(params["mean_daily"], params["vol_daily"], n_days)

    crash_indices = np.random.choice(
        range(n_days), int(n_days * params["crash_prob"]), replace=False
    )
    returns[crash_indices] = params["crash_size"]

    return pd.Series(returns, index=dates)


def determine_market_state(cumulative_return, volatility):
    if cumulative_return > 0.2 and volatility < 0.012:
        return "bull"
    elif cumulative_return < -0.15 or volatility > 0.025:
        return "crisis"
    elif cumulative_return < -0.05 or volatility > 0.018:
        return "bear"
    else:
        return "neutral"


def get_dynamic_hedge_ratio(market_state):
    hedge_ratios = {
        "bull": 0.20,
        "neutral": 0.40,
        "bear": 0.75,
        "crisis": 0.90,
    }
    return hedge_ratios.get(market_state, 0.40)


def calculate_portfolio_returns(
    positions, market_returns, hedge_ratio=0.5, market_state="neutral"
):
    sector_amounts = {}
    total_amount = 0
    for _code, pos in positions["positions"].items():
        sector = pos.get("sector", "其他")
        amount = pos.get("amount", 0)
        if sector not in sector_amounts:
            sector_amounts[sector] = 0
        sector_amounts[sector] += amount
        total_amount += amount

    if total_amount == 0:
        return 0

    sector_weights = {
        sector: amount / total_amount for sector, amount in sector_amounts.items()
    }

    sector_beta = {
        "科技": 1.3,
        "医药": 1.1,
        "宽基": 1.0,
        "金融": 0.9,
        "新能源": 1.4,
        "资源": 0.8,
        "防御": 0.5,
        "制造": 1.1,
        "顺周期": 1.0,
        "成长": 1.2,
        "国债": 0.1,
        "其他": 1.0,
    }

    portfolio_beta = sum(
        sector_weights.get(sector, 0) * sector_beta.get(sector, 1.0)
        for sector in sector_weights
    )

    sector_alpha = {
        "防御": 0.0005,
        "国债": 0.0003,
        "资源": 0.0002,
        "宽基": 0.0001,
        "金融": 0.0001,
        "医药": -0.0001,
        "顺周期": -0.0001,
        "制造": -0.0002,
        "科技": -0.0002,
        "成长": -0.0002,
        "新能源": -0.0003,
        "其他": 0.0,
    }

    portfolio_alpha = sum(
        sector_weights.get(sector, 0) * sector_alpha.get(sector, 0)
        for sector in sector_weights
    )

    effective_beta = portfolio_beta - hedge_ratio

    systematic_return = effective_beta * market_returns

    idiosyncratic_return = np.random.normal(0, 0.004)

    gross_return = systematic_return + portfolio_alpha + idiosyncratic_return

    transaction_cost = 0.00005

    net_return = gross_return - transaction_cost

    return net_return


def calculate_max_drawdown(equity_curve):
    max_so_far = equity_curve.cummax()
    drawdown = (equity_curve - max_so_far) / max_so_far
    return drawdown.min()


def calculate_annualized_return(equity_curve, days):
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
    years = days / 252
    return (1 + total_return) ** (1 / years) - 1


def calculate_drawdown_scale(equity_series, current_index, max_drawdown_limit=0.15):
    current_equity = equity_series.iloc[current_index]
    max_equity = equity_series.iloc[: current_index + 1].max()
    current_drawdown = 1 - current_equity / max_equity

    if current_drawdown < 0.05:
        return 1.0
    elif current_drawdown < 0.10:
        return 0.8
    elif current_drawdown < 0.12:
        return 0.6
    elif current_drawdown < max_drawdown_limit:
        return 0.4
    else:
        return 0.2


def main():
    positions = load_positions()
    portfolio = load_portfolio()

    start_date = portfolio.get("start_date", "2026-07-13")
    end_date = portfolio.get("clearance_date", "2030-12-31")

    print("=== 组合压力测试 ===")
    print(f"测试周期: {start_date} ~ {end_date}")
    print(f"初始资金: {portfolio.get('stock_etf_capital', 4000000):,} 元")
    print(f"目标年化: {portfolio.get('target_annual_return', 0.08) * 100:.1f}%")
    print(f"最大回撤限制: {portfolio.get('target_max_drawdown', 0.15) * 100:.1f}%")
    print()

    scenarios = ["moderate", "bull", "bear"]
    results = []

    for scenario in scenarios:
        print(f"--- 情景: {scenario} ---")
        np.random.seed(42)
        market_returns = simulate_market_scenario(start_date, end_date, scenario)

        equity = pd.Series(1.0, index=market_returns.index)
        market_equity = pd.Series(1.0, index=market_returns.index)
        hedge_ratios = []
        drawdown_scales = []

        for i, _date in enumerate(market_returns.index):
            if i == 0:
                continue

            market_equity.iloc[i] = market_equity.iloc[i - 1] * (
                1 + market_returns.iloc[i]
            )
            cum_return = market_equity.iloc[i] - 1

            lookback = min(i, 60)
            volatility = market_returns.iloc[i - lookback : i].std()

            market_state = determine_market_state(cum_return, volatility)
            hedge_ratio = get_dynamic_hedge_ratio(market_state)
            hedge_ratios.append(hedge_ratio)

            drawdown_scale = calculate_drawdown_scale(equity, i - 1)
            drawdown_scales.append(drawdown_scale)

            daily_return = calculate_portfolio_returns(
                positions, market_returns.iloc[i], hedge_ratio, market_state
            )
            adjusted_return = daily_return * drawdown_scale
            equity.iloc[i] = equity.iloc[i - 1] * (1 + adjusted_return)

        days = len(market_returns)
        annualized_return = calculate_annualized_return(equity, days)
        max_drawdown = calculate_max_drawdown(equity)
        total_return = equity.iloc[-1] - 1
        avg_hedge_ratio = np.mean(hedge_ratios) if hedge_ratios else 0.4
        avg_drawdown_scale = np.mean(drawdown_scales) if drawdown_scales else 1.0

        results.append(
            {
                "scenario": scenario,
                "annualized_return": annualized_return,
                "max_drawdown": max_drawdown,
                "total_return": total_return,
                "avg_hedge_ratio": avg_hedge_ratio,
                "avg_drawdown_scale": avg_drawdown_scale,
                "pass_return": annualized_return >= 0.08,
                "pass_drawdown": abs(max_drawdown) < 0.15,
            }
        )

        print(
            f"年化收益: {annualized_return * 100:.2f}% {'✓' if annualized_return >= 0.08 else '✗'}"
        )
        print(
            f"最大回撤: {max_drawdown * 100:.2f}% {'✓' if abs(max_drawdown) < 0.15 else '✗'}"
        )
        print(f"累计收益: {total_return * 100:.2f}%")
        print(f"平均对冲比率: {avg_hedge_ratio * 100:.1f}%")
        print(f"平均回撤熔断系数: {avg_drawdown_scale:.2f}")
        print()

    print("=== 测试结果汇总 ===")
    print(
        f"{'情景':<10} {'年化收益':<12} {'最大回撤':<12} {'收益达标':<8} {'回撤达标':<8}"
    )
    print(f"{'---':<10} {'---':<12} {'---':<12} {'---':<8} {'---':<8}")
    for r in results:
        print(
            f"{r['scenario']:<10} {r['annualized_return']*100:<11.2f}% {r['max_drawdown']*100:<11.2f}% {'✓' if r['pass_return'] else '✗':<8} {'✓' if r['pass_drawdown'] else '✗':<8}"  # noqa: E501
        )

    all_pass = all(r["pass_return"] and r["pass_drawdown"] for r in results)
    print()
    if all_pass:
        print("✓ 所有情景测试通过！")
    else:
        print("✗ 部分情景测试未通过，建议调整配置")


if __name__ == "__main__":
    main()
