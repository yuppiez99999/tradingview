"""G7 覆盖率冲刺 — utils/alpha_factor/expectation.py 单元测试

目标: 覆盖率 70% → ≥85%
测试重点:
    - compute_expectation_factors 主入口
    - 预期类因子 (SUE / SUE_REVISION / CONSENSUS_2Y / RD_RATIO) 从 fundamentals 读取
    - 微观结构类因子 (MS_TAIL_VOL / MS_OPEN_BIG) 优先 price_data, 其次 fundamentals
    - 字段缺失/为零/为 None 的降级分支
    - sign 方向 (MS_TAIL_VOL 反向, MS_OPEN_BIG 正向)
    - price_data 非 dict (DataFrame 误传) 的类型守卫
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha_factor.expectation import compute_expectation_factors  # noqa: E402

# ============================================================
# 基础: 空输入
# ============================================================


class TestExpectationEmpty:
    def test_empty_fundamentals_and_price_data(self):
        result = compute_expectation_factors({}, None)
        # 6 个因子全部产出, 但 values 为空
        assert set(result.keys()) == {
            "SUE",
            "SUE_REVISION",
            "CONSENSUS_2Y",
            "RD_RATIO",
            "MS_TAIL_VOL",
            "MS_OPEN_BIG",
        }
        for fval in result.values():
            assert fval.values == {}
            assert fval.category == "Expectation"

    def test_empty_fundamentals_with_price_data(self):
        price_data = {"600519": {"tail_volume_ratio": 0.3, "open_big_buy_ratio": 0.1}}
        result = compute_expectation_factors({}, price_data)
        # 预期类为空, 微观结构类从 price_data 读取
        assert result["SUE"].values == {}
        assert "600519" in result["MS_TAIL_VOL"].values
        assert "600519" in result["MS_OPEN_BIG"].values

    def test_price_data_empty_dict(self):
        """price_data 为空 dict 时, 微观结构类降级从 fundamentals 读取."""
        fund = {"600519": {"tail_volume_ratio": 0.4, "open_big_buy_ratio": 0.2}}
        result = compute_expectation_factors(fund, {})
        # price_data 为空 dict (falsy), 跳过 price_data 分支, 从 fundamentals 读取
        assert "600519" in result["MS_TAIL_VOL"].values
        assert "600519" in result["MS_OPEN_BIG"].values


# ============================================================
# 预期类因子 (从 fundamentals)
# ============================================================


class TestExpectationFromFundamentals:
    def test_sue_positive(self):
        fund = {"000001": {"sue": 1.5}}
        result = compute_expectation_factors(fund, None)
        assert result["SUE"].values["000001"] == pytest.approx(1.5)

    def test_sue_revision_positive(self):
        fund = {"000002": {"np_revision": 0.3}}
        result = compute_expectation_factors(fund, None)
        assert result["SUE_REVISION"].values["000002"] == pytest.approx(0.3)

    def test_consensus_2y(self):
        fund = {"000003": {"consensus_2y_growth": 0.25}}
        result = compute_expectation_factors(fund, None)
        assert result["CONSENSUS_2Y"].values["000003"] == pytest.approx(0.25)

    def test_rd_ratio(self):
        fund = {"000004": {"rd_ratio": 0.08}}
        result = compute_expectation_factors(fund, None)
        assert result["RD_RATIO"].values["000004"] == pytest.approx(0.08)

    def test_field_zero_skipped(self):
        """字段值为 0 时跳过 (raw != 0 守卫)."""
        fund = {"000005": {"sue": 0, "np_revision": 0}}
        result = compute_expectation_factors(fund, None)
        assert result["SUE"].values == {}
        assert result["SUE_REVISION"].values == {}

    def test_field_none_skipped(self):
        """字段值为 None 时跳过."""
        fund = {"000006": {"sue": None}}
        result = compute_expectation_factors(fund, None)
        assert result["SUE"].values == {}

    def test_field_missing_skipped(self):
        """字段缺失时跳过 (fund.get(fld, None) 返回 None)."""
        fund = {"000007": {"pe": 10}}  # 无 sue 字段
        result = compute_expectation_factors(fund, None)
        assert result["SUE"].values == {}

    def test_multiple_symbols(self):
        fund = {
            "A": {"sue": 1.0},
            "B": {"sue": 2.0},
            "C": {"sue": -0.5},
        }
        result = compute_expectation_factors(fund, None)
        assert set(result["SUE"].values.keys()) == {"A", "B", "C"}
        assert result["SUE"].values["C"] == pytest.approx(-0.5)


# ============================================================
# 微观结构类因子 (price_data 优先, fundamentals 兜底)
# ============================================================


class TestMicrostructureFactors:
    def test_ms_tail_vol_from_price_data_reverse_sign(self):
        """MS_TAIL_VOL 反向 (sign=-1): 高尾盘占比 = 低分."""
        price_data = {"600519": {"tail_volume_ratio": 0.4}}
        result = compute_expectation_factors({}, price_data)
        assert result["MS_TAIL_VOL"].values["600519"] == pytest.approx(-0.4)

    def test_ms_open_big_from_price_data_positive_sign(self):
        """MS_OPEN_BIG 正向 (sign=1)."""
        price_data = {"600519": {"open_big_buy_ratio": 0.15}}
        result = compute_expectation_factors({}, price_data)
        assert result["MS_OPEN_BIG"].values["600519"] == pytest.approx(0.15)

    def test_price_data_takes_priority_over_fundamentals(self):
        """price_data 优先; fundamentals 仅补充 price_data 中没有的 symbol."""
        price_data = {"A": {"tail_volume_ratio": 0.1}}
        fund = {"A": {"tail_volume_ratio": 0.9}, "B": {"tail_volume_ratio": 0.5}}
        result = compute_expectation_factors(fund, price_data)
        # A 用 price_data 的 0.1 (反向 -0.1), 不被 fundamentals 覆盖
        assert result["MS_TAIL_VOL"].values["A"] == pytest.approx(-0.1)
        # B 仅在 fundamentals, 用 0.5
        assert result["MS_TAIL_VOL"].values["B"] == pytest.approx(-0.5)

    def test_micro_field_zero_skipped(self):
        price_data = {"A": {"tail_volume_ratio": 0}}
        result = compute_expectation_factors({}, price_data)
        assert result["MS_TAIL_VOL"].values == {}

    def test_micro_field_none_skipped(self):
        price_data = {"A": {"tail_volume_ratio": None}}
        result = compute_expectation_factors({}, price_data)
        assert result["MS_TAIL_VOL"].values == {}

    def test_price_data_not_dict_skipped(self):
        """price_data 非 dict (如 DataFrame 误传) 时, 类型守卫跳过 price_data 分支."""
        fund = {"A": {"tail_volume_ratio": 0.3, "open_big_buy_ratio": 0.2}}
        # 模拟误传 DataFrame (有 __iter__ 但非 dict)
        not_dict = [("A", {"tail_volume_ratio": 0.9})]
        result = compute_expectation_factors(fund, not_dict)  # type: ignore[arg-type]
        # price_data 非 dict, 跳过 price_data 分支, 从 fundamentals 读取
        assert result["MS_TAIL_VOL"].values.get("A") == pytest.approx(-0.3)

    def test_micro_from_fundamentals_only(self):
        """price_data=None 时, 微观结构类从 fundamentals 读取."""
        fund = {"A": {"tail_volume_ratio": 0.6, "open_big_buy_ratio": 0.4}}
        result = compute_expectation_factors(fund, None)
        assert result["MS_TAIL_VOL"].values["A"] == pytest.approx(-0.6)
        assert result["MS_OPEN_BIG"].values["A"] == pytest.approx(0.4)


# ============================================================
# 综合: 因子结构完整性
# ============================================================


class TestFactorStructure:
    def test_all_factors_have_correct_category(self):
        fund = {"A": {"sue": 1.0, "tail_volume_ratio": 0.1}}
        result = compute_expectation_factors(fund, None)
        for fval in result.values():
            assert fval.category == "Expectation"

    def test_factor_names_match_keys(self):
        result = compute_expectation_factors({}, None)
        for name, fval in result.items():
            assert fval.name == name

    def test_mixed_input(self):
        """混合: 预期类从 fundamentals, 微观结构类从 price_data + fundamentals."""
        price_data = {"A": {"tail_volume_ratio": 0.2}}
        fund = {
            "A": {"sue": 1.0, "open_big_buy_ratio": 0.1},
            "B": {"sue": 2.0, "tail_volume_ratio": 0.3, "open_big_buy_ratio": 0.05},
        }
        result = compute_expectation_factors(fund, price_data)
        # 预期类: A/B 都有 sue
        assert result["SUE"].values["A"] == pytest.approx(1.0)
        assert result["SUE"].values["B"] == pytest.approx(2.0)
        # MS_TAIL_VOL: A 从 price_data (0.2), B 从 fundamentals (0.3)
        assert result["MS_TAIL_VOL"].values["A"] == pytest.approx(-0.2)
        assert result["MS_TAIL_VOL"].values["B"] == pytest.approx(-0.3)
        # MS_OPEN_BIG: A/B 都从 fundamentals (price_data 中无该字段)
        assert result["MS_OPEN_BIG"].values["A"] == pytest.approx(0.1)
        assert result["MS_OPEN_BIG"].values["B"] == pytest.approx(0.05)
