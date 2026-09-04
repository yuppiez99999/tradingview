# -*- coding: utf-8 -*-
"""portfolio_etf200w_backtest.py — 200 万 ETF 持仓月度再平衡组合回测。

标的: config/portfolio.yaml v5.10 中的 6 只 ETF, 权重归一化到 200 万:
  sh510300 沪深300ETF  8/35 ≈ 22.86%
  sh510500 中证500ETF  6/35 ≈ 17.14%
  sh512100 中证1000ETF 4/35 ≈ 11.43%
  sh588000 科创50ETF   6/35 ≈ 17.14%
  sz159915 创业板ETF   6/35 ≈ 17.14%
  sh518880 黄金ETF     5/35 ≈ 14.29%

规则:
  - 窗口: 2023-09-01 ~ 数据末日 (前复权日K, 本地缓存)
  - 建仓: 窗口首个交易日开盘价买入
  - 再平衡: 每月最后一个交易日收盘产生信号, 次日开盘执行恢复目标权重
  - 费用: 佣金万3 (最低5元, 双边); ETF 免印花税
  - A股规则: 100 份整手, T+1 (月末信号次日执行天然满足)
  - 期末: 最后交易日收盘价强制平仓 (强平单)
  - 对照: 同初始权重的买入持有基准

运行: python portfolio_etf200w_backtest.py
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
# skill 参考库缓存根 (user 级缓存); 缺失时保持原 ImportError 语义
EXPERT_CACHE = Path.home() / ".workbuddy" / "plugins" / "cache" / "experts"
REF_DIR = EXPERT_CACHE / "strategy-backtest-expert/1.0.0/skills/quant-backtest-lab/reference"
sys.path.insert(0, str(REF_DIR))
from export_results import export_results  # noqa: E402

# ---------------------------------------------------------------- 参数
CAPITAL = 2_000_000.0
START = "2023-09-01"
COMMISSION = 0.0003          # 佣金 万3
MIN_COMMISSION = 5.0         # 单笔最低 5 元
LOT = 100                    # 100 份整手
DATA_CSV = HERE.parent / "v510_portfolio_20260903" / "klines_qfq.csv"

# symbol -> (名称, 目标权重[归一化])
ETFS: dict[str, tuple[str, float]] = {
    "sh510300": ("沪深300ETF", 8 / 35),
    "sh510500": ("中证500ETF", 6 / 35),
    "sh512100": ("中证1000ETF", 4 / 35),
    "sh588000": ("科创50ETF", 6 / 35),
    "sz159915": ("创业板ETF", 6 / 35),
    "sh518880": ("黄金ETF", 5 / 35),
}
WEIGHTS = {s: w for s, (_, w) in ETFS.items()}
SYMBOLS = list(ETFS)


def commission(amount: float) -> float:
    return max(amount * COMMISSION, MIN_COMMISSION) if amount > 0 else 0.0


def load_data() -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, dtype={"symbol": str})
    df = df[df.symbol.isin(SYMBOLS)]
    if set(df.symbol) != set(SYMBOLS):
        raise SystemExit(f"数据缺失: {set(SYMBOLS) - set(df.symbol)}")
    return df.sort_values("date")


def run(rebalance: bool, prefix: str) -> dict:
    df = load_data()
    px = df.pivot(index="date", columns="symbol", values="close").sort_index()
    op = df.pivot(index="date", columns="symbol", values="open").sort_index()
    dates = [d for d in px.index if d >= START]
    if len(dates) < 60:
        raise SystemExit("评估窗口不足 60 个交易日")

    # 月末信号日: 每个日历月的最后一个交易日 (窗口内)
    month_of = [d[:7] for d in dates]
    month_end_idx = []
    for i in range(len(dates)):
        if i == len(dates) - 1 or month_of[i] != month_of[i + 1]:
            month_end_idx.append(i)

    cash = CAPITAL
    shares = {s: 0 for s in SYMBOLS}          # 持仓份额
    avg_cost = {s: 0.0 for s in SYMBOLS}      # 含佣均价
    lot_open_date = {s: None for s in SYMBOLS}  # 当前仓位开仓日 (trades 记录用)
    lot_open_bar = {s: 0 for s in SYMBOLS}
    equity_curve: list[dict] = []
    trade_history: list[dict] = []
    rebalance_events: list[dict] = []
    total_cost = 0.0
    n_rebalance = 0
    bar = {d: i for i, d in enumerate(dates)}

    def buy(sym: str, d: str, size: int, price: float) -> None:
        nonlocal cash
        if size <= 0:
            return
        amount = size * price
        fee = commission(amount)
        cash -= amount + fee
        # 更新含佣均价
        old_v = shares[sym] * avg_cost[sym]
        shares[sym] += size
        avg_cost[sym] = (old_v + amount + fee) / shares[sym]
        if shares[sym] == size:  # 新开仓
            lot_open_date[sym] = d
            lot_open_bar[sym] = bar[d]

    def sell(sym: str, d: str, size: int, price: float) -> None:
        nonlocal cash
        size = min(size, shares[sym])
        if size <= 0:
            return
        amount = size * price
        fee = commission(amount)
        cash += amount - fee
        cost = avg_cost[sym] * size
        trade_history.append({
            "entry_date": lot_open_date[sym],
            "exit_date": d,
            "side": "long",
            "size": size,
            "entry_price": round(avg_cost[sym], 4),
            "exit_price": round(price, 4),
            "pnl": round(amount - fee - cost, 2),
            "pnl_pct": round((amount - fee) / cost - 1, 6) * 100 if cost > 0 else 0.0,
            "holding_bars": bar[d] - lot_open_bar[sym],
            "symbol": sym,
            "symbol_name": ETFS[sym][0],
        })
        shares[sym] -= size
        if shares[sym] == 0:
            avg_cost[sym] = 0.0
            lot_open_date[sym] = None

    # ---- 建仓: 窗口首日开盘 ----
    d0 = dates[0]
    for s in SYMBOLS:
        p = float(op.loc[d0, s])
        target_v = CAPITAL * WEIGHTS[s]
        size = int(target_v / p // LOT) * LOT
        buy(s, d0, size, p)

    # ---- 逐日 ----
    pending_rebalance = False
    for i, d in enumerate(dates):
        # 次日开盘执行月末再平衡 (信号产生于昨日收盘, 无 look-ahead)
        if pending_rebalance and rebalance:
            pending_rebalance = False
            o = {s: float(op.loc[d, s]) for s in SYMBOLS}
            total_v = cash + sum(shares[s] * o[s] for s in SYMBOLS)
            traded = 0.0
            # 先卖超配, 再买低配
            for s in SYMBOLS:
                target_v = total_v * WEIGHTS[s]
                delta_v = target_v - shares[s] * o[s]
                if delta_v < 0:
                    size = int((-delta_v) / o[s] // LOT) * LOT
                    if size >= LOT:
                        sell(s, d, size, o[s])
                        traded += size * o[s]
            for s in SYMBOLS:
                target_v = total_v * WEIGHTS[s]
                delta_v = target_v - shares[s] * o[s]
                if delta_v > 0:
                    size = int(delta_v / o[s] // LOT) * LOT
                    if size >= LOT:
                        buy(s, d, size, o[s])
                        traded += size * o[s]
            if traded > 0:
                n_rebalance += 1
                rebalance_events.append(
                    {"date": d, "turnover": round(traded, 0)})

        # 当日是否月末信号日 → 明日执行
        if i in month_end_idx and i < len(dates) - 1:
            pending_rebalance = True

        # 收盘估值
        c = {s: float(px.loc[d, s]) for s in SYMBOLS}
        equity_curve.append({
            "date": d,
            "value": round(cash + sum(shares[s] * c[s] for s in SYMBOLS), 2),
        })

    # ---- 期末强平 (最后交易日收盘价) ----
    dN = dates[-1]
    for s in SYMBOLS:
        sell(s, dN, shares[s], float(px.loc[dN, s]))
    equity_curve[-1]["value"] = round(cash, 2)  # 强平后全现金

    total_cost = sum(
        commission(t["size"] * t["entry_price"]) for t in trade_history
    ) + sum(commission(t["size"] * t["exit_price"]) for t in trade_history)

    paths = export_results(
        equity_curve=equity_curve,
        trade_history=trade_history,
        prefix=prefix,
        initial_cash=CAPITAL,
        start=START,
        end=dates[-1],
        market="china_a",
        output_dir=HERE,
        strategy_name=f"ETF200万{'月度再平衡' if rebalance else '买入持有'}",
        symbol=",".join(SYMBOLS),
        is_flat_at_end=True,
    )
    # 月度再平衡执行日志
    if rebalance:
        with open(HERE / f"{prefix}_rebalance_log.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "turnover_cny"])
            for e in rebalance_events:
                w.writerow([e["date"], e["turnover"]])

    return {
        "prefix": prefix,
        "equity_curve": equity_curve,
        "trade_history": trade_history,
        "n_rebalance": n_rebalance,
        "total_cost": round(total_cost, 2),
        "n_days": len(dates),
        "start": dates[0],
        "end": dates[-1],
        "paths": {k: str(v) for k, v in paths.items()},
    }


def main() -> None:
    r1 = run(rebalance=True, prefix="etf200w")
    r0 = run(rebalance=False, prefix="etf200wbh")

    for r in (r1, r0):
        print(f"\n=== {r['prefix']} ===")
        print(f"窗口: {r['start']} ~ {r['end']}  ({r['n_days']} 个交易日)")
        print(f"再平衡次数: {r['n_rebalance']}  平仓笔数: {len(r['trade_history'])}")
        print(f"估计双边费用合计: ¥{r['total_cost']:,}")
        print(f"期末强平后现金: ¥{r['equity_curve'][-1]['value']:,.2f}")
        print("产物:", "; ".join(r["paths"].values()))

    # ---- 摘要对比 ----
    import json
    s1 = json.loads((HERE / "etf200w_summary.json").read_text(encoding="utf-8"))["summary"]
    s0 = json.loads((HERE / "etf200wbh_summary.json").read_text(encoding="utf-8"))["summary"]
    print("\n=== 月度再平衡 vs 买入持有 ===")
    for k in ("total_return_pct", "annual_return_pct", "max_drawdown_pct", "sharpe", "win_rate_pct"):
        print(f"{k:>20}: {s1[k]:>10.3f}  vs  {s0[k]:>10.3f}")


if __name__ == "__main__":
    main()
