"""每日三阶段交易工作流 - 盘前计划/盘中策略/盘后报告"""

from core.context import BASE_DIR


def run_daily_workflow(args):
    """每日三阶段交易工作流 - 盘前计划/盘中策略/盘后报告"""
    print("\n📅 每日交易工作流")
    print("=" * 70)

    # 直接调用 daily_trading_workflow.py 模块
    try:
        import daily_trading_workflow as dtw
    except ImportError as e:
        print(f"\n❌ 无法导入 daily_trading_workflow 模块: {e}")
        print("💡 请确保 daily_trading_workflow.py 存在于当前目录")
        return

    # 执行指定阶段
    phase = getattr(args, "phase", "all")
    if phase is None:
        phase = "all"

    print(f"\n🎯 执行阶段: {phase}")
    print("-" * 70)

    try:
        # 阶段注册表：函数名 / 完成提示
        PHASE_MAP = {
            "premarket": ("run_premarket", "盘前计划生成完成"),
            "intraday": ("run_intraday", "盘中策略扫描完成"),
            "postmarket": ("run_postmarket", "盘后报告生成完成"),
        }

        if phase in PHASE_MAP:
            func_name, success_msg = PHASE_MAP[phase]
            func = getattr(dtw, func_name, None)
            if func:
                func()
                print(f"\n✅ {success_msg}")
            else:
                print(f"\n❌ {func_name} 函数不存在")

        elif phase == "all":
            # 全流程执行
            print("\n🚀 开始全流程执行...")

            if hasattr(dtw, "run_all"):
                dtw.run_all()
            else:
                # 手动串联三个阶段
                for i, (func_name, success_msg) in enumerate(PHASE_MAP.values(), 1):
                    print(f"\n[{i}/{len(PHASE_MAP)}] {success_msg[:4]}")
                    func = getattr(dtw, func_name, None)
                    if func:
                        func()

            # ── v5.9: 盘后联动分析 (对冲+再平衡) ──
            print("\n  🔗 盘后联动分析 (对冲+再平衡 v5.9)...")
            print("  " + "-" * 60)
            try:
                from utils.hedge_rebalance_integrator import (
                    HedgeMode,
                    HedgeRebalanceIntegrator,
                )

                integrator = HedgeRebalanceIntegrator(
                    base_dir=BASE_DIR, hedge_mode=HedgeMode.TAIL_ONLY
                )
                plan = integrator.run_full_workflow()
                report_path = integrator.save_report(plan)
                print(
                    f"  ✅ 联动分析完成 v5.9 | 模式: {integrator.hedge_mode.value} | 优先级: {plan.execution_priority} | 窗口: {plan.execution_window}"  # noqa: E501
                )
                print(f"  📄 报告: {report_path}")
                print(
                    f"  📊 预估: 年化{plan.estimated_annual_return*100:.1f}% | 最大回撤{plan.estimated_max_drawdown*100:.1f}% | 夏普{plan.estimated_sharpe:.2f}"  # noqa: E501
                )
            except Exception as e:
                print(f"  ⚠️ 联动分析跳过: {e}")

            print("\n✅ 全流程执行完成")

        else:
            print(f"\n❌ 未知阶段: {phase}")
            print("💡 可用阶段: premarket, intraday, postmarket, all")

    except Exception as e:
        print(f"\n❌ 工作流执行失败: {e}")
        import traceback

        traceback.print_exc()
