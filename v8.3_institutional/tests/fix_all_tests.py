# -*- coding: utf-8 -*-
"""
v8.5 测试修复脚本 - 修复所有失败的测试类
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from unittest.mock import MagicMock


def test_risk_budgeter_initialization():
    """Test RiskBudgeter initialization with correct API"""
    from v75.risk_budgeter import RiskBudgeter
    
    budgeter = RiskBudgeter(total_capital=5_000_000)
    
    assert budgeter.C == 5_000_000
    assert budgeter.target == 0.08
    assert budgeter.max_dd == 0.08
    assert budgeter.mode == "NORMAL"
    assert budgeter.position_multiplier == 1.0
    print("✓ test_risk_budgeter_initialization passed")


def test_risk_budgeter_drawdown_modes():
    """Test drawdown mode transitions"""
    from v75.risk_budgeter import RiskBudgeter
    
    budgeter = RiskBudgeter(total_capital=5_000_000)
    
    # Test NORMAL mode (equity > 95% of HWM)
    mode = budgeter.update_drawdown(4_800_000)
    assert mode == "NORMAL", f"Expected NORMAL, got {mode}"
    
    # Test DEFENSE mode (drawdown >= 5%)
    mode = budgeter.update_drawdown(4_500_000)
    assert mode in ["DEFENSE_ACTIVATED", "DEFENSE_HOLD"], f"Expected DEFENSE, got {mode}"
    assert budgeter.position_multiplier == 0.5
    
    # Test CIRCUIT_BREAKER mode (drawdown >= 7%)
    mode = budgeter.update_drawdown(3_500_000)
    assert mode == "CIRCUIT_BREAKER", f"Expected CIRCUIT_BREAKER, got {mode}"
    assert budgeter.position_multiplier == 0.0
    print("✓ test_risk_budgeter_drawdown_modes passed")


def test_risk_manager_initialization():
    """Test RiskManager initialization with correct API"""
    from v75.risk_manager import RiskManager
    
    manager = RiskManager()
    
    assert manager.budgeter is not None
    assert manager.hedger is not None
    assert len(manager.event_log) == 0
    print("✓ test_risk_manager_initialization passed")


def test_risk_manager_run_cycle():
    """Test run_risk_cycle with correct parameters"""
    from v75.risk_manager import RiskManager
    import pandas as pd
    
    manager = RiskManager()
    
    # Create mock returns DataFrame
    returns = pd.DataFrame({
        'AAPL': [0.01, -0.02, 0.015],
        'GOOGL': [0.005, 0.01, -0.01]
    })
    
    decision = manager.run_risk_cycle(
        equity=5_000_000,
        portfolio_beta=1.2,
        vix_level=20.0,
        avg_correlation=0.4,
        positions={'AAPL': 100, 'GOOGL': 200},
        returns=returns
    )
    
    assert 'mode' in decision
    assert 'position_multiplier' in decision
    assert 'allow_new_positions' in decision
    assert 'hedge_actions' in decision
    print("✓ test_risk_manager_run_cycle passed")


def test_sor_initialization():
    """Test SmartOrderRouter initialization with correct API"""
    from v75.sor import SmartOrderRouter
    
    router = SmartOrderRouter(broker_api=None)
    
    assert router.broker_api is None
    assert router.queue_size == 0
    assert router.daily_order_count == 0
    print("✓ test_sor_initialization passed")


def test_sor_split_order():
    """Test order splitting logic"""
    from v75.sor import SmartOrderRouter
    
    router = SmartOrderRouter(broker_api=None)
    
    # Test TWAP split
    orders = router.split_order('AAPL', 10000, 'TWAP', num_slices=10)
    assert len(orders) == 10
    for order in orders:
        assert order['symbol'] == 'AAPL'
        assert order['quantity'] == 1000
    print("✓ test_sor_split_order passed")


def test_sor_submit_order():
    """Test order submission"""
    from v75.sor import SmartOrderRouter
    
    # Mock broker API
    mock_broker = MagicMock()
    mock_broker.submit_order = MagicMock(return_value={
        'order_id': 'TEST123',
        'status': 'filled',
        'fill_price': 150.0,
        'fill_quantity': 100
    })
    
    router = SmartOrderRouter(broker_api=mock_broker)
    
    result = router.submit_order('AAPL', 100, 'MARKET')
    
    assert result['order_id'] == 'TEST123'
    assert result['status'] == 'filled'
    assert router.daily_order_count == 1
    print("✓ test_sor_submit_order passed")


def test_v75_backtest_engine():
    """Test backtest engine with correct API"""
    from v75.backtest_engine import BacktestEngine
    import pandas as pd
    import numpy as np
    
    # Create sample data
    dates = pd.date_range('2024-01-01', periods=100, freq='D')
    data = pd.DataFrame({
        'date': dates,
        'close': np.random.randn(100) * 100 + 1000,
        'volume': np.random.randint(1000, 10000, 100)
    }).set_index('date')
    
    engine = BacktestEngine(
        initial_capital=1_000_000,
        commission_rate=0.001
    )
    
    results = engine.run_backtest(data)
    
    assert 'total_return' in results
    assert 'sharpe_ratio' in results
    assert 'max_drawdown' in results
    assert 'trade_count' in results
    print(f"✓ test_v75_backtest_engine passed (return: {results['total_return']:.2%})")


def test_v75_signal_generator():
    """Test signal generator"""
    from v75.signal_generator import SignalGenerator
    
    signals = SignalGenerator()
    
    # Test MA crossover signal
    signal = signals.generate_ma_signal(
        prices=[100, 101, 102, 103, 104],
        short_window=3,
        long_window=4
    )
    
    assert 'signal' in signal
    assert 'short_ma' in signal
    assert 'long_ma' in signal
    print(f"✓ test_v75_signal_generator passed (signal: {signal['signal']})")


def test_v75_position_manager():
    """Test position manager"""
    from v75.position_manager import PositionManager
    
    pm = PositionManager(total_capital=5_000_000)
    
    # Add position
    pm.add_position('AAPL', 100, 150.0)
    
    assert pm.total_positions == 1
    assert pm.total_exposure == 15000
    
    # Get position info
    pos_info = pm.get_position('AAPL')
    assert pos_info['symbol'] == 'AAPL'
    assert pos_info['quantity'] == 100
    
    # Remove position
    pm.remove_position('AAPL')
    assert pm.total_positions == 0
    print("✓ test_v75_position_manager passed")


if __name__ == '__main__':
    tests = [
        test_risk_budgeter_initialization,
        test_risk_budgeter_drawdown_modes,
        test_risk_manager_initialization,
        test_risk_manager_run_cycle,
        test_sor_initialization,
        test_sor_split_order,
        test_sor_submit_order,
        test_v75_backtest_engine,
        test_v75_signal_generator,
        test_v75_position_manager,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1
    
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")
