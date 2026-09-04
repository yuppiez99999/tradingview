# -*- coding: utf-8 -*-
"""portfolio_v510_backtest.py — 28系统 v5.10 组合（23只股票/ETF sleeve）再平衡回测。

策略口径 (来自 config/portfolio.yaml + cli/modes/backtest.py 内置设置):
  - 初始资金: 4,000,000 (股票/ETF sleeve, 期货/期权/对冲部分不在本回测范围)
  - 目标权重: 23只标的合计 0.92, 其余为现金缓冲
  - 再平衡规则: 任一标的收盘权重偏离目标超过 6 个百分点,
                且距上次再平衡 >= 5 个交易日 -> 次日开盘价执行再平衡
  - 执行时点: 信号当日收盘确认, 次日开盘成交 (防 look-ahead)
  - 费用: 佣金万3 (最低5元, 双边); 印花税万5 (仅股票卖出)
  - A股规则: 100股整数手, T+1 (次日开盘执行天然满足)
  - 期末: 最后一根K线收盘价强制平仓

对比基准: 同权重买入持有 (无再平衡), 同样费用假设, 期末强平。

产出 (写入脚本所在目录):
  portfolio_v510_equity.csv / portfolio_v510_trades.csv / portfolio_v510_summary.json
  portfolio_v510bh_equity.csv / portfolio_v510bh_trades.csv / portfolio_v510bh_summary.json
  rebalance_log.csv (每次再平衡的权重偏离明细)
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
# skill 参考库缓存根 (user 级缓存); 缺失时保持原 ImportError 语义
EXPERT_CACHE = Path.home() / ".workbuddy" / "plugins" / "cache" / "experts"
REF_DIR = EXPERT_CACHE / "strategy-backtest-expert/1.0.0/skills/quant-backtest-lab/reference"
sys.path.insert(0, str(REF_DIR))
from export_results import export_results  # noqa: E402

# ---------------------------------------------------------------- 配置 ----
INITIAL_CASH = 4_000_000.0
EVAL_START = "2023-09-01"          # 评估窗口起点 (含), 数据本身从更早开始
REBALANCE_THRESHOLD = 0.06         # 权重偏离阈值 (绝对百分点)
MIN_INTERVAL_DAYS = 5              # 两次再平衡最小间隔 (交易日)
BUY_COMM = 0.0003                  # 佣金 万3
SELL_COMM = 0.0003
MIN_COMMISSION = 5.0               # 最低佣金 5 元
STAMP_TAX = 0.0005                 # 印花税 万5, 仅股票卖出
LOT = 100

# symbol -> (标准代码, 名称, 目标权重, 类型)
PORTFOLIO = {
    "sh510300": ("510300.SH", "沪深300ETF", 0.08, "etf"),
    "sh510500": ("510500.SH", "中证500ETF", 0.06, "etf"),
    "sh512100": ("512100.SH", "中证1000ETF", 0.04, "etf"),
    "sh588000": ("588000.SH", "科创50ETF", 0.06, "etf"),
    "sz159915": ("159915.SZ", "创业板ETF", 0.06, "etf"),
    "sh688041": ("688041.SH", "海光信息", 0.04, "stock"),
    "sz300308": ("300308.SZ", "中际旭创", 0.05, "stock"),
    "sz300274": ("300274.SZ", "阳光电源", 0.04, "stock"),
    "sz002371": ("002371.SZ", "北方华创", 0.04, "stock"),
    "sh688981": ("688981.SH", "中芯国际", 0.03, "stock"),
    "sh600276": ("600276.SH", "恒瑞医药", 0.03, "stock"),
    "sh603019": ("603019.SH", "中科曙光", 0.02, "stock"),
    "sh600089": ("600089.SH", "特变电工", 0.04, "stock"),
    "sh600875": ("600875.SH", "东方电气", 0.03, "stock"),
    "sh601088": ("601088.SH", "中国神华", 0.04, "stock"),
    "sh600219": ("600219.SH", "南山铝业", 0.03, "stock"),
    "sh600019": ("600019.SH", "宝钢股份", 0.03, "stock"),
    "sh518880": ("518880.SH", "华安黄金ETF", 0.05, "etf"),
    "sz000792": ("000792.SZ", "盐湖股份", 0.03, "stock"),
    "sh600900": ("600900.SH", "长江电力", 0.04, "stock"),
    "sz000858": ("000858.SZ", "五粮液", 0.03, "stock"),
    "sh601318": ("601318.SH", "中国平安", 0.02, "stock"),
    "sh600036": ("600036.SH", "招商银行", 0.03, "stock"),
}
WEIGHT_SUM = sum(v[2] for v in PORTFOLIO.values())

# ---------------------------------------------------------------- 数据 ----


def load_data() -> tuple[pd.DataFrame, dict[str, dict[str, pd.Series]]]:
    df = pd.read_csv(HERE / "klines_qfq.csv", dtype={"symbol": str})
    data: dict[str, dict[str, pd.Series]] = {}
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("date").drop_duplicates("date").set_index("date")
        data[sym] = {
            "open": g["open"], "high": g["high"],
            "low": g["low"], "close": g["close"],
        }
    return df, data


# ------------------------------------------------------------ 费用工具 ----


def buy_fee(amount: float) -> float:
    return max(amount * BUY_COMM, MIN_COMMISSION)


def sell_fee(amount: float, is_etf: bool) -> float:
    tax = 0.0 if is_etf else amount * STAMP_TAX
    return max(amount * SELL_COMM, MIN_COMMISSION) + tax


# ------------------------------------------------------------- 回测核心 ----


class Portfolio:
    """多标的组合账本: cash + 持仓 + 每标的成本池"""

    def __init__(self, calendar: list[str]):
        self.cash = INITIAL_CASH
        self.pos: dict[str, dict] = {}   # sym -> {shares, cost_basis, avg_px, open_date}
        self.calendar = calendar
        self.trade_history: list[dict] = []

    # ---- 估值 ----
    def equity(self, date: str, prices: dict[str, float]) -> float:
        total = self.cash
        for sym, p in self.pos.items():
            px = prices.get(sym)
            if px is not None:
                total += p["shares"] * px
        return total

    # ---- 交易 ----
    def _buy(self, sym: str, date: str, price: float, shares: int, label: str) -> None:
        if shares <= 0:
            return
        amount = shares * price
        fee = buy_fee(amount)
        cost = amount + fee
        if cost > self.cash:  # 现金不足, 按手递减
            while shares > 0 and cost > self.cash:
                shares -= LOT
                amount = shares * price
                fee = buy_fee(amount) if shares > 0 else 0.0
                cost = amount + fee
            if shares <= 0:
                return
        self.cash -= cost
        p = self.pos.get(sym)
        if p and p["shares"] > 0:
            p["cost_basis"] += cost
            p["shares"] += shares
            p["avg_px"] = (p["avg_px"] * (p["shares"] - shares) + price * shares) / p["shares"]
        else:
            self.pos[sym] = {
                "shares": shares, "cost_basis": cost,
                "avg_px": price, "open_date": date,
            }
        _ = label  # 仅注释用途

    def _sell(self, sym: str, date: str, price: float, shares: int, label: str) -> None:
        p = self.pos.get(sym)
        if not p or shares <= 0:
            return
        shares = min(shares, p["shares"])
        if shares <= 0:
            return
        is_etf = PORTFOLIO[sym][3] == "etf"
        amount = shares * price
        fee = sell_fee(amount, is_etf)
        self.cash += amount - fee
        unit_cost = p["cost_basis"] / p["shares"]
        pnl = (amount - fee) - unit_cost * shares
        std, name = PORTFOLIO[sym][0], PORTFOLIO[sym][1]
        self.trade_history.append({
            "entry_date": p["open_date"],
            "exit_date": date,
            "side": "long",
            "size": shares,
            "entry_price": round(p["avg_px"], 4),
            "exit_price": round(price, 4),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / (unit_cost * shares) * 100.0, 4),
            "holding_bars": self._bars_between(p["open_date"], date),
            "symbol": std,
            "symbol_name": name,
            "label": label,
        })
        p["cost_basis"] -= unit_cost * shares
        p["shares"] -= shares
        if p["shares"] <= 0:
            self.pos[sym] = {"shares": 0, "cost_basis": 0.0, "avg_px": 0.0, "open_date": None}

    def _bars_between(self, d1: str, d2: str) -> int:
        try:
            return self.calendar.index(d2) - self.calendar.index(d1)
        except ValueError:
            return 0

    # ---- 再平衡 (次日开盘执行) ----
    def rebalance_to_target(self, date: str, opens: dict[str, float]) -> None:
        eq = self.equity(date, opens)
        # 先卖后买
        for sym in PORTFOLIO:
            px = opens.get(sym)
            if px is None:
                continue
            w = PORTFOLIO[sym][2]
            desired = int((eq * w) / (px * LOT)) * LOT
            held = self.pos.get(sym, {}).get("shares", 0)
            if desired < held:
                self._sell(sym, date, px, held - desired, "再平衡减仓")
        for sym in PORTFOLIO:
            px = opens.get(sym)
            if px is None:
                continue
            w = PORTFOLIO[sym][2]
            desired = int((eq * w) / (px * LOT)) * LOT
            held = self.pos.get(sym, {}).get("shares", 0)
            if desired > held:
                self._buy(sym, date, px, desired - held, "再平衡加仓")

    def initial_buy(self, date: str, opens: dict[str, float]) -> None:
        for sym in PORTFOLIO:
            px = opens.get(sym)
            if px is None:
                continue
            w = PORTFOLIO[sym][2]
            desired = int((INITIAL_CASH * w) / (px * LOT)) * LOT
            self._buy(sym, date, px, desired, "建仓")

    def force_close(self, date: str, closes: dict[str, float]) -> None:
        for sym in list(self.pos):
            p = self.pos[sym]
            if p["shares"] > 0:
                px = closes.get(sym)
                if px is not None:
                    self._sell(sym, date, px, p["shares"], "期末强平")


def run(rebalance_enabled: bool = True):
    _, data = load_data()
    all_dates = sorted({d for sym in data.values() for d in sym["close"].index})
    cal = [d for d in all_dates if d >= EVAL_START]
    # 数据覆盖检查
    for sym in PORTFOLIO:
        first = data[sym]["close"].index.min()
        assert first <= cal[0], f"{sym} 数据起点 {first} 晚于评估窗口 {cal[0]}"

    pf = Portfolio(cal)
    equity_curve: list[dict] = []
    rebalance_log: list[dict] = []
    last_reb_idx = None
    pending_rebalance = False
    n_rebalances = 0
    last_close: dict[str, float] = {}

    for i, date in enumerate(cal):
        opens = {s: float(data[s]["open"].get(date, float("nan"))) for s in PORTFOLIO}
        opens = {s: v for s, v in opens.items() if v == v}
        closes = {s: float(data[s]["close"].get(date, float("nan"))) for s in PORTFOLIO}
        closes = {s: v for s, v in closes.items() if v == v}
        mark = {**last_close, **closes}  # 停牌日沿用最后有效收盘价

        # 1) 次日开盘执行昨日信号
        if i == 0:
            pf.initial_buy(date, opens)
            last_reb_idx = 0
        elif pending_rebalance and rebalance_enabled:
            pf.rebalance_to_target(date, opens)
            n_rebalances += 1
            last_reb_idx = i
            pending_rebalance = False
            rebalance_log.append({
                "date": date,
                **{PORTFOLIO[s][0]: round(
                    (pf.pos.get(s, {}).get("shares", 0) * opens.get(s, 0)) /
                    max(pf.equity(date, opens), 1e-9), 4)
                   for s in PORTFOLIO},
            })

        # 2) 收盘检查再平衡触发 (信号 -> 次日执行)
        eq = pf.equity(date, mark)
        if eq > 0:
            devs = []
            for s in PORTFOLIO:
                cur_w = (pf.pos.get(s, {}).get("shares", 0) * mark.get(s, 0.0)) / eq
                devs.append((abs(cur_w - PORTFOLIO[s][2]), PORTFOLIO[s][0], cur_w))
            max_dev = max(devs)[0]
            interval_ok = last_reb_idx is None or (i - last_reb_idx) >= MIN_INTERVAL_DAYS
            if rebalance_enabled and max_dev > REBALANCE_THRESHOLD and interval_ok:
                pending_rebalance = True

        # 3) 记录净值
        equity_curve.append({"date": date, "value": round(eq, 2)})
        last_close = mark

    # 4) 期末强平
    last_date = cal[-1]
    final_mark = {s: float(data[s]["close"].get(last_date, float("nan"))) for s in PORTFOLIO}
    final_mark = {s: v for s, v in final_mark.items() if v == v}
    pf.force_close(last_date, final_mark)
    equity_curve[-1] = {"date": last_date, "value": round(pf.cash, 2)}

    return pf, equity_curve, rebalance_log, n_rebalances, cal


def main() -> None:
    results = {}
    for tag, enabled, prefix in [
        ("rebalance", True, "portfolio_v510"),
        ("buyhold", False, "portfolio_v510bh"),
    ]:
        pf, eq, reb_log, n_reb, cal = run(rebalance_enabled=enabled)
        export_results(
            equity_curve=eq,
            trade_history=pf.trade_history,
            prefix=prefix,
            initial_cash=INITIAL_CASH,
            start=cal[0],
            end=cal[-1],
            market="china_a",
            is_flat_at_end=True,
            strategy_name="v5.10组合再平衡" if enabled else "v5.10组合买入持有基准",
            symbol="23只标的组合",
        )
        if reb_log:
            with (HERE / "rebalance_log.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["date"] + [v[0] for v in PORTFOLIO.values()])
                w.writeheader()
                w.writerows(reb_log)
        results[tag] = (pf, eq, n_reb, cal)
        print(f"[{tag}] 再平衡次数={n_reb} 平仓笔数={len(pf.trade_history)} "
              f"期末现金={pf.cash:,.0f} 区间={cal[0]}~{cal[-1]}")

    # sanity 输出
    for tag in ("rebalance", "buyhold"):
        pf, eq, n_reb, cal = results[tag]
        vals = [p["value"] for p in eq]
        total_ret = vals[-1] / vals[0] - 1
        years = len(vals) / 252
        ann = (1 + total_ret) ** (1 / years) - 1
        peak, mdd = vals[0], 0.0
        for v in vals:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        print(f"[sanity {tag}] 总收益={total_ret*100:.2f}% 年化={ann*100:.2f}% "
              f"最大回撤={mdd*100:.2f}% 交易日数={len(vals)}")
    pf_rb = results["rebalance"][0]
    print(f"[trades head]\n{pd.DataFrame(pf_rb.trade_history).head(5).to_string()}")
    print(f"[trades tail]\n{pd.DataFrame(pf_rb.trade_history).tail(5).to_string()}")


if __name__ == "__main__":
    main()
