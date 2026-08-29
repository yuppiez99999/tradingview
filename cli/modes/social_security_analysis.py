"""
社保基金ETF风格追踪模式 — v5.10 P0-9 重构
"""

from core.context import (
    SOCIAL_SECURITY_ETF_AVAILABLE,
    ProgressIndicator,
    SocialSecurityETFTracker,
)
from utils.cli_helpers import archive_report, get_etf_flow_data, write_report_file


def run_social_security_analysis(args):
    """社保基金ETF风格追踪模式 — 风格分类 + 国家队信号 + 配置建议"""
    print("\n🏦 社保基金ETF风格追踪")
    print("=" * 70)

    if not SOCIAL_SECURITY_ETF_AVAILABLE:
        print("❌ 社保基金ETF追踪模块不可用，请检查 utils/social_security_etf.py")
        return None

    progress = ProgressIndicator("社保基金ETF追踪", 4)

    progress.update(1, "初始化社保ETF追踪器...")
    tracker = SocialSecurityETFTracker()

    progress.update(2, "分析社保基金投资风格...")
    summary = tracker.classifier.get_style_summary()
    print("\n  📊 社保基金四大风格配置:")
    for style, info in summary.items():
        print(
            f"    {style}: 权重={info['weight']:.0%}, 建议={info['recommended_action']}"
        )
        print(f"      ETF: {', '.join(info['top_etfs'][:2])}")

    progress.update(3, "获取ETF风格映射...")
    etf_classifications = tracker.classifier.get_all_etf_classifications()
    print("\n  🔗 ETF风格映射 (前10):")
    for etf in etf_classifications[:10]:
        print(
            f"    {etf['name']} → {etf['social_style']} (匹配度={etf['match_score']})"
        )

    progress.update(4, "生成报告...")
    flow_data = get_etf_flow_data()
    if flow_data:
        print(f"\n  💰 已获取 {len(flow_data)} 只ETF资金流数据")

    report = tracker.generate_report(flow_data=flow_data)

    write_report_file(report, args.output)
    archive_report(report, "社保基金ETF追踪")

    progress.complete("✅ 社保基金ETF追踪完成")
    return tracker
