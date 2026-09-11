"""`utils.universe.portfolio_builder.apply_risk_constraints` 风险约束回归 (P2-1).

背景 (2026-09-11 代码质量与系统Bug审查 §P2-1)
--------------------------------------------
原实现顺序为「砍帽 → 全局归一化」, 归一化会把刚压到上限的权重**重新抬超**上限:
实测 smoke 3/3/4 输入下每只被抬到 10% (2× 单股上限 5%)、超限行业 40% (1.6× 上限 25%)。
`apply_risk_constraints` 此前**无任何测试覆盖**。

修复 (2026-09-11)
-----------------
① 层内迭代收敛 (`_redistribute_within_layer`): 层总权重 = 层配比, 且每只 ≤ 单股上限;
② 行业上限作用于全组合且优先 (`_enforce_industry_cap`);
③ 不可行/未收敛显式 WARNING, 不静默放行。
"""

from __future__ import annotations

import pytest

from utils.universe.portfolio_builder import (
    Holding,
    LayeredPortfolio,
    PortfolioConfig,
    apply_risk_constraints,
)


def _build(counts: tuple[int, int, int], n_industries: int) -> LayeredPortfolio:
    holdings: list[Holding] = []
    i = 0
    for layer, cnt in zip(("short", "mid", "long"), counts, strict=True):
        for _ in range(cnt):
            holdings.append(
                Holding(
                    symbol=f"S{i}",
                    name=f"N{i}",
                    industry=f"I{i % n_industries}",
                    layer=layer,
                )
            )
            i += 1
    return LayeredPortfolio(holdings=holdings)


def _max_industry(p: LayeredPortfolio) -> float:
    totals: dict[str, float] = {}
    for h in p.holdings:
        totals[h.industry] = totals.get(h.industry, 0.0) + h.weight
    return max(totals.values()) if totals else 0.0


def _layer_totals(p: LayeredPortfolio) -> dict[str, float]:
    totals: dict[str, float] = {}
    for h in p.holdings:
        totals[h.layer] = totals.get(h.layer, 0.0) + h.weight
    return totals


def _smoke_config() -> PortfolioConfig:
    cfg = PortfolioConfig()
    cfg.short_count, cfg.mid_count, cfg.long_count = 3, 3, 4
    return cfg


# ============================================================
# P2-1 核心: 归一化后上限必须仍然成立
# ============================================================
class TestConstraintsHoldAfterNormalization:
    """修复前必红: 归一化把权重抬超上限."""

    def test_smoke_single_position_cap_respected(self):
        """smoke 3/3/4: 修复前实测 max=0.10 (2× 上限), 修复后必须 ≤ 5%."""
        p = _build((3, 3, 4), n_industries=3)
        apply_risk_constraints(p, _smoke_config())
        assert max(h.weight for h in p.holdings) <= 0.05 + 1e-9

    def test_smoke_industry_cap_respected(self):
        """smoke 3/3/4: 修复前实测行业 0.40 (1.6× 上限), 修复后必须 ≤ 25%."""
        p = _build((3, 3, 4), n_industries=3)
        apply_risk_constraints(p, _smoke_config())
        assert _max_industry(p) <= 0.25 + 1e-9

    def test_full_config_single_cap_respected(self):
        """满配 20/30/50: 单股上限同样必须成立."""
        p = _build((20, 30, 50), n_industries=10)
        apply_risk_constraints(p, PortfolioConfig())
        assert max(h.weight for h in p.holdings) <= 0.05 + 1e-9

    def test_full_config_industry_cap_respected(self):
        p = _build((20, 30, 50), n_industries=10)
        apply_risk_constraints(p, PortfolioConfig())
        assert _max_industry(p) <= 0.25 + 1e-9


# ============================================================
# 层权重配比: 修复前被全局归一化抹平
# ============================================================
class TestLayerWeightsPreserved:
    def test_layer_ratios_preserved_when_feasible(self):
        """层配比 0.20/0.30/0.50 在可行时必须保持 (修复前被抹为按只数等权)."""
        cfg = PortfolioConfig()
        p = _build((20, 30, 50), n_industries=10)
        apply_risk_constraints(p, cfg)
        lay = _layer_totals(p)
        assert lay["short"] == pytest.approx(0.20, abs=1e-6)
        assert lay["mid"] == pytest.approx(0.30, abs=1e-6)
        assert lay["long"] == pytest.approx(0.50, abs=1e-6)

    def test_total_weight_is_one_when_feasible(self):
        p = _build((20, 30, 50), n_industries=10)
        apply_risk_constraints(p, PortfolioConfig())
        assert sum(h.weight for h in p.holdings) == pytest.approx(1.0, abs=1e-6)


# ============================================================
# 不可行场景: 不得静默放行
# ============================================================
class TestInfeasibleNotSilent:
    def test_layer_infeasible_gives_cap_not_inflation(self):
        """层内 n × cap < 层权重 -> 给满上限 (不超), 而非抬超上限凑满."""
        cfg = _smoke_config()
        p = _build((3, 3, 4), n_industries=3)
        apply_risk_constraints(p, cfg)
        assert max(h.weight for h in p.holdings) <= cfg.max_single_position + 1e-9
        assert sum(h.weight for h in p.holdings) < 1.0  # 未满配是预期

    def test_concentrated_industry_cap_priority(self):
        """行业高度集中时, 行业上限优先于总权重凑满 (风控铁律)."""
        p = _build((20, 30, 50), n_industries=1)  # 全部同一行业
        apply_risk_constraints(p, PortfolioConfig())
        assert _max_industry(p) <= 0.25 + 1e-9
        assert sum(h.weight for h in p.holdings) == pytest.approx(0.25, abs=1e-6)

    def test_empty_portfolio_no_crash(self):
        p = LayeredPortfolio(holdings=[])
        apply_risk_constraints(p, PortfolioConfig())  # 不得抛异常
        assert p.holdings == []

    def test_zero_cap_no_crash(self):
        cfg = PortfolioConfig()
        cfg.max_single_position = 0.0
        p = _build((3, 3, 4), n_industries=3)
        apply_risk_constraints(p, cfg)  # 上限为 0 -> 直接返回, 不除零
        assert all(h.weight >= 0 for h in p.holdings)
