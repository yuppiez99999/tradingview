#!/usr/bin/env python
"""
影子账户 5 日 dry_run 验证脚本
==============================

模拟 Phase 4.5 影子账户 5 日灰度验证流程：
1. 每日运行完整闭环 (dry_run 模式)
2. 收集各阶段指标
3. 汇总分析报告
4. 验证稳定性与 fail-closed 机制

用法:
    python scripts/shadow_account_dry_run.py          # 5 日模拟
    python scripts/shadow_account_dry_run.py --days=3  # 自定义天数
    python scripts/shadow_account_dry_run.py --verbose   # 详细日志

输出:
    reports/pipeline/dry_run_report_{timestamp}.json   # 汇总报告
    reports/pipeline/dry_run_day_{n}_{timestamp}.json   # 每日明细
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.pipeline import PipelineOrchestrator


def setup_logging(level: str = "INFO") -> None:
    """配置日志"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 降低第三方库日志级别
    logging.getLogger("pipeline").setLevel(
        getattr(logging, level.upper(), logging.INFO)
    )


def run_single_day(
    day: int,
    total_days: int,
    orchestrator: PipelineOrchestrator,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    执行单日 dry_run 验证

    Args:
        day: 当前天数 (1-based)
        total_days: 总天数
        orchestrator: 流水线编排器实例
        verbose: 是否输出详细日志

    Returns:
        该日运行指标
    """
    date_label = (now_bj() + timedelta(days=day - 1)).strftime("%Y-%m-%d")
    sim_date = (now_bj() + timedelta(days=day - 1)).strftime("%Y%m%d")

    print(f"\n{'='*60}")
    print(f"  第 {day}/{total_days} 日 | 模拟日期: {date_label}")
    print(f"{'='*60}")

    start_time = time.time()

    # 运行完整闭环
    result = orchestrator.run_full_cycle(mode="dry_run")

    elapsed = time.time() - start_time

    # 收集指标
    day_metrics: dict[str, Any] = {
        "day": day,
        "sim_date": sim_date,
        "status": "PASS" if result.success else "FAIL",
        "stage": result.stage.value,
        "duration_seconds": round(elapsed, 3),
        "duration_ms": round(result.duration_ms, 2),
        "error": result.error,
        "metrics": result.metrics,
    }

    # 输出摘要
    status_icon = "✅" if result.success else "❌"
    print(f"  {status_icon} 状态: {result.stage.value} | 耗时: {elapsed:.2f}s")
    print(
        f"  📊 数据清洗: {result.metrics.get('data_cleaning', {}).get('reports_count', 0)} 只标的"
    )
    print(
        f"  📊 Alpha 信号: {result.metrics.get('alpha', {}).get('signals_count', 0)} 只标的"
    )
    print(
        f"  📊 回测验证: {'通过' if result.metrics.get('backtest', {}).get('passed') else '跳过/未通过'}"
    )
    print(
        f"  📊 执行: {result.metrics.get('execution', {}).get('total_orders', 0)} 订单"
    )

    if verbose and result.metrics:
        print("\n  📋 详细指标:")
        print(f"    {json.dumps(result.metrics, ensure_ascii=False, indent=4)}")

    return day_metrics


def generate_summary_report(
    all_days: list[dict[str, Any]],
    report_dir: Path,
    timestamp: str,
) -> dict[str, Any]:
    """
    生成 5 日汇总报告

    Args:
        all_days: 每日运行指标列表
        report_dir: 报告输出目录
        timestamp: 时间戳

    Returns:
        汇总报告
    """
    # 统计
    total = len(all_days)
    passed = sum(1 for d in all_days if d["status"] == "PASS")
    failed = total - passed
    avg_duration = (
        sum(d["duration_seconds"] for d in all_days) / total if total > 0 else 0
    )
    total_duration = sum(d["duration_seconds"] for d in all_days)

    # 阶段通过率
    stage_counts: dict[str, int] = {}
    for d in all_days:
        stage = d["stage"]
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    # 指标汇总
    avg_signals = []
    avg_orders = []
    for d in all_days:
        m = d.get("metrics", {})
        avg_signals.append(m.get("alpha", {}).get("signals_count", 0))
        avg_orders.append(m.get("execution", {}).get("total_orders", 0))

    summary: dict[str, Any] = {
        "report_title": "影子账户 5 日 dry_run 验证报告",
        "generated_at": now_bj().isoformat(),
        "total_days": total,
        "passed_days": passed,
        "failed_days": failed,
        "pass_rate": f"{passed / total * 100:.1f}%" if total > 0 else "N/A",
        "avg_duration_seconds": round(avg_duration, 3),
        "total_duration_seconds": round(total_duration, 3),
        "stage_distribution": stage_counts,
        "avg_signals_per_day": (
            round(sum(avg_signals) / len(avg_signals), 1) if avg_signals else 0
        ),
        "avg_orders_per_day": (
            round(sum(avg_orders) / len(avg_orders), 1) if avg_orders else 0
        ),
        "daily_results": all_days,
        "verdict": "✅ 通过" if failed == 0 else f"⚠️ {failed}/{total} 天失败",
    }

    # 保存汇总报告
    report_path = report_dir / f"dry_run_report_{timestamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n📄 汇总报告已保存: {report_path}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="影子账户 5 日 dry_run 验证")
    parser.add_argument("--days", type=int, default=5, help="模拟天数 (默认: 5)")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    setup_logging(args.log_level)
    timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
    report_dir = PROJECT_ROOT / "reports" / "pipeline"

    print(f"\n{'='*60}")
    print("  影子账户 dry_run 验证")
    print(f"  天数: {args.days} 日 | 模式: dry_run")
    print(f"  开始时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 初始化编排器
    orchestrator = PipelineOrchestrator()

    # 执行每日验证
    all_days: list[dict[str, Any]] = []
    for day in range(1, args.days + 1):
        day_metrics = run_single_day(
            day=day,
            total_days=args.days,
            orchestrator=orchestrator,
            verbose=args.verbose,
        )

        # 保存每日明细
        day_path = report_dir / f"dry_run_day_{day}_{timestamp}.json"
        day_path.parent.mkdir(parents=True, exist_ok=True)
        with open(day_path, "w", encoding="utf-8") as f:
            json.dump(day_metrics, f, ensure_ascii=False, indent=2)

        all_days.append(day_metrics)

        # 日间间隔 (模拟不同交易日)
        if day < args.days:
            print("  ⏳ 等待 1 秒进入下一日...")
            time.sleep(1)

    # 生成汇总报告
    summary = generate_summary_report(all_days, report_dir, timestamp)

    # 输出最终结果
    print(f"\n{'='*60}")
    print("  验证完成")
    print(f"  通过: {summary['passed_days']}/{summary['total_days']} 天")
    print(f"  平均耗时: {summary['avg_duration_seconds']}s")
    print(f"  总耗时: {summary['total_duration_seconds']}s")
    print(f"  判定: {summary['verdict']}")
    print(f"{'='*60}")

    return 0 if summary["failed_days"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
