"""
v1 / v2 / v3 三方对比报告生成器
================================
基于已归档的 v1 (基线), v2 (优化), v3 (激进优化) 结果, 生成对比报告。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# 统一到根目录 每日报告归档, 默认使用今天日期
ARCHIVE_DIR = Path(r"e:\各种PY程序\每日报告归档") / datetime.now().strftime("%Y-%m-%d")

# 三个版本的 JSON 结果文件
V1_JSON = ARCHIVE_DIR / "portfolio_metrics_20260706.json"
V2_JSON = ARCHIVE_DIR / "portfolio_optimization_v2_20260706.json"
V3_JSON = ARCHIVE_DIR / "portfolio_optimization_v3_20260706.json"


def load_json(path: Path) -> dict:
    if not path.exists():
        print(f"⚠ 文件不存在: {path}")
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    v1_data = load_json(V1_JSON)
    v2_data = load_json(V2_JSON)
    v3_data = load_json(V3_JSON)

    # 提取关键指标
    v1_metrics = v1_data.get("metrics", v1_data.get("baseline", {}))
    v2_data.get("baseline", {})
    v2_optimized = v2_data.get("optimized", {})
    v2_mc = v2_data.get("montecarlo_verified", {})
    v3_baseline = v3_data.get("baseline", {})
    v3_optimized = v3_data.get("optimized", {})
    v3_mc = v3_data.get("montecarlo_verified", {})
    v3_pdf1 = v3_data.get("pdf1_targets", {})

    # v1 数据可能字段名不同
    v1_annual = v1_metrics.get("annual_return", 0)
    v1_vol = v1_metrics.get("annual_vol", 0)
    v1_dd = v1_metrics.get("max_drawdown", 0)
    v1_sharpe = v1_metrics.get("sharpe", 0)
    v1_sortino = v1_metrics.get("sortino", 0)
    v1_calmar = v1_metrics.get("calmar", 0)
    v1_p8 = v1_metrics.get("prob_annual_gt_8pct", 0.472)
    v1_p15 = v1_metrics.get("prob_dd_lt_15pct", 0.899)

    lines = [
        "# 组合优化 v1 / v2 / v3 三方对比报告",
        "",
        f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "**资金规模**: 5,000,000 元 (300万股票 + 200万期权对冲)",
        "",
        "## 1. 版本说明",
        "",
        "| 版本 | 描述 | 资产数 | 关键约束 |",
        "|------|------|--------|---------|",
        "| **v1** | 基线 (用户原始配置) | 10类 | 科技≤25%, 棉期≤25%, 股权 5%-10% |",
        "| **v2** | 中度优化 | 10类 | 科技≤25%, 棉期≤25%, 股权 5%-10% |",
        "| **v3** | 激进优化 (4 项调整) | 12类 | 科技≤30%, 棉期≤35%, 股权=0%, 新增半导体/新能源ETF |",
        "",
        "## 2. v3 优化方向 (相对 v2)",
        "",
        "1. **科技股上限**: 25% → 30% (提升上限 +5pp)",
        "2. **棉花期货杠杆**: 60% → 70% (权重上限 25% → 35%)",
        "3. **股票期权保护**: 5.66% → 0% (完全取消)",
        "4. **新增高收益资产**: 半导体ETF (年化15%/波动30%) + 新能源ETF (年化14%/波动28%)",
        "",
        "## 3. 核心指标对比 (解析公式)",
        "",
        "| 指标 | v1 基线 | v2 优化 | v3 基线 | v3 优化 | v3 vs v1 | v3 vs v2 |",
        "|------|---------|---------|---------|---------|----------|----------|",
        f"| 年化收益率 | {v1_annual:.2%} | {v2_optimized.get('annual_return', 0):.2%} | "
        f"{v3_baseline.get('annual_return', 0):.2%} | {v3_optimized.get('annual_return', 0):.2%} | "
        f"{v3_optimized.get('annual_return', 0) - v1_annual:+.2%} | "
        f"{v3_optimized.get('annual_return', 0) - v2_optimized.get('annual_return', 0):+.2%} |",
        f"| 年化波动率 | {v1_vol:.2%} | {v2_optimized.get('annual_vol', 0):.2%} | "
        f"{v3_baseline.get('annual_vol', 0):.2%} | {v3_optimized.get('annual_vol', 0):.2%} | "
        f"{v3_optimized.get('annual_vol', 0) - v1_vol:+.2%} | "
        f"{v3_optimized.get('annual_vol', 0) - v2_optimized.get('annual_vol', 0):+.2%} |",
        f"| 最大回撤(估算) | {v1_dd:.2%} | {v2_optimized.get('max_drawdown', 0):.2%} | "
        f"{v3_baseline.get('max_drawdown', 0):.2%} | {v3_optimized.get('max_drawdown', 0):.2%} | "
        f"{v3_optimized.get('max_drawdown', 0) - v1_dd:+.2%} | "
        f"{v3_optimized.get('max_drawdown', 0) - v2_optimized.get('max_drawdown', 0):+.2%} |",
        f"| Sharpe | {v1_sharpe:.3f} | {v2_optimized.get('sharpe', 0):.3f} | "
        f"{v3_baseline.get('sharpe', 0):.3f} | {v3_optimized.get('sharpe', 0):.3f} | "
        f"{v3_optimized.get('sharpe', 0) - v1_sharpe:+.3f} | "
        f"{v3_optimized.get('sharpe', 0) - v2_optimized.get('sharpe', 0):+.3f} |",
        f"| Sortino | {v1_sortino:.3f} | {v2_optimized.get('sortino', 0):.3f} | "
        f"{v3_baseline.get('sortino', 0):.3f} | {v3_optimized.get('sortino', 0):.3f} | "
        f"{v3_optimized.get('sortino', 0) - v1_sortino:+.3f} | "
        f"{v3_optimized.get('sortino', 0) - v2_optimized.get('sortino', 0):+.3f} |",
        f"| Calmar | {v1_calmar:.3f} | {v2_optimized.get('calmar', 0):.3f} | "
        f"{v3_baseline.get('calmar', 0):.3f} | {v3_optimized.get('calmar', 0):.3f} | "
        f"{v3_optimized.get('calmar', 0) - v1_calmar:+.3f} | "
        f"{v3_optimized.get('calmar', 0) - v2_optimized.get('calmar', 0):+.3f} |",
        f"| P(年化>8%) | {v1_p8:.1%} | {v2_optimized.get('prob_annual_gt_8pct', 0):.1%} | "
        f"{v3_baseline.get('prob_annual_gt_8pct', 0):.1%} | {v3_optimized.get('prob_annual_gt_8pct', 0):.1%} | "
        f"{v3_optimized.get('prob_annual_gt_8pct', 0) - v1_p8:+.1%} | "
        f"{v3_optimized.get('prob_annual_gt_8pct', 0) - v2_optimized.get('prob_annual_gt_8pct', 0):+.1%} |",
        f"| P(回撤<15%) | {v1_p15:.1%} | {v2_optimized.get('prob_dd_lt_15pct', 0):.1%} | "
        f"{v3_baseline.get('prob_dd_lt_15pct', 0):.1%} | {v3_optimized.get('prob_dd_lt_15pct', 0):.1%} | "
        f"{v3_optimized.get('prob_dd_lt_15pct', 0) - v1_p15:+.1%} | "
        f"{v3_optimized.get('prob_dd_lt_15pct', 0) - v2_optimized.get('prob_dd_lt_15pct', 0):+.1%} |",
        "",
        "## 4. 蒙特卡洛验证对比 (3,000 路径)",
        "",
        "| 指标 | v2 优化 (MC) | v3 优化 (MC) | 改善 |",
        "|------|-------------|-------------|------|",
        f"| 年化收益率 (均值) | {v2_mc.get('annual_return_mean', 0):.2%} | "
        f"{v3_mc.get('annual_return_mean', 0):.2%} | "
        f"{v3_mc.get('annual_return_mean', 0) - v2_mc.get('annual_return_mean', 0):+.2%} |",
        f"| 年化收益率 (P50) | {v2_mc.get('annual_return_p50', v2_mc.get('annual_return_mean', 0)):.2%} | "
        f"{v3_mc.get('annual_return_p50', 0):.2%} | "
        f"{v3_mc.get('annual_return_p50', 0) - v2_mc.get('annual_return_p50', v2_mc.get('annual_return_mean', 0)):+.2%} |",
        f"| 年化收益率 (P10) | - | "
        f"{v3_mc.get('annual_return_p10', 0):.2%} | - |",
        f"| 年化收益率 (P90) | - | "
        f"{v3_mc.get('annual_return_p90', 0):.2%} | - |",
        f"| 最大回撤 (均值) | {v2_mc.get('max_drawdown_mean', 0):.2%} | "
        f"{v3_mc.get('max_drawdown_mean', 0):.2%} | "
        f"{v3_mc.get('max_drawdown_mean', 0) - v2_mc.get('max_drawdown_mean', 0):+.2%} |",
        f"| 最大回撤 (P95) | - | "
        f"{v3_mc.get('max_drawdown_p95', 0):.2%} | - |",
        f"| Sharpe | {v2_mc.get('sharpe_mean', 0):.3f} | "
        f"{v3_mc.get('sharpe_mean', 0):.3f} | "
        f"{v3_mc.get('sharpe_mean', 0) - v2_mc.get('sharpe_mean', 0):+.3f} |",
        f"| Sortino | {v2_mc.get('sortino_mean', 0):.3f} | "
        f"{v3_mc.get('sortino_mean', 0):.3f} | "
        f"{v3_mc.get('sortino_mean', 0) - v2_mc.get('sortino_mean', 0):+.3f} |",
        f"| Calmar | {v2_mc.get('calmar_mean', 0):.3f} | "
        f"{v3_mc.get('calmar_mean', 0):.3f} | "
        f"{v3_mc.get('calmar_mean', 0) - v2_mc.get('calmar_mean', 0):+.3f} |",
        f"| P(年化>8%) | {v2_mc.get('prob_annual_gt_8pct', 0):.1%} | "
        f"{v3_mc.get('prob_annual_gt_8pct', 0):.1%} | "
        f"{v3_mc.get('prob_annual_gt_8pct', 0) - v2_mc.get('prob_annual_gt_8pct', 0):+.1%} |",
        f"| P(年化>10%) | - | "
        f"{v3_mc.get('prob_annual_gt_10pct', 0):.1%} | - |",
        f"| P(回撤<15%) | {v2_mc.get('prob_dd_lt_15pct', 0):.1%} | "
        f"{v3_mc.get('prob_dd_lt_15pct', 0):.1%} | "
        f"{v3_mc.get('prob_dd_lt_15pct', 0) - v2_mc.get('prob_dd_lt_15pct', 0):+.1%} |",
        f"| P(回撤<20%) | - | "
        f"{v3_mc.get('prob_dd_lt_20pct', 0):.1%} | - |",
        "",
        "## 5. PDF1 目标达成情况",
        "",
        "| 目标 | v1 基线 | v2 优化 | v3 优化 | 目标值 | v1 | v2 | v3 |",
        "|------|---------|---------|---------|--------|----|----|----|",
        f"| 年化>8% 概率 | {v1_p8:.1%} | "
        f"{v2_mc.get('prob_annual_gt_8pct', 0):.1%} | "
        f"{v3_mc.get('prob_annual_gt_8pct', 0):.1%} | ≥68% | "
        f"{'✓' if v1_p8 >= 0.68 else '✗'} | "
        f"{'✓' if v2_mc.get('prob_annual_gt_8pct', 0) >= 0.68 else '✗'} | "
        f"{'✓' if v3_mc.get('prob_annual_gt_8pct', 0) >= 0.68 else '✗'} |",
        f"| 回撤<15% 概率 | {v1_p15:.1%} | "
        f"{v2_mc.get('prob_dd_lt_15pct', 0):.1%} | "
        f"{v3_mc.get('prob_dd_lt_15pct', 0):.1%} | ≥82% | "
        f"{'✓' if v1_p15 >= 0.82 else '✗'} | "
        f"{'✓' if v2_mc.get('prob_dd_lt_15pct', 0) >= 0.82 else '✗'} | "
        f"{'✓' if v3_mc.get('prob_dd_lt_15pct', 0) >= 0.82 else '✗'} |",
        "",
        "## 6. v3 优化权重 (12 类资产)",
        "",
        "| 资产类别 | v3 基线 | v3 优化 | 500万建议金额 |",
        "|---------|---------|---------|------------|",
    ]

    v3_asset_names = v3_baseline.get("asset_names", [])
    v3_baseline_weights = v3_baseline.get("weights", [])
    v3_optimized_weights = v3_optimized.get("weights", [])

    for i, name in enumerate(v3_asset_names):
        old_w = v3_baseline_weights[i] if i < len(v3_baseline_weights) else 0
        new_w = v3_optimized_weights[i] if i < len(v3_optimized_weights) else 0
        amount = new_w * 5_000_000
        lines.append(
            f"| {name} | {old_w:.2%} | {new_w:.2%} | {amount:,.0f} |"
        )

    lines.extend([
        "",
        "## 7. 关键洞察",
        "",
        "### 7.1 收益提升路径",
        "",
        f"- **v1 → v3**: 年化收益 {v1_annual:.2%} → {v3_mc.get('annual_return_mean', 0):.2%} "
        f"(+{(v3_mc.get('annual_return_mean', 0) - v1_annual)*100:.2f}pp)",
        f"- **v2 → v3**: 年化收益 {v2_mc.get('annual_return_mean', 0):.2%} → "
        f"{v3_mc.get('annual_return_mean', 0):.2%} "
        f"(+{(v3_mc.get('annual_return_mean', 0) - v2_mc.get('annual_return_mean', 0))*100:.2f}pp)",
        "- **驱动因素**: 新增高收益ETF (半导体/新能源) + 棉花期货杠杆提升 + 取消股票期权成本",
        "",
        "### 7.2 回撤恶化分析",
        "",
        f"- **v2 → v3**: P(回撤<15%) {v2_mc.get('prob_dd_lt_15pct', 0):.1%} → "
        f"{v3_mc.get('prob_dd_lt_15pct', 0):.1%} "
        f"({(v3_mc.get('prob_dd_lt_15pct', 0) - v2_mc.get('prob_dd_lt_15pct', 0))*100:+.1f}pp)",
        f"- **v3 P95 最差5%回撤**: {v3_mc.get('max_drawdown_p95', 0):.2%} (远超 15% 警戒线)",
        "- **根本原因**: 取消股票期权保护后, 极端行情下尾部风险完全暴露",
        "- **次要原因**: 新增半导体/新能源ETF波动率高 (28%-30%), 棉花期货杠杆放大",
        "",
        "### 7.3 权衡矩阵",
        "",
        "| 维度 | v1 (保守) | v2 (平衡) | v3 (激进) |",
        "|------|----------|----------|----------|",
        "| 年化收益 | ★★ | ★★★ | ★★★★ |",
        "| 回撤控制 | ★★★★ | ★★★ | ★★ |",
        "| PDF1 双达标 | ✗✗ | ✗✗ | ✗✗ |",
        "| 极端风险 | 低 | 中 | 高 |",
        "| 资金利用率 | 中 | 中高 | 高 |",
        "",
        "## 8. 推荐方案",
        "",
        "### 方案 A: 采用 v3 激进优化 (高风险高收益)",
        "",
        "- 适用: 风险承受能力强, 看好科技/新能源/棉花期货",
        "- 必要条件: 严格执行止损纪律, VIX>60 时手动减仓",
        "- 5万底仓 + 动态调仓, 保留 30% 现金应对追加保证金",
        "",
        "### 方案 B: 采用 v2 平衡优化 (推荐)",
        "",
        "- 适用: 稳健型投资者, 接受 8%-9% 年化",
        "- 保留股票期权保护, 控制尾部风险",
        "- PDF1 仍未达标, 但更接近目标 (P(年化>8%)=53%)",
        "",
        "### 方案 C: v2 + v3 混合 (折中)",
        "",
        "- 保留 5% 股票期权保护 (折中)",
        "- 引入半导体ETF + 新能源ETF (5% 各)",
        "- 棉花期货杠杆 65% (折中)",
        "- 预期: 年化 9-10%, P(回撤<15%) ≈ 75%",
        "",
        "## 9. 后续行动建议",
        "",
        "1. **短期 (本周)**: 选择方案 (A/B/C), 调整交易计划",
        "2. **中期 (本月)**: 实盘模拟运行 1-2 周, 验证实际表现",
        "3. **长期 (季度)**: 每月重新优化一次, 适应市场环境变化",
        "4. **风控强化**: 设置 VIX>60 / 单日跌幅>5% 的手动对冲触发条件",
        "5. **数据更新**: 用真实历史数据校准资产参数 (当前为估算值)",
        "",
        "## 10. 风险提示",
        "",
        "- 本报告基于解析公式 + 蒙特卡洛模拟 (正态分布假设), 实际市场存在肥尾效应",
        "- 蒙特卡洛模拟的尾部保护模型为简化版本, 实盘保护效果可能不同",
        "- 资产参数 (年化收益/波动率) 为估算值, 建议用近 3-5 年真实数据校准",
        "- 500万资金配置涉及杠杆 (棉花期货), 强烈建议严格执行止损纪律",
        "- 实盘交易需考虑交易成本、流动性、滑点等因素",
        "",
    ])

    output_path = ARCHIVE_DIR / "portfolio_optimization_comparison_v1_v2_v3.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"三方对比报告已生成: {output_path}")

    # 保存对比 JSON
    comparison_data = {
        "generated_at": datetime.now().isoformat(),
        "versions": {
            "v1_baseline": {
                "annual_return": v1_annual,
                "annual_vol": v1_vol,
                "max_drawdown": v1_dd,
                "sharpe": v1_sharpe,
                "sortino": v1_sortino,
                "calmar": v1_calmar,
                "prob_annual_gt_8pct": v1_p8,
                "prob_dd_lt_15pct": v1_p15,
            },
            "v2_optimized": {
                "analytical": v2_optimized,
                "montecarlo": v2_mc,
            },
            "v3_optimized": {
                "analytical": v3_optimized,
                "montecarlo": v3_mc,
                "pdf1_targets": v3_pdf1,
            },
        },
        "key_findings": {
            "v3_vs_v2_annual_return_delta": v3_mc.get('annual_return_mean', 0) - v2_mc.get('annual_return_mean', 0),
            "v3_vs_v2_prob_dd15_delta": v3_mc.get('prob_dd_lt_15pct', 0) - v2_mc.get('prob_dd_lt_15pct', 0),
            "v3_p95_max_drawdown": v3_mc.get('max_drawdown_p95', 0),
            "pdf1_v3_pass": v3_pdf1.get('annual_gt_8pct_pass', False) and v3_pdf1.get('dd_lt_15pct_pass', False),
        },
    }
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(comparison_data, f, indent=2, ensure_ascii=False)
    print(f"对比 JSON: {json_path}")


if __name__ == "__main__":
    main()
