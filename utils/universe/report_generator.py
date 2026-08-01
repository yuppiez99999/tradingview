"""
可视化报告生成器

生成内容：
1. 候选股清单（CSV + Markdown 表）
2. 行业分布饼图
3. 因子贡献柱状图
4. 风险敞口雷达图
5. 打分分布直方图
6. 汇总报告 Markdown
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 中文字体配置（matplotlib 中文显示）
try:
    import matplotlib

    matplotlib.use("Agg")  # 非交互式后端
    import matplotlib.pyplot as plt

    # 尝试设置中文字体
    for font in ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]:
        try:
            matplotlib.rcParams["font.sans-serif"] = [font]
            matplotlib.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            continue
    _MPL_OK = True
except ImportError:
    _MPL_OK = False
    logger.warning("matplotlib 未安装，将跳过图表生成")


@dataclass
class ReportPaths:
    """报告输出路径"""

    candidates_csv: str = ""
    portfolio_json: str = ""
    report_md: str = ""
    chart_industry: str = ""
    chart_factor_contrib: str = ""
    chart_risk_radar: str = ""
    chart_score_dist: str = ""


def _save_industry_pie(portfolio_df: pd.DataFrame, output_path: Path) -> str:
    """行业分布饼图"""
    if not _MPL_OK:
        return ""
    industry_exp = portfolio_df.groupby("industry")["weight"].sum().sort_values(ascending=False)
    # 只显示 Top 8，其余合并
    if len(industry_exp) > 8:
        top = industry_exp.head(8)
        others = industry_exp.iloc[8:].sum()
        industry_exp = pd.concat([top, pd.Series({"其他": others})])

    _fig, ax = plt.subplots(figsize=(10, 7))
    colors = plt.cm.Set3(np.linspace(0, 1, len(industry_exp)))
    _wedges, texts, autotexts = ax.pie(
        industry_exp.values,
        labels=industry_exp.index,
        autopct="%1.1f%%",
        startangle=90,
        colors=colors,
        pctdistance=0.85,
    )
    for t in texts:
        t.set_fontsize(9)
    for t in autotexts:
        t.set_fontsize(8)
        t.set_color("black")
    ax.set_title(f"组合行业分布 (n={len(portfolio_df)})", fontsize=14, pad=20)
    plt.tight_layout()
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close()
    return str(output_path)


def _save_factor_contrib_bar(portfolio_df: pd.DataFrame, output_path: Path) -> str:
    """因子贡献柱状图"""
    if not _MPL_OK:
        return ""
    themes = ["momentum", "reversal", "volume", "volatility", "liquidity"]
    cols = [f"{t}_score" for t in themes if f"{t}_score" in portfolio_df.columns]
    if not cols:
        return ""
    avg_scores = portfolio_df[cols].mean()
    avg_scores.index = [c.replace("_score", "") for c in avg_scores.index]

    _fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(avg_scores.index, avg_scores.values, color=plt.cm.viridis(np.linspace(0, 0.8, len(avg_scores))))
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.set_xlabel("因子主题", fontsize=12)
    ax.set_ylabel("平均得分 (z-score)", fontsize=12)
    ax.set_title("组合各因子主题贡献度", fontsize=14, pad=15)
    for bar, val in zip(bars, avg_scores.values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{val:.3f}",
            ha="center",
            va="bottom" if val >= 0 else "top",
            fontsize=10,
        )
    plt.tight_layout()
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close()
    return str(output_path)


def _save_risk_radar(portfolio_df: pd.DataFrame, output_path: Path) -> str:
    """风险敞口雷达图"""
    if not _MPL_OK:
        return ""
    themes = ["momentum", "reversal", "volume", "volatility", "liquidity"]
    cols = [f"{t}_score" for t in themes if f"{t}_score" in portfolio_df.columns]
    if len(cols) < 3:
        return ""

    # 归一化到 [0, 1]
    vals = []
    labels = []
    for c in cols:
        v = portfolio_df[c].mean()
        # z-score 转 [0,1]
        v_norm = 1 / (1 + np.exp(-v))  # sigmoid
        vals.append(v_norm)
        labels.append(c.replace("_score", ""))

    # 雷达图
    angles = np.linspace(0, 2 * np.pi, len(vals), endpoint=False).tolist()
    vals_closed = [*vals, vals[0]]
    angles_closed = [*angles, angles[0]]

    _fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(projection="polar"))
    ax.plot(angles_closed, vals_closed, "o-", linewidth=2, color="#2E86AB")
    ax.fill(angles_closed, vals_closed, alpha=0.25, color="#2E86AB")
    ax.set_xticks(angles)
    ax.set_xticklabels(labels, fontsize=12)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75])
    ax.set_yticklabels(["0.25", "0.50", "0.75"], fontsize=9)
    ax.set_title("组合因子风险敞口 (sigmoid 归一化)", fontsize=13, pad=20)
    plt.tight_layout()
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close()
    return str(output_path)


def _save_score_dist(scores_df: pd.DataFrame, output_path: Path, top_n: int = 100) -> str:
    """打分分布直方图"""
    if not _MPL_OK or "composite_score" not in scores_df.columns:
        return ""
    _fig, ax = plt.subplots(figsize=(11, 6))
    all_scores = scores_df["composite_score"].dropna()
    ax.hist(all_scores, bins=50, color="#5DADE2", alpha=0.7, edgecolor="white", label="全市场")

    # 标记 Top N
    top_scores = all_scores.nlargest(top_n)
    ax.hist(top_scores, bins=20, color="#E74C3C", alpha=0.7, edgecolor="white", label=f"Top {top_n}")
    ax.axvline(
        x=top_scores.min(),
        color="#C0392B",
        linestyle="--",
        linewidth=2,
        label=f"Top {top_n} 阈值={top_scores.min():.3f}",
    )
    ax.set_xlabel("综合得分", fontsize=12)
    ax.set_ylabel("股票数量", fontsize=12)
    ax.set_title(f"全市场综合得分分布 (n={len(all_scores)})", fontsize=14, pad=15)
    ax.legend(fontsize=10)
    plt.tight_layout()
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close()
    return str(output_path)


def _generate_markdown_report(
    portfolio_df: pd.DataFrame,
    portfolio,
    filter_stats: dict,
    scores_df: pd.DataFrame,
    output_path: Path,
) -> str:
    """生成 Markdown 汇总报告"""
    lines = [
        "# 全市场自动选股报告",
        "",
        f"**交易日期**: {portfolio.trade_date}",
        f"**股票池**: {portfolio.universe_size} 只 → 风险过滤后 {portfolio.filtered_size} 只",
        f"**最终持仓**: {len(portfolio.holdings)} 只 (短线 {sum(1 for h in portfolio.holdings if h.layer == 'short')} + 中线 {sum(1 for h in portfolio.holdings if h.layer == 'mid')} + 长线 {sum(1 for h in portfolio.holdings if h.layer == 'long')})",
        "",
        "## 风险过滤统计",
        "",
        "| 过滤项 | 剔除数 |",
        "|--------|--------|",
    ]
    for k, v in filter_stats.items():
        if k not in ("initial", "final", "pass_rate", "total_removed"):
            lines.append(f"| {k} | {v} |")
    lines.append(f"| **合计剔除** | {filter_stats.get('total_removed', 0)} |")
    lines.append(f"| **通过率** | {filter_stats.get('pass_rate', '0%')} |")
    lines.append("")

    # 行业暴露
    lines.extend(
        [
            "## 行业暴露 Top 10",
            "",
            "| 行业 | 权重 | 持仓数 |",
            "|------|------|--------|",
        ]
    )
    ind_summary = (
        portfolio_df.groupby("industry")
        .agg(
            weight=("weight", "sum"),
            count=("symbol", "count"),
        )
        .sort_values("weight", ascending=False)
    )
    for ind, row in ind_summary.head(10).iterrows():
        lines.append(f"| {ind} | {row['weight'] * 100:.2f}% | {int(row['count'])} |")
    lines.append("")

    # Top 10 持仓
    lines.extend(
        [
            "## Top 10 持仓",
            "",
            "| 代码 | 名称 | 行业 | 层 | 权重 | 综合得分 | 排名 |",
            "|------|------|------|----|------|---------|------|",
        ]
    )
    top10 = portfolio_df.sort_values("weight", ascending=False).head(10)
    for _, row in top10.iterrows():
        lines.append(
            f"| {row['symbol']} | {row['name']} | {row['industry']} | {row['layer']} | "
            f"{row['weight'] * 100:.2f}% | {row['composite_score']:.4f} | {row['rank']} |"
        )
    lines.append("")

    # 完整持仓清单（按层分组）
    lines.extend(["## 完整持仓清单", ""])
    for layer, label in [("short", "短线层"), ("mid", "中线层"), ("long", "长线层")]:
        layer_df = portfolio_df[portfolio_df["layer"] == layer].sort_values("rank")
        if layer_df.empty:
            continue
        lines.extend(
            [
                f"### {label} ({len(layer_df)} 只)",
                "",
                "| 代码 | 名称 | 行业 | 权重 | 综合得分 | 排名 | 入选理由 |",
                "|------|------|------|------|---------|------|---------|",
            ]
        )
        for _, row in layer_df.iterrows():
            lines.append(
                f"| {row['symbol']} | {row['name']} | {row['industry']} | "
                f"{row['weight'] * 100:.2f}% | {row['composite_score']:.4f} | {row['rank']} | {row['reason']} |"
            )
        lines.append("")

    # 风险指标
    lines.extend(
        [
            "## 风险指标",
            "",
            f"- 组合 HHI 集中度: {portfolio.concentration_hhi:.4f} (越低越分散)",
            f"- 最大单股权重: {portfolio_df['weight'].max() * 100:.2f}%",
            f"- 最大行业暴露: {ind_summary['weight'].max() * 100:.2f}%",
            f"- 平均综合得分: {portfolio_df['composite_score'].mean():.4f}",
            "",
            "---",
            "*本报告由对冲基金级全市场选股系统自动生成*",
        ]
    )

    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    return str(output_path)


def generate_full_report(
    portfolio,
    scores_df: pd.DataFrame,
    filter_stats: dict,
    output_dir,
    theme_stats: dict | None = None,
) -> ReportPaths:
    """生成完整可视化报告

    Args:
        portfolio: LayeredPortfolio
        scores_df: 横截面打分 DataFrame
        filter_stats: 风险过滤统计
        output_dir: 输出目录
        theme_stats: 各主题因子数统计

    Returns:
        ReportPaths: 所有输出文件路径
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(exist_ok=True)

    paths = ReportPaths()
    portfolio_df = portfolio.to_df()

    # 1. 候选股清单 CSV
    csv_path = output_dir / "candidates.csv"
    portfolio_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    paths.candidates_csv = str(csv_path)
    logger.info(f"  ✅ 候选股清单: {csv_path}")

    # 2. 组合 JSON
    json_path = output_dir / "portfolio.json"
    portfolio_json = {
        "trade_date": portfolio.trade_date,
        "universe_size": portfolio.universe_size,
        "filtered_size": portfolio.filtered_size,
        "holdings_count": len(portfolio.holdings),
        "industry_exposure": portfolio.industry_exposure,
        "layer_stats": portfolio.layer_stats,
        "concentration_hhi": portfolio.concentration_hhi,
        "holdings": portfolio_df.to_dict(orient="records"),
    }
    import json

    json_path.write_text(json.dumps(portfolio_json, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths.portfolio_json = str(json_path)
    logger.info(f"  ✅ 组合 JSON: {json_path}")

    # 3. 行业分布饼图
    industry_chart = _save_industry_pie(portfolio_df, charts_dir / "industry_distribution.png")
    if industry_chart:
        paths.chart_industry = industry_chart
        logger.info(f"  ✅ 行业分布图: {industry_chart}")

    # 4. 因子贡献柱状图
    factor_chart = _save_factor_contrib_bar(portfolio_df, charts_dir / "factor_contribution.png")
    if factor_chart:
        paths.chart_factor_contrib = factor_chart
        logger.info(f"  ✅ 因子贡献图: {factor_chart}")

    # 5. 风险雷达图
    radar_chart = _save_risk_radar(portfolio_df, charts_dir / "risk_radar.png")
    if radar_chart:
        paths.chart_risk_radar = radar_chart
        logger.info(f"  ✅ 风险雷达图: {radar_chart}")

    # 6. 打分分布直方图
    dist_chart = _save_score_dist(scores_df, charts_dir / "score_distribution.png")
    if dist_chart:
        paths.chart_score_dist = dist_chart
        logger.info(f"  ✅ 打分分布图: {dist_chart}")

    # 7. Markdown 汇总报告
    md_path = output_dir / "report.md"
    _generate_markdown_report(portfolio_df, portfolio, filter_stats, scores_df, md_path)
    paths.report_md = str(md_path)
    logger.info(f"  ✅ 汇总报告: {md_path}")

    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("report_generator ready")
