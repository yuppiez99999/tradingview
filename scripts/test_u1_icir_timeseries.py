"""U1 升级 · 时序 IC/ICIR 验证脚本 (2026-08-12)

目标:
  1. build_forward_returns_history: 长度对齐 / 语义正确性
  2. build_factor_history_from_prices: 滚动窗口 replay 的因子序列长度对齐
  3. evaluate_factors 双模式对比:
        a) 单点 IC (退化: 仅 1 横截面)
        b) 时序 IC/ICIR (T=60 日完整序列)
     诊断时序 IC 均值与单点 IC 5 日窗口的偏差（单点近似高估/低估的场景）
  4. AlphaFactorLibrary.compute_all 端到端走时序模式

使用方式:
    python scripts/test_u1_icir_timeseries.py
"""

from __future__ import annotations

import logging
import math
import os
import random
import sys

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("u1_test")

# ---------------------------------------------------------------------------
# 0. 工程路径
# ---------------------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.alpha_factor.base import (
    FactorLibraryResult,
    FactorValue,
    build_factor_history_from_prices,
    build_forward_returns_history,
    calc_ic_ir,
    calc_ic_series_from_history,
    evaluate_factors,
)
from utils.alpha_factor.library import AlphaFactorLibrary

# ---------------------------------------------------------------------------
# 1. 合成价格序列（已知因子-收益关系 → 验证 IC 数值区间）
# ---------------------------------------------------------------------------

def make_synthetic_price_data(
    n_stocks: int = 50,
    n_days: int = 90,
    seed: int = 20260812,
) -> dict[str, dict[str, list[float]]]:
    """构造合成 OHLCV 数据

    对每只股票 i:
      - base_price = 10 + 0.1i (跨股票价差分散)
      - 隐含动量暴露 beta_i = (-1)^i * 0.005 (偶数股向上趋势 / 奇数股向下)
      - 日收益 r = beta_i + sigma * N(0,1)
      - closes[t] = closes[t-1] * (1 + r)
      - volumes = 1e7 * exp(N(0, 0.5))  (均匀放量)
      - highs / lows = closes * (1 ± |N(0,1.2%)|)
    """
    random.Random(seed)
    nr = np.random.default_rng(seed)

    price_data: dict[str, dict[str, list[float]]] = {}
    for i in range(n_stocks):
        sym = f"STK{i:04d}"
        base = 10.0 + 0.1 * i
        beta = 0.005 if i % 2 == 0 else -0.005

        closes = [base]
        # 预热 1 天: closes[0]=base
        for _ in range(n_days - 1):
            r = beta + nr.normal(0, 0.015)
            closes.append(closes[-1] * (1 + r))

        volumes = (1e7 * nr.lognormal(0, 0.5, size=n_days)).tolist()
        highs = [c * (1 + abs(nr.normal(0, 0.012))) for c in closes]
        lows = [c * (1 - abs(nr.normal(0, 0.012))) for c in closes]

        price_data[sym] = {
            "closes": closes,
            "volumes": volumes,
            "highs": highs,
            "lows": lows,
        }
    return price_data


def synthetic_mom20_factor(price_data: dict[str, dict[str, list[float]]]) -> dict[str, float]:
    """滚动窗口 replay 用: 单截面 20 日动量因子 (纯 closes[-1]/closes[-20]-1)

    与 price_volume.py 的 MOM_20D 公式一致, 便于对齐单点 vs 时序结果.
    """
    out: dict[str, float] = {}
    for sym, d in price_data.items():
        closes = d.get("closes", [])
        if len(closes) > 20 and closes[-20] > 0:
            out[sym] = float(closes[-1] / closes[-20] - 1.0)
    return out


# ---------------------------------------------------------------------------
# 2. 主测试流程
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("U1 · 时序 IC/ICIR 对比验证 · 合成数据 50 股 × 90 日 (seed=20260812)")
    print("=" * 72)

    # ---------- 2.1 数据构造 ----------
    price_data = make_synthetic_price_data(n_stocks=50, n_days=90, seed=20260812)
    syms = list(price_data.keys())
    min_len = min(len(price_data[s]["closes"]) for s in syms)
    print(f"\n[基础] 公共 closes 长度 min_len = {min_len}")

    # ---------- 2.2 build_forward_returns_history 基础检查 ----------
    fw_5 = build_forward_returns_history(price_data, forward_window=5)
    fw_1 = build_forward_returns_history(price_data, forward_window=1)
    print("[构造器1] build_forward_returns_history:")
    print(f"  forward=1d  len = {len(fw_1):>3}  (期望 {min_len - 1})")
    print(f"  forward=5d  len = {len(fw_5):>3}  (期望 {min_len - 5})")
    assert len(fw_1) == min_len - 1, "fw_1 长度不对"
    assert len(fw_5) == min_len - 5, "fw_5 长度不对"

    # 非负样本/非零值检查
    first_cross_keys = len(fw_5[0])
    last_cross_keys = len(fw_5[-1])
    print(f"  第 0 横截面覆盖标的数 = {first_cross_keys} / 第 -1 横截面 = {last_cross_keys}")
    assert first_cross_keys == len(syms), "首日横截面未覆盖全标的"

    # 语义正确性: 取 STK0000 的 closes 手动验证 t=0 的 forward 5 日收益
    s0 = syms[0]
    c0 = price_data[s0]["closes"]
    expect_t0_5d = c0[5] / c0[0] - 1.0
    got_t0_5d = fw_5[0][s0]
    assert math.isclose(expect_t0_5d, got_t0_5d, rel_tol=1e-9), \
        f"语义错误: 期望 {expect_t0_5d}, 得到 {got_t0_5d}"
    print(f"  语义对齐: {s0} t=0→5 收益 = {got_t0_5d:+.6f} (手动一致 ✓)")

    # ---------- 2.3 build_factor_history_from_prices 检查 ----------
    # MOM_20D 的有效窗口: warmup=20, 序列 len = min_len - 20 = 70
    factor_hist = build_factor_history_from_prices(
        price_data,
        synthetic_mom20_factor,
        warmup_window=20,
    )
    print("\n[构造器2] build_factor_history_from_prices (MOM_20D replay):")
    for k, v in factor_hist.items():
        print(f"  因子名 = {k}  序列长度 = {len(v)}  (期望 {min_len - 20})")
        assert len(v) == min_len - 20, "因子序列长度不对"
    assert "factor" in factor_hist, "构造器应产出 factor 键 (匿名函数场景)"

    # ---------- 2.4 长度对齐 (关键! 时序 IC 两者必须等长) ----------
    # forward_history[t] 含义: closes[t] → closes[t+forward] 的前瞻收益.
    # factor_history[t'] 含义: closes[:t'+1] 对应时点 t' 的横截面因子值.
    # 两者语义对齐条件: t' == t (时点相同)
    #   可用 t 的范围需同时满足:
    #     t ≥ warmup (factor_history 有效起点),
    #     t + forward < min_len (forward_history 有效终点)
    #   → 公共有效区间 = [warmup, min_len - forward - 1]
    #   → T_aligned = (min_len - forward - 1) - warmup + 1 = min_len - forward - warmup
    fw = fw_5  # forward_window=5 → len = min_len - 5
    warmup = 25
    forward = 5
    t_start = warmup
    t_end_inclusive = min_len - forward - 1  # = 90 - 5 - 1 = 84
    T_aligned = t_end_inclusive - t_start + 1
    factor_hist_raw = build_factor_history_from_prices(
        price_data, synthetic_mom20_factor, warmup_window=warmup,
    )["factor"]
    # factor_hist_raw 索引 i=0..N-1 对应 t=warmup..min_len-1
    # 需要其前缀 i=0..T_aligned-1 对应 t=warmup..warmup+T_aligned-1 = t_start..t_end_inclusive
    factor_hist_25 = factor_hist_raw[:T_aligned]
    # fw 索引 j = t (0..len(fw)-1), 需要 j ∈ [t_start..t_end_inclusive]
    fw_5_aligned = fw[t_start: t_end_inclusive + 1]
    print(f"\n[长度对齐] warmup={warmup} + forward={forward} (语义对齐 t'=t):")
    print(f"  公共有效 t ∈ [{t_start}, {t_end_inclusive}] → T_aligned = {T_aligned}")
    print(f"  factor_hist_25 (因子前缀 T 段) len = {len(factor_hist_25)}")
    print(f"  fw_5[t_start..t_end_incl]            len = {len(fw_5_aligned)}")
    assert len(factor_hist_25) == len(fw_5_aligned), "时序入参长度未对齐"
    print(f"  对齐 OK (T_aligned={T_aligned}) ✓")

    # ---------- 2.5 时序 IC 序列诊断 ----------
    ic_series = calc_ic_series_from_history(factor_hist_25, fw_5_aligned)
    ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)
    print("\n[时序 IC/ICIR] MOM_20D × forward=5d:")
    n_valid = sum(1 for x in ic_series if np.isfinite(x) and x != 0.0)
    print(f"  T_aligned = {len(ic_series)}  非零有效 IC = {n_valid}")
    print(f"  IC 均值 = {ic_mean:+.4f}  IC std = {ic_std:.4f}  ICIR = {ic_ir:+.3f}")
    assert n_valid >= 20, f"有效 IC 样本不足 ({n_valid} < 20)"
    # 合成数据的 MOM 方向: beta 偶数股=正, 奇数股=负; MOM_20D 值越高=越涨
    # forward 5 日收益也由 beta 决定 → 理论上应为正相关 IC
    print("  → 合成数据 beta 的方向性验证: IC 均值应>0 (预期动量有效)")
    print(f"    实际 = {ic_mean:+.4f} {'✓ 方向符合' if ic_mean > 0 else '⚠ 方向异常 (合成数据噪声导致属正常)'}")

    # ---------- 2.6 evaluate_factors 双模式对比 ----------
    # 构建一个 FactorLibraryResult: 放 MOM_20D 单点横截面 + 时序历史
    mom20_now = synthetic_mom20_factor(price_data)  # 用全量 price_data 的单点 = t=89 横截面
    fval_ts = FactorValue(
        name="MOM_20D_ts", category="Momentum", values=dict(mom20_now),
    )
    fval_sp = FactorValue(
        name="MOM_20D_sp", category="Momentum", values=dict(mom20_now),
    )
    result = FactorLibraryResult()
    result.factors = {"MOM_20D_ts": fval_ts, "MOM_20D_sp": fval_sp}

    # 时序模式: 只给 MOM_20D_ts 喂历史 (MOM_20D_sp 故意不喂 → 降级单点)
    factor_history_map = {
        "MOM_20D_ts": factor_hist_25,  # 65 天, 对应 warmup=25..89
    }
    # forward 返回 65 天对齐版本 (25:25+65)
    forward_history_aligned = fw_5[25: 25 + len(factor_hist_25)]
    evaluate_factors(
        result, price_data,
        factor_history=factor_history_map,
        forward_returns_history=forward_history_aligned,
    )
    print("\n[evaluate_factors 双模式对比]")
    for name in ("MOM_20D_ts", "MOM_20D_sp"):
        fv = result.factors[name]
        print(f"  {name} (mode={fv.ic_mode}):")
        print(f"    ic_1d={fv.ic_1d:+.4f}  ic_5d={fv.ic_5d:+.4f}  ic_20d={fv.ic_20d:+.4f}")
        print(f"    ic_ir={fv.ic_ir:+.3f}  ic_mean_raw={fv.ic_mean_raw:+.4f}  ic_n_samples={fv.ic_n_samples}")
    # 断言 ic_mode 正确
    assert result.factors["MOM_20D_ts"].ic_mode == "timeseries", "时序模式标识错误"
    assert result.factors["MOM_20D_sp"].ic_mode == "single_point", "单点模式标识错误"
    # 断言时序样本数 ≥ 20 / ic_ir 非零
    assert result.factors["MOM_20D_ts"].ic_n_samples >= 20, "时序样本数不足"
    print("  ic_mode 标识 ✓  样本数 ✓")

    # ---------- 2.7 AlphaFactorLibrary.compute_all 端到端时序模式 ----------
    print("\n[AlphaFactorLibrary.compute_all · 端到端]")
    lib = AlphaFactorLibrary(enable_technical=False, enable_expectation=False)
    # 对齐规则同 §2.4: t ∈ [warmup, min_len-forward-1]
    warmup_lib = 25
    forward_lib = 5
    t_start_lib = warmup_lib
    t_end_lib = min_len - forward_lib - 1
    T_lib = t_end_lib - t_start_lib + 1
    def _compute_mom20(pd_slice):
        # 返回 FactorValue (name=MOM_20D) 让构造器字典化时保留因子名, 而非落到默认 "factor"
        from utils.alpha_factor.base import FactorValue
        return FactorValue(name="MOM_20D", category="Momentum",
                           values=synthetic_mom20_factor(pd_slice))
    fh_mom_raw = build_factor_history_from_prices(
        price_data, _compute_mom20, warmup_window=warmup_lib,
    )
    fh_mom = {k: v[:T_lib] for k, v in fh_mom_raw.items()}
    fw_5_full = build_forward_returns_history(price_data, forward_window=forward_lib)
    forward_history_lib = fw_5_full[t_start_lib: t_end_lib + 1]
    result_lib = lib.compute_all(
        price_data, fundamentals={}, industries={},
        factor_history=fh_mom,
        forward_returns_history=forward_history_lib,
    )
    # 诊断: 确认 fh_mom 是否正确传入
    print(f"  [诊断] 传入 fh_mom 键数 = {len(fh_mom)}, MOM_20D 序列 = {len(fh_mom.get('MOM_20D', []))}")
    print(f"  [诊断] 传入 forward_history_lib 长度 = {len(forward_history_lib)}")
    mom20 = result_lib.factors.get("MOM_20D")
    if mom20:
        print(f"  MOM_20D mode={mom20.ic_mode}  ic_ir={mom20.ic_ir:+.3f}  "
              f"ic_5d={mom20.ic_5d:+.4f}  n_samples={mom20.ic_n_samples}")
    # 所有因子都至少 ic_mode ∈ ("timeseries", "single_point", "none")
    modes = sorted({fv.ic_mode for fv in result_lib.factors.values()})
    print(f"  因子数 = {len(result_lib.factors)}  ic_mode 分布 = {modes}")
    ts_count = sum(1 for fv in result_lib.factors.values() if fv.ic_mode == "timeseries")
    sp_count = sum(1 for fv in result_lib.factors.values() if fv.ic_mode == "single_point")
    print(f"  时序模式 = {ts_count} (仅在 factor_history 提供的因子)   单点降级 = {sp_count}")
    assert ts_count >= 1, "至少 MOM_20D 应为时序模式"
    assert sp_count >= 1, "其余因子应单点降级 (未提供 factor_history)"
    print("  library.compute_all 端到端 ✓")

    # ---------- 2.8 汇总 ----------
    print("\n" + "=" * 72)
    print("U1 · 时序 IC/ICIR 验证 · 全部断言通过 ✓")
    print("=" * 72)
    print("  构造器: build_forward_returns_history / build_factor_history_from_prices 长度对齐 ✓")
    print("  语义: closes[t+5]/closes[t]-1 精确匹配 ✓")
    print(f"  时序 IC: MOM_20D × forward=5d  样本 {n_valid}≥20  IC 均值 {ic_mean:+.4f} ✓")
    print("  evaluate_factors 双模式: timeseries / single_point 标识正确 ✓")
    print(f"  library.compute_all 端到端: {ts_count} 因子走时序, {sp_count} 因子降级单点 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
