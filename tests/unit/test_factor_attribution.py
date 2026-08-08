"""T5.2 单元测试 — utils/attribution/factor_attribution.py.

测试覆盖:
  1. 常量定义完整性
  2. 异常体系可抛可捕
  3. 数据类字段与序列化 (FactorAttribution / FactorAttributionResult)
  4. 核心算法函数 (单因子贡献 / 主动暴露 / 因子风险 / 个股风险 / IR / 因子对齐 / 因子分类)
  5. 数学恒等式验证 (factor_pnl = Σ contribution_i, residual = 0)
  6. attribute_factors 主函数
  7. FactorAttributionManager 主类 (Feature Flag + ConfigManager)
  8. Feature Flag 透传 (HC-1)
  9. 便捷函数
  10. 边界条件 (空输入 / 单因子 / 异常收益率 / 暴露集中)
  11. Markdown 报告输出
  12. 持仓聚合归因 (attribute_from_positions)

设计原则:
  - 算法正确性优先: 用手工计算的期望值验证公式
  - 数学恒等式: 因子收益总和 = Σ 单因子贡献 (残差 = 0)
  - 不依赖网络: ConfigManager / Feature Flag 通过 mock 控制
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.attribution.factor_attribution import (  # noqa: E402
    ABNORMAL_FACTOR_RETURN_THRESHOLD,
    BARRA_STYLE_FACTORS,
    CATEGORY_COUNTRY,
    CATEGORY_SECTOR,
    CATEGORY_SPECIFIC,
    # 因子类别
    CATEGORY_STYLE,
    CONCENTRATION_THRESHOLD,
    DEFAULT_ANNUALIZATION_FACTOR,
    DEFAULT_CONFIG_NAME,
    DEFAULT_MIN_FACTORS,
    DEFAULT_PRIMARY_BENCHMARK,
    DEFAULT_RISK_BUDGET,
    DEFAULT_SECONDARY_BENCHMARK,
    DEFAULT_SIGNIFICANCE_THRESHOLD,
    FACTOR_NAMES,
    # 常量
    FLAG_NAME,
    MISSING_THRESHOLD,
    SECTOR_FACTORS,
    STATUS_EMPTY_INPUT,
    STATUS_FACTOR_MISMATCH,
    STATUS_FEATURE_FLAG_DISABLED,
    STATUS_INSUFFICIENT_DATA,
    # 状态码
    STATUS_OK,
    ZERO_EXPOSURE_EPSILON,
    ZERO_RETURN_EPSILON,
    ExposureNotNormalizedError,
    # 数据类
    FactorAttribution,
    # 异常
    FactorAttributionError,
    # 主类
    FactorAttributionManager,
    FactorAttributionResult,
    FactorMismatchError,
    InsufficientFactorDataError,
    InvalidFactorInputError,
    align_factors,
    attribute_factors,
    attribute_factors_simple,
    categorize_factor,
    compute_active_exposure,
    # 核心算法函数
    compute_factor_contribution,
    compute_factor_risk,
    compute_information_ratio,
    compute_specific_risk,
    create_default_manager,
    # 便捷函数
    is_factor_attribution_enabled,
)

# ============================================================
# 1. 常量定义测试
# ============================================================

class TestConstants:
    """常量定义完整性测试."""

    def test_flag_name(self):
        """Feature Flag 名称正确."""
        assert FLAG_NAME == "USE_FACTOR_ATTRIBUTION"

    def test_config_name(self):
        """ConfigManager 配置名正确."""
        assert DEFAULT_CONFIG_NAME == "factor_attribution"

    def test_primary_benchmark(self):
        """默认主基准为沪深300ETF."""
        assert DEFAULT_PRIMARY_BENCHMARK == "510300.SH"

    def test_secondary_benchmark(self):
        """默认次基准为中证500ETF."""
        assert DEFAULT_SECONDARY_BENCHMARK == "510500.SH"

    def test_annualization_factor(self):
        """年化因子为 252 交易日."""
        assert DEFAULT_ANNUALIZATION_FACTOR == 252

    def test_risk_budget(self):
        """默认风险预算 5%."""
        assert DEFAULT_RISK_BUDGET == 0.05

    def test_min_factors(self):
        """最小因子数 >= 2."""
        assert DEFAULT_MIN_FACTORS >= 2

    def test_significance_threshold_positive(self):
        """显著性阈值为正."""
        assert DEFAULT_SIGNIFICANCE_THRESHOLD > 0

    def test_zero_epsilon_positive(self):
        """零值阈值为正."""
        assert ZERO_EXPOSURE_EPSILON > 0
        assert ZERO_RETURN_EPSILON > 0

    def test_abnormal_factor_return_threshold(self):
        """异常因子收益率阈值为正."""
        assert ABNORMAL_FACTOR_RETURN_THRESHOLD > 0
        assert ABNORMAL_FACTOR_RETURN_THRESHOLD == 0.10

    def test_concentration_threshold(self):
        """暴露集中度阈值为正."""
        assert CONCENTRATION_THRESHOLD > 0
        assert CONCENTRATION_THRESHOLD == 0.8

    def test_missing_threshold_negative(self):
        """缺失阈值为负."""
        assert MISSING_THRESHOLD < 0
        assert MISSING_THRESHOLD == -0.3

    def test_barra_style_factors_count(self):
        """Barra 风格因子共 10 个."""
        assert len(BARRA_STYLE_FACTORS) == 10
        for f in ["Size", "Beta", "Momentum", "ResidualVolatility",
                  "NonLinearSize", "BookToPrice", "Liquidity",
                  "EarningsYield", "Growth", "Leverage"]:
            assert f in BARRA_STYLE_FACTORS

    def test_sector_factors_count(self):
        """行业因子共 8 个."""
        assert len(SECTOR_FACTORS) == 8
        for s in ["tech", "manufacturing", "cyclical", "resources",
                  "defensive", "finance", "consumer", "healthcare"]:
            assert s in SECTOR_FACTORS

    def test_factor_names_mapping_complete(self):
        """因子中文名称映射完整."""
        assert FACTOR_NAMES["Size"] == "市值"
        assert FACTOR_NAMES["Beta"] == "市场敏感度"
        assert FACTOR_NAMES["Momentum"] == "动量"
        assert FACTOR_NAMES["finance"] == "金融"
        assert FACTOR_NAMES["tech"] == "科技"
        assert FACTOR_NAMES["consumer"] == "消费"
        assert FACTOR_NAMES["healthcare"] == "医药"
        # 10 风格 + 8 行业 = 18
        assert len(FACTOR_NAMES) >= 18

    def test_category_constants_distinct(self):
        """因子类别常量互不相同."""
        cats = [CATEGORY_STYLE, CATEGORY_SECTOR, CATEGORY_COUNTRY, CATEGORY_SPECIFIC]
        assert len(set(cats)) == len(cats)

    def test_status_codes_distinct(self):
        """状态码互不相同."""
        codes = [STATUS_OK, STATUS_FEATURE_FLAG_DISABLED, STATUS_INSUFFICIENT_DATA,
                 STATUS_FACTOR_MISMATCH, STATUS_EMPTY_INPUT]
        assert len(set(codes)) == len(codes)


# ============================================================
# 2. 异常体系测试
# ============================================================

class TestExceptions:
    """异常体系完整性测试."""

    def test_base_exception_raisable(self):
        """基础异常可被 raise 和 catch."""
        with pytest.raises(FactorAttributionError):
            raise FactorAttributionError("test")

    def test_insufficient_factor_data_inherits_base(self):
        """InsufficientFactorDataError 继承 FactorAttributionError."""
        with pytest.raises(FactorAttributionError):
            raise InsufficientFactorDataError("test")

    def test_factor_mismatch_inherits_base(self):
        """FactorMismatchError 继承 FactorAttributionError."""
        with pytest.raises(FactorAttributionError):
            raise FactorMismatchError("test")

    def test_exposure_not_normalized_inherits_base(self):
        """ExposureNotNormalizedError 继承 FactorAttributionError."""
        with pytest.raises(FactorAttributionError):
            raise ExposureNotNormalizedError("test")

    def test_invalid_factor_input_inherits_base(self):
        """InvalidFactorInputError 继承 FactorAttributionError."""
        with pytest.raises(FactorAttributionError):
            raise InvalidFactorInputError("test")

    def test_specific_exception_catchable(self):
        """具体异常可被自身类型捕获."""
        with pytest.raises(InsufficientFactorDataError):
            raise InsufficientFactorDataError("insufficient")
        with pytest.raises(FactorMismatchError):
            raise FactorMismatchError("mismatch")
        with pytest.raises(ExposureNotNormalizedError):
            raise ExposureNotNormalizedError("not normalized")
        with pytest.raises(InvalidFactorInputError):
            raise InvalidFactorInputError("invalid")


# ============================================================
# 3. 数据类测试
# ============================================================

class TestDataClasses:
    """数据类字段与序列化测试."""

    def test_factor_attribution_default_values(self):
        """FactorAttribution 默认值正确."""
        fa = FactorAttribution()
        assert fa.factor_name == ""
        assert fa.factor_category == CATEGORY_STYLE
        assert fa.factor_display_name == ""
        assert fa.portfolio_exposure == 0.0
        assert fa.benchmark_exposure == 0.0
        assert fa.active_exposure == 0.0
        assert fa.factor_return == 0.0
        assert fa.contribution_to_pnl == 0.0
        assert fa.contribution_pct == 0.0
        assert fa.contribution_to_active_risk == 0.0
        assert fa.is_significant is False
        assert fa.is_concentrated is False
        assert fa.is_missing is False
        assert fa.ic_metrics is None

    def test_factor_attribution_to_dict(self):
        """FactorAttribution.to_dict() 字段完整."""
        fa = FactorAttribution(
            factor_name="Size",
            factor_category=CATEGORY_STYLE,
            factor_display_name="市值",
            portfolio_exposure=0.5,
            benchmark_exposure=0.0,
            active_exposure=0.5,
            factor_return=0.001,
            contribution_to_pnl=500.0,
            contribution_pct=0.5,
            is_significant=True,
        )
        d = fa.to_dict()
        assert d["factor_name"] == "Size"
        assert d["factor_category"] == CATEGORY_STYLE
        assert d["factor_display_name"] == "市值"
        assert d["portfolio_exposure"] == 0.5
        assert d["active_exposure"] == 0.5
        assert d["factor_return"] == 0.001
        assert d["contribution_to_pnl"] == 500.0
        assert d["contribution_pct"] == 0.5
        assert d["is_significant"] is True

    def test_factor_attribution_to_dict_with_ic_metrics(self):
        """FactorAttribution.to_dict() 包含 IC 指标."""
        fa = FactorAttribution(
            factor_name="Momentum",
            ic_metrics={"ic_mean": 0.05, "ic_ir": 0.8},
        )
        d = fa.to_dict()
        assert d["ic_metrics"] == {"ic_mean": 0.05, "ic_ir": 0.8}

    def test_factor_attribution_result_default_values(self):
        """FactorAttributionResult 默认值正确."""
        r = FactorAttributionResult()
        assert r.attribution_date == ""
        assert r.portfolio_value == 0.0
        assert r.total_pnl == 0.0
        assert r.active_return == 0.0
        assert r.factor_pnl == 0.0
        assert r.specific_pnl == 0.0
        assert r.residual == 0.0
        assert r.style_factor_attributions == []
        assert r.sector_factor_attributions == []
        assert r.n_factors == 0
        assert r.n_significant == 0
        assert r.active_risk == 0.0
        assert r.factor_risk == 0.0
        assert r.specific_risk == 0.0
        assert r.information_ratio == 0.0
        assert r.risk_budget == DEFAULT_RISK_BUDGET
        assert r.concentrated_factors == []
        assert r.missing_factors == []
        assert r.status == STATUS_OK

    def test_factor_attribution_result_to_dict(self):
        """FactorAttributionResult.to_dict() 字段完整."""
        r = FactorAttributionResult(
            attribution_date="2026-07-27",
            portfolio_value=1_000_000,
            total_pnl=5000.0,
            active_return=5000.0,
            factor_pnl=4500.0,
            specific_pnl=500.0,
            n_factors=3,
            n_significant=2,
            active_risk=0.04,
            factor_risk=0.03,
            specific_risk=0.026,
            information_ratio=0.5,
            benchmark_code="510300.SH",
            status=STATUS_OK,
        )
        d = r.to_dict()
        assert d["attribution_date"] == "2026-07-27"
        assert d["portfolio_value"] == 1_000_000
        assert d["total_pnl"] == 5000.0
        assert d["factor_pnl"] == 4500.0
        assert d["specific_pnl"] == 500.0
        assert d["n_factors"] == 3
        assert d["n_significant"] == 2
        assert d["active_risk"] == 0.04
        assert d["information_ratio"] == 0.5
        assert d["benchmark_code"] == "510300.SH"
        assert d["status"] == STATUS_OK
        assert d["style_factor_attributions"] == []
        assert d["sector_factor_attributions"] == []

    def test_factor_attribution_result_to_dict_with_attributions(self):
        """FactorAttributionResult.to_dict() 包含因子明细."""
        r = FactorAttributionResult(
            style_factor_attributions=[
                FactorAttribution(factor_name="Size", factor_display_name="市值"),
                FactorAttribution(factor_name="Beta", factor_display_name="市场敏感度"),
            ],
            sector_factor_attributions=[
                FactorAttribution(factor_name="finance", factor_display_name="金融"),
            ],
            n_factors=3,
        )
        d = r.to_dict()
        assert len(d["style_factor_attributions"]) == 2
        assert d["style_factor_attributions"][0]["factor_name"] == "Size"
        assert len(d["sector_factor_attributions"]) == 1
        assert d["sector_factor_attributions"][0]["factor_name"] == "finance"

    def test_factor_attribution_result_to_markdown_contains_sections(self):
        """FactorAttributionResult.to_markdown() 包含必要章节."""
        r = FactorAttributionResult(
            attribution_date="2026-07-27",
            portfolio_value=1_000_000,
            total_pnl=5000.0,
            active_return=5000.0,
            factor_pnl=4500.0,
            specific_pnl=500.0,
            benchmark_code="510300.SH",
            style_factor_attributions=[
                FactorAttribution(factor_name="Size", factor_display_name="市值",
                                  portfolio_exposure=0.5, active_exposure=0.5,
                                  factor_return=0.001, contribution_to_pnl=500.0,
                                  contribution_pct=0.1),
            ],
        )
        md = r.to_markdown()
        assert "因子归因报告" in md
        assert "2026-07-27" in md
        assert "510300.SH" in md
        assert "收益分解" in md
        assert "风险分解" in md
        assert "风格因子明细" in md
        assert "市值" in md

    def test_factor_attribution_result_to_markdown_handles_zero_pnl(self):
        """FactorAttributionResult.to_markdown() 处理零 PnL (避免除零)."""
        r = FactorAttributionResult()
        md = r.to_markdown()
        assert "N/A" in md  # 占比显示 N/A

    def test_factor_attribution_result_to_markdown_includes_concentration(self):
        """FactorAttributionResult.to_markdown() 包含集中因子章节."""
        r = FactorAttributionResult(
            concentrated_factors=["Size", "Beta"],
            missing_factors=["Momentum"],
        )
        md = r.to_markdown()
        assert "暴露集中因子" in md
        assert "Size" in md
        assert "暴露缺失因子" in md
        assert "Momentum" in md


# ============================================================
# 4. 核心算法函数测试
# ============================================================

class TestCoreFunctions:
    """核心算法函数测试."""

    def test_compute_factor_contribution_basic(self):
        """单因子贡献计算: contribution = active_exposure × factor_return × portfolio_value."""
        # active_exposure=0.5, factor_return=0.001, portfolio_value=1_000_000
        # contribution = 0.5 × 0.001 × 1_000_000 = 500
        contrib = compute_factor_contribution(0.5, 0.001, 1_000_000)
        assert abs(contrib - 500.0) < 1e-6

    def test_compute_factor_contribution_zero_exposure(self):
        """主动暴露为 0 时贡献为 0."""
        contrib = compute_factor_contribution(0.0, 0.001, 1_000_000)
        assert contrib == 0.0

    def test_compute_factor_contribution_zero_return(self):
        """因子收益率为 0 时贡献为 0."""
        contrib = compute_factor_contribution(0.5, 0.0, 1_000_000)
        assert contrib == 0.0

    def test_compute_factor_contribution_negative_exposure(self):
        """负主动暴露 → 负贡献."""
        contrib = compute_factor_contribution(-0.5, 0.001, 1_000_000)
        assert contrib < 0

    def test_compute_factor_contribution_negative_return(self):
        """负因子收益率 → 负贡献."""
        contrib = compute_factor_contribution(0.5, -0.001, 1_000_000)
        assert contrib < 0

    def test_compute_active_exposure_basic(self):
        """主动暴露 = 组合暴露 - 基准暴露."""
        assert compute_active_exposure(0.5, 0.3) == 0.2
        assert compute_active_exposure(0.3, 0.5) == -0.2
        assert compute_active_exposure(0.5, 0.5) == 0.0

    def test_compute_active_exposure_zero_benchmark(self):
        """基准暴露为 0 时主动暴露等于组合暴露."""
        assert compute_active_exposure(0.5, 0.0) == 0.5

    def test_compute_factor_risk_simple_mode(self):
        """简化模式因子风险 (协方差矩阵=None)."""
        # 假设因子独立, factor_risk = √(Σ active_exposure²) × √(252)
        exposures = {"Size": 0.5, "Beta": 0.3}
        # √(0.25 + 0.09) × √252 = √0.34 × 15.8745 ≈ 0.5831 × 15.8745 ≈ 9.256
        risk = compute_factor_risk(exposures)
        expected = math.sqrt(0.5**2 + 0.3**2) * math.sqrt(252)
        assert abs(risk - expected) < 1e-6

    def test_compute_factor_risk_empty_exposures(self):
        """空暴露字典返回 0."""
        assert compute_factor_risk({}) == 0.0

    def test_compute_factor_risk_with_cov_matrix(self):
        """使用协方差矩阵计算因子风险."""
        exposures = {"Size": 0.5, "Beta": 0.3}
        # 协方差矩阵: 对角 0.01, 非对角 0
        cov = {
            "Size": {"Size": 0.01, "Beta": 0.0},
            "Beta": {"Size": 0.0, "Beta": 0.01},
        }
        # variance = 0.5²×0.01 + 0.3²×0.01 = 0.0025 + 0.0009 = 0.0034
        # risk = √0.0034 × √252 ≈ 0.0583 × 15.8745 ≈ 0.926
        risk = compute_factor_risk(exposures, cov)
        expected = math.sqrt(0.0034) * math.sqrt(252)
        assert abs(risk - expected) < 1e-6

    def test_compute_factor_risk_with_correlated_factors(self):
        """相关性非零时因子风险更大."""
        exposures = {"Size": 0.5, "Beta": 0.3}
        cov_uncorrelated = {
            "Size": {"Size": 0.01, "Beta": 0.0},
            "Beta": {"Size": 0.0, "Beta": 0.01},
        }
        cov_correlated = {
            "Size": {"Size": 0.01, "Beta": 0.005},
            "Beta": {"Size": 0.005, "Beta": 0.01},
        }
        risk_uncorrelated = compute_factor_risk(exposures, cov_uncorrelated)
        risk_correlated = compute_factor_risk(exposures, cov_correlated)
        # 正相关会增加风险
        assert risk_correlated > risk_uncorrelated

    def test_compute_specific_risk_basic(self):
        """个股特异性风险计算."""
        weights = {"600519": 0.4, "000858": 0.6}
        specific_risks = {"600519": 0.02, "000858": 0.025}
        # variance = 0.4²×0.02² + 0.6²×0.025² = 0.16×0.0004 + 0.36×0.000625
        #        = 0.000064 + 0.000225 = 0.000289
        # risk = √0.000289 × √252 ≈ 0.017 × 15.8745 ≈ 0.27
        risk = compute_specific_risk(weights, specific_risks)
        expected = math.sqrt(0.000289) * math.sqrt(252)
        assert abs(risk - expected) < 1e-6

    def test_compute_specific_risk_empty_weights(self):
        """空权重返回 0."""
        assert compute_specific_risk({}, {"600519": 0.02}) == 0.0

    def test_compute_specific_risk_empty_risks(self):
        """空风险字典返回 0."""
        assert compute_specific_risk({"600519": 0.4}, {}) == 0.0

    def test_compute_information_ratio_basic(self):
        """信息比率 = 主动收益 / 主动风险."""
        ir = compute_information_ratio(0.05, 0.10)
        assert abs(ir - 0.5) < 1e-10

    def test_compute_information_ratio_zero_risk(self):
        """主动风险为 0 时返回 0 (避免除零)."""
        assert compute_information_ratio(0.05, 0.0) == 0.0

    def test_compute_information_ratio_zero_return(self):
        """主动收益为 0 时 IR 为 0."""
        assert compute_information_ratio(0.0, 0.10) == 0.0

    def test_compute_information_ratio_negative_return(self):
        """负主动收益 → 负 IR."""
        ir = compute_information_ratio(-0.05, 0.10)
        assert ir < 0

    def test_align_factors_identical(self):
        """相同因子集合."""
        factors = align_factors(
            {"Size": 0.5, "Beta": 0.3},
            {"Size": 0.0, "Beta": 1.0},
            {"Size": 0.001, "Beta": 0.002},
        )
        assert factors == ["Beta", "Size"]  # 按字母序

    def test_align_factors_different_sets(self):
        """不同因子集合取并集."""
        factors = align_factors(
            {"Size": 0.5, "Beta": 0.3},
            {"Beta": 1.0, "Momentum": 0.0},  # Momentum 新增
            {"Size": 0.001, "Beta": 0.002, "Growth": 0.003},  # Growth 新增
        )
        assert set(factors) == {"Size", "Beta", "Momentum", "Growth"}
        assert factors == sorted(factors)  # 按字母序

    def test_align_factors_empty(self):
        """空字典返回空列表."""
        assert align_factors({}, {}, {}) == []

    def test_categorize_factor_style(self):
        """风格因子分类."""
        assert categorize_factor("Size") == CATEGORY_STYLE
        assert categorize_factor("Beta") == CATEGORY_STYLE
        assert categorize_factor("Momentum") == CATEGORY_STYLE
        assert categorize_factor("ResidualVolatility") == CATEGORY_STYLE
        assert categorize_factor("NonLinearSize") == CATEGORY_STYLE
        assert categorize_factor("BookToPrice") == CATEGORY_STYLE
        assert categorize_factor("Liquidity") == CATEGORY_STYLE
        assert categorize_factor("EarningsYield") == CATEGORY_STYLE
        assert categorize_factor("Growth") == CATEGORY_STYLE
        assert categorize_factor("Leverage") == CATEGORY_STYLE

    def test_categorize_factor_sector(self):
        """行业因子分类."""
        assert categorize_factor("tech") == CATEGORY_SECTOR
        assert categorize_factor("manufacturing") == CATEGORY_SECTOR
        assert categorize_factor("cyclical") == CATEGORY_SECTOR
        assert categorize_factor("resources") == CATEGORY_SECTOR
        assert categorize_factor("defensive") == CATEGORY_SECTOR
        assert categorize_factor("finance") == CATEGORY_SECTOR
        assert categorize_factor("consumer") == CATEGORY_SECTOR
        assert categorize_factor("healthcare") == CATEGORY_SECTOR

    def test_categorize_factor_unknown_defaults_to_style(self):
        """未知因子默认归为风格."""
        assert categorize_factor("unknown_factor") == CATEGORY_STYLE


# ============================================================
# 5. attribute_factors 主函数测试
# ============================================================

class TestAttributeFactors:
    """attribute_factors 主函数测试."""

    def test_basic_attribution(self):
        """基础归因: 单因子贡献正确."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 1.1},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            attribution_date="2026-07-27",
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 2
        # Size 贡献: 0.5 × 0.001 × 1_000_000 = 500
        # Beta 贡献: 0.1 × 0.002 × 1_000_000 = 200
        # 总因子 PnL: 700
        assert abs(result.factor_pnl - 700.0) < 1e-6
        # 残差 = active_return - factor_pnl - specific_pnl = 700 - 700 - 0 = 0
        assert abs(result.residual) < 1e-6

    def test_attribution_with_specific_pnl(self):
        """包含个股特异性收益的归因."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.0},
            portfolio_value=1_000_000,
            specific_pnl=200.0,
        )
        # 因子贡献 = 0.5 × 0.001 × 1_000_000 = 500 (Beta 贡献为 0)
        # 主动收益 = 500 + 200 = 700
        assert abs(result.factor_pnl - 500.0) < 1e-6
        assert abs(result.specific_pnl - 200.0) < 1e-6
        assert abs(result.active_return - 700.0) < 1e-6
        assert abs(result.residual) < 1e-6

    def test_attribution_with_explicit_active_return(self):
        """显式提供主动收益时残差 = active_return - factor_pnl - specific_pnl."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.0},
            portfolio_value=1_000_000,
            specific_pnl=100.0,
            active_return=800.0,  # 显式提供
        )
        # factor_pnl = 500, specific_pnl = 100, active_return = 800
        # residual = 800 - 500 - 100 = 200
        assert abs(result.factor_pnl - 500.0) < 1e-6
        assert abs(result.active_return - 800.0) < 1e-6
        assert abs(result.residual - 200.0) < 1e-6

    def test_attribution_with_sector_factors(self):
        """包含行业因子的归因 (分类到 sector_factor_attributions)."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "finance": 0.4},
            benchmark_exposures={"Size": 0.0, "finance": 0.3},
            factor_returns={"Size": 0.001, "finance": 0.002},
            portfolio_value=1_000_000,
        )
        assert result.status == STATUS_OK
        assert len(result.style_factor_attributions) == 1  # Size
        assert len(result.sector_factor_attributions) == 1  # finance
        assert result.style_factor_attributions[0].factor_name == "Size"
        assert result.sector_factor_attributions[0].factor_name == "finance"

    def test_attribution_factor_alignment(self):
        """因子对齐: 缺失因子按 0 处理."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5},  # 缺 Beta
            benchmark_exposures={"Beta": 1.0},  # 缺 Size
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        assert result.n_factors == 2
        # Size: active_exposure = 0.5 - 0 = 0.5
        # Beta: active_exposure = 0 - 1.0 = -1.0
        size_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Size")
        beta_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Beta")
        assert abs(size_fa.active_exposure - 0.5) < 1e-6
        assert abs(beta_fa.active_exposure - (-1.0)) < 1e-6

    def test_attribution_factor_pnl_sum_equals_total(self):
        """数学恒等式: factor_pnl = Σ contribution_i."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3, "Momentum": 0.2},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0, "Momentum": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005},
            portfolio_value=1_000_000,
        )
        # 各因子贡献
        expected_pnl = (
            0.5 * 0.001 * 1_000_000 +    # Size: 500
            (-0.7) * 0.002 * 1_000_000 +  # Beta: -1400
            0.2 * 0.005 * 1_000_000        # Momentum: 1000
        )
        assert abs(result.factor_pnl - expected_pnl) < 1e-6
        # 验证手动求和 = factor_pnl
        manual_sum = sum(f.contribution_to_pnl for f in result.style_factor_attributions)
        assert abs(manual_sum - result.factor_pnl) < 1e-6

    def test_attribution_residual_is_zero_when_no_explicit_active_return(self):
        """无显式 active_return 时残差为 0 (数学恒等式)."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            specific_pnl=300.0,
        )
        # active_return = factor_pnl + specific_pnl
        # residual = active_return - factor_pnl - specific_pnl = 0
        assert abs(result.residual) < 1e-6

    def test_attribution_empty_inputs_raises(self):
        """空输入抛 InvalidFactorInputError."""
        with pytest.raises(InvalidFactorInputError):
            attribute_factors(
                portfolio_exposures={},
                benchmark_exposures={},
                factor_returns={"Size": 0.001},
                portfolio_value=1_000_000,
            )

    def test_attribution_empty_factor_returns_raises(self):
        """因子收益率为空抛 InvalidFactorInputError."""
        with pytest.raises(InvalidFactorInputError):
            attribute_factors(
                portfolio_exposures={"Size": 0.5},
                benchmark_exposures={"Size": 0.0},
                factor_returns={},
                portfolio_value=1_000_000,
            )

    def test_attribution_non_positive_portfolio_value_raises(self):
        """组合价值非正抛 InvalidFactorInputError."""
        with pytest.raises(InvalidFactorInputError):
            attribute_factors(
                portfolio_exposures={"Size": 0.5},
                benchmark_exposures={"Size": 0.0},
                factor_returns={"Size": 0.001},
                portfolio_value=0,
            )
        with pytest.raises(InvalidFactorInputError):
            attribute_factors(
                portfolio_exposures={"Size": 0.5},
                benchmark_exposures={"Size": 0.0},
                factor_returns={"Size": 0.001},
                portfolio_value=-100,
            )

    def test_attribution_insufficient_factors_returns_status(self):
        """因子数 < 最小要求返回 insufficient_data 状态."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5},  # 只有 1 个因子
            benchmark_exposures={"Size": 0.0},
            factor_returns={"Size": 0.001},
            portfolio_value=1_000_000,
        )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert "因子数" in result.reason

    def test_attribution_significance_flag(self):
        """显著性标记: |贡献/组合价值| > 阈值."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.001},  # Beta 暴露极小
            benchmark_exposures={"Size": 0.0, "Beta": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            significance_threshold=0.005,  # 0.5%
        )
        # Size 贡献 = 0.5 × 0.001 × 1M = 500, 500/1M = 0.0005 < 0.005 → 不显著
        # Beta 贡献 = 0.001 × 0.002 × 1M = 0.002, 远小于阈值 → 不显著
        # 注意: 实际计算时, 由于 contribution/portfolio_value = 0.0005 < 0.005
        size_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Size")
        assert not size_fa.is_significant

    def test_attribution_significant_factor(self):
        """显著因子标记: 贡献足够大时为显著."""
        result = attribute_factors(
            portfolio_exposures={"Size": 1.0},  # 大暴露
            benchmark_exposures={"Size": 0.0},
            factor_returns={"Size": 0.01},  # 大因子收益
            portfolio_value=1_000_000,
            significance_threshold=0.005,
        )
        # 贡献 = 1.0 × 0.01 × 1M = 10000, 10000/1M = 0.01 > 0.005 → 显著
        # 但 n_factors=1 < 2, 返回 insufficient_data
        assert result.status == STATUS_INSUFFICIENT_DATA

    def test_attribution_concentration_flag(self):
        """暴露集中度标记: |active_exposure| > 0.8."""
        result = attribute_factors(
            portfolio_exposures={"Size": 1.0, "Beta": 0.3},  # Size 主动暴露 1.0
            benchmark_exposures={"Size": 0.0, "Beta": 0.3},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        assert "Size" in result.concentrated_factors
        assert "Beta" not in result.concentrated_factors

    def test_attribution_missing_flag(self):
        """暴露缺失标记: active_exposure < -0.3."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.0, "Beta": 0.3},
            benchmark_exposures={"Size": 0.5, "Beta": 0.3},  # Size 缺失 0 - 0.5 = -0.5
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        assert "Size" in result.missing_factors

    def test_attribution_factor_pnl_sorting(self):
        """因子按贡献绝对值降序排序."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3, "Momentum": 0.2},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0, "Momentum": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005},
            portfolio_value=1_000_000,
        )
        # 贡献: Size=500, Beta=-1400, Momentum=1000
        # 按绝对值降序: Beta(1400) > Momentum(1000) > Size(500)
        contribs = [abs(f.contribution_to_pnl) for f in result.style_factor_attributions]
        assert contribs == sorted(contribs, reverse=True)

    def test_attribution_contribution_pct(self):
        """贡献占比 = 单因子贡献 / 总因子 PnL."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        # Size: 500, Beta: 600, 总: 1100
        # Size 占比: 500/1100 ≈ 0.4545
        # Beta 占比: 600/1100 ≈ 0.5455
        size_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Size")
        beta_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Beta")
        assert abs(size_fa.contribution_pct - 500.0 / 1100.0) < 1e-6
        assert abs(beta_fa.contribution_pct - 600.0 / 1100.0) < 1e-6

    def test_attribution_with_risk_computation(self):
        """归因包含风险分解计算."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4, "000858": 0.3},
            stock_specific_risks={"600519": 0.02, "000858": 0.025},
        )
        assert result.active_risk > 0
        assert result.factor_risk > 0
        assert result.specific_risk > 0
        # 主动风险 = √(factor_risk² + specific_risk²)
        expected_active = math.sqrt(result.factor_risk**2 + result.specific_risk**2)
        assert abs(result.active_risk - expected_active) < 1e-6
        # 因子风险占比 = factor_risk / active_risk
        expected_pct = result.factor_risk / result.active_risk
        assert abs(result.factor_risk_pct - expected_pct) < 1e-6

    def test_attribution_risk_budget_audit(self):
        """风险预算审计."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            risk_budget=0.10,  # 10%
        )
        assert result.risk_budget == 0.10
        assert result.risk_budget_used == result.active_risk
        assert result.risk_budget_remaining == max(0.0, 0.10 - result.active_risk)
        if result.active_risk > 0:
            expected_util = result.active_risk / 0.10
            assert abs(result.risk_budget_utilization - expected_util) < 1e-6

    def test_attribution_information_ratio(self):
        """信息比率 = 主动收益 / 主动风险."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4},
            stock_specific_risks={"600519": 0.02},
        )
        if result.active_risk > 0:
            expected_ir = result.active_return / result.active_risk
            assert abs(result.information_ratio - expected_ir) < 1e-6

    def test_attribution_with_ic_metrics(self):
        """归因包含 IC 指标元数据."""
        ic_metrics = {
            "Size": {"ic_mean": 0.05, "ic_ir": 0.8},
            "Beta": {"ic_mean": 0.02, "ic_ir": 0.4},
        }
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            ic_metrics=ic_metrics,
        )
        size_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Size")
        assert size_fa.ic_metrics == {"ic_mean": 0.05, "ic_ir": 0.8}
        beta_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Beta")
        assert beta_fa.ic_metrics == {"ic_mean": 0.02, "ic_ir": 0.4}

    def test_attribution_benchmark_code_passed_through(self):
        """基准标的代码透传."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            benchmark_code="510500.SH",
        )
        assert result.benchmark_code == "510500.SH"

    def test_attribution_total_return_pct(self):
        """总收益率 = 主动收益 / 组合价值."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        # factor_pnl = 500 + 600 = 1100, active_return = 1100
        # total_return_pct = 1100 / 1_000_000 = 0.0011
        assert abs(result.total_return_pct - 0.0011) < 1e-6


# ============================================================
# 6. FactorAttributionManager 主类测试
# ============================================================

class TestFactorAttributionManager:
    """FactorAttributionManager 主类测试."""

    def test_init_with_defaults(self):
        """默认初始化."""
        mgr = FactorAttributionManager()
        assert mgr._feature_flag_name == FLAG_NAME
        assert mgr._benchmark_code == DEFAULT_PRIMARY_BENCHMARK
        assert mgr._risk_budget == DEFAULT_RISK_BUDGET
        assert mgr._min_factors == DEFAULT_MIN_FACTORS

    def test_init_with_custom_config(self):
        """自定义配置初始化."""
        custom_cfg = {
            "settings": {
                "primary_benchmark": "510500.SH",
                "risk_budget": 0.08,
                "min_factors": 3,
                "significance_threshold": 0.01,
            },
            "style_factors": [{"code": "CustomFactor", "name": "自定义因子"}],
            "sector_factors": [{"code": "custom_sector", "name": "自定义行业"}],
        }
        mgr = FactorAttributionManager(config=custom_cfg)
        assert mgr._benchmark_code == "510500.SH"
        assert mgr._risk_budget == 0.08
        assert mgr._min_factors == 3
        assert mgr._significance_threshold == 0.01
        assert mgr._factor_names.get("CustomFactor") == "自定义因子"
        assert mgr._factor_names.get("custom_sector") == "自定义行业"

    def test_attribute_with_feature_flag_disabled(self):
        """Feature Flag 关闭时返回降级结果 (HC-1)."""
        mgr = FactorAttributionManager()
        # Feature Flag 默认 False
        result = mgr.attribute(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        assert result.status == STATUS_FEATURE_FLAG_DISABLED
        assert FLAG_NAME in result.reason
        assert result.factor_pnl == 0.0
        assert result.active_return == 0.0
        assert result.style_factor_attributions == []

    def test_attribute_with_feature_flag_enabled(self):
        """Feature Flag 启用时执行归因."""
        mgr = FactorAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_exposures={"Size": 0.5, "Beta": 1.1},
                benchmark_exposures={"Size": 0.0, "Beta": 1.0},
                factor_returns={"Size": 0.001, "Beta": 0.002},
                portfolio_value=1_000_000,
                attribution_date="2026-07-27",
            )
        assert result.status == STATUS_OK
        assert result.n_factors == 2
        # Size 贡献: 500, Beta 贡献: 200, 总 700
        assert abs(result.factor_pnl - 700.0) < 1e-6

    def test_attribute_uses_default_benchmark_exposures(self):
        """未提供 benchmark_exposures 时使用配置中的默认值."""
        mgr = FactorAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_exposures={"Size": 0.5, "Beta": 1.1},
                factor_returns={"Size": 0.001, "Beta": 0.002},
                portfolio_value=1_000_000,
            )
        # 使用配置中的默认基准暴露
        assert result.status == STATUS_OK

    def test_attribute_returns_insufficient_when_no_benchmark(self):
        """无基准暴露且配置无默认值时返回 insufficient_data."""
        mgr = FactorAttributionManager(
            config={"settings": {}, "benchmark_factor_exposures": {}}
        )
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_exposures={"Size": 0.5},
                factor_returns={"Size": 0.001},
                portfolio_value=1_000_000,
            )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert "基准暴露" in result.reason

    def test_attribute_returns_insufficient_when_no_returns(self):
        """未提供因子收益率时返回 insufficient_data."""
        mgr = FactorAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_exposures={"Size": 0.5},
                benchmark_exposures={"Size": 0.0},
                factor_returns={},
                portfolio_value=1_000_000,
            )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert "因子收益率" in result.reason

    def test_attribute_handles_invalid_input_gracefully(self):
        """无效输入优雅处理 (返回 empty_input 状态)."""
        mgr = FactorAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_exposures={},
                benchmark_exposures={},
                factor_returns={"Size": 0.001},
                portfolio_value=1_000_000,
            )
        assert result.status == STATUS_EMPTY_INPUT

    def test_attribute_handles_non_positive_portfolio_value(self):
        """组合价值非正返回 empty_input."""
        mgr = FactorAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute(
                portfolio_exposures={"Size": 0.5},
                benchmark_exposures={"Size": 0.0},
                factor_returns={"Size": 0.001},
                portfolio_value=0,
            )
        assert result.status == STATUS_EMPTY_INPUT

    def test_attribute_from_positions_basic(self):
        """从持仓列表归因 (聚合到因子维度)."""
        mgr = FactorAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute_from_positions(
                portfolio_positions=[
                    {"code": "600519", "weight": 0.4,
                     "factor_exposures": {"Size": 0.5, "Beta": 1.1}},
                    {"code": "000858", "weight": 0.6,
                     "factor_exposures": {"Size": 0.3, "Beta": 0.9}},
                ],
                benchmark_positions=[
                    {"code": "600519", "weight": 0.5,
                     "factor_exposures": {"Size": 0.0, "Beta": 1.0}},
                    {"code": "000858", "weight": 0.5,
                     "factor_exposures": {"Size": 0.0, "Beta": 1.0}},
                ],
                factor_returns={"Size": 0.001, "Beta": 0.002},
                portfolio_value=1_000_000,
                attribution_date="2026-07-27",
            )
        assert result.status == STATUS_OK
        assert result.n_factors == 2  # Size + Beta

    def test_attribute_from_positions_flag_disabled(self):
        """Feature Flag 关闭时持仓列表归因返回降级结果."""
        mgr = FactorAttributionManager()
        result = mgr.attribute_from_positions(
            portfolio_positions=[
                {"code": "600519", "weight": 1.0, "factor_exposures": {"Size": 0.5}}
            ],
            benchmark_positions=[
                {"code": "600519", "weight": 1.0, "factor_exposures": {"Size": 0.0}}
            ],
            factor_returns={"Size": 0.001},
        )
        assert result.status == STATUS_FEATURE_FLAG_DISABLED

    def test_attribute_from_positions_empty_list(self):
        """空持仓列表返回 insufficient_data."""
        mgr = FactorAttributionManager()
        with patch.object(mgr, '_is_enabled', return_value=True):
            result = mgr.attribute_from_positions(
                portfolio_positions=[],
                benchmark_positions=[
                    {"code": "a", "weight": 1.0, "factor_exposures": {"Size": 0.0}}
                ],
                factor_returns={"Size": 0.001},
            )
        assert result.status == STATUS_INSUFFICIENT_DATA

    def test_get_default_benchmark_exposures(self):
        """获取默认基准因子暴露."""
        mgr = FactorAttributionManager()
        exposures = mgr.get_default_benchmark_exposures()
        assert isinstance(exposures, dict)
        # 配置文件中应该有因子暴露默认值
        # 至少包含 Beta=1.0 (市场中性基准)
        assert "Beta" in exposures or len(exposures) > 0

    def test_get_factor_names(self):
        """获取因子名称映射."""
        mgr = FactorAttributionManager()
        names = mgr.get_factor_names()
        assert isinstance(names, dict)
        assert names.get("Size") == "市值"
        assert names.get("Beta") == "市场敏感度"
        assert names.get("finance") == "金融"
        assert names.get("tech") == "科技"

    def test_aggregate_positions_to_factors(self):
        """持仓聚合到因子维度 (加权平均)."""
        mgr = FactorAttributionManager()
        positions = [
            {"code": "600519", "weight": 0.4,
             "factor_exposures": {"Size": 0.5, "Beta": 1.1}},
            {"code": "000858", "weight": 0.6,
             "factor_exposures": {"Size": 0.3, "Beta": 0.9}},
        ]
        exposures, active_weights = mgr._aggregate_positions_to_factors(positions)
        # Size 加权平均: 0.4×0.5 + 0.6×0.3 = 0.38, 权重和 = 1.0, 所以 0.38/1.0 = 0.38
        assert abs(exposures["Size"] - 0.38) < 1e-6
        # Beta 加权平均: 0.4×1.1 + 0.6×0.9 = 0.98
        assert abs(exposures["Beta"] - 0.98) < 1e-6
        # 主动权重
        assert active_weights["600519"] == 0.4
        assert active_weights["000858"] == 0.6

    def test_load_config_returns_dict(self):
        """_load_config 返回字典 (走 ConfigManager)."""
        mgr = FactorAttributionManager()
        cfg = mgr._load_config(DEFAULT_CONFIG_NAME)
        assert isinstance(cfg, dict)

    def test_load_config_handles_exception(self):
        """_load_config 异常时返回空字典."""
        mgr = FactorAttributionManager()
        with patch('utils.attribution.factor_attribution.get_config',
                   side_effect=Exception("test")):
            cfg = mgr._load_config("non_existent")
        assert cfg == {}

    def test_is_enabled_returns_bool(self):
        """_is_enabled 返回布尔值."""
        mgr = FactorAttributionManager()
        result = mgr._is_enabled()
        assert isinstance(result, bool)

    def test_is_enabled_handles_exception(self):
        """_is_enabled 异常时返回 False."""
        mgr = FactorAttributionManager()
        with patch('utils.infra.feature_flags.is_enabled',
                   side_effect=Exception("test")):
            result = mgr._is_enabled()
        assert result is False


# ============================================================
# 7. Feature Flag 透传测试
# ============================================================

class TestFeatureFlag:
    """Feature Flag 透传测试 (HC-1)."""

    def test_is_factor_attribution_enabled_returns_bool(self):
        """is_factor_attribution_enabled 返回布尔值."""
        result = is_factor_attribution_enabled()
        assert isinstance(result, bool)

    def test_is_factor_attribution_enabled_default_false(self):
        """Feature Flag 默认 False (HC-1)."""
        # 由于环境未设置 USE_FACTOR_ATTRIBUTION=True, 应返回 False
        result = is_factor_attribution_enabled()
        assert result is False

    def test_is_factor_attribution_enabled_handles_import_error(self):
        """导入失败时返回 False."""
        with patch('utils.infra.feature_flags.is_enabled',
                   side_effect=ImportError("no module")):
            result = is_factor_attribution_enabled()
        assert result is False

    def test_is_factor_attribution_enabled_handles_exception(self):
        """异常时返回 False."""
        with patch('utils.infra.feature_flags.is_enabled',
                   side_effect=RuntimeError("test")):
            result = is_factor_attribution_enabled()
        assert result is False

    def test_feature_flag_disabled_returns_zero_result(self):
        """Feature Flag 关闭时返回全零降级结果 (HC-1)."""
        mgr = FactorAttributionManager()
        result = mgr.attribute(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        assert result.status == STATUS_FEATURE_FLAG_DISABLED
        assert result.factor_pnl == 0.0
        assert result.specific_pnl == 0.0
        assert result.active_return == 0.0
        assert result.active_risk == 0.0
        assert result.information_ratio == 0.0
        assert result.style_factor_attributions == []
        assert result.sector_factor_attributions == []

    def test_feature_flag_disabled_does_not_execute_algorithm(self):
        """Feature Flag 关闭时不执行算法 (零开销)."""
        mgr = FactorAttributionManager()
        with patch('utils.attribution.factor_attribution.attribute_factors') as mock_attr:
            result = mgr.attribute(
                portfolio_exposures={"Size": 0.5},
                benchmark_exposures={"Size": 0.0},
                factor_returns={"Size": 0.001},
                portfolio_value=1_000_000,
            )
        mock_attr.assert_not_called()
        assert result.status == STATUS_FEATURE_FLAG_DISABLED


# ============================================================
# 8. 便捷函数测试
# ============================================================

class TestConvenienceFunctions:
    """便捷函数测试."""

    def test_attribute_factors_simple_basic(self):
        """attribute_factors_simple 基础调用."""
        result = attribute_factors_simple(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            attribution_date="2026-07-27",
        )
        assert result.status == STATUS_OK
        assert result.benchmark_code == DEFAULT_PRIMARY_BENCHMARK
        assert result.n_factors == 2

    def test_attribute_factors_simple_default_benchmark(self):
        """attribute_factors_simple 默认使用主基准."""
        result = attribute_factors_simple(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
        )
        assert result.benchmark_code == DEFAULT_PRIMARY_BENCHMARK

    def test_create_default_manager(self):
        """create_default_manager 创建默认配置的管理器."""
        mgr = create_default_manager()
        assert isinstance(mgr, FactorAttributionManager)
        assert mgr._feature_flag_name == FLAG_NAME
        assert mgr._benchmark_code == DEFAULT_PRIMARY_BENCHMARK


# ============================================================
# 9. 边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_single_factor_returns_insufficient_data(self):
        """单因子返回 insufficient_data (n < MIN_FACTORS)."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5},
            benchmark_exposures={"Size": 0.0},
            factor_returns={"Size": 0.001},
            portfolio_value=1_000_000,
        )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert "因子数" in result.reason

    def test_zero_active_exposures(self):
        """所有主动暴露为 0 时因子 PnL 为 0."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 1.0},
            benchmark_exposures={"Size": 0.5, "Beta": 1.0},  # 完全相同
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        assert result.factor_pnl == 0.0
        assert result.active_return == 0.0

    def test_zero_factor_returns(self):
        """所有因子收益率为 0 时因子 PnL 为 0."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.0, "Beta": 0.0},
            portfolio_value=1_000_000,
        )
        assert result.factor_pnl == 0.0
        # 贡献占比为 0 (避免除零)
        for fa in result.style_factor_attributions:
            assert fa.contribution_pct == 0.0

    def test_negative_active_exposure(self):
        """负主动暴露产生负贡献."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.0, "Beta": 0.3},
            benchmark_exposures={"Size": 0.5, "Beta": 0.3},  # Size 缺失
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
        )
        size_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Size")
        assert size_fa.active_exposure < 0
        assert size_fa.contribution_to_pnl < 0

    def test_large_portfolio_value(self):
        """大组合价值计算正确."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000_000,  # 10 亿
        )
        # Size 贡献: 0.5 × 0.001 × 1e9 = 500000
        # Beta 贡献: 0.3 × 0.002 × 1e9 = 600000
        assert abs(result.factor_pnl - 1_100_000.0) < 1e-3

    def test_attribution_with_factor_cov_matrix(self):
        """归因支持协方差矩阵."""
        cov = {
            "Size": {"Size": 0.01, "Beta": 0.0},
            "Beta": {"Size": 0.0, "Beta": 0.01},
        }
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            factor_cov_matrix=cov,
        )
        assert result.status == STATUS_OK
        assert result.factor_risk > 0

    def test_attribution_with_only_style_factors(self):
        """仅风格因子归因 (无行业因子)."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3, "Momentum": 0.2},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0, "Momentum": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005},
            portfolio_value=1_000_000,
        )
        assert len(result.style_factor_attributions) == 3
        assert len(result.sector_factor_attributions) == 0

    def test_attribution_with_only_sector_factors(self):
        """仅行业因子归因 (无风格因子)."""
        result = attribute_factors(
            portfolio_exposures={"finance": 0.4, "tech": 0.3},
            benchmark_exposures={"finance": 0.3, "tech": 0.4},
            factor_returns={"finance": 0.002, "tech": 0.003},
            portfolio_value=1_000_000,
        )
        assert len(result.style_factor_attributions) == 0
        assert len(result.sector_factor_attributions) == 2

    def test_attribution_mixed_style_and_sector(self):
        """混合风格和行业因子归因."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "finance": 0.4, "tech": 0.3},
            benchmark_exposures={"Size": 0.0, "finance": 0.3, "tech": 0.4},
            factor_returns={"Size": 0.001, "finance": 0.002, "tech": 0.003},
            portfolio_value=1_000_000,
        )
        assert len(result.style_factor_attributions) == 1  # Size
        assert len(result.sector_factor_attributions) == 2  # finance + tech
        assert result.n_factors == 3

    def test_attribution_unknown_factor_classified_as_style(self):
        """未知因子默认归为风格因子."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "UnknownFactor": 0.3},
            benchmark_exposures={"Size": 0.0, "UnknownFactor": 0.0},
            factor_returns={"Size": 0.001, "UnknownFactor": 0.002},
            portfolio_value=1_000_000,
        )
        # UnknownFactor 默认归为风格
        assert len(result.style_factor_attributions) == 2
        assert len(result.sector_factor_attributions) == 0

    def test_attribution_with_custom_factor_names(self):
        """自定义因子名称映射."""
        custom_names = {"Size": "市值(自定义)", "Beta": "贝塔(自定义)"}
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            factor_names=custom_names,
        )
        size_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Size")
        assert size_fa.factor_display_name == "市值(自定义)"
        beta_fa = next(f for f in result.style_factor_attributions if f.factor_name == "Beta")
        assert beta_fa.factor_display_name == "贝塔(自定义)"

    def test_attribution_with_custom_annualization_factor(self):
        """自定义年化因子."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4},
            stock_specific_risks={"600519": 0.02},
            annualization_factor=52,  # 周度
        )
        # 因子风险应小于年化 252 的版本
        result_annual = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4},
            stock_specific_risks={"600519": 0.02},
            annualization_factor=252,
        )
        assert result.factor_risk < result_annual.factor_risk


# ============================================================
# 10. 数学恒等式与一致性测试
# ============================================================

class TestMathematicalIdentities:
    """数学恒等式验证测试."""

    def test_factor_pnl_equals_sum_of_contributions(self):
        """因子 PnL = Σ 单因子贡献."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3, "Momentum": 0.2,
                                 "finance": 0.4},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0, "Momentum": 0.0,
                                 "finance": 0.3},
            factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005,
                            "finance": 0.003},
            portfolio_value=1_000_000,
        )
        all_attributions = result.style_factor_attributions + result.sector_factor_attributions
        manual_sum = sum(f.contribution_to_pnl for f in all_attributions)
        assert abs(manual_sum - result.factor_pnl) < 1e-6

    def test_residual_zero_when_no_explicit_active_return(self):
        """无显式 active_return 时残差 = 0."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            specific_pnl=300.0,
        )
        # active_return = factor_pnl + specific_pnl
        # residual = active_return - factor_pnl - specific_pnl = 0
        assert abs(result.residual) < 1e-6
        assert abs(result.active_return - (result.factor_pnl + result.specific_pnl)) < 1e-6

    def test_active_risk_pythagorean(self):
        """主动风险满足勾股定理: active_risk² = factor_risk² + specific_risk²."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4, "000858": 0.3},
            stock_specific_risks={"600519": 0.02, "000858": 0.025},
        )
        # active_risk² = factor_risk² + specific_risk²
        assert abs(result.active_risk**2 -
                   (result.factor_risk**2 + result.specific_risk**2)) < 1e-6

    def test_factor_risk_pct_between_zero_and_one(self):
        """因子风险占比在 [0, 1] 之间."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4},
            stock_specific_risks={"600519": 0.02},
        )
        assert 0.0 <= result.factor_risk_pct <= 1.0

    def test_contribution_pct_sum_equals_one(self):
        """所有因子贡献占比之和 = 1 (或 -1, 当总 PnL 为负时)."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3, "Momentum": 0.2},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0, "Momentum": 0.0},
            factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005},
            portfolio_value=1_000_000,
        )
        total_pct = sum(f.contribution_pct for f in result.style_factor_attributions)
        assert abs(total_pct - 1.0) < 1e-6

    def test_information_ratio_decomposition(self):
        """信息比率分解: IR = active_return / active_risk."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4},
            stock_specific_risks={"600519": 0.02},
        )
        if result.active_risk > 0:
            expected_ir = result.active_return / result.active_risk
            assert abs(result.information_ratio - expected_ir) < 1e-6

    def test_factor_ir_decomposition(self):
        """因子 IR = factor_pnl / factor_risk."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.4},
            stock_specific_risks={"600519": 0.02},
        )
        if result.factor_risk > 0:
            expected_ir = result.factor_pnl / result.factor_risk
            assert abs(result.factor_ir - expected_ir) < 1e-6


# ============================================================
# 11. 综合场景测试
# ============================================================

class TestIntegrationScenarios:
    """综合场景测试."""

    def test_full_barra_10_factor_attribution(self):
        """完整 Barra 10 因子归因场景."""
        portfolio_exposures = {f: 0.1 * (i + 1) for i, f in enumerate(BARRA_STYLE_FACTORS)}
        benchmark_exposures = {f: 0.0 for f in BARRA_STYLE_FACTORS}
        factor_returns = {f: 0.001 * (i + 1) for i, f in enumerate(BARRA_STYLE_FACTORS)}

        result = attribute_factors(
            portfolio_exposures=portfolio_exposures,
            benchmark_exposures=benchmark_exposures,
            factor_returns=factor_returns,
            portfolio_value=10_000_000,
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 10
        assert len(result.style_factor_attributions) == 10
        assert len(result.sector_factor_attributions) == 0
        # 验证因子 PnL = Σ contribution
        manual_sum = sum(f.contribution_to_pnl for f in result.style_factor_attributions)
        assert abs(manual_sum - result.factor_pnl) < 1e-6

    def test_full_8_sector_attribution(self):
        """完整 8 行业因子归因场景."""
        portfolio_exposures = {s: 0.125 for s in SECTOR_FACTORS}  # 等权
        benchmark_exposures = {s: 0.125 for s in SECTOR_FACTORS}
        factor_returns = {s: 0.002 * (i + 1) for i, s in enumerate(SECTOR_FACTORS)}

        result = attribute_factors(
            portfolio_exposures=portfolio_exposures,
            benchmark_exposures=benchmark_exposures,
            factor_returns=factor_returns,
            portfolio_value=5_000_000,
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 8
        assert len(result.sector_factor_attributions) == 8
        assert len(result.style_factor_attributions) == 0
        # 由于组合和基准暴露相同, 主动暴露为 0, factor_pnl = 0
        assert result.factor_pnl == 0.0

    def test_mixed_style_and_sector_full_attribution(self):
        """混合 10 风格 + 8 行业的完整归因."""
        portfolio_exposures = {f: 0.1 for f in BARRA_STYLE_FACTORS}
        portfolio_exposures.update({s: 0.125 for s in SECTOR_FACTORS})
        benchmark_exposures = {f: 0.0 for f in BARRA_STYLE_FACTORS}
        benchmark_exposures.update({s: 0.125 for s in SECTOR_FACTORS})
        factor_returns = {f: 0.001 for f in BARRA_STYLE_FACTORS}
        factor_returns.update({s: 0.002 for s in SECTOR_FACTORS})

        result = attribute_factors(
            portfolio_exposures=portfolio_exposures,
            benchmark_exposures=benchmark_exposures,
            factor_returns=factor_returns,
            portfolio_value=10_000_000,
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 18
        assert len(result.style_factor_attributions) == 10
        assert len(result.sector_factor_attributions) == 8

    def test_attribution_with_risk_budget_exceeded(self):
        """风险预算超限场景."""
        # 大主动暴露 + 大个股风险
        result = attribute_factors(
            portfolio_exposures={"Size": 2.0, "Beta": 1.5},
            benchmark_exposures={"Size": 0.0, "Beta": 0.0},
            factor_returns={"Size": 0.01, "Beta": 0.02},
            portfolio_value=1_000_000,
            active_weights={"600519": 0.5, "000858": 0.5},
            stock_specific_risks={"600519": 0.05, "000858": 0.06},
            risk_budget=0.01,  # 1% 极小预算
        )
        # 风险预算利用率 > 100%
        assert result.risk_budget_utilization > 1.0
        assert result.risk_budget_remaining == 0.0

    def test_attribution_markdown_report_complete(self):
        """归因报告 Markdown 输出完整."""
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3, "finance": 0.4},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0, "finance": 0.3},
            factor_returns={"Size": 0.001, "Beta": 0.002, "finance": 0.003},
            portfolio_value=1_000_000,
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
        )
        md = result.to_markdown()
        assert "因子归因报告" in md
        assert "2026-07-27" in md
        assert "510300.SH" in md
        assert "收益分解" in md
        assert "风险分解" in md
        assert "风格因子明细" in md
        assert "行业因子明细" in md

    def test_attribution_to_dict_json_serializable(self):
        """归因结果可 JSON 序列化."""
        import json
        result = attribute_factors(
            portfolio_exposures={"Size": 0.5, "Beta": 0.3},
            benchmark_exposures={"Size": 0.0, "Beta": 1.0},
            factor_returns={"Size": 0.001, "Beta": 0.002},
            portfolio_value=1_000_000,
            attribution_date="2026-07-27",
        )
        d = result.to_dict()
        # 应该可以序列化为 JSON
        json_str = json.dumps(d, ensure_ascii=False)
        assert isinstance(json_str, str)
        # 反序列化后字段完整
        parsed = json.loads(json_str)
        assert parsed["attribution_date"] == "2026-07-27"
        assert parsed["n_factors"] == 2
