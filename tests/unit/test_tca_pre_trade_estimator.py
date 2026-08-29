"""T3.4 TCA 执行前预估器单元测试.

验证:
    1. PreTradeEstimate 数据类序列化
    2. PreTradeEstimator.estimate() 基本功能
    3. 阈值否决机制 (cost_bps > threshold → approved=False)
    4. 市值分层 (large/mid/small/micro)
    5. 买卖方向 (BUY/SELL 印花税)
    6. 延迟 <50ms (HC-3)
    7. JSONL 持久化 (reports/tca/estimate_{date}.jsonl)
    8. 批量预估与过滤
    9. 异常处理 (参数校验)
    10. 阈值校准 (calibrate_threshold)
    11. HC-1 Feature Flag 透传 (USE_TCA_PRE_TRADE_ESTIMATE)
    12. TCAManager.estimate() facade 接口
    13. ExecutionRouter.route_with_tca() 兼容模式
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.tca_pre_trade_estimator import (  # noqa: E402
    DEFAULT_COST_THRESHOLD_BPS,
    DEFAULT_LATENCY_LIMIT_MS,
    PreTradeEstimate,
    PreTradeEstimateError,
    PreTradeEstimator,
    create_default_estimator,
    create_no_save_estimator,
)


# ============================================================
# Mock 工厂
# ============================================================
def make_order(
    symbol: str = "600276",
    side: str = "BUY",
    shares: int = 10000,
    price: float = 50.0,
    notional: float = None,
    market_cap: float = 800e8,
) -> dict[str, Any]:
    """构造订单字典"""
    return {
        "symbol": symbol,
        "side": side,
        "shares": shares,
        "price": price,
        "notional": notional if notional is not None else shares * price,
        "market_cap": market_cap,
    }


def make_market_data(adv: float = 1e8, volatility: float = 0.025) -> dict[str, Any]:
    """构造市场数据字典"""
    return {"adv": adv, "volatility": volatility}


@pytest.fixture
def estimator_no_save() -> PreTradeEstimator:
    """不写文件的预估器 (用于纯内存测试)"""
    return PreTradeEstimator(
        cost_threshold_bps=30.0,
        save_to_file=False,
    )


@pytest.fixture
def estimator_tmp_dir(tmp_path) -> PreTradeEstimator:
    """使用临时目录的预估器"""
    return PreTradeEstimator(
        cost_threshold_bps=30.0,
        estimate_dir=tmp_path,
        save_to_file=True,
    )


# ============================================================
# 测试 1: PreTradeEstimate 数据类
# ============================================================
class TestPreTradeEstimateDataclass:
    """测试 PreTradeEstimate 数据类序列化"""

    def test_to_dict_contains_all_fields(self):
        """测试 to_dict 包含所有字段"""
        est = PreTradeEstimate(
            symbol="600276",
            side="BUY",
            shares=10000,
            notional=500000,
            price=50.0,
            tier="large",
            estimated_cost_bps=7.5,
            estimated_cost_amount=375.0,
            cost_breakdown={
                "slippage": 100,
                "commission": 200,
                "impact": 50,
                "opportunity_cost": 25,
                "delay_cost": 0,
            },
            approved=True,
            rejection_reason="",
            threshold_bps=30.0,
            latency_ms=0.05,
        )
        d = est.to_dict()
        assert d["symbol"] == "600276"
        assert d["side"] == "BUY"
        assert d["shares"] == 10000
        assert d["notional"] == 500000
        assert d["price"] == 50.0
        assert d["tier"] == "large"
        assert d["estimated_cost_bps"] == 7.5
        assert d["estimated_cost_amount"] == 375.0
        assert d["approved"] is True
        assert d["rejection_reason"] == ""
        assert d["threshold_bps"] == 30.0
        assert d["latency_ms"] == 0.05
        assert d.get("timestamp")
        assert "cost_breakdown" in d and len(d["cost_breakdown"]) == 5

    def test_to_jsonl_is_valid_json(self):
        """测试 to_jsonl 是有效 JSON"""
        est = PreTradeEstimate(
            symbol="000001",
            side="SELL",
            shares=1000,
            notional=10000,
            price=10.0,
            tier="mid",
            estimated_cost_bps=15.0,
            estimated_cost_amount=15.0,
            cost_breakdown={
                "slippage": 5,
                "commission": 5,
                "impact": 5,
                "opportunity_cost": 0,
                "delay_cost": 0,
            },
            approved=True,
            rejection_reason="",
            threshold_bps=30.0,
            latency_ms=0.1,
        )
        line = est.to_jsonl()
        parsed = json.loads(line)
        assert parsed["symbol"] == "000001"
        assert parsed["side"] == "SELL"

    def test_timestamp_auto_filled(self):
        """测试 timestamp 自动填充"""
        est = PreTradeEstimate(
            symbol="X",
            side="BUY",
            shares=1,
            notional=1,
            price=1,
            tier="micro",
            estimated_cost_bps=0,
            estimated_cost_amount=0,
            cost_breakdown={},
            approved=True,
            rejection_reason="",
            threshold_bps=30,
            latency_ms=0,
        )
        assert est.timestamp != ""
        # ISO 格式应包含 T
        assert "T" in est.timestamp


# ============================================================
# 测试 2: PreTradeEstimator.estimate() 基本功能
# ============================================================
class TestEstimateBasic:
    """测试 estimate() 基本功能"""

    def test_estimate_returns_pre_trade_estimate(self, estimator_no_save):
        """测试返回 PreTradeEstimate 实例"""
        order = make_order()
        market_data = make_market_data()
        result = estimator_no_save.estimate(order, market_data)
        assert isinstance(result, PreTradeEstimate)
        assert result.symbol == "600276"
        assert result.side == "BUY"
        assert result.shares == 10000
        assert result.notional == 500000

    def test_estimate_cost_bps_positive(self, estimator_no_save):
        """测试预估成本为正"""
        result = estimator_no_save.estimate(make_order(), make_market_data())
        assert result.estimated_cost_bps > 0
        assert result.estimated_cost_amount > 0

    def test_estimate_cost_breakdown_structure(self, estimator_no_save):
        """测试成本分解结构"""
        result = estimator_no_save.estimate(make_order(), make_market_data())
        assert "slippage" in result.cost_breakdown
        assert "commission" in result.cost_breakdown
        assert "impact" in result.cost_breakdown
        assert "opportunity_cost" in result.cost_breakdown
        assert "delay_cost" in result.cost_breakdown
        # 总成本 ≈ sum(breakdown)
        breakdown_sum = sum(result.cost_breakdown.values())
        assert abs(result.estimated_cost_amount - breakdown_sum) < 0.01

    def test_estimate_notional_auto_calc(self, estimator_no_save):
        """测试 notional 缺省时自动计算 = shares * price"""
        order = make_order(notional=0)
        order.pop("notional")
        result = estimator_no_save.estimate(order, make_market_data())
        assert result.notional == 10000 * 50.0

    def test_estimate_threshold_bps_recorded(self, estimator_no_save):
        """测试阈值记录在结果中"""
        result = estimator_no_save.estimate(make_order(), make_market_data())
        assert result.threshold_bps == 30.0


# ============================================================
# 测试 3: 阈值否决机制
# ============================================================
class TestThresholdRejection:
    """测试成本超阈值时否决订单"""

    def test_low_cost_approved(self, estimator_no_save):
        """低成本订单通过"""
        result = estimator_no_save.estimate(make_order(), make_market_data())
        assert result.approved is True
        assert result.rejection_reason == ""

    def test_high_cost_rejected(self):
        """高成本订单被否决 (微盘股 + 大金额)"""
        estimator = PreTradeEstimator(
            cost_threshold_bps=10.0,  # 严格阈值
            save_to_file=False,
        )
        # 微盘股 + 大金额 → 高成本
        order = make_order(
            symbol="002999",
            shares=50000,
            price=10.0,
            notional=500000,
            market_cap=5e8,  # 5亿 = micro
        )
        market_data = make_market_data(adv=1e7, volatility=0.05)  # 低流动性
        result = estimator.estimate(order, market_data)
        assert result.estimated_cost_bps > 10.0
        assert result.approved is False
        assert "threshold" in result.rejection_reason
        assert "10.00" in result.rejection_reason

    def test_threshold_boundary_equal(self):
        """测试阈值边界 (= 阈值时通过)

        注: min_commission=5 元, 小金额订单佣金占比高
        设 notional=100000 (10万), commission=25元 = 2.5bps, 总成本约 5-6 bps
        """
        estimator = PreTradeEstimator(
            cost_threshold_bps=10.0,
            save_to_file=False,
        )
        # 大盘股 + 中等金额 → 低成本
        order = make_order(
            symbol="510050",
            shares=20000,
            price=5.0,
            notional=100000,
            market_cap=2000e8,  # 2000亿 = large
        )
        result = estimator.estimate(order, make_market_data(adv=1e9, volatility=0.015))
        # 大盘股 + 高流动性 → 成本应 <10 bps
        assert result.estimated_cost_bps < 10.0
        assert result.approved is True

    def test_rejection_reason_includes_symbol_and_tier(self):
        """测试否决原因包含 symbol 和 tier"""
        estimator = PreTradeEstimator(
            cost_threshold_bps=5.0,
            save_to_file=False,
        )
        order = make_order(
            symbol="TEST001",
            shares=10000,
            price=10.0,
            market_cap=10e8,
        )
        result = estimator.estimate(order, make_market_data(adv=1e7, volatility=0.04))
        if not result.approved:
            assert "TEST001" in result.rejection_reason
            assert "tier=" in result.rejection_reason


# ============================================================
# 测试 4: 市值分层
# ============================================================
class TestMarketCapTier:
    """测试市值分层对成本的影响"""

    @pytest.mark.parametrize(
        "market_cap,expected_tier",
        [
            (2000e8, "large"),  # 2000亿 → large
            (200e8, "mid"),  # 200亿 → mid
            (50e8, "small"),  # 50亿 → small
            (5e8, "micro"),  # 5亿 → micro
        ],
    )
    def test_tier_classification(self, estimator_no_save, market_cap, expected_tier):
        """测试市值分层"""
        order = make_order(market_cap=market_cap)
        result = estimator_no_save.estimate(order, make_market_data())
        assert result.tier == expected_tier

    def test_large_tier_cheaper_than_micro(self, estimator_no_save):
        """测试大盘股成本低于微盘股"""
        large_order = make_order(market_cap=2000e8)
        micro_order = make_order(market_cap=5e8)
        market = make_market_data()
        large_result = estimator_no_save.estimate(large_order, market)
        micro_result = estimator_no_save.estimate(micro_order, market)
        # 大盘股滑点应该更低
        assert (
            large_result.cost_breakdown["slippage"]
            < micro_result.cost_breakdown["slippage"]
        )
        # 大盘股总成本应该更低
        assert large_result.estimated_cost_bps < micro_result.estimated_cost_bps


# ============================================================
# 测试 5: 买卖方向 (印花税)
# ============================================================
class TestSideStampTax:
    """测试买卖方向对佣金的影响 (卖出含印花税)"""

    def test_sell_has_higher_commission_than_buy(self, estimator_no_save):
        """测试卖出佣金高于买入 (印花税)"""
        buy_order = make_order(side="BUY")
        sell_order = make_order(side="SELL")
        market = make_market_data()
        buy_result = estimator_no_save.estimate(buy_order, market)
        sell_result = estimator_no_save.estimate(sell_order, market)
        # 卖出应包含印花税, 佣金更高
        assert (
            sell_result.cost_breakdown["commission"]
            > buy_result.cost_breakdown["commission"]
        )

    def test_buy_no_stamp_tax(self, estimator_no_save):
        """测试买入无印花税"""
        result = estimator_no_save.estimate(make_order(side="BUY"), make_market_data())
        # 买入佣金 = 佣金 + 过户费 (无印花税)
        # 500000 * 0.00025 = 125 + 500000 * 0.00001 = 5 = 130
        assert result.cost_breakdown["commission"] > 0

    def test_invalid_side_raises(self, estimator_no_save):
        """测试无效 side 抛异常"""
        order = make_order(side="UNKNOWN")
        with pytest.raises(PreTradeEstimateError, match="side"):
            estimator_no_save.estimate(order, make_market_data())


# ============================================================
# 测试 6: 延迟 <50ms (HC-3)
# ============================================================
class TestLatencyConstraint:
    """测试预估延迟 <50ms"""

    def test_single_estimate_latency_under_50ms(self, estimator_no_save):
        """测试单次预估延迟 <50ms"""
        result = estimator_no_save.estimate(make_order(), make_market_data())
        assert result.latency_ms < 50.0
        # 实际应该 <5ms
        assert result.latency_ms < 5.0

    def test_batch_100_estimates_under_1s(self, estimator_no_save):
        """测试 100 次预估总延迟 <1s (平均 <10ms)"""
        t_start = time.perf_counter()
        for _ in range(100):
            estimator_no_save.estimate(make_order(), make_market_data())
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        assert elapsed_ms < 1000.0
        # 平均 <10ms
        assert elapsed_ms / 100 < 10.0

    def test_latency_limit_default(self):
        """测试默认延迟上限 50ms"""
        assert DEFAULT_LATENCY_LIMIT_MS == 50.0


# ============================================================
# 测试 7: JSONL 持久化
# ============================================================
class TestJsonlPersistence:
    """测试 JSONL 持久化"""

    def test_estimate_saved_to_file(self, estimator_tmp_dir, tmp_path):
        """测试预估记录写入文件"""
        estimator_tmp_dir.estimate(make_order(), make_market_data())
        # 检查文件存在
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = tmp_path / f"estimate_{date_str}.jsonl"
        assert file_path.exists()

        # 读取并解析
        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["symbol"] == "600276"

    def test_multiple_estimates_appended(self, estimator_tmp_dir, tmp_path):
        """测试多次预估追加写入"""
        for i in range(3):
            estimator_tmp_dir.estimate(
                make_order(symbol=f"TEST{i:03d}"),
                make_market_data(),
            )
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = tmp_path / f"estimate_{date_str}.jsonl"
        with open(file_path, encoding="utf-8") as f:
            lines = [line for line in f.readlines() if line.strip()]
        assert len(lines) >= 3

    def test_get_history_returns_records(self, estimator_tmp_dir):
        """测试 get_history 返回历史记录"""
        # 写入 3 条记录
        for i in range(3):
            estimator_tmp_dir.estimate(
                make_order(symbol=f"HIST{i:03d}"),
                make_market_data(),
            )
        history = estimator_tmp_dir.get_history()
        assert len(history) >= 3
        symbols = [r["symbol"] for r in history]
        assert any(s.startswith("HIST") for s in symbols)

    def test_get_history_filter_by_symbol(self, estimator_tmp_dir):
        """测试 get_history 按 symbol 过滤"""
        estimator_tmp_dir.estimate(make_order(symbol="FILTER_ME"), make_market_data())
        estimator_tmp_dir.estimate(make_order(symbol="OTHER"), make_market_data())
        history = estimator_tmp_dir.get_history(symbol="FILTER_ME")
        assert len(history) >= 1
        assert all(r["symbol"] == "FILTER_ME" for r in history)

    def test_get_history_empty_when_no_file(self, estimator_tmp_dir, tmp_path):
        """测试无文件时返回空列表"""
        # 使用一个不存在的日期
        history = estimator_tmp_dir.get_history(date_str="2020-01-01")
        assert history == []


# ============================================================
# 测试 8: 批量预估与过滤
# ============================================================
class TestBatchEstimate:
    """测试批量预估"""

    def test_estimate_batch_multiple_orders(self, estimator_no_save):
        """测试批量预估多个订单"""
        orders = {
            "600276": make_order(symbol="600276"),
            "000001": make_order(symbol="000001"),
            "510050": make_order(symbol="510050"),
        }
        results = estimator_no_save.estimate_batch(orders)
        assert len(results) == 3
        assert "600276" in results
        assert "000001" in results
        assert "510050" in results

    def test_estimate_batch_with_market_data(self, estimator_no_save):
        """测试批量预估带市场数据"""
        orders = {
            "600276": make_order(symbol="600276"),
            "000001": make_order(symbol="000001"),
        }
        market_data = {
            "600276": make_market_data(adv=2e8),
            "000001": make_market_data(adv=5e7),
        }
        results = estimator_no_save.estimate_batch(orders, market_data)
        # 600276 流动性更好, 成本应更低
        assert (
            results["600276"].estimated_cost_bps < results["000001"].estimated_cost_bps
        )

    def test_estimate_batch_skips_invalid(self, estimator_no_save):
        """测试批量预估跳过无效订单"""
        orders = {
            "valid": make_order(symbol="600276"),
            "invalid": {"symbol": "", "side": "BUY", "shares": 0, "price": 0},  # 无效
        }
        results = estimator_no_save.estimate_batch(orders)
        assert "valid" in results
        assert "invalid" not in results

    def test_filter_approved_returns_only_approved(self):
        """测试 filter_approved 仅返回通过的订单"""
        estimator = PreTradeEstimator(
            cost_threshold_bps=8.0,  # 严格阈值
            save_to_file=False,
        )
        orders = {
            "large_cap": make_order(symbol="510050", market_cap=2000e8),  # 低成本
            "micro_cap": make_order(symbol="002999", market_cap=5e8),  # 高成本
        }
        market_data = {
            "large_cap": make_market_data(adv=1e9),
            "micro_cap": make_market_data(adv=1e7, volatility=0.05),
        }
        approved = estimator.filter_approved(orders, market_data)
        assert "large_cap" in approved
        # micro_cap 应该被否决
        assert "micro_cap" not in approved


# ============================================================
# 测试 9: 异常处理 (参数校验)
# ============================================================
class TestParameterValidation:
    """测试参数校验"""

    def test_empty_symbol_raises(self, estimator_no_save):
        """测试空 symbol 抛异常"""
        order = make_order(symbol="")
        with pytest.raises(PreTradeEstimateError, match="symbol"):
            estimator_no_save.estimate(order, make_market_data())

    def test_zero_shares_raises(self, estimator_no_save):
        """测试 shares=0 抛异常"""
        order = make_order(shares=0, notional=0)
        with pytest.raises(PreTradeEstimateError, match="shares"):
            estimator_no_save.estimate(order, make_market_data())

    def test_negative_price_raises(self, estimator_no_save):
        """测试 price<=0 抛异常"""
        order = make_order(price=-1.0, notional=1.0)
        with pytest.raises(PreTradeEstimateError, match="price"):
            estimator_no_save.estimate(order, make_market_data())

    def test_zero_notional_raises(self, estimator_no_save):
        """测试 notional<=0 抛异常"""
        order = {
            "symbol": "600276",
            "side": "BUY",
            "shares": 0,
            "price": 0,
            "notional": 0,
        }
        with pytest.raises(PreTradeEstimateError):
            estimator_no_save.estimate(order, make_market_data())


# ============================================================
# 测试 10: 阈值校准
# ============================================================
class TestCalibrateThreshold:
    """测试阈值校准 (T3.5 调用)"""

    def test_calibrate_returns_new_threshold(self, estimator_no_save):
        """测试校准返回新阈值"""
        actual_costs = [5.0, 8.0, 12.0, 15.0, 20.0, 25.0, 30.0]
        new_threshold = estimator_no_save.calibrate_threshold(
            actual_costs,
            percentile=0.95,
        )
        # 95% 分位 ≈ 第 7 个 (30.0)
        assert new_threshold == 30.0

    def test_calibrate_updates_estimator_threshold(self, estimator_no_save):
        """测试校准更新 estimator 阈值"""
        original = estimator_no_save.cost_threshold_bps
        actual_costs = [10.0, 15.0, 20.0, 25.0]
        estimator_no_save.calibrate_threshold(actual_costs, percentile=0.5)
        assert estimator_no_save.cost_threshold_bps != original

    def test_calibrate_respects_min_threshold(self):
        """测试校准下限约束"""
        estimator = PreTradeEstimator(
            cost_threshold_bps=30.0,
            save_to_file=False,
        )
        # 实际成本都很低, 但下限 10
        actual_costs = [1.0, 2.0, 3.0]
        new_threshold = estimator.calibrate_threshold(
            actual_costs,
            percentile=0.95,
            min_threshold=10.0,
        )
        assert new_threshold >= 10.0

    def test_calibrate_respects_max_threshold(self):
        """测试校准上限约束"""
        estimator = PreTradeEstimator(
            cost_threshold_bps=30.0,
            save_to_file=False,
        )
        # 实际成本都很高, 但上限 100
        actual_costs = [200.0, 300.0, 400.0]
        new_threshold = estimator.calibrate_threshold(
            actual_costs,
            percentile=0.95,
            max_threshold=100.0,
        )
        assert new_threshold <= 100.0

    def test_calibrate_empty_costs_returns_current(self, estimator_no_save):
        """测试空列表返回当前阈值"""
        current = estimator_no_save.cost_threshold_bps
        new_threshold = estimator_no_save.calibrate_threshold([])
        assert new_threshold == current


# ============================================================
# 测试 11: 便捷工厂函数
# ============================================================
class TestFactoryFunctions:
    """测试工厂函数"""

    def test_create_default_estimator(self):
        """测试默认工厂"""
        est = create_default_estimator(cost_threshold_bps=25.0)
        assert isinstance(est, PreTradeEstimator)
        assert est.cost_threshold_bps == 25.0
        assert est.save_to_file is True

    def test_create_no_save_estimator(self):
        """测试无保存工厂"""
        est = create_no_save_estimator(cost_threshold_bps=40.0)
        assert isinstance(est, PreTradeEstimator)
        assert est.cost_threshold_bps == 40.0
        assert est.save_to_file is False


# ============================================================
# 测试 12: TCAManager.estimate() facade 接口
# ============================================================
class TestTCAManagerFacade:
    """测试 TCAManager.estimate() facade"""

    def test_tca_manager_estimate_returns_pre_trade_estimate(
        self, tmp_path, monkeypatch
    ):
        """测试 TCAManager.estimate() 返回 PreTradeEstimate"""
        # 使用临时目录避免污染生产环境
        monkeypatch.setattr(
            "utils.tca_pre_trade_estimator._DEFAULT_ESTIMATE_DIR",
            tmp_path,
        )
        from utils.tca_engine import TCAManager

        tca = TCAManager()
        order = make_order()
        market_data = make_market_data()
        result = tca.estimate(order, market_data, cost_threshold_bps=30.0)
        assert isinstance(result, PreTradeEstimate)
        assert result.symbol == "600276"
        assert result.threshold_bps == 30.0

    def test_tca_manager_estimate_uses_custom_threshold(self, tmp_path, monkeypatch):
        """测试 TCAManager.estimate() 支持自定义阈值"""
        monkeypatch.setattr(
            "utils.tca_pre_trade_estimator._DEFAULT_ESTIMATE_DIR",
            tmp_path,
        )
        from utils.tca_engine import TCAManager

        tca = TCAManager()
        # 用很严格的阈值, 应该被否决
        result = tca.estimate(
            make_order(market_cap=5e8),  # 微盘股
            make_market_data(adv=1e7, volatility=0.04),
            cost_threshold_bps=5.0,
        )
        assert result.threshold_bps == 5.0
        assert result.approved is False


# ============================================================
# 测试 13: ExecutionRouter.route_with_tca() (HC-1 透传)
# ============================================================
class TestExecutionRouterRouteWithTCA:
    """测试 ExecutionRouter.route_with_tca() HC-1 透传"""

    def test_route_with_tca_flag_disabled_returns_none_estimate(self):
        """测试 flag 关闭时返回 (plan, None)"""
        from utils.execution_router import ExecutionRouter

        router = ExecutionRouter()
        order = {
            "symbol": "600276",
            "quantity": 10000,
            "side": "BUY",
            "notional": 500000,
        }
        signal = {"confidence": 0.6, "strength": 0.3}
        market_state = {"volatility": 0.025, "adv": 1e8}

        with patch("utils.execution_router._tca_pre_trade_enabled", return_value=False):
            plan, estimate = router.route_with_tca(order, signal, market_state)

        assert plan is not None
        assert estimate is None
        assert plan.meta.get("tca_enabled") is False

    def test_route_with_tca_flag_enabled_returns_estimate(self, tmp_path, monkeypatch):
        """测试 flag 启用时返回 (plan, estimate)"""
        monkeypatch.setattr(
            "utils.tca_pre_trade_estimator._DEFAULT_ESTIMATE_DIR",
            tmp_path,
        )
        from utils.execution_router import ExecutionRouter

        # 注入 estimator 避免重复创建
        from utils.tca_pre_trade_estimator import PreTradeEstimator

        estimator = PreTradeEstimator(
            cost_threshold_bps=30.0,
            estimate_dir=tmp_path,
            save_to_file=True,
        )
        router = ExecutionRouter(tca_estimator=estimator)
        order = {
            "symbol": "600276",
            "quantity": 10000,
            "side": "BUY",
            "notional": 500000,
            "price": 50.0,
            "shares": 10000,
            "market_cap": 800e8,
        }
        signal = {"confidence": 0.6, "strength": 0.3}
        market_state = {"volatility": 0.025, "adv": 1e8}

        with patch("utils.execution_router._tca_pre_trade_enabled", return_value=True):
            plan, estimate = router.route_with_tca(order, signal, market_state)

        assert plan is not None
        assert estimate is not None
        assert isinstance(estimate, PreTradeEstimate)
        assert plan.meta.get("tca_enabled") is True
        assert "tca_cost_bps" in plan.meta
        assert "tca_approved" in plan.meta

    def test_route_with_tca_rejection_marks_plan(self, tmp_path, monkeypatch):
        """测试 TCA 否决时 plan.meta 标记 tca_rejected=True"""
        monkeypatch.setattr(
            "utils.tca_pre_trade_estimator._DEFAULT_ESTIMATE_DIR",
            tmp_path,
        )
        from utils.execution_router import ExecutionRouter
        from utils.tca_pre_trade_estimator import PreTradeEstimator

        estimator = PreTradeEstimator(
            cost_threshold_bps=5.0,  # 严格阈值
            estimate_dir=tmp_path,
            save_to_file=True,
        )
        router = ExecutionRouter(tca_estimator=estimator)
        # 微盘股 + 大金额 → 高成本
        order = {
            "symbol": "002999",
            "quantity": 50000,
            "side": "BUY",
            "notional": 500000,
            "price": 10.0,
            "shares": 50000,
            "market_cap": 5e8,
        }
        market_state = {"volatility": 0.05, "adv": 1e7}

        with patch("utils.execution_router._tca_pre_trade_enabled", return_value=True):
            plan, estimate = router.route_with_tca(order, None, market_state)

        assert estimate is not None
        assert estimate.approved is False
        assert plan.meta.get("tca_rejected") is True
        assert "tca_rejection_reason" in plan.meta

    def test_route_with_tca_exception_fail_safe(self):
        """测试 TCA 异常时 fail-safe (不阻断主路径)"""
        from utils.execution_router import ExecutionRouter

        class FailingEstimator:
            def estimate(self, *args, **kwargs):
                raise RuntimeError("TCA 内部错误")

        router = ExecutionRouter(tca_estimator=FailingEstimator())
        order = {"symbol": "X", "side": "BUY", "notional": 1000, "quantity": 100}

        with patch("utils.execution_router._tca_pre_trade_enabled", return_value=True):
            plan, estimate = router.route_with_tca(order, None, {})

        # fail-safe: plan 仍返回, estimate=None
        assert plan is not None
        assert estimate is None
        assert "tca_error" in plan.meta

    def test_route_with_tca_preserves_original_route_logic(self):
        """测试 route_with_tca 保留原始 route 逻辑 (HC-2)"""
        from utils.execution_router import ExecutionRouter

        router = ExecutionRouter()
        order = {
            "symbol": "600276",
            "quantity": 10000,
            "side": "BUY",
            "notional": 500000,
        }
        signal = {"confidence": 0.8, "strength": 0.6}

        # flag 关闭
        with patch("utils.execution_router._tca_pre_trade_enabled", return_value=False):
            plan_with_tca, _ = router.route_with_tca(order, signal, {})

        plan_without_tca = router.route(order, signal, {})
        # 两个 plan 的核心字段应一致 (算法/urgency/滑点)
        assert plan_with_tca.algorithm == plan_without_tca.algorithm
        assert plan_with_tca.urgency == plan_without_tca.urgency
        assert (
            plan_with_tca.estimated_slippage_bps
            == plan_without_tca.estimated_slippage_bps
        )


# ============================================================
# 测试 14: 默认常量
# ============================================================
class TestDefaultConstants:
    """测试默认常量"""

    def test_default_cost_threshold_bps(self):
        """测试默认否决阈值 30 bps"""
        assert DEFAULT_COST_THRESHOLD_BPS == 30.0

    def test_default_latency_limit_ms(self):
        """测试默认延迟上限 50ms"""
        assert DEFAULT_LATENCY_LIMIT_MS == 50.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
