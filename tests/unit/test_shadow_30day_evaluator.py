"""Shadow30DayEvaluator 单元测试 — 对应 utils/shadow_30day_evaluator.py.

聚焦评估器核心判定路径:
    1. 空记录失败判定
    2. MVSK 通过/失败路径 (Δ夏普, 换仓成本)
    3. qlib 通过/失败路径 (方向一致率, 信号差异)
    4. 综合报告 overall_pass 与 Markdown 渲染

说明: 与 test_shadow_30day_unit.py (启动器全量) 互补, 本文件按
      TDD Guard 一一对应规则命名, 覆盖评估器公共契约。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _write_jsonl(filepath: Path, records: list[dict]) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _mvsk_record(date: str, diff: float) -> dict:
    return {
        "date": date,
        "timestamp": f"{date}T10:00:00",
        "weight_diff_l2": diff,
        "window": 378,
    }


def _qlib_record(date: str, qlib_sig: float, v9_sig: float) -> dict:
    return {
        "date": date,
        "symbol": "510300",
        "qlib_signal": qlib_sig,
        "v9_signal": v9_sig,
        "signal_diff": qlib_sig - v9_sig,
        "model": "qlib_lgb_v2",
    }


class TestEvaluator:
    """评估器公共契约测试."""

    def test_empty_records_fails(self, tmp_path: Path) -> None:
        """空记录 → 全部失败判定."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [])
        _write_jsonl(qlib, [])

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib)

        assert report.mvsk.records_count == 0
        assert report.qlib.records_count == 0
        assert not report.overall_pass
        assert report.summary.startswith("验证未通过")

    def test_missing_file_treated_empty(self, tmp_path: Path) -> None:
        """文件不存在 → 空记录, 不抛异常."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        report = Shadow30DayEvaluator().evaluate(
            tmp_path / "no_mvsk.jsonl", tmp_path / "no_qlib.jsonl"
        )
        assert report.mvsk.records_count == 0
        assert not report.overall_pass

    def test_mvsk_pass_path(self, tmp_path: Path) -> None:
        """小且稳定的 weight_diff → Δ夏普>0, 换仓成本<阈值, 通过."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [_mvsk_record("2026-08-21", 0.005) for _ in range(10)])
        _write_jsonl(
            qlib,
            [_qlib_record(f"2026-08-2{d}", 0.3, 0.2) for d in range(1, 10)],
        )

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib)

        assert report.mvsk.pass_
        assert report.mvsk.delta_sharpe > 0
        assert report.mvsk.turnover_cost < 0.02
        assert report.overall_pass

    def test_mvsk_fail_on_large_turnover(self, tmp_path: Path) -> None:
        """大 weight_diff → 换仓成本超阈值, 失败."""
        from utils.shadow_30day_evaluator import (
            MVSK_TURNOVER_THRESHOLD,
            Shadow30DayEvaluator,
        )

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        big_diff = MVSK_TURNOVER_THRESHOLD / 0.5 + 0.05
        _write_jsonl(mvsk, [_mvsk_record("2026-08-21", big_diff)])
        _write_jsonl(
            qlib,
            [_qlib_record("2026-08-21", 0.3, 0.2)],
        )

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib)

        assert not report.mvsk.pass_
        assert any("换仓成本" in r for r in report.mvsk.fail_reasons)

    def test_qlib_direction_agreement(self, tmp_path: Path) -> None:
        """方向一致率计算 — 同向记录应趋近 1.0."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [_mvsk_record("2026-08-21", 0.005)])
        _write_jsonl(
            qlib,
            [_qlib_record(f"2026-08-2{d}", 0.3, 0.2) for d in range(1, 10)],
        )

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib)

        assert report.qlib.direction_agreement_rate == pytest.approx(1.0)
        assert report.qlib.delta_sharpe > 0

    def test_qlib_large_signal_diff_fails(self, tmp_path: Path) -> None:
        """信号差异超阈值 → qlib 失败."""
        from utils.shadow_30day_evaluator import (
            QLIB_SIGNAL_DIFF_THRESHOLD,
            Shadow30DayEvaluator,
        )

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [_mvsk_record("2026-08-21", 0.005)])
        big = QLIB_SIGNAL_DIFF_THRESHOLD + 0.1
        _write_jsonl(qlib, [_qlib_record("2026-08-21", big, 0.0)])

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib)

        assert not report.qlib.pass_
        assert any("信号差异" in r for r in report.qlib.fail_reasons)

    def test_fail_fast_from_status(self, tmp_path: Path) -> None:
        """status 文件触发 fail-fast → overall 失败."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        status = tmp_path / "status.json"
        _write_jsonl(mvsk, [_mvsk_record("2026-08-21", 0.005)])
        _write_jsonl(qlib, [_qlib_record("2026-08-21", 0.3, 0.2)])
        status.write_text(
            json.dumps(
                {"fail_fast_triggered": True, "fail_fast_reason": "连续 3 天亏损"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib, status)

        assert report.fail_fast_triggered
        assert not report.overall_pass
        assert "fail-fast" in report.summary

    def test_markdown_renders(self, tmp_path: Path) -> None:
        """Markdown 报告包含关键判定字段."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [_mvsk_record("2026-08-21", 0.005)])
        _write_jsonl(qlib, [_qlib_record("2026-08-21", 0.3, 0.2)])

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib)
        md = report.to_markdown()

        assert "Shadow 30 天验证评估报告" in md
        assert "MVSK P5-2" in md
        assert "qlib_lgb_v2" in md
        assert "总体判定" in md
        assert "Δ夏普" in md
