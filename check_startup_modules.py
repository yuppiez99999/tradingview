# -*- coding: utf-8 -*-
"""
自动交易计划启动检查
====================

验证自动交易计划启动时所有模块是否都同时启动:
  1. Python 路径一致性 (所有 bat/ps1/py 使用同一个 Python)
  2. run_all_modules.py 模块清单完整性
  3. 每个模块脚本存在性检查
  4. 关键模块导入测试
  5. 数据源连接器状态
  6. 任务计划注册状态
  7. dry-run 模拟启动测试

用法:
    python check_startup_modules.py
    python check_startup_modules.py --dry-run   # 模拟启动所有模块
"""
import os
import sys
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple

# ============================================================
# 配置
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent
V75_DIR = PROJECT_ROOT / "v7.5_institutional"
EXPECTED_PYTHON = r"C:\Program Files\Python38\python.exe"

# 期望的模块清单 (与 run_all_modules.py 一致)
EXPECTED_PREMARKET = [
    {"name": "wind_calibrate", "script": "calibrate_asset_params_wind.py", "schedule": "weekly"},
    {"name": "update_prices", "script": "../update_position_prices.py", "schedule": "daily"},
    {"name": "daily_trade_executor_pre", "script": "../daily_trade_executor.py", "schedule": "daily",
     "extra_args": ["pre-market"]},
    {"name": "daily_workflow", "script": "daily_workflow.py", "schedule": "daily"},
]

EXPECTED_POSTMARKET = [
    {"name": "daily_trade_executor_post", "script": "../daily_trade_executor.py", "schedule": "daily",
     "extra_args": ["post-market"]},
    {"name": "daily_pnl_report", "script": "../generate_daily_report.py", "schedule": "daily"},
    {"name": "stop_loss_monitor", "script": "../stop_loss_monitor.py", "schedule": "daily"},
]

# 需要检查 Python 路径的文件
FILES_WITH_PYTHON_PATH = [
    "v7.5_institutional/run_all_modules.bat",
    "v7.5_institutional/run_all_modules.py",
    "v7.5_institutional/run_daily.bat",
    "v7.5_institutional/check_all_modules.bat",
    "v7.5_institutional/scheduler_daemon.py",
    "run_pre_market.bat",
    "run_daily_report.bat",
    "trading_scheduler.bat",
    "run_start_live.bat",
    "deploy_trading_schedule.bat",
    "install_daily_hedge_task.bat",
    "service_manager.ps1",
    "repair_cn_tasks.ps1",
    "verify_cn_tasks.ps1",
    "repair_scheduled_tasks.ps1",
    "v7.5_institutional/register_task_ascii.ps1",
]

# 关键 Python 模块导入测试
CRITICAL_IMPORTS = [
    ("utils.data_provider", "MarketDataProvider"),
    ("utils.tf_price_predictor", "PricePredictor"),
    ("utils.external_data_source", "ExternalDataManager"),
    ("utils.web_scraper", "WebScraper"),
    ("utils.ai_report_agent", "AIReportAgent"),
    ("utils.ifind_client", "IFindClient"),
    ("daily_trade_executor", None),
    ("generate_daily_report", None),
    ("update_position_prices", None),
    ("stop_loss_monitor", None),
]

# v7.5 src 模块
V75_SRC_IMPORTS = [
    "risk.risk_manager",
    "risk.circuit_breaker",
    "hedging.hedge_coordinator",
    "execution.smart_order_router",
    "alpha.signal_fusion",
    "backtest.metrics",
    "backtest.cost_model",
]


def print_header(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def check_python_path() -> Tuple[int, int]:
    """检查1: Python 路径一致性"""
    print_header("[1/7] Python 路径一致性检查")
    ok_count, fail_count = 0, 0
    for rel_path in FILES_WITH_PYTHON_PATH:
        filepath = PROJECT_ROOT / rel_path
        if not filepath.exists():
            print(f"  [SKIP] {rel_path} (文件不存在)")
            continue
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        if "Python314" in content:
            print(f"  [FAIL] {rel_path} — 仍使用 Python 3.14 路径")
            fail_count += 1
        elif EXPECTED_PYTHON.replace("\\", "\\\\") in content or EXPECTED_PYTHON in content:
            print(f"  [OK]   {rel_path}")
            ok_count += 1
        else:
            # 检查是否有其他 Python 路径
            import re
            matches = re.findall(r'[A-Z]:\\[^"\']*python[^"\']*\.exe', content, re.IGNORECASE)
            if matches:
                print(f"  [WARN] {rel_path} — 使用其他路径: {matches[0]}")
                ok_count += 1
            else:
                print(f"  [OK]   {rel_path} (未发现 Python 路径引用)")
                ok_count += 1
    print(f"\n  汇总: {ok_count} OK / {fail_count} FAIL")
    return ok_count, fail_count


def check_module_list() -> Tuple[int, int]:
    """检查2: run_all_modules.py 模块清单完整性"""
    print_header("[2/7] run_all_modules.py 模块清单完整性")
    ram_file = V75_DIR / "run_all_modules.py"
    if not ram_file.exists():
        print(f"  [FAIL] {ram_file} 不存在")
        return 0, 1

    content = ram_file.read_text(encoding="utf-8")
    ok_count, fail_count = 0, 0

    # 检查每个期望模块是否在清单中
    for mod in EXPECTED_PREMARKET + EXPECTED_POSTMARKET:
        if mod["name"] in content:
            print(f"  [OK]   {mod['name']}")
            ok_count += 1
        else:
            print(f"  [FAIL] {mod['name']} — 未在模块清单中找到")
            fail_count += 1

    # 检查硬编码 API Key
    if "ak_Tk4Y" in content:
        print(f"  [FAIL] 硬编码 WIND_API_KEY 仍然存在")
        fail_count += 1
    else:
        print(f"  [OK]   无硬编码 API Key")
        ok_count += 1

    print(f"\n  汇总: {ok_count} OK / {fail_count} FAIL")
    return ok_count, fail_count


def check_scripts_exist() -> Tuple[int, int]:
    """检查3: 每个模块脚本存在性"""
    print_header("[3/7] 模块脚本存在性检查")
    ok_count, fail_count = 0, 0
    all_modules = EXPECTED_PREMARKET + EXPECTED_POSTMARKET
    for mod in all_modules:
        script_path = V75_DIR / mod["script"]
        if script_path.exists():
            size = script_path.stat().st_size
            print(f"  [OK]   {mod['name']:30s} {mod['script']:40s} ({size} bytes)")
            ok_count += 1
        else:
            print(f"  [FAIL] {mod['name']:30s} {mod['script']:40s} 不存在")
            fail_count += 1
    print(f"\n  汇总: {ok_count} OK / {fail_count} FAIL")
    return ok_count, fail_count


def check_critical_imports() -> Tuple[int, int]:
    """检查4: 关键模块导入测试"""
    import importlib
    print_header("[4/7] 关键模块导入测试")
    sys.path.insert(0, str(PROJECT_ROOT))
    sys.path.insert(0, str(V75_DIR / "src"))
    ok_count, fail_count = 0, 0

    for mod_path, cls_name in CRITICAL_IMPORTS:
        try:
            mod = importlib.import_module(mod_path)
            if cls_name:
                getattr(mod, cls_name)
            print(f"  [OK]   {mod_path}" + (f".{cls_name}" if cls_name else ""))
            ok_count += 1
        except Exception as e:
            print(f"  [FAIL] {mod_path} — {str(e)[:60]}")
            fail_count += 1

    # v7.5 src 模块
    print(f"\n  --- v7.5 src 模块 ---")
    for mod_path in V75_SRC_IMPORTS:
        try:
            importlib.import_module(mod_path)
            print(f"  [OK]   {mod_path}")
            ok_count += 1
        except Exception as e:
            print(f"  [FAIL] {mod_path} — {str(e)[:60]}")
            fail_count += 1

    print(f"\n  汇总: {ok_count} OK / {fail_count} FAIL")
    return ok_count, fail_count


def check_data_sources() -> Tuple[int, int]:
    """检查5: 数据源连接器状态"""
    print_header("[5/7] 数据源连接器状态")
    ok_count, fail_count = 0, 0
    try:
        from utils.data_provider import MarketDataProvider
        provider = MarketDataProvider()
        health = provider.source_health
        for source, status in health.items():
            state = "OK" if status.get("ok") else "WARN"
            err = status.get("last_error", "")
            if err and not status.get("ok"):
                print(f"  [{state}] {source:15s} — {str(err)[:50]}")
            else:
                print(f"  [{state}] {source:15s}")
            ok_count += 1

        # 扩展模块状态
        ext = provider.get_extended_status()
        for k, v in ext.items():
            if k == "cache":
                continue
            state = "OK" if v else "WARN"
            print(f"  [{state}] {k}")
            ok_count += 1
    except Exception as e:
        print(f"  [FAIL] 数据源检查失败: {e}")
        fail_count += 1
    print(f"\n  汇总: {ok_count} OK / {fail_count} FAIL")
    return ok_count, fail_count


def check_scheduled_tasks() -> Tuple[int, int]:
    """检查6: Windows 任务计划注册状态"""
    print_header("[6/7] Windows 任务计划注册状态")
    expected_tasks = [
        "v75_PreMarket",
        "v75_PostMarket",
        "v75_DailyPnlReport",
    ]
    ok_count, fail_count = 0, 0
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=10,
            encoding="gbk", errors="ignore"
        )
        output = result.stdout or ""
        for task in expected_tasks:
            if task in output:
                print(f"  [OK]   {task}")
                ok_count += 1
            else:
                print(f"  [WARN] {task} — 未注册 (可能需要运行 register_all_scheduled_tasks.ps1)")
                fail_count += 1
    except Exception as e:
        print(f"  [WARN] 无法查询任务计划: {e}")
        fail_count += len(expected_tasks)
    print(f"\n  汇总: {ok_count} OK / {fail_count} FAIL")
    return ok_count, fail_count


def check_dry_run() -> Tuple[int, int]:
    """检查7: dry-run 模拟启动测试"""
    print_header("[7/7] dry-run 模拟启动测试")
    ok_count, fail_count = 0, 0
    ram_script = V75_DIR / "run_all_modules.py"
    if not ram_script.exists():
        print(f"  [FAIL] run_all_modules.py 不存在")
        return 0, 1

    print(f"  执行: {EXPECTED_PYTHON} run_all_modules.py --phase all --dry-run")
    print(f"  (模拟启动所有模块, 不实际执行交易)\n")
    try:
        result = subprocess.run(
            [EXPECTED_PYTHON, str(ram_script), "--phase", "all", "--dry-run"],
            capture_output=True, text=True, timeout=60,
            cwd=str(V75_DIR),
            encoding="utf-8", errors="ignore"
        )
        # 输出日志
        lines = (result.stdout or "").split("\n")
        for line in lines:
            if line.strip():
                print(f"  {line.rstrip()}")

        if result.returncode == 0:
            print(f"\n  [OK] dry-run 退出码=0")
            ok_count += 1
        else:
            print(f"\n  [FAIL] dry-run 退出码={result.returncode}")
            fail_count += 1
            # 输出 stderr
            if result.stderr:
                for line in result.stderr.strip().split("\n")[-5:]:
                    print(f"  stderr: {line}")
    except subprocess.TimeoutExpired:
        print(f"  [FAIL] dry-run 超时 (60s)")
        fail_count += 1
    except Exception as e:
        print(f"  [FAIL] dry-run 异常: {e}")
        fail_count += 1
    print(f"\n  汇总: {ok_count} OK / {fail_count} FAIL")
    return ok_count, fail_count


def main():
    parser = argparse.ArgumentParser(description="自动交易计划启动检查")
    parser.add_argument("--dry-run", action="store_true",
                        help="包含 dry-run 模拟启动测试")
    args = parser.parse_args()

    print_header("自动交易计划启动模块检查")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  项目: {PROJECT_ROOT}")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  期望Python: {EXPECTED_PYTHON}")

    total_ok, total_fail = 0, 0

    ok, fail = check_python_path()
    total_ok += ok; total_fail += fail

    ok, fail = check_module_list()
    total_ok += ok; total_fail += fail

    ok, fail = check_scripts_exist()
    total_ok += ok; total_fail += fail

    ok, fail = check_critical_imports()
    total_ok += ok; total_fail += fail

    ok, fail = check_data_sources()
    total_ok += ok; total_fail += fail

    ok, fail = check_scheduled_tasks()
    total_ok += ok; total_fail += fail

    if args.dry_run:
        ok, fail = check_dry_run()
        total_ok += ok; total_fail += fail
    else:
        print_header("[7/7] dry-run 模拟启动测试 (跳过, 使用 --dry-run 启用)")

    # 总结
    print_header("总结")
    status = "✅ 全部通过" if total_fail == 0 else f"⚠️ {total_fail} 项失败"
    print(f"  通过: {total_ok}")
    print(f"  失败: {total_fail}")
    print(f"  状态: {status}")
    print(f"  自动交易计划启动就绪: {'是' if total_fail == 0 else '否'}")
    print("=" * 70)

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
