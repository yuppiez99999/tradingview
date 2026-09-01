"""
ETF 动量轮动策略回测 — 复现 28 系统 (v8.6.14) 生产链路策略内核
================================================================
复现目标: utils/strategy/etf_rotation/engine.py 的动量轮动内核
  - momentum = closes.pct_change(lookback)   (lookback = 20)
  - 每日从"可用池"取 top-holdings(3) 等权 (1/3)
  - 可用池 = 11 只社保风格 ETF 白名单中当日已有行情的标的 (动态扩展)
  - 信号 T 生成 -> T+1 执行 (signals.shift(1))
  - 换手率 turnover = |Δw|.sum / 2, 成本 = turnover * commission_rate (3bp/边)
  - 权益曲线: 收盘-收盘向量化 (1+net_ret).cumprod() * initial_capital

数据: 通达信后复权日线 (2012-04-09 .. 2026-09-01)
评估窗口: 2014-01-01 .. 2026-09-01  (export_results 按 start/end 切片重算指标)
输出: etf_rotation_2014_{equity.csv, trades.csv, summary.json}

合约要点:
  - 防 look-ahead: 信号只用 <=T 的数据, 执行用 T+1 开盘价
  - warmup: 数据起点 2012 年远早于评估起点 2014; 交易/权益记录由
    export_results(start, end) 切片, 指标只在评估窗口重算
  - 期末强制平仓: 最后一个 bar 收盘价清算所有持仓, is_flat_at_end=True
  - A股多头: market="china_a", 无 side="short"
  - 等权恒为 1/3 -> 权重非 0 即 1/3 -> 一次轮动 = 整仓进出,
    trades.csv 一行 = 一只 ETF 的一个持仓周期 (闭环交易)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
sys.path.insert(0, str(BASE))
from export_results import export_results  # noqa: E402

# ---------------------------------------------------------------------------
# 参数 (与 engine.py 默认一致)
# ---------------------------------------------------------------------------
LOOKBACK = 20            # 动量回看天数
HOLDINGS = 3             # 持仓 ETF 数量
COMMISSION_RATE = 0.0003  # 佣金 3bp / 边
INITIAL_CAPITAL = 1_000_000.0
EVAL_START = "2014-01-01"
EVAL_END = "2026-09-01"

ETF_NAMES = {
    "159915": "创业板ETF",
    "510300": "沪深300ETF",
    "512010": "医药ETF",
    "518880": "黄金ETF",
    "512880": "证券ETF",
    "512100": "中证1000ETF",
    "512800": "银行ETF",
    "512760": "半导体ETF",
    "512170": "医疗ETF",
    "515030": "新能源车ETF",
    "588000": "科创50ETF",
}


# ---------------------------------------------------------------------------
# 信号生成 — 与 engine.generate_rotation_signals 逐行一致
# ---------------------------------------------------------------------------
def generate_rotation_signals(
    closes: pd.DataFrame,
    lookback: int,
    holdings: int,
) -> pd.DataFrame:
    """N 日动量 -> 每日 top-K 等权信号矩阵 (动态池自动处理)。"""
    momentum = closes.pct_change(lookback)
    signals = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    for i in range(len(closes)):
        if i < lookback:
            continue
        row = momentum.iloc[i].dropna()
        if len(row) < holdings:
            continue
        top_k = row.nlargest(holdings).index
        signals.loc[closes.index[i], top_k] = 1.0 / holdings
    return signals


# ---------------------------------------------------------------------------
# 向量化回测 — 与 engine._run_backtest 逐行一致 (收盘-收盘)
# ---------------------------------------------------------------------------
def run_vectorized_backtest(
    closes: pd.DataFrame,
    signals: pd.DataFrame,
    initial_capital: float,
    commission_rate: float,
) -> tuple[pd.Series, pd.Series]:
    """返回 (权益序列, 净日收益序列)。"""
    daily_returns = closes.pct_change().fillna(0.0)
    shifted_signals = signals.shift(1).fillna(0.0)
    portfolio_returns = (shifted_signals * daily_returns).sum(axis=1)
    turnover = shifted_signals.diff().abs().sum(axis=1) / 2.0
    cost = turnover * commission_rate
    net_returns = portfolio_returns - cost
    equity = (1.0 + net_returns).cumprod() * initial_capital
    return equity, net_returns


# ---------------------------------------------------------------------------
# 交易台账 — 权重转移 -> 闭环持仓记录 (T 信号 -> T+1 开盘执行)
# ---------------------------------------------------------------------------
def build_trade_ledger(
    closes: pd.DataFrame,
    opens: pd.DataFrame,
    shifted_signals: pd.DataFrame,
    equity_full: pd.Series,
    commission_rate: float,
) -> list[dict]:
    """把权重转移转成逐 ETF 持仓周期交易记录。等权恒为 1/3 -> 整仓进出。"""
    open_episodes: dict[str, dict] = {}
    trades: list[dict] = []

    def close_episode(code, ep, exit_idx, exit_px, exit_date):
        size = ep["size"]
        entry_px = ep["entry_price"]
        pnl = size * exit_px * (1.0 - commission_rate) - size * entry_px * (1.0 + commission_rate)
        pnl_pct = (exit_px * (1.0 - commission_rate) / (entry_px * (1.0 + commission_rate)) - 1.0) * 100.0
        return {
            "entry_date": ep["entry_date"],
            "exit_date": exit_date,
            "side": "long",
            "size": round(size, 2),
            "entry_price": round(entry_px, 4),
            "exit_price": round(exit_px, 4),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "holding_bars": int(exit_idx - ep["entry_idx"]),
            "symbol": ep["symbol"],
            "symbol_name": ep["symbol_name"],
        }

    for t in range(1, len(shifted_signals)):
        prev_w = shifted_signals.iloc[t - 1]
        cur_w = shifted_signals.iloc[t]
        delta = cur_w - prev_w
        date_str = shifted_signals.index[t].strftime("%Y-%m-%d")
        eq_before = float(equity_full.iloc[t - 1])
        o = opens.iloc[t]

        # 1) 卖出: 1/3 -> 0 (先处理卖出, 资金回笼口径与引擎一致)
        for code in delta.index[delta < -0.01]:
            if code not in open_episodes:
                continue
            px = float(o[code])
            if not np.isfinite(px) or px <= 0:
                continue
            ep = open_episodes.pop(code)
            trades.append(close_episode(code, ep, t, px, date_str))

        # 2) 买入: 0 -> 1/3
        for code in delta.index[delta > 0.01]:
            if code in open_episodes:
                continue
            px = float(o[code])
            if not np.isfinite(px) or px <= 0:
                continue
            w = float(cur_w[code])
            notional = w * eq_before
            size = notional / px
            open_episodes[code] = {
                "entry_idx": t,
                "entry_date": date_str,
                "entry_price": px,
                "size": size,
                "weight": w,
                "symbol": code,
                "symbol_name": ETF_NAMES.get(code, code),
            }

    # 3) 期末强制平仓: 最后 bar 收盘价
    last_idx = len(shifted_signals) - 1
    last_date = shifted_signals.index[last_idx].strftime("%Y-%m-%d")
    last_close = closes.iloc[last_idx]
    for code, ep in list(open_episodes.items()):
        px = float(last_close[code])
        if not np.isfinite(px) or px <= 0:
            continue
        trades.append(close_episode(code, ep, last_idx, px, last_date))
    open_episodes.clear()

    return trades


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    # 1. 加载数据
    closes = pd.read_csv(DATA / "etf_close_panel.csv", index_col="date", parse_dates=True)
    raw = pd.read_csv(DATA / "etf_daily_raw.csv", dtype={"code": str})
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last")
    opens = (
        raw.pivot_table(index="date", columns="code", values="open")
        .reindex(index=closes.index)
        .reindex(columns=closes.columns)
    )
    print(f"[数据] {len(closes)} 根日线, {closes.shape[1]} 只 ETF, "
          f"{closes.index[0].date()} .. {closes.index[-1].date()}")

    # 内部 NaN 体检 (停牌缺口)
    for c in closes.columns:
        series = closes[c].dropna()
        if len(series) == 0:
            continue
        first = series.index[0]
        internal_nan = int(closes.loc[first:, c].isna().sum())
        if internal_nan:
            print(f"[体检] {c} 上市后内部 NaN 天数 = {internal_nan} (停牌, 引擎口径沿用收盘价 NaN 即无动量)")

    # 2. 信号 + 向量化回测
    signals = generate_rotation_signals(closes, LOOKBACK, HOLDINGS)
    shifted_signals = signals.shift(1).fillna(0.0)
    equity_full, net_returns = run_vectorized_backtest(
        closes, signals, INITIAL_CAPITAL, COMMISSION_RATE
    )

    # 3. 交易台账
    trades = build_trade_ledger(
        closes, opens, shifted_signals, equity_full, COMMISSION_RATE
    )
    n_rebalances = int((shifted_signals.diff().abs().sum(axis=1) > 0.01).sum())
    total_turnover = float((shifted_signals.diff().abs().sum(axis=1) / 2.0).sum())
    first_signal_date = shifted_signals[shifted_signals.abs().sum(axis=1) > 0.01].index
    print(f"[信号] 首次有效信号 = {first_signal_date[0].date() if len(first_signal_date) else '无'}, "
          f"换仓次数 = {n_rebalances}, 累计双边换手 = {total_turnover:.2f} (倍)")

    # 4. 权益曲线记录
    equity_curve = [
        {"date": ts.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
        for ts, v in equity_full.items()
    ]

    # 5. 导出三件套 (start/end 显式传参 -> 评估窗口切片重算指标)
    paths = export_results(
        equity_curve=equity_curve,
        trade_history=trades,
        prefix="etf_rotation_2014",
        initial_cash=INITIAL_CAPITAL,
        start=EVAL_START,
        end=EVAL_END,
        market="china_a",
        output_dir=BASE,
        strategy_name="ETF动量轮动·20日动量Top3等权·社保风格11只白名单",
        symbol="510300/159915/518880/512010/512880/512100/512800/512760/512170/515030/588000",
        is_flat_at_end=True,
    )

    # 6. 打印摘要
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    s, m = summary["summary"], summary["meta"]
    print("\n================ 回测摘要 (评估窗口 2014-01-01 .. 2026-09-01) ================")
    print(f"  期末权益      : ¥{m['final_value']:,.2f}  (初始 ¥{m['window_start_value']:,.2f})")
    print(f"  累计收益      : {s['total_return_pct']:.2f}%")
    print(f"  年化收益      : {s['annual_return_pct']:.2f}%")
    print(f"  年化夏普      : {s['sharpe']:.3f}")
    print(f"  最大回撤      : {s['max_drawdown_pct']:.2f}%")
    print(f"  交易笔数      : {s['total_trades']}  (胜率 {s['win_rate_pct']:.1f}%)")
    print(f"  输出文件      : {[str(p) for p in paths.values()]}")

    # 7. 分年度收益表 (评估窗口内)
    eq = equity_full.loc[EVAL_START:EVAL_END]
    years = sorted({ts.year for ts in eq.index})
    print("\n================ 分年度收益 (策略净收益) ================")
    print(f"  {'年份':<8}{'年初净值':>12}{'年末净值':>12}{'年度收益':>10}")
    for y in years:
        sub = eq[eq.index.year == y]
        if len(sub) == 0:
            continue
        start_v = float(sub.iloc[0])
        end_v = float(sub.iloc[-1])
        ret = (end_v / start_v - 1.0) * 100.0
        print(f"  {y:<8}{start_v:>12,.0f}{end_v:>12,.0f}{ret:>9.2f}%")

    # 8. 动态池演变 (评估起点各 ETF 是否已上市)
    print("\n================ 白名单上市覆盖 (相对 2014-01-01) ================")
    for c in closes.columns:
        first = closes[c].dropna().index[0].date()
        status = "2014前已上市" if first < pd.Timestamp(EVAL_START).date() else f"上市 {first}"
        print(f"  {c:<8}{ETF_NAMES.get(c, ''):<10}{status}")


if __name__ == "__main__":
    main()
