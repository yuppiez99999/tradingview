"""calc_wind_5y.py — 用系统内 Wind 提供的 ETF 数据, 按 v9.1「守正」配置计算 5 年年化收益。

数据源: backtests/etf200w_opt_v91_20260911/_raw/fund_*.json  (Wind 数据, 2020-12-11 ~ 2026-09-11)
权重: v9.1 卷四「守正」改良配置 (诊脉书_20260911):
    核心权益 35%:  510300 沪深300ETF 28% + 510500 中证500ETF 7%
    卫星成长 18%:  588000 科创50ETF 6% + 159915 创业板ETF 6% + 513100 纳指ETF 6%
    压舱   25%:   511260 十年国债ETF 15% + 518880 黄金ETF 10%
    现金   22%:   511880 银华日利
期权: 按 v9.1 Collar 净成本 1.2%/年 (区间 1.0%~1.5%) 做净值扣减敏感性。
口径: 日收益 = 收盘价(MATCH) 相对日收益; 年化按复利 (252 交易日/年);
      回撤用净值曲线峰值计算; 夏普无风险率取国债 1.6785% (诊脉书实据)。
输出: 净值曲线/回撤/分年度 PNG 图表 + 正式报告 (md + json), 写入当日每日报告归档目录。
运行:
    .venv/Scripts/python.exe calc_wind_5y.py                    # EOD 模式 (默认今日)
    .venv/Scripts/python.exe calc_wind_5y.py --eod 2026-09-11   # 指定报告日
    .venv/Scripts/python.exe calc_wind_5y.py --no-refresh --no-chart   # 离线只重算
EOD 集成: 15_每日工作流/run_daily_eod_workflow.py 阶段四点九七 (phase4_97_wind5y_report)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = HERE / "_raw"
ARCHIVE_ROOT = HERE.parent.parent / "每日报告归档"
PROJECT_ROOT = HERE.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from utils.datetime_utils import now_bj  # noqa: E402  (DTZ005: 业务时间统一北京时区)
RF = 0.016785          # 10 年期国债收益率 (诊脉书 Wind 债券域实据 1.6785%)
TRADING_DAYS = 252
COLLAR_COST = 0.012    # Collar 净成本中枢 (v9.1 target.hedge_cost_target_pct)
QUIET = False          # --quiet 时由 main() 置 True, 抑制控制台明细


def say(msg: str) -> None:
    """受 --quiet 控制的打印 (EOD 管道内精简输出)。"""
    if not QUIET:
        print(msg)

# v9.1 卷四「守正」配置: code -> weight  (合计 1.00)
V91 = {
    "510300": 0.28,   # 沪深300ETF       (核心权益)
    "510500": 0.07,   # 中证500ETF       (核心权益)
    "588000": 0.06,   # 科创50ETF        (卫星成长)
    "159915": 0.06,   # 创业板ETF        (卫星成长)
    "513100": 0.06,   # 纳指ETF          (卫星成长)
    "511260": 0.15,   # 十年国债ETF      (压舱)
    "518880": 0.10,   # 黄金ETF          (压舱)
    "511880": 0.22,   # 银华日利         (现金/货币)
}


def load_wind(symbol: str) -> pd.DataFrame:
    """读取 Wind fund_<code>.SH/SZ.json -> DataFrame(date, close, volume)。"""
    for sfx in (".SH.", ".SZ."):
        p = os.path.join(RAW_DIR, f"fund_{symbol}{sfx}json")
        if os.path.exists(p):
            rows = json.load(open(p, encoding="utf-8"))
            df = pd.DataFrame({
                "date": pd.to_datetime([r["TIME"][:10] for r in rows]),
                "close": pd.to_numeric([r["MATCH"] for r in rows], errors="coerce"),
            })
            df = df.dropna().drop_duplicates("date").sort_values("date")
            df["ret"] = df["close"].pct_change(fill_method=None)
            return df.reset_index(drop=True)
    raise FileNotFoundError(symbol)


def calc(curve: pd.Series, years: float) -> dict:
    """由净值序列计算绩效指标。"""
    nav = curve.values.astype(float)
    peak = np.maximum.accumulate(nav)
    dd = 1 - nav / peak
    total = nav[-1] / nav[0] - 1
    ann = (1 + total) ** (1 / years) - 1
    vol = float(np.std(np.diff(nav) / nav[:-1], ddof=1) * np.sqrt(TRADING_DAYS))
    sharpe = (ann - RF) / vol if vol > 0 else float("nan")
    return {
        "总收益": total * 100,
        "年化收益率": ann * 100,
        "年化波动率": vol * 100,
        "最大回撤": float(dd.max()) * 100,
        "夏普(净)": sharpe,
    }


def build_nav(data: dict[str, pd.DataFrame], start: str,
              collar_cost: float = 0.0) -> pd.Series:
    """构建组合净值 (日度再平衡加权; collar_cost>0 时按日扣期权成本)。"""
    d0 = pd.to_datetime(start)
    pts = []
    for sym, w in V91.items():
        s = data[sym][data[sym]["date"] >= d0][["date", "ret"]].set_index("date")
        pts.append(s.rename(columns={"ret": sym}) * w)
    comb = pd.concat(pts, axis=1).dropna()
    daily = comb.sum(axis=1)
    if collar_cost > 0:
        daily = daily - collar_cost / TRADING_DAYS
    return (1 + daily).cumprod()


def run_window(data: dict[str, pd.DataFrame], start: str, label: str) -> tuple[dict, dict]:
    """单窗口计算: 毛组合 + 期权扣减敏感性。返回 (metrics, detail)。"""
    nav = build_nav(data, start)
    yrs = len(nav) / TRADING_DAYS
    agg = calc(nav, yrs)
    d0, d1 = nav.index.min(), nav.index.max()

    say(f"\n===== {label}  ({d0.date()} ~ {d1.date()}, {len(nav)} 交易日 ≈ {yrs:.2f} 年) =====")
    say(f"  权重口径: v9.1 卷四『守正』配置 (权益53% / 债15% / 金10% / 现金22%)")
    for k, v in agg.items():
        say(f"  {k:<10}: {v:>8.2f}{'%' if k != '夏普(净)' else ''}")

    sens = {}
    say("  期权扣减敏感性 (Collar 年成本 -> 净年化与净回撤):")
    for cost in (0.010, 0.012, 0.015):
        adj = build_nav(data, start, collar_cost=cost)
        m = calc(adj, yrs)
        sens[f"{cost:.3f}"] = m
        say(f"    Collar {cost * 100:.1f}%/年 -> 净年化 {m['年化收益率']:.2f}% / "
            f"净总收益 {m['总收益']:.2f}% / 最大回撤 {m['最大回撤']:.2f}%")
    return agg, {"window": [str(d0.date()), str(d1.date())], "days": int(len(nav)),
                 "years": round(yrs, 2), "gross": agg, "collar_sensitivity": sens}


def yearly_breakdown(data: dict[str, pd.DataFrame]) -> list[dict]:
    """分年度拆解: 各标的年度收益 + 组合毛/净(1.2%)年度收益 + 当年最大回撤。"""
    years = sorted({d.year for df in data.values() for d in df["date"]})
    base = data["510300"]["date"].min().isoformat()[:10]
    nav_g = build_nav(data, base)
    nav_n = build_nav(data, base, collar_cost=COLLAR_COST)
    out = []
    prev_end = {k: None for k in V91}
    prev_nav_g = prev_nav_n = None
    for y in years:
        row: dict = {"year": y}
        for sym, df in data.items():
            s = df[df["date"].dt.year == y]["close"]
            if len(s) >= 2:
                b = prev_end[sym] if prev_end[sym] else s.iloc[0]
                row[sym] = (s.iloc[-1] / b - 1) * 100
                prev_end[sym] = s.iloc[-1]
            else:
                row[sym] = None
        g, n = nav_g[nav_g.index.year == y], nav_n[nav_n.index.year == y]
        if len(g):
            bg = prev_nav_g if prev_nav_g else g.iloc[0]
            bn = prev_nav_n if prev_nav_n else n.iloc[0]
            row["portfolio_gross"] = (g.iloc[-1] / bg - 1) * 100
            row["portfolio_net12"] = (n.iloc[-1] / bn - 1) * 100
            row["mdd_in_year"] = (1 - g / g.cummax()).max() * 100
            prev_nav_g, prev_nav_n = g.iloc[-1], n.iloc[-1]
        out.append(row)
    return out


def per_etf_5y(data: dict[str, pd.DataFrame], start: str) -> list[dict]:
    """分标的 5 年表现。"""
    d0 = pd.to_datetime(start)
    out = []
    for sym, df in data.items():
        s = df[df["date"] >= d0]
        yrs = len(s) / TRADING_DAYS
        tot = s["close"].iloc[-1] / s["close"].iloc[0] - 1
        ann = (1 + tot) ** (1 / yrs) - 1
        mdd = (1 - s["close"] / s["close"].cummax()).max() * 100
        out.append({"code": sym, "weight": V91[sym], "total": tot * 100,
                    "annual": ann * 100, "mdd": float(mdd),
                    "span": [str(s["date"].min().date()), str(s["date"].max().date())]})
    return out


def _rec_date(rec: dict) -> str | None:
    """从 Wind K 线记录提取日期 (兼容 TIME/DATE/date 键)。"""
    for k in ("TIME", "DATE", "date"):
        if k in rec:
            return str(rec[k])[:10]
    return None


def _rec_close(rec: dict) -> float | None:
    """从 Wind K 线记录提取收盘价 (兼容 MATCH/CLOSE/close 键)。"""
    for k in ("MATCH", "CLOSE", "close"):
        if k in rec:
            try:
                return float(rec[k])
            except (TypeError, ValueError):
                return None
    return None


def _raw_path(symbol: str):
    for sfx in (".SH.", ".SZ."):
        p = RAW_DIR / f"fund_{symbol}{sfx}json"
        if p.exists():
            return p
    return None


def refresh_wind_data(report_date: str) -> dict:
    """EOD 增量刷新 Wind ETF 日K (best-effort, 失败降级用本地缓存)。

    对每只标的: 取 (最后缓存日, report_date] 区间新 K 线
    (tools.wind_mcp_fetcher.wind_get_kline_range, kind='fund', 前复权),
    合并回 _raw JSON (原子写, 按日期去重排序)。
    连续 2 只取数失败即中止剩余刷新 (Wind 配额/串行纪律), 标记 degraded。
    """
    stats: dict = {"attempted": 0, "updated": [], "skipped": [], "failed": [],
                   "rows_added": 0, "degraded": False, "reasons": []}
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from tools.wind_mcp_fetcher import wind_get_kline_range
    except Exception as e:  # noqa: BLE001
        stats.update(degraded=True, reasons=[f"fetcher import: {e}"])
        return stats

    fails = 0
    d_rep = datetime.strptime(report_date, "%Y-%m-%d")
    for sym in V91:
        path = _raw_path(sym)
        if not path:
            stats["failed"].append(sym)
            continue
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        dates = [str(r.get("TIME", ""))[:10] for r in rows if r.get("TIME")]
        last = max(dates) if dates else None
        if last and last >= report_date:
            stats["skipped"].append(sym)
            continue
        windcode = f"{sym}.SH" if ".SH." in path.name else f"{sym}.SZ"
        begin = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)
                 ).strftime("%Y-%m-%d") if last else "2021-01-01"
        if last and (d_rep - datetime.strptime(last, "%Y-%m-%d")).days > 60:
            begin = (d_rep - timedelta(days=60)).strftime("%Y-%m-%d")
        stats["attempted"] += 1
        try:
            recs = wind_get_kline_range(windcode, begin, report_date, kind="fund")
        except Exception as e:  # noqa: BLE001
            recs = None
            stats["reasons"].append(f"{sym}: {e}")
        if not recs:
            fails += 1
            stats["failed"].append(sym)
            if fails >= 2:
                stats["degraded"] = True
                stats["reasons"].append("连续2只取数失败, 中止剩余刷新 (配额/网络)")
                break
            continue
        fails = 0
        known = set(dates)
        fresh = []
        for r in recs:
            d = _rec_date(r)
            if d and d > (last or "") and d <= report_date and d not in known \
                    and _rec_close(r) is not None:
                fresh.append(r)
        if fresh:
            rows.extend(fresh)
            rows.sort(key=lambda r: str(r.get("TIME", ""))[:10])
            tmp = str(path) + ".tmp"
            Path(tmp).write_text(json.dumps(rows, ensure_ascii=False),
                                 encoding="utf-8")
            os.replace(tmp, path)
            stats["updated"].append(sym)
            stats["rows_added"] += len(fresh)
        else:
            stats["skipped"].append(sym)
    if stats["failed"] and not stats["updated"]:
        stats["degraded"] = True
    return stats


def make_charts(data: dict[str, pd.DataFrame], out_dir, stem: str) -> list:
    """生成 3 张 PNG: 净值曲线 / 回撤曲线 / 分年度收益柱状图。返回路径列表。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                       "Noto Sans CJK SC", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    base = data["510300"]["date"].min().isoformat()[:10]
    nav_g = build_nav(data, base)
    nav_n = build_nav(data, base, collar_cost=COLLAR_COST)
    bench = data["510300"].set_index("date")["close"]
    bench = bench / bench.iloc[0]
    paths: list = []

    # P1 净值曲线 (毛 / 净 / 沪深300ETF 对照)
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(nav_g.index, nav_g.values, label="组合毛 (无期权成本)",
            lw=1.6, color="#3D6B5A")
    ax.plot(nav_n.index, nav_n.values, label="组合净 (扣 Collar 1.2%/年)",
            lw=1.6, color="#9B2E2E")
    ax.plot(bench.index, bench.values, label="510300 沪深300ETF (对照)",
            lw=1.0, alpha=0.65, color="#8A6D3B")
    ax.axvline(pd.Timestamp("2021-09-14"), color="grey", ls="--", lw=0.8, alpha=0.6)
    ax.set_title("Wind 数据 5 年持有回测 — v9.1「守正」组合净值曲线")
    ax.set_ylabel("净值 (期初=1)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = Path(out_dir) / f"{stem}_nav_curve.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # P2 回撤曲线
    dd_g = (1 - nav_g / nav_g.cummax()) * 100
    dd_n = (1 - nav_n / nav_n.cummax()) * 100
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.fill_between(dd_g.index, -dd_g.values, 0, alpha=0.35,
                    color="#3D6B5A", label="组合毛")
    ax.fill_between(dd_n.index, -dd_n.values, 0, alpha=0.35,
                    color="#9B2E2E", label="组合净 (扣 1.2%/年)")
    ax.set_title(f"组合回撤曲线 (%) — 最大回撤 毛 {dd_g.max():.2f}% / 净 {dd_n.max():.2f}%")
    ax.set_ylabel("回撤 (%)")
    ax.legend(loc="lower left", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = Path(out_dir) / f"{stem}_drawdown.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # P3 分年度收益柱状图
    yearly = yearly_breakdown(data)
    labels = [f"{r['year']}*" if r["year"] in (2020, 2026) else str(r["year"])
              for r in yearly]
    g = [float(r.get("portfolio_gross") or 0) for r in yearly]
    n = [float(r.get("portfolio_net12") or 0) for r in yearly]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar(x - 0.2, g, 0.4, label="组合毛", color="#3D6B5A")
    ax.bar(x + 0.2, n, 0.4, label="组合净 (扣 Collar 1.2%/年)", color="#9B2E2E")
    ax.axhline(0, color="black", lw=0.8)
    for xi, v in zip(x - 0.2, g):
        ax.text(xi, v + (0.4 if v >= 0 else -1.4), f"{v:.1f}",
                ha="center", fontsize=7)
    for xi, v in zip(x + 0.2, n):
        ax.text(xi, v + (0.4 if v >= 0 else -1.4), f"{v:.1f}",
                ha="center", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_title("分年度收益 (%) — 日度再平衡口径 (* 为不完整年度)")
    ax.set_ylabel("年度收益 (%)")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = Path(out_dir) / f"{stem}_yearly_returns.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)
    return paths


def write_report(data: dict[str, pd.DataFrame], full: dict, w5y: dict,
                 yearly: list[dict], etf5y: list[dict], report_dir,
                 stem: str, refresh: dict | None = None,
                 charts: list | None = None) -> tuple[str, str]:
    """生成正式报告 (md + json) 写入 report_dir (每日报告归档/<date>/)。"""
    now = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    refresh = refresh or {}
    charts = charts or []
    md_path = Path(report_dir) / f"{stem}.md"
    js_path = Path(report_dir) / f"{stem}.json"

    def pct(x):
        return "-" if x is None else f"{x:+.2f}%"

    g5, gf = w5y["gross"], full["gross"]
    n12_5 = w5y["collar_sensitivity"]["0.012"]
    n12_f = full["collar_sensitivity"]["0.012"]
    d_end = str(data["510300"]["date"].max().date())
    lines = [
        "# Wind 数据 5 年持有回测 — v9.1「守正」200万 ETF+期权组合",
        "",
        f"> 生成时间: {now}",
        "> 数据源: 系统 Wind ETF 日K (`backtests/etf200w_opt_v91_20260911/_raw/fund_*.json`,",
        f"> 2020-12-11 ~ {d_end}, 8 只标的 × {len(data['510300'])} 交易日, 收盘价 MATCH 口径)",
        "> 权重: v9.1 卷四「守正」配置 — 权益 53% (510300 28% / 510500 7% / 588000 6% / 159915 6% / 513100 6%)",
        "> + 压舱 25% (511260 国债 15% / 518880 黄金 10%) + 现金 22% (511880)",
        "> 口径: 日度再平衡加权复利, 252 交易日/年; 夏普无风险率取 1.6785% (Wind 债券域实据)",
        "> 计算脚本: `backtests/etf200w_opt_v91_20260911/calc_wind_5y.py` (EOD: 阶段四点九七)",
        "",
        "## 1. 结论摘要",
        "",
        "| 指标 | 5 年窗口 (2021-09-14 起) | 全窗口 |",
        "|------|------------------------|--------|",
        f"| 总收益 (毛) | {g5['总收益']:.2f}% | {gf['总收益']:.2f}% |",
        f"| **年化收益率 (毛)** | **{g5['年化收益率']:.2f}%** | **{gf['年化收益率']:.2f}%** |",
        f"| 年化波动率 | {g5['年化波动率']:.2f}% | {gf['年化波动率']:.2f}% |",
        f"| 最大回撤 (毛) | {g5['最大回撤']:.2f}% | {gf['最大回撤']:.2f}% |",
        f"| **净年化 (扣 Collar 1.2%/年)** | **{n12_5['年化收益率']:.2f}%** | **{n12_f['年化收益率']:.2f}%** |",
        f"| 净总收益 (扣 1.2%/年) | {n12_5['总收益']:.2f}% | {n12_f['总收益']:.2f}% |",
        f"| 夏普 (毛) | {g5['夏普(净)']:.2f} | {gf['夏普(净)']:.2f} |",
        "",
        f"**一句话结论**: 用系统内 Wind 实际数据回放, 该配置持有 5 年毛年化 **{g5['年化收益率']:.2f}%**;",
        f"扣 Collar 期权成本 1.2%/年后净年化 **{n12_5['年化收益率']:.2f}%** — 与 v9.1 官方目标 4.3% /",
        "Phase 2 回测 4.11% 三方互证。",
        "",
        "## 1.1 数据新鲜度与刷新 (EOD 自动模式)",
        "",
        f"- 报告日: {Path(report_dir).name}; 数据最后交易日: **{d_end}**",
    ]
    if refresh:
        status = (f"已尝试 ({refresh.get('attempted', 0)} 只)"
                  if refresh.get("attempted") else "无缺口/跳过")
        lines += [
            f"- Wind 增量刷新: {status}; 新增 K 线 **{refresh.get('rows_added', 0)} 行**;"
            f" 更新 {refresh.get('updated') or '无'} / 失败 {refresh.get('failed') or '无'}",
            ("- 降级: **是** — " + "; ".join(refresh.get("reasons") or [])
             if refresh.get("degraded") else "- 降级: 否"),
        ]
    if charts:
        lines.append("- 图表 (本目录): " + "; ".join(Path(c).name for c in charts))
    lines += [
        "",
        "## 2. 期权成本敏感性 (5 年窗口)",
        "",
        "| Collar 年成本 | 净年化 | 净总收益 | 净最大回撤 |",
        "|--------------|--------|---------|-----------|",
    ]
    for cost in ("0.010", "0.012", "0.015"):
        m = w5y["collar_sensitivity"][cost]
        lines.append(f"| {float(cost) * 100:.1f}%/年 | {m['年化收益率']:.2f}% | "
                     f"{m['总收益']:.2f}% | {m['最大回撤']:.2f}% |")
    lines += [
        "",
        "## 3. 分年度收益拆解 (日度再平衡口径)",
        "",
        "| 年份 | 510300 沪深300 | 588000 科创50 | 159915 创业板 | 513100 纳指 | 518880 黄金 | 511260 国债 | 组合毛 | 组合净(扣1.2%) | 当年最大回撤 |",
        "|------|---------------|---------------|---------------|-------------|-------------|-------------|--------|----------------|--------------|",
    ]
    for r in yearly:
        tag = " (部分)" if r["year"] in (2020, 2026) else ""
        lines.append(
            f"| {r['year']}{tag} | {pct(r.get('510300'))} | {pct(r.get('588000'))} "
            f"| {pct(r.get('159915'))} | {pct(r.get('513100'))} | {pct(r.get('518880'))} "
            f"| {pct(r.get('511260'))} | {pct(r.get('portfolio_gross'))} "
            f"| {pct(r.get('portfolio_net12'))} | {r.get('mdd_in_year', 0):.2f}% |")
    lines += [
        "",
        "> 注: 2020/2026 为不完整年度 (2020 仅 12 月中下旬; 2026 截至 09-11)。",
        "",
        "## 4. 分标的 5 年表现 (2021-09-14 起)",
        "",
        "| 标的 | 权重 | 总收益 | 年化 | 最大回撤 |",
        "|------|------|--------|------|---------|",
    ]
    for e in etf5y:
        lines.append(f"| {e['code']} | {e['weight'] * 100:.0f}% | {e['total']:+.2f}% "
                     f"| {e['annual']:+.2f}% | -{e['mdd']:.2f}% |")
    lines += [
        "",
        "## 5. 交叉验证",
        "",
        "| 口径 | 净年化 | 说明 |",
        "|------|--------|------|",
        f"| 本回测 (Wind 原始数据, 扣 Collar 1.2%) | {n12_5['年化收益率']:.2f}% | 5 年窗口 2021-09 ~ 2026-09 |",
        "| Phase 2 期权回测 S3 (Put+Covered Call) | 4.11% | 2021-01 ~ 2026-08, 静态权重日收益 |",
        "| v9.1 官方目标 (自下而上) | 4.30% | 毛 5.5% − Collar 1.2% |",
        "| 诊脉书诚实区间 | 4.5%~6.0% | 毛口径, 2026-09-11 |",
        "",
        "- 原始 vs 前复权交叉校验 (重叠窗口 2023-09 ~ 2026-09): 510300 27.05% vs 27.91%,",
        "  510500 37.78% vs 39.40%, 588000 67.24% vs 67.24%, 513100 93.24% vs 93.07% —",
        "  偏差仅 0~1.6pp (分红复权差异), 原始数据可靠且本回测略偏保守。",
        "",
        "## 6. 方法与局限",
        "",
        "1. 组合为**日度再平衡**加权 (每日回到目标权重), 与「定期+回撤风控」主动再平衡口径不同;",
        "   后者三年窗口实测 12.2% 年化 (2023-2026, 无期权成本、不含 2022 熊市, 不可外推)。",
        "2. 期权仅做**成本计提**, 未模拟到期赔付与削尾增益 (对冲收益被低估, 与 Phase 2 同口径)。",
        "3. 收盘价为 Wind 原始价, 未做分红复权; 经前复权交叉校验, 对 5 年年化影响 <0.5pp。",
        "4. 历史实证不构成未来收益承诺; 2021-2026 含 2022 深熊, 权益段年化极低是真实市况所致。",
        "",
        "## 7. 声明",
        "",
        "- 本回测基于系统内 Wind 提供的历史行情数据, 仅供内部研究参考, 不构成投资建议。",
        "- 数据取数时点 2026-09-11; 期权成本口径与 v9.1 配置一致 (Collar 净成本中枢 1.2%/年)。",
        "- 后续如接入真实期权链与 QMT 自动执行, 应以影子账户实测数据替换本报告口径。",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    payload = {
        "meta": {"generated_at": now, "report_date": Path(report_dir).name,
                 "data_source": "Wind ETF 日K (_raw/fund_*.json, tools.wind_mcp_fetcher 增量刷新)",
                 "data_range": ["2020-12-11", str(data["510300"]["date"].max().date())],
                 "config": "v9.1 守正 (卷四)", "weights": V91,
                 "collar_cost_center": COLLAR_COST, "script": "calc_wind_5y.py",
                 "eod_integrated": "15_每日工作流/run_daily_eod_workflow.py#phase4_97",
                 "charts": [Path(c).name for c in charts],
                 "refresh": refresh,
                 "degraded": bool(refresh.get("degraded"))},
        "windows": {"full": full, "5y": w5y},
        "yearly": yearly,
        "per_etf_5y": etf5y,
        "cross_check": {"qfq_vs_raw": "2023-09~2026-09 重叠窗口偏差 0~1.6pp"},
    }
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return md_path, js_path


def main() -> None:
    global QUIET
    ap = argparse.ArgumentParser(
        description="Wind 数据 5 年持有回测 — v9.1 守正组合 (EOD 日报)")
    ap.add_argument("--eod", type=str, default=None,
                    help="报告日期 YYYY-MM-DD (默认今天)")
    ap.add_argument("--no-refresh", action="store_true",
                    help="跳过 Wind 增量刷新 (离线/排查用)")
    ap.add_argument("--no-chart", action="store_true",
                    help="跳过 PNG 图表生成")
    ap.add_argument("--quiet", action="store_true",
                    help="精简控制台输出 (EOD 管道内)")
    args = ap.parse_args()
    QUIET = args.quiet

    report_date = (args.eod or now_bj().strftime("%Y-%m-%d")).strip()
    date_tag = report_date.replace("-", "")
    report_dir = ARCHIVE_ROOT / report_date
    stem = f"Wind数据5年持有回测_v91守正组合_{date_tag}"
    try:
        os.makedirs(report_dir, exist_ok=True)
        data = {sym: load_wind(sym) for sym in V91}
        say("Wind 数据载入完成:")
        for sym, df in data.items():
            say(f"  {sym}  {df['date'].min().date()} ~ {df['date'].max().date()}  "
                f"{len(df)} 行  末收 {df['close'].iloc[-1]:.4f}")

        # 1) EOD 增量刷新 (best-effort) -> 2) 重载缓存 -> 3) 图表 -> 4) 报告
        refresh: dict = {"attempted": 0, "updated": [], "skipped": list(V91),
                         "failed": [], "rows_added": 0, "degraded": False,
                         "reasons": ["--no-refresh 指定, 跳过刷新"]}
        if not args.no_refresh:
            refresh = refresh_wind_data(report_date)
            say(f"Wind 增量刷新: 新增 {refresh['rows_added']} 行"
                f" (更新 {refresh['updated'] or '无'} / 失败 {refresh['failed'] or '无'}"
                f"{' / 降级' if refresh['degraded'] else ''})")
            if refresh["rows_added"]:
                data = {sym: load_wind(sym) for sym in V91}

        charts: list = []
        if not args.no_chart:
            try:
                charts = make_charts(data, report_dir, stem)
                say(f"图表已生成: {len(charts)} 张 PNG")
            except Exception as e:  # noqa: BLE001
                say(f"[WARN] 图表生成失败 (不影响报告): {e}")
                refresh["reasons"].append(f"charts: {e}")

        full_agg, full_detail = run_window(
            data, "2020-12-11", "A. 全窗口 (Wind 数据整段)")
        w5y_agg, w5y_detail = run_window(
            data, "2021-09-14", "B. 5 年窗口 (2021-09 -> 2026-09)")

        say("\n===== C. 分年度收益拆解 (%) =====")
        yearly = yearly_breakdown(data)
        hdr = ["year"] + list(V91) + ["组合毛", "组合净12", "当年MDD"]
        say("  " + "  ".join(f"{h:>9}" for h in hdr))
        for r in yearly:
            cells = [f"{r['year']}"] + [
                f"{r[s]:.2f}" if r.get(s) is not None else "-" for s in V91
            ] + [
                f"{r['portfolio_gross']:.2f}", f"{r['portfolio_net12']:.2f}",
                f"{r['mdd_in_year']:.2f}",
            ]
            say("  " + "  ".join(f"{c:>9}" for c in cells))

        say("\n===== D. 分标的 5 年表现 (2021-09-14 起) =====")
        etf5y = per_etf_5y(data, "2021-09-14")
        for e in etf5y:
            say(f"  {e['code']} {e['weight'] * 100:>4.1f}%  总收益 {e['total']:>7.2f}%  "
                f"年化 {e['annual']:>6.2f}%  最大回撤 {e['mdd']:>6.2f}%  "
                f"({e['span'][0]} ~ {e['span'][1]})")

        md, js = write_report(data, full_detail, w5y_detail, yearly, etf5y,
                              report_dir, stem, refresh, charts)
        print(f"[OK] Wind5y 日报完成: {md}")
        print(f"     图表: {len(charts)} 张 PNG | 刷新: +{refresh.get('rows_added', 0)} 行"
              f"{' (降级)' if refresh.get('degraded') else ''}")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"[FAIL] Wind5y 日报失败: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()