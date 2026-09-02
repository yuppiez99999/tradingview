"""S13 路径 A: 重建 2021-2026 月频 ETF 聚合信号 (selection alpha 注入).

管线 (三阶段, 均可断点续跑):
  1. constituents: akshare 拉卫星候选 ETF 的跟踪指数成分 (当前快照, csindex/SZSE)
  2. klines:       baostock 拉成分股并集 2020-2026 前复权日线 (缓存 parquet)
  3. build:        月末截面 15 因子 (纯 pandas, GTJA191 风格, 先验固定) →
                   截面 z-score + 主题加权合成 → 成分等权聚合为 ETF 信号

输出: data/etf_option_backtest/s13_signals.parquet
      index=月末交易日, columns=[etf_code...], 值=该 ETF 成分综合分均值

诚实声明 (已知局限, 详见 docs/S13_selection_alpha_注入设计_20260902.md):
  - 成分为当前快照 (point-in-time 历史成分不可得) → 幸存者偏差, 已知折衷
  - K线严格 point-in-time (月末截面只用 ≤当日数据, 滚动窗口无前视)
  - 因子公式与主题权重全部先验固定, 不进调优循环

用法:
  python scripts/build_s13_signals.py --stage constituents
  python scripts/build_s13_signals.py --stage klines
  python scripts/build_s13_signals.py --stage build
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger("s13_signals")

DATA_DIR = _PROJECT_ROOT / "data" / "etf_option_backtest"
CONS_DIR = DATA_DIR / "s13_constituents"
KLINE_DIR = DATA_DIR / "s13_klines"
OUTPUT_PATH = DATA_DIR / "s13_signals.parquet"

# 卫星候选 = 14 ETF 主池排除防御三资产 (518880/511260/510310)
# (fetch_etf_phase2_data.py ETF_LIST); 底仓 512890 为 P2 池扩展不在主池
ETF_SOURCES: dict[str, tuple[str, str]] = {
    "510050": ("000016", "csindex"),   # 上证50
    "510300": ("000300", "csindex"),   # 沪深300
    "510500": ("000905", "csindex"),   # 中证500
    "512100": ("000852", "csindex"),   # 中证1000
    "588000": ("000688", "csindex"),   # 科创50
    "512480": ("H30184", "csindex"),   # 中证全指半导体
    "512010": ("000933", "csindex"),   # 中证医药
    "515170": ("930997", "csindex"),   # CS新能车
    "159939": ("000993", "csindex"),   # 中证全指信息技术
    "512660": ("399967", "szse"),      # 中证军工 (SZSE)
    "159915": ("399006", "szse"),      # 创业板指 (SZSE)
}

KLINE_START = "2020-01-01"   # 月频因子需 ~1 年预热窗, 回测起点 2021-01
KLINE_END = "2026-09-01"
BACKTEST_START = "2021-01-01"

# ============================================================
# 主题权重 (先验固定, 与 utils/universe/factor_scorer.DEFAULT_THEME_WEIGHTS 一致)
# ============================================================
THEME_WEIGHTS = {
    "momentum": 0.30,
    "reversal": 0.20,
    "volume": 0.20,
    "volatility": 0.15,
    "liquidity": 0.15,
}

# 15 因子: {factor_id: (theme, 方向)} 方向 +1 越大越看好
FACTOR_DEFS: dict[str, tuple[str, int]] = {
    # momentum: 经典 12-1 动量 / 中期动量
    "mom_252_21": ("momentum", +1),
    "mom_120": ("momentum", +1),
    "mom_60": ("momentum", +1),
    # reversal: 短期反转 (取负)
    "rev_5": ("reversal", -1),
    "rev_10": ("reversal", -1),
    "rev_ma5": ("reversal", -1),
    # volume: 量能趋势 / 换手加速
    "vol_z_20_60": ("volume", +1),
    "amount_trend": ("volume", +1),
    "turn_z_20_60": ("volume", +1),
    # volatility: 低波动偏好 (取负)
    "vol_20": ("volatility", -1),
    "vol_60": ("volatility", -1),
    "vol_ratio": ("volatility", -1),
    # liquidity: Amihud 非流动性 (取负) / 换手 / 零收益日 (取负)
    "amihud_20": ("liquidity", -1),
    "turn_20": ("liquidity", +1),
    "zero_ret_60": ("liquidity", -1),
}


# ============================================================
# Stage 1: 成分股
# ============================================================
def fetch_constituents() -> None:
    import akshare as ak

    CONS_DIR.mkdir(parents=True, exist_ok=True)
    for etf, (idx, src) in ETF_SOURCES.items():
        fp = CONS_DIR / f"{etf}.json"
        if fp.exists():
            logger.info("[constituents] %s 已缓存, 跳过", etf)
            continue
        try:
            if src == "csindex":
                df = ak.index_stock_cons_csindex(symbol=idx)
                codes = sorted(df["成分券代码"].astype(str).str.zfill(6))
            else:
                df = ak.index_stock_cons(symbol=idx)
                col = "品种代码" if "品种代码" in df.columns else df.columns[1]
                codes = sorted(df[col].astype(str).str.zfill(6))
            payload = {"etf": etf, "index": idx, "source": src,
                       "fetched_at": pd.Timestamp.now().isoformat(), "codes": codes}
            fp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            logger.info("[constituents] %s <- %s: %d 只", etf, idx, len(codes))
        except (ConnectionError, TimeoutError, OSError, KeyError, ValueError) as e:
            logger.warning("[constituents] %s <- %s 失败: %s (该 ETF 退出卫星候选)", etf, idx, e)
    _ = ak  # noqa


def load_constituents() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for etf in ETF_SOURCES:
        fp = CONS_DIR / f"{etf}.json"
        if fp.exists():
            out[etf] = json.loads(fp.read_text(encoding="utf-8"))["codes"]
    return out


# ============================================================
# Stage 2: K线 (baostock, 前复权)
# ============================================================
def fetch_klines(shard: tuple[int, int] = (0, 1)) -> None:
    import baostock as bs

    cons = load_constituents()
    union = sorted({c for codes in cons.values() for c in codes})
    KLINE_DIR.mkdir(parents=True, exist_ok=True)
    todo = [c for c in union if not (KLINE_DIR / f"{c}.parquet").exists()]
    if shard != (0, 1):
        i, n = shard
        todo = [c for j, c in enumerate(todo) if j % n == i]
    logger.info("[klines] 并集 %d 只, 分片 %s 待拉 %d 只", len(union), shard, len(todo))

    lg = bs.login()
    if lg.error_code != "0":
        raise ConnectionError(f"baostock 登录失败: {lg.error_msg}")
    fields = "date,open,high,low,close,volume,amount,turn"
    n_ok = n_fail = 0
    t0 = time.time()
    try:
        for i, code in enumerate(todo):
            bs_code = ("sh." if code.startswith(("6", "9")) else
                       "bj." if code.startswith(("4", "8")) else "sz.") + code
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, fields, start_date=KLINE_START, end_date=KLINE_END,
                    frequency="d", adjustflag="2")  # 2 = 前复权
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    df = pd.DataFrame(rows, columns=rs.fields)
                    for c in ("open", "high", "low", "close", "volume", "amount", "turn"):
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.dropna(subset=["close"]).reset_index(drop=True)
                    df.to_parquet(KLINE_DIR / f"{code}.parquet")
                    n_ok += 1
                else:
                    n_fail += 1
            except (ConnectionError, OSError, ValueError, KeyError):
                n_fail += 1
            if (i + 1) % 100 == 0:
                el = time.time() - t0
                logger.info("[klines] %d/%d (%.0fs, %.2fs/只, 失败 %d)",
                            i + 1, len(todo), el, el / (i + 1), n_fail)
    finally:
        bs.logout()
    logger.info("[klines] 完成: 成功 %d, 失败/空 %d", n_ok, n_fail)


# ============================================================
# Stage 3: 月末截面因子 → ETF 信号
# ============================================================
def _compute_factor_series(df: pd.DataFrame) -> dict[str, pd.Series]:
    """对单股全序列计算 15 因子 (滚动窗口, 值在 T 只用 ≤T 数据)."""
    close, vol, amount = df["close"], df["volume"], df["amount"]
    turn = df["turn"] if "turn" in df.columns else pd.Series(np.nan, index=df.index)
    ret = close.pct_change()
    out: dict[str, pd.Series] = {}

    out["mom_252_21"] = close.shift(21) / close.shift(252) - 1.0
    out["mom_120"] = close / close.shift(120) - 1.0
    out["mom_60"] = close / close.shift(60) - 1.0

    out["rev_5"] = close / close.shift(5) - 1.0
    out["rev_10"] = close / close.shift(10) - 1.0
    out["rev_ma5"] = close / close.rolling(5).mean() - 1.0

    out["vol_z_20_60"] = (vol.rolling(20).mean() / vol.rolling(60).mean() - 1.0)
    log_amt = np.log(amount.clip(lower=1.0))
    out["amount_trend"] = log_amt.rolling(20).mean() - log_amt.rolling(60).mean()
    out["turn_z_20_60"] = (turn.rolling(20).mean() / turn.rolling(60).mean() - 1.0)

    out["vol_20"] = ret.rolling(20).std()
    out["vol_60"] = ret.rolling(60).std()
    out["vol_ratio"] = out["vol_20"] / out["vol_60"] - 1.0

    out["amihud_20"] = (ret.abs() / amount.clip(lower=1.0)).rolling(20).mean()
    out["turn_20"] = turn.rolling(20).mean()
    out["zero_ret_60"] = (ret == 0).rolling(60).mean()

    return out


def _winsorize_z(s: pd.Series) -> pd.Series:
    """截面 z-score, ±3 缩尾."""
    s = s.clip(s.quantile(0.01), s.quantile(0.99))
    z = (s - s.mean()) / (s.std() if s.std() > 1e-12 else 1.0)
    return z.clip(-3.0, 3.0)


def build_signals() -> pd.DataFrame:
    cons = load_constituents()
    if not cons:
        raise FileNotFoundError("成分缓存为空, 先跑 --stage constituents")

    # 月末交易日 (来自 ETF 面板, 与回测日历一致)
    panel = pd.read_parquet(DATA_DIR / "all_etf_daily.parquet")
    dates = pd.to_datetime(panel["date"]).sort_values().unique()
    cal = pd.DatetimeIndex(dates)
    month_ends = sorted(pd.Series(cal).groupby([cal.year, cal.month]).last().tolist())

    # 逐股全序列因子 → 月末取值
    fids = list(FACTOR_DEFS)
    raw: dict[pd.Timestamp, dict[str, dict[str, float]]] = {}
    files = sorted(KLINE_DIR.glob("*.parquet"))
    for j, fp in enumerate(files):
        code = fp.stem
        try:
            df = pd.read_parquet(fp)
        except OSError:
            continue
        if len(df) < 260:
            continue  # 预热不足
        df = df.set_index("date")
        fs = _compute_factor_series(df)
        dt_idx = df.index
        for me in month_ends:
            if me not in dt_idx:
                continue
            vals = {f: float(fs[f].loc[me]) for f in fids}
            raw.setdefault(me, {})[code] = vals
        if (j + 1) % 300 == 0:
            logger.info("[build] 因子序列 %d/%d", j + 1, len(files))

    # 逐月截面合成 → ETF 聚合
    sig_rows: dict[pd.Timestamp, dict[str, float]] = {}
    for me in month_ends:
        stocks = raw.get(me, {})
        if len(stocks) < 100:
            continue  # 截面过小跳过
        fac_df = pd.DataFrame.from_dict(stocks, orient="index")[fids]
        theme_scores = {}
        for theme, _w in THEME_WEIGHTS.items():
            cols = [f for f in fids if FACTOR_DEFS[f][0] == theme]
            zs = pd.DataFrame({f: _winsorize_z(fac_df[f]) for f in cols})
            theme_scores[theme] = zs.mean(axis=1)
        composite = sum(w * theme_scores[t] for t, w in THEME_WEIGHTS.items())
        # ETF 信号 = 成分综合分等权均值 (无权重数据, 已知折衷)
        row: dict[str, float] = {}
        for etf, codes in cons.items():
            members = [c for c in codes if c in composite.index]
            if len(members) >= 20:  # 成分覆盖过低则该月无信号
                row[etf] = float(composite.loc[members].mean())
        sig_rows[me] = row
        logger.info("[build] %s: %d 只股票, %d 个 ETF 信号", me.date(), len(stocks), len(row))

    sig = pd.DataFrame.from_dict(sig_rows, orient="index").sort_index()
    sig = sig[sig.index >= pd.Timestamp(BACKTEST_START)]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sig.to_parquet(OUTPUT_PATH)
    logger.info("[build] 落盘 %s: %d 月份 × %d ETF", OUTPUT_PATH, len(sig), sig.shape[1])
    return sig


def main() -> None:
    parser = argparse.ArgumentParser(description="S13 路径 A 信号重建")
    parser.add_argument("--stage", default="all",
                        choices=["all", "constituents", "klines", "build"])
    parser.add_argument("--shard", default="0/1",
                        help="klines 分片 i/n (并行拉取), 如 0/4")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.stage in ("all", "constituents"):
        fetch_constituents()
    if args.stage in ("all", "klines"):
        i, n = (int(x) for x in args.shard.split("/"))
        fetch_klines(shard=(i, n))
    if args.stage in ("all", "build"):
        sig = build_signals()
        print(sig.tail(8).round(3).to_string())


if __name__ == "__main__":
    main()
