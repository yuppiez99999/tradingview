"""S13 路径 A 诚实验证: S12 vs S13 对照 + ablation + DSR/CPCV/Noise 三件套.

设计真相源: docs/S13_selection_alpha_注入设计_20260902.md §六 验收标准:
  年化 ≥8.48% (S12 7.48% + 1pp) | 回撤 ≤12% | DSR ≥0.95 (n_trials=15)
  CPCV CV <0.5 | ablation S13-S12 超额 ≥1pp 且来自卫星仓

多重检验计数: S1-S12 家族累计 14 次, S13 计入后 n_trials=15.

用法:
  python scripts/run_s13_validation.py            # 完整验证
  python scripts/run_s13_validation.py --quick   # 仅 DSR
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import run_p23_validation as p23  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [S13-V] %(message)s")
logger = logging.getLogger("s13_validation")

# 验收门槛 (设计文档 §六)
THRESH_ANNUAL_PCT = 8.48          # S12 7.48% + 1pp
THRESH_MAX_DD_PCT = 12.0
THRESH_ABLATION_PCT = 1.0         # S13 - S12 年化超额
S12_ANNUAL_PCT = 7.48             # 已验证诚实下限 (P2.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="S13 三件套验证")
    parser.add_argument("--n-trials", type=int, default=15,
                        help="DSR 多重检验次数 (S1-S12 家族 14 + S13)")
    parser.add_argument("--quick", action="store_true", help="仅 DSR")
    parser.add_argument("--data-file", default=None)
    args = parser.parse_args()

    mod = p23._ensure_ref_script()
    prices = mod.load_etf_prices(args.data_file)
    cfg = mod.load_config()
    tws = mod.extract_target_weights(cfg)
    bench_eq = mod.run_benchmark(prices)

    logger.info("运行 S12 (对照)...")
    eq_s12, _ = mod.run_s12_defensive_rp(prices, tws)
    logger.info("运行 S13 (核心-卫星)...")
    eq_s13, _ = mod.run_s13_core_satellite(prices, tws)

    strategies = [
        ("S12 纯防御风险平价(对照)", eq_s12, bench_eq),
        ("S13 核心-卫星(因子信号)", eq_s13, bench_eq),
    ]

    results = p23.run_validation(strategies, args.n_trials, args.quick)
    v_by_name = {v.name: v for v in results}
    m_by_name: dict[str, dict[str, float]] = {}
    for name, eq, _ in strategies:
        v = v_by_name.get(name)
        if v is None:
            continue
        m = p23.compute_metrics_from_eq(eq, bench_eq)
        m_by_name[name] = m
        v.annual_return_pct = m["annual_return"] * 100
        v.max_drawdown_pct = m["max_drawdown"] * 100
        v.sharpe = m["sharpe"]
        logger.info("%s: 年化=%.2f%% 回撤=%.2f%% Sharpe=%.3f DSR=%.4f(%s)",
                    name, v.annual_return_pct, v.max_drawdown_pct, v.sharpe,
                    v.dsr, "PASS" if v.dsr_pass else "FAIL")

    # ---- ablation: S13 - S12 ----
    m12 = m_by_name.get("S12 纯防御风险平价(对照)", {})
    m13 = m_by_name.get("S13 核心-卫星(因子信号)", {})
    abl_annual = (m13.get("annual_return", 0.0) - m12.get("annual_return", 0.0)) * 100
    abl_dd = (m13.get("max_drawdown", 0.0) - m12.get("max_drawdown", 0.0)) * 100

    # ---- 验收判定 ----
    v13 = v_by_name.get("S13 核心-卫星(因子信号)")
    checks = {
        "annual_ge_8pct": m13.get("annual_return", 0.0) * 100 >= THRESH_ANNUAL_PCT,
        "dd_le_12pct": m13.get("max_drawdown", 0.0) * 100 <= THRESH_MAX_DD_PCT,
        "dsr_pass": bool(v13.dsr_pass) if v13 else False,
        "cpcv_stable": bool(v13.cpcv_stable) if v13 else False,
        "ablation_ge_1pp": abl_annual >= THRESH_ABLATION_PCT,
        "noise_stable": bool(v13.noise_stable) if v13 else False,
    }
    overall = all(checks.values())

    # ---- 报告 ----
    lines = []
    lines.append("=" * 78)
    lines.append("  S13 Selection Alpha 路径 A 诚实验证 (S12 对照 + 三件套 + ablation)")
    lines.append("=" * 78)
    lines.append(f"  检验时间: {datetime.now().isoformat()}")
    lines.append(f"  DSR 多重检验修正: n_trials = {args.n_trials} (S1-S12 家族 14 + S13)")
    lines.append("")
    lines.append(p23.fmt_report(results, args.n_trials))
    lines.append("  === Ablation (S13 − S12) ===")
    lines.append(f"  年化超额: {abl_annual:+.2f}pp (门槛 ≥ {THRESH_ABLATION_PCT}pp)")
    lines.append(f"  回撤变化: {abl_dd:+.2f}pp")
    lines.append("")
    lines.append("  === 验收判定 (设计文档 §六) ===")
    for k, ok in checks.items():
        lines.append(f"  [{'x' if ok else ' '}] {k}")
    lines.append(f"  总判定: {'PASS — S13 进入 shadow 双轨' if overall else 'FAIL — 路径A信号不足, 按 §七回退 S12'}")
    lines.append("=" * 78)
    report = "\n".join(lines)
    print(report)  # allow-print

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_ROOT / "data" / "etf_option_backtest"
    rp = out_dir / f"s13_validation_{ts}.md"
    rp.write_text(report, encoding="utf-8")
    jp = out_dir / f"s13_validation_{ts}.json"
    jp.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "n_trials_dsr": args.n_trials,
        "ablation_annual_pp": abl_annual,
        "ablation_dd_pp": abl_dd,
        "checks": checks,
        "overall_pass": overall,
        "strategies": [
            {"name": v.name, "annual_return_pct": v.annual_return_pct,
             "max_drawdown_pct": v.max_drawdown_pct, "sharpe": v.sharpe,
             "dsr": v.dsr, "dsr_pass": v.dsr_pass,
             "cpcv_stable": v.cpcv_stable, "cpcv_cv": (None if v.cpcv_cv == float("inf") else v.cpcv_cv),
             "noise_stable": v.noise_stable, "is_honest": v.is_honest}
            for v in results
        ],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("报告: %s", rp)
    logger.info("结果: %s", jp)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
