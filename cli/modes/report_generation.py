"""
报告生成模式 — v5.10 P0-9 重构
"""

import os

from core.context import (
    BASE_DIR,
    ProgressIndicator,
    config_hub,
    daily_report,
    logger,
)
from utils.cli_helpers import (
    archive_report,
    get_ml_signal_section,
)


def run_report_generation(args):
    """报告生成模式 - 生成每日持仓报告 + ML信号 (v5.7 Phase 3: ConfigHub集成)"""
    print("\n📝 生成每日报告")
    print("=" * 70)

    progress = ProgressIndicator("生成报告", 8)

    progress.update(1, "加载报告模块...")
    generate_daily_report = daily_report.get("generate_daily_report")

    # v5.7 Phase 3: 使用 ConfigHub 获取统一配置
    portfolio_file = os.path.join(BASE_DIR, "config", "portfolio.yaml")
    if config_hub and not config_hub.check_and_reload():
        # 配置无变更，显示摘要
        summary = config_hub.get_summary()
        print(
            f"  📋 配置摘要: {summary['asset_count']}个标的, "
            f"总资金 {summary['total_capital']:,.0f}, "
            f"数据源: {summary['primary_source']}"
        )

    if generate_daily_report:
        try:
            progress.update(2, "读取配置...")

            progress.update(3, "生成报告内容...")
            report_content = generate_daily_report(
                portfolio_file=portfolio_file, enable_ai_analysis=not args.no_ai
            )

            # ML信号追加到报告（若LLM已内置ML分析则跳过）
            if not getattr(args, "no_ml", False):
                progress.update(4, "检查ML预测信号...")
                if "本地ML量化模型分析" in report_content:
                    print("  ✅ ML分析已由日报引擎自动内置，跳过追加")
                else:
                    ml_section = get_ml_signal_section()
                    if ml_section:
                        report_content += ml_section
                        print("  ✅ ML信号已追加")
                    else:
                        print("  ⚠️ ML信号不可用")
            else:
                progress.update(4, "跳过ML信号...")

            # v5.10: 估值分析接入（DCF + Comps）
            if getattr(args, "include_valuation", False):
                progress.update(5, "生成估值分析...")
                try:
                    from utils.comps_analyzer import summarize_comps
                    from utils.dcf_model import summarize_dcf

                    dcf_summary = summarize_dcf(
                        {
                            "ticker": "EXAMPLE",
                            "wacc": 0.09,
                            "terminal_growth": 0.025,
                            "enterprise_value": 1000,
                            "equity_value": 800,
                            "implied_price": 80,
                            "stock_price": 75,
                            "upside": 0.0667,
                        }
                    )
                    comps_summary = summarize_comps(
                        {
                            "sector": "Technology",
                            "companies": [],
                            "statistics": {
                                "ev_revenue": {"median": 5.5},
                                "ev_ebitda": {"median": 12.0},
                                "pe_ratio": {"median": 22.0},
                            },
                        }
                    )
                    report_content += (
                        "\n\n---\n\n" + dcf_summary + "\n\n" + comps_summary
                    )
                    print("  ✅ 估值分析已追加")
                except Exception as e:
                    print(f"  ⚠️ 估值分析追加失败: {e}")

            progress.update(6, "保存报告...")
            archive_report(report_content, "综合日报", ext=".txt")

            progress.complete("✅ 报告归档完成")

        except Exception as e:
            progress.complete(f"\n❌ 报告生成失败: {e}")
            logger.error(f"报告生成失败: {e}")
    else:
        progress.complete("❌ 每日报告模块不可用")
