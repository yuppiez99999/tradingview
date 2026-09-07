"""AI 盘中实时决策模式"""

from datetime import datetime

from core.context import get_ai_coordinator, logger


def run_ai_decision(args):
    """AI盘中实时决策模式 v5.9 - 多模型场景路由 + Wind MCP 动态数据"""
    print("\n🤖 AI 盘中实时决策模式 v5.9")
    print("=" * 70)
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"决策场景: {'盘中并行对冲' if getattr(args, 'scene', 'intraday_decision') == 'intraday_decision' else '再平衡交叉验证'}"  # noqa: E501
    )
    print(f"Wind MCP: {'启用' if not getattr(args, 'no_wind', False) else '禁用'}")
    print("-" * 70)

    try:
        from utils.intraday_decision import IntradayDecisionMonitor

        # v5.9: 从 args 获取场景参数
        scene = getattr(args, "scene", "intraday_decision")
        use_wind = not getattr(args, "no_wind", False)

        # 创建监控器 (v5.9: 场景路由 + Wind MCP)
        # (doubao 已 2026-09-07 出局; 本 CLI 依赖的 IntradayDecisionMonitor 已不存在, 属死入口)
        monitor = IntradayDecisionMonitor(
            api_model="mlx_qwen3_8b",  # 向后兼容
            check_interval=getattr(args, "interval", 300),
            enable_notifications=True,
            scene=scene,
            use_wind_mcp=use_wind,
        )

        # 加载持仓
        if not monitor.load_positions():
            print("❌ 持仓数据加载失败,请检查 config/positions.json")
            return

        print(f"✅ 已加载 {len(monitor.positions)} 只持仓")

        # 生成决策
        model_info = {
            "intraday_decision": "MLX Qwen3-8B (本地盘中决策)",
            "rebalancing_analysis": "DeepSeek V4 Pro + GLM-5.3 (交叉验证)",
        }
        print("\n📊 正在调用 AI 生成交易决策...")
        print(f"   场景路由: {model_info.get(scene, '默认')}")
        print("   (这需要10-30秒,请耐心等待)")
        print("-" * 70)

        decision = monitor.generate_decision()

        if not decision:
            print("❌ 决策生成失败")
            return

        # 显示结果
        print("\n" + "=" * 70)
        print("📈 决策结果")
        print("=" * 70)

        print("\n📋 市场概况:")
        print(f"   {decision.market_summary}")

        print(f"\n📊 交易信号: {len(decision.trading_signals)} 条")
        if decision.trading_signals:
            # v5.7 Phase 2: 记录AI决策到统一数据库
            try:
                coordinator = get_ai_coordinator()
            except Exception as e:
                logger.debug(f"获取AI协调器失败: {e}")
                coordinator = None
            for sig in decision.trading_signals:
                action_map = {
                    "BUY": "买入",
                    "SELL": "卖出",
                    "HOLD": "持有",
                    "REDUCE": "减仓",
                }
                action_cn = action_map.get(sig.action, sig.action)
                print(f"   [{action_cn}] {sig.code} {sig.name}")
                print(f"      理由: {sig.reason}")
                print(f"      置信度: {sig.confidence:.2f}, 紧急程度: {sig.urgency}")
                if hasattr(sig, "key_factors") and sig.key_factors:
                    print(f"      关键因子: {', '.join(sig.key_factors)}")
                if hasattr(sig, "risk_considerations") and sig.risk_considerations:
                    print(f"      风险考量: {sig.risk_considerations}")
                # 记录到AI协调器数据库 (v5.9: 添加模型路由信息)
                if coordinator:
                    try:
                        coordinator.record_decision(
                            source="model_router",
                            ticker=sig.code,
                            action=sig.action,
                            confidence=sig.confidence,
                            reasoning=sig.reason,
                            model_used=getattr(sig, "model_used", scene),
                            task_type=scene,
                        )
                    except Exception as e:
                        logger.debug(f"记录AI决策失败 {sig.code}: {e}")
        else:
            print("   暂无交易信号 - 当前持仓无需调整")

        print(f"\n⚠️  风险预警: {len(decision.risk_alerts)} 条")
        if decision.risk_alerts:
            for alert in decision.risk_alerts:
                icon = {
                    "CRITICAL": "🚨",
                    "HIGH": "⚠️",
                    "MEDIUM": "⚡",
                    "LOW": "ℹ️",
                }.get(alert.severity, "•")
                print(f"   {icon} [{alert.severity}] {alert.message}")
        else:
            print("   暂无风险预警")

        if decision.portfolio_advice:
            print("\n💡 组合调整建议:")
            print(f"   {decision.portfolio_advice}")

        if decision.macro_outlook:
            print("\n🔮 宏观展望:")
            print(f"   {decision.macro_outlook}")

        print(f"\n📈 AI置信度: {decision.ai_confidence:.2%}")

        # 导出报告
        report_path = monitor.export_report(decision)
        if report_path:
            print(f"\n✅ 决策报告已保存: {report_path}")

        print("\n" + "=" * 70)
        print("AI决策完成 - 请人工审核后再执行交易")
        print("=" * 70)

    except ImportError:
        print("❌ AI决策模块未安装")
        print(
            "   请确保 utils/glm5_decision_engine.py, utils/multi_model_router.py 和 utils/wind_data_provider.py 存在"
        )
    except Exception as e:
        print(f"❌ AI决策执行失败: {e}")
        import traceback

        traceback.print_exc()
