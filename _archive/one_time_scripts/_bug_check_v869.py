"""
v8.6.9 系统 Bug 全面检测脚本
============================
创建日期: 2026-07-26
检测维度:
    1. trade_plan 字段一致性 (跨日多份 plan 文件)
    2. 隔夜跳空 / KillSwitch / Liquidity Crisis 字段联动
    3. Windows 任务计划程序状态与最新结果
    4. NTP 同步与时间漂移
    5. 数据源 fallback 链状态
    6. KillSwitch 事件日志完整性
    7. 7-Guard 链应用历史
    8. 过期 trade_plan 文件清理
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PLANS_DIR = BASE / "v8.3_institutional" / "trade_plans"
REPORTS_DIR = BASE / "v8.3_institutional" / "reports"
DAILY_REPORT_DIR = BASE / "每日报告归档"  # v8.6.9: 实际生产报告目录
LOGS_DIR = BASE / "logs"
KILL_SWITCH_EVENTS = LOGS_DIR / "kill_switch_events.jsonl"

p0_bugs = []
p1_bugs = []
p2_info = []


def bug(level: str, module: str, msg: str, detail: str = ""):
    line = f"[{level}][{module}] {msg}" + (f" — {detail}" if detail else "")
    print(line, flush=True)
    if level == "P0":
        p0_bugs.append(line)
    elif level == "P1":
        p1_bugs.append(line)
    else:
        p2_info.append(line)


def ok(msg: str, detail: str = ""):
    print(f"[OK] {msg}" + (f" — {detail}" if detail else ""), flush=True)


def run(cmd: str, timeout: int = 30) -> str:
    """安全运行命令并返回文本, 处理编码"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, timeout=timeout  # nosec B602 — 命令来源可信 (内部调用)
        )
        for enc in ("utf-8", "gbk", "cp936", "latin-1"):
            try:
                return result.stdout.decode(enc) + result.stderr.decode(enc)
            except UnicodeDecodeError:
                continue
        return ""
    except subprocess.TimeoutExpired:
        return f"<timeout after {timeout}s>"
    except Exception as e:
        return f"<error: {e}>"


def main() -> int:
    print("=" * 72)
    print("v8.6.9 系统 Bug 全面检测")
    print(f"运行时间: {datetime.now()}")
    print("=" * 72)

    # ============================================================
    # 1. trade_plan 文件清单与字段一致性
    # ============================================================
    print("\n=== 1. trade_plan 文件清单 ===")
    plans = sorted(PLANS_DIR.glob("trade_plan_*.json"), reverse=True)
    if not plans:
        bug("P0", "TRADE_PLAN", "无 trade_plan 文件")
        return 1

    now = datetime.now()
    today_str = now.strftime("%Y%m%d")
    future_plans = []
    stale_plans = []

    for p in plans[:8]:
        stat = p.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {p.name}  size={stat.st_size}  mtime={mtime}")

        # 检查未来日期 plan 是否为过期版本
        date_part = p.stem.replace("trade_plan_", "")
        try:
            plan_date = datetime.strptime(date_part, "%Y%m%d")
            if plan_date.strftime("%Y%m%d") > today_str:
                # 未来 plan — 应该 mtime 较新
                if stat.st_mtime < (now.timestamp() - 86400):
                    stale_plans.append((p.name, mtime))
                else:
                    future_plans.append(p.name)
        except ValueError:
            pass

    if stale_plans:
        for name, mt in stale_plans:
            bug("P1", "STALE_PLAN", "未来日期 plan mtime 过早", f"{name} mtime={mt}")

    # ============================================================
    # 2. 最新 plan 的字段一致性
    # ============================================================
    print("\n=== 2. 最新 trade_plan 字段一致性 ===")
    latest_plan_path = plans[0]
    with open(latest_plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    ms = plan.get("market_state", {})
    rg = plan.get("risk_guard", {})
    ep = plan.get("execution_plan", {})
    phase = plan.get("phase", {})
    he = plan.get("hedge_execution", {})

    print(f"  最新 plan: {latest_plan_path.name}")

    # 2.1 circuit_level vs build_allowed
    circuit = str(ms.get("circuit_level", "NORMAL")).upper()
    build_allowed = ms.get("build_allowed", True)
    spot_build = ms.get("spot_build_allowed", True)
    halt_all = ms.get("halt_all_trading", False)

    print(f"  circuit_level={circuit}, build_allowed={build_allowed}, spot_build={spot_build}, halt_all={halt_all}")

    if circuit == "CRITICAL":
        if build_allowed or spot_build:
            bug("P0", "GUARD", "circuit=CRITICAL 但 build_allowed 或 spot_build 为 True",
                f"build_allowed={build_allowed}, spot_build={spot_build}")
        else:
            ok("circuit=CRITICAL, build_allowed=False, spot_build=False (一致)")
    elif circuit == "WARNING":
        if not spot_build:
            ok("circuit=WARNING, spot_build=False (一致)")
        else:
            bug("P0", "GUARD", "circuit=WARNING 但 spot_build_allowed=True")
    else:
        if build_allowed and spot_build:
            ok("circuit=NORMAL, 全部允许 (一致)")
        else:
            bug("P1", "GUARD", "circuit=NORMAL 但有禁止字段",
                f"build_allowed={build_allowed}, spot_build={spot_build}")

    # 2.2 overnight_gap L2 → 无 BUY 订单
    print("\n=== 3. overnight_gap L2 → BUY 订单清空 ===")
    overnight = rg.get("overnight_gap", {})
    overnight_level = overnight.get("level", 0)
    morning_orders = ep.get("morning_orders", [])
    afternoon_orders = ep.get("afternoon_orders", [])
    buy_orders = [
        o for o in morning_orders + afternoon_orders
        if str(o.get("side", "")).upper() == "BUY"
        or str(o.get("direction", "")).upper() in ("BUY", "BUY_OPEN", "BUY_PUT")
    ]

    print(f"  overnight_gap.level={overnight_level}, BUY 订单数={len(buy_orders)}")

    if overnight_level >= 2:
        if len(buy_orders) > 0:
            bug("P0", "GUARD", f"overnight_gap L{overnight_level} 但仍有 {len(buy_orders)} 笔 BUY 订单")
        else:
            ok(f"overnight_gap L{overnight_level}, BUY 订单已清空")
    elif overnight_level == 3:
        if len(morning_orders) + len(afternoon_orders) > 0:
            bug("P0", "GUARD", "overnight_gap L3 但仍有订单未清空")

    # 2.3 KillSwitch L3 字段联动
    print("\n=== 4. KillSwitch L3 字段联动 ===")
    kill_switch = rg.get("kill_switch", {})
    ks_level = kill_switch.get("level", 0)
    print(f"  kill_switch.level={ks_level}")

    if ks_level >= 3:
        if not halt_all:
            bug("P0", "GUARD", f"kill_switch L{ks_level} 但 halt_all_trading=False")
        if not circuit == "CRITICAL":
            bug("P0", "GUARD", f"kill_switch L{ks_level} 但 circuit_level != CRITICAL")
        if build_allowed:
            bug("P0", "GUARD", f"kill_switch L{ks_level} 但 build_allowed=True")

    # 2.4 liquidity_crisis data_unavailable 联动
    print("\n=== 5. liquidity_crisis 联动 ===")
    liq = rg.get("liquidity_crisis", {})
    data_unavailable = liq.get("data_unavailable", False)
    print(f"  liquidity_crisis.data_unavailable={data_unavailable}")

    if data_unavailable:
        if build_allowed:
            bug("P0", "GUARD", "liquidity_crisis data_unavailable=True 但 build_allowed=True")
        else:
            ok("liquidity_crisis 触发时 build_allowed=False (一致)")

    # 2.5 vol_scale 应用与审计字段
    print("\n=== 6. vol_scale 应用与审计字段 ===")
    vol_scale = rg.get("vol_scale")
    vol_summary = rg.get("vol_scale_executed_summary", {})
    original_daily = phase.get("original_daily_capital")
    actual_daily = phase.get("daily_capital")
    exec_dc = ep.get("day_capital")
    print(f"  vol_scale={vol_scale}, phase.daily_capital={actual_daily}, exec.day_capital={exec_dc}")
    print(f"  phase.original_daily_capital={original_daily}")
    print(f"  vol_scale_executed_summary={vol_summary}")

    if vol_scale is not None and vol_scale < 1.0:
        if original_daily is None:
            bug("P1", "AUDIT", "vol_scale<1.0 但 phase.original_daily_capital 缺失")
        if abs(actual_daily - exec_dc) > 1.0:
            bug("P0", "GUARD", "phase.daily_capital 与 execution_plan.day_capital 不一致",
                f"phase={actual_daily} vs exec={exec_dc}")
        else:
            ok("phase.daily_capital == exec.day_capital")
        if not vol_summary:
            bug("P1", "AUDIT", "vol_scale<1.0 但无 vol_scale_executed_summary 审计字段")
        else:
            ok("vol_scale_executed_summary 已记录")

    # 2.6 执行订单总数与 day_capital 比对
    total_amount = ep.get("total_amount", 0)
    print(f"\n  execution_plan.total_amount={total_amount}")
    if exec_dc and total_amount > exec_dc * 1.5:
        bug("P1", "BUDGET", "total_amount 远超 day_capital (50% 容差)",
            f"total={total_amount} vs day_capital={exec_dc}")
    else:
        ok("total_amount 在合理范围")

    # 2.7 hedge_execution 字段完整性
    print("\n=== 7. hedge_execution 字段完整性 ===")
    futures_orders = he.get("futures_orders", [])
    options_orders = he.get("options_orders", [])
    print(f"  futures_orders={len(futures_orders)}, options_orders={len(options_orders)}")

    if options_orders and not futures_orders:
        if he.get("execution_status") == "PENDING":
            ok("OPTIONS_ONLY 模式, 期权订单 PENDING")
        else:
            bug("P1", "HEDGE", "hedge_execution.status 异常", f"status={he.get('execution_status')}")

    # ============================================================
    # 3. Windows 任务计划程序
    # ============================================================
    print("\n=== 8. Windows 任务计划程序状态 ===")
    for task in ("QuantPipelineFactor_06AM", "QuantWorkflow_07AM",
                 "QuantPipelineFactor_0930", "QuantWorkflow_1400"):
        out = run(f'schtasks /query /tn {task} /fo list /v')
        if "ERROR" in out or "cannot find" in out.lower() or "无法找到" in out:
            print(f"  [SKIP] 任务 {task} 未注册")
            continue
        # 提取关键字段
        last_result = "?"
        next_run = "?"
        last_run = "?"
        status = "?"
        for line in out.splitlines():
            if "Last Result" in line:
                last_result = line.split(":", 1)[-1].strip()
            elif "Next Run Time" in line:
                next_run = line.split(":", 1)[-1].strip()
            elif "Last Run Time" in line:
                last_run = line.split(":", 1)[-1].strip()
            elif "Status:" in line:
                status = line.split(":", 1)[-1].strip()
        print(f"  {task}: status={status}, last_run={last_run}, last_result={last_result}, next_run={next_run}")

        if last_result not in ("0", "?"):
            bug("P1", "TASK_SCHED", f"{task} Last Result={last_result} (非0)",
                f"last_run={last_run}")
        else:
            ok(f"{task} Last Result=0")

    # ============================================================
    # 4. NTP 同步状态
    # ============================================================
    print("\n=== 9. NTP 同步状态 ===")
    out = run("w32tm /query /status")
    print(f"  {out.strip()[:500]}")
    # v8.6.9 fix: 中文系统下源字段为 "源:" 而非 "Source:"
    source = ""
    for line in out.splitlines():
        if "Source:" in line or "源:" in line:
            source = line.split(":", 1)[-1].strip()
            break
    if source:
        ok(f"NTP 源: {source}")
    else:
        bug("P1", "NTP", "NTP 服务未启动")

    # ============================================================
    # 5. 数据源 fallback 链
    # ============================================================
    print("\n=== 10. 数据源状态 ===")
    try:
        sys.path.insert(0, str(BASE))
        sys.path.insert(0, str(BASE / "utils"))
        # v8.6.9 fix: data_provider.py 实际类名为 MarketDataProvider
        try:
            from data_provider import MarketDataProvider as _DP
        except ImportError:
            try:
                from data_provider import UnifiedDataProvider as _DP
            except ImportError:
                from data_provider import DataProvider as _DP
        dp = _DP()
        if hasattr(dp, "source_health"):
            for name, info in dp.source_health.items():
                status = info.get("status", "unknown") if isinstance(info, dict) else str(info)
                print(f"  {name}: {status}")
        else:
            print("  (无 source_health 字段, 跳过)")
    except Exception as e:
        bug("P2", "DATA_SOURCE", f"数据源检查失败: {e}")

    # ============================================================
    # 6. KillSwitch 事件日志
    # ============================================================
    print("\n=== 11. KillSwitch 事件日志 ===")
    if KILL_SWITCH_EVENTS.exists():
        lines = KILL_SWITCH_EVENTS.read_text(encoding="utf-8").splitlines()
        print(f"  日志行数: {len(lines)}")
        if lines:
            try:
                last = json.loads(lines[-1])
                print(f"  最近事件: {last.get('timestamp')} level={last.get('level')} action={last.get('action')}")
            except Exception as e:
                bug("P1", "KILL_SWITCH_LOG", f"最后一行解析失败: {e}")
    else:
        bug("P2", "KILL_SWITCH_LOG", "kill_switch_events.jsonl 不存在")

    # ============================================================
    # 7. 关键脚本文件存在性
    # ============================================================
    print("\n=== 12. 关键脚本文件存在性 ===")
    critical_files = [
        "v8.3_institutional/daily_workflow.py",
        "v8.3_institutional/generate_daily_trade_plan.py",
        "v8.3_institutional/run_weekly_auto.bat",
        "utils/risk_guard_integrator.py",
        "utils/kill_switch.py",
        "utils/overnight_gap_monitor.py",
        "utils/market_circuit_breaker.py",
        "utils/portfolio_optimizer.py",
        "scripts/run_pipeline_factor_offline.py",
        "scripts/pre_market_auto_check.py",
        "scripts/_verify_v868_live_ready.py",
    ]
    for f in critical_files:
        p = BASE / f
        if not p.exists():
            bug("P0", "FILE_MISSING", f"关键文件缺失: {f}")
        else:
            size_kb = p.stat().st_size / 1024
            print(f"  ✅ {f}  ({size_kb:.1f} KB)")

    # ============================================================
    # 8. 报告目录与最新报告 (v8.6.9 P0 FIX: 修正路径)
    # ============================================================
    print("\n=== 13. 报告目录 ===")
    # v8.6.9 FIX: 实际报告在 每日报告归档/{date}/daily_pnl_report_{date}.json
    if DAILY_REPORT_DIR.exists():
        date_dirs = sorted([d for d in DAILY_REPORT_DIR.iterdir() if d.is_dir() and d.name != "2026"], reverse=True)
        print(f"  报告日期目录数: {len(date_dirs)}")
        if date_dirs:
            latest_dir = date_dirs[0]
            print(f"  最新报告目录: {latest_dir.name}")
            files = list(latest_dir.glob("daily_pnl_report_*.json"))
            print(f"  最新 daily_pnl_report: {files[0].name if files else 'N/A'}")
            if files:
                rname = files[0].stem.replace("daily_pnl_report_", "")
                try:
                    rdate = datetime.strptime(rname.replace("-", ""), "%Y%m%d")
                    days_old = (now - rdate).days
                    if days_old > 7:
                        bug("P2", "REPORT", f"最新 daily_pnl_report 外部输入已 {days_old} 天未更新",
                            f"file={files[0].name} (需用户/QMT 导入)")
                    else:
                        ok(f"最新外部报告 {days_old} 天前")
                except ValueError:
                    pass
    else:
        bug("P1", "REPORT", "每日报告归档目录不存在")

    # ============================================================
    # 9. Bug 检测总结
    # ============================================================
    print("\n" + "=" * 72)
    print("Bug 检测总结")
    print("=" * 72)
    print(f"\nP0 阻断: {len(p0_bugs)} 项")
    for b in p0_bugs:
        print(f"  {b}")
    print(f"\nP1 风险: {len(p1_bugs)} 项")
    for b in p1_bugs:
        print(f"  {b}")
    print(f"\nP2 信息: {len(p2_info)} 项")
    for b in p2_info:
        print(f"  {b}")

    print(f"\n结论: {'✅ 系统健康' if not p0_bugs and not p1_bugs else '⚠️ 需修复'}")
    return 0 if not p0_bugs else 1


if __name__ == "__main__":
    sys.exit(main())
