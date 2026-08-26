"""ETF期权对冲 Phase 2 — P2.3 诚实回测三件套验证 (DSR + CPCV + Noise)

2026-08-25: P2.2 诚实基线 → S5 回撤 15.72% (唯一达校准验收线的策略), S1/S3 作 Sharpe 对照.
P2.3 跑三件套检验:
  - DSR (Deflated Sharpe Ratio): 多重检验修正, 判定策略 Sharpe 显著非零
  - CPCV (组合清洗交叉验证): 多路径 Sharpe 变异系数, 检验过拟合
  - Noise 注入稳定性: 噪音注入 1000 次, 检验信号稳健性
联合判定: DSR.is_pass AND Noise.is_stable AND CPCV SV < 0.5 → HONEST

用法:
  python scripts/run_p23_validation.py              # 完整三件套
  python scripts/run_p23_validation.py --quick       # 仅 DSR (跳过依赖 ms_strategy 的 CPCV/Noise)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [P2.3] %(message)s")
logger = logging.getLogger("p23_validation")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REF_SCRIPT = PROJECT_ROOT / "data" / "etf_option_backtest" / "run_etf_option_backtest.py"
SYS_PATH_ADDED = False


def _ensure_ref_script() -> Any:
    global SYS_PATH_ADDED
    if not SYS_PATH_ADDED:
        sys.path.insert(0, str(PROJECT_ROOT))
        SYS_PATH_ADDED = True
    import importlib.util
    spec = importlib.util.spec_from_file_location("etf_ref_backtest", str(REF_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etf_ref_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


def _equity_to_daily_returns(eq: list[float]) -> np.ndarray:
    arr = np.array(eq, dtype=float)
    if len(arr) < 2:
        return np.array([])
    return np.diff(arr) / arr[:-1]


@dataclass
class StrategyValidation:
    name: str
    annual_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    n_returns: int = 0
    dsr_p_value: float = 1.0
    dsr: float = 0.0
    dsr_pass: bool = False
    cpcv_stable: bool = False
    cpcv_cv: float = float("inf")
    cpcv_n_paths: int = 0
    noise_stable: bool = False
    is_honest: bool = False
    verdict: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def run_validation(strategies: list[tuple[str, Any, list[float]]],
                   n_trials_dsr: int,
                   quick: bool,
                   cpcv_n_groups: int = 6,
                   cpcv_n_test_groups: int = 2) -> list[StrategyValidation]:
    results = []
    for name, strategy_eq, _bench_eq in strategies:
        rets = _equity_to_daily_returns(strategy_eq)
        if len(rets) < 20:
            logger.warning("%s: 样本不足 (%d), 跳过", name, len(rets))
            continue

        logger.info("--- %s ---", name)
        logger.info("  日收益率: mean=%.6f std=%.6f n=%d", float(rets.mean()), float(rets.std()), len(rets))

        v = StrategyValidation(name=name, n_returns=len(rets))

        try:
            from utils.backtest.honest_validation import run_honest_validation

            hvr = run_honest_validation(
                daily_returns=rets.tolist(),
                n_trials_dsr=n_trials_dsr,
                required_dsr=0.95,
                risk_free_rate=0.02,
                cpcv_n_groups=cpcv_n_groups,
                cpcv_n_test_groups=cpcv_n_test_groups,
                noise_ratio=0.1,
                noise_n_trials=1000 if not quick else 100,
                random_seed=42,
            )

            v.dsr = float(hvr.dsr) if hvr.dsr else 0.0
            v.dsr_p_value = hvr.dsr.p_value if hvr.dsr else 1.0
            v.dsr_pass = hvr.dsr.is_pass if hvr.dsr else False
            v.cpcv_stable = hvr.cpcv.is_stable
            v.cpcv_cv = hvr.cpcv.sharpe_cv
            v.cpcv_n_paths = hvr.cpcv.n_paths
            v.noise_stable = getattr(hvr.noise, "is_stable", False) if hvr.noise else False
            v.is_honest = hvr.is_honest
            v.verdict = hvr.verdict
            v.details = {
                "original_sharpe": hvr.original_sharpe,
                "dsr_expected_max_sr": hvr.dsr.expected_max_sr if hvr.dsr else 0.0,
                "dsr_skewness": hvr.dsr.skewness if hvr.dsr else 0.0,
            }
        except Exception as e:
            logger.error("%s: 三件套检验异常 — %s", name, e)
            v.verdict = f"error: {e}"
        results.append(v)
    return results


def compute_metrics_from_eq(eq: list[float], bench_eq: list[float]) -> dict[str, float]:
    mod = _ensure_ref_script()
    return mod.compute_metrics(eq, bench_eq)


def fmt_report(results: list[StrategyValidation], n_trials_dsr: int) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("  ETF期权对冲 Phase 2 — P2.3 诚实回测三件套验证 (DSR + CPCV + Noise)")
    lines.append("=" * 78)
    lines.append(f"  检验时间: {datetime.now().isoformat()}")
    lines.append(f"  DSR 多重检验修正: n_trials = {n_trials_dsr} (探索的 5 策略 S1-S5)")
    lines.append("  验收标准: DSR≥0.95 AND CPCV CV<0.5 AND Noise stable → HONEST")
    lines.append("")

    lines.append(f"  {'策略':<28} {'年化%':>6} {'回撤%':>6} {'Sharpe':>7} {'DSR':>7} {'P值':>7} {'CPCV':>7} {'Noise':>7} {'判定':>8}")
    lines.append("  " + "-" * 76)
    for v in results:
        dsr_s = f"{v.dsr:.4f}" if v.dsr else "N/A"
        pval_s = f"{v.dsr_p_value:.4f}" if v.dsr_p_value < 1 else "1.0000"
        cpcv_s = "✓" if v.cpcv_stable else ("✗" if v.cpcv_n_paths > 0 else "N/A")
        noise_s = "✓" if v.noise_stable else ("✗" if v.cpcv_n_paths > 0 else "N/A")
        verdict = "HONEST" if v.is_honest else "FAIL"
        lines.append(
            f"  {v.name:<28} {v.annual_return_pct:>5.2f} {v.max_drawdown_pct:>5.2f} "
            f"{v.sharpe:>7.3f} {dsr_s:>7} {pval_s:>7} {cpcv_s:>7} {noise_s:>7} {verdict:>8}"
        )
    lines.append("")
    for v in results:
        if v.verdict:
            lines.append(f"  [{v.name}] {v.verdict}")
    lines.append("")
    lines.append("  === 判定说明 ===")
    lines.append("  DSR (Deflated Sharpe Ratio): 经 n_trials 多重检验修正后的显著性, ≥0.95 表示策略")
    lines.append("    收益不大可能由随机筛选(数据窥探)产生")
    lines.append("  CPCV (组合清洗交叉验证): 多路径 Sharpe 变异系数 <0.5 且正向路径 >80% 表示策略")
    lines.append("    在不同时间段表现一致, 非特定窗口过拟合")
    lines.append("  Noise (噪音注入): 注入 10% 噪音后 1000 次实验中策略指标稳定表示策略信号")
    lines.append("    而非高端噪音")
    lines.append("  联合判定: DSR≥0.95 AND CPCV stable AND Noise stable → HONEST")
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="P2.3 诚实回测三件套验证")
    parser.add_argument("--quick", action="store_true", help="仅 DSR (跳过依赖 ms_strategy 的 CPCV/Noise)")
    parser.add_argument("--data-file", default=None, help="合并面板 parquet 路径 (默认 data/etf_option_backtest/all_etf_daily.parquet)")
    parser.add_argument("--strategies", default="s1s3s5", help="策略选择: s1s3s5|s1s3s5s6|s6|all")
    parser.add_argument("--n-trials", type=int, default=5, help="DSR 多重检验修正的 n_trials")
    parser.add_argument("--cpcv-groups", type=int, default=6, help="CPCV 分组数 (默认 6)")
    parser.add_argument("--cpcv-test-groups", type=int, default=2, help="CPCV 测试组数 (默认 2)")
    args = parser.parse_args()

    logger.info("加载参考模块 (run_etf_option_backtest)...")
    mod = _ensure_ref_script()
    wind = mod.load_etf_prices(args.data_file)
    cfg = mod.load_config()
    tws = mod.extract_target_weights(cfg)
    bench_eq = mod.run_benchmark(wind)

    sel = args.strategies.lower()
    strategy_eqs = []

    if "s1" in sel or sel == "all":
        logger.info("运行 S1...")
        eq, _ = mod.run_s1_baseline(wind, tws)
        strategy_eqs.append(("S1 基线(静态无对冲)", eq))
    if "s3" in sel or sel == "all":
        logger.info("运行 S3...")
        eq, _ = mod.run_s3_option_hedge(wind, tws)
        strategy_eqs.append(("S3 期权对冲(BS认沽)", eq))
    if "s5" in sel or sel == "all":
        logger.info("运行 S5...")
        eq, _ = mod.run_s5_tail_hedge(wind, tws)
        strategy_eqs.append(("S5 尾部熔断(回撤加码)", eq))
    if "s6" in sel or sel == "all":
        logger.info("运行 S6 V9 Regime...")
        eq, _ = mod.run_s6_v9_regime(wind, tws)
        strategy_eqs.append(("S6 V9Regime轮动+熔断", eq))
    if "s7" in sel or sel == "all":
        logger.info("运行 S7 V9 Regime+动量...")
        eq, _ = mod.run_s7_v9_momentum(wind, tws)
        strategy_eqs.append(("S7 V9Regime+动量+熔断", eq))

    strategies = [(name, eq, bench_eq) for name, eq in strategy_eqs]
    n_trials_dsr = args.n_trials

    logger.info("运行诚实回测三件套 (n_trials=%d, quick=%s, cpcv=%d/%d)...",
                n_trials_dsr, args.quick, args.cpcv_groups, args.cpcv_test_groups)
    results = run_validation(strategies, n_trials_dsr, args.quick,
                             args.cpcv_groups, args.cpcv_test_groups)

    # BUG FIX (2026-08-25): results 可能因样本不足被跳过而比 strategies 短,
    # zip 按位置配对会把指标错配到错误的策略上, 改为按 name 对齐。
    v_by_name = {v.name: v for v in results}
    for name, eq, _ in strategies:
        v = v_by_name.get(name)
        if v is None:
            continue
        m = compute_metrics_from_eq(eq, bench_eq)
        v.annual_return_pct = m["annual_return"] * 100
        v.max_drawdown_pct = m["max_drawdown"] * 100
        v.sharpe = m["sharpe"]
        logger.info(
            "%s: 年化=%.2f%% 回撤=%.2f%% Sharpe=%.3f DSR=%.4f(%s) CPCV=%s Noise=%s → %s",
            name, v.annual_return_pct, v.max_drawdown_pct, v.sharpe,
            v.dsr, "PASS" if v.dsr_pass else "FAIL",
            "PASS" if v.cpcv_stable else "N/A",
            "PASS" if v.noise_stable else "N/A",
            "HONEST" if v.is_honest else "FAIL",
        )

    report = fmt_report(results, n_trials_dsr)
    print(report)  # allow-print

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = PROJECT_ROOT / "data" / "etf_option_backtest"
    report_path = output_dir / f"p23_honest_validation_{ts}.md"
    report_path.write_text(report, encoding="utf-8")
    json_path = output_dir / f"p23_honest_validation_{ts}.json"
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "phase": "P2.3",
                "n_trials_dsr": n_trials_dsr,
                "strategies": [
                    {
                        "name": v.name,
                        "annual_return_pct": v.annual_return_pct,
                        "max_drawdown_pct": v.max_drawdown_pct,
                        "sharpe": v.sharpe,
                        "dsr": v.dsr,
                        "dsr_p_value": v.dsr_p_value,
                        "dsr_pass": v.dsr_pass,
                        "cpcv_stable": v.cpcv_stable,
                        "cpcv_cv": v.cpcv_cv,
                        "noise_stable": v.noise_stable,
                        "is_honest": v.is_honest,
                        "verdict": v.verdict,
                    }
                    for v in results
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.info("报告: %s", report_path)
    logger.info("结果: %s", json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
