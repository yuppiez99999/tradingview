"""
十五五规划适配分析模式 — v5.10 P0-9 重构
"""

from core.context import (
    FIFTEEN_FIVE_AVAILABLE,
    FifteenFivePlanAnalyzer,
    ProgressIndicator,
)
from utils.cli_helpers import archive_report, write_report_file


def run_fifteen_five_analysis(args):
    """十五五规划适配分析模式 — 持仓对标 + 政策对齐度评分 + 权重调整建议"""
    print("\n🏛️ 十五五规划适配分析")
    print("=" * 70)

    if not FIFTEEN_FIVE_AVAILABLE:
        print("❌ 十五五规划分析模块不可用，请检查 utils/five_year_plan.py")
        return None

    progress = ProgressIndicator("十五五规划分析", 4)

    progress.update(1, "初始化十五五分析器...")
    analyzer = FifteenFivePlanAnalyzer()

    progress.update(2, "分析持仓适配度...")
    overview = analyzer.get_policy_overview()
    print("\n  📋 十五五规划七大战略方向:")
    for o in overview:
        print(
            f"    {o['direction']}: 权重={o['weight']:.0%}, 优先级={o['relevance_score']}"
        )

    progress.update(3, "生成权重调整建议...")
    holdings = analyzer.analyze_holdings()
    adjustments = analyzer.get_weight_adjustments()
    print("\n  📊 持仓适配评级:")
    for h in holdings:
        print(f"    {h['name']}: 评分={h['overall_score']}, 等级={h['grade']}")
    print("\n  ⚖️ 权重调整建议:")
    for adj in adjustments:
        direction = "+" if adj["weight_adjust_pct"] > 0 else ""
        print(
            f"    {adj['name']}: {adj['suggestion']} ({direction}{adj['weight_adjust_pct']:.1f}%)"
        )

    progress.update(4, "生成报告...")
    report = analyzer.generate_report()

    write_report_file(report, args.output)
    archive_report(report, "十五五规划适配")

    progress.complete("✅ 十五五规划分析完成")
    return analyzer
