# -*- coding: utf-8 -*-
"""barra_risk_decomposer 单元测试 — Barra 风险因子暴露分解全分支覆盖.

被测模块: utils/barra_risk_decomposer.py
覆盖目标: >=90%

测试内容:
- FactorExposure / BarraDecomposition 数据结构
- BarraRiskDecomposer.decompose 主入口 (各参数组合 + 异常分支)
- BarraRiskDecomposer.decompose_from_positions 简化入口
- BarraRiskDecomposer.save_result 序列化
- 集中/缺失因子诊断、风险预算审计、信息比率分解
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.barra_risk_decomposer import (  # noqa: E402
    BARRA_STYLE_FACTORS,
    SW_INDUSTRIES,
    BarraDecomposition,
    BarraRiskDecomposer,
    FactorExposure,
)

N_FACTORS = len(BARRA_STYLE_FACTORS)  # 10


# ============================================================
# 辅助构造
# ============================================================

def _full_factor_exposure(symbols, seed=0):
    """构造每个标的的 10 因子暴露 (含一个集中因子 + 一个缺失因子)."""
    rng = np.random.RandomState(seed)
    exposures = {}
    for i, sym in enumerate(symbols):
        f = {fa: float(rng.randn() * 0.2) for fa in BARRA_STYLE_FACTORS}
        # 第一个标的 Size 集中 (>0.8), 第二个标的 Momentum 缺失 (<-0.3)
        if i == 0:
            f["Size"] = 1.0
        if i == 1:
            f["Momentum"] = -0.5
        exposures[sym] = f
    return exposures


def _identity_cov(scale=0.01):
    return np.eye(N_FACTORS) * scale


# ============================================================
# 数据结构测试
# ============================================================

class FactorExposureTest:
    def test_defaults(self):
        fe = FactorExposure(factor_name="Size", exposure=0.5, contribution_to_active_risk=0.01)
        assert fe.factor_name == "Size"
        assert fe.exposure == 0.5
        assert fe.contribution_to_active_risk == 0.01
        assert fe.factor_return == 0.0
        assert fe.contribution_to_active_return == 0.0

    def test_full(self):
        fe = FactorExposure("Beta", 0.8, 0.02, 0.001, 0.0008)
        assert fe.factor_return == 0.001
        assert fe.contribution_to_active_return == 0.0008


class BarraDecompositionTest:
    def test_construct_minimal(self):
        d = BarraDecomposition(
            style_factor_exposures=[],
            industry_exposures={},
            country_exposure=0.0,
            active_risk=0.0,
            factor_risk=0.0,
            specific_risk=0.0,
            factor_risk_pct=0.0,
            active_return=0.0,
            factor_return=0.0,
            specific_return=0.0,
            information_ratio=0.0,
            factor_ir=0.0,
            specific_ir=0.0,
            risk_budget_used=0.0,
            risk_budget_remaining=0.05,
            risk_budget_utilization=0.0,
            symbols=[],
            weights=[],
            benchmark_weights=[],
            concentrated_factors=[],
            missing_factors=[],
        )
        assert d.style_factor_exposures == []
        assert d.risk_budget_remaining == 0.05


class ConstantsTest:
    def test_style_factors_count(self):
        assert len(BARRA_STYLE_FACTORS) == 10
        assert "Size" in BARRA_STYLE_FACTORS
        assert "Beta" in BARRA_STYLE_FACTORS

    def test_industries_count(self):
        assert len(SW_INDUSTRIES) == 28
        assert "银行" in SW_INDUSTRIES


# ============================================================
# BarraRiskDecomposer 构造
# ============================================================

class BarraRiskDecomposerInitTest:
    def test_default_annual_factor(self):
        d = BarraRiskDecomposer()
        assert d.annual_factor == pytest.approx(252**0.5)

    def test_custom_annual_factor(self):
        d = BarraRiskDecomposer(annualization_factor=10.0)
        assert d.annual_factor == 10.0

    def test_thresholds(self):
        d = BarraRiskDecomposer()
        assert d.CONCENTRATION_THRESHOLD == 0.8
        assert d.MISSING_THRESHOLD == -0.3


# ============================================================
# decompose 主入口
# ============================================================

class DecomposeTest:
    def setup_method(self):
        self.dec = BarraRiskDecomposer()

    def test_empty_symbols_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            self.dec.decompose(
                symbols=[],
                weights=[],
                benchmark_weights=[],
                factor_exposures={},
            )

    def test_weight_dim_mismatch_raises(self):
        with pytest.raises(ValueError, match="维度不匹配"):
            self.dec.decompose(
                symbols=["a", "b"],
                weights=[0.5],
                benchmark_weights=[0.5, 0.5],
                factor_exposures={},
            )

    def test_benchmark_dim_mismatch_raises(self):
        with pytest.raises(ValueError, match="维度不匹配"):
            self.dec.decompose(
                symbols=["a", "b"],
                weights=[0.5, 0.5],
                benchmark_weights=[0.5],
                factor_exposures={},
            )

    def test_basic_decompose_with_all_inputs(self):
        symbols = ["600519", "000858"]
        fe = _full_factor_exposure(symbols)
        fr = {f: 0.001 * (i + 1) for i, f in enumerate(BARRA_STYLE_FACTORS)}
        cov = _identity_cov(0.01)
        sr = {"600519": 0.02, "000858": 0.025}
        ind = {"600519": "食品饮料", "000858": "食品饮料"}

        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            factor_returns=fr,
            factor_cov_matrix=cov,
            stock_specific_risks=sr,
            industries=ind,
            risk_budget=0.05,
        )

        # 基本结构
        assert res.symbols == ["600519", "000858"]
        assert res.weights == [0.6, 0.4]
        assert res.benchmark_weights == [0.5, 0.5]
        assert len(res.style_factor_exposures) == N_FACTORS
        # 主动权重 = 0.1, -0.1
        # 行业暴露 = 0.1 + (-0.1) = 0
        assert res.industry_exposures["食品饮料"] == pytest.approx(0.0, abs=1e-10)
        # 风险预算审计
        assert res.risk_budget_used == pytest.approx(res.active_risk)
        assert res.risk_budget_remaining == pytest.approx(max(0.05 - res.active_risk, 0.0))
        assert res.risk_budget_utilization == pytest.approx(res.active_risk / 0.05)
        # 主动风险 > 0
        assert res.active_risk > 0
        assert res.factor_risk >= 0
        assert res.specific_risk > 0
        # 因子风险占比
        assert 0 <= res.factor_risk_pct <= 1
        # 信息比率
        assert res.information_ratio == pytest.approx(res.active_return / res.active_risk)
        # 个股特异性收益简化为 0
        assert res.specific_return == 0.0
        assert res.active_return == pytest.approx(res.factor_return)
        # 国家因子暴露 = Beta 主动暴露
        beta_idx = BARRA_STYLE_FACTORS.index("Beta")
        expected_country = (fe["600519"]["Beta"] - fe["000858"]["Beta"]) * 0.1 * -1 + (
            fe["600519"]["Beta"] * 0.1 + fe["000858"]["Beta"] * -0.1
        )
        # active_factor_exposure[Beta] = X[:,Beta] @ active_weights
        active_w = np.array([0.1, -0.1])
        expected_country = np.array([fe["600519"]["Beta"], fe["000858"]["Beta"]]) @ active_w
        assert res.country_exposure == pytest.approx(expected_country)

    def test_decompose_with_none_factor_returns(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.5, 0.5],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
        )
        # 等权 → 主动权重=0 → 主动风险=0
        assert res.active_risk == 0.0
        assert res.factor_risk == 0.0
        assert res.specific_risk == 0.0
        assert res.factor_risk_pct == 0.0
        assert res.information_ratio == 0.0
        assert res.factor_ir == 0.0
        assert res.specific_ir == 0.0
        assert res.active_return == 0.0
        assert res.factor_return == 0.0

    def test_decompose_default_factor_cov_when_none(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
        )
        # 默认 cov = eye * 0.01, 应正常计算
        assert res.active_risk > 0
        assert res.factor_risk > 0

    def test_decompose_default_specific_risks_when_none(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            stock_specific_risks=None,
        )
        # 默认 2% 日波动
        assert res.specific_risk > 0
        # specific_var = (0.1^2 + (-0.1)^2) * 0.02^2 = 0.02 * 0.0004 = 0.000008
        expected_specific = math.sqrt(0.000008) * (252**0.5)
        assert res.specific_risk == pytest.approx(expected_specific)

    def test_decompose_mismatched_cov_shape_falls_back_to_eye(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        # 错误形状的 cov → 回退到 eye * 0.01
        bad_cov = np.eye(3) * 0.05
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            factor_cov_matrix=bad_cov,
        )
        assert res.active_risk > 0

    def test_decompose_no_industries(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            industries=None,
        )
        assert res.industry_exposures == {}

    def test_decompose_industries_with_unknown(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            industries={"a": "银行"},  # b 缺失 → "未知"
        )
        assert "银行" in res.industry_exposures
        assert "未知" in res.industry_exposures

    def test_decompose_concentrated_and_missing_factors(self):
        symbols = ["a", "b"]
        # 构造主动暴露使 Size 集中, Momentum 缺失
        fe = {f: 0.0 for f in BARRA_STYLE_FACTORS}
        fe = {
            "a": {f: 0.0 for f in BARRA_STYLE_FACTORS},
            "b": {f: 0.0 for f in BARRA_STYLE_FACTORS},
        }
        # a 的 Size=10, b 的 Size=0 → active_exposure[Size] = 10*0.1 + 0*(-0.1) = 1.0 > 0.8
        fe["a"]["Size"] = 10.0
        # a 的 Momentum=-10, b 的 Momentum=0 → active = -10*0.1 = -1.0 < -0.3
        fe["a"]["Momentum"] = -10.0

        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
        )
        assert "Size" in res.concentrated_factors
        assert "Momentum" in res.missing_factors

    def test_decompose_numpy_weights(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=np.array([0.6, 0.4]),
            benchmark_weights=np.array([0.5, 0.5]),
            factor_exposures=fe,
        )
        assert res.active_risk > 0
        assert isinstance(res.weights, list)
        assert res.weights == [0.6, 0.4]

    def test_decompose_risk_budget_zero(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            risk_budget=0.0,
        )
        # risk_budget=0 → utilization=0
        assert res.risk_budget_utilization == 0.0
        assert res.risk_budget_remaining == 0.0

    def test_decompose_factor_ir_when_factor_risk_zero(self):
        symbols = ["a", "b"]
        fe = {s: {f: 0.0 for f in BARRA_STYLE_FACTORS} for s in symbols}
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            factor_returns={f: 0.001 for f in BARRA_STYLE_FACTORS},
        )
        # 所有因子暴露=0 → factor_risk=0 → factor_ir=0
        assert res.factor_risk == 0.0
        assert res.factor_ir == 0.0

    def test_decompose_factor_exposure_contributions(self):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        fr = {f: 0.001 for f in BARRA_STYLE_FACTORS}
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            factor_returns=fr,
        )
        # 检查每个 FactorExposure 的字段
        for fe_obj in res.style_factor_exposures:
            assert fe_obj.factor_name in BARRA_STYLE_FACTORS
            assert fe_obj.factor_return == 0.001
            # contribution_to_active_return = active_exposure * factor_return
            assert fe_obj.contribution_to_active_return == pytest.approx(fe_obj.exposure * 0.001)
        # factor_return = sum of contributions
        total_contrib = sum(fe_obj.contribution_to_active_return for fe_obj in res.style_factor_exposures)
        assert res.factor_return == pytest.approx(total_contrib)


# ============================================================
# decompose_from_positions 简化入口
# ============================================================

class DecomposeFromPositionsTest:
    def setup_method(self):
        self.dec = BarraRiskDecomposer()

    def test_empty_positions_raises(self):
        with pytest.raises(ValueError, match="positions 不能为空"):
            self.dec.decompose_from_positions(positions=[])

    def test_zero_total_value_raises(self):
        with pytest.raises(ValueError, match="持仓总金额必须 > 0"):
            self.dec.decompose_from_positions(
                positions=[{"code": "a", "amount": 0}, {"code": "b", "amount": 0}]
            )

    def test_basic_from_positions_equal_benchmark(self):
        positions = [
            {"code": "600519", "amount": 60000, "market_cap": 1e10, "sector": "食品饮料"},
            {"code": "000858", "amount": 40000, "market_cap": 5e9, "sector": "食品饮料"},
        ]
        res = self.dec.decompose_from_positions(positions=positions)
        assert res.symbols == ["600519", "000858"]
        assert res.weights == pytest.approx([0.6, 0.4])
        # 默认等权基准
        assert res.benchmark_weights == pytest.approx([0.5, 0.5])
        assert res.active_risk > 0
        assert "食品饮料" in res.industry_exposures

    def test_from_positions_with_benchmark_weights(self):
        positions = [
            {"code": "a", "amount": 50000, "market_cap": 1e10, "sector": "银行"},
            {"code": "b", "amount": 50000, "market_cap": 5e9, "sector": "非银金融"},
        ]
        bench = {"a": 0.7, "b": 0.3}
        res = self.dec.decompose_from_positions(
            positions=positions, benchmark_weights=bench
        )
        # benchmark 归一化
        assert res.benchmark_weights == pytest.approx([0.7, 0.3])
        assert res.weights == pytest.approx([0.5, 0.5])

    def test_from_positions_factor_estimation(self):
        """验证简化因子暴露估算公式."""
        positions = [
            {
                "code": "a",
                "amount": 100000,
                "market_cap": 1e10,
                "pe": 20.0,
                "pb": 2.0,
                "turnover": 0.5,
                "beta": 1.0,
                "momentum": 0.0,
                "volatility": 0.25,
                "growth_rate": 0.1,
                "debt_ratio": 0.5,
                "sector": "银行",
            },
        ]
        res = self.dec.decompose_from_positions(positions=positions)
        # 单标的 → 主动权重=0 (等权基准=1.0)
        assert res.active_risk == 0.0
        # 验证 Size 暴露 = log(1e10)/log(1e8) = 10/8 = 1.25
        size_fe = next(fe for fe in res.style_factor_exposures if fe.factor_name == "Size")
        assert size_fe.exposure == pytest.approx(0.0, abs=1e-10)  # 主动暴露=0
        # Beta 暴露 = (1.0-1.0)*2 = 0
        beta_fe = next(fe for fe in res.style_factor_exposures if fe.factor_name == "Beta")
        assert beta_fe.exposure == pytest.approx(0.0, abs=1e-10)

    def test_from_positions_default_market_cap_uses_amount(self):
        """market_cap 缺省时用 amount."""
        positions = [{"code": "a", "amount": 100000}]  # 无 market_cap
        res = self.dec.decompose_from_positions(positions=positions)
        assert res.symbols == ["a"]
        # log(100000)/log(1e8) = log(1e5)/log(1e8) = 5/8
        size_fe = next(fe for fe in res.style_factor_exposures if fe.factor_name == "Size")
        assert size_fe.exposure == pytest.approx(0.0, abs=1e-10)  # 单标的主动暴露=0

    def test_from_positions_negative_pe(self):
        """pe <= 0 → ep=0."""
        positions = [{"code": "a", "amount": 100000, "pe": -5, "pb": -1}]
        res = self.dec.decompose_from_positions(positions=positions)
        assert res.symbols == ["a"]
        # 不应抛异常
        assert len(res.style_factor_exposures) == N_FACTORS


# ============================================================
# save_result 序列化
# ============================================================

class SaveResultTest:
    def setup_method(self):
        self.dec = BarraRiskDecomposer()

    def test_save_result_writes_json(self, tmp_path):
        symbols = ["a", "b"]
        fe = _full_factor_exposure(symbols)
        res = self.dec.decompose(
            symbols=symbols,
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=fe,
            industries={"a": "银行", "b": "非银金融"},
        )
        out = tmp_path / "nested" / "barra_result.json"
        ret = self.dec.save_result(res, out)
        assert ret == out
        assert out.exists()
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)
        # save_result 不保存 symbols/weights, 只保存风险分解字段
        assert data["active_risk"] == pytest.approx(res.active_risk)
        assert len(data["style_factor_exposures"]) == N_FACTORS
        assert "银行" in data["industry_exposures"]
        assert data["concentrated_factors"] == res.concentrated_factors
        assert data["missing_factors"] == res.missing_factors
        assert data["country_exposure"] == pytest.approx(res.country_exposure)
        assert data["factor_risk_pct"] == pytest.approx(res.factor_risk_pct)
        assert data["information_ratio"] == pytest.approx(res.information_ratio)
        assert data["risk_budget_utilization"] == pytest.approx(res.risk_budget_utilization)
        # 检查 FactorExposure 序列化字段
        fe0 = data["style_factor_exposures"][0]
        assert "factor_name" in fe0
        assert "exposure" in fe0
        assert "contribution_to_active_risk" in fe0
        assert "factor_return" in fe0
        assert "contribution_to_active_return" in fe0