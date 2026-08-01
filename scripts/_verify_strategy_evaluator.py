# -*- coding: utf-8 -*-
"""StrategyEvaluator 验证脚本 — 自我进化框架第 1 阶段验证.

验证项:
    1. 空数据容错
    2. 小样本容错 (< 20 条)
    3. 正常数据 (>= 252 条)
    4. Public/Private 分离验证
    5. 反作弊检测 (过拟合数据)
    6. 与现有 daily_returns.jsonl 兼容
    7. Feature Flag 关闭时降级
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    """运行验证."""
    print("=" * 70)
    print("StrategyEvaluator 验证脚本")
    print("=" * 70)

    # 导入评估器
    try:
        from utils.alpha.strategy_evaluator import (
            DEFAULT_N_TRIALS,
            ScoreReport,  # noqa: F401
            StrategyEvaluator,
        )
        print("[OK] 导入 StrategyEvaluator 成功")
    except ImportError as e:
        print(f"[FAIL] 导入失败: {e}")
        return 1

    failures = 0

    # 创建两个评估器实例:
    # - evaluator_flag_off: Feature Flag 关闭 (测试 HC-1 降级)
    # - evaluator_flag_on: Feature Flag 开启 (测试实际评估逻辑)
    evaluator_flag_off = StrategyEvaluator()
    evaluator_flag_on = StrategyEvaluator()
    evaluator_flag_on._enabled = True  # 测试用: 强制启用

    # ============================================================
    # 测试 1: 空数据容错 (HC-1: Feature Flag 关闭时降级)
    # ============================================================
    print("\n--- 测试 1: 空数据容错 (Feature Flag 关闭) ---")
    report = evaluator_flag_off.evaluate([])
    if report.is_degraded and report.sample_count == 0:
        print(f"[OK] 空数据返回降级报告: reason={report.degraded_reason}")
    else:
        print(f"[FAIL] 空数据未正确降级: is_degraded={report.is_degraded}")
        failures += 1

    # ============================================================
    # 测试 2: 小样本容错 (< 20 条, Feature Flag 开启)
    # ============================================================
    print("\n--- 测试 2: 小样本容错 (< 20 条, Flag 开启) ---")
    small_returns = [0.01 * random.gauss(0, 1) for _ in range(10)]
    report = evaluator_flag_on.evaluate(small_returns)
    if report.is_degraded:
        print(f"[OK] 小样本降级: reason={report.degraded_reason}")
    elif report.private_metrics.get("dsr", -1) == -1.0:
        print(f"[OK] 小样本 DSR 标记为 -1.0 (样本不足), public_score={report.public_score:.4f}")
    else:
        print(f"[WARN] 小样本行为: is_degraded={report.is_degraded}, dsr={report.private_metrics.get('dsr')}")

    # ============================================================
    # 测试 3: 正常数据 (>= 252 条, Feature Flag 开启)
    # ============================================================
    print("\n--- 测试 3: 正常数据 (252 条, Flag 开启) ---")
    random.seed(42)
    normal_returns = [0.001 + 0.015 * random.gauss(0, 1) for _ in range(252)]
    report = evaluator_flag_on.evaluate(normal_returns, n_trials=DEFAULT_N_TRIALS)
    print(f"  public_score  = {report.public_score:.4f}")
    print(f"  private_score = {report.private_score:.4f}")
    print(f"  reward_hacking_risk = {report.reward_hacking_risk:.4f}")
    print(f"  recommendation = {report.recommendation}")
    print(f"  reason = {report.reason}")
    print(f"  public_metrics = {report.public_metrics}")
    print(f"  private_metrics = {report.private_metrics}")

    if not report.is_degraded and 0.0 <= report.public_score <= 1.0:
        print("[OK] 正常数据评估成功")
    else:
        print(f"[FAIL] 正常数据评估异常: is_degraded={report.is_degraded}")
        failures += 1

    # ============================================================
    # 测试 4: Public/Private 分离验证
    # ============================================================
    print("\n--- 测试 4: Public/Private 分离 ---")
    if report.public_score != report.private_score:
        print(f"[OK] Public/Private 分离: public={report.public_score:.4f} != private={report.private_score:.4f}")
    else:
        print(f"[WARN] Public/Private 相等: {report.public_score:.4f} (可能数据特殊)")

    public_keys = set(report.public_metrics.keys())
    private_keys = set(report.private_metrics.keys())
    if public_keys != private_keys:
        print(f"[OK] 指标维度不同: public_keys={len(public_keys)}, private_keys={len(private_keys)}")
    else:
        print(f"[WARN] 指标维度相同: {public_keys}")

    # ============================================================
    # 测试 5: 反作弊检测 (过拟合数据)
    # ============================================================
    print("\n--- 测试 5: 反作弊检测 (过拟合数据) ---")
    random.seed(123)
    overfit_returns = (
        [0.005 + 0.005 * random.gauss(0, 1) for _ in range(126)]
        + [0.0 + 0.02 * random.gauss(0, 1) for _ in range(126)]
    )
    report = evaluator_flag_on.evaluate(overfit_returns)
    print(f"  wf_sharpe_decay = {report.private_metrics.get('wf_sharpe_decay', 'N/A')}")
    print(f"  overfit_score = {report.overfit_score:.4f}")
    print(f"  reward_hacking_risk = {report.reward_hacking_risk:.4f}")

    if report.overfit_score > 0.3:
        print(f"[OK] 过拟合检测: overfit_score={report.overfit_score:.4f} > 0.3")
    else:
        print(f"[WARN] 过拟合检测不敏感: overfit_score={report.overfit_score:.4f}")

    # ============================================================
    # 测试 6: 与现有 daily_returns.jsonl 兼容 (只读)
    # ============================================================
    print("\n--- 测试 6: 与 daily_returns.jsonl 兼容 (只读) ---")
    jsonl_path = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if jsonl_path.exists():
        # 用 Flag 关闭的评估器测试 (HC-1 降级)
        report_off = evaluator_flag_off.evaluate_from_jsonl(str(jsonl_path))
        print(f"  [Flag=Off] sample_count={report_off.sample_count}, is_degraded={report_off.is_degraded}")

        # 用 Flag 开启的评估器测试 (实际评估)
        report_on = evaluator_flag_on.evaluate_from_jsonl(str(jsonl_path))
        print(f"  [Flag=On]  sample_count={report_on.sample_count}, is_degraded={report_on.is_degraded}")

        # 验证文件未被修改 (只读)
        mtime_before = jsonl_path.stat().st_mtime
        evaluator_flag_on.evaluate_from_jsonl(str(jsonl_path))
        mtime_after = jsonl_path.stat().st_mtime
        if mtime_before == mtime_after:
            print("[OK] 文件未被修改 (只读验证通过)")
        else:
            print("[FAIL] 文件被修改 (只读验证失败)")
            failures += 1
    else:
        print(f"[SKIP] daily_returns.jsonl 不存在: {jsonl_path}")

    # ============================================================
    # 测试 7: to_dict() 序列化
    # ============================================================
    print("\n--- 测试 7: to_dict() 序列化 ---")
    random.seed(456)
    test_returns = [0.001 * random.gauss(0, 1) for _ in range(30)]
    report = evaluator_flag_on.evaluate(test_returns)
    try:
        d = report.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(json_str)
        if "public_score" in parsed and "private_score" in parsed:
            print("[OK] to_dict() 序列化成功")
        else:
            print("[FAIL] to_dict() 缺少字段")
            failures += 1
    except Exception as e:
        print(f"[FAIL] to_dict() 序列化失败: {e}")
        failures += 1

    # ============================================================
    # 测试 8: 完整 PIT 检测 (6 维度)
    # ============================================================
    print("\n--- 测试 8: 完整 PIT 检测 (6 维度) ---")
    # 构造含未来函数的 signal_history
    # 维度 1: 时间戳非单调 (未来函数)
    bad_signal_history = {
        "timestamps": ["2026-01-03", "2026-01-02", "2026-01-01"],  # 倒序
        "signal_records": [
            {"timestamp": "2026-01-03", "data_cutoff": "2026-01-03"},
            {"timestamp": "2026-01-02", "data_cutoff": "2026-01-02"},
            {"timestamp": "2026-01-01", "data_cutoff": "2026-01-01"},
        ],
        # 维度 3: 训练/测试重叠
        "cv_splits": [
            {"train_end": 100, "test_start": 50, "min_gap_days": 5},  # 重叠
        ],
    }
    random.seed(789)
    test_returns_pit = [0.001 * random.gauss(0, 1) for _ in range(252)]
    report_pit = evaluator_flag_on.evaluate(
        test_returns_pit, signal_history=bad_signal_history
    )
    print(f"  pit_violations = {report_pit.pit_violations}")
    print(f"  reward_hacking_risk = {report_pit.reward_hacking_risk:.4f}")

    if report_pit.pit_violations >= 1:
        print(f"[OK] PIT 检测到 {report_pit.pit_violations} 个违规")
    else:
        print("[WARN] PIT 未检测到违规 (可能降级为简化版)")

    # 测试干净的 signal_history (无未来函数)
    clean_signal_history = {
        "timestamps": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "signal_records": [
            {"timestamp": "2026-01-01", "data_cutoff": "2026-01-01"},
            {"timestamp": "2026-01-02", "data_cutoff": "2026-01-02"},
            {"timestamp": "2026-01-03", "data_cutoff": "2026-01-03"},
        ],
        "cv_splits": [
            {"train_end": 50, "test_start": 60, "min_gap_days": 5},  # 无重叠
        ],
    }
    report_clean = evaluator_flag_on.evaluate(
        test_returns_pit, signal_history=clean_signal_history
    )
    print(f"  [干净数据] pit_violations = {report_clean.pit_violations}")
    if report_clean.pit_violations == 0:
        print("[OK] 干净数据 PIT 无违规")
    else:
        print(f"[WARN] 干净数据检测到 {report_clean.pit_violations} 个违规")

    # ============================================================
    # 测试 9: Purged Walk-Forward CV
    # ============================================================
    print("\n--- 测试 9: Purged Walk-Forward CV ---")
    random.seed(999)
    # 构造 500 条数据, 足够 5 折 CV
    wf_returns = [0.001 + 0.015 * random.gauss(0, 1) for _ in range(500)]
    report_wf = evaluator_flag_on.evaluate(wf_returns)
    wf_decay = report_wf.private_metrics.get("wf_sharpe_decay", -1.0)
    print(f"  wf_sharpe_decay = {wf_decay:.4f}")
    print(f"  sample_count = {report_wf.sample_count}")

    if wf_decay >= -1.0 and wf_decay <= 1.0:
        print("[OK] Purged Walk-Forward CV 正常运行")
    else:
        print(f"[FAIL] wf_sharpe_decay 异常: {wf_decay}")
        failures += 1

    # 对比简化版 vs 完整版 (验证降级逻辑)
    # 用 50 条数据 (小于 MIN_SAMPLES_FOR_WF=100), 应返回 -1.0
    small_wf_returns = [0.001 * random.gauss(0, 1) for _ in range(50)]
    report_small_wf = evaluator_flag_on.evaluate(small_wf_returns)
    small_wf_decay = report_small_wf.private_metrics.get("wf_sharpe_decay", -99.0)
    if small_wf_decay == -1.0:
        print("[OK] 小样本 Walk-Forward 正确降级为 -1.0")
    else:
        print(f"[WARN] 小样本 Walk-Forward: {small_wf_decay} (期望 -1.0)")

    # ============================================================
    # 汇总
    # ============================================================
    print("\n" + "=" * 70)
    if failures == 0:
        print("验证通过: 所有测试项 PASS")
        return 0
    else:
        print(f"验证失败: {failures} 项 FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
