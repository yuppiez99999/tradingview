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


def _make_mvsk_record(date: str, weight_diff: float = 0.0, symbols: list[str] | None = None) -> dict:
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


def _patch_mvsk_cache(tmp_path: Path, monkeypatch) -> None:
    """构造 MVSK 378d 缓存 parquet 并 patch 到模块常量 (隔离真实 reports/shadow).

    run_preflight 会读 MVSK_RETURNS_CACHE 并检查 ≥378 行; 若不 patch, 测试
    结果会与真实 reports/shadow 缓存文件存在性耦合 (环境依赖, 语义错配).
    """
    import numpy as np
    import pandas as pd

    import scripts.launch_shadow_30day as mod

    cache = tmp_path / "mvsk_cache.parquet"
    pd.DataFrame(
        np.zeros((378, 4)),
        columns=["510300", "510500", "513100", "512890"],
    ).to_parquet(cache)
    monkeypatch.setattr(mod, "MVSK_RETURNS_CACHE", cache)


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

        records = [_make_mvsk_record(f"2026-09-{d:02d}", weight_diff=0.001) for d in range(13, 43)]
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
        records = [_make_mvsk_record(f"2026-09-{d:02d}", weight_diff=high_diff) for d in range(13, 20)]
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

        mvsk_records = [_make_mvsk_record(f"2026-09-{d:02d}", weight_diff=0.0005) for d in range(13, 43)]
        qlib_records = [_make_qlib_record(f"2026-09-{d:02d}", qlib_signal=0.3, v9_signal=0.25) for d in range(13, 43)]
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

        high = evaluator._estimate_qlib_delta_sharpe(mean_diff=0.1, std_diff=0.01, agreement_rate=0.8)
        mid = evaluator._estimate_qlib_delta_sharpe(mean_diff=0.1, std_diff=0.01, agreement_rate=0.5)
        low = evaluator._estimate_qlib_delta_sharpe(mean_diff=0.1, std_diff=0.01, agreement_rate=0.2)

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

    def test_load_mid_layer_portfolio_default(self, tmp_path: Path, monkeypatch) -> None:
        """config/positions.json 不可用 → 默认 4 ETF 组合.

        R-6 治理③后 positions.json (真实 26 条持仓) 解析成功不再回退; 兜底
        路径仅在配置文件缺失/损坏时触发 — 本测试隔离项目根验证兜底语义.
        """
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import _load_mid_layer_portfolio

        monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)  # 无 config/positions.json
        portfolio = _load_mid_layer_portfolio("2026-09-13")

        assert portfolio.trade_date == "2026-09-13"
        mid_holdings = [h for h in portfolio.holdings if h.layer == "mid"]
        assert len(mid_holdings) == 4
        total_weight = sum(h.weight for h in mid_holdings)
        assert total_weight == pytest.approx(1.0)

    def test_load_mid_layer_portfolio_reads_positions_dict(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """治理③回归: positions.json 的 dict 结构 ({meta, positions, ...}) 正确解析.

        历史缺陷: 旧实现遍历顶层 dict → p 为 "meta" 等 str key → AttributeError
        → 静默回退默认 4 ETF; 修复后应读到 positions 段全部记录且市值归一权重
        和为 1.
        """
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import _load_mid_layer_portfolio

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps(
                {
                    "meta": {"total_capital": 1_000_000, "updated": "2026-09-07"},
                    "positions": {
                        "510300": {"code": "510300", "name": "沪深300ETF", "amount": 500_000},
                        "588080": {"code": "588080", "name": "科创50ETF", "amount": 300_000},
                        "600519": {"code": "600519", "name": "贵州茅台", "amount": 200_000},
                    },
                    "hedge_positions": {"active_orders": []},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)

        portfolio = _load_mid_layer_portfolio("2026-09-13")
        mid_holdings = [h for h in portfolio.holdings if h.layer == "mid"]

        # 三条持仓全部解析 (无 layer 字段 → 默认 mid), 不得回落 4 ETF
        assert len(mid_holdings) == 3
        total_weight = sum(h.weight for h in mid_holdings)
        assert total_weight == pytest.approx(1.0)
        # 市值归一权重: 500k/1M, 300k/1M, 200k/1M
        by_code = {h.symbol: h.weight for h in mid_holdings}
        assert by_code["510300"] == pytest.approx(0.5)
        assert by_code["588080"] == pytest.approx(0.3)
        assert by_code["600519"] == pytest.approx(0.2)

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
        """USE_MVSK_MID_LAYER=false → MVSK 跳过 (路径隔离, 不写真实 reports)."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import run_daily_shadow

        monkeypatch.setenv("USE_MVSK_MID_LAYER", "false")
        monkeypatch.setenv("USE_QLIB_LGB_V2", "false")

        # 测试隔离: run_daily_shadow 会 _save_status 写 SHADOW_REPORT_DIR,
        # 若不重定向会污染真实 reports/shadow (批次C/P2-3 同款根因).
        monkeypatch.setattr(mod, "SHADOW_REPORT_DIR", tmp_path)
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", tmp_path / "status.json")
        monkeypatch.setattr(mod, "MVSK_DIFF_FILE", tmp_path / "mvsk_diff.jsonl")
        monkeypatch.setattr(mod, "QLIB_DIFF_FILE", tmp_path / "qlib_diff.jsonl")

        monkeypatch.chdir(tmp_path)
        result = run_daily_shadow("2026-09-13")

        assert result.mvsk_success is False
        assert "USE_MVSK_MID_LAYER=false" in result.mvsk_error
        assert result.qlib_success is False
        assert "USE_QLIB_LGB_V2=false" in result.qlib_error

    def test_run_daily_shadow_future_date_is_preview_no_status_write(self, tmp_path: Path, monkeypatch) -> None:
        """未来日期运行 = 演练, 不得写/锚定窗口状态 (污染根因回归).

        历史缺陷: 08-24~09-03 反复以 --date 2026-09-13 (未来) 演练, 首条
        运行把 start_date 锚到 09-13, 真实运行后 days_elapsed 恒为负
        (-8/30 天). 修复后未来日期运行应跳过状态写入.
        """
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import run_daily_shadow

        monkeypatch.setenv("USE_MVSK_MID_LAYER", "false")
        monkeypatch.setenv("USE_QLIB_LGB_V2", "false")

        monkeypatch.setattr(mod, "SHADOW_REPORT_DIR", tmp_path)
        status_file = tmp_path / "status.json"
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", status_file)
        monkeypatch.setattr(mod, "MVSK_DIFF_FILE", tmp_path / "mvsk_diff.jsonl")
        monkeypatch.setattr(mod, "QLIB_DIFF_FILE", tmp_path / "qlib_diff.jsonl")
        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-04")
        monkeypatch.chdir(tmp_path)

        result = run_daily_shadow("2026-09-13")  # 晚于 today → 预演

        assert result.mvsk_success is False
        assert result.days_elapsed == 0
        # 演练不得创建/锚定状态文件
        assert not status_file.exists()

    def test_run_daily_shadow_real_date_updates_status(self, tmp_path: Path, monkeypatch) -> None:
        """当天/过去日期运行 → 正常推进窗口状态 (与预演分支对照)."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import run_daily_shadow

        monkeypatch.setenv("USE_MVSK_MID_LAYER", "false")
        monkeypatch.setenv("USE_QLIB_LGB_V2", "false")

        monkeypatch.setattr(mod, "SHADOW_REPORT_DIR", tmp_path)
        status_file = tmp_path / "status.json"
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", status_file)
        monkeypatch.setattr(mod, "MVSK_DIFF_FILE", tmp_path / "mvsk_diff.jsonl")
        monkeypatch.setattr(mod, "QLIB_DIFF_FILE", tmp_path / "qlib_diff.jsonl")
        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-04")
        monkeypatch.chdir(tmp_path)

        result = run_daily_shadow("2026-09-04")  # == today → 真实运行

        assert result.mvsk_success is False
        # 真实运行应写状态且 start_date 锚定为当天
        assert status_file.exists()
        data = json.loads(status_file.read_text(encoding="utf-8"))
        assert data["start_date"] == "2026-09-04"
        assert data["days_elapsed"] == 1
        # 一致性回归 (2026-09-07): daily_results 内快照进度须与 top-level 一致,
        # 修复前 asdict(daily) 早于同步执行 → 内部恒为 0/30 而 top-level 1/29
        assert len(data["daily_results"]) == 1
        assert data["daily_results"][0]["days_elapsed"] == data["days_elapsed"]
        assert data["daily_results"][0]["days_remaining"] == data["days_remaining"]

    def test_fail_fast_first_trigger_latches_terminated(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """一次性 fail-fast: 首触当次 cron → exit-1 信号 + terminated 锁存落盘.

        锁存语义 (2026-09-07): 触发当次 daily.fail_fast_triggered=True
        (main 层据此 exit 1 报警), 同时 terminated=True/date/reason 写盘;
        后续 cron 由 run_daily_shadow 顶部 terminated 检查跳过 (exit 0).
        """
        from datetime import datetime

        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import run_daily_shadow

        today = datetime.now().strftime("%Y-%m-%d")
        monkeypatch.setattr(mod, "SHADOW_REPORT_DIR", tmp_path)
        status_file = tmp_path / "status.json"
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", status_file)
        monkeypatch.setattr(mod, "MVSK_DIFF_FILE", tmp_path / "mvsk_diff.jsonl")
        monkeypatch.setattr(mod, "QLIB_DIFF_FILE", tmp_path / "qlib_diff.jsonl")
        monkeypatch.setattr(mod, "_today_str", lambda: today)
        # cron (无 --date) 走交易日门控, 放行
        monkeypatch.setattr("utils.trade_calendar.is_trading_day", lambda _d: True)
        calls = {"mvsk": 0}

        def fake_mvsk(_trade_date: str):
            calls["mvsk"] += 1
            return True, 0.45, ""  # L2 > 0.30 → 触发 fail-fast

        monkeypatch.setattr(mod, "_run_mvsk_shadow", fake_mvsk)
        monkeypatch.setattr(mod, "_run_qlib_shadow", lambda _d: (False, 0.0, "skip"))
        monkeypatch.chdir(tmp_path)

        result = run_daily_shadow("")

        assert result.fail_fast_triggered is True  # main 层将 exit 1
        assert "MVSK" in result.fail_fast_reason
        assert calls["mvsk"] == 1
        data = json.loads(status_file.read_text(encoding="utf-8"))
        assert data["terminated"] is True
        assert data["terminated_date"] == today
        assert data["terminated_reason"]
        assert data["daily_results"][-1]["fail_fast_triggered"] is True

    def test_fail_fast_terminated_cron_skips_later_runs(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """后续 cron 不再重复红: terminated 状态 → 跳过 shadow 计算 (exit 0)."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import run_daily_shadow

        monkeypatch.setattr(mod, "SHADOW_REPORT_DIR", tmp_path)
        status_file = tmp_path / "status.json"
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", status_file)
        monkeypatch.setattr(mod, "MVSK_DIFF_FILE", tmp_path / "mvsk_diff.jsonl")
        monkeypatch.setattr(mod, "QLIB_DIFF_FILE", tmp_path / "qlib_diff.jsonl")
        status_file.write_text(
            json.dumps(
                {
                    "start_date": "2026-09-07",
                    "terminated": True,
                    "terminated_date": "2026-09-07",
                    "terminated_reason": "MVSK 权重差异 0.45 > 阈值 0.30",
                    "daily_results": [],
                }
            ),
            encoding="utf-8",
        )
        calls = {"mvsk": 0}

        def fake_mvsk(_trade_date: str):
            calls["mvsk"] += 1
            return True, 0.45, ""

        monkeypatch.setattr(mod, "_run_mvsk_shadow", fake_mvsk)
        monkeypatch.chdir(tmp_path)

        result = run_daily_shadow("")  # cron 模式

        assert result.fail_fast_triggered is False  # exit 0, 不再红
        assert calls["mvsk"] == 0  # 未重复计算/写 jsonl
        data = json.loads(status_file.read_text(encoding="utf-8"))
        assert data["terminated"] is True  # 锁存未被破坏

    def test_fail_fast_explicit_date_keeps_latch_date(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """显式 --date (补跑/诊断) 放行计算; 已锁存的 terminated_date 不被覆盖."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import run_daily_shadow

        monkeypatch.setattr(mod, "SHADOW_REPORT_DIR", tmp_path)
        status_file = tmp_path / "status.json"
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", status_file)
        monkeypatch.setattr(mod, "MVSK_DIFF_FILE", tmp_path / "mvsk_diff.jsonl")
        monkeypatch.setattr(mod, "QLIB_DIFF_FILE", tmp_path / "qlib_diff.jsonl")
        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-10")
        status_file.write_text(
            json.dumps(
                {
                    "start_date": "2026-09-07",
                    "days_elapsed": 1,
                    "terminated": True,
                    "terminated_date": "2026-09-07",
                    "terminated_reason": "MVSK 权重差异 0.45 > 阈值 0.30",
                    "daily_results": [],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "_run_mvsk_shadow", lambda _d: (True, 0.45, ""))
        monkeypatch.setattr(mod, "_run_qlib_shadow", lambda _d: (False, 0.0, "skip"))
        monkeypatch.chdir(tmp_path)

        result = run_daily_shadow("2026-09-09")  # 显式过去日期, 放行

        assert result.fail_fast_triggered is True  # 人工补跑仍如实报背离
        data = json.loads(status_file.read_text(encoding="utf-8"))
        assert data["terminated"] is True
        assert data["terminated_date"] == "2026-09-07"  # 首触日期锁存不变

    def test_run_reset_clears_terminated_and_archives(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """--reset: 状态归档 .bak (审计保留) + terminated/进度清零."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import run_reset

        monkeypatch.setattr(mod, "SHADOW_REPORT_DIR", tmp_path)
        status_file = tmp_path / "status.json"
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", status_file)
        status_file.write_text(
            json.dumps(
                {
                    "start_date": "2026-09-07",
                    "days_elapsed": 3,
                    "terminated": True,
                    "terminated_date": "2026-09-07",
                    "terminated_reason": "MVSK 权重差异 0.45 > 阈值 0.30",
                    "daily_results": [{"date": "2026-09-07", "mvsk_success": True}],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        run_reset()

        archives = list(tmp_path.glob("shadow_30day_status.json.*.bak"))
        assert len(archives) == 1  # 归档保留审计
        fresh = json.loads(status_file.read_text(encoding="utf-8"))
        assert fresh["terminated"] is False
        assert fresh["start_date"] == ""
        assert fresh["daily_results"] == []
        assert "复位" in capsys.readouterr().out

    def test_update_status_negative_elapsed_defense(self) -> None:
        """_update_status: start_date 晚于运行日 (污染残留) 时重置窗口起点."""
        from scripts.launch_shadow_30day import (
            Shadow30DayStatus,
            ShadowDailyResult,
            _update_status,
        )

        # 构造污染状态: start_date 锚在未来 09-13 (历史未来日期演练所致)
        status = Shadow30DayStatus(start_date="2026-09-13")
        daily = ShadowDailyResult(date="2026-09-04", timestamp="2026-09-04T10:00:00")

        new_status = _update_status(status, daily)

        # 起点被重置到真实运行日, days_elapsed 恢复为 1 而非 -8
        assert new_status.start_date == "2026-09-04"
        assert new_status.days_elapsed == 1
        assert new_status.days_remaining == 29

    def test_update_status_idempotent_same_date(self) -> None:
        """_update_status: 同 date 重复运行只保留最后一条 daily_results."""
        from scripts.launch_shadow_30day import (
            Shadow30DayStatus,
            ShadowDailyResult,
            _update_status,
        )

        status = Shadow30DayStatus()
        daily1 = ShadowDailyResult(
            date="2026-09-04",
            timestamp="2026-09-04T10:00:00",
            mvsk_error="first",
        )
        daily2 = ShadowDailyResult(
            date="2026-09-04",
            timestamp="2026-09-04T11:00:00",
            mvsk_error="second",
        )

        status = _update_status(status, daily1)
        status = _update_status(status, daily2)

        assert len(status.daily_results) == 1
        assert status.daily_results[0]["mvsk_error"] == "second"

    def test_update_status_future_date_does_not_anchor(self, monkeypatch) -> None:
        """_update_status: 未来日期 (绕过 run_daily_shadow 拦截直接进入) 不锚定窗口.

        双防线验证: 即使调用层 preview 分支失效, 函数自身也拒绝演练日期,
        不写 start_date / daily_results / last_run 等任何字段.
        """
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import Shadow30DayStatus, ShadowDailyResult

        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-05")

        status = Shadow30DayStatus()  # 窗口未启动
        daily = ShadowDailyResult(date="2026-09-13", timestamp="2026-09-13T10:00:00")

        new_status = mod._update_status(status, daily)

        assert new_status.start_date == ""
        assert new_status.end_date == ""
        assert new_status.days_elapsed == 0
        assert new_status.days_remaining == 30
        assert new_status.daily_results == []
        assert new_status.last_run_date == ""
        assert new_status.mvsk_records == 0  # 未来演练不统计/不触碰

    def test_update_status_future_date_keeps_started_window(self, monkeypatch) -> None:
        """_update_status: 已启动窗口收到未来日期 → 状态完全不变 (演练零副作用)."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import Shadow30DayStatus, ShadowDailyResult

        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-05")

        status = Shadow30DayStatus(
            start_date="2026-09-04",
            days_elapsed=1,
            days_remaining=29,
            last_run_date="2026-09-04",
            daily_results=[{"date": "2026-09-04", "mvsk_error": "keep"}],
        )
        daily = ShadowDailyResult(date="2026-09-13", timestamp="2026-09-13T10:00:00")

        new_status = mod._update_status(status, daily)

        assert new_status.start_date == "2026-09-04"
        assert new_status.days_elapsed == 1
        assert new_status.days_remaining == 29
        assert new_status.last_run_date == "2026-09-04"
        assert len(new_status.daily_results) == 1
        assert new_status.daily_results[0]["date"] == "2026-09-04"

    def test_update_status_invalid_date_fail_closed(self, monkeypatch) -> None:
        """_update_status: 空/非法日期 fail-closed, 不写任何状态字段."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import Shadow30DayStatus, ShadowDailyResult

        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-05")

        # 空日期
        status = Shadow30DayStatus()
        daily = ShadowDailyResult(date="", timestamp="2026-09-05T10:00:00")
        new_status = mod._update_status(status, daily)
        assert new_status.start_date == ""
        assert new_status.last_run_date == ""

        # 非 YYYY-MM-DD 格式
        daily = ShadowDailyResult(date="2026/09/05", timestamp="2026-09-05T10:00:00")
        new_status = mod._update_status(status, daily)
        assert new_status.start_date == ""
        assert new_status.days_elapsed == 0
        assert new_status.daily_results == []

    def test_update_status_start_date_garbage_resets(self, monkeypatch) -> None:
        """_update_status: start_date 非标准格式 (脏数据) → 重置窗口起点, 不留负值."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import Shadow30DayStatus, ShadowDailyResult

        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-05")

        # 状态文件残留: start_date 乱码 + 负进度 + 越界剩余 (全量污染快照)
        status = Shadow30DayStatus(
            start_date="not-a-date",
            days_elapsed=-8,
            days_remaining=38,
        )
        daily = ShadowDailyResult(date="2026-09-05", timestamp="2026-09-05T10:00:00")

        new_status = mod._update_status(status, daily)

        assert new_status.start_date == "2026-09-05"
        assert new_status.days_elapsed == 1
        assert new_status.days_remaining == 29

    def test_update_status_reset_purges_future_records(self, monkeypatch) -> None:
        """_update_status: 污染起点重置时同步剔除起点之后的历史脏记录."""
        import scripts.launch_shadow_30day as mod
        from scripts.launch_shadow_30day import Shadow30DayStatus, ShadowDailyResult

        monkeypatch.setattr(mod, "_today_str", lambda: "2026-09-05")

        status = Shadow30DayStatus(
            start_date="2026-09-13",  # 历史演练污染
            daily_results=[
                {"date": "2026-09-13", "mvsk_error": "future dirty"},
                {"date": "2026-09-04", "mvsk_error": "legit"},
            ],
        )
        daily = ShadowDailyResult(date="2026-09-05", timestamp="2026-09-05T10:00:00")

        new_status = mod._update_status(status, daily)

        assert new_status.start_date == "2026-09-05"
        dates = [r["date"] for r in new_status.daily_results]
        # 未来脏记录被剔除, 历史合法记录保留, 本次记录追加
        assert dates == ["2026-09-04", "2026-09-05"]

    def test_load_status_sanitizes_negative_progress(self, tmp_path: Path, monkeypatch) -> None:
        """_load_status: 状态文件残留负进度/越界剩余 → 加载净化, 不展示 -8/30."""
        import scripts.launch_shadow_30day as mod

        status_file = tmp_path / "status.json"
        status_file.write_text(
            json.dumps(
                {
                    "start_date": "2026-09-13",
                    "end_date": "",
                    "days_elapsed": -8,
                    "days_remaining": 38,
                    "daily_results": [],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "SHADOW_STATUS_FILE", status_file)

        loaded = mod._load_status()

        assert loaded.start_date == "2026-09-13"
        assert loaded.days_elapsed == 0
        assert loaded.days_remaining == 30

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
        """fail-fast 触发 — 差异过大 (独立量纲阈值: MVSK>0.30 / qlib>0.80).

        2026-09-04 修正: 旧断言 (diff=0.05 触发) 沿用了把 signal_diff 当回撤
        百分比的量纲错误 — signal_diff 常态 0.3~0.6, 混比会每日误触发.
        """
        from scripts.launch_shadow_30day import ShadowDailyResult, _check_fail_fast

        # MVSK 权重差异超阈值触发
        daily = ShadowDailyResult(
            mvsk_weight_diff_l2=0.35,
            qlib_signal_diff=0.01,
        )
        triggered, reason = _check_fail_fast(daily)
        assert triggered is True
        assert "阈值" in reason

        # qlib 信号差异超阈值触发
        daily = ShadowDailyResult(
            mvsk_weight_diff_l2=0.01,
            qlib_signal_diff=0.85,
        )
        triggered, reason = _check_fail_fast(daily)
        assert triggered is True
        assert "阈值" in reason

        # 常态差异不触发 (signal_diff 0.3~0.6 属正常范围)
        daily = ShadowDailyResult(
            mvsk_weight_diff_l2=0.05,
            qlib_signal_diff=0.01,
        )
        triggered, _ = _check_fail_fast(daily)
        assert triggered is False

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

    # ---- run_preflight (09-13 启动前自检) 测试 ----

    def _preflight_isolation(self, tmp_path: Path, monkeypatch) -> None:
        """preflight 测试公共隔离: env 变量 (R-6 归档: qlib 默认关) + 临时目录.

        历史缺陷: 测试未显式控制 USE_QLIB_LGB_V2, 若宿主机 user env 残留
        =true (2026-09-07 实测存在) 会让 qlib_forced=True → 测试偶然"通过"
        但语义完全错配 (归档态应不要求模型). 此处统一 delenv + patch 缓存.
        """
        monkeypatch.delenv("USE_QLIB_LGB_V2", raising=False)
        monkeypatch.delenv("USE_MVSK_MID_LAYER", raising=False)
        monkeypatch.setattr(
            "scripts.launch_shadow_30day.SHADOW_STATUS_FILE",
            tmp_path / "s.json",
        )
        monkeypatch.setattr(
            "scripts.launch_shadow_30day.SHADOW_REPORT_DIR",
            tmp_path,
        )
        monkeypatch.setattr(
            "scripts.launch_shadow_30day._check_import",
            lambda *a, **k: (True, "ok"),
        )
        _patch_mvsk_cache(tmp_path, monkeypatch)

    def test_preflight_ready(self, tmp_path: Path, monkeypatch) -> None:
        """R-6 归档态全部前置就绪 → True (可启动 30 天窗口)."""
        from scripts.launch_shadow_30day import run_preflight

        self._preflight_isolation(tmp_path, monkeypatch)
        # qlib 模型已落盘 (归档态其实不要求, 此处构造但非阻塞前提)
        monkeypatch.setattr(
            "scripts.launch_shadow_30day._latest_glob",
            lambda p: Path(p.replace("qlib_model_*.pkl", "qlib_model_x.pkl")),
        )

        assert run_preflight() is True

    def test_preflight_archived_model_missing_not_block(self, tmp_path: Path, monkeypatch) -> None:
        """R-6 归档态 (USE_QLIB_LGB_V2 默认关): qlib 模型缺失不阻塞 → True.

        2026-09-07 语义迁移: qlib_lgb_v2 停跑归档, 模型仅 USE_QLIB_LGB_V2=1
        强制启用时才校验 — 归档态不再因模型缺失判定 NOT READY.
        """
        from scripts.launch_shadow_30day import run_preflight

        self._preflight_isolation(tmp_path, monkeypatch)
        # qlib 模型文件不存在 (归档态应忽略)
        monkeypatch.setattr("scripts.launch_shadow_30day._latest_glob", lambda p: None)

        assert run_preflight() is True

    def test_preflight_forced_qlib_model_missing_blocks(self, tmp_path: Path, monkeypatch) -> None:
        """强制启用 (USE_QLIB_LGB_V2=1) 但 qlib 模型缺失 → 阻塞 False.

        验证 R-6 归档"门是关着但可开": 显式 =1 后重新恢复模型校验,
        防静默假就绪.
        """
        from scripts.launch_shadow_30day import run_preflight

        self._preflight_isolation(tmp_path, monkeypatch)
        monkeypatch.setenv("USE_QLIB_LGB_V2", "1")
        monkeypatch.setattr("scripts.launch_shadow_30day._latest_glob", lambda p: None)

        assert run_preflight() is False

    def test_preflight_window_complete_blocks(self, tmp_path: Path, monkeypatch) -> None:
        """窗口已完成 → 阻塞, 返回 False (禁止重复启动)."""
        import json as _json

        from scripts.launch_shadow_30day import run_preflight

        status_file = tmp_path / "status.json"
        status_file.write_text(
            _json.dumps(
                {
                    "start_date": "2026-09-13",
                    "end_date": "2026-10-12",
                    "days_elapsed": 30,
                    "days_remaining": 0,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.launch_shadow_30day.SHADOW_STATUS_FILE", status_file)
        monkeypatch.setattr(
            "scripts.launch_shadow_30day.SHADOW_REPORT_DIR",
            tmp_path,
        )
        monkeypatch.setattr(
            "scripts.launch_shadow_30day._check_import",
            lambda *a, **k: (True, "ok"),
        )
        monkeypatch.setattr(
            "scripts.launch_shadow_30day._latest_glob",
            lambda p: Path(p.replace("qlib_model_*.pkl", "qlib_model_x.pkl")),
        )
        _patch_mvsk_cache(tmp_path, monkeypatch)

        assert run_preflight() is False

    def test_preflight_flag_off_warns_not_block(self, tmp_path: Path, monkeypatch) -> None:
        """feature flag 关闭仅警告不阻塞 → 仍返回 True."""
        from scripts.launch_shadow_30day import run_preflight

        self._preflight_isolation(tmp_path, monkeypatch)
        monkeypatch.setenv("USE_MVSK_MID_LAYER", "false")
        monkeypatch.setenv("USE_QLIB_LGB_V2", "1")
        # 强制启用场景下需模型就绪才不阻塞
        monkeypatch.setattr(
            "scripts.launch_shadow_30day._latest_glob",
            lambda p: Path(p.replace("qlib_model_*.pkl", "qlib_model_x.pkl")),
        )

        assert run_preflight() is True

    def test_latest_glob_empty(self, tmp_path: Path) -> None:
        """_latest_glob 无匹配 → None."""
        from scripts.launch_shadow_30day import _latest_glob

        assert _latest_glob(str(tmp_path / "none_*.pkl")) is None

    def test_latest_glob_returns_latest(self, tmp_path: Path) -> None:
        """_latest_glob 返回字典序最新文件."""
        from scripts.launch_shadow_30day import _latest_glob

        (tmp_path / "qlib_model_a.pkl").write_text("a", encoding="utf-8")
        (tmp_path / "qlib_model_b.pkl").write_text("b", encoding="utf-8")
        latest = _latest_glob(str(tmp_path / "qlib_model_*.pkl"))
        assert latest is not None
        assert latest.name == "qlib_model_b.pkl"
