"""G7 覆盖率冲刺 — utils/risk_constraints.py 单元测试

目标: 覆盖率 73.64% → ≥85%
测试重点:
    - enforce_hard_constraints: 单标的上限/板块上限/归一化/负权重/违例记录
    - validate_risk_budget: 集中度/板块/VaR 多维校验
    - _approx_var: 历史收益率 VaR 近似 (pct_change / 默认波动率降级)
    - BUG-04 修复: 归一化后板块二次压缩
    - V3 优化: 默认 max_weight=0.10
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.risk_constraints import (  # noqa: E402
    DEFAULT_MAX_DAILY_VAR,
    DEFAULT_MAX_SECTOR,
    DEFAULT_MAX_SINGLE_VAR,
    DEFAULT_MAX_WEIGHT,
    _approx_var,
    enforce_hard_constraints,
    validate_risk_budget,
)

# ============================================================
# enforce_hard_constraints: 单标的硬上限
# ============================================================

class TestEnforceSingleWeight:
    def test_within_limit_no_violation(self):
        weights = {"A": 0.05, "B": 0.05}
        clamped, violations = enforce_hard_constraints(weights)
        assert violations == []
        assert clamped == {"A": 0.05, "B": 0.05}

    def test_exceed_max_weight_clamped(self):
        weights = {"A": 0.15, "B": 0.05}  # A 超 10%
        clamped, violations = enforce_hard_constraints(weights)
        assert clamped["A"] == pytest.approx(0.10)
        assert any("A" in v for v in violations)

    def test_default_max_weight_is_10pct(self):
        assert DEFAULT_MAX_WEIGHT == 0.10

    def test_custom_max_weight(self):
        weights = {"A": 0.20}
        clamped, violations = enforce_hard_constraints(weights, max_weight=0.25)
        assert clamped["A"] == pytest.approx(0.20)
        assert violations == []

    def test_negative_weight_clamped_to_zero(self):
        weights = {"A": -0.05, "B": 0.10}
        clamped, violations = enforce_hard_constraints(weights)
        assert clamped["A"] == 0.0
        # 负权重不产生违例 (仅截断到 0)

    def test_zero_weights(self):
        clamped, violations = enforce_hard_constraints({})
        assert clamped == {}
        assert violations == []


# ============================================================
# enforce_hard_constraints: 板块集中度
# ============================================================

class TestEnforceSectorLimit:
    def test_sector_within_limit(self):
        weights = {"A": 0.08, "B": 0.07}
        sector_map = {"A": "tech", "B": "tech"}  # 合计 15% < 25%
        clamped, violations = enforce_hard_constraints(weights, sector_map=sector_map)
        assert violations == []
        assert clamped["A"] == pytest.approx(0.08)

    def test_sector_exceed_limit_compressed(self):
        weights = {"A": 0.10, "B": 0.10, "C": 0.10}  # tech 合计 30% > 25%
        sector_map = {"A": "tech", "B": "tech", "C": "tech"}
        clamped, violations = enforce_hard_constraints(weights, sector_map=sector_map)
        # 压缩到 25%: 每个权重按比例缩放
        sector_total = clamped["A"] + clamped["B"] + clamped["C"]
        assert sector_total <= 0.25 + 1e-6
        assert any("tech" in v for v in violations)

    def test_sector_unknown_not_checked(self):
        """sector_map 中未映射的标的归为 'unknown', 不做板块校验."""
        weights = {"A": 0.10, "B": 0.10}
        sector_map = {"A": "tech"}  # B 未映射
        clamped, violations = enforce_hard_constraints(weights, sector_map=sector_map)
        # B 归 unknown, 不触发板块违例
        assert not any("unknown" in v for v in violations)

    def test_sector_no_map_skipped(self):
        weights = {"A": 0.10, "B": 0.10}
        clamped, violations = enforce_hard_constraints(weights, sector_map=None)
        assert violations == []

    def test_sector_multiple_sectors_both_exceed(self):
        """多个板块同时超限, 各自独立压缩."""
        weights = {
            "A": 0.10, "B": 0.10, "C": 0.10,  # tech 30%
            "D": 0.10, "E": 0.10, "F": 0.10,  # fin 30%
        }
        sector_map = {
            "A": "tech", "B": "tech", "C": "tech",
            "D": "fin", "E": "fin", "F": "fin",
        }
        clamped, violations = enforce_hard_constraints(weights, sector_map=sector_map)
        tech_total = clamped["A"] + clamped["B"] + clamped["C"]
        fin_total = clamped["D"] + clamped["E"] + clamped["F"]
        assert tech_total <= 0.25 + 1e-6
        assert fin_total <= 0.25 + 1e-6
        assert any("tech" in v for v in violations)
        assert any("fin" in v for v in violations)


# ============================================================
# enforce_hard_constraints: 归一化
# ============================================================

class TestEnforceNormalization:
    def test_total_exceed_100pct_normalized(self):
        weights = {"A": 0.10, "B": 0.10, "C": 0.10, "D": 0.10, "E": 0.10}  # 合计 50% < 100%
        clamped, violations = enforce_hard_constraints(weights)
        # 不超 100%, 不归一化
        assert violations == []
        assert sum(clamped.values()) == pytest.approx(0.5)

    def test_total_exceed_100pct_triggers_normalization(self):
        # 11 个标的各 10% = 110%, 触发归一化
        weights = {f"S{i}": 0.10 for i in range(11)}
        clamped, violations = enforce_hard_constraints(weights)
        assert any("100%" in v or "归一化" in v for v in violations)
        assert sum(clamped.values()) <= 1.0 + 1e-6

    def test_normalization_preserves_relative_weights(self):
        weights = {"A": 0.20, "B": 0.10}  # 合计 30%, 不触发
        clamped, _ = enforce_hard_constraints(weights, max_weight=0.25)
        # A=0.20, B=0.10, 合计 0.30 < 1.0, 不归一化
        assert clamped["A"] == pytest.approx(0.20)
        assert clamped["B"] == pytest.approx(0.10)


# ============================================================
# enforce_hard_constraints: BUG-04 修复 (归一化后板块二次压缩)
# ============================================================

class TestEnforceBug04Fix:
    def test_normalization_then_sector_recompress(self):
        """归一化后板块仍超限时二次压缩."""
        # 构造: 单标的截断后总权重 > 100%, 归一化后板块仍超限
        weights = {f"S{i}": 0.10 for i in range(12)}  # 12 * 10% = 120%
        sector_map = {f"S{i}": "tech" for i in range(12)}  # 全部同一板块
        clamped, violations = enforce_hard_constraints(weights, sector_map=sector_map)
        # 归一化后板块应被压缩到 25% 以下
        sector_total = sum(clamped.values())
        assert sector_total <= 0.25 + 1e-6


# ============================================================
# validate_risk_budget: 集中度校验
# ============================================================

class TestValidateRiskBudgetConcentration:
    def test_within_limits_ok(self):
        weights = {"A": 0.05, "B": 0.05}
        ok, violations = validate_risk_budget(weights)
        assert ok is True
        assert violations == []

    def test_exceed_max_weight_violation(self):
        weights = {"A": 0.15, "B": 0.05}  # A 超 10%
        ok, violations = validate_risk_budget(weights)
        assert ok is False
        assert any("A" in v and "10.00%" in v for v in violations)

    def test_sector_exceed_violation(self):
        weights = {"A": 0.10, "B": 0.10, "C": 0.10}  # tech 30% > 25%
        sector_map = {"A": "tech", "B": "tech", "C": "tech"}
        ok, violations = validate_risk_budget(weights, sector_map=sector_map)
        assert ok is False
        assert any("tech" in v for v in violations)

    def test_empty_weights_ok(self):
        ok, violations = validate_risk_budget({})
        assert ok is True
        assert violations == []

    def test_sector_within_limit_no_violation(self):
        """validate_risk_budget 板块未超限 (覆盖 138->137 分支)."""
        weights = {"A": 0.08, "B": 0.07}
        sector_map = {"A": "tech", "B": "tech"}  # 合计 15% < 25%
        ok, violations = validate_risk_budget(weights, sector_map=sector_map)
        assert ok is True
        assert violations == []

    def test_sector_unknown_skipped_in_validate(self):
        """validate_risk_budget 中 unknown 板块跳过校验."""
        weights = {"A": 0.10, "B": 0.10}
        sector_map = {"A": "tech"}  # B 未映射 → unknown
        ok, violations = validate_risk_budget(weights, sector_map=sector_map)
        # B 归 unknown 不校验, A=10% < 25%, 通过
        assert ok is True

    def test_price_data_empty_dict_skips_var(self):
        """price_data={} (空 dict) 时跳过 VaR 校验."""
        weights = {"A": 0.05}
        ok, violations = validate_risk_budget(weights, price_data={})
        assert ok is True


# ============================================================
# validate_risk_budget: VaR 校验
# ============================================================

class TestValidateRiskBudgetVar:
    def test_no_price_data_skips_var(self):
        """无 price_data 时跳过 VaR 校验."""
        weights = {"A": 0.05}
        ok, violations = validate_risk_budget(weights, price_data=None)
        assert ok is True

    def test_var_within_limit(self):
        """提供 price_data 但 VaR 在限内."""
        weights = {"A": 0.05}
        # 构造低波动价格序列
        prices = pd.Series(np.linspace(100, 101, 50))
        ok, violations = validate_risk_budget(weights, price_data={"A": prices})
        assert ok is True

    def test_var_exceed_limit(self):
        """构造高波动使组合 VaR 超限."""
        weights = {"A": 0.10}
        # 构造极端波动: 大幅下跌的尾部
        np.random.seed(42)
        rets = np.concatenate([np.random.normal(0, 0.001, 100), [-0.5]])  # 尾部 -50%
        prices = pd.Series(np.cumprod(1 + rets) * 100)
        ok, violations = validate_risk_budget(
            weights, price_data={"A": prices}, max_daily_var=0.001
        )
        assert ok is False
        assert any("VaR" in v for v in violations)

    def test_single_var_exceed(self):
        """单标的 VaR 超限: 构造多个极端负值落入 5% 分位数, 使单标的 VaR * |w| 超限."""
        weights = {"A": 0.10}
        # 6 个 -0.5 尾部: 106 点的 5% 分位 idx=4, sorted_r[4]=-0.5, var_sym=0.5
        # single = 0.5 * 0.1 = 0.05 > max_single_var=0.001
        np.random.seed(0)
        rets = np.concatenate([np.random.normal(0, 0.001, 100), [-0.5] * 6])
        prices = pd.Series(np.cumprod(1 + rets) * 100)
        ok, violations = validate_risk_budget(
            weights, price_data={"A": prices}, max_single_var=0.001
        )
        assert ok is False
        assert any("VaR" in v for v in violations)


# ============================================================
# _approx_var: 内部 VaR 近似
# ============================================================

class TestApproxVar:
    def test_empty_weights_returns_zero(self):
        port_var, single = _approx_var({}, {}, 1_000_000)
        assert port_var == 0.0
        assert single == {}

    def test_no_price_data_uses_default_vol(self):
        """无价格序列时用默认波动率近似."""
        weights = {"A": 0.10}
        port_var, single = _approx_var(weights, {}, 1_000_000)
        # A 无价格 → 用 default_vol_daily * 1.65 * |w|
        assert "A" in single
        assert single["A"] > 0
        # 无有效收益序列 → port_var=0
        assert port_var == 0.0

    def test_short_series_uses_default_vol(self):
        """价格序列 < 5 个点时用默认波动率."""
        weights = {"A": 0.10}
        prices = pd.Series([100, 101, 102])  # 仅 3 个点
        port_var, single = _approx_var(weights, {"A": prices}, 1_000_000)
        assert single["A"] > 0
        assert port_var == 0.0  # 无足够样本构建组合

    def test_valid_series_computes_var(self):
        """有效价格序列计算 VaR."""
        weights = {"A": 0.10, "B": 0.10}
        np.random.seed(42)
        prices_a = pd.Series(np.cumprod(1 + np.random.normal(0, 0.01, 100)) * 100)
        prices_b = pd.Series(np.cumprod(1 + np.random.normal(0, 0.01, 100)) * 100)
        port_var, single = _approx_var(
            weights, {"A": prices_a, "B": prices_b}, 1_000_000
        )
        assert port_var >= 0
        assert "A" in single and "B" in single

    def test_pct_change_exception_fallback(self):
        """pct_change 抛异常时降级到默认波动率."""
        weights = {"A": 0.10}

        class BadSeries:
            def pct_change(self):
                raise ValueError("forced")

        port_var, single = _approx_var(weights, {"A": BadSeries()}, 1_000_000)
        assert single["A"] > 0  # 用默认波动率
        assert port_var == 0.0


# ============================================================
# 默认常量
# ============================================================

class TestDefaultConstants:
    def test_default_max_sector(self):
        assert DEFAULT_MAX_SECTOR == 0.25

    def test_default_max_daily_var(self):
        assert DEFAULT_MAX_DAILY_VAR == 0.015

    def test_default_max_single_var(self):
        assert DEFAULT_MAX_SINGLE_VAR == 0.008
