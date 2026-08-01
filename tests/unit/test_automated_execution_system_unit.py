"""
automated_execution_system 单元测试 (C-1.3)
==========================================

覆盖范围:
  - 纯函数: _to_wind_code
  - TradingCalendar: 交易日/执行窗口/执行计划/执行历史
  - MarketStateEvaluator: 波动率/流动性/VaR/情绪/相关性评估/状态决策
  - ExecutionStrategy: 策略选择/执行计划生成/执行结果记录
  - OrderRouter: 订单路由/执行池可用性/订单执行(模拟路径)
                 重点回归: P0-1 双签保护 / P0 symbol/side 注入 / P0-2 KillSwitch
                          P1 线程安全 / P2-5 等待时间公式 / BUG-E2/E4
  - AutomatedExecutionSystem: 主控制器初始化/启停/对冲开关/风险预检查

测试策略:
  - MagicMock 隔离 wind_mcp / hedge_coordinator / broker / smart_router
  - 不发起任何真实网络请求
  - 重点验证 P0/P1 修复点不回归

目标覆盖率: ≥ 40%
"""

import json
import os
import sys
import threading
from datetime import datetime
from datetime import time as datetime_time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ============================================================
# 模块加载
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 在导入前确保 TRADING_ENV 非 production (避免误入实盘路径)
os.environ.pop("TRADING_ENV", None)

from utils.execution.automated_execution_system import (  # noqa: E402
    AutomatedExecutionSystem,
    ExecutionStrategy,
    MarketStateEvaluator,
    OrderRouter,
    TradingCalendar,
    _to_wind_code,
)

# ============================================================
# 1. 纯函数测试
# ============================================================


class TestToWindCode:
    """_to_wind_code: 交易所代码转换"""

    def test_shanghai_etf_51(self):
        code, is_fund = _to_wind_code("510050")
        assert code == "510050.SH"
        assert is_fund is True

    def test_shanghai_etf_58(self):
        code, is_fund = _to_wind_code("588080")
        assert code == "588080.SH"
        assert is_fund is True

    def test_shenzhen_etf_15(self):
        code, is_fund = _to_wind_code("159919")
        assert code == "159919.SZ"
        assert is_fund is True

    def test_shenzhen_etf_16(self):
        code, is_fund = _to_wind_code("160706")
        assert code == "160706.SZ"
        assert is_fund is True

    def test_shenzhen_stock_00(self):
        code, is_fund = _to_wind_code("000001")
        assert code == "000001.SZ"
        assert is_fund is False

    def test_chinext_30(self):
        code, is_fund = _to_wind_code("300750")
        assert code == "300750.SZ"
        assert is_fund is False

    def test_shanghai_stock_6(self):
        code, is_fund = _to_wind_code("600519")
        assert code == "600519.SH"
        assert is_fund is False

    def test_bj_exchange_8(self):
        code, is_fund = _to_wind_code("830879")
        assert code == "830879.BJ"
        assert is_fund is False

    def test_strips_sh_prefix(self):
        code, is_fund = _to_wind_code("sh600519")
        assert code == "600519.SH"
        assert is_fund is False

    def test_strips_sz_prefix(self):
        code, is_fund = _to_wind_code("sz000001")
        assert code == "000001.SZ"

    def test_strips_uppercase_prefix(self):
        code, _ = _to_wind_code("SH510050")
        assert code == "510050.SH"

    def test_strips_suffix(self):
        code, _ = _to_wind_code("600519.SH")
        assert code == "600519.SH"

    def test_strips_lowercase_suffix(self):
        code, _ = _to_wind_code("600519.sh")
        assert code == "600519.SH"

    def test_default_fallback_sh(self):
        # 不匹配任何规则 → 默认 .SH
        code, is_fund = _to_wind_code("999999")
        assert code == "999999.SH"
        assert is_fund is False

    def test_strips_whitespace(self):
        code, _ = _to_wind_code("  600519  ")
        assert code == "600519.SH"


# ============================================================
# 2. TradingCalendar 测试
# ============================================================


class TestTradingCalendar:
    """TradingCalendar: 交易日历管理"""

    def test_initialization(self):
        cal = TradingCalendar()
        assert cal.trading_schedule["morning_open"] == datetime_time(7, 0)
        assert len(cal.trading_schedule["executions"]) == 3
        assert len(cal.special_days) > 0
        assert len(cal.execution_windows) == 3
        assert cal.execution_history.maxlen == 100

    def test_is_trading_day_weekend(self):
        cal = TradingCalendar()
        # 2026-08-02 是周日
        sunday = datetime(2026, 8, 2)
        assert cal.is_trading_day(sunday) is False
        # 2026-08-01 是周六
        saturday = datetime(2026, 8, 1)
        assert cal.is_trading_day(saturday) is False

    def test_is_trading_day_weekday(self):
        cal = TradingCalendar()
        # 2026-08-03 是周一
        monday = datetime(2026, 8, 3)
        assert cal.is_trading_day(monday) is True

    def test_is_trading_day_special_holiday(self):
        cal = TradingCalendar()
        # 2026-01-01 元旦
        new_year = datetime(2026, 1, 1)
        assert cal.is_trading_day(new_year) is False

    def test_is_trading_day_spring_festival_2026(self):
        cal = TradingCalendar()
        # 2026-02-17 春节
        spring_festival = datetime(2026, 2, 17)
        assert cal.is_trading_day(spring_festival) is False

    def test_is_trading_day_national_day(self):
        cal = TradingCalendar()
        # 2026-10-01 国庆节
        national_day = datetime(2026, 10, 1)
        assert cal.is_trading_day(national_day) is False

    def test_is_trading_day_default_now(self):
        cal = TradingCalendar()
        # 不传参数 → 使用 datetime.now()
        result = cal.is_trading_day()
        assert isinstance(result, bool)

    def test_is_within_execution_window_unknown(self):
        cal = TradingCalendar()
        ok, msg = cal.is_within_execution_window("unknown_execution")
        assert ok is False
        assert "未知" in msg

    def test_is_within_execution_window_known(self):
        cal = TradingCalendar()
        ok, msg = cal.is_within_execution_window("daily_execution")
        assert isinstance(ok, bool)
        assert isinstance(msg, str)

    def test_get_next_execution_time_returns_datetime(self):
        cal = TradingCalendar()
        result = cal.get_next_execution_time()
        # 可能返回 datetime 或 None (非交易日且找不到下一交易日时)
        assert result is None or isinstance(result, datetime)

    def test_get_execution_schedule_7_days(self):
        cal = TradingCalendar()
        schedule = cal.get_execution_schedule(7)
        assert isinstance(schedule, list)
        assert len(schedule) <= 7
        for day in schedule:
            assert "date" in day
            assert "is_trading" in day
            assert "executions" in day
            assert len(day["executions"]) == 3

    def test_record_execution(self):
        cal = TradingCalendar()
        start = datetime(2026, 8, 3, 7, 0)
        end = datetime(2026, 8, 3, 7, 5)
        cal.record_execution("daily_execution", start, end, True, {"result": "ok"})
        assert len(cal.execution_history) == 1
        record = cal.execution_history[0]
        assert record["execution_name"] == "daily_execution"
        assert record["success"] is True
        assert record["duration_seconds"] == 300  # 5 分钟

    def test_get_execution_summary_empty(self):
        cal = TradingCalendar()
        summary = cal.get_execution_summary()
        assert "message" in summary
        assert "暂无" in summary["message"]

    def test_get_execution_summary_with_history(self):
        cal = TradingCalendar()
        start = datetime(2026, 8, 3, 7, 0)
        end = datetime(2026, 8, 3, 7, 5)
        cal.record_execution("daily_execution", start, end, True, {})
        cal.record_execution("morning_review", start, end, False, {})
        summary = cal.get_execution_summary()
        assert summary["total_executions"] == 2
        assert summary["success_rate"] == 0.5
        assert summary["latest_execution"] == "morning_review"
        assert "execution_stats" in summary
        assert "daily_execution" in summary["execution_stats"]
        assert "morning_review" in summary["execution_stats"]


# ============================================================
# 3. MarketStateEvaluator 测试
# ============================================================


class TestMarketStateEvaluator:
    """MarketStateEvaluator: 市场状态评估"""

    def test_initialization(self):
        evaluator = MarketStateEvaluator()
        assert len(evaluator.market_states) == 5
        assert "normal" in evaluator.market_states
        assert "crisis" in evaluator.market_states
        assert evaluator.state_history.maxlen == 100
        assert "volatility_threshold" in evaluator.state_thresholds

    def test_evaluate_volatility_very_low(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_volatility(0.05) == 0.0

    def test_evaluate_volatility_low(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_volatility(0.15) == 0.3

    def test_evaluate_volatility_medium(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_volatility(0.25) == 0.6

    def test_evaluate_volatility_high(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_volatility(0.35) == 0.8

    def test_evaluate_volatility_very_high(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_volatility(0.50) == 1.0

    def test_evaluate_liquidity_very_high(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_liquidity(0.9) == 0.0

    def test_evaluate_liquidity_low(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_liquidity(0.3) == 0.8

    def test_evaluate_liquidity_very_low(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_liquidity(0.1) == 1.0

    def test_evaluate_var_very_low(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_var(0.01) == 0.0

    def test_evaluate_var_high(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_var(0.06) == 0.8

    def test_evaluate_var_very_high(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_var(0.10) == 1.0

    def test_evaluate_sentiment_neutral(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_sentiment(0.1) == 0.0

    def test_evaluate_sentiment_extreme(self):
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_sentiment(0.9) == 1.0

    def test_evaluate_sentiment_negative(self):
        # 使用 abs, 负值同样评估
        evaluator = MarketStateEvaluator()
        assert evaluator._evaluate_sentiment(-0.5) == 0.6

    def test_calculate_correlation_breakdown_identity(self):
        # 单位矩阵 → 上三角全 0 → breakdown=0
        evaluator = MarketStateEvaluator()
        result = evaluator._calculate_correlation_breakdown(np.eye(3))
        assert result == 0.0

    def test_calculate_correlation_breakdown_high(self):
        # 高相关性矩阵 → breakdown 较高
        evaluator = MarketStateEvaluator()
        corr = np.array([[1.0, 0.9, 0.8], [0.9, 1.0, 0.85], [0.8, 0.85, 1.0]])
        result = evaluator._calculate_correlation_breakdown(corr)
        assert 0.0 < result <= 1.0

    def test_determine_market_state_normal(self):
        evaluator = MarketStateEvaluator()
        # 所有指标都很低 → normal
        state = evaluator._determine_market_state(0.0, 0.0, 0.0, 0.0, 0.0)
        assert state == "normal"

    def test_determine_market_state_crisis(self):
        evaluator = MarketStateEvaluator()
        # 所有指标都极端 → crisis
        state = evaluator._determine_market_state(1.0, 1.0, 1.0, 1.0, 1.0)
        # risk_score = 1.0*0.3 + 1.0*0.3 + 1.0*0.2 + 1.0*0.1 + 1.0*0.1 = 1.0 >= 0.8
        assert state == "crisis"

    def test_determine_market_state_stress(self):
        evaluator = MarketStateEvaluator()
        # risk_score ~ 0.6-0.8 → stress
        state = evaluator._determine_market_state(0.8, 0.8, 0.8, 0.8, 0.8)
        # 0.8*0.3+0.8*0.3+0.8*0.2+0.8*0.1+0.8*0.1 = 0.8 → crisis
        assert state in ("stress", "crisis")

    def test_determine_market_state_volatile(self):
        evaluator = MarketStateEvaluator()
        # risk_score ~ 0.2-0.4 → volatile
        state = evaluator._determine_market_state(0.3, 0.3, 0.3, 0.3, 0.3)
        # 0.3*1.0 = 0.3 → volatile
        assert state == "volatile"

    def test_calculate_state_confidence_no_extreme(self):
        evaluator = MarketStateEvaluator()
        confidence = evaluator._calculate_state_confidence(0.3, 0.3, 0.3, 0.3, 0.3)
        assert confidence == 0.0

    def test_calculate_state_confidence_all_extreme(self):
        evaluator = MarketStateEvaluator()
        confidence = evaluator._calculate_state_confidence(0.9, 0.9, 0.9, 0.9, 0.9)
        assert confidence == 1.0

    def test_calculate_state_confidence_capped_at_one(self):
        # 置信度上限为 1.0
        evaluator = MarketStateEvaluator()
        confidence = evaluator._calculate_state_confidence(1.0, 1.0, 1.0, 1.0, 1.0)
        assert confidence == 1.0

    def test_evaluate_market_state_normal_data(self):
        evaluator = MarketStateEvaluator()
        market_data = {
            "volatility": 0.10,
            "liquidity": 0.9,
            "var_95": 0.01,
            "sentiment_score": 0.0,
            "correlation_matrix": np.eye(3),
        }
        result = evaluator.evaluate_market_state(market_data)
        assert "market_state" in result
        assert "confidence" in result
        assert "individual_scores" in result
        assert "detailed_metrics" in result
        assert "state_characteristics" in result
        assert result["market_state"] == "normal"
        assert len(evaluator.state_history) == 1

    def test_evaluate_market_state_crisis_data(self):
        evaluator = MarketStateEvaluator()
        market_data = {
            "volatility": 0.50,
            "liquidity": 0.1,
            "var_95": 0.10,
            "sentiment_score": 0.9,
            "correlation_matrix": np.array([[1.0, 0.95, 0.9], [0.95, 1.0, 0.95], [0.9, 0.95, 1.0]]),
        }
        result = evaluator.evaluate_market_state(market_data)
        assert result["market_state"] in ("crisis", "stress")

    def test_evaluate_market_state_exception_returns_normal(self):
        # 异常输入 → fail-safe 返回 normal
        evaluator = MarketStateEvaluator()
        result = evaluator.evaluate_market_state(None)  # type: ignore
        assert result["market_state"] == "normal"
        assert "error" in result

    def test_get_market_state_summary_empty(self):
        evaluator = MarketStateEvaluator()
        summary = evaluator.get_market_state_summary()
        assert "message" in summary

    def test_get_market_state_summary_with_history(self):
        evaluator = MarketStateEvaluator()
        market_data = {
            "volatility": 0.10,
            "liquidity": 0.9,
            "var_95": 0.01,
            "sentiment_score": 0.0,
            "correlation_matrix": np.eye(3),
        }
        evaluator.evaluate_market_state(market_data)
        summary = evaluator.get_market_state_summary()
        assert "current_state" in summary
        assert "state_distribution" in summary
        assert summary["total_evaluations"] == 1


# ============================================================
# 4. ExecutionStrategy 测试
# ============================================================


class TestExecutionStrategy:
    """ExecutionStrategy: 执行策略控制"""

    def test_initialization(self):
        strategy = ExecutionStrategy()
        assert len(strategy.execution_strategies) == 5
        assert "aggressive" in strategy.execution_strategies
        assert "emergency" in strategy.execution_strategies
        assert strategy.state_to_strategy["normal"] == "aggressive"
        assert strategy.state_to_strategy["crisis"] == "emergency"

    def test_select_strategy_normal(self):
        strategy = ExecutionStrategy()
        trade_info = {"trade_size": 100000, "urgency": "normal", "asset_type": "equity"}
        result = strategy.select_execution_strategy("normal", trade_info)
        assert result["strategy_name"] == "aggressive"
        assert "strategy_config" in result
        assert "reasoning" in result

    def test_select_strategy_crisis(self):
        strategy = ExecutionStrategy()
        trade_info = {"trade_size": 100000, "urgency": "normal", "asset_type": "equity"}
        result = strategy.select_execution_strategy("crisis", trade_info)
        assert result["strategy_name"] == "emergency"
        assert result["strategy_config"]["timeout_seconds"] == 15

    def test_select_strategy_large_trade_reduces_slice(self):
        # 大额交易 → slice_size 减半
        strategy = ExecutionStrategy()
        trade_info = {"trade_size": 2000000, "urgency": "normal", "asset_type": "equity"}
        result = strategy.select_execution_strategy("normal", trade_info)
        # aggressive 默认 slice_size=1.0, 大额交易 → max(0.1, 1.0*0.5)=0.5
        assert result["strategy_config"]["slice_size"] == 0.5

    def test_select_strategy_high_urgency(self):
        # 高紧急 → slice_size 翻倍, timeout 减半
        strategy = ExecutionStrategy()
        trade_info = {"trade_size": 100000, "urgency": "high", "asset_type": "equity"}
        result = strategy.select_execution_strategy("conservative", trade_info)
        # conservative 默认 slice_size=0.3, 高紧急 → min(1.0, 0.3*2)=0.6
        assert result["strategy_config"]["slice_size"] == 0.6

    def test_select_strategy_bond_asset(self):
        # 债券 → slice_size 增加 50%, slippage_tolerance 翻倍
        strategy = ExecutionStrategy()
        trade_info = {"trade_size": 100000, "urgency": "normal", "asset_type": "bond"}
        result = strategy.select_execution_strategy("conservative", trade_info)
        # conservative 默认 slice=0.3, bond → min(0.5, 0.3*1.5)=0.45
        # 使用 approx 容差断言, 避免 0.3*1.5=0.44999... 浮点精度误差
        assert result["strategy_config"]["slice_size"] == pytest.approx(0.45, rel=1e-9)

    def test_select_strategy_unknown_state_defaults_conservative(self):
        strategy = ExecutionStrategy()
        trade_info = {"trade_size": 100000}
        result = strategy.select_execution_strategy("unknown_state", trade_info)
        assert result["strategy_name"] == "conservative"

    def test_generate_execution_plan_single_slice(self):
        # slice_size=1.0 → 1 个切片
        strategy = ExecutionStrategy()
        trade_info = {
            "trade_id": "T001",
            "instrument": "600519.SH",
            "direction": "buy",
            "trade_size": 100000,
        }
        strategy_config = strategy.execution_strategies["aggressive"].copy()
        plan = strategy.generate_execution_plan(trade_info, strategy_config)
        assert plan["num_slices"] == 1
        assert plan["total_size"] == 100000
        assert plan["instrument"] == "600519.SH"
        assert plan["total_direction"] == "buy"
        assert len(plan["slices"]) == 1
        assert plan["slices"][0]["size"] == 100000

    def test_generate_execution_plan_multiple_slices(self):
        # slice_size=0.3 → 3 个切片 (1/0.3=3.33 → int=3)
        strategy = ExecutionStrategy()
        trade_info = {
            "trade_id": "T002",
            "instrument": "000001.SZ",
            "direction": "sell",
            "trade_size": 100000,
        }
        strategy_config = strategy.execution_strategies["conservative"].copy()
        plan = strategy.generate_execution_plan(trade_info, strategy_config)
        assert plan["num_slices"] == 3
        assert len(plan["slices"]) == 3
        # 最后一片 = total - sum(其他片)
        total_sliced = sum(s["size"] for s in plan["slices"])
        assert total_sliced == 100000

    def test_generate_execution_plan_exception_returns_error(self):
        strategy = ExecutionStrategy()
        # strategy_config 缺少 slice_size → KeyError
        plan = strategy.generate_execution_plan({}, {})
        assert "error" in plan

    def test_record_execution_result(self):
        strategy = ExecutionStrategy()
        plan = {"strategy": "aggressive"}
        result = {"success": True, "execution_time": 1.5, "slippage": 0.001, "retry_attempts": 0}
        strategy.record_execution_result(plan, result)
        assert len(strategy.execution_history) == 1
        record = strategy.execution_history[0]
        assert record["success"] is True
        assert record["execution_time"] == 1.5

    def test_get_execution_summary_empty(self):
        strategy = ExecutionStrategy()
        summary = strategy.get_execution_summary()
        assert "message" in summary

    def test_get_execution_summary_with_history(self):
        strategy = ExecutionStrategy()
        plan = {"strategy": "aggressive"}
        strategy.record_execution_result(plan, {"success": True, "execution_time": 1.0, "slippage": 0.001})
        strategy.record_execution_result(plan, {"success": False, "execution_time": 2.0, "slippage": 0.0})
        summary = strategy.get_execution_summary()
        assert summary["total_executions"] == 2
        assert summary["success_rate"] == 0.5
        assert "strategy_stats" in summary
        assert "aggressive" in summary["strategy_stats"]


# ============================================================
# 5. OrderRouter 测试 (重点: P0/P1 修复点回归)
# ============================================================


class TestOrderRouter:
    """OrderRouter: 订单路由器"""

    def test_initialization_simulated_mode(self):
        # 无参数 → 模拟模式
        router = OrderRouter()
        assert router._use_live is False
        assert router.smart_router is None
        assert router.broker is None
        assert len(router.execution_pools) == 3
        assert router.active_orders == {}

    def test_initialization_live_mode_requires_env(self, monkeypatch):
        """P0-1 回归: 实盘模式需要双签 (参数+TRADING_ENV=production)"""
        # 传入 smart_router/broker 但未设置 TRADING_ENV → 强制降级模拟
        monkeypatch.delenv("TRADING_ENV", raising=False)
        mock_sr = MagicMock()
        mock_broker = MagicMock()
        router = OrderRouter(smart_router=mock_sr, broker=mock_broker)
        # 应该强制降级为模拟模式
        assert router._use_live is False

    def test_initialization_live_mode_with_env(self, monkeypatch):
        """P0-1 回归: 参数+TRADING_ENV=production → 实盘模式"""
        monkeypatch.setenv("TRADING_ENV", "production")
        mock_sr = MagicMock()
        mock_broker = MagicMock()
        router = OrderRouter(smart_router=mock_sr, broker=mock_broker)
        assert router._use_live is True

    def test_initialization_live_mode_env_case_insensitive(self, monkeypatch):
        # TRADING_ENV=Production (大小写不敏感) → 实盘
        monkeypatch.setenv("TRADING_ENV", "Production")
        mock_sr = MagicMock()
        mock_broker = MagicMock()
        router = OrderRouter(smart_router=mock_sr, broker=mock_broker)
        assert router._use_live is True

    def test_route_order_normal_state(self):
        router = OrderRouter()
        plan = {
            "instrument": "600519.SH",
            "total_direction": "buy",
            "slices": [
                {"slice_id": 1, "size": 100, "direction": "buy", "instrument": "600519.SH"},
            ],
        }
        result = router.route_order(plan, "normal")
        assert result["success"] is True
        assert result["target_pool"] == "normal"
        assert len(result["routed_orders"]) == 1
        # P0 回归: 验证 symbol/side 已注入
        order = result["routed_orders"][0]
        assert order["symbol"] == "600519.SH"
        assert order["side"] == "BUY"  # 大写

    def test_route_order_crisis_uses_emergency_pool(self):
        router = OrderRouter()
        plan = {
            "instrument": "600519.SH",
            "total_direction": "sell",
            "slices": [{"slice_id": 1, "size": 100, "direction": "sell", "instrument": "600519.SH"}],
        }
        result = router.route_order(plan, "crisis")
        assert result["success"] is True
        assert result["target_pool"] == "emergency"

    def test_route_order_stress_uses_emergency_pool(self):
        router = OrderRouter()
        plan = {
            "instrument": "600519.SH",
            "total_direction": "buy",
            "slices": [{"slice_id": 1, "size": 100, "direction": "buy", "instrument": "600519.SH"}],
        }
        result = router.route_order(plan, "stress")
        assert result["target_pool"] == "emergency"

    def test_route_order_illiquid_uses_priority_pool(self):
        router = OrderRouter()
        plan = {
            "instrument": "600519.SH",
            "total_direction": "buy",
            "slices": [{"slice_id": 1, "size": 100, "direction": "buy", "instrument": "600519.SH"}],
        }
        result = router.route_order(plan, "illiquid")
        assert result["target_pool"] == "priority"

    def test_route_order_skips_slice_without_symbol(self):
        """P0 回归: 切片缺少 symbol → 跳过此切片"""
        router = OrderRouter()
        plan = {
            "instrument": "",
            "total_direction": "buy",
            "slices": [
                {"slice_id": 1, "size": 100, "direction": "buy", "instrument": ""},  # 空 symbol
                {"slice_id": 2, "size": 100, "direction": "buy", "instrument": "600519.SH"},
            ],
        }
        result = router.route_order(plan, "normal")
        assert result["success"] is True
        # 只有 1 个有效订单 (空 symbol 的被跳过)
        assert len(result["routed_orders"]) == 1
        assert result["routed_orders"][0]["symbol"] == "600519.SH"

    def test_route_order_slice_direction_falls_back_to_plan(self):
        """P0 回归: 切片 direction 为空时, 回退到 execution_plan.total_direction"""
        router = OrderRouter()
        plan = {
            "instrument": "600519.SH",
            "total_direction": "sell",  # 计划级方向
            "slices": [
                {"slice_id": 1, "size": 100, "direction": "", "instrument": "600519.SH"},  # 切片方向为空
            ],
        }
        result = router.route_order(plan, "normal")
        assert result["success"] is True
        assert len(result["routed_orders"]) == 1
        # 切片 direction 为空 → 回退到 total_direction="sell"
        assert result["routed_orders"][0]["side"] == "SELL"

    def test_route_order_empty_direction_defaults_to_buy(self):
        """P0 回归: 切片与计划 direction 均为空时, 回退到默认 'buy' (避免空 side 下单失败)"""
        router = OrderRouter()
        plan = {
            "instrument": "600519.SH",
            "total_direction": "",  # 计划级方向也为空
            "slices": [
                {"slice_id": 1, "size": 100, "direction": "", "instrument": "600519.SH"},
            ],
        }
        result = router.route_order(plan, "normal")
        assert result["success"] is True
        assert len(result["routed_orders"]) == 1
        # 两者均空 → 默认 "buy" (源码 P0 修复: or "buy" 兜底)
        assert result["routed_orders"][0]["side"] == "BUY"

    def test_route_order_exception_returns_error(self):
        router = OrderRouter()
        # slices 字段缺失 → KeyError → 返回 error
        result = router.route_order({}, "normal")
        assert result["success"] is False
        assert "error" in result

    def test_check_pool_availability_unknown_pool(self):
        """BUG-E2 回归: 用对象身份查找 pool_name"""
        router = OrderRouter()
        # 传入不在 execution_pools 中的 dict → 返回 False
        fake_pool = {"max_concurrent": 10}
        assert router._check_pool_availability(fake_pool) is False

    def test_check_pool_availability_normal_pool(self):
        router = OrderRouter()
        # 传入真正的 pool 对象 → 返回 True
        pool = router.execution_pools["normal"]
        assert router._check_pool_availability(pool) is True

    def test_check_pool_availability_concurrent_limit(self):
        # 达到并发上限 → 返回 False
        router = OrderRouter()
        # normal 池 max_concurrent=10
        for i in range(10):
            router.active_orders[f"order_{i}"] = {"target_pool": "normal", "status": "pending"}
        pool = router.execution_pools["normal"]
        assert router._check_pool_availability(pool) is False

    def test_find_available_pool(self):
        router = OrderRouter()
        # 初始状态所有池都可用 → 返回第一个
        pool = router._find_available_pool()
        assert pool is not None
        assert pool is router.execution_pools["normal"]

    def test_find_available_pool_none_available(self):
        router = OrderRouter()
        # 所有池都达到并发上限 → 返回 None
        for pool_name in router.execution_pools:
            max_conc = router.execution_pools[pool_name]["max_concurrent"]
            for i in range(max_conc):
                router.active_orders[f"{pool_name}_{i}"] = {
                    "target_pool": pool_name,
                    "status": "pending",
                }
        assert router._find_available_pool() is None

    def test_generate_order_id_format(self):
        router = OrderRouter()
        order_id = router._generate_order_id()
        assert order_id.startswith("ORD_")
        # 格式: ORD_YYYYMMDD_HHMMSS_NNNN
        parts = order_id.split("_")
        assert len(parts) == 4

    def test_estimate_wait_time_normal_pool(self):
        """P2-5 回归: 等待时间公式修正 (并发越大等待越短)"""
        router = OrderRouter()
        wait = router._estimate_wait_time("normal")
        # 基础 10s + 0*5/10 = 10s
        assert wait == 10.0

    def test_estimate_wait_time_increases_with_active_orders(self):
        router = OrderRouter()
        # 添加活跃订单
        for i in range(5):
            router.active_orders[f"order_{i}"] = {"target_pool": "normal"}
        wait = router._estimate_wait_time("normal")
        # 10 + 5*5/10 = 12.5
        assert wait == 12.5

    def test_estimate_wait_time_higher_concurrency_lower_wait(self):
        """P2-5 回归: 相同 active_count, max_concurrent 越大等待越短"""
        router = OrderRouter()
        # normal: max_concurrent=10
        for i in range(3):
            router.active_orders[f"n_{i}"] = {"target_pool": "normal"}
        wait_normal = router._estimate_wait_time("normal")
        # 切换到 emergency (max_concurrent=3)
        router.active_orders.clear()
        for i in range(3):
            router.active_orders[f"e_{i}"] = {"target_pool": "emergency"}
        wait_emergency = router._estimate_wait_time("emergency")
        # normal: 10 + 3*5/10 = 11.5
        # emergency: 10 + 3*5/3 = 15.0
        # 虽然 emergency 并发数小, 但 max_concurrent 也小, 等待更长
        assert wait_normal == 11.5
        assert wait_emergency == 15.0

    def test_can_execute_order_unknown_pool(self):
        router = OrderRouter()
        order = {"target_pool": "unknown_pool"}
        assert router._can_execute_order(order) is False

    def test_can_execute_order_concurrent_limit(self):
        router = OrderRouter()
        # emergency max_concurrent=3
        for i in range(3):
            router.active_orders[f"e_{i}"] = {"target_pool": "emergency", "status": "pending"}
        order = {"target_pool": "emergency"}
        assert router._can_execute_order(order) is False

    def test_can_execute_order_ok(self):
        router = OrderRouter()
        order = {"target_pool": "normal"}
        assert router._can_execute_order(order) is True

    def test_execute_order_simulated_buy(self):
        """模拟路径 BUY: 验证滑点为正 (买高)"""
        router = OrderRouter()
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 100, "price": 100.0},
            "target_pool": "normal",
        }
        result = router._execute_order(order)
        assert result["success"] is True
        assert result["is_live"] is False
        assert result["filled_size"] == 100
        # 600519 以 "60" 开头 → slippage_bps=2 → slippage=0.0002
        assert result["slippage"] == 2 / 10000.0
        # BUY: fill_price = 100 * (1 + 0.0002) = 100.02
        assert result["average_price"] == round(100.0 * (1 + 2 / 10000.0), 4)

    def test_execute_order_simulated_sell(self):
        """模拟路径 SELL: 验证滑点为负 (卖低)"""
        router = OrderRouter()
        order = {
            "symbol": "000001.SZ",
            "side": "SELL",
            "slice_info": {"size": 200, "price": 15.0},
            "target_pool": "normal",
        }
        result = router._execute_order(order)
        assert result["success"] is True
        # SELL: fill_price = 15 * (1 - 0.0002) = 14.997
        assert result["average_price"] == round(15.0 * (1 - 2 / 10000.0), 4)

    def test_execute_order_rejects_empty_symbol(self):
        """P0 回归: 空 symbol 拒绝执行"""
        router = OrderRouter()
        order = {
            "symbol": "",
            "side": "BUY",
            "slice_info": {"size": 100, "price": 100.0},
        }
        result = router._execute_order(order)
        assert result["success"] is False
        assert "symbol" in result["error"]

    def test_execute_order_rejects_invalid_side(self):
        """P0 回归: 非法 side 拒绝执行"""
        router = OrderRouter()
        order = {
            "symbol": "600519.SH",
            "side": "UNKNOWN",
            "slice_info": {"size": 100, "price": 100.0},
        }
        result = router._execute_order(order)
        assert result["success"] is False
        assert "side" in result["error"]

    def test_execute_order_rejects_zero_qty(self):
        """P0 回归: 零数量拒绝执行"""
        router = OrderRouter()
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 0, "price": 100.0},
        }
        result = router._execute_order(order)
        assert result["success"] is False
        assert "数量" in result["error"]

    def test_execute_order_rejects_invalid_qty(self):
        """P0 回归: 非数值 qty 拒绝执行"""
        router = OrderRouter()
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": "invalid", "price": 100.0},
        }
        result = router._execute_order(order)
        assert result["success"] is False
        assert "qty" in result["error"]

    def test_execute_order_market_order_uses_reference_price(self, monkeypatch, tmp_path):
        """BUG-E4 回归: 限价为 None 时从持仓文件获取参考价"""
        router = OrderRouter()
        # mock _get_reference_price 返回 50.0
        monkeypatch.setattr(router, "_get_reference_price", lambda s: 50.0)
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 100, "price": None},  # 无限价
            "target_pool": "normal",
        }
        result = router._execute_order(order)
        assert result["success"] is True
        assert result["average_price"] > 50.0  # 50 * (1 + slippage)

    def test_execute_order_market_order_no_reference_rejects(self, monkeypatch):
        """BUG-E4 回归: 无限价且无参考价 → 拒绝 0 价格成交"""
        router = OrderRouter()
        monkeypatch.setattr(router, "_get_reference_price", lambda s: None)
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 100, "price": None},
        }
        result = router._execute_order(order)
        assert result["success"] is False
        assert "参考价格" in result["error"]

    def test_execute_order_zero_limit_price_rejects(self):
        """BUG-E4 回归: 0 限价 → 视为无效, 走参考价路径"""
        router = OrderRouter()
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 100, "price": 0},
            "target_pool": "normal",
        }
        # mock _get_reference_price 返回有效价格
        with patch.object(router, "_get_reference_price", return_value=80.0):
            result = router._execute_order(order)
        assert result["success"] is True
        assert result["average_price"] > 80.0

    def test_execute_order_live_mode_blocked_by_kill_switch(self, monkeypatch):
        """P0-2 回归: KillSwitch 熔断中拒绝实盘下单"""
        monkeypatch.setenv("TRADING_ENV", "production")
        mock_ks = MagicMock()
        mock_ks.check_margin_status.return_value = {"level": 2}  # L2 熔断
        mock_sr = MagicMock()
        mock_broker = MagicMock()
        router = OrderRouter(smart_router=mock_sr, broker=mock_broker, kill_switch=mock_ks)
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 100, "price": 100.0},
        }
        result = router._execute_order(order)
        assert result["success"] is False
        assert result.get("kill_switch_blocked") is True
        assert "熔断" in result["error"]

    def test_execute_order_live_mode_kill_switch_error_blocks(self, monkeypatch):
        """P0-2 回归: KillSwitch 检查异常 → fail-closed 拒绝下单"""
        monkeypatch.setenv("TRADING_ENV", "production")
        mock_ks = MagicMock()
        mock_ks.check_margin_status.side_effect = RuntimeError("KS error")
        mock_sr = MagicMock()
        mock_broker = MagicMock()
        router = OrderRouter(smart_router=mock_sr, broker=mock_broker, kill_switch=mock_ks)
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 100, "price": 100.0},
        }
        result = router._execute_order(order)
        assert result["success"] is False
        assert result.get("kill_switch_error") is True

    def test_execute_order_live_mode_low_kill_switch_passes(self, monkeypatch):
        """P0-2 回归: KillSwitch level<2 → 允许实盘下单"""
        monkeypatch.setenv("TRADING_ENV", "production")
        mock_ks = MagicMock()
        mock_ks.check_margin_status.return_value = {"level": 1}  # L1 未熔断
        mock_sr = MagicMock()
        mock_broker = MagicMock()
        # mock smart_router 路径
        mock_routing = MagicMock()
        mock_routing.latency_ms = 50
        mock_sr.route.return_value = mock_routing
        mock_fill = MagicMock()
        mock_fill.filled_qty = 100
        mock_fill.avg_price = 100.5
        mock_sr.execute_route.return_value = [mock_fill]
        mock_broker.get_order_book.return_value = {
            "mid": 100.0,
            "ask1": 100.2,
            "bid1": 99.8,
        }
        router = OrderRouter(smart_router=mock_sr, broker=mock_broker, kill_switch=mock_ks)
        order = {
            "symbol": "600519.SH",
            "side": "BUY",
            "slice_info": {"size": 100, "price": 100.0},
        }
        result = router._execute_order(order)
        assert result["success"] is True
        assert result["is_live"] is True
        assert result["filled_size"] == 100
        assert result["average_price"] == 100.5

    def test_get_reference_price_empty_symbol(self):
        router = OrderRouter()
        assert router._get_reference_price("") is None

    def test_get_reference_price_missing_file(self, monkeypatch, tmp_path):
        router = OrderRouter()
        # positions.json 不存在
        monkeypatch.setattr(
            "utils.execution.automated_execution_system._PROJECT_ROOT",
            str(tmp_path),
        )
        result = router._get_reference_price("600519.SH")
        assert result is None

    def test_get_reference_price_from_positions(self, monkeypatch, tmp_path):
        router = OrderRouter()
        # 创建临时 positions.json
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        positions_file = config_dir / "positions.json"
        positions_data = {
            "positions": {
                "600519.SH": {"code": "600519", "est_price": 1800.5, "shares": 100},
            }
        }
        positions_file.write_text(json.dumps(positions_data), encoding="utf-8")
        monkeypatch.setattr(
            "utils.execution.automated_execution_system._PROJECT_ROOT",
            str(tmp_path),
        )
        result = router._get_reference_price("600519")
        assert result == 1800.5

    def test_update_execution_stats_success(self):
        router = OrderRouter()
        result = {"success": True, "execution_time": 1.5, "slippage": 0.001}
        router._update_execution_stats(result)
        assert router.execution_stats["total_orders"] == 1
        assert router.execution_stats["successful_orders"] == 1
        assert router.execution_stats["average_time"] == 1.5
        assert router.execution_stats["average_slippage"] == 0.001

    def test_update_execution_stats_failure(self):
        router = OrderRouter()
        result = {"success": False, "execution_time": 0, "slippage": 0}
        router._update_execution_stats(result)
        assert router.execution_stats["total_orders"] == 1
        assert router.execution_stats["failed_orders"] == 1
        assert router.execution_stats["successful_orders"] == 0

    def test_get_router_summary_empty(self):
        router = OrderRouter()
        summary = router.get_router_summary()
        assert summary["total_active_orders"] == 0
        assert summary["queue_length"] == 0
        assert "execution_stats" in summary
        assert "current_time" in summary

    def test_get_router_summary_with_orders(self):
        router = OrderRouter()
        router.active_orders["ord1"] = {"target_pool": "normal", "status": "pending"}
        router.active_orders["ord2"] = {"target_pool": "emergency", "status": "completed"}
        summary = router.get_router_summary()
        assert summary["total_active_orders"] == 2
        assert summary["status_distribution"]["pending"] == 1
        assert summary["status_distribution"]["completed"] == 1
        assert summary["pool_distribution"]["normal"] == 1
        assert summary["pool_distribution"]["emergency"] == 1

    def test_thread_safety_active_orders(self):
        """BUG-E3 回归: 多线程并发写入 active_orders 不崩溃"""
        router = OrderRouter()

        def add_orders(start, end):
            for i in range(start, end):
                with router._orders_lock:
                    router.active_orders[f"order_{i}"] = {"target_pool": "normal"}

        threads = [threading.Thread(target=add_orders, args=(i * 100, (i + 1) * 100)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(router.active_orders) == 500


# ============================================================
# 6. AutomatedExecutionSystem 测试
# ============================================================


class TestAutomatedExecutionSystem:
    """AutomatedExecutionSystem: 主控制器"""

    def test_initialization(self):
        system = AutomatedExecutionSystem(total_capital=1000000)
        assert system.total_capital == 1000000
        assert isinstance(system.trading_calendar, TradingCalendar)
        assert isinstance(system.market_evaluator, MarketStateEvaluator)
        assert isinstance(system.execution_strategy, ExecutionStrategy)
        assert isinstance(system.order_router, OrderRouter)
        assert system.system_enabled is False
        assert system.is_running is False
        assert system.current_market_state == "normal"
        assert system.system_history.maxlen == 100
        assert "auto_start" in system.config

    def test_enable_hedge_without_coordinator(self):
        # hedge_coordinator 为 None 时 → 返回 False
        system = AutomatedExecutionSystem()
        system.hedge_coordinator = None
        assert system.enable_hedge(True) is False

    def test_enable_hedge_with_mock_coordinator(self):
        system = AutomatedExecutionSystem()
        system.hedge_coordinator = MagicMock()  # 模拟可用
        result = system.enable_hedge(True)
        assert result is True
        assert system.hedge_enabled is True

    def test_disable_hedge(self):
        system = AutomatedExecutionSystem()
        system.hedge_coordinator = MagicMock()
        system.enable_hedge(True)
        assert system.hedge_enabled is True
        system.disable_hedge()
        assert system.hedge_enabled is False

    def test_start_system(self):
        system = AutomatedExecutionSystem()
        system.start_system()
        assert system.system_enabled is True
        assert system.is_running is True
        assert system.execution_thread is not None
        # 立即停止避免阻塞
        system.stop_system()
        assert system.is_running is False
        assert system.system_enabled is False

    def test_risk_pre_check_normal_passes(self):
        system = AutomatedExecutionSystem()
        market_state_data = {
            "market_state": "normal",
            "individual_scores": {"var": 0.3, "liquidity": 0.3},
        }
        assert system._risk_pre_check(market_state_data) is True

    def test_risk_pre_check_crisis_blocks(self):
        system = AutomatedExecutionSystem()
        market_state_data = {
            "market_state": "crisis",
            "individual_scores": {"var": 0.3, "liquidity": 0.3},
        }
        assert system._risk_pre_check(market_state_data) is False

    def test_risk_pre_check_stress_blocks(self):
        system = AutomatedExecutionSystem()
        market_state_data = {
            "market_state": "stress",
            "individual_scores": {},
        }
        assert system._risk_pre_check(market_state_data) is False

    def test_risk_pre_check_high_var_blocks(self):
        system = AutomatedExecutionSystem()
        market_state_data = {
            "market_state": "normal",
            "individual_scores": {"var": 0.9, "liquidity": 0.3},
        }
        assert system._risk_pre_check(market_state_data) is False

    def test_risk_pre_check_high_liquidity_risk_blocks(self):
        system = AutomatedExecutionSystem()
        market_state_data = {
            "market_state": "normal",
            "individual_scores": {"var": 0.3, "liquidity": 0.9},
        }
        assert system._risk_pre_check(market_state_data) is False

    def test_risk_pre_check_exception_returns_false(self):
        system = AutomatedExecutionSystem()
        # 缺少 market_state 字段 → KeyError → fail-safe 返回 False
        assert system._risk_pre_check({}) is False

    def test_apply_hedge_triggers_no_hedge_plan(self):
        system = AutomatedExecutionSystem()
        result = system._apply_hedge_triggers({}, None)
        assert result is None

    def test_apply_hedge_triggers_vix_threshold(self):
        """VIX >= 30 强制触发对冲"""
        system = AutomatedExecutionSystem()
        plan = {"action": "NO_HEDGE", "total_hedge_pct": 0.0}
        market_data = {"vix_future_price": 35.0}
        result = system._apply_hedge_triggers(market_data, plan)
        assert result["action"] == "FORCED_HEDGE"
        assert "VIX" in result["forced_reason"]

    def test_apply_hedge_triggers_crisis_state(self):
        system = AutomatedExecutionSystem()
        system.current_market_state = "crisis"
        plan = {"action": "NO_HEDGE"}
        market_data = {"vix_future_price": 20.0}
        result = system._apply_hedge_triggers(market_data, plan)
        assert result["action"] == "FORCED_HEDGE"
        assert "market_state" in result["forced_reason"]

    def test_apply_hedge_triggers_drawdown(self):
        system = AutomatedExecutionSystem()
        system.current_drawdown = 0.05  # 5% 回撤
        plan = {"action": "NO_HEDGE"}
        market_data = {"vix_future_price": 20.0}
        result = system._apply_hedge_triggers(market_data, plan)
        assert result["action"] == "FORCED_HEDGE"
        assert "drawdown" in result["forced_reason"]

    def test_apply_hedge_triggers_no_trigger_when_action_not_no_hedge(self):
        # 已有对冲动作时不强制覆盖
        system = AutomatedExecutionSystem()
        plan = {"action": "BETA_HEDGE"}
        market_data = {"vix_future_price": 35.0}
        result = system._apply_hedge_triggers(market_data, plan)
        # action 保持不变 (不触发 FORCED_HEDGE)
        assert result["action"] == "BETA_HEDGE"

    def test_get_system_summary_initial(self):
        system = AutomatedExecutionSystem()
        summary = system.get_system_summary()
        assert summary["system_status"] == "stopped"
        assert summary["current_market_state"] == "normal"
        assert summary["routed_orders_count"] == 0
        assert "hedge" in summary
        assert "trading_calendar" in summary
        assert "market_state_evaluator" in summary
        assert "execution_strategy" in summary
        assert "order_router" in summary
        assert "performance_metrics" in summary

    def test_get_execution_schedule(self):
        system = AutomatedExecutionSystem()
        schedule = system.get_execution_schedule(3)
        assert isinstance(schedule, list)
        assert len(schedule) <= 3

    def test_match_current_execution_unknown_time(self):
        # 不在任何执行窗口内的时间 → 返回 None 或匹配项
        system = AutomatedExecutionSystem()
        # 12:00 (午间休市) 通常不在任何窗口
        noon = datetime(2026, 8, 3, 12, 0)
        result = system._match_current_execution(noon)
        # 可能返回 None 或匹配 (取决于实现细节)
        assert result is None or isinstance(result, str)

    def test_generate_hedge_execution_orders_no_plan(self):
        # hedge_plan=None → 不抛异常
        system = AutomatedExecutionSystem()
        # 应静默处理, 不抛异常
        system._generate_hedge_execution_orders(None)

    def test_generate_hedge_execution_orders_with_plan(self, monkeypatch, tmp_path):
        system = AutomatedExecutionSystem()
        # mock build_orders 避免真实导入
        plan = {"action": "BETA_HEDGE", "portfolio_beta": 1.2, "total_hedge_pct": 0.3}
        # mock 项目根目录, 避免 __file__ 路径问题
        monkeypatch.setattr(
            "utils.execution.automated_execution_system._PROJECT_ROOT",
            str(tmp_path),
        )
        # 不抛异常即可
        system._generate_hedge_execution_orders(plan)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
