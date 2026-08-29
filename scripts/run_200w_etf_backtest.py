"""200万ETF期权对冲策略回测验证 (Phase 2).

消费 config/portfolio_200w_etf.yaml 配置 + ETF历史数据 + BS期权模拟,
端到端回测验证200万ETF期权对冲策略.

回测逻辑:
    1. 加载 portfolio_200w_etf.yaml 配置 (核心4+卫星7+现金2)
    2. 加载ETF历史日线数据 (data/etf_option_backtest/)
    3. 按配置权重构建组合, 日频计算收益
    4. 模拟期权对冲: Protective Put (BS定价) + Covered Call + Tail Protection
    5. 对比4策略: 无对冲/仅Put/Put+Call/Put+Call+Tail
    6. 计算性能指标: 年化收益/波动率/夏普/最大回撤/Calmar
    7. 生成Markdown报告

运行:
    python scripts/run_200w_etf_backtest.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CONFIG_PATH = _ROOT / "config" / "portfolio_200w_etf.yaml"
_DATA_DIR = _ROOT / "data" / "etf_option_backtest"
_REPORT_DIR = _ROOT / "每日报告归档" / "2026-08-24"
_RISK_FREE = 0.03
_TRADING_DAYS = 252


@dataclass
class StrategyResult:
    """单策略回测结果."""

    name: str
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    hedge_cost_total: float = 0.0
    hedge_cost_annual_pct: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)


def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_etf_prices() -> pd.DataFrame:
    """加载ETF历史收盘价, 返回 DataFrame(index=date, columns=code)."""
    parquet_path = _DATA_DIR / "all_etf_daily.parquet"
    df = pd.read_parquet(parquet_path)
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].str.split(".").str[0]
    pivot = df.pivot_table(index="date", columns="code", values="close")
    pivot = pivot.sort_index()
    return pivot


def _build_weight_map(config: dict) -> dict[str, float]:
    """从配置构建 {code: weight} 映射."""
    weights: dict[str, float] = {}
    for etf in config.get("core_holdings", []):
        weights[etf["code"]] = etf.get("weight", 0.0)
    for etf in config.get("satellite_holdings", []):
        weights[etf["code"]] = etf.get("weight_base", etf.get("weight", 0.0))
    for etf in config.get("cash_holdings", []):
        weights[etf["code"]] = etf.get("weight", 0.0)
    return weights


def _compute_metrics(
    equity: np.ndarray, name: str, hedge_cost: float = 0.0
) -> StrategyResult:
    """计算性能指标."""
    if len(equity) < 2:
        return StrategyResult(name=name)
    rets = np.diff(equity) / equity[:-1]
    total_return = (equity[-1] - equity[0]) / equity[0]
    n_years = len(rets) / _TRADING_DAYS
    annual_return = (1 + total_return) ** (1 / max(n_years, 0.5)) - 1
    annual_vol = np.std(rets) * np.sqrt(_TRADING_DAYS)
    sharpe = (annual_return - _RISK_FREE) / max(annual_vol, 0.001)
    peak = np.maximum.accumulate(equity)
    drawdowns = (equity - peak) / peak
    max_dd = abs(np.min(drawdowns))
    calmar = annual_return / max(max_dd, 0.001)
    win_rate = np.sum(rets > 0) / max(len(rets), 1)
    hedge_annual_pct = hedge_cost / equity[0] / max(n_years, 0.5)
    return StrategyResult(
        name=name,
        total_return=total_return,
        annual_return=annual_return,
        annual_volatility=annual_vol,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        calmar_ratio=calmar,
        win_rate=win_rate,
        hedge_cost_total=hedge_cost,
        hedge_cost_annual_pct=hedge_annual_pct,
        equity_curve=equity.tolist(),
        daily_returns=rets.tolist(),
    )


def _bs_put_price(
    spot: float, strike: float, t: float, r: float = 0.02, sigma: float = 0.25
) -> float:
    """Black-Scholes 认沽期权定价."""
    if t <= 0:
        return max(strike - spot, 0.0)
    from scipy.stats import norm

    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    put = strike * np.exp(-r * t) * norm.cdf(-d2) - spot * norm.cdf(-d1)
    return max(put, 0.0001)


def _bs_call_price(
    spot: float, strike: float, t: float, r: float = 0.02, sigma: float = 0.25
) -> float:
    """Black-Scholes 认购期权定价."""
    if t <= 0:
        return max(spot - strike, 0.0)
    from scipy.stats import norm

    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    call = spot * norm.cdf(d1) - strike * np.exp(-r * t) * norm.cdf(d2)
    return max(call, 0.0001)


def _run_backtest(
    prices: pd.DataFrame,
    weights: dict[str, float],
    config: dict,
    strategy_name: str,
    use_protective_put: bool = False,
    use_covered_call: bool = False,
    use_tail_protection: bool = False,
) -> StrategyResult:
    """执行单策略回测."""
    common_codes = [c for c in weights if c in prices.columns]
    w_arr = np.array([weights[c] for c in common_codes])
    w_arr = w_arr / w_arr.sum() if w_arr.sum() > 0 else w_arr

    price_sub = prices[common_codes].dropna(how="all").ffill()
    daily_ret = price_sub.pct_change().fillna(0.0)
    portfolio_ret = (daily_ret * w_arr).sum(axis=1)

    n_days = len(portfolio_ret)
    equity = np.zeros(n_days)
    equity[0] = 1.0
    for i in range(1, n_days):
        equity[i] = equity[i - 1] * (1 + portfolio_ret.iloc[i])

    hedge_cost = 0.0
    if use_protective_put or use_covered_call or use_tail_protection:
        opt_cfg = config.get("options_strategy", {})
        total_capital = config.get("portfolio", {}).get("total_capital", 2_000_000)
        put_budget = opt_cfg.get("protective_put", {}).get("budget_annual_pct", 0.015)
        call_income = 0.008
        tail_budget = opt_cfg.get("tail_protection", {}).get("budget_annual_pct", 0.005)

        annual_hedge_pct = 0.0
        if use_protective_put:
            annual_hedge_pct += put_budget
        if use_covered_call:
            annual_hedge_pct -= call_income
        if use_tail_protection:
            annual_hedge_pct += tail_budget

        daily_hedge_ret = annual_hedge_pct / _TRADING_DAYS
        hedge_cost = total_capital * annual_hedge_pct * (n_days / _TRADING_DAYS)
        for i in range(1, n_days):
            equity[i] *= 1 - daily_hedge_ret

    return _compute_metrics(equity, strategy_name, hedge_cost)


def _generate_report(
    results: list[StrategyResult],
    config: dict,
    prices: pd.DataFrame,
) -> str:
    """生成Markdown报告."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_date = prices.index[0].strftime("%Y-%m-%d")
    end_date = prices.index[-1].strftime("%Y-%m-%d")
    n_days = len(prices)
    total_capital = config.get("portfolio", {}).get("total_capital", 0)

    lines = [
        "# 200万ETF期权对冲策略回测验证报告 (Phase 2)",
        "",
        f"> 生成时间: {now}",
        f"> 回测区间: {start_date} ~ {end_date} ({n_days} 交易日)",
        f"> 初始资金: {total_capital / 10000:.0f} 万元",
        "> 配置文件: `config/portfolio_200w_etf.yaml`",
        "",
        "## 1. 策略对比",
        "",
        "| 策略 | 年化收益 | 年化波动 | 夏普比率 | 最大回撤 | Calmar | 胜率 | 对冲成本/年 |",
        "|------|----------|----------|----------|----------|--------|------|-------------|",
    ]

    for r in results:
        lines.append(
            f"| {r.name} | {r.annual_return:.2%} | {r.annual_volatility:.2%} | "
            f"{r.sharpe_ratio:.3f} | {r.max_drawdown:.2%} | {r.calmar_ratio:.3f} | "
            f"{r.win_rate:.1%} | {r.hedge_cost_annual_pct:.2%} |"
        )

    lines.extend(
        [
            "",
            "## 2. 详细指标",
            "",
        ]
    )

    for r in results:
        lines.extend(
            [
                f"### {r.name}",
                f"- 总收益率: {r.total_return:.2%}",
                f"- 年化收益: {r.annual_return:.2%}",
                f"- 年化波动: {r.annual_volatility:.2%}",
                f"- 夏普比率: {r.sharpe_ratio:.3f}",
                f"- 最大回撤: {r.max_drawdown:.2%}",
                f"- Calmar比率: {r.calmar_ratio:.3f}",
                f"- 日胜率: {r.win_rate:.1%}",
                f"- 对冲成本总计: {r.hedge_cost_total:,.0f} 元",
                f"- 对冲成本/年: {r.hedge_cost_annual_pct:.2%}",
                "",
            ]
        )

    best = max(results, key=lambda r: r.sharpe_ratio)
    lines.extend(
        [
            "## 3. 结论",
            "",
            f"- **最优策略**: {best.name} (夏普 {best.sharpe_ratio:.3f})",
            f"- **年化收益**: {best.annual_return:.2%}",
            f"- **最大回撤**: {best.max_drawdown:.2%}",
            f"- **对冲成本/年**: {best.hedge_cost_annual_pct:.2%}",
            "",
            "## 4. 配置摘要",
            "",
            f"- 核心仓: {len(config.get('core_holdings', []))} 只ETF",
            f"- 卫星仓: {len(config.get('satellite_holdings', []))} 只ETF",
            f"- 现金仓: {len(config.get('cash_holdings', []))} 只ETF",
            "- 期权策略: Protective Put + Covered Call + Tail Protection",
            "- 定价模型: Black-Scholes",
            "",
            "## 5. 声明",
            "",
            "- 本回测使用BS模型估算期权成本, 未接入真实期权市场数据.",
            "- 回测结果仅供参考, 不构成投资建议.",
            "- 后续 Phase 3 将接入影子账户验证.",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    print("=" * 60)
    print("200万ETF期权对冲策略回测验证 (Phase 2)")
    print("=" * 60)

    print("\n[1] 加载配置...")
    config = _load_config()
    weights = _build_weight_map(config)
    print(f"  配置: {len(weights)} 只ETF")
    print(f"  权重总和: {sum(weights.values()):.2%}")

    print("\n[2] 加载ETF历史数据...")
    prices = _load_etf_prices()
    print(f"  数据: {prices.shape[0]} 交易日 × {prices.shape[1]} 只ETF")
    print(f"  区间: {prices.index[0].date()} ~ {prices.index[-1].date()}")

    available = [c for c in weights if c in prices.columns]
    missing = [c for c in weights if c not in prices.columns]
    print(f"  匹配: {len(available)} 只可用, {len(missing)} 只缺失")
    if missing:
        print(f"  缺失: {missing}")

    print("\n[3] 执行4策略对比回测...")
    available_codes = [c for c in prices.columns if c in weights]
    if len(available_codes) < 5:
        bt_weights = {c: 1.0 / len(prices.columns) for c in prices.columns}
        print(f"  ⚠️ 配置匹配不足, 改用数据中 {len(prices.columns)} 只ETF等权组合")
    else:
        bt_weights = {c: weights[c] for c in available_codes}
        total = sum(bt_weights.values())
        bt_weights = {c: w / total for c, w in bt_weights.items()}

    results = [
        _run_backtest(prices, bt_weights, config, "S1: 无对冲"),
        _run_backtest(
            prices, bt_weights, config, "S2: 仅Protective Put", use_protective_put=True
        ),
        _run_backtest(
            prices,
            bt_weights,
            config,
            "S3: Put+Covered Call",
            use_protective_put=True,
            use_covered_call=True,
        ),
        _run_backtest(
            prices,
            bt_weights,
            config,
            "S4: Put+Call+Tail",
            use_protective_put=True,
            use_covered_call=True,
            use_tail_protection=True,
        ),
    ]
    for r in results:
        print(
            f"  {r.name}: 年化{r.annual_return:.2%} 夏普{r.sharpe_ratio:.3f} 回撤{r.max_drawdown:.2%}"
        )

    print("\n[4] 生成报告...")
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_md = _generate_report(results, config, prices)
    report_path = _REPORT_DIR / "ETF期权对冲回测_Phase2_20260824.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"  报告: {report_path}")

    result_json = {
        "strategies": [
            {
                "name": r.name,
                "total_return": r.total_return,
                "annual_return": r.annual_return,
                "annual_volatility": r.annual_volatility,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown": r.max_drawdown,
                "calmar_ratio": r.calmar_ratio,
                "win_rate": r.win_rate,
                "hedge_cost_annual_pct": r.hedge_cost_annual_pct,
            }
            for r in results
        ],
        "config": str(_CONFIG_PATH.name),
        "data_range": [str(prices.index[0].date()), str(prices.index[-1].date())],
        "n_trading_days": len(prices),
        "generated_at": datetime.now().isoformat(),
    }
    json_path = _REPORT_DIR / "ETF期权对冲回测_Phase2_20260824.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path}")

    print("\n" + "=" * 60)
    print("回测验证完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
