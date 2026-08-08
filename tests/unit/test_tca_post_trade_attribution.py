"""T3.5 TCA 执行后归因单元测试.

验证:
    1. FillRecord 数据类
    2. PostTradeAttribution.record() 记录成交
    3. compare_estimate_vs_actual() 预估 vs 实际对比
    4. attribute_pnl() PnL 归因拆分 (Alpha/Execution/Risk)
    5. calibrate() EOD 校准
    6. summarize() 汇总报告
    7. 历史查询
    8. 异常处理与参数校验
    9. JSONL 持久化
    10. TCAManager.record() facade 接口
    11. TCAManager.calibrate() facade 接口
    12. HedgeExecutionEngine.on_fill() 接口
    13. 买卖方向 (BUY/SELL)
    14. PnL 归因恒等式: Alpha + Execution + Risk = Total
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.tca_post_trade_attribution import (  # noqa: E402
    DEFAULT_ESTIMATE_VS_ACTUAL_TOLERANCE_BPS,
    EstimateVsActual,
    FillRecord,
    PnLAttribution,
    PostTradeAttribution,
    PostTradeAttributionError,
    create_default_attribution,
    create_no_save_attribution,
)


# ============================================================
# Mock 工厂
# ============================================================
class MockPreTradeEstimate:
    """模拟 T3.4 的 PreTradeEstimate"""

    def __init__(
        self,
        symbol: str = "600276",
        side: str = "BUY",
        shares: int = 10000,
        notional: float = 500000,
        price: float = 50.0,
        estimated_cost_bps: float = 7.5,
        estimated_cost_amount: float = 375.0,
    ):
        self.symbol = symbol
        self.side = side
        self.shares = shares
        self.notional = notional
        self.price = price
        self.estimated_cost_bps = estimated_cost_bps
        self.estimated_cost_amount = estimated_cost_amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "shares": self.shares,
            "notional": self.notional,
            "estimated_cost_bps": self.estimated_cost_bps,
            "estimated_cost_amount": self.estimated_cost_amount,
        }


def make_fill(
    symbol: str = "600276",
    side: str = "BUY",
    shares: int = 10000,
    price: float = 50.0,
) -> FillRecord:
    return FillRecord(symbol=symbol, side=side, shares=shares, price=price)


@pytest.fixture
def attribution_no_save() -> PostTradeAttribution:
    """不写文件的归因器"""
    return PostTradeAttribution(save_to_file=False)


@pytest.fixture
def attribution_tmp_dir(tmp_path) -> PostTradeAttribution:
    """使用临时目录的归因器"""
    return PostTradeAttribution(attribution_dir=tmp_path, save_to_file=True)


# ============================================================
# 测试 1: FillRecord 数据类
# ============================================================
class TestFillRecord:
    def test_fill_record_basic(self):
        fill = FillRecord(symbol="600276", side="BUY", shares=10000, price=50.0)
        assert fill.symbol == "600276"
        assert fill.side == "BUY"
        assert fill.shares == 10000
        assert fill.price == 50.0
        assert fill.timestamp != ""

    def test_fill_record_with_optional_fields(self):
        fill = FillRecord(
            symbol="600276", side="SELL", shares=1000, price=10.0,
            broker="CTA", venue="SSE", order_id="ORD001",
            timestamp="2026-07-27T10:00:00",
        )
        assert fill.broker == "CTA"
        assert fill.venue == "SSE"
        assert fill.order_id == "ORD001"
        assert fill.timestamp == "2026-07-27T10:00:00"


# ============================================================
# 测试 2: record() 记录成交
# ============================================================
class TestRecord:
    def test_record_single_fill(self, attribution_no_save):
        fill = make_fill()
        attribution_no_save.record(fill)
        records = attribution_no_save.get_fills("600276")
        assert len(records) == 1
        assert records[0][0].symbol == "600276"

    def test_record_with_estimate(self, attribution_no_save):
        fill = make_fill()
        estimate = MockPreTradeEstimate()
        attribution_no_save.record(fill, estimate)
        records = attribution_no_save.get_fills("600276")
        assert len(records) == 1
        assert records[0][1] is estimate

    def test_record_multiple_fills_same_symbol(self, attribution_no_save):
        for i in range(3):
            fill = make_fill(price=50.0 + i * 0.1)
            attribution_no_save.record(fill)
        records = attribution_no_save.get_fills("600276")
        assert len(records) == 3

    def test_record_multiple_symbols(self, attribution_no_save):
        attribution_no_save.record(make_fill("600276"))
        attribution_no_save.record(make_fill("000001"))
        attribution_no_save.record(make_fill("510050"))
        all_fills = attribution_no_save.get_fills()
        assert len(all_fills) == 3

    def test_record_invalid_type_raises(self, attribution_no_save):
        with pytest.raises(PostTradeAttributionError, match="FillRecord"):
            attribution_no_save.record("not a FillRecord")  # type: ignore[misc]

# ============================================================
# 测试 3: compare_estimate_vs_actual() 预估 vs 实际对比
# ============================================================
class TestCompareEstimateVsActual:
    def test_compare_returns_estimate_vs_actual(self, attribution_no_save):
        fill = make_fill(price=50.05)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0, estimated_cost_amount=250.0)
        attribution_no_save.record(fill, estimate)
        comparison = attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)
        assert isinstance(comparison, EstimateVsActual)
        assert comparison.symbol == "600276"
        # 实际成本 = (50.05 - 50.0) / 50.0 * 10000 = 10 bps
        assert abs(comparison.actual_cost_bps - 10.0) < 0.01
        # 偏差 = 10 - 5 = 5 bps
        assert abs(comparison.deviation_bps - 5.0) < 0.01

    def test_compare_no_records_returns_none(self, attribution_no_save):
        result = attribution_no_save.compare_estimate_vs_actual("NONEXIST")
        assert result is None

    def test_compare_no_estimate_returns_none(self, attribution_no_save):
        fill = make_fill()
        attribution_no_save.record(fill)  # 无 estimate
        result = attribution_no_save.compare_estimate_vs_actual("600276")
        assert result is None

    def test_compare_within_tolerance(self, attribution_no_save):
        # 实际成本与预估偏差 < 5 bps
        fill = make_fill(price=50.05)  # 实际成本 = 10 bps
        estimate = MockPreTradeEstimate(estimated_cost_bps=8.0)  # 偏差 2 bps < 5
        attribution_no_save.record(fill, estimate)
        comparison = attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)
        assert comparison.within_tolerance is True

    def test_compare_outside_tolerance(self, attribution_no_save):
        # 实际成本与预估偏差 > 5 bps
        fill = make_fill(price=50.5)  # 实际成本 = 100 bps
        estimate = MockPreTradeEstimate(estimated_cost_bps=10.0)  # 偏差 90 bps > 5
        attribution_no_save.record(fill, estimate)
        comparison = attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)
        assert comparison.within_tolerance is False

    def test_compare_sell_side(self, attribution_no_save):
        fill = make_fill(side="SELL", price=49.95)
        estimate = MockPreTradeEstimate(side="SELL", estimated_cost_bps=5.0)
        attribution_no_save.record(fill, estimate)
        # 卖出: 实际成本 = |49.95 - 50.0| / 50.0 * 10000 = 10 bps
        comparison = attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)
        assert comparison.side == "SELL"
        assert abs(comparison.actual_cost_bps - 10.0) < 0.01


# ============================================================
# 测试 4: attribute_pnl() PnL 归因拆分
# ============================================================
class TestAttributePnL:
    def test_attribute_returns_pnl_attribution(self, attribution_no_save):
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY",
        )
        assert isinstance(result, PnLAttribution)
        assert result.symbol == "600276"
        assert result.side == "BUY"
        assert result.shares == 10000
        assert result.decision_price == 10.0
        assert result.avg_exec_price == 10.05

    def test_buy_execution_pnl_negative_when_price_higher(self, attribution_no_save):
        """买入: 实际价 > 决策价 → Execution PnL 为负 (执行成本)"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY", estimated_entry_price=10.0,
        )
        # Execution = (10.0 - 10.05) * 10000 * 1 = -500
        assert result.execution_pnl == pytest.approx(-500.0, abs=0.01)
        assert result.execution_bps < 0

    def test_buy_execution_pnl_positive_when_price_lower(self, attribution_no_save):
        """买入: 实际价 < 决策价 → Execution PnL 为正 (执行优于预估)"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=9.95,
            shares=10000, side="BUY", estimated_entry_price=10.0,
        )
        # Execution = (10.0 - 9.95) * 10000 * 1 = 500
        assert result.execution_pnl == pytest.approx(500.0, abs=0.01)
        assert result.execution_bps > 0

    def test_sell_execution_pnl_positive_when_price_higher(self, attribution_no_save):
        """卖出: 实际价 > 决策价 → Execution PnL 为正 (执行优于预估)"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="SELL", estimated_entry_price=10.0,
        )
        # Execution = (10.0 - 10.05) * 10000 * -1 = 500
        assert result.execution_pnl == pytest.approx(500.0, abs=0.01)

    def test_sell_execution_pnl_negative_when_price_lower(self, attribution_no_save):
        """卖出: 实际价 < 决策价 → Execution PnL 为负"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=9.95,
            shares=10000, side="SELL", estimated_entry_price=10.0,
        )
        # Execution = (10.0 - 9.95) * 10000 * -1 = -500
        assert result.execution_pnl == pytest.approx(-500.0, abs=0.01)

    def test_alpha_pnl_zero_when_no_estimated_entry(self, attribution_no_save):
        """无 estimated_entry_price 时 Alpha PnL = 0 (entry = decision)"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY",
        )
        assert result.alpha_pnl == 0.0

    def test_alpha_pnl_with_estimated_entry(self, attribution_no_save):
        """有 estimated_entry_price 时 Alpha PnL = (decision - entry) * shares * direction"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY", estimated_entry_price=9.8,
        )
        # Alpha = (10.0 - 9.8) * 10000 * 1 = 2000
        assert result.alpha_pnl == pytest.approx(2000.0, abs=0.01)

    def test_risk_pnl_sum_of_components(self, attribution_no_save):
        """Risk PnL = hedge + stop_loss + position_adjust"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY",
            hedge_pnl=-500, stop_loss_pnl=-200, position_adjust_pnl=100,
        )
        assert result.risk_pnl == pytest.approx(-600.0, abs=0.01)

    def test_total_pnl_equals_alpha_plus_execution_plus_risk(self, attribution_no_save):
        """恒等式: Total = Alpha + Execution + Risk"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY", estimated_entry_price=9.8,
            hedge_pnl=-500, stop_loss_pnl=0, position_adjust_pnl=0,
        )
        assert result.total_pnl == pytest.approx(
            result.alpha_pnl + result.execution_pnl + result.risk_pnl, abs=0.01
        )

    def test_invalid_decision_price_raises(self, attribution_no_save):
        with pytest.raises(PostTradeAttributionError, match="decision_price"):
            attribution_no_save.attribute_pnl(
                symbol="X", decision_price=-1, avg_exec_price=10,
                shares=100, side="BUY",
            )

    def test_invalid_shares_raises(self, attribution_no_save):
        with pytest.raises(PostTradeAttributionError, match="shares"):
            attribution_no_save.attribute_pnl(
                symbol="X", decision_price=10, avg_exec_price=10,
                shares=0, side="BUY",
            )

    def test_invalid_side_raises(self, attribution_no_save):
        with pytest.raises(PostTradeAttributionError, match="side"):
            attribution_no_save.attribute_pnl(
                symbol="X", decision_price=10, avg_exec_price=10,
                shares=100, side="UNKNOWN",
            )

    def test_bps_calculation(self, attribution_no_save):
        """测试 bps = pnl / notional * 10000"""
        result = attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.0,
            shares=10000, side="BUY", hedge_pnl=-100,
        )
        # notional = 10000 * 10.0 = 100000
        # risk_bps = -100 / 100000 * 10000 = -10
        assert result.risk_bps == pytest.approx(-10.0, abs=0.01)


# ============================================================
# 测试 5: calibrate() EOD 校准
# ============================================================
class TestCalibrate:
    def test_calibrate_no_data_returns_none(self, attribution_no_save):
        result = attribution_no_save.calibrate()
        assert result is None

    def test_calibrate_no_estimator_returns_none_with_data(self, attribution_no_save):
        """有数据但无 estimator → 仅记录统计, 返回 None"""
        fill = make_fill(price=50.5)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0)
        attribution_no_save.record(fill, estimate)
        attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)
        result = attribution_no_save.calibrate()  # 无 estimator
        assert result is None

    def test_calibrate_with_estimator_adjusts_threshold(self, attribution_no_save):
        """有数据 + estimator → 调整阈值"""
        # 准备数据
        fill = make_fill(price=50.5)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0)
        attribution_no_save.record(fill, estimate)
        attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)

        # Mock estimator
        mock_estimator = MagicMock()
        mock_estimator.cost_threshold_bps = 30.0
        mock_estimator.calibrate_threshold.return_value = 25.0

        result = attribution_no_save.calibrate(mock_estimator, percentile=0.95)
        assert result == 25.0
        mock_estimator.calibrate_threshold.assert_called_once()
        # 检查传入的 actual_costs_bps 参数
        call_args = mock_estimator.calibrate_threshold.call_args
        actual_costs = call_args.kwargs.get("actual_costs_bps") or call_args.args[0]
        assert len(actual_costs) == 1
        assert actual_costs[0] > 0

    def test_calibrate_invalid_estimator_raises(self, attribution_no_save):
        """estimator 缺少 calibrate_threshold 方法 → 抛异常"""
        fill = make_fill(price=50.5)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0)
        attribution_no_save.record(fill, estimate)
        attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)

        invalid_estimator = MagicMock(spec=[])  # 空接口
        with pytest.raises(PostTradeAttributionError, match="calibrate_threshold"):
            attribution_no_save.calibrate(invalid_estimator)


# ============================================================
# 测试 6: summarize() 汇总报告
# ============================================================
class TestSummarize:
    def test_summarize_empty_returns_zeros(self, attribution_no_save):
        result = attribution_no_save.summarize()
        assert result["total_pnl"] == 0
        assert result["alpha_pnl"] == 0
        assert result["execution_pnl"] == 0
        assert result["risk_pnl"] == 0
        assert result["n_fills"] == 0

    def test_summarize_with_pnl_data(self, attribution_no_save):
        attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY", hedge_pnl=-500,
        )
        attribution_no_save.attribute_pnl(
            symbol="000001", decision_price=15.0, avg_exec_price=14.95,
            shares=5000, side="BUY", hedge_pnl=-200,
        )
        result = attribution_no_save.summarize()
        assert result["n_fills"] == 0  # fills 是 record() 的, 不是 attribute_pnl 的
        assert "600276" in result["per_symbol"]
        assert "000001" in result["per_symbol"]
        assert result["total_pnl"] != 0

    def test_summarize_with_comparison_data(self, attribution_no_save):
        fill = make_fill(price=50.5)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0)
        attribution_no_save.record(fill, estimate)
        attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)
        result = attribution_no_save.summarize()
        assert result["n_fills"] == 1
        assert result["n_comparisons"] == 1

    def test_summarize_pct_calculation(self, attribution_no_save):
        """测试 alpha_pct + execution_pct + risk_pct = 100% (当 total != 0)"""
        attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10.0, avg_exec_price=10.05,
            shares=10000, side="BUY", hedge_pnl=-500, estimated_entry_price=10.0,
        )
        result = attribution_no_save.summarize()
        if result["total_pnl"] != 0:
            pct_sum = result["alpha_pct"] + result["execution_pct"] + result["risk_pct"]
            assert abs(pct_sum - 100.0) < 0.1


# ============================================================
# 测试 7: 历史查询
# ============================================================
class TestHistoryQueries:
    def test_get_pnl_history_by_symbol(self, attribution_no_save):
        attribution_no_save.attribute_pnl(
            symbol="600276", decision_price=10, avg_exec_price=10.05,
            shares=10000, side="BUY",
        )
        attribution_no_save.attribute_pnl(
            symbol="000001", decision_price=15, avg_exec_price=14.95,
            shares=5000, side="BUY",
        )
        history_600276 = attribution_no_save.get_pnl_history("600276")
        assert len(history_600276) == 1
        assert all(h.symbol == "600276" for h in history_600276)

    def test_get_pnl_history_all(self, attribution_no_save):
        for sym in ["A", "B", "C"]:
            attribution_no_save.attribute_pnl(
                symbol=sym, decision_price=10, avg_exec_price=10,
                shares=1000, side="BUY",
            )
        all_history = attribution_no_save.get_pnl_history()
        assert len(all_history) == 3

    def test_get_comparison_history_by_symbol(self, attribution_no_save):
        fill = make_fill(price=50.5)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0)
        attribution_no_save.record(fill, estimate)
        attribution_no_save.compare_estimate_vs_actual("600276", decision_price=50.0)
        history = attribution_no_save.get_comparison_history("600276")
        assert len(history) == 1
        assert history[0].symbol == "600276"

    def test_get_fills_by_symbol(self, attribution_no_save):
        attribution_no_save.record(make_fill("600276"))
        attribution_no_save.record(make_fill("000001"))
        fills_600276 = attribution_no_save.get_fills("600276")
        assert len(fills_600276) == 1
        assert fills_600276[0][0].symbol == "600276"


# ============================================================
# 测试 8: JSONL 持久化
# ============================================================
class TestJsonlPersistence:
    def test_record_saved_to_fills_file(self, attribution_tmp_dir, tmp_path):
        attribution_tmp_dir.record(make_fill())
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = tmp_path / f"fills_{date_str}.jsonl"
        assert file_path.exists()
        with open(file_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["type"] == "fill"
        assert record["fill"]["symbol"] == "600276"

    def test_comparison_saved_to_file(self, attribution_tmp_dir, tmp_path):
        fill = make_fill(price=50.5)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0)
        attribution_tmp_dir.record(fill, estimate)
        attribution_tmp_dir.compare_estimate_vs_actual("600276", decision_price=50.0)
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = tmp_path / f"estimate_vs_actual_{date_str}.jsonl"
        assert file_path.exists()

    def test_pnl_attribution_saved_to_file(self, attribution_tmp_dir, tmp_path):
        attribution_tmp_dir.attribute_pnl(
            symbol="600276", decision_price=10, avg_exec_price=10.05,
            shares=10000, side="BUY",
        )
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = tmp_path / f"pnl_attribution_{date_str}.jsonl"
        assert file_path.exists()
        with open(file_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["type"] == "pnl_attribution"
        assert record["symbol"] == "600276"

    def test_calibration_log_saved(self, attribution_tmp_dir, tmp_path):
        """测试校准日志写入文件"""
        # 准备数据
        fill = make_fill(price=50.5)
        estimate = MockPreTradeEstimate(estimated_cost_bps=5.0)
        attribution_tmp_dir.record(fill, estimate)
        attribution_tmp_dir.compare_estimate_vs_actual("600276", decision_price=50.0)

        # Mock estimator
        mock_estimator = MagicMock()
        mock_estimator.cost_threshold_bps = 30.0
        mock_estimator.calibrate_threshold.return_value = 25.0

        attribution_tmp_dir.calibrate(mock_estimator)
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = tmp_path / f"calibration_{date_str}.jsonl"
        assert file_path.exists()
        with open(file_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["type"] == "calibration"
        assert record["old_threshold_bps"] == 30.0
        assert record["new_threshold_bps"] == 25.0


# ============================================================
# 测试 9: 便捷工厂函数
# ============================================================
class TestFactoryFunctions:
    def test_create_default_attribution(self):
        attr = create_default_attribution()
        assert isinstance(attr, PostTradeAttribution)
        assert attr.save_to_file is True

    def test_create_no_save_attribution(self):
        attr = create_no_save_attribution()
        assert isinstance(attr, PostTradeAttribution)
        assert attr.save_to_file is False


# ============================================================
# 测试 10: TCAManager.record() facade
# ============================================================
class TestTCAManagerRecordFacade:
    def test_tca_manager_record_with_dict(self, tmp_path, monkeypatch):
        """测试 TCAManager.record() 接受字典"""
        monkeypatch.setattr(
            "utils.tca_post_trade_attribution._DEFAULT_ATTRIBUTION_DIR",
            tmp_path,
        )
        from utils.tca_engine import TCAManager
        tca = TCAManager()
        tca.record({
            "symbol": "600276", "side": "BUY",
            "shares": 1000, "price": 50.0,
        })
        # 验证记录已写入
        attr = tca._post_trade_attribution
        fills = attr.get_fills("600276")
        assert len(fills) == 1

    def test_tca_manager_record_with_fill_record(self, tmp_path, monkeypatch):
        """测试 TCAManager.record() 接受 FillRecord"""
        monkeypatch.setattr(
            "utils.tca_post_trade_attribution._DEFAULT_ATTRIBUTION_DIR",
            tmp_path,
        )
        from utils.tca_engine import TCAManager
        from utils.tca_post_trade_attribution import FillRecord
        tca = TCAManager()
        fill = FillRecord(symbol="000001", side="SELL", shares=500, price=15.0)
        tca.record(fill)
        fills = tca._post_trade_attribution.get_fills("000001")
        assert len(fills) == 1

    def test_tca_manager_calibrate_no_data(self, tmp_path, monkeypatch):
        """测试 TCAManager.calibrate() 无数据返回 None"""
        monkeypatch.setattr(
            "utils.tca_post_trade_attribution._DEFAULT_ATTRIBUTION_DIR",
            tmp_path,
        )
        from utils.tca_engine import TCAManager
        tca = TCAManager()
        result = tca.calibrate()
        assert result is None


# ============================================================
# 测试 11: HedgeExecutionEngine.on_fill() 接口
# ============================================================
class TestHedgeEngineOnFill:
    def test_on_fill_with_dict(self, tmp_path, monkeypatch):
        """测试 on_fill 接受字典"""
        monkeypatch.setattr(
            "utils.tca_post_trade_attribution._DEFAULT_ATTRIBUTION_DIR",
            tmp_path,
        )
        from utils.hedge_execution_engine import HedgeExecutionEngine
        engine = HedgeExecutionEngine()
        engine.on_fill({"symbol": "510050", "side": "BUY", "shares": 10000, "price": 3.45})
        attr = engine.get_post_trade_attribution()
        assert attr is not None
        fills = attr.get_fills("510050")
        assert len(fills) == 1
        assert fills[0][0].price == 3.45

    def test_on_fill_with_fill_record(self, tmp_path, monkeypatch):
        """测试 on_fill 接受 FillRecord"""
        monkeypatch.setattr(
            "utils.tca_post_trade_attribution._DEFAULT_ATTRIBUTION_DIR",
            tmp_path,
        )
        from utils.hedge_execution_engine import HedgeExecutionEngine
        from utils.tca_post_trade_attribution import FillRecord
        engine = HedgeExecutionEngine()
        fill = FillRecord(symbol="600276", side="BUY", shares=1000, price=50.0)
        engine.on_fill(fill)
        attr = engine.get_post_trade_attribution()
        assert attr is not None
        fills = attr.get_fills("600276")
        assert len(fills) == 1

    def test_on_fill_with_tca_engine_fill_record(self, tmp_path, monkeypatch):
        """测试 on_fill 接受 tca_engine.FillRecord (兼容性)"""
        monkeypatch.setattr(
            "utils.tca_post_trade_attribution._DEFAULT_ATTRIBUTION_DIR",
            tmp_path,
        )
        from utils.hedge_execution_engine import HedgeExecutionEngine
        from utils.tca_engine import FillRecord as TCAFillRecord
        engine = HedgeExecutionEngine()
        fill = TCAFillRecord(
            symbol="600276", side="BUY", shares=1000, price=50.0,
            timestamp="2026-07-27T10:00:00",
        )
        engine.on_fill(fill)
        attr = engine.get_post_trade_attribution()
        assert attr is not None
        fills = attr.get_fills("600276")
        assert len(fills) == 1

    def test_on_fill_fail_safe_on_exception(self, tmp_path, monkeypatch):
        """测试 on_fill 异常时 fail-safe"""
        monkeypatch.setattr(
            "utils.tca_post_trade_attribution._DEFAULT_ATTRIBUTION_DIR",
            tmp_path,
        )
        from utils.hedge_execution_engine import HedgeExecutionEngine

        # 构造无效 fill 触发异常
        class BadFill:
            @property
            def symbol(self):
                raise RuntimeError("mock error")

        engine = HedgeExecutionEngine()
        # 不应抛异常
        result = engine.on_fill(BadFill())
        # 应返回 None (fail-safe 后 attribution 仍可能未初始化)
        assert result is None or hasattr(result, "record")


# ============================================================
# 测试 12: PnL 归因恒等式
# ============================================================
class TestPnLIdentity:
    @pytest.mark.parametrize("side, decision, exec_price, entry, hedge, stop, adjust, shares", [
        ("BUY", 10.0, 10.05, 10.0, -500, 0, 0, 10000),
        ("SELL", 10.0, 9.95, 10.0, 200, -100, 50, 5000),
        ("BUY", 50.0, 49.8, 49.5, 0, 0, 0, 20000),
        ("SELL", 100.0, 100.5, 100.0, -1000, -500, 300, 8000),
    ])
    def test_total_equals_alpha_plus_exec_plus_risk(
        self, attribution_no_save,
        side, decision, exec_price, entry, hedge, stop, adjust, shares,
    ):
        """测试多场景下 Total = Alpha + Execution + Risk"""
        result = attribution_no_save.attribute_pnl(
            symbol="TEST", decision_price=decision, avg_exec_price=exec_price,
            shares=shares, side=side, hedge_pnl=hedge,
            stop_loss_pnl=stop, position_adjust_pnl=adjust,
            estimated_entry_price=entry,
        )
        assert result.total_pnl == pytest.approx(
            result.alpha_pnl + result.execution_pnl + result.risk_pnl, abs=0.01
        )


# ============================================================
# 测试 13: 默认常量
# ============================================================
class TestDefaultConstants:
    def test_default_tolerance_bps(self):
        assert DEFAULT_ESTIMATE_VS_ACTUAL_TOLERANCE_BPS == 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
