# -*- coding: utf-8 -*-
"""验证 predict_annual_return 动态化双路径 (Phase 1).

测试覆盖:
    1. Flag OFF (默认): _static_predict() 行为等价 (expected_return≈6.85%)
    2. Flag ON: _dynamic_predict() 注入 V9 基准 (expected_return≈19.62%)
    3. 动态路径结构与静态路径一致 (相同 keys)
    4. 动态路径使用 V9 Shadow 基准数据
    5. 动态路径计算真实 build_ratio
    6. 动态路径 phase_factor 非硬编码 0.70
    7. V9 基准缺失时回退到静态路径
    8. 动态路径通过 V9 基线门禁 (expected_return>=15%, sharpe>=1.0)
    9. Gate 集成: Flag ON 时 ReturnExpectationGate 通过
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_V83_DIR = _PROJECT_ROOT / "v8.3_institutional"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_V83_DIR))

_FLAG_NAME = "USE_DYNAMIC_RETURN_PREDICTION"
_OVERRIDE_DIR = _PROJECT_ROOT / "reports" / "flag_overrides"
_OVERRIDE_FILE = _OVERRIDE_DIR / f"{_FLAG_NAME}.json"


@pytest.fixture
def flag_off():
    """确保 Flag OFF (默认状态)."""
    # 清除可能存在的覆盖文件
    if _OVERRIDE_FILE.exists():
        os.remove(_OVERRIDE_FILE)
    from utils.infra.feature_flags import FeatureFlags
    FeatureFlags.reset_instance()
    yield
    # 清理
    if _OVERRIDE_FILE.exists():
        os.remove(_OVERRIDE_FILE)
    FeatureFlags.reset_instance()


@pytest.fixture
def flag_on():
    """启用 Flag (通过运行时覆盖)."""
    _OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "flag_name": _FLAG_NAME,
            "enabled": True,
            "signer": "test",
            "co_signer": "test_cosigner",
            "reason": "unit test",
            "action": "enable",
            "timestamp": "2026-07-28T00:00:00Z",
        }, f)
    from utils.infra.feature_flags import FeatureFlags
    FeatureFlags.reset_instance()
    yield
    # 清理
    if _OVERRIDE_FILE.exists():
        os.remove(_OVERRIDE_FILE)
    FeatureFlags.reset_instance()


# ============================================================
# 静态路径测试 (Flag OFF)
# ============================================================

class TestStaticPath:
    """Flag OFF 时走静态路径 (v7.6 行为等价)."""

    def test_static_path_returns_dict(self, flag_off):
        """Flag OFF 时返回字典."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert isinstance(result, dict)

    def test_static_path_expected_return_below_15(self, flag_off):
        """Flag OFF 时 expected_return < 15% (静态硬编码 ~6.85%)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        er = result["expected_return"]
        assert 0.05 < er < 0.15, f"静态路径 expected_return 应在 5-15%, 实际 {er:.4f}"

    def test_static_path_phase_factor_is_070(self, flag_off):
        """Flag OFF 时 phase_factor = 0.70 (硬编码)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        pf = result["assumptions"]["phase_factor"]
        assert pf == 0.70, f"静态路径 phase_factor 应为 0.70, 实际 {pf}"

    def test_static_path_sharpe_below_1(self, flag_off):
        """Flag OFF 时 sharpe < 1.0 (静态估算 ~0.72)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        sh = result["sharpe_estimate"]
        assert sh < 1.0, f"静态路径 sharpe 应 < 1.0, 实际 {sh:.4f}"


# ============================================================
# 动态路径测试 (Flag ON)
# ============================================================

class TestDynamicPath:
    """Flag ON 时走动态路径 (V9 基准 + 真实持仓)."""

    def test_dynamic_path_returns_dict(self, flag_on):
        """Flag ON 时返回字典."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert isinstance(result, dict)

    def test_dynamic_path_expected_return_is_v9_benchmark(self, flag_on):
        """Flag ON 时 expected_return = V9 基准 (19.62%)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        er = result["expected_return"]
        assert er == pytest.approx(0.1962, abs=0.001), \
            f"动态路径 expected_return 应为 0.1962, 实际 {er:.4f}"

    def test_dynamic_path_expected_return_above_15(self, flag_on):
        """Flag ON 时 expected_return >= 15% (通过 V9 基线门禁)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        er = result["expected_return"]
        assert er >= 0.15, f"动态路径 expected_return 应 >= 15%, 实际 {er:.2%}"

    def test_dynamic_path_phase_adjusted_above_15(self, flag_on):
        """Flag ON 时 phase_adjusted_return >= 15% (通过门禁)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        par = result["phase_adjusted_return"]
        assert par >= 0.15, f"动态路径 phase_adjusted_return 应 >= 15%, 实际 {par:.2%}"

    def test_dynamic_path_sharpe_above_1(self, flag_on):
        """Flag ON 时 sharpe >= 1.0 (V9 基准 sharpe=1.315)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        sh = result["sharpe_estimate"]
        assert sh >= 1.0, f"动态路径 sharpe 应 >= 1.0, 实际 {sh:.4f}"

    def test_dynamic_path_phase_factor_not_070(self, flag_on):
        """Flag ON 时 phase_factor != 0.70 (动态计算)."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        pf = result["assumptions"]["phase_factor"]
        assert pf != 0.70, f"动态路径 phase_factor 不应为 0.70, 实际 {pf}"

    def test_dynamic_path_phase_factor_in_valid_range(self, flag_on):
        """Flag ON 时 phase_factor 在 [0.92, 1.0] 范围内."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        pf = result["assumptions"]["phase_factor"]
        assert 0.92 <= pf <= 1.0, f"动态路径 phase_factor 应在 [0.92, 1.0], 实际 {pf}"

    def test_dynamic_path_uses_v9_benchmark(self, flag_on):
        """Flag ON 时 assumptions 包含 V9 基准数据."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assumptions = result["assumptions"]
        assert "v9_annual_return" in assumptions, "缺少 v9_annual_return"
        assert "v9_max_drawdown" in assumptions, "缺少 v9_max_drawdown"
        assert "v9_sharpe" in assumptions, "缺少 v9_sharpe"
        assert assumptions["v9_annual_return"] == pytest.approx(0.1962, abs=0.001)
        assert assumptions["v9_max_drawdown"] == pytest.approx(0.0995, abs=0.001)
        assert assumptions["v9_sharpe"] == pytest.approx(1.315, abs=0.01)

    def test_dynamic_path_has_build_ratio(self, flag_on):
        """Flag ON 时 assumptions 包含真实 build_ratio."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        br = result["assumptions"]["build_ratio"]
        assert 0.0 < br <= 2.0, f"build_ratio 应在 (0, 2], 实际 {br}"
        # 真实持仓建仓比例约 0.74
        assert br > 0.5, f"build_ratio 应 > 0.5 (真实持仓), 实际 {br}"

    def test_dynamic_path_data_source_is_shadow(self, flag_on):
        """Flag ON 时 data_source = shadow_benchmark."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert result["assumptions"]["data_source"] == "shadow_benchmark"

    def test_dynamic_path_has_positions_meta(self, flag_on):
        """Flag ON 时 assumptions 包含 positions_meta."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        pm = result["assumptions"].get("positions_meta", {})
        assert isinstance(pm, dict), "positions_meta 应为字典"
        assert "n_positions" in pm, "positions_meta 缺少 n_positions"
        assert "total_market_value" in pm, "positions_meta 缺少 total_market_value"
        assert pm["n_positions"] > 0, "n_positions 应 > 0"


# ============================================================
# 结构一致性测试
# ============================================================

class TestStructureConsistency:
    """动态路径与静态路径返回结构一致."""

    REQUIRED_KEYS = {
        "scenarios", "expected_return", "expected_vol",
        "sharpe_estimate", "phase_adjusted_return", "base_return",
        "assumptions", "contributions", "generated_at",
    }

    REQUIRED_ASSUMPTION_KEYS = {
        "total_capital", "equity_capital", "hedge_capital",
        "build_ratio", "phase_factor", "risk_free_rate",
    }

    def test_dynamic_has_same_top_level_keys(self, flag_on, flag_off):
        """动态路径与静态路径 top-level keys 一致."""
        from predict_annual_return import predict_annual_return_struct
        # 先拿静态
        static_result = predict_annual_return_struct()
        # 再拿动态 (flag_on fixture 已重置)
        from utils.infra.feature_flags import FeatureFlags
        FeatureFlags.reset_instance()
        dynamic_result = predict_annual_return_struct()

        static_keys = set(static_result.keys())
        dynamic_keys = set(dynamic_result.keys())
        assert static_keys == dynamic_keys, \
            f"keys 不一致: 静态 {static_keys} vs 动态 {dynamic_keys}"

    def test_dynamic_has_required_keys(self, flag_on):
        """动态路径包含所有必需 keys."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        for key in self.REQUIRED_KEYS:
            assert key in result, f"动态路径结果缺少 key: {key}"

    def test_dynamic_assumptions_has_required_keys(self, flag_on):
        """动态路径 assumptions 包含所有必需 keys."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assumptions = result["assumptions"]
        for key in self.REQUIRED_ASSUMPTION_KEYS:
            assert key in assumptions, f"assumptions 缺少 key: {key}"

    def test_dynamic_scenarios_has_three_scenarios(self, flag_on):
        """动态路径包含 bull/base/bear 三情景."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        scenarios = result["scenarios"]
        assert "bull" in scenarios, "缺少 bull 情景"
        assert "base" in scenarios, "缺少 base 情景"
        assert "bear" in scenarios, "缺少 bear 情景"

    def test_dynamic_probabilities_sum_to_one(self, flag_on):
        """动态路径三情景概率和 = 1.0."""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        scenarios = result["scenarios"]
        total = (scenarios["bull"]["prob"] +
                 scenarios["base"]["prob"] +
                 scenarios["bear"]["prob"])
        assert total == pytest.approx(1.0, abs=0.001), \
            f"概率和应为 1.0, 实际 {total}"


# ============================================================
# 回退测试
# ============================================================

class TestFallback:
    """动态路径异常时回退到静态路径."""

    def test_fallback_when_v9_benchmark_missing(self, flag_on):
        """V9 基准缺失时回退到静态路径."""
        from predict_annual_return import _dynamic_predict

        # Mock _load_shadow_benchmark 返回空字典
        with patch("predict_annual_return._load_shadow_benchmark", return_value={}):
            result = _dynamic_predict()
            # 应回退到静态路径
            assert result["assumptions"]["phase_factor"] == 0.70, \
                "V9 基准缺失应回退到静态路径 (phase_factor=0.70)"

    def test_fallback_when_v9_annual_return_zero(self, flag_on):
        """V9 annual_return=0 时回退到静态路径."""
        from predict_annual_return import _dynamic_predict

        # Mock 返回 annual_return=0 的基准
        fake_benchmark = {"annual_return": 0.0, "max_drawdown": 0.1, "sharpe_annual": 0.5}
        with patch("predict_annual_return._load_shadow_benchmark", return_value=fake_benchmark):
            result = _dynamic_predict()
            assert result["assumptions"]["phase_factor"] == 0.70, \
                "annual_return=0 应回退到静态路径"

    def test_fallback_when_flag_check_fails(self, flag_off):
        """Feature Flag 检查异常时回退到静态路径."""
        from predict_annual_return import predict_annual_return_struct

        # Mock is_enabled 抛异常
        with patch("utils.infra.feature_flags.is_enabled", side_effect=Exception("test")):
            result = predict_annual_return_struct()
            # 异常时应回退到静态路径
            assert result["expected_return"] < 0.15, \
                "Flag 检查异常应回退到静态路径 (expected_return < 15%)"


# ============================================================
# Gate 集成测试
# ============================================================

class TestGateIntegration:
    """动态预测与 ReturnExpectationGate 集成."""

    def test_gate_passes_with_dynamic_prediction(self, flag_on):
        """Flag ON 时 ReturnExpectationGate 评估通过."""
        from gate_manager import ReturnExpectationGate
        from predict_annual_return import predict_annual_return_struct

        prediction = predict_annual_return_struct()
        gate = ReturnExpectationGate()
        result = gate.evaluate(mode="sim")

        # sim 模式不阻断, 但应记录通过
        assert result.passed, f"Gate 应通过, blockers={result.blockers}"
        # metrics 应反映动态预测值
        metrics = result.metrics
        assert metrics["expected_return"] >= 0.15, \
            f"Gate metrics expected_return 应 >= 15%, 实际 {metrics['expected_return']}"

    def test_gate_fails_with_static_prediction(self, flag_off):
        """Flag OFF 时 ReturnExpectationGate 评估不通过 (sim 模式 warn_only)."""
        from gate_manager import ReturnExpectationGate
        from predict_annual_return import predict_annual_return_struct

        prediction = predict_annual_return_struct()
        gate = ReturnExpectationGate()
        result = gate.evaluate(mode="sim")

        # sim 模式 warn_only, passed=True 但应有 blockers
        # (因为静态预测 expected_return < 15%)
        metrics = result.metrics
        assert metrics["expected_return"] < 0.15, \
            f"静态预测 expected_return 应 < 15%, 实际 {metrics['expected_return']}"


# ============================================================
# Feature Flag 注册测试
# ============================================================

class TestFeatureFlagRegistration:
    """USE_DYNAMIC_RETURN_PREDICTION Flag 正确注册."""

    def test_flag_registered_in_feature_flags_yaml(self):
        """Flag 必须在 feature_flags.yaml 中注册."""
        ff_path = _V83_DIR / "config" / "feature_flags.yaml"
        content = ff_path.read_text(encoding="utf-8")
        assert _FLAG_NAME in content, \
            f"{_FLAG_NAME} 必须在 feature_flags.yaml 中注册"

    def test_flag_default_is_false(self, flag_off):
        """Flag 默认值必须为 False (不改变现状)."""
        from utils.infra.feature_flags import is_enabled
        assert not is_enabled(_FLAG_NAME), \
            f"{_FLAG_NAME} 默认应为 False (ADR-003 铁律)"

    def test_flag_has_fallback_description(self):
        """Flag 必须有 fallback 描述."""
        ff_path = _V83_DIR / "config" / "feature_flags.yaml"
        content = ff_path.read_text(encoding="utf-8")
        # 检查 fallback 字段存在
        # 找到 FLAG_NAME 附近的内容
        idx = content.find(_FLAG_NAME)
        assert idx >= 0, f"{_FLAG_NAME} 未在 yaml 中找到"
        # 截取该 flag 定义块 (到下一个 flag 或文件末尾)
        next_flag_idx = content.find("USE_", idx + len(_FLAG_NAME))
        if next_flag_idx < 0:
            next_flag_idx = len(content)
        flag_block = content[idx:next_flag_idx]
        assert "fallback" in flag_block.lower(), \
            f"{_FLAG_NAME} 必须有 fallback 字段"
        assert "rollback_seconds" in flag_block, \
            f"{_FLAG_NAME} 必须有 rollback_seconds 字段"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
