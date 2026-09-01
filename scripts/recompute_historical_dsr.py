"""历史 DSR 报告复核脚本 (2026-08-31)

用修复后的 DSR 公式重算 output/validation_reports/dsr_*.json,
对比旧 pass 字段, 标记 PASS→FAIL 的误放候选策略。

注意: 历史 JSON 未存时序 returns, 此处用存的 sharpe(年化)/skew/kurt(超额)/n_months
做近似重算 (假设日频 T=n_months*21, kurt+3 转原始峰度)。
若发现大量误放, 建议重跑 scripts/run_p23_validation.py 精确验证。
"""

import glob
import json
import math
from pathlib import Path

from scipy.stats import norm

TRADING_DAYS = 252
REQUIRED_DSR = 0.95


def recompute_dsr(sharpe_ann: float, skew: float, kurt_excess: float,
                  n_months: int, n_trials: int) -> float:
    """近似重算 DSR (修复后口径, 与 utils/backtest/deflated_sharpe.py 一致)。"""
    T = max(n_months * 21, 2)  # 日频观测数近似
    if T < 2:
        return 0.0
    sr_daily = sharpe_ann / math.sqrt(TRADING_DAYS)
    kurt_raw = kurt_excess + 3.0  # 超额峰度→原始峰度
    if n_trials <= 1:
        e_max = 0.0  # 与 utils 版一致: n_trials<=1 时 E[max SR]=0
    else:
        z_max = math.sqrt(2 * math.log(max(n_trials, 2)))
        correction = (
            1
            + (skew / 6) * (z_max**2 - 1)
            + ((kurt_raw - 3) / 24) * (z_max**3 - 3 * z_max)
        )
        e_max = max(z_max * correction / math.sqrt(T), 0.0)
    denom = math.sqrt(max(
        1.0 - skew * sr_daily + (kurt_raw - 1.0) / 4.0 * sr_daily**2,
        1e-12,
    ))
    z = (sr_daily - e_max) * math.sqrt(max(T - 1, 1)) / denom
    return float(norm.cdf(z))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pattern = str(root / "output" / "validation_reports" / "dsr_*.json")
    files = sorted(glob.glob(pattern))

    flipped = []  # PASS→FAIL 误放候选
    stable_pass = []  # 仍 PASS (真技能)
    all_rows = []

    for f in files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        sharpe_ann = d.get("sharpe", 0.0)
        skew = d.get("skewness", 0.0)
        kurt_excess = d.get("kurtosis", 0.0)
        n_months = d.get("n_months", 0)
        fname = Path(f).name

        for old in d.get("dsr_results", []):
            n_trials = old.get("n_trials", 1)
            old_dsr = old.get("dsr", 0.0)
            old_pass = old.get("pass", False)
            new_dsr = recompute_dsr(sharpe_ann, skew, kurt_excess, n_months, n_trials)
            new_pass = new_dsr >= REQUIRED_DSR
            row = (fname, n_trials, old_dsr, new_dsr, old_pass, new_pass)
            all_rows.append(row)
            if old_pass and not new_pass:
                flipped.append(row)
            elif old_pass and new_pass:
                stable_pass.append(row)

    # 输出汇总
    print(f"扫描 DSR 报告: {len(files)} 个文件, {len(all_rows)} 条 (strategy, n_trials) 记录")
    print(f"旧 PASS 且 新 PASS (真技能, 稳定): {len(stable_pass)}")
    print(f"旧 PASS → 新 FAIL (误放候选, 需复核): {len(flipped)}")
    print()
    if flipped:
        print("=== 误放候选策略 (PASS→FAIL) ===")
        print(f"{'文件':<55} {'n_trials':>8} {'旧DSR':>8} {'新DSR':>8}")
        for fname, nt, old_d, new_d, _, _ in flipped[:30]:
            print(f"{fname:<55} {nt:>8} {old_d:>8.4f} {new_d:>8.4f}")
        if len(flipped) > 30:
            print(f"... 共 {len(flipped)} 条")
    else:
        print("无 PASS→FAIL 翻转")

    # 影子账户实盘 DSR
    print()
    print("=== 影子账户实盘跟踪 DSR ===")
    shadow_dir = root / "reports" / "shadow"
    for sf in sorted(shadow_dir.glob("*_dsr.json")):
        sd = json.loads(sf.read_text(encoding="utf-8"))
        m = sd.get("metrics", {})
        print(f"{sf.name}: DSR={m.get('dsr', 'N/A')}, "
              f"Sharpe={m.get('sharpe_ratio', 'N/A')}, "
              f"年化={m.get('annual_return', 'N/A')}, "
              f"真实数据={m.get('is_real_data', 'N/A')}")

    # 写 JSON 结果
    out = root / "风险扫描报告" / "dsr_recompute_result_20260831.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "scanned_files": len(files),
        "total_records": len(all_rows),
        "stable_pass": len(stable_pass),
        "flipped_pass_to_fail": len(flipped),
        "flipped_details": [
            {"file": r[0], "n_trials": r[1], "old_dsr": r[2], "new_dsr": r[3]}
            for r in flipped
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n详细结果: {out}")


if __name__ == "__main__":
    main()
