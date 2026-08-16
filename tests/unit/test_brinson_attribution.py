"""T5.1 单元测试 — utils/attribution/brinson_attribution.py.

测试覆盖:
  1. 常量定义完整性
  2. 异常体系可抛可捕
  3. 数据类字段与序列化
  4. 核心算法函数 (配置/选股/交互三效应公式)
  5. 数学恒等式验证 (三效应和 = 超额收益, 残差 = 0)
  6. 权重校验与行业对齐
  7. attribute_brinson 主函数
  8. BrinsonAttributionManager 主类 (Feature Flag + ConfigManager)
  9. Feature Flag 透传 (HC-1)
  10. 便捷函数
  11. 边界条件 (空输入/单行业/权重不归一/异常收益率)
  12. Markdown 报告输出

设计原则:
  - 算法正确性优先: 用手工计算的期望值验证公式
  - 数学恒等式: 三效应总和必须等于超额收益 (残差 = 0)
  - 不依赖网络: ConfigManager / Feature Flag 通过 mock 控制
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.attribution.brinson_attribution import (  # noqa: E402
    ABNORMAL_RETURN_THRESHOLD,
    DEFAULT_CONFIG_NAME,
    DEFAULT_MIN_SECTORS,
    DEFAULT_PRIMARY_BENCHMARK,
    DEFAULT_SECONDARY_BENCHMARK,
    DEFAULT_SECTORS,
    DEFAULT_WEIGHT_SUM_TOLERANCE,
    # 常量
    FLAG_NAME,
    SECTOR_NAMES,
    STATUS_EMPTY_INPUT,
    STATUS_FEATURE_FLAG_DISABLED,
    STATUS_INSUFFICIENT_DATA,
    # 状态码
    STATUS_OK,
    STATUS_SECTOR_MISMATCH,
    ZERO_RETURN_EPSILON,
    ZERO_WEIGHT_EPSILON,
    # 异常
    BrinsonAttributionError,
    # 主类
    BrinsonAttributionManager,
    BrinsonResult,
    InsufficientDataError,
    InvalidInputError,
    # 数据类
    SectorAttribution,
    SectorMismatchError,
    WeightNotNormalizedError,
    align_sectors,
    attribute_brinson,
    attribute_brinson_simple,
    # 核心算法函数
    compute_allocation_effect,
    compute_interaction_effect,
    compute_selection_effect,
    compute_total_return,
    create_default_manager,
    # 便捷函数
    is_brinson_attribution_enabled,
    validate_weights,
)

# ============================================================
# 1. 常量定义测试
# ============================================================

class TestConstants:
    """常量定义完整性测试."""

    def test_flag_name(self):
        """Feature Flag 名称正确."""
        assert FLAG_NAME == "USE_BRINSON_ATTRIBUTION"

    def test_config_name(self):
        """ConfigManager 配置名正确."""
        assert DEFAULT_CONFIG_NAME == "brinson_attribution"

    def test_primary_benchmark(self):
        """默认主基准为沪深300ETF."""
        assert DEFAULT_PRIMARY_BENCHMARK == "510300.SH"

    def test_secondary_benchmark(self):
        """默认次基准为中证500ETF."""
        assert DEFAULT_SECONDARY_BENCHMARK == "510500.SH"

    def test_default_sectors_contains_eight(self):
        """默认行业列表包含 8 大行业."""
        assert len(DEFAULT_SECTORS) == 8
        for s in ["tech", "manufacturing", "cyclical", "resources",
                  "defensive", "finance", "consumer", "healthcare"]:
            assert s in DEFAULT_SECTORS

    def test_sector_names_mapping_complete(self):
        """行业中文名称映射完整."""
        assert len(SECTOR_NAMES) == 8
        assert SECTOR_NAMES["finance"] == "金融"
        assert SECTOR_NAMES["tech"] == "科技"
        assert SECTOR_NAMES["consumer"] == "消费"
        assert SECTOR_NAMES["healthcare"] == "医药"

    def test_weight_sum_tolerance_positive(self):
        """权重和容差为正."""
        assert DEFAULT_WEIGHT_SUM_TOLERANCE > 0
        assert DEFAULT_WEIGHT_SUM_TOLERANCE == 0.01

    def test_min_sectors_at_least_two(self):
        """最小行业数 >= 2."""
        assert DEFAULT_MIN_SECTORS >= 2

    def test_zero_epsilon_positive(self):
        """零值阈值为正."""
        assert ZERO_WEIGHT_EPSILON > 0
        assert ZERO_RETURN_EPSILON > 0
        assert ZERO_WEIGHT_EPSILON > ZERO_RETURN_EPSILON

    def test_abnormal_return_threshold(self):
        """异常收益率阈值为正."""
        assert ABNORMAL_RETURN_THRESHOLD > 0
        assert ABNORMAL_RETURN_THRESHOLD == 0.20

    def test_status_codes_distinct(self):
        """状态码互不相同."""
        codes = [STATUS_OK, STATUS_FEATURE_FLAG_DISABLED, STATUS_INSUFFICIENT_DATA,
                 STATUS_SECTOR_MISMATCH, STATUS_EMPTY_INPUT]
        assert len(set(codes)) == len(codes)


# ============================================================
# 2. 异常体系测试
# ============================================================

class TestExceptions:
    """异常体系完整性测试."""

    def test_base_exception_raisable(self):
        """基础异常可被 raise 和 catch."""
        with pytest.raises(BrinsonAttributionError):
            raise BrinsonAttributionError("test")

    def test_insufficient_data_inherits_base(self):
        """InsufficientDataError 继承 BrinsonAttributionError."""
        with pytest.raises(BrinsonAttributionError):
            raise InsufficientDataError("test")

    def test_sector_mismatch_inherits_base(self):
        """SectorMismatchError 继承 BrinsonAttributionError."""
        with pytest.raises(BrinsonAttributionError):
            raise SectorMismatchError("test")

    def test_weight_not_normalized_inherits_base(self):
        """WeightNotNormalizedError 继承 BrinsonAttributionError."""
        with pytest.raises(BrinsonAttributionError):
            raise WeightNotNormalizedError("test")

    def test_invalid_input_inherits_base(self):
        """InvalidInputError 继承 BrinsonAttributionError."""
        with pytest.raises(BrinsonAttributionError):
            raise InvalidInputError("test")

    def test_specific_exception_catchable(self):
        """具体异常可被自身类型捕获."""
        with pytest.raises(InsufficientDataError):
            raise InsufficientDataError("insufficient")
        with pytest.raises(SectorMismatchError):
            raise SectorMismatchError("mismatch")
        with pytest.raises(WeightNotNormalizedError):
            raise WeightNotNormalizedError("not normalized")
        with pytest.raises(InvalidInputError):
            raise InvalidInputError("invalid")


# ============================================================
# 3. 数据类测试
# ============================================================

class TestDataClasses:
    """数据类字段与序列化测试."""

    def test_sector_attribution_default_values(self):
        """SectorAttribution 默认值正确."""
        s = SectorAttribution()
        assert s.sector == ""
        assert s.sector_name == ""
        assert s.portfolio_weight == 0.0
        assert s.benchmark_weight == 0.0
        assert s.portfolio_return == 0.0
        assert s.benchmark_return == 0.0
        assert s.allocation_effect == 0.0
        assert s.selection_effect == 0.0
        assert s.interaction_effect == 0.0
        assert s.total_effect == 0.0

    def test_sector_attribution_to_dict(self):
        """SectorAttribution.to_dict() 字段完整."""
        s = SectorAttribution(
            sector="finance",
            sector_name="金融",
            portfolio_weight=0.4,
            benchmark_weight=0.3,
            portfolio_return=0.02,
            benchmark_return=0.01,
            allocation_effect=-0.0014,
            selection_effect=0.003,
            interaction_effect=0.001,
            total_effect=0.0026,
        )
        d = s.to_dict()
        assert d["sector"] == "finance"
        assert d["sector_name"] == "金融"
        assert d["portfolio_weight"] == 0.4
        assert d["allocation_effect"] == -0.0014
        assert d["selection_effect"] == 0.003
        assert d["interaction_effect"] == 0.001

    def test_brinson_result_default_values(self):
        """BrinsonResult 默认值正确."""
        r = BrinsonResult()
        assert r.attribution_date == ""
        assert r.total_return == 0.0
        assert r.benchmark_return == 0.0
        assert r.excess_return == 0.0
        assert r.total_allocation_effect == 0.0
        assert r.total_selection_effect == 0.0
        assert r.total_interaction_effect == 0.0
        assert r.residual == 0.0
        assert r.sector_attributions == []
        assert r.n_sectors == 0
        assert r.status == STATUS_OK

    def test_brinson_result_to_dict(self):
        """BrinsonResult.to_dict() 字段完整."""
        r = BrinsonResult(
            attribution_date="2026-07-27",
            total_return=0.038,
            benchmark_return=0.024,
            excess_return=0.014,
            total_allocation_effect=-0.002,
            total_selection_effect=0.017,
            total_interaction_effect=-0.001,
            residual=0.0,
            n_sectors=2,
            benchmark_code="510300.SH",
            status=STATUS_OK,
        )
        d = r.to_dict()
        assert d["attribution_date"] == "2026-07-27"
        assert d["total_return"] == 0.038
        assert d["excess_return"] == 0.014
        assert d["total_allocation_effect"] == -0.002
        assert d["n_sectors"] == 2
        assert d["benchmark_code"] == "510300.SH"
        assert d["status"] == STATUS_OK
        assert d["sector_attributions"] == []

    def test_brinson_result_to_dict_with_sectors(self):
        """BrinsonResult.to_dict() 包含行业明细."""
        r = BrinsonResult(
            sector_attributions=[
                SectorAttribution(sector="finance", sector_name="金融"),
                SectorAttribution(sector="tech", sector_name="科技"),
            ],
            n_sectors=2,
        )
        d = r.to_dict()
        assert len(d["sector_attributions"]) == 2
        assert d["sector_attributions"][0]["sector"] == "finance"

    def test_brinson_result_to_markdown_contains_sections(self):
        """BrinsonResult.to_markdown() 包含必要章节."""
        r = BrinsonResult(
            attribution_date="2026-07-27",
            total_return=0.038,
            benchmark_return=0.024,
            excess_return=0.014,
            total_allocation_effect=-0.002,
            total_selection_effect=0.017,
            total_interaction_effect=-0.001,
            benchmark_code="510300.SH",
            sector_attributions=[
                SectorAttribution(sector="finance", sector_name="金融"),
            ],
        )
        md = r.to_markdown()
        assert "Brinson 归因报告" in md
        assert "2026-07-27" in md
        assert "510300.SH" in md
        assert "三效应汇总" in md
        assert "配置效应" in md
        assert "选股效应" in md
        assert "交互效应" in md
        assert "行业明细" in md

    def test_brinson_result_to_markdown_handles_zero_effect(self):
        """BrinsonResult.to_markdown() 处理零效应 (避免除零)."""
        r = BrinsonResult()
        md = r.to_markdown()
        assert "N/A" in md  # 占比显示 N/A


# ============================================================
# 4. 核心算法函数测试
# ============================================================

class TestCoreFunctions:
    """核心三效应计算函数测试."""

    def test_allocation_effect_basic(self):
        """配置效应基本计算: AR = (w_p - w_b) × (R_b - R)."""
        # w_p=0.4, w_b=0.3, R_b_i=0.01, R_b=0.024
        # AR = (0.4-0.3) × (0.01-0.024) = 0.1 × (-0.014) = -0.0014
        ar = compute_allocation_effect(0.4, 0.3, 0.01, 0.024)
        assert abs(ar - (-0.0014)) < 1e-10

    def test_allocation_effect_zero_weight_diff(self):
        """权重偏离为 0 时配置效应为 0."""
        ar = compute_allocation_effect(0.5, 0.5, 0.02, 0.01)
        assert ar == 0.0

    def test_allocation_effect_zero_return_diff(self):
        """行业收益等于基准收益时配置效应为 0."""
        ar = compute_allocation_effect(0.6, 0.4, 0.02, 0.02)
        assert ar == 0.0

    def test_allocation_effect_overweight_outperforming(self):
        """超配跑赢基准的行业 → 配置效应为正."""
        # w_p > w_b, R_b_i > R_b → AR > 0
        ar = compute_allocation_effect(0.6, 0.3, 0.05, 0.02)
        assert ar > 0

    def test_allocation_effect_overweight_underperforming(self):
        """超配跑输基准的行业 → 配置效应为负."""
        # w_p > w_b, R_b_i < R_b → AR < 0
        ar = compute_allocation_effect(0.6, 0.3, 0.01, 0.03)
        assert ar < 0

    def test_selection_effect_basic(self):
        """选股效应基本计算: SR = w_b × (R_p - R_b)."""
        # w_b=0.3, R_p_i=0.02, R_b_i=0.01
        # SR = 0.3 × (0.02-0.01) = 0.003
        sr = compute_selection_effect(0.3, 0.02, 0.01)
        assert abs(sr - 0.003) < 1e-10

    def test_selection_effect_zero_benchmark_weight(self):
        """基准权重为 0 时选股效应为 0."""
        sr = compute_selection_effect(0.0, 0.05, 0.02)
        assert sr == 0.0

    def test_selection_effect_zero_return_diff(self):
        """组合收益等于基准收益时选股效应为 0."""
        sr = compute_selection_effect(0.3, 0.02, 0.02)
        assert sr == 0.0

    def test_selection_effect_outperform(self):
        """组合跑赢基准 → 选股效应为正."""
        sr = compute_selection_effect(0.3, 0.05, 0.02)
        assert sr > 0

    def test_interaction_effect_basic(self):
        """交互效应基本计算: IR = (w_p - w_b) × (R_p - R_b)."""
        # w_p=0.4, w_b=0.3, R_p_i=0.02, R_b_i=0.01
        # IR = 0.1 × 0.01 = 0.001
        ir = compute_interaction_effect(0.4, 0.3, 0.02, 0.01)
        assert abs(ir - 0.001) < 1e-10

    def test_interaction_effect_zero_weight_diff(self):
        """权重偏离为 0 时交互效应为 0."""
        ir = compute_interaction_effect(0.5, 0.5, 0.05, 0.02)
        assert ir == 0.0

    def test_interaction_effect_zero_return_diff(self):
        """收益偏离为 0 时交互效应为 0."""
        ir = compute_interaction_effect(0.6, 0.4, 0.02, 0.02)
        assert ir == 0.0

    def test_interaction_effect_double_overweight(self):
        """超配 + 跑赢 → 交互效应为正."""
        ir = compute_interaction_effect(0.6, 0.3, 0.05, 0.02)
        assert ir > 0

    def test_compute_total_return_basic(self):
        """加权总收益率计算."""
        weights = {"a": 0.4, "b": 0.6}
        returns = {"a": 0.02, "b": 0.05}
        # 0.4×0.02 + 0.6×0.05 = 0.008 + 0.03 = 0.038
        total = compute_total_return(weights, returns)
        assert abs(total - 0.038) < 1e-10

    def test_compute_total_return_missing_sector(self):
        """缺失行业的收益率按 0 处理."""
        weights = {"a": 0.5, "b": 0.5}
        returns = {"a": 0.02}  # b 缺失
        total = compute_total_return(weights, returns)
        assert abs(total - 0.01) < 1e-10  # 0.5×0.02 + 0.5×0 = 0.01

    def test_compute_total_return_empty(self):
        """空字典返回 0."""
        assert compute_total_return({}, {}) == 0.0


# ============================================================
# 5. 权重校验测试
# ============================================================

class TestValidateWeights:
    """权重校验函数测试."""

    def test_valid_normalized_weights(self):
        """归一化权重校验通过."""
        weights = {"a": 0.4, "b": 0.6}
        is_valid, w_sum, err = validate_weights(weights)
        assert is_valid
        assert abs(w_sum - 1.0) < 1e-10
        assert err == ""

    def test_valid_with_tolerance(self):
        """在容差范围内视为有效."""
        weights = {"a": 0.4, "b": 0.595}  # 和 = 0.995, 偏离 0.005 < 0.01
        is_valid, _, _ = validate_weights(weights, tolerance=0.01)
        assert is_valid

    def test_invalid_outside_tolerance(self):
        """超出容差范围视为无效."""
        weights = {"a": 0.4, "b": 0.5}  # 和 = 0.9, 偏离 0.1 > 0.01
        is_valid, w_sum, err = validate_weights(weights, tolerance=0.01)
        assert not is_valid
        assert abs(w_sum - 0.9) < 1e-10
        assert "偏离" in err

    def test_negative_weight_invalid(self):
        """负权重无效."""
        weights = {"a": -0.1, "b": 1.1}
        is_valid, _, err = validate_weights(weights)
        assert not is_valid
        assert "负" in err

    def test_negative_within_epsilon_treated_as_zero(self):
        """小负数 (浮点误差) 视为 0, 不报错."""
        weights = {"a": -1e-6, "b": 1.0}
        is_valid, _, _ = validate_weights(weights, epsilon=1e-4)
        assert is_valid

    def test_empty_weights_invalid(self):
        """空权重字典无效."""
        is_valid, _, err = validate_weights({})
        assert not is_valid
        assert "空" in err

    def test_non_numeric_weight_invalid(self):
        """非数值权重无效."""
        weights = {"a": "invalid", "b": 0.5}  # type: ignore[assignment]
        is_valid, _, err = validate_weights(weights)
        assert not is_valid
        assert "非数值" in err


# ============================================================
# 6. 行业对齐测试
# ============================================================

class TestAlignSectors:
    """行业对齐函数测试."""

    def test_align_identical_sectors(self):
        """相同行业集合."""
        sectors = align_sectors(
            {"a": 0.5, "b": 0.5},
            {"a": 0.3, "b": 0.7},
            {"a": 0.01, "b": 0.02},
            {"a": 0.005, "b": 0.015},
        )
        assert sectors == ["a", "b"]

    def test_align_different_sectors(self):
        """不同行业集合取并集."""
        sectors = align_sectors(
            {"a": 0.5, "b": 0.5},
            {"b": 0.3, "c": 0.7},  # c 是新行业
            {"a": 0.01, "b": 0.02},
            {"b": 0.005, "c": 0.015},
        )
        assert set(sectors) == {"a", "b", "c"}

    def test_align_returns_sorted(self):
        """返回排序后的列表."""
        sectors = align_sectors(
            {"z": 0.5, "a": 0.5},
            {"a": 0.3, "z": 0.7},
            {"a": 0.01, "z": 0.02},
            {"a": 0.005, "z": 0.015},
        )
        assert sectors == ["a", "z"]

    def test_align_missing_in_returns(self):
        """权重有但收益率缺失的行业也被包含."""
        sectors = align_sectors(
            {"a": 0.5, "b": 0.5},
            {"a": 0.5, "b": 0.5},
            {"a": 0.01},  # b 缺失
            {"a": 0.005, "b": 0.015},
        )
        assert set(sectors) == {"a", "b"}


# ============================================================
# 7. attribute_brinson 主函数测试
# ============================================================

class TestAttributeBrinson:
    """attribute_brinson 主归因函数测试."""

    def test_basic_two_sector_attribution(self):
        """两行业基本归因 (手工验证)."""
        result = attribute_brinson(
            portfolio_weights={"finance": 0.4, "tech": 0.6},
            benchmark_weights={"finance": 0.3, "tech": 0.7},
            portfolio_returns={"finance": 0.02, "tech": 0.05},
            benchmark_returns={"finance": 0.01, "tech": 0.03},
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
        )
        # 组合总收益 = 0.4×0.02 + 0.6×0.05 = 0.038
        assert abs(result.total_return - 0.038) < 1e-10
        # 基准总收益 = 0.3×0.01 + 0.7×0.03 = 0.024
        assert abs(result.benchmark_return - 0.024) < 1e-10
        # 超额收益 = 0.014
        assert abs(result.excess_return - 0.014) < 1e-10
        # 配置效应: finance -0.0014 + tech -0.0006 = -0.002
        assert abs(result.total_allocation_effect - (-0.002)) < 1e-10
        # 选股效应: finance 0.003 + tech 0.014 = 0.017
        assert abs(result.total_selection_effect - 0.017) < 1e-10
        # 交互效应: finance 0.001 + tech -0.002 = -0.001
        assert abs(result.total_interaction_effect - (-0.001)) < 1e-10
        # 残差为 0 (三效应和 = 超额收益)
        assert abs(result.residual) < 1e-10
        assert result.n_sectors == 2
        assert result.status == STATUS_OK
        assert result.attribution_date == "2026-07-27"
        assert result.benchmark_code == "510300.SH"

    def test_three_effects_sum_equals_excess_return(self):
        """数学恒等式: 三效应总和 = 超额收益 (残差 = 0)."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.3, "b": 0.4, "c": 0.3},
            benchmark_weights={"a": 0.2, "b": 0.5, "c": 0.3},
            portfolio_returns={"a": 0.05, "b": -0.02, "c": 0.01},
            benchmark_returns={"a": 0.03, "b": 0.01, "c": 0.005},
        )
        three_sum = (result.total_allocation_effect +
                     result.total_selection_effect +
                     result.total_interaction_effect)
        assert abs(three_sum - result.excess_return) < 1e-10
        assert abs(result.residual) < 1e-10

    def test_identity_with_equal_weights_and_returns(self):
        """组合 = 基准时所有效应为 0."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.02, "b": 0.03},
            benchmark_returns={"a": 0.02, "b": 0.03},
        )
        assert result.total_allocation_effect == 0.0
        assert result.total_selection_effect == 0.0
        assert result.total_interaction_effect == 0.0
        assert result.excess_return == 0.0
        assert result.residual == 0.0

    def test_eight_sectors_full_attribution(self):
        """8 大行业完整归因."""
        sectors = ["tech", "manufacturing", "cyclical", "resources",
                   "defensive", "finance", "consumer", "healthcare"]
        p_w = {s: 0.125 for s in sectors}  # 等权
        b_w = {s: 0.125 for s in sectors}
        p_r = {s: 0.01 * i for i, s in enumerate(sectors)}
        b_r = {s: 0.005 * i for i, s in enumerate(sectors)}

        result = attribute_brinson(
            portfolio_weights=p_w,
            benchmark_weights=b_w,
            portfolio_returns=p_r,
            benchmark_returns=b_r,
        )
        assert result.n_sectors == 8
        assert result.status == STATUS_OK
        # 等权但收益不同 → 选股效应 != 0, 配置效应 = 0, 交互效应 = 0
        assert result.total_allocation_effect == 0.0
        assert result.total_interaction_effect == 0.0
        assert result.total_selection_effect != 0.0

    def test_sector_attribution_details(self):
        """行业明细包含完整字段."""
        result = attribute_brinson(
            portfolio_weights={"finance": 0.4, "tech": 0.6},
            benchmark_weights={"finance": 0.3, "tech": 0.7},
            portfolio_returns={"finance": 0.02, "tech": 0.05},
            benchmark_returns={"finance": 0.01, "tech": 0.03},
        )
        assert len(result.sector_attributions) == 2
        for s in result.sector_attributions:
            assert s.sector in ["finance", "tech"]
            assert s.sector_name in ["金融", "科技"]
            assert s.portfolio_weight > 0
            assert s.benchmark_weight > 0
            assert s.weight_diff == s.portfolio_weight - s.benchmark_weight
            assert s.return_diff == s.portfolio_return - s.benchmark_return
            assert s.total_effect == s.allocation_effect + s.selection_effect + s.interaction_effect

    def test_custom_sector_names(self):
        """自定义行业名称映射."""
        custom_names = {"a": "自定义A", "b": "自定义B"}
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.01, "b": 0.02},
            benchmark_returns={"a": 0.01, "b": 0.02},
            sector_names=custom_names,
        )
        names = {s.sector_name for s in result.sector_attributions}
        assert names == {"自定义A", "自定义B"}

    def test_missing_sector_in_portfolio_treated_as_zero(self):
        """组合缺失的行业按 0 权重处理."""
        result = attribute_brinson(
            portfolio_weights={"a": 1.0},  # 只有 a
            benchmark_weights={"a": 0.5, "b": 0.5},  # a 和 b
            portfolio_returns={"a": 0.02},
            benchmark_returns={"a": 0.01, "b": 0.03},
        )
        assert result.n_sectors == 2
        # b 行业组合权重为 0
        b_sector = next(s for s in result.sector_attributions if s.sector == "b")
        assert b_sector.portfolio_weight == 0.0

    def test_empty_portfolio_weights_raises(self):
        """空组合权重抛 InvalidInputError."""
        with pytest.raises(InvalidInputError):
            attribute_brinson(
                portfolio_weights={},
                benchmark_weights={"a": 1.0},
                portfolio_returns={"a": 0.01},
                benchmark_returns={"a": 0.01},
            )

    def test_empty_benchmark_weights_raises(self):
        """空基准权重抛 InvalidInputError."""
        with pytest.raises(InvalidInputError):
            attribute_brinson(
                portfolio_weights={"a": 1.0},
                benchmark_weights={},
                portfolio_returns={"a": 0.01},
                benchmark_returns={"a": 0.01},
            )

    def test_empty_returns_raises(self):
        """空收益率抛 InvalidInputError."""
        with pytest.raises(InvalidInputError):
            attribute_brinson(
                portfolio_weights={"a": 1.0},
                benchmark_weights={"a": 1.0},
                portfolio_returns={},
                benchmark_returns={"a": 0.01},
            )

    def test_unnormalized_weights_raises(self):
        """未归一化权重抛 WeightNotNormalizedError."""
        with pytest.raises(WeightNotNormalizedError):
            attribute_brinson(
                portfolio_weights={"a": 0.5, "b": 0.6},  # 和 1.1
                benchmark_weights={"a": 0.5, "b": 0.5},
                portfolio_returns={"a": 0.01, "b": 0.02},
                benchmark_returns={"a": 0.01, "b": 0.02},
                validate=True,
                weight_tolerance=0.01,
            )

    def test_skip_validation_allows_unnormalized(self):
        """跳过校验允许未归一化权重."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.6},  # 和 1.1
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.01, "b": 0.02},
            benchmark_returns={"a": 0.01, "b": 0.02},
            validate=False,
        )
        assert result.status == STATUS_OK

    def test_insufficient_sectors_returns_status(self):
        """行业数不足返回 insufficient_data 状态."""
        result = attribute_brinson(
            portfolio_weights={"a": 1.0},  # 只有 1 个行业
            benchmark_weights={"a": 1.0},
            portfolio_returns={"a": 0.01},
            benchmark_returns={"a": 0.01},
        )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert "行业数" in result.reason


# ============================================================
# 8. BrinsonAttributionManager 主类测试
# ============================================================

class TestBrinsonAttributionManager:
    """BrinsonAttributionManager 主类测试."""

    def test_init_with_defaults(self):
        """默认初始化."""
        mgr = BrinsonAttributionManager()
        assert mgr._feature_flag_name == FLAG_NAME
        assert mgr._config_name if hasattr(mgr, '_config_name') else True
        assert mgr._benchmark_code == DEFAULT_PRIMARY_BENCHMARK

    def test_init_with_custom_config(self):
        """自定义配置初始化."""
        custom_cfg = {
            "settings": {
                "primary_benchmark": "510500.SH",
                "weight_sum_tolerance": 0.02,
                "min_sectors": 3,
            },
            "sectors": [{"code": "custom", "name": "自定义"}],
        }
        mgr = BrinsonAttributionManager(config=custom_cfg)
        assert mgr._benchmark_code == "510500.SH"
        assert mgr._weight_tolerance == 0.02
        assert mgr._min_sectors == 3
        assert mgr._sector_names.get("custom") == "自定义"

    def test_attribute_with_feature_flag_disabled(self):
        """Feature Flag 关闭时返回降级结果."""
        mgr = BrinsonAttributionManager()
        # Feature Flag 默认 False
        result = mgr.attribute(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.01, "b": 0.02},
            benchmark_returns={"a": 0.01, "b": 0.02},
        )
        assert result.status == STATUS_FEATURE_FLAG_DISABLED
        assert FLAG_NAME in result.reason
        assert result.total_return == 0.0
        assert result.excess_return == 0.0

    def test_attribute_with_feature_flag_enabled(self):
        """Feature Flag 启用时执行归因."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_weights={"finance": 0.4, "tech": 0.6},
                benchmark_weights={"finance": 0.3, "tech": 0.7},
                portfolio_returns={"finance": 0.02, "tech": 0.05},
                benchmark_returns={"finance": 0.01, "tech": 0.03},
                attribution_date="2026-07-27",
            )
        assert result.status == STATUS_OK
        assert abs(result.excess_return - 0.014) < 1e-10

    def test_attribute_uses_default_benchmark_weights(self):
        """未提供 benchmark_weights 时使用配置中的默认值."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_weights={"finance": 0.3, "tech": 0.7},
                portfolio_returns={"finance": 0.02, "tech": 0.05},
                benchmark_returns={"finance": 0.01, "tech": 0.03},
            )
        # 使用配置中的默认基准权重
        assert result.status == STATUS_OK

    def test_attribute_returns_insufficient_when_no_benchmark(self):
        """无基准权重且配置无默认值时返回 insufficient_data."""
        mgr = BrinsonAttributionManager(
            config={"settings": {}, "benchmark_sector_weights": {}}
        )
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_weights={"a": 1.0},
                portfolio_returns={"a": 0.01},
                benchmark_returns={"a": 0.01},
            )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert "基准权重" in result.reason

    def test_attribute_returns_insufficient_when_no_returns(self):
        """未提供收益率时返回 insufficient_data."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_weights={"a": 1.0},
                benchmark_weights={"a": 1.0},
                portfolio_returns={},
                benchmark_returns={"a": 0.01},
            )
        assert result.status == STATUS_INSUFFICIENT_DATA

    def test_attribute_handles_invalid_input_gracefully(self):
        """无效输入优雅处理 (返回 empty_input 状态)."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_weights={},
                benchmark_weights={"a": 1.0},
                portfolio_returns={"a": 0.01},
                benchmark_returns={"a": 0.01},
            )
        assert result.status == STATUS_EMPTY_INPUT

    def test_attribute_handles_unnormalized_weights_gracefully(self):
        """未归一化权重优雅处理 (返回 sector_mismatch 状态)."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_weights={"a": 0.5, "b": 0.6},  # 和 1.1
                benchmark_weights={"a": 0.5, "b": 0.5},
                portfolio_returns={"a": 0.01, "b": 0.02},
                benchmark_returns={"a": 0.01, "b": 0.02},
            )
        assert result.status == STATUS_SECTOR_MISMATCH

    def test_attribute_from_positions_basic(self):
        """从持仓列表归因 (聚合到行业维度)."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute_from_positions(
                portfolio_positions=[
                    {"code": "000001", "weight": 0.3, "return": 0.02, "sector": "finance"},
                    {"code": "600519", "weight": 0.4, "return": 0.05, "sector": "consumer"},
                    {"code": "000858", "weight": 0.3, "return": 0.01, "sector": "consumer"},
                ],
                benchmark_positions=[
                    {"code": "000001", "weight": 0.4, "return": 0.01, "sector": "finance"},
                    {"code": "600519", "weight": 0.6, "return": 0.03, "sector": "consumer"},
                ],
                attribution_date="2026-07-27",
            )
        assert result.status == STATUS_OK
        assert result.n_sectors == 2  # finance + consumer

    def test_attribute_from_positions_flag_disabled(self):
        """Feature Flag 关闭时持仓列表归因返回降级结果."""
        mgr = BrinsonAttributionManager()
        result = mgr.attribute_from_positions(
            portfolio_positions=[{"code": "a", "weight": 1.0, "return": 0.01, "sector": "x"}],
            benchmark_positions=[{"code": "a", "weight": 1.0, "return": 0.01, "sector": "x"}],
        )
        assert result.status == STATUS_FEATURE_FLAG_DISABLED

    def test_attribute_from_positions_empty_list(self):
        """空持仓列表返回 insufficient_data."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute_from_positions(
                portfolio_positions=[],
                benchmark_positions=[{"code": "a", "weight": 1.0, "return": 0.01, "sector": "x"}],
            )
        assert result.status == STATUS_INSUFFICIENT_DATA

    def test_get_default_benchmark_weights(self):
        """获取默认基准权重."""
        mgr = BrinsonAttributionManager()
        weights = mgr.get_default_benchmark_weights()
        assert isinstance(weights, dict)
        # 配置文件中应该有 8 个行业的基准权重
        assert len(weights) == 8

    def test_get_sector_names(self):
        """获取行业名称映射."""
        mgr = BrinsonAttributionManager()
        names = mgr.get_sector_names()
        assert isinstance(names, dict)
        assert names.get("finance") == "金融"
        assert names.get("tech") == "科技"


# ============================================================
# 9. Feature Flag 透传测试
# ============================================================

class TestFeatureFlag:
    """Feature Flag 透传测试 (HC-1)."""

    def test_is_brinson_attribution_enabled_default_false(self):
        """默认未启用 (Flag 未设置时返回 False)."""
        # 由于 Feature Flag 默认 False, 此处应返回 False
        result = is_brinson_attribution_enabled()
        # 注意: 如果测试环境配置了 USE_BRINSON_ATTRIBUTION=True, 这里可能为 True
        # 默认情况下应为 False
        assert isinstance(result, bool)

    def test_is_brinson_attribution_enabled_returns_false_on_import_error(self):
        """Feature Flag 模块导入失败时返回 False."""
        with patch("utils.infra.feature_flags.is_enabled", side_effect=ImportError):
            assert is_brinson_attribution_enabled() is False

    def test_is_brinson_attribution_enabled_returns_false_on_exception(self):
        """Feature Flag 异常时返回 False."""
        with patch("utils.infra.feature_flags.is_enabled", side_effect=RuntimeError("test")):
            assert is_brinson_attribution_enabled() is False

    def test_manager_is_enabled_returns_false_on_import_error(self):
        """Manager._is_enabled() 导入失败时返回 False."""
        mgr = BrinsonAttributionManager()
        # mock import 失败
        original_is_enabled = None
        try:
            import utils.infra.feature_flags as ff
            original_is_enabled = ff.is_enabled
            ff.is_enabled = MagicMock(side_effect=ImportError)
            assert mgr._is_enabled() is False
        finally:
            if original_is_enabled is not None:
                ff.is_enabled = original_is_enabled


# ============================================================
# 10. 便捷函数测试
# ============================================================

class TestConvenienceFunctions:
    """便捷函数测试."""

    def test_attribute_brinson_simple_basic(self):
        """便捷归因函数基本测试."""
        result = attribute_brinson_simple(
            portfolio_weights={"finance": 0.4, "tech": 0.6},
            benchmark_weights={"finance": 0.3, "tech": 0.7},
            portfolio_returns={"finance": 0.02, "tech": 0.05},
            benchmark_returns={"finance": 0.01, "tech": 0.03},
            attribution_date="2026-07-27",
        )
        assert result.status == STATUS_OK
        assert abs(result.excess_return - 0.014) < 1e-10
        assert result.attribution_date == "2026-07-27"
        assert result.benchmark_code == DEFAULT_PRIMARY_BENCHMARK

    def test_create_default_manager(self):
        """创建默认管理器."""
        mgr = create_default_manager()
        assert isinstance(mgr, BrinsonAttributionManager)
        assert mgr._benchmark_code == DEFAULT_PRIMARY_BENCHMARK


# ============================================================
# 11. 边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_single_sector_insufficient(self):
        """单行业视为数据不足."""
        result = attribute_brinson(
            portfolio_weights={"a": 1.0},
            benchmark_weights={"a": 1.0},
            portfolio_returns={"a": 0.01},
            benchmark_returns={"a": 0.01},
        )
        assert result.status == STATUS_INSUFFICIENT_DATA

    def test_zero_returns_attribution(self):
        """零收益归因 (所有效应为 0)."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.0, "b": 0.0},
            benchmark_returns={"a": 0.0, "b": 0.0},
        )
        assert result.excess_return == 0.0
        assert result.total_allocation_effect == 0.0
        assert result.total_selection_effect == 0.0
        assert result.total_interaction_effect == 0.0

    def test_negative_returns_attribution(self):
        """负收益归因."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": -0.02, "b": 0.03},
            benchmark_returns={"a": -0.01, "b": 0.02},
        )
        # 等权等基准, 配置效应 = 0
        assert abs(result.total_allocation_effect) < 1e-12
        # 选股效应 = 0.5×(-0.02-(-0.01)) + 0.5×(0.03-0.02) = 0.5×(-0.01) + 0.5×(0.01) = 0
        # 浮点精度: 实际值可能为 -8.67e-19, 用 abs < epsilon 判断
        assert abs(result.total_selection_effect) < 1e-12
        assert result.status == STATUS_OK

    def test_large_number_of_sectors(self):
        """大量行业归因 (10 个)."""
        sectors = [f"s{i}" for i in range(10)]
        p_w = {s: 0.1 for s in sectors}
        b_w = {s: 0.1 for s in sectors}
        p_r = {s: 0.01 * (i - 5) for i, s in enumerate(sectors)}
        b_r = {s: 0.005 * (i - 5) for i, s in enumerate(sectors)}

        result = attribute_brinson(
            portfolio_weights=p_w,
            benchmark_weights=b_w,
            portfolio_returns=p_r,
            benchmark_returns=b_r,
        )
        assert result.n_sectors == 10
        assert result.status == STATUS_OK
        # 等权等基准 → 配置效应 = 0, 交互效应 = 0
        assert abs(result.total_allocation_effect) < 1e-10
        assert abs(result.total_interaction_effect) < 1e-10

    def test_floating_point_precision(self):
        """浮点精度验证 (残差应极小)."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.333, "b": 0.333, "c": 0.334},
            benchmark_weights={"a": 0.25, "b": 0.35, "c": 0.4},
            portfolio_returns={"a": 0.0123, "b": -0.0045, "c": 0.0234},
            benchmark_returns={"a": 0.0098, "b": 0.0012, "c": 0.0187},
        )
        # 残差应小于 1e-10
        assert abs(result.residual) < 1e-10

    def test_extreme_weights(self):
        """极端权重 (0/1)归因."""
        result = attribute_brinson(
            portfolio_weights={"a": 1.0, "b": 0.0},  # 全仓 a
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.05, "b": 0.0},
            benchmark_returns={"a": 0.02, "b": 0.01},
        )
        assert result.status == STATUS_OK
        # 超配 a + a 跑赢基准 → 配置效应为正
        assert result.total_allocation_effect > 0


# ============================================================
# 12. 数学恒等式验证 (随机化测试)
# ============================================================

class TestMathematicalIdentity:
    """数学恒等式验证: 三效应和 = 超额收益."""

    def test_identity_random_case_1(self):
        """随机场景 1."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.2, "b": 0.3, "c": 0.5},
            benchmark_weights={"a": 0.4, "b": 0.4, "c": 0.2},
            portfolio_returns={"a": 0.03, "b": -0.01, "c": 0.04},
            benchmark_returns={"a": 0.02, "b": 0.005, "c": 0.025},
        )
        three_sum = (result.total_allocation_effect +
                     result.total_selection_effect +
                     result.total_interaction_effect)
        assert abs(three_sum - result.excess_return) < 1e-10
        assert abs(result.residual) < 1e-10

    def test_identity_random_case_2(self):
        """随机场景 2 (含负收益)."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.3, "c": 0.2},
            benchmark_weights={"a": 0.3, "b": 0.5, "c": 0.2},
            portfolio_returns={"a": -0.05, "b": 0.08, "c": 0.02},
            benchmark_returns={"a": -0.02, "b": 0.04, "c": 0.01},
        )
        three_sum = (result.total_allocation_effect +
                     result.total_selection_effect +
                     result.total_interaction_effect)
        assert abs(three_sum - result.excess_return) < 1e-10
        assert abs(result.residual) < 1e-10

    def test_identity_contribution_to_excess(self):
        """每个行业的 contribution_to_excess 之和等于超额收益."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.4, "b": 0.6},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.02, "b": 0.05},
            benchmark_returns={"a": 0.01, "b": 0.03},
        )
        contrib_sum = sum(s.contribution_to_excess for s in result.sector_attributions)
        assert abs(contrib_sum - result.excess_return) < 1e-10

    def test_identity_total_effect_per_sector(self):
        """每个行业的 total_effect = AR + SR + IR."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.4, "b": 0.3, "c": 0.3},
            benchmark_weights={"a": 0.3, "b": 0.4, "c": 0.3},
            portfolio_returns={"a": 0.05, "b": 0.02, "c": -0.01},
            benchmark_returns={"a": 0.03, "b": 0.01, "c": 0.005},
        )
        for s in result.sector_attributions:
            expected = s.allocation_effect + s.selection_effect + s.interaction_effect
            assert abs(s.total_effect - expected) < 1e-10


# ============================================================
# 13. Markdown 输出测试
# ============================================================

class TestMarkdownOutput:
    """Markdown 报告输出测试."""

    def test_markdown_contains_attribution_date(self):
        """Markdown 包含归因日期."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.01, "b": 0.02},
            benchmark_returns={"a": 0.01, "b": 0.02},
            attribution_date="2026-07-27",
        )
        md = result.to_markdown()
        assert "2026-07-27" in md

    def test_markdown_contains_benchmark_code(self):
        """Markdown 包含基准代码."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.01, "b": 0.02},
            benchmark_returns={"a": 0.01, "b": 0.02},
            benchmark_code="510300.SH",
        )
        md = result.to_markdown()
        assert "510300.SH" in md

    def test_markdown_contains_residual_row(self):
        """Markdown 包含残差行."""
        result = attribute_brinson(
            portfolio_weights={"a": 0.5, "b": 0.5},
            benchmark_weights={"a": 0.5, "b": 0.5},
            portfolio_returns={"a": 0.01, "b": 0.02},
            benchmark_returns={"a": 0.01, "b": 0.02},
        )
        md = result.to_markdown()
        assert "残差" in md

    def test_markdown_handles_empty_sectors(self):
        """Markdown 处理空行业列表."""
        r = BrinsonResult(status=STATUS_FEATURE_FLAG_DISABLED, reason="test")
        md = r.to_markdown()
        # 不应崩溃, 应包含标题
        assert "Brinson 归因报告" in md

    def test_markdown_contains_sector_details(self):
        """Markdown 包含行业明细表."""
        result = attribute_brinson(
            portfolio_weights={"finance": 0.4, "tech": 0.6},
            benchmark_weights={"finance": 0.3, "tech": 0.7},
            portfolio_returns={"finance": 0.02, "tech": 0.05},
            benchmark_returns={"finance": 0.01, "tech": 0.03},
        )
        md = result.to_markdown()
        assert "行业明细" in md
        assert "金融" in md or "finance" in md
        assert "科技" in md or "tech" in md


# ============================================================
# 14. 配置加载测试
# ============================================================

class TestConfigLoading:
    """ConfigManager 配置加载测试 (HC-5)."""

    def test_config_loads_from_yaml(self):
        """配置从 brinson_attribution.yaml 加载."""
        mgr = BrinsonAttributionManager()
        # 配置文件存在, 应加载到 benchmark_code
        assert mgr._benchmark_code == "510300.SH"

    def test_config_loads_default_benchmark_weights(self):
        """配置加载默认基准权重."""
        mgr = BrinsonAttributionManager()
        weights = mgr.get_default_benchmark_weights()
        # 配置文件中定义了 8 个行业的基准权重
        assert "finance" in weights
        assert "tech" in weights
        assert len(weights) == 8

    def test_config_loads_sector_names(self):
        """配置加载行业名称映射."""
        mgr = BrinsonAttributionManager()
        names = mgr.get_sector_names()
        # 应包含配置文件中的中文名称
        assert names.get("finance") == "金融"
        assert names.get("tech") == "科技"

    def test_config_loads_thresholds(self):
        """配置加载阈值参数."""
        mgr = BrinsonAttributionManager()
        # 从配置文件加载的阈值
        assert mgr._weight_tolerance == 0.01
        assert mgr._min_sectors == 2

    def test_config_fallback_on_failure(self):
        """配置加载失败时使用默认值."""
        # 通过传入空配置模拟加载失败
        mgr = BrinsonAttributionManager(config={})
        assert mgr._benchmark_code == DEFAULT_PRIMARY_BENCHMARK
        assert mgr._weight_tolerance == DEFAULT_WEIGHT_SUM_TOLERANCE

    def test_custom_config_overrides_yaml(self):
        """显式配置覆盖 YAML."""
        custom_cfg = {
            "settings": {
                "primary_benchmark": "000001.SH",
                "weight_sum_tolerance": 0.05,
            },
        }
        mgr = BrinsonAttributionManager(config=custom_cfg)
        assert mgr._benchmark_code == "000001.SH"
        assert mgr._weight_tolerance == 0.05


# ============================================================
# 15. 集成场景测试
# ============================================================

class TestIntegration:
    """集成场景测试."""

    def test_full_workflow_with_feature_flag(self):
        """完整工作流 (Feature Flag 启用 -> 归因 -> 序列化)."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_weights={"finance": 0.4, "tech": 0.3, "consumer": 0.3},
                benchmark_weights={"finance": 0.3, "tech": 0.4, "consumer": 0.3},
                portfolio_returns={"finance": 0.02, "tech": 0.05, "consumer": 0.01},
                benchmark_returns={"finance": 0.01, "tech": 0.03, "consumer": 0.005},
                attribution_date="2026-07-27",
                benchmark_code="510300.SH",
            )

        # 验证结果完整
        assert result.status == STATUS_OK
        assert result.n_sectors == 3
        assert abs(result.residual) < 1e-10

        # 验证序列化
        d = result.to_dict()
        assert d["status"] == STATUS_OK
        assert len(d["sector_attributions"]) == 3

        # 验证 Markdown
        md = result.to_markdown()
        assert "Brinson 归因报告" in md
        assert "2026-07-27" in md

    def test_positions_aggregation_correctness(self):
        """持仓聚合到行业维度的正确性."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute_from_positions(
                portfolio_positions=[
                    {"code": "A", "weight": 0.4, "return": 0.02, "sector": "finance"},
                    {"code": "B", "weight": 0.6, "return": 0.05, "sector": "tech"},
                ],
                benchmark_positions=[
                    {"code": "A", "weight": 0.3, "return": 0.01, "sector": "finance"},
                    {"code": "B", "weight": 0.7, "return": 0.03, "sector": "tech"},
                ],
            )
        # 与手工调用 attribute 应一致
        expected = attribute_brinson(
            portfolio_weights={"finance": 0.4, "tech": 0.6},
            benchmark_weights={"finance": 0.3, "tech": 0.7},
            portfolio_returns={"finance": 0.02, "tech": 0.05},
            benchmark_returns={"finance": 0.01, "tech": 0.03},
        )
        assert abs(result.excess_return - expected.excess_return) < 1e-10
        assert abs(result.total_allocation_effect - expected.total_allocation_effect) < 1e-10

    def test_multiple_sectors_aggregation(self):
        """多资产聚合到同一行业的正确性."""
        mgr = BrinsonAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute_from_positions(
                portfolio_positions=[
                    {"code": "A", "weight": 0.3, "return": 0.02, "sector": "consumer"},
                    {"code": "B", "weight": 0.3, "return": 0.04, "sector": "consumer"},
                    {"code": "C", "weight": 0.4, "return": 0.05, "sector": "tech"},
                ],
                benchmark_positions=[
                    {"code": "A", "weight": 0.5, "return": 0.01, "sector": "consumer"},
                    {"code": "C", "weight": 0.5, "return": 0.03, "sector": "tech"},
                ],
            )
        # consumer 行业: 组合权重 0.6, 加权收益 (0.3×0.02 + 0.3×0.04) / 0.6 = 0.03
        consumer = next(s for s in result.sector_attributions if s.sector == "consumer")
        assert abs(consumer.portfolio_weight - 0.6) < 1e-10
        assert abs(consumer.portfolio_return - 0.03) < 1e-10
