#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略多维评分器 N2 验证脚本 — Day 2.

测试目标:
  - StrategyEvaluator 类可导入
  - Feature Flag 启用后能返回有效评分
  - 降级报告 (Flag 关闭) 正确返回
  - 评分结果在合理范围内 [0,1]
"""
import os
import sys

BASE = r"e:\各种PY程序\28-终极量化交易系统8.4"
sys.path.insert(0, BASE)


def test_n2_basic_import():
    """N2.1: 基础导入测试."""
    from strategy_evaluator import StrategyEvaluator
    assert StrategyEvaluator is not None
    print("✓ N2.1 StrategyEvaluator 导入通过")


def test_n2_feature_flag_disabled():
    """N2.2: Feature Flag 关闭时返回降级报告."""
    # 默认不设置 env var, Flag 应禁用
    from strategy_evaluator import EVOLUTION_CONFIG, StrategyEvaluator
    flag_name = EVOLUTION_CONFIG.get("feature_flag_name", "USE_STRATEGY_EVALUATOR")
    # 确保 env var 未设置为 True
    if flag_name in os.environ:
        del os.environ[flag_name]

    evaluator = StrategyEvaluator()
    assert not evaluator._enabled, "Feature Flag 应默认为 False"

    report = evaluator.evaluate()
    assert report.is_degraded, "应返回降级报告"
    assert "feature_flag_disabled" in report.reason.lower(), "降级原因应包含 Flag 禁用"
    print(f"✓ N2.2 Flag 禁用降级报告: {report.reason[:50]}...")


def test_n2_feature_flag_enabled():
    """N2.3: Feature Flag 启用后计算有效评分."""
    os.environ["USE_STRATEGY_EVALUATOR"] = "True"
    # 重新导入以确保捕获新的 env var
    from strategy_evaluator import StrategyEvaluator as SE_new

    # 实例化 (注意 Feature Flag 检查是在 __init__ 时读取 env var 的)
    evaluator = SE_new(feature_flag_name="USE_STRATEGY_EVALUATOR")
    assert evaluator._enabled, "Feature Flag 启用后 enabled 应为 True"

    # 调用 evaluate (会尝试读取 positions.json 和 daily_returns.jsonl)
    report = evaluator.evaluate()

    # 评分应在 [0,1] 范围内
    assert 0.0 <= report.public_score <= 1.0, f"public_score [{report.public_score}] 超出范围"
    assert 0.0 <= report.private_score <= 1.0, f"private_score [{report.private_score}] 超出范围"
    assert 0.0 <= report.overall_score <= 1.0, f"overall_score [{report.overall_score}] 超出范围"

    # 检查回报指标有基本数据
    assert isinstance(report.return_metrics.sample_count, int)
    assert report.return_metrics.sample_count >= 0

    print(f"✓ N2.3 Flag 启用评分: public={report.public_score:.4f}, private={report.private_score:.4f}, overall={report.overall_score:.4f}, rec={report.recommendation}")


def test_n2_simple_evaluator_function():
    """N2.4: 便捷函数 evaluate_strategy_simple 可调用."""
    from strategy_evaluator import evaluate_strategy_simple

    result = evaluate_strategy_simple()
    assert isinstance(result, dict) or hasattr(result, 'to_dict')
    print(f"✓ N2.4 便捷函数评价通过: recommendation={result.recommendation if hasattr(result, 'recommendation') else 'unknown'}")


if __name__ == "__main__":
    print("=" * 60)
    print("  N2 策略评分器验证")
    print("=" * 60)

    test_n2_basic_import()
    test_n2_feature_flag_disabled()
    test_n2_feature_flag_enabled()
    test_n2_simple_evaluator_function()

    print("=" * 60)
    print("  ✅ N2 所有测试通过!")
    print("=" * 60)
