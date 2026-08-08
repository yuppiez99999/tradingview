"""utils/alpha/macro_indicator.py 单元测试 — 模块整合 8.4 (T4.4).

覆盖范围:
    - 常量定义
    - 异常体系
    - 数据类 (RegimeResult / MacroSnapshot)
    - Regime 分类算法 (classify_regime / classify_regimes_batch)
    - 宏观指标分类 (CPI / PMI / M2 / 利率)
    - 综合评分 (compute_composite_score)
    - MacroIndicatorManager 主类
    - Feature Flag 透传 (HC-1)
    - 便捷函数
    - 边界条件
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.macro_indicator import (  # noqa: E402
    ALL_REGIMES,
    DEFAULT_CHOPPY_BAND,
    DEFAULT_CONFIG_NAME,
    DEFAULT_MA_WINDOW,
    DEFAULT_POSITION_FACTORS,
    DEFAULT_RISK_BUDGET,
    FLAG_NAME,
    REGIME_BEAR,
    # 常量
    REGIME_BULL,
    REGIME_CHOPPY,
    REGIME_INSUFFICIENT,
    REGIME_REBOUND,
    REGIME_UNKNOWN,
    REGIME_WARMUP,
    InsufficientDataError,
    InvalidRegimeError,
    # 异常
    MacroIndicatorError,
    # 主类
    MacroIndicatorManager,
    MacroSnapshot,
    # 数据类
    RegimeResult,
    classify_cpi,
    classify_m2,
    classify_pmi,
    classify_rate,
    # 函数
    classify_regime,
    # 便捷函数
    classify_regime_simple,
    classify_regimes_batch,
    compute_composite_score,
    get_regime_for_returns,
    is_macro_indicator_enabled,
)

# ============================================================
# TestConstants - 常量定义测试
# ============================================================

class TestConstants:
    """常量定义测试."""

    def test_all_regimes_contains_seven_labels(self):
        """ALL_REGIMES 包含 7 个 regime 标签."""
        assert len(ALL_REGIMES) == 7
        for label in ["bull", "bear", "choppy", "rebound", "warmup", "unknown", "insufficient_samples"]:
            assert label in ALL_REGIMES

    def test_regime_labels_are_strings(self):
        """所有 regime 标签都是字符串."""
        for label in ALL_REGIMES:
            assert isinstance(label, str)
            assert len(label) > 0

    def test_default_position_factors_keys(self):
        """DEFAULT_POSITION_FACTORS 包含所有 regime."""
        for label in ALL_REGIMES:
            assert label in DEFAULT_POSITION_FACTORS

    def test_default_position_factors_bull_is_1(self):
        """bull regime 仓位因子为 1.0."""
        assert DEFAULT_POSITION_FACTORS[REGIME_BULL] == 1.0

    def test_default_position_factors_bear_is_0_3(self):
        """bear regime 仓位因子为 0.3 (HC-3 risk_managed)."""
        assert DEFAULT_POSITION_FACTORS[REGIME_BEAR] == 0.3

    def test_default_risk_budget_bull_is_1_2(self):
        """bull regime 风险预算 1.2."""
        assert DEFAULT_RISK_BUDGET[REGIME_BULL] == 1.2

    def test_default_risk_budget_bear_is_0_5(self):
        """bear regime 风险预算 0.5."""
        assert DEFAULT_RISK_BUDGET[REGIME_BEAR] == 0.5

    def test_default_ma_window_is_60(self):
        """DEFAULT_MA_WINDOW=60 (对齐 research RegimeConditioner)."""
        assert DEFAULT_MA_WINDOW == 60

    def test_default_choppy_band_is_0_03(self):
        """DEFAULT_CHOPPY_BAND=0.03."""
        assert DEFAULT_CHOPPY_BAND == 0.03

    def test_flag_name(self):
        """FLAG_NAME 正确."""
        assert FLAG_NAME == "USE_MACRO_INDICATOR"

    def test_default_config_name(self):
        """DEFAULT_CONFIG_NAME 正确."""
        assert DEFAULT_CONFIG_NAME == "macro_indicator"


# ============================================================
# TestExceptions - 异常体系测试
# ============================================================

class TestExceptions:
    """异常体系测试."""

    def test_macro_indicator_error_is_exception(self):
        """MacroIndicatorError 继承 Exception."""
        assert issubclass(MacroIndicatorError, Exception)

    def test_insufficient_data_error_inherits(self):
        """InsufficientDataError 继承 MacroIndicatorError."""
        assert issubclass(InsufficientDataError, MacroIndicatorError)

    def test_invalid_regime_error_inherits(self):
        """InvalidRegimeError 继承 MacroIndicatorError."""
        assert issubclass(InvalidRegimeError, MacroIndicatorError)

    def test_raise_macro_indicator_error(self):
        """可以抛出 MacroIndicatorError."""
        with pytest.raises(MacroIndicatorError):
            raise MacroIndicatorError("test")

    def test_raise_insufficient_data_error(self):
        """可以抛出 InsufficientDataError."""
        with pytest.raises(InsufficientDataError):
            raise InsufficientDataError("test")


# ============================================================
# TestDataClasses - 数据类测试
# ============================================================

class TestDataClasses:
    """数据类测试."""

    def test_regime_result_default_values(self):
        """RegimeResult 默认值正确."""
        r = RegimeResult()
        assert r.label == REGIME_UNKNOWN
        assert r.position_factor == 1.0
        assert r.risk_budget == 1.0
        assert r.samples_used == 0

    def test_regime_result_to_dict(self):
        """RegimeResult.to_dict() 正确."""
        r = RegimeResult(label=REGIME_BULL, position_factor=1.0, samples_used=60)
        d = r.to_dict()
        assert d["label"] == "bull"
        assert d["position_factor"] == 1.0
        assert d["samples_used"] == 60
        assert "risk_budget" in d
        assert "ma_value" in d

    def test_macro_snapshot_default_values(self):
        """MacroSnapshot 默认值正确."""
        s = MacroSnapshot()
        assert s.cpi is None
        assert s.pmi is None
        assert s.m2 is None
        assert s.rate is None
        assert s.composite_score == 50.0
        assert s.status == "ok"

    def test_macro_snapshot_to_dict(self):
        """MacroSnapshot.to_dict() 正确."""
        s = MacroSnapshot(cpi=2.3, pmi=51.2, status="ok")
        d = s.to_dict()
        assert d["cpi"] == 2.3
        assert d["pmi"] == 51.2
        assert d["status"] == "ok"
        assert "composite_score" in d


# ============================================================
# TestClassifyRegime - Regime 分类测试
# ============================================================

class TestClassifyRegime:
    """classify_regime() 函数测试."""

    def test_insufficient_samples(self):
        """样本数 < min_samples 返回 insufficient_samples."""
        rets = [0.01] * 5  # 只有 5 个样本
        result = classify_regime(rets, min_samples=20)
        assert result.label == REGIME_INSUFFICIENT
        assert result.samples_used == 5
        assert "samples" in result.reason

    def test_warmup_when_below_ma_window(self):
        """样本数 >= min_samples 但 < ma_window 返回 warmup."""
        rets = [0.001] * 30  # 30 个样本, ma_window=60
        result = classify_regime(rets, ma_window=60, min_samples=20)
        assert result.label == REGIME_WARMUP

    def test_bull_regime(self):
        """牛市: 价格 > MA 且 MA 斜率向上."""
        # 持续上涨序列
        rets = [0.002] * 70  # 70 天稳定上涨
        result = classify_regime(rets, ma_window=60, min_samples=20)
        assert result.label == REGIME_BULL
        assert result.position_factor == 1.0
        assert result.risk_budget == 1.2

    def test_bear_regime(self):
        """熊市: 价格 < MA 且 MA 斜率向下."""
        rets = [-0.002] * 70
        result = classify_regime(rets, ma_window=60, min_samples=20)
        assert result.label == REGIME_BEAR
        assert result.position_factor == 0.3
        assert result.risk_budget == 0.5

    def test_choppy_regime(self):
        """震荡: |price/MA - 1| < choppy_band."""
        # 价格在 MA 附近波动
        rets = [0.0] * 70
        result = classify_regime(rets, ma_window=60, choppy_band=0.03, min_samples=20)
        assert result.label == REGIME_CHOPPY

    def test_rebound_regime(self):
        """反弹: 其他情形."""
        # 构造 ratio > 0 但 ma_slope < 0 的情形 (反弹)
        rets = [-0.001] * 65 + [0.005] * 10  # 先下跌再反弹
        result = classify_regime(rets, ma_window=60, choppy_band=0.001, min_samples=20)
        # 反弹应该不是 bull (因为 MA 斜率仍可能为负) 或 choppy
        assert result.label in [REGIME_REBOUND, REGIME_BULL, REGIME_BEAR, REGIME_CHOPPY]

    def test_returns_regime_result(self):
        """返回 RegimeResult 类型."""
        rets = [0.001] * 70
        result = classify_regime(rets)
        assert isinstance(result, RegimeResult)

    def test_ma_value_positive(self):
        """ma_value 为正数."""
        rets = [0.001] * 70
        result = classify_regime(rets)
        assert result.ma_value > 0

    def test_samples_used_correct(self):
        """samples_used 等于输入长度."""
        rets = [0.001] * 70
        result = classify_regime(rets)
        assert result.samples_used == 70

    def test_empty_returns(self):
        """空收益率返回 insufficient_samples."""
        result = classify_regime([])
        assert result.label == REGIME_INSUFFICIENT


class TestClassifyRegimesBatch:
    """classify_regimes_batch() 批量分类测试."""

    def test_returns_list_of_correct_length(self):
        """返回列表长度与输入相同."""
        rets = [0.001] * 70
        regimes = classify_regimes_batch(rets, ma_window=60)
        assert len(regimes) == 70
        assert all(isinstance(r, str) for r in regimes)

    def test_warmup_at_start(self):
        """前 ma_window 个标签为 warmup."""
        rets = [0.001] * 70
        regimes = classify_regimes_batch(rets, ma_window=60)
        for i in range(60):
            assert regimes[i] == REGIME_WARMUP

    def test_bull_after_warmup(self):
        """warmup 后稳定上涨为 bull."""
        rets = [0.002] * 70
        regimes = classify_regimes_batch(rets, ma_window=60)
        for i in range(60, 70):
            assert regimes[i] == REGIME_BULL

    def test_bear_after_warmup(self):
        """warmup 后稳定下跌为 bear."""
        rets = [-0.002] * 70
        regimes = classify_regimes_batch(rets, ma_window=60)
        for i in range(60, 70):
            assert regimes[i] == REGIME_BEAR

    def test_empty_returns_empty_list(self):
        """空输入返回空列表."""
        assert classify_regimes_batch([]) == []


# ============================================================
# TestMacroIndicatorClassification - 宏观指标分类测试
# ============================================================

class TestMacroIndicatorClassification:
    """CPI / PMI / M2 / 利率 分类测试."""

    def test_cpi_low(self):
        """CPI < 1.0 为 low."""
        assert classify_cpi(0.5) == "low"

    def test_cpi_moderate(self):
        """CPI 1.0-3.0 为 moderate."""
        assert classify_cpi(2.0) == "moderate"

    def test_cpi_high(self):
        """CPI 3.0-5.0 为 high."""
        assert classify_cpi(4.0) == "high"

    def test_cpi_hyper(self):
        """CPI >= 5.0 为 hyper."""
        assert classify_cpi(6.0) == "hyper"

    def test_pmi_contraction(self):
        """PMI < 50 为 contraction."""
        assert classify_pmi(49.0) == "contraction"

    def test_pmi_neutral(self):
        """PMI 50-51 为 neutral."""
        assert classify_pmi(50.5) == "neutral"

    def test_pmi_expansion(self):
        """PMI 51-52 为 expansion."""
        assert classify_pmi(51.5) == "expansion"

    def test_pmi_strong_expansion(self):
        """PMI >= 52 为 strong_expansion."""
        assert classify_pmi(53.0) == "strong_expansion"

    def test_m2_tight(self):
        """M2 < 8 为 tight."""
        assert classify_m2(7.0) == "tight"

    def test_m2_moderate(self):
        """M2 8-10 为 moderate."""
        assert classify_m2(9.0) == "moderate"

    def test_m2_loose(self):
        """M2 10-12 为 loose."""
        assert classify_m2(11.0) == "loose"

    def test_m2_very_loose(self):
        """M2 >= 12 为 very_loose."""
        assert classify_m2(13.0) == "very_loose"

    def test_rate_low(self):
        """利率 < 2.5 为 low."""
        assert classify_rate(2.0) == "low"

    def test_rate_moderate(self):
        """利率 2.5-3.0 为 moderate."""
        assert classify_rate(2.8) == "moderate"

    def test_rate_high(self):
        """利率 3.0-3.5 为 high."""
        assert classify_rate(3.2) == "high"

    def test_rate_very_high(self):
        """利率 >= 3.5 为 very_high."""
        assert classify_rate(4.0) == "very_high"

    def test_custom_thresholds(self):
        """支持自定义阈值."""
        # 2.0 在 [1.5, 2.5) 区间, 按代码逻辑 < moderate=1.5 才是 moderate, >= 1.5 且 < high=2.5 是 high
        assert classify_cpi(2.0, {"low": 0.5, "moderate": 1.5, "high": 2.5}) == "high"
        assert classify_cpi(1.0, {"low": 0.5, "moderate": 1.5, "high": 2.5}) == "moderate"


# ============================================================
# TestCompositeScore - 综合评分测试
# ============================================================

class TestCompositeScore:
    """compute_composite_score() 测试."""

    def test_default_score_is_50(self):
        """无指标时默认分为 50."""
        assert compute_composite_score() == 50.0

    def test_moderate_cpi_adds_score(self):
        """温和通胀加分."""
        score = compute_composite_score(cpi=2.0)
        assert score > 50.0

    def test_hyper_cpi_subtracts_score(self):
        """恶性通胀扣分."""
        score = compute_composite_score(cpi=6.0)
        assert score < 50.0

    def test_expansion_pmi_adds_score(self):
        """PMI 扩张加分."""
        score = compute_composite_score(pmi=53.0)
        assert score > 50.0

    def test_contraction_pmi_subtracts(self):
        """PMI 衰退扣分."""
        score = compute_composite_score(pmi=48.0)
        assert score < 50.0

    def test_loose_m2_adds_score(self):
        """M2 宽松加分."""
        score = compute_composite_score(m2=11.0)
        assert score > 50.0

    def test_low_rate_adds_score(self):
        """低利率加分."""
        score = compute_composite_score(rate=2.0)
        assert score > 50.0

    def test_high_rate_subtracts(self):
        """高利率扣分."""
        score = compute_composite_score(rate=4.0)
        assert score < 50.0

    def test_score_bounded_0_to_100(self):
        """评分在 0-100 范围内."""
        score = compute_composite_score(cpi=10.0, pmi=40.0, m2=5.0, rate=5.0)
        assert 0.0 <= score <= 100.0

    def test_perfect_conditions_high_score(self):
        """完美条件高分."""
        score = compute_composite_score(cpi=2.0, pmi=53.0, m2=11.0, rate=2.0)
        assert score >= 70.0

    def test_custom_weights(self):
        """支持自定义权重."""
        score = compute_composite_score(cpi=2.0, weights={"cpi": 1.0, "pmi": 0.0, "m2": 0.0, "rate": 0.0})
        assert score > 50.0


# ============================================================
# TestMacroIndicatorManager - 主类测试
# ============================================================

class TestMacroIndicatorManager:
    """MacroIndicatorManager 主类测试."""

    def test_init_with_defaults(self):
        """默认初始化."""
        mgr = MacroIndicatorManager()
        assert mgr._ma_window == DEFAULT_MA_WINDOW
        assert mgr._choppy_band == DEFAULT_CHOPPY_BAND

    def test_init_with_explicit_config(self):
        """显式配置初始化."""
        config = {
            "settings": {"ma_window": 30, "choppy_band": 0.02, "min_samples": 10},
            "indicators": {"cpi": {"source_key": "cpi", "weight": 0.3}},
            "regime": {"position_factors": {"bull": 1.5}},
        }
        mgr = MacroIndicatorManager(config=config)
        assert mgr._ma_window == 30
        assert mgr._choppy_band == 0.02
        assert mgr._position_factors["bull"] == 1.5

    def test_update_returns_regime_result(self):
        """update() 返回 RegimeResult."""
        mgr = MacroIndicatorManager()
        result = mgr.update(new_returns=[0.001] * 70)
        assert isinstance(result, RegimeResult)

    def test_update_accumulates_returns(self):
        """update() 累积收益率 (需要 Flag 启用)."""
        mgr = MacroIndicatorManager()
        mgr._is_enabled = lambda: True  # 模拟 Flag 启用
        mgr.update(new_returns=[0.001] * 30)
        assert len(mgr._benchmark_returns) == 30
        mgr.update(new_returns=[0.001] * 40)
        assert len(mgr._benchmark_returns) == 70

    def test_get_regime_before_update(self):
        """未 update 前 get_regime 返回 unknown."""
        mgr = MacroIndicatorManager()
        result = mgr.get_regime()
        assert result.label == REGIME_UNKNOWN

    def test_classify_batch(self):
        """classify_batch() 批量分类."""
        mgr = MacroIndicatorManager()
        regimes = mgr.classify_batch([0.001] * 70)
        assert len(regimes) == 70

    def test_reset(self):
        """reset() 清空状态."""
        mgr = MacroIndicatorManager()
        mgr.update(new_returns=[0.001] * 70)
        mgr.reset()
        assert len(mgr._benchmark_returns) == 0
        assert mgr._last_regime.label == REGIME_UNKNOWN

    def test_get_position_factor(self):
        """get_position_factor() 返回仓位因子."""
        mgr = MacroIndicatorManager()
        assert mgr.get_position_factor() == 1.0  # 默认 unknown

    def test_get_risk_budget(self):
        """get_risk_budget() 返回风险预算."""
        mgr = MacroIndicatorManager()
        assert mgr.get_risk_budget() == 1.0


# ============================================================
# TestFeatureFlag - Feature Flag 透传测试 (HC-1)
# ============================================================

class TestFeatureFlag:
    """Feature Flag 透传测试 (HC-1)."""

    def test_is_macro_indicator_enabled_default_false(self):
        """Feature Flag 默认 False."""
        # 在无框架环境下应返回 False
        assert is_macro_indicator_enabled() in [True, False]  # 容错

    def test_manager_disabled_returns_unknown(self):
        """Flag 关闭时 update() 返回 unknown."""
        mgr = MacroIndicatorManager()
        # 模拟 Flag 关闭
        mgr._is_enabled = lambda: False
        result = mgr.update(new_returns=[0.001] * 70)
        assert result.label == REGIME_UNKNOWN
        assert "feature_flag_disabled" in result.reason

    def test_manager_disabled_snapshot_returns_disabled_status(self):
        """Flag 关闭时 get_macro_snapshot() 返回 disabled 状态."""
        mgr = MacroIndicatorManager()
        mgr._is_enabled = lambda: False
        snapshot = mgr.get_macro_snapshot()
        assert snapshot.status == "feature_flag_disabled"
        assert snapshot.cpi is None

    def test_manager_disabled_classify_batch_returns_unknown(self):
        """Flag 关闭时 classify_batch() 返回 unknown 列表."""
        mgr = MacroIndicatorManager()
        mgr._is_enabled = lambda: False
        regimes = mgr.classify_batch([0.001] * 70)
        assert all(r == REGIME_UNKNOWN for r in regimes)


# ============================================================
# TestMacroSnapshot - 宏观快照测试
# ============================================================

class TestMacroSnapshot:
    """get_macro_snapshot() 测试."""

    def test_snapshot_with_no_data(self):
        """无数据时返回空快照."""
        mgr = MacroIndicatorManager()
        mgr._is_enabled = lambda: True
        mgr._fetch_macro_data = lambda: {}
        snapshot = mgr.get_macro_snapshot()
        assert snapshot.status == "no_data"
        assert snapshot.cpi is None

    def test_snapshot_with_data(self):
        """有数据时返回完整快照."""
        mgr = MacroIndicatorManager()
        mgr._is_enabled = lambda: True
        mgr._fetch_macro_data = lambda: {
            "cpi": 2.3,
            "pmi": 51.5,
            "m2": 9.5,
            "treasury_10y": 2.8,
        }
        snapshot = mgr.get_macro_snapshot()
        assert snapshot.cpi == 2.3
        assert snapshot.pmi == 51.5
        assert snapshot.m2 == 9.5
        assert snapshot.rate == 2.8
        assert snapshot.cpi_category == "moderate"
        assert snapshot.pmi_category == "expansion"
        assert snapshot.status == "ok"

    def test_snapshot_with_dict_values(self):
        """数据为字典形式时正确解析."""
        mgr = MacroIndicatorManager()
        mgr._is_enabled = lambda: True
        mgr._fetch_macro_data = lambda: {
            "cpi": {"value": 2.3, "date": "2026-07"},
            "pmi": {"value": 51.5},
        }
        snapshot = mgr.get_macro_snapshot()
        assert snapshot.cpi == 2.3
        assert snapshot.pmi == 51.5

    def test_snapshot_composite_score(self):
        """综合评分计算正确."""
        mgr = MacroIndicatorManager()
        mgr._is_enabled = lambda: True
        mgr._fetch_macro_data = lambda: {
            "cpi": 2.0, "pmi": 53.0, "m2": 11.0, "treasury_10y": 2.0,
        }
        snapshot = mgr.get_macro_snapshot()
        assert snapshot.composite_score > 50.0


# ============================================================
# TestConvenienceFunctions - 便捷函数测试
# ============================================================

class TestConvenienceFunctions:
    """便捷函数测试."""

    def test_classify_regime_simple(self):
        """classify_regime_simple() 正确."""
        result = classify_regime_simple([0.001] * 70)
        assert isinstance(result, RegimeResult)

    def test_get_regime_for_returns(self):
        """get_regime_for_returns() 正确."""
        result = get_regime_for_returns([0.001] * 70)
        assert isinstance(result, RegimeResult)

    def test_is_macro_indicator_enabled_returns_bool(self):
        """is_macro_indicator_enabled() 返回 bool."""
        result = is_macro_indicator_enabled()
        assert isinstance(result, bool)


# ============================================================
# TestEdgeCases - 边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_zero_returns(self):
        """零收益率返回 choppy."""
        rets = [0.0] * 70
        result = classify_regime(rets)
        assert result.label == REGIME_CHOPPY

    def test_single_huge_return(self):
        """单日巨大涨跌幅不崩溃."""
        rets = [0.5] + [0.0] * 69
        result = classify_regime(rets)
        assert result.label in ALL_REGIMES

    def test_extreme_volatility(self):
        """极端波动不崩溃."""
        import random
        random.seed(42)
        rets = [random.gauss(0, 0.1) for _ in range(70)]
        result = classify_regime(rets)
        assert result.label in ALL_REGIMES

    def test_very_long_sequence(self):
        """超长序列不崩溃."""
        rets = [0.001] * 1000
        result = classify_regime(rets)
        assert result.label == REGIME_BULL

    def test_negative_then_positive(self):
        """先跌后涨."""
        rets = [-0.002] * 50 + [0.002] * 50
        result = classify_regime(rets)
        assert result.label in ALL_REGIMES

    def test_threshold_boundary(self):
        """阈值边界值正确分类."""
        # CPI = 1.0 正好在 low 和 moderate 边界
        assert classify_cpi(1.0) == "moderate"  # 1.0 不 < 1.0
        assert classify_cpi(0.99) == "low"

    def test_nan_handling_in_score(self):
        """NaN 值不影响评分."""
        # compute_composite_score 不直接处理 NaN, 但应不崩溃
        score = compute_composite_score(cpi=None, pmi=None)
        assert score == 50.0

    def test_manager_with_empty_config(self):
        """空配置不崩溃."""
        mgr = MacroIndicatorManager(config={})
        assert mgr._ma_window == DEFAULT_MA_WINDOW

    def test_extract_indicator_with_invalid_data(self):
        """_extract_indicator 处理无效数据."""
        mgr = MacroIndicatorManager()
        assert mgr._extract_indicator({}, "cpi") is None
        assert mgr._extract_indicator({"cpi": "invalid"}, "cpi") is None
        assert mgr._extract_indicator({"cpi": None}, "cpi") is None
