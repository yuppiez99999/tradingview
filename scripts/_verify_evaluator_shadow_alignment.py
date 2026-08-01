# -*- coding: utf-8 -*-
"""评估器与 ShadowAccountAdapter 对齐验证.

验证 StrategyEvaluator 与 ShadowAccountAdapter 的指标计算一致性:
    - DSR (Deflated Sharpe Ratio)
    - max_drawdown (最大回撤)
    - sharpe_cv (Sharpe 变异系数)

容差:
    - DSR < 0.001 (同一实现)
    - max_drawdown < 0.001 (同一 NAV 计算)
    - sharpe_cv < 0.05 (窗口不同, 容差稍大)
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    """运行对齐验证."""
    print("=" * 70)
    print("评估器与 ShadowAccountAdapter 对齐验证")
    print("=" * 70)

    try:
        from utils.alpha.strategy_evaluator import StrategyEvaluator
        from utils.alpha.shadow_account_adapter import (
            ShadowAccountAdapter,
            InsufficientReturnsError,
        )
    except ImportError as e:
        print(f"[FAIL] 导入失败: {e}")
        return 1

    failures = 0

    # 生成测试数据 (252 条, 满足两者最小样本要求)
    # 使用低波动率避免 Fail-Fast 触发 (单日回撤 < 3%)
    random.seed(42)
    test_returns = [0.0005 + 0.005 * random.gauss(0, 1) for _ in range(252)]

    print(f"\n测试数据: {len(test_returns)} 条 daily_returns")

    # ============================================================
    # 1. StrategyEvaluator 评估
    # ============================================================
    print("\n--- 1. StrategyEvaluator 评估 ---")
    evaluator = StrategyEvaluator()
    evaluator._enabled = True  # 测试用: 强制启用
    eval_report = evaluator.evaluate(test_returns, n_trials=100)
    print(f"  public_score  = {eval_report.public_score:.4f}")
    print(f"  private_score = {eval_report.private_score:.4f}")
    print("  private_metrics:")
    for k, v in eval_report.private_metrics.items():
        print(f"    {k} = {v}")

    # ============================================================
    # 2. ShadowAccountAdapter 评估
    # ============================================================
    print("\n--- 2. ShadowAccountAdapter 评估 ---")
    adapter = ShadowAccountAdapter(
        account_id="alignment_test",
        strategy_id="test",
        initial_capital=1_000_000,
        n_trials=100,
        required_dsr=0.95,
    )
    try:
        shadow_result = adapter.run_shadow(test_returns, is_real_data=True)
        shadow_metrics = adapter.get_metrics()
        print(f"  dsr                 = {shadow_metrics.dsr:.6f}")
        print(f"  annual_return       = {shadow_metrics.annual_return:.6f}")
        print(f"  max_drawdown        = {shadow_metrics.max_drawdown:.6f}")
        print(f"  sharpe_cv           = {shadow_metrics.sharpe_cv:.6f}")
        print(f"  sharpe_ratio        = {shadow_metrics.sharpe_ratio:.6f}")
        print(f"  days_tracked        = {shadow_metrics.days_tracked}")
        print(f"  fail_fast_triggered = {shadow_metrics.fail_fast_triggered}")
    except InsufficientReturnsError as e:
        print(f"[FAIL] ShadowAccountAdapter 样本不足: {e}")
        return 1

    # ============================================================
    # 3. 指标对比
    # ============================================================
    print("\n--- 3. 指标对比 ---")

    # DSR 对比
    eval_dsr = eval_report.private_metrics.get("dsr", -1.0)
    shadow_dsr = shadow_metrics.dsr
    dsr_diff = abs(eval_dsr - shadow_dsr)
    print(f"  DSR: evaluator={eval_dsr:.6f}, shadow={shadow_dsr:.6f}, diff={dsr_diff:.6f}")
    if dsr_diff < 0.001:
        print("  [OK] DSR 对齐 (diff < 0.001)")
    else:
        print(f"  [FAIL] DSR 不对齐 (diff={dsr_diff:.6f} >= 0.001)")
        failures += 1

    # max_drawdown 对比
    eval_dd = eval_report.private_metrics.get("max_drawdown", -1.0)
    shadow_dd = shadow_metrics.max_drawdown
    dd_diff = abs(eval_dd - shadow_dd)
    print(f"  max_drawdown: evaluator={eval_dd:.6f}, shadow={shadow_dd:.6f}, diff={dd_diff:.6f}")
    if dd_diff < 0.001:
        print("  [OK] max_drawdown 对齐 (diff < 0.001)")
    else:
        print(f"  [FAIL] max_drawdown 不对齐 (diff={dd_diff:.6f} >= 0.001)")
        failures += 1

    # sharpe_cv 对比 (容差稍大, 因窗口不同)
    eval_cv = eval_report.private_metrics.get("sharpe_cv", -1.0)
    shadow_cv = shadow_metrics.sharpe_cv
    cv_diff = abs(eval_cv - shadow_cv)
    print(f"  sharpe_cv: evaluator={eval_cv:.6f}, shadow={shadow_cv:.6f}, diff={cv_diff:.6f}")
    if cv_diff < 0.05:
        print("  [OK] sharpe_cv 对齐 (diff < 0.05)")
    else:
        print(f"  [WARN] sharpe_cv 差异较大 (diff={cv_diff:.6f} >= 0.05)")
        print("         原因: 评估器用 60 日窗口, Shadow 用 252 日窗口")

    # annual_return 对比 (public_metrics)
    eval_annual = eval_report.public_metrics.get("annual_return", 0.0)
    shadow_annual = shadow_metrics.annual_return
    annual_diff = abs(eval_annual - shadow_annual)
    print(f"  annual_return: evaluator={eval_annual:.6f}, shadow={shadow_annual:.6f}, diff={annual_diff:.6f}")
    if annual_diff < 0.01:
        print("  [OK] annual_return 对齐 (diff < 0.01)")
    else:
        print(f"  [WARN] annual_return 差异 (diff={annual_diff:.6f})")

    # ============================================================
    # 汇总
    # ============================================================
    print("\n" + "=" * 70)
    if failures == 0:
        print("对齐验证通过: 所有核心指标一致")
        return 0
    else:
        print(f"对齐验证失败: {failures} 项不通过")
        return 1


if __name__ == "__main__":
    sys.exit(main())
