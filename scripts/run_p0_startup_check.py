#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 启动自检 - 命令行入口
=========================
用途: 在任何交易系统工作流启动前,统一执行 8 大类健康检查,
      在错误进入工作流前拦截,从源头减少 bug 传播。

推荐用法:
    1. 手动运行 (盘前):
        python scripts/run_p0_startup_check.py

    2. 严格模式 (WARN FAIL 也算失败):
        python scripts/run_p0_startup_check.py --strict

    3. 跳过数据源 (快速检查,适合盘后):
        python scripts/run_p0_startup_check.py --skip-datasource

    4. JSON 报告 (供其他程序消费):
        python scripts/run_p0_startup_check.py --json

    5. 报告归档 (自动保存到 reports/system_check/):
        python scripts/run_p0_startup_check.py --archive

退出码:
    0 = 全部通过,可进入工作流
    1 = 存在阻止性失败,必须人工干预
    2 = 自检脚本异常 (非业务失败)

集成示例 (在入口点开头):
    # Python 入口
    from utils.system_check import assert_system_ready
    assert_system_ready()  # 失败立即退出

    # PowerShell 脚本
    python scripts/run_p0_startup_check.py
    if ($LASTEXITCODE -ne 0) { exit 1 }
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# 项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.system_check import SystemChecker, run_system_check


def archive_report(report_json: str, check_time: str) -> Path:
    """将自检报告归档到 reports/system_check/"""
    archive_dir = PROJECT_ROOT / "reports" / "system_check"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 文件名格式: system_check_YYYYMMDD_HHMMSS.json
    ts = datetime.fromisoformat(check_time).strftime("%Y%m%d_%H%M%S")
    archive_path = archive_dir / f"system_check_{ts}.json"

    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(report_json)

    # 保留最近 30 份
    archives = sorted(archive_dir.glob("system_check_*.json"))
    if len(archives) > 30:
        for old in archives[:-30]:
            try:
                old.unlink()
            except Exception:
                pass

    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P0 启动自检 - 在工作流启动前拦截错误"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="严格模式: WARN FAIL 也算阻止性失败"
    )
    parser.add_argument(
        "--skip-datasource", action="store_true",
        help="跳过数据源连通性检查 (加速启动,但降低覆盖度)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="输出 JSON 格式报告 (供其他程序消费)"
    )
    parser.add_argument(
        "--archive", action="store_true",
        help="将报告归档到 reports/system_check/ (保留最近 30 份)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="安静模式: 只输出失败项与最终结论"
    )
    args = parser.parse_args()

    # 运行自检
    report = run_system_check(
        strict=args.strict,
        skip_datasource=args.skip_datasource,
        output_json=args.json,
    )

    # 归档
    if args.archive and not args.json:
        report_json = SystemChecker.report_to_json(report)
        archive_path = archive_report(report_json, report.check_time)
        if not args.quiet:
            print(f"\n📁 报告已归档: {archive_path}")

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
