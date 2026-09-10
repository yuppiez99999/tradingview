"""test_walk_forward_20260910.py — 审计 item 14 样本外验证回归

缺陷 (2026-09-09 审计 item 14 "样本外验证主链路接入"):
    1. ``utils/alpha/fast_backtest.py`` 的 run() 只对同一段 returns 算静态指标,
       ``window_details`` 恒为空、``n_windows`` 仅为估算 —— 无任何滚动样本外能力;
    2. ``utils/pipeline/backtest_gate.py`` 的 ``_run_walk_forward`` 名为 Walk-Forward,
       实际只算单窗口静态 IC/Sharpe/回撤, 并把 ``ic >= min_ic`` 当作"WF 通过";
    3. 更严重: 该文件 ``from utils.alpha.purged_kfold import PurgedKFold`` ——
       **该模块在仓库中从未存在** → ImportError → ``_has_purged_kfold`` 恒 False →
       run() 第一步永远被跳过且按"通过"处理 (确定性假 PASS);
    4. ``_run_stress_test`` 传空持仓 ``run_all_scenarios([])`` → 各场景 pnl=0,
       且读取的 ``result["max_drawdown"]`` 键**不存在**(真实键 ``worst_dd``) →
       回撤恒 0 → 恒通过;
    5. ``_run_dsr_check`` 自造公式 + 失败默认 1.5, 而配置 min_dsr 默认 1.0
       (对概率值域不可达) → 恒通过。

修复 (2026-09-10):
    - 新增 ``utils/alpha/walk_forward.py``: 真实滚动 WF (强制 purge 间隔 + 逐窗口
      明细 + OOS 拼接 Sharpe/CV) 与 CPCV 路径分布 (委托 ms_strategy 内核);
    - ``FastBacktest.run_walk_forward()`` / ``run_cpcv()`` 显式入口;
    - ``BacktestGate`` 三处假 PASS 改为真实计算 + **决策路径 fail-closed**;
    - ``PipelineConfig.min_dsr`` 1.0 → 0.5 (raw 概率口径), 且 >1 视为口径错误拒绝。

本文件即"修复前会失败"的回归。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from utils.alpha.fast_backtest import FastBacktest
from utils.alpha.walk_forward import (
    STABILITY_CV_MAX,
    STABILITY_MIN_WINDOWS,
    WalkForwardConfig,
    build_windows,
    cpcv_path_distribution,
    walk_forward_evaluate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate_stress_report_dir(tmp_path, monkeypatch):
    """压力测试报告目录重定向 tmp_path (禁止单测写生产 reports/)。"""
    monkeypatch.setattr("utils.stress_test_runner.REPORT_DIR", tmp_path)


def _stable_returns(n: int = 1600) -> np.ndarray:
    """高度稳定的正收益序列 (确定性, 无随机): 窗口间 Sharpe 应高度一致。"""
    cycle = np.linspace(0.0018, 0.0022, 200)
    return np.tile(cycle, n // 200)


def _regime_shift_returns() -> np.ndarray:
    """前半正、后半负 (确定性): 正收益窗口占比 ~50% → 必须判为不稳定。"""
    return np.concatenate([np.full(800, 0.002), np.full(800, -0.002)])


# ============================================================
# build_windows — purge 间隔是硬约束
# ============================================================


class TestBuildWindows:
    @pytest.mark.unit
    def test_purge_gap_always_respected(self):
        cfg = WalkForwardConfig(train_days=252, test_days=63, step_days=63, purge_days=5)
        windows = build_windows(1500, cfg)
        assert windows, "应能生成窗口"
        for _tr_s, tr_e, te_s, te_e in windows:
            # 修复前不存在这个约束 (也没有任何切分)
            assert te_s - tr_e == cfg.purge_days
            assert tr_e <= te_s - cfg.purge_days  # 无泄漏: train 结束早于 test 开始
            assert te_e <= 1500
            assert te_s < te_e

    @pytest.mark.unit
    def test_zero_purge_still_no_overlap(self):
        cfg = WalkForwardConfig(train_days=100, test_days=30, step_days=30, purge_days=0)
        for _tr_s, tr_e, te_s, _te_e in build_windows(400, cfg):
            assert tr_e == te_s

    @pytest.mark.unit
    def test_insufficient_samples_returns_empty(self):
        cfg = WalkForwardConfig(train_days=252, test_days=63, purge_days=5)
        assert build_windows(100, cfg) == []

    @pytest.mark.unit
    def test_step_controls_window_count(self):
        base = build_windows(1500, WalkForwardConfig(252, 63, 63, 5))
        coarse = build_windows(1500, WalkForwardConfig(252, 63, 126, 5))
        assert len(coarse) < len(base)

    @pytest.mark.unit
    @pytest.mark.parametrize("kwargs", [
        {"train_days": 0},
        {"test_days": 0},
        {"step_days": 0},
        {"purge_days": -1},
        {"trading_days": 0},
    ])
    def test_invalid_config_rejected(self, kwargs):
        with pytest.raises(ValueError):
            WalkForwardConfig(**kwargs)


# ============================================================
# walk_forward_evaluate — 真实执行 + fail-closed
# ============================================================


class TestWalkForwardEvaluate:
    @pytest.mark.unit
    def test_insufficient_data_fails_closed(self):
        """回归主用例: 数据不足必须 n_windows=0 且 is_stable=False, 绝不算"稳定"。"""
        wf = walk_forward_evaluate(np.zeros(100))
        assert wf.n_windows == 0
        assert wf.is_stable is False
        assert wf.executed is False
        assert "样本不足" in wf.insufficient_reason
        assert wf.verdict.startswith("UNVERIFIED")

    @pytest.mark.unit
    def test_stable_series_is_stable(self):
        wf = walk_forward_evaluate(_stable_returns())
        assert wf.executed is True
        assert wf.n_windows >= STABILITY_MIN_WINDOWS
        assert len(wf.windows) == wf.n_windows
        assert wf.oos_sharpe_cv < STABILITY_CV_MAX
        assert wf.pct_positive_windows == 1.0
        assert wf.is_stable is True

    @pytest.mark.unit
    def test_regime_shift_is_unstable(self):
        wf = walk_forward_evaluate(_regime_shift_returns())
        assert wf.executed is True
        assert wf.pct_positive_windows < 1.0
        assert wf.is_stable is False

    @pytest.mark.unit
    def test_flat_series_not_claimed_stable(self):
        """全零收益 (无信号) 不得被判为"稳定"。"""
        wf = walk_forward_evaluate(np.zeros(1600))
        assert wf.is_stable is False

    @pytest.mark.unit
    def test_windows_have_real_details(self):
        """window_details 必须非空且带索引/样本数 (修复前恒为空列表)。"""
        wf = walk_forward_evaluate(_stable_returns())
        assert wf.windows
        w = wf.windows[0]
        assert w.n_train > 0 and w.n_test > 0
        assert w.test_end > w.test_start
        assert w.purge_gap >= 0
        assert isinstance(w.as_dict()["sharpe"], float)

    @pytest.mark.unit
    def test_oos_returns_concatenated(self):
        wf = walk_forward_evaluate(_stable_returns())
        assert wf.oos_returns.size == sum(w.n_test for w in wf.windows)
        assert wf.oos_max_drawdown >= 0.0

    @pytest.mark.unit
    def test_strategy_fn_is_used(self):
        calls = {"n": 0}

        def strategy(train_ret: np.ndarray, test_ret: np.ndarray) -> np.ndarray:
            calls["n"] += 1
            return test_ret

        wf = walk_forward_evaluate(_stable_returns(), strategy_fn=strategy)
        assert calls["n"] >= 3
        assert wf.n_windows >= 3

    @pytest.mark.unit
    def test_strategy_fn_returning_empty_skips_windows(self):
        wf = walk_forward_evaluate(
            _stable_returns(), strategy_fn=lambda t, s: np.array([])
        )
        assert wf.n_windows == 0
        assert wf.is_stable is False
        assert "全部被过滤" in wf.insufficient_reason

    @pytest.mark.unit
    def test_as_dict_serializable(self):
        wf = walk_forward_evaluate(_stable_returns())
        payload = json.dumps(wf.as_dict(), ensure_ascii=False, default=str)
        assert "n_windows" in payload


# ============================================================
# CPCV 路径分布 — 不可用时必须显式 available=False
# ============================================================


class TestCPCVPathDistribution:
    @pytest.mark.unit
    def test_available_with_enough_samples(self):
        dist = cpcv_path_distribution(_stable_returns())
        assert dist["available"] is True
        assert dist["n_paths"] > 0
        assert len(dist["path_sharpes"]) == dist["n_paths"]

    @pytest.mark.unit
    def test_insufficient_samples_not_available(self):
        dist = cpcv_path_distribution(np.zeros(20))
        assert dist["available"] is False
        assert dist["n_paths"] == 0
        assert dist["is_stable"] is False
        assert dist["note"]


# ============================================================
# FastBacktest 新入口 (run() 契约保持不变)
# ============================================================


class TestFastBacktestOutOfSampleEntries:
    @pytest.mark.unit
    def test_run_walk_forward_entry(self):
        result = FastBacktest().run_walk_forward(_stable_returns())
        assert result.executed is True
        assert result.n_windows >= 3

    @pytest.mark.unit
    def test_run_cpcv_entry(self):
        dist = FastBacktest().run_cpcv(_stable_returns())
        assert dist["available"] is True

    @pytest.mark.unit
    def test_run_still_rejects_strategy_fn(self):
        """run() 的 fail-fast 是刻意保留的防误用, 不得被本次改动放开。"""
        with pytest.raises(NotImplementedError):
            FastBacktest().run(_stable_returns(), strategy_fn=lambda a, b: a)


# ============================================================
# BacktestGate — 三处假 PASS 的负向验证
# ============================================================


class TestBacktestGateFalsePassFixes:
    @pytest.fixture
    def gate(self, tmp_path):
        from utils.pipeline import BacktestGate
        from utils.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        cfg.backtest_gate_enabled = True
        cfg.min_ic = 0.03
        cfg.min_dsr = 0.5
        cfg.max_drawdown = 0.15
        cfg.report_dir = str(tmp_path / "reports")
        return BacktestGate(cfg)

    @pytest.mark.unit
    def test_walk_forward_kernel_is_really_available(self, gate):
        """回归主用例: 修复前该标志恒为 False (导入不存在的模块) → 步骤被跳过。"""
        assert gate._has_walk_forward is True
        assert gate._walk_forward_evaluate is not None
        assert gate._WalkForwardConfig is not None

    @pytest.mark.unit
    def test_stress_test_reads_real_worst_dd(self, gate):
        """回归: 修复前读不存在的 max_drawdown 键 → 恒 0 → 恒通过。"""
        from utils.pipeline.types import BacktestGateResult

        result = gate._run_stress_test(None, BacktestGateResult())
        assert result.details.get("stress_test_status") == "EXECUTED"
        assert result.details.get("stress_test_n_scenarios") == 4
        assert result.details.get("stress_test_worst_dd", 0) > 0
        # max_drawdown 字段应等于报告 worst_dd (而非恒 0)
        assert result.max_drawdown == pytest.approx(
            result.details["stress_test_worst_dd"], abs=1e-6
        )

    @pytest.mark.unit
    def test_stress_test_fails_closed_without_positions(self, gate, monkeypatch):
        from utils.pipeline.types import BacktestGateResult

        monkeypatch.setattr(gate, "_load_stress_positions", lambda: ([], 0.0))
        result = gate._run_stress_test(None, BacktestGateResult())
        assert result.stress_test_passed is False
        assert result.details["stress_test_status"] == "UNVERIFIED_NO_POSITIONS"

    @pytest.mark.unit
    def test_min_dsr_wrong_scale_is_rejected(self, gate):
        """min_dsr > 1 对概率值域不可达 → 必须报口径错误并拒绝, 不得静默放行。"""
        from utils.pipeline.types import BacktestGateResult

        gate.config.min_dsr = 1.0
        g = BacktestGateResult(
            ic=0.5, dsr=0.9, walk_forward_passed=True, stress_test_passed=True
        )
        g = gate._final_judgment(g)
        assert g.passed is False
        assert "min_dsr=1.0" in (g.rejection_reason or "")
        assert "dsr_threshold_scale_error" in g.details

    @pytest.mark.unit
    def test_final_judgment_passes_when_all_real(self, gate):
        from utils.pipeline.types import BacktestGateResult

        g = BacktestGateResult(
            ic=0.5, dsr=0.9, walk_forward_passed=True, stress_test_passed=True
        )
        g = gate._final_judgment(g)
        assert g.passed is True
        assert g.rejection_reason is None

    @pytest.mark.unit
    def test_final_judgment_reports_status_of_failed_steps(self, gate):
        from utils.pipeline.types import BacktestGateResult

        g = BacktestGateResult(ic=0.5, dsr=0.9)
        g.details["walk_forward_status"] = "UNVERIFIED_INSUFFICIENT_HISTORY"
        g.details["stress_test_status"] = "UNVERIFIED_NO_POSITIONS"
        g = gate._final_judgment(g)
        assert g.passed is False
        assert "UNVERIFIED_INSUFFICIENT_HISTORY" in (g.rejection_reason or "")
        assert "UNVERIFIED_NO_POSITIONS" in (g.rejection_reason or "")
