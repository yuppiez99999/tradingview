#!/usr/bin/env python
"""
_detect_coverage_stagnation.py — 覆盖率提升停滞检测器

检测连续 N 次补测提升是否 < 阈值, 停滞则告警。
告警时评估难测分支 (LLM 调用 / 网络 IO 等不可测路径), 不强行堆砌。
停滞记录持久化到 reports/ci/coverage_stagnation_log.jsonl。

用法:
    python scripts/_detect_coverage_stagnation.py [--series 0.68,0.69,0.70] [--threshold 0.01]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_STAGNATION_LOG = _ROOT / "reports" / "ci" / "coverage_stagnation_log.jsonl"


def detect_stagnation(
    line_rate_series: list[float], threshold: float = 0.01, window: int = 3
) -> bool:
    """检测覆盖率提升是否停滞.

    连续 window 次补测提升 < threshold 则判定停滞。

    Args:
        line_rate_series: 覆盖率序列 (按时间顺序, 最早在前)
        threshold: 停滞阈值 (每次提升 < 此值视为无显著提升)
        window: 连续无显著提升的次数

    Returns:
        True = 停滞 (需告警), False = 未停滞
    """
    if len(line_rate_series) < window + 1:
        return False  # 样本不足

    # 计算每次提升 (相邻差值)
    deltas = [
        line_rate_series[i] - line_rate_series[i - 1]
        for i in range(1, len(line_rate_series))
    ]
    # 检查最近 window 次提升是否都 < threshold
    recent_deltas = deltas[-window:]
    return all(d < threshold for d in recent_deltas)


def log_stagnation(series: list[float], threshold: float, window: int) -> Path:
    """将停滞记录追加到 coverage_stagnation_log.jsonl."""
    _STAGNATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "series": series,
        "threshold": threshold,
        "window": window,
        "recent_deltas": [series[i] - series[i - 1] for i in range(1, len(series))][
            -window:
        ],
    }
    with _STAGNATION_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return _STAGNATION_LOG


def main() -> int:
    parser = argparse.ArgumentParser(description="覆盖率提升停滞检测器")
    parser.add_argument(
        "--series",
        type=str,
        default="",
        help="覆盖率序列, 逗号分隔 (如 0.68,0.69,0.70)",
    )
    parser.add_argument("--threshold", type=float, default=0.01)
    parser.add_argument("--window", type=int, default=3)
    args = parser.parse_args()

    if not args.series:
        print("未提供覆盖率序列 (--series), 跳过")
        return 0

    try:
        series = [float(x.strip()) for x in args.series.split(",") if x.strip()]
    except ValueError as exc:
        print(f"序列解析失败: {exc}")
        return 1

    is_stagnant = detect_stagnation(series, args.threshold, args.window)
    if is_stagnant:
        log_path = log_stagnation(series, args.threshold, args.window)
        print(
            f"[STAGNATION] 覆盖率提升停滞: 最近 {args.window} 次提升 < {args.threshold}"
        )
        print(f"  序列: {series}")
        print(f"  日志: {log_path.name}")
        print("  建议: 评估难测分支 (LLM/网络IO), 不强行堆砌")
        return 1
    print(f"覆盖率未停滞 ✓ (序列: {series})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
