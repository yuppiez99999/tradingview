"""U1 衔接 research 版 PipelineOrchestrator 集成测试.

验证 research 版 IC 计算函数替换为 U1 函数后的一致性:
    - 场景 1: calc_ic_series_from_history vs compute_rolling_ic_series (IC 序列长度+符号一致率)
    - 场景 2: calc_ic_ir vs compute_ic_ir (同输入同输出)
    - 场景 3: PipelineResult 新增 factor_history 字段可读写
    - 场景 4: to_dict() 排除 factor_history 避免体积膨胀

签名核对 (2026-08-05):
    - U1: calc_ic_series_from_history(factor_history, forward_returns_history, min_samples=5) -> list[float]
    - U1: calc_ic_ir(ic_series, min_periods=20) -> (ic_ir, ic_mean, ic_std)
    - research: compute_rolling_ic_series(factor_history, forward_returns_history) -> list[float]  (Pearson)
    - research: compute_ic_ir(ic_series, min_periods=20) -> (ic_ir, ic_mean, ic_std)
    - PipelineResult.factor_history: dict[str, list[dict[str, float]]]
    - PipelineResult.forward_returns_history: list[dict[str, float]]
"""
from __future__ import annotations

import logging

import numpy as np
import pytest

logger = logging.getLogger(__name__)

# G5 物理隔离 (2026-08-09): 本测试对比 research.vibe_trading_factor_analysis (Pearson IC)
# 与 U1 版 (utils.alpha_factor.base, Spearman IC) 的一致性。research 版 pipeline 已被 U1 版
# utils.alpha_factor.base 完全替代并删除, 原对比对象不存在; 引用的 PipelineResult 是已隔离废弃的
# research 实验版 (_archive/research_references_quarantine/), 与生产 utils.pipeline.types.PipelineResult
# 语义不同。对比失去意义, 跳过待 U1 版对应测试成熟后重写。
pytestmark = [
    pytest.mark.unit,
    pytest.mark.skip(
        reason="research.vibe_trading_factor_analysis 已废弃删除, U1 版 utils.alpha_factor.base 为唯一实现; PipelineResult 引用的是已隔离的 research 实验版"
    ),
]


# ============================================================
# Helper: 构造测试数据
# ============================================================

def _build_factor_history_seq(n_days: int = 25, n_symbols: int = 10) -> list[dict[str, float]]:
    """构造单因子日频历史: [{symbol: value}, ...]."""
    np.random.seed(42)
    symbols = [f"TEST{i:03d}.SZ" for i in range(n_symbols)]
    return [{sym: float(np.random.randn()) for sym in symbols} for _ in range(n_days)]


def _build_forward_returns_history(n_days: int = 25, n_symbols: int = 10) -> list[dict[str, float]]:
    """构造 forward returns: [{symbol: ret}, ...]."""
    np.random.seed(123)
    symbols = [f"TEST{i:03d}.SZ" for i in range(n_symbols)]
    return [{sym: float(np.random.randn() * 0.02) for sym in symbols} for _ in range(n_days)]


# ============================================================
# 场景 1: calc_ic_series_from_history vs compute_rolling_ic_series ✅
# ============================================================

class TestICSeriesConsistency:
    """U1 (Spearman) vs research (Pearson) IC 序列一致性."""

    def test_ic_series_length_consistent(self):
        """两版本 IC 序列长度一致 (取 min(len(factor_history), len(forward_returns)))."""
        from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
            compute_rolling_ic_series,
        )

        from utils.alpha_factor.base import calc_ic_series_from_history

        factor_history = _build_factor_history_seq(n_days=25)
        fwd_returns = _build_forward_returns_history(n_days=25)

        u1_series = calc_ic_series_from_history(factor_history, fwd_returns)
        research_series = compute_rolling_ic_series(factor_history, fwd_returns)

        assert len(u1_series) == len(research_series), (
            f"IC 序列长度不一致: U1={len(u1_series)}, research={len(research_series)}"
        )

    def test_ic_series_sign_consistency(self):
        """两版本 IC 符号一致率 >= 80% (Spearman vs Pearson 在非极端数据下高度相关)."""
        from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
            compute_rolling_ic_series,
        )

        from utils.alpha_factor.base import calc_ic_series_from_history

        factor_history = _build_factor_history_seq(n_days=25)
        fwd_returns = _build_forward_returns_history(n_days=25)

        u1_series = calc_ic_series_from_history(factor_history, fwd_returns)
        research_series = compute_rolling_ic_series(factor_history, fwd_returns)

        # 符号一致率 (排除两端都接近 0 的情况)
        n_consistent = 0
        n_total = 0
        for u, r in zip(u1_series, research_series, strict=True):
            if abs(u) < 1e-6 or abs(r) < 1e-6:
                continue
            n_total += 1
            if (u > 0) == (r > 0):
                n_consistent += 1

        if n_total > 0:
            consistency_rate = n_consistent / n_total
            assert consistency_rate >= 0.8, (
                f"IC 符号一致率 {consistency_rate:.2%} < 80% "
                f"({n_consistent}/{n_total})"
            )


# ============================================================
# 场景 2: calc_ic_ir vs compute_ic_ir ✅
# ============================================================

class TestICIRConsistency:
    """U1 vs research IC_IR 一致性 (同输入同输出)."""

    def test_ic_ir_same_input_same_output(self):
        """同输入 IC 序列: 两版本 IC_IR 结果一致 (算法 1:1, ddof=1)."""
        from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
            compute_ic_ir,
        )

        from utils.alpha_factor.base import calc_ic_ir

        # 构造固定 IC 序列
        np.random.seed(456)
        ic_series = list(np.random.randn(30) * 0.1)

        u1_result = calc_ic_ir(ic_series, min_periods=20)
        research_result = compute_ic_ir(ic_series, min_periods=20)

        # 三元组 (ic_ir, ic_mean, ic_std)
        assert len(u1_result) == 3
        assert len(research_result) == 3

        # IC_IR 数值接近 (算法 1:1, 应完全一致)
        assert abs(u1_result[0] - research_result[0]) < 1e-10, (
            f"IC_IR 不一致: U1={u1_result[0]}, research={research_result[0]}"
        )
        assert abs(u1_result[1] - research_result[1]) < 1e-10, (
            f"IC_mean 不一致: U1={u1_result[1]}, research={research_result[1]}"
        )
        assert abs(u1_result[2] - research_result[2]) < 1e-10, (
            f"IC_std 不一致: U1={u1_result[2]}, research={research_result[2]}"
        )

    def test_ic_ir_insufficient_samples_returns_zero(self):
        """样本不足 (<20): 两版本都返回 (0.0, 0.0, 0.0)."""
        from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
            compute_ic_ir,
        )

        from utils.alpha_factor.base import calc_ic_ir

        short_series = [0.1, 0.05, -0.02]  # 仅 3 个样本

        u1_result = calc_ic_ir(short_series, min_periods=20)
        research_result = compute_ic_ir(short_series, min_periods=20)

        assert u1_result == (0.0, 0.0, 0.0)
        assert research_result == (0.0, 0.0, 0.0)


# ============================================================
# 场景 3: PipelineResult 新增 factor_history 字段 ✅
# ============================================================

class TestPipelineResultFields:
    """PipelineResult 新增 factor_history / forward_returns_history 字段."""

    def test_pipeline_result_has_factor_history_field(self):
        """PipelineResult 实例有 factor_history 属性 (默认空 dict)."""
        from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
            PipelineResult,
        )

        result = PipelineResult(batch_id="test_001")
        assert hasattr(result, "factor_history")
        assert hasattr(result, "forward_returns_history")
        assert result.factor_history == {}
        assert result.forward_returns_history == []

    def test_pipeline_result_factor_history_writable(self):
        """PipelineResult.factor_history 可读写."""
        from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
            PipelineResult,
        )

        result = PipelineResult(batch_id="test_002")
        factor_hist = {"MOM_20D": [{"TEST000.SZ": 0.1}, {"TEST000.SZ": 0.2}]}
        fwd_returns = [{"TEST000.SZ": 0.01}, {"TEST000.SZ": -0.005}]

        result.factor_history = factor_hist
        result.forward_returns_history = fwd_returns

        assert result.factor_history == factor_hist
        assert result.forward_returns_history == fwd_returns


# ============================================================
# 场景 4: to_dict() 排除 factor_history 避免体积膨胀 ✅
# ============================================================

class TestToDictExcludesFactorHistory:
    """to_dict() 排除 factor_history / forward_returns_history."""

    def test_to_dict_excludes_factor_history(self):
        """to_dict() 返回的 dict 不含 factor_history / forward_returns_history 键."""
        from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
            PipelineResult,
        )

        result = PipelineResult(batch_id="test_003")
        result.factor_history = {"MOM_20D": [{"TEST000.SZ": 0.1}] * 120}  # 模拟大体量
        result.forward_returns_history = [{"TEST000.SZ": 0.01}] * 120

        d = result.to_dict()

        assert "factor_history" not in d, "to_dict() 不应包含 factor_history"
        assert "forward_returns_history" not in d, "to_dict() 不应包含 forward_returns_history"
        # 其他字段仍存在
        assert "batch_id" in d
        assert d["batch_id"] == "test_003"
