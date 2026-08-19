"""
康波周期+十五五交叠分析模式
周期阶段判定 + 行业轮动 + 大宗商品信号
"""

from __future__ import annotations

from core.context import (
    KONDRATIEV_AVAILABLE,
    KondratievCycleAnalyzer,
    ProgressIndicator,
)
from utils.cli_helpers import archive_report, write_report_file


def run_kondratiev_analysis(args):
    """康波周期+十五五交叠分析模式 — 周期阶段判定 + 行业轮动 + 大宗商品信号"""
    print("\n🌊 康波周期 + 十五五规划交叠分析")
    print("=" * 70)

    if not KONDRATIEV_AVAILABLE:
        print("❌ 康波周期分析模块不可用，请检查 utils/kondratiev_cycle.py")
        return None

    progress = ProgressIndicator("康波周期分析", 4)

    progress.update(1, "初始化康波周期分析器...")
    analyzer = KondratievCycleAnalyzer()

    progress.update(2, "判定当前周期阶段...")
    phase = analyzer.get_current_phase()
    print(f"\n  📍 当前阶段: {phase['phase_name_cn']} (进度: {phase['progress_pct']}%)")
    print(f"  📊 置信度: {phase['confidence']}")
    print(f"  🎯 推荐风格: {phase['recommended_style']}")
    print(f"  ⚠️ 风险等级: {phase['risk_level']}")

    progress.update(3, "生成行业配置+商品信号...")
    sectors = analyzer.get_sector_allocation()
    print("\n  📈 行业配置建议:")
    for s in sectors[:5]:
        print(f"    {s['sector']}: 综合得分={s['combined_score']}, 建议={s['recommendation']}")

    progress.update(4, "生成报告...")
    report = analyzer.generate_report()

    write_report_file(report, getattr(args, 'output', None))
    archive_report(report, '康波周期分析')

    progress.complete("✅ 康波周期分析完成")
    return analyzer
