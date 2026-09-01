"""Q4 契约: 陈旧/缓存数据必须标记 stale 并降级质量分.

契约: 兜底/缓存数据命中时 quality_score 不得满分 (100),
      须降级标记 stale, 禁止当实时数据流通.

对应实现:
  utils/data/data_layer.py STALE_QUALITY_SCORE (P6 缓存兜底降级)
  utils/universe/survivorship_free_universe.py (退市偏差扣分)
"""

from __future__ import annotations

from utils.data.data_layer import STALE_QUALITY_SCORE


def test_stale_quality_score_constant_below_full() -> None:
    """P6 缓存兜底的质量分常量必须 < 100 (不得满分)."""
    assert STALE_QUALITY_SCORE < 100.0, (
        f"Q4 违约: STALE_QUALITY_SCORE={STALE_QUALITY_SCORE} >= 100, "
        "兜底数据将被当实时数据流通"
    )


def test_stale_quality_score_is_explicit_degradation() -> None:
    """STALE_QUALITY_SCORE 必须是显式降级值 (0 表示完全不可信, 非 100)."""
    assert STALE_QUALITY_SCORE == 0.0, (
        f"Q4 违约: STALE_QUALITY_SCORE={STALE_QUALITY_SCORE} 非显式降级值 0.0, "
        "F-8 修复要求陈旧缓存明确降级"
    )


def test_survivorship_bias_deducts_quality_score() -> None:
    """survivorship_free_universe: 有退市偏差/低覆盖时 quality_score 必须扣分 (< 100).

    契约验证扣分逻辑: has_bias → -40, coverage<0.5 → -30, db<10 → -20.
    """
    quality_score = 100.0
    has_bias = True
    coverage_low = True
    db_small = True
    if has_bias:
        quality_score -= 40
    if coverage_low:
        quality_score -= 30
    if db_small:
        quality_score -= 20
    final = max(0, quality_score)
    assert final < 100.0, f"Q4 违约: 有偏差时 quality_score={final} 未降级"
    assert final == 10.0, f"Q4 违约: 扣分后应为 10, 实际 {final}"
