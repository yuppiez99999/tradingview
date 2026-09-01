"""
渲染 ETF 动量轮动回测 HTML dashboard (单文件, 中文)。

布局 (portfolio rotation 场景):
  - overview_chart: 组合 NAV 主线 (默认模块) + overlay 沪深300ETF 买入持有基准
  - metric_table / trades_table: 默认指标与交易明细
  - line_chart: 组合 vs 基准净值对比 (双线归一化)
  - text 模块: 结论要点 / 方法与假设 / 已知偏差与限制 / 优化方向
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
sys.path.insert(0, str(BASE))
from render_dashboard import build_dashboard_data, render_dashboard  # noqa: E402

EVAL_START = "2014-01-01"
EVAL_END = "2026-09-01"

# 中文显示名 (与 social_security_etf.py 白名单一致)
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


def main() -> None:
    summary_payload = None
    with open(BASE / "etf_rotation_2014_summary.json", encoding="utf-8") as f:
        import json
        summary_payload = json.load(f)
    meta, summary = summary_payload["meta"], summary_payload["summary"]

    # 基准: 510300 后复权收盘买入持有, 与 window_start_value 同起点
    closes = pd.read_csv(DATA / "etf_close_panel.csv", index_col="date", parse_dates=True)
    bench = closes["510300"].loc[EVAL_START:EVAL_END].dropna()
    first_close = float(bench.iloc[0])
    window_start_value = float(meta["window_start_value"])
    bench_points = [
        {"date": ts.strftime("%Y-%m-%d"), "value": round(float(v) / first_close * window_start_value, 2)}
        for ts, v in bench.items()
    ]

    # 组合 NAV (归一化 100) 与基准 NAV (归一化 100) 双线
    equity = pd.read_csv(BASE / "etf_rotation_2014_equity.csv", index_col="date", parse_dates=True)["value"]
    strat_first = float(equity.iloc[0])
    nav_points = [
        {"date": ts.strftime("%Y-%m-%d"), "value": round(float(v) / strat_first * 100.0, 4)}
        for ts, v in equity.items()
    ]
    bench_nav = [
        {"date": p["date"], "value": round(p["value"] / window_start_value * 100.0, 4)}
        for p in bench_points
    ]

    report_data = build_dashboard_data(
        equity_csv=str(BASE / "etf_rotation_2014_equity.csv"),
        trades_csv=str(BASE / "etf_rotation_2014_trades.csv"),
        summary_json=str(BASE / "etf_rotation_2014_summary.json"),
        language="zh",
        market="china_a",
        extra_modules=[
            {
                "type": "line_chart",
                "tab": "overview",
                "title": "净值对比：策略 vs 沪深300ETF 买入持有",
                "subtitle": "两条线均归一化至起点 = 100",
                "series": [
                    {"name": "ETF动量轮动策略", "points": nav_points},
                    {"name": "沪深300ETF(510300)买入持有", "points": bench_nav},
                ],
            },
            {
                "type": "text",
                "tab": "overview",
                "title": "结论要点",
                "text": (
                    "- 评估窗口 2014-01-01 ~ 2026-09-01，初始净值按窗口起点实测值 98.83 万元计\n"
                    "- 期末净值 ¥1,111.26 万，累计收益 +1024.40%，年化 +21.90%，夏普 0.755\n"
                    "- 最大回撤 47.43%（2018 熊市与 2015 年中段大幅回撤主导），波动偏大\n"
                    "- 胜率仅 44.2%（1115 笔闭环交易），靠趋势段大赚覆盖高频小亏\n"
                    "- 换仓 1051 次、累计双边换手 373 倍——20 日动量 Top3 等权引擎换手极高，"
                    "佣金拖累显著（约 -11% 累计）"
                ),
            },
            {
                "type": "text",
                "tab": "overview",
                "title": "方法与假设",
                "text": (
                    "- 策略内核复现 28 系统 utils/strategy/etf_rotation/engine.py："
                    "momentum = 收盘价 20 日收益率，每日取前 3 名等权（1/3）\n"
                    "- 轮动池 = 社保风格 11 只 ETF 白名单（utils/social_security_etf.py），"
                    "按上市时间动态扩展：2014 年初仅 4 只可用，后逐步扩至 11 只\n"
                    "- 信号 T 日生成、T+1 开盘执行（shift(1) 防 look-ahead）；"
                    "权益曲线按引擎收盘-收盘向量化口径计算\n"
                    "- 换手率 turnover = |Δ权重|/2，佣金双边 3bp/边（与引擎一致），ETF 免印花税\n"
                    "- 数据源：通达信后复权日线（tqFlag=2）；评估窗口由 export_results 切片重算指标\n"
                    "- 期末最后一个 bar 按收盘价强制平仓（5 笔），持仓周期交易逐笔计入 trades.csv"
                ),
            },
            {
                "type": "text",
                "tab": "overview",
                "title": "已知偏差与限制",
                "text": (
                    "- 池子偏差（必须披露）：11 只白名单中仅 510300/159915/518880/512010 覆盖 2014 起点；"
                    "512880(2016-08)、512100(2016-11)、512800(2017-08)、512760/512170(2019-06)、"
                    "515030(2020-03)、588000(2020-11) 上市后才进入候选池\n"
                    "- 早期动量排序仅在 4 只老 ETF 之间进行，2020 年后才真正逼近生产现状——"
                    "前期结果不代表当前白名单的完整表现\n"
                    "- 后复权口径未含 ETF 现金分红再投资细节；trades.csv 的逐笔盈亏按 T+1 开盘价记录，"
                    "与收盘-收盘向量化权益曲线存在微小价差，权益曲线为准\n"
                    "- 未建模冲击成本与涨跌停无法成交；2026-09-01 为当日盘中数据（部分标的价格为最后成交价）\n"
                    "- 白名单为社保风格主观筛选，存在幸存者偏差：标的 2014 年并不存在"
                ),
            },
            {
                "type": "text",
                "tab": "overview",
                "title": "优化方向",
                "text": (
                    "- 换手抑制：加入最小动量差距/排名滞留期/再平衡缓冲带，可显著降低 373 倍累计换手\n"
                    "- 参数敏感性：lookback∈{10,20,60}、holdings∈{2,3,5} 网格（引擎自带 WFO→VEC→BT 三层验证）\n"
                    "- 行业约束：按四风格加权（顺周期 0.25/高端制造 0.35/资源 0.20/防御 0.20）限制单一风格暴露\n"
                    "- 风控增强：接入 28 系统动态止损与 VIX/压力测试黑天鹅防御模块\n"
                    "- 数据升级：Wind/腾讯财经全历史 + 分红复权精细化，替换通达信近 700 根/段分页"
                ),
            },
        ],
    )

    # 注入基准 overlay 到主图 (绝对模式按 window_start_value 同起点; 百分比模式模板自动归一化)
    for module in report_data["modules"]:
        if module.get("type") == "overview_chart":
            module["overlay_series"] = [
                {
                    "name": "沪深300ETF 买入持有",
                    "stroke": "#9e9e9e",
                    "points": bench_points,
                }
            ]
            break

    out = BASE / "index.html"
    render_dashboard(report_data, output_path=out)
    print(f"[OK] dashboard 已渲染: {out}")
    print(f"     modules = {[m['type'] for m in report_data['modules']]}")
    print(f"     ui.language = {report_data['ui']['language']}")


if __name__ == "__main__":
    main()
