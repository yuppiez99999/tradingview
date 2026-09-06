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
  - 风控 (P1-11): 买入做现金含费校验, 永不透支; 停牌/缺K线日不可成交
    (买卖跳过该标的), 持仓估值沿用最近有效收盘价, 杜绝 NaN 污染权益曲线
  - 交易成本 (P1-14): 单边滑点 ETF 5bp 计入成交价 (买入上浮/卖出下浮)
  - 涨跌停 (P1-14): 开盘触及涨停价不可买入 / 跌停价不可卖出
    (涨跌幅限制: 科创50/创业板ETF 20%, 其余 ETF 10%)
  - 期末: 最后交易日收盘价强制平仓 (强平单; 末日停牌以最近收盘近似)
  - 对照: 同初始权重的买入持有基准

局限 (P1-13): 本回测是"固定成分条件回测" —— 标的清单取自当前 config/
  portfolio.yaml (v5.10) 快照并用其回溯 2023-09 以来的历史, 存在幸存者
  偏差 (以今日选出的组合回测过去, 不等于当时可实现的策略)。结论仅代表
  "若自 2023-09-01 起按此清单持有"的情景, 不可外推为组合真实历史业绩。
  脚本在 load_data 中守卫: 任何标的数据起点晚于评估窗口起点即拒绝运行,
  防止未来把新上市标的拉入旧窗口产生真正的前视偏差。

运行: python portfolio_etf200w_backtest.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
# P1-10: 共享工具模块内联到项目内副本 (backtests/_etf_rotation_2014),
# 不再依赖外部插件缓存目录 (~/.workbuddy/plugins/cache/...)。
REF_DIR = HERE.parent / "_etf_rotation_2014"
if not (REF_DIR / "export_results.py").exists():
    raise SystemExit(
        f"缺少共享工具模块副本: {REF_DIR / 'export_results.py'}"
    )
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


# ---- P1-14: 交易成本与涨跌停建模 ----
SLIPPAGE = 0.0005   # ETF 单边滑点 5bp (买入成交价上浮, 卖出下浮)


def fill_buy_px(open_px: float) -> float:
    return open_px * (1 + SLIPPAGE)


def fill_sell_px(open_px: float) -> float:
    return open_px * (1 - SLIPPAGE)


def limit_pct(sym: str) -> float:
    """涨跌幅限制: 科创50ETF(sh588000)/创业板ETF(sz159915) 20%, 其余 10%。"""
    return 0.20 if sym in ("sh588000", "sz159915") else 0.10


def buy_blocked(prev_close: float | None, open_px: float, sym: str) -> bool:
    """开盘即封涨停 -> 当日买不进 (prev_close 缺失时不做限制)。"""
    if prev_close is None or pd.isna(prev_close) or prev_close <= 0:
        return False
    return open_px >= prev_close * (1 + limit_pct(sym)) - 1e-9


def sell_blocked(prev_close: float | None, open_px: float, sym: str) -> bool:
    """开盘即封跌停 -> 当日卖不出 (prev_close 缺失时不做限制)。"""
    if prev_close is None or pd.isna(prev_close) or prev_close <= 0:
        return False
    return open_px <= prev_close * (1 - limit_pct(sym)) + 1e-9


def load_data() -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, dtype={"symbol": str})
    df = df[df.symbol.isin(SYMBOLS)]
    if set(df.symbol) != set(SYMBOLS):
        raise SystemExit(f"数据缺失: {set(SYMBOLS) - set(df.symbol)}")
    # P1-13: 防前视 —— 任何标的数据起点晚于评估窗口起点即拒绝
    first_dates = df.groupby("symbol")["date"].min()
    late = {s: d for s, d in first_dates.items() if d > START}
    if late:
        raise SystemExit(
            f"标的上市/数据起点晚于评估窗口起点 {START}, 纳入回测会形成"
            f"前视偏差(幸存者): {late}")
    return df.sort_values("date")


def run(rebalance: bool, prefix: str) -> dict:
    df = load_data()
    px = df.pivot(index="date", columns="symbol", values="close").sort_index()
    op = df.pivot(index="date", columns="symbol", values="open").sort_index()
    # P1-11: 停牌日(缺K线)估值沿用最近有效收盘价 (ffill 只回溯、无前视);
    # 成交仍以原生 open 为准 —— 停牌不可成交, 见下方 tradeable 过滤。
    px_mark = px.ffill()

    def mark_px(sym: str, d: str) -> float:
        """停牌兜底估值价: 最近收盘; 该标的当日仍无任何历史收盘时按 0 处理(未持仓)。"""
        v = px_mark.loc[d, sym]
        return 0.0 if pd.isna(v) else float(v)

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
        if size <= 0 or pd.isna(price) or price <= 0:
            return
        # P1-11: 现金含费校验 —— 成交总成本不得超过可用现金, 超额按整手递减,
        # 保证 cash 恒 >= 0, 杜绝透支成交。
        while size >= LOT and size * price + commission(size * price) > cash + 1e-9:
            size -= LOT
        if size < LOT:
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
        if size <= 0 or pd.isna(price) or price <= 0:
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

    # ---- 逐日 (建仓: 首日开盘买入, 停牌顺延至复牌日; P1-11) ----
    pending_rebalance = False
    to_buy = {s: CAPITAL * WEIGHTS[s] for s in SYMBOLS}
    for i, d in enumerate(dates):
        # 建仓: 当日有开盘价且未封涨停即成交, 停牌/涨停时顺延
        if to_buy:
            prev_c = px.loc[dates[i - 1]] if i > 0 else None
            for s in [s for s in to_buy if not pd.isna(op.loc[d, s])]:
                open_px = float(op.loc[d, s])
                if buy_blocked(
                        None if prev_c is None else prev_c[s], open_px, s):
                    continue  # 一字涨停买不进, 顺延下日
                size = int(to_buy[s] / open_px // LOT) * LOT
                buy(s, d, size, fill_buy_px(open_px))
                del to_buy[s]
        # 次日开盘执行月末再平衡 (信号产生于昨日收盘, 无 look-ahead)
        if pending_rebalance and rebalance:
            pending_rebalance = False
            # P1-11: 停牌/缺K线标的当日不可成交 —— 从本轮买卖中剔除,
            # 其持仓按 mark_px(最近收盘) 计入组合市值, 待复牌后下个信号日再调。
            tradeable = [s for s in SYMBOLS if not pd.isna(op.loc[d, s])]
            prev_c = px.loc[dates[i - 1]] if i > 0 else None
            o = {s: float(op.loc[d, s]) for s in tradeable}
            # P1-14: 开盘封涨停不可买、封跌停不可卖, 该标的当轮买卖剔除
            sellable = [s for s in tradeable if not sell_blocked(
                None if prev_c is None else prev_c[s], o[s], s)]
            buyable = [s for s in tradeable if not buy_blocked(
                None if prev_c is None else prev_c[s], o[s], s)]
            total_v = cash + sum(shares[s] * mark_px(s, d) for s in SYMBOLS)
            traded = 0.0
            # 先卖超配, 再买低配
            for s in sellable:
                target_v = total_v * WEIGHTS[s]
                delta_v = target_v - shares[s] * o[s]
                if delta_v < 0:
                    size = int((-delta_v) / o[s] // LOT) * LOT
                    if size >= LOT:
                        sell(s, d, size, fill_sell_px(o[s]))
                        traded += size * o[s]
            for s in buyable:
                target_v = total_v * WEIGHTS[s]
                delta_v = target_v - shares[s] * o[s]
                if delta_v > 0:
                    size = int(delta_v / o[s] // LOT) * LOT
                    if size >= LOT:
                        buy(s, d, size, fill_buy_px(o[s]))
                        traded += size * o[s]
            if traded > 0:
                n_rebalance += 1
                rebalance_events.append(
                    {"date": d, "turnover": round(traded, 0)})

        # 当日是否月末信号日 → 明日执行
        if i in month_end_idx and i < len(dates) - 1:
            pending_rebalance = True

        # 收盘估值 (P1-11: 停牌沿用最近收盘, 未持仓标的按 0, 杜绝 NaN 传染)
        c = {s: mark_px(s, d) for s in SYMBOLS}
        equity_curve.append({
            "date": d,
            "value": round(cash + sum(shares[s] * c[s] for s in SYMBOLS), 2),
        })

    # ---- 期末强平 (最后交易日收盘价; 末日停牌以最近有效收盘近似) ----
    d_last = dates[-1]
    for s in SYMBOLS:
        px_last = px_mark.loc[d_last, s]
        if not pd.isna(px_last):
            sell(s, d_last, shares[s], fill_sell_px(float(px_last)))
    # 期末权益 = 现金 + 未能强平的残余持仓市值(按最近收盘, 正常情况下残余为 0)
    end_value = cash + sum(shares[s] * mark_px(s, d_last) for s in SYMBOLS)
    equity_curve[-1]["value"] = round(end_value, 2)

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
