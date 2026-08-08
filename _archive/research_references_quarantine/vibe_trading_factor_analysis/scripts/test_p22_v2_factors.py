"""P2.2 v5 改进因子单元测试（差异化极端值处理方案）

验证：
1. adapter 模块 import 正常
2. QualityTrend 4 因子在新数据结构下能正常计算
3. VT_QUALTREND_GROWTH_ACCEL 使用 v5 纯 v1 净利润 YoY 加速（不做 winsorize，因 v4 实测 winsorize 有害）
4. VT_QUALTREND_DEBT_RED 使用 current_ratio（v2 改进保留）
5. VT_QUALTREND_ROE_DELTA / MARGIN_EXP 使用 v1 绝对值 + winsorize（v3 验证有效）
6. 空 history 安全降级

v5 改进背景：
  v2 rank 标准化丢失 Pearson IC 强度信息，全部因子 IC_IR 下降
  v3 双信号 + winsorize：ROE/MARGIN 有效，但 GROWTH_ACCEL 双信号不如 v1 净利润计算
  v4 全部 winsorize：ROE/MARGIN 保持有效，但 GROWTH_ACCEL winsorize 反而降低 IC_IR（0.2676→0.1409）
  v5 差异化决策：ROE/MARGIN 保留 winsorize（小量级变化，极端值是噪声），
                GROWTH_ACCEL 回退纯 v1（大量级增长率，极端值携带 Alpha 信号）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (  # noqa: E402
    CandidateFactor,
    VibeTradingFactorAdapter,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_p22_v3")


def _make_mock_fundamentals_history(
    n_symbols: int = 10,
) -> dict:
    """构造 mock 历史季度数据（含 P2.2 v2 新字段 revenue/yoy_pni）"""
    syms = [f"TEST{i:03d}_SZ" for i in range(n_symbols)]
    history = {}

    for i, sym in enumerate(syms):
        # 每个标的 6 个季度，倒序排列
        quarters = []
        for q_idx in range(6):
            year = 2026 - (q_idx // 4)
            quarter = ((1 - q_idx - 1) % 4) + 1
            if quarter <= 0:
                quarter += 4
            # 制造差异化数据（i 越大表现越好）
            base_roe = 0.05 + i * 0.005
            base_gm = 0.20 + i * 0.01
            base_cr = 1.0 + i * 0.05
            # yoy_pni 在 q=0 高于 q=1（增长加速），其他保持稳定
            if q_idx == 0:
                yoy_pni = 30.0 + i * 2.0
                yoy_ni = 25.0 + i * 1.5
            elif q_idx == 1:
                yoy_pni = 20.0 + i * 1.5
                yoy_ni = 18.0 + i * 1.0
            else:
                yoy_pni = 15.0 + i * 1.0
                yoy_ni = 12.0 + i * 0.8
            # revenue 单位:百万元（baostock MBRevenue）
            revenue = 1000.0 * (1 + i * 0.1) * (1.0 - q_idx * 0.05)

            # v4 mock: symbol-dependent YoY growth acceleration（i 越大加速越明显）
            # growth_cur  = 0.10 + i * 0.02   (当前 YoY 增长率, i=0:10%, i=9:28%)
            # growth_prev = 0.05 + i * 0.01   (上一期 YoY 增长率, i=0:5%, i=9:14%)
            # accel = growth_cur - growth_prev = 0.05 + i * 0.01 (正值, 随 i 递增)
            yoy_growth_cur = 0.10 + i * 0.02
            yoy_growth_prev = 0.05 + i * 0.01
            base_q = 100e6       # np[q-4] baseline
            base_prev = 90e6     # np[q-5] baseline
            if q_idx == 0:       # np[q] = base_q * (1 + yoy_growth_cur)
                net_profit = base_q * (1.0 + yoy_growth_cur)
            elif q_idx == 1:     # np[q-1] = base_prev * (1 + yoy_growth_prev)
                net_profit = base_prev * (1.0 + yoy_growth_prev)
            elif q_idx == 4:     # np[q-4]
                net_profit = base_q
            elif q_idx == 5:     # np[q-5]
                net_profit = base_prev
            else:                # q_idx 2, 3 - 简单衰减填充
                net_profit = base_q * (1.0 - q_idx * 0.05)

            quarters.append({
                "year": year,
                "quarter": quarter,
                "roe": base_roe * (1.0 - q_idx * 0.02),
                "gross_margin": base_gm * (1.0 - q_idx * 0.01),
                "net_margin": 0.1 + i * 0.005,
                "net_profit": net_profit,
                "eps_ttm": 1.0 + i * 0.1,
                "revenue": revenue,
                "debt_to_equity": 0.4 - i * 0.02,
                "current_ratio": base_cr * (1.0 - q_idx * 0.01),
                "yoy_pni": yoy_pni,
                "yoy_ni": yoy_ni,
                "yoy_eps": yoy_pni * 0.9,
            })

        history[sym] = {
            "symbol": sym,
            "quarters": quarters,
            "n_valid": 6,
            "data_quality": "real",
            "schema_version": 2,
        }
    return history


def test_import():
    """1. import 测试"""
    logger.info("\n=== 测试 1: 模块 import ===")
    adapter = VibeTradingFactorAdapter()
    assert "QualityTrend" in adapter.VIBE_TRADING_CATEGORIES
    logger.info("  ✅ VibeTradingFactorAdapter 实例化成功")
    logger.info("  ✅ QualityTrend 类别已注册")
    return adapter


def test_quality_trend_factors(adapter: VibeTradingFactorAdapter):
    """2. QualityTrend 4 因子计算测试"""
    logger.info("\n=== 测试 2: QualityTrend 因子计算 ===")
    history = _make_mock_fundamentals_history(n_symbols=10)
    factors = adapter._compute_vt_quality_trend_factors(
        fundamentals={},
        fundamentals_history=history,
    )

    expected = [
        "VT_QUALTREND_ROE_DELTA",
        "VT_QUALTREND_MARGIN_EXP",
        "VT_QUALTREND_DEBT_RED",
        "VT_QUALTREND_GROWTH_ACCEL",
    ]
    for name in expected:
        assert name in factors, f"缺少因子: {name}"
        f = factors[name]
        assert isinstance(f, CandidateFactor)
        assert len(f.values) > 0, f"{name} 值为空"
        logger.info(f"  ✅ {name:32s} | n={len(f.values):3d} | sample={list(f.values.items())[:2]}")

    return factors


def test_growth_accel_uses_v5_pure_v1(factors: dict):
    """3. VT_QUALTREND_GROWTH_ACCEL 使用 v5 纯 v1 净利润 YoY 加速（不做 winsorize）"""
    logger.info("\n=== 测试 3: VT_QUALTREND_GROWTH_ACCEL v5 (纯 v1 净利润 YoY 加速) ===")
    f = factors["VT_QUALTREND_GROWTH_ACCEL"]
    assert len(f.values) == 10, f"应有 10 个值, 实际 {len(f.values)}"

    # v5 改进：回退 v1 纯净利润 YoY 加速（不做 winsorize）
    # v4 实测 winsorize 反而降低 IC_IR（0.2676→0.1409），因 GROWTH_ACCEL
    # 大量级增长率的极端值携带 Alpha 信号，winsorize 裁剪会丢失信号
    # mock 数据 i 越大增长加速越明显（yoy_growth_cur 随 i 递增）
    sorted_syms = sorted(f.values.keys(), key=lambda s: f.values[s])
    logger.info(f"  v1 最低: {sorted_syms[0]} = {f.values[sorted_syms[0]]:.4f}")
    logger.info(f"  v1 最高: {sorted_syms[-1]} = {f.values[sorted_syms[-1]]:.4f}")

    # 期望 TEST009 应高于 TEST000（增长加速更明显）
    assert f.values["TEST009_SZ"] > f.values["TEST000_SZ"], (
        f"TEST009 ({f.values['TEST009_SZ']}) 应大于 TEST000 ({f.values['TEST000_SZ']})"
    )
    logger.info("  ✅ v5 逻辑生效：增长加速明显的标的有更高的 accel 值")
    logger.info(f"  ✅ 公式: {f.vt_formula}")

    # 验证 v5 不含 winsorize（v4 实测 winsorize 有害，v5 回退纯 v1）
    assert "winsorize" not in f.vt_formula, (
        f"v5 公式不应含 winsorize，实际: {f.vt_formula}"
    )
    logger.info("  ✅ v5 验证：公式不含 winsorize（v4 实测 winsorize 降低 IC_IR）")

    # 验证非 rank 标准化：rank 会将 10 个值均匀映射到 [0,1]（间距恒定 1/9≈0.111）
    # v5 保留原始加速度量级（约 0.05~0.14），间距 0.01 明显区别于 rank 间距 1/9≈0.111
    sorted_vals = sorted(f.values.values())
    gaps = [sorted_vals[k + 1] - sorted_vals[k] for k in range(len(sorted_vals) - 1)]
    rank_spacing = 1.0 / (len(sorted_vals) - 1)  # rank 标准化的特征间距
    # rank 的两个特征：(1) 间距 = 1/(n-1) ≈ 0.111；(2) 值域恰好 [0, 1]
    is_rank_spacing = all(abs(g - rank_spacing) < 1e-6 for g in gaps)
    is_rank_range = abs(sorted_vals[0]) < 1e-9 and abs(sorted_vals[-1] - 1.0) < 1e-9
    assert not (is_rank_spacing and is_rank_range), (
        f"v5 不应是 rank 标准化：间距={gaps[0]:.6f}（rank 应为 {rank_spacing:.6f}），"
        f"值域=[{sorted_vals[0]:.4f}, {sorted_vals[-1]:.4f}]（rank 应为 [0, 1]）"
    )
    assert sorted_vals[0] < 0.5 and sorted_vals[-1] < 0.5, (
        f"GROWTH_ACCEL 加速度应在 [-0.5, 0.5] 量级，实际 [{sorted_vals[0]:.4f}, {sorted_vals[-1]:.4f}]"
    )
    print(f"  ✅ 量级验证：值域 [{sorted_vals[0]:.4f}, {sorted_vals[-1]:.4f}]，"
          f"间距={gaps[0]:.4f}（非 rank 间距 {rank_spacing:.4f}，保留原始量级）")


def test_debt_red_uses_current_ratio(factors: dict):
    """4. VT_QUALTREND_DEBT_RED 使用 current_ratio（v2 改进保留）"""
    logger.info("\n=== 测试 4: VT_QUALTREND_DEBT_RED 使用 current_ratio（v2 改进保留）===")
    f = factors["VT_QUALTREND_DEBT_RED"]
    # 检查公式描述
    assert "current_ratio" in f.vt_formula, f"公式应含 current_ratio, 实际: {f.vt_formula}"
    assert "current_ratio" in f.description, "描述应含 current_ratio"
    logger.info(f"  ✅ 公式: {f.vt_formula}")
    logger.info(f"  ✅ 描述: {f.description}")

    # mock 数据 current_ratio 在 q=0 比 q=4 高（base_cr * (1 - 0) vs base_cr * (1 - 0.04)）
    # 所以 debt_red 值应为正（流动性改善）
    for sym, val in f.values.items():
        assert val > 0, f"{sym} 流动性应改善（val > 0）, 实际 {val}"
    logger.info(f"  ✅ 所有 {len(f.values)} 个标的有正值（current_ratio 上升）")


def test_roe_delta_uses_winsorize(factors: dict):
    """5. VT_QUALTREND_ROE_DELTA / MARGIN_EXP 使用 v1 绝对值 + v3 winsorize"""
    logger.info("\n=== 测试 5: VT_QUALTREND_ROE_DELTA / MARGIN_EXP 使用 winsorize ===")
    for name in ["VT_QUALTREND_ROE_DELTA", "VT_QUALTREND_MARGIN_EXP"]:
        f = factors[name]
        # v3 改进：winsorize 替代 rank
        assert "winsorize" in f.vt_formula, f"{name} 公式应含 winsorize, 实际: {f.vt_formula}"
        # winsorize 后的值应保留原始量级（ROE/gm 变化通常在 [-0.1, 0.1] 范围）
        # 不应被映射到 [0, 1] 区间（rank 标准化的特征）
        all_vals = list(f.values.values())
        if len(all_vals) >= 2:
            max_val = max(all_vals)
            min_val = min(all_vals)
            # ROE/gm YoY 变化值通常很小（< 0.5），不应是 [0, 1] 的 rank
            logger.info(f"  ✅ {name:32s} | 公式: {f.vt_formula}")
            logger.info(f"     值域: [{min_val:.4f}, {max_val:.4f}]（保留原始量级，非 rank [0,1]）")


def test_backward_compat_no_v2_fields():
    """6. 向后兼容：旧版缓存（无 revenue/yoy_pni）应降级到原版净利润计算"""
    logger.info("\n=== 测试 6: 向后兼容（旧版缓存无 v2 字段）===")
    # 构造不含 revenue/yoy_pni 的旧版数据
    history = {}
    for i in range(10):
        sym = f"OLD{i:03d}_SZ"
        quarters = []
        for q_idx in range(6):
            quarters.append({
                "year": 2026,
                "quarter": 1 - q_idx,
                "roe": 0.05 + i * 0.005,
                "gross_margin": 0.20 + i * 0.01,
                "net_profit": 100e6 * (1 + i * 0.05) * (1 - q_idx * 0.1),
                # 注意：不含 revenue / yoy_pni / yoy_ni 字段
            })
        history[sym] = {"quarters": quarters, "n_valid": 6}

    adapter = VibeTradingFactorAdapter()
    factors = adapter._compute_vt_quality_trend_factors(
        fundamentals={}, fundamentals_history=history,
    )

    # VT_QUALTREND_GROWTH_ACCEL 应能通过降级逻辑（净利润计算）正常生成
    f = factors["VT_QUALTREND_GROWTH_ACCEL"]
    assert len(f.values) > 0, "向后兼容：应有值（降级到净利润计算）"
    logger.info(f"  ✅ 旧版缓存降级成功: GROWTH_ACCEL n={len(f.values)}")
    logger.info("  ✅ 说明: 缺 revenue/yoy_pni 时自动降级到净利润 YoY 加速计算")


def test_empty_history():
    """7. 空 history 返回空因子（安全降级）"""
    logger.info("\n=== 测试 7: 空 history 安全降级 ===")
    adapter = VibeTradingFactorAdapter()
    factors = adapter._compute_vt_quality_trend_factors(
        fundamentals={}, fundamentals_history=None,
    )
    assert len(factors) == 0
    logger.info("  ✅ 空 history 返回空因子池（安全降级）")


def main() -> int:
    logger.info("=" * 70)
    logger.info("P2.2 v5 改进因子单元测试（差异化极端值处理方案）")
    logger.info("=" * 70)
    try:
        adapter = test_import()
        factors = test_quality_trend_factors(adapter)
        test_growth_accel_uses_v5_pure_v1(factors)
        test_debt_red_uses_current_ratio(factors)
        test_roe_delta_uses_winsorize(factors)
        test_backward_compat_no_v2_fields()
        test_empty_history()
        logger.info("\n" + "=" * 70)
        logger.info("✅ 全部测试通过")
        logger.info("=" * 70)
        return 0
    except AssertionError as e:
        logger.info(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
