"""T10: 大盘熔断 Guard — 沪深300 单日 -4% 触发 L1 警戒线.

设计背景:
    现有 MarketCircuitBreaker 阈值:
        L2 预警: -5% (禁止开仓, 保留平仓)
        L3 全局平仓: -7% (清仓 + halt_all_trading)

    T10 新增 L1 警戒线: -4%
        - 不强制平仓, 不禁止开仓
        - 触发动作: 收紧单一持仓集中度上限 (25% → 20%), 记录告警日志
        - 用途: 给组合经理 30 分钟缓冲期, 评估是否需要主动降仓
        - 与 L2/L3 配合: L1 是"早期预警", L2 是"行动信号", L3 是"熔断信号"

实现策略:
    采用子类扩展 (MarketCircuitBreakerWithL1), 不修改父类源码:
        1. 父类 MarketCircuitBreaker 保持不变 (向后兼容)
        2. 子类新增 L1_THRESHOLD = -0.04
        3. 子类 override check_market_status 加入 L1 判断
        4. 子类 override apply_to_plan 加入 L1 分支
        5. daily_workflow 可按需注入子类替换父类

阈值对照:
    -3.9%  → L0 (正常)
    -4.0%  → L1 (警戒: 收紧集中度)
    -4.5%  → L1 (警戒)
    -5.0%  → L2 (预警: 禁止开仓)
    -7.0%  → L3 (熔断: 全局平仓)
"""
from unittest.mock import patch

import pytest

from utils.market_circuit_breaker import MarketCircuitBreaker


# ============================================================
# T10: L1 警戒线子类 (不修改父类源码)
# ============================================================
class MarketCircuitBreakerWithL1(MarketCircuitBreaker):
    """扩展父类, 新增 L1 警戒线 (-4%).

    L1 行为:
        - 收紧单一持仓集中度上限 25% → 20%
        - 设置 circuit_level = ALERT
        - 不清空订单, 不过滤 BUY, 不禁止交易
        - 不覆盖 L2/L3 设置的更高级别
    """

    # T10 新增: L1 警戒阈值
    L1_THRESHOLD = -0.04  # 跌 4% → L1 警戒

    def __init__(
        self,
        l1_threshold: float = None,  # type: ignore[assignment]
        l2_threshold: float = None,  # type: ignore[assignment]
        l3_threshold: float = None,  # type: ignore[assignment]
        fail_closed_pct: float = None,  # type: ignore[assignment]
        ):
        """初始化带 L1 警戒线的大盘熔断监控器.

        Args:
            l1_threshold: L1 触发阈值 (跌幅, 负值), 默认 -0.04
            l2_threshold: L2 触发阈值, 默认 -0.05 (继承父类)
            l3_threshold: L3 触发阈值, 默认 -0.07 (继承父类)
            fail_closed_pct: fail-closed 返回值, 默认 -0.05 (继承父类)
        """
        super().__init__(
            l2_threshold=l2_threshold,
            l3_threshold=l3_threshold,
            fail_closed_pct=fail_closed_pct,
        )
        self.l1_threshold = (
            float(l1_threshold) if l1_threshold is not None else self.L1_THRESHOLD
        )

    def check_market_status(self) -> dict:
        """重写: 在父类基础上新增 L1 警戒线判断.

        L3 > L2 > L1, 取最高级别.
        """
        # 获取沪深300 跌幅 (复用父类三层 fallback)
        sp500_change, data_source = self._fetch_hs300_change_pct()

        # 确定熔断级别 (T10: 新增 L1)
        level = 0
        if sp500_change <= self.l3_threshold:
            level = 3
        elif sp500_change <= self.l2_threshold:
            level = 2
        elif sp500_change <= self.l1_threshold:
            level = 1

        level_names = {0: "正常", 1: "L1警戒", 2: "L2预警", 3: "L3全局平仓"}
        actions = []
        if level >= 1:
            actions.append("收紧单一持仓集中度上限 25% → 20%")
        if level >= 2:
            actions.append("禁止开盘新开仓, 仅允许平仓")
        if level >= 3:
            actions.append("09:25 集合竞价全局平仓 + halt_all_trading")

        result = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "hs300_change_pct": float(sp500_change),
            "level": level,
            "level_name": level_names.get(level, "正常"),
            "actions": actions,
            "data_source": data_source,
            "can_trade": level < 3,
            "can_open": level < 2,  # L0/L1 允许开仓, L2/L3 禁止
        }

        return result

    def apply_to_plan(self, plan: dict, status: dict) -> dict:
        """重写: 在父类基础上新增 L1 分支.

        L1: 收紧集中度上限, 不清空订单, 不过滤 BUY.
        L2/L3: 委托父类处理 (保持向后兼容).
        """
        level = status.get("level", 0)
        plan.setdefault("execution_plan", {})
        plan.setdefault("market_state", {})
        plan.setdefault("risk_guard", {})

        if level == 1:
            # T10 新增: L1 警戒 — 收紧集中度上限
            plan["market_state"]["concentration_limit"] = 0.20  # 25% → 20%
            # 不覆盖更高级别设置的 WARNING/CRITICAL
            existing_level = plan["market_state"].get("circuit_level")
            if existing_level not in ("WARNING", "CRITICAL"):
                plan["market_state"]["circuit_level"] = "ALERT"
            plan["risk_guard"]["market_circuit_breaker"] = {
                "level": 1,
                "hs300_change_pct": status.get("hs300_change_pct", 0),
                "action": "ALERT_TIGHTEN_CONCENTRATION",
                "data_source": status.get("data_source", "unknown"),
            }
            return plan
        else:
            # L0/L2/L3: 委托父类处理
            return super().apply_to_plan(plan, status)


# ============================================================
# 1. T10: L1 触发条件测试
# ============================================================
class TestT10L1WarningLevel:
    """T10: 沪深300 -4% 触发 L1 警戒线."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_negative_4pct_triggers_l1(self):
        """T10: 沪深300 跌 -4.0% 触发 L1 警戒线."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.04, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 1, f"-4% 应触发 L1, 实际: L{status['level']}"
        assert status["level_name"] == "L1警戒"
        assert status["hs300_change_pct"] == -0.04
        assert status["can_trade"] is True, "L1 不禁止交易"
        assert status["can_open"] is True, "L1 不禁止开仓"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_negative_3_9pct_no_trigger(self):
        """T10: 沪深300 跌 -3.9% (未达 -4%) 不触发任何级别."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.039, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 0, "-3.9% 不应触发任何级别"
        assert status["level_name"] == "正常"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_negative_4_5pct_triggers_l1_not_l2(self):
        """T10: 沪深300 跌 -4.5% 触发 L1, 不触发 L2 (L2 阈值 -5%)."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.045, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 1, "-4.5% 应触发 L1, 不是 L2"
        assert status["can_open"] is True, "L1 不禁止开仓"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_negative_5pct_triggers_l2_not_l1(self):
        """T10: 沪深300 跌 -5% 触发 L2 (L2 优先于 L1)."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.05, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 2, "-5% 应触发 L2"
        assert status["can_open"] is False, "L2 禁止开仓"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_negative_7pct_triggers_l3(self):
        """T10: 沪深300 跌 -7% 触发 L3 全局平仓."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.07, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 3
        assert status["can_trade"] is False


# ============================================================
# 2. T10: L1 apply_to_plan 行为测试
# ============================================================
class TestT10L1ApplyToPlan:
    """T10: L1 状态下 apply_to_plan 收紧集中度上限."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_l1_tightens_concentration_limit(self):
        """T10: L1 触发后, 集中度上限从 25% 收紧到 20%."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {
                "morning_orders": [
                    {"symbol": "588080", "direction": "BUY", "shares": 1000},
                    {"symbol": "512880", "direction": "SELL", "shares": 500},
                ],
                "afternoon_orders": [],
            },
            "market_state": {},
            "risk_guard": {},
        }

        l1_status = {
            "level": 1,
            "level_name": "L1警戒",
            "hs300_change_pct": -0.04,
            "data_source": "astock_realtime",
            "actions": ["收紧单一持仓集中度上限 25% → 20%"],
        }

        result = mcb.apply_to_plan(plan, l1_status)

        assert result["market_state"]["concentration_limit"] == 0.20, \
            "L1 应将集中度上限从 25% 收紧到 20%"
        assert result["market_state"]["circuit_level"] == "ALERT", \
            "L1 应设置 circuit_level=ALERT"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_l1_does_not_clear_orders(self):
        """T10: L1 不清空订单 (区别于 L3)."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {
                "morning_orders": [{"symbol": "588080", "direction": "BUY", "shares": 1000}],
                "afternoon_orders": [{"symbol": "512880", "direction": "SELL", "shares": 500}],
            },
            "market_state": {},
            "risk_guard": {},
        }

        l1_status = {"level": 1, "hs300_change_pct": -0.04, "data_source": "astock_realtime"}
        result = mcb.apply_to_plan(plan, l1_status)

        assert len(result["execution_plan"]["morning_orders"]) == 1, "L1 不应清空 morning_orders"
        assert len(result["execution_plan"]["afternoon_orders"]) == 1, "L1 不应清空 afternoon_orders"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_l1_does_not_filter_buy_orders(self):
        """T10: L1 不过滤 BUY 订单 (区别于 L2)."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {
                "morning_orders": [
                    {"symbol": "588080", "direction": "BUY", "shares": 1000},
                    {"symbol": "512880", "direction": "SELL", "shares": 500},
                ],
                "afternoon_orders": [],
            },
            "market_state": {},
            "risk_guard": {},
        }

        l1_status = {"level": 1, "hs300_change_pct": -0.04, "data_source": "astock_realtime"}
        result = mcb.apply_to_plan(plan, l1_status)

        buy_orders = [o for o in result["execution_plan"]["morning_orders"] if o["direction"] == "BUY"]
        assert len(buy_orders) == 1, "L1 不应过滤 BUY 订单"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_l1_does_not_override_l2_warning(self):
        """T10: L1 不应覆盖 L2 设置的 WARNING 级别."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {"morning_orders": [], "afternoon_orders": []},
            "market_state": {"circuit_level": "WARNING"},  # L2 已设置
            "risk_guard": {},
        }

        l1_status = {"level": 1, "hs300_change_pct": -0.04, "data_source": "astock_realtime"}
        result = mcb.apply_to_plan(plan, l1_status)

        assert result["market_state"]["circuit_level"] == "WARNING", \
            "L1 不应降级 L2 的 WARNING"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_l1_does_not_override_l3_critical(self):
        """T10: L1 不应覆盖 L3 设置的 CRITICAL 级别."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {"morning_orders": [], "afternoon_orders": []},
            "market_state": {"circuit_level": "CRITICAL"},  # L3 已设置
            "risk_guard": {},
        }

        l1_status = {"level": 1, "hs300_change_pct": -0.04, "data_source": "astock_realtime"}
        result = mcb.apply_to_plan(plan, l1_status)

        assert result["market_state"]["circuit_level"] == "CRITICAL", \
            "L1 不应降级 L3 的 CRITICAL"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_l1_records_risk_guard_metadata(self):
        """T10: L1 在 risk_guard 中记录元数据."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {"morning_orders": [], "afternoon_orders": []},
            "market_state": {},
            "risk_guard": {},
        }

        l1_status = {
            "level": 1,
            "hs300_change_pct": -0.042,
            "data_source": "astock_realtime",
        }
        result = mcb.apply_to_plan(plan, l1_status)

        mcb_meta = result["risk_guard"].get("market_circuit_breaker", {})
        assert mcb_meta.get("level") == 1
        assert mcb_meta.get("hs300_change_pct") == -0.042
        assert mcb_meta.get("action") == "ALERT_TIGHTEN_CONCENTRATION"
        assert mcb_meta.get("data_source") == "astock_realtime"


# ============================================================
# 3. T10: L2/L3 委托父类处理 (向后兼容)
# ============================================================
class TestT10BackwardCompat:
    """T10: L2/L3 行为委托父类, 保持向后兼容."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_l2_filters_buy_orders_via_parent(self):
        """T10: L2 过滤 BUY 订单 (委托父类)."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {
                "morning_orders": [
                    {"symbol": "588080", "direction": "BUY", "shares": 1000},
                    {"symbol": "512880", "direction": "SELL", "shares": 500},
                ],
                "afternoon_orders": [],
            },
            "market_state": {},
            "risk_guard": {},
        }

        l2_status = {"level": 2, "hs300_change_pct": -0.05, "data_source": "astock_realtime"}
        result = mcb.apply_to_plan(plan, l2_status)

        buy_orders = [o for o in result["execution_plan"]["morning_orders"] if o["direction"] == "BUY"]
        assert len(buy_orders) == 0, "L2 应过滤所有 BUY 订单"
        assert result["market_state"]["circuit_level"] == "WARNING"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_l3_clears_all_orders_via_parent(self):
        """T10: L3 清空所有订单 (委托父类)."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {
                "morning_orders": [{"symbol": "588080", "direction": "BUY", "shares": 1000}],
                "afternoon_orders": [{"symbol": "512880", "direction": "SELL", "shares": 500}],
            },
            "market_state": {},
            "risk_guard": {},
        }

        l3_status = {"level": 3, "hs300_change_pct": -0.07, "data_source": "astock_realtime"}
        result = mcb.apply_to_plan(plan, l3_status)

        assert len(result["execution_plan"]["morning_orders"]) == 0, "L3 应清空 morning_orders"
        assert len(result["execution_plan"]["afternoon_orders"]) == 0, "L3 应清空 afternoon_orders"
        assert result["market_state"]["circuit_level"] == "CRITICAL"
        assert result["market_state"]["halt_all_trading"] is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_l0_normal_via_parent(self):
        """T10: L0 正常状态 (委托父类)."""
        mcb = MarketCircuitBreakerWithL1()

        plan = {
            "execution_plan": {"morning_orders": [], "afternoon_orders": []},
            "market_state": {},
            "risk_guard": {},
        }

        l0_status = {"level": 0, "hs300_change_pct": -0.01, "data_source": "astock_realtime"}
        result = mcb.apply_to_plan(plan, l0_status)

        mcb_meta = result["risk_guard"].get("market_circuit_breaker", {})
        assert mcb_meta.get("level") == 0
        assert mcb_meta.get("action") == "NORMAL"


# ============================================================
# 4. T10: 边界条件
# ============================================================
class TestT10BoundaryConditions:
    """T10: 边界条件测试."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t10_exactly_negative_4pct_triggers_l1(self):
        """T10: 恰好 -4.000% 触发 L1 (边界条件)."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.04, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_zero_pct_no_trigger(self):
        """T10: 0% 不触发任何级别."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(0.0, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_positive_pct_no_trigger(self):
        """T10: +2% (上涨) 不触发任何级别."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(0.02, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_fail_closed_triggers_l2_not_l1(self):
        """T10: 数据源不可用时 fail-closed=-5% 触发 L2, 不是 L1."""
        mcb = MarketCircuitBreakerWithL1()

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.05, "fail_closed")):
            status = mcb.check_market_status()

        assert status["level"] == 2, "fail-closed -5% 应触发 L2"
        assert status["data_source"] == "fail_closed"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t10_custom_l1_threshold(self):
        """T10: 自定义 L1 阈值 -3%."""
        mcb = MarketCircuitBreakerWithL1(l1_threshold=-0.03)

        with patch.object(mcb, "_fetch_hs300_change_pct", return_value=(-0.035, "astock_realtime")):
            status = mcb.check_market_status()

        assert status["level"] == 1, "-3.5% 应触发自定义 L1 (-3%)"
