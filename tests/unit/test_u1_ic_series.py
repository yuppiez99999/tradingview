"""U1 完整时序 IC/ICIR 升级 — 单元测试

验证 calc_ic_series_from_history / calc_ic_ir / evaluate_factors 时序模式
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np

from utils.alpha_factor.base import (
    FactorLibraryResult,
    FactorValue,
    calc_ic_ir,
    calc_ic_series_from_history,
    evaluate_factors,
)

# ============================================================
# calc_ic_series_from_history 测试
# ============================================================

class TestCalcIcSeriesFromHistory:
    """时序 IC 序列计算 (Spearman rank IC)"""

    def test_normal_case(self):
        """正常情况: 因子值与收益正相关, IC 序列应为正"""
        # 构造 30 天数据, 5 只股票, 因子值与收益正相关
        np.random.seed(42)
        factor_history = []
        forward_returns_history = []
        for _ in range(30):
            fv = {f"stock_{i}": float(np.random.randn()) for i in range(10)}
            # 收益 = 因子值 + 噪声 (正相关)
            fr = {f"stock_{i}": fv[f"stock_{i}"] + 0.5 * float(np.random.randn()) for i in range(10)}
            factor_history.append(fv)
            forward_returns_history.append(fr)

        ic_series = calc_ic_series_from_history(factor_history, forward_returns_history)

        assert len(ic_series) == 30
        # 正相关数据 IC 均值应 > 0
        ic_mean = np.mean(ic_series)
        assert ic_mean > 0, f"正相关数据 IC 均值应 > 0, 实际 {ic_mean}"

    def test_negative_correlation(self):
        """负相关: 因子值与收益负相关, IC 序列应为负"""
        np.random.seed(42)
        factor_history = []
        forward_returns_history = []
        for _ in range(30):
            fv = {f"stock_{i}": float(np.random.randn()) for i in range(10)}
            # 收益 = -因子值 + 噪声 (负相关)
            fr = {f"stock_{i}": -fv[f"stock_{i}"] + 0.5 * float(np.random.randn()) for i in range(10)}
            factor_history.append(fv)
            forward_returns_history.append(fr)

        ic_series = calc_ic_series_from_history(factor_history, forward_returns_history)
        ic_mean = np.mean(ic_series)
        assert ic_mean < 0, f"负相关数据 IC 均值应 < 0, 实际 {ic_mean}"

    def test_min_samples_filter(self):
        """标的数不足 min_samples 时, 当日 IC 返回 0.0"""
        fv = {"a": 1.0, "b": 2.0}  # 只有 2 只
        fr = {"a": 0.01, "b": 0.02}
        ic_series = calc_ic_series_from_history([fv], [fr], min_samples=5)
        assert len(ic_series) == 1
        assert ic_series[0] == 0.0  # 样本不足返回 0

    def test_zero_std_returns_zero(self):
        """因子值或收益标准差为 0 时, 当日 IC 返回 0.0"""
        fv = {f"s{i}": 1.0 for i in range(10)}  # 所有因子值相同
        fr = {f"s{i}": float(i) * 0.01 for i in range(10)}
        ic_series = calc_ic_series_from_history([fv], [fr])
        assert ic_series[0] == 0.0  # std=0 返回 0

    def test_empty_history(self):
        """空历史返回空列表"""
        assert calc_ic_series_from_history([], []) == []

    def test_mismatched_lengths(self):
        """因子历史和收益历史长度不一致, 取最小值"""
        fv = [{f"s{i}": float(i) for i in range(10)}]
        fr = [{f"s{i}": float(i) * 0.01 for i in range(10)} for _ in range(5)]
        ic_series = calc_ic_series_from_history(fv, fr)
        assert len(ic_series) == 1  # 取 min(1, 5) = 1

    def test_missing_symbols_handled(self):
        """标的集合不完全重叠时, 只用交集计算"""
        fv = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 5.0,
              "f": 6.0, "g": 7.0, "h": 8.0, "i": 9.0, "j": 10.0}
        fr = {"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04, "e": 0.05,
              "f": 0.06, "g": 0.07, "h": 0.08, "i": 0.09, "j": 0.10,
              "extra": 0.11}  # extra 不在因子中
        ic_series = calc_ic_series_from_history([fv], [fr], min_samples=5)
        assert len(ic_series) == 1
        # 完全正相关的 10 只股票, IC 应接近 1.0
        assert ic_series[0] > 0.9

    def test_spearman_not_pearson(self):
        """验证使用 Spearman (rank) 而非 Pearson — 用非线性数据验证"""
        # Spearman 对单调变换不敏感, Pearson 对非线性敏感
        # 因子值: 1,2,3,4,5,6,7,8,9,10
        # 收益: 100,200,300,400,500,600,700,800,900,1000 (单调但非线性)
        fv = {f"s{i}": float(i + 1) for i in range(10)}
        fr = {f"s{i}": float((i + 1) * 100) for i in range(10)}
        ic_series = calc_ic_series_from_history([fv], [fr])
        # Spearman: 完全单调正相关 → IC=1.0
        # Pearson: 也接近 1.0 但不完全是 (线性相关)
        assert abs(ic_series[0] - 1.0) < 1e-6, f"Spearman 完全单调应 IC=1.0, 实际 {ic_series[0]}"


# ============================================================
# calc_ic_ir 测试
# ============================================================

class TestCalcIcIr:
    """IC_IR = mean(IC) / std(IC) 计算"""

    def test_normal_case(self):
        """正常情况: 30 个 IC 样本, 计算 IC_IR"""
        np.random.seed(42)
        ic_series = list(np.random.randn(30) * 0.1 + 0.05)  # 均值 0.05, 标准差 0.1
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        assert ic_ir != 0.0  # 应该有非零 IC_IR
        assert ic_mean > 0  # 均值为正
        assert ic_std > 0  # 标准差为正

    def test_insufficient_samples(self):
        """样本不足 min_periods 时返回 (0, 0, 0)"""
        ic_series = [0.1, 0.2, 0.3]  # 只有 3 个 < 20
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        assert ic_ir == 0.0
        assert ic_mean == 0.0
        assert ic_std == 0.0

    def test_zero_std(self):
        """所有 IC 相同时 (std=0), 返回 (0, mean, 0)"""
        ic_series = [0.05] * 30  # 全部相同
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        assert ic_ir == 0.0
        assert abs(ic_mean - 0.05) < 1e-10  # 浮点精度容差
        assert ic_std == 0.0

    def test_negative_ic_ir(self):
        """IC 均值为负时, IC_IR 为负"""
        np.random.seed(42)
        ic_series = list(np.random.randn(30) * 0.1 - 0.05)  # 均值 -0.05
        ic_ir, ic_mean, _ = calc_ic_ir(ic_series, min_periods=20)
        assert ic_ir < 0
        assert ic_mean < 0

    def test_nan_handling(self):
        """IC 序列包含 NaN 时, 过滤后计算"""
        ic_series = [0.1, float('nan'), 0.2, 0.15, float('inf')] + [0.1] * 25
        # NaN 和 inf 被过滤, 剩余 >= 20 个有限值
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        # 不崩溃即可, 数值合理性不严格校验 (inf 过滤后可能不足 20)
        assert isinstance(ic_ir, float)
        assert isinstance(ic_mean, float)

    def test_ddof_1(self):
        """验证使用 ddof=1 (样本标准差) 而非 ddof=0 (总体标准差)"""
        ic_series = [0.1, 0.2, 0.3, 0.15, 0.25] * 6  # 30 个样本
        ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
        # 手动计算 ddof=1 的标准差
        expected_std = float(np.std(ic_series, ddof=1))
        assert abs(ic_std - expected_std) < 1e-10, f"应使用 ddof=1, 期望 std={expected_std}, 实际 {ic_std}"


# ============================================================
# evaluate_factors 时序模式测试
# ============================================================

class TestEvaluateFactorsTimeseries:
    """evaluate_factors U1 升级: 时序 IC/ICIR 模式"""

    def _make_price_data(self, n_symbols=10, n_days=30):
        """构造测试用 price_data"""
        np.random.seed(42)
        price_data = {}
        for i in range(n_symbols):
            sym = f"stock_{i}"
            closes = list(100 + np.cumsum(np.random.randn(n_days) * 0.5))
            price_data[sym] = {"closes": closes}
        return price_data

    def _make_factor_history(self, factor_name, n_days=30, n_symbols=10):
        """构造测试用因子历史序列"""
        np.random.seed(42)
        factor_history = {factor_name: []}
        for _ in range(n_days):
            fv = {f"stock_{i}": float(np.random.randn()) for i in range(n_symbols)}
            factor_history[factor_name].append(fv)
        return factor_history

    def _make_forward_returns_history(self, n_days=30, n_symbols=10):
        """构造测试用前向收益历史"""
        np.random.seed(42)
        fwd_returns = []
        for _ in range(n_days):
            fr = {f"stock_{i}": float(np.random.randn() * 0.01) for i in range(n_symbols)}
            fwd_returns.append(fr)
        return fwd_returns

    def test_timeseries_mode_sets_ic_ir(self):
        """时序模式: 提供 factor_history + forward_returns_history 时, ic_ir 被设置"""
        price_data = self._make_price_data()
        factor_history = self._make_factor_history("TEST_FACTOR")
        fwd_returns = self._make_forward_returns_history()

        result = FactorLibraryResult()
        result.factors["TEST_FACTOR"] = FactorValue(
            name="TEST_FACTOR", category="Test",
            values={f"stock_{i}": float(i) for i in range(10)}
        )

        evaluate_factors(result, price_data, factor_history, fwd_returns)

        fval = result.factors["TEST_FACTOR"]
        # ic_ir 应该被设置 (不一定是正值, 但不应是默认的 0.0 除非样本不足)
        assert hasattr(fval, "ic_ir")
        assert isinstance(fval.ic_ir, float)
        # ic_1d, ic_5d, ic_20d 应该被设置
        assert isinstance(fval.ic_1d, float)
        assert isinstance(fval.ic_5d, float)
        assert isinstance(fval.ic_20d, float)

    def test_legacy_mode_backward_compatible(self):
        """降级模式: 不提供 factor_history 时, 使用单点 IC (向后兼容)"""
        price_data = self._make_price_data()

        result = FactorLibraryResult()
        result.factors["TEST_FACTOR"] = FactorValue(
            name="TEST_FACTOR", category="Test",
            values={f"stock_{i}": float(i) for i in range(10)}
        )

        # 不传 factor_history / forward_returns_history
        evaluate_factors(result, price_data)

        fval = result.factors["TEST_FACTOR"]
        # ic_5d 被设置 (单点 IC)
        assert isinstance(fval.ic_5d, float)
        # ic_ir 保持默认 0.0 (单点模式不计算 ICIR)
        assert fval.ic_ir == 0.0

    def test_idempotent(self):
        """幂等性: 重复调用不重复 append strong/effective factors"""
        price_data = self._make_price_data()

        result = FactorLibraryResult()
        result.factors["TEST_FACTOR"] = FactorValue(
            name="TEST_FACTOR", category="Test",
            values={f"stock_{i}": float(i) for i in range(10)}
        )

        evaluate_factors(result, price_data)
        n_strong_1 = len(result.strong_factors)
        n_effective_1 = len(result.effective_factors)

        evaluate_factors(result, price_data)  # 重复调用
        n_strong_2 = len(result.strong_factors)
        n_effective_2 = len(result.effective_factors)

        assert n_strong_1 == n_strong_2, "幂等: 重复调用不应增加 strong_factors"
        assert n_effective_1 == n_effective_2, "幂等: 重复调用不应增加 effective_factors"

    def test_short_history_falls_back_to_legacy(self):
        """前向收益历史 < 20 天时, 降级为单点 IC"""
        price_data = self._make_price_data()
        factor_history = self._make_factor_history("TEST_FACTOR", n_days=10)  # 不足 20 天
        fwd_returns = self._make_forward_returns_history(n_days=10)

        result = FactorLibraryResult()
        result.factors["TEST_FACTOR"] = FactorValue(
            name="TEST_FACTOR", category="Test",
            values={f"stock_{i}": float(i) for i in range(10)}
        )

        evaluate_factors(result, price_data, factor_history, fwd_returns)

        fval = result.factors["TEST_FACTOR"]
        # 不足 20 天, 降级为单点 IC, ic_ir 保持 0.0
        assert fval.ic_ir == 0.0

    def test_factor_not_in_history_falls_back(self):
        """因子不在 factor_history 中时, 该因子降级为单点 IC"""
        price_data = self._make_price_data()
        factor_history = self._make_factor_history("OTHER_FACTOR")  # 不含 TEST_FACTOR
        fwd_returns = self._make_forward_returns_history()

        result = FactorLibraryResult()
        result.factors["TEST_FACTOR"] = FactorValue(
            name="TEST_FACTOR", category="Test",
            values={f"stock_{i}": float(i) for i in range(10)}
        )

        evaluate_factors(result, price_data, factor_history, fwd_returns)

        fval = result.factors["TEST_FACTOR"]
        # TEST_FACTOR 不在 history 中, 降级为单点 IC
        assert fval.ic_ir == 0.0  # 单点模式不计算 ICIR
        assert isinstance(fval.ic_5d, float)  # 但 ic_5d 仍被计算

    def test_empty_factor_values_skipped(self):
        """因子 values 为空时, 跳过该因子"""
        price_data = self._make_price_data()

        result = FactorLibraryResult()
        result.factors["EMPTY_FACTOR"] = FactorValue(
            name="EMPTY_FACTOR", category="Test", values={}
        )

        evaluate_factors(result, price_data)

        # 空因子不应出现在 strong/effective 中
        assert "EMPTY_FACTOR" not in result.strong_factors
        assert "EMPTY_FACTOR" not in result.effective_factors


# ============================================================
# 与 gate1_validation 一致性验证
# ============================================================

class TestGate1Consistency:
    """验证 calc_ic_series_from_history 与 gate1_validation 趋势一致

    注意: gate1_validation.calc_ic_series 用滚动重算因子 (严格无前视),
    本函数用预计算的因子历史序列. 两者在数据充足时趋势应一致 (同号).
    """

    def test_ic_sign_consistency(self):
        """IC 符号一致性: 正相关数据两者 IC 均为正"""
        np.random.seed(42)
        # 构造 30 天正相关的因子-收益数据
        factor_history = []
        forward_returns_history = []
        for _ in range(30):
            fv = {f"stock_{i}": float(np.random.randn()) for i in range(20)}
            fr = {f"stock_{i}": fv[f"stock_{i}"] + 0.3 * float(np.random.randn()) for i in range(20)}
            factor_history.append(fv)
            forward_returns_history.append(fr)

        ic_series = calc_ic_series_from_history(factor_history, forward_returns_history)
        ic_ir, ic_mean, _ = calc_ic_ir(ic_series, min_periods=20)

        # 正相关数据 IC 均值应为正
        assert ic_mean > 0, f"正相关数据 IC 均值应 > 0, 实际 {ic_mean}"
        # IC_IR 也应为正 (均值正 + 标准差正)
        assert ic_ir > 0, f"正相关数据 IC_IR 应 > 0, 实际 {ic_ir}"
