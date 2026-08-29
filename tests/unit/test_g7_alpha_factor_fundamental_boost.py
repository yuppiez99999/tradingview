"""G7 覆盖率冲刺 — utils/alpha_factor/fundamental.py 单元测试

目标: 覆盖率 57.84% → ≥80%
测试重点:
    - compute_value_factors: EP/BP/SP/CFP/DP/FCFP/EV_EBITDA/SP_EV/PE_EX 倒数/直接类
    - PEG 反向 (低 PEG = 高分) + 从 pe/net_profit_yoy 计算
    - PB_INT 行业调整 BP
    - compute_growth_factors: 预计算字段 + fundamentals_prev 同比计算
    - compute_quality_factors: 正向/反向 + QUICK_RATIO + GROSS_MARGIN_STAB
    - compute_leverage_factors: 反向 (低杠杆 = 高分)
    - compute_operation_factors: 正向/反向
    - 数据缺失/为零/为负的降级分支
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha_factor.fundamental import (  # noqa: E402
    compute_growth_factors,
    compute_leverage_factors,
    compute_operation_factors,
    compute_quality_factors,
    compute_value_factors,
)

# ============================================================
# compute_value_factors
# ============================================================


class TestValueFactors:
    def test_empty_fundamentals(self):
        result = compute_value_factors({})
        assert set(result.keys()) == {
            "EP",
            "BP",
            "SP",
            "CFP",
            "DP",
            "FCFP",
            "EV_EBITDA",
            "SP_EV",
            "PE_EX",
            "PEG",
            "PB_INT",
        }
        for fval in result.values():
            assert fval.values == {}
            assert fval.category == "Value"

    def test_ep_inverse(self):
        """EP = 1/pe."""
        fund = {"A": {"pe": 25.0}}
        result = compute_value_factors(fund)
        assert result["EP"].values["A"] == pytest.approx(1 / 25.0)

    def test_bp_inverse(self):
        fund = {"A": {"pb": 5.0}}
        result = compute_value_factors(fund)
        assert result["BP"].values["A"] == pytest.approx(1 / 5.0)

    def test_dp_direct(self):
        """DP = dividend_yield (直接类)."""
        fund = {"A": {"dividend_yield": 0.03}}
        result = compute_value_factors(fund)
        assert result["DP"].values["A"] == pytest.approx(0.03)

    def test_fcfp_direct(self):
        fund = {"A": {"fcf_yield": 0.05}}
        result = compute_value_factors(fund)
        assert result["FCFP"].values["A"] == pytest.approx(0.05)

    def test_pe_zero_skipped(self):
        """pe=0 → EP 跳过 (raw and raw > 0 守卫)."""
        fund = {"A": {"pe": 0}}
        result = compute_value_factors(fund)
        assert result["EP"].values == {}

    def test_pe_negative_skipped(self):
        """pe<0 → EP 跳过."""
        fund = {"A": {"pe": -10}}
        result = compute_value_factors(fund)
        assert result["EP"].values == {}

    def test_pe_missing_skipped(self):
        """pe 缺失 → EP 跳过."""
        fund = {"A": {"pb": 5.0}}  # 无 pe
        result = compute_value_factors(fund)
        assert result["EP"].values == {}

    def test_peg_from_peg_field(self):
        """PEG 从 peg 字段读取 (反向: -peg)."""
        fund = {"A": {"peg": 1.5}}
        result = compute_value_factors(fund)
        assert result["PEG"].values["A"] == pytest.approx(-1.5)

    def test_peg_from_pe_and_yoy(self):
        """无 peg 字段时从 pe/net_profit_yoy 计算."""
        fund = {"A": {"pe": 20.0, "net_profit_yoy": 10.0}}
        result = compute_value_factors(fund)
        assert result["PEG"].values["A"] == pytest.approx(-20.0 / 10.0)

    def test_peg_both_conditions(self):
        """peg 字段优先."""
        fund = {"A": {"peg": 1.5, "pe": 20.0, "net_profit_yoy": 10.0}}
        result = compute_value_factors(fund)
        assert result["PEG"].values["A"] == pytest.approx(-1.5)

    def test_peg_neither_condition_skipped(self):
        """无 peg 且 pe/yoy 不满足 → 跳过."""
        fund = {"A": {"pe": 20.0}}  # 无 net_profit_yoy
        result = compute_value_factors(fund)
        assert result["PEG"].values == {}

    def test_pb_int_no_industries(self):
        """无 industries 时 PB_INT = 1/pb."""
        fund = {"A": {"pb": 5.0}}
        result = compute_value_factors(fund)
        assert result["PB_INT"].values["A"] == pytest.approx(1 / 5.0)

    def test_pb_int_with_industries(self):
        """有 industries 时 PB_INT 做行业中性化."""
        fund = {"A": {"pb": 5.0}, "B": {"pb": 10.0}}
        industries = {"A": "tech", "B": "tech"}
        result = compute_value_factors(fund, industries)
        # BP: A=0.2, B=0.1, 行业均值=0.15, 中性化后 A=0.05, B=-0.05
        assert result["PB_INT"].values["A"] == pytest.approx(0.05)
        assert result["PB_INT"].values["B"] == pytest.approx(-0.05)

    def test_ev_ebitda_inverse(self):
        fund = {"A": {"ev_ebitda": 8.0}}
        result = compute_value_factors(fund)
        assert result["EV_EBITDA"].values["A"] == pytest.approx(1 / 8.0)

    def test_sp_ev_inverse(self):
        fund = {"A": {"sales_ev": 2.0}}
        result = compute_value_factors(fund)
        assert result["SP_EV"].values["A"] == pytest.approx(1 / 2.0)

    def test_pe_ex_inverse(self):
        fund = {"A": {"pe_excluding": 30.0}}
        result = compute_value_factors(fund)
        assert result["PE_EX"].values["A"] == pytest.approx(1 / 30.0)

    def test_multiple_symbols(self):
        fund = {"A": {"pe": 10.0}, "B": {"pe": 20.0}, "C": {"pe": 0}}
        result = compute_value_factors(fund)
        assert set(result["EP"].values.keys()) == {"A", "B"}


# ============================================================
# compute_growth_factors
# ============================================================


class TestGrowthFactors:
    def test_empty_fundamentals(self):
        result = compute_growth_factors({})
        # 5 bases × 3 suffixes = 15 因子
        assert len(result) == 15
        for fval in result.values():
            assert fval.category == "Growth"

    def test_precomputed_qoq(self):
        """从预计算字段 revenue_qoq 读取."""
        fund = {"A": {"revenue_qoq": 0.15}}
        result = compute_growth_factors(fund)
        assert result["REV_Q"].values["A"] == pytest.approx(0.15)

    def test_precomputed_yoy(self):
        fund = {"A": {"revenue_yoy": 0.20}}
        result = compute_growth_factors(fund)
        assert result["REV_YOY"].values["A"] == pytest.approx(0.20)

    def test_precomputed_ttm(self):
        fund = {"A": {"revenue_ttm": 0.10}}
        result = compute_growth_factors(fund)
        assert result["REV_TTM"].values["A"] == pytest.approx(0.10)

    def test_zero_field_skipped(self):
        """字段值为 0 → 跳过 (raw != 0 守卫)."""
        fund = {"A": {"revenue_yoy": 0}}
        result = compute_growth_factors(fund)
        assert result["REV_YOY"].values == {}

    def test_yoy_from_prev(self):
        """无预计算字段时从 fundamentals_prev 计算同比."""
        fund = {"A": {"revenue": 120.0}}
        fund_prev = {"A": {"revenue": 100.0}}
        result = compute_growth_factors(fund, fund_prev)
        # (120 - 100) / |100| = 0.20
        assert result["REV_YOY"].values["A"] == pytest.approx(0.20)

    def test_yoy_prev_zero_skipped(self):
        """prev_v=0 → 跳过 (abs(prev_v) > 1e-10 守卫)."""
        fund = {"A": {"revenue": 120.0}}
        fund_prev = {"A": {"revenue": 0}}
        result = compute_growth_factors(fund, fund_prev)
        assert result["REV_YOY"].values == {}

    def test_yoy_prev_missing_skipped(self):
        """fundamentals_prev 中无该标的 → 跳过."""
        fund = {"A": {"revenue": 120.0}}
        fund_prev = {"B": {"revenue": 100.0}}
        result = compute_growth_factors(fund, fund_prev)
        assert result["REV_YOY"].values == {}

    def test_qoq_not_computed_from_prev(self):
        """Q (环比) 不从 fundamentals_prev 计算 (仅 YOY/TTM)."""
        fund = {"A": {"revenue": 120.0}}
        fund_prev = {"A": {"revenue": 100.0}}
        result = compute_growth_factors(fund, fund_prev)
        # REV_Q 无预计算字段, 也不从 prev 计算
        assert result["REV_Q"].values == {}

    def test_all_five_bases(self):
        """5 个基础量都有预计算字段."""
        fund = {
            "A": {
                "revenue_yoy": 0.1,
                "net_profit_yoy": 0.2,
                "operating_cash_flow_yoy": 0.3,
                "roe_yoy": 0.4,
                "roa_yoy": 0.5,
            }
        }
        result = compute_growth_factors(fund)
        assert result["REV_YOY"].values["A"] == pytest.approx(0.1)
        assert result["NP_YOY"].values["A"] == pytest.approx(0.2)
        assert result["OCF_YOY"].values["A"] == pytest.approx(0.3)
        assert result["ROE_YOY"].values["A"] == pytest.approx(0.4)
        assert result["ROA_YOY"].values["A"] == pytest.approx(0.5)


# ============================================================
# compute_quality_factors
# ============================================================


class TestQualityFactors:
    def test_empty_fundamentals(self):
        result = compute_quality_factors({})
        assert len(result) == 13  # 11 + QUICK_RATIO + GROSS_MARGIN_STAB
        for fval in result.values():
            assert fval.category == "Quality"

    def test_roe_positive(self):
        fund = {"A": {"roe": 0.20}}
        result = compute_quality_factors(fund)
        assert result["ROE"].values["A"] == pytest.approx(0.20)

    def test_accruals_negative_sign(self):
        """ACCRUALS 反向 (sign=-1)."""
        fund = {"A": {"accruals": 0.5}}
        result = compute_quality_factors(fund)
        assert result["ACCRUALS"].values["A"] == pytest.approx(-0.5)

    def test_debt_to_equity_negative_sign(self):
        """DEBT_TO_EQUITY 反向."""
        fund = {"A": {"debt_to_equity": 2.0}}
        result = compute_quality_factors(fund)
        assert result["DEBT_TO_EQUITY"].values["A"] == pytest.approx(-2.0)

    def test_zero_field_skipped(self):
        """字段值为 0 → 跳过 (if raw 守卫)."""
        fund = {"A": {"roe": 0}}
        result = compute_quality_factors(fund)
        assert result["ROE"].values == {}

    def test_quick_ratio(self):
        """QUICK_RATIO = quick_ratio - current_ratio."""
        fund = {"A": {"quick_ratio": 2.0, "current_ratio": 1.5}}
        result = compute_quality_factors(fund)
        assert result["QUICK_RATIO"].values["A"] == pytest.approx(0.5)

    def test_quick_ratio_missing_one_skipped(self):
        """quick 或 current 任一为 0 → 跳过."""
        fund = {"A": {"quick_ratio": 2.0}}  # 无 current_ratio
        result = compute_quality_factors(fund)
        assert result["QUICK_RATIO"].values == {}

    def test_gross_margin_stab(self):
        """GROSS_MARGIN_STAB: stab > 0 时取值."""
        fund = {"A": {"gross_margin_stab": 5.0}}
        result = compute_quality_factors(fund)
        assert result["GROSS_MARGIN_STAB"].values["A"] == pytest.approx(5.0)

    def test_gross_margin_stab_zero_skipped(self):
        """stab <= 0 → 跳过."""
        fund = {"A": {"gross_margin_stab": 0}}
        result = compute_quality_factors(fund)
        assert result["GROSS_MARGIN_STAB"].values == {}

    def test_cash_np(self):
        fund = {"A": {"cash_to_np": 1.2}}
        result = compute_quality_factors(fund)
        assert result["CASH_NP"].values["A"] == pytest.approx(1.2)


# ============================================================
# compute_leverage_factors
# ============================================================


class TestLeverageFactors:
    def test_empty_fundamentals(self):
        result = compute_leverage_factors({})
        assert len(result) == 6
        for fval in result.values():
            assert fval.category == "Leverage"

    def test_eq_mult_negative_sign(self):
        """EQ_MULT 反向 (权益乘数, 低=高分)."""
        fund = {"A": {"eq_multiplier": 3.0}}
        result = compute_leverage_factors(fund)
        assert result["EQ_MULT"].values["A"] == pytest.approx(-3.0)

    def test_quick_positive_sign(self):
        """QUICK 正向 (速动比率, 高=高分)."""
        fund = {"A": {"quick_ratio": 2.0}}
        result = compute_leverage_factors(fund)
        assert result["QUICK"].values["A"] == pytest.approx(2.0)

    def test_int_cov_positive_sign(self):
        """INT_COV 正向 (利息保障倍数)."""
        fund = {"A": {"int_coverage": 5.0}}
        result = compute_leverage_factors(fund)
        assert result["INT_COV"].values["A"] == pytest.approx(5.0)

    def test_de_ratio_negative_sign(self):
        fund = {"A": {"debt_ratio": 0.6}}
        result = compute_leverage_factors(fund)
        assert result["DE_RATIO"].values["A"] == pytest.approx(-0.6)

    def test_zero_field_skipped(self):
        fund = {"A": {"eq_multiplier": 0}}
        result = compute_leverage_factors(fund)
        assert result["EQ_MULT"].values == {}


# ============================================================
# compute_operation_factors
# ============================================================


class TestOperationFactors:
    def test_empty_fundamentals(self):
        result = compute_operation_factors({})
        assert len(result) == 5
        for fval in result.values():
            assert fval.category == "Operation"

    def test_asset_turn_positive(self):
        fund = {"A": {"asset_turnover": 1.5}}
        result = compute_operation_factors(fund)
        assert result["ASSET_TURN"].values["A"] == pytest.approx(1.5)

    def test_cash_cycle_negative_sign(self):
        """CASH_CYCLE 反向 (短=高效=高分)."""
        fund = {"A": {"cash_cycle": 60.0}}
        result = compute_operation_factors(fund)
        assert result["CASH_CYCLE"].values["A"] == pytest.approx(-60.0)

    def test_inv_turn_positive(self):
        fund = {"A": {"inventory_turnover": 8.0}}
        result = compute_operation_factors(fund)
        assert result["INV_TURN"].values["A"] == pytest.approx(8.0)

    def test_ar_turn_positive(self):
        fund = {"A": {"ar_turnover": 10.0}}
        result = compute_operation_factors(fund)
        assert result["AR_TURN"].values["A"] == pytest.approx(10.0)

    def test_ap_turn_positive(self):
        fund = {"A": {"ap_turnover": 12.0}}
        result = compute_operation_factors(fund)
        assert result["AP_TURN"].values["A"] == pytest.approx(12.0)

    def test_zero_field_skipped(self):
        fund = {"A": {"asset_turnover": 0}}
        result = compute_operation_factors(fund)
        assert result["ASSET_TURN"].values == {}
