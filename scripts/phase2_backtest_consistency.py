# -*- coding: utf-8 -*-
"""Phase 2 双环境回测一致性验证脚本
=====================================

在 Python 3.8 和 Python 3.14 环境中分别运行相同的回测,
对比关键指标的一致性, 验证 Python 版本升级不会改变回测结果.

用法:
    # Step 1: Python 3.8 环境运行
    python scripts/phase2_backtest_consistency.py run --output reports/phase2/results_py38.json

    # Step 2: Python 3.14 环境运行
    .venv\\Scripts\\python.exe scripts/phase2_backtest_consistency.py run --output reports/phase2/results_py314.json

    # Step 3: 对比结果 (任意环境)
    python scripts/phase2_backtest_consistency.py compare \\
        --py38 reports/phase2/results_py38.json \\
        --py314 reports/phase2/results_py314.json

验收标准 (对齐 README Phase 2):
    - 每个指标的相对误差 < 1%
    - 所有测试组全部 PASS 才算通过
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 确定性测试数据生成
# ============================================================
def generate_deterministic_returns(
    n_days: int = 504,
    n_assets: int = 10,
    seed: int = 42,
    annual_vol: float = 0.15,
    annual_return: float = 0.08,
) -> np.ndarray:
    """生成确定性收益率序列 (固定 seed, 保证两个环境数据完全相同).

    生成多资产收益率后等权聚合为一维组合收益率, 满足 FastBacktest
    对一维输入的要求, 同时保留多资产的随机结构.

    Args:
        n_days: 天数 (默认 504 = 2 年)
        n_assets: 标的数 (默认 10)
        seed: 随机种子 (默认 42)
        annual_vol: 年化波动率
        annual_return: 年化收益率

    Returns:
        (n_days,) 一维组合收益率数组
    """
    rng = np.random.RandomState(seed)
    daily_vol = annual_vol / np.sqrt(252)
    daily_return = annual_return / 252
    # 多资产收益率 (n_days, n_assets)
    multi_returns = rng.randn(n_days, n_assets) * daily_vol + daily_return
    # 等权组合 → 一维收益率序列 (分散化后波动率降低)
    portfolio_returns = multi_returns.mean(axis=1)
    return portfolio_returns


# ============================================================
# 回测测试组定义
# ============================================================
@dataclass
class BacktestTestCase:
    """单个回测测试用例."""
    name: str
    n_days: int
    n_assets: int
    seed: int
    train_months: int
    test_months: int
    step_months: int
    cv_folds: int
    n_trials: int


# 5 组测试用例, 覆盖不同数据规模和参数
TEST_CASES: list[BacktestTestCase] = [
    BacktestTestCase(
        name="basic_2y_10assets",
        n_days=504, n_assets=10, seed=42,
        train_months=12, test_months=3, step_months=3,
        cv_folds=3, n_trials=10,
    ),
    BacktestTestCase(
        name="longer_3y_20assets",
        n_days=756, n_assets=20, seed=123,
        train_months=18, test_months=3, step_months=3,
        cv_folds=5, n_trials=15,
    ),
    BacktestTestCase(
        name="short_1y_5assets",
        n_days=252, n_assets=5, seed=999,
        train_months=6, test_months=2, step_months=2,
        cv_folds=3, n_trials=5,
    ),
    BacktestTestCase(
        name="high_vol_2y_15assets",
        n_days=504, n_assets=15, seed=777,
        train_months=12, test_months=3, step_months=3,
        cv_folds=5, n_trials=10,
    ),
    BacktestTestCase(
        name="large_4y_30assets",
        n_days=1008, n_assets=30, seed=555,
        train_months=24, test_months=3, step_months=3,
        cv_folds=5, n_trials=20,
    ),
]


# ============================================================
# 回测执行
# ============================================================
def run_single_backtest(case: BacktestTestCase) -> dict[str, Any]:
    """运行单个回测测试用例.

    Args:
        case: 回测测试配置

    Returns:
        包含回测指标和元信息的字典
    """
    # 延迟导入, 避免模块加载阶段触发依赖链问题
    from utils.alpha.fast_backtest import BacktestConfig, FastBacktest

    # 生成确定性数据
    returns = generate_deterministic_returns(
        n_days=case.n_days,
        n_assets=case.n_assets,
        seed=case.seed,
    )

    # 配置回测
    config = BacktestConfig(
        train_months=case.train_months,
        test_months=case.test_months,
        step_months=case.step_months,
        cv_folds=case.cv_folds,
    )

    # 运行回测
    engine = FastBacktest(config)
    result = engine.run(returns=returns, n_trials=case.n_trials)

    # 提取关键指标
    metrics = {
        "annual_return": float(result.annual_return),
        "annual_vol": float(result.annual_vol),
        "sharpe": float(result.sharpe),
        "sortino": float(result.sortino),
        "calmar": float(result.calmar),
        "max_drawdown": float(result.max_drawdown),
        "win_rate": float(result.win_rate),
        "dsr": float(result.dsr),
        "ic_ir": float(result.ic_ir),
        "sharpe_cv": float(result.sharpe_cv),
    }

    return {
        "name": case.name,
        "config": asdict(case),
        "metrics": metrics,
    }


def run_all_backtests(output_path: str) -> None:
    """运行所有回测测试用例并保存结果.

    Args:
        output_path: 结果 JSON 输出路径
    """
    print(f"Python 版本: {sys.version}")
    print(f"NumPy 版本: {np.__version__}")
    print(f"运行 {len(TEST_CASES)} 组回测测试...")

    results: list[dict[str, Any]] = []
    for i, case in enumerate(TEST_CASES, 1):
        print(f"  [{i}/{len(TEST_CASES)}] 运行 {case.name}...", end=" ")
        try:
            result = run_single_backtest(case)
            results.append(result)
            print(f"✅ sharpe={result['metrics']['sharpe']:.4f}")
        except Exception as e:
            print(f"❌ 失败: {e}")
            results.append({
                "name": case.name,
                "config": asdict(case),
                "error": str(e),
            })

    # 保存结果
    output = {
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "timestamp": datetime.now().isoformat(),
        "results": results,
    }

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n结果已保存到: {output_file}")


# ============================================================
# 结果对比
# ============================================================
# 对比容差: 相对误差阈值 (1% = 0.01, 对齐 README Phase 2 验收标准)
TOLERANCE_REL = 0.01
# 绝对误差阈值 (用于接近 0 的指标)
TOLERANCE_ABS = 0.001

# 关键对比指标 (按重要性排序)
COMPARE_METRICS = [
    "annual_return",
    "annual_vol",
    "sharpe",
    "max_drawdown",
    "dsr",
    "ic_ir",
    "sharpe_cv",
    "sortino",
    "calmar",
    "win_rate",
]


@dataclass
class MetricComparison:
    """单个指标的对比结果."""
    name: str
    py38_value: float
    py314_value: float
    abs_diff: float
    rel_diff: float
    passed: bool


@dataclass
class TestCaseComparison:
    """单个测试用例的对比结果."""
    name: str
    metrics: list[MetricComparison] = field(default_factory=list)
    all_passed: bool = True
    error: str | None = None


def compare_values(name: str, v38: float, v314: float) -> MetricComparison:
    """对比两个指标值.

    判定逻辑:
        1. 绝对误差 < TOLERANCE_ABS → PASS (处理接近 0 的值)
        2. 相对误差 < TOLERANCE_REL → PASS
        3. 否则 → FAIL
    """
    abs_diff = abs(v314 - v38)

    # 处理接近 0 的值 (绝对误差判定)
    if abs(v38) < TOLERANCE_ABS and abs(v314) < TOLERANCE_ABS:
        rel_diff = 0.0
        passed = True
    elif abs(v38) < TOLERANCE_ABS:
        # py38 接近 0 但 py314 不接近 0
        rel_diff = float("inf") if v314 != 0 else 0.0
        passed = abs_diff < TOLERANCE_ABS
    else:
        rel_diff = abs_diff / abs(v38)
        passed = rel_diff < TOLERANCE_REL or abs_diff < TOLERANCE_ABS

    return MetricComparison(
        name=name,
        py38_value=v38,
        py314_value=v314,
        abs_diff=abs_diff,
        rel_diff=rel_diff,
        passed=passed,
    )


def compare_results(py38_path: str, py314_path: str) -> None:
    """对比两个环境的回测结果.

    Args:
        py38_path: Python 3.8 结果 JSON 路径
        py314_path: Python 3.14 结果 JSON 路径
    """
    # 加载结果
    with open(py38_path, encoding="utf-8") as f:
        data38 = json.load(f)
    with open(py314_path, encoding="utf-8") as f:
        data314 = json.load(f)

    print("=" * 80)
    print("Phase 2 双环境回测一致性对比报告")
    print("=" * 80)
    print(f"Python 3.8 : {data38['python_version']}")
    print(f"  NumPy    : {data38['numpy_version']}")
    print(f"Python 3.14: {data314['python_version']}")
    print(f"  NumPy    : {data314['numpy_version']}")
    print(f"容差标准  : 相对误差 < {TOLERANCE_REL:.0%} 或 绝对误差 < {TOLERANCE_ABS}")
    print("-" * 80)

    # 按测试用例名称建立索引
    results38 = {r["name"]: r for r in data38["results"]}
    results314 = {r["name"]: r for r in data314["results"]}

    all_cases: list[TestCaseComparison] = []
    total_metrics = 0
    passed_metrics = 0

    for case_name in [c.name for c in TEST_CASES]:
        r38 = results38.get(case_name)
        r314 = results314.get(case_name)

        comparison = TestCaseComparison(name=case_name)

        if r38 is None or r314 is None:
            comparison.error = f"测试用例 {case_name} 在某环境缺失"
            comparison.all_passed = False
            all_cases.append(comparison)
            continue

        if "error" in r38 or "error" in r314:
            err38 = r38.get("error", "")
            err314 = r314.get("error", "")
            comparison.error = f"执行错误 (py38: {err38}, py314: {err314})"
            comparison.all_passed = False
            all_cases.append(comparison)
            continue

        metrics38 = r38["metrics"]
        metrics314 = r314["metrics"]

        print(f"\n📋 测试组: {case_name}")
        print(f"  {'指标':<20} {'Python 3.8':>14} {'Python 3.14':>14} {'绝对差异':>12} {'相对差异':>10} {'结果':>6}")
        print(f"  {'-'*20} {'-'*14} {'-'*14} {'-'*12} {'-'*10} {'-'*6}")

        for metric_name in COMPARE_METRICS:
            v38 = metrics38.get(metric_name, 0.0)
            v314 = metrics314.get(metric_name, 0.0)
            mc = compare_values(metric_name, v38, v314)
            comparison.metrics.append(mc)
            total_metrics += 1
            if mc.passed:
                passed_metrics += 1

            status = "✅ PASS" if mc.passed else "❌ FAIL"
            rel_str = f"{mc.rel_diff:.4%}" if mc.rel_diff != float("inf") else "  inf"
            print(f"  {metric_name:<20} {v38:>14.6f} {v314:>14.6f} {mc.abs_diff:>12.2e} {rel_str:>10} {status:>6}")

        if not all(m.passed for m in comparison.metrics):
            comparison.all_passed = False

        all_cases.append(comparison)

    # 汇总
    all_passed_cases = sum(1 for c in all_cases if c.all_passed)
    total_cases = len(all_cases)

    print("\n" + "=" * 80)
    print("汇总")
    print("=" * 80)
    print(f"测试组通过: {all_passed_cases}/{total_cases}")
    print(f"指标通过  : {passed_metrics}/{total_metrics} ({passed_metrics/total_metrics*100:.1f}%)")

    if all_passed_cases == total_cases:
        print("\n🎉 Phase 2 验收通过! 回测结果在两个环境中一致 (差异 < 1%).")
    else:
        print("\n⚠️  Phase 2 验收未通过! 存在差异超过 1% 的指标, 需排查原因.")
        # 列出失败的指标
        for case in all_cases:
            if not case.all_passed:
                if case.error:
                    print(f"  ❌ {case.name}: {case.error}")
                else:
                    failed = [m for m in case.metrics if not m.passed]
                    for m in failed:
                        print(f"  ❌ {case.name}/{m.name}: py38={m.py38_value:.6f} vs py314={m.py314_value:.6f} (rel_diff={m.rel_diff:.4%})")

    # 保存对比报告
    report_path = Path(py38_path).parent / "consistency_report.json"
    report = {
        "py38_version": data38["python_version"],
        "py314_version": data314["python_version"],
        "py38_numpy": data38["numpy_version"],
        "py314_numpy": data314["numpy_version"],
        "tolerance_rel": TOLERANCE_REL,
        "tolerance_abs": TOLERANCE_ABS,
        "total_cases": total_cases,
        "passed_cases": all_passed_cases,
        "total_metrics": total_metrics,
        "passed_metrics": passed_metrics,
        "all_passed": all_passed_cases == total_cases,
        "details": [
            {
                "name": c.name,
                "all_passed": c.all_passed,
                "error": c.error,
                "metrics": [
                    {
                        "name": m.name,
                        "py38": m.py38_value,
                        "py314": m.py314_value,
                        "abs_diff": m.abs_diff,
                        "rel_diff": m.rel_diff if m.rel_diff != float("inf") else None,
                        "passed": m.passed,
                    }
                    for m in c.metrics
                ],
            }
            for c in all_cases
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n对比报告已保存到: {report_path}")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2 双环境回测一致性验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # run 子命令
    run_parser = subparsers.add_parser("run", help="运行回测并保存结果")
    run_parser.add_argument(
        "--output", "-o",
        required=True,
        help="结果 JSON 输出路径",
    )

    # compare 子命令
    cmp_parser = subparsers.add_parser("compare", help="对比两个环境的结果")
    cmp_parser.add_argument(
        "--py38",
        required=True,
        help="Python 3.8 结果 JSON 路径",
    )
    cmp_parser.add_argument(
        "--py314",
        required=True,
        help="Python 3.14 结果 JSON 路径",
    )

    args = parser.parse_args()

    if args.command == "run":
        run_all_backtests(args.output)
        return 0
    elif args.command == "compare":
        compare_results(args.py38, args.py314)
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
