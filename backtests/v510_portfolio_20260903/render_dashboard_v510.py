"""render_dashboard_v510.py — 渲染 v5.10 组合回测 HTML 仪表盘 (index.html)。

读取 portfolio_v510_* / portfolio_v510bh_* 两组标准文件,
通过 build_dashboard_data + dashboard_template.html 渲染单一 index.html。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
# P1-10: 共享工具模块内联到项目内副本 (backtests/_etf_rotation_2014),
# 不再依赖外部插件缓存目录 (~/.workbuddy/plugins/cache/...)。
REF_DIR = HERE.parent / "_etf_rotation_2014"
if not (REF_DIR / "render_dashboard.py").exists():
    raise SystemExit(
        f"缺少共享工具模块副本: {REF_DIR / 'render_dashboard.py'}"
    )
sys.path.insert(0, str(REF_DIR))
from render_dashboard import build_dashboard_data, render_dashboard  # noqa: E402


def main() -> None:
    report = build_dashboard_data(
        equity_csv=HERE / "portfolio_v510_equity.csv",
        trades_csv=HERE / "portfolio_v510_trades.csv",
        summary_json=HERE / "portfolio_v510_summary.json",
        language="zh",
        market="china_a",
    )

    # ---- 基准 (买入持有) 曲线与指标 ----
    with open(HERE / "portfolio_v510bh_summary.json", encoding="utf-8") as f:
        bh = json.load(f)
    bh_eq = []
    with open(HERE / "portfolio_v510bh_equity.csv", encoding="utf-8") as f:
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
        "subtitle": "组合再平衡 vs 买入持有 (起点=100)",
        "series": [
            {"name": "组合再平衡",
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
        "columns": ["指标", "组合再平衡", "买入持有基准"],
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
            row("再平衡次数", "2", "0"),
            row("平仓笔数", str(s["total_trades"]), str(bs["total_trades"])),
        ],
    })

    # ---- 文字模块 ----
    report["modules"].extend([
        {"type": "text", "tab": "overview", "title": "结论摘要",
         "text": (
             "- 评估窗口 2023-09-01 ~ 2026-09-03 (728 个交易日), 股票/ETF 仓 400 万口径\n"
             "- 组合再平衡: 总收益 88.69%, 年化 24.62%, 最大回撤 15.02%, Sharpe 1.15\n"
             "- 买入持有基准: 总收益 108.59%, 年化 29.03%, 最大回撤 21.60%, Sharpe 1.16\n"
             "- 6 个百分点的偏离阈值非常宽松: 3 年仅触发 2 次再平衡 (2025-08-14, 2026-04-17)\n"
             "- 再平衡把最大回撤从 21.6% 压到 15.0% (满足系统 ≤15% 目标), 但在动量行情中"
             "削减了强势股 (海光信息/中际旭创), 年化收益比基准低约 4.4 个百分点\n"
             "- 收益高度集中于科技成长: 若剔除海光信息与中际旭创的贡献, 组合收益将大幅下降"
         )},
        {"type": "text", "tab": "overview", "title": "关键假设与实现口径",
         "text": (
             "- 标的与权重: config/portfolio.yaml v5.10 默认仓位 (23 只, 权重合计 0.92, 其余现金缓冲)\n"
             "- 再平衡规则: 任一标的收盘权重偏离目标 >6 个百分点且距上次再平衡 ≥5 个交易日,"
             " 次日开盘价执行\n"
             "- 费用: 佣金万3 (最低5元, 双边); 印花税万5 仅股票卖出; ETF 免印花税\n"
             "- A股规则: 100 股整手, T+1 (次日开盘执行天然满足)\n"
             "- 数据: 腾讯自选股前复权日K线 (westock-data 在线拉取, 23/23 全覆盖)\n"
             "- 期货/期权/对冲 sleeve (100万) 不在本回测范围内\n"
             "- 期末按最后交易日收盘价强制平仓, 最后一笔为强平单"
         )},
        {"type": "text", "tab": "overview", "title": "局限与已知偏差",
         "text": (
             "- 评估窗口仅 3 年且覆盖科技牛市, 年化 24.62% 不可外推; 若在震荡/熊市,"
             " 高科技权重组合回撤可能显著更大\n"
             "- 未建模: 市场冲击成本/滑点、AI 分析师模块、动量择时、风险平价优化、"
             " 对冲联动 —— 这些是 v5.10 系统宣称的能力, 实盘表现会与本回测不同\n"
             "- 胜率 95% 主要由期末强平在牛市中贡献, 不代表信号质量\n"
             "- 前复权价格回测分红处理依赖数据源复权口径, 与真实税费可能有细微差异"
         )},
        {"type": "text", "tab": "overview", "title": "免责声明",
         "text": "⚠️ 以上内容由 AI 基于公开信息整理生成, 仅供参考, 不构成任何投资建议或个股推荐。"
                 "投资有风险, 决策需谨慎。"},
    ])

    out = render_dashboard(report, output_path=HERE / "index.html")
    print(f"[done] {out}")
    # 占位符残留检查
    html_text = out.read_text(encoding="utf-8")
    for ph in ("__REPORT_TITLE__", "__HTML_LANG__", "__REPORT_DATA__"):
        assert ph not in html_text, f"placeholder {ph} 残留"
    print("[check] 占位符全部替换, 大小 %.1f KB" % (out.stat().st_size / 1024))


if __name__ == "__main__":
    main()
