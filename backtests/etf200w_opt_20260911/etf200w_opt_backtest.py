"""etf200w_opt_backtest.py — 200万 ETF+期权组合「配置 + 风控」层回测。

标的与权重取自 config/portfolio_200w_etf.yaml:
  核心 60%: 510300 15% / 510500 15% / 513100 15% / 512890 15%
  卫星 25%: 588000 3.5% / 512760 3.5% / 515070 3.0% / 516160 4.0%
            515790 3.5% / 513180 4.0% / 159920 3.5%
  现金 15%: 511010 10% / 511880 5%
  基准: 沪深300 价格指数 (sh000300)

回测的规则口径 (全部来自 config 的 rebalance / drawdown_action 段):
  1. 定期再平衡: 每年 3/9/12 月最后一个交易日收盘产生信号, 次一交易日开盘执行
  2. 类别偏离触发: 核心/卫星/现金 任一类别实际权重偏离目标 >= 5 个百分点 ->
     次一交易日开盘调回 (config 写"1-2周内", 本回测取最严格的次日开盘)
  3. 回撤分级减仓 (config drawdown_action + rebalance.triggered):
       NORMAL  回撤 < 5%   -> 核心60 / 卫星25 / 现金15
       WARN    回撤 8~12%  -> 核心60 / 卫星20 (减20%) / 现金20
       DANGER  回撤 12~15% -> 核心54 (减10%) / 卫星0 (全清) / 现金46
       BREACH  回撤 > 15%  -> 核心40 / 卫星0 / 现金60
     带 3 个百分点滞后: 从高危档回到 NORMAL 需回撤修复到 5% 以下, 避免来回打脸
  4. 现金仓恒以 511010 国债ETF 占 10% 为底, 其余多出现金放 511880 银华日利

信号在当日收盘产生 -> 次一交易日开盘成交 (无前视)。
费用: ETF 佣金万3 (单笔最低5元, 双边), 免印花税, 单边滑点 5bp。
A股规则: 100 份整手; 停牌(缺K线)不可成交, 估值沿用最近有效收盘;
         开盘封涨停不可买 / 封跌停不可卖 (科创类 ETF 限 20%, 其余 10%)。
期末: 最后交易日收盘强制平仓。

不在本回测范围内的部分 (会在报告中说明):
  - 期权 (Protective Put / Covered Call / 尾部保护): 不做定价, 只做年化成本敏感性
  - 卫星景气度轮动: 依赖 PMI/PE分位/资金流三因子打分, 历史序列不可得, 按静态权重处理

运行: py etf200w_opt_backtest.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
REF_DIR = HERE.parent / "_etf_rotation_2014"
if not (REF_DIR / "export_results.py").exists():
    raise SystemExit(f"缺少共享工具模块: {REF_DIR / 'export_results.py'}")
sys.path.insert(0, str(REF_DIR))
from export_results import export_results  # noqa: E402

# ---------------------------------------------------------------- 参数
CAPITAL = 2_000_000.0
START = "2023-09-01"
COMMISSION = 0.0003
MIN_COMMISSION = 5.0
LOT = 100
SLIPPAGE = 0.0005     # 单边 5bp
DATA_CSV = HERE / "klines_qfq.csv"
BENCH = "sh000300"

# 涨跌幅限制: 跟踪指数含创业板/科创板的主题 ETF 为 20%
LIMIT_20 = {"sh588000", "sh512760", "sh515070", "sh516160", "sh515790"}

CORE = ["sh510300", "sh510500", "sh513100", "sh512890"]
SAT = ["sh588000", "sh512760", "sh515070", "sh516160", "sh515790",
       "sh513180", "sz159920"]
CASH = ["sh511010", "sh511880"]
SYMBOLS = CORE + SAT + CASH

NAMES = {
    "sh510300": "沪深300ETF", "sh510500": "中证500ETF", "sh513100": "纳指ETF",
    "sh512890": "红利低波ETF", "sh588000": "科创50ETF", "sh512760": "半导体芯片ETF",
    "sh515070": "人工智能ETF", "sh516160": "新能源ETF", "sh515790": "光伏ETF",
    "sh513180": "恒生科技ETF", "sz159920": "恒生ETF", "sh511010": "国债ETF",
    "sh511880": "银华日利",
}

# 卫星基准权重 (config weight_base)
SAT_BASE = {
    "sh588000": 0.035, "sh512760": 0.035, "sh515070": 0.030,
    "sh516160": 0.040, "sh515790": 0.035, "sh513180": 0.040, "sz159920": 0.035,
}

REGIMES = {
    "NORMAL": {"core": 0.60, "sat_scale": 1.00},
    "WARN":   {"core": 0.60, "sat_scale": 0.80},
    "DANGER": {"core": 0.54, "sat_scale": 0.00},
    "BREACH": {"core": 0.40, "sat_scale": 0.00},
}
DD_WARN, DD_DANGER, DD_BREACH, DD_RECOVER = 0.08, 0.12, 0.15, 0.05


def target_weights(state: str) -> dict[str, float]:
    """按档位给出全组合目标权重 (合计 1.0, 多出现金进 511880)。"""
    r = REGIMES[state]
    w: dict[str, float] = {}
    for s in CORE:
        w[s] = r["core"] / len(CORE)
    for s in SAT:
        w[s] = SAT_BASE[s] * r["sat_scale"]
    cash_total = 1.0 - sum(w.values())
    w["sh511010"] = min(0.10, cash_total)
    w["sh511880"] = cash_total - w["sh511010"]
    return w


def commission(amount: float) -> float:
    return max(amount * COMMISSION, MIN_COMMISSION) if amount > 0 else 0.0


def fill_buy_px(px: float) -> float:
    return px * (1 + SLIPPAGE)


def fill_sell_px(px: float) -> float:
    return px * (1 - SLIPPAGE)


def limit_pct(sym: str) -> float:
    return 0.20 if sym in LIMIT_20 else 0.10


def load_data() -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, dtype={"symbol": str})
    need = set(SYMBOLS) | {BENCH}
    df = df[df.symbol.isin(need)]
    miss = need - set(df.symbol)
    if miss:
        raise SystemExit(f"数据缺失: {miss}")
    # 防幸存者/前视: 任何标的数据起点晚于评估窗口起点即拒绝
    late = {s: d for s, d in df.groupby("symbol")["date"].min().items()
            if d > START}
    if late:
        raise SystemExit(f"标的数据起点晚于窗口起点 {START}: {late}")
    return df.sort_values("date")


def run(mode: str, prefix: str) -> dict:
    """mode: 'strategy' = 定期+偏离+回撤减仓; 'sched' = 仅定期+偏离(不做回撤减仓);
    'buyhold' = 建仓后一直持有。"""
    use_dd = mode == "strategy"
    df = load_data()
    px = df.pivot(index="date", columns="symbol", values="close").sort_index()
    op = df.pivot(index="date", columns="symbol", values="open").sort_index()
    px_mark = px.ffill()          # 停牌估值: 只回溯, 无前视

    def mark(sym: str, d: str) -> float:
        v = px_mark.loc[d, sym]
        return 0.0 if pd.isna(v) else float(v)

    dates = [d for d in px.index if d >= START]
    if len(dates) < 60:
        raise SystemExit("评估窗口不足 60 个交易日")
    bar = {d: i for i, d in enumerate(dates)}

    # 定期再平衡信号日: 3/9/12 月最后一个交易日
    sched_months = {"03", "09", "12"}
    month_of = [d[:7] for d in dates]
    sched_idx = {i for i in range(len(dates) - 1)
                 if month_of[i][5:] in sched_months
                 and month_of[i] != month_of[i + 1]}

    cash = CAPITAL
    shares = {s: 0 for s in SYMBOLS}
    avg_cost = {s: 0.0 for s in SYMBOLS}
    open_date: dict[str, str | None] = {s: None for s in SYMBOLS}
    open_bar = {s: 0 for s in SYMBOLS}
    equity_curve: list[dict] = []
    trade_history: list[dict] = []
    rebal_log: list[dict] = []
    state = "NORMAL"
    pending = False
    pending_reason = ""
    blocked_buy = blocked_sell = 0
    n_by_reason = {"initial": 0, "scheduled": 0, "deviation": 0, "drawdown": 0}
    state_days = {"NORMAL": 0, "WARN": 0, "DANGER": 0, "BREACH": 0}

    def buy(sym: str, d: str, size: int, price: float) -> None:
        nonlocal cash
        if size <= 0 or pd.isna(price) or price <= 0:
            return
        # 现金含费校验: 超额按整手递减, 保证 cash >= 0, 杜绝透支
        while size >= LOT and size * price + commission(size * price) > cash + 1e-9:
            size -= LOT
        if size < LOT:
            return
        amount = size * price
        fee = commission(amount)
        cash -= amount + fee
        old_v = shares[sym] * avg_cost[sym]
        shares[sym] += size
        avg_cost[sym] = (old_v + amount + fee) / shares[sym]
        if shares[sym] == size:
            open_date[sym], open_bar[sym] = d, bar[d]

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
            "entry_date": open_date[sym], "exit_date": d, "side": "long",
            "size": size, "entry_price": round(avg_cost[sym], 4),
            "exit_price": round(price, 4),
            "pnl": round(amount - fee - cost, 2),
            "pnl_pct": round((amount - fee) / cost - 1, 6) * 100 if cost > 0 else 0.0,
            "holding_bars": bar[d] - open_bar[sym],
            "symbol": sym, "symbol_name": NAMES[sym],
        })
        shares[sym] -= size
        if shares[sym] == 0:
            avg_cost[sym] = 0.0
            open_date[sym] = None

    def do_rebalance(d: str, w: dict[str, float], reason: str) -> None:
        nonlocal cash, blocked_buy, blocked_sell
        prev_d = dates[bar[d] - 1] if bar[d] > 0 else None
        prev_c = px.loc[prev_d] if prev_d else None
        tradeable = [s for s in SYMBOLS if not pd.isna(op.loc[d, s])]

        def blocked(s: str, o: float, is_buy: bool) -> bool:
            p = None if prev_c is None or pd.isna(prev_c[s]) else float(prev_c[s])
            if p is None or p <= 0:
                return False
            lim = limit_pct(s)
            return o >= p * (1 + lim) - 1e-9 if is_buy else o <= p * (1 - lim) + 1e-9

        sellable = [s for s in tradeable if not blocked(s, float(op.loc[d, s]), False)]
        buyable = [s for s in tradeable if not blocked(s, float(op.loc[d, s]), True)]
        blocked_sell += len(tradeable) - len(sellable)
        blocked_buy += len(tradeable) - len(buyable)

        total_v = cash + sum(shares[s] * mark(s, d) for s in SYMBOLS)
        o = {s: float(op.loc[d, s]) for s in tradeable}
        traded = 0.0
        for s in sellable:                        # 先卖超配, 腾出资金
            delta = total_v * w[s] - shares[s] * o[s]
            if delta < 0:
                size = int((-delta) / o[s] // LOT) * LOT
                if size >= LOT:
                    sell(s, d, size, fill_sell_px(o[s]))
                    traded += size * o[s]
        for s in buyable:                         # 再买低配
            delta = total_v * w[s] - shares[s] * o[s]
            if delta > 0:
                size = int(delta / o[s] // LOT) * LOT
                if size >= LOT:
                    buy(s, d, size, fill_buy_px(o[s]))
                    traded += size * o[s]
        if traded > 0:
            n_by_reason[reason] += 1
            rebal_log.append({"date": d, "reason": reason, "state": state,
                              "turnover_cny": round(traded, 0)})

    peak = CAPITAL
    for i, d in enumerate(dates):
        # 1) 执行上一交易日收盘产生的信号 (次日开盘成交)
        if i == 0:
            do_rebalance(d, target_weights("NORMAL"), "initial")
        elif pending and mode in ("strategy", "sched"):
            pending = False
            do_rebalance(d, target_weights(state), pending_reason)

        # 2) 收盘估值
        c = {s: mark(s, d) for s in SYMBOLS}
        eq = cash + sum(shares[s] * c[s] for s in SYMBOLS)
        equity_curve.append({"date": d, "value": round(eq, 2)})
        state_days[state] += 1

        # 3) 收盘产生次日信号 (只用当日及之前的信息)
        if mode == "buyhold" or i == len(dates) - 1:
            continue
        reason = ""
        if i in sched_idx:
            reason = "scheduled"
        peak = max(peak, eq)
        dd = 1.0 - eq / peak if peak > 0 else 0.0
        if use_dd:
            new_state = state
            if dd >= DD_BREACH:
                new_state = "BREACH"
            elif dd >= DD_DANGER:
                new_state = "DANGER"
            elif dd >= DD_WARN:
                new_state = "WARN"
            elif dd < DD_RECOVER:
                new_state = "NORMAL"
            if new_state != state:
                state = new_state
                reason = "drawdown"
        if not reason:                            # 类别偏离 >= 5pp
            w = target_weights(state)
            cur = {s: (shares[s] * c[s] / eq if eq > 0 else 0.0) for s in SYMBOLS}
            for grp in (CORE, SAT, CASH):
                if abs(sum(cur[s] for s in grp) - sum(w[s] for s in grp)) >= 0.05:
                    reason = "deviation"
                    break
        if reason:
            pending, pending_reason = True, reason

    # 4) 期末强平 (最后交易日收盘价)
    d_last = dates[-1]
    for s in SYMBOLS:
        v = px_mark.loc[d_last, s]
        if not pd.isna(v):
            sell(s, d_last, shares[s], fill_sell_px(float(v)))
    end_value = cash + sum(shares[s] * mark(s, d_last) for s in SYMBOLS)
    equity_curve[-1]["value"] = round(end_value, 2)

    paths = export_results(
        equity_curve=equity_curve, trade_history=trade_history, prefix=prefix,
        initial_cash=CAPITAL, start=START, end=d_last, market="china_a",
        output_dir=HERE,
        strategy_name=({"strategy": "200万ETF组合-定期+回撤风控",
                        "sched": "200万ETF组合-仅定期再平衡",
                        "buyhold": "200万ETF组合-买入持有"}[mode]),
        symbol=",".join(SYMBOLS), is_flat_at_end=True,
    )
    if mode == "strategy":
        with open(HERE / f"{prefix}_rebalance_log.csv", "w", newline="",
                  encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["date", "reason", "state", "turnover_cny"])
            for e in rebal_log:
                wr.writerow([e["date"], e["reason"], e["state"], e["turnover_cny"]])

    total_fee = sum(commission(t["size"] * t["entry_price"])
                    + commission(t["size"] * t["exit_price"])
                    for t in trade_history)
    return {
        "prefix": prefix, "equity_curve": equity_curve,
        "trade_history": trade_history, "n_rebalance": len(rebal_log),
        "by_reason": n_by_reason, "state_days": state_days,
        "total_fee": round(total_fee, 2),
        "blocked_buy": blocked_buy, "blocked_sell": blocked_sell,
        "start": dates[0], "end": d_last, "n_days": len(dates),
        "paths": {k: str(v) for k, v in paths.items()},
    }


def benchmark() -> list[dict]:
    """沪深300 价格指数基准: 窗口首日开盘买入, 每日按收盘估值 (无费用、不可投资)。"""
    df = load_data()
    b = df[df.symbol == BENCH].set_index("date").sort_index()
    b = b[b.index >= START]
    base = float(b["open"].iloc[0])
    return [{"date": d, "value": round(CAPITAL * float(r["close"]) / base, 2)}
            for d, r in b.iterrows()]


def max_dd(cur: list[dict]) -> tuple[float, str]:
    peak, mdd, at = -1e18, 0.0, ""
    for p in cur:
        peak = max(peak, p["value"])
        dd = 1 - p["value"] / peak
        if dd > mdd:
            mdd, at = dd, p["date"]
    return mdd, at


def yearly(cur: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    first = {}
    for p in cur:
        y = p["date"][:4]
        first.setdefault(y, p["value"])
    last = {}
    for p in cur:
        last[p["date"][:4]] = p["value"]
    prev_end = None
    for y in sorted(first):
        base = prev_end if prev_end else first[y]
        out[y] = (last[y] / base - 1) * 100
        prev_end = last[y]
    return out


def main() -> None:
    r1 = run("strategy", "opt200w")
    r2 = run("sched", "opt200ws")
    r0 = run("buyhold", "opt200wbh")
    bench = benchmark()
    with open(HERE / "opt200w_bench_equity.csv", "w", newline="",
              encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["date", "value"])
        for p in bench:
            wr.writerow([p["date"], p["value"]])

    for r in (r1, r2, r0):
        print(f"\n=== {r['prefix']} ===")
        print(f"窗口: {r['start']} ~ {r['end']} ({r['n_days']} 交易日)")
        print(f"再平衡次数: {r['n_rebalance']}  触发来源: {r['by_reason']}")
        print(f"平仓笔数: {len(r['trade_history'])}  双边费用: {r['total_fee']:,.0f}")
        print(f"涨跌停拦截: 买 {r['blocked_buy']} / 卖 {r['blocked_sell']}")
        print(f"期末净值: {r['equity_curve'][-1]['value']:,.2f}")
    print("档位停留天数:", r1["state_days"])

    yrs = r1["n_days"] / 252
    print(f"\n=== 四口径对比 ({r1['start']} ~ {r1['end']}, {yrs:.2f} 年) ===")
    print(f"{'口径':<26}{'期末净值':>16}{'总收益':>10}{'年化':>9}{'最大回撤':>10}{'回撤日':>13}")
    rows = []
    for name, cur in (("定期再平衡+回撤风控", r1["equity_curve"]),
                      ("仅定期再平衡", r2["equity_curve"]),
                      ("组合买入持有", r0["equity_curve"]),
                      ("沪深300价格指数", bench)):
        tot = cur[-1]["value"] / CAPITAL - 1
        ann = (cur[-1]["value"] / CAPITAL) ** (1 / yrs) - 1
        mdd, mdd_at = max_dd(cur)
        rows.append((name, cur))
        print(f"{name:<26}{cur[-1]['value']:>16,.0f}{tot * 100:>9.2f}%"
              f"{ann * 100:>8.2f}%{mdd * 100:>9.2f}%{mdd_at:>13}")

    print("\n=== 分年度收益 (%) ===")
    ys = [yearly(c) for _, c in rows]
    years = sorted({y for d in ys for y in d})
    print(f"{'口径':<26}" + "".join(f"{y:>10}" for y in years))
    for (name, _), d in zip(rows, ys):
        print(f"{name:<26}" + "".join(f"{d.get(y, float('nan')):>9.2f}%" for y in years))

    print("\n=== 期权成本敏感性 (年化拖拽, 叠加于策略净值) ===")
    base = r1["equity_curve"][-1]["value"] / CAPITAL
    for rate in (0.0, 0.005, 0.010, 0.015, 0.020):
        mult = (1 - rate / 252) ** (r1["n_days"] - 1)
        v = base * mult
        print(f"  {rate * 100:>4.1f}%/年 -> 期末 ¥{CAPITAL * v:,.0f}  "
              f"年化 {(v ** (1 / yrs) - 1) * 100:>6.2f}%")


if __name__ == "__main__":
    main()
