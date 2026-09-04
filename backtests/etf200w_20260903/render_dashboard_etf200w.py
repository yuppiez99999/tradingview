# -*- coding: utf-8 -*-
"""render_dashboard_etf200w.py — 渲染 200万 ETF 月度再平衡回测 HTML 仪表盘 (index.html)。

读取 etf200w_* / etf200wbh_* 两组标准文件,
通过 build_dashboard_data + dashboard_template.html 渲染单一 index.html。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
EXPERT_CACHE = Path.home() / ".workbuddy" / "plugins" / "cache" / "experts"
REF_DIR = EXPERT_CACHE / "strategy-backtest-expert/1.0.0/skills/quant-backtest-lab/reference"
sys.path.insert(0, str(REF_DIR))
from render_dashboard import build_dashboard_data, render_dashboard  # noqa: E402


def main() -> None:
    report = build_dashboard_data(
        equity_csv=HERE / "etf200w_equity.csv",
        trades_csv=HERE / "etf200w_trades.csv",
        summary_json=HERE / "etf200w_summary.json",
        language="zh",
        market="china_a",
    )

    # ---- 基准 (买入持有) 曲线与指标 ----
    with open(HERE / "etf200wbh_summary.json", encoding="utf-8") as f:
        bh = json.load(f)
    bh_eq = []
    with open(HERE / "etf200wbh_equity.csv", encoding="utf-8") as f:
        next(f)
        for line in f:
            d, v = line.strip().split(",")
            bh_eq.append({"date": d, "value": float(v)})
    s, bs = report["summary"], bh["summary"]
    start_v = report["equity_curve"][0]["value"]
    bh_start_v = bh_eq[0]["value"]

    # ---- 主图叠加基准净值线 ----
    for m in report["modules"]:
        if m.get("type") == "overview_chart":
            m["overlay_series"] = [{
                "name": "买入持有基准",
                "stroke": "#9e9e9e",
                "points": [{"date": p["date"], "value": p["value"]} for p in bh_eq],
            }]
            break

    # ---- 对比折线图 (归一化净值, 起点=100) ----
    report["modules"].append({
        "type": "line_chart",
        "tab": "overview",
        "title": "归一化净值对比",
        "subtitle": "月度再平衡 vs 买入持有 (起点=100)",
        "series": [
            {"name": "月度再平衡",
             "points": [{"date": p["date"], "value": round(p["value"] / start_v * 100, 4)}
                        for p in report["equity_curve"]]},
            {"name": "买入持有基准",
             "points": [{"date": p["date"], "value": round(p["value"] / bh_start_v * 100, 4)}
                        for p in bh_eq]},
        ],
    })

    # ---- 双列指标对比表 ----
    def row(metric, a, b, ra=None, rb=None):
        return {"metric": metric, "values": [
            {"main": a, "raw": ra}, {"main": b, "raw": rb}]}

    report["modules"].append({
        "type": "metric_table",
        "tab": "overview",
        "title": "策略 vs 基准",
        "subtitle": "同一标的池 / 同一费用假设 / 期末强平口径一致",
        "columns": ["指标", "月度再平衡", "买入持有基准"],
        "rows": [
            row("总收益", f"{s['total_return_pct']:.2f}%", f"{bs['total_return_pct']:.2f}%",
                s["total_return_pct"], bs["total_return_pct"]),
            row("年化收益", f"{s['annual_return_pct']:.2f}%", f"{bs['annual_return_pct']:.2f}%",
                s["annual_return_pct"], bs["annual_return_pct"]),
            row("最大回撤", f"{s['max_drawdown_pct']:.2f}%", f"{bs['max_drawdown_pct']:.2f}%",
                -s["max_drawdown_pct"], -bs["max_drawdown_pct"]),
            row("Sharpe", f"{s['sharpe']:.3f}", f"{bs['sharpe']:.3f}",
                s["sharpe"], bs["sharpe"]),
            row("胜率(平仓笔)", f"{s['win_rate_pct']:.1f}%", f"{bs['win_rate_pct']:.1f}%",
                s["win_rate_pct"], bs["win_rate_pct"]),
            row("再平衡次数", "36", "0"),
            row("平仓笔数", str(s["total_trades"]), str(bs["total_trades"])),
            row("期末资产(强平后)", "¥3,217,824", "¥3,098,163"),
        ],
    })

    # ---- 文字模块 ----
    report["modules"].extend([
        {"type": "text", "tab": "overview", "title": "结论摘要",
         "text": (
             "- 评估窗口 2023-09-01 ~ 2026-09-03 (728 个交易日), 200 万 ETF 组合\n"
             "- 月度再平衡: 总收益 61.30%, 年化 18.03%, 最大回撤 21.14%, Sharpe 0.81\n"
             "- 买入持有基准: 总收益 55.30%, 年化 16.48%, 最大回撤 20.83%, Sharpe 0.77\n"
             "- 月度再平衡在本窗口同时提高收益 (+6.0pp) 和 Sharpe (+0.04), 回撤基本持平\n"
             "- 最大回撤 -21.14% 发生于 2024 年初微盘股流动性危机"
             " (2023-09-04 峰 → 2024-02-05 谷, 2024-09-30 修复), 黄金 ETF 提供了部分缓冲\n"
             "- 3 年仅产生费用 ¥2,939 (约 0.15%), ETF 组合调仓成本可忽略\n"
             "- 同期单 ETF 涨幅: 黄金 +102.8% / 科创50 +73.8% / 创业板 +62.4% /"
             " 中证500 +42.5% / 中证1000 +29.7% / 沪深300 +29.1%"
         )},
        {"type": "text", "tab": "overview", "title": "关键假设与实现口径",
         "text": (
             "- 标的与权重: config/portfolio.yaml v5.10 的 6 只 ETF"
             " (510300/510500/512100/588000/159915/518880),"
             " 按原配置权重 (8/6/4/6/6/5) 归一化到 200 万\n"
             "- 再平衡规则: 每月最后一个交易日收盘产生信号, 次日开盘价执行恢复目标权重"
             " (先卖后买, 100 份整手, 最小交易 1 手)\n"
             "- 费用: 佣金万3 (最低5元, 双边); ETF 免印花税\n"
             "- A股规则: T+1 (月末信号次日执行天然满足)\n"
             "- 数据: 腾讯自选股前复权日K线 (westock-data, 6/6 全覆盖)\n"
             "- 期末按最后交易日收盘价强制平仓, 最后一笔为强平单\n"
             "- 独立交叉验证: 零成本月末收盘近似 63.93%, 与实测 61.30% 差额由"
             " 执行时点与费用解释; 买入持有两法一致 (55.3%)"
         )},
        {"type": "text", "tab": "overview", "title": "局限与已知偏差",
         "text": (
             "- 评估窗口仅 3 年且覆盖科技牛市 + 黄金牛市, 年化 18% 不可外推;\n"
             "- 月度再平衡跑赢买入持有的机制是高波动成长 ETF (科创50/创业板)"
             " 的月度均值回归 + 股金跷跷板, 在单边趋势市中该机制会失效甚至反噬\n"
             "- 最大回撤 21.14% 超过系统 ≤15% 风控目标: 纯 ETF 组合缺乏"
             " 个股级防御与对冲工具时, 回撤红线难以达成\n"
             "- 未建模: 市场冲击成本/滑点 (ETF 流动性好, 影响小)、IA 分析师、"
             " 动量择时、期权保护等系统模块\n"
             "- 前复权价格对分红的处理依赖数据源复权口径, 与实盘税费有细微差异\n"
             "- 胜率 76.8% 由月度调仓的均值回归特性贡献, 不代表方向判断能力"
         )},
        {"type": "text", "tab": "overview", "title": "免责声明",
         "text": "⚠️ 以上内容由 AI 基于公开信息整理生成, 仅供参考, 不构成任何投资建议或个股推荐。"
                 "投资有风险, 决策需谨慎。"},
    ])

    out = render_dashboard(report, output_path=HERE / "index.html")
    print(f"[done] {out}")
    html_text = out.read_text(encoding="utf-8")
    for ph in ("__REPORT_TITLE__", "__HTML_LANG__", "__REPORT_DATA__"):
        assert ph not in html_text, f"placeholder {ph} 残留"
    print("[check] 占位符全部替换, 大小 %.1f KB" % (out.stat().st_size / 1024))


if __name__ == "__main__":
    main()
