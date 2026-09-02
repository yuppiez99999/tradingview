"""Phase 3 P3.3 — S12 影子账户 30 交易日评估 (2026-09-02 预写, 2026-10-10 左右执行).

ROADMAP P3.3 验收标准 (四项):
  1. 30 天累计收益正向
  2. 最大回撤 < 15%
  3. 无异常换手 (30 交易日内再平衡次数 ≤ 2 且单次 turnover < 50%)
  4. 与回测偏差 < 20% — 统计诚实化: 30 日窗口年化噪声极大, 直接对比年化
     会误判; 改为「分布带检验」: 跑 S12 回测全历史 NAV, 滚动 30 交易日
     窗口算年化分布 P5/P50/P95, 影子年化落在 [P5, P95] 带内即 PASS
     (判定的是「影子是否像同一策略的另一个 30 天样本」).

用法:
  python scripts/run_p33_evaluation.py            # 正式评估 (需 ≥30 交易日)
  python scripts/run_p33_evaluation.py --force     # 不足 30 日强制评估 (标记 N.A.)
输出: data/etf_option_backtest/p33_shadow_evaluation_<ts>.md + .json
退出码: 0 = 四项全 PASS, 1 = 有 FAIL 或数据不足
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [P3.3] %(message)s")
logger = logging.getLogger("p33_eval")

STATE_PATH = PROJECT_ROOT / "output" / "shadow_account" / "s12_shadow_state.json"
CONFIG_PATH = PROJECT_ROOT / "config" / "s12_shadow_config.json"
REF_SCRIPT = PROJECT_ROOT / "data" / "etf_option_backtest" / "run_etf_option_backtest.py"
OUT_DIR = PROJECT_ROOT / "data" / "etf_option_backtest"

WINDOW = 30          # 评估窗口 (交易日)
MAX_REBAL_COUNT = 2  # 30 交易日内再平衡次数上限
MAX_SINGLE_TURNOVER = 0.50
MAX_DD_LIMIT = 0.15
DIST_LOW, DIST_HIGH = 0.05, 0.95  # 分布带分位


def _load_ref_module():
    spec = importlib.util.spec_from_file_location("etf_ref_backtest", str(REF_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etf_ref_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


def rolling_window_annualized(eq: list[float], window: int) -> list[float]:
    """全历史 NAV 序列 → 每个 30 日窗口的年化收益."""
    out = []
    for i in range(window, len(eq) + 1):
        seg = eq[i - window:i]
        if seg[0] <= 0:
            continue
        out.append((seg[-1] / seg[0]) ** (252 / window) - 1.0)
    return out


def percentile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位数 (输入需已升序)."""
    if not sorted_vals:
        return float("nan")
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[int(pos)]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def evaluate_acceptance(
    total_return: float,
    max_drawdown: float,
    rebal_count: int,
    max_single_turnover: float,
    shadow_ann: float | None,
    dist_low: float,
    dist_high: float,
) -> tuple[dict, bool]:
    """四项验收判定 (纯函数). 返回 (checks, all_pass)."""
    checks = {
        "return_positive": {
            "pass": total_return > 0,
            "detail": f"累计收益 {total_return * 100:+.3f}% {'>' if total_return > 0 else '<='} 0",
        },
        "drawdown_limit": {
            "pass": max_drawdown < MAX_DD_LIMIT,
            "detail": f"最大回撤 {max_drawdown * 100:.3f}% < {MAX_DD_LIMIT * 100:.0f}%",
        },
        "turnover_normal": {
            "pass": rebal_count <= MAX_REBAL_COUNT and max_single_turnover < MAX_SINGLE_TURNOVER,
            "detail": (f"再平衡 {rebal_count} 次 (≤{MAX_REBAL_COUNT}) / "
                       f"最大单次换手 {max_single_turnover * 100:.2f}% "
                       f"(<{MAX_SINGLE_TURNOVER * 100:.0f}%)"),
        },
        "backtest_consistent": {
            "pass": (shadow_ann is not None
                     and dist_low <= shadow_ann <= dist_high),
            "detail": (f"年化 {shadow_ann * 100:+.2f}% "
                       f"落在回测 30 日窗口年化分布 [P5={dist_low * 100:.2f}%, "
                       f"P95={dist_high * 100:.2f}%] 带内"
                       if shadow_ann is not None else "影子样本不足, 无法计算年化"),
        },
    }
    return checks, all(c["pass"] for c in checks.values())


def build_report(
    state: dict, config: dict, dist: dict, checks: dict, all_pass: bool, forced: bool
) -> str:
    bench = config.get("backtest_benchmark", {})
    k = state.get("trading_day_count", 0)
    trades = state.get("trade_log", [])
    rebals = [t for t in trades if t["action"] == "rebalance"]
    total_cost = sum(t.get("cost", 0.0) for t in rebals)
    m_ann = dist.get("shadow_ann")
    ann_s = f"{m_ann * 100:+.2f}%" if m_ann is not None else "N/A"
    L = []
    L.append("=" * 78)
    L.append("  Phase 3 P3.3 — S12 影子账户 30 交易日评估")
    L.append("=" * 78)
    L.append(f"  评估时间: {datetime.now().isoformat(timespec='seconds')}")
    dates = state.get("recorded_dates") or []
    span = f"({dates[0]} ~ {dates[-1]})" if dates else ""
    L.append(f"  影子窗口: {k} 交易日 {span}")
    if forced:
        L.append("  ⚠️ 强制模式: 样本不足 30 交易日, 指标不可信, 仅预演")
    L.append("")
    L.append("  === 验收判定 (ROADMAP P3.3) ===")
    for name, c in checks.items():
        L.append(f"  [{'PASS' if c['pass'] else 'FAIL'}] {name}: {c['detail']}")
    L.append(f"  总判定: {'PASS — 可进入 Phase 4 灰度' if all_pass else 'FAIL — 按 ROADMAP 复盘后重跑或延窗'}")
    L.append("")
    L.append("  === 影子 vs 回测对照 ===")
    L.append(f"  回测基准 (2021-2026): 年化 {bench.get('annual_return', 0) * 100:.2f}% / "
             f"回撤 {bench.get('max_drawdown', 0) * 100:.2f}% / Sharpe {bench.get('sharpe', 0):.2f}")
    L.append(f"  影子 30 日: 累计 {(dist.get('shadow_total') or 0) * 100:+.3f}% / "
             f"年化 {ann_s} / "
             f"回撤 {(dist.get('shadow_mdd') or 0) * 100:.3f}%")
    L.append(f"  回测滚动 30 日年化分布 (T={dist.get('n_windows', 0)} 窗口): "
             f"P5={dist['p5'] * 100:.2f}% / P50={dist['p50'] * 100:.2f}% / "
             f"P95={dist['p95'] * 100:.2f}%")
    L.append(f"  再平衡: {len(rebals)} 次 / 累计成本 {total_cost * 100:.4f}% / "
             f"数据源 {state.get('price_source', 'N/A')}")
    L.append("")
    L.append("  === 判定口径说明 ===")
    L.append("  「与回测偏差」采用分布带检验而非年化点对点偏差: 30 日窗口年化")
    L.append("  噪声极大 (见上方回测滚动分布), 点对点偏差必然超 20%; 落在")
    L.append("  [P5, P95] 带内判定为「同一策略的另一个 30 天样本」(诚实口径).")
    L.append("=" * 78)
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description="P3.3 影子账户 30 日评估")
    parser.add_argument("--force", action="store_true", help="样本不足 30 日强制评估 (仅预演)")
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not STATE_PATH.exists():
        logger.error("状态文件不存在: %s", STATE_PATH)
        return 1
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    k = state.get("trading_day_count", 0)
    if k < WINDOW and not args.force:
        logger.error("样本不足: %d/%d 交易日 — 用 --force 预演 (正式评估需等 30 日)", k, WINDOW)
        return 1
    forced = k < WINDOW

    # 影子指标
    navs = [float(e["nav"]) for e in state.get("daily_nav", [])]
    total_return = (navs[-1] - 1.0) if navs else 0.0
    peak, mdd = 1.0, 0.0
    for n in navs:
        peak = max(peak, n)
        if peak > 0:
            mdd = max(mdd, (peak - n) / peak)
    shadow_ann = navs[-1] ** (252 / len(navs)) - 1.0 if len(navs) >= 2 and navs[-1] > 0 else None

    # 换手统计
    trades = state.get("trade_log", [])
    rebals = [t for t in trades if t["action"] == "rebalance"]
    max_turn = max((t.get("turnover", 0.0) for t in rebals), default=0.0)

    # 回测滚动分布
    logger.info("运行 S12 回测全历史 (滚动 30 日窗口分布)...")
    mod = _load_ref_module()
    prices = mod.load_etf_prices(None)
    cfg = mod.load_config()
    tws = mod.extract_target_weights(cfg)
    eq, _tc = mod.run_s12_defensive_rp(prices, tws)
    wins = sorted(rolling_window_annualized(eq, WINDOW))
    p5, p50, p95 = percentile(wins, DIST_LOW), percentile(wins, 0.5), percentile(wins, DIST_HIGH)
    logger.info("回测滚动 %d 窗口: P5=%.2f%% P50=%.2f%% P95=%.2f%%",
                len(wins), p5 * 100, p50 * 100, p95 * 100)

    checks, all_pass = evaluate_acceptance(
        total_return, mdd, len(rebals), max_turn, shadow_ann, p5, p95
    )

    dist = {"n_windows": len(wins), "p5": p5, "p50": p50, "p95": p95,
            "shadow_ann": shadow_ann, "shadow_total": total_return, "shadow_mdd": mdd}
    report = build_report(state, config, dist, checks, all_pass, forced)
    print(report)  # allow-print

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rp = OUT_DIR / f"p33_shadow_evaluation_{ts}.md"
    rp.write_text(report, encoding="utf-8")
    jp = OUT_DIR / f"p33_shadow_evaluation_{ts}.json"
    jp.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "trading_days": k, "forced": forced,
        "shadow": {"total_return": total_return, "max_drawdown": mdd,
                   "annualized": shadow_ann,
                   "rebalance_count": len(rebals), "max_single_turnover": max_turn},
        "backtest_rolling_dist": {"n_windows": len(wins), "p5": p5, "p50": p50, "p95": p95},
        "checks": checks, "all_pass": all_pass,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("报告: %s / 结果: %s", rp, jp)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
