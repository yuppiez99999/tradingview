"""验证 P0-D / P0-E / P0-F 修复是否生效 (2026-07-26 v8.6.5)

P0-D: EOD Guard KillSwitch 检查失效 (risk_guard_integrator.py margin_used=null 不回退)
P0-E: hedge_execution_engine.py 'str' object has no attribute 'get'
P0-F: Windows 任务从未运行 (改为 SYSTEM + HIGHEST)
"""

import sys
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def verify_p0_d():
    """验证 P0-D: EOD Guard KillSwitch 检查"""
    print("\n" + "=" * 60)
    print("验证 P0-D: EOD Guard KillSwitch 检查 (margin_used=null)")
    print("=" * 60)

    # 模拟 2026-07-24 daily_pnl_report 实际状态: margin_used=null, total_equity=null
    broken_pnl_report = {
        "portfolio_pnl": {
            "summary": {
                "margin_used": None,  # 字段存在但值为 None (原始 bug 触发条件)
                "total_equity": None,
                "positions": {},
            }
        }
    }

    from utils.kill_switch import KillSwitch
    from utils.risk_guard_integrator import RiskGuardIntegrator

    # 1. 验证 KillSwitch._estimate_margin_from_positions() 真实工作
    ks = KillSwitch()
    real_ratio = ks._estimate_margin_from_positions()
    print(
        f"[验证] KillSwitch._estimate_margin_from_positions() = {real_ratio:.4f} ({real_ratio:.1%})"
    )

    # 2. 调用 guard_kill_switch (传入有问题的 pnl_report)
    rgi = RiskGuardIntegrator(total_capital=5_000_000)
    plan = {
        "trade_date": "2026-07-27",
        "execution_plan": {
            "morning_orders": [{"direction": "BUY", "code": "588080"}],
            "afternoon_orders": [{"direction": "BUY", "code": "159915"}],
        },
    }
    result = rgi.guard_kill_switch(broken_pnl_report, plan)

    # 3. 检查输出
    ks_field = result.get("risk_guard", {}).get("kill_switch", {})
    level = ks_field.get("level")
    margin_usage = ks_field.get("margin_usage", 0)
    can_trade = ks_field.get("can_trade")
    can_open = ks_field.get("can_open")

    print("[验证] EOD Guard kill_switch 字段:")
    print(f"  level:         {level}")
    print(f"  margin_usage:  {margin_usage}")
    print(f"  can_trade:     {can_trade}")
    print(f"  can_open:      {can_open}")

    # 4. 判定
    if margin_usage >= 0.70:
        print(
            f"[PASS] ✅ P0-D 修复生效: margin_usage={margin_usage} >= 0.70 (基于真实持仓)"
        )
        return True
    print(f"[FAIL] ❌ P0-D 修复未生效: margin_usage={margin_usage} < 0.70")
    return False


def verify_p0_e():
    """验证 P0-E: 对冲执行引擎"""
    print("\n" + "=" * 60)
    print("验证 P0-E: hedge_execution_engine generate_hedge_orders")
    print("=" * 60)

    from utils.hedge_execution_engine import HedgeExecutionEngine

    engine = HedgeExecutionEngine()

    try:
        result = engine.generate_hedge_orders(drawdown_level=0)
        print("[PASS] ✅ P0-E 修复生效: generate_hedge_orders() 执行成功")

        futures_orders = result.get("futures_orders", [])
        options_orders = result.get("options_orders", [])
        portfolio_status = result.get("portfolio_status", {})
        cost_summary = result.get("cost_summary", {})

        print(f"  futures_orders:  {len(futures_orders)} 个")
        print(f"  options_orders:  {len(options_orders)} 个")
        print(f"  portfolio_value: ¥{portfolio_status.get('market_value', 0):,.0f}")
        print(f"  portfolio_beta:  {portfolio_status.get('portfolio_beta_before', 0)}")
        print(
            f"  total_margin:    ¥{cost_summary.get('total_margin_required', 0):,.0f}"
        )
        print(f"  total_premium:   ¥{cost_summary.get('total_premium_budget', 0):,.0f}")

        # 列出 options_orders (应该有 ETF Put 等)
        for o in options_orders:
            inst = o.get("instrument", "?")
            contracts = o.get("contracts", 0)
            budget = o.get("premium_budget", 0)
            print(f"    - {inst}: {contracts} 张, 预算 ¥{budget:,.0f}")

        return True

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        import traceback

        print(f"[FAIL] ❌ P0-E 修复未生效: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def verify_p0_f():
    """验证 P0-F: Windows 任务计划 (只读检查, 不修改)"""
    print("\n" + "=" * 60)
    print("验证 P0-F: Windows 任务计划程序 (SYSTEM + HIGHEST)")
    print("=" * 60)

    import subprocess

    tasks = [
        "QuantPipelineFactor_06AM",
        "QuantWorkflow_07AM",
        "QuantMorning_0930",
        "QuantAfternoon_1400",
    ]

    all_pass = True
    for task_name in tasks:
        try:
            result = subprocess.run(
                ["schtasks", "/query", "/tn", task_name, "/fo", "list", "/v"],
                capture_output=True,
                text=True,
                encoding="gbk",
                errors="replace",
            )
            output = result.stdout

            # 提取关键字段
            run_as_user = ""
            logon_mode = ""
            status = ""
            next_run = ""
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("Run As User:"):
                    run_as_user = line.split(":", 1)[1].strip()
                elif line.startswith("Logon Mode:"):
                    logon_mode = line.split(":", 1)[1].strip()
                elif line.startswith("Status:"):
                    status = line.split(":", 1)[1].strip()
                elif line.startswith("Next Run Time:"):
                    next_run = line.split(":", 1)[1].strip()

            # 判定
            is_system = run_as_user.upper() == "SYSTEM"
            is_background = (
                "Background" in logon_mode or "Interactive/Background" in logon_mode
            )
            is_ready = status == "Ready"

            mark = "✅" if (is_system and is_background and is_ready) else "❌"
            print(f"{mark} {task_name}")
            print(f"     Run As User: {run_as_user}")
            print(f"     Logon Mode:  {logon_mode}")
            print(f"     Status:      {status}")
            print(f"     Next Run:    {next_run}")

            if not (is_system and is_background and is_ready):
                all_pass = False

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"❌ {task_name}: 查询失败 - {e}")
            all_pass = False

    if all_pass:
        print("[PASS] ✅ P0-F 修复生效: 4 个任务全部以 SYSTEM + Background 模式运行")
    else:
        print("[FAIL] ❌ P0-F 修复未完全生效")
    return all_pass


def main():
    print("v8.6.5 P0 修复验证脚本")
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Python: {sys.version}")

    results = {}
    results["P0-D"] = verify_p0_d()
    results["P0-E"] = verify_p0_e()
    results["P0-F"] = verify_p0_f()

    print("\n" + "=" * 60)
    print("验证汇总")
    print("=" * 60)
    for k, v in results.items():
        mark = "✅ PASS" if v else "❌ FAIL"
        print(f"  {k}: {mark}")

    all_pass = all(results.values())
    if all_pass:
        print("\n[全部通过] 3 个 P0 级 bug 修复全部生效, 符合项目硬约束")
        return 0
    print("\n[部分失败] 需要进一步排查")
    return 1


if __name__ == "__main__":
    sys.exit(main())
