"""生成 200万ETF组合回测的独立图表 (PNG)。运行: py charts_etf200w.py"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).parent
CAPITAL = 2_000_000.0

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian",
                                   "SimSun", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

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


def load_equity(name: str) -> list[dict]:
    p = HERE / name
    with open(p, encoding="utf-8") as f:
        return [{"date": r["date"], "value": float(r["value"])}
                for r in csv.DictReader(f)]


def drawdown(eq: list[dict]) -> list[float]:
    peak, out = -1e18, []
    for p in eq:
        peak = max(peak, p["value"])
        out.append((1 - p["value"] / peak) * 100)
    return out


def main() -> None:
    strat = load_equity("opt200w_equity.csv")
    sched = load_equity("opt200ws_equity.csv")
    bh = load_equity("opt200wbh_equity.csv")
    bench = load_equity("opt200w_bench_equity.csv")
    dates = [p["date"] for p in strat]
    x = range(len(dates))

    # 1) 四口径净值对比
    fig, ax = plt.subplots(figsize=(12, 6))
    for cur, label, color in (
        (strat, "定期再平衡 + 回撤风控", "#d9363e"),
        (sched, "仅定期再平衡", "#f5a524"),
        (bh, "组合买入持有", "#2f7ed8"),
        (bench, "沪深300价格指数", "#8d8d8d"),
    ):
        ax.plot(x, [p["value"] / 10000 for p in cur], label=label,
                color=color, linewidth=1.6)
    ax.axhline(CAPITAL / 10000, color="#bbb", linestyle="--", linewidth=1)
    step = max(1, len(dates) // 12)
    ax.set_xticks(list(x)[::step])
    ax.set_xticklabels([dates[i] for i in list(x)[::step]], rotation=45,
                       ha="right", fontsize=8)
    ax.set_ylabel("组合净值 (万元)", fontsize=11)
    ax.set_title("200万ETF组合 回测净值对比 (2023-09-01 ~ 2026-09-11, 前复权)",
                 fontsize=13)
    ax.legend(fontsize=9)
    ax.grid(alpha=.3)
    fig.tight_layout()
    plt.show()
    fig.savefig(HERE / "opt200w_nav_compare.png", dpi=130)
    plt.close(fig)

    # 2) 回撤对比
    fig, ax = plt.subplots(figsize=(12, 5))
    for cur, label, color in (
        (strat, "定期再平衡 + 回撤风控", "#d9363e"),
        (bh, "组合买入持有", "#2f7ed8"),
        (bench, "沪深300价格指数", "#8d8d8d"),
    ):
        ax.plot(x, drawdown(cur), label=label, color=color, linewidth=1.4)
    ax.invert_yaxis()
    step = max(1, len(dates) // 12)
    ax.set_xticks(list(x)[::step])
    ax.set_xticklabels([dates[i] for i in list(x)[::step]], rotation=45,
                       ha="right", fontsize=8)
    ax.set_ylabel("回撤 (%)", fontsize=11)
    ax.set_title("回撤对比：组合最大回撤显著小于沪深300，但风控层未带来额外改善",
                 fontsize=13)
    ax.legend(fontsize=9)
    ax.grid(alpha=.3)
    fig.tight_layout()
    plt.show()
    fig.savefig(HERE / "opt200w_drawdown.png", dpi=130)
    plt.close(fig)

    # 3) 个券收益贡献归因
    import pandas as pd
    px = pd.read_csv(HERE / "klines_qfq.csv",
                     dtype={"symbol": str}).pivot(
        index="date", columns="symbol", values="close").sort_index()
    w = px[px.index >= "2023-09-01"]
    rows = []
    for s, wt in WEIGHTS.items():
        r = w[s].iloc[-1] / w[s].iloc[0] - 1
        rows.append((NAMES[s], wt, r * 100, wt * r * 100))
    rows.sort(key=lambda t: t[3], reverse=True)
    fig, ax = plt.subplots(figsize=(11, 6))
    colors = ["#d9363e" if v >= 0 else "#1f9d55" for v in (r[3] for r in rows)]
    ax.bar([r[0] for r in rows], [r[3] for r in rows], color=colors)
    ax.set_ylabel("对组合总收益的贡献 (百分点)", fontsize=11)
    ax.set_title("收益归因：纳指ETF 以 15% 权重贡献了约 36% 的组合收益", fontsize=13)
    ax.tick_params(axis="x", rotation=40, labelsize=9)
    for i, r in enumerate(rows):
        ax.text(i, r[3] + (0.25 if r[3] >= 0 else -0.45),
                f"{r[3]:+.2f}", ha="center", fontsize=8)
    ax.grid(axis="y", alpha=.3)
    fig.tight_layout()
    plt.show()
    fig.savefig(HERE / "opt200w_attribution.png", dpi=130)
    plt.close(fig)

    # 4) 期权成本敏感性
    yrs = len(dates) / 252
    base = strat[-1]["value"] / CAPITAL
    rates = [0.0, 0.005, 0.010, 0.015, 0.020]
    anns = [(((1 - r / 252) ** (len(dates) - 1)) * base) ** (1 / yrs) * 100 - 100
            for r in rates]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar([f"{r * 100:.1f}%" for r in rates], anns, color="#2f7ed8")
    ax.axhline(6.26, color="#8d8d8d", linestyle="--", linewidth=1.2,
               label="沪深300价格指数年化 6.26%")
    ax.axhline(8.0, color="#d9363e", linestyle=":", linewidth=1.2,
               label="组合目标年化 8%")
    for i, v in enumerate(anns):
        ax.text(i, v + 0.12, f"{v:.2f}%", ha="center", fontsize=9)
    ax.set_ylim(0, max(anns) * 1.25)
    ax.set_xlabel("期权净成本 (年化)", fontsize=11)
    ax.set_ylabel("策略年化收益 (%)", fontsize=11)
    ax.set_title("期权成本敏感性：年化 2% 的成本会把策略年化从 12.2% 压到 10.0%",
                 fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=.3)
    fig.tight_layout()
    plt.show()
    fig.savefig(HERE / "opt200w_option_sensitivity.png", dpi=130)
    plt.close(fig)

    print("图表已生成:", sorted(p.name for p in HERE.glob("opt200w_*.png")))


if __name__ == "__main__":
    main()
