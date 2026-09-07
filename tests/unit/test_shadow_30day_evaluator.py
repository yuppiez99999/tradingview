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

    def test_qlib_archived_overall_only_mvsk(self, tmp_path: Path) -> None:
        """R-6 归档: qlib_archived=True → overall_pass 只由 MVSK + fail-fast 决定.

        即使 qlib 记录本身差异超阈值 (旧语义下会 FAIL), 归档后亦不参与判定.
        """
        from utils.shadow_30day_evaluator import (
            QLIB_SIGNAL_DIFF_THRESHOLD,
            Shadow30DayEvaluator,
        )

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [_mvsk_record("2026-09-15", 0.005) for _ in range(10)])
        # qlib 记录含超阈值信号差异 → 若参与判定必 FAIL
        big = QLIB_SIGNAL_DIFF_THRESHOLD + 0.1
        _write_jsonl(qlib, [_qlib_record("2026-09-15", big, 0.0)])

        report = Shadow30DayEvaluator().evaluate(
            mvsk,
            qlib,
            window_start="2026-09-13",
            window_end="2026-10-12",
            qlib_archived=True,
        )

        assert report.qlib_archived
        assert report.mvsk.pass_
        assert not report.qlib.pass_  # 归档态不评估 → 保持默认 False
        assert report.overall_pass
        assert "MVSK P5-2 通过" in report.summary

    def test_qlib_archived_qlib_fail_does_not_block(self, tmp_path: Path) -> None:
        """R-6 归档: qlib 维度即使失败 (旧语义) 也不阻断 overall."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [_mvsk_record("2026-09-15", 0.005) for _ in range(10)])
        _write_jsonl(qlib, [])  # 无 qlib 记录 (归档后不再产出)

        report = Shadow30DayEvaluator().evaluate(
            mvsk,
            qlib,
            qlib_archived=True,
        )

        assert report.overall_pass
        assert report.qlib.records_count == 0
        # summary 不出现 "qlib 未通过" 归因
        assert "qlib 未通过" not in report.summary

    def test_window_start_filters_preheat_records(self, tmp_path: Path) -> None:
        """R-6 窗口过滤: window_start=09-13 时, 09-07~09-12 预热记录不计入.

        预热记录仍保留在 jsonl (此处写入) 但不进评估计数 — ROADMAP
        "窗口 09-13~10-12" 口径.
        """
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        records = [
            _mvsk_record("2026-09-08", 0.005),  # 预热 (窗内起点前)
            _mvsk_record("2026-09-11", 0.005),  # 预热
            _mvsk_record("2026-09-13", 0.005),  # 正式样本起
            _mvsk_record("2026-09-16", 0.005),  # 正式样本
            _mvsk_record("2026-10-12", 0.005),  # 正式样本 (上界含当日)
            _mvsk_record("2026-10-13", 0.005),  # 上界后 → 排除
        ]
        _write_jsonl(mvsk, records)
        _write_jsonl(qlib, [])

        report = Shadow30DayEvaluator().evaluate(
            mvsk,
            qlib,
            window_start="2026-09-13",
            window_end="2026-10-12",
            qlib_archived=True,
        )

        # 6 条原始记录, 窗内计 3 条 (09-13/09-16/10-12), 预热与上界后排除
        assert report.mvsk.records_count == 3
        assert report.actual_days == 3
        assert report.window_label == "2026-09-13 ~ 2026-10-12"
        assert report.mvsk.pass_

    def test_window_label_fallback_status_start(self, tmp_path: Path) -> None:
        """未显式传窗口时回退 status.start_date (状态首触发日) 过滤."""
        import json as _json

        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        status = tmp_path / "status.json"
        _write_jsonl(
            mvsk,
            [
                _mvsk_record("2026-09-06", 0.005),  # status.start_date 前
                _mvsk_record("2026-09-07", 0.005),  # == start_date
                _mvsk_record("2026-09-08", 0.005),
            ],
        )
        _write_jsonl(qlib, [])
        status.write_text(
            _json.dumps({"start_date": "2026-09-07", "end_date": "2026-10-12"}),
            encoding="utf-8",
        )

        report = Shadow30DayEvaluator().evaluate(mvsk, qlib, status, qlib_archived=True)

        assert report.mvsk.records_count == 2
        assert report.window_label == "自 2026-09-07 起"
        assert report.mvsk.pass_

    def test_markdown_archived_marks_qlib(self, tmp_path: Path) -> None:
        """归档态 Markdown: qlib 章节改为归档说明, 不渲染误导性指标表."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk = tmp_path / "mvsk.jsonl"
        qlib = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk, [_mvsk_record("2026-09-15", 0.005) for _ in range(5)])
        _write_jsonl(qlib, [])

        report = Shadow30DayEvaluator().evaluate(
            mvsk,
            qlib,
            window_start="2026-09-13",
            window_end="2026-10-12",
            qlib_archived=True,
        )
        md = report.to_markdown()

        assert "评估样本窗" in md
        assert "R-6 已停跑归档" in md
        assert "参与判定 | 否" in md
        # 归档态不应渲染 qlib 指标表 (无 qlib_signal_diff 字段等)
        assert "信号差异标准差" not in md
