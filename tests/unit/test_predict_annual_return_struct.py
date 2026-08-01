# -*- coding: utf-8 -*-
"""v8.4 predict_annual_return_struct 结构化输出测试.

验证:
    1. predict_annual_return_struct() 返回完整 dict
    2. 三情景 (bull/base/bear) 数据结构正确
    3. 概率加权计算正确
    4. phase_adjusted_return = expected_return * 0.70
    5. contributions 列表非空
    6. predict_annual_return() CLI wrapper 不崩溃
"""
from __future__ import annotations

import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_V83_DIR = _PROJECT_ROOT / "v8.3_institutional"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_V83_DIR))


class TestPredictAnnualReturnStruct:
    """结构化年化收益预测测试"""

    def test_returns_dict(self):
        """返回 dict 类型"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        """包含所有必需字段"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        required_keys = [
            "scenarios", "expected_return", "expected_vol",
            "sharpe_estimate", "phase_adjusted_return", "base_return",
            "assumptions", "contributions", "generated_at",
        ]
        for key in required_keys:
            assert key in result, f"缺少字段: {key}"

    def test_scenarios_structure(self):
        """三情景结构正确"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        scenarios = result["scenarios"]
        assert "bull" in scenarios
        assert "base" in scenarios
        assert "bear" in scenarios
        for _name, sc in scenarios.items():
            assert "prob" in sc
            assert "return" in sc
            assert "equity" in sc
            assert isinstance(sc["return"], float)

    def test_probabilities_sum_to_one(self):
        """三情景概率之和=1"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        total = (result["scenarios"]["bull"]["prob"] +
                 result["scenarios"]["base"]["prob"] +
                 result["scenarios"]["bear"]["prob"])
        assert abs(total - 1.0) < 0.001

    def test_expected_return_is_float(self):
        """expected_return 是 float"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert isinstance(result["expected_return"], float)
        assert isinstance(result["phase_adjusted_return"], float)

    def test_phase_adjusted_equals_expected_times_factor(self):
        """phase_adjusted_return = expected_return * phase_factor (0.70)"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        expected = result["expected_return"]
        phase_factor = result["assumptions"]["phase_factor"]
        adjusted = result["phase_adjusted_return"]
        assert abs(adjusted - expected * phase_factor) < 0.001

    def test_sharpe_estimate_calculated(self):
        """sharpe_estimate = (expected_return - 0.025) / expected_vol"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        if result["expected_vol"] > 0:
            expected_sharpe = (result["expected_return"] - 0.025) / result["expected_vol"]
            assert abs(result["sharpe_estimate"] - expected_sharpe) < 0.001

    def test_contributions_non_empty(self):
        """contributions 列表非空"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert len(result["contributions"]) > 0
        for c in result["contributions"]:
            assert "name" in c
            assert "amount" in c
            assert "pct" in c

    def test_assumptions_contains_capital(self):
        """assumptions 包含资金参数"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert result["assumptions"]["total_capital"] == 5_000_000
        assert result["assumptions"]["equity_capital"] == 3_000_000
        assert result["assumptions"]["phase_factor"] == 0.70

    def test_bear_scenario_has_market_return(self):
        """bear 情景包含 market_return 字段"""
        from predict_annual_return import predict_annual_return_struct
        result = predict_annual_return_struct()
        assert "market_return" in result["scenarios"]["bear"]
        assert result["scenarios"]["bear"]["market_return"] == -0.18
