"""验证除零风险修复效果"""
import sys
import os
import pytest

ROOT = r"e:\各种PY程序\28-终极量化交易系统8.4"
sys.path.insert(0, ROOT)


def test_market_impact_zero_n_steps_raises():
    """market_impact_model.optimal_trajectory 应拒绝 n_steps=0"""
    from utils.market_impact_model import MarketImpactModel, ImpactParams
    model = MarketImpactModel(ImpactParams())
    with pytest.raises(ValueError, match="n_steps"):
        model.optimal_trajectory(total_shares=10000, n_steps=0)


def test_market_impact_zero_horizon_raises():
    """market_impact_model.optimal_trajectory 应拒绝 time_horizon=0"""
    from utils.market_impact_model import MarketImpactModel, ImpactParams
    model = MarketImpactModel(ImpactParams())
    with pytest.raises(ValueError, match="time_horizon"):
        model.optimal_trajectory(total_shares=10000, time_horizon=0)


def test_market_impact_invalid_alpha_raises():
    """market_impact_model.optimal_trajectory 应拒绝 alpha=-1"""
    from utils.market_impact_model import MarketImpactModel, ImpactParams
    model = MarketImpactModel(ImpactParams(alpha=-1.0))
    with pytest.raises(ValueError, match="alpha"):
        model.optimal_trajectory(total_shares=10000)


def test_market_impact_normal_call():
    """正常调用应该工作"""
    from utils.market_impact_model import MarketImpactModel, ImpactParams
    model = MarketImpactModel(ImpactParams())
    traj = model.optimal_trajectory(total_shares=10000, n_steps=10, time_horizon=1.0)
    assert traj is not None
    assert len(traj.times) == 11


def test_strategy_evaluator_zero_required_dsr():
    """strategy_evaluator 在 required_dsr=0 时不触发除零"""
    from utils.alpha.strategy_evaluator import StrategyEvaluator
    evaluator = StrategyEvaluator(required_dsr=0.0)
    # 应返回 0.0, 不抛出 ZeroDivisionError
    score = evaluator._score_anti_cheat_dsr({"is_pass": False, "deflated_sharpe_ratio": 0.5})
    assert score == 0.0


def test_kalman_beta_zero_Q_R():
    """kalman_beta 在 Q=0, R=0 时不触发除零"""
    from utils.fineng.kalman_beta import fit_kalman_beta
    # 构造极端数据: Q=0, R=0, 历史数据
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    y = [2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
    # 不抛异常即通过
    try:
        result = fit_kalman_beta(x, y, Q=0.0, R=0.0)
        assert result is not None
    except ZeroDivisionError:
        pytest.fail("kalman_beta 在 Q=0, R=0 时触发了 ZeroDivisionError")


if __name__ == "__main__":
    # 直接运行 (无 pytest)
    state = {"run": 0, "passed": 0, "failed": []}

    def run_test(name, fn):
        state["run"] += 1
        try:
            fn()
            state["passed"] += 1
            print(f"  PASS: {name}")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            state["failed"].append((name, str(e)))
            print(f"  FAIL: {name} — {type(e).__name__}: {e}")

    print("=" * 60)
    print("除零风险修复验证")
    print("=" * 60)

    run_test("market_impact_zero_n_steps_raises", test_market_impact_zero_n_steps_raises)
    run_test("market_impact_zero_horizon_raises", test_market_impact_zero_horizon_raises)
    run_test("market_impact_invalid_alpha_raises", test_market_impact_invalid_alpha_raises)
    run_test("market_impact_normal_call", test_market_impact_normal_call)
    run_test("strategy_evaluator_zero_required_dsr", test_strategy_evaluator_zero_required_dsr)
    run_test("kalman_beta_zero_Q_R", test_kalman_beta_zero_Q_R)

    print(f"\n总计: {state['passed']}/{state['run']} 通过")
    if state["failed"]:
        print(f"失败: {len(state['failed'])} 个")
        sys.exit(1)
    else:
        print("全部通过!")
        sys.exit(0)
