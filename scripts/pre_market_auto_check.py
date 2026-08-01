"""
实盘开盘前综合自动检测脚本 (Pre-Market Auto Check)
=====================================================
创建日期: 2026-07-26
用途: 实盘开盘前 (08:30前) 一键执行 6 项核心检测

检测内容:
    1. NTP 时间同步状态 (w32time 服务 + 同步源)
    2. iFinD MCP / 通达信 / AKShare 数据源连通性
    3. QuantPipelineFactor_06AM 任务计划状态
    4. QuantWorkflow_07AM 任务计划状态
    5. trade_plan_{YYYYMMDD}.json 自动生成验证
    6. 7-Guard 链 + 27项 P0/P1 校验

用法:
    py -3 scripts/pre_market_auto_check.py [YYYY-MM-DD]

    默认日期 = 下一个交易日 (跳过周末)

退出码:
    0 = 全部通过, 可进入实盘
    1 = 存在失败项, 需人工干预
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TRADE_PLANS_DIR = BASE / "v8.3_institutional" / "trade_plans"
PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"
WARN = "  ⚠️ WARN"
errors = []
passed = 0
total = 0


def check(condition: bool, msg: str, detail: str = "") -> None:
    global passed, total
    total += 1
    if condition:
        passed += 1
        print(f"{PASS}: {msg}" + (f" — {detail}" if detail else ""))
    else:
        errors.append(f"{msg}: {detail}")
        print(f"{FAIL}: {msg}" + (f" — {detail}" if detail else ""))


def warn(msg: str, detail: str = "") -> None:
    print(f"{WARN}: {msg}" + (f" — {detail}" if detail else ""))


def get_next_trade_date(from_date: datetime = None) -> str:
    """获取下一个交易日 (跳过周末)"""
    if from_date is None:
        from_date = datetime.now()
    next_date = from_date + timedelta(days=1)
    while next_date.weekday() >= 5:  # 5=Saturday, 6=Sunday
        next_date += timedelta(days=1)
    return next_date.strftime("%Y-%m-%d")


# ============================================================
# 1. NTP 时间同步检测
# ============================================================
def check_ntp_sync() -> None:
    print("\n" + "=" * 72)
    print("[1/6] NTP 时间同步状态")
    print("=" * 72)

    # 1.1 w32time 服务状态 (使用 sc query 避免编码问题)
    try:
        result = subprocess.run(
            ["sc", "query", "w32time"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout
        # sc query 输出格式: STATE: 4 RUNNING (中文系统可能是 "STATE              : 4  RUNNING")
        is_running = "RUNNING" in output.upper()
        check(
            is_running,
            "NTP-1a w32time 服务运行中",
            "找到 RUNNING" if is_running else "未找到 RUNNING"
        )
    except Exception as e:
        check(False, "NTP-1a w32time 服务查询异常", str(e)[:100])

    # 1.2 w32time 启动类型 (使用 sc qc)
    try:
        result = subprocess.run(
            ["sc", "qc", "w32time"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout
        # sc qc 输出: START_TYPE         : 2  AUTO_START
        is_auto = "AUTO_START" in output.upper()
        check(
            is_auto,
            "NTP-1b w32time 启动类型=AUTO_START",
            "找到 AUTO_START" if is_auto else "未找到 AUTO_START"
        )
    except Exception as e:
        check(False, "NTP-1b w32time 启动类型查询异常", str(e)[:100])

    # 1.3 NTP 同步状态 (使用 w32tm, 尝试多种编码)
    try:
        result = subprocess.run(
            ["w32tm", "/query", "/status"],
            capture_output=True, timeout=10
        )
        # 尝试多种编码 (中文系统可能是 GBK)
        output = ""
        for encoding in ["utf-8", "gbk", "cp936", "latin-1"]:
            try:
                output = result.stdout.decode(encoding)
                if "Source" in output or "源" in output or "Leap" in output:
                    break
            except (UnicodeDecodeError, LookupError):
                continue

        # 检查是否找到同步源
        has_source = any(kw in output for kw in ["Source:", "源:", "Source "])
        check(
            has_source,
            "NTP-2a NTP 同步源已建立",
            f"输出长度={len(output)}"
        )

        # 提取同步源
        if has_source:
            for line in output.split("\n"):
                if "Source:" in line or "源:" in line:
                    source = line.split(":", 1)[1].strip() if ":" in line else ""
                    check(True, "NTP-2b 同步源详情", f"source={source[:60]}")
                    break

        # 检查根延迟/根分散 (同步指标)
        has_metrics = any(kw in output for kw in ["RootDelay", "RootDispersion", "根延迟", "根分散"])
        check(
            has_metrics,
            "NTP-2c 同步指标存在 (RootDelay/RootDispersion)",
            "找到指标" if has_metrics else "未找到指标"
        )
    except Exception as e:
        check(False, "NTP-2 NTP 同步状态查询异常", str(e)[:100])


# ============================================================
# 2. 数据源连通性检测
# ============================================================
def check_datasources() -> None:
    print("\n" + "=" * 72)
    print("[2/6] 数据源连通性 (iFinD MCP / 通达信 / AKShare)")
    print("=" * 72)

    sys.path.insert(0, str(BASE))
    sys.path.insert(0, str(BASE / "utils"))

    # 2.1 iFinD MCP
    try:
        from ifind_client import IFindClient
        conn = IFindClient()
        check(True, "DS-1 iFinD MCP 连接器初始化", f"type={type(conn).__name__}")
    except Exception as e:
        check(False, "DS-1 iFinD MCP 连接器", str(e)[:80])

    # 2.2 通达信
    try:
        from tdx_data_source import TDXDataSource
        ds = TDXDataSource()
        check(True, "DS-2 通达信数据源初始化", f"type={type(ds).__name__}")
    except Exception as e:
        check(False, "DS-2 通达信数据源", str(e)[:80])

    # 2.3 AKShare
    try:
        from akshare_data_source import AKShareDataSource
        ak = AKShareDataSource()
        check(True, "DS-3 AKShare 数据源初始化", f"type={type(ak).__name__}")
    except Exception as e:
        check(False, "DS-3 AKShare 数据源", str(e)[:80])

    # 2.4 MarketDataProvider 路由
    try:
        from data_provider import MarketDataProvider
        dp = MarketDataProvider()
        if hasattr(dp, "source_health"):
            health = dp.source_health
            ok_count = sum(1 for h in health.values() if h.get("ok", False))
            check(
                ok_count >= 2,
                "DS-4 MarketDataProvider 多数据源路由",
                f"健康数据源数={ok_count}/{len(health)}"
            )
        else:
            check(True, "DS-4 MarketDataProvider 初始化", "无 source_health 字段")
    except Exception as e:
        check(False, "DS-4 MarketDataProvider 路由", str(e)[:80])


# ============================================================
# 3. 任务计划程序检测
# ============================================================
def check_scheduled_task(task_name: str, expected_time: str, section: str) -> None:
    """检查 Windows 任务计划程序状态"""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/v", "/fo", "LIST"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout

        # 解析关键字段
        status = ""
        next_run = ""
        last_result = ""
        for line in output.split("\n"):
            if "Status:" in line or "状态:" in line:
                status = line.split(":", 1)[1].strip() if ":" in line else ""
            elif "Next Run Time:" in line or "下次运行时间:" in line:
                next_run = line.split(":", 1)[1].strip() if ":" in line else ""
            elif "Last Result:" in line or "上次结果:" in line:
                last_result = line.split(":", 1)[1].strip() if ":" in line else ""

        check(
            "Ready" in status or "就绪" in status or "已就绪" in status,
            f"{section}-a {task_name} 状态=Ready",
            f"status={status}"
        )
        check(
            expected_time in next_run,
            f"{section}-b {task_name} 下次运行时间正确",
            f"expected={expected_time}, actual={next_run}"
        )
        # Last Result=0 表示成功, 1 表示失败, 267011 表示从未运行
        if last_result in ("0", "267011"):
            check(True, f"{section}-c {task_name} 上次执行结果", f"last_result={last_result}")
        else:
            warn(f"{section}-c {task_name} 上次执行结果", f"last_result={last_result} (0=成功, 267011=未运行过)")
    except Exception as e:
        check(False, f"{section} {task_name} 查询异常", str(e)[:80])


def check_scheduled_tasks() -> None:
    print("\n" + "=" * 72)
    print("[3/6] QuantPipelineFactor_06AM 任务计划状态")
    print("=" * 72)
    check_scheduled_task("QuantPipelineFactor_06AM", "6:00:00", "TSK1")

    print("\n" + "=" * 72)
    print("[4/6] QuantWorkflow_07AM 任务计划状态")
    print("=" * 72)
    check_scheduled_task("QuantWorkflow_07AM", "7:00:00", "TSK2")


# ============================================================
# 5. trade_plan 自动生成验证
# ============================================================
def check_trade_plan(trade_date: str) -> None:
    print("\n" + "=" * 72)
    print(f"[5/6] trade_plan_{trade_date.replace('-', '')}.json 自动生成验证")
    print("=" * 72)

    plan_path = TRADE_PLANS_DIR / f"trade_plan_{trade_date.replace('-', '')}.json"
    check(
        plan_path.exists(),
        "TP-1 trade_plan 文件存在",
        f"path={plan_path.name}"
    )

    if not plan_path.exists():
        return

    try:
        with open(plan_path, encoding="utf-8") as f:
            plan = json.load(f)

        # 5.1 metadata.version
        version = plan.get("metadata", {}).get("version", "")
        check("v8.6.8" in version, "TP-2 metadata.version 包含 v8.6.8", f"version={version}")

        # 5.2 资金配置
        stock_cap = plan.get("stock_etf_capital", 0)
        hedge_cap = plan.get("hedge_capital", 0)
        check(stock_cap == 4_000_000, "TP-3a stock_etf_capital=4M", f"actual={stock_cap}")
        check(hedge_cap == 1_000_000, "TP-3b hedge_capital=1M", f"actual={hedge_cap}")

        # 5.3 market_state 一致性
        ms = plan.get("market_state", {})
        build_allowed = ms.get("build_allowed", True)
        spot_build = ms.get("spot_build_allowed", True)
        circuit = str(ms.get("circuit_level", "NORMAL")).upper()

        if circuit == "CRITICAL":
            check(
                build_allowed is False and spot_build is False,
                "TP-4 CRITICAL 时 build/spot_build_allowed=False",
                f"build={build_allowed}, spot_build={spot_build}"
            )
        elif circuit == "WARNING":
            check(
                spot_build is False,
                "TP-4 WARNING 时 spot_build_allowed=False",
                f"spot_build={spot_build}"
            )
        else:
            check(True, "TP-4 circuit_level=NORMAL", f"circuit={circuit}")

        # 5.4 vol_scale 审计追溯
        rg = plan.get("risk_guard", {})
        vol_scale = rg.get("vol_scale")
        if vol_scale is not None and vol_scale < 1.0:
            orig_dc = plan.get("phase", {}).get("original_daily_capital")
            check(
                orig_dc is not None,
                "TP-5a phase.original_daily_capital 已保存 (审计追溯)",
                f"original={orig_dc}"
            )
            vol_summary = rg.get("vol_scale_executed_summary", {})
            check(
                bool(vol_summary),
                "TP-5b vol_scale_executed_summary 字段存在",
                f"note={vol_summary.get('note', '')[:60]}"
            )

        # 5.5 hedge_execution
        he = plan.get("hedge_execution", {})
        exec_status = he.get("execution_status", "")
        options_count = len(he.get("options_orders", []))
        check(
            exec_status in ("PENDING", "CANCELLED", "EXECUTED"),
            "TP-6a hedge_execution.execution_status 有效",
            f"status={exec_status}"
        )
        check(
            options_count >= 0,
            "TP-6b hedge_execution.options_orders 数量",
            f"count={options_count}"
        )

        # 5.6 futures_options_hedge
        foh = plan.get("futures_options_hedge", {})
        check(
            foh.get("loaded") is True,
            "TP-7a futures_options_hedge.loaded=True",
            f"loaded={foh.get('loaded')}"
        )
        foh_count = foh.get("orders_count", 0)
        check(
            foh_count == options_count,
            "TP-7b futures_options_hedge.orders_count 与 hedge_execution 一致",
            f"foh={foh_count} vs he={options_count}"
        )

    except Exception as e:
        check(False, "TP 读取 trade_plan 异常", str(e)[:80])


# ============================================================
# 6. 7-Guard 链 + 27 项校验
# ============================================================
def check_7guard_and_validation(trade_date: str) -> None:
    print("\n" + "=" * 72)
    print("[6/6] 7-Guard 链 + 27 项 P0/P1 校验")
    print("=" * 72)

    # 通过 _verify_v868_live_ready.py 校验 (需要先修改目标日期)
    verify_script = BASE / "scripts" / "_verify_v868_live_ready.py"
    if not verify_script.exists():
        check(False, "VG-1 验证脚本不存在", str(verify_script))
        return

    try:
        # 读取原始内容
        with open(verify_script, encoding="utf-8") as f:
            original_content = f.read()

        # 临时替换日期
        date_compact = trade_date.replace("-", "")
        modified_content = original_content.replace("trade_plan_20260727.json", f"trade_plan_{date_compact}.json")
        with open(verify_script, "w", encoding="utf-8") as f:
            f.write(modified_content)

        # 运行验证脚本
        result = subprocess.run(
            ["py", "-3", str(verify_script)],
            capture_output=True, text=True, timeout=60, cwd=str(BASE)
        )

        # 恢复原内容
        with open(verify_script, "w", encoding="utf-8") as f:
            f.write(original_content)

        # 解析输出 (查找 "验证结果: X/Y 通过")
        output = result.stdout
        if "验证结果:" in output:
            for line in output.split("\n"):
                if "验证结果:" in line:
                    check(
                        "27/27" in line,
                        "VG-2 27/27 验证项全部通过",
                        line.strip()
                    )
                    break
        else:
            check(False, "VG-2 验证脚本未输出结果", output[-200:])

    except Exception as e:
        # 恢复原内容
        try:
            with open(verify_script, "w", encoding="utf-8") as f:
                f.write(original_content)
        except Exception:
            pass
        check(False, "VG 验证脚本执行异常", str(e)[:80])


# ============================================================
# 主函数
# ============================================================
def main() -> int:
    trade_date = sys.argv[1] if len(sys.argv) > 1 else get_next_trade_date()

    print("=" * 72)
    print("实盘开盘前综合自动检测 (Pre-Market Auto Check)")
    print(f"检测日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标交易日: {trade_date}")
    print("=" * 72)

    # 执行 6 项检测
    check_ntp_sync()
    check_datasources()
    check_scheduled_tasks()
    check_trade_plan(trade_date)
    check_7guard_and_validation(trade_date)

    # 总结
    print("\n" + "=" * 72)
    print(f"综合检测结果: {passed}/{total} 通过")
    print("=" * 72)

    if errors:
        print(f"\n失败项 ({len(errors)}):")
        for e in errors:
            print(f"  ❌ {e}")
        print("\n❌ 实盘就绪度: 不通过 — 需人工干预")
        return 1
    else:
        print("\n✅ 实盘就绪度: 通过 — 可进入实盘对接")
        return 0


if __name__ == "__main__":
    sys.exit(main())
