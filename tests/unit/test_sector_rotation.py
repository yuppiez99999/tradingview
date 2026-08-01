# -*- coding: utf-8 -*-
"""utils/alpha/sector_rotation.py 单元测试 — 模块整合 8.4 (T4.4).

覆盖范围:
    - 常量定义
    - 异常体系
    - 数据类 (SectorSignal / RotationResult)
    - 评分函数 (momentum / flow / valuation / label)
    - SectorRotation 主类
    - Feature Flag 透传 (HC-1)
    - Regime 调整 (HC-3 risk_managed)
    - 便捷函数
    - 边界条件
"""
from __future__ import annotations

import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.sector_rotation import (
    DEFAULT_CONFIG_NAME,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_REGIME_ADJUSTMENTS,
    # 常量
    DEFAULT_SIGNAL_WEIGHTS,
    DEFAULT_THRESHOLDS,
    DEFAULT_TOP_N,
    FLAG_NAME,
    InsufficientSectorsError,
    InvalidSignalError,
    RotationResult,
    # 主类
    SectorRotation,
    # 异常
    SectorRotationError,
    # 数据类
    SectorSignal,
    classify_signal_label,
    compute_flow_score,
    # 评分函数
    compute_momentum_score,
    compute_valuation_score,
    # 便捷函数
    is_sector_rotation_enabled,
)

# ============================================================
# TestConstants - 常量定义测试
# ============================================================

class TestConstants:
    """常量定义测试."""

    def test_default_signal_weights(self):
        """DEFAULT_SIGNAL_WEIGHTS 包含三个维度."""
        assert "momentum" in DEFAULT_SIGNAL_WEIGHTS
        assert "flow" in DEFAULT_SIGNAL_WEIGHTS
        assert "valuation" in DEFAULT_SIGNAL_WEIGHTS

    def test_signal_weights_sum_to_one(self):
        """默认信号权重总和为 1.0."""
        total = sum(DEFAULT_SIGNAL_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6

    def test_default_thresholds(self):
        """DEFAULT_THRESHOLDS 包含所有阈值."""
        for key in ["momentum_strong", "momentum_weak", "flow_in_strong", "flow_out_strong", "valuation_cheap", "valuation_expensive"]:
            assert key in DEFAULT_THRESHOLDS

    def test_default_regime_adjustments(self):
        """DEFAULT_REGIME_ADJUSTMENTS 包含所有 regime."""
        for regime in ["bull", "bear", "choppy", "rebound", "default"]:
            assert regime in DEFAULT_REGIME_ADJUSTMENTS

    def test_default_top_n_is_3(self):
        """DEFAULT_TOP_N=3."""
        assert DEFAULT_TOP_N == 3

    def test_default_lookback_days_is_20(self):
        """DEFAULT_LOOKBACK_DAYS=20."""
        assert DEFAULT_LOOKBACK_DAYS == 20

    def test_flag_name(self):
        """FLAG_NAME 正确."""
        assert FLAG_NAME == "USE_SECTOR_ROTATION"

    def test_default_config_name(self):
        """DEFAULT_CONFIG_NAME 正确."""
        assert DEFAULT_CONFIG_NAME == "sector_rotation"


# ============================================================
# TestExceptions - 异常体系测试
# ============================================================

class TestExceptions:
    """异常体系测试."""

    def test_sector_rotation_error_is_exception(self):
        """SectorRotationError 继承 Exception."""
        assert issubclass(SectorRotationError, Exception)

    def test_insufficient_sectors_error_inherits(self):
        """InsufficientSectorsError 继承 SectorRotationError."""
        assert issubclass(InsufficientSectorsError, SectorRotationError)

    def test_invalid_signal_error_inherits(self):
        """InvalidSignalError 继承 SectorRotationError."""
        assert issubclass(InvalidSignalError, SectorRotationError)


# ============================================================
# TestDataClasses - 数据类测试
# ============================================================

class TestDataClasses:
    """数据类测试."""

    def test_sector_signal_default_values(self):
        """SectorSignal 默认值正确."""
        s = SectorSignal()
        assert s.code == ""
        assert s.composite_score == 50.0
        assert s.label == "NEUTRAL"
        assert s.rank == 0

    def test_sector_signal_to_dict(self):
        """SectorSignal.to_dict() 正确."""
        s = SectorSignal(code="801010", name="农林牧渔", composite_score=75.0, label="BUY")
        d = s.to_dict()
        assert d["code"] == "801010"
        assert d["name"] == "农林牧渔"
        assert d["composite_score"] == 75.0
        assert d["label"] == "BUY"

    def test_rotation_result_default_values(self):
        """RotationResult 默认值正确."""
        r = RotationResult()
        assert r.signals == []
        assert r.top_sectors == []
        assert r.bottom_sectors == []
        assert r.regime == "unknown"
        assert r.status == "ok"

    def test_rotation_result_to_dict(self):
        """RotationResult.to_dict() 正确."""
        s = SectorSignal(code="801010", composite_score=75.0)
        r = RotationResult(signals=[s], top_sectors=["801010"], regime="bull", n_sectors=1)
        d = r.to_dict()
        assert d["top_sectors"] == ["801010"]
        assert d["regime"] == "bull"
        assert d["n_sectors"] == 1
        assert len(d["signals"]) == 1
        assert d["signals"][0]["code"] == "801010"


# ============================================================
# TestScoringFunctions - 评分函数测试
# ============================================================

class TestMomentumScore:
    """compute_momentum_score() 测试."""

    def test_empty_returns_50(self):
        """空收益率返回 50."""
        assert compute_momentum_score([]) == 50.0

    def test_strong_positive_returns_100(self):
        """强势上涨返回 100."""
        rets = [0.02] * 20  # 累计 ~48% > 3%
        score = compute_momentum_score(rets)
        assert score == 100.0

    def test_strong_negative_returns_0(self):
        """强势下跌返回 0."""
        rets = [-0.02] * 20  # 累计 ~-33% < -3%
        score = compute_momentum_score(rets)
        assert score == 0.0

    def test_zero_returns_50(self):
        """零收益率返回 50."""
        rets = [0.0] * 20
        score = compute_momentum_score(rets)
        assert score == 50.0

    def test_moderate_positive(self):
        """温和上涨返回 50-100."""
        rets = [0.001] * 10  # 累计 ~1%
        score = compute_momentum_score(rets)
        assert 50.0 < score < 100.0

    def test_moderate_negative(self):
        """温和下跌返回 0-50."""
        rets = [-0.001] * 10
        score = compute_momentum_score(rets)
        assert 0.0 < score < 50.0

    def test_lookback_truncation(self):
        """lookback_days 截断长序列."""
        rets = [0.0] * 30 + [0.02] * 10  # 最后 10 天强势
        score = compute_momentum_score(rets, lookback_days=10)
        assert score == 100.0


class TestFlowScore:
    """compute_flow_score() 测试."""

    def test_strong_inflow_100(self):
        """强流入 100."""
        assert compute_flow_score(2.0) == 100.0

    def test_strong_outflow_0(self):
        """强流出 0."""
        assert compute_flow_score(-2.0) == 0.0

    def test_zero_flow_50(self):
        """零流入 50."""
        assert compute_flow_score(0.0) == 50.0

    def test_moderate_inflow(self):
        """温和流入 50-100."""
        score = compute_flow_score(0.5)
        assert 50.0 < score < 100.0

    def test_custom_thresholds(self):
        """自定义阈值."""
        score = compute_flow_score(0.5, {"flow_in_strong": 0.5, "flow_out_strong": -0.5})
        assert score == 100.0


class TestValuationScore:
    """compute_valuation_score() 测试."""

    def test_cheap_returns_100(self):
        """低估值返回 100."""
        assert compute_valuation_score(0.1) == 100.0

    def test_expensive_returns_0(self):
        """高估值返回 0."""
        assert compute_valuation_score(0.9) == 0.0

    def test_mid_returns_50(self):
        """中位估值返回 50 左右."""
        score = compute_valuation_score(0.5)
        assert 40.0 < score < 60.0

    def test_clamp_to_0_1(self):
        """分位数 clamp 到 0-1."""
        assert compute_valuation_score(-1.0) == 100.0
        assert compute_valuation_score(2.0) == 0.0


class TestClassifySignalLabel:
    """classify_signal_label() 测试."""

    def test_strong_buy(self):
        """>=80 为 STRONG_BUY."""
        assert classify_signal_label(85) == "STRONG_BUY"

    def test_buy(self):
        """60-80 为 BUY."""
        assert classify_signal_label(70) == "BUY"

    def test_neutral(self):
        """40-60 为 NEUTRAL."""
        assert classify_signal_label(50) == "NEUTRAL"

    def test_sell(self):
        """20-40 为 SELL."""
        assert classify_signal_label(30) == "SELL"

    def test_strong_sell(self):
        """<20 为 STRONG_SELL."""
        assert classify_signal_label(10) == "STRONG_SELL"

    def test_boundary_80(self):
        """80 边界为 STRONG_BUY."""
        assert classify_signal_label(80) == "STRONG_BUY"

    def test_boundary_60(self):
        """60 边界为 BUY."""
        assert classify_signal_label(60) == "BUY"


# ============================================================
# TestSectorRotation - 主类测试
# ============================================================

class TestSectorRotation:
    """SectorRotation 主类测试."""

    def test_init_with_defaults(self):
        """默认初始化."""
        sr = SectorRotation()
        assert sr._lookback_days == DEFAULT_LOOKBACK_DAYS
        assert sr._top_n == DEFAULT_TOP_N

    def test_init_with_explicit_config(self):
        """显式配置初始化."""
        config = {
            "settings": {"lookback_days": 10, "top_n": 5, "bottom_n": 2},
            "sectors": [{"code": "801010", "name": "农林牧渔"}],
            "signal_weights": {"momentum": 0.6, "flow": 0.2, "valuation": 0.2},
        }
        sr = SectorRotation(config=config)
        assert sr._lookback_days == 10
        assert sr._top_n == 5
        assert sr._bottom_n == 2
        assert sr._sector_names["801010"] == "农林牧渔"

    def test_generate_signals_returns_rotation_result(self):
        """generate_signals() 返回 RotationResult."""
        sr = SectorRotation()
        # 模拟 Flag 启用
        sr._is_enabled = lambda: True
        returns = {f"8010{i}0": [0.001] * 20 for i in range(15)}
        result = sr.generate_signals(sector_returns=returns)
        assert isinstance(result, RotationResult)

    def test_generate_signals_with_no_data(self):
        """无数据返回 no_data 状态."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        result = sr.generate_signals()
        assert result.status == "no_data"

    def test_generate_signals_insufficient_sectors(self):
        """行业数不足返回 insufficient_data."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        # 仅 3 个行业, min_samples=10
        returns = {"801010": [0.001] * 20, "801030": [0.001] * 20, "801050": [0.001] * 20}
        result = sr.generate_signals(sector_returns=returns)
        assert result.status == "insufficient_data"

    def test_generate_signals_with_sufficient_sectors(self):
        """充足行业返回 ok 状态."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        # 15 个行业, 满足 min_samples=10
        returns = {f"8010{i}0": [0.001] * 20 for i in range(15)}
        result = sr.generate_signals(sector_returns=returns)
        assert result.status == "ok"
        assert result.n_sectors == 15
        assert len(result.top_sectors) == DEFAULT_TOP_N

    def test_top_sectors_are_best_performers(self):
        """Top sectors 是表现最好的行业."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        # 801050 涨得最多, 801030 跌得最多
        returns = {
            "801010": [0.001] * 20,
            "801030": [-0.005] * 20,  # 最差
            "801050": [0.005] * 20,   # 最好
        }
        # 补足 7 个行业满足 min_samples=10 (默认)
        for i in range(4, 11):
            returns[f"S{i:04d}"] = [0.0] * 20
        # 覆盖 min_samples 为 3 以减少用例数据
        sr._min_samples = 3
        result = sr.generate_signals(sector_returns=returns)
        assert "801050" in result.top_sectors
        assert "801030" in result.bottom_sectors

    def test_signals_ranked_by_composite_score(self):
        """信号按综合评分降序排名."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {
            "A": [0.005] * 20,  # 高分
            "B": [0.001] * 20,  # 中分
            "C": [-0.005] * 20,  # 低分
            "D": [0.0] * 20,
            "E": [0.0] * 20,
        }
        result = sr.generate_signals(sector_returns=returns)
        assert result.signals[0].rank == 1
        assert result.signals[0].composite_score >= result.signals[-1].composite_score

    def test_regime_adjustment_bull(self):
        """bull regime 调整权重."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {f"S{i}": [0.001] * 20 for i in range(5)}
        result = sr.generate_signals(sector_returns=returns, regime="bull")
        assert result.regime == "bull"
        assert result.regime_adjusted is True

    def test_regime_adjustment_unknown_not_adjusted(self):
        """unknown regime 不调整."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {f"S{i}": [0.001] * 20 for i in range(5)}
        result = sr.generate_signals(sector_returns=returns, regime="unknown")
        assert result.regime_adjusted is False

    def test_get_top_sectors(self):
        """get_top_sectors() 便捷接口."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {f"S{i}": [0.001] * 20 for i in range(5)}
        top = sr.get_top_sectors(sector_returns=returns)
        assert isinstance(top, list)
        assert len(top) <= DEFAULT_TOP_N

    def test_get_bottom_sectors(self):
        """get_bottom_sectors() 便捷接口."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {f"S{i}": [0.001] * 20 for i in range(5)}
        bottom = sr.get_bottom_sectors(sector_returns=returns)
        assert isinstance(bottom, list)


# ============================================================
# TestFeatureFlag - Feature Flag 透传测试 (HC-1)
# ============================================================

class TestFeatureFlag:
    """Feature Flag 透传测试 (HC-1)."""

    def test_is_sector_rotation_enabled_returns_bool(self):
        """is_sector_rotation_enabled() 返回 bool."""
        result = is_sector_rotation_enabled()
        assert isinstance(result, bool)

    def test_disabled_returns_equal_weight(self):
        """Flag 关闭时返回等权模式 (HC-1)."""
        sr = SectorRotation()
        sr._is_enabled = lambda: False
        returns = {f"S{i}": [0.001] * 20 for i in range(15)}
        result = sr.generate_signals(sector_returns=returns)
        assert result.status == "feature_flag_disabled"
        # 所有信号评分应为 50 (等权)
        for s in result.signals:
            assert s.composite_score == 50.0
            assert s.label == "NEUTRAL"

    def test_disabled_top_sectors_still_returned(self):
        """Flag 关闭时仍返回 Top N (按字母序)."""
        sr = SectorRotation()
        sr._is_enabled = lambda: False
        returns = {"C": [0.001] * 20, "A": [0.001] * 20, "B": [0.001] * 20}
        result = sr.generate_signals(sector_returns=returns)
        assert len(result.top_sectors) > 0


# ============================================================
# TestIntegration - 集成场景测试
# ============================================================

class TestIntegration:
    """集成场景测试."""

    def test_full_workflow_with_regime(self):
        """完整工作流: regime + signals."""
        # 1. 获取 regime
        from utils.alpha.macro_indicator import MacroIndicatorManager
        macro_mgr = MacroIndicatorManager()
        macro_mgr._is_enabled = lambda: True
        macro_mgr.update(new_returns=[0.002] * 70)
        regime = macro_mgr.get_regime().label

        # 2. 根据 regime 生成行业信号
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {f"S{i}": [0.001] * 20 for i in range(10)}
        result = sr.generate_signals(sector_returns=returns, regime=regime)
        assert result.regime == regime
        assert result.status == "ok"

    def test_three_dimensional_signals(self):
        """三维度信号融合."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {f"S{i}": [0.001] * 20 for i in range(5)}
        flows = {f"S{i}": 0.5 for i in range(5)}  # 温和流入
        valuations = {f"S{i}": 0.3 for i in range(5)}  # 低估
        result = sr.generate_signals(
            sector_returns=returns,
            sector_flows=flows,
            sector_valuations=valuations,
        )
        assert result.status == "ok"
        # 低估 + 流入 + 上涨 应该有较高评分
        for s in result.signals:
            assert s.composite_score > 50.0

    def test_bear_regime_favors_valuation(self):
        """bear regime 提升估值权重 (寻找防御)."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        # 构造: 高估值但动量强的行业 vs 低估值但动量弱的行业
        returns = {
            "HIGH_VAL": [0.005] * 20,  # 动量强
            "LOW_VAL": [-0.001] * 20,  # 动量弱
            "S1": [0.0] * 20,
            "S2": [0.0] * 20,
            "S3": [0.0] * 20,
        }
        valuations = {
            "HIGH_VAL": 0.9,  # 高估
            "LOW_VAL": 0.1,   # 低估
            "S1": 0.5,
            "S2": 0.5,
            "S3": 0.5,
        }
        # bear regime 应该更看重估值
        result_bear = sr.generate_signals(
            sector_returns=returns,
            sector_valuations=valuations,
            regime="bear",
        )
        # 在 bear regime 下, LOW_VAL 排名应比 bull regime 下更高
        result_bull = sr.generate_signals(
            sector_returns=returns,
            sector_valuations=valuations,
            regime="bull",
        )
        bear_rank_low_val = next(s.rank for s in result_bear.signals if s.code == "LOW_VAL")
        bull_rank_low_val = next(s.rank for s in result_bull.signals if s.code == "LOW_VAL")
        assert bear_rank_low_val <= bull_rank_low_val


# ============================================================
# TestEdgeCases - 边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_empty_returns_dict(self):
        """空 returns 字典."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        result = sr.generate_signals(sector_returns={})
        assert result.status == "no_data"

    def test_single_sector(self):
        """单行业 (样本不足)."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        result = sr.generate_signals(sector_returns={"S1": [0.001] * 20})
        assert result.status == "insufficient_data"

    def test_extreme_returns(self):
        """极端收益率不崩溃."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {
            "S1": [0.5] * 20,  # 暴涨
            "S2": [-0.5] * 20,  # 暴跌
            "S3": [0.0] * 20,
            "S4": [0.0] * 20,
            "S5": [0.0] * 20,
        }
        result = sr.generate_signals(sector_returns=returns)
        assert result.status == "ok"
        # S1 应该排第一
        assert result.signals[0].code == "S1"

    def test_zero_returns_all_neutral(self):
        """全零收益率全部 NEUTRAL."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {f"S{i}": [0.0] * 20 for i in range(5)}
        result = sr.generate_signals(sector_returns=returns)
        for s in result.signals:
            assert s.label == "NEUTRAL"

    def test_nan_returns_not_crash(self):
        """NaN 收益率不崩溃 (会被忽略)."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        returns = {
            "S1": [0.001, float("nan"), 0.002] * 7,
            "S2": [0.0] * 20,
            "S3": [0.0] * 20,
            "S4": [0.0] * 20,
            "S5": [0.0] * 20,
        }
        # 不崩溃即通过
        result = sr.generate_signals(sector_returns=returns)
        assert result.status == "ok"

    def test_bottom_n_zero(self):
        """bottom_n=0 时不返回规避列表."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        sr._bottom_n = 0
        returns = {f"S{i}": [0.001] * 20 for i in range(5)}
        result = sr.generate_signals(sector_returns=returns)
        assert result.bottom_sectors == []

    def test_top_n_larger_than_sectors(self):
        """top_n > 行业数时返回所有."""
        sr = SectorRotation()
        sr._is_enabled = lambda: True
        sr._min_samples = 3
        sr._top_n = 10
        returns = {f"S{i}": [0.001] * 20 for i in range(5)}
        result = sr.generate_signals(sector_returns=returns)
        assert len(result.top_sectors) <= 5

    def test_with_empty_config(self):
        """空配置不崩溃."""
        sr = SectorRotation(config={})
        assert sr._lookback_days == DEFAULT_LOOKBACK_DAYS
