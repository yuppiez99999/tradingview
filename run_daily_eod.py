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
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional"))

TRADE_PLANS_DIR = PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
REPORTS_DIR = PROJECT_ROOT / "每日报告归档"

# Guard key → 中文展示名映射 (与 risk_guard 字典 key 对齐, 单一来源)
GUARD_NAMES: dict = {
    "kill_switch": "保证金熔断",
    "drawdown": "回撤检查",
    "vol_target": "波动率控制",
    "hedge_execution": "对冲执行",
    "protective_put": "认沽保护",
}


def _extract_guard_passed(guard_data) -> Optional[bool]:
    """[已废弃] 旧版字段提取器, 仅用于向后兼容

    新代码请使用 _evaluate_guard_passed(guard_key, data).
    保留此函数以兼容可能的外部调用 (如 tests/ 中可能直接调用).

    Args:
        guard_data: Guard 结果字典

    Returns:
        Optional[bool]: True/False/None(None 表示字段缺失)
    """
    if not isinstance(guard_data, dict):
        return None
    if "passed" in guard_data:
        return bool(guard_data["passed"])
    if "build_allowed" in guard_data:
        return bool(guard_data["build_allowed"])
    return None


def _extract_guard_data(guard_key: str, risk_guard: dict, plan: dict) -> Optional[dict]:
    """从 trade_plan 提取 Guard 数据 (适配 RiskGuardIntegrator 实际输出结构)

    RiskGuardIntegrator 各 Guard 的字段位置不一致 (历史设计):
      - kill_switch:     risk_guard["kill_switch"] (嵌套字典)
      - drawdown:        risk_guard["drawdown_level"] + drawdown_action (标量)
      - vol_target:      risk_guard["vol_scale"] + vol_action (标量)
      - hedge_execution: plan["hedge_execution"] (顶层) + risk_guard["hedge_action"] (标量)
      - protective_put:  risk_guard["put_action"] (标量) + plan["put_protection_orders"]

    本函数将各 Guard 的字段归一化为嵌套 dict 结构, 供 _evaluate_guard_passed 统一消费.

    Args:
        guard_key: GUARD_NAMES 中的 key
        risk_guard: trade_plan["risk_guard"] 字典
        plan: 完整的 trade_plan 字典

    Returns:
        Guard 数据字典 (归一化后); None 表示字段完全缺失
    """
    if not isinstance(risk_guard, dict):
        return None

    if guard_key == "kill_switch":
        ks = risk_guard.get("kill_switch")
        return ks if isinstance(ks, dict) else None

    if guard_key == "drawdown":
        level = risk_guard.get("drawdown_level")
        action = risk_guard.get("drawdown_action")
        if level is None and action is None:
            return None
        return {"level": level, "action": action, "note": risk_guard.get("drawdown_note")}

    if guard_key == "vol_target":
        scale = risk_guard.get("vol_scale")
        action = risk_guard.get("vol_action")
        if scale is None and action is None:
            return None
        return {
            "vol_scale": scale,
            "vol_action": action,
            "vol_note": risk_guard.get("vol_note"),
            "executed_summary": risk_guard.get("vol_scale_executed_summary"),
        }

    if guard_key == "hedge_execution":
        # hedge_execution 的详细信息在 plan 顶层 (非 risk_guard 嵌套)
        hedge_exec = plan.get("hedge_execution") if isinstance(plan, dict) else None
        hedge_action = risk_guard.get("hedge_action")
        if not isinstance(hedge_exec, dict) and hedge_action is None:
            return None
        return {
            "execution_status": hedge_exec.get("execution_status") if isinstance(hedge_exec, dict) else None,
            "hedge_action": hedge_action,
            "cost_summary": hedge_exec.get("cost_summary") if isinstance(hedge_exec, dict) else None,
            "total_orders": hedge_exec.get("total_orders") if isinstance(hedge_exec, dict) else None,
        }

    if guard_key == "protective_put":
        action = risk_guard.get("put_action")
        put_orders = plan.get("put_protection_orders", []) if isinstance(plan, dict) else []
        if action is None and not put_orders:
            return None
        return {"put_action": action, "put_orders": put_orders}

    return None


def _evaluate_guard_passed(guard_key: str, data: Optional[dict]) -> Optional[bool]:
    """评估 Guard 是否通过 (Fail-Safe: 字段缺失返回 None)

    每个 Guard 的通过判断逻辑基于 RiskGuardIntegrator 实际输出字段:

    - kill_switch:     level ∈ {0, 1, "OK", "L1"} 且 can_trade is True; L2/L3 视为未通过
    - drawdown:         drawdown_level <= 1 (Level 0=正常, 1=预警仍可交易)
    - vol_target:       vol_scale >= 0.80 或 vol_action="NORMAL" 或已响应 SCALE_DOWN (非 ERROR)
    - hedge_execution:  execution_status in ("PENDING", "COMPLETED") 或 hedge_action 含 GENERATED/NO_CHANGE_NEEDED
    - protective_put:   put_action 不含 "ERROR" (BELOW_THRESHOLD/EXISTING_PROTECTION_OK/GENERATED_X_PUTS 均通过)

    Args:
        guard_key: GUARD_NAMES 中的 key
        data: _extract_guard_data 返回的 Guard 数据

    Returns:
        True=通过, False=未通过, None=字段缺失 (视为异常)
    """
    if not isinstance(data, dict):
        return None

    # 向后兼容: 如果 Guard 已输出 passed/build_allowed 字段, 直接使用
    if "passed" in data:
        return bool(data["passed"])
    if "build_allowed" in data:
        return bool(data["build_allowed"])

    if guard_key == "kill_switch":
        can_trade = data.get("can_trade")
        level = data.get("level")
        if can_trade is None and level is None:
            return None
        # L2/L3 视为未通过 (禁止开仓/强制平仓)
        try:
            if isinstance(level, str):
                level_str = level.upper().replace("L", "")
                if level_str in ("2", "3"):
                    return False
                if level_str in ("0", "OK", "1"):
                    return bool(can_trade) if can_trade is not None else True
            elif isinstance(level, int):
                if level >= 2:
                    return False
                return bool(can_trade) if can_trade is not None else True
        except (ValueError, TypeError):
            pass
        # 未预期的 level 值（如 L4 / CRITICAL / 畸形字符串）采用 fail-safe 保守策略，禁止交易
        return False

    if guard_key == "drawdown":
        level = data.get("level")
        if level is None:
            return None
        try:
            return int(level) <= 1  # Level 0/1 通过, Level 2/3/4 失败
        except (ValueError, TypeError):
            return None

    if guard_key == "vol_target":
        vol_scale = data.get("vol_scale")
        vol_action = data.get("vol_action")
        if vol_scale is None and vol_action is None:
            return None
        if "ERROR" in str(vol_action or ""):
            return False
        if vol_action == "NORMAL":
            return True
        # vol_scale < 0.80 但已响应 SCALE_DOWN → 仍算通过 (执行了保护动作)
        if vol_scale is not None:
            try:
                return float(vol_scale) >= 0.80 or str(vol_action or "").startswith("SCALE_DOWN")
            except (ValueError, TypeError):
                return None
        return True

    if guard_key == "hedge_execution":
        execution_status = data.get("execution_status")
        hedge_action = data.get("hedge_action")
        if execution_status is None and hedge_action is None:
            return None
        if "ERROR" in str(hedge_action or ""):
            return False
        if execution_status == "CANCELLED":
            return False
        if execution_status in ("PENDING", "COMPLETED"):
            return True
        if hedge_action and ("GENERATED" in str(hedge_action) or "NO_CHANGE_NEEDED" in str(hedge_action)):
            return True
        return None

    if guard_key == "protective_put":
        put_action = data.get("put_action")
        if put_action is None:
            return None
        if "ERROR" in str(put_action):
            return False
        # BELOW_THRESHOLD / EXISTING_PROTECTION_OK / GENERATED_X_PUTS 都算通过
        return True

    return None


def _format_guard_metric(guard_key: str, data: dict) -> str:
    """格式化 Guard 关键指标用于报告表格 (None 防御, 避免格式化崩溃)

    适配 RiskGuardIntegrator 实际输出字段 (而非旧版期望的嵌套字段).

    Args:
        guard_key: GUARD_NAMES 中的 key
        data: _extract_guard_data 返回的归一化 Guard 数据

    Returns:
        指标字符串 (无数据时返回 "-")
    """
    if not isinstance(data, dict):
        return "-"
    if guard_key == "kill_switch":
        level = data.get("level", "?")
        margin = data.get("margin_usage")
        if isinstance(margin, (int, float)):
            return f"L{level}, margin={margin:.1%}"
        return f"L{level}"
    if guard_key == "drawdown":
        level = data.get("level", "?")
        action = data.get("action", "-")
        return f"Level {level}, {action}"
    if guard_key == "vol_target":
        vol_scale = data.get("vol_scale")
        vol_action = data.get("vol_action", "-")
        if vol_scale is None:
            return f"{vol_action}"
        try:
            return f"scale={float(vol_scale):.2f}, {vol_action}"
        except (ValueError, TypeError):
            return f"scale={vol_scale}, {vol_action}"
    if guard_key == "hedge_execution":
        status = data.get("execution_status", "-")
        action = data.get("hedge_action", "-")
        orders = data.get("total_orders", "?")
        return f"status={status}, orders={orders}, {action}"
    if guard_key == "protective_put":
        action = data.get("put_action", "-")
        orders = data.get("put_orders", [])
        if isinstance(orders, list):
            return f"{action}, puts={len(orders)}"
        return f"{action}, puts=?"
    return "-"


def _write_report_with_retry(lines: list, report_path: Path, max_retries: int = 3) -> Optional[Path]:
    """写入报告文件, 带重试 + fallback 机制

    主路径重试 max_retries 次 (间隔 1s); 全部失败后 fallback 写入
    PROJECT_ROOT/logs 目录. 确保报告内容总能落盘.

    Args:
        lines: 报告行列表
        report_path: 目标路径
        max_retries: 最大重试次数 (默认 3)

    Returns:
        成功写入的 Path (可能为 fallback 路径); 失败返回 None
    """
    content = "\n".join(lines)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    # 主路径重试
    for attempt in range(max_retries):
        try:
            report_path.write_text(content, encoding="utf-8")
            print(f"[EOD] 报告已生成: {report_path}")
            return report_path
        except (PermissionError, OSError, UnicodeEncodeError) as e:
            if attempt < max_retries - 1:
                print(f"[EOD][WARN] 报告写入失败 ({type(e).__name__}: {e}), 重试 {attempt + 1}/{max_retries}...")
                time.sleep(1)
            else:
                print(f"[EOD][ERROR] 报告写入失败 ({max_retries}/{max_retries}): {type(e).__name__}: {e}")

    # Fallback: 写入 logs 目录
    fallback_dir = PROJECT_ROOT / "logs"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    fallback_path = fallback_dir / report_path.name
    try:
        fallback_path.write_text(content, encoding="utf-8")
        print(f"[EOD][WARN] 报告写入 fallback: {fallback_path}")
        return fallback_path
    except (PermissionError, OSError, UnicodeEncodeError) as e:
        print(f"[EOD][ERROR] Fallback 写入也失败: {e}")
        return None


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

    print(f"[EOD] 定位交易计划: {plan_file.name}")
    # trade_plan 由 RiskGuardIntegrator.run_all_guards() 内部 _load_next_trade_plan 加载,
    # 此处仅校验文件存在, 避免重复读取 (旧代码 _plan 加载后从未使用)

    # 执行四 Guard 链
    guards_result = {}
    errors = []

    try:
        from utils.risk_guard_integrator import RiskGuardIntegrator

        print("[EOD] 初始化 RiskGuardIntegrator...")
        integrator = RiskGuardIntegrator(report_date=report_date)

        print("[EOD] 执行四 Guard 风控链...")
        updated_plan = integrator.run_all_guards(next_trade_date)

        # M2 修复: None 检查, 防止 integrator 返回 None 时后续 .get() 崩溃
        if updated_plan is None:
            error_msg = "RiskGuardIntegrator.run_all_guards() 返回 None, 风控链异常"
            print(f"[EOD][CRITICAL] {error_msg}")
            errors.append(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "next_trade_date": next_trade_date,
                "errors": errors,
                "guards": {},
            }

        # 提取 Guard 结果并评估通过状态
        # RiskGuardIntegrator 各 Guard 字段位置不一致:
        #   - kill_switch: risk_guard.kill_switch (嵌套字典)
        #   - drawdown:    risk_guard.drawdown_level + drawdown_action (标量)
        #   - vol_target:  risk_guard.vol_scale + vol_action (标量)
        #   - hedge_execution: plan.hedge_execution (顶层) + risk_guard.hedge_action (标量)
        #   - protective_put:  risk_guard.put_action (标量) + plan.put_protection_orders
        # _extract_guard_data 将各 Guard 字段归一化为嵌套 dict 结构供统一消费.
        risk_guard = updated_plan.get("risk_guard", {}) or {}
        guards_result = {}
        for guard_key in GUARD_NAMES:
            guard_data = _extract_guard_data(guard_key, risk_guard, updated_plan)
            guards_result[guard_key] = guard_data
            passed = _evaluate_guard_passed(guard_key, guard_data)
            if passed is None:
                msg = f"[{guard_key}] Guard 数据缺失或字段不完整, 视为未通过"
                errors.append(msg)
                print(f"[EOD][ERROR] {msg}")
                continue
            if not passed:
                msg = f"[{guard_key}] Guard 未通过: {guard_data}"
                errors.append(msg)
                print(f"[EOD][WARN] {msg}")

        # 保存更新后的 trade_plan (C2 修复: 备份 + 原子写入, 防止数据丢失)
        if not dry_run:
            # 写入前备份原文件
            if plan_file.exists():
                backup_path = plan_file.with_suffix(f".bak_{datetime.now().strftime('%H%M%S')}")
                try:
                    shutil.copy2(plan_file, backup_path)
                    print(f"[EOD] 已备份原交易计划: {backup_path.name}")
                except (OSError, IOError) as e:
                    print(f"[EOD][WARN] 备份失败, 继续写入: {e}")

            # 原子写入: 先写 .tmp 再 replace, 防止中途异常导致文件损坏
            tmp_path = plan_file.with_suffix(".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(updated_plan, f, ensure_ascii=False, indent=2)
                tmp_path.replace(plan_file)
                print(f"[EOD] 已更新交易计划: {plan_file.name}")
            except (OSError, IOError, ValueError) as e:
                print(f"[EOD][ERROR] 交易计划写入失败: {e}")
                # 清理临时文件
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:  # best-effort 清理, 失败不影响主流程
                        pass
                errors.append(f"trade_plan 写入失败: {e}")
        else:
            print("[EOD] dry-run 模式, 不保存交易计划")

        # 生成 EOD 摘要报告
        report_path = generate_eod_report(report_date, next_trade_date, guards_result, errors, dry_run)

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
        # 兜底: 生产风控脚本必须永不崩溃, 永远返回结构化结果给上层调度 (cron/Airflow)
        # integrator 内部各 guard 已各自 try-except, 此处捕获的是加载/数据访问/未知异常
        error_msg = f"四 Guard 链执行异常: {type(e).__name__}: {e}"
        print(f"[EOD][CRITICAL] {error_msg}")
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

    all_passed = True
    for key, name in GUARD_NAMES.items():
        data = guards.get(key)
        passed = _evaluate_guard_passed(key, data)
        if passed is None:
            # 字段缺失, 视为异常 (Fail-Safe)
            all_passed = False
            lines.append(f"| {name} | ⚠️ 字段缺失 | 数据不完整 |")
            continue
        status = "✅ 通过" if passed else "❌ 未通过"
        if not passed:
            all_passed = False
        metric = _format_guard_metric(key, data if isinstance(data, dict) else {})
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

    # 写入报告文件 (重试 + fallback 由 helper 处理)
    report_path = REPORTS_DIR / report_date / f"eod_guard_report_{report_date}.md"
    return _write_report_with_retry(lines, report_path)


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
    parser.add_argument(
        "--skip-system-check",
        action="store_true",
        help="跳过 P0 启动自检 (仅紧急情况使用)",
    )
    args = parser.parse_args()

    # ═══════════════════════════════════════════════════════════════
    # P0 启动自检 (v8.6.12) — 在任何业务逻辑之前拦截错误
    # ═══════════════════════════════════════════════════════════════
    if not args.skip_system_check:
        try:
            from utils.system_check import assert_system_ready
            assert_system_ready()  # 失败时 sys.exit(1)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[P0 自检] 异常 (容错通过): {e}", file=sys.stderr)

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
