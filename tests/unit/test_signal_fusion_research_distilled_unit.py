"""
SignalFusionEngine 研究蒸馏信号 (第 6 信号源) 单元测试
=====================================================

测试 v8.6.9 新增的研究蒸馏信号 post-mix 集成:
- 默认参数与缓存初始化
- 无注入时向后兼容
- 注入后 post-mix 生效
- NaN/Inf 防御 (4 层)
- 空字典/None 降级
- 零权重降级
- 与 pipeline_factor 共存
- 负信号 (看跌) 注入

设计依据: .trae/documents/GitHub热门项目深度集成方案_2026-07-26.md 阶段5
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.signal_fusion import SignalFusionEngine  # noqa: E402

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def engine():
    """默认参数的 SignalFusionEngine"""
    return SignalFusionEngine()


@pytest.fixture
def alpha_signal():
    """标准 alpha 信号 (strength=0.5, confidence=0.8)"""
    return {"600276.SH": {"strength": 0.5, "confidence": 0.8}}


# ============================================================
# 测试组 1: 默认参数与初始化
# ============================================================


class TestResearchDistilledDefaults:
    """默认参数与缓存初始化"""

    def test_default_weight_is_003(self, engine):
        """默认 research_distilled_weight 应为 0.03"""
        assert engine.research_distilled_weight == 0.03

    def test_default_cache_empty(self, engine):
        """默认缓存应为空 dict"""
        assert engine._research_distilled_signals == {}
        assert isinstance(engine._research_distilled_signals, dict)

    def test_custom_weight(self):
        """支持自定义权重"""
        e = SignalFusionEngine(research_distilled_weight=0.05)
        assert e.research_distilled_weight == 0.05


# ============================================================
# 测试组 2: 无注入时向后兼容
# ============================================================


class TestResearchDistilledNoInjection:
    """无注入时不影响融合 (向后兼容)"""

    def test_fuse_without_injection_has_zero_research_strength(
        self, engine, alpha_signal
    ):
        """无注入时 research_distilled_strength=0.0"""
        result = engine.fuse(alpha_signals=alpha_signal)
        assert len(result) == 1
        r = result[0]
        assert r.sources["research_distilled_strength"] == 0.0

    def test_fuse_without_injection_not_applied(self, engine, alpha_signal):
        """无注入时 research_distilled_applied=False"""
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.meta["research_distilled_applied"] is False

    def test_fuse_without_injection_weight_recorded(self, engine, alpha_signal):
        """无注入时 meta 仍记录权重值"""
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.meta["research_distilled_weight"] == 0.03

    def test_fuse_with_missing_research_no_effect(self, engine, alpha_signal):
        """关键回归: 无 research 信号时 strength 与无 post-mix 一致"""
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        # research_distilled 未触发, strength 应仅由 alpha 决定 (经指数响应)
        # alpha_s=0.5, 经 _dynamic_weights 和指数响应后
        assert r.strength != 0.0  # alpha 信号足够强
        assert r.meta["research_distilled_applied"] is False


# ============================================================
# 测试组 3: 注入后 post-mix 生效
# ============================================================


class TestResearchDistilledInjection:
    """注入后 post-mix 调整生效"""

    def test_inject_valid_signals(self, engine):
        """注入有效信号后缓存更新"""
        engine.inject_research_distilled_signals({"600276.SH": 0.8, "000001.SZ": -0.6})
        assert len(engine._research_distilled_signals) == 2
        assert engine._research_distilled_signals["600276.SH"] == 0.8
        assert engine._research_distilled_signals["000001.SZ"] == -0.6

    def test_fuse_after_injection_applied(self, engine, alpha_signal):
        """注入后融合 research_distilled_applied=True"""
        engine.inject_research_distilled_signals({"600276.SH": 0.8})
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.sources["research_distilled_strength"] == 0.8
        assert r.meta["research_distilled_applied"] is True

    def test_post_mix_strength_changed(self, engine, alpha_signal):
        """post-mix 后 strength 与无注入时不同"""
        # 无注入
        result_no_inject = engine.fuse(alpha_signals=alpha_signal)
        strength_no_inject = result_no_inject[0].strength

        # 注入看涨信号
        engine.inject_research_distilled_signals({"600276.SH": 0.8})
        result_with_inject = engine.fuse(alpha_signals=alpha_signal)
        strength_with_inject = result_with_inject[0].strength

        # post-mix: 0.5*(1-0.03) + 0.8*0.03 = 0.509 > 0.5 (看涨信号增强)
        # 经指数响应后仍应增强
        assert (
            strength_with_inject > strength_no_inject
        ), f"看涨 research 信号应增强 strength: {strength_with_inject} > {strength_no_inject}"

    def test_negative_research_signal_decreases_strength(self, engine, alpha_signal):
        """看跌 research 信号应减弱 strength"""
        # 无注入
        result_no_inject = engine.fuse(alpha_signals=alpha_signal)
        strength_no_inject = result_no_inject[0].strength

        # 注入看跌信号
        engine.inject_research_distilled_signals({"600276.SH": -0.8})
        result_with_inject = engine.fuse(alpha_signals=alpha_signal)
        strength_with_inject = result_with_inject[0].strength

        assert (
            strength_with_inject < strength_no_inject
        ), f"看跌 research 信号应减弱 strength: {strength_with_inject} < {strength_no_inject}"


# ============================================================
# 测试组 4: NaN/Inf 防御 (4 层)
# ============================================================


class TestResearchDistilledNaNDefense:
    """NaN/Inf 4 层防御"""

    def test_inject_nan_filtered(self, engine):
        """层 1: 注入时 NaN 被过滤"""
        engine.inject_research_distilled_signals(
            {
                "600276.SH": float("nan"),
                "000001.SZ": 0.6,
                "600519.SH": float("inf"),
            }
        )
        # NaN 和 Inf 都应被过滤
        assert "600276.SH" not in engine._research_distilled_signals
        assert "600519.SH" not in engine._research_distilled_signals
        assert "000001.SZ" in engine._research_distilled_signals
        assert len(engine._research_distilled_signals) == 1

    def test_fuse_with_nan_in_cache_does_not_propagate(self, engine, alpha_signal):
        """层 2-4: 即使缓存有异常值, 融合结果也不含 NaN"""
        # 手动注入 NaN (绕过 inject 过滤, 模拟极端情况)
        engine._research_distilled_signals = {"600276.SH": float("nan")}
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        # strength 必须是有限数
        assert math.isfinite(r.strength), f"strength 不应是 NaN: {r.strength}"
        assert -1.0 <= r.strength <= 1.0

    def test_fuse_with_inf_in_cache_does_not_propagate(self, engine, alpha_signal):
        """Inf 也应被防御"""
        engine._research_distilled_signals = {"600276.SH": float("inf")}
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert math.isfinite(r.strength)
        assert -1.0 <= r.strength <= 1.0

    def test_strength_bounded_after_research_mix(self, engine, alpha_signal):
        """post-mix 后 strength 仍在 [-1, 1] 内"""
        # 极端信号值
        engine.inject_research_distilled_signals({"600276.SH": 1.0})
        result = engine.fuse(alpha_signals=alpha_signal)
        assert -1.0 <= result[0].strength <= 1.0

        engine.inject_research_distilled_signals({"600276.SH": -1.0})
        result = engine.fuse(alpha_signals=alpha_signal)
        assert -1.0 <= result[0].strength <= 1.0


# ============================================================
# 测试组 5: 降级链
# ============================================================


class TestResearchDistilledDegradation:
    """降级链测试"""

    def test_empty_dict_injection_no_effect(self, engine, alpha_signal):
        """空字典注入不影响融合"""
        engine.inject_research_distilled_signals({})
        assert engine._research_distilled_signals == {}
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.meta["research_distilled_applied"] is False

    def test_none_injection_no_effect(self, engine, alpha_signal):
        """None 注入不影响融合"""
        engine.inject_research_distilled_signals(None)  # type: ignore[misc]
        assert engine._research_distilled_signals == {}

    def test_non_dict_injection_no_effect(self, engine, alpha_signal):
        """非 dict 类型注入不影响融合"""
        engine.inject_research_distilled_signals([0.8, 0.6])  # type: ignore[misc]
        assert engine._research_distilled_signals == {}
        engine.inject_research_distilled_signals("not_a_dict")  # type: ignore[misc]
        assert engine._research_distilled_signals == {}

    def test_zero_weight_no_effect(self, alpha_signal):
        """零权重时即使有信号也不应用"""
        e = SignalFusionEngine(research_distilled_weight=0.0)
        e.inject_research_distilled_signals({"600276.SH": 0.8})
        result = e.fuse(alpha_signals=alpha_signal)
        r = result[0]
        # weight=0 时 post-mix 不触发
        assert r.meta["research_distilled_applied"] is False
        assert r.meta["research_distilled_weight"] == 0.0


# ============================================================
# 测试组 6: 与 pipeline_factor 共存
# ============================================================


class TestResearchDistilledCoexistenceWithPipeline:
    """研究蒸馏信号与 Pipeline 因子信号共存"""

    def test_both_sources_recorded(self, engine, alpha_signal):
        """两个信号源都应在 sources 中记录"""
        engine.inject_pipeline_factor_signals({"600276.SH": 0.6})
        engine.inject_research_distilled_signals({"600276.SH": 0.8})
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.sources["pipeline_factor_strength"] == 0.6
        assert r.sources["research_distilled_strength"] == 0.8
        assert r.meta["pipeline_factor_applied"] is True
        assert r.meta["research_distilled_applied"] is True

    def test_both_weights_recorded(self, engine, alpha_signal):
        """两个权重都应在 meta 中记录"""
        engine.inject_pipeline_factor_signals({"600276.SH": 0.6})
        engine.inject_research_distilled_signals({"600276.SH": 0.8})
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.meta["pipeline_factor_weight"] == 0.05
        assert r.meta["research_distilled_weight"] == 0.03

    def test_pipeline_only_when_research_missing(self, engine, alpha_signal):
        """仅有 pipeline 信号时 research 不应用"""
        engine.inject_pipeline_factor_signals({"600276.SH": 0.6})
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.meta["pipeline_factor_applied"] is True
        assert r.meta["research_distilled_applied"] is False
        assert r.sources["research_distilled_strength"] == 0.0

    def test_research_only_when_pipeline_missing(self, engine, alpha_signal):
        """仅有 research 信号时 pipeline 不应用"""
        engine.inject_research_distilled_signals({"600276.SH": 0.8})
        result = engine.fuse(alpha_signals=alpha_signal)
        r = result[0]
        assert r.meta["pipeline_factor_applied"] is False
        assert r.meta["research_distilled_applied"] is True
        assert r.sources["pipeline_factor_strength"] == 0.0


# ============================================================
# 测试组 7: 多标的场景
# ============================================================


class TestResearchDistilledMultiSymbol:
    """多标的场景"""

    def test_multi_symbol_injection(self, engine):
        """多标的注入各取各值"""
        engine.inject_research_distilled_signals(
            {
                "600276.SH": 0.8,
                "000001.SZ": -0.6,
                "600519.SH": 0.3,
            }
        )
        alpha = {
            "600276.SH": {"strength": 0.5, "confidence": 0.8},
            "000001.SZ": {"strength": 0.4, "confidence": 0.7},
            "600519.SH": {"strength": 0.6, "confidence": 0.9},
        }
        result = engine.fuse(alpha_signals=alpha)
        result_map = {r.symbol: r for r in result}
        assert result_map["600276.SH"].sources["research_distilled_strength"] == 0.8
        assert result_map["000001.SZ"].sources["research_distilled_strength"] == -0.6
        assert result_map["600519.SH"].sources["research_distilled_strength"] == 0.3

    def test_symbol_not_in_research_uses_zero(self, engine):
        """不在 research 缓存中的标的用 0.0"""
        engine.inject_research_distilled_signals({"600276.SH": 0.8})
        alpha = {
            "600276.SH": {"strength": 0.5, "confidence": 0.8},
            "000001.SZ": {"strength": 0.4, "confidence": 0.7},
        }
        result = engine.fuse(alpha_signals=alpha)
        result_map = {r.symbol: r for r in result}
        assert result_map["600276.SH"].meta["research_distilled_applied"] is True
        assert result_map["000001.SZ"].meta["research_distilled_applied"] is False
        assert result_map["000001.SZ"].sources["research_distilled_strength"] == 0.0
