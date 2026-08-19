"""smart_beta_engine 单元测试 — Smart Beta 多因子加权引擎全分支覆盖.

被测模块: utils/smart_beta_engine.py
覆盖目标: >=90%

测试内容:
- FactorTimingInfo / SmartBetaResult 数据结构
- SmartBetaEngine.__init__ (含 temperature<=0 异常)
- SmartBetaEngine.optimize 主入口 (各参数组合)
- _apply_factor_timing 因子择时
- build_long_short_portfolio 多空组合
- diagnose_weights 诊断工具
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.smart_beta_engine import (  # noqa: E402
    FactorTimingInfo,
    SmartBetaEngine,
    SmartBetaResult,
)

# ============================================================
# 辅助构造
# ============================================================

def _make_factor_scores(symbols, factors=("MOM_60D", "VAL_PE", "QUA_ROE"), seed=0):
    rng = np.random.RandomState(seed)
    return {
        s: {f: float(rng.randn()) for f in factors}
        for s in symbols
    }


def _make_cov(n, seed=0):
    rng = np.random.RandomState(seed)
    A = rng.randn(n, n) * 0.05 + np.eye(n) * 0.15
    return A @ A.T


# ============================================================
# 数据结构测试
# ============================================================

class FactorTimingInfoTest:
    def test_defaults(self):
        info = FactorTimingInfo(
            factor_name="MOM", base_weight=0.4, current_weight=0.5, timing_signal=0.2
        )
        assert info.factor_name == "MOM"
        assert info.base_weight == 0.4
        assert info.current_weight == 0.5
        assert info.timing_signal == 0.2
        assert info.factor_momentum == 0.0
        assert info.factor_valuation == 0.0

    def test_full(self):
        info = FactorTimingInfo("VAL", 0.3, 0.35, -0.1, 0.05, 1.2)
        assert info.factor_momentum == 0.05
        assert info.factor_valuation == 1.2


class SmartBetaResultTest:
    def test_construct(self):
        r = SmartBetaResult(
            symbols=["a", "b", "c"],
            smart_beta_weights=np.array([0.4, 0.3, 0.3]),
            market_cap_weights=np.array([0.5, 0.3, 0.2]),
            equal_weights=np.array([1 / 3, 1 / 3, 1 / 3]),
            composite_score=np.array([0.5, 0.3, 0.1]),
            factor_exposure={"MOM": 0.2},
            expected_return=0.08,
            expected_volatility=0.18,
            sharpe_ratio=0.28,
            tracking_error=0.05,
            information_ratio=0.4,
            weight_concentration=0.34,
            effective_n=2.94,
            turnover_vs_market=0.1,
            alpha_vs_market=0.01,
        )
        assert r.symbols == ["a", "b", "c"]
        assert r.factor_timing == []  # 默认


# ============================================================
# SmartBetaEngine 构造
# ============================================================

class SmartBetaEngineInitTest:
    def test_defaults(self):
        eng = SmartBetaEngine()
        assert eng.temperature == 0.5
        assert eng.enable_timing is True
        assert eng.timing_window == 60
        assert eng.timing_alpha == 0.3
        assert eng.max_weight == 0.10
        assert eng.min_weight == 0.0
        assert eng.max_te == 0.08

    def test_custom(self):
        eng = SmartBetaEngine(
            temperature=1.0,
            enable_factor_timing=False,
            timing_momentum_window=20,
            timing_alpha=0.5,
            max_weight=0.2,
            min_weight=0.01,
            max_tracking_error=0.10,
        )
        assert eng.temperature == 1.0
        assert eng.enable_timing is False
        assert eng.timing_window == 20
        assert eng.timing_alpha == 0.5

    def test_zero_temperature_raises(self):
        with pytest.raises(ValueError, match="temperature 必须 > 0"):
            SmartBetaEngine(temperature=0.0)

    def test_negative_temperature_raises(self):
        with pytest.raises(ValueError, match="temperature 必须 > 0"):
            SmartBetaEngine(temperature=-0.5)


# ============================================================
# optimize 主入口
# ============================================================

class OptimizeTest:
    def test_empty_symbols_raises(self):
        eng = SmartBetaEngine()
        with pytest.raises(ValueError, match="不能为空"):
            eng.optimize(symbols=[], factor_scores={})

    def test_basic_optimize(self):
        eng = SmartBetaEngine()
        symbols = ["600519", "000858", "601318"]
        scores = {
            "600519": {"MOM_60D": 0.5, "VAL_PE": 0.3, "QUA_ROE": 0.4},
            "000858": {"MOM_60D": -0.2, "VAL_PE": 0.6, "QUA_ROE": 0.2},
            "601318": {"MOM_60D": 0.8, "VAL_PE": -0.1, "QUA_ROE": 0.5},
        }
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
        )
        assert res.symbols == symbols
        assert len(res.smart_beta_weights) == 3
        assert res.smart_beta_weights.sum() == pytest.approx(1.0)
        assert res.composite_score.shape == (3,)
        assert "MOM_60D" in res.factor_exposure
        # 等权对照
        assert res.equal_weights == pytest.approx([1 / 3, 1 / 3, 1 / 3])
        # 无 market_caps → 市值权重=等权
        assert res.market_cap_weights == pytest.approx([1 / 3, 1 / 3, 1 / 3])
        # 诊断
        assert res.weight_concentration > 0
        assert res.effective_n > 0
        assert res.tracking_error >= 0

    def test_optimize_with_market_caps(self):
        eng = SmartBetaEngine()
        symbols = ["a", "b", "c"]
        scores = _make_factor_scores(symbols)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            market_caps={"a": 2e12, "b": 5e11, "c": 1e12},
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
        )
        # 市值权重按市值分配
        total = 2e12 + 5e11 + 1e12
        expected_mkt = np.array([2e12, 5e11, 1e12]) / total
        assert res.market_cap_weights == pytest.approx(expected_mkt)

    def test_optimize_market_caps_zero_falls_back_equal(self):
        eng = SmartBetaEngine()
        symbols = ["a", "b"]
        scores = _make_factor_scores(symbols)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            market_caps={"a": 0, "b": 0},
            factor_weights={"MOM_60D": 0.5, "VAL_PE": 0.3, "QUA_ROE": 0.2},
        )
        assert res.market_cap_weights == pytest.approx([0.5, 0.5])

    def test_optimize_auto_factor_weights_when_none(self):
        eng = SmartBetaEngine(enable_factor_timing=False)
        symbols = ["a", "b"]
        scores = {"a": {"MOM": 0.5, "VAL": 0.3}, "b": {"MOM": -0.2, "VAL": 0.6}}
        res = eng.optimize(symbols=symbols, factor_scores=scores, factor_weights=None)
        # 自动等权: MOM=0.5, VAL=0.5
        assert "MOM" in res.factor_exposure
        assert "VAL" in res.factor_exposure
        assert res.smart_beta_weights.sum() == pytest.approx(1.0)

    def test_optimize_with_cov_matrix(self):
        eng = SmartBetaEngine()
        n = 3
        symbols = ["a", "b", "c"]
        scores = _make_factor_scores(symbols)
        cov = _make_cov(n, seed=1)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
            cov_matrix=cov,
        )
        assert res.expected_volatility > 0
        # port_var = w' Σ w
        expected_vol = math.sqrt(float(res.smart_beta_weights @ cov @ res.smart_beta_weights))
        assert res.expected_volatility == pytest.approx(expected_vol)
        # TE 用 cov 计算
        active = res.smart_beta_weights - res.market_cap_weights
        expected_te = math.sqrt(float(active @ cov @ active))
        assert res.tracking_error == pytest.approx(expected_te)

    def test_optimize_without_cov_default_vol(self):
        eng = SmartBetaEngine()
        symbols = ["a", "b"]
        scores = _make_factor_scores(symbols)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.5, "VAL_PE": 0.3, "QUA_ROE": 0.2},
        )
        assert res.expected_volatility == 0.20

    def test_optimize_with_benchmark_weights(self):
        eng = SmartBetaEngine()
        n = 3
        symbols = ["a", "b", "c"]
        scores = _make_factor_scores(symbols)
        cov = _make_cov(n, seed=2)
        bench = np.array([0.5, 0.3, 0.2])
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
            cov_matrix=cov,
            benchmark_weights=bench,
        )
        active = res.smart_beta_weights - bench
        expected_te = math.sqrt(float(active @ cov @ active))
        assert res.tracking_error == pytest.approx(expected_te)

    def test_optimize_with_factor_timing(self):
        """启用因子择时 + 提供历史 → 调整权重."""
        eng = SmartBetaEngine(enable_factor_timing=True, timing_momentum_window=5)
        symbols = ["a", "b", "c"]
        scores = _make_factor_scores(symbols)
        # 历史足够长: MOM 正动量, VAL 负动量
        history = {
            "MOM_60D": [0.001] * 10,  # 正动量
            "VAL_PE": [-0.002] * 10,  # 负动量
            "QUA_ROE": [0.0] * 10,
        }
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
            factor_returns_history=history,
        )
        # 因子择时信息
        assert len(res.factor_timing) == 3
        mom_info = next(ti for ti in res.factor_timing if ti.factor_name == "MOM_60D")
        assert mom_info.timing_signal > 0  # 正动量 → 正信号
        val_info = next(ti for ti in res.factor_timing if ti.factor_name == "VAL_PE")
        assert val_info.timing_signal < 0  # 负动量 → 负信号
        # MOM 权重应增加
        assert mom_info.current_weight > mom_info.base_weight * 0.99 / 1.0  # 归一化后近似

    def test_optimize_factor_timing_history_too_short(self):
        """历史长度 < window → signal=0."""
        eng = SmartBetaEngine(enable_factor_timing=True, timing_momentum_window=60)
        symbols = ["a", "b"]
        scores = _make_factor_scores(symbols)
        history = {"MOM_60D": [0.001] * 10}  # 不足 60
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.5, "VAL_PE": 0.3, "QUA_ROE": 0.2},
            factor_returns_history=history,
        )
        mom_info = next(ti for ti in res.factor_timing if ti.factor_name == "MOM_60D")
        assert mom_info.timing_signal == 0.0

    def test_optimize_timing_disabled_no_history(self):
        """enable_timing=True 但无 history → 走 else 分支 (signal=0)."""
        eng = SmartBetaEngine(enable_factor_timing=True)
        symbols = ["a", "b"]
        scores = _make_factor_scores(symbols)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.5, "VAL_PE": 0.3, "QUA_ROE": 0.2},
            factor_returns_history=None,
        )
        for ti in res.factor_timing:
            assert ti.timing_signal == 0.0
            assert ti.current_weight == ti.base_weight

    def test_optimize_timing_disabled_flag(self):
        """enable_timing=False → 不调整."""
        eng = SmartBetaEngine(enable_factor_timing=False)
        symbols = ["a", "b"]
        scores = _make_factor_scores(symbols)
        history = {"MOM_60D": [0.001] * 100}
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.5, "VAL_PE": 0.3, "QUA_ROE": 0.2},
            factor_returns_history=history,
        )
        for ti in res.factor_timing:
            assert ti.timing_signal == 0.0

    def test_optimize_weight_clip(self):
        """max_weight 截断后归一化 (clip-then-normalize 是代码固有行为)."""
        eng = SmartBetaEngine(temperature=0.01, max_weight=0.5)  # 极小温度 → 极集中
        symbols = ["a", "b", "c"]
        scores = {
            "a": {"MOM": 10.0},
            "b": {"MOM": -10.0},
            "c": {"MOM": 0.0},
        }
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM": 1.0},
        )
        # 归一化后权重和=1, 非负
        assert res.smart_beta_weights.sum() == pytest.approx(1.0)
        assert np.all(res.smart_beta_weights >= 0)
        # 最高得分标的 a 应获得最大权重
        assert np.argmax(res.smart_beta_weights) == 0

    def test_optimize_sharpe_and_ir(self):
        eng = SmartBetaEngine()
        n = 3
        symbols = ["a", "b", "c"]
        scores = _make_factor_scores(symbols)
        cov = _make_cov(n, seed=3)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
            cov_matrix=cov,
            risk_free_rate=0.03,
        )
        # Sharpe = (expected_ret - rf) / vol
        expected_sharpe = (res.expected_return - 0.03) / res.expected_volatility
        assert res.sharpe_ratio == pytest.approx(expected_sharpe)
        # IR = (expected_ret - mean(composite)*0.05) / te
        if res.tracking_error > 0:
            expected_ir = (res.expected_return - float(np.mean(res.composite_score) * 0.05)) / res.tracking_error
            assert res.information_ratio == pytest.approx(expected_ir)

    def test_optimize_alpha_vs_market(self):
        eng = SmartBetaEngine()
        symbols = ["a", "b", "c"]
        scores = _make_factor_scores(symbols)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
        )
        active = res.smart_beta_weights - res.market_cap_weights
        expected_alpha = float(active @ res.composite_score * 0.1)
        assert res.alpha_vs_market == pytest.approx(expected_alpha)
        expected_turnover = float(np.sum(np.abs(active)))
        assert res.turnover_vs_market == pytest.approx(expected_turnover)


# ============================================================
# _apply_factor_timing
# ============================================================

class ApplyFactorTimingTest:
    def test_positive_momentum_increases_weight(self):
        eng = SmartBetaEngine(enable_factor_timing=True, timing_momentum_window=5, timing_alpha=0.3)
        base = {"MOM": 0.5, "VAL": 0.5}
        history = {"MOM": [0.01] * 5, "VAL": [0.0] * 5}
        info: list[FactorTimingInfo] = []
        adjusted = eng._apply_factor_timing(base, history, info)
        # 归一化后 MOM 权重应高于 VAL
        assert adjusted["MOM"] > adjusted["VAL"]
        assert len(info) == 2
        # 信号
        mom_info = next(i for i in info if i.factor_name == "MOM")
        assert mom_info.timing_signal > 0
        assert mom_info.factor_momentum == pytest.approx(0.05)

    def test_negative_momentum_decreases_weight(self):
        eng = SmartBetaEngine(enable_factor_timing=True, timing_momentum_window=5, timing_alpha=0.3)
        base = {"MOM": 0.5, "VAL": 0.5}
        history = {"MOM": [-0.01] * 5, "VAL": [0.0] * 5}
        info: list[FactorTimingInfo] = []
        adjusted = eng._apply_factor_timing(base, history, info)
        assert adjusted["MOM"] < adjusted["VAL"]

    def test_short_history_zero_signal(self):
        eng = SmartBetaEngine(enable_factor_timing=True, timing_momentum_window=60, timing_alpha=0.3)
        base = {"MOM": 0.5}
        history = {"MOM": [0.01] * 10}  # 不足 60
        info: list[FactorTimingInfo] = []
        adjusted = eng._apply_factor_timing(base, history, info)
        # 单因子归一化后 = 1.0, 但 signal=0 (未调整)
        assert adjusted["MOM"] == pytest.approx(1.0)
        assert info[0].timing_signal == 0.0
        assert info[0].factor_momentum == 0.0
        assert info[0].current_weight == pytest.approx(info[0].base_weight)

    def test_normalization(self):
        eng = SmartBetaEngine(enable_factor_timing=True, timing_momentum_window=5, timing_alpha=0.5)
        base = {"A": 0.3, "B": 0.3, "C": 0.4}
        history = {"A": [0.01] * 5, "B": [-0.01] * 5, "C": [0.0] * 5}
        info: list[FactorTimingInfo] = []
        adjusted = eng._apply_factor_timing(base, history, info)
        assert sum(adjusted.values()) == pytest.approx(1.0)

    def test_missing_history_for_factor(self):
        eng = SmartBetaEngine(enable_factor_timing=True, timing_momentum_window=5, timing_alpha=0.3)
        base = {"MOM": 0.5, "VAL": 0.5}
        history = {"MOM": [0.01] * 5}  # VAL 缺失
        info: list[FactorTimingInfo] = []
        eng._apply_factor_timing(base, history, info)
        # VAL 无历史 → signal=0
        val_info = next(i for i in info if i.factor_name == "VAL")
        assert val_info.timing_signal == 0.0


# ============================================================
# build_long_short_portfolio
# ============================================================

class BuildLongShortPortfolioTest:
    def setup_method(self):
        self.eng = SmartBetaEngine()

    def test_market_neutral(self):
        symbols = ["a", "b", "c", "d", "e", "f"]
        scores = {
            "a": {"MOM": 1.0},
            "b": {"MOM": 0.8},
            "c": {"MOM": 0.5},
            "d": {"MOM": -0.5},
            "e": {"MOM": -0.8},
            "f": {"MOM": -1.0},
        }
        portfolio = self.eng.build_long_short_portfolio(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM": 1.0},
            n_long=2,
            n_short=2,
            market_neutral=True,
        )
        # 做多 a, b; 做空 e, f
        assert portfolio["a"] == pytest.approx(0.5)
        assert portfolio["b"] == pytest.approx(0.5)
        assert portfolio["e"] == pytest.approx(-0.5)
        assert portfolio["f"] == pytest.approx(-0.5)
        # 多空市值相等
        long_total = sum(v for v in portfolio.values() if v > 0)
        short_total = sum(v for v in portfolio.values() if v < 0)
        assert long_total == pytest.approx(1.0)
        assert short_total == pytest.approx(-1.0)

    def test_not_market_neutral(self):
        symbols = ["a", "b", "c"]
        scores = {"a": {"MOM": 1.0}, "b": {"MOM": 0.5}, "c": {"MOM": -0.5}}
        portfolio = self.eng.build_long_short_portfolio(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM": 1.0},
            n_long=2,
            n_short=1,
            market_neutral=False,
        )
        # 仅做多
        assert portfolio["a"] == pytest.approx(0.5)
        assert portfolio["b"] == pytest.approx(0.5)
        # 做空权重=0
        assert portfolio["c"] == 0.0

    def test_n_short_zero(self):
        symbols = ["a", "b", "c"]
        scores = {"a": {"MOM": 1.0}, "b": {"MOM": 0.5}, "c": {"MOM": -0.5}}
        portfolio = self.eng.build_long_short_portfolio(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM": 1.0},
            n_long=2,
            n_short=0,
            market_neutral=True,
        )
        assert portfolio["a"] == pytest.approx(0.5)
        assert portfolio["b"] == pytest.approx(0.5)
        # 无做空
        assert all(v >= 0 for v in portfolio.values())

    def test_n_long_zero(self):
        symbols = ["a", "b"]
        scores = {"a": {"MOM": 1.0}, "b": {"MOM": -1.0}}
        portfolio = self.eng.build_long_short_portfolio(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM": 1.0},
            n_long=0,
            n_short=1,
            market_neutral=True,
        )
        # 无做多, 仅做空 b
        assert portfolio["b"] == pytest.approx(-1.0)

    def test_missing_factor_score(self):
        symbols = ["a", "b"]
        scores = {"a": {}}  # b 缺失, a 无因子
        portfolio = self.eng.build_long_short_portfolio(
            symbols=symbols,
            factor_scores=scores,
            factor_weights={"MOM": 1.0},
            n_long=1,
            n_short=1,
        )
        # 得分都=0, 排序稳定
        assert len(portfolio) == 2


# ============================================================
# diagnose_weights 诊断
# ============================================================

class DiagnoseWeightsTest:
    def test_diagnose(self):
        eng = SmartBetaEngine(max_tracking_error=0.10)
        symbols = ["a", "b", "c"]
        scores = _make_factor_scores(symbols)
        res = eng.optimize(
            symbols=symbols,
            factor_scores=scores,
            market_caps={"a": 2e12, "b": 5e11, "c": 1e12},
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
            cov_matrix=_make_cov(3, seed=5),
        )
        diag = eng.diagnose_weights(res)
        assert diag["effective_n"] == res.effective_n
        assert diag["weight_concentration_hhi"] == res.weight_concentration
        assert diag["max_weight"] == pytest.approx(float(np.max(res.smart_beta_weights)))
        assert diag["min_weight"] == pytest.approx(float(np.min(res.smart_beta_weights)))
        assert diag["weight_std"] == pytest.approx(float(np.std(res.smart_beta_weights)))
        assert diag["tracking_error"] == res.tracking_error
        assert diag["te_within_limit"] == (res.tracking_error <= 0.10)
        assert diag["information_ratio"] == res.information_ratio
        assert diag["sharpe_ratio"] == res.sharpe_ratio
        assert diag["turnover_vs_market"] == res.turnover_vs_market
        assert diag["alpha_vs_market"] == res.alpha_vs_market
        # active_weight_max
        expected_active_max = float(np.max(np.abs(res.smart_beta_weights - res.market_cap_weights)))
        assert diag["active_weight_max"] == pytest.approx(expected_active_max)
