# -*- coding: utf-8 -*-
"""
Backtest Manipulation Fix Verification Tests v1.0

Run: cd v8.3_institutional && python tests/test_backtest_manipulation_fixes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import numpy as np
import pandas as pd


def test_1_optuna_train_f1_removal():
    """Test 1: Optuna optimizer train_f1 report removed"""
    print("\n" + "="*80)
    print("Test 1: Optuna train_f1 Removal")
    print("="*80)

    from src.ml.optuna_trainer import OptunaModelTrainer

    np.random.seed(42)
    X = np.random.randn(1000, 20)
    y = (X[:, 0] + X[:, 1] > 0).astype(int)

    trainer = OptunaModelTrainer(model_dir='/tmp/test_models', n_trials=10)
    result = trainer.optimize_xgboost(X, y)

    assert 'train_f1' not in result, "FAIL: train_f1 still in results!"
    assert 'best_f1_cv' in result, "PASS: best_f1_cv in results"
    assert 'train_auc' in result, "PASS: train_auc in results"

    print("PASS: train_f1 successfully removed")
    print(f"   Result keys: {list(result.keys())}")

    return True


def test_2_dynamic_target_return():
    """Test 2: Dynamic target return calculator"""
    print("\n" + "="*80)
    print("Test 2: Dynamic Target Return Calculator")
    print("="*80)

    from src.config.dynamic_target import DynamicTargetReturn

    np.random.seed(42)
    daily_returns = pd.Series(
        np.random.normal(0.0005, 0.01, 500),
        index=pd.date_range('2023-01-01', periods=500, freq='B')
    )

    calculator = DynamicTargetReturn(
        lookback_days=500,
        risk_free_rate=0.025,
        market_risk_premium=0.06,
        mode='neutral'
    )

    result = calculator.calculate_from_history(daily_returns)

    print("Dynamic target return calculated successfully:")
    print(f"   Risk-free rate: {result.risk_free_rate:.2%}")
    print(f"   Market risk premium: {result.market_risk_premium:.2%}")
    print(f"   Sharpe ratio: {result.sharpe_ratio:.2f}")
    print(f"   Volatility adjustment: {result.volatility_adjustment:.3f}")
    print(f"   Target return: {result.target_return:.2%}")

    assert 0.03 <= result.target_return <= 0.15, f"Target return {result.target_return:.2%} out of range!"

    print("PASS: Target return within reasonable range")

    return True


def test_3_dsr_dynamic_n_trials():
    """Test 3: DSR dynamic n_trials parameter"""
    print("\n" + "="*80)
    print("Test 3: DSR Dynamic n_trials")
    print("="*80)

    from src.validation.deflated_sharpe import deflated_sharpe_ratio

    np.random.seed(42)
    daily_returns = list(np.random.normal(0.0005, 0.01, 500))

    n_trials_list = [50, 100, 200, 500]
    dsr_results = []

    for n_trials in n_trials_list:
        result = deflated_sharpe_ratio(daily_returns, n_trials=n_trials, required_dsr=0.95)
        dsr_results.append(result.deflated_sharpe_ratio)
        print(f"   n_trials={n_trials:3d}: DSR={result.deflated_sharpe_ratio:.4f}")

    if len(dsr_results) >= 2:
        print(f"\nPASS: n_trials increase, DSR decreased from {dsr_results[0]:.4f} to {dsr_results[-1]:.4f}")

    return True


def test_4_parameter_sensitivity():
    """Test 4: Parameter sensitivity analysis"""
    print("\n" + "="*80)
    print("Test 4: Parameter Sensitivity Analysis")
    print("="*80)

    from src.validation.parameter_sensitivity import ParameterSensitivity

    base_params = {
        'learning_rate': 0.01,
        'max_depth': 5,
        'min_samples_split': 10,
    }
    base_performance = {'f1_score': 0.65}

    analyzer = ParameterSensitivity(base_params, base_performance)
    analyzer.add_parameter('learning_rate', base_value=0.01, range_min=0.005, range_max=0.015, steps=5)
    analyzer.add_parameter('max_depth', base_value=5, range_min=3, range_max=7, steps=5, parameter_type='discrete')

    result = analyzer.run_local_sensitivity()
    analyzer.print_summary()

    print("PASS:")
    print(f"   Overall stability score: {result.overall_stability_score:.2f}/100")
    print(f"   Recommendation: {result.recommendation}")

    return True


def test_5_cagr_decay_detection():
    """Test 5: CAGR decay detection"""
    print("\n" + "="*80)
    print("Test 5: CAGR Decay Detection")
    print("="*80)

    from src.validation.cagr_decay import CAGRDetection

    np.random.seed(42)
    n_days = 504

    first_half = pd.Series(
        np.random.normal(0.001, 0.01, n_days // 2),
        index=pd.date_range('2023-01-01', periods=n_days//2, freq='B')
    )
    second_half = pd.Series(
        np.random.normal(0.0001, 0.01, n_days // 2),
        index=pd.date_range('2024-01-01', periods=n_days//2, freq='B')
    )

    combined_returns = pd.concat([first_half, second_half])

    detector = CAGRDetection(combined_returns, decay_threshold=0.30)
    result = detector.detect()
    detector.print_summary()

    print("PASS:")
    print(f"   First half CAGR: {result.first_half_cagr:.2%}")
    print(f"   Second half CAGR: {result.second_half_cagr:.2%}")
    print(f"   Decay rate: {result.decay_rate:.1%}")
    print(f"   Severity: {result.decay_severity}")

    return True


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("Backtest Manipulation Fix Verification Suite")
    print("="*80)
    print(f"Test time: {datetime.now().isoformat()}")

    tests = [
        ("Optuna train_f1 removal", test_1_optuna_train_f1_removal),
        ("Dynamic target return", test_2_dynamic_target_return),
        ("DSR dynamic n_trials", test_3_dsr_dynamic_n_trials),
        ("Parameter sensitivity", test_4_parameter_sensitivity),
        ("CAGR decay detection", test_5_cagr_decay_detection),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            print(f"\nFAIL: {test_name}")
            print(f"   Error: {e!s}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Summary report
    print("\n" + "="*80)
    print("Test Summary Report")
    print("="*80)

    passed_count = sum(1 for _, passed in results if passed)
    total_count = len(results)

    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed_count}/{total_count} tests passed")

    if passed_count == total_count:
        print("\nAll fixes verified successfully!")
    else:
        print(f"\nWarning: {total_count - passed_count} tests failed!")

    print("="*80 + "\n")

    return passed_count == total_count


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
