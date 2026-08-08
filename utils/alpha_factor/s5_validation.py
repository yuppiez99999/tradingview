"""S5 组合层面检验 — CHAIN_MOM_60D 边际夏普改善验证

S5 门禁标准 (cairn/alpha-factor-system.md §五):
    纳入生产组合后边际夏普改善 > 0.05

验证设计:
    基准组合 = MOM_60D 单因子 Top20%/Bottom20% 多空 (代表现有动量因子)
    增强组合 = (MOM_60D + direction * CHAIN_MOM_60D) 合成后 Top20%/Bottom20% 多空
    边际改善 = 增强组合年化夏普 - 基准组合年化夏普

    CHAIN_MOM_60D 是对 MOM_60D 正交化后的邻居增量因子 (direction=-1 反向)。
    基准 = 被正交化对象 (动量本身), 增强 = 动量 + 邻居增量, 直接验证边际价值。

    多期非重叠滚动窗口 (step=horizon), 每窗口期初 entry_idx 重算因子 (B1/B2 无前视),
    按因子值排序分档, 计算 [entry, T] 整期多空收益 → 年化夏普。

用法:
    python -m utils.alpha_factor.s5_validation
    python -m utils.alpha_factor.s5_validation --days 400 --horizon 20
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from utils.alpha_factor.gate1_validation import (
    fetch_prices,
    load_expanded_universe,
    _compute_momentum_factors,
)
from utils.alpha_factor.graph import compute_lead_lag_factors, orthogonalize_chain_factors
from utils.supply_chain_builder import SupplyChainBuilder

logger = logging.getLogger(__name__)


def _build_universe(days: int) -> Tuple[Dict[str, dict], List[str], Any, Dict[str, str]]:
    """构建 universe + 价格 + 图 + 行业标签 (复用 gate1 数据管线)."""
    symbols, industries = load_expanded_universe()
    logger.info(f"[1/4] universe: {len(symbols)} 只, 行业标签 {len(industries)} 个")
    price_data = fetch_prices(symbols, days=days)
    valid = [s for s in symbols if s in price_data and len(price_data[s].get("closes", [])) > 60]
    logger.info(f"[2/4] 有效价格: {len(valid)} 只")
    builder = SupplyChainBuilder(symbols=valid, include_themes=True, max_hops=2)
    graph_info = builder.build()
    logger.info(f"[3/4] 图: {graph_info['node_count']} 节点 / {graph_info['edge_count']} 边")
    return price_data, valid, builder.graph, industries


def _compute_factors_at_entry(
    closes_map: Dict[str, List[float]],
    graph: Any,
    industries: Dict[str, str],
    entry_idx: int,
) -> Dict[str, Dict[str, float]]:
    """在 entry_idx 时点重算因子 (B1/B2 无前视).

    Returns:
        {symbol: {"mom_60d": v, "chain_mom_60d": v}} — 正交化后的因子值
    """
    truncated = {
        sym: {"closes": c[: entry_idx + 1]}
        for sym, c in closes_map.items()
        if len(c) > entry_idx
    }
    chain_t = compute_lead_lag_factors(truncated, graph, industries=industries)
    mom_t = _compute_momentum_factors(truncated)
    chain_t = orthogonalize_chain_factors(chain_t, mom_t)

    result: Dict[str, Dict[str, float]] = {}
    mom_60d = mom_t.get("MOM_60D")
    chain_60d = chain_t.get("CHAIN_MOM_60D")
    for sym in truncated:
        result[sym] = {
            "mom_60d": mom_60d.values.get(sym, 0.0) if mom_60d else 0.0,
            "chain_mom_60d": chain_60d.values.get(sym, 0.0) if chain_60d else 0.0,
        }
    return result


def _zscore(values: List[float]) -> np.ndarray:
    """Z-score 标准化 (消除量纲差异, 使等权合成有意义)."""
    arr = np.array(values, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std())
    if std < 1e-12:
        return np.zeros_like(arr)
    return (arr - mean) / std


def _top_bottom_ls(
    factor_values: List[float],
    returns: List[float],
    top_pct: float = 0.2,
) -> float:
    """按因子值排序, 计算 Top/Bottom 多空收益."""
    rows = sorted(zip(factor_values, returns), key=lambda x: x[0], reverse=True)
    n = len(rows)
    top_n = max(2, int(n * top_pct))
    long_ret = float(np.mean([r[1] for r in rows[:top_n]]))
    short_ret = float(np.mean([r[1] for r in rows[-top_n:]]))
    return long_ret - short_ret


def _calc_annualized_sharpe(ls_returns: List[float], horizon: int) -> float:
    """年化夏普 = mean/std × sqrt(252/horizon)."""
    if len(ls_returns) < 3:
        return 0.0
    arr = np.array(ls_returns)
    std = float(arr.std())
    if std < 1e-12:
        return 0.0
    return float(arr.mean() / std * (252.0 / horizon) ** 0.5)


def run_s5_validation(
    days: int = 400,
    horizon: int = 20,
    windows: int = 8,
    direction: int = -1,
) -> Dict[str, Any]:
    """S5 组合层面检验: CHAIN_MOM_60D 边际夏普改善.

    Args:
        days: 历史天数
        horizon: 持有期 (天)
        windows: 非重叠滚动窗口数
        direction: CHAIN_MOM_60D 方向 (-1=反向, B3 修复后确定)

    Returns:
        {benchmark_sharpe, enhanced_sharpe, marginal_improvement, verdict, details}
    """
    price_data, symbols, graph, industries = _build_universe(days)
    closes_map = {sym: data.get("closes", []) for sym, data in price_data.items()}

    eligible = [len(c) for c in closes_map.values() if len(c) > horizon + 60]
    if len(eligible) < 10:
        return {"verdict": "FAIL", "reason": "有效股票不足 10 只"}
    el = np.array(sorted(eligible))
    max_len = int(el[max(0, int(len(el) * 0.2) - 1)])
    step = horizon

    benchmark_ls: List[float] = []
    enhanced_ls: List[float] = []
    window_details: List[Dict[str, Any]] = []

    for w in range(windows):
        T = max_len - 1 - w * step
        entry_idx = T - horizon
        if entry_idx < 60 or T >= max_len:
            continue

        factors = _compute_factors_at_entry(closes_map, graph, industries, entry_idx)

        rows_data: List[Tuple[str, float, float, float]] = []
        for sym, fv in factors.items():
            closes = closes_map.get(sym, [])
            if len(closes) <= T or closes[entry_idx] <= 0:
                continue
            ret = closes[T] / closes[entry_idx] - 1
            rows_data.append((sym, fv["mom_60d"], fv["chain_mom_60d"], ret))

        if len(rows_data) < 10:
            continue

        syms = [r[0] for r in rows_data]
        mom_vals = [r[1] for r in rows_data]
        chain_vals = [r[2] for r in rows_data]
        rets = [r[3] for r in rows_data]

        mom_z = _zscore(mom_vals)
        chain_z = _zscore(chain_vals)

        enhanced_vals = (mom_z + direction * chain_z).tolist()

        bench_ret = _top_bottom_ls(mom_vals, rets)
        enh_ret = _top_bottom_ls(enhanced_vals, rets)

        benchmark_ls.append(bench_ret)
        enhanced_ls.append(enh_ret)

        window_details.append({
            "window": w,
            "entry_idx": entry_idx,
            "T": T,
            "n_stocks": len(rows_data),
            "benchmark_ls": round(bench_ret, 6),
            "enhanced_ls": round(enh_ret, 6),
            "delta": round(enh_ret - bench_ret, 6),
        })

    bench_sharpe = _calc_annualized_sharpe(benchmark_ls, horizon)
    enh_sharpe = _calc_annualized_sharpe(enhanced_ls, horizon)
    marginal = enh_sharpe - bench_sharpe

    verdict = "PASS" if marginal > 0.05 else "FAIL"

    result = {
        "verdict": verdict,
        "benchmark_sharpe": round(bench_sharpe, 4),
        "enhanced_sharpe": round(enh_sharpe, 4),
        "marginal_improvement": round(marginal, 4),
        "threshold": 0.05,
        "n_windows": len(benchmark_ls),
        "horizon": horizon,
        "direction": direction,
        "benchmark_factor": "MOM_60D",
        "enhanced_factor": "MOM_60D + direction * CHAIN_MOM_60D (等权 Z-score 合成)",
        "window_details": window_details,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="S5 组合层面检验 — CHAIN_MOM_60D 边际夏普改善")
    parser.add_argument("--days", type=int, default=400, help="历史天数")
    parser.add_argument("--horizon", type=int, default=20, help="持有期")
    parser.add_argument("--windows", type=int, default=8, help="非重叠滚动窗口数")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    logger.info("=" * 80)
    logger.info("S5 组合层面检验 — CHAIN_MOM_60D 边际夏普改善")
    logger.info("=" * 80)

    result = run_s5_validation(
        days=args.days,
        horizon=args.horizon,
        windows=args.windows,
    )

    logger.info(f"\n基准因子: {result.get('benchmark_factor', 'N/A')}")
    logger.info(f"增强因子: {result.get('enhanced_factor', 'N/A')}")
    logger.info(f"方向修正: direction={result.get('direction', 'N/A')}")
    logger.info(f"窗口数: {result.get('n_windows', 0)} | horizon={result.get('horizon', 0)}")
    logger.info("-" * 80)
    logger.info(f"{'窗口':>6} {'股票数':>8} {'基准多空':>12} {'增强多空':>12} {'差值':>12}")
    logger.info("-" * 80)
    for d in result.get("window_details", []):
        logger.info(f"{d['window']:>6} {d['n_stocks']:>8} {d['benchmark_ls']:>12.6f} "
              f"{d['enhanced_ls']:>12.6f} {d['delta']:>+12.6f}")
    logger.info("-" * 80)
    logger.info(f"基准组合年化夏普:   {result.get('benchmark_sharpe', 0):>+10.4f}")
    logger.info(f"增强组合年化夏普:   {result.get('enhanced_sharpe', 0):>+10.4f}")
    logger.info(f"边际夏普改善:       {result.get('marginal_improvement', 0):>+10.4f}")
    logger.info(f"阈值:               {result.get('threshold', 0.05):>10.4f}")
    logger.info("-" * 80)
    verdict = result.get("verdict", "FAIL")
    tag = "✅ PASS" if verdict == "PASS" else "❌ FAIL"
    logger.info(f"S5 判定: {tag} (边际夏普改善 {'>' if verdict == 'PASS' else '<='} 阈值)")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
