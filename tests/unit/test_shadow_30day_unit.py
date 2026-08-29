"""Shadow 30 天验证单元测试 — W7.2.8 + W7.2.9.

测试覆盖:
    1. Shadow30DayEvaluator — 评估器核心逻辑
    2. launch_shadow_30day — 每日运行器核心函数
    3. fail-fast 监控集成
    4. Markdown 报告生成
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 辅助函数
# ============================================================


def _write_jsonl(filepath: Path, records: list[dict]) -> None:
    """写入 jsonl 测试文件."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _make_mvsk_record(
    date: str, weight_diff: float = 0.0, symbols: list[str] | None = None
) -> dict:
    """构造 MVSK shadow 记录."""
    if symbols is None:
        symbols = ["510300", "510500", "513100", "512890"]
    n = len(symbols)
    weights = {s: 1.0 / n for s in symbols}
    return {
        "date": date,
        "timestamp": f"{date}T10:00:00",
        "mvsk_weights": weights,
        "baseline_weights": weights,
        "weight_diff_l2": weight_diff,
        "gamma_s": 0.1,
        "gamma_k": 0.1,
        "window": 378,
    }


def _make_qlib_record(
    date: str,
    symbol: str = "510300",
    qlib_signal: float = 0.3,
    v9_signal: float = 0.2,
) -> dict:
    """构造 qlib shadow 记录."""
    return {
        "date": date,
        "timestamp": f"{date}T10:00:00",
        "symbol": symbol,
        "qlib_signal": qlib_signal,
        "v9_signal": v9_signal,
        "signal_diff": qlib_signal - v9_signal,
        "model": "qlib_lgb_v2",
        "sharpe_oos": {"train": 1.86, "test": 2.44},
    }


# ============================================================
# Shadow30DayEvaluator 测试
# ============================================================


class TestShadow30DayEvaluator:
    """评估器测试."""

    def test_empty_records(self, tmp_path: Path) -> None:
        """空记录评估 — 应失败."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk_path, [])
        _write_jsonl(qlib_path, [])

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path)

        assert report.mvsk.records_count == 0
        assert report.qlib.records_count == 0
        assert not report.mvsk.pass_
        assert not report.qlib.pass_
        assert not report.overall_pass
        assert "无 MVSK shadow 记录" in report.mvsk.fail_reasons
        assert "无 qlib shadow 记录" in report.qlib.fail_reasons

    def test_mvsk_pass(self, tmp_path: Path) -> None:
        """MVSK 评估通过 — Δ夏普 > 0 + 换仓成本 < 阈值."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        records = [
            _make_mvsk_record(f"2026-09-{d:02d}", weight_diff=0.001)
            for d in range(13, 43)
        ]
        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk_path, records)
        _write_jsonl(qlib_path, [_make_qlib_record("2026-09-13")])

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path)

        assert report.mvsk.records_count == 30
        assert report.mvsk.mean_weight_diff_l2 == pytest.approx(0.001)
        assert report.mvsk.delta_sharpe > 0
        assert report.mvsk.turnover_cost < 0.02
        assert report.mvsk.pass_

    def test_mvsk_fail_high_turnover(self, tmp_path: Path) -> None:
        """MVSK 评估失败 — 换仓成本过高."""
        from utils.shadow_30day_evaluator import (
            MVSK_TURNOVER_THRESHOLD,
            Shadow30DayEvaluator,
        )

        high_diff = MVSK_TURNOVER_THRESHOLD * 3
        records = [
            _make_mvsk_record(f"2026-09-{d:02d}", weight_diff=high_diff)
            for d in range(13, 20)
        ]
        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk_path, records)
        _write_jsonl(qlib_path, [])

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path)

        assert report.mvsk.turnover_cost >= MVSK_TURNOVER_THRESHOLD
        assert not report.mvsk.pass_
        assert any("换仓成本" in r for r in report.mvsk.fail_reasons)

    def test_qlib_pass(self, tmp_path: Path) -> None:
        """qlib 评估通过 — 方向一致率高."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        records = [
            _make_qlib_record(
                f"2026-09-{d:02d}",
                qlib_signal=0.3,
                v9_signal=0.2,
            )
            for d in range(13, 43)
        ]
        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk_path, [_make_mvsk_record("2026-09-13")])
        _write_jsonl(qlib_path, records)

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path)

        assert report.qlib.records_count == 30
        assert report.qlib.direction_agreement_rate == 1.0
        assert report.qlib.delta_sharpe > 0
        assert report.qlib.pass_

    def test_qlib_fail_low_agreement(self, tmp_path: Path) -> None:
        """qlib 评估 — 方向一致率低导致 Δ夏普折扣."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        records = []
        for d in range(13, 43):
            qlib_sig = 0.3 if d % 2 == 0 else -0.3
            v9_sig = 0.2 if d % 2 == 0 else 0.2
            records.append(
                _make_qlib_record(
                    f"2026-09-{d:02d}",
                    qlib_signal=qlib_sig,
                    v9_signal=v9_sig,
                )
            )
        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk_path, [])
        _write_jsonl(qlib_path, records)

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path)

        assert report.qlib.direction_agreement_rate == pytest.approx(0.5)
        assert report.qlib.delta_sharpe > 0

    def test_overall_pass(self, tmp_path: Path) -> None:
        """综合评估通过."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk_records = [
            _make_mvsk_record(f"2026-09-{d:02d}", weight_diff=0.0005)
            for d in range(13, 43)
        ]
        qlib_records = [
            _make_qlib_record(f"2026-09-{d:02d}", qlib_signal=0.3, v9_signal=0.25)
            for d in range(13, 43)
        ]
        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk_path, mvsk_records)
        _write_jsonl(qlib_path, qlib_records)

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path)

        assert report.mvsk.pass_
        assert report.qlib.pass_
        assert not report.fail_fast_triggered
        assert report.overall_pass

    def test_fail_fast_triggered(self, tmp_path: Path) -> None:
        """fail-fast 触发 → 总体失败."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        status_path = tmp_path / "status.json"
        _write_jsonl(mvsk_path, [_make_mvsk_record("2026-09-13")])
        _write_jsonl(qlib_path, [_make_qlib_record("2026-09-13")])
        status_path.write_text(
            json.dumps(
                {
                    "fail_fast_triggered": True,
                    "fail_fast_reason": "单日差异 0.05 > 阈值 0.03",
                }
            ),
            encoding="utf-8",
        )

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path, status_path)

        assert report.fail_fast_triggered
        assert not report.overall_pass

    def test_markdown_report(self, tmp_path: Path) -> None:
        """Markdown 报告生成."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        mvsk_path = tmp_path / "mvsk.jsonl"
        qlib_path = tmp_path / "qlib.jsonl"
        _write_jsonl(mvsk_path, [_make_mvsk_record("2026-09-13", weight_diff=0.001)])
        _write_jsonl(qlib_path, [_make_qlib_record("2026-09-13")])

        evaluator = Shadow30DayEvaluator()
        report = evaluator.evaluate(mvsk_path, qlib_path)
        md = report.to_markdown()

        assert "# Shadow 30 天验证评估报告" in md
        assert "MVSK P5-2" in md
        assert "qlib_lgb_v2" in md
        assert "Δ夏普" in md
        assert "通过条件" in md

    def test_mvsk_delta_sharpe_stability(self, tmp_path: Path) -> None:
        """MVSK Δ夏普 — 稳定差异 > 不稳定差异."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        evaluator = Shadow30DayEvaluator()

        stable = evaluator._estimate_mvsk_delta_sharpe(mean_diff=0.01, std_diff=0.001)
        unstable = evaluator._estimate_mvsk_delta_sharpe(mean_diff=0.01, std_diff=0.01)
        zero_diff = evaluator._estimate_mvsk_delta_sharpe(mean_diff=0.0, std_diff=0.0)

        assert stable > unstable
        assert zero_diff > 0

    def test_qlib_delta_sharpe_confidence(self, tmp_path: Path) -> None:
        """qlib Δ夏普 — 高一致率 > 低一致率."""
        from utils.shadow_30day_evaluator import Shadow30DayEvaluator

        evaluator = Shadow30DayEvaluator()

        high = evaluator._estimate_qlib_delta_sharpe(
            mean_diff=0.1, std_diff=0.01, agreement_rate=0.8
        )
        mid = evaluator._estimate_qlib_delta_sharpe(
            mean_diff=0.1, std_diff=0.01, agreement_rate=0.5
        )
        low = evaluator._estimate_qlib_delta_sharpe(
            mean_diff=0.1, std_diff=0.01, agreement_rate=0.2
        )

        assert high > mid > low > 0


# ============================================================
# launch_shadow_30day 测试
# ============================================================


class TestLaunchShadow30Day:
    """每日运行器测试."""

    def test_get_trade_date_default(self) -> None:
        """默认日期 = 今天."""
        from datetime import datetime

        from scripts.launch_shadow_30day import _get_trade_date

        result = _get_trade_date("")
        expected = datetime.now().strftime("%Y-%m-%d")
        assert result == expected

    def test_get_trade_date_specified(self) -> None:
        """指定日期."""
        from scripts.launch_shadow_30day import _get_trade_date

        assert _get_trade_date("2026-09-13") == "2026-09-13"

    def test_load_mid_layer_portfolio_default(self) -> None:
        """默认 4 ETF 组合."""
        from scripts.launch_shadow_30day import _load_mid_layer_portfolio

        portfolio = _load_mid_layer_portfolio("2026-09-13")

        assert portfolio.trade_date == "2026-09-13"
        mid_holdings = [h for h in portfolio.holdings if h.layer == "mid"]
        assert len(mid_holdings) == 4
        total_weight = sum(h.weight for h in mid_holdings)
        assert total_weight == pytest.approx(1.0)

    def test_count_jsonl_records(self, tmp_path: Path) -> None:
        """统计 jsonl 行数."""
        from scripts.launch_shadow_30day import _count_jsonl_records

        filepath = tmp_path / "test.jsonl"
        _write_jsonl(filepath, [{"a": 1}, {"b": 2}, {"c": 3}])
        assert _count_jsonl_records(filepath) == 3

    def test_count_jsonl_nonexistent(self) -> None:
        """不存在文件 → 0."""
        from scripts.launch_shadow_30day import _count_jsonl_records

        assert _count_jsonl_records(Path("/nonexistent/file.jsonl")) == 0

    def test_shadow_daily_result_defaults(self) -> None:
        """ShadowDailyResult 默认值."""
        from scripts.launch_shadow_30day import ShadowDailyResult

        r = ShadowDailyResult()
        assert r.mvsk_success is False
        assert r.qlib_success is False
        assert r.fail_fast_triggered is False
        assert r.days_remaining == 30

    def test_shadow_30day_status_defaults(self) -> None:
        """Shadow30DayStatus 默认值."""
        from scripts.launch_shadow_30day import Shadow30DayStatus

        s = Shadow30DayStatus()
        assert s.days_elapsed == 0
        assert s.days_remaining == 30
        assert s.fail_fast_triggered is False
        assert s.daily_results == []

    def test_run_daily_shadow_mvsk_disabled(self, tmp_path: Path, monkeypatch) -> None:
        """USE_MVSK_MID_LAYER=false → MVSK 跳过."""
        from scripts.launch_shadow_30day import run_daily_shadow

        monkeypatch.setenv("USE_MVSK_MID_LAYER", "false")
        monkeypatch.setenv("USE_QLIB_LGB_V2", "false")

        monkeypatch.chdir(tmp_path)
        result = run_daily_shadow("2026-09-13")

        assert result.mvsk_success is False
        assert "USE_MVSK_MID_LAYER=false" in result.mvsk_error
        assert result.qlib_success is False
        assert "USE_QLIB_LGB_V2=false" in result.qlib_error

    def test_check_fail_fast_normal(self) -> None:
        """fail-fast 正常不触发."""
        from scripts.launch_shadow_30day import ShadowDailyResult, _check_fail_fast

        daily = ShadowDailyResult(
            mvsk_weight_diff_l2=0.001,
            qlib_signal_diff=0.01,
        )
        triggered, reason = _check_fail_fast(daily)
        assert triggered is False
        assert reason == ""

    def test_check_fail_fast_triggered(self) -> None:
        """fail-fast 触发 — 差异过大."""
        from scripts.launch_shadow_30day import ShadowDailyResult, _check_fail_fast

        daily = ShadowDailyResult(
            mvsk_weight_diff_l2=0.05,
            qlib_signal_diff=0.01,
        )
        triggered, reason = _check_fail_fast(daily)
        assert triggered is True
        assert "阈值" in reason

    def test_status_save_load(self, tmp_path: Path, monkeypatch) -> None:
        """状态保存/加载."""
        from scripts.launch_shadow_30day import (
            Shadow30DayStatus,
            _load_status,
            _save_status,
        )

        monkeypatch.setattr(
            "scripts.launch_shadow_30day.SHADOW_STATUS_FILE",
            tmp_path / "status.json",
        )
        monkeypatch.setattr(
            "scripts.launch_shadow_30day.SHADOW_REPORT_DIR",
            tmp_path,
        )

        status = Shadow30DayStatus(
            start_date="2026-09-13",
            days_elapsed=5,
            days_remaining=25,
            last_run_date="2026-09-17",
        )
        _save_status(status)
        loaded = _load_status()

        assert loaded.start_date == "2026-09-13"
        assert loaded.days_elapsed == 5
        assert loaded.days_remaining == 25
        assert loaded.last_run_date == "2026-09-17"

    def test_load_status_nonexistent(self, tmp_path: Path, monkeypatch) -> None:
        """状态文件不存在 → 默认."""
        from scripts.launch_shadow_30day import _load_status

        monkeypatch.setattr(
            "scripts.launch_shadow_30day.SHADOW_STATUS_FILE",
            tmp_path / "nonexistent.json",
        )

        status = _load_status()
        assert status.start_date == ""
        assert status.days_elapsed == 0
