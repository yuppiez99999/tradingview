"""渲染 200万ETF组合回测仪表盘 index.html。
运行: py render_dashboard_etf200w.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
REF_DIR = HERE.parent / "_etf_rotation_2014"
sys.path.insert(0, str(REF_DIR))
from render_dashboard import build_dashboard_data, render_dashboard  # noqa: E402

CAPITAL = 2_000_000.0
PREFIX = "opt200w"

NAMES = {
    "sh510300": "沪深300ETF", "sh510500": "中证500ETF", "sh513100": "纳指ETF",
    "sh512890": "红利低波ETF", "sh588000": "科创50ETF", "sh512760": "半导体芯片ETF",
    "sh515070": "人工智能ETF", "sh516160": "新能源ETF", "sh515790": "光伏ETF",
    "sh513180": "恒生科技ETF", "sz159920": "恒生ETF", "sh511010": "国债ETF",
    "sh511880": "银华日利",
}
WEIGHTS = {
    "sh510300": .15, "sh510500": .15, "sh513100": .15, "sh512890": .15,
    "sh588000": .035, "sh512760": .035, "sh515070": .030, "sh516160": .040,
    "sh515790": .035, "sh513180": .040, "sz159920": .035,
    "sh511010": .10, "sh511880": .05,
}
CORE = ["sh510300", "sh510500", "sh513100", "sh512890"]
SAT = ["sh588000", "sh512760", "sh515070", "sh516160", "sh515790", "sh513180",
       "sz159920"]
CASH = ["sh511010", "sh511880"]


def load_eq(name: str) -> list[dict]:
    with open(HERE / name, encoding="utf-8") as f:
        return [{"date": r["date"], "value": float(r["value"])}
                for r in csv.DictReader(f)]


def stats(cur: list[dict]) -> dict:
    peak, mdd = -1e18, 0.0
    rets = []
    for i, p in enumerate(cur):
        peak = max(peak, p["value"])
        mdd = max(mdd, 1 - p["value"] / peak)
        if i:
            rets.append(p["value"] / cur[i - 1]["value"] - 1)
    yrs = len(cur) / 252
    tot = cur[-1]["value"] / CAPITAL - 1
    import math
    sharpe = (sum(rets) / len(rets)) / (pd.Series(rets).std(ddof=0)) * math.sqrt(252)
    return {"total": tot * 100, "ann": ((1 + tot) ** (1 / yrs) - 1) * 100,
            "mdd": mdd * 100, "sharpe": sharpe}


def fmt(v: float, kind: str = "pct") -> dict:
    if kind == "pct":
        return {"main": f"{v:+.2f}%" if abs(v) < 1000 else f"{v:.2f}%"}
    return {"main": f"{v:.3f}"}


def max_dd_of(cur: list[dict]) -> float:
    peak, mdd = -1e18, 0.0
    for p in cur:
        peak = max(peak, p["value"])
        mdd = max(mdd, 1 - p["value"] / peak)
    return mdd * 100


def yearly(cur: list[dict]) -> dict[str, float]:
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for p in cur:
        y = p["date"][:4]
        first.setdefault(y, p["value"])
        last[y] = p["value"]
    out, prev = {}, None
    for y in sorted(first):
        base = prev if prev else first[y]
        out[y] = (last[y] / base - 1) * 100
        prev = last[y]
    return out


def attribution_html() -> str:
    px = pd.read_csv(HERE / "klines_qfq.csv", dtype={"symbol": str}).pivot(
        index="date", columns="symbol", values="close").sort_index()
    w = px[px.index >= "2023-09-01"]
    rows = []
    for s, wt in WEIGHTS.items():
        r = w[s].iloc[-1] / w[s].iloc[0] - 1
        rows.append((NAMES[s], wt, r, wt * r))
    rows.sort(key=lambda t: -t[3])
    mx = max(abs(t[3]) for t in rows)
    tr = "".join(
        f'<tr><td class="bt-custom-name">{n}</td>'
        f'<td class="bt-custom-w">{wt:.1%}</td>'
        f'<td class="bt-custom-r" style="color:{"#d9363e" if r >= 0 else "#1f9d55"}">'
        f'{r:+.2%}</td>'
        f'<td><div class="bt-custom-barwrap"><div class="bt-custom-bar" '
        f'style="width:{abs(c) / mx * 100:.1f}%;'
        f'background:{"#d9363e" if c >= 0 else "#1f9d55"}"></div></div></td>'
        f'<td class="bt-custom-c">{c * 100:+.2f}pp</td></tr>'
        for n, wt, r, c in rows)
    return (
        '<div class="bt-custom-attr"><table class="bt-custom-table">'
        '<thead><tr><th>标的</th><th>目标权重</th><th>区间收益</th>'
        f'<th>对总收益 {sum(t[3] for t in rows) * 100:.2f} 个百分点的贡献</th>'
        '<th>贡献</th></tr></thead><tbody>' + tr + "</tbody></table>"
        '<p class="bt-custom-note">口径：按期初目标权重静态持有（买入持有）计算；'
        '实际含再平衡的组合与该口径略有差异。</p></div>'
        "<style>.bt-custom-attr{font-size:13px}.bt-custom-table{width:100%;"
        "border-collapse:collapse}.bt-custom-table th,.bt-custom-table td{"
        "padding:4px 8px;border-bottom:1px solid rgba(128,128,128,.2);"
        "text-align:left}.bt-custom-w,.bt-custom-r,.bt-custom-c{"
        "font-variant-numeric:tabular-nums;white-space:nowrap}.bt-custom-barwrap"
        "{background:rgba(128,128,128,.12);border-radius:3px;height:14px;"
        "min-width:120px}.bt-custom-bar{height:14px;border-radius:3px}"
        ".bt-custom-note{opacity:.65;font-size:12px;margin-top:8px}</style>")


def rebalance_html() -> str:
    with open(HERE / f"{PREFIX}_rebalance_log.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    reason_cn = {"initial": "建仓", "scheduled": "定期(3/9/12月)",
                 "deviation": "类别偏离≥5pp", "drawdown": "回撤降档"}
    tr = "".join(
        f'<tr><td>{r["date"]}</td><td>{reason_cn.get(r["reason"], r["reason"])}</td>'
        f'<td>{r["state"]}</td>'
        f'<td style="text-align:right">{float(r["turnover_cny"]):,.0f}</td></tr>'
        for r in rows)
    return ('<div class="bt-custom-rb"><table class="bt-custom-table2">'
            "<thead><tr><th>执行日(次日开盘)</th><th>触发原因</th>"
            "<th>当时档位</th><th>成交额(元)</th></tr></thead><tbody>"
            f"{tr}</tbody></table>"
            f"<p class='bt-custom-note'>共 {len(rows)} 次调仓。定期触发 "
            f"{sum(1 for r in rows if r['reason'] == 'scheduled')} 次、"
            f"回撤降档触发 "
            f"{sum(1 for r in rows if r['reason'] == 'drawdown')} 次、"
            f"类别偏离触发 0 次（定期调仓先行复位，偏离从未累计到 5pp）。</p></div>"
            "<style>.bt-custom-table2{width:100%;border-collapse:collapse;"
            "font-size:13px}.bt-custom-table2 th,.bt-custom-table2 td{padding:4px"
            " 8px;border-bottom:1px solid rgba(128,128,128,.2);text-align:left;"
            "font-variant-numeric:tabular-nums}</style>")


def option_html() -> str:
    rows = [(0.0, 2795023), (0.005, 2754667), (0.010, 2714893),
            (0.015, 2675692), (0.020, 2637057)]
    tr = "".join(
        f'<tr><td>{r * 100:.1f}%</td><td style="text-align:right">{v:,.0f}</td>'
        f'<td style="text-align:right">{(v / CAPITAL) ** (252 / 734) * 100 - 100:+.2f}%'
        "</td></tr>" for r, v in rows)
    return ('<div><table class="bt-custom-table3"><thead><tr><th>期权年化净成本</th>'
            "<th>期末净值(元)</th><th>年化收益</th></tr></thead><tbody>" + tr +
            "</tbody></table><p class='bt-custom-note'>期权本身不做定价（超出日线回测范围）。"
            "按 config 预算：Protective Put 1.5%/年 + 尾部保护 0.5%/年 ≈ 2%/年毛成本；"
            "若 Covered Call 权利金收入能覆盖一半，净成本约 1%/年。</p></div>"
            "<style>.bt-custom-table3{width:100%;border-collapse:collapse;"
            "font-size:13px}.bt-custom-table3 th,.bt-custom-table3 td{padding:4px 8px;"
            "border-bottom:1px solid rgba(128,128,128,.2);text-align:left;"
            "font-variant-numeric:tabular-nums}.bt-custom-note{opacity:.65;"
            "font-size:12px;margin-top:8px}</style>")


def main() -> None:
    strat = load_eq(f"{PREFIX}_equity.csv")
    sched = load_eq(f"{PREFIX}s_equity.csv")
    bh = load_eq(f"{PREFIX}bh_equity.csv")
    bench = load_eq(f"{PREFIX}_bench_equity.csv")
    s_strat, s_sched, s_bh, s_bench = (stats(c) for c in
                                       (strat, sched, bh, bench))
    y_all = [yearly(c) for c in (strat, sched, bh, bench)]
    years = sorted({y for d in y_all for y in d})
    year_lbl = {y: (y + "YTD" if y == "2026" else y) for y in years}

    def wan(cur: list[dict]) -> list[dict]:
        return [{"date": p["date"], "value": round(p["value"] / 10000, 2)}
                for p in cur]

    comparison_modules = [
        {"type": "text", "tab": "overview", "title": "结论摘要",
         "text": (
             "**三年（2023-09 ~ 2026-09）组合年化约 12.2%，跑赢沪深300 约 6 个百分点，"
             "最大回撤 11.7% 明显小于指数的 17.9%。但拆开看，超额收益主要不是「策略」创造的，"
             "而是「纳指 QDII + 科技卫星」这两个事后才知道的赢家贡献的。**\n\n"
             "1. **回撤风控层基本无效**：仅定期再平衡年化 12.27%、最大回撤 12.08%；"
             "加上回撤分级减仓后年化 12.18%、最大回撤 11.75% —— 多花 953 元费用、多 61 笔交易，"
             "换回 0.33 个百分点的回撤改善，收益反而少 0.35 个百分点。四次 WARN 降档"
             "（2024-01 / 2024-08 / 2025-04 / 2026-03 / 2026-07）都是跌满 8% 才减仓、"
             "反弹后又买回，属于典型的「低位减仓、高位补仓」。\n"
             "2. **真正贡献回撤优势的是配置本身**：15% 现金/债券 + 15% 纳指 QDII（与 A 股低相关）"
             "才是把回撤从 17.9% 压到 11.7% 的原因，与风控触发器无关。\n"
             "3. **卫星仓位没有做出 alpha**：卫星 25% 权重只贡献了约 24% 的收益，"
             "与权重相当；而纳指 ETF 以 15% 权重贡献了约 36% 的收益。"
             "组合对单一资产的依赖度很高，且该 ETF 的境内对冲工具（config 写 reverse_etf_or_futures）"
             "实际不可用。\n"
             "4. **期权成本必须当真**：按 config 预算的 2%/年，策略年化会从 12.18% 降到 9.96%，"
             "直接吃掉相对沪深300超额的三分之一。\n"
             "5. **DANGER / BREACH 两档从未触发**：12% / 15% 的减仓逻辑三年内没有执行过一次，"
             "属于未经验证的代码路径。")},
        {"type": "line_chart", "tab": "overview", "title": "四口径净值对比",
         "subtitle": "单位：万元　前复权日K　2023-09-01 ~ 2026-09-11",
         "series": [
             {"name": "定期再平衡+回撤风控", "points": wan(strat)},
             {"name": "仅定期再平衡", "points": wan(sched)},
             {"name": "组合买入持有", "points": wan(bh)},
             {"name": "沪深300价格指数", "points": wan(bench)},
         ]},
        {"type": "metric_table", "tab": "overview", "title": "核心指标对比",
         "columns": ["指标", "定期+回撤风控", "仅定期再平衡", "买入持有", "沪深300指数"],
         "rows": [
             {"metric": "总收益", "values": [fmt(s["total"]) for s in
                                            (s_strat, s_sched, s_bh, s_bench)]},
             {"metric": "年化收益", "values": [fmt(s["ann"]) for s in
                                              (s_strat, s_sched, s_bh, s_bench)]},
             {"metric": "最大回撤", "values": [fmt(-s["mdd"]) for s in
                                              (s_strat, s_sched, s_bh, s_bench)]},
             {"metric": "夏普(无风险=0)", "values": [fmt(s["sharpe"], "num") for s in
                                                    (s_strat, s_sched, s_bh, s_bench)]},
             *[{"metric": year_lbl[y], "values": [fmt(d[y]) for d in y_all]}
               for y in years],
             {"metric": "调仓次数", "values": [{"main": "20"}, {"main": "10"},
                                              {"main": "1"}, {"main": "—"}]},
         ]},
        {"type": "custom_html", "tab": "overview", "title": "收益归因（买入持有口径）",
         "html": attribution_html()},
    ]

    report = build_dashboard_data(
        equity_csv=HERE / f"{PREFIX}_equity.csv",
        trades_csv=HERE / f"{PREFIX}_trades.csv",
        summary_json=HERE / f"{PREFIX}_summary.json",
        language="zh", market="china_a",
        ui_overrides={
            "subtitle": "200万 ETF+期权组合 · 配置与风控层回测 · 2023-09 ~ 2026-09",
            "active_tab": "overview",
            "tabs": [{"id": "overview", "label": "总览对比"},
                     {"id": "detail", "label": "策略详情"},
                     {"id": "risk", "label": "再平衡与风控"}],
        },
    )
    # 先把默认生成的单策略模块整体归入「策略详情」标签页
    for m in report["modules"]:
        if m.get("tab") == "overview":
            m["tab"] = "detail"
    # 再追加对比总览与风控标签页的模块
    report["modules"].extend(comparison_modules + [
        {"type": "text", "tab": "risk", "title": "再平衡与回撤档位",
             "text": (
                 "**档位规则（来自 config/portfolio_200w_etf.yaml）**\n\n"
                 "- NORMAL（回撤 < 5%）：核心 60% / 卫星 25% / 现金 15%\n"
                 "- WARN（回撤 8%~12%）：卫星减持 20% → 20%，现金升至 20%\n"
                 "- DANGER（回撤 12%~15%）：核心减 10% → 54%，卫星全清\n"
                 "- BREACH（回撤 > 15%）：核心减至 40%，卫星全清，现金 60%\n\n"
                 "三年样本中：NORMAL 停留 625 天、WARN 109 天、DANGER / BREACH 各 0 天。\n\n"
                 "**⚠️ 关键发现：DANGER 与 BREACH 两档从未触发，这套减仓逻辑没有被样本验证过。**"
                 "若未来真出现 15% 级别回撤，这段代码属于首次实战路径。")},
            {"type": "custom_html", "tab": "risk", "title": "调仓日志（20 次）",
             "html": rebalance_html()},
            {"type": "custom_html", "tab": "risk", "title": "期权成本敏感性",
             "html": option_html()},
        ],
    )
    report["modules"].append(
        {"type": "text", "tab": "detail", "title": "实现口径与已知局限",
         "text": (
             "**口径**\n"
             "- 标的与权重：config/portfolio_200w_etf.yaml（核心 4 只各 15%、卫星 7 只共 25%、"
             "现金 2 只共 15%）\n"
             "- 数据：westock 前复权日K，2023-08-01 ~ 2026-09-11，评估窗 2023-09-01 起，"
             "14 个标的各 734 个交易日、零缺失\n"
             "- 执行：信号当日收盘产生、次一交易日开盘成交；100 份整手；佣金万3（单笔最低 5 元，双边）；"
             "免印花税；单边滑点 5bp\n"
             "- 停牌不可成交、估值沿用最近收盘；开盘封涨停不买 / 封跌停不卖（样本内仅 2024-10-08 触发 5 次买入拦截）\n"
             "- 期末最后交易日收盘强制平仓\n\n"
             "**已知偏差**\n"
             "- **幸存者 / 后视偏差（最关键）**：标的清单取自今天的 config 回测过去，"
             "纳指 +93%、芯片 +113%、AI +106% 都是事后已知的赢家，真实可实现收益必然低于回测值\n"
             "- **基准口径**：沪深300 用的是价格指数（不含股息），ETF 用前复权（含分红），"
             "该口径差约 2.9 个百分点/年 —— 组合相对指数的真实超额约 3pp/年，而不是 5.9pp/年\n"
             "- **整手取整**：国债ETF(128元/份)、银华日利(97元/份) 单价高，200万规模下取整误差约 0.57% 残余现金\n"
             "- **未回测的部分**：期权不做定价（只做成本敏感性）；卫星「景气度×0.5 + 估值×0.3 + 资金流×0.2」"
             "轮动依赖 PMI/PE分位/资金流历史序列，无法取得，按静态权重处理 —— 这部分 alpha 未被验证\n\n"
             "⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。"
             "投资有风险，决策需谨慎。")})

    out = render_dashboard(report, HERE / "index.html",
                           template_path=REF_DIR / "dashboard_template.html")
    print("已渲染:", out, f"{out.stat().st_size / 1024:.0f} KB")
    if "--selfcheck" in sys.argv:      # 自检用: 单独渲染另两个标签页
        for tab in ("detail", "risk"):
            report["ui"]["active_tab"] = tab
            p = render_dashboard(report, HERE / f"check_{tab}.html",
                                 template_path=REF_DIR / "dashboard_template.html")
            print("自检页:", p.name)


if __name__ == "__main__":
    main()
