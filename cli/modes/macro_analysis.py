"""
宏观综合分析模式
一键运行康波周期 + 十五五规划 + 社保基金ETF三大分析
"""

from __future__ import annotations

from core.context import (
    BASE_DIR,
    FIFTEEN_FIVE_AVAILABLE,
    KONDRATIEV_AVAILABLE,
    SOCIAL_SECURITY_ETF_AVAILABLE,
    FifteenFivePlanAnalyzer,
    KondratievCycleAnalyzer,
    SocialSecurityETFTracker,
)
from utils.cli_helpers import archive_report, get_archive_dir
from utils.datetime_utils import now_bj


def run_macro_analysis(args):
    """宏观综合分析 — 一键运行康波周期 + 十五五规划 + 社保基金ETF三大分析"""
    print("\n🔬 宏观综合分析（康波周期 + 十五五规划 + 社保基金ETF）")
    print("=" * 70)
    print(f"启动时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 70)

    results = {}
    archive_dir = get_archive_dir(BASE_DIR)

    # 1. 康波周期分析
    if KONDRATIEV_AVAILABLE:
        print("\n" + "=" * 50)
        print("  第一部分：康波周期 + 十五五规划交叠分析")
        print("=" * 50)
        try:
            kondratiev = KondratievCycleAnalyzer()
            phase = kondratiev.get_current_phase()
            print(f"\n  📍 第六轮康波（AI/算力驱动）当前阶段: {phase['phase_name_cn']}")
            print(
                f"  📊 阶段进度: {phase['progress_pct']}% | 置信度: {phase['confidence']}"
            )
            print(
                f"  🎯 推荐风格: {phase['recommended_style']} | 风险等级: {phase['risk_level']}"
            )
            print(f"  🔄 预计转入下一阶段: {phase.get('estimated_transition', 'N/A')}")

            # 行业配置
            sectors = kondratiev.get_sector_allocation()
            print("\n  📈 康波周期行业配置建议:")
            for s in sectors:
                print(
                    f"    {s['sector']}: 综合得分={s['combined_score']} → {s['recommendation']}"
                )

            # 大宗商品信号
            commodities = kondratiev.get_commodity_signals()
            print("\n  🛢️ 大宗商品周期信号:")
            for c in commodities:
                print(
                    f"    {c['name']}: 信号={c['current_signal']}, 康波建议={c.get('kondratiev_recommendation', 'N/A')}"
                )

            # 十五五交叠
            overlay = kondratiev.get_fifteen_five_overlay()
            print("\n  🔗 十五五与康波交叠结论:")
            print(f"    {overlay.get('synergy_conclusion', 'N/A')[:100]}...")

            report = kondratiev.generate_report()
            archive_report(report, "康波周期分析")
            print("\n  ✅ 康波周期报告已归档")
            results["kondratiev"] = True
        except Exception as e:
            print(f"\n  ❌ 康波周期分析失败: {e}")
            results["kondratiev"] = False
    else:
        print("\n  ⚠️ 康波周期模块不可用，跳过")
        results["kondratiev"] = None

    # 2. 十五五规划分析
    if FIFTEEN_FIVE_AVAILABLE:
        print("\n" + "=" * 50)
        print("  第二部分：十五五规划适配分析")
        print("=" * 50)
        try:
            fifteen_five = FifteenFivePlanAnalyzer()
            holdings = fifteen_five.analyze_holdings()
            adjustments = fifteen_five.get_weight_adjustments()

            print("\n  📊 持仓十五五适配评级:")
            for h in holdings:
                flag = (
                    "🟢"
                    if h["overall_score"] >= 85
                    else "🟡" if h["overall_score"] >= 70 else "🔴"
                )
                print(
                    f"    {flag} {h['name']}: 评分={h['overall_score']}, 等级={h['grade']}"
                )

            print("\n  ⚖️ 十五五驱动的权重调整建议:")
            for adj in adjustments:
                if adj["weight_adjust_pct"] != 0:
                    direction = "▲" if adj["weight_adjust_pct"] > 0 else "▼"
                    print(
                        f"    {direction} {adj['name']}: {adj['suggestion']} ({adj['weight_adjust_pct']:+.1f}%)"
                    )

            report = fifteen_five.generate_report()
            archive_report(report, "十五五规划适配")
            print("\n  ✅ 十五五规划报告已归档")
            results["fifteen_five"] = True
        except Exception as e:
            print(f"\n  ❌ 十五五规划分析失败: {e}")
            results["fifteen_five"] = False
    else:
        print("\n  ⚠️ 十五五规划模块不可用，跳过")
        results["fifteen_five"] = None

    # 3. 社保基金ETF追踪
    if SOCIAL_SECURITY_ETF_AVAILABLE:
        print("\n" + "=" * 50)
        print("  第三部分：社保基金ETF风格追踪")
        print("=" * 50)
        try:
            ss_tracker = SocialSecurityETFTracker()
            summary = ss_tracker.classifier.get_style_summary()

            print("\n  📊 社保基金四大投资风格:")
            for style, info in summary.items():
                icon = (
                    "📈"
                    if info["recommended_action"] == "超配"
                    else "📊" if info["recommended_action"] == "标配" else "📉"
                )
                print(
                    f"    {icon} {style} ({info['weight']:.0%}): {info['recommended_action']}"
                )
                print(f"       代表ETF: {', '.join(info['top_etfs'][:2])}")

            from utils.cli_helpers import get_portfolio_quotes

            flow_data = get_portfolio_quotes()

            report = ss_tracker.generate_report(flow_data=flow_data)
            archive_report(report, "社保基金ETF追踪")
            print("\n  ✅ 社保基金ETF报告已归档")
            results["social_security"] = True
        except Exception as e:
            print(f"\n  ❌ 社保基金ETF追踪失败: {e}")
            results["social_security"] = False
    else:
        print("\n  ⚠️ 社保基金ETF追踪模块不可用，跳过")
        results["social_security"] = None

    # 汇总
    print("\n" + "=" * 70)
    success_count = sum(1 for v in results.values() if v is True)
    total_count = sum(1 for v in results.values() if v is not None)
    print(f"🔬 宏观综合分析完成: {success_count}/{total_count} 模块成功")
    print(f"📁 报告归档目录: {archive_dir}")
    print("=" * 70)

    return results
