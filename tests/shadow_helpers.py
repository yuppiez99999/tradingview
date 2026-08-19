"""Shadow 数据质量闭环测试辅助函数 — 供 unit + integration 测试共享.

提供:
    - 数据生成: make_real_records / make_mixed_records / make_progress_dict / make_mock_drift_report
    - 文件操作: write_cleaned_jsonl / write_progress_json / read_jsonl

关联文档: cairn/shadow-data-quality-loop.md
关联测试: tests/unit/test_shadow_threshold_gates.py / tests/integration/test_shadow_two_layer_integration.py
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# ============================================================
# 数据生成
# ============================================================


def make_real_records(n: int, start_date: str = "2026-07-23") -> list[dict[str, Any]]:
    """生成 n 条 quality=="real" 的测试记录.

    Args:
        n: 记录数
        start_date: 起始日期 (YYYY-MM-DD)

    Returns:
        记录列表, 每条含 date / daily_return / quality / source 字段
    """
    base = date.fromisoformat(start_date)
    records = []
    for i in range(n):
        d = base + timedelta(days=i)
        # 生成不同收益率, 避免全零导致 PSI=0
        ret = 0.001 * ((i % 7) - 3)  # -0.003 ~ +0.003 循环
        records.append({
            "date": d.isoformat(),
            "daily_return": ret,
            "quality": "real",
            "source": "w13a_real_market_feed",
        })
    return records


def make_mixed_records(
    real_n: int,
    backtest_n: int,
    start_date: str = "2026-07-23",
) -> list[dict[str, Any]]:
    """生成混合质量记录 (real + backtest).

    Args:
        real_n: real 记录数
        backtest_n: backtest 记录数
        start_date: 起始日期

    Returns:
        混合记录列表
    """
    records = make_real_records(real_n, start_date)
    base = date.fromisoformat(start_date) + timedelta(days=real_n)
    for i in range(backtest_n):
        d = base + timedelta(days=i)
        records.append({
            "date": d.isoformat(),
            "daily_return": 0.002 * i,
            "quality": "backtest",
            "source": "backtest_backfill",
        })
    return records


def make_progress_dict(
    days_completed: int,
    start_date: str = "2026-07-23",
) -> dict[str, Any]:
    """生成 observation_progress.json 的模拟内容.

    Args:
        days_completed: 已完成天数
        start_date: 观察期起始日期

    Returns:
        模拟的 progress 字典
    """
    return {
        "collected_at": "2026-08-03 12:00:00",
        "observation": {
            "days_completed": days_completed,
            "required_days": 14,
            "progress_pct": min(100.0, days_completed / 14 * 100),
            "samples_collected": days_completed,
            "min_samples": 20,
            "ready_for_phase_b": days_completed >= 20,
            "start_date": start_date,
            "estimated_completion": "2026-08-13",
        },
    }


def make_mock_drift_report() -> MagicMock:
    """创建模拟的 DriftReport 对象.

    Returns:
        MagicMock, 模拟 DriftReport 的 to_dict() / severity.value 等接口
    """
    report = MagicMock()
    report.severity.value = "low"
    report.drift_score = 0.15
    report.psi = 0.08
    report.baseline_mean = 0.001
    report.current_mean = 0.002
    report.to_dict.return_value = {
        "timestamp": "2026-08-03T00:00:00Z",
        "model_name": "v9_lgb",
        "model_version": "observation_period",
        "feature_name": "__prediction__",
        "drift_score": 0.15,
        "psi": 0.08,
        "severity": "low",
        "baseline_mean": 0.001,
        "current_mean": 0.002,
        "baseline_size": 8,
        "current_size": 6,
        "owner": "",
        "runbook_url": "docs/runbooks/MODEL_DRIFT_RUNBOOK.md",
    }
    return report


# ============================================================
# 文件操作
# ============================================================


def write_cleaned_jsonl(file_path: Path, records: list[dict[str, Any]]) -> None:
    """写入 daily_returns_cleaned.jsonl.

    Args:
        file_path: 文件路径
        records: 记录列表
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_progress_json(
    file_path: Path,
    days_completed: int,
    start_date: str = "2026-07-23",
) -> None:
    """写入 observation_progress.json.

    Args:
        file_path: 文件路径
        days_completed: 已完成天数
        start_date: 观察期起始日期
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    progress = make_progress_dict(days_completed, start_date)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def read_jsonl(file_path: Path) -> list[dict[str, Any]]:
    """读取 jsonl 文件, 返回记录列表.

    Args:
        file_path: 文件路径

    Returns:
        记录列表, 文件不存在返回空列表
    """
    if not file_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records
