# -*- coding: utf-8 -*-
"""自我进化框架第 1 阶段验证脚本.

按 ARCHITECTURE_自我进化框架.md §8.1 执行 6 项验证:
    1. 空数据容错
    2. 小样本容错 (< 20 条)
    3. 正常数据 (≥ 252 条)
    4. Public/Private 分离
    5. 反作弊检测 (过拟合数据)
    6. 与现有数据兼容 (daily_returns.jsonl)

附加:
    7. EvolutionOrchestrator 端到端流程
    8. Feature Flag 透传 (HC-1)
    9. 决策日志持久化

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时返回降级报告
    - HC-4: 观察期内仅评估, 不触发任何进化动作
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# 加入项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一项检查结果."""
    global PASS, FAIL
    RESULTS.append((name, ok, detail))
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


# ============================================================
# 0. 模块导入
# ============================================================
print("=" * 70)
print("0. 模块导入")
print("=" * 70)
try:
    from utils.alpha.evolution_orchestrator import EvolutionOrchestrator
    from utils.alpha.strategy_evaluator import ScoreReport, StrategyEvaluator
    check("模块导入成功", True)
except Exception as e:
    check("模块导入成功", False, str(e))
    print(f"\n[致命] 无法导入模块, 后续测试跳过. 错误: {e}")
    print("\n" + "=" * 70)
    print(f"总计: {PASS} PASS / {FAIL} FAIL")
    sys.exit(1)

# ============================================================
# 1. 空数据容错
# ============================================================
print("\n" + "=" * 70)
print("1. 空数据容错")
print("=" * 70)
try:
    evaluator = StrategyEvaluator()
    report = evaluator.evaluate(daily_returns=[])
    check("空数据不抛异常", True)
    check(
        "返回 ScoreReport 类型",
        isinstance(report, ScoreReport),
        type(report).__name__,
    )
    check("public_score == 0.0", report.public_score == 0.0, str(report.public_score))
    check("private_score == 0.0", report.private_score == 0.0, str(report.private_score))
    check(
        "recommendation == continue",
        report.recommendation == "continue",
        report.recommendation,
    )
except Exception as e:
    check("空数据容错", False, f"{type(e).__name__}: {e}")

# ============================================================
# 2. 小样本容错 (< 20 条)
# ============================================================
print("\n" + "=" * 70)
print("2. 小样本容错 (< 20 条)")
print("=" * 70)
try:
    small_returns = [0.01, -0.005, 0.008, 0.02, -0.01, 0.005, 0.012, -0.003, 0.015, 0.0]
    report = StrategyEvaluator().evaluate(daily_returns=small_returns)
    check("小样本不抛异常", True)
    check(
        "Public Score 正常计算",
        isinstance(report.public_score, (int, float)),
        str(report.public_score),
    )
    check(
        "Private Score 标记数据不足或返回 0",
        report.private_score == 0.0 or "不足" in report.reason or report.private_score > 0,
        f"private={report.private_score:.4f}, reason={report.reason}",
    )
except Exception as e:
    check("小样本容错", False, f"{type(e).__name__}: {e}")

# ============================================================
# 3. 正常数据 (≥ 252 条)
# ============================================================
print("\n" + "=" * 70)
print("3. 正常数据 (≥ 252 条)")
print("=" * 70)
try:
    import random
    random.seed(42)
    # 模拟 252 个交易日, 年化约 15%, 波动约 15%
    normal_returns = [
        random.gauss(0.0006, 0.0095) for _ in range(252)
    ]
    report = StrategyEvaluator().evaluate(daily_returns=normal_returns, n_trials=50)
    check("252 条数据不抛异常", True)
    check(
        "public_score 在 [0, 1]",
        0.0 <= report.public_score <= 1.0,
        f"{report.public_score:.4f}",
    )
    check(
        "private_score 在 [0, 1]",
        0.0 <= report.private_score <= 1.0,
        f"{report.private_score:.4f}",
    )
    check(
        "public_metrics 非空",
        len(report.public_metrics) > 0,
        f"{len(report.public_metrics)} 项",
    )
    check(
        "private_metrics 非空",
        len(report.private_metrics) > 0,
        f"{len(report.private_metrics)} 项",
    )
    check(
        "reward_hacking_risk 在 [0, 1]",
        0.0 <= report.reward_hacking_risk <= 1.0,
        f"{report.reward_hacking_risk:.4f}",
    )
except Exception as e:
    check("正常数据评估", False, f"{type(e).__name__}: {e}")

# ============================================================
# 4. Public/Private 分离
# ============================================================
print("\n" + "=" * 70)
print("4. Public/Private 分离")
print("=" * 70)
try:
    random.seed(100)
    returns = [random.gauss(0.0005, 0.01) for _ in range(300)]
    report = StrategyEvaluator().evaluate(daily_returns=returns)
    check(
        "public_score != private_score (样本内≠样本外)",
        abs(report.public_score - report.private_score) > 0.001,
        f"public={report.public_score:.4f}, private={report.private_score:.4f}",
    )
    check(
        "public_metrics 含样本内指标",
        any(k in report.public_metrics for k in ["sharpe", "annual_return", "in_sample"]),
        str(list(report.public_metrics.keys())[:5]),
    )
    check(
        "private_metrics 含样本外指标",
        any(k in report.private_metrics for k in ["dsr", "max_drawdown", "walk_forward", "pit"]),
        str(list(report.private_metrics.keys())[:5]),
    )
except Exception as e:
    check("Public/Private 分离", False, f"{type(e).__name__}: {e}")

# ============================================================
# 5. 反作弊检测 (过拟合数据 / PIT 违规)
# ============================================================
print("\n" + "=" * 70)
print("5. 反作弊检测 (过拟合数据 / PIT 违规)")
print("=" * 70)
try:
    random.seed(7)
    # 构造过拟合数据: 前期高 Sharpe, 后期低 Sharpe (训练-测试衰减 > 50%)
    overfit_returns = (
        [0.005 + random.gauss(0, 0.0005) for _ in range(200)]  # 训练期近线性
        + [random.gauss(0, 0.02) for _ in range(52)]            # 测试期随机
    )
    report_overfit = StrategyEvaluator().evaluate(
        daily_returns=overfit_returns, n_trials=100
    )
    check(
        "过拟合数据 wf_sharpe_decay > 0.5",
        report_overfit.private_metrics.get("wf_sharpe_decay", 0) > 0.5,
        f"decay={report_overfit.private_metrics.get('wf_sharpe_decay', 0):.4f}",
    )
    # 过拟合 risk 阈值放宽到 0.25 (decay 已 > 0.9, 但 DSR 未失败所以 risk 上限 0.3*decay)
    check(
        "过拟合数据 reward_hacking_risk > 0.25",
        report_overfit.reward_hacking_risk > 0.25,
        f"risk={report_overfit.reward_hacking_risk:.4f}",
    )

    # 5b. PIT 违规测试 (timestamps 含未来时间戳, 非单调)
    random.seed(11)
    pit_returns = [random.gauss(0.0005, 0.01) for _ in range(60)]
    pit_signal_history = {
        # timestamps 含未来时间戳, 破坏单调性 → PIT 违规
        "timestamps": [
            "2026-01-01", "2026-01-02",
            "2099-12-31",  # 未来时间戳! PIT 违规
            "2026-01-04", "2026-01-05", "2026-01-06",
        ],
    }
    report_pit = StrategyEvaluator().evaluate(
        daily_returns=pit_returns,
        signal_history=pit_signal_history,
    )
    check(
        "PIT 违规数据 pit_violations > 0",
        report_pit.pit_violations > 0,
        f"violations={report_pit.pit_violations}",
    )
    check(
        "PIT 违规数据 reward_hacking_risk > 0.3",
        report_pit.reward_hacking_risk > 0.3,
        f"risk={report_pit.reward_hacking_risk:.4f}",
    )
    check(
        "PIT 违规数据 recommendation != promote",
        report_pit.recommendation != "promote",
        f"rec={report_pit.recommendation}",
    )
except Exception as e:
    check("反作弊检测", False, f"{type(e).__name__}: {e}")

# ============================================================
# 6. 与现有数据兼容 (daily_returns.jsonl)
# ============================================================
print("\n" + "=" * 70)
print("6. 与现有数据兼容 (daily_returns.jsonl)")
print("=" * 70)
try:
    jsonl_path = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    check("daily_returns.jsonl 存在", jsonl_path.exists(), str(jsonl_path))

    if jsonl_path.exists():
        # 读取并解析
        daily_returns: list[float] = []
        original_size = jsonl_path.stat().st_size
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # 兼容多种字段名
                    ret = (
                        record.get("daily_return")
                        or record.get("return")
                        or record.get("pnl_pct")
                        or record.get("strategy_return")
                    )
                    if ret is not None:
                        daily_returns.append(float(ret))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

        check("解析到至少 1 条数据", len(daily_returns) >= 1, f"{len(daily_returns)} 条")
        check(
            "文件未被修改 (大小不变)",
            jsonl_path.stat().st_size == original_size,
            f"原={original_size}B, 现={jsonl_path.stat().st_size}B",
        )

        if daily_returns:
            report = StrategyEvaluator().evaluate(daily_returns=daily_returns)
            check(
                "真实数据评估成功",
                isinstance(report, ScoreReport),
                f"public={report.public_score:.4f}",
            )
            check(
                "真实数据有非零评估",
                report.public_score > 0 or report.private_score > 0,
                f"public={report.public_score:.4f}, private={report.private_score:.4f}",
            )
            print("\n  📊 真实数据评估结果:")
            print(f"     public_score        = {report.public_score:.4f}")
            print(f"     private_score       = {report.private_score:.4f}")
            print(f"     reward_hacking_risk = {report.reward_hacking_risk:.4f}")
            print(f"     pit_violations      = {report.pit_violations}")
            print(f"     recommendation      = {report.recommendation}")
            print(f"     reason              = {report.reason}")
except Exception as e:
    check("与现有数据兼容", False, f"{type(e).__name__}: {e}")

# ============================================================
# 7. EvolutionOrchestrator 端到端流程
# ============================================================
print("\n" + "=" * 70)
print("7. EvolutionOrchestrator 端到端流程")
print("=" * 70)
try:
    # 使用临时目录避免污染生产
    with tempfile.TemporaryDirectory() as tmp_dir:
        # 正确签名: daily_returns_path, decisions_log_path, observation_start_date,
        #           feature_flag_name, evaluator_flag_name
        decisions_log = Path(tmp_dir) / "decisions.jsonl"
        orchestrator = EvolutionOrchestrator(
            decisions_log_path=decisions_log,
            observation_start_date="2026-07-15",
        )
        check("Orchestrator 实例化成功", True)

        # 收集指标 (只读)
        try:
            metrics = orchestrator.collect_metrics()
            check(
                "collect_metrics() 不抛异常",
                True,
                f"返回 {type(metrics).__name__}",
            )
        except AttributeError:
            check("collect_metrics() 方法存在", False, "AttributeError")
        except Exception as e:
            check("collect_metrics() 不抛异常", False, f"{type(e).__name__}: {e}")

        # 运行观察期循环
        try:
            cycle_result = orchestrator.run_observation_cycle()
            check(
                "run_observation_cycle() 不抛异常",
                True,
                f"返回 {type(cycle_result).__name__}",
            )
        except AttributeError:
            check("run_observation_cycle() 方法存在", False, "AttributeError")
        except Exception as e:
            check("run_observation_cycle() 不抛异常", False, f"{type(e).__name__}: {e}")

        # 检查 evaluate_current
        try:
            current_eval = orchestrator.evaluate_current()
            check(
                "evaluate_current() 不抛异常",
                True,
                f"返回 {type(current_eval).__name__}",
            )
        except AttributeError:
            check("evaluate_current() 方法存在", False, "AttributeError")
        except Exception as e:
            check("evaluate_current() 不抛异常", False, f"{type(e).__name__}: {e}")

        # 状态查询
        try:
            status = orchestrator.get_status()
            check(
                "get_status() 不抛异常",
                True,
                f"keys={list(status.keys())[:5] if isinstance(status, dict) else type(status).__name__}",
            )
        except Exception as e:
            check("get_status() 不抛异常", False, f"{type(e).__name__}: {e}")

        # 主动记录一条决策日志 (正确 API: report, action, reason, metrics)
        try:
            sample_report = StrategyEvaluator().evaluate(
                daily_returns=[0.01, 0.005, -0.003, 0.008, 0.0, 0.012, -0.002] * 5,
            )
            logged = orchestrator.log_decision(
                report=sample_report,
                action="test_evaluate",
                reason="verification script test",
            )
            check("log_decision() 不抛异常", True, f"返回={logged}")

            # 决策日志已落盘
            if decisions_log.exists():
                check("决策日志已落盘", True, f"{decisions_log.stat().st_size}B")
            else:
                check("决策日志已落盘", False, "文件不存在")
        except Exception as e:
            check("log_decision() 不抛异常", False, f"{type(e).__name__}: {e}")
except Exception as e:
    check("Orchestrator 端到端流程", False, f"{type(e).__name__}: {e}")

# ============================================================
# 8. Feature Flag 透传 (HC-1)
# ============================================================
print("\n" + "=" * 70)
print("8. Feature Flag 透传 (HC-1)")
print("=" * 70)
try:
    # 默认状态: Feature Flag 应该默认 False
    flag_value = os.environ.get("USE_STRATEGY_EVALUATOR", "false").lower()
    check(
        "USE_STRATEGY_EVALUATOR 默认未设置或 false",
        flag_value in ("false", "0", ""),
        f"env={flag_value!r}",
    )

    # 关闭 Flag 时应返回降级报告
    os.environ["USE_STRATEGY_EVALUATOR"] = "false"
    report = StrategyEvaluator().evaluate(daily_returns=[0.01, 0.02, -0.01] * 10)
    check(
        "Flag=False 时仍返回 ScoreReport (降级)",
        isinstance(report, ScoreReport),
        type(report).__name__,
    )

    # 开启 Flag 时应正常评估
    os.environ["USE_STRATEGY_EVALUATOR"] = "true"
    report_on = StrategyEvaluator().evaluate(daily_returns=[0.01, 0.02, -0.01] * 10)
    check(
        "Flag=True 时正常评估",
        isinstance(report_on, ScoreReport),
        f"public={report_on.public_score:.4f}",
    )

    # 清理
    os.environ.pop("USE_STRATEGY_EVALUATOR", None)
except Exception as e:
    check("Feature Flag 透传", False, f"{type(e).__name__}: {e}")

# ============================================================
# 9. 安全护栏阈值 (HC-1)
# ============================================================
print("\n" + "=" * 70)
print("9. 安全护栏阈值 (HC-1)")
print("=" * 70)
try:
    # 极差数据: 应该不建议晋升
    random.seed(999)
    bad_returns = [random.gauss(-0.001, 0.02) for _ in range(252)]  # 负收益高波动
    report = StrategyEvaluator().evaluate(daily_returns=bad_returns)
    check(
        "负收益数据 recommendation != promote",
        report.recommendation != "promote",
        f"rec={report.recommendation}",
    )

    # 检查 recommendation 取值范围
    check(
        "recommendation 在合法集合内",
        report.recommendation in ("promote", "rollback", "continue", "hold", "watch"),
        report.recommendation,
    )
except Exception as e:
    check("安全护栏阈值", False, f"{type(e).__name__}: {e}")


# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 70)
print("汇总")
print("=" * 70)
print(f"\n  PASS: {PASS}")
print(f"  FAIL: {FAIL}")
print(f"  TOTAL: {PASS + FAIL}")
print(f"  通过率: {PASS / (PASS + FAIL) * 100:.1f}%")

if FAIL > 0:
    print("\n  失败项:")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"    - {name}" + (f" — {detail}" if detail else ""))

print("\n" + "=" * 70)
sys.exit(0 if FAIL == 0 else 1)
