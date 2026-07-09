# -*- coding: utf-8 -*-
"""
因果验证引擎 + 回测稳健性检验 单元测试

测试内容：
1. CausalValidationEngine 三大方法测试
   - DID 双重差分
   - 安慰剂检验
   - RDD 断点回归
2. EnhancedBacktestEngine 稳健性检验测试
   - 子样本分析
   - 参数敏感性
   - 成本敏感性
   - Bootstrap 置信区间
   - 单标的剔除
"""

import sys
import os
import time
import numpy as np
import pandas as pd

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from utils.logger import get_logger
logger = get_logger('test_causal_robustness')


def test_causal_did():
    """测试 DID 双重差分"""
    from utils.causal_validation import CausalValidationEngine

    logger.info("测试 DID 双重差分...")
    engine = CausalValidationEngine(significance_level=0.05, random_seed=42)

    dates = pd.date_range('2025-01-01', periods=60, freq='B')
    np.random.seed(42)

    n_treat = 5
    n_ctrl = 5
    all_codes = [f'T{i:02d}' for i in range(n_treat)] + [f'C{i:02d}' for i in range(n_ctrl)]

    returns = {}
    for i in range(n_treat):
        ret = np.random.normal(0.0005, 0.015, len(dates))
        ret[30:] += 0.003
        returns[f'T{i:02d}'] = ret
    for i in range(n_ctrl):
        ret = np.random.normal(0.0005, 0.015, len(dates))
        returns[f'C{i:02d}'] = ret

    returns_df = pd.DataFrame(returns, index=dates)

    event_date = dates[30].strftime('%Y-%m-%d')
    treat_codes = [f'T{i:02d}' for i in range(n_treat)]
    ctrl_codes = [f'C{i:02d}' for i in range(n_ctrl)]

    result = engine.did_test(
        returns_df, event_date, treat_codes, ctrl_codes,
        pre_window=20, post_window=20
    )

    assert result is not None, "DID 结果不应为 None"
    assert hasattr(result, 'treatment_effect'), "缺少 treatment_effect 属性"
    assert hasattr(result, 'p_value'), "缺少 p_value 属性"
    assert hasattr(result, 'significant'), "缺少 significant 属性"
    assert hasattr(result, 'report'), "缺少 report 属性"
    assert len(result.report) > 0, "报告不应为空"
    assert result.n_treatment == n_treat, f"处理组数量错误: {result.n_treatment}"
    assert result.n_control == n_ctrl, f"控制组数量错误: {result.n_control}"

    logger.info(f"  DID 处理效应: {result.treatment_effect:.4%}, p值: {result.p_value:.4f}")
    logger.info(f"  DID 测试 ✓ 通过")
    return True


def test_causal_placebo():
    """测试安慰剂检验"""
    from utils.causal_validation import CausalValidationEngine

    logger.info("测试安慰剂检验...")
    engine = CausalValidationEngine(significance_level=0.05, random_seed=42)

    dates = pd.date_range('2025-01-01', periods=100, freq='B')
    np.random.seed(42)

    codes = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META']
    returns = {}
    for code in codes:
        returns[code] = np.random.normal(0.0008, 0.02, len(dates))

    returns_df = pd.DataFrame(returns, index=dates)

    def momentum_strategy(df):
        return df.mean(axis=1)

    result = engine.placebo_test(returns_df, momentum_strategy, n_iterations=50)

    assert result is not None, "安慰剂结果不应为 None"
    assert hasattr(result, 'real_effect'), "缺少 real_effect 属性"
    assert hasattr(result, 'placebo_effects'), "缺少 placebo_effects 属性"
    assert hasattr(result, 'p_value'), "缺少 p_value 属性"
    assert hasattr(result, 'significant'), "缺少 significant 属性"
    assert hasattr(result, 'report'), "缺少 report 属性"
    assert len(result.placebo_effects) > 0, "安慰剂效应列表不应为空"
    assert result.n_iterations > 0, "迭代次数应大于0"

    logger.info(f"  真实效应: {result.real_effect:.4%}, p值: {result.p_value:.4f}")
    logger.info(f"  安慰剂分布: 均值 {result.placebo_mean:.4%}, 标准差 {result.placebo_std:.4%}")
    logger.info(f"  安慰剂检验 ✓ 通过")
    return True


def test_causal_rdd():
    """测试 RDD 断点回归"""
    from utils.causal_validation import CausalValidationEngine

    logger.info("测试 RDD 断点回归...")
    engine = CausalValidationEngine(significance_level=0.05, random_seed=42)

    np.random.seed(42)
    n = 200
    signal = np.random.normal(0, 1, n)
    base_ret = np.random.normal(0, 0.01, n)
    jump_effect = np.where(signal > 0, 0.005, 0)
    returns = base_ret + jump_effect

    result = engine.rdd_test(signal, returns, cutoff=0.0, bandwidth_ratio=0.5)

    assert result is not None, "RDD 结果不应为 None"
    assert hasattr(result, 'effect_at_cutoff'), "缺少 effect_at_cutoff 属性"
    assert hasattr(result, 'jump_size'), "缺少 jump_size 属性"
    assert hasattr(result, 'p_value'), "缺少 p_value 属性"
    assert hasattr(result, 'significant'), "缺少 significant 属性"
    assert hasattr(result, 'report'), "缺少 report 属性"
    assert result.n_left > 0 and result.n_right > 0, "断点两侧应有样本"

    logger.info(f"  断点跳跃: {result.jump_size:.4%}, p值: {result.p_value:.4f}")
    logger.info(f"  左侧样本: {result.n_left}, 右侧样本: {result.n_right}")
    logger.info(f"  RDD 测试 ✓ 通过")
    return True


def test_backtest_robustness():
    """测试回测稳健性检验"""
    from utils.enhanced_backtest import EnhancedBacktestEngine

    logger.info("测试回测稳健性检验...")

    portfolio_config = {
        'assets': [
            {'code': 'AAPL', 'target_weight': 0.4},
            {'code': 'MSFT', 'target_weight': 0.3},
            {'code': 'GOOGL', 'target_weight': 0.3},
        ]
    }

    dates = pd.date_range('2025-01-01', periods=120, freq='B')
    np.random.seed(42)

    klines = {}
    for code in ['AAPL', 'MSFT', 'GOOGL']:
        prices = 100 * np.cumprod(1 + np.random.normal(0.0008, 0.02, len(dates)))
        klines[code] = pd.DataFrame({
            'open': prices * 0.995,
            'high': prices * 1.01,
            'low': prices * 0.99,
            'close': prices,
            'volume': np.random.randint(1000000, 5000000, len(dates)),
        }, index=dates)

    engine = EnhancedBacktestEngine(
        portfolio_config,
        initial_capital=1_000_000,
        rebalance_interval=10,
        rebalance_threshold=0.03,
        use_risk_parity=False,
        use_dynamic_weights=False
    )

    result = engine.robustness_check(klines, n_bootstrap=20, random_seed=42)

    assert 'error' not in result, f"稳健性检验出错: {result.get('error')}"
    assert 'baseline' in result, "缺少基准结果"
    assert 'subsample' in result, "缺少子样本分析"
    assert 'param_sensitivity' in result, "缺少参数敏感性"
    assert 'cost_sensitivity' in result, "缺少成本敏感性"
    assert 'bootstrap_ci' in result, "缺少 Bootstrap 置信区间"
    assert 'leave_one_out' in result, "缺少单标的剔除"
    assert 'robustness_score' in result, "缺少稳健性评分"
    assert 'report' in result, "缺少报告"

    score = result['robustness_score']
    assert 'overall' in score, "评分缺少 overall"
    assert 0 <= score['overall'] <= 100, f"评分应在 0-100 之间: {score['overall']}"
    assert 'grade' in score, "评分缺少等级"

    assert len(result['report']) > 500, "报告内容过短"

    logger.info(f"  基准收益: {result['baseline']['total_return']:.2%}")
    logger.info(f"  稳健性评分: {score['overall']}/100 ({score['grade']})")
    logger.info(f"  参数敏感性测试点: {len(result['param_sensitivity'])}")
    logger.info(f"  成本敏感性测试点: {len(result['cost_sensitivity'])}")
    logger.info(f"  单标的剔除测试: {len(result['leave_one_out'])}")
    logger.info(f"  回测稳健性检验 ✓ 通过")
    return True


def test_edge_cases():
    """测试边界情况"""
    from utils.causal_validation import CausalValidationEngine

    logger.info("测试边界情况...")
    engine = CausalValidationEngine(random_seed=42)

    # 空数据
    result = engine.did_test(pd.DataFrame(), '2025-01-01', ['A'], ['B'])
    assert not result.significant, "空数据应返回不显著"
    assert "失败" in result.report, "空数据应有失败提示"

    # RDD 样本不足
    result = engine.rdd_test(np.array([1.0]), np.array([0.01]))
    assert not result.significant, "样本不足应返回不显著"
    assert "失败" in result.report, "样本不足应有失败提示"

    logger.info(f"  边界情况 ✓ 通过")
    return True


def run_all_tests():
    """运行所有测试"""
    logger.info("=" * 60)
    logger.info("  因果验证引擎 + 回测稳健性检验 单元测试")
    logger.info("=" * 60)

    tests = [
        ("DID 双重差分", test_causal_did),
        ("安慰剂检验", test_causal_placebo),
        ("RDD 断点回归", test_causal_rdd),
        ("回测稳健性检验", test_backtest_robustness),
        ("边界情况", test_edge_cases),
    ]

    passed = 0
    failed = 0
    results = []

    for name, test_func in tests:
        start = time.time()
        try:
            test_func()
            elapsed = time.time() - start
            passed += 1
            results.append((name, True, "", elapsed))
        except Exception as e:
            elapsed = time.time() - start
            failed += 1
            import traceback
            error_msg = f"{e}\n{traceback.format_exc()}"
            results.append((name, False, error_msg, elapsed))
            logger.error(f"  ✗ {name} 失败: {e}")

    logger.info("=" * 60)
    logger.info(f"  测试结果: {passed} 通过, {failed} 失败")
    logger.info("=" * 60)

    for name, success, msg, elapsed in results:
        status = "✓" if success else "✗"
        logger.info(f"  {status} {name} ({elapsed:.3f}s)")
        if not success:
            logger.info(f"    错误: {msg[:200]}")

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
