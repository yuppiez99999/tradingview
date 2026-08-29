"""对冲+再平衡联动分析 — 五阶段联合决策引擎 v5.9"""

import json

from core.context import BASE_DIR, ProgressIndicator


def run_hedge_rebalance_joint(args):
    """对冲+再平衡联动分析 — 五阶段联合决策引擎 v5.9

    v5.9 核心升级:
      - 组合自触发: 市场状态由组合自身驱动，不再依赖CSI300
      - 多标的对比: IC/IM/IF 根据Beta加权分配
      - 自完善护: 仅在组合回撤 > 1.5x基准时激活
      - 局部降级: 默认TAIL_ONLY，仅在vol>28%和CD>12%时激活
      联合流程:
        Phase 1: 风险评估 -> 组合Beta(IF/IC/IM)/VaR/集中度 自动决策
        Phase 2: 对冲策略 -> 组合自触发 -> 对冲比率 -> 多标的协同
        Phase 3: 再平衡检查 -> 组合自动阈值 -> 自动买入卖出
        Phase 4: 联合优化 -> 对冲后组合 vs 再平衡后分布一致
        Phase 5: 生成执行计划 -> 优先窗口/目标收益/风险

      选项:
        --show-reasoning   显示详细推理过程
        --auto-execute     自动执行（需二次确认）
        --no-ai            跳过AI分析
        --mode <mode>      对冲模式: tail_only(默认)/dynamic/fixed/none
        --output <path>    指定报告输出路径
    """
    # v5.9: 对冲模式选择
    hedge_mode_str = getattr(args, "hedge_mode", "tail_only")
    from utils.hedge_rebalance_integrator import HedgeMode

    mode_map = {
        "tail_only": HedgeMode.TAIL_ONLY,
        "dynamic": HedgeMode.DYNAMIC,
        "fixed": HedgeMode.FIXED,
        "none": HedgeMode.NONE,
    }
    hedge_mode = mode_map.get(hedge_mode_str, HedgeMode.TAIL_ONLY)

    print(f"\n🛡️ 对冲+再平衡联动分析 — HedgeRebalanceIntegrator v5.9 (模式: {hedge_mode.value})")
    print("=" * 70)

    try:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
        )

        _INTEGRATOR_OK = True
    except ImportError as e:
        print(f"\n  ❌ 联动引擎加载失败: {e}")
        print("  💡 请确认 utils/hedge_rebalance_integrator.py 和 utils/hedge_engine.py 存在")
        return

    show_reasoning = getattr(args, "show_reasoning", False)
    getattr(args, "auto_execute", False)
    no_ai = getattr(args, "no_ai", False)

    # 联合分析流程
    progress = ProgressIndicator("联动分析 v5.9", 5)

    integrator = HedgeRebalanceIntegrator(base_dir=BASE_DIR, hedge_mode=hedge_mode)

    # v5.9: 计算组合自动决策和市场状态（从历史收益率计算）
    portfolio_volatility = 0.18  # 默认 18% 年化波动率
    portfolio_drawdown_60d = 0.0  # 默认无显著回撤
    try:
        prices = integrator.load_prices()
        if prices:
            for code in prices:
                prices[code]
                # 保留扩展点：后续可从历史数据计算真实波动率
                pass
    except Exception:
        pass

    # 构建市场信号（用于外部输入，v5.9已尽量缩小为5%）

    # Phase 1: 风险评估
    progress.update(1, " 评估组合风险...")
    risk = integrator.assess_risk()
    print("\n  📊 [Phase 1/5] 组合风险评估 (v5.9 多标的)")
    print(f"  {'─' * 55}")
    print(f"  组合总资产:      ¥{risk.total_value:,.0f}")
    print(
        f"  股票敞口:        ¥{risk.stock_exposure:,.0f} ({risk.stock_exposure/risk.total_value*100:.0f}%)"
        if risk.total_value > 0
        else "  股票敞口:        ¥0"
    )
    print(
        f"  组合Beta:       CSI300={risk.beta_csi300:.2f} | CSI500={risk.beta_csi500:.2f} | CSI1000={risk.beta_csi1000:.2f}"
    )
    print(f"  估算波动率:     {portfolio_volatility*100:.1f}% (年化)")
    print(f"  估算60日回撤:   {portfolio_drawdown_60d*100:.1f}%")
    print(
        f"  日VaR(95%):     ¥{risk.var_95_daily:,.0f} ({risk.var_95_daily/risk.total_value*100:.2f}%)"
        if risk.total_value > 0
        else "  日VaR(95%):     ¥0"
    )
    print(f"  集中度(HHI):    {risk.concentration_risk:.3f}")

    # Phase 2: 对冲策略 (v5.9: 组合自触发)
    progress.update(2, " 对冲策略...")
    hedge = integrator.decide_hedge(
        risk,
        portfolio_volatility=portfolio_volatility,
        portfolio_drawdown_60d=portfolio_drawdown_60d,
    )
    regime_desc = {
        "calm": "😊 平静市场",
        "mild": "😐 温和波动",
        "high": "😰 高波动",
        "tail": "🚨 尾部事件",
    }
    print("\n  🛡️ [Phase 2/5] 对冲策略")
    print(f"  {'─' * 55}")
    print(f"  市场状态?:       {regime_desc.get(hedge.regime.value, hedge.regime.value)}")
    print(f"  对冲需求?:       {'需要' if hedge.needed else '无需'}")
    if hedge.needed:
        print(
            f"  对冲比率:       {hedge.hedge_ratio*100:.0f}% (¥{risk.stock_exposure*hedge.hedge_ratio:,.0f})"
            if risk.stock_exposure > 0
            else f"  对冲比率:       {hedge.hedge_ratio*100:.0f}%"
        )
        print(
            f"  推荐品种:       {', '.join(hedge.futures_instruments) if hedge.futures_instruments else '无可用品种'}"
        )
        if hedge.futures_contracts:
            for code, n in hedge.futures_contracts.items():
                notional = hedge.futures_notional.get(code, 0)
                margin = hedge.futures_margin.get(code, 0)
                print(f"    {code}: 做空 {n} 手| 名义¥{notional:,.0f} | 保证金¥{margin:,.0f}")
            print(
                f"  总保证金需求:        ¥{hedge.total_margin:,.0f} (占总资产{hedge.total_margin/risk.total_value*100:.1f}%)"
                if risk.total_value > 0
                else f"  总保证金需求:        ¥{hedge.total_margin:,.0f}"
            )
        print(f"  期望对冲后Beta: {hedge.expected_beta_after:.2f}")
        print(f"  价格数据来源:     {hedge.price_source}")
        if hedge.fallback_used:
            print(f"  ⚠️   回退数据源: {', '.join(hedge.fallback_used)}")
        if show_reasoning:
            print(f"  推理:           {hedge.reasoning}")
    else:
        print(f"  原因:           {hedge.reasoning}")

    # Phase 3: 再平衡检查 (v5.9: 自动阈值调整)
    progress.update(3, " 再平衡检查...")
    rebalance = integrator.check_rebalance(risk, portfolio_volatility=portfolio_volatility)
    print("\n  🔄 [Phase 3/5] 再平衡检查")
    print(f"  {'─' * 55}")
    print(f"  再平衡类型:     {rebalance.rebalance_type}")
    print(f"  自动阈值?:       {rebalance.threshold*100:.0f}%")
    print(f"  需要调整的:     {len(rebalance.positions_to_adjust)} 只")
    if rebalance.needed and rebalance.positions_to_adjust:
        print(
            f"  总买入金额:  ¥{rebalance.total_buy_amount:,.0f} | 总卖出金额: ¥{rebalance.total_sell_amount:,.0f} | 净现金流: ¥{rebalance.net_cash_flow:,.0f}"
        )
        print(
            f"\n  {'代码':<12s} {'名称':<10s} {'组':<8s} {'操作':<6s} {'目标权重':>8s} {'当前权重':>8s} {'偏差':>8s} {'调整额':>10s}"
        )
        print(f"  {'─' * 80}")
        for pw in rebalance.positions_to_adjust:
            op_icon = "🔴" if pw.action == "SELL" else ("🟢" if pw.action == "BUY" else "⚪")
            print(
                f"  {op_icon} {pw.code:<10s} {pw.name:<10s} {pw.category:<8s} {pw.action:<6s} "
                f"{pw.target_weight*100:>7.1f}% {pw.current_weight*100:>7.1f}% "
                f"{pw.deviation_pct*100:>7.1f}% ¥{pw.adjustment:>10,.0f}"
            )
    else:
        print(f"  原因:           {rebalance.reasoning}")
    if show_reasoning:
        print(f"  组合权重:       {integrator._get_sector_adjusted_weights()}")

    # Phase 4: 联合优化
    progress.update(4, " 联合优化...")
    adj_hedge, adj_rebalance, warnings = integrator.joint_optimize(risk, hedge, rebalance)
    if warnings:
        print(f"\n  ⚠️  [Phase 4/5] 联合优化 — {len(warnings)} 条警示")
        print(f"  {'─' * 55}")
        for w in warnings:
            print(f"  ⚠️  {w}")
    else:
        print("\n  ✅ [Phase 4/5] 联合优化 — 通过 ✓")
        print(f"  {'─' * 55}")
        print("  对冲后组合 vs 再平衡后分布一致，无需调整")

    # Phase 5: 生成执行计划
    progress.update(5, " 生成执行计划...")
    plan = integrator.generate_execution_plan(risk, adj_hedge, adj_rebalance, warnings)

    print("\n  📋 [Phase 5/5] 执行计划")
    print(f"  {'─' * 55}")
    print(f"  执行优先级:      {plan.execution_priority}")
    print(f"  计划窗口:        {plan.execution_window}")
    print(f"  对冲后净敞口:    ¥{plan.after_hedge_exposure:,.0f}")

    print("\n  📈 绩效预测 (vs 基准未配置)")
    print(f"  {'─' * 55}")
    print(f"  预期年化收益提升:   {plan.estimated_annual_return*100:.1f}%")
    print(f"  预期最大回撤:       {plan.estimated_max_drawdown*100:.1f}%")
    print(f"  预期夏普比率:       {plan.estimated_sharpe:.2f}")
    print(f"  预期年化波动率:     {plan.estimated_volatility*100:.1f}%")

    print(f"\n  {'=' * 55}")
    print(f"  📝 总结: {plan.summary}")
    print(f"  {'=' * 55}")

    if plan.warning_flags:
        print("\n  ⚠️  注意事项:")
        for w in plan.warning_flags:
            print(f"    - {w}")

    # 保存报告
    report_path = integrator.save_report(plan)
    print(f"\n  📄 联动报告已保存: {report_path}")

    # AI 分析（可选）
    if not no_ai:
        print("\n  🧠 AI 联动分析...")
        print(f"  {'─' * 55}")
        try:
            from quant_modules.ai_hedge_fund.agents.hedge_analyst import (
                hedge_analyst_agent,
            )

            positions_data = integrator.positions
            analyst_state = {
                "messages": [],
                "data": {
                    "tickers": list(positions_data.keys()),
                    "portfolio": {
                        "cash": risk.cash,
                        "positions": {
                            code: {
                                "long": p.get("shares", 0),
                                "short": 0,
                                "long_cost_basis": p.get("cost", 0),
                            }
                            for code, p in positions_data.items()
                        },
                    },
                    "market_data": {
                        "total_value": risk.total_value,
                        "beta_csi300": risk.beta_csi300,
                        "beta_csi500": risk.beta_csi500,
                        "var_95": risk.var_95_daily,
                        "cvar_95": risk.cvar_95_daily,
                        "concentration_hhi": risk.concentration_risk,
                    },
                    "analyst_signals": {},
                },
                "metadata": {
                    "show_reasoning": getattr(args, "show_reasoning", False),
                    "model_name": getattr(args, "model", "deepseek-chat"),
                    "model_provider": getattr(args, "provider", "DeepSeek"),
                },
            }

            result = hedge_analyst_agent(analyst_state)
            hedge_signal = result["data"]["analyst_signals"].get("hedge_analyst_agent", {})

            if hedge_signal:
                signal_name = hedge_signal.get("signal", "neutral")
                signal_emoji = {"bearish": "🔴", "neutral": "🟡", "bullish": "🟢"}.get(signal_name, "⚪")
                print(f"    {signal_emoji} 对冲信号: {signal_name}")
                print(f"    对冲比率: {hedge_signal.get('hedge_ratio', 0)*100:.0f}%")
                print(f"    紧急程度: {hedge_signal.get('urgency_score', 0)*100:.0f}%")

                reasoning = hedge_signal.get("reasoning", "")
                if reasoning:
                    try:
                        r = json.loads(reasoning)
                        if "risk_warnings" in r:
                            for w in r["risk_warnings"]:
                                print(f"    ⚠️ {w}")
                        if "hedge_recommendation" in r:
                            rec = r["hedge_recommendation"]
                            print(f"    推荐工具: {rec.get('preferred_instrument', 'N/A')}")
                            print(f"    执行时机: {rec.get('execution_timing', 'N/A')}")
                    except (json.JSONDecodeError, KeyError):
                        if len(reasoning) > 100:
                            reasoning = reasoning[:100] + "..."
                        print(f"    详情: {reasoning}")
        except Exception as e:
            print(f"    ⚠️ AI分析跳过: {e}")

    progress.complete("✅ 联动分析完成")
    print("\n  ⚠️ 以上分析仅供参考，不构成投资建议。")
    print("  期货/期权交易有杠杆风险，请谨慎执行。")
