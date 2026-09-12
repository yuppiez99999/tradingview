"""
RegimeFolio 制度感知组合优化 — 单元测试
========================================

测试覆盖:
- Regime 枚举
- RegimeClassifier VIX 分类
- CovarianceShrinkage Ledoit-Wolf 收缩
- RegimeConfig 配置
- RegimeAwareAllocator 分配器
- 端到端制度对比

文献: #41 RegimeFolio 2025.10
"""

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.regime_aware_allocator import (
    AllocationResult,
    CovarianceShrinkage,
    Regime,
    RegimeAwareAllocator,
    RegimeClassifier,
    RegimeConfig,
    RegimeThresholds,
)

# ============================================================
# 枚举测试
# ============================================================


class TestRegime:
    """制度枚举测试。"""

    def test_count(self):
        assert len(Regime) == 4

    def test_values(self):
        assert Regime.LOW_VOL.value == "low_vol"
        assert Regime.CRISIS.value == "crisis"


# ============================================================
# VIX 分类器测试
# ============================================================


class TestRegimeClassifier:
    """VIX 制度分类器测试。"""

    def test_low_vol(self):
        clf = RegimeClassifier()
        assert clf.classify(10.0) == Regime.LOW_VOL

    def test_normal(self):
        clf = RegimeClassifier()
        assert clf.classify(18.0) == Regime.NORMAL

    def test_high_vol(self):
        clf = RegimeClassifier()
        assert clf.classify(25.0) == Regime.HIGH_VOL

    def test_crisis(self):
        clf = RegimeClassifier()
        assert clf.classify(40.0) == Regime.CRISIS

    def test_boundaries(self):
        """边界值。"""
        clf = RegimeClassifier()
        assert clf.classify(15.0) == Regime.NORMAL
        assert clf.classify(20.0) == Regime.HIGH_VOL
        assert clf.classify(30.0) == Regime.CRISIS

    def test_custom_thresholds(self):
        """自定义阈值。"""
        thresholds = RegimeThresholds(low_vol=10, normal=15, high_vol=25)
        clf = RegimeClassifier(thresholds)
        assert clf.classify(8.0) == Regime.LOW_VOL
        assert clf.classify(12.0) == Regime.NORMAL

    def test_batch(self):
        """批量分类。"""
        clf = RegimeClassifier()
        vix = np.array([10, 18, 25, 40])
        regimes = clf.classify_batch(vix)
        assert len(regimes) == 4

    def test_distribution(self):
        """制度分布。"""
        clf = RegimeClassifier()
        clf.classify(10)
        clf.classify(10)
        clf.classify(25)
        dist = clf.regime_distribution()
        assert dist[Regime.LOW_VOL.value] == 2 / 3
        assert dist[Regime.HIGH_VOL.value] == 1 / 3


# ============================================================
# 协方差收缩测试
# ============================================================


class TestCovarianceShrinkage:
    """协方差收缩测试。"""

    def test_ledoit_wolf(self):
        """Ledoit-Wolf 收缩。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((100, 5)) * 0.02
        cov, delta = CovarianceShrinkage.ledoit_wolf(returns)
        assert cov.shape == (5, 5)
        assert 0 <= delta <= 1

    def test_ledoit_wolf_positive_definite(self):
        """收缩后正定。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((50, 3)) * 0.02
        cov, _ = CovarianceShrinkage.ledoit_wolf(returns)
        eigenvalues = np.linalg.eigvalsh(cov)
        assert np.all(eigenvalues > 0)

    def test_shrink_to_identity(self):
        """向单位矩阵收缩。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((100, 4)) * 0.02
        cov = CovarianceShrinkage.shrink_to_identity(returns, intensity=0.5)
        assert cov.shape == (4, 4)

    def test_shrinkage_reduces_offdiag(self):
        """收缩减少非对角元素。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((50, 3)) * 0.02
        cov_raw = np.cov(returns, rowvar=False)
        cov_shrunk, _ = CovarianceShrinkage.ledoit_wolf(returns)
        off_diag_raw = np.sum(np.abs(cov_raw - np.diag(np.diag(cov_raw))))
        off_diag_shrunk = np.sum(np.abs(cov_shrunk - np.diag(np.diag(cov_shrunk))))
        # 收缩后非对角元素通常更小
        assert off_diag_shrunk <= off_diag_raw + 1e-10


# ============================================================
# 配置测试
# ============================================================


class TestRegimeConfig:
    """制度配置测试。"""

    def test_defaults(self):
        config = RegimeConfig()
        assert (
            config.risk_aversion[Regime.CRISIS] > config.risk_aversion[Regime.LOW_VOL]
        )
        assert config.max_weight[Regime.CRISIS] < config.max_weight[Regime.LOW_VOL]
        assert (
            config.defense_boost[Regime.CRISIS] > config.defense_boost[Regime.LOW_VOL]
        )
        assert (
            config.offense_boost[Regime.CRISIS] < config.offense_boost[Regime.LOW_VOL]
        )


# ============================================================
# 分配器测试
# ============================================================


class TestRegimeAwareAllocator:
    """制度感知分配器测试。"""

    def setup_method(self):
        self.rng = np.random.default_rng(42)
        self.returns = self.rng.standard_normal((252, 5)) * 0.02 + 0.001

    def test_allocate(self):
        """基本分配。"""
        allocator = RegimeAwareAllocator()
        result = allocator.allocate(self.returns, vix=18.0)
        assert isinstance(result, AllocationResult)
        assert result.regime == Regime.NORMAL
        assert abs(result.weights.sum() - 1.0) < 1e-6

    def test_different_regimes_different_weights(self):
        """不同制度产生不同权重。"""
        allocator = RegimeAwareAllocator()
        r_low = allocator.allocate(self.returns, vix=10.0)
        r_crisis = allocator.allocate(self.returns, vix=40.0)
        assert not np.allclose(r_low.weights, r_crisis.weights)

    def test_crisis_more_defensive(self):
        """危机时防御资产权重更高。"""
        n_assets = 4
        returns = self.rng.standard_normal((252, n_assets)) * 0.02 + 0.001
        defense_mask = np.array([0, 0, 1, 1])
        offense_mask = np.array([1, 1, 0, 0])

        allocator = RegimeAwareAllocator()
        r_low = allocator.allocate(
            returns, vix=10.0, defense_mask=defense_mask, offense_mask=offense_mask
        )
        r_crisis = allocator.allocate(
            returns, vix=40.0, defense_mask=defense_mask, offense_mask=offense_mask
        )

        defense_low = r_low.weights[defense_mask == 1].sum()
        defense_crisis = r_crisis.weights[defense_mask == 1].sum()
        assert defense_crisis > defense_low

    def test_max_weight_constraint(self):
        """最大权重约束。

        修复 (2026-09-11, Issue #13 巡检续批): 原断言写成
        ``effective_max = max(max_w, 1.0 / n_assets)`` —— 把"约束不可行时的越限"
        直接编码进期望值, 因此**无论如何都不会失败** (典型的"断言被污染的期望
        而非行为")。现改为: 只要上限在活跃资产上可行, 就必须真实满足。
        """
        n_assets = 3
        returns = self.rng.standard_normal((252, n_assets)) * 0.02 + 0.001
        allocator = RegimeAwareAllocator()
        result = allocator.allocate(returns, vix=40.0)
        max_w = allocator.config.max_weight[Regime.CRISIS]
        n_active = int(np.sum(result.weights > 1e-12))
        if max_w * n_active >= 1.0 - 1e-10:
            # 可行 → 必须满足上限
            assert np.max(result.weights) <= max_w + 1e-6
        else:
            # 不可行 → 只要求归一 + 落在"活跃等权"最接近解
            assert abs(result.weights.sum() - 1.0) < 1e-6
            assert np.max(result.weights) <= 1.0 / n_active + 1e-6

    def test_shrinkage(self):
        """收缩协方差。"""
        allocator = RegimeAwareAllocator(use_shrinkage=True)
        result = allocator.allocate(self.returns, vix=18.0)
        assert result.shrinkage_intensity >= 0

    def test_no_shrinkage(self):
        """禁用收缩。"""
        allocator = RegimeAwareAllocator(use_shrinkage=False)
        result = allocator.allocate(self.returns, vix=18.0)
        assert result.shrinkage_intensity == 0.0

    def test_batch(self):
        """批量分配。"""
        allocator = RegimeAwareAllocator()
        vix_series = np.array([10, 18, 25, 40])
        results = allocator.allocate_batch(self.returns, vix_series)
        assert len(results) == 4

    def test_stats(self):
        """统计信息。"""
        allocator = RegimeAwareAllocator()
        allocator.allocate(self.returns, vix=18.0)
        allocator.allocate(self.returns, vix=40.0)
        stats = allocator.get_stats()
        assert stats["total"] == 2

    def test_compare_regimes(self):
        """制度对比。"""
        allocator = RegimeAwareAllocator()
        comparison = allocator.compare_regimes(self.returns)
        assert len(comparison) == 4
        assert "crisis" in comparison


# ============================================================
# 端到端集成测试
# ============================================================


class TestEndToEnd:
    """端到端集成测试。"""

    def test_full_pipeline(self):
        """完整管道。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((252, 6)) * 0.02 + np.array(
            [0.001, 0.002, 0.0015, 0.0005, 0.0025, 0.0003]
        )

        allocator = RegimeAwareAllocator()
        defense_mask = np.array([0, 0, 0, 1, 0, 1])
        offense_mask = np.array([1, 1, 0, 0, 1, 0])

        for vix in [10, 18, 25, 40]:
            result = allocator.allocate(returns, vix, defense_mask, offense_mask)
            assert abs(result.weights.sum() - 1.0) < 1e-6
            assert np.all(result.weights >= -1e-6)

    def test_regime_aware_improves_risk_adjusted(self):
        """制度感知改善风险调整收益。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((500, 5)) * 0.02 + 0.001

        allocator = RegimeAwareAllocator()
        r_normal = allocator.allocate(returns, vix=18.0)
        r_crisis = allocator.allocate(returns, vix=40.0)

        # 危机时风险更低 (更保守)
        assert r_crisis.expected_risk <= r_normal.expected_risk + 1e-6


# ============================================================
# P2 修复回归: max_weight 约束真实生效 (Issue #13 巡检续批)
# ============================================================


class TestMaxWeightConstraintRegression:
    """最大权重约束回归 — 覆盖修复前两处静默失效。

    修复前实测缺陷 (基线: 24 个制度×资产数组合中 9 个"可行却越限"):
    1. ``max_w * n < 1`` 时静默 ``return np.ones(n)/n``, 等权本身越限;
    2. ``max_w >= 1.0/n`` 时早退 ``return weights``, 高度集中的权重原样通过;
    3. 迭代裁剪时仅按 n 而非**活跃资产数**判可行, 当活跃数不足时
       "钉上限 + 重归一化" 再次越限 (n=8/max_w=0.15/4 活跃 → 0.2333)。
    """

    @staticmethod
    def _positive_count(w):
        return int(np.sum(np.asarray(w) > 1e-12))

    def test_cap_enforced_when_feasible_across_grid(self):
        """网格: 只要上限在活跃资产上可行, 实测最大权重必须 ≤ 上限。"""
        rng = np.random.default_rng(7)
        allocator = RegimeAwareAllocator()
        checked = 0
        for vix in (12.0, 18.0, 25.0, 40.0):
            for n in (4, 6, 8, 10, 15, 20):
                returns = rng.standard_normal((252, n)) * 0.02 + 0.001
                r = allocator.allocate(returns, vix=vix)
                mw = allocator.config.max_weight[r.regime]
                n_active = self._positive_count(r.weights)
                assert abs(r.weights.sum() - 1.0) < 1e-6
                if mw * n_active >= 1.0 - 1e-10:
                    assert r.weights.max() <= mw + 1e-6, (
                        f"制度 {r.regime.value} n={n} 活跃={n_active} "
                        f"上限={mw} 实测={r.weights.max():.4f}"
                    )
                    checked += 1
        assert checked >= 12, "可行场景样本过少, 回归未真正覆盖"

    def test_concentrated_weights_are_trimmed_not_passed_through(self):
        """早退分支回归: max_w >= 1/n 时也必须裁剪, 而非原样返回。

        修复前: n=5, max_w=0.25, 权重 [0.9, 0.025*4] 原样返回 (0.9 越限 3.6×)。
        """
        w = np.array([0.9, 0.025, 0.025, 0.025, 0.025])
        out = RegimeAwareAllocator._apply_max_weight(w, 0.25)
        assert out.max() <= 0.25 + 1e-6, f"集中权重未被裁剪: {out}"
        assert abs(out.sum() - 1.0) < 1e-6

    def test_infeasible_cap_returns_closest_solution_not_silent(self, caplog):
        """不可行时返回最接近解 + 显式 WARNING, 不静默越限。"""
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="regime_aware_allocator"):
            out = RegimeAwareAllocator._apply_max_weight(np.ones(4) / 4, 0.05)
        # 4 × 0.05 = 0.2 < 1 → 不可行
        assert abs(out.sum() - 1.0) < 1e-6
        assert np.allclose(out, 0.25)
        assert any("不可行" in rec.message for rec in caplog.records), (
            "不可行场景必须显式告警, 不得静默"
        )

    def test_active_set_feasibility_not_total_count(self):
        """活跃资产数不足时, 不得"钉上限后重归一化"再次越限。

        修复前实证: n=8, max_w=0.15, 4 个活跃资产 → 结果 [0.2333, 0.15, 0.2333, ...]
        (0.2333 > 0.15)。修复后应为活跃等权 0.25 或真实满足上限。
        """
        w = np.array([0.2, 0.2, 0.35, 0.0, 0.25, 0.0, 0.0, 0.0])
        out = RegimeAwareAllocator._apply_max_weight(w, 0.15)
        n_active = self._positive_count(out)
        assert abs(out.sum() - 1.0) < 1e-6
        # 活跃 4 个 × 0.15 = 0.6 < 1 不可行 → 允许等权 0.25, 但不允许例如 0.2333 这类
        # "钉上限+重归一化"的非等权越限混合解
        if out.max() > 0.15 + 1e-6:
            assert np.allclose(out[out > 1e-12], 1.0 / n_active), (
                f"不可行时必须是活跃等权, 实测 {out}"
            )

    def test_no_silent_violation_in_allocate_output(self, caplog):
        """端到端: allocate 输出若越限, 必须伴随 WARNING (无静默越限)。"""
        import logging as _logging

        rng = np.random.default_rng(11)
        allocator = RegimeAwareAllocator()
        with caplog.at_level(_logging.WARNING, logger="regime_aware_allocator"):
            for n in (4, 6, 8, 10):
                returns = rng.standard_normal((252, n)) * 0.02 + 0.001
                r = allocator.allocate(returns, vix=40.0)
                mw = allocator.config.max_weight[r.regime]
                if r.weights.max() > mw + 1e-6:
                    assert any("不可行" in rec.message for rec in caplog.records), (
                        f"n={n} 越限却无告警 (静默失效)"
                    )

    def test_weights_always_normalized_and_non_negative(self):
        """回归不变量: 归一 + 非负, 任意上限下成立。"""
        rng = np.random.default_rng(3)
        for mw in (0.02, 0.05, 0.15, 0.25, 0.4, 0.9):
            for n in (1, 3, 7, 12):
                w = np.abs(rng.standard_normal(n))
                out = RegimeAwareAllocator._apply_max_weight(w, mw)
                assert abs(out.sum() - 1.0) < 1e-6
                assert np.all(out >= -1e-12)
                assert len(out) == n

    def test_zero_weights_fall_back_to_equal(self):
        """全零权重 → 等权回退 (不产生 NaN/零和)。"""
        out = RegimeAwareAllocator._apply_max_weight(np.zeros(5), 0.3)
        assert abs(out.sum() - 1.0) < 1e-6
        assert np.allclose(out, 0.2)

    def test_non_finite_cap_is_skipped_with_warning(self, caplog):
        """非有限上限 → 跳过裁剪 + 告警, 不抛异常。"""
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="regime_aware_allocator"):
            out = RegimeAwareAllocator._apply_max_weight(np.ones(3) / 3, float("nan"))
        assert np.allclose(out, 1.0 / 3)
        assert any("非有限" in rec.message for rec in caplog.records)


    def test_shrinkage_stabilizes(self):
        """收缩稳定化协方差估计。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((50, 10)) * 0.02

        cov_raw = np.cov(returns, rowvar=False)
        cov_shrunk, _ = CovarianceShrinkage.ledoit_wolf(returns)

        cond_raw = np.linalg.cond(cov_raw)
        cond_shrunk = np.linalg.cond(cov_shrunk)
        # 收缩后条件数通常更小 (更稳定)
        assert cond_shrunk <= cond_raw * 1.1
