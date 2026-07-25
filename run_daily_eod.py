#!/usr/bin/env python3
"""每日 EOD 风控守卫执行脚本 (v8.6.1)

以世界顶级对冲基金视角, 每日收盘后强制执行四 Guard 风控链:
    Guard 1: 保证金熔断 (KillSwitch)
    Guard 2: 回撤检查 (DrawdownController)
    Guard 3: 波动率控制 (VolTargetController)
    Guard 4: 对冲执行 + 认沽保护 (HedgeExecutionEngine + ProtectivePutEngine)

用法:
    python run_daily_eod.py [--date YYYY-MM-DD] [--dry-run]

输出:
    1. 更新次日 trade_plan (含 risk_guard 结果)
    2. EOD 摘要报告 → 每日报告归档/{date}/eod_guard_report_{date}.md
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional"))

TRADE_PLANS_DIR = PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
REPORTS_DIR = PROJECT_ROOT / "每日报告归档"


def get_next_trading_day(current_date: str) -> str:
    """计算下一交易日 (跳过周末)

    Args:
        current_date: 当前日期 YYYY-MM-DD

    Returns:
        下一交易日 YYYY-MM-DD
    """
    dt = datetime.strptime(current_date, "%Y-%m-%d")
    next_dt = dt + timedelta(days=1)
    # 跳过周六(5)和周日(6)
    while next_dt.weekday() >= 5:
        next_dt += timedelta(days=1)
    return next_dt.strftime("%Y-%m-%d")


def run_eod_guards(report_date: str, dry_run: bool = False) -> dict:
    """执行 EOD 四 Guard 风控链

    Args:
        report_date: 报告日期 YYYY-MM-DD (当日)
        dry_run: 若为 True, 仅输出日志不保存文件

    Returns:
        执行结果字典 {success, guards, next_trade_date, errors}
    """
    next_trade_date = get_next_trading_day(report_date)
    print(f"[EOD] 报告日: {report_date} → 次交易日: {next_trade_date}")

    # 加载次日 trade_plan
    plan_file = TRADE_PLANS_DIR / f"trade_plan_{next_trade_date.replace('-', '')}.json"
    if not plan_file.exists():
        # 尝试带横杠的文件名格式
        plan_file = TRADE_PLANS_DIR / f"trade_plan_{next_trade_date}.json"
    if not plan_file.exists():
        print(f"[EOD][ERROR] 次日交易计划不存在: {plan_file}")
        return {
            "success": False,
            "error": f"trade_plan not found: {plan_file}",
            "next_trade_date": next_trade_date,
        }

    print(f"[EOD] 加载交易计划: {plan_file.name}")
    with open(plan_file, "r", encoding="utf-8") as f:
        plan = json.load(f)

    # 执行四 Guard 链
    guards_result = {}
    errors = []

    try:
        from utils.risk_guard_integrator import RiskGuardIntegrator

        print("[EOD] 初始化 RiskGuardIntegrator...")
        integrator = RiskGuardIntegrator(report_date=report_date)

        print("[EOD] 执行四 Guard 风控链...")
        updated_plan = integrator.run_all_guards(next_trade_date)

        # 提取 Guard 结果
        risk_guard = updated_plan.get("risk_guard", {})
        guards_result = {
            "kill_switch": risk_guard.get("kill_switch", {}),
            "drawdown": risk_guard.get("drawdown", {}),
            "vol_target": risk_guard.get("vol_target", {}),
            "hedge_execution": risk_guard.get("hedge_execution", {}),
            "protective_put": risk_guard.get("protective_put", {}),
        }

        # 检查 Guard 是否通过
        for guard_name, guard_data in guards_result.items():
            if isinstance(guard_data, dict):
                passed = guard_data.get("passed", guard_data.get("build_allowed", True))
                if not passed:
                    msg = f"[{guard_name}] Guard 未通过: {guard_data}"
                    errors.append(msg)
                    print(f"[EOD][WARN] {msg}")

        # 保存更新后的 trade_plan
        if not dry_run:
            with open(plan_file, "w", encoding="utf-8") as f:
                json.dump(updated_plan, f, ensure_ascii=False, indent=2)
            print(f"[EOD] 已更新交易计划: {plan_file.name}")
        else:
            print("[EOD] dry-run 模式, 不保存交易计划")

        # 生成 EOD 摘要报告
        report_path = generate_eod_report(
            report_date, next_trade_date, guards_result, errors, dry_run
        )

        return {
            "success": len(errors) == 0,
            "guards": guards_result,
            "next_trade_date": next_trade_date,
            "errors": errors,
            "report_path": str(report_path) if report_path else None,
        }

    except ImportError as e:
        error_msg = f"RiskGuardIntegrator 导入失败: {e}"
        print(f"[EOD][CRITICAL] {error_msg}")
        errors.append(error_msg)
        return {"success": False, "error": error_msg, "errors": errors}
    except Exception as e:
        error_msg = f"四 Guard 链执行异常: {e}"
        print(f"[EOD][CRITICAL] {error_msg}")
        import traceback

        traceback.print_exc()
        errors.append(error_msg)
        return {"success": False, "error": error_msg, "errors": errors}


def generate_eod_report(
    report_date: str,
    next_trade_date: str,
    guards: dict,
    errors: list,
    dry_run: bool,
) -> Path:
    """生成 EOD 摘要报告

    Args:
        report_date: 报告日期
        next_trade_date: 次交易日
        guards: Guard 执行结果
        errors: 错误列表
        dry_run: 是否 dry-run 模式

    Returns:
        报告文件路径 (dry-run 时返回 None)
    """
    lines = [
        f"# EOD 风控守卫报告 — {report_date}",
        "",
        f"**报告日期**: {report_date}",
        f"**次交易日**: {next_trade_date}",
        f"**执行时间**: {datetime.now().isoformat()}",
        f"**模式**: {'dry-run' if dry_run else 'production'}",
        "",
        "## 四 Guard 执行结果",
        "",
    ]

    # Guard 摘要表
    lines.append("| Guard | 状态 | 关键指标 |")
    lines.append("|-------|------|----------|")

    guard_names = {
        "kill_switch": "保证金熔断",
        "drawdown": "回撤检查",
        "vol_target": "波动率控制",
        "hedge_execution": "对冲执行",
        "protective_put": "认沽保护",
    }

    all_passed = True
    for key, name in guard_names.items():
        data = guards.get(key, {})
        if isinstance(data, dict):
            passed = data.get("passed", data.get("build_allowed", True))
            status = "✅ 通过" if passed else "❌ 未通过"
            if not passed:
                all_passed = False
            # 提取关键指标
            metric = ""
            if key == "kill_switch":
                metric = f"L{data.get('level', 0)}"
            elif key == "drawdown":
                metric = f"Level {data.get('level', 0)}"
            elif key == "vol_target":
                metric = f"vol_scale={data.get('vol_scale', 1.0):.2f}"
            elif key == "hedge_execution":
                metric = f"hedge_pct={data.get('hedge_pct', 0):.1%}"
            elif key == "protective_put":
                metric = f"orders={data.get('put_orders', [])}"
        else:
            status = "⚠️ 无数据"
            metric = "-"
        lines.append(f"| {name} | {status} | {metric} |")

    lines.append("")
    if all_passed and not errors:
        lines.append("## 总结: ✅ 全部 Guard 通过, 次日可正常交易")
    else:
        lines.append("## 总结: ❌ 存在 Guard 未通过或异常, 请检查")
        if errors:
            lines.append("")
            lines.append("### 错误详情")
            for err in errors:
                lines.append(f"- {err}")

    lines.append("")
    lines.append("---")
    lines.append("*本报告由 run_daily_eod.py v8.6.1 自动生成*")

    if dry_run:
        print("\n" + "\n".join(lines))
        return None

    # 写入报告文件
    report_dir = REPORTS_DIR / report_date
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"eod_guard_report_{report_date}.md"

    # 写入重试机制 (与 daily_workflow 一致)
    success = False
    for attempt in range(3):
        try:
            report_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"[EOD] 报告已生成: {report_path}")
            success = True
            break
        except PermissionError:
            if attempt < 2:
                import time

                print(f"[EOD][WARN] 报告写入权限拒绝, 重试 {attempt + 1}/3...")
                time.sleep(1)
            else:
                print(f"[EOD][ERROR] 报告写入失败 ({attempt + 1}/3): 权限拒绝")

    if not success:
        # Fallback: 写入 logs 目录
        fallback_dir = PROJECT_ROOT / "logs"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback_path = fallback_dir / f"eod_guard_report_{report_date}.md"
        try:
            fallback_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"[EOD][WARN] 报告写入 fallback: {fallback_path}")
            report_path = fallback_path
        except Exception as e:
            print(f"[EOD][ERROR] Fallback 写入也失败: {e}")
            return None

    return report_path


def main():
    """主入口"""
    parser = argparse.ArgumentParser(description="EOD 风控守卫执行脚本 (v8.6.1)")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="报告日期 YYYY-MM-DD (默认今天)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="dry-run 模式, 仅输出日志不保存文件",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"EOD 风控守卫执行 — {args.date}")
    print("=" * 60)

    result = run_eod_guards(args.date, dry_run=args.dry_run)

    print("\n" + "=" * 60)
    if result.get("success"):
        print("[EOD] ✅ 四 Guard 链执行完成, 全部通过")
    else:
        print("[EOD] ❌ 四 Guard 链存在未通过项或异常")
        if result.get("errors"):
            for err in result["errors"]:
                print(f"  - {err}")
    print("=" * 60)

    # 退出码: 成功=0, 失败=1
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
