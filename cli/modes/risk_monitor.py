"""
风险监控模式
检查止损止盈状态 + 组合内相关性监控 (P0-7)
"""

from __future__ import annotations

import json
import os

from core.context import (
    BASE_DIR,
    ProgressIndicator,
    logger,
    stop_loss,
)
from utils.alert_notifier import AlertLevel, AlertNotifier
from utils.cli_helpers import (
    get_portfolio_quotes,
    get_stock_name,
    load_historical_returns_from_cache,
)
from utils.hedge_engine import HedgeEngine


def run_risk_monitor(args):
    """风险监控模式 - 检查止损止盈状态 + 相关性监控"""
    print("\n🛡️ 运行风险监控")
    print("=" * 70)

    progress = ProgressIndicator("风险监控", 5)
    alert_notifier = AlertNotifier()

    progress.update(1, "加载监控模块...")
    StopLossMonitor = stop_loss.get("StopLossMonitor")
    generate_risk_alert_report = stop_loss.get("generate_risk_alert_report")

    if StopLossMonitor and generate_risk_alert_report:
        progress.update(2, "创建监控实例...")
        monitor = StopLossMonitor()

        progress.update(3, "获取行情数据...")
        quotes = get_portfolio_quotes()

        if not quotes:
            print("\n⚠️ 使用模拟数据进行风险监控")
            quotes = {
                "600989": {"price": 23.50},
                "600276": {"price": 45.00},
                "300274": {"price": 165.00},
                "601088": {"price": 47.80},
                "002371": {"price": 520.00},
            }

        progress.update(4, "检查风险状态...")
        alerts = monitor.check_all(quotes)
        report = generate_risk_alert_report(alerts)
        print("\n" + report)

        # v5.10 P1-11: 风险告警通知
        if alerts:
            alert_notifier.quick_alert(
                title="风险监控告警",
                content=f"共检测到 {len(alerts)} 条风险告警，请查看详情",
                level=AlertLevel.WARNING,
                source="risk_monitor",
            )

        # v5.10 P0-7: 组合内相关性监控
        progress.update(5, "监控组合内相关性...")
        print("\n📊 组合内相关性监控 (P0-7)")
        print("-" * 70)

        try:
            # 加载持仓数据
            positions_path = os.path.join(BASE_DIR, "config", "positions.json")
            positions = {}
            if os.path.exists(positions_path):
                with open(positions_path, encoding="utf-8") as f:
                    pos_data = json.load(f)
                    for code, p in pos_data.get("positions", {}).items():
                        positions[code] = {
                            "shares": p.get("shares", 0),
                            "cost": p.get("cost", 0),
                        }

            if len(positions) < 2:
                print("  ⚠️ 持仓标的不足2个，无法计算相关性矩阵")
            else:
                # v5.10 P0-7修复: 从本地Parquet缓存加载真实历史收益率
                # (替代之前的模拟数据 [ret * (1 + i * 0.01)])
                historical_returns = load_historical_returns_from_cache(
                    codes=list(positions.keys()),
                    lookback_days=60,
                )

                if not historical_returns:
                    print(
                        "  ⚠️ 数据缓存中无足够K线数据（需至少20个交易日），"
                        "请先运行数据下载"
                    )
                    print("     python v5.10.py --check  # 确认缓存状态")

                if historical_returns and len(historical_returns) >= 2:
                    engine = HedgeEngine()
                    corr_result = engine.monitor_daily_correlation(
                        positions=positions,
                        historical_returns=historical_returns,
                        alert_threshold=0.7,
                        lookback_days=60,
                    )

                    print(
                        f"\n  滚动60日平均相关系数: {corr_result.get('average_correlation', 0):.4f}"
                    )
                    print(
                        f"  最高相关系数:         {corr_result.get('max_correlation', 0):.4f}"
                    )

                    high_pairs = corr_result.get("high_correlation_pairs", [])
                    if high_pairs:
                        print("\n  ⚠️  高相关标的对 (相关系数 > 0.7):")
                        for ci, cj, corr in high_pairs:
                            name_i = get_stock_name(ci)
                            name_j = get_stock_name(cj)
                            print(
                                f"    {ci} ({name_i}) <-> {cj} ({name_j}): {corr:.4f}"
                            )

                    if corr_result.get("alert"):
                        print(
                            f"\n  🚨 相关性预警: {corr_result.get('alert_reason', '')}"
                        )
                        print(
                            f"    风险评分: {corr_result.get('risk_score', 0):.2f}/1.0"
                        )
                        alert_notifier.quick_alert(
                            title="组合内相关性预警",
                            content=corr_result.get("alert_reason", ""),
                            level=AlertLevel.WARNING,
                            source="risk_monitor",
                        )
                    else:
                        print("\n  ✅ 组合内相关性正常，无共振风险")
                else:
                    print("  ⚠️ 历史收益率数据不足，跳过相关性监控")
        except Exception as e:
            print(f"  ❌ 相关性监控异常: {e}")
            logger.warning(f"相关性监控失败: {e}")

        progress.complete("✅ 风险监控完成")
    else:
        progress.complete("❌ 止损止盈监控模块不可用")
