"""统一健康度报告 CLI — 三层面自我进化 Stage 1.

用法:
    python scripts/run_health_report.py                    # 采集一次并打印报告
    python scripts/run_health_report.py --json             # JSON 格式输出
    python scripts/run_health_report.py --history 7        # 显示最近 7 天历史
    python scripts/run_health_report.py --compare 2026-07-30  # 与指定日期对比

Feature Flag: USE_UNIFIED_HEALTH_METRICS (默认 False, HC-1)
配置: evolution.yaml (走 ConfigManager, HC-5)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def format_layer_score(layer_name: str, score) -> str:
    """格式化单层评分."""
    status = "⚠️ 降级" if score.is_degraded else "✅ 正常"
    lines = [
        f"  {layer_name:10s} | 得分: {score.score:.4f} | {status}",
    ]
    if score.is_degraded and score.degraded_reason:
        lines.append(f"             | 原因: {score.degraded_reason}")
    if score.sub_metrics:
        metrics_str = " | ".join(f"{k}={v:.2f}" for k, v in score.sub_metrics.items())
        lines.append(f"             | 子指标: {metrics_str}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="三层面统一健康度报告 (Stage 1)")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument(
        "--history", type=int, default=0, help="显示最近 N 天历史 (默认 0=不显示)"
    )
    parser.add_argument(
        "--compare", type=str, default="", help="与指定日期对比 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--skip-system-check", action="store_true", help="跳过 P0 自检 (仅紧急情况)"
    )
    args = parser.parse_args()

    from utils.alpha.health_metrics import UnifiedHealthMetrics

    metrics = UnifiedHealthMetrics()

    # JSON 模式
    if args.json:
        report = metrics.collect_all()
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0

    # 历史模式
    if args.history > 0:
        history = metrics.get_history(args.history)
        print(f"\n{'='*70}")
        print(f"最近 {args.history} 天健康度历史 ({len(history)} 条记录)")
        print(f"{'='*70}")
        for h in history:
            trend = h.trend_vs_yesterday
            trend_str = f"趋势: {'+' if trend >= 0 else ''}{trend:.4f}"
            print(
                f"  {h.generated_at[:19]} | 总分: {h.overall_score:.4f} | {trend_str}"
            )
        return 0

    # 默认: 采集并打印报告
    print(f"\n{'='*70}")
    print("三层面统一健康度报告 (Stage 1)")
    print(f"{'='*70}")

    status = metrics.get_status()
    print("\n系统状态:")
    print(f"  Feature Flag: {status['feature_flag']} = {status['enabled']}")
    print(f"  权重: {status['weights']}")
    print(f"  历史路径: {status['history_path']}")

    report = metrics.collect_all()

    print(f"\n报告时间: {report.generated_at}")
    print(f"\n{'─'*70}")
    print(
        f"综合健康度: {report.overall_score:.4f} {'⚠️ 降级' if report.is_degraded else '✅'}"
    )
    print(f"样本数: {report.sample_count}")
    print(
        f"趋势(vs昨天): {'+' if report.trend_vs_yesterday >= 0 else ''}{report.trend_vs_yesterday:.4f}"
    )
    print(
        f"趋势(vs上周): {'+' if report.trend_vs_last_week >= 0 else ''}{report.trend_vs_last_week:.4f}"
    )

    if report.degraded_layers:
        print(f"降级层面: {', '.join(report.degraded_layers)}")

    print(f"\n{'─'*70}")
    print("各层面详情:")
    print(f"{'─'*70}")
    for layer_name in ("code", "strategy", "ops"):
        if layer_name in report.layer_scores:
            print(format_layer_score(layer_name, report.layer_scores[layer_name]))
            print()

    # 对比模式
    if args.compare:
        print(f"{'─'*70}")
        print(f"与基线 {args.compare} 对比:")
        print(f"{'─'*70}")
        diff = metrics.compare_baseline(report, args.compare)
        if "error" in diff:
            print(f"  ❌ {diff.get('reason', '对比失败')}")
        else:
            for k, v in diff.items():
                sign = "+" if v >= 0 else ""
                print(f"  {k:20s}: {sign}{v:.4f}")

    print(f"\n{'='*70}")
    if report.is_degraded and not status["enabled"]:
        print("提示: Feature Flag 未启用, 报告为降级模式.")
        print(
            "      启用方式: 在 feature_flags.yaml 设置 USE_UNIFIED_HEALTH_METRICS=true (需双签)"
        )
    print(f"{'='*70}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
