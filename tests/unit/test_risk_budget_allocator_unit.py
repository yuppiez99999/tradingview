"""RiskBudgetAllocator 单元测试.

被测模块: utils/risk_budget_allocator.py
覆盖目标: >=85%
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk_budget_allocator import RiskBudgetAllocator  # noqa: E402


class RiskBudgetAllocatorTest:
    """RiskBudgetAllocator 单元测试."""

    # ------ __init__ ------
    def test_init_defaults(self):
        alloc = RiskBudgetAllocator()
        assert alloc.total_capital == 3_000_000
        assert alloc.target_return == 0.08
        assert alloc.max_dd == 0.15
        assert alloc.single_trade_risk == 0.015
        assert alloc.daily_budget_limit == 200_000

    def test_init_custom(self):
        alloc = RiskBudgetAllocator(total_capital=1_000_000, daily_budget_limit=50_000)
        assert alloc.total_capital == 1_000_000
        assert alloc.daily_budget_limit == 50_000

    # ------ estimate_symbol_risk ------
    def test_estimate_symbol_risk_none_prices(self):
        alloc = RiskBudgetAllocator()
        assert alloc.estimate_symbol_risk("000001", prices=None) == 0.25

    def test_estimate_symbol_risk_insufficient_prices(self):
        alloc = RiskBudgetAllocator()
        prices = np.array([10.0, 11.0, 12.0])  # len=3 < 20
        assert alloc.estimate_symbol_risk("000001", prices=prices) == 0.25

    def test_estimate_symbol_risk_normal(self):
        alloc = RiskBudgetAllocator()
        rng = np.random.default_rng(42)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=50)))
        vol = alloc.estimate_symbol_risk("000001", prices=prices)
        assert vol > 0
        # 年化波动率应在合理范围
        assert 0.05 < vol < 1.0

    def test_estimate_symbol_risk_all_nan(self):
        alloc = RiskBudgetAllocator()
        prices = np.full(30, np.nan)
        assert alloc.estimate_symbol_risk("000001", prices=prices) == 0.25

    def test_estimate_symbol_risk_custom_default_vol(self):
        alloc = RiskBudgetAllocator()
        assert alloc.estimate_symbol_risk("000001", prices=None, default_vol=0.4) == 0.4

    # ------ allocate_daily_budget 空入参 ------
    def test_allocate_empty_pending_positions(self):
        alloc = RiskBudgetAllocator()
        assert alloc.allocate_daily_budget([]) == {}

    def test_allocate_no_returns_matrix_equal_weight(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        result = alloc.allocate_daily_budget(positions)
        assert "000001" in result
        assert result["000001"]["allocated"] > 0
        assert result["000001"]["weight"] == 1.0

    def test_allocate_returns_matrix_none_equal_weight(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        result = alloc.allocate_daily_budget(positions, returns_matrix=None)
        assert "000001" in result

    # ------ remaining <= 0 跳过 ------
    def test_allocate_remaining_zero_skipped(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 0}]
        result = alloc.allocate_daily_budget(positions)
        assert result == {}

    def test_allocate_remaining_negative_skipped(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": -100}]
        result = alloc.allocate_daily_budget(positions)
        assert result == {}

    # ------ 信号调整 ------
    def test_allocate_strong_bearish_skipped(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        signals = {
            "000001": {"direction": "DOWN", "confidence": 0.8, "signal_strength": -0.6}
        }
        result = alloc.allocate_daily_budget(positions, signals=signals)
        assert result["000001"]["allocated"] == 0.0

    def test_allocate_strong_bullish_boost(self):
        alloc = RiskBudgetAllocator()
        # remaining 足够大，避免 base_allocated 被 remaining 截断
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 500000}]
        signals_neutral = {
            "000001": {
                "direction": "NEUTRAL",
                "confidence": 0.0,
                "signal_strength": 0.0,
            }
        }
        signals_bull = {
            "000001": {"direction": "UP", "confidence": 0.8, "signal_strength": 0.6}
        }
        r_neutral = alloc.allocate_daily_budget(positions, signals=signals_neutral)
        r_bull = alloc.allocate_daily_budget(positions, signals=signals_bull)
        # 强看多加码 1.3 倍
        assert r_bull["000001"]["allocated"] > r_neutral["000001"]["allocated"]

    def test_allocate_macro_strong_alignment_boost(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        macro = {"000001": {"combined_score": 1.2}}  # >= 1.15
        result = alloc.allocate_daily_budget(positions, macro_scores=macro)
        assert result["000001"]["allocated"] > 0

    def test_allocate_macro_weak_reduced(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        macro = {"000001": {"combined_score": 0.8}}  # < 0.85
        result = alloc.allocate_daily_budget(positions, macro_scores=macro)
        assert result["000001"]["allocated"] > 0  # 缩减但非零

    def test_allocate_macro_skip_reachable_after_fix(self):
        # 特征化测试: 修复后 combined < 0.7 跳过分支可达 (原死代码消除).
        # combined=0.6 命中跳过分支 → allocated=0.0 (而非缩减分支的 base×0.8).
        # 对齐 spec §5.1.1.3 条件优先级规则与 AC-A1.
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 500000}]
        macro = {"000001": {"combined_score": 0.6}}  # < 0.7 → 跳过, allocated=0.0
        result = alloc.allocate_daily_budget(positions, macro_scores=macro)
        assert result["000001"]["allocated"] == 0.0

    def test_allocate_macro_skip_boundary(self):
        # 边界值锁定: combined=0.6 与 combined=0.69 均命中跳过分支 → allocated=0.0.
        # 对齐 AC-A1 (跳过分支可达) 与 spec §5.1.3.2 边界条件.
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        for combined in (0.6, 0.69):
            macro = {"000001": {"combined_score": combined}}
            result = alloc.allocate_daily_budget(positions, macro_scores=macro)
            assert result["000001"]["allocated"] == 0.0, (
                f"combined={combined} 应命中跳过分支 (allocated=0.0), "
                f"实际 allocated={result['000001']['allocated']}"
            )

    def test_allocate_macro_reduce_still_reachable(self):
        # 确认缩减分支仍可达, 未被跳过分支遮蔽.
        # combined=0.75 (0.7 <= combined < 0.85) → allocated == base × 0.8.
        # 对齐 AC-A2 (缩减分支仍可达) 与 spec §5.1.1.3 条件互斥语义.
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        # 基线: combined=1.0 (默认, 不命中任何 macro 分支)
        base_result = alloc.allocate_daily_budget(positions)
        base_allocated = base_result["000001"]["allocated"]
        # 缩减: combined=0.75 → 命中缩减分支, allocated = base × 0.8
        macro = {"000001": {"combined_score": 0.75}}
        reduced_result = alloc.allocate_daily_budget(positions, macro_scores=macro)
        assert reduced_result["000001"]["allocated"] == pytest.approx(
            base_allocated * 0.8
        )
        assert reduced_result["000001"]["allocated"] > 0  # 缩减但非零

    @pytest.mark.skip(
        reason="原死分支已消除: combined<0.7 现可达 (条件顺序前移至 combined<0.85 之前), "
        "此测试锁定历史遮蔽行为, 详见 spec §5.1.3.2 异常场景 2"
    )
    def test_allocate_macro_very_weak_historical_dead_branch(self):
        # 历史死分支锁定: 修复前 combined<0.7 被 combined<0.85 遮蔽, 永不可达.
        # 修复后该分支可达 (见 test_allocate_macro_skip_reachable_after_fix).
        # 此测试保留以记录历史遮蔽原因, 不再执行.
        pass

    # ------ ETF 弱信号缩减 ------
    def test_allocate_etf_weak_signal_reduced(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        etf_signals = {"000001": "关注-弱信号"}
        # combined < 1.0 (默认 combined=1.0，用 macro 使其 < 1.0 但 >= 0.85)
        macro = {"000001": {"combined_score": 0.9}}
        r_with_etf = alloc.allocate_daily_budget(
            positions, macro_scores=macro, etf_signals=etf_signals
        )
        r_no_etf = alloc.allocate_daily_budget(positions, macro_scores=macro)
        assert r_with_etf["000001"]["allocated"] < r_no_etf["000001"]["allocated"]

    # ------ 单日预算上限 ------
    def test_allocate_daily_budget_limit(self):
        alloc = RiskBudgetAllocator(daily_budget_limit=50000)
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 1000000}]
        result = alloc.allocate_daily_budget(positions)
        assert result["000001"]["allocated"] <= 50000

    # ------ 预算归一化 ------
    def test_allocate_budget_normalization(self):
        alloc = RiskBudgetAllocator(daily_budget_limit=100000, total_capital=10_000_000)
        positions = [
            {"code": "000001", "code_clean": "000001", "remaining": 1000000},
            {"code": "000002", "code_clean": "000002", "remaining": 1000000},
            {"code": "000003", "code_clean": "000003", "remaining": 1000000},
        ]
        result = alloc.allocate_daily_budget(positions)
        total = sum(v["allocated"] for v in result.values())
        # 归一化后总分配不超上限
        assert total <= 100000 + 1

    # ------ returns_matrix 路径（risk_budgeter 不存在 → 回退等权）------
    def test_allocate_returns_matrix_fallback_equal_weight(self):
        alloc = RiskBudgetAllocator()
        positions = [
            {"code": "000001", "code_clean": "000001", "remaining": 100000},
            {"code": "000002", "code_clean": "000002", "remaining": 100000},
        ]
        rng = np.random.default_rng(7)
        rm = pd.DataFrame(
            rng.normal(0, 0.01, size=(50, 2)), columns=["000001", "000002"]
        )
        result = alloc.allocate_daily_budget(positions, returns_matrix=rm)
        # risk_budgeter 不存在 → spec is None → 回退等权
        assert len(result) == 2
        assert abs(result["000001"]["weight"] - 0.5) < 1e-9

    def test_allocate_returns_matrix_single_column_skips_risk_parity(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        rng = np.random.default_rng(7)
        rm = pd.DataFrame(rng.normal(0, 0.01, size=(50, 1)), columns=["000001"])
        result = alloc.allocate_daily_budget(positions, returns_matrix=rm)
        # shape[1]=1 < 2 → 跳过 Risk Parity
        assert "000001" in result

    # ------ mock Risk Parity 成功路径 ------
    def test_allocate_risk_parity_success(self, monkeypatch):
        alloc = RiskBudgetAllocator()
        positions = [
            {"code": "000001", "code_clean": "000001", "remaining": 100000},
            {"code": "000002", "code_clean": "000002", "remaining": 100000},
        ]
        rng = np.random.default_rng(7)
        rm = pd.DataFrame(
            rng.normal(0, 0.01, size=(50, 2)), columns=["000001", "000002"]
        )

        mock_budgeter = MagicMock()
        mock_budgeter.risk_parity_weights.return_value = np.array([0.3, 0.7])
        mock_mod = MagicMock()
        mock_mod.RiskBudgeter.return_value = mock_budgeter

        mock_spec = MagicMock()
        mock_spec.loader.exec_module.side_effect = lambda m: setattr(
            m, "RiskBudgeter", mock_mod.RiskBudgeter
        )

        monkeypatch.setattr(
            importlib.util, "spec_from_file_location", lambda *a, **k: mock_spec
        )
        result = alloc.allocate_daily_budget(positions, returns_matrix=rm)
        assert abs(result["000001"]["weight"] - 0.3) < 1e-9
        assert abs(result["000002"]["weight"] - 0.7) < 1e-9

    def test_allocate_risk_parity_dimension_mismatch(self, monkeypatch):
        alloc = RiskBudgetAllocator()
        positions = [
            {"code": "000001", "code_clean": "000001", "remaining": 100000},
            {"code": "000002", "code_clean": "000002", "remaining": 100000},
        ]
        rng = np.random.default_rng(7)
        rm = pd.DataFrame(
            rng.normal(0, 0.01, size=(50, 2)), columns=["000001", "000002"]
        )

        mock_budgeter = MagicMock()
        mock_budgeter.risk_parity_weights.return_value = np.array(
            [0.5, 0.3, 0.2]
        )  # len=3 != n=2
        mock_mod = MagicMock()
        mock_mod.RiskBudgeter.return_value = mock_budgeter

        mock_spec = MagicMock()
        mock_spec.loader.exec_module.side_effect = lambda m: setattr(
            m, "RiskBudgeter", mock_mod.RiskBudgeter
        )

        monkeypatch.setattr(
            importlib.util, "spec_from_file_location", lambda *a, **k: mock_spec
        )
        result = alloc.allocate_daily_budget(positions, returns_matrix=rm)
        # 维度不匹配 → 回退等权
        assert abs(result["000001"]["weight"] - 0.5) < 1e-9

    def test_allocate_risk_parity_exec_module_exception(self, monkeypatch):
        alloc = RiskBudgetAllocator()
        positions = [
            {"code": "000001", "code_clean": "000001", "remaining": 100000},
            {"code": "000002", "code_clean": "000002", "remaining": 100000},
        ]
        rng = np.random.default_rng(7)
        rm = pd.DataFrame(
            rng.normal(0, 0.01, size=(50, 2)), columns=["000001", "000002"]
        )

        mock_spec = MagicMock()
        mock_spec.loader.exec_module.side_effect = RuntimeError("exec failed")

        monkeypatch.setattr(
            importlib.util, "spec_from_file_location", lambda *a, **k: mock_spec
        )
        result = alloc.allocate_daily_budget(positions, returns_matrix=rm)
        # 异常 → 回退等权
        assert abs(result["000001"]["weight"] - 0.5) < 1e-9

    # ------ 多标的综合 ------
    def test_allocate_multiple_positions(self):
        alloc = RiskBudgetAllocator()
        positions = [
            {"code": "000001", "code_clean": "000001", "remaining": 100000},
            {"code": "000002", "code_clean": "000002", "remaining": 200000},
        ]
        result = alloc.allocate_daily_budget(positions)
        assert len(result) == 2
        assert result["000001"]["allocated"] > 0
        assert result["000002"]["allocated"] > 0

    # ------ reason 字段 ------
    def test_allocate_reason_field_format(self):
        alloc = RiskBudgetAllocator()
        positions = [{"code": "000001", "code_clean": "000001", "remaining": 100000}]
        result = alloc.allocate_daily_budget(positions)
        assert "risk_parity_weight" in result["000001"]["reason"]
        assert "single_risk" in result["000001"]["reason"]
